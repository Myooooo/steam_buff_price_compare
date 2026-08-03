"""认证路由：登录状态、创建/刷新二维码、登出。"""
from __future__ import annotations

import base64
from typing import Any

from fastapi import APIRouter

from ..state import app_state

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/status")
async def auth_status() -> dict[str, Any]:
    user = await app_state.refresh_login()
    st = app_state.qr.status() if app_state.qr else {"state": "idle"}
    return {
        "logged_in": app_state.buff_logged_in,
        "user": user,
        "qr": st,
    }


@router.post("/qr")
async def create_qr() -> dict[str, Any]:
    """创建二维码并启动后台轮询；返回 PNG(base64) 供前端渲染。"""
    if app_state.qr is None:
        return {"error": "登录服务未就绪"}
    info = await app_state.qr.start()
    img = await app_state.qr.qr_image()
    image_b64 = base64.b64encode(img).decode() if img else None
    return {
        "code_id": info["code_id"],
        "qr_url": info["qr_url"],
        "state": app_state.qr.state,
        "qr_image": f"data:image/png;base64,{image_b64}" if image_b64 else None,
    }


@router.post("/logout")
async def logout() -> dict[str, Any]:
    if app_state.buff is not None:
        await app_state.buff.logout()
    if app_state.qr is not None:
        await app_state.qr.cancel()
    await app_state.refresh_login()
    await app_state.broadcast(app_state.snapshot())
    return {"ok": True}
