"""跨市场机会评分测试。"""
from backend.scoring import opportunity_score, opportunity_score_breakdown, spread_pct


def test_spread_pct_uses_buff_best_bid_and_ask():
    assert spread_pct(100.0, 95.0) == 0.05
    assert spread_pct(100.0, 101.0) == 0.0
    assert spread_pct(100.0, None) is None


def test_score_is_bounded_and_rewards_better_opportunities():
    strong = opportunity_score(0.65, 1000, 1000, 0.02)
    weak = opportunity_score(0.95, 5, 10, 0.18)
    assert strong == 100.0
    assert weak is not None
    assert 0 <= weak < strong


def test_missing_steam_depth_uses_buff_without_zero_penalty():
    fallback = opportunity_score(0.80, 100, None, 0.05)
    same_depth = opportunity_score(0.80, 100, 100, 0.05)
    richer_steam = opportunity_score(0.80, 100, 1000, 0.05)
    thinner_steam = opportunity_score(0.80, 100, 1, 0.05)
    assert fallback == same_depth
    assert richer_steam is not None and thinner_steam is not None and fallback is not None
    assert richer_steam > fallback > thinner_steam > 0
    assert richer_steam - fallback <= 4
    assert fallback - thinner_steam <= 4


def test_score_breakdown_explains_liquidity_source_and_points():
    fallback = opportunity_score_breakdown(0.80, 100, None, 0.05)
    direct = opportunity_score_breakdown(0.80, 100, 1000, 0.05)
    assert fallback is not None and direct is not None
    assert fallback["depth_source"] == "buff"
    assert direct["depth_source"] == "buff_steam"
    assert fallback["score"] == opportunity_score(0.80, 100, None, 0.05)
    assert fallback["liquidity_points"] == round(
        fallback["spread_points"] + fallback["depth_points"], 1
    )


def test_score_requires_discount():
    assert opportunity_score(None, 100, 100, 0.05) is None
