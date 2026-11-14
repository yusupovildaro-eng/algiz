"""Разбор того, что пишет продавец («Nike Air Force 1, 12900, 40-44»), и тексты подписей."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from .imaging.cards import CardInfo

BRANDS = [
    "New Balance", "Nike", "Jordan", "Adidas", "Puma", "Reebok", "Asics", "Converse", "Vans", "Salomon", "Hoka",
    "On Running", "Saucony", "Mizuno", "Skechers", "Fila", "Under Armour", "The North Face", "Columbia",
    "Stone Island", "C.P. Company", "Carhartt", "Dickies", "Stussy", "Supreme", "Palm Angels", "Off-White",
    "Zara", "Zara Home", "Massimo Dutti", "Mango", "COS", "Arket", "H&M", "Uniqlo", "Bershka", "Pull&Bear",
    "Stradivarius", "Oysho", "Lacoste", "Tommy Hilfiger", "Calvin Klein", "Ralph Lauren", "Polo Ralph Lauren",
    "Levi's", "Diesel", "Guess", "Michael Kors", "Coach", "Furla", "Max Mara", "Weekend Max Mara", "Loro Piana",
    "Brunello Cucinelli", "Moncler", "Canada Goose", "Burberry", "Gucci", "Prada", "Miu Miu", "Balenciaga",
    "Bottega Veneta", "Saint Laurent", "Louis Vuitton", "Dior", "Chanel", "Hermes", "Hermès", "Fendi", "Celine",
    "Valentino", "Versace", "Dolce & Gabbana", "Armani", "Emporio Armani", "Hugo Boss", "Boss", "Hugo",
    "Lululemon", "Alo Yoga", "Dr. Martens", "Timberland", "UGG", "Birkenstock", "Crocs", "Clarks", "Ecco",
    "Geox", "Steve Madden", "Golden Goose", "Veja", "Axel Arigato", "Common Projects", "Maison Margiela",
    "Acne Studios", "Ami Paris", "Kenzo", "Jacquemus", "Ganni", "Sandro", "Maje", "Claudie Pierlot",
    "Villeroy & Boch", "Rosenthal", "Wedgwood", "Royal Copenhagen", "Le Creuset", "Bormioli", "Luminarc",
    "Iittala", "Seletti", "Alessi", "Riedel", "Spiegelau", "Pasabahce", "Paşabahçe", "Kütahya Porselen",
    "Karaca", "Tefal", "WMF", "Zwilling", "Fissler", "IKEA", "H&M Home", "Pandora", "Swarovski",
    "Charlotte Tilbury", "Estée Lauder", "Estee Lauder", "Clinique", "MAC", "NARS", "Rare Beauty", "Fenty Beauty",
    "Huda Beauty", "Benefit", "Too Faced", "Urban Decay", "Sol de Janeiro", "Rhode", "Glossier", "The Ordinary",
    "CeraVe", "La Roche-Posay", "Vichy", "Drunk Elephant", "Kiehl's", "Lancôme", "Lancome", "Guerlain", "Sisley",
    "Byredo", "Le Labo", "Jo Malone", "Diptyque", "Kayali", "YSL Beauty", "Tom Ford", "Dyson",
]

SHOE_WORDS = [
    "кроссовк", "кросы", "кроссы", "кеды", "кед ", "ботин", "туфл", "сапог", "сапож", "лофер", "мокасин", "босонож",
    "сандал", "шлепан", "шлёпан", "сланц", "слипон", "крокс", "угги", "обув", "челси", "мюли", "балетк", "сабо",
    "дерби", "броги", "эспадрил", "sneaker", "shoe", "boots", "loafer", "air force", "air max", "yeezy",
    "dunk", "jordan", "2002r", "samba", "gazelle", "campus", "superstar", "forum",
]
BEAUTY_WORDS = [
    "крем ", "сыворот", "помад", "тушь", "спрей", "парфюм", "духи", "туалетная вода", "тени ", "пудр", "румян",
    "тональн", "палетк", "теней", "блеск", "набор для глаз", "консилер", "хайлайтер", "праймер", "бальзам для губ", "блеск для губ", "косметик", "маска для",
    "шампун", "кондиционер для волос", "лосьон", "тоник", "мицелляр", "скраб", "serum", "lipstick", "mascara",
    "perfume", "parfum", "setting spray", "foundation", "palette", "lip gloss", "skincare", "cosmetic",
]
HOME_WORDS = [
    "тарел", "чашк", "кружк", "бокал", "сервиз", "посуд", "миск", "салатник", "блюд", "ваз", "кастрюл",
    "сковород", "чайник", "ложк", "вилк", "приборы", "стакан", "графин", "пиал", "поднос", "фарфор", "керамик",
    "кувшин", "сахарниц", "молочник", "соусник", "менажниц", "конфетниц", "френч-пресс", "заварник",
    "plate", "mug", "cup", "bowl", "свеч", "подсвечник", "плед", "полотенц", "скатерт", "салфетк",
]

SIZE_TOKEN = r"(?:\d{2}(?:[.,]5)?|X{0,3}S|S|M|L|X{1,3}L|\dXL|XXS|ONE ?SIZE|OS|\d{2}/\d{2}|\d{1,2}(?:[.,]5)?(?:US|UK|EU)?)"
SIZE_SEQ = re.compile(rf"^\s*{SIZE_TOKEN}(?:\s*(?:[-–—]|,|/|;|\s)\s*{SIZE_TOKEN})*\s*$", re.I)
SIZE_PREFIX = re.compile(r"^\s*(?:размер(?:ы|ов)?|разм\.?|р-ры|р-р|рр\.?|р\.|size[s]?)\s*[:\-–]?\s*", re.I)
PRICE_PREFIX = re.compile(r"^\s*(?:цена|стоимость|price|стоит)\s*[:\-–]?\s*", re.I)
NUM = re.compile(r"\d{1,3}(?:[ \u00a0\u202f.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?")
CURRENCIES = [
    (re.compile(r"\$|usd|дол", re.I), "$"),
    (re.compile(r"€|eur|евро", re.I), "€"),
    (re.compile(r"₽|руб|\bр\.?(?:\s|$)|rub", re.I), "₽"),
    (re.compile(r"сум|sum|so['’`]?m|сўм|uzs", re.I), "сум"),
    (re.compile(r"₸|тенге|тг|kzt", re.I), "₸"),
    (re.compile(r"₺|лир|try", re.I), "₺"),
    (re.compile(r"£|gbp|фунт", re.I), "£"),
]
OLD_MARK = re.compile(r"было|вместо|старая|без скидки|→|->|=>|до скидки|теперь|стало|скидк", re.I)


@dataclass
class Details:
    title: str = ""
    brand: str = ""
    price: float | None = None
    old_price: float | None = None
    currency: str = ""
    sizes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    note: str = ""
    category: str = "clothes"


def _num(s: str) -> float:
    s = s.replace("\u00a0", " ").replace("\u202f", " ").strip()
    m = re.fullmatch(r"(\d{1,3}(?:[ .,]\d{3})+)(?:[.,](\d{1,2}))?", s)
    if m:
        return float(re.sub(r"[ .,]", "", m.group(1)) + ("." + m.group(2) if m.group(2) else ""))
    return float(s.replace(",", "."))


def _currency(s: str) -> str:
    for rx, cur in CURRENCIES:
        if rx.search(s):
            return cur
    return ""


def guess_category(text: str) -> str:
    t = f" {text.lower()} "
    if any(w in t for w in BEAUTY_WORDS):
        return "beauty"
    if any(w in t for w in HOME_WORDS):
        return "home"
    if any(w in t for w in SHOE_WORDS):
        return "shoes"
    return "clothes"


BRAND_ALIASES = {
    "найк": "Nike", "адидас": "Adidas", "нью баланс": "New Balance", "нб": "New Balance", "пума": "Puma",
    "рибок": "Reebok", "конверс": "Converse", "ванс": "Vans", "асикс": "Asics", "саломон": "Salomon",
    "зара": "Zara", "манго": "Mango", "лакост": "Lacoste", "гуччи": "Gucci", "прада": "Prada", "диор": "Dior",
    "шанель": "Chanel", "баленсиага": "Balenciaga", "монклер": "Moncler", "томми": "Tommy Hilfiger",
    "кельвин кляйн": "Calvin Klein", "левис": "Levi's", "юникло": "Uniqlo", "джордан": "Jordan",
}


def find_brand(text: str) -> str:
    """Первый по тексту известный бренд («Nike … Carhartt» -> Nike)."""
    low = text.lower()
    found: list[tuple[int, int, str]] = []
    for b in BRANDS:
        m = re.search(rf"(?<![\w]){re.escape(b.lower())}(?![\w])", low)
        if m:
            found.append((m.start(), -len(b), b))
    for alias, b in BRAND_ALIASES.items():
        m = re.search(rf"(?<![\w]){alias}\w{{0,2}}(?![\w])", low)
        if m:
            found.append((m.start(), -len(alias), b))
    return min(found)[2] if found else ""


INLINE_PRICE = re.compile(
    rf"(?:(?:цена|price|стоимость)\s*[:\-–]?\s*)?({NUM.pattern})\s*(\$|€|₽|₸|₺|£|сум|so['’`]?m|руб\.?|usd|eur|тг)?",
    re.I,
)
INLINE_SIZES = re.compile(
    r"(?:(?:размер(?:ы)?|р-р|size[s]?)\s*[:\-–]?\s*)?"
    r"((?:\b(?:[1-5]\d(?:[.,]5)?|X{0,3}S|M|X{0,3}L|\dXL)\b[\s,/–-]*){2,})\s*$",
    re.I,
)


def _inline_extract(d: "Details") -> None:
    """Всё написано одной строкой без запятых: «кросы найк 40 41 42 цена 900000»."""
    t = d.title
    if d.price is None:
        best = None
        for m in INLINE_PRICE.finditer(t):
            v = _num(m.group(1))
            if v >= 1000 or (m.group(2) and v > 0):
                best = m
        if best:
            d.price = _num(best.group(1))
            if best.group(2):
                d.currency = _currency(best.group(2))
            t = (t[: best.start()] + " " + t[best.end():]).strip()
    if not d.sizes:
        m = INLINE_SIZES.search(t)
        if m and len(t[: m.start()].split()) >= 1:
            d.sizes = _split_sizes(m.group(1).strip(" ,/–-"))
            t = t[: m.start()].strip()
    d.title = re.sub(r"\s{2,}", " ", t).strip(" ,;–-") or d.title


def _split_sizes(s: str) -> list[str]:
    s = s.strip().strip(".")
    if re.fullmatch(r"(?i)one\s*size|os|универсальный", s):
        return ["One size"]
    s = re.sub(r"\s*[-–—]\s*", "–", s)
    parts = [p for p in re.split(r"[\s,;/]+", s) if p]
    return [p.upper() if re.fullmatch(r"[a-zA-Z]+", p) else p for p in parts]


CURRENCY_WORDS = re.compile(r"\$|€|₽|₸|₺|£|usd|eur|rub|uzs|kzt|сум|so['’`]?m|сўм|руб\w*|р\.|тенге|тг|евро|дол\w*", re.I)


def _is_price(seg: str) -> bool:
    """Сегмент целиком — это цена («12 900», «$120», «было 15000 стало 12000»)."""
    body = PRICE_PREFIX.sub("", seg)
    nums = NUM.findall(body)
    if not nums:
        return False
    rest = OLD_MARK.sub("", CURRENCY_WORDS.sub("", NUM.sub("", body)))
    if re.sub(r"[\s()\-–—:=>/,.]+", "", rest):
        return False
    if PRICE_PREFIX.match(seg) or _currency(seg):
        return True
    return max(_num(n) for n in nums) >= 100  # «40 41 42» — это размеры, а не цена


def parse_details(text: str, default_currency: str = "сум") -> Details:
    d = Details()
    text = (text or "").strip()
    lines = [ln.strip(" •-–—\t") for ln in text.splitlines() if ln.strip(" •-–—\t")]
    segs: list[str] = []
    for ln in lines:  # «Nike Air Force 1, 12900, 40-44» — части через запятую
        if _is_price(ln) or SIZE_PREFIX.match(ln):
            segs.append(ln)
        else:
            segs += [x for x in re.split(r"\s*[;|]\s*|,\s+", ln) if x.strip()]

    rest: list[str] = []
    for seg in segs:
        low = seg.lower()
        if low.startswith(("бренд", "brand")):
            d.brand = re.sub(r"^(бренд|brand)\s*[:\-–]?\s*", "", seg, flags=re.I).strip()
            continue
        if SIZE_PREFIX.match(seg):
            d.sizes += _split_sizes(SIZE_PREFIX.sub("", seg))
            continue
        if d.price is None and _is_price(seg):
            nums = [_num(n) for n in NUM.findall(PRICE_PREFIX.sub("", seg))]
            nums = [n for n in nums if n > 0]
            if nums:
                if len(nums) >= 2:
                    a, b = nums[0], nums[1]
                    d.old_price, d.price = max(a, b), min(a, b)
                    if d.old_price == d.price:
                        d.old_price = None
                else:
                    d.price = nums[0]
                d.currency = _currency(seg)
                continue
        if SIZE_SEQ.match(seg):
            d.sizes += _split_sizes(seg)
            continue
        rest.append(seg)

    if rest:
        d.title = rest[0]
        for r in rest[1:]:
            if len(r) <= 32:
                d.tags.append(r[:1].upper() + r[1:])
            else:
                d.note = (d.note + "\n" + r).strip()
    if d.title:
        _inline_extract(d)
    d.title = d.title or "Новинка"
    d.title = d.title[:1].upper() + d.title[1:]
    d.brand = d.brand or find_brand(text)
    d.currency = d.currency or default_currency
    d.category = guess_category(text)
    return d


def fmt_money(v: float | None, currency: str) -> str:
    if v is None:
        return ""
    if abs(v - round(v)) < 0.005:
        s = f"{int(round(v)):,}".replace(",", "\u00a0")
    else:
        s = f"{v:,.2f}".replace(",", "\u00a0").replace(".", ",")
    if currency in ("$", "£"):
        return f"{currency}{s}"
    return f"{s} {currency}".strip()


def channel_name(raw: str) -> str:
    """«@Modnaya_Lux», «t.me/Modnaya_Lux», «https://t.me/Modnaya_Lux» → «Modnaya_Lux» (закрытый -100… → «»)."""
    raw = (raw or "").strip()
    m = re.match(r"^(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{4,})", raw) or re.match(r"^@?([A-Za-z][A-Za-z0-9_]{3,})$", raw)
    return m.group(1) if m else ""


def to_card_info(d: Details, shop: str, contact: str, channel: str = "") -> CardInfo:
    discount = ""
    if d.price and d.old_price and d.old_price > d.price:
        discount = f"−{round((d.old_price - d.price) / d.old_price * 100)}%"
    return CardInfo(
        title=d.title,
        brand=d.brand,
        price=fmt_money(d.price, d.currency),
        old_price=fmt_money(d.old_price, d.currency) if d.old_price else "",
        discount=discount,
        sizes=d.sizes[:14],
        tags=d.tags[:5],
        shop=shop,
        contact=contact,
        channel=f"@{channel_name(channel)}" if channel_name(channel) else "",
        category=d.category,
    )


def _hashtag(s: str) -> str:
    return "#" + re.sub(r"[^\w]", "", s.replace(" ", "_").replace("&", "and")).lower()


def _visible_len(s: str) -> int:
    return len(html.unescape(re.sub(r"<[^>]+>", "", s)))


def build_caption(d: Details, info: CardInfo, description: str = "", hashtags: list[str] | None = None,
                  html_mode: bool = True, max_len: int = 1024) -> str:
    """Подпись к посту: название, описание, цена, размеры, детали, как заказать, хэштеги.

    max_len — лимит Telegram для подписи к фото (1024 символа); описание при необходимости сокращается.
    """
    b = (lambda s: f"<b>{html.escape(s)}</b>") if html_mode else (lambda s: s)
    e = html.escape if html_mode else (lambda s: s)
    lines = []
    if info.price:
        price = b(info.price)
        if info.old_price:
            old = f"<s>{e(info.old_price)}</s>" if html_mode else f"(было {info.old_price})"
            price = f"{price} {old} {info.discount}".strip()
        lines.append(f"💰 {price}")
    if info.sizes:
        lines.append(f"📏 Размеры: {e(' · '.join(info.sizes))}")
    for tag in info.tags:
        lines.append(f"✔️ {e(tag)}")
    tags = list(hashtags or [])
    if not tags:
        if info.brand:
            tags.append(_hashtag(info.brand))
        tags.append({"shoes": "#обувь", "home": "#посуда", "beauty": "#косметика"}.get(info.category, "#одежда"))
        tags.append("#новинка")
    tags = [t if t.startswith("#") else _hashtag(t) for t in tags]

    desc = (description or d.note or "").strip()
    while True:
        ch = info.channel.lstrip("@")
        channel_line = ""
        if ch:
            channel_line = (f'📲 Telegram-канал: <a href="https://t.me/{ch}">@{ch}</a>' if html_mode
                            else f"📲 Telegram-канал: t.me/{ch}")
        contacts = "\n".join(x for x in [f"📩 Заказ: {e(info.contact)}" if info.contact else "", channel_line] if x)
        parts = [b(info.title), e(desc) if desc else "", "\n".join(lines), contacts, " ".join(dict.fromkeys(tags))]
        text = "\n\n".join(p for p in parts if p)
        if _visible_len(text) <= max_len or (not desc and not tags):
            break
        if desc:  # сначала сокращаем описание по предложениям, потом убираем совсем
            cut = re.split(r"(?<=[.!?…])\s+", desc)
            desc = " ".join(cut[:-1]) if len(cut) > 1 else ""
        else:
            tags = tags[:-1]
    return text
