"""WebSocket 实时通道：推送状态快照；商品列表通过分页 HTTP API 获取。"""
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
        # 连接即推状态快照；大规模商品索引不进入 WebSocket 消息。
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
