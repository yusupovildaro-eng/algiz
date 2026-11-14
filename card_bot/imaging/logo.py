"""Логотип магазина: круглый значок в углу каждой карточки."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from ..config import DATA_DIR
from .photo import load_image

LOGO_PATH = DATA_DIR / "logo.png"
_cache: tuple[float, Image.Image] | None = None


def circle_logo(data: bytes) -> Image.Image:
    """Круг по центру картинки — как аватарка канала."""
    img = load_image(data)
    side = min(img.size)
    img = ImageOps.fit(img, (side, side), Image.LANCZOS).resize((600, 600), Image.LANCZOS).convert("RGBA")
    big = Image.new("L", (2400, 2400), 0)
    ImageDraw.Draw(big).ellipse((12, 12, 2388, 2388), fill=255)
    img.putalpha(big.resize((600, 600), Image.LANCZOS))
    return img


def save_logo(data: bytes, path: Path = LOGO_PATH) -> Image.Image:
    img = circle_logo(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    return img


def load_logo(path: Path = LOGO_PATH) -> Image.Image | None:
    global _cache
    if not path.exists():
        return None
    mtime = path.stat().st_mtime
    if _cache is None or _cache[0] != mtime:
        with Image.open(path) as im:
            _cache = (mtime, im.convert("RGBA"))
    return _cache[1]


def remove_logo(path: Path = LOGO_PATH) -> None:
    if path.exists():
        path.unlink()

