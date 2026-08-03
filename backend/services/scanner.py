"""扫描器：keyword 轮 / 深度扫描轮 → 去重 → 取 Steam 价 → 算折价 → 入库 → 广播。

并发模型：单 asyncio.Lock。定时 tick、手动「立即扫描」、登录后自动首扫都收敛到
request_scan()；若已有扫描在跑，则置 pending 标志，跑完自动补跑一轮。
遇到 Buff LoginRequired 立即中止本轮并切换到未登录状态，不自动重试。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Optional

from .. import db
from ..config import Config
from . import steam as steam_svc
from .buff import BuffClient, LoginRequiredError, normalize_item

logger = logging.getLogger("scanner")

UpdateCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


class Scanner:
    def __init__(
        self,
        buff: BuffClient,
        steam_session: Any,
        db_conn: Any,
        db_lock: asyncio.Lock,
        get_config: Callable[[], Config],
        on_update: Optional[UpdateCallback] = None,
    ):
        self.buff = buff
        self.steam_session = steam_session
        self.db_conn = db_conn
        self.db_lock = db_lock
        self.get_config = get_config
        self.on_update = on_update

        self.state = "idle"  # idle | scanning | login_required
        self.pending: Optional[str] = None
        self._task: asyncio.Task | None = None
        self.last_mode: Optional[str] = None
        self.last_status: Optional[str] = None
        self.last_item_count: Optional[int] = None
        self.last_error: Optional[str] = None
        self.last_run: Optional[str] = None
        self.last_duration_sec: Optional[float] = None

    # ---------- 状态 ----------

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "last_mode": self.last_mode,
            "last_status": self.last_status,
            "last_item_count": self.last_item_count,
            "last_error": self.last_error,
            "last_run": self.last_run,
            "last_duration_sec": self.last_duration_sec,
            "pending": self.pending,
        }

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self.on_update:
            try:
                await self.on_update(event, payload)
            except Exception:  # noqa: BLE001
                logger.exception("扫描广播失败")

    # ---------- 触发 ----------

    async def request_scan(self, mode: str = "keyword") -> dict[str, Any]:
        """请求一次扫描；运行中时只保留最后一次待补跑模式。"""
        if self.running:
            self.pending = mode
            return {"queued": True, "mode": mode}
        self._task = asyncio.create_task(self._run_worker(mode))
        return {"queued": False, "mode": mode}

    async def stop(self) -> None:
        """取消当前扫描并清空待补跑请求。"""
        task = self._task
        self.pending = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None
        if self.state == "scanning":
            self.state = "idle"

    async def _run_worker(self, mode: str) -> None:
        try:
            while mode:
                await self._run_mode(mode)
                if self.state == "login_required":
                    self.pending = None
                    break
                mode = self.pending
                self.pending = None
        finally:
            self._task = None

    async def _run_mode(self, mode: str) -> None:
        self.state = "scanning"
        self.last_mode = mode
        self.last_error = None
        await self._emit("scan_start", {"mode": mode})
        try:
            count = await self._scan_once(mode)
        except asyncio.CancelledError:
            self.last_status = "cancelled"
            self.state = "idle"
            raise
        except LoginRequiredError as exc:
            self.last_status = "login_required"
            self.last_error = str(exc)
            self.state = "login_required"
            logger.warning("扫描被中断（需要重新登录）: %s", exc)
            await self._emit("login_required", {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self.last_status = "error"
            self.last_error = str(exc)
            self.state = "idle"
            logger.exception("扫描出错")
            await self._emit("scan_error", {"error": str(exc)})
        else:
            self.last_status = "ok"
            self.last_item_count = count
            self.state = "idle"
            await self._emit("scan_done", {"mode": mode, "item_count": count})

    # ---------- 扫描主体 ----------

    async def _scan_once(self, mode: str) -> int:
        cfg = self.get_config()
        started = db.now_iso()
        started_monotonic = time.monotonic()
        scan_id = db.record_scan(self.db_conn, mode, "running", started_at=started)
        processed = 0
        scan_status = "error"
        try:
            candidates = await self._collect_candidates(cfg, mode)
            for item in candidates:
                price = await steam_svc.get_price(
                    self.steam_session,
                    item["market_hash_name"],
                    appid=cfg.steam_appid,
                    currency=cfg.currency,
                    delay_sec=cfg.steam_delay_sec,
                )
                if price is None or not price.success or price.lowest <= 0:
                    continue
                net = steam_svc.steam_net(
                    price.lowest,
                    cfg.steam_fee_steam_pct,
                    cfg.steam_fee_game_pct,
                    cfg.fee_min,
                    cfg.fee_round,
                )
                item.update(
                    steam_price=price.lowest,
                    steam_volume=price.volume,
                    steam_net=net,
                    discount=steam_svc.discount(item["buff_price"], net),
                    updated_at=db.now_iso(),
                )
                await self._save_item(item)
                processed += 1

            if cfg.history_keep_days > 0:
                try:
                    db.prune_history(self.db_conn, cfg.history_keep_days)
                except Exception:  # noqa: BLE001
                    logger.exception("历史修剪失败")
            scan_status = "ok"
            logger.info("扫描完成 mode=%s items=%d", mode, processed)
            return processed
        except asyncio.CancelledError:
            scan_status = "cancelled"
            raise
        except LoginRequiredError:
            scan_status = "login_required"
            raise
        finally:
            finished = db.now_iso()
            db.finish_scan(self.db_conn, scan_id, scan_status, processed, finished_at=finished)
            self.last_run = finished
            self.last_duration_sec = round(time.monotonic() - started_monotonic, 1)

    async def _collect_candidates(self, cfg: Config, mode: str) -> list[dict[str, Any]]:
        """按 mode 收集候选饰品（去重、过滤无价格）。"""
        seen: dict[str, dict[str, Any]] = {}
        delay = cfg.request_delay_sec

        async def add(items: list[dict], source: str) -> None:
            for raw in items:
                item = normalize_item(raw, game=cfg.game, source=source)
                name = item["market_hash_name"]
                if not name:
                    continue
                if item["buff_price"] is None:
                    continue  # 无在售价，跳过
                if name not in seen:
                    seen[name] = item

        if mode == "deepscan":
            ds = cfg.deep_scan
            page = 1
            while page <= ds.max_pages:
                result = await self.buff.browse_market(
                    ds.min_price, ds.max_price, game=cfg.game, page_num=page, page_size=cfg.page_size
                )
                await add(result["items"], "deepscan")
                if page >= result["total_page"]:
                    break
                page += 1
                await asyncio.sleep(delay)
        else:
            for kw in cfg.keywords:
                items = await self.buff.search_goods(kw, game=cfg.game, page_size=cfg.page_size)
                await add(items, "keyword")
                await asyncio.sleep(delay)

        # 软上限：防止单轮 Steam 请求过多触发风控
        max_items = cfg.max_items_per_cycle
        if len(seen) > max_items:
            logger.warning("候选 %d 个超过上限 %d，截断", len(seen), max_items)
            seen = dict(list(seen.items())[:max_items])
        return list(seen.values())

    async def _save_item(self, item: dict[str, Any]) -> None:
        async with self.db_lock:
            db.save_item(self.db_conn, item)
