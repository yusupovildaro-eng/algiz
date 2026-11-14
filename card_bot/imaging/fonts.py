"""Шрифты (все с кириллицей, лицензия SIL OFL — можно использовать в коммерции)."""

from __future__ import annotations

from functools import lru_cache

from PIL import ImageFont

from ..config import FONTS_DIR

FAMILIES = {
    "sans": "Manrope-Variable.ttf",  # чистый гротеск — маркетплейсы, Zara/COS
    "serif": "PlayfairDisplay-Variable.ttf",  # журнальная антиква — Vogue-стиль
    "serif_italic": "PlayfairDisplay-Italic-Variable.ttf",
    "display": "Unbounded-Variable.ttf",  # широкий жирный — стритвир, «дропы»
}


# символы, которых нет в шрифте, и чем их заменить
MISSING = {"sans": "₸", "serif": "₽₸₺", "serif_italic": "₽₸₺", "display": ""}
FALLBACK = {"₽": "руб.", "₸": "тг", "₺": "TL", "\u202f": "\u00a0", "\u2009": "\u00a0"}


def safe(text: str, f: ImageFont.FreeTypeFont) -> str:
    miss = getattr(f, "missing", "") + "\u202f\u2009"
    for ch in miss:
        if ch in text:
            text = text.replace(ch, FALLBACK.get(ch, ""))
    return text


@lru_cache(maxsize=512)
def font(family: str, size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONTS_DIR / FAMILIES[family]), size=max(4, int(size)))
    try:
        axes = f.get_variation_axes()
        if axes:
            lo, hi = axes[0]["minimum"], axes[0]["maximum"]
            f.set_variation_by_axes([min(hi, max(lo, weight))])
    except OSError:
        pass  # шрифт не вариативный
    f.missing = MISSING.get(family, "")  # type: ignore[attr-defined]
    return f
