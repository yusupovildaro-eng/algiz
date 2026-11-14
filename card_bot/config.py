"""Настройки бота. Читаются из переменных окружения или файла card_bot/.env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.environ.get("CARD_BOT_DATA_DIR", BASE_DIR / "data"))
FONTS_DIR = BASE_DIR / "fonts"


def _ids(raw: str) -> set[int]:
    out: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out


@dataclass
class Config:
    bot_token: str = field(default_factory=lambda: os.environ.get("CARD_BOT_TOKEN", ""))
    # Кто может пользоваться ботом. Пусто — владельцем станет первый, кто нажмёт /start.
    admin_ids: set[int] = field(default_factory=lambda: _ids(os.environ.get("ADMIN_IDS", "")))
    # Канал для кнопки «Опубликовать» (@username или -100…). Бот должен быть админом канала.
    channel_id: str = field(default_factory=lambda: os.environ.get("CHANNEL_ID", ""))
    shop_name: str = field(default_factory=lambda: os.environ.get("SHOP_NAME", "MY SHOP"))
    contact: str = field(default_factory=lambda: os.environ.get("SHOP_CONTACT", ""))
    currency: str = field(default_factory=lambda: os.environ.get("CURRENCY", "сум"))
    # Модель удаления фона (rembg): birefnet-general-lite — аккуратнее, isnet-general-use — быстрее.
    rembg_model: str = field(default_factory=lambda: os.environ.get("REMBG_MODEL", "birefnet-general-lite"))
    # Необязательно: тексты подписей пишет Claude, если задан ключ.
    anthropic_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    claude_model: str = field(default_factory=lambda: os.environ.get("CLAUDE_MODEL", "claude-opus-5-5"))


config = Config()
