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


@router.post("/stop")
async def stop_scan(req: ScanRequest) -> dict[str, Any]:
    """终止指定模式；深度扫描的已落库检查点仍可继续。"""
    if app_state.scanner is None:
        raise HTTPException(status_code=503, detail="扫描器未就绪")
    cancelled = await app_state.scanner.cancel_scan(req.mode)
    if not cancelled:
        raise HTTPException(status_code=409, detail="对应扫描模式当前未运行或排队")
    return {"ok": True, "cancelled": True, "mode": req.mode}


@router.post("/deep/pause")
async def pause_deep_scan() -> dict[str, Any]:
    """暂停长时间运行的深度扫描，已落库检查点不会丢失。"""
    if app_state.scanner is None:
        raise HTTPException(status_code=503, detail="扫描器未就绪")
    paused = await app_state.scanner.pause_deep_scan()
    if not paused:
        raise HTTPException(status_code=409, detail="当前没有正在运行的深度扫描")
    return {"ok": True, "paused": True, "progress": app_state.scanner.status().get("deep_scan")}
