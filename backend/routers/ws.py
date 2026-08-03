"""WebSocket 实时通道：连接/重连后立即推送全量快照，之后推送增量事件。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..state import app_state

logger = logging.getLogger("router.ws")

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    app_state.ws_connections.add(ws)
    try:
        # 连接即推全量快照（含登录态、扫描状态、配置、排名）
        await app_state.refresh_login()
        await ws.send_json(app_state.snapshot())
        while True:
            # 前端目前不发消息；收到任何消息仅作心跳保活
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        logger.debug("WS 连接异常断开", exc_info=True)
    finally:
        app_state.ws_connections.discard(ws)
