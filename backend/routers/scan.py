"""扫描路由：手动触发、状态查询。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..models import ScanRequest
from ..state import app_state

router = APIRouter(prefix="/api/scan", tags=["scan"])


@router.post("")
async def trigger_scan(req: ScanRequest) -> dict[str, Any]:
    """手动触发一次扫描（keyword 或 deepscan）。"""
    if app_state.scanner is None:
        raise HTTPException(status_code=503, detail="扫描器未就绪")
    # 未登录直接拒绝，避免空跑
    await app_state.refresh_login()
    if not app_state.buff_logged_in:
        raise HTTPException(status_code=401, detail="请先登录 Buff")
    result = await app_state.scanner.request_scan(req.mode)
    return {"ok": True, **result}


@router.get("/status")
async def scan_status() -> dict[str, Any]:
    if app_state.scanner is None:
        return {"state": "idle"}
    return app_state.scanner.status()
