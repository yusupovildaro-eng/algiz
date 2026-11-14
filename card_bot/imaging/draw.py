"""Низкоуровневые помощники рисования: сглаженные фигуры, текст, фоны, тени, зерно."""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .fonts import font, safe

RGB = tuple[int, int, int]
SS = 4  # суперсэмплинг для гладких фигур


# ---------- цвет ----------

def lum(c: RGB) -> float:
    return (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) / 255


def mix(a: RGB, b: RGB, t: float) -> RGB:
    return tuple(int(round(a[i] * (1 - t) + b[i] * t)) for i in range(3))  # type: ignore[return-value]


def rgba(c: RGB, a: float = 1.0) -> tuple[int, int, int, int]:
    return (c[0], c[1], c[2], int(round(255 * a)))


# ---------- композиция ----------

def paste(canvas: Image.Image, layer: Image.Image, x: float, y: float) -> None:
    """alpha_composite, который понимает отрицательные координаты и вылеты за край."""
    x, y = int(round(x)), int(round(y))
    lw, lh = layer.size
    cw, ch = canvas.size
    sx0, sy0 = max(0, -x), max(0, -y)
    sx1, sy1 = min(lw, cw - x), min(lh, ch - y)
    if sx1 <= sx0 or sy1 <= sy0:
        return
    if layer.mode != "RGBA":
        layer = layer.convert("RGBA")
    canvas.alpha_composite(layer, dest=(x + sx0, y + sy0), source=(sx0, sy0, sx1, sy1))


def shape(w: int, h: int, fn) -> Image.Image:
    """Рисует фигуру в 4× и уменьшает — получаются гладкие края."""
    big = Image.new("RGBA", (max(1, w) * SS, max(1, h) * SS), (0, 0, 0, 0))
    fn(ImageDraw.Draw(big), SS)
    return big.resize((max(1, w), max(1, h)), Image.LANCZOS)


def rounded(w: int, h: int, r: float, fill=None, outline=None, width: float = 0) -> Image.Image:
    def fn(d: ImageDraw.ImageDraw, s: int) -> None:
        d.rounded_rectangle(
            (0, 0, w * s - 1, h * s - 1), radius=r * s, fill=fill, outline=outline, width=int(round(width * s))
        )

    return shape(w, h, fn)


def ellipse(w: int, h: int, fill=None, outline=None, width: float = 0) -> Image.Image:
    def fn(d: ImageDraw.ImageDraw, s: int) -> None:
        d.ellipse((0, 0, w * s - 1, h * s - 1), fill=fill, outline=outline, width=int(round(width * s)))

    return shape(w, h, fn)


def soft_shadow(w: int, h: int, r: float, blur: float, opacity: float, color: RGB = (0, 0, 0)) -> Image.Image:
    pad = int(blur * 3)
    base = Image.new("L", (w + 2 * pad, h + 2 * pad), 0)
    m = rounded(w, h, r, fill=(255, 255, 255, 255)).getchannel("A")
    base.paste(m, (pad, pad))
    base = base.filter(ImageFilter.GaussianBlur(blur))
    layer = Image.new("RGBA", base.size, rgba(color))
    layer.putalpha(base.point(lambda v: int(v * opacity)))
    return layer


# ---------- текст ----------

def text_w(text: str, f: ImageFont.FreeTypeFont, tracking: float = 0) -> float:
    if not text:
        return 0
    text = safe(text, f)
    if tracking:
        return sum(f.getlength(ch) for ch in text) + tracking * (len(text) - 1)
    return f.getlength(text)


def line_h(f: ImageFont.FreeTypeFont) -> int:
    a, d = f.getmetrics()
    return a + d


def cap_h(f: ImageFont.FreeTypeFont) -> int:
    b = f.getbbox("H", anchor="ls")
    return -b[1]


def draw_text(img: Image.Image, x: float, y: float, text: str, f, fill, tracking: float = 0, anchor: str = "ls") -> None:
    """Текст по базовой линии. anchor: ls — слева, ms — по центру, rs — справа."""
    d = ImageDraw.Draw(img)
    text = safe(text, f)
    if not tracking:
        d.text((x, y), text, font=f, fill=fill, anchor=anchor)
        return
    total = text_w(text, f, tracking)
    if anchor[0] == "m":
        x -= total / 2
    elif anchor[0] == "r":
        x -= total
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill, anchor="l" + anchor[1])
        x += f.getlength(ch) + tracking


def wrap(text: str, f, max_w: float, max_lines: int) -> tuple[list[str], bool]:
    """Перенос по словам. Возвращает строки и признак, что всё влезло без обрезки."""
    words = safe(text, f).split()
    lines: list[str] = []
    cur = ""
    ok = True
    for w in words:
        t = f"{cur} {w}".strip()
        if f.getlength(t) <= max_w or not cur:
            cur = t
            if f.getlength(cur) > max_w:
                ok = False
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if any(f.getlength(ln) > max_w for ln in lines):
        ok = False  # одно длинное слово шире строки
    if len(lines) > max_lines:
        ok = False
        lines = lines[:max_lines]
        last = lines[-1]
        while last and f.getlength(last + "…") > max_w:
            last = last[:-1]
        lines[-1] = last.rstrip(" ,.-") + "…"
    return lines, ok


def fit_lines(text: str, family: str, weight: int, max_w: float, max_lines: int, size_max: int, size_min: int):
    """Подбирает самый крупный кегль, при котором текст влезает в max_lines строк."""
    size = size_max
    while True:
        f = font(family, size, weight)
        lines, ok = wrap(text, f, max_w, max_lines)
        if ok or size <= size_min:
            return f, lines
        size = max(size_min, int(size * 0.93))


def fit_one_line(text: str, family: str, weight: int, max_w: float, size_max: int, size_min: int = 8, tracking_em: float = 0):
    size = size_max
    if not text:
        return font(family, size_max, weight)
    while size > size_min:
        f = font(family, size, weight)
        if text_w(text, f, tracking_em * size) <= max_w:
            return f
        size = max(size_min, int(size * 0.95))
    return font(family, size_min, weight)


# ---------- фоны ----------

def bg_studio(w: int, h: int, base: RGB, light: float = 0.06, center_y: float = 0.45) -> Image.Image:
    """Студийный фон: светлее в центре, мягко темнее к краям (как фотостудия с софтбоксом)."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx = (xx / w - 0.5) / 0.8
    dy = (yy / h - center_y) / 0.9
    d = np.sqrt(dx * dx + dy * dy)
    k = 1 + light - light * 2.2 * np.clip(d, 0, 1.2) ** 2
    arr = np.clip(np.array(base, dtype=np.float32)[None, None, :] * k[..., None], 0, 255)
    return Image.fromarray(arr.astype(np.uint8)).convert("RGBA")


def bg_spot(w: int, h: int, base: RGB, center_y: float = 0.45, strength: float = 0.16) -> Image.Image:
    """Яркий однотонный фон с «прожектором» за товаром."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((xx / w - 0.5) / 0.6) ** 2 + ((yy / h - center_y) / 0.55) ** 2)
    glow = np.clip(1 - d, 0, 1) ** 2 * strength
    edge = np.clip(d - 0.9, 0, 1) * 0.12
    b = np.array(base, dtype=np.float32)
    arr = b[None, None, :] + (255 - b)[None, None, :] * glow[..., None] - b[None, None, :] * edge[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGBA")


def bg_gradient(w: int, h: int, top: RGB, bottom: RGB, glow: RGB | None = None, glow_y: float = 0.72,
                glow_strength: float = 0.35) -> Image.Image:
    """Вертикальный градиент (насыщенный «ягодный» и т.п.) + мягкое свечение за товаром."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    t = (yy / h)[..., None]
    a, b = np.array(top, dtype=np.float32), np.array(bottom, dtype=np.float32)
    arr = a * (1 - t) + b * t
    if glow is not None:
        d = np.sqrt(((xx / w - 0.5) / 0.55) ** 2 + ((yy / h - glow_y) / 0.32) ** 2)
        g = (np.clip(1 - d, 0, 1) ** 2 * glow_strength)[..., None]
        arr = arr * (1 - g) + np.array(glow, dtype=np.float32) * g
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGBA")


def bg_linen(w: int, h: int, base: RGB, seed: int = 7) -> Image.Image:
    """Лён/хлопок: тонкая фактура ткани + тёплый свет из окна слева сверху."""
    rng = np.random.default_rng(seed)
    n1 = rng.normal(0, 1, (h, w)).astype(np.float32)
    n2 = rng.normal(0, 1, (h, w)).astype(np.float32)
    from scipy import ndimage

    weft = ndimage.uniform_filter1d(n1, size=9, axis=1)
    warp = ndimage.uniform_filter1d(n2, size=9, axis=0)
    slub = ndimage.gaussian_filter(rng.normal(0, 1, (h, w)).astype(np.float32), 3)
    tex = (weft + warp) * 4.5 + slub * 6
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    light = 1.04 - 0.10 * np.clip(np.sqrt((xx / w) ** 2 * 0.8 + (yy / h) ** 2) - 0.2, 0, 1)
    arr = np.array(base, dtype=np.float32)[None, None, :] * light[..., None] + tex[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGBA")


def gobo_shadow(w: int, h: int, opacity: float = 0.2, angle: float = -32) -> Image.Image:
    """Тень от оконной рамы/жалюзи — популярный «солнечный» приём в предметной съёмке."""
    sw, sh = w // 4, h // 4
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    a = math.radians(angle)
    u = xx * math.cos(a) + yy * math.sin(a)
    v = -xx * math.sin(a) + yy * math.cos(a)
    period = sw * 0.16
    bands = (np.sin(u / period * 2 * math.pi) > 0.15).astype(np.float32)
    frame = (np.abs(((v / (sw * 0.55)) % 1.0) - 0.5) < 0.035).astype(np.float32)
    m = np.clip(bands + frame, 0, 1)
    region = np.clip(1.2 - np.sqrt(((xx / sw) - 0.95) ** 2 + ((yy / sh) - 0.05) ** 2) * 1.4, 0, 1)
    m = m * region
    img = Image.fromarray((m * 255).astype(np.uint8)).resize((w, h), Image.BILINEAR)
    img = img.filter(ImageFilter.GaussianBlur(w * 0.007))
    layer = Image.new("RGBA", (w, h), (40, 28, 18, 0))
    layer.putalpha(img.point(lambda v: int(v * opacity)))
    return layer


def add_grain(img: Image.Image, amount: float = 3.2, seed: int = 3) -> Image.Image:
    """Плёночное зерно: убирает «ступеньки» градиентов и делает картинку живой."""
    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    noise = np.random.default_rng(seed).normal(0, amount, arr.shape[:2]).astype(np.float32)
    arr = np.clip(arr + noise[..., None], 0, 255)
    return Image.fromarray(arr.astype(np.uint8))


# ---------- товар ----------

def _fit(src_w: int, src_h: int, box_w: float, box_h: float, fill: float) -> float:
    return min(box_w * fill / src_w, box_h * fill / src_h)


def place_cutout(
    canvas: Image.Image,
    cutout: Image.Image,
    box: tuple[float, float, float, float],
    *,
    fill: float = 0.9,
    valign: str = "center",
    shadow: str = "float",
    strength: float = 1.0,
    rotate: float = 0.0,
    light_dx: float = 0.0,
) -> tuple[int, int, int, int]:
    """Кладёт вырезанный товар в рамку box с реалистичной тенью. Возвращает его габариты."""
    prod = cutout
    if rotate:
        prod = prod.rotate(rotate, resample=Image.BICUBIC, expand=True)
        bb = prod.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
        if bb:
            prod = prod.crop(bb)
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    s = _fit(prod.width, prod.height, bw, bh, fill)
    nw, nh = max(1, round(prod.width * s)), max(1, round(prod.height * s))
    prod = prod.resize((nw, nh), Image.LANCZOS)
    rgb = prod.convert("RGB").filter(ImageFilter.UnsharpMask(radius=1.4, percent=55, threshold=2))
    alpha = prod.getchannel("A")
    prod = rgb.convert("RGBA")
    prod.putalpha(alpha)

    px = x0 + (bw - nw) / 2
    if valign == "bottom":  # стоит на «полу», но широкие предметы не прижимаем к самому низу
        py = y0 + (bh - nh) * 0.72 + bh * (1 - fill) * 0.2
    else:
        py = y0 + (bh - nh) / 2

    if shadow != "none":
        if shadow == "ground":
            # контактная тень: силуэт, сплющенный к основанию
            ch = max(4, int(nh * 0.07))
            contact = alpha.resize((nw, ch), Image.BILINEAR)
            pad = int(ch * 2.5)
            m = Image.new("L", (nw + 2 * pad, ch + 2 * pad), 0)
            m.paste(contact, (pad, pad))
            m = m.filter(ImageFilter.GaussianBlur(ch * 0.7))
            layer = Image.new("RGBA", m.size, (0, 0, 0, 0))
            layer.putalpha(m.point(lambda v: int(v * 0.55 * strength)))
            paste(canvas, layer, px - pad + light_dx * 0.3, py + nh - ch * 0.75 - pad)
            # мягкая общая тень
            r = max(6, int(nh * 0.035))
            pad = r * 3
            m = Image.new("L", (nw + 2 * pad, nh + 2 * pad), 0)
            m.paste(alpha, (pad, pad))
            m = m.filter(ImageFilter.GaussianBlur(r))
            layer = Image.new("RGBA", m.size, (0, 0, 0, 0))
            layer.putalpha(m.point(lambda v: int(v * 0.16 * strength)))
            paste(canvas, layer, px - pad + light_dx, py - pad + nh * 0.02)
        else:  # float — предмет чуть «парит» над фоном
            r = max(8, int(max(nw, nh) * 0.03))
            pad = r * 3
            m = Image.new("L", (nw + 2 * pad, nh + 2 * pad), 0)
            m.paste(alpha, (pad, pad))
            m = m.filter(ImageFilter.GaussianBlur(r))
            layer = Image.new("RGBA", m.size, (0, 0, 0, 0))
            layer.putalpha(m.point(lambda v: int(v * 0.26 * strength)))
            paste(canvas, layer, px - pad + light_dx, py - pad + max(nw, nh) * 0.03)

    paste(canvas, prod, px, py)
    return int(px), int(py), int(px + nw), int(py + nh)


def place_photo(
    canvas: Image.Image,
    photo: Image.Image,
    box: tuple[float, float, float, float],
    *,
    radius: float = 28,
    border: float = 0,
    border_color: RGB = (255, 255, 255),
    rotate: float = 0.0,
    shadow: float = 0.22,
    centering: tuple[float, float] = (0.5, 0.45),
    contain: bool = False,
) -> tuple[int, int, int, int]:
    """Фото без вырезания фона — аккуратной карточкой со скруглением и тенью.

    contain=True — показать картинку целиком, без обрезки (картинки из галереи магазина).
    """
    x0, y0, x1, y1 = box
    if contain:
        s = min((x1 - x0) / photo.width, (y1 - y0) / photo.height)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        nw, nh = photo.width * s, photo.height * s
        x0, y0, x1, y1 = cx - nw / 2, cy - nh / 2, cx + nw / 2, cy + nh / 2
    bw, bh = int(x1 - x0), int(y1 - y0)
    iw, ih = bw - int(2 * border), bh - int(2 * border)
    img = ImageOps.fit(photo, (iw, ih), Image.LANCZOS, centering=centering)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=45, threshold=2)).convert("RGBA")
    layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    if border:
        paste(layer, rounded(bw, bh, radius, fill=rgba(border_color)), 0, 0)
    inner_r = max(0, radius - border)
    mask = rounded(iw, ih, inner_r, fill=(255, 255, 255, 255)).getchannel("A")
    img.putalpha(mask)
    paste(layer, img, border, border)
    if rotate:
        layer = layer.rotate(rotate, resample=Image.BICUBIC, expand=True)
    lx = x0 + (bw - layer.width) / 2
    ly = y0 + (bh - layer.height) / 2
    if shadow:
        blur = max(bw, bh) * 0.025
        pad = int(blur * 3)
        sh = Image.new("L", (layer.width + 2 * pad, layer.height + 2 * pad), 0)
        sh.paste(layer.getchannel("A"), (pad, pad))
        sh = sh.filter(ImageFilter.GaussianBlur(blur))
        s_layer = Image.new("RGBA", sh.size, (0, 0, 0, 0))
        s_layer.putalpha(sh.point(lambda v: int(v * shadow)))
        paste(canvas, s_layer, lx - pad, ly - pad + blur * 0.6)
    paste(canvas, layer, lx, ly)
    return int(lx), int(ly), int(lx + layer.width), int(ly + layer.height)


def checkmark(size: int, color) -> Image.Image:
    def fn(d: ImageDraw.ImageDraw, s: int) -> None:
        w = size * s
        d.line([(w * 0.18, w * 0.52), (w * 0.42, w * 0.76), (w * 0.84, w * 0.28)], fill=color, width=int(w * 0.13), joint="curve")

    return shape(size, size, fn)
