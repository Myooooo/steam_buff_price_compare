"""可解释的跨市场机会评分。

评分范围 0–100：折价 60 分，流动性 40 分。流动性由买卖价差
（24 分）与挂牌深度（16 分）组成；有 Steam 数量时以 25% 权重
融入挂牌深度，没有时只使用 BUFF 数量，不因数据缺失额外扣分。
"""
from __future__ import annotations

import math
from typing import Any, Optional


DISCOUNT_POINTS = 60.0
SPREAD_POINTS = 24.0
DEPTH_POINTS = 16.0
LISTING_CAP = 1000
BUFF_DEPTH_SHARE = 0.75
STEAM_DEPTH_SHARE = 0.25


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def spread_pct(sell_price: Optional[float], buy_price: Optional[float]) -> Optional[float]:
    """BUFF 买一/卖一价差系数 = (卖一 - 买一) / 卖一。"""
    if sell_price is None or buy_price is None or sell_price <= 0 or buy_price <= 0:
        return None
    return round(max(0.0, (sell_price - buy_price) / sell_price), 4)


def _log_liquidity(count: Optional[int]) -> float:
    if count is None or count <= 0:
        return 0.0
    return _clamp(math.log1p(count) / math.log1p(LISTING_CAP))


def opportunity_score_breakdown(
    discount: Optional[float],
    buff_sell_num: Optional[int],
    steam_sell_num: Optional[int],
    spread: Optional[float],
) -> Optional[dict[str, Any]]:
    """返回总分及各项贡献；无有效折价时不评分。"""
    if discount is None or discount <= 0:
        return None

    discount_quality = _clamp((1.0 - discount) / 0.35)
    spread_quality = 0.0 if spread is None else _clamp((0.20 - spread) / 0.18)

    buff_depth = _log_liquidity(buff_sell_num)
    steam_depth = _log_liquidity(steam_sell_num)
    if buff_sell_num is not None and steam_sell_num is not None:
        depth_quality = BUFF_DEPTH_SHARE * buff_depth + STEAM_DEPTH_SHARE * steam_depth
        depth_source = "buff_steam"
    elif buff_sell_num is not None:
        depth_quality = buff_depth
        depth_source = "buff"
    elif steam_sell_num is not None:
        depth_quality = steam_depth
        depth_source = "steam"
    else:
        depth_quality = 0.0
        depth_source = "none"

    discount_points = DISCOUNT_POINTS * discount_quality
    spread_points = SPREAD_POINTS * spread_quality
    depth_points = DEPTH_POINTS * depth_quality
    liquidity_points = spread_points + depth_points
    score = discount_points + liquidity_points
    return {
        "score": round(score, 1),
        "discount_points": round(discount_points, 1),
        "liquidity_points": round(liquidity_points, 1),
        "spread_points": round(spread_points, 1),
        "depth_points": round(depth_points, 1),
        "depth_source": depth_source,
    }


def opportunity_score(
    discount: Optional[float],
    buff_sell_num: Optional[int],
    steam_sell_num: Optional[int],
    spread: Optional[float],
) -> Optional[float]:
    """计算 0–100 分机会评分；无有效折价时不评分。"""
    breakdown = opportunity_score_breakdown(
        discount,
        buff_sell_num,
        steam_sell_num,
        spread,
    )
    return breakdown["score"] if breakdown is not None else None
