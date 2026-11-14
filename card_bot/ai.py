"""Необязательно: Claude смотрит на фото и пишет название, описание и хэштеги.

Работает, только если в .env задан ANTHROPIC_API_KEY. Цена и размеры всегда берутся
из того, что написал продавец, — модель их не трогает.
"""

from __future__ import annotations

import base64
import io
import json
import logging

from PIL import Image

from .config import config

log = logging.getLogger(__name__)

# модели, у которых есть серверный запасной вариант на случай отказа
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-1", "claude-fable-5-2"}

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Название для карточки, до 45 символов"},
        "brand": {"type": "string", "description": "Бренд латиницей или пустая строка"},
        "category": {"type": "string", "enum": ["clothes", "shoes", "home", "beauty"]},
        "description": {"type": "string", "description": "2–3 коротких предложения для поста"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "brand", "category", "description", "hashtags"],
    "additionalProperties": False,
}

SYSTEM = """Ты пишешь тексты для небольшого магазина: продавец привозит из-за границы одежду, обувь, посуду и иногда косметику \
и продаёт через Telegram-канал и Instagram. Тебе дают фото товара и заметку продавца.

Часто вместо фото присылают скриншоты страницы товара из зарубежного интернет-магазина (бывает несколько: фото товара и страница с текстом). Тогда возьми название товара со страницы или с упаковки, тип товара напиши по-русски, а название линейки/оттенка оставь латиницей (например «Палетка теней Pillow Talk Dreams — 2 шт.»). Цены со скриншота не используй — у продавца своя цена.

Верни:
- title — название для карточки на русском: тип вещи + бренд + модель (например «Кроссовки Nike Air Force 1»). \
Если продавец уже написал нормальное название, оставь его по смыслу, только поправь опечатки и регистр. \
Модель указывай, только если она есть в заметке или явно читается на фото (логотип, надпись). До 45 символов.
- brand — бренд латиницей, если он есть в заметке или виден на фото; иначе пустая строка.
- category — clothes (одежда, сумки, аксессуары), shoes (обувь), home (посуда, текстиль и вещи для дома) или beauty (косметика, уход, парфюм).
- description — 2–3 коротких живых предложения для поста: что это, как носить или где использовать, какое настроение. \
Не пиши цену, размеры, контакты и призывы написать в директ — их добавят автоматически. \
Не выдумывай состав, материал, страну и «оригинальность», если продавец этого не написал. Без канцелярита, максимум одно эмодзи.
- hashtags — 5–7 хэштегов на русском и английском без пробелов, с #, по типу вещи, бренду и стилю."""


def enabled() -> bool:
    return bool(config.anthropic_key)


def _jpeg_b64(img: Image.Image) -> str:
    im = img.convert("RGB")
    im.thumbnail((1200, 1600), Image.LANCZOS)  # скриншот телефона целиком, текст остаётся читаемым
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


async def describe(photos: list[Image.Image], seller_text: str) -> dict | None:
    """Возвращает dict по SCHEMA или None (нет ключа, отказ, ошибка сети).

    photos — до 4 картинок: обложка и скриншоты, где может быть название товара.
    """
    if not enabled():
        return None
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=config.anthropic_key, timeout=60.0, max_retries=2)
    kwargs = dict(
        model=config.claude_model,
        max_tokens=8000,
        system=SYSTEM,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{
            "role": "user",
            "content": [
                *[{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _jpeg_b64(ph)}}
                  for ph in photos[:4]],
                {"type": "text", "text": f"Заметка продавца:\n{seller_text.strip() or '(пусто)'}"},
            ],
        }],
    )
    try:
        if config.claude_model in _FALLBACK_MODELS:
            resp = await client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
            )
        else:
            resp = await client.messages.create(**kwargs)
    except anthropic.AuthenticationError:
        log.error("ANTHROPIC_API_KEY неверный — тексты будут по шаблону")
        return None
    except anthropic.RateLimitError:
        log.warning("Claude: превышен лимит запросов — текст по шаблону")
        return None
    except anthropic.APIStatusError as e:
        log.warning("Claude: ошибка API %s — текст по шаблону", e.status_code)
        return None
    except anthropic.APIConnectionError:
        log.warning("Claude: нет связи — текст по шаблону")
        return None
    finally:
        await client.close()

    if resp.stop_reason == "refusal":
        log.info("Claude отказался описывать это фото — текст по шаблону")
        return None
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except ValueError:
        log.warning("Claude вернул не-JSON — текст по шаблону")
        return None
    return data if isinstance(data, dict) else None


TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "ru": {"type": "string"}},
                "required": ["id", "ru"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

TRANSLATE_SYSTEM = """Ты переводишь надписи на рекламных картинках интернет-магазина на русский язык.
Тебе дают картинку, где текстовые блоки обведены красными рамками с номерами, и распознанный текст
блоков (распознавание теряет турецкие буквы и пробелы — сверяйся с картинкой).

Для каждого блока верни id и ru:
- ru — короткий естественный рекламный перевод на русский, по длине близкий к оригиналу
  (лучше короче: место на картинке ограничено). Без кавычек и точки в конце заголовков.
- ru = "" (не трогать), если это надпись на самом товаре или упаковке, название бренда или линейки,
  цифры и единицы (SPF 50, 80 ml), или элементы сайта/приложения (кнопки, цены, рейтинг, меню).
- Если текст уже на русском — ru = ""."""


async def translate_blocks(image: Image.Image, texts: list[str]) -> list[str | None] | None:
    """Перевод текстовых блоков картинки. None — перевод не удался (оставляем как есть)."""
    if not enabled() or not texts:
        return None
    import anthropic

    listing = "\n".join(f"{i}: {t}" for i, t in enumerate(texts))
    client = anthropic.AsyncAnthropic(api_key=config.anthropic_key, timeout=60.0, max_retries=2)
    kwargs = dict(
        model=config.claude_model,
        max_tokens=8000,
        system=TRANSLATE_SYSTEM,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": TRANSLATE_SCHEMA}},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _jpeg_b64(image)}},
                {"type": "text", "text": f"Распознанные блоки:\n{listing}"},
            ],
        }],
    )
    try:
        if config.claude_model in _FALLBACK_MODELS:
            resp = await client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
            )
        else:
            resp = await client.messages.create(**kwargs)
    except anthropic.APIError as e:
        log.warning("Claude: перевод надписей не удался (%s)", type(e).__name__)
        return None
    finally:
        await client.close()
    if resp.stop_reason == "refusal":
        return None
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        items = json.loads(text)["items"]
    except (ValueError, KeyError, TypeError):
        return None
    out: list[str | None] = [None] * len(texts)
    for it in items:
        i, ru = it.get("id"), (it.get("ru") or "").strip()
        if isinstance(i, int) and 0 <= i < len(texts) and ru:
            out[i] = ru
    return out
