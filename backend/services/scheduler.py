"""按配置间隔触发常规扫描和深度扫描。"""
from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import Awaitable, Callable

logger = logging.getLogger("scheduler")

ScanTrigger = Callable[[str], Awaitable[object]]
IntervalProvider = Callable[[], tuple[int, int | None]]


def _iso(timestamp: float) -> str:
    return datetime.datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


class Scheduler:
    def __init__(self, request_scan: ScanTrigger, get_intervals: IntervalProvider):
        self.request_scan = request_scan
        self.get_intervals = get_intervals
        self._task: asyncio.Task | None = None
        self._last_keyword: float | None = None
        self._last_deep: float | None = None
        self.next_run: str | None = None
        self.next_deep_run: str | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task = self._task
        self._task = None
        self.next_run = None
        self.next_deep_run = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _current_intervals(self) -> tuple[float, float | None]:
        keyword_minutes, deep_minutes = self.get_intervals()
        keyword_period = max(keyword_minutes, 1) * 60
        deep_period = deep_minutes * 60 if deep_minutes and deep_minutes > 0 else None
        return keyword_period, deep_period

    async def _loop(self) -> None:
        while True:
            try:
                keyword_period, deep_period = self._current_intervals()
                now = time.time()
                if self._last_keyword is None:
                    self._last_keyword = now
                keyword_next = self._last_keyword + keyword_period
                if deep_period and self._last_deep is None:
                    self._last_deep = now
                deep_next = self._last_deep + deep_period if deep_period else None

                if deep_next is not None and deep_next < keyword_next:
                    mode = "deepscan"
                    scheduled_at = deep_next
                else:
                    mode = "keyword"
                    scheduled_at = keyword_next

                self.next_run = _iso(keyword_next)
                self.next_deep_run = _iso(deep_next) if deep_next else None
                await asyncio.sleep(max(scheduled_at - now, 1.0))
                await self.request_scan(mode)

                triggered_at = time.time()
                if mode == "keyword":
                    self._last_keyword = triggered_at
                else:
                    self._last_deep = triggered_at
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("调度循环异常，2 秒后重试")
                await asyncio.sleep(2)
