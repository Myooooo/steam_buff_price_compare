"""关键词完整分页扫描与可断点续传的全市场深度扫描。"""
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


class DeepScanPreempted(Exception):
    """深度扫描在持久化检查点主动让出执行权给关键词扫描。"""


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
        self.current_mode: Optional[str] = None
        self.pending: Optional[str] = None
        self.progress: dict[str, Any] = {}
        self._task: asyncio.Task | None = None
        self._resume_deep = False
        self._pause_requested = False

        self.last_mode: Optional[str] = None
        self.last_status: Optional[str] = None
        self.last_item_count: Optional[int] = None
        self.last_error: Optional[str] = None
        self.last_run: Optional[str] = None
        self.last_duration_sec: Optional[float] = None

    # ---------- 状态与生命周期 ----------

    def status(self) -> dict[str, Any]:
        cfg = self.get_config()
        deep_progress = db.get_deep_progress(self.db_conn, cfg.game) if self.db_conn is not None else None
        if deep_progress:
            deep_progress["active"] = self.running and self.current_mode == "deepscan"
            deep_progress["queued"] = self._resume_deep or self.pending == "deepscan"
            deep_progress["resumable"] = deep_progress["phase"] != "complete"
        return {
            "state": self.state,
            "current_mode": self.current_mode,
            "progress": self.progress,
            "deep_scan": deep_progress,
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

    async def _emit_progress(self) -> None:
        await self._emit("scan_progress", self.status())

    async def request_scan(self, mode: str = "keyword") -> dict[str, Any]:
        """触发扫描；关键词请求可在深度扫描的安全检查点获得优先执行。"""
        if self.running:
            if mode == "deepscan" and self._resume_deep:
                return {"queued": False, "already_running": True, "mode": mode}
            if mode == self.current_mode or mode == self.pending:
                return {"queued": False, "already_running": True, "mode": mode}
            if mode == "keyword" or self.pending is None:
                self.pending = mode
            return {"queued": True, "mode": mode}
        self._task = asyncio.create_task(self._run_worker(mode))
        return {"queued": False, "already_running": False, "mode": mode}

    async def pause_deep_scan(self) -> bool:
        """暂停当前深度扫描；已持久化的页码/定价进度可在下次触发时续跑。"""
        if self.running and self.current_mode == "deepscan":
            self._pause_requested = True
            await self.stop()
            self._pause_requested = False
            return True
        if self._resume_deep or self.pending == "deepscan":
            self._resume_deep = False
            if self.pending == "deepscan":
                self.pending = None
            await self._emit("scan_paused", {"mode": "deepscan"})
            return True
        return False

    async def stop(self) -> None:
        task = self._task
        self.pending = None
        self._resume_deep = False
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None
        self.current_mode = None
        if self.state == "scanning":
            self.state = "idle"

    async def _run_worker(self, mode: str) -> None:
        try:
            while mode:
                self.current_mode = mode
                await self._run_mode(mode)
                if self.state == "login_required":
                    self.pending = None
                    self._resume_deep = False
                    break

                mode = self.pending
                self.pending = None
                if mode == "deepscan":
                    self._resume_deep = False
                elif mode is None and self._resume_deep:
                    mode = "deepscan"
                    self._resume_deep = False
        finally:
            self.current_mode = None
            self._task = None
            if self.state == "scanning":
                self.state = "idle"

    async def _run_mode(self, mode: str) -> None:
        self.state = "scanning"
        self.last_mode = mode
        self.last_error = None
        self.progress = {"mode": mode, "phase": "starting"}
        await self._emit("scan_start", {"mode": mode})
        try:
            count = await self._scan_once(mode)
        except asyncio.CancelledError:
            self.last_status = "paused" if self._pause_requested else "cancelled"
            self.state = "idle"
            if self._pause_requested:
                await self._emit("scan_paused", {"mode": mode})
            raise
        except DeepScanPreempted:
            self.last_status = "paused"
            self.state = "idle"
            self._resume_deep = True
            await self._emit("scan_paused", {"mode": mode, "reason": "keyword_priority"})
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
            if mode == "deepscan":
                processed = await self._scan_deep(cfg, scan_id)
            else:
                processed = await self._scan_keywords(cfg, scan_id)

            if cfg.history_keep_days > 0:
                try:
                    db.prune_history(self.db_conn, cfg.history_keep_days)
                except Exception:  # noqa: BLE001
                    logger.exception("历史修剪失败")
            scan_status = "ok"
            logger.info("扫描完成 mode=%s items=%d", mode, processed)
            return processed
        except asyncio.CancelledError:
            scan_status = "paused" if self._pause_requested else "cancelled"
            raise
        except DeepScanPreempted:
            scan_status = "paused"
            raise
        except LoginRequiredError:
            scan_status = "login_required"
            raise
        finally:
            finished = db.now_iso()
            db.finish_scan(self.db_conn, scan_id, scan_status, processed, finished_at=finished)
            self.last_run = finished
            self.last_duration_sec = round(time.monotonic() - started_monotonic, 1)

    async def _scan_keywords(self, cfg: Config, scan_id: int) -> int:
        seen: dict[str, dict[str, Any]] = {}
        for keyword_index, keyword in enumerate(cfg.keywords, start=1):
            page = 1
            total_pages = 1
            while page <= total_pages:
                result = await self.buff.search_goods(
                    keyword,
                    game=cfg.game,
                    page_num=page,
                    page_size=cfg.page_size,
                )
                total_pages = max(1, int(result.get("total_page") or 1))
                for raw in result.get("items") or []:
                    item = normalize_item(raw, game=cfg.game, source="keyword")
                    name = item["market_hash_name"]
                    if name and item["buff_price"] is not None:
                        seen.setdefault(name, item)
                self.progress = {
                    "mode": "keyword",
                    "phase": "collecting",
                    "keyword": keyword,
                    "keyword_index": keyword_index,
                    "keyword_count": len(cfg.keywords),
                    "page": page,
                    "total_pages": total_pages,
                    "candidates": len(seen),
                }
                await self._emit_progress()
                page += 1
                if page <= total_pages and cfg.request_delay_sec > 0:
                    await asyncio.sleep(cfg.request_delay_sec)
            if keyword_index < len(cfg.keywords) and cfg.request_delay_sec > 0:
                await asyncio.sleep(cfg.request_delay_sec)

        candidates = list(seen.values())
        processed = 0
        for index, item in enumerate(candidates, start=1):
            if await self._price_item(item, cfg, scan_id):
                processed += 1
            self.progress = {
                "mode": "keyword",
                "phase": "pricing",
                "current": index,
                "total": len(candidates),
                "processed": processed,
            }
            if index == len(candidates) or index % 10 == 0:
                await self._emit_progress()
        return processed

    async def _scan_deep(self, cfg: Config, scan_id: int) -> int:
        async with self.db_lock:
            progress = db.start_deep_cycle(self.db_conn, cfg.game)
        generation = int(progress["generation"])
        processed = 0

        if progress["phase"] == "indexing":
            page = int(progress.get("next_page") or 1)
            while True:
                result = await self.buff.browse_market(
                    game=cfg.game,
                    page_num=page,
                    page_size=cfg.page_size,
                )
                total_pages = max(1, int(result.get("total_page") or 1))
                normalized = []
                for raw in result.get("items") or []:
                    item = normalize_item(raw, game=cfg.game, source="deepscan")
                    if item["market_hash_name"]:
                        normalized.append(item)
                async with self.db_lock:
                    progress = db.save_deep_index_page(
                        self.db_conn,
                        cfg.game,
                        generation,
                        normalized,
                        next_page=page + 1,
                        total_pages=total_pages,
                    )
                self.progress = {"mode": "deepscan", **progress}
                await self._emit_progress()

                if page >= total_pages:
                    async with self.db_lock:
                        db.set_deep_phase(self.db_conn, cfg.game, generation, "pricing")
                    break
                if self.pending == "keyword":
                    raise DeepScanPreempted
                page += 1
                if cfg.request_delay_sec > 0:
                    await asyncio.sleep(cfg.request_delay_sec)

        while True:
            async with self.db_lock:
                item = db.next_deep_item(self.db_conn, cfg.game, generation)
            if item is None:
                async with self.db_lock:
                    db.set_deep_phase(self.db_conn, cfg.game, generation, "complete")
                self.progress = {"mode": "deepscan", **(db.get_deep_progress(self.db_conn, cfg.game) or {})}
                await self._emit_progress()
                return processed

            success = await self._price_deep_item(item, cfg, generation, scan_id)
            if success:
                processed += 1
            progress = db.get_deep_progress(self.db_conn, cfg.game) or {}
            self.progress = {"mode": "deepscan", **progress}
            completed = int(progress.get("priced_count") or 0) + int(progress.get("failed_count") or 0)
            if completed % 10 == 0:
                await self._emit_progress()
            if self.pending == "keyword":
                raise DeepScanPreempted

    async def _price_item(self, item: dict[str, Any], cfg: Config, scan_id: int) -> bool:
        price = await steam_svc.get_price(
            self.steam_session,
            item["market_hash_name"],
            appid=cfg.steam_appid,
            currency=cfg.currency,
            delay_sec=cfg.steam_delay_sec,
        )
        if price is None or not price.success or price.lowest <= 0:
            return False
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
        async with self.db_lock:
            db.save_item(self.db_conn, item, scan_id=scan_id)
        return True

    async def _price_deep_item(
        self,
        item: dict[str, Any],
        cfg: Config,
        generation: int,
        scan_id: int,
    ) -> bool:
        success = False
        if item.get("buff_price") is not None:
            price = await steam_svc.get_price(
                self.steam_session,
                item["market_hash_name"],
                appid=cfg.steam_appid,
                currency=cfg.currency,
                delay_sec=cfg.steam_delay_sec,
            )
            if price is not None and price.success and price.lowest > 0:
                net = steam_svc.steam_net(
                    price.lowest,
                    cfg.steam_fee_steam_pct,
                    cfg.steam_fee_game_pct,
                    cfg.fee_min,
                    cfg.fee_round,
                )
                item.update(
                    source="deepscan",
                    steam_price=price.lowest,
                    steam_volume=price.volume,
                    steam_net=net,
                    discount=steam_svc.discount(item["buff_price"], net),
                    updated_at=db.now_iso(),
                )
                success = True
        async with self.db_lock:
            db.finish_deep_item(
                self.db_conn,
                item,
                generation,
                scan_id,
                success=success,
            )
        return success
