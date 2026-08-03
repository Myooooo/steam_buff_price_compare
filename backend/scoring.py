"""可解释的跨市场机会评分。

评分范围 0–100，权重固定，便于不同扫描批次直接比较：
- 折价 50 分：discount <= 0.65 满分，>= 1.00 为 0，区间内线性变化。
- BUFF 在售 15 分：按 log1p 归一化，500 件封顶。
- Steam 在售 20 分：按 log1p 归一化，1000 件封顶。
- BUFF 买一/卖一价差 15 分：<= 2% 满分，>= 20% 为 0。
"""
from __future__ import annotations

import math
from typing import Optional


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def spread_pct(sell_price: Optional[float], buy_price: Optional[float]) -> Optional[float]:
    """BUFF 买一/卖一价差系数 = (卖一 - 买一) / 卖一。"""
    if sell_price is None or buy_price is None or sell_price <= 0 or buy_price <= 0:
        return None
    return round(max(0.0, (sell_price - buy_price) / sell_price), 4)


def _log_liquidity(count: Optional[int], cap: int) -> float:
    if count is None or count <= 0:
        return 0.0
    return _clamp(math.log1p(count) / math.log1p(cap))


def opportunity_score(
    discount: Optional[float],
    buff_sell_num: Optional[int],
    steam_sell_num: Optional[int],
    spread: Optional[float],
) -> Optional[float]:
    """计算 0–100 分机会评分；无有效折价时不评分。"""
    if discount is None or discount <= 0:
        return None

    discount_quality = _clamp((1.0 - discount) / 0.35)
    buff_liquidity = _log_liquidity(buff_sell_num, 500)
    steam_liquidity = _log_liquidity(steam_sell_num, 1000)
    spread_quality = 0.0 if spread is None else _clamp((0.20 - spread) / 0.18)

    score = (
        50.0 * discount_quality
        + 15.0 * buff_liquidity
        + 20.0 * steam_liquidity
        + 15.0 * spread_quality
    )
    return round(score, 1)
