"""Шаблоны карточек товара.

Стили собраны по итогам анализа самых кликабельных карточек:
  • studio    — «Студия»: чистый фон, товар ~80% кадра, минимум текста (Wildberries/Ozon-лидеры, Zara, COS).
  • editorial — «Журнал»: кремовый фон, антиква, крупное слово за товаром (fashion-журналы, quiet luxury).
  • drop      — «Дроп»: яркий фон, огромный шрифт за товаром, ценник-стикер (стритвир/сникер-ресейл).
  • home      — «Дом»: лён, солнечная тень от окна, этикетка (интерьер/посуда, Pinterest-эстетика).
  • promo     — «Акция»: глубокий ягодный градиент, огромная скидка, антиква капсом, белая «кнопка»
                с ценой (промо-рассылки бьюти-брендов).
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass, field

from PIL import Image

from . import draw as D
from .draw import RGB
from .fonts import font
from .photo import ProductPhoto

POST = (1080, 1350)  # 4:5 — максимум площади в ленте Instagram и Telegram
STORY = (1080, 1920)  # 9:16

STYLES = ["studio", "editorial", "drop", "home", "promo"]
STYLE_NAMES = {"studio": "Студия", "editorial": "Журнал", "drop": "Дроп", "home": "Дом", "promo": "Акция"}
STYLE_HINTS = {
    "studio": "чистый маркетплейс-стиль, подходит всему",
    "editorial": "дорого и спокойно, для одежды и брендов",
    "drop": "ярко и дерзко, для кроссовок и стритвира",
    "home": "уютно, для посуды и вещей для дома",
    "promo": "для скидок и косметики",
}


@dataclass
class CardInfo:
    title: str = "Новинка"
    brand: str = ""
    price: str = ""
    old_price: str = ""
    discount: str = ""
    sizes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    shop: str = ""
    contact: str = ""
    category: str = "clothes"  # clothes | shoes | home | beauty
    logo: Image.Image | None = None  # круглый логотип магазина (RGBA)
    channel: str = ""  # Telegram-канал магазина, «@name»

    @property
    def handle(self) -> str:
        """Что подписать на карточке: личный контакт для заказов или канал."""
        return self.contact or self.channel


@dataclass
class Theme:
    key: str
    bg: RGB
    ink: RGB
    muted: RGB
    accent: RGB
    accent_ink: RGB
    sale: RGB = (229, 72, 77)
    title_family: str = "sans"
    title_weight: int = 700
    label_family: str = "sans"
    sticker: RGB = (255, 225, 77)
    sticker_ink: RGB = (17, 17, 17)


# ---------------- палитры ----------------

DROP_COLORS: list[RGB] = [
    (46, 75, 255),  # кобальт
    (255, 85, 51),  # томат
    (200, 240, 49),  # лайм
    (255, 79, 163),  # розовый
    (255, 138, 31),  # оранжевый
    (61, 220, 151),  # мята
    (123, 92, 255),  # фиолетовый
    (255, 210, 63),  # солнечный
]


PROMO_PALETTES: list[tuple[RGB, RGB, RGB]] = [
    ((70, 20, 46), (146, 66, 108), (238, 170, 198)),  # ягодный/сливовый
    ((18, 28, 68), (62, 82, 158), (168, 186, 240)),  # глубокий синий
]


def _hue(c: RGB) -> float:
    return colorsys.rgb_to_hsv(*(v / 255 for v in c))[0]


def _hsv(h: float, s: float, v: float) -> RGB:
    return tuple(int(round(c * 255)) for c in colorsys.hsv_to_rgb(h % 1.0, max(0, min(1, s)), max(0, min(1, v))))  # type: ignore[return-value]


def accent(p: ProductPhoto | None) -> RGB | None:
    """Самый «говорящий» цвет товара: насыщенный и заметный по площади (золото крышки, розовые тени…)."""
    if p is None or not p.palette:
        return None
    best, score = None, 0.0
    for c, share in p.palette:
        _, s, v = colorsys.rgb_to_hsv(*(x / 255 for x in c))
        if share < 0.04 or s < 0.16 or v < 0.14:
            continue
        sc = s * share ** 0.5 * (0.6 + 0.4 * v)
        if sc > score:
            best, score = c, sc
    return best


def _warmth(p: ProductPhoto | None) -> float:
    """Оттенок для нейтральных товаров: тёплый беж или холодный серый — по самому товару."""
    if p is None or not p.palette:
        return 0.08
    r = sum(c[0] * sh for c, sh in p.palette)
    b = sum(c[2] * sh for c, sh in p.palette)
    return 0.08 if r >= b else 0.6


def _drop_color(p: ProductPhoto | None) -> RGB:
    if p is None:
        return DROP_COLORS[0]
    acc = accent(p)
    if acc is None:
        if p.luminance > 0.58:
            return DROP_COLORS[0]  # белое/светлое — на кобальте
        if p.luminance < 0.32:
            return DROP_COLORS[2]  # чёрное — на лайме
        return DROP_COLORS[1]
    h = _hue(acc)

    def dist(c: RGB) -> float:
        d = abs(_hue(c) - h)
        return min(d, 1 - d)

    return max(DROP_COLORS, key=dist)  # контраст: цвет напротив главного цвета товара


def _promo_palette(p: ProductPhoto | None) -> tuple[RGB, RGB, RGB]:
    """Глубокий градиент в тон товару: золото → бронза, розовое → винный, беж → мокко."""
    if p is None:
        return PROMO_PALETTES[0]
    acc = accent(p)
    dark_item = p.luminance < 0.32
    if acc is not None:
        h, s, _ = colorsys.rgb_to_hsv(*(x / 255 for x in acc))
        s2 = min(0.78, max(0.5, s * 1.25))
        return (_hsv(h, s2, 0.31), _hsv(h, s2 * 0.72, 0.68 if dark_item else 0.6),
                _hsv(h, 0.22, 0.98))
    h = _warmth(p)
    if dark_item:  # чёрное платье: низ светлее, чтобы вещь читалась
        return _hsv(h, 0.2, 0.30), _hsv(h, 0.14, 0.64), _hsv(h, 0.06, 0.98)
    return _hsv(h, 0.34, 0.20), _hsv(h, 0.28, 0.43), _hsv(h, 0.14, 0.95)


def theme_for(style: str, p: ProductPhoto | None) -> Theme:
    """Палитра карточки подбирается под цвета конкретного товара."""
    l = p.luminance if p else 0.5
    acc = accent(p)
    hue = _hue(acc) if acc else _warmth(p)
    if style == "studio":
        v = 0.885 if l > 0.72 else (0.955 if l < 0.28 else 0.925)
        bg = _hsv(hue, 0.075 if acc else 0.045, v)
        return Theme("studio", bg, (22, 22, 22), (112, 106, 100), (22, 22, 22), (255, 255, 255))
    if style == "editorial":
        if l > 0.74:  # светлый товар — тёмная «люкс» версия в тон товару
            bg = _hsv(hue, 0.4 if acc else 0.22, 0.16)
            return Theme("editorial", bg, (240, 233, 222), (176, 165, 150), (240, 233, 222), bg,
                         title_family="serif", title_weight=500)
        return Theme("editorial", _hsv(hue, 0.085 if acc else 0.075, 0.95), (33, 29, 26), (128, 118, 106),
                     (33, 29, 26), (241, 235, 225), title_family="serif", title_weight=500)
    if style == "drop":
        bg = _drop_color(p)
        light = D.lum(bg) > 0.62
        ink = (17, 17, 17) if light else (255, 255, 255)
        muted = D.mix(ink, bg, 0.35)
        sticker, sticker_ink = ((17, 17, 17), (255, 255, 255)) if light else ((255, 225, 77), (17, 17, 17))
        return Theme("drop", bg, ink, muted, ink, bg, title_family="display", title_weight=700,
                     label_family="display", sticker=sticker, sticker_ink=sticker_ink)
    if style == "promo":
        top, bottom, glow = _promo_palette(p)
        return Theme("promo", top, (255, 255, 255), D.mix(glow, (255, 255, 255), 0.35), (255, 255, 255), top,
                     title_family="serif", title_weight=500, sticker=bottom, sticker_ink=glow)
    # home: лён в тон товару
    bg = _hsv(hue, 0.14 if acc else 0.11, 0.87 if l > 0.78 else 0.92)
    return Theme("home", bg, (58, 48, 40), (130, 116, 102), (58, 48, 40), (250, 246, 240),
                 title_family="serif", title_weight=500)


def background(t: Theme, w: int, h: int) -> Image.Image:
    if t.key == "studio":
        return D.bg_studio(w, h, t.bg)
    if t.key == "editorial":
        return D.bg_studio(w, h, t.bg, light=0.035)
    if t.key == "drop":
        return D.bg_spot(w, h, t.bg)
    if t.key == "promo":
        return D.bg_gradient(w, h, t.bg, t.sticker, t.sticker_ink, glow_strength=0.3)
    img = D.bg_linen(w, h, t.bg)
    img.alpha_composite(D.gobo_shadow(w, h))
    return img


# ---------------- общие элементы ----------------

def _chips(canvas, x, y_top, items, f, *, ink, bg=None, outline=True, max_w=10_000, h=None, gap=12, max_rows=1):
    """Ряд «таблеток» с размерами. Возвращает (ширина, высота) занятой области."""
    if not items:
        return 0, 0
    h = h or int(D.line_h(f) * 1.55)
    padx = int(h * 0.42)
    rows: list[list[tuple[str, int]]] = [[]]
    cur_w = 0
    for i, it in enumerate(items):
        w = max(h, int(D.text_w(it, f) + 2 * padx))
        need = w if not rows[-1] else cur_w + gap + w
        if need > max_w and rows[-1]:
            if len(rows) >= max_rows:
                rest = len(items) - i
                more = f"+{rest}"
                mw = max(h, int(D.text_w(more, f) + 2 * padx))
                while rows[-1] and cur_w + gap + mw > max_w:
                    cur_w -= rows[-1].pop()[1] + gap
                    rest += 1
                    more = f"+{rest}"
                rows[-1].append((more, mw))
                break
            rows.append([])
            cur_w = 0
            need = w
        rows[-1].append((it, w))
        cur_w = need
    total_w = 0
    yy = y_top
    for row in rows:
        xx = x
        for label, w in row:
            if outline and bg is None:
                chip = D.rounded(w, h, h / 2, outline=D.rgba(ink), width=max(1.5, h * 0.035))
            else:
                chip = D.rounded(w, h, h / 2, fill=D.rgba(bg or ink))
            D.paste(canvas, chip, xx, yy)
            fill = ink if (outline and bg is None) else (ink if bg is not None else (255, 255, 255))
            D.draw_text(canvas, xx + w / 2, yy + h / 2 + D.cap_h(f) / 2, label, f, fill, anchor="ms")
            xx += w + gap
        total_w = max(total_w, xx - gap - x)
        yy += h + gap
    return total_w, yy - gap - y_top


def _pill(canvas, x, y, text, f, *, fill, ink, padx=None, h=None, anchor="l", outline=False, tracking=0):
    h = h or int(D.line_h(f) * 1.6)
    padx = padx if padx is not None else int(h * 0.5)
    w = int(D.text_w(text, f, tracking) + 2 * padx)
    if anchor == "r":
        x -= w
    elif anchor == "m":
        x -= w / 2
    if outline:
        D.paste(canvas, D.rounded(w, h, h / 2, outline=D.rgba(fill), width=max(1.5, h * 0.04)), x, y)
    else:
        D.paste(canvas, D.rounded(w, h, h / 2, fill=D.rgba(fill)), x, y)
    D.draw_text(canvas, x + w / 2, y + h / 2 + D.cap_h(f) / 2, text, f, ink, tracking=tracking, anchor="ms")
    return w, h


def _strike(canvas, x, y_base, text, f, color, anchor="ls"):
    D.draw_text(canvas, x, y_base, text, f, color, anchor=anchor)
    w = D.text_w(text, f)
    x0 = x - (w if anchor[0] == "r" else w / 2 if anchor[0] == "m" else 0)
    yy = y_base - D.cap_h(f) * 0.42
    th = max(2, int(f.size * 0.07))
    D.paste(canvas, D.rounded(int(w + 6), th, th / 2, fill=D.rgba(color)), x0 - 3, yy - th / 2)
    return w


def _logo(canvas: Image.Image, info: CardInfo, x: float, y: float, size: int, anchor: str = "l") -> int:
    """Круглый логотип магазина с мягкой тенью. Возвращает его размер (0 — логотипа нет)."""
    if info.logo is None:
        return 0
    lg = info.logo.resize((size, size), Image.LANCZOS)
    if anchor == "m":
        x -= size / 2
    elif anchor == "r":
        x -= size
    blur = size * 0.05
    D.paste(canvas, D.soft_shadow(size, size, size / 2, blur, 0.28), x - int(blur * 3), y - int(blur * 3) + size * 0.03)
    D.paste(canvas, lg, x, y)
    return size


def _cut(p: ProductPhoto | None, use_cutout: bool) -> bool:
    """Показывать ли товар вырезанным: фон отделился и товар не обрезан краем кадра."""
    return bool(use_cutout and p is not None and p.cutout is not None and not p.cropped)


def _place(canvas, photo, box, t: Theme, info: CardInfo, use_cutout: bool, *, fill=0.88, rotate=0.0,
           shadow=None, strength=1.0, photo_style=None):
    """Товар: вырезанный (если получилось) или фото-карточкой."""
    if photo is None:
        return tuple(int(v) for v in box)
    if _cut(photo, use_cutout):
        if shadow is None:
            shadow = "ground" if info.category in ("shoes", "home") else "float"
        valign = "bottom" if shadow == "ground" else "center"
        light_dx = (box[2] - box[0]) * 0.02 if t.key == "home" else 0.0
        return D.place_cutout(canvas, photo.cutout, box, fill=fill, valign=valign, shadow=shadow,
                              strength=strength, rotate=rotate, light_dx=light_dx)
    ps = dict(photo_style or {})
    if photo.from_screenshot:  # картинка из галереи магазина — показываем целиком, без обрезки
        ps["contain"] = True
    return D.place_photo(canvas, photo.image, box, **ps)


def _masthead_word(info: CardInfo) -> str:
    if info.brand:
        return info.brand
    words = info.title.split()
    return words[0] if words else "New"


# ---------------- обложки ----------------

def cover_studio(p, info, t, size, safe_top, safe_bottom, use_cutout):
    w, h = size
    m = int(w * 0.06)
    img = background(t, w, h)
    top = safe_top + m

    f_shop = font("sans", int(w * 0.024), 700)
    head = _logo(img, info, m, top - int(w * 0.025), int(w * 0.12)) - int(w * 0.025)
    if not head > 0:
        D.draw_text(img, m, top + D.cap_h(f_shop), (info.shop or "").upper(), f_shop, t.ink, tracking=w * 0.004)
    f_tag = font("sans", int(w * 0.02), 600)
    x_r = w - m
    for tag in reversed(info.tags[:2]):
        tw, th = _pill(img, x_r, top - int(w * 0.012), tag.upper(), f_tag, fill=t.ink, ink=t.ink,
                       anchor="r", outline=True, tracking=w * 0.002, h=int(w * 0.044))
        x_r -= tw + int(w * 0.012)

    # нижний блок
    f_title, lines = D.fit_lines(info.title, "sans", 750, w - 2 * m, 2, int(w * 0.052), int(w * 0.036))
    f_brand = font("sans", int(w * 0.022), 600)
    f_chip = font("sans", int(w * 0.024), 600)
    f_price = font("sans", int(w * 0.042), 800)
    chip_h = int(w * 0.05)
    price_h = int(w * 0.078)
    title_lh = int(D.line_h(f_title) * 0.98)
    block = (D.cap_h(f_brand) + int(w * 0.022) if info.brand else 0) + title_lh * len(lines) + int(w * 0.02) + max(chip_h, price_h)
    y = h - safe_bottom - m - block

    # товар
    box = (m, top + max(int(w * 0.06), head + int(w * 0.015)), w - m, y - int(w * 0.03))
    bb = _place(img, p, box, t, info, use_cutout, fill=0.9,
                photo_style={"radius": w * 0.026, "shadow": 0.18})

    if info.discount:
        f_disc = font("sans", int(w * 0.03), 800)
        _pill(img, max(m, bb[0]), max(top + int(w * 0.07), bb[1]), info.discount, f_disc, fill=t.sale,
              ink=(255, 255, 255), h=int(w * 0.06))

    if info.brand:
        D.draw_text(img, m, y + D.cap_h(f_brand), info.brand.upper(), f_brand, t.muted, tracking=w * 0.003)
        y += D.cap_h(f_brand) + int(w * 0.022)
    for ln in lines:
        D.draw_text(img, m, y + D.cap_h(f_title) + (title_lh - D.line_h(f_title)) // 2 + int(title_lh * 0.08), ln, f_title, t.ink)
        y += title_lh
    y += int(w * 0.02)
    row_h = max(chip_h, price_h)

    pw = 0
    if info.price:
        pw, _ = _pill(img, w - m, y + (row_h - price_h) / 2, info.price, f_price, fill=t.accent, ink=t.accent_ink,
                      anchor="r", h=price_h, padx=int(price_h * 0.42))
        if info.old_price:
            f_old = font("sans", int(w * 0.026), 600)
            _strike(img, w - m - pw - int(w * 0.02), y + row_h / 2 + D.cap_h(f_old) / 2, info.old_price, f_old,
                    t.muted, anchor="rs")
            pw += int(D.text_w(info.old_price, f_old) + w * 0.03)
    _chips(img, m, y + (row_h - chip_h) / 2, info.sizes, f_chip, ink=t.ink, h=chip_h,
           max_w=w - 2 * m - pw - int(w * 0.03), gap=int(w * 0.011))
    return img


def cover_editorial(p, info, t, size, safe_top, safe_bottom, use_cutout):
    w, h = size
    m = int(w * 0.06)
    img = background(t, w, h)
    inset = int(w * 0.03)
    frame = D.rounded(w - 2 * inset, h - 2 * inset - safe_top - safe_bottom, 0, outline=D.rgba(t.ink, 0.55), width=1.6)
    top = safe_top + m

    f_small = font("sans", int(w * 0.017), 650)
    f_shop = font("serif", int(w * 0.03), 500)
    ls = _logo(img, info, w / 2, top - int(w * 0.02), int(w * 0.11), "m")
    extra = max(0, ls - D.cap_h(f_shop) - int(w * 0.035))
    if not ls:
        D.draw_text(img, w / 2, top + D.cap_h(f_shop), (info.shop or "").upper(), f_shop, t.ink,
                    tracking=w * 0.009, anchor="ms")
    D.draw_text(img, m, top + D.cap_h(f_shop), "NEW IN", f_small, t.muted, tracking=w * 0.004)
    if info.handle:
        D.draw_text(img, w - m, top + D.cap_h(f_shop), info.handle.upper(), f_small, t.muted, tracking=w * 0.003,
                    anchor="rs")

    word = _masthead_word(info)
    f_mast = D.fit_one_line(word, "serif_italic", 450, w - 2 * m * 1.3, int(w * 0.2), int(w * 0.08))
    mast_y = top + extra + int(w * 0.07) + D.cap_h(f_mast)
    D.draw_text(img, w / 2, mast_y, word, f_mast, t.ink, anchor="ms")

    # низ
    f_title, lines = D.fit_lines(info.title.upper(), "sans", 600, w - 2 * m * 1.4, 2, int(w * 0.024), int(w * 0.018))
    f_price = font("serif", int(w * 0.05), 500)
    f_sizes = font("sans", int(w * 0.02), 500)
    tl = int(D.line_h(f_title) * 1.15)
    block = tl * len(lines) + int(w * 0.03) + D.cap_h(f_price) + (int(w * 0.05) if info.sizes else 0)
    y = h - safe_bottom - m - int(w * 0.02) - block

    has_cut = _cut(p, use_cutout)
    box_top = mast_y - (D.cap_h(f_mast) * 0.45 if has_cut else -int(w * 0.035))
    box = (m, box_top, w - m, y - int(w * 0.03))
    _place(img, p, box, t, info, use_cutout, fill=0.92 if has_cut else 1.0,
           photo_style={"radius": 0, "shadow": 0.0, "border": 0})
    D.paste(img, frame, inset, inset + safe_top)

    for ln in lines:
        y += tl
        D.draw_text(img, w / 2, y, ln, f_title, t.ink, tracking=w * 0.004, anchor="ms")
    y += int(w * 0.03) + D.cap_h(f_price)
    if info.price:
        if info.old_price:
            f_old = font("serif", int(w * 0.032), 400)
            gap = int(w * 0.025)
            pw = D.text_w(info.price, f_price)
            ow = D.text_w(info.old_price, f_old)
            x0 = w / 2 - (pw + ow + gap) / 2
            _strike(img, x0, y, info.old_price, f_old, t.muted)
            D.draw_text(img, x0 + ow + gap, y, info.price, f_price, t.ink)
        else:
            D.draw_text(img, w / 2, y, info.price, f_price, t.ink, anchor="ms")
    if info.sizes:
        y += int(w * 0.05)
        D.draw_text(img, w / 2, y, "  ·  ".join(info.sizes), f_sizes, t.muted, tracking=w * 0.002, anchor="ms")
    return img


def cover_drop(p, info, t, size, safe_top, safe_bottom, use_cutout):
    w, h = size
    m = int(w * 0.06)
    img = background(t, w, h)
    top = safe_top + m
    has_cut = _cut(p, use_cutout)

    # огромные буквы за товаром: бренд сплошным, модель контуром; товар перекрывает их низ
    title_words = info.title.upper().split()
    if info.brand:
        first = info.brand.upper()
        bw = first.split()
        idx = next((i for i in range(len(title_words)) if title_words[i:i + len(bw)] == bw), None)
        tail = title_words[idx + len(bw):] if idx is not None else [x for x in title_words if x not in bw]
    else:
        first, tail = (title_words[0] if title_words else "NEW"), title_words[1:]
    f0 = D.fit_one_line(first, "display", 900, w - m, int(w * 0.3), int(w * 0.055))
    if D.text_w(first, f0) > w - m and " " in first:  # длинный бренд — в две строки
        first, rest_brand = first.split(" ", 1)
        f0 = D.fit_one_line(first, "display", 900, w - m, int(w * 0.3), int(w * 0.06))
        tail = [rest_brand]
    lines_big = [first]
    fonts_big = [f0]
    for k in (2, 1):  # вторая строка контуром — только если влезает по ширине
        cand = " ".join(tail[:k])
        if cand:
            f1 = D.fit_one_line(cand, "display", 900, w - m, f0.size, int(w * 0.07))
            if D.text_w(cand, f1) <= w - m:
                lines_big.append(cand)
                fonts_big.append(f1)
                break
    shift = int(w * 0.03) if info.logo is not None else 0
    yb = top + int(w * 0.09) + shift
    for i, (ln, f) in enumerate(zip(lines_big, fonts_big)):
        yb += D.cap_h(f)
        if i == 0:
            D.draw_text(img, w / 2, yb, ln, f, t.ink, anchor="ms")
        else:
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            from PIL import ImageDraw
            ImageDraw.Draw(layer).text((w / 2, yb), ln, font=f, fill=(0, 0, 0, 0), anchor="ms",
                                       stroke_width=max(2, int(f.size * 0.022)), stroke_fill=D.rgba(t.ink))
            img.alpha_composite(layer)
        yb += int(w * 0.025)
    first_cap = D.cap_h(fonts_big[0])

    f_lab = font("display", int(w * 0.019), 600)
    _pill(img, m, top - int(w * 0.012), "NEW DROP", f_lab, fill=t.ink, ink=t.ink, outline=True, h=int(w * 0.05),
          tracking=w * 0.002)
    if not _logo(img, info, w - m, top - int(w * 0.025), int(w * 0.11), "r"):
        D.draw_text(img, w - m, top + int(w * 0.013) + D.cap_h(f_lab) / 2, (info.shop or "").upper(), f_lab, t.ink,
                    anchor="rs", tracking=w * 0.002)

    rotate = 0.0
    if has_cut and info.category == "shoes" and p.cutout.width > p.cutout.height * 1.2:
        rotate = 10.0
    box_top = top + int(w * 0.09) + shift + first_cap * (0.6 if has_cut else 1.25)
    box = (m * 0.6, box_top, w - m * 0.6, h - safe_bottom - int(w * 0.22))
    _place(img, p, box, t, info, use_cutout, fill=0.94, rotate=rotate, shadow="float", strength=1.5,
           photo_style={"radius": w * 0.02, "border": w * 0.018, "border_color": (255, 255, 255), "rotate": -3,
                        "shadow": 0.3})

    # низ: название + размеры слева, стикер-цена справа
    sticker_w = int(w * 0.33)
    f_title, lines = D.fit_lines(info.title.upper(), "display", 700, w - 2 * m - sticker_w, 2, int(w * 0.034),
                                 int(w * 0.024))
    f_chip = font("display", int(w * 0.02), 600)
    chip_h = int(w * 0.05)
    tl = int(D.line_h(f_title) * 1.05)
    y = h - safe_bottom - m - (chip_h + int(w * 0.022) if info.sizes else 0) - tl * len(lines)
    for ln in lines:
        y += tl
        D.draw_text(img, m, y - (tl - D.cap_h(f_title)) / 2 + int(tl * 0.05), ln, f_title, t.ink)
    if info.sizes:
        _chips(img, m, y + int(w * 0.022), info.sizes, f_chip, ink=t.bg, bg=t.ink, outline=False, h=chip_h,
               max_w=w - 2 * m - sticker_w, gap=int(w * 0.01))

    if info.price:
        f_price = D.fit_one_line(info.price, "display", 800, sticker_w * 0.82, int(w * 0.042), int(w * 0.024))
        f_old = font("display", int(w * 0.022), 500)
        sh = int(w * 0.15) if info.old_price else int(w * 0.115)
        st = Image.new("RGBA", (sticker_w, sh), (0, 0, 0, 0))
        D.paste(st, D.rounded(sticker_w, sh, sh * 0.22, fill=D.rgba(t.sticker)), 0, 0)
        if info.old_price:
            _strike(st, sticker_w / 2, sh * 0.36, info.old_price, f_old, D.mix(t.sticker_ink, t.sticker, 0.35),
                    anchor="ms")
            D.draw_text(st, sticker_w / 2, sh * 0.78, info.price, f_price, t.sticker_ink, anchor="ms")
        else:
            D.draw_text(st, sticker_w / 2, sh / 2 + D.cap_h(f_price) / 2, info.price, f_price, t.sticker_ink,
                        anchor="ms")
        st = st.rotate(5, resample=Image.BICUBIC, expand=True)
        D.paste(img, st, w - m - st.width + int(w * 0.01), h - safe_bottom - m - st.height + int(w * 0.01))
        if info.discount:
            f_d = font("display", int(w * 0.026), 800)
            ds = int(w * 0.12)
            badge = D.ellipse(ds, ds, fill=D.rgba(t.sale))
            D.draw_text(badge, ds / 2, ds / 2 + D.cap_h(f_d) / 2, info.discount, f_d, (255, 255, 255), anchor="ms")
            D.paste(img, badge, w - m - st.width - ds * 0.35, h - safe_bottom - m - st.height - ds * 0.6)
    return img


def cover_home(p, info, t, size, safe_top, safe_bottom, use_cutout):
    w, h = size
    m = int(w * 0.06)
    img = background(t, w, h)
    top = safe_top + m

    # этикетка внизу
    f_small = font("sans", int(w * 0.018), 650)
    f_price = font("sans", int(w * 0.04), 800)
    f_old = font("sans", int(w * 0.024), 600)
    f_tags = font("sans", int(w * 0.021), 500)
    pad = int(w * 0.045)
    panel_w = w - 2 * m
    price_w = max(D.text_w(info.price, f_price), D.text_w(info.old_price, f_old)) if info.price else 0
    title_w = panel_w - 2 * pad - (price_w + int(w * 0.04) if price_w else 0)
    f_title, lines = D.fit_lines(info.title, "serif", 500, title_w, 2, int(w * 0.046), int(w * 0.03))
    tl = int(D.line_h(f_title) * 0.98)
    extra = " · ".join(info.tags[:3] + ([("Размеры: " + ", ".join(info.sizes))] if info.sizes else []))
    g1, g2 = int(w * 0.03), int(w * 0.035)
    panel_h = (pad + D.cap_h(f_small) + g1 + D.cap_h(f_title) + tl * (len(lines) - 1)
               + (g2 + D.cap_h(f_tags) if extra else 0) + int(pad * 1.1))
    py = h - safe_bottom - m - panel_h

    ls = _logo(img, info, m, top - int(w * 0.025), int(w * 0.12))
    box = (m, top + ls * 0.55, w - m, py - int(w * 0.03))
    _place(img, p, box, t, info, use_cutout, fill=0.9,
           photo_style={"radius": w * 0.03, "shadow": 0.16})

    D.paste(img, D.soft_shadow(panel_w, panel_h, w * 0.03, w * 0.025, 0.12), m - w * 0.075, py - w * 0.065)
    D.paste(img, D.rounded(panel_w, panel_h, w * 0.03, fill=(251, 248, 243, 242)), m, py)
    x = m + pad
    y = py + pad + D.cap_h(f_small)
    D.draw_text(img, x, y, (info.shop or "").upper(), f_small, t.muted, tracking=w * 0.004)
    y += g1 + D.cap_h(f_title)
    for i, ln in enumerate(lines):
        D.draw_text(img, x, y + tl * i, ln, f_title, t.ink)
    y += tl * (len(lines) - 1)
    if extra:
        y += g2 + D.cap_h(f_tags)
        lines_x, _ = D.wrap(extra, f_tags, panel_w - 2 * pad, 1)
        D.draw_text(img, x, y, lines_x[0], f_tags, t.muted)

    if info.price:
        xr = m + panel_w - pad
        yb = py + pad + D.cap_h(f_small) + g1 + D.cap_h(f_title)
        D.draw_text(img, xr, yb, info.price, f_price, t.ink, anchor="rs")
        if info.old_price:
            _strike(img, xr, yb - D.cap_h(f_price) - int(w * 0.022), info.old_price, f_old, t.muted, anchor="rs")
    if info.discount:
        f_d = font("sans", int(w * 0.024), 800)
        _pill(img, m + panel_w - pad, py - int(w * 0.03), info.discount, f_d, fill=t.sale, ink=(255, 255, 255),
              anchor="r", h=int(w * 0.055))
    return img


def cover_promo(p, info, t, size, safe_top, safe_bottom, use_cutout):
    w, h = size
    m = int(w * 0.07)
    img = background(t, w, h)
    white = (255, 255, 255)
    y = safe_top + int(w * 0.06)

    _logo(img, info, m * 0.6, y - int(w * 0.03), int(w * 0.1))
    f_k = font("sans", int(w * 0.028), 400)
    kicker = ("НОВЫЕ ПОСТУПЛЕНИЯ" if not info.discount else "ВЫГОДНОЕ ПРЕДЛОЖЕНИЕ") + "!"
    D.draw_text(img, w / 2, y + D.cap_h(f_k), kicker, f_k, white, tracking=w * 0.002, anchor="ms")
    y += D.cap_h(f_k) + int(w * 0.05)

    small = "СКИДКА" if info.discount else (info.shop or "NEW IN").upper()
    head = info.discount if info.discount else (info.brand or "Новинка").upper()
    f_s = font("sans", int(w * 0.024), 700)
    D.draw_text(img, w / 2, y + D.cap_h(f_s), small, f_s, white, tracking=w * 0.002, anchor="ms")
    y += D.cap_h(f_s) + int(w * 0.025)
    f_head = D.fit_one_line(head, "sans", 800, w - 2 * m, int(w * 0.16), int(w * 0.07))
    y += D.cap_h(f_head)
    D.draw_text(img, w / 2, y, head, f_head, white, anchor="ms")

    f_sub, lines = D.fit_lines(info.title.upper(), "serif", 400, w - 2 * m, 2, int(w * 0.05), int(w * 0.032))
    sl = int(D.line_h(f_sub) * 0.92)
    y += int(w * 0.02)
    for ln in lines:
        y += sl
        D.draw_text(img, w / 2, y, ln, f_sub, white, anchor="ms")

    if info.price:
        f_p = font("sans", int(w * 0.042), 700)
        bw = int(max(w * 0.6, D.text_w(info.price, f_p) + w * 0.12))
        bh = int(w * 0.085)
        y += int(w * 0.04)
        D.paste(img, D.rounded(bw, bh, 2, fill=D.rgba(white)), (w - bw) / 2, y)
        D.draw_text(img, w / 2, y + bh / 2 + D.cap_h(f_p) / 2, info.price, f_p, t.accent_ink, anchor="ms")
        y += bh
        if info.old_price:
            f_o = font("sans", int(w * 0.026), 500)
            y += int(w * 0.042)
            _strike(img, w / 2, y, info.old_price, f_o, t.muted, anchor="ms")

    foot = []
    if info.sizes:
        foot.append("Размеры: " + " · ".join(info.sizes))
    if info.tags:
        foot.append(" · ".join(info.tags[:3]))
    f_f = font("sans", int(w * 0.022), 500)
    fl = int(D.line_h(f_f) * 1.1)
    fy = h - safe_bottom - int(w * 0.05) - fl * (len(foot) - 1)

    box = (m * 0.5, y + int(w * 0.02), w - m * 0.5, fy - D.cap_h(f_f) - int(w * 0.02))
    _place(img, p, box, t, info, use_cutout, fill=0.98, shadow="ground" if info.category != "clothes" else "float",
           photo_style={"radius": 0, "shadow": 0.3})
    for i, ln in enumerate(foot):
        D.draw_text(img, w / 2, fy + i * fl, ln, f_f, t.muted, anchor="ms")
    return img


COVERS = {"studio": cover_studio, "editorial": cover_editorial, "drop": cover_drop, "home": cover_home,
          "promo": cover_promo}


def _safe(size) -> tuple[int, int]:
    w, h = size
    if h / w > 1.6:  # сторис: верх и низ закрывает интерфейс Instagram
        return int(h * 0.11), int(h * 0.13)
    return 0, 0


def render_cover(style: str, p: ProductPhoto | None, info: CardInfo, size=POST, use_cutout=True) -> Image.Image:
    t = theme_for(style, p)
    st, sb = _safe(size)
    img = COVERS[style](p, info, t, size, st, sb, use_cutout)
    return D.add_grain(img, 2.6 if style != "drop" else 2.2)


# ---------------- остальные слайды карусели ----------------

def render_slide(style: str, p: ProductPhoto, info: CardInfo, index: int, total: int, size=POST,
                 use_cutout=True, cover_photo: ProductPhoto | None = None) -> Image.Image:
    """Дополнительное фото товара в том же оформлении (единый стиль ленты)."""
    t = theme_for(style, cover_photo or p)
    w, h = size
    m = int(w * 0.06)
    img = background(t, w, h)
    f_lab = font(t.label_family, int(w * 0.022), 650)
    top = m
    ls = _logo(img, info, m, top - int(w * 0.025), int(w * 0.1))
    if not ls:
        D.draw_text(img, m, top + D.cap_h(f_lab), (info.shop or "").upper(), f_lab, t.ink, tracking=w * 0.004)
    D.draw_text(img, w - m, top + D.cap_h(f_lab), f"{index}/{total}", f_lab, t.muted, anchor="rs")
    bottom = h - m - int(w * 0.06)
    box = (m, top + max(int(w * 0.07), ls), w - m, bottom)
    # слайды из скриншотов (текстуры, инфографика, фото с моделью) — без вырезания, как в магазине
    _place(img, p, box, t, info, use_cutout and not p.from_screenshot, fill=0.92,
           shadow="float" if t.key == "drop" else None, strength=1.3 if t.key == "drop" else 1.0,
           photo_style={"radius": 0 if t.key == "editorial" else w * 0.026, "shadow": 0.16})
    caption = info.handle or info.title
    D.draw_text(img, w / 2, h - m, caption, font("sans", int(w * 0.022), 600), t.muted, anchor="ms")
    if t.key == "editorial":
        inset = int(w * 0.03)
        D.paste(img, D.rounded(w - 2 * inset, h - 2 * inset, 0, outline=D.rgba(t.ink, 0.55), width=1.6), inset, inset)
    return D.add_grain(img, 2.4)


def render_info(style: str, p: ProductPhoto | None, info: CardInfo, size=POST, use_cutout=True) -> Image.Image:
    """Финальный слайд: цена, размеры, детали, как заказать."""
    t = theme_for(style, p)
    w, h = size
    m = int(w * 0.07)
    img = background(t, w, h)
    if t.key == "home":
        img = D.bg_linen(w, h, t.bg)
    f_lab = font(t.label_family, int(w * 0.022), 650)
    ls = _logo(img, info, m, m - int(w * 0.025), int(w * 0.1))
    if not ls:
        D.draw_text(img, m, m + D.cap_h(f_lab), (info.shop or "").upper(), f_lab, t.ink, tracking=w * 0.004)

    thumb = int(w * 0.34)
    if p is not None:
        tb = (w - m - thumb, m + int(w * 0.06), w - m, m + int(w * 0.06) + thumb)
        _place(img, p, tb, t, info, use_cutout, fill=0.95, shadow="float",
               photo_style={"radius": w * 0.025, "shadow": 0.14})

    y = m + max(int(w * 0.11), ls + int(w * 0.02))
    tw = w - 2 * m - (thumb + int(w * 0.04) if p is not None else 0)
    f_title, lines = D.fit_lines(info.title, t.title_family, t.title_weight, tw, 3, int(w * 0.06), int(w * 0.04))
    tl = int(D.line_h(f_title) * 1.02)
    for ln in lines:
        y += tl
        D.draw_text(img, m, y - int(tl * 0.2), ln, f_title, t.ink)
    y = max(y + int(w * 0.07), m + int(w * 0.06) + thumb + int(w * 0.07))

    f_sec = font("sans", int(w * 0.019), 700)

    def section(label: str) -> None:
        nonlocal y
        D.draw_text(img, m, y + D.cap_h(f_sec), label.upper(), f_sec, t.muted, tracking=w * 0.004)
        y += D.cap_h(f_sec) + int(w * 0.03)

    if info.price:
        section("Цена")
        f_price = font(t.title_family if t.key != "studio" else "sans", int(w * 0.075), 800 if t.key in ("studio", "drop") else 500)
        D.draw_text(img, m, y + D.cap_h(f_price), info.price, f_price, t.ink)
        x = m + D.text_w(info.price, f_price) + int(w * 0.035)
        if info.old_price:
            f_old = font("sans", int(w * 0.034), 600)
            x += _strike(img, x, y + D.cap_h(f_price), info.old_price, f_old, t.muted) + int(w * 0.025)
        if info.discount:
            f_d = font("sans", int(w * 0.026), 800)
            _pill(img, x, y + D.cap_h(f_price) - int(w * 0.05), info.discount, f_d, fill=t.sale, ink=(255, 255, 255),
                  h=int(w * 0.054))
        y += D.cap_h(f_price) + int(w * 0.065)

    if info.sizes:
        section("Размеры в наличии")
        f_chip = font("sans", int(w * 0.03), 650)
        _, ch = _chips(img, m, y, info.sizes, f_chip, ink=t.ink, h=int(w * 0.068), max_w=w - 2 * m,
                       gap=int(w * 0.014), max_rows=3)
        y += ch + int(w * 0.065)

    cta_h = int(w * 0.15)
    cta_y = h - m - cta_h
    if info.tags:
        f_tag = font("sans", int(w * 0.03), 550)
        cs = int(w * 0.04)
        step = int(w * 0.058)
        head = D.cap_h(f_sec) + int(w * 0.03)
        n = min(len(info.tags), 5, max(0, (cta_y - int(w * 0.04) - y - head) // step))
        if n:
            section("Детали")
            for tag in info.tags[:n]:
                D.paste(img, D.checkmark(cs, D.rgba(t.ink)), m - int(cs * 0.1), y - int(cs * 0.05))
                D.draw_text(img, m + cs + int(w * 0.015), y + cs * 0.5 + D.cap_h(f_tag) / 2, tag, f_tag, t.ink)
                y += step

    # призыв к действию
    ch, cy = cta_h, cta_y
    D.paste(img, D.rounded(w - 2 * m, ch, w * 0.03, fill=D.rgba(t.accent)), m, cy)
    f_c1 = font("sans", int(w * 0.024), 600)
    if info.contact:
        lead, main = "Чтобы заказать — напишите", info.contact
    elif info.channel:
        lead, main = "Заказ и новинки — в нашем Telegram", info.channel
    else:
        lead, main = "Чтобы заказать —", "пишите в комментарии"
    f_c2 = D.fit_one_line(main, t.title_family if t.key == "drop" else "sans",
                          800 if t.key != "drop" else 700, w - 2 * m - int(w * 0.1), int(w * 0.05), int(w * 0.03))
    D.draw_text(img, w / 2, cy + ch * 0.38, lead, f_c1, D.mix(t.accent_ink, t.accent, 0.3), anchor="ms")
    D.draw_text(img, w / 2, cy + ch * 0.78, main, f_c2, t.accent_ink, anchor="ms")
    if info.contact and info.channel:  # под плашкой — ещё и канал
        f_ch = font("sans", int(w * 0.022), 600)
        D.draw_text(img, w / 2, cy - int(w * 0.025), f"Telegram-канал: {info.channel}", f_ch, t.muted, anchor="ms")
    return D.add_grain(img, 2.4)


# ---------------- превью всех стилей ----------------

def _star(size: int, color) -> Image.Image:
    import math

    def fn(d, sc):
        r1, r2 = size * sc / 2, size * sc / 2 * 0.45
        c = size * sc / 2
        pts = [(c + (r1 if i % 2 == 0 else r2) * math.sin(i * math.pi / 5),
                c - (r1 if i % 2 == 0 else r2) * math.cos(i * math.pi / 5)) for i in range(10)]
        d.polygon(pts, fill=color)

    return D.shape(size, size, fn)


def render_preview(p: ProductPhoto | None, info: CardInfo, use_cutout=True, recommended: str | None = None) -> Image.Image:
    """Обложки во всех стилях (2 колонки) с подписями — чтобы выбрать глазами."""
    tw, th = 540, 675
    gap, strip = 10, 64
    rows = (len(STYLES) + 2) // 2
    sheet = Image.new("RGBA", (tw * 2 + gap, (th + strip) * rows + gap * (rows - 1)), (255, 255, 255, 255))
    f = font("sans", 28, 700)
    for i, style in enumerate(STYLES):
        cov = render_cover(style, p, info, POST, use_cutout).resize((tw, th), Image.LANCZOS)
        x, y = (i % 2) * (tw + gap), (i // 2) * (th + strip + gap)
        sheet.paste(cov, (x, y))
        label = f"{i + 1}. {STYLE_NAMES[style]}"
        D.draw_text(sheet, x + 14, y + th + 42, label, f, (17, 17, 17))
        if style == recommended:
            lx = x + 14 + D.text_w(label, f) + 14
            D.paste(sheet, _star(30, (255, 176, 0, 255)), lx, y + th + 17)
            D.draw_text(sheet, lx + 38, y + th + 42, "совет", font("sans", 22, 600), (140, 140, 140))
    if len(STYLES) % 2:  # пустая клетка — подсказка
        x, y = tw + gap, (len(STYLES) // 2) * (th + strip + gap)
        D.paste(sheet, D.rounded(tw, th, 24, fill=(244, 242, 239, 255)), x, y)
        fb = font("sans", 34, 700)
        fr = font("sans", 25, 500)
        yy = y + 120
        D.draw_text(sheet, x + 44, yy, "Как выбрать", fb, (17, 17, 17))
        for ln in [f"{i + 1}. {STYLE_NAMES[s_]} — {STYLE_HINTS[s_]}" for i, s_ in enumerate(STYLES)]:
            for part in D.wrap(ln, fr, tw - 88, 2)[0]:
                yy += 40
                D.draw_text(sheet, x + 44, yy + 16, part, fr, (70, 70, 70))
            yy += 16
        D.paste(sheet, _star(28, (255, 176, 0, 255)), x + 44, yy + 50)
        D.draw_text(sheet, x + 82, yy + 72, "— совет для этого товара", fr, (70, 70, 70))
    return sheet.convert("RGB")


def recommend_style(info: CardInfo) -> str:
    if info.discount or info.category == "beauty":
        return "promo"
    return {"shoes": "studio", "home": "home"}.get(info.category, "editorial")


def render_set(style: str, photos: list[ProductPhoto], info: CardInfo, use_cutout=True) -> dict[str, list[Image.Image] | Image.Image]:
    """Полный комплект: карусель (обложка + фото + инфо) и сторис."""
    main = photos[0] if photos else None
    slides: list[Image.Image] = [render_cover(style, main, info, POST, use_cutout)]
    extra = photos[1:8]
    total = len(extra) + 2
    for i, ph in enumerate(extra):
        slides.append(render_slide(style, ph, info, i + 2, total, POST, use_cutout, cover_photo=main))
    slides.append(render_info(style, main, info, POST, use_cutout))
    story = render_cover(style, main, info, STORY, use_cutout)
    return {"slides": slides, "story": story}
