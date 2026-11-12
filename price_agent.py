"""
Агент цен: ищет, почём конкуренты в Узбекистане продают наши товары,
и присылает владельцу отчёт в личный Telegram (не в канал).

Как работает:
  1. Читает список товаров из price_watch.json.
  2. Для каждого товара запускает отдельного исследователя (Claude + веб-поиск),
     несколько товаров параллельно.
  3. Исследователь сдаёт найденные предложения через инструмент submit_offers.
  4. Python (не модель) переводит цены в сумы по курсу ЦБ РУз, считает
     мин/медиану/макс, нашу позицию, маржу и изменение с прошлой проверки.
  5. Отчёт уходит в ADMIN_CHAT_ID, копия сохраняется в reports/prices/,
     история — в posted.db.

Usage:
  python main.py prices                  — все товары
  python main.py prices ermak tsefey     — только эти id
  python main.py prices --no-send        — не отправлять в Telegram
"""

import html
import json
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import anthropic
import requests
from dotenv import load_dotenv

import db

load_dotenv()

BASE_DIR = Path(__file__).parent
WATCHLIST_PATH = BASE_DIR / "price_watch.json"
REPORT_DIR = BASE_DIR / "reports" / "prices"

MODEL = os.environ.get("PRICE_AGENT_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("PRICE_AGENT_EFFORT", "medium")
MAX_WORKERS = int(os.environ.get("PRICE_AGENT_WORKERS", "3"))

MAX_TURNS = 8          # запросов к API на один товар, с учётом продолжений
MAX_NUDGES = 2         # сколько раз напомнить сдать результат
TELEGRAM_LIMIT = 3900  # лимит Telegram 4096, оставляем запас

CURRENCIES = ["UZS", "USD", "EUR", "RUB", "CNY", "TRY"]
CBU_URL = "https://cbu.uz/ru/arkhiv-kursov-valyut/json/"
TASHKENT = timezone(timedelta(hours=5))

# Серверный запасной вариант при отказе модели (refusal) — для моделей, где он есть
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2"}

# $ за 1 млн токенов: вход, выход, чтение кэша. Только для примерной оценки в отчёте.
PRICING = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}
WEB_SEARCH_USD = 0.01  # за один веб-поиск


# ── Инструменты исследователя ────────────────────────────────────────────────

SUBMIT_TOOL = {
    "name": "submit_offers",
    "description": (
        "Call this exactly once, when your research is finished, to hand in every "
        "offer you found for the product. Submit an empty list if you found none. "
        "Only include offers whose price you actually saw."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "offers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "seller": {"type": "string", "description": "Shop or seller name as shown on the page"},
                        "title": {"type": "string", "description": "Product title as shown on the page"},
                        "price": {"type": "number", "description": "Price for one unit, plain number without spaces"},
                        "currency": {"type": "string", "enum": CURRENCIES},
                        "url": {"type": "string", "description": "Page where you saw this price"},
                        "match": {
                            "type": "string",
                            "enum": ["exact", "analog"],
                            "description": "exact = the same model; analog = a comparable product of another brand or model",
                        },
                        "in_stock": {"type": "string", "enum": ["yes", "no", "unknown"]},
                        "note": {
                            "type": "string",
                            "description": "Short note: wholesale, with VAT, used, price from search snippet, etc. Empty string if nothing",
                        },
                    },
                    "required": ["seller", "title", "price", "currency", "url", "match", "in_stock", "note"],
                    "additionalProperties": False,
                },
            },
            "summary": {
                "type": "string",
                "description": "1-2 sentences in Russian: what the market for this product in Uzbekistan looks like",
            },
            "sources_checked": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Sites and searches you checked, including ones with no result",
            },
        },
        "required": ["offers", "summary", "sources_checked"],
        "additionalProperties": False,
    },
}

SYSTEM_PROMPT = """You are a price researcher for {company}, a shop in Uzbekistan that sells {sells}.
Your job: find the prices other sellers in Uzbekistan currently ask for ONE product (and close analogs), so the owner can compare them with his own prices.

How to work:
- Search the way a buyer in Uzbekistan would: in Russian and in Uzbek (Latin script), with and without the brand. For example "<product> цена Ташкент", "<product> купить Узбекистан", "<product> narxi".
- Look at the marketplaces and classifieds people in Uzbekistan use (for example olx.uz and uzum.uz) and at specialist shops for special-vehicle lights, sirens and breathalyzers.
- Open pages with web_fetch to read the actual price. A price seen only in a search snippet is acceptable when the snippet shows it clearly; then write "snippet" in note.
- Only sellers in Uzbekistan, or foreign shops that clearly deliver to Uzbekistan.
- Never invent a seller, a price or a link. Skip offers with no price ("договорная", "по запросу"). Finding nothing is a valid result.
- One unit price per offer. Keep the currency shown on the page: "сум", "so'm" and "UZS" are UZS; "у.е." is USD.
- match is "exact" only for the same model. A comparable product of another brand or model is "analog". Skip clearly different products (another product type, spare parts, toys).
- Do not include our own sites: {own_domains}.
- Stop when you have about {target} relevant offers or when new searches stop turning up new sellers. Then call submit_offers once."""

USER_PROMPT = """Product: {name}
What to search for: {query}
Accept as analogs: {analogs}"""

NUDGE = (
    "You have not called submit_offers yet. Call it now with the offers you have "
    "found so far (an empty list is fine)."
)


def _server_tools(settings: dict) -> list:
    search = {
        "type": "web_search_20260209",
        "name": "web_search",
        "max_uses": int(settings.get("max_searches", 6)),
        "user_location": {
            "type": "approximate",
            "city": "Tashkent",
            "country": "UZ",
            "timezone": "Asia/Tashkent",
        },
    }
    own = settings.get("own_domains") or []
    if own:
        search["blocked_domains"] = own
    fetch = {
        "type": "web_fetch_20260209",
        "name": "web_fetch",
        "max_uses": int(settings.get("max_fetches", 8)),
        "max_content_tokens": 20000,
    }
    return [search, fetch]


# ── Один исследователь на один товар ─────────────────────────────────────────

def _add_usage(total: dict, usage) -> None:
    if usage is None:
        return
    total["input"] += getattr(usage, "input_tokens", 0) or 0
    total["output"] += getattr(usage, "output_tokens", 0) or 0
    total["cache_write"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
    total["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0
    server = getattr(usage, "server_tool_use", None)
    if server is not None:
        total["searches"] += getattr(server, "web_search_requests", 0) or 0


def research_item(client, item: dict, settings: dict) -> dict:
    """Run one researcher for one product. Never raises: errors go into result["error"]."""
    usage = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "searches": 0}
    result = {"id": item["id"], "offers": [], "summary": "", "sources_checked": [], "usage": usage, "error": None}

    system = SYSTEM_PROMPT.format(
        company=settings.get("company", "our shop"),
        sells=settings.get("sells", "special-vehicle equipment"),
        own_domains=", ".join(settings.get("own_domains") or []) or "none",
        target=settings.get("target_offers", 8),
    )
    messages = [{
        "role": "user",
        "content": USER_PROMPT.format(
            name=item["name"],
            query=item.get("query") or item["name"],
            analogs=item.get("analogs") or "comparable products of other brands",
        ),
    }]
    tools = _server_tools(settings) + [SUBMIT_TOOL]

    extra = {}
    if MODEL in FALLBACK_MODELS:
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}

    nudges = 0
    try:
        for _ in range(MAX_TURNS):
            response = client.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=system,
                tools=tools,
                messages=messages,
                output_config={"effort": EFFORT},
                cache_control={"type": "ephemeral"},
                **extra,
            )
            _add_usage(usage, response.usage)

            if response.stop_reason == "refusal":
                result["error"] = "модель отказалась выполнять запрос"
                return result
            if response.stop_reason == "max_tokens":
                result["error"] = "ответ модели обрезан по лимиту токенов"
                return result

            submit = next(
                (b for b in response.content if b.type == "tool_use" and b.name == "submit_offers"),
                None,
            )
            if submit is not None:
                data = submit.input if isinstance(submit.input, dict) else json.loads(submit.input)
                result["offers"] = data.get("offers") or []
                result["summary"] = data.get("summary") or ""
                result["sources_checked"] = data.get("sources_checked") or []
                return result

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "pause_turn":
                continue  # серверный поиск не закончил — просто продолжаем

            other_tools = [b for b in response.content if b.type == "tool_use"]
            if other_tools:
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                     "content": f"Unknown tool {b.name}. Use web_search, web_fetch and submit_offers."}
                    for b in other_tools
                ]})
                continue

            if nudges >= MAX_NUDGES:
                break
            nudges += 1
            messages.append({"role": "user", "content": NUDGE})

        result["error"] = "исследователь не сдал результат"
    except anthropic.APIStatusError as e:
        result["error"] = f"ошибка API {e.status_code}: {e.message}"
    except anthropic.APIConnectionError as e:
        result["error"] = f"нет связи с API: {e}"
    return result


# ── Курсы и расчёты (без модели) ─────────────────────────────────────────────

def get_rates(manual_rates: dict | None = None) -> tuple[dict, str]:
    """Курсы ЦБ РУз: сколько сумов за 1 единицу валюты. Возвращает (курсы, подпись)."""
    rates = {"UZS": 1.0}
    label = "курс ЦБ РУз недоступен"
    try:
        r = requests.get(CBU_URL, timeout=15)
        r.raise_for_status()
        for row in r.json():
            ccy = row.get("Ccy")
            if ccy in CURRENCIES:
                rates[ccy] = float(row["Rate"]) / float(row.get("Nominal") or 1)
                label = f"курс ЦБ РУз на {row.get('Date', '?')}"
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"[prices] курсы ЦБ не получены: {e}")
    for ccy, value in (manual_rates or {}).items():
        if ccy.upper() not in rates:
            rates[ccy.upper()] = float(value)
            label += f", {ccy.upper()} — из настроек"
    return rates, label


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
            "in_stock": offer.get("in_stock") or "unknown",
            "note": str(offer.get("note") or ""),
        })
    return cleaned


def summarize(item: dict, offers: list, rates: dict, settings: dict) -> dict:
    """Все цифры отчёта считаются здесь, из найденных цен и price_watch.json."""
    priced = [o for o in offers if o["price_uzs"] is not None]
    exact = [o for o in priced if o["match"] == "exact"]
    basis_offers = exact or priced
    prices = sorted(o["price_uzs"] for o in basis_offers)

    s = {
        "count": len(prices),
        "basis": "exact" if exact else "analog",
        "min": prices[0] if prices else None,
        "median": statistics.median(prices) if prices else None,
        "max": prices[-1] if prices else None,
        "our_price": item.get("our_price_uzs"),
        "our_vs_median_pct": None,
        "change_pct": None,
        "cost_uzs": None,
        "landed_uzs": None,
        "margin_pct": None,
        "market_vs_cost": None,
        "cheapest": sorted(priced, key=lambda o: o["price_uzs"])[:3],
        "unconverted": [o for o in offers if o["price_uzs"] is None],
    }
    median = s["median"]
    our = s["our_price"]
    if our and median:
        s["our_vs_median_pct"] = (our / median - 1) * 100

    supplier_price = item.get("supplier_price")
    rate = rates.get(str(item.get("supplier_currency") or "").upper())
    if supplier_price and rate:
        s["cost_uzs"] = supplier_price * rate
        pct = settings.get("landed_cost_pct")
        if pct is not None:
            s["landed_uzs"] = s["cost_uzs"] * (1 + float(pct) / 100)
        cost_for_margin = s["landed_uzs"] or s["cost_uzs"]
        if our:
            s["margin_pct"] = (our - cost_for_margin) / our * 100
        if median:
            s["market_vs_cost"] = median / s["cost_uzs"]
    return s


def apply_change(s: dict, prev_median: float | None) -> None:
    """Изменение медианы с прошлой проверки (prev берётся с тем же basis)."""
    if prev_median and s["median"]:
        s["change_pct"] = (s["median"] / prev_median - 1) * 100


def estimate_cost(usage: dict) -> float:
    price_in, price_out, price_cache = PRICING.get(MODEL, PRICING["claude-opus-5-5"])
    return (
        (usage["input"] + usage["cache_write"] * 1.25) * price_in
        + usage["cache_read"] * price_cache
        + usage["output"] * price_out
    ) / 1_000_000 + usage["searches"] * WEB_SEARCH_USD


# ── Отчёт ────────────────────────────────────────────────────────────────────

def _sum(value: float) -> str:
    return f"{round(value):,}".replace(",", " ") + " сум"


def _pct(value: float) -> str:
    return f"{value:+.0f}%"


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
        lines.append("Наша цена: не указана в price_watch.json")

    if s["cost_uzs"]:
        sp = item["supplier_price"]
        line = (f"Закупка: {sp:,.0f} {item['supplier_currency'].upper()}".replace(",", " ")
                + f" ≈ {_sum(s['cost_uzs'])} по курсу ЦБ")
        if s["landed_uzs"]:
            line += f", с надбавкой {settings['landed_cost_pct']}% ≈ {_sum(s['landed_uzs'])}"
        else:
            line += " (без доставки и таможни)"
        lines.append(line)
        if s["margin_pct"] is not None:
            lines.append(f"Маржа при нашей цене: {s['margin_pct']:.0f}%")
        ratio = s["market_vs_cost"]
        if ratio and ratio >= 1:
            lines.append(f"Медиана рынка выше закупки в {ratio:.2f} раза".replace(".", ","))
        elif ratio:
            lines.append(f"⚠️ Медиана рынка ниже закупки на {(1 - ratio) * 100:.0f}%")

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


def build_report(rows: list, rates_label: str, total_usage: dict, when: datetime, settings: dict) -> list[str]:
    """Возвращает отчёт, уже разбитый на сообщения Telegram."""
    header = f"📊 <b>Цены конкурентов — {when:%d.%m.%Y}</b>\n{_esc(rates_label)}"
    footer = (
        f"Расход: ≈ ${estimate_cost(total_usage):.2f} · поисков: {total_usage['searches']} · модель {MODEL}\n"
        "Цены собраны агентом с сайтов. Перед решением проверьте по ссылкам."
    )
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
        print(f"[prices] Telegram не принял отчёт: {e}. Копия в reports/prices/.")
        return False
    return True


# ── Запуск ───────────────────────────────────────────────────────────────────

def load_watchlist(path: Path = WATCHLIST_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run(item_ids: list | None = None, send: bool = True, client=None,
        watchlist_path: Path = WATCHLIST_PATH, report_dir: Path = REPORT_DIR) -> list[str]:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    watch = load_watchlist(watchlist_path)
    settings = watch.get("settings", {})
    items = watch.get("items", [])
    if item_ids:
        unknown = set(item_ids) - {it["id"] for it in items}
        if unknown:
            print(f"[prices] нет в price_watch.json: {', '.join(sorted(unknown))}")
        items = [it for it in items if it["id"] in item_ids]
    if not items:
        print("[prices] нечего проверять.")
        return []

    if client is None and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print("[prices] Не найден ANTHROPIC_API_KEY. Добавьте ключ в .env и запустите снова.")
        return []

    db.init_db()
    rates, rates_label = get_rates(settings.get("manual_rates"))
    client = client or anthropic.Anthropic(max_retries=4)

    print(f"[prices] товаров: {len(items)}, модель {MODEL}, параллельно {MAX_WORKERS}")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(lambda it: research_item(client, it, settings), items))

    own = [d.lower() for d in settings.get("own_domains") or []]
    total = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "searches": 0}
    rows, saved = [], []
    for item, res in zip(items, results):
        for k in total:
            total[k] += res["usage"][k]
        offers = clean_offers(res["offers"], rates, own)
        s = summarize(item, offers, rates, settings)
        apply_change(s, db.last_price_median(item["id"], s["basis"]))
        if not res["error"]:
            db.save_price_run(item["id"], s, offers)
        rows.append((item, res, s))
        saved.append({"item": item, "error": res["error"], "summary": res["summary"],
                      "sources_checked": res["sources_checked"], "offers": offers,
                      "stats": {k: v for k, v in s.items() if k not in ("cheapest", "unconverted")}})
        print(f"[prices] {item['id']}: {s['count']} предл." + (f", ошибка: {res['error']}" if res["error"] else ""))

    now = datetime.now(TASHKENT)
    messages = build_report(rows, rates_label, total, now, settings)

    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{now:%Y-%m-%d_%H%M%S}"
    (report_dir / f"{stamp}.json").write_text(
        json.dumps({"model": MODEL, "rates": rates, "usage": total, "items": saved},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    (report_dir / f"{stamp}.txt").write_text("\n\n".join(messages), encoding="utf-8")
    print(f"[prices] отчёт сохранён: {report_dir / stamp}.txt, расход ≈ ${estimate_cost(total):.2f}")

    if send:
        send_report(messages)
    return messages
