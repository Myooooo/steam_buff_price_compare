"""单元测试：手续费/折价纯函数、价格解析、Buff 数据归一化。"""
from backend.services import steam
from backend.services.buff import normalize_item, to_float


# ---------- fee / steam_net ----------

def test_fee_cent_rounding():
    # 官方口径（到分）：284 -> 5% = 14.20, 10% = 28.40
    assert steam.fee(284, 5) == 14.2
    assert steam.fee(284, 10) == 28.4


def test_fee_yuan_rounding():
    assert steam.fee(284, 5, fee_round="yuan") == 15
    assert steam.fee(284, 10, fee_round="yuan") == 29


def test_steam_net_official():
    assert steam.steam_net(284, 5, 10) == 241.4
    assert steam.steam_net(100, 5, 10) == 85.0
    # 官方示例：0.50 -> 0.03 + 0.05 = 0.42
    assert steam.steam_net(0.5, 5, 10) == 0.42


def test_steam_net_yuan():
    assert steam.steam_net(284, 5, 10, fee_round="yuan") == 240.0


def test_steam_net_zero_price():
    assert steam.steam_net(0, 5, 10) == 0.0


def test_fee_min_floor():
    # 0.05 -> 两项费率都触发 0.01 地板
    assert steam.steam_net(0.05, 5, 10) == 0.03


# ---------- discount ----------

def test_discount():
    assert steam.discount(200, 241.4) == round(200 / 241.4, 4)
    assert steam.discount(241.4, 241.4) == 1.0
    assert steam.discount(250, 241.4) > 1.0


def test_discount_invalid():
    assert steam.discount(200, 0) is None
    assert steam.discount(None, 100) is None


# ---------- price parsing ----------

def test_parse_price_str():
    assert steam.parse_price_str("¥ 284.00") == 284.0
    assert steam.parse_price_str("¥1,234.56") == 1234.56
    assert steam.parse_price_str("1,234.56") == 1234.56
    assert steam.parse_price_str(None) is None
    assert steam.parse_price_str("N/A") is None


# ---------- Buff 归一化 ----------

def test_normalize_item():
    raw = {
        "id": 12345,
        "name": "AK-47 | 红线 (久经沙场)",
        "market_hash_name": "AK-47 | Redline (Field-Tested)",
        "sell_min_price": "200.0",
        "sell_num": 5,
        "buy_max_price": "190.0",
        "buy_num": 1,
        "goods_info": {
            "icon_url": "https://example.com/icon.webp",
            "info": {"tags": {
                "type": {"localized_name": "步枪"},
                "weapon": {"localized_name": "AK-47"},
                "exterior": {"localized_name": "久经沙场"},
            }},
        },
    }
    item = normalize_item(raw)
    assert item["market_hash_name"] == raw["market_hash_name"]
    assert item["buff_price"] == 200.0
    assert item["buff_sell_num"] == 5
    assert item["buff_buy_max_price"] == 190.0
    assert item["source"] == "keyword"
    assert item["buff_goods_id"] == 12345
    assert item["buff_url"].endswith("goods_id=12345&from=market#tab=selling")
    assert item["weapon"] == "AK-47"
    assert item["item_type"] == "步枪"
    assert item["exterior"] == "久经沙场"
    assert item["updated_at"]


def test_normalize_item_null_price():
    item = normalize_item({"market_hash_name": "X", "sell_min_price": None})
    assert item["buff_price"] is None
    item2 = normalize_item({"market_hash_name": "Y", "sell_min_price": "0.0"})
    assert item2["buff_price"] is None


def test_to_float():
    assert to_float("12.5") == 12.5
    assert to_float(12) == 12.0
    assert to_float(None) is None
    assert to_float("0") is None
    assert to_float("0.0") is None
    assert to_float(-1) is None
    assert to_float("abc") is None
