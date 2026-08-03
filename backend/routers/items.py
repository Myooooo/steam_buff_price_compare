"""物品路由：排名列表、单品价格历史。"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Response

from .. import db
from ..state import app_state

router = APIRouter(prefix="/api/items", tags=["items"])


@router.get("")
async def list_items(
    q: Optional[str] = Query(None, max_length=120),
    weapon: Optional[str] = Query(None, max_length=80),
    item_type: Optional[str] = Query(None, max_length=80),
    exterior: Optional[str] = Query(None, max_length=80),
    price_basis: Literal["buff_price", "steam_price", "steam_net"] = Query("buff_price"),
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    min_score: Optional[float] = Query(None, ge=0, le=100),
    max_score: Optional[float] = Query(None, ge=0, le=100),
    min_discount: Optional[float] = Query(None, ge=0),
    max_discount: Optional[float] = Query(None, ge=0),
    only_profitable: bool = Query(False, alias="only_profitable"),
    source: Optional[str] = Query(None),
    data_state: Optional[Literal["latest", "cached"]] = Query(None),
    sort_by: Literal[
        "name", "score", "buff_price", "steam_price", "steam_net", "discount",
        "spread_pct", "steam_sell_num", "steam_volume", "buff_sell_num", "updated_at",
    ] = Query("score"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
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
        price_basis=price_basis,
        min_price=min_price,
        max_price=max_price,
        min_score=min_score,
        max_score=max_score,
        min_discount=min_discount,
        max_discount=max_discount,
        only_profitable=only_profitable,
        source=source,
        data_state=data_state,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=page_size,
    )
    return {**result, "facets": db.item_facets(app_state.db_conn), "ts": db.now_iso()}


@router.get("/image/{name:path}")
async def item_image(name: str) -> Response:
    """从 SQLite 返回扫描时缓存的商品图片。"""
    asset = db.get_item_asset(app_state.db_conn, name)
    if asset is None:
        raise HTTPException(status_code=404, detail="图片资产尚未缓存")
    return Response(
        content=asset["image_data"],
        media_type=asset["mime_type"],
        headers={"Cache-Control": "private, max-age=86400"},
    )


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
