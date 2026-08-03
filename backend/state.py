"""应用共享状态（单进程单 worker 下即全局单例）。

持有：config、DB 连接、Buff 客户端、Steam 会话、扫码登录、扫描器、调度器、
WebSocket 连接集合，以及统一的快照/广播方法。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Any, Optional

from fastapi import WebSocket

from . import db as dbmod
from .config import Config
from .services.buff import is_login_complete

logger = logging.getLogger("state")


class AppState:
    def __init__(self) -> None:
        self.config: Config = Config()
        self.db_conn: Any = None
        self.db_lock = asyncio.Lock()

        self.buff: Any = None  # BuffClient
        self.steam_session: Any = None  # curl_cffi AsyncSession
        self.qr: Any = None  # QRLogin
        self.scanner: Any = None  # Scanner
        self.scheduler: Any = None  # Scheduler

        self.ws_connections: set[WebSocket] = set()
        self._buff_logged_in = False
        self._login_user: Optional[dict] = None

    # ---------- WebSocket ----------

    async def broadcast(self, payload: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in list(self.ws_connections):
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.ws_connections.discard(ws)

    # ---------- 数据 ----------

    def items(self, **kw: Any) -> list[dict[str, Any]]:
        return dbmod.list_items(self.db_conn, **kw)

    async def refresh_login(self) -> Optional[dict]:
        """刷新 Buff 登录状态并返回当前可用的用户资料。"""
        if self.buff is None:
            return None
        try:
            st = await self.buff.login_status()
        except Exception:  # noqa: BLE001
            logger.warning("查询登录状态失败", exc_info=True)
            return None
        if is_login_complete(st):
            self._buff_logged_in = True
            user = st.get("user") or {}
            if not user and self.qr is not None and self.qr.state == "confirmed":
                user = self.qr.user or {}
            if user:
                self._login_user = user
            return self._login_user
        self._buff_logged_in = False
        self._login_user = None
        return None

    @property
    def buff_logged_in(self) -> bool:
        return self._buff_logged_in

    @property
    def login_user(self) -> Optional[dict]:
        return self._login_user

    # ---------- 快照 ----------

    def snapshot(self, include_items: bool = True) -> dict[str, Any]:
        scan = self.scanner.status() if self.scanner else {}
        qr_state = self.qr.status() if self.qr else {"state": "idle"}
        user = self._login_user or {}
        items = self.items() if include_items else []
        return {
            "type": "snapshot",
            "buff": {
                "logged_in": self._buff_logged_in,
                "nickname": user.get("nickname"),
                "qr_state": qr_state.get("state", "idle"),
            },
            "scan": scan,
            "scheduler": {
                "next_run": self.scheduler.next_run if self.scheduler else None,
                "next_deep_run": self.scheduler.next_deep_run if self.scheduler else None,
            },
            "config": asdict(self.config),
            "items": items,
            "ts": dbmod.now_iso(),
        }


# 全局单例（单进程单 worker）
app_state = AppState()
