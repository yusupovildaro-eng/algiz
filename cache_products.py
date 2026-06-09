"""
One-time script: scrape all products from all 3 sources and save to data/*.json
Run locally: python cache_products.py
"""

import json
import os
import sys
import io
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)


def save(filename: str, data: list):
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  Saved {len(data)} products → {path}")


def cache_armas():
    print("=== armaselektronik.com ===")
    from scraper import fetch_all_products, fetch_product_detail
    from config import CATALOG_CATEGORIES

    products = []
    for cat in CATALOG_CATEGORIES:
        print(f"  Category: {cat}")
        items = fetch_all_products(limit_per_category=50)
        for item in items:
            if any(p["id"] == item["id"] for p in products):
                continue
            try:
                detail = fetch_product_detail(item)
                products.append(detail)
                print(f"    + {detail.get('name_tr', '?')}")
            except Exception as e:
                print(f"    ! skip {item.get('id')}: {e}")
            time.sleep(0.5)
        break  # fetch_all_products already loops all categories

    save("armas_products.json", products)
    return products


def cache_elina():
    print("=== elina.ru ===")
    from elina_scraper import fetch_all_elina_products, fetch_product_detail

    items = fetch_all_elina_products(limit_per_category=50)
    print(f"  Found {len(items)} products, fetching details...")
    products = []
    for item in items:
        try:
            detail = fetch_product_detail(item)
            products.append(detail)
            print(f"    + {detail.get('name_ru', '?')}")
        except Exception as e:
            print(f"    ! skip {item.get('id')}: {e}")
        time.sleep(0.5)

    save("elina_products.json", products)
    return products


def cache_wlb():
    print("=== warninglightbars.com ===")
    from warning_scraper import fetch_all_wlb_products, fetch_product_detail

    items = fetch_all_wlb_products(max_pages=15)
    print(f"  Found {len(items)} products, fetching details...")
    products = []
    for item in items:
        try:
            detail = fetch_product_detail(item)
            products.append(detail)
            print(f"    + {detail.get('name_en', '?')}")
        except Exception as e:
            print(f"    ! skip {item.get('id')}: {e}")
        time.sleep(0.5)

    save("wlb_products.json", products)
    return products


def init_state():
    path = os.path.join(DATA_DIR, "state.json")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"posted_ids": [], "last_source": "wlb"}, f, indent=2)
        print(f"  Created state.json")


if __name__ == "__main__":
    from db import init_db
    init_db()

    sources = sys.argv[1:] or ["armas", "elina", "wlb"]

    if "armas" in sources:
        cache_armas()
    if "elina" in sources:
        cache_elina()
    if "wlb" in sources:
        cache_wlb()

    init_state()
    print("\nDone! Push to GitHub: git add data/ && git commit -m 'cache products' && git push")
