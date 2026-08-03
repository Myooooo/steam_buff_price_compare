"""配置路由：GET/PUT 配置（校验、保存、生效）。"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter

from ..config import save_config
from ..models import ConfigIn
from ..state import app_state

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
async def get_config() -> dict[str, Any]:
    return asdict(app_state.config)


@router.put("")
async def update_config(payload: ConfigIn) -> dict[str, Any]:
    cfg = app_state.config
    auto_scan_before = cfg.auto_scan
    updates = payload.model_dump(exclude_unset=True)
    deep_updates = updates.pop("deep_scan", None) or {}

    # 覆盖顶层字段
    for k, v in updates.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    # 覆盖 deep_scan 子字段
    for k, v in deep_updates.items():
        if hasattr(cfg.deep_scan, k):
            setattr(cfg.deep_scan, k, v)

    save_config(cfg)
    if app_state.scheduler is not None and cfg.auto_scan != auto_scan_before:
        if cfg.auto_scan:
            app_state.scheduler.start()
        else:
            await app_state.scheduler.stop()
    await app_state.broadcast({"type": "config", "config": asdict(cfg)})
    return asdict(cfg)
