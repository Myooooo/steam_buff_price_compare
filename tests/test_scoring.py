"""跨市场机会评分测试。"""
from backend.scoring import opportunity_score, spread_pct


def test_spread_pct_uses_buff_best_bid_and_ask():
    assert spread_pct(100.0, 95.0) == 0.05
    assert spread_pct(100.0, 101.0) == 0.0
    assert spread_pct(100.0, None) is None


def test_score_is_bounded_and_rewards_better_opportunities():
    strong = opportunity_score(0.65, 500, 1000, 0.02)
    weak = opportunity_score(0.95, 5, 10, 0.18)
    assert strong == 100.0
    assert weak is not None
    assert 0 <= weak < strong


def test_missing_steam_depth_reduces_but_does_not_remove_score():
    direct = opportunity_score(0.80, 100, 500, 0.05)
    fallback = opportunity_score(0.80, 100, None, 0.05)
    assert direct is not None and fallback is not None
    assert direct > fallback > 0


def test_score_requires_discount():
    assert opportunity_score(None, 100, 100, 0.05) is None
