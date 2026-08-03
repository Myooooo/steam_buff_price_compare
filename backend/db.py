"""SQLite 数据层：饰品快照、价格历史、扫描记录与深度扫描检查点。

单一连接（check_same_thread=False）+ 应用持有的 asyncio.Lock 序列化写入。
SQLite 本身串行写，应用内也只有 scanner 一个写者，足够安全。
"""
from __future__ import annotations

import datetime
import sqlite3
from typing import Any, Optional

from .scoring import opportunity_score, spread_pct

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  market_hash_name TEXT PRIMARY KEY,
  game TEXT NOT NULL DEFAULT 'csgo',
  buff_goods_id INTEGER,
  display_name TEXT,
  buff_url TEXT,
  icon_url TEXT,
  item_type TEXT,
  weapon TEXT,
  category TEXT,
  exterior TEXT,
  rarity TEXT,
  quality TEXT,
  buff_price REAL,
  buff_sell_num INTEGER,
  buff_buy_num INTEGER,
  buff_buy_max_price REAL,
  steam_price REAL,
  steam_volume INTEGER,
  steam_sell_num INTEGER,
  steam_price_source TEXT,
  steam_net REAL,
  discount REAL,
  spread_pct REAL,
  score REAL,
  source TEXT NOT NULL DEFAULT 'keyword',
  last_scan_id INTEGER,
  updated_at TEXT NOT NULL,
  first_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_discount ON items(discount);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source);

CREATE TABLE IF NOT EXISTS item_assets (
  market_hash_name TEXT PRIMARY KEY,
  icon_url TEXT,
  mime_type TEXT NOT NULL,
  image_data BLOB NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market_hash_name TEXT NOT NULL,
  ts TEXT NOT NULL,
  buff_price REAL,
  steam_price REAL,
  steam_net REAL,
  discount REAL
);
CREATE INDEX IF NOT EXISTS idx_history_name_ts ON price_history(market_hash_name, ts);

CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  mode TEXT NOT NULL,
  item_count INTEGER,
  status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deep_scan_index (
  game TEXT NOT NULL,
  market_hash_name TEXT NOT NULL,
  generation INTEGER NOT NULL,
  buff_goods_id INTEGER,
  display_name TEXT,
  buff_url TEXT,
  icon_url TEXT,
  item_type TEXT,
  weapon TEXT,
  category TEXT,
  exterior TEXT,
  rarity TEXT,
  quality TEXT,
  buff_price REAL,
  buff_sell_num INTEGER,
  buff_buy_num INTEGER,
  buff_buy_max_price REAL,
  steam_reference_usd REAL,
  steam_reference_cny REAL,
  indexed_at TEXT NOT NULL,
  priced_generation INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (game, market_hash_name)
);
CREATE INDEX IF NOT EXISTS idx_deep_index_generation
  ON deep_scan_index(game, generation, priced_generation);

CREATE TABLE IF NOT EXISTS deep_scan_progress (
  game TEXT PRIMARY KEY,
  generation INTEGER NOT NULL,
  phase TEXT NOT NULL,
  next_page INTEGER NOT NULL DEFAULT 1,
  total_pages INTEGER,
  indexed_count INTEGER NOT NULL DEFAULT 0,
  priced_count INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);
"""

_ITEM_MIGRATIONS = {
    "buff_goods_id": "INTEGER",
    "display_name": "TEXT",
    "buff_url": "TEXT",
    "icon_url": "TEXT",
    "item_type": "TEXT",
    "weapon": "TEXT",
    "category": "TEXT",
    "exterior": "TEXT",
    "rarity": "TEXT",
    "quality": "TEXT",
    "steam_sell_num": "INTEGER",
    "steam_price_source": "TEXT",
    "spread_pct": "REAL",
    "score": "REAL",
    "last_scan_id": "INTEGER",
}

_DEEP_INDEX_MIGRATIONS = {
    "steam_reference_usd": "REAL",
    "steam_reference_cny": "REAL",
}


def now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _upsert_asset(conn: sqlite3.Connection, item: dict[str, Any]) -> None:
    image_data = item.get("icon_data")
    mime_type = item.get("icon_mime")
    if not isinstance(image_data, bytes) or not image_data or not mime_type:
        return
    conn.execute(
        """INSERT INTO item_assets
           (market_hash_name, icon_url, mime_type, image_data, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(market_hash_name) DO UPDATE SET
             icon_url = excluded.icon_url,
             mime_type = excluded.mime_type,
             image_data = excluded.image_data,
             updated_at = excluded.updated_at""",
        (
            item["market_hash_name"],
            item.get("icon_url"),
            mime_type,
            image_data,
            item.get("updated_at", now_iso()),
        ),
    )


def save_assets(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    """保存已下载的图片资产，返回实际带图片数据的商品数。"""
    assets = [item for item in items if isinstance(item.get("icon_data"), bytes) and item["icon_data"]]
    with conn:
        for item in assets:
            _upsert_asset(conn, item)
    return len(assets)


def missing_asset_names(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> set[str]:
    """找出没有本地图片，或图片 URL 已变化的商品。"""
    candidates = {
        item["market_hash_name"]: item.get("icon_url")
        for item in items
        if item.get("market_hash_name") and item.get("icon_url")
    }
    if not candidates:
        return set()
    placeholders = ",".join("?" for _ in candidates)
    rows = conn.execute(
        f"SELECT market_hash_name, icon_url FROM item_assets WHERE market_hash_name IN ({placeholders})",
        list(candidates),
    ).fetchall()
    cached = {row[0]: row[1] for row in rows}
    return {name for name, url in candidates.items() if cached.get(name) != url}


def get_item_asset(conn: sqlite3.Connection, market_hash_name: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT mime_type, image_data, updated_at FROM item_assets WHERE market_hash_name = ?",
        (market_hash_name,),
    ).fetchone()
    return dict(row) if row else None


def init_db(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    for column, definition in _ITEM_MIGRATIONS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE items ADD COLUMN {column} {definition}")
    deep_existing = {row[1] for row in conn.execute("PRAGMA table_info(deep_scan_index)")}
    for column, definition in _DEEP_INDEX_MIGRATIONS.items():
        if column not in deep_existing:
            conn.execute(f"ALTER TABLE deep_scan_index ADD COLUMN {column} {definition}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_filters ON items(weapon, item_type, exterior)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_item_type ON items(item_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_exterior ON items(exterior)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_score ON items(score)")
    rows = conn.execute(
        """SELECT market_hash_name, discount, buff_sell_num, steam_sell_num,
                  buff_price, buff_buy_max_price
           FROM items WHERE discount IS NOT NULL AND (score IS NULL OR spread_pct IS NULL)"""
    ).fetchall()
    for row in rows:
        spread = spread_pct(row[4], row[5])
        score = opportunity_score(row[1], row[2], row[3], spread)
        conn.execute(
            "UPDATE items SET spread_pct = ?, score = ? WHERE market_hash_name = ?",
            (spread, score, row[0]),
        )
    conn.commit()


def _upsert_item(conn: sqlite3.Connection, item: dict[str, Any], scan_id: Optional[int] = None) -> None:
    """按 market_hash_name 插入或更新饰品快照（保留 first_seen_at）。"""
    conn.execute(
        """INSERT INTO items
           (market_hash_name, game, buff_goods_id, display_name, buff_url, icon_url,
            item_type, weapon, category, exterior, rarity, quality,
            buff_price, buff_sell_num, buff_buy_num,
            buff_buy_max_price, steam_price, steam_volume, steam_sell_num,
            steam_price_source, steam_net, discount, spread_pct, score,
            source, last_scan_id, updated_at, first_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(market_hash_name) DO UPDATE SET
             game = excluded.game,
             buff_goods_id = COALESCE(excluded.buff_goods_id, items.buff_goods_id),
             display_name = COALESCE(excluded.display_name, items.display_name),
             buff_url = COALESCE(excluded.buff_url, items.buff_url),
             icon_url = COALESCE(excluded.icon_url, items.icon_url),
             item_type = COALESCE(excluded.item_type, items.item_type),
             weapon = COALESCE(excluded.weapon, items.weapon),
             category = COALESCE(excluded.category, items.category),
             exterior = COALESCE(excluded.exterior, items.exterior),
             rarity = COALESCE(excluded.rarity, items.rarity),
             quality = COALESCE(excluded.quality, items.quality),
             buff_price = excluded.buff_price,
             buff_sell_num = excluded.buff_sell_num,
             buff_buy_num = excluded.buff_buy_num,
             buff_buy_max_price = excluded.buff_buy_max_price,
             steam_price = excluded.steam_price,
             steam_volume = excluded.steam_volume,
             steam_sell_num = excluded.steam_sell_num,
             steam_price_source = excluded.steam_price_source,
             steam_net = excluded.steam_net,
             discount = excluded.discount,
             spread_pct = excluded.spread_pct,
             score = excluded.score,
             source = excluded.source,
             last_scan_id = excluded.last_scan_id,
             updated_at = excluded.updated_at""",
        (
            item["market_hash_name"],
            item.get("game", "csgo"),
            item.get("buff_goods_id"),
            item.get("display_name"),
            item.get("buff_url"),
            item.get("icon_url"),
            item.get("item_type"),
            item.get("weapon"),
            item.get("category"),
            item.get("exterior"),
            item.get("rarity"),
            item.get("quality"),
            item.get("buff_price"),
            item.get("buff_sell_num"),
            item.get("buff_buy_num"),
            item.get("buff_buy_max_price"),
            item.get("steam_price"),
            item.get("steam_volume"),
            item.get("steam_sell_num"),
            item.get("steam_price_source"),
            item.get("steam_net"),
            item.get("discount"),
            item.get("spread_pct"),
            item.get("score"),
            item.get("source", "keyword"),
            scan_id,
            item.get("updated_at", now_iso()),
            item.get("updated_at", now_iso()),
        ),
    )


def _insert_history(conn: sqlite3.Connection, item: dict[str, Any]) -> None:
    conn.execute(
        """INSERT INTO price_history (market_hash_name, ts, buff_price, steam_price, steam_net, discount)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            item["market_hash_name"],
            item.get("updated_at", now_iso()),
            item.get("buff_price"),
            item.get("steam_price"),
            item.get("steam_net"),
            item.get("discount"),
        ),
    )


def save_item(conn: sqlite3.Connection, item: dict[str, Any], scan_id: Optional[int] = None) -> None:
    """原子保存当前快照及对应历史点。"""
    with conn:
        _upsert_asset(conn, item)
        _upsert_item(conn, item, scan_id=scan_id)
        _insert_history(conn, item)


def list_items(
    conn: sqlite3.Connection,
    only_profitable: bool = False,
    source: Optional[str] = None,
) -> list[dict[str, Any]]:
    """按机会评分降序返回全部饰品快照。"""
    sql = "SELECT * FROM items"
    conds: list[str] = []
    args: list[Any] = []
    if only_profitable:
        conds.append("discount IS NOT NULL AND discount > 0 AND discount <= 1.0")
    if source:
        conds.append("source = ?")
        args.append(source)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY score DESC NULLS LAST, discount ASC NULLS LAST, updated_at DESC"
    rows = conn.execute(sql, args).fetchall()
    latest_scan_id = conn.execute("SELECT MAX(last_scan_id) FROM items").fetchone()[0]
    return [_with_data_state(dict(row), latest_scan_id) for row in rows]


def _with_data_state(item: dict[str, Any], latest_scan_id: Optional[int]) -> dict[str, Any]:
    item["data_state"] = (
        "latest" if latest_scan_id is not None and item.get("last_scan_id") == latest_scan_id else "cached"
    )
    return item


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def query_items(
    conn: sqlite3.Connection,
    *,
    query: Optional[str] = None,
    weapon: Optional[str] = None,
    item_type: Optional[str] = None,
    exterior: Optional[str] = None,
    price_basis: str = "buff_price",
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    min_score: Optional[float] = None,
    max_score: Optional[float] = None,
    min_discount: Optional[float] = None,
    max_discount: Optional[float] = None,
    only_profitable: bool = False,
    source: Optional[str] = None,
    data_state: Optional[str] = None,
    sort_by: str = "score",
    sort_order: str = "desc",
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    """服务端筛选、排序和分页，避免全量索引直接塞进浏览器。"""
    latest_scan_id = conn.execute("SELECT MAX(last_scan_id) FROM items").fetchone()[0]
    conds: list[str] = []
    args: list[Any] = []
    if query:
        conds.append("(market_hash_name LIKE ? ESCAPE '\\' OR display_name LIKE ? ESCAPE '\\')")
        pattern = _like_pattern(query.strip())
        args.extend((pattern, pattern))
    for column, value in (("weapon", weapon), ("item_type", item_type), ("exterior", exterior)):
        if value:
            conds.append(f"{column} = ?")
            args.append(value)
    price_columns = {
        "buff_price": "buff_price",
        "steam_price": "steam_price",
        "steam_net": "steam_net",
    }
    price_column = price_columns.get(price_basis, "buff_price")
    if min_price is not None:
        conds.append(f"{price_column} >= ?")
        args.append(min_price)
    if max_price is not None:
        conds.append(f"{price_column} <= ?")
        args.append(max_price)
    for column, minimum, maximum in (
        ("score", min_score, max_score),
        ("discount", min_discount, max_discount),
    ):
        if minimum is not None:
            conds.append(f"{column} >= ?")
            args.append(minimum)
        if maximum is not None:
            conds.append(f"{column} <= ?")
            args.append(maximum)
    if only_profitable:
        conds.append("discount IS NOT NULL AND discount > 0 AND discount <= 1.0")
    if source:
        conds.append("source = ?")
        args.append(source)
    if data_state == "latest":
        conds.append("last_scan_id = ?")
        args.append(latest_scan_id)
    elif data_state == "cached":
        if latest_scan_id is None:
            conds.append("1 = 1")
        else:
            conds.append("(last_scan_id IS NULL OR last_scan_id != ?)")
            args.append(latest_scan_id)

    where = " WHERE " + " AND ".join(conds) if conds else ""
    total = int(conn.execute("SELECT COUNT(*) FROM items" + where, args).fetchone()[0])
    sort_columns = {
        "name": "market_hash_name",
        "buff_price": "buff_price",
        "steam_price": "steam_price",
        "steam_net": "steam_net",
        "discount": "discount",
        "score": "score",
        "spread_pct": "spread_pct",
        "steam_sell_num": "steam_sell_num",
        "steam_volume": "steam_volume",
        "buff_sell_num": "buff_sell_num",
        "updated_at": "updated_at",
    }
    order_column = sort_columns.get(sort_by, "score")
    direction = "DESC" if sort_order.lower() == "desc" else "ASC"
    offset = (page - 1) * page_size
    rows = conn.execute(
        f"SELECT * FROM items{where} "
        f"ORDER BY {order_column} {direction} NULLS LAST, market_hash_name ASC LIMIT ? OFFSET ?",
        [*args, page_size, offset],
    ).fetchall()
    return {
        "items": [_with_data_state(dict(row), latest_scan_id) for row in rows],
        "count": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "latest_scan_id": latest_scan_id,
    }


def item_facets(conn: sqlite3.Connection) -> dict[str, list[str]]:
    def values(column: str) -> list[str]:
        rows = conn.execute(
            f"""SELECT DISTINCT value FROM (
                  SELECT {column} AS value FROM items
                  UNION ALL
                  SELECT {column} AS value FROM deep_scan_index
                ) WHERE value IS NOT NULL AND value != '' ORDER BY value"""
        ).fetchall()
        return [row[0] for row in rows]

    return {
        "weapons": values("weapon"),
        "item_types": values("item_type"),
        "exteriors": values("exterior"),
    }


def get_item(conn: sqlite3.Connection, market_hash_name: str) -> Optional[dict[str, Any]]:
    row = conn.execute("SELECT * FROM items WHERE market_hash_name = ?", (market_hash_name,)).fetchone()
    if not row:
        return None
    return dict(row)


def get_history(
    conn: sqlite3.Connection,
    market_hash_name: str,
    days: int = 30,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    """返回某饰品最近 N 天的价格历史（时间升序）。"""
    since = (datetime.datetime.now().astimezone() - datetime.timedelta(days=days)).isoformat(timespec="seconds")
    rows = conn.execute(
        """SELECT ts, buff_price, steam_price, steam_net, discount
           FROM price_history
           WHERE market_hash_name = ? AND ts >= ?
           ORDER BY ts ASC LIMIT ?""",
        (market_hash_name, since, limit),
    ).fetchall()
    return [
        {"ts": r[0], "buff_price": r[1], "steam_price": r[2], "steam_net": r[3], "discount": r[4]}
        for r in rows
    ]


def prune_history(conn: sqlite3.Connection, keep_days: int = 30) -> int:
    """删除超过 keep_days 的历史，返回删除行数。"""
    cutoff = (datetime.datetime.now().astimezone() - datetime.timedelta(days=keep_days)).isoformat(timespec="seconds")
    cur = conn.execute("DELETE FROM price_history WHERE ts < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def record_scan(
    conn: sqlite3.Connection,
    mode: str,
    status: str = "running",
    started_at: Optional[str] = None,
    item_count: Optional[int] = None,
    finished_at: Optional[str] = None,
) -> int:
    """写入一条扫描记录，返回 id。"""
    started_at = started_at or now_iso()
    cur = conn.execute(
        """INSERT INTO scans (started_at, finished_at, mode, item_count, status)
           VALUES (?, ?, ?, ?, ?)""",
        (started_at, finished_at, mode, item_count, status),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_scan(
    conn: sqlite3.Connection,
    scan_id: int,
    status: str,
    item_count: Optional[int] = None,
    finished_at: Optional[str] = None,
) -> None:
    conn.execute(
        "UPDATE scans SET status = ?, item_count = COALESCE(?, item_count), finished_at = ? WHERE id = ?",
        (status, item_count, finished_at or now_iso(), scan_id),
    )
    conn.commit()


# ---------- 深度扫描持久化索引 ----------


def get_deep_progress(conn: sqlite3.Connection, game: str = "csgo") -> Optional[dict[str, Any]]:
    row = conn.execute("SELECT * FROM deep_scan_progress WHERE game = ?", (game,)).fetchone()
    if row is None:
        return None
    progress = dict(row)
    total_pages = progress.get("total_pages") or 0
    indexed = progress.get("indexed_count") or 0
    completed = (progress.get("priced_count") or 0) + (progress.get("failed_count") or 0)
    if progress["phase"] == "complete":
        percent = 100.0
    elif progress["phase"] == "pricing":
        percent = 35.0 + (65.0 * completed / indexed if indexed else 0.0)
    else:
        finished_pages = max(0, (progress.get("next_page") or 1) - 1)
        percent = 35.0 * finished_pages / total_pages if total_pages else 0.0
    progress["percent"] = round(min(100.0, percent), 1)
    return progress


def start_deep_cycle(conn: sqlite3.Connection, game: str = "csgo") -> dict[str, Any]:
    """创建新一轮全量索引，或返回尚未完成的持久化检查点。"""
    current = get_deep_progress(conn, game)
    if current is not None and current["phase"] != "complete":
        return current
    generation = (current["generation"] + 1) if current else 1
    now = now_iso()
    with conn:
        conn.execute(
            """INSERT INTO deep_scan_progress
               (game, generation, phase, next_page, total_pages, indexed_count,
                priced_count, failed_count, started_at, updated_at, completed_at)
               VALUES (?, ?, 'indexing', 1, NULL, 0, 0, 0, ?, ?, NULL)
               ON CONFLICT(game) DO UPDATE SET
                 generation = excluded.generation,
                 phase = 'indexing', next_page = 1, total_pages = NULL,
                 indexed_count = 0, priced_count = 0, failed_count = 0,
                 started_at = excluded.started_at, updated_at = excluded.updated_at,
                 completed_at = NULL""",
            (game, generation, now, now),
        )
    return get_deep_progress(conn, game) or {}


def save_deep_index_page(
    conn: sqlite3.Connection,
    game: str,
    generation: int,
    items: list[dict[str, Any]],
    *,
    next_page: int,
    total_pages: int,
) -> dict[str, Any]:
    """原子保存一个 BUFF 索引页及下一页检查点。"""
    indexed_at = now_iso()
    unique_items = list({item["market_hash_name"]: item for item in items}.values())
    with conn:
        existing_count = 0
        if unique_items:
            placeholders = ",".join("?" for _ in unique_items)
            names = [item["market_hash_name"] for item in unique_items]
            existing_count = conn.execute(
                f"""SELECT COUNT(*) FROM deep_scan_index
                    WHERE game = ? AND generation = ? AND market_hash_name IN ({placeholders})""",
                [game, generation, *names],
            ).fetchone()[0]
        for item in unique_items:
            _upsert_asset(conn, item)
            conn.execute(
                """INSERT INTO deep_scan_index
                   (game, market_hash_name, generation, buff_goods_id, display_name,
                    buff_url, icon_url, item_type, weapon, category, exterior, rarity,
                    quality, buff_price, buff_sell_num, buff_buy_num, buff_buy_max_price,
                    steam_reference_usd, steam_reference_cny, indexed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(game, market_hash_name) DO UPDATE SET
                     generation = excluded.generation,
                     buff_goods_id = excluded.buff_goods_id,
                     display_name = excluded.display_name,
                     buff_url = excluded.buff_url,
                     icon_url = excluded.icon_url,
                     item_type = excluded.item_type,
                     weapon = excluded.weapon,
                     category = excluded.category,
                     exterior = excluded.exterior,
                     rarity = excluded.rarity,
                     quality = excluded.quality,
                     buff_price = excluded.buff_price,
                     buff_sell_num = excluded.buff_sell_num,
                     buff_buy_num = excluded.buff_buy_num,
                     buff_buy_max_price = excluded.buff_buy_max_price,
                     steam_reference_usd = excluded.steam_reference_usd,
                     steam_reference_cny = excluded.steam_reference_cny,
                     indexed_at = excluded.indexed_at""",
                (
                    game,
                    item["market_hash_name"],
                    generation,
                    item.get("buff_goods_id"),
                    item.get("display_name"),
                    item.get("buff_url"),
                    item.get("icon_url"),
                    item.get("item_type"),
                    item.get("weapon"),
                    item.get("category"),
                    item.get("exterior"),
                    item.get("rarity"),
                    item.get("quality"),
                    item.get("buff_price"),
                    item.get("buff_sell_num"),
                    item.get("buff_buy_num"),
                    item.get("buff_buy_max_price"),
                    item.get("steam_reference_usd"),
                    item.get("steam_reference_cny"),
                    indexed_at,
                ),
            )
        conn.execute(
            """UPDATE deep_scan_progress
               SET next_page = ?, total_pages = ?,
                   indexed_count = indexed_count + ?, updated_at = ?
               WHERE game = ? AND generation = ?""",
            (
                next_page,
                total_pages,
                len(unique_items) - existing_count,
                indexed_at,
                game,
                generation,
            ),
        )
    return get_deep_progress(conn, game) or {}


def set_deep_phase(conn: sqlite3.Connection, game: str, generation: int, phase: str) -> None:
    now = now_iso()
    completed_at = now if phase == "complete" else None
    with conn:
        conn.execute(
            """UPDATE deep_scan_progress SET phase = ?, updated_at = ?, completed_at = ?
               WHERE game = ? AND generation = ?""",
            (phase, now, completed_at, game, generation),
        )


def next_deep_item(conn: sqlite3.Connection, game: str, generation: int) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """SELECT * FROM deep_scan_index
           WHERE game = ? AND generation = ? AND priced_generation < ?
           LIMIT 1""",
        (game, generation, generation),
    ).fetchone()
    return dict(row) if row else None


def finish_deep_item(
    conn: sqlite3.Connection,
    item: dict[str, Any],
    generation: int,
    scan_id: int,
    *,
    success: bool,
) -> None:
    """原子记录深度索引定价结果和进度；失败项本轮不再无限重试。"""
    with conn:
        if success:
            _upsert_item(conn, item, scan_id=scan_id)
            _insert_history(conn, item)
        conn.execute(
            """UPDATE deep_scan_index SET priced_generation = ?
               WHERE game = ? AND market_hash_name = ?""",
            (generation, item.get("game", "csgo"), item["market_hash_name"]),
        )
        counter = "priced_count" if success else "failed_count"
        conn.execute(
            f"""UPDATE deep_scan_progress
                SET {counter} = {counter} + 1, updated_at = ?
                WHERE game = ? AND generation = ?""",
            (now_iso(), item.get("game", "csgo"), generation),
        )
