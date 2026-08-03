"""可解释的跨市场机会评分。

评分范围 0–100，由折价质量与流动性质量按加权几何平均合成，避免
极低流动性被高折价完全抵消。流动性以挂牌深度为主、买卖价差为辅；
有 Steam 数量时以 25% 权重融入深度，没有时只使用 BUFF 数量。
"""
from __future__ import annotations

import math
from typing import Any, Optional


DISCOUNT_WEIGHT = 0.60
LIQUIDITY_WEIGHT = 0.40
DEPTH_SHARE = 0.65
SPREAD_SHARE = 0.35
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


def _discount_quality(discount: float) -> float:
    """以 5 折为最优点，非线性惩罚过低异常值和接近亏损的报价。"""
    if discount < 0.50:
        # 平方根在接近 0 时斜率更高，使极低、易失真的折价快速失分。
        return math.sqrt(_clamp(discount / 0.50))
    if discount < 1.0:
        # 二次惩罚在接近 10 折时斜率更高。
        normalized = (discount - 0.50) / 0.50
        return 1.0 - normalized**2
    return 0.0


def opportunity_score_breakdown(
    discount: Optional[float],
    buff_sell_num: Optional[int],
    steam_sell_num: Optional[int],
    spread: Optional[float],
) -> Optional[dict[str, Any]]:
    """返回总分及各项贡献；无有效折价时不评分。"""
    if discount is None or discount <= 0:
        return None

    discount_quality = _discount_quality(discount)
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

    liquidity_quality = DEPTH_SHARE * depth_quality + SPREAD_SHARE * spread_quality
    score = 100.0 * (
        discount_quality ** DISCOUNT_WEIGHT
        * liquidity_quality ** LIQUIDITY_WEIGHT
    )
    return {
        "score": round(score, 1),
        "discount_quality": round(discount_quality * 100, 1),
        "liquidity_quality": round(liquidity_quality * 100, 1),
        "spread_quality": round(spread_quality * 100, 1),
        "depth_quality": round(depth_quality * 100, 1),
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
