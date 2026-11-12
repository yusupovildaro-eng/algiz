import io
import requests
from PIL import Image
from config import BOT_TOKEN, CHANNEL_ID

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Telegram crops images with aspect ratio outside this range
MIN_RATIO = 0.75   # taller than 3:4 → add side padding
MAX_RATIO = 2.5    # wider than 5:2 → add top/bottom padding


def _pad_image(image_bytes: bytes) -> bytes:
    """Add white padding so aspect ratio stays within Telegram's display range."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    ratio = w / h

    if MIN_RATIO <= ratio <= MAX_RATIO:
        return image_bytes  # already fine

    if ratio > MAX_RATIO:
        # Too wide → pad top and bottom
        new_h = int(w / MAX_RATIO)
        pad = (new_h - h) // 2
        canvas = Image.new("RGB", (w, new_h), (255, 255, 255))
        canvas.paste(img, (0, pad))
    else:
        # Too tall → pad left and right
        new_w = int(h * MIN_RATIO)
        pad = (new_w - w) // 2
        canvas = Image.new("RGB", (new_w, h), (255, 255, 255))
        canvas.paste(img, (pad, 0))

    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def send_text(text: str, parse_mode: str = "HTML", chat_id: str | None = None) -> dict:
    r = requests.post(
        f"{API}/sendMessage",
        json={
            "chat_id": chat_id or CHANNEL_ID,
            "text": text,
            "parse_mode": parse_mode,
            "link_preview_options": {"is_disabled": True},
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def send_photo(image_url: str, caption: str, parse_mode: str = "HTML") -> dict:
    r = requests.post(
        f"{API}/sendPhoto",
        json={
            "chat_id": CHANNEL_ID,
            "photo": image_url,
            "caption": caption,
            "parse_mode": parse_mode,
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def send_photo_bytes(image_bytes: bytes, filename: str, caption: str, parse_mode: str = "HTML") -> dict:
    image_bytes = _pad_image(image_bytes)
    r = requests.post(
        f"{API}/sendPhoto",
        data={"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": parse_mode},
        files={"photo": ("photo.jpg", image_bytes, "image/jpeg")},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()
