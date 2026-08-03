"""Steam 市场价格获取 + 手续费/净价/折价纯函数。

手续费模型（CS2，2013 年至今未变）：Steam 平台 5% + 游戏 10% = 15%，
从买家支付价中扣除，每项手续费向上取整到分、最低 ¥0.01。
    net = price - fee(price, steam_pct) - fee(price, game_pct)

Steam Community 的 ``market/priceoverview`` 是网页内部接口，2026-08 起即使使用
浏览器 TLS 指纹也会稳定返回 429。价格查询改走仍可用的市场搜索 JSON 接口；匿名
会话可能固定返回 USD，因此用 BUFF 随商品同步的 Steam USD/CNY 参考对换算人民币。
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Optional

# macOS + conda Python 的已知问题：curl_cffi 的 _wrapper.so 依赖 CoreFoundation
# 符号，需先 import requests 让系统框架进入进程命名空间，否则 ImportError。
# 在正常 CPython 上这只是个无害的额外 import。
import requests  # noqa: F401
from curl_cffi import requests as cffi_requests

logger = logging.getLogger("steam")

MARKET_SEARCH_URL = "https://steamcommunity.com/market/search/render/"
RATE_LIMIT_COOLDOWN_SEC = 60.0

# 解析 "¥ 284.00" / "¥1,234.56" / "1,234.56" 这类价格字符串
_PRICE_RE = re.compile(r"[\d.]+")


@dataclass
class SteamPrice:
    lowest: float  # 最低在售价（CNY）
    sell_listings: Optional[int]  # 当前在售条目数；参考价回退时未知
    success: bool
    source: str = "steam_search"


class SteamRateLimitedError(RuntimeError):
    """Steam 429 限流；扫描器负责展示倒计时并在冷却后重试。"""

    def __init__(self, market_hash_name: str, retry_after_sec: float):
        self.market_hash_name = market_hash_name
        self.retry_after_sec = max(0.0, retry_after_sec)
        super().__init__(
            f"Steam 价格查询受限，{self.retry_after_sec:.0f} 秒后重试: {market_hash_name}"
        )


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
    """创建带稳定浏览器特征的 Steam HTTP 会话。"""
    return cffi_requests.AsyncSession(
        impersonate="chrome",
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            # market_hash_name 是英文；中文搜索语言会把部分英文皮肤名当作无效词，
            # 导致 StatTrak™/纪念品精确款落出前 10 条结果。
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://steamcommunity.com/market/search?appid=730",
            "X-Requested-With": "XMLHttpRequest",
        },
    )


def _reference_price(reference_cny: Optional[float]) -> Optional[SteamPrice]:
    """把 BUFF 同步的 Steam CNY 参考价包装成降级结果。"""
    if reference_cny is None or reference_cny <= 0:
        return None
    return SteamPrice(
        lowest=round(reference_cny, 2),
        sell_listings=None,
        success=True,
        source="buff_steam_reference",
    )


def _exact_search_result(payload: dict, market_hash_name: str) -> Optional[dict]:
    """从搜索结果中只接受 market_hash_name 完全一致的商品。"""
    if not payload.get("success"):
        return None
    for result in payload.get("results") or []:
        result_name = result.get("hash_name")
        if not result_name:
            result_name = (result.get("asset_description") or {}).get("market_hash_name")
        if result_name == market_hash_name:
            return result
    return None


def _search_price_cny(
    result: dict,
    *,
    reference_usd: Optional[float],
    reference_cny: Optional[float],
    usd_to_cny: Optional[float],
) -> Optional[float]:
    """解析搜索价；匿名接口返回 USD 时按 Steam 参考价格对换算 CNY。"""
    price_text = str(result.get("sell_price_text") or "")
    price = parse_price_str(price_text)
    if price is None or price <= 0:
        return None
    if "CNY" in price_text.upper():
        return round(price, 2)

    ratio = None
    if reference_usd and reference_usd > 0 and reference_cny and reference_cny > 0:
        ratio = reference_cny / reference_usd
    elif usd_to_cny and usd_to_cny > 0:
        ratio = usd_to_cny
    if "USD" in price_text.upper() and ratio:
        return round(price * ratio, 2)
    return None


def _retry_after_seconds(headers: object) -> int:
    try:
        return int(headers.get("Retry-After", "0") or 0)  # type: ignore[union-attr]
    except (AttributeError, TypeError, ValueError):
        return 0


def _listing_count(result: dict) -> Optional[int]:
    try:
        value = int(result.get("sell_listings"))
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


async def get_price(
    session: "cffi_requests.AsyncSession",
    market_hash_name: str,
    appid: int = 730,
    currency: int = 23,
    delay_sec: float = 0.5,
    max_retries: int = 3,
    reference_usd: Optional[float] = None,
    reference_cny: Optional[float] = None,
    allow_reference_fallback: bool = True,
    rate_limit_mode: str = "wait_retry",
) -> Optional[SteamPrice]:
    """获取某饰品的 Steam 市场价和在售数；失败时可返回 BUFF 参考价。

    ``market/search/render`` 当前对匿名请求可能忽略 ``currency=23`` 并返回 USD。
    BUFF 商品数据同时包含同一 Steam 参考价的 USD/CNY 值，可据此得到 Steam 使用的
    换算比例。429 默认抛出 ``SteamRateLimitedError``，由扫描器暂停并重试；
    ``rate_limit_mode=buff_fallback`` 时才在冷却期直接使用 BUFF 参考价。
    """
    fallback = _reference_price(reference_cny) if allow_reference_fallback else None
    rate_limit_fallback = _reference_price(reference_cny)
    wait_on_rate_limit = rate_limit_mode != "buff_fallback"
    now = time.monotonic()
    cooldown_until = float(getattr(session, "_steam_market_rate_limited_until", 0.0) or 0.0)
    if cooldown_until > now:
        remaining = cooldown_until - now
        if wait_on_rate_limit:
            raise SteamRateLimitedError(market_hash_name, remaining)
        logger.info(
            "Steam 搜索接口冷却中，使用同步参考价: %s（剩余 %.0f 秒）",
            market_hash_name,
            remaining,
        )
        return rate_limit_fallback

    if reference_usd and reference_usd >= 1 and reference_cny and reference_cny > 0:
        setattr(session, "_steam_usd_to_cny", reference_cny / reference_usd)

    params = {
        "query": market_hash_name,
        "start": 0,
        "count": 10,
        "search_descriptions": 0,
        "sort_column": "price",
        "sort_dir": "asc",
        "appid": appid,
        "currency": currency,
        "country": "CN",
        "language": "english",
        "norender": 1,
    }
    for attempt in range(max_retries):
        try:
            if delay_sec > 0:
                await asyncio.sleep(delay_sec)
            resp = await session.get(MARKET_SEARCH_URL, params=params, timeout=15.0)
            if resp.status_code == 429:
                retry_after = _retry_after_seconds(resp.headers)
                cooldown = max(RATE_LIMIT_COOLDOWN_SEC, float(retry_after))
                setattr(session, "_steam_market_rate_limited_until", time.monotonic() + cooldown)
                if wait_on_rate_limit:
                    logger.warning(
                        "Steam 搜索接口 HTTP 429: %s，暂停扫描 %.0f 秒后重试",
                        market_hash_name,
                        cooldown,
                    )
                    raise SteamRateLimitedError(market_hash_name, cooldown)
                logger.warning(
                    "Steam 搜索接口 HTTP 429: %s，暂停直查 %.0f 秒并使用同步参考价",
                    market_hash_name,
                    cooldown,
                )
                return rate_limit_fallback
            if resp.status_code in (500, 502, 503, 504):
                retry_after = _retry_after_seconds(resp.headers)
                wait_seconds = max(2 ** (attempt + 1), retry_after)
                logger.warning(
                    "Steam 搜索暂缓 HTTP %s: %s，%s 秒后重试 (%d/%d)",
                    resp.status_code,
                    market_hash_name,
                    wait_seconds,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(wait_seconds)
                continue
            if resp.status_code != 200:
                logger.warning("Steam 价格请求 HTTP %s: %s", resp.status_code, market_hash_name)
                return fallback
            payload = resp.json()
            result = _exact_search_result(payload, market_hash_name)
            if result is None:
                logger.info("Steam 搜索无精确结果，使用同步参考价: %s", market_hash_name)
                return fallback
            lowest = _search_price_cny(
                result,
                reference_usd=reference_usd,
                reference_cny=reference_cny,
                usd_to_cny=getattr(session, "_steam_usd_to_cny", None),
            )
            if lowest is None:
                logger.info("Steam 搜索价币种无法换算，使用同步参考价: %s", market_hash_name)
                return fallback
            return SteamPrice(
                lowest=lowest,
                sell_listings=_listing_count(result),
                success=True,
            )
        except SteamRateLimitedError:
            raise
        except Exception as e:  # noqa: BLE001 - curl_cffi 异常类型繁杂，统一退避
            logger.debug("Steam 价格请求异常(%s): %s", market_hash_name, e)
            if attempt + 1 < max_retries:
                await asyncio.sleep(2 ** attempt)
    logger.warning("Steam 价格查询重试耗尽: %s", market_hash_name)
    return fallback
