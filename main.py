"""
CLI entry point.

Usage:
  python main.py card              — post next unposted card (cycles armas→elina→wlb)
  python main.py card armas        — post from armaselektronik.com only
  python main.py card elina        — post from elina.ru only
  python main.py card wlb          — post from warninglightbars.com only
  python main.py card --force      — post even if already posted
  python main.py editorial         — post editorial (random type)
  python main.py editorial review  — post review specifically
  python main.py prices init [ids]        — price agent: new run folder + briefs (research: /prices in Claude Code)
  python main.py prices check <dir>       — which items are not handed in yet
  python main.py prices report <dir> [--no-send]  — compute and send the report to ADMIN_CHAT_ID
  python main.py init              — initialize DB
"""

import sys
from db import init_db

SOURCES = ["armas", "elina", "wlb"]


def _next_source() -> str:
    """Cycle armas → elina → wlb → armas ..."""
    from db import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key='last_source'").fetchone()
    last = row["value"] if row else "wlb"
    idx = SOURCES.index(last) if last in SOURCES else -1
    return SOURCES[(idx + 1) % len(SOURCES)]


def _save_source(source: str):
    from db import get_conn
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO kv (key, value) VALUES ('last_source', ?)",
            (source,)
        )


def cmd_card(source: str | None = None, force: bool = False):
    from product_card import post_product_card
    from db import is_posted

    if source is None:
        source = _next_source()

    print(f"Source: {source}")

    if source == "elina":
        from elina_scraper import fetch_all_elina_products, fetch_product_detail
        products = fetch_all_elina_products(limit_per_category=20)
    elif source == "wlb":
        from warning_scraper import fetch_all_wlb_products, fetch_product_detail
        products = fetch_all_wlb_products(max_pages=15)
    else:
        from scraper import fetch_all_products, fetch_product_detail
        products = fetch_all_products(limit_per_category=10)

    for p in products:
        if force or not is_posted(p["id"]):
            p = fetch_product_detail(p)
            ok = post_product_card(p, force=force)
            if ok:
                name = p.get("name_ru") or p.get("name_en") or p.get("name_tr", "?")
                print(f"Posted: {name}")
                _save_source(source)
                return

    print(f"No new products from {source}")


def cmd_editorial(post_type: str | None = None):
    from editorial_post import post_editorial
    topic = post_editorial(post_type)
    print(f"Posted editorial: {topic}")


def main():
    init_db()

    args = sys.argv[1:]
    if not args or args[0] == "init":
        print("DB initialized.")
        return

    cmd = args[0]

    if cmd == "card":
        source = None
        if len(args) > 1 and args[1] in SOURCES:
            source = args[1]
        cmd_card(source=source, force="--force" in args)

    elif cmd == "editorial":
        post_type = args[1] if len(args) > 1 and not args[1].startswith("-") else None
        cmd_editorial(post_type)

    elif cmd == "prices":
        from price_agent import cli
        sys.exit(cli(args[1:]))

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
