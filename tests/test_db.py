"""数据筛选、新鲜度标签和深度扫描检查点测试。"""
import sqlite3

from backend import db


def make_conn():
    conn = sqlite3.connect(":memory:")
    db.init_db(conn)
    return conn


def item(name, scan_id, **overrides):
    value = {
        "market_hash_name": name,
        "game": "csgo",
        "display_name": name,
        "weapon": "AK-47",
        "item_type": "步枪",
        "exterior": "久经沙场",
        "buff_price": 100.0,
        "steam_price": 200.0,
        "steam_net": 170.0,
        "discount": 0.588,
        "steam_sell_num": 100,
        "steam_price_source": "steam_search",
        "spread_pct": 0.05,
        "score": 60.0,
        "source": "keyword",
        "updated_at": db.now_iso(),
    }
    value.update(overrides)
    return value, scan_id


def test_query_items_filters_sorts_and_marks_freshness():
    conn = make_conn()
    first, first_scan = item(
        "AK-47 | Redline", 1, buff_price=120.0, steam_price=90.0,
        steam_net=76.0, discount=0.8, score=45.0,
    )
    second, second_scan = item(
        "M4A1-S | Printstream",
        2,
        weapon="M4A1-S",
        exterior="崭新出厂",
        buff_price=80.0,
        discount=0.5,
        score=88.0,
    )
    db.save_item(conn, first, scan_id=first_scan)
    db.save_item(conn, second, scan_id=second_scan)

    result = db.query_items(
        conn,
        query="Print",
        weapon="M4A1-S",
        min_price=50,
        max_price=100,
        data_state="latest",
        sort_by="buff_price",
        sort_order="desc",
    )

    assert result["count"] == 1
    assert result["items"][0]["market_hash_name"] == "M4A1-S | Printstream"
    assert result["items"][0]["data_state"] == "latest"
    cached = db.query_items(conn, data_state="cached")
    assert [row["market_hash_name"] for row in cached["items"]] == ["AK-47 | Redline"]
    steam_filtered = db.query_items(conn, price_basis="steam_price", min_price=150, max_price=220)
    assert [row["market_hash_name"] for row in steam_filtered["items"]] == ["M4A1-S | Printstream"]
    net_filtered = db.query_items(conn, price_basis="steam_net", max_price=100)
    assert [row["market_hash_name"] for row in net_filtered["items"]] == ["AK-47 | Redline"]
    opportunity_filtered = db.query_items(conn, min_score=80, max_discount=0.7)
    assert [row["market_hash_name"] for row in opportunity_filtered["items"]] == ["M4A1-S | Printstream"]
    assert [row["market_hash_name"] for row in db.query_items(conn)["items"]] == [
        "M4A1-S | Printstream", "AK-47 | Redline",
    ]
    assert "M4A1-S" in db.item_facets(conn)["weapons"]
    conn.close()


def test_item_assets_are_stored_separately_and_refreshed_by_url():
    conn = make_conn()
    value, scan_id = item(
        "AK-47 | Redline",
        1,
        icon_url="https://example.com/one.webp",
        icon_data=b"webp-data",
        icon_mime="image/webp",
    )
    db.save_item(conn, value, scan_id=scan_id)

    asset = db.get_item_asset(conn, value["market_hash_name"])
    assert asset["mime_type"] == "image/webp"
    assert asset["image_data"] == b"webp-data"
    assert db.missing_asset_names(conn, [value]) == set()
    changed = {**value, "icon_url": "https://example.com/two.webp"}
    assert db.missing_asset_names(conn, [changed]) == {value["market_hash_name"]}
    conn.close()


def test_deep_scan_checkpoint_resumes_and_completes():
    conn = make_conn()
    progress = db.start_deep_cycle(conn, "csgo")
    assert progress["generation"] == 1
    normalized = [{
        "market_hash_name": "AK-47 | Redline",
        "game": "csgo",
        "buff_goods_id": 100,
        "display_name": "AK-47 | 红线",
        "buff_price": 100.0,
        "steam_reference_usd": 40.0,
        "steam_reference_cny": 270.0,
        "source": "deepscan",
        "updated_at": db.now_iso(),
    }]
    db.save_deep_index_page(
        conn,
        "csgo",
        1,
        normalized,
        next_page=2,
        total_pages=3,
    )

    resumed = db.start_deep_cycle(conn, "csgo")
    assert resumed["generation"] == 1
    assert resumed["next_page"] == 2
    assert resumed["indexed_count"] == 1
    repeated = db.save_deep_index_page(
        conn,
        "csgo",
        1,
        normalized,
        next_page=3,
        total_pages=3,
    )
    assert repeated["indexed_count"] == 1

    db.set_deep_phase(conn, "csgo", 1, "pricing")
    candidate = db.next_deep_item(conn, "csgo", 1)
    assert candidate["market_hash_name"] == "AK-47 | Redline"
    assert candidate["steam_reference_usd"] == 40.0
    assert candidate["steam_reference_cny"] == 270.0
    candidate.update(
        steam_price=200.0,
        steam_sell_num=10,
        steam_price_source="steam_search",
        steam_net=170.0,
        discount=0.588,
        spread_pct=0.05,
        score=70.0,
        updated_at=db.now_iso(),
    )
    db.finish_deep_item(conn, candidate, 1, 9, success=True)
    assert db.next_deep_item(conn, "csgo", 1) is None
    assert db.get_deep_progress(conn, "csgo")["priced_count"] == 1

    db.set_deep_phase(conn, "csgo", 1, "complete")
    next_cycle = db.start_deep_cycle(conn, "csgo")
    assert next_cycle["generation"] == 2
    assert next_cycle["next_page"] == 1
    conn.close()


def test_init_db_migrates_legacy_items_table():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE items (
           market_hash_name TEXT PRIMARY KEY, game TEXT, buff_price REAL,
           buff_sell_num INTEGER, buff_buy_num INTEGER, buff_buy_max_price REAL,
           steam_price REAL, steam_volume INTEGER, steam_net REAL, discount REAL,
           source TEXT, updated_at TEXT, first_seen_at TEXT)"""
    )
    conn.execute(
        """CREATE TABLE deep_scan_index (
           game TEXT, market_hash_name TEXT, generation INTEGER,
           priced_generation INTEGER, PRIMARY KEY (game, market_hash_name))"""
    )

    db.init_db(conn)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    assert {
        "buff_goods_id", "weapon", "exterior", "last_scan_id", "steam_sell_num",
        "steam_price_source", "spread_pct", "score",
    } <= columns
    deep_columns = {row[1] for row in conn.execute("PRAGMA table_info(deep_scan_index)")}
    assert {"steam_reference_usd", "steam_reference_cny"} <= deep_columns
    conn.close()
