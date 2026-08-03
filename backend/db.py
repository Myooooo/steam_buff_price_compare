"""SQLite 数据层：饰品快照、价格历史、扫描记录。

单一连接（check_same_thread=False）+ 应用持有的 asyncio.Lock 序列化写入。
SQLite 本身串行写，应用内也只有 scanner 一个写者，足够安全。
"""
from __future__ import annotations

import datetime
import sqlite3
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  market_hash_name TEXT PRIMARY KEY,
  game TEXT NOT NULL DEFAULT 'csgo',
  buff_price REAL,
  buff_sell_num INTEGER,
  buff_buy_num INTEGER,
  buff_buy_max_price REAL,
  steam_price REAL,
  steam_volume INTEGER,
  steam_net REAL,
  discount REAL,
  source TEXT NOT NULL DEFAULT 'keyword',
  updated_at TEXT NOT NULL,
  first_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_discount ON items(discount);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source);

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
"""


def now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def init_db(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()


def _upsert_item(conn: sqlite3.Connection, item: dict[str, Any]) -> None:
    """按 market_hash_name 插入或更新饰品快照（保留 first_seen_at）。"""
    conn.execute(
        """INSERT INTO items
           (market_hash_name, game, buff_price, buff_sell_num, buff_buy_num,
            buff_buy_max_price, steam_price, steam_volume, steam_net, discount,
            source, updated_at, first_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(market_hash_name) DO UPDATE SET
             game = excluded.game,
             buff_price = excluded.buff_price,
             buff_sell_num = excluded.buff_sell_num,
             buff_buy_num = excluded.buff_buy_num,
             buff_buy_max_price = excluded.buff_buy_max_price,
             steam_price = excluded.steam_price,
             steam_volume = excluded.steam_volume,
             steam_net = excluded.steam_net,
             discount = excluded.discount,
             source = excluded.source,
             updated_at = excluded.updated_at""",
        (
            item["market_hash_name"],
            item.get("game", "csgo"),
            item.get("buff_price"),
            item.get("buff_sell_num"),
            item.get("buff_buy_num"),
            item.get("buff_buy_max_price"),
            item.get("steam_price"),
            item.get("steam_volume"),
            item.get("steam_net"),
            item.get("discount"),
            item.get("source", "keyword"),
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


def save_item(conn: sqlite3.Connection, item: dict[str, Any]) -> None:
    """原子保存当前快照及对应历史点。"""
    with conn:
        _upsert_item(conn, item)
        _insert_history(conn, item)


def list_items(
    conn: sqlite3.Connection,
    only_profitable: bool = False,
    source: Optional[str] = None,
) -> list[dict[str, Any]]:
    """按折价升序（最优在前）返回全部饰品快照。"""
    sql = "SELECT * FROM items"
    conds: list[str] = []
    args: list[Any] = []
    if only_profitable:
        conds.append("discount IS NOT NULL AND discount <= 1.0")
    if source:
        conds.append("source = ?")
        args.append(source)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY discount ASC NULLS LAST, updated_at DESC"
    rows = conn.execute(sql, args).fetchall()
    return [dict(row) for row in rows]


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
