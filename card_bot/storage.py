"""Настройки магазина, которые можно менять командами бота. Хранятся в data/settings.json."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

from .config import DATA_DIR, config

log = logging.getLogger(__name__)
PATH = DATA_DIR / "settings.json"


@dataclass
class Settings:
    owner_id: int = 0
    shop_name: str = config.shop_name
    contact: str = config.contact
    currency: str = config.currency
    channel_id: str = config.channel_id
    translate: bool = True  # переводить надписи на картинках магазина (нужен ИИ)

    def save(self) -> None:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")


def load() -> Settings:
    s = Settings()
    if PATH.exists():
        try:
            data = json.loads(PATH.read_text(encoding="utf-8"))
            for k, v in data.items():
                if hasattr(s, k) and v not in (None, ""):
                    setattr(s, k, v)
        except (OSError, ValueError):
            log.exception("Не прочитал %s — использую значения по умолчанию", PATH)
    return s


settings = load()
