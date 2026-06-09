"""
Cloud-safe posting: reads products from cached JSON files in data/.
No scraping — works from cloud environments.

Usage:
  python cloud_post.py card
  python cloud_post.py editorial
"""

import json
import os
import sys
import base64
import requests
from db import init_db, is_posted, mark_posted

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SOURCES = ["armas", "elina", "wlb"]

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "yusupovildaro-eng/algiz")
STATE_FILE = "data/state.json"


def _load_json(filename: str) -> list:
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_state() -> dict:
    """Read state from GitHub API (works in cloud where local files may be reset)."""
    if GITHUB_TOKEN:
        try:
            r = requests.get(
                f"https://api.github.com/repos/{GITHUB_REPO}/contents/{STATE_FILE}",
                headers={"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"},
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                content = base64.b64decode(data["content"]).decode("utf-8")
                state = json.loads(content)
                state["_sha"] = data["sha"]
                return state
        except Exception as e:
            print(f"[state read error] {e}")
    # Fallback: read local file
    path = os.path.join(DATA_DIR, "state.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"posted_ids": [], "last_source": "wlb"}


def _write_state(state: dict):
    """Write state back to GitHub API."""
    sha = state.pop("_sha", None)
    content = base64.b64encode(json.dumps(state, ensure_ascii=False, indent=2).encode()).decode()
    if GITHUB_TOKEN:
        try:
            body = {
                "message": "update state",
                "content": content,
                "committer": {"name": "algiz-bot", "email": "bot@algiz.uz"},
            }
            if sha:
                body["sha"] = sha
            r = requests.put(
                f"https://api.github.com/repos/{GITHUB_REPO}/contents/{STATE_FILE}",
                headers={"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"},
                json=body,
                timeout=10,
            )
            if r.status_code in (200, 201):
                print("[state] Updated on GitHub.")
                return
        except Exception as e:
            print(f"[state write error] {e}")
    # Fallback: write local
    path = os.path.join(DATA_DIR, "state.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _next_source(last: str) -> str:
    idx = SOURCES.index(last) if last in SOURCES else -1
    return SOURCES[(idx + 1) % len(SOURCES)]


def cmd_card():
    from product_card import post_product_card

    state = _read_state()
    posted_ids = set(state.get("posted_ids", []))
    source = _next_source(state.get("last_source", "wlb"))

    # Try each source in cycle until we find an unposted product
    for _ in range(len(SOURCES)):
        file_map = {"armas": "armas_products.json", "elina": "elina_products.json", "wlb": "wlb_products.json"}
        products = _load_json(file_map[source])

        for p in products:
            if p["id"] not in posted_ids and not is_posted(p["id"]):
                ok = post_product_card(p, force=False)
                if ok:
                    name = p.get("name_ru") or p.get("name_en") or p.get("name_tr", "?")
                    print(f"Posted: {name} (source={source})")
                    posted_ids.add(p["id"])
                    state["posted_ids"] = list(posted_ids)
                    state["last_source"] = source
                    _write_state(state)
                    return True

        print(f"No new products from {source}, trying next source...")
        source = _next_source(source)

    print("All cached products have been posted. Re-run cache_products.py to refresh.")
    return False


def cmd_editorial():
    from editorial_post import post_editorial
    import random
    types = ["review", "tips", "compare", "digest"]
    topic = post_editorial(random.choice(types))
    print(f"Editorial posted: {topic}")


if __name__ == "__main__":
    init_db()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "card"
    if cmd == "card":
        cmd_card()
    elif cmd == "editorial":
        cmd_editorial()
