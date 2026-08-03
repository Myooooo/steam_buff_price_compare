"""Steam 市场价格获取 + 手续费/净价/折价纯函数。

手续费模型（CS2，2013 年至今未变）：Steam 平台 5% + 游戏 10% = 15%，
从买家支付价中扣除，每项手续费向上取整到分、最低 ¥0.01。
    net = price - fee(price, steam_pct) - fee(price, game_pct)

注意：Steam 的 WAF 会按 TLS 指纹限流 —— httpx/requests 的指纹会被 429，
浏览器指纹（curl_cffi impersonate=chrome）可通过。因此 Steam 请求统一走
curl_cffi 的 AsyncSession，不用 httpx。
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass
from typing import Optional

# macOS + conda Python 的已知问题：curl_cffi 的 _wrapper.so 依赖 CoreFoundation
# 符号，需先 import requests 让系统框架进入进程命名空间，否则 ImportError。
# 在正常 CPython 上这只是个无害的额外 import。
import requests  # noqa: F401
from curl_cffi import requests as cffi_requests

logger = logging.getLogger("steam")

# 解析 "¥ 284.00" / "¥1,234.56" / "1,234.56" 这类价格字符串
_PRICE_RE = re.compile(r"[\d.]+")


@dataclass
class SteamPrice:
    lowest: float  # 最低在售价（CNY）
    volume: int  # 近 24h 成交量，可能为 0
    success: bool


def parse_price_str(s: Optional[str]) -> Optional[float]:
    """把 Steam 价格字符串解析成 float；None/空/解析失败返回 None。"""
    if not s:
        return None
    m = _PRICE_RE.search(str(s).replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def fee(price: float, pct: float, fee_min: float = 0.01, fee_round: str = "cent") -> float:
    """单项手续费 = max(fee_min, ceil(price * pct%))。

    fee_round="cent" 时向上取整到分（官方口径）；"yuan" 时向上取整到元。
    """
    if price <= 0 or pct <= 0:
        return 0.0
    raw = price * pct / 100.0
    if fee_round == "yuan":
        return max(fee_min, math.ceil(raw))
    return max(fee_min, math.ceil(raw * 100) / 100.0)


def steam_net(
    price: float,
    steam_pct: float = 5.0,
    game_pct: float = 10.0,
    fee_min: float = 0.01,
    fee_round: str = "cent",
) -> float:
    """Steam 卖出到手余额 = price - 两项手续费。"""
    if price <= 0:
        return 0.0
    f = fee(price, steam_pct, fee_min, fee_round) + fee(price, game_pct, fee_min, fee_round)
    return round(price - f, 2)


def discount(buff_price: float, steam_net_price: float) -> Optional[float]:
    """折价系数 = buff买入价 / steam到手余额。<=1 表示可赚（越小越赚）。"""
    if buff_price is None or steam_net_price is None or steam_net_price <= 0:
        return None
    return round(buff_price / steam_net_price, 4)


def create_steam_session() -> "cffi_requests.AsyncSession":
    """创建浏览器指纹的 Steam HTTP 会话（impersonate chrome，绕开 WAF 限流）。"""
    return cffi_requests.AsyncSession(impersonate="chrome")


def _parse_payload(payload: dict) -> SteamPrice:
    """解析 priceoverview 的 JSON 响应。"""
    success = bool(payload.get("success"))
    lowest = parse_price_str(payload.get("lowest_price"))
    try:
        volume = int(payload.get("volume") or 0)
    except (TypeError, ValueError):
        volume = 0
    return SteamPrice(
        lowest=lowest if lowest is not None else 0.0,
        volume=volume,
        success=success and lowest is not None,
    )


async def get_price(
    session: "cffi_requests.AsyncSession",
    market_hash_name: str,
    appid: int = 730,
    currency: int = 23,
    delay_sec: float = 0.5,
    max_retries: int = 3,
) -> Optional[SteamPrice]:
    """获取某饰品的 Steam 市场价（匿名即可）。失败返回 None，不抛异常。"""
    params = {
        "market_hash_name": market_hash_name,
        "appid": appid,
        "currency": currency,
    }
    url = "https://steamcommunity.com/market/priceoverview/"
    for attempt in range(max_retries):
        try:
            if delay_sec > 0:
                await asyncio.sleep(delay_sec)
            resp = await session.get(url, params=params, timeout=15.0)
            if resp.status_code in (429, 500, 502, 503, 504):
                retry_after = int(resp.headers.get("Retry-After", "0")) if resp.headers else 0
                await asyncio.sleep(max(2 ** (attempt + 1), retry_after))
                continue
            if resp.status_code != 200:
                logger.warning("Steam 价格请求 HTTP %s: %s", resp.status_code, market_hash_name)
                return None
            payload = resp.json()
            return _parse_payload(payload)
        except Exception as e:  # noqa: BLE001 - curl_cffi 异常类型繁杂，统一退避
            logger.debug("Steam 价格请求异常(%s): %s", market_hash_name, e)
            await asyncio.sleep(2 ** attempt)
    return None
