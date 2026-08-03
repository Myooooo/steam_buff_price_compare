"""物品路由：排名列表、单品价格历史。"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from .. import db
from ..state import app_state

router = APIRouter(prefix="/api/items", tags=["items"])


@router.get("")
async def list_items(
    only_profitable: bool = Query(False, alias="only_profitable"),
    source: Optional[str] = Query(None),
) -> dict[str, Any]:
    """返回按折价升序排名的饰品列表（最优在前）。"""
    items = db.list_items(
        app_state.db_conn,
        only_profitable=only_profitable,
        source=source,
    )
    return {"items": items, "count": len(items), "ts": db.now_iso()}


@router.get("/{name:path}/history")
async def item_history(
    name: str,
    days: int = Query(30, ge=1, le=365),
) -> dict[str, Any]:
    item = db.get_item(app_state.db_conn, name)
    if item is None:
        raise HTTPException(status_code=404, detail="饰品不存在")
    history = db.get_history(app_state.db_conn, name, days=days)
    return {"item": item, "history": history}
