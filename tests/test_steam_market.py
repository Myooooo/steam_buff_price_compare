"""Steam 新市场搜索端点、币种换算与限流降级测试。"""
import asyncio

import pytest

from backend.services import steam


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def get(self, url, params=None, timeout=15):
        self.calls.append((url, params, timeout))
        return self.responses.pop(0)


def search_payload(name, price_text, *, sell_listings=120, extra_results=None):
    results = list(extra_results or [])
    results.append({
        "hash_name": name,
        "sell_price_text": price_text,
        "sell_listings": sell_listings,
    })
    return {"success": True, "results": results}


def test_search_price_converts_live_usd_with_buff_steam_reference_pair():
    async def scenario():
        session = FakeSession([
            FakeResponse(200, search_payload(
                "AK-47 | Redline (Field-Tested)",
                "$41.86 USD",
                extra_results=[{"hash_name": "StatTrak™ AK-47 | Redline (Field-Tested)",
                                "sell_price_text": "$96.56 USD"}],
            ))
        ])
        result = await steam.get_price(
            session,
            "AK-47 | Redline (Field-Tested)",
            delay_sec=0,
            reference_usd=42.52,
            reference_cny=287.07,
        )

        assert result is not None
        assert result.source == "steam_search"
        assert result.lowest == pytest.approx(41.86 * (287.07 / 42.52), abs=0.01)
        assert result.sell_listings == 120
        assert session.calls[0][0] == steam.MARKET_SEARCH_URL
        assert session.calls[0][1]["query"] == "AK-47 | Redline (Field-Tested)"
        assert session.calls[0][1]["language"] == "english"

    asyncio.run(scenario())


def test_steam_session_searches_english_market_hash_names():
    session = steam.create_steam_session()
    try:
        assert session.headers["Accept-Language"].startswith("en-US")
    finally:
        asyncio.run(session.close())


def test_search_price_uses_direct_cny_response():
    async def scenario():
        session = FakeSession([
            FakeResponse(200, search_payload("Sticker | Test", "¥ 12.34 CNY"))
        ])
        result = await steam.get_price(session, "Sticker | Test", delay_sec=0)
        assert result is not None
        assert result.lowest == 12.34

    asyncio.run(scenario())


def test_search_requires_exact_name_and_falls_back_to_reference():
    async def scenario():
        session = FakeSession([
            FakeResponse(200, search_payload("StatTrak™ AK-47 | Redline", "$90.00 USD"))
        ])
        result = await steam.get_price(
            session,
            "AK-47 | Redline",
            delay_sec=0,
            reference_cny=280.0,
        )
        assert result is not None
        assert result.lowest == 280.0
        assert result.source == "buff_steam_reference"
        assert result.sell_listings is None

    asyncio.run(scenario())


def test_reference_fallback_can_be_disabled():
    async def scenario():
        session = FakeSession([FakeResponse(429, None)])
        result = await steam.get_price(
            session,
            "No Fallback",
            delay_sec=0,
            reference_cny=100.0,
            allow_reference_fallback=False,
        )
        assert result is None

    asyncio.run(scenario())


def test_429_enters_cooldown_and_does_not_retry_each_item():
    async def scenario():
        session = FakeSession([FakeResponse(429, None)])
        first = await steam.get_price(
            session,
            "First Item",
            delay_sec=0,
            reference_cny=100.0,
        )
        second = await steam.get_price(
            session,
            "Second Item",
            delay_sec=0,
            reference_cny=200.0,
        )

        assert first is not None and first.lowest == 100.0
        assert second is not None and second.lowest == 200.0
        assert len(session.calls) == 1

    asyncio.run(scenario())
