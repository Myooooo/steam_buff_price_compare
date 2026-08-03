"""物品路由：排名列表、单品价格历史。"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query

from .. import db
from ..state import app_state

router = APIRouter(prefix="/api/items", tags=["items"])


@router.get("")
async def list_items(
    q: Optional[str] = Query(None, max_length=120),
    weapon: Optional[str] = Query(None, max_length=80),
    item_type: Optional[str] = Query(None, max_length=80),
    exterior: Optional[str] = Query(None, max_length=80),
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    only_profitable: bool = Query(False, alias="only_profitable"),
    source: Optional[str] = Query(None),
    data_state: Optional[Literal["latest", "cached"]] = Query(None),
    sort_by: Literal[
        "name", "buff_price", "steam_price", "steam_net", "discount",
        "steam_volume", "buff_sell_num", "updated_at",
    ] = Query("discount"),
    sort_order: Literal["asc", "desc"] = Query("asc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=20, le=200),
) -> dict[str, Any]:
    """返回服务端筛选、排序并分页后的饰品列表。"""
    result = db.query_items(
        app_state.db_conn,
        query=q,
        weapon=weapon,
        item_type=item_type,
        exterior=exterior,
        min_price=min_price,
        max_price=max_price,
        only_profitable=only_profitable,
        source=source,
        data_state=data_state,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )
    return {**result, "facets": db.item_facets(app_state.db_conn), "ts": db.now_iso()}


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
