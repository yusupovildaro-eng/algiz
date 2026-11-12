"""
Агент цен — работает по подписке Claude, без API-ключа Anthropic.

Поиск делает Claude Code: скил /prices (.claude/skills/prices) запускает
по одному субагенту на товар, каждый пишет найденные предложения в файл.
Этот модуль делает всё остальное, без модели:

  python main.py prices init [id ...]             папка проверки + брифы для субагентов
  python main.py prices check <папка>             какие товары ещё не сданы
  python main.py prices report <папка> [--no-send]   посчитать, сохранить, отправить отчёт

Расчёты: цены переводятся в сумы по курсу ЦБ РУз; мин/медиана/макс,
наша позиция, себестоимость и маржа (из sebestoimost.json, если товар там есть),
изменение с прошлой проверки. Отчёт уходит в ADMIN_CHAT_ID — никогда в канал.
"""

import argparse
import html
import json
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

import db
import sebestoimost
from rates import CURRENCIES, get_rates

load_dotenv()

BASE_DIR = Path(__file__).parent
WATCHLIST_PATH = BASE_DIR / "price_watch.json"
COSTS_PATH = BASE_DIR / "sebestoimost.json"
REPORT_DIR = BASE_DIR / "reports" / "prices"

TELEGRAM_LIMIT = 3900  # лимит Telegram 4096, оставляем запас
TASHKENT = timezone(timedelta(hours=5))

BRIEF = """# Бриф исследователя цен: {name}

Ты исследуешь цены для {company} — магазина в Узбекистане, который продаёт {sells}.
Задача: узнать, почём другие продавцы в Узбекистане сейчас продают ОДИН товар
(и близкие аналоги), чтобы владелец сравнил их со своими ценами.

## Товар

- Название: {name}
- Что искать: {query}
- Аналогами считать: {analogs}

## Как работать

- Ищи (WebSearch) так, как искал бы покупатель в Узбекистане: на русском и на узбекском
  (латиница), с брендом и без. Например: «{query} цена Ташкент», «{query} купить Узбекистан»,
  «{query} narxi».
- Смотри маркетплейсы и объявления, которыми пользуются в Узбекистане (например olx.uz, uzum.uz),
  и профильные магазины спецсигналов, сирен, алкотестеров.
- Открывай страницы (WebFetch), чтобы увидеть настоящую цену. Цена только из сниппета поиска
  допустима, если она видна явно, — тогда напиши в note «сниппет».
- Только продавцы в Узбекистане или зарубежные магазины с явной доставкой в Узбекистан.
- Ничего не выдумывай: ни продавца, ни цену, ни ссылку. Предложения без цены («договорная»,
  «по запросу») пропускай. Ничего не нашёл — это нормальный результат.
- Цена за 1 штуку. Валюта как на странице: «сум», «so'm» — это "UZS"; «у.е.» — это "USD".
- match: "exact" — та же модель; "analog" — сопоставимый товар другого бренда или модели.
  Явно другие товары (другой тип, запчасти, игрушки) пропускай.
- Наши сайты не учитывай: {own_domains}.
- Остановись, когда наберёшь около {target} подходящих предложений или новые поиски перестанут
  давать новых продавцов. Не больше {max_searches} поисков и {max_fetches} открытых страниц.

## Результат

Запиши ОДИН файл:

    {out_path}

JSON такого вида:

```json
{{
  "offers": [
    {{
      "seller": "Название магазина или продавца, как на странице",
      "title": "Название товара, как на странице",
      "price": 1250000,
      "currency": "UZS",
      "url": "https://страница-где-видна-цена",
      "match": "exact",
      "in_stock": "unknown",
      "note": ""
    }}
  ],
  "summary": "1–2 предложения по-русски: какие цены на этот товар в Узбекистане",
  "sources_checked": ["olx.uz", "поиск: ..."]
}}
```

- price — число без пробелов; currency — одно из: {currencies}.
- match — "exact" или "analog"; in_stock — "yes", "no" или "unknown"; note — строка, можно пустую.
- sources_checked — какие сайты и поиски проверил, включая те, где ничего не нашлось.

Других файлов не создавай и не меняй. В конце ответь одной строкой: сколько предложений записано.
"""


# ── Подготовка проверки ──────────────────────────────────────────────────────

def load_watchlist(path: Path = WATCHLIST_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def init_run(item_ids: list | None = None, watchlist_path: Path = WATCHLIST_PATH,
             report_root: Path = REPORT_DIR) -> Path | None:
    watch = load_watchlist(watchlist_path)
    settings = watch.get("settings", {})
    items = watch.get("items", [])
    if item_ids:
        unknown = set(item_ids) - {it["id"] for it in items}
        if unknown:
            print(f"Нет в price_watch.json: {', '.join(sorted(unknown))}")
        items = [it for it in items if it["id"] in item_ids]
    if not items:
        print("Нечего проверять.")
        return None

    run_dir = (report_root / f"{datetime.now(TASHKENT):%Y-%m-%d_%H%M%S}").resolve()
    (run_dir / "briefs").mkdir(parents=True)
    (run_dir / "offers").mkdir()
    (run_dir / "run.json").write_text(json.dumps(
        {"created": datetime.now(TASHKENT).isoformat(timespec="seconds"),
         "items": [it["id"] for it in items], "reported_at": None},
        ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Папка проверки: {run_dir}")
    print("Брифы — по одному субагенту на каждый:")
    for it in items:
        brief = BRIEF.format(
            name=it["name"],
            query=it.get("query") or it["name"],
            analogs=it.get("analogs") or "сопоставимые товары других брендов",
            company=settings.get("company", "наш магазин"),
            sells=settings.get("sells", "оборудование для спецтранспорта"),
            own_domains=", ".join(settings.get("own_domains") or []) or "—",
            target=settings.get("target_offers", 8),
            max_searches=settings.get("max_searches", 6),
            max_fetches=settings.get("max_fetches", 8),
            out_path=run_dir / "offers" / f"{it['id']}.json",
            currencies=", ".join(CURRENCIES),
        )
        path = run_dir / "briefs" / f"{it['id']}.md"
        path.write_text(brief, encoding="utf-8")
        print(f"  {it['id']}: {path}")
    print(f'Когда все сдадут: python main.py prices check "{run_dir}", '
          f'затем python main.py prices report "{run_dir}"')
    return run_dir


# ── Приём результатов ────────────────────────────────────────────────────────

def read_offers(path: Path) -> tuple[dict | None, str | None]:
    """Файл исследователя → (данные, ошибка). Битые отдельные предложения отсеет clean_offers."""
    if not path.exists():
        return None, "исследователь не сдал результат"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return None, f"файл с ошибкой JSON: {e}"
    if not isinstance(data, dict) or not isinstance(data.get("offers"), list):
        return None, "в файле нет списка offers"
    return {
        "offers": [o for o in data["offers"] if isinstance(o, dict)],
        "summary": str(data.get("summary") or ""),
        "sources_checked": [str(x) for x in data.get("sources_checked") or []],
    }, None


def _load_run(run_dir: Path) -> dict:
    path = Path(run_dir) / "run.json"
    if not path.exists():
        raise SystemExit(f"Нет {path} — это не папка проверки (сначала: python main.py prices init)")
    return json.loads(path.read_text(encoding="utf-8"))


def check_run(run_dir: Path) -> list[tuple[str, str]]:
    run_dir = Path(run_dir)
    problems = []
    for item_id in _load_run(run_dir)["items"]:
        _, err = read_offers(run_dir / "offers" / f"{item_id}.json")
        if err:
            problems.append((item_id, err))
    if problems:
        print("Не сданы или с ошибкой — запустите для них субагентов ещё раз:")
        for item_id, err in problems:
            print(f"  {item_id}: {err} — бриф {run_dir / 'briefs' / f'{item_id}.md'}")
    else:
        print("Все товары сданы.")
    return problems


# ── Расчёты (без модели) ─────────────────────────────────────────────────────

def _is_own(host: str, own_domains: list) -> bool:
    return any(host == d or host.endswith("." + d) for d in own_domains)


def clean_offers(raw: list, rates: dict, own_domains: list) -> list:
    """Отбрасывает предложения без ссылки или цены, наши сайты и дубли; переводит в сумы."""
    cleaned, seen = [], set()
    for offer in raw or []:
        url = str(offer.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        host = (urlparse(url).hostname or "").lower()
        if _is_own(host, own_domains):
            continue
        try:
            price = float(offer.get("price"))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        currency = str(offer.get("currency") or "").upper()
        key = (url, round(price, 2))
        if key in seen:
            continue
        seen.add(key)
        rate = rates.get(currency)
        cleaned.append({
            "seller": str(offer.get("seller") or host),
            "title": str(offer.get("title") or ""),
            "price": price,
            "currency": currency,
            "price_uzs": price * rate if rate else None,
            "url": url,
            "match": offer.get("match") if offer.get("match") in ("exact", "analog") else "analog",
            "in_stock": offer.get("in_stock") if offer.get("in_stock") in ("yes", "no") else "unknown",
            "note": str(offer.get("note") or ""),
        })
    return cleaned


def load_costs(rates: dict, prices: dict, path: Path = COSTS_PATH) -> dict:
    """Себестоимость из sebestoimost.json для товаров с тем же id: {id: {cost, margin_pct, sale_price}}."""
    if not path.exists():
        return {}
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
        settings = spec.get("settings") or {}
    except (json.JSONDecodeError, AttributeError) as e:
        print(f"[prices] sebestoimost.json не прочитан: {e}")
        return {}
    costs = {}
    for p in spec.get("products") or []:
        pid = p.get("id")
        if pid not in prices:
            continue
        price = prices[pid] or p.get("sale_price_uzs")
        try:
            a = sebestoimost.analyse_product(p, settings, rates, price=price or None)
        except (sebestoimost.NumbersError, KeyError, TypeError, ValueError) as e:
            print(f"[prices] себестоимость '{pid}' не посчитана: {e}")
            continue
        costs[pid] = {"cost": a["cost"]["total"], "margin_pct": a["margin_pct"], "sale_price": price}
    return costs


def summarize(item: dict, offers: list, rates: dict, settings: dict, cost_info: dict | None = None) -> dict:
    """Все цифры отчёта считаются здесь, из найденных цен, price_watch.json и sebestoimost.json."""
    priced = [o for o in offers if o["price_uzs"] is not None]
    exact = [o for o in priced if o["match"] == "exact"]
    prices = sorted(o["price_uzs"] for o in (exact or priced))

    s = {
        "count": len(prices),
        "basis": "exact" if exact else "analog",
        "min": prices[0] if prices else None,
        "median": statistics.median(prices) if prices else None,
        "max": prices[-1] if prices else None,
        "our_price": item.get("our_price_uzs") or (cost_info or {}).get("sale_price"),
        "our_vs_median_pct": None,
        "change_pct": None,
        "cost_uzs": None,        # закупка по курсу (если нет sebestoimost.json)
        "landed_uzs": None,      # полная себестоимость
        "cost_source": None,     # "sebestoimost" | "pct" | "purchase"
        "margin_pct": None,
        "market_vs_cost": None,
        "cheapest": sorted(priced, key=lambda o: o["price_uzs"])[:3],
        "unconverted": [o for o in offers if o["price_uzs"] is None],
    }
    median, our = s["median"], s["our_price"]
    if our and median:
        s["our_vs_median_pct"] = (our / median - 1) * 100

    if cost_info:
        s["landed_uzs"], s["cost_source"] = cost_info["cost"], "sebestoimost"
        s["margin_pct"] = cost_info["margin_pct"]
    else:
        supplier_price = item.get("supplier_price")
        rate = rates.get(str(item.get("supplier_currency") or "").upper())
        if supplier_price and rate:
            s["cost_uzs"] = supplier_price * rate
            pct = settings.get("landed_cost_pct")
            if pct is not None:
                s["landed_uzs"], s["cost_source"] = s["cost_uzs"] * (1 + float(pct) / 100), "pct"
            else:
                s["cost_source"] = "purchase"
            if our:
                s["margin_pct"] = (our - (s["landed_uzs"] or s["cost_uzs"])) / our * 100

    base_cost = s["landed_uzs"] or s["cost_uzs"]
    if base_cost and median:
        s["market_vs_cost"] = median / base_cost
    return s


def apply_change(s: dict, prev_median: float | None) -> None:
    """Изменение медианы с прошлой проверки (prev берётся с тем же basis)."""
    if prev_median and s["median"]:
        s["change_pct"] = (s["median"] / prev_median - 1) * 100


# ── Отчёт ────────────────────────────────────────────────────────────────────

def _sum(value: float) -> str:
    return ("−" if value < 0 else "") + f"{abs(round(value)):,}".replace(",", " ") + " сум"


def _pct(value: float) -> str:
    return ("+" if value >= 0 else "−") + f"{abs(value):.0f}%"


def _esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def format_item(n: int, item: dict, res: dict, s: dict, settings: dict) -> str:
    lines = [f"<b>{n}. {_esc(item['name'])}</b>"]
    if res.get("error"):
        lines.append(f"⚠️ Не удалось проверить: {_esc(res['error'])}")
        return "\n".join(lines)

    if s["count"]:
        basis = "точные совпадения" if s["basis"] == "exact" else "только аналоги"
        lines.append(
            f"Рынок: {_sum(s['min'])} – {_sum(s['max'])}, медиана <b>{_sum(s['median'])}</b> "
            f"({s['count']} предл., {basis})"
        )
        if s["change_pct"] is not None:
            lines.append(f"С прошлой проверки: медиана {_pct(s['change_pct'])}")
    else:
        checked = ", ".join(res.get("sources_checked") or [])[:300]
        lines.append("Предложений с ценой не найдено." + (f" Проверено: {_esc(checked)}" if checked else ""))

    if s["our_price"]:
        line = f"Наша цена: {_sum(s['our_price'])}"
        if s["our_vs_median_pct"] is not None:
            d = s["our_vs_median_pct"]
            if abs(d) < 2:
                line += " — на уровне медианы"
            else:
                line += f" — на {abs(d):.0f}% {'выше' if d > 0 else 'ниже'} медианы"
        lines.append(line)
    else:
        lines.append("Наша цена: не указана (price_watch.json или sebestoimost.json)")

    if s["cost_source"] == "sebestoimost":
        lines.append(f"Себестоимость (sebestoimost.json): {_sum(s['landed_uzs'])}")
    elif s["cost_uzs"]:
        line = (f"Закупка: {item['supplier_price']:,.0f} {item['supplier_currency'].upper()}".replace(",", " ")
                + f" ≈ {_sum(s['cost_uzs'])} по курсу ЦБ")
        if s["cost_source"] == "pct":
            line += f", с надбавкой {settings['landed_cost_pct']}% ≈ {_sum(s['landed_uzs'])}"
        else:
            line += " (без доставки, пошлины и налогов)"
        lines.append(line)
    if s["margin_pct"] is not None:
        tail = " (с налогами)" if s["cost_source"] == "sebestoimost" else ""
        lines.append(f"Маржа при нашей цене: {_pct(s['margin_pct']).lstrip('+')}{tail}")
    ratio = s["market_vs_cost"]
    what = "себестоимости" if s["cost_source"] in ("sebestoimost", "pct") else "закупки"
    if ratio and ratio >= 1:
        lines.append(f"Медиана рынка выше {what} в {ratio:.2f} раза".replace(".", ","))
    elif ratio:
        lines.append(f"⚠️ Медиана рынка ниже {what} на {(1 - ratio) * 100:.0f}%")

    if s["cheapest"]:
        lines.append("Дешевле всего:")
        for o in s["cheapest"]:
            extra = []
            if o["currency"] != "UZS":
                extra.append(f"{o['price']:,.0f} {o['currency']}".replace(",", " "))
            if o["match"] == "analog":
                extra.append("аналог")
            if o["in_stock"] == "no":
                extra.append("нет в наличии")
            tail = f" ({', '.join(extra)})" if extra else ""
            lines.append(f"• <a href=\"{_esc(o['url'])}\">{_esc(o['seller'])}</a> — {_sum(o['price_uzs'])}{tail}")
    if s["unconverted"]:
        lines.append(f"Без пересчёта в сумы (нет курса): {len(s['unconverted'])} предл.")

    if res.get("summary"):
        lines.append(f"💬 {_esc(res['summary'][:500])}")
    return "\n".join(lines)


def build_report(rows: list, rates_label: str, when: datetime, settings: dict) -> list[str]:
    """Возвращает отчёт, уже разбитый на сообщения Telegram."""
    header = f"📊 <b>Цены конкурентов — {when:%d.%m.%Y}</b>\n{_esc(rates_label)}"
    footer = "Собрано Claude Code по подписке. Цены взяты с сайтов — перед решением проверьте по ссылкам."
    blocks = [format_item(i, item, res, s, settings) for i, (item, res, s) in enumerate(rows, 1)]

    messages, current = [], header
    for block in blocks + [footer]:
        if len(current) + len(block) + 2 > TELEGRAM_LIMIT:
            messages.append(current)
            current = block[:TELEGRAM_LIMIT]
        else:
            current += "\n\n" + block
    messages.append(current)
    return messages


def send_report(messages: list[str]) -> bool:
    chat_id = os.environ.get("ADMIN_CHAT_ID", "").strip()
    if not chat_id:
        print("[prices] ADMIN_CHAT_ID не задан — отчёт не отправлен (см. PRICE_AGENT.md).")
        return False
    if chat_id == os.environ.get("CHANNEL_ID", "").strip():
        print("[prices] ADMIN_CHAT_ID совпадает с каналом — не отправляю: отчёт не для публики.")
        return False
    from telegram_client import send_text
    try:
        for text in messages:
            send_text(text, chat_id=chat_id)
    except requests.RequestException as e:
        print(f"[prices] Telegram не принял отчёт: {e}. Копия сохранена в папке проверки.")
        return False
    print("[prices] отчёт отправлен в Telegram.")
    return True


def finish_run(run_dir: Path, send: bool = True, watchlist_path: Path = WATCHLIST_PATH,
               costs_path: Path = COSTS_PATH) -> list[str]:
    run_dir = Path(run_dir)
    run = _load_run(run_dir)
    watch = load_watchlist(watchlist_path)
    settings = watch.get("settings", {})
    by_id = {it["id"]: it for it in watch.get("items", [])}
    items = [by_id[i] for i in run["items"] if i in by_id]
    already = bool(run.get("reported_at"))

    db.init_db()
    rates, rates_label = get_rates(settings.get("manual_rates"))
    costs = load_costs(rates, {it["id"]: it.get("our_price_uzs") for it in items}, costs_path)
    own = [d.lower() for d in settings.get("own_domains") or []]

    rows, saved = [], []
    for item in items:
        data, err = read_offers(run_dir / "offers" / f"{item['id']}.json")
        res = {"error": err, "summary": (data or {}).get("summary", ""),
               "sources_checked": (data or {}).get("sources_checked", [])}
        offers = clean_offers((data or {}).get("offers"), rates, own)
        s = summarize(item, offers, rates, settings, costs.get(item["id"]))
        if not already:
            apply_change(s, db.last_price_median(item["id"], s["basis"]))
            if not err:
                db.save_price_run(item["id"], s, offers)
        rows.append((item, res, s))
        saved.append({"item": item, **res, "offers": offers,
                      "stats": {k: v for k, v in s.items() if k not in ("cheapest", "unconverted")}})

    now = datetime.now(TASHKENT)
    messages = build_report(rows, rates_label, now, settings)
    (run_dir / "result.json").write_text(json.dumps({"rates": rates, "items": saved},
                                                    ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "report.txt").write_text("\n\n".join(messages), encoding="utf-8")
    run["reported_at"] = now.isoformat(timespec="seconds")
    (run_dir / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n\n".join(messages))
    print(f"\n[prices] отчёт: {run_dir / 'report.txt'}")
    if already:
        print("[prices] отчёт по этой папке уже делался — история цен не обновлялась.")
    if send:
        send_report(messages)
    return messages


# ── CLI ──────────────────────────────────────────────────────────────────────

def cli(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(prog="python main.py prices", description="Агент цен (по подписке Claude)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init", help="создать папку проверки и брифы")
    p_init.add_argument("ids", nargs="*", help="id товаров из price_watch.json (по умолчанию все)")
    p_check = sub.add_parser("check", help="какие товары ещё не сданы")
    p_check.add_argument("run_dir")
    p_report = sub.add_parser("report", help="посчитать и отправить отчёт")
    p_report.add_argument("run_dir")
    p_report.add_argument("--no-send", action="store_true", help="не отправлять в Telegram")
    args = ap.parse_args(argv)

    if args.cmd == "init":
        return 0 if init_run(args.ids or None) else 1
    if args.cmd == "check":
        return 1 if check_run(Path(args.run_dir)) else 0
    finish_run(Path(args.run_dir), send=not args.no_send)
    return 0
