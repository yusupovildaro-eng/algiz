import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "posted.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS posted_products (
                product_id TEXT PRIMARY KEY,
                url TEXT,
                posted_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS posted_editorials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT,
                posted_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT,
                run_at TEXT DEFAULT (datetime('now')),
                offers INTEGER,
                basis TEXT,
                min_uzs REAL,
                median_uzs REAL,
                max_uzs REAL,
                our_price_uzs REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_offers (
                run_id INTEGER,
                item_id TEXT,
                seller TEXT,
                title TEXT,
                price REAL,
                currency TEXT,
                price_uzs REAL,
                url TEXT,
                match TEXT,
                in_stock TEXT,
                note TEXT
            )
        """)


def is_posted(product_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM posted_products WHERE product_id = ?", (product_id,)
        ).fetchone()
    return row is not None


def mark_posted(product_id: str, url: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO posted_products (product_id, url) VALUES (?, ?)",
            (product_id, url),
        )


def log_editorial(topic: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO posted_editorials (topic) VALUES (?)", (topic,)
        )


def last_price_median(item_id: str, basis: str) -> float | None:
    """Median market price from the previous price-agent run for this item, same basis (exact/analog)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT median_uzs FROM price_runs WHERE item_id = ? AND basis = ? AND median_uzs IS NOT NULL "
            "ORDER BY id DESC LIMIT 1",
            (item_id, basis),
        ).fetchone()
    return row["median_uzs"] if row else None


def save_price_run(item_id: str, stats: dict, offers: list) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO price_runs (item_id, offers, basis, min_uzs, median_uzs, max_uzs, our_price_uzs) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item_id, stats["count"], stats["basis"], stats["min"], stats["median"],
             stats["max"], stats["our_price"]),
        )
        run_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO price_offers (run_id, item_id, seller, title, price, currency, price_uzs, "
            "url, match, in_stock, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(run_id, item_id, o["seller"], o["title"], o["price"], o["currency"], o["price_uzs"],
              o["url"], o["match"], o["in_stock"], o["note"]) for o in offers],
        )
    return run_id
