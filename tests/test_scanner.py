"""扫描器集成测试：用 Mock 的 Buff/Steam 客户端 + 内存 SQLite。

注意：每个测试在同一个事件循环内完成「触发扫描 + 等待结束」，避免 asyncio.create_task
被 asyncio.run 关闭循环时取消。
"""
import asyncio
import sqlite3

import pytest

from backend import db
from backend.config import Config
from backend.services.buff import LoginRequiredError
from backend.services.scanner import Scanner


class FakeBuff:
    def __init__(self, items_by_kw=None, fail=False, deep=None):
        self.items_by_kw = items_by_kw or {}
        self.fail = fail
        self.deep = deep or {"items": [], "total_page": 1}

    async def search_goods(self, keyword, game="csgo", page_size=20):
        if self.fail:
            raise LoginRequiredError("请先登录")
        return self.items_by_kw.get(keyword, [])

    async def browse_market(self, lo, hi, game="csgo", page_num=1, page_size=20):
        if self.fail:
            raise LoginRequiredError("请先登录")
        return self.deep


class FakeSteam:
    def __init__(self, prices=None):
        self.prices = prices or {}

    async def get(self, url, params=None, timeout=15):
        class R:
            status_code = 200
            headers = {}

            def json(self):
                return self._p

        r = R()
        r._p = self.prices.get(params["market_hash_name"], {"success": False})
        return r


def make_item(name, buff_price, extra=None):
    d = {
        "market_hash_name": name,
        "sell_min_price": str(buff_price) if buff_price is not None else None,
        "sell_num": 3,
        "buy_max_price": "190.0",
        "buy_num": 1,
    }
    if extra:
        d.update(extra)
    return d


def make_cfg(**kw):
    base = dict(request_delay_sec=0, steam_delay_sec=0)
    base.update(kw)
    return Config(**base)


async def wait_idle(scanner):
    for _ in range(300):
        if not scanner.running:
            await asyncio.sleep(0.03)
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.02)


def test_keyword_scan_computes_prices():
    async def scenario():
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        db.init_db(conn)
        lock = asyncio.Lock()
        cfg = make_cfg(keywords=["ak-47"])
        buff = FakeBuff({
            "ak-47": [
                make_item("AK-47 | Redline (Field-Tested)", 200.0),
                make_item("AK-47 | Vulcan (Factory New)", None),  # 无价 -> 跳过
            ]
        })
        steam = FakeSteam({
            "AK-47 | Redline (Field-Tested)": {"success": True, "lowest_price": "¥ 289.00", "volume": "91"},
        })
        events = []

        async def on_update(ev, payload):
            events.append(ev)

        scanner = Scanner(buff, steam, conn, lock, lambda: cfg, on_update)
        await scanner.request_scan("keyword")
        await wait_idle(scanner)

        assert scanner.last_status == "ok"
        assert scanner.last_item_count == 1
        items = db.list_items(conn)
        assert len(items) == 1
        it = items[0]
        assert it["steam_net"] == 245.65  # 289 -> 到分
        assert it["discount"] == pytest.approx(200 / 245.65, rel=1e-3)
        assert db.get_history(conn, "AK-47 | Redline (Field-Tested)") != []
        assert events == ["scan_start", "scan_done"]
        conn.close()

    asyncio.run(scenario())


def test_deepscan_mode():
    async def scenario():
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        db.init_db(conn)
        lock = asyncio.Lock()
        cfg = make_cfg(deep_scan={"enabled": True, "min_price": 20, "max_price": 300, "max_pages": 2})
        buff = FakeBuff(
            deep={
                "items": [make_item("Glock-18 | Fade (Factory New)", 50.0, {"sell_num": 1})],
                "total_page": 1,
            }
        )
        steam = FakeSteam({"Glock-18 | Fade (Factory New)": {"success": True, "lowest_price": "¥ 60.00", "volume": "3"}})
        scanner = Scanner(buff, steam, conn, lock, lambda: cfg)
        await scanner.request_scan("deepscan")
        await wait_idle(scanner)
        items = db.list_items(conn, source="deepscan")
        assert len(items) == 1
        assert items[0]["source"] == "deepscan"
        conn.close()

    asyncio.run(scenario())


def test_login_required_aborts_and_marks_state():
    async def scenario():
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        db.init_db(conn)
        lock = asyncio.Lock()
        cfg = make_cfg(keywords=["x"])
        buff = FakeBuff(fail=True)
        steam = FakeSteam()
        events = []

        async def on_update(ev, payload):
            events.append(ev)

        scanner = Scanner(buff, steam, conn, lock, lambda: cfg, on_update)
        await scanner.request_scan("keyword")
        await wait_idle(scanner)

        assert scanner.state == "login_required"
        assert scanner.last_status == "login_required"
        assert "login_required" in events
        assert db.list_items(conn) == []
        scan = conn.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 1").fetchone()
        assert scan["status"] == "login_required"
        assert scan["finished_at"] is not None
        conn.close()

    asyncio.run(scenario())


def test_pending_flag_queues():
    async def scenario():
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        db.init_db(conn)
        lock = asyncio.Lock()
        cfg = make_cfg(keywords=["ak-47"])
        buff = FakeBuff({"ak-47": [make_item("AK-47 | Redline", 200.0)]})

        class SlowSteam(FakeSteam):
            async def get(self, url, params=None, timeout=15):
                await asyncio.sleep(0.15)
                return await super().get(url, params=params, timeout=timeout)

        steam = SlowSteam({"AK-47 | Redline": {"success": True, "lowest_price": "¥ 289.00", "volume": "5"}})
        events = []

        async def on_update(ev, payload):
            events.append(ev)

        scanner = Scanner(buff, steam, conn, lock, lambda: cfg, on_update)

        # 触发第一轮，并在其进行中再请求一轮 -> 第二轮应排队补跑
        await scanner.request_scan("keyword")
        # 等第一轮真正开始（SlowSteam 保证扫描进行中）
        for _ in range(50):
            if scanner.state == "scanning":
                break
            await asyncio.sleep(0.02)
        assert scanner.state == "scanning"
        res2 = await scanner.request_scan("keyword")
        assert res2["queued"] is True
        assert scanner.pending == "keyword"

        await wait_idle(scanner)
        # 第一轮完成后自动补跑第二轮（SlowSteam 单轮约 0.15s，多等一会）
        await asyncio.sleep(0.4)
        assert events.count("scan_start") == 2
        assert events.count("scan_done") == 2
        assert scanner.last_status == "ok"
        assert scanner.last_item_count == 1
        conn.close()

    asyncio.run(scenario())


def test_stop_cancels_active_scan_and_finishes_record():
    async def scenario():
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        db.init_db(conn)
        cfg = make_cfg(keywords=["ak-47"])
        buff = FakeBuff({"ak-47": [make_item("AK-47 | Redline", 200.0)]})

        class BlockingSteam(FakeSteam):
            async def get(self, url, params=None, timeout=15):
                await asyncio.sleep(60)

        scanner = Scanner(buff, BlockingSteam(), conn, asyncio.Lock(), lambda: cfg)
        await scanner.request_scan("keyword")
        for _ in range(50):
            if scanner.state == "scanning":
                break
            await asyncio.sleep(0.01)

        await scanner.stop()

        scan = conn.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 1").fetchone()
        assert scanner.running is False
        assert scanner.last_status == "cancelled"
        assert scan["status"] == "cancelled"
        assert scan["finished_at"] is not None
        conn.close()

    asyncio.run(scenario())


def test_scan_error_finishes_record():
    class BrokenBuff(FakeBuff):
        async def search_goods(self, keyword, game="csgo", page_size=20):
            raise RuntimeError("upstream failed")

    async def scenario():
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        db.init_db(conn)
        scanner = Scanner(
            BrokenBuff(),
            FakeSteam(),
            conn,
            asyncio.Lock(),
            lambda: make_cfg(keywords=["broken"]),
        )

        await scanner.request_scan("keyword")
        await wait_idle(scanner)

        scan = conn.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 1").fetchone()
        assert scanner.last_status == "error"
        assert scan["status"] == "error"
        assert scan["finished_at"] is not None
        conn.close()

    asyncio.run(scenario())
