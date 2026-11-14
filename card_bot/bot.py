"""Telegram-бот: присылаете фото товара + пару слов → получаете готовые карточки для канала и Instagram."""

from __future__ import annotations

import asyncio
import contextlib
import html
import io
import logging
import os
import shutil
import ssl
import time
from dataclasses import dataclass, field

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InputMediaDocument,
    InputMediaPhoto,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from PIL import Image, ImageDraw

from . import ai, describe
from .config import DATA_DIR, config
from .imaging import cards
from .imaging.logo import load_logo, remove_logo, save_logo
from .imaging.fonts import font
from .imaging.photo import ProductPhoto, load_image, process, warmup
from .storage import settings
from .links import URL_RE, fetch_product, find_url
from .text import Details, build_caption, guess_category, parse_details, to_card_info

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == ChatType.PRIVATE)

MAX_PHOTOS = 8
ALBUM_WAIT = 1.6  # сек: ждём, пока Telegram досылает все фото альбома
HEAVY = asyncio.Semaphore(1)  # обработка фото грузит процессор — по одной задаче за раз
_urgent = 0  # сколько «срочных» задач ждёт (превью, выбранный стиль) — фоновая обработка им уступает


@contextlib.asynccontextmanager
async def urgent():
    global _urgent
    _urgent += 1
    try:
        yield
    finally:
        _urgent -= 1

EXAMPLE = (
    "<i>Кроссовки Nike Air Force 1\n"
    "1 450 000\n"
    "40, 41, 42, 43\n"
    "Оригинал</i>"
)
HOW_TO = (
    "Напишите одним сообщением — каждое с новой строки:\n"
    "1) название (бренд, модель)\n"
    "2) цену (если есть скидка: «было 1 800 000 стало 1 450 000»)\n"
    "3) размеры\n"
    "4) по желанию — детали: «Оригинал», «Из Италии», «Натуральная кожа»…\n\n"
    f"Например:\n{EXAMPLE}\n\n"
    "Можно и в одну строку через запятую. Пропустить — отправьте «-».\n\n"
    "💡 Вместо скриншота можно прислать <b>ссылку на товар</b> — я скачаю с сайта оригинальные фото "
    "в высоком качестве и возьму название со страницы."
)


@dataclass
class Draft:
    photos: list[bytes] = field(default_factory=list)
    processed: dict[int, ProductPhoto] = field(default_factory=dict)
    text: str = ""
    have_text: bool = False
    details: Details | None = None
    info: cards.CardInfo | None = None
    caption: str = ""
    caption_plain: str = ""
    style: str = ""
    use_cutout: bool = True
    slides: list[bytes] = field(default_factory=list)
    story: bytes | None = None
    rendered: bool = False
    album_timer: asyncio.Task | None = None
    work: asyncio.Task | None = None
    warned_limit: bool = False
    cover: int = 0  # какое фото — обложка
    seen: set = field(default_factory=set)  # file_unique_id — дубли не берём
    pending: int = 0  # сколько фото ещё скачивается
    prompted: bool = False  # уже попросили написать цену
    link_title: str = ""
    link_brand: str = ""
    link_desc: str = ""
    debug_dir: object = None


drafts: dict[int, Draft] = {}
awaiting_logo: set[int] = set()


# ---------------- доступ ----------------

def allowed(user_id: int) -> bool:
    if config.admin_ids:
        return user_id in config.admin_ids
    return settings.owner_id in (0, user_id)


async def access_middleware(handler, event, data):
    user = data.get("event_from_user")
    if user is None or allowed(user.id):
        return await handler(event, data)
    if isinstance(event, Message):
        await event.answer("Это личный бот магазина. Доступ есть только у владельца.")
    elif isinstance(event, CallbackQuery):
        await event.answer("Нет доступа", show_alert=True)
    return None


# ---------------- утилиты ----------------

DEBUG_DIR = DATA_DIR / "debug"
DEBUG_KEEP = 5


def _debug_save(d: Draft, name: str, files: list[tuple[str, bytes]]) -> None:
    """Последние запросы (исходники и готовые карточки) — чтобы разбирать, если что-то вышло криво."""
    try:
        if not getattr(d, "debug_dir", None):
            stamp = time.strftime("%Y%m%d-%H%M%S")
            d.debug_dir = DEBUG_DIR / stamp
            d.debug_dir.mkdir(parents=True, exist_ok=True)
            for i, raw in enumerate(d.photos):
                (d.debug_dir / f"in_{i + 1}.img").write_bytes(raw)
            (d.debug_dir / "text.txt").write_text(d.text or "", encoding="utf-8")
            for old in sorted(DEBUG_DIR.iterdir())[:-DEBUG_KEEP]:
                shutil.rmtree(old, ignore_errors=True)
        for fname, data in files:
            (d.debug_dir / f"{name}_{fname}").write_bytes(data)
    except OSError:
        log.exception("Не сохранил отладку")


def _jpeg(img, quality: int = 94) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality, subsampling=0, optimize=True)
    return buf.getvalue()


async def _download(bot: Bot, file_id: str) -> bytes:
    buf = await bot.download(file_id)
    return buf.read() if buf else b""


def _order(d: Draft) -> list[int]:
    """Сначала обложка, потом остальные фото."""
    rest = [i for i in range(len(d.photos)) if i != d.cover]
    return [d.cover] + rest if d.photos else []


async def _process(d: Draft, indices: list[int], status: Message | None = None, background: bool = False) -> None:
    """Вырезает фон и улучшает указанные фото (результат кэшируется).

    Блокировка берётся на каждое фото отдельно, чтобы обложка могла «влезть» между
    фото, которые обрабатываются в фоне.
    """
    todo = [i for i in indices if i not in d.processed]
    for n, i in enumerate(todo, 1):
        while background and _urgent:
            await asyncio.sleep(0.2)
        async with HEAVY:
            if i in d.processed:
                continue
            if status and len(todo) > 1:
                try:
                    await status.edit_text(f"⏳ Обрабатываю фото {n}/{len(todo)}: убираю фон, выравниваю свет…")
                except TelegramAPIError:
                    pass
            d.processed[i] = await asyncio.to_thread(process, d.photos[i], True)


def _start_background_processing(d: Draft) -> None:
    """Начинаем вырезать фон сразу, пока продавец пишет цену и выбирает стиль."""
    if d.work is None or d.work.done():
        d.work = asyncio.create_task(_process(d, _order(d), background=True))


def _photos(d: Draft) -> list[ProductPhoto]:
    """Фото для карусели: обложка первой, скриншоты без товара пропускаем."""
    all_ = [d.processed[i] for i in _order(d)]
    return [p for p in all_ if not p.unusable] or all_


def _style_keyboard(d: Draft):
    kb = InlineKeyboardBuilder()
    rec = cards.recommend_style(d.info) if d.info else "studio"
    for i, st in enumerate(cards.STYLES, 1):
        kb.button(text=f"{i}. {cards.STYLE_NAMES[st]}" + (" ★" if st == rec else ""), callback_data=f"style:{st}")
    kb.button(text="✂️ Не вырезать фон" if d.use_cutout else "✂️ Вырезать фон", callback_data="cut")
    rows = [2, 2, 1, 1]
    if len(d.photos) > 1:
        kb.button(text=f"🖼 Выбрать обложку (сейчас фото {d.cover + 1})", callback_data="covers")
        rows.append(1)
    kb.adjust(*rows)
    return kb.as_markup()


def _actions_keyboard(d: Draft):
    kb = InlineKeyboardBuilder()
    rows = []
    if settings.channel_id:
        kb.button(text="📢 Опубликовать в канал", callback_data="publish")
        rows.append(1)
    kb.button(text="📱 Сторис 9:16", callback_data="story")
    kb.button(text="📎 Файлы без сжатия", callback_data="files")
    kb.button(text="📝 Текст для Instagram", callback_data="insta")
    kb.button(text="🎨 Другой стиль", callback_data="styles")
    kb.button(text="✏️ Изменить текст", callback_data="edit")
    rows += [2, 2, 1]
    kb.adjust(*rows)
    return kb.as_markup()


def _new_draft(chat_id: int) -> Draft:
    old = drafts.get(chat_id)
    if old:
        for t in (old.album_timer, old.work):
            if t and not t.done():
                t.cancel()
    d = Draft()
    drafts[chat_id] = d
    return d


# ---------------- команды ----------------

@router.message(CommandStart())
async def cmd_start(m: Message) -> None:
    if not config.admin_ids and settings.owner_id == 0:
        settings.owner_id = m.from_user.id
        settings.save()
        log.info("Владелец бота: %s", m.from_user.id)
    await m.answer(
        "Привет! Я делаю из обычных фото товара красивые карточки для канала и Instagram.\n\n"
        "<b>Как пользоваться</b>\n"
        "1. Пришлите 1–8 фото или скриншотов одной вещи (можно альбомом) — или ссылку на товар.\n"
        "2. Напишите название, цену и размеры.\n"
        "3. Выберите стиль — получите карусель 4:5, сторис и готовый текст поста.\n\n"
        "Я сам уберу фон, выровняю свет и цвет, добавлю тень и сверстаю карточку.\n\n"
        f"<b>Ваш магазин:</b> {html.escape(settings.shop_name)}\n"
        f"<b>Контакт для заказов:</b> {html.escape(settings.contact or 'не указан')}\n"
        "Поменять: /shop, /contact, /currency, /channel — подробнее в /settings"
    )


@router.message(Command("help"))
async def cmd_help(m: Message) -> None:
    await m.answer("Пришлите фото товара, потом текст.\n\n" + HOW_TO + "\n\nНастройки: /settings")


@router.message(Command("settings"))
async def cmd_settings(m: Message) -> None:
    await m.answer(
        "<b>Настройки</b>\n"
        f"Название магазина: <b>{html.escape(settings.shop_name)}</b> — /shop Название\n"
        f"Контакт для заказов: <b>{html.escape(settings.contact or '—')}</b> — /contact @username\n"
        f"Валюта по умолчанию: <b>{html.escape(settings.currency)}</b> — /currency сум (или $, €, ₽)\n"
        f"Канал для публикации: <b>{html.escape(settings.channel_id or '—')}</b> — /channel @канал\n"
        f"Логотип: <b>{'есть' if load_logo() is not None else 'нет'}</b> — /logo\n"
        f"Тексты от ИИ (Claude): <b>{'включены' if ai.enabled() else 'выключены (описание по шаблону)'}</b>\n\n"
        "Чтобы публиковать в канал, добавьте бота в администраторы канала с правом публикации."
    )


async def _set(m: Message, cmd: CommandObject, attr: str, label: str) -> None:
    value = (cmd.args or "").strip()
    if not value:
        await m.answer(f"Напишите значение после команды, например: /{cmd.command} …")
        return
    if attr == "contact" and not value.startswith(("@", "+", "http")):
        value = "@" + value
    setattr(settings, attr, value)
    settings.save()
    await m.answer(f"✅ {label}: <b>{html.escape(value)}</b>")


@router.message(Command("shop"))
async def cmd_shop(m: Message, command: CommandObject) -> None:
    await _set(m, command, "shop_name", "Название магазина")


@router.message(Command("contact"))
async def cmd_contact(m: Message, command: CommandObject) -> None:
    await _set(m, command, "contact", "Контакт для заказов")


@router.message(Command("currency"))
async def cmd_currency(m: Message, command: CommandObject) -> None:
    await _set(m, command, "currency", "Валюта")


@router.message(Command("channel"))
async def cmd_channel(m: Message, command: CommandObject) -> None:
    await _set(m, command, "channel_id", "Канал")


@router.message(Command("logo"))
async def cmd_logo(m: Message, command: CommandObject) -> None:
    if (command.args or "").strip().lower() in ("off", "нет", "удалить", "убрать"):
        remove_logo()
        await m.answer("Логотип убран с карточек.")
        return
    awaiting_logo.add(m.chat.id)
    await m.answer("Пришлите логотип картинкой — лучше квадратную аватарку канала. "
                   "Я вырежу круг и поставлю его в угол каждой карточки.\nУбрать логотип: /logo off")


@router.message(Command("new"))
async def cmd_new(m: Message) -> None:
    _new_draft(m.chat.id)
    await m.answer("Начинаем заново — пришлите фото товара 📸")


# ---------------- фото ----------------

async def _after_album(bot: Bot, chat_id: int, d: Draft) -> None:
    await asyncio.sleep(ALBUM_WAIT)
    while d.pending:  # большие файлы ещё скачиваются
        await asyncio.sleep(0.3)
    d.photos = [p for p in d.photos if p]
    if not d.photos:
        return
    _start_background_processing(d)
    if d.have_text:
        await _generate(bot, chat_id, d)
        return
    d.prompted = True
    extra = f" (беру первые {MAX_PHOTOS})" if d.warned_limit else ""
    if ai.enabled():
        ask = ("Теперь напишите <b>цену</b> (и размеры, если есть) — название я прочитаю со скриншотов:\n"
               "<i>450 000</i>")
    else:
        ask = ("Теперь напишите <b>название и цену</b> (и размеры, если есть), например:\n"
               "<i>Крем Calliel Glow Primer SPF 50\n450 000</i>\n\n"
               "Читать название со скриншотов я смогу, когда подключат ИИ (ключ ANTHROPIC_API_KEY).")
    await bot.send_message(
        chat_id,
        f"📸 Фото: {len(d.photos)}{extra}. Уже убираю фон.\n"
        "Обложкой будет первое фото — потом её можно поменять кнопкой.\n\n" + ask + "\n\nФормат подробно: /help",
    )


async def _add_photo(m: Message, bot: Bot, file_id: str, unique_id: str) -> None:
    if m.chat.id in awaiting_logo:
        awaiting_logo.discard(m.chat.id)
        await asyncio.to_thread(save_logo, await _download(bot, file_id))
        await m.answer("✅ Логотип сохранён — он будет на всех новых карточках.")
        return
    d = drafts.get(m.chat.id)
    if d is not None and d.prompted and not d.rendered and d.info is None and d.photos:
        # бот уже попросил цену, а пришли новые фото — это новая карточка, старые не смешиваем
        d = _new_draft(m.chat.id)
        await m.answer("🆕 Начинаю новую карточку с этими фото. "
                       "Если это фото к прошлому товару — пришлите все фото одним альбомом.")
    elif d is None or d.rendered or d.info is not None:
        d = _new_draft(m.chat.id)
    if unique_id in d.seen:
        return  # то же фото второй раз
    if len(d.photos) >= MAX_PHOTOS:
        d.warned_limit = True
        return
    d.seen.add(unique_id)
    idx = len(d.photos)
    d.photos.append(b"")  # место занято сразу — альбом приходит пачкой, лимит не «проскочит»
    d.pending += 1
    if m.caption:
        d.text, d.have_text = m.caption, True
    if d.album_timer and not d.album_timer.done():
        d.album_timer.cancel()
    d.album_timer = asyncio.create_task(_after_album(bot, m.chat.id, d))
    try:
        d.photos[idx] = await _download(bot, file_id)
    finally:
        d.pending -= 1


@router.message(F.photo)
async def on_photo(m: Message, bot: Bot) -> None:
    await _add_photo(m, bot, m.photo[-1].file_id, m.photo[-1].file_unique_id)


@router.message(F.document)
async def on_document(m: Message, bot: Bot) -> None:
    doc = m.document
    if not (doc.mime_type or "").startswith("image/"):
        await m.answer("Пришлите фото (картинкой или файлом JPG/PNG/HEIC).")
        return
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await m.answer("Файл больше 20 МБ — Telegram не даст боту его скачать. Пришлите фото поменьше.")
        return
    await _add_photo(m, bot, doc.file_id, doc.file_unique_id)


# ---------------- текст ----------------

async def _ask_screenshots(m: Message, d: Draft, status: Message) -> None:
    """Сайт не открылся — просим скриншоты; текст, присланный вместе со ссылкой, запоминаем."""
    try:
        await status.delete()
    except TelegramAPIError:
        pass
    tail = ("Цену и текст, которые вы написали со ссылкой, я запомнил — после скриншотов сразу сделаю карточки."
            if d.have_text else "После скриншотов напишите цену и размеры.")
    await m.answer(
        "😔 <b>Не получается открыть эту ссылку</b> — сайт закрыт для ботов.\n\n"
        "Пришлите, пожалуйста, <b>скриншоты товара</b> (1–5 штук, можно альбомом):\n"
        "• страницу товара, где хорошо видно фото;\n"
        "• другие ракурсы, если есть.\n\n"
        "Я сам найду товар на скриншоте и уберу фон, кнопки и текст сайта.\n" + tail
    )


async def _from_link(m: Message, bot: Bot, url: str) -> None:
    d = _new_draft(m.chat.id)  # ссылка — всегда новая карточка
    rest = URL_RE.sub("", m.text or "").strip()
    if rest:
        d.text, d.have_text = rest, True
    status = await m.answer("🔗 Открываю ссылку, ищу фото товара…")
    try:
        lp = await fetch_product(url)
    except Exception as e:  # noqa: BLE001
        log.info("Ссылка %s не открылась: %s", url, e)
        await _ask_screenshots(m, d, status)
        return
    if not lp.images:  # открылась «проверка на робота» или страница без фото
        log.info("На странице %s нет фото товара", url)
        await _ask_screenshots(m, d, status)
        return
    d.photos = lp.images[:MAX_PHOTOS]
    d.link_title, d.link_brand, d.link_desc = lp.title, lp.brand, lp.description
    _start_background_processing(d)
    if d.have_text:
        await status.delete()
        await _generate(bot, m.chat.id, d)
        return
    d.prompted = True
    await status.edit_text(
        f"Нашёл: <b>{html.escape(lp.title or 'товар')}</b>\nФото с сайта: {len(lp.images)}. Уже убираю фон.\n\n"
        "Теперь напишите цену (и размеры, если есть). Название возьму со страницы — "
        "или напишите своё первой строкой."
    )


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(m: Message, bot: Bot) -> None:
    url = find_url(m.text)
    if url:
        await _from_link(m, bot, url)
        return
    d = drafts.get(m.chat.id)
    if d is None or not d.photos:
        await m.answer("Сначала пришлите фото, скриншот или ссылку на товар 📸, потом цену.")
        return
    d.text = "" if m.text.strip() in ("-", "—") else m.text
    d.have_text = True
    if d.album_timer and not d.album_timer.done():
        return  # альбом ещё догружается — текст подхватится автоматически
    await _generate(bot, m.chat.id, d)


async def _generate(bot: Bot, chat_id: int, d: Draft) -> None:
    status = await bot.send_message(chat_id, "⏳ Обрабатываю обложку: убираю фон, выравниваю свет…")
    try:
        async with urgent():
            await _process(d, [d.cover])  # для превью нужна только обложка, остальные — в фоне
            if not d.processed[d.cover].good_cover and len(d.photos) > 1:
                # первое фото — обрезанный товар, текстура или страница с текстом: ищем фото получше
                try:
                    await status.edit_text("🔎 Подбираю для обложки фото получше…")
                except TelegramAPIError:
                    pass
                for i in _order(d)[1:]:
                    await _process(d, [i])
                    if d.processed[i].good_cover:
                        d.cover = i
                        break
        _start_background_processing(d)
    except Exception:  # noqa: BLE001
        log.exception("Ошибка обработки фото")
        await status.edit_text("😔 Не получилось обработать фото. Попробуйте другое фото или /new.")
        return

    text = d.text
    if d.link_title:
        # своё название — только если оно в первой строке; иначе берём название со страницы
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        if parse_details(first, settings.currency).title == "Новинка":
            text = f"{d.link_title}\n{text}"
    det = parse_details(text, settings.currency)
    det.brand = det.brand or d.link_brand
    kind = describe.detect_type(det.title)
    if kind and det.category == "clothes":
        det.category = kind[1]
    original_title = det.title
    det.title = describe.russian_title(det.title)  # «Draped tulle midi dress» → «Платье миди из тюля…»
    info = to_card_info(det, settings.shop_name, settings.contact)
    info.logo = load_logo()
    context = f"{original_title}\n{d.link_desc}\n{' '.join(det.tags)}"
    description = describe.selling_text(f"{det.title} {original_title}", det.category, context)
    hashtags = None
    if ai.enabled():
        try:
            await status.edit_text("✍️ Пишу текст поста…")
        except TelegramAPIError:
            pass
        await _process(d, _order(d))  # ИИ нужны все скриншоты: название часто на странице с текстом
        ordered = [d.processed[i] for i in _order(d)]
        ordered = ordered[:1] + [p for p in ordered[1:] if p.unusable] + [p for p in ordered[1:] if not p.unusable]
        res = await ai.describe([p.source or p.image for p in ordered], text)
        if res:
            info.title = (res.get("title") or info.title).strip()[:60]
            info.brand = info.brand or (res.get("brand") or "").strip()
            if res.get("category") in ("clothes", "shoes", "home", "beauty") and guess_category(text) == "clothes":
                info.category = res["category"]
            description = res.get("description") or description
            hashtags = [h for h in res.get("hashtags") or [] if isinstance(h, str)][:8] or None
    d.details, d.info = det, info
    d.caption = build_caption(det, info, description, hashtags)
    d.caption_plain = build_caption(det, info, description, hashtags, html_mode=False, max_len=2200)
    d.rendered = False

    await status.edit_text(f"🎨 Собираю карточки в {len(cards.STYLES)} стилях…")
    await _send_preview(bot, chat_id, d)
    try:
        await status.delete()
    except TelegramAPIError:
        pass


async def _send_preview(bot: Bot, chat_id: int, d: Draft) -> None:
    rec = cards.recommend_style(d.info)
    async with urgent():
        await _process(d, [d.cover])
        for _ in range(len(d.photos) - 1):  # обложка без товара (страница с текстом) — берём следующее фото
            if not d.processed[d.cover].unusable:
                break
            d.cover = (d.cover + 1) % len(d.photos)
            await _process(d, [d.cover])
        photos = [d.processed[d.cover]]
        async with HEAVY:
            img = await asyncio.to_thread(cards.render_preview, photos[0], d.info, d.use_cutout, rec)
    note = ""
    skipped = sum(1 for p in d.processed.values() if p.unusable)
    if skipped:
        note += f"\n\nПропускаю скриншоты без товара (страницы с текстом): {skipped}."
    if d.use_cutout and photos[0].cutout is None:
        note += "\n\nФон на обложке не отделился — фото стоит аккуратной карточкой. Лучше всего вырезается вещь на однотонном фоне."
    elif d.use_cutout and photos[0].cropped:
        note += "\n\nВещь на обложке обрезана краем кадра, поэтому фон оставил. Снимайте так, чтобы вещь целиком помещалась в кадр."
    preview = _jpeg(img, 90)
    _debug_save(d, "preview", [(f"cover{d.cover + 1}.jpg", preview)])
    await bot.send_photo(
        chat_id,
        BufferedInputFile(preview, "styles.jpg"),
        caption=f"Выберите стиль карточки. ★ — совет для этой категории.{note}",
        reply_markup=_style_keyboard(d),
    )


# ---------------- кнопки ----------------

def _draft_or_none(cb: CallbackQuery) -> Draft | None:
    d = drafts.get(cb.message.chat.id) if cb.message else None
    return d if d and d.info and d.photos else None


@router.callback_query(F.data.startswith("style:"))
async def cb_style(cb: CallbackQuery, bot: Bot) -> None:
    d = _draft_or_none(cb)
    if not d:
        await cb.answer("Черновик устарел — пришлите фото заново", show_alert=True)
        return
    style = cb.data.split(":", 1)[1]
    if style not in cards.STYLES:
        await cb.answer()
        return
    await cb.answer(f"Собираю «{cards.STYLE_NAMES[style]}»…")
    chat_id = cb.message.chat.id
    async with urgent():
        if any(i not in d.processed for i in range(len(d.photos))):
            status = await bot.send_message(chat_id, "⏳ Дообрабатываю остальные фото…")
            await _process(d, _order(d), status)
            try:
                await status.delete()
            except TelegramAPIError:
                pass
        photos = _photos(d)
        async with HEAVY:
            res = await asyncio.to_thread(cards.render_set, style, photos, d.info, d.use_cutout)
    d.style = style
    d.slides = [_jpeg(im) for im in res["slides"]]
    d.story = _jpeg(res["story"])
    d.rendered = True
    _debug_save(d, style, [(f"slide{i + 1}.jpg", b) for i, b in enumerate(d.slides)] + [("story.jpg", d.story)])
    media = [
        InputMediaPhoto(media=BufferedInputFile(b, f"card_{i + 1}.jpg"),
                        caption=d.caption if i == 0 else None, parse_mode=ParseMode.HTML)
        for i, b in enumerate(d.slides)
    ]
    await bot.send_media_group(chat_id, media)
    await bot.send_message(
        chat_id,
        f"Готово: карусель из {len(d.slides)} слайдов 4:5 (стиль «{cards.STYLE_NAMES[style]}»).\n"
        "Новый товар — просто пришлите новые фото. Поправить текст — отправьте его заново.",
        reply_markup=_actions_keyboard(d),
    )


@router.callback_query(F.data == "cut")
async def cb_cut(cb: CallbackQuery, bot: Bot) -> None:
    d = _draft_or_none(cb)
    if not d:
        await cb.answer("Черновик устарел — пришлите фото заново", show_alert=True)
        return
    d.use_cutout = not d.use_cutout
    await cb.answer("Фон вырезаю" if d.use_cutout else "Оставляю родной фон фото")
    await _send_preview(bot, cb.message.chat.id, d)


def _contact_sheet(d: Draft) -> bytes:
    """Все присланные фото с номерами — чтобы выбрать обложку."""
    th = 420
    tiles = []
    for raw in d.photos:
        im = load_image(raw)
        im.thumbnail((th * 2, th))
        tiles.append(im)
    cols = min(4, len(tiles))
    rows = (len(tiles) + cols - 1) // cols
    cw = max(t.width for t in tiles)
    sheet = Image.new("RGB", (cols * (cw + 12) + 12, rows * (th + 12) + 12), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    f = font("sans", 44, 800)
    for i, t in enumerate(tiles):
        x = 12 + (i % cols) * (cw + 12) + (cw - t.width) // 2
        y = 12 + (i // cols) * (th + 12)
        sheet.paste(t, (x, y))
        draw.ellipse((x + 10, y + 10, x + 76, y + 76), fill=(17, 17, 17))
        draw.text((x + 43, y + 43), str(i + 1), font=f, fill=(255, 255, 255), anchor="mm")
        if i == d.cover:
            draw.rectangle((x - 4, y - 4, x + t.width + 3, y + t.height + 3), outline=(255, 176, 0), width=6)
    return _jpeg(sheet, 88)


@router.callback_query(F.data == "covers")
async def cb_covers(cb: CallbackQuery) -> None:
    d = _draft_or_none(cb)
    if not d:
        await cb.answer("Черновик устарел — пришлите фото заново", show_alert=True)
        return
    await cb.answer()
    kb = InlineKeyboardBuilder()
    for i in range(len(d.photos)):
        kb.button(text=f"{i + 1}" + (" ✓" if i == d.cover else ""), callback_data=f"cover:{i}")
    kb.adjust(4)
    await cb.message.answer_photo(BufferedInputFile(await asyncio.to_thread(_contact_sheet, d), "photos.jpg"),
                                  caption="Какое фото поставить на обложку?", reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("cover:"))
async def cb_cover(cb: CallbackQuery, bot: Bot) -> None:
    d = _draft_or_none(cb)
    if not d:
        await cb.answer("Черновик устарел — пришлите фото заново", show_alert=True)
        return
    try:
        i = int(cb.data.split(":", 1)[1])
    except ValueError:
        await cb.answer()
        return
    if not 0 <= i < len(d.photos):
        await cb.answer()
        return
    d.cover = i
    await cb.answer(f"Обложка — фото {i + 1}")
    await _send_preview(bot, cb.message.chat.id, d)


@router.callback_query(F.data == "styles")
async def cb_styles(cb: CallbackQuery, bot: Bot) -> None:
    d = _draft_or_none(cb)
    if not d:
        await cb.answer("Черновик устарел — пришлите фото заново", show_alert=True)
        return
    await cb.answer()
    await _send_preview(bot, cb.message.chat.id, d)


@router.callback_query(F.data == "edit")
async def cb_edit(cb: CallbackQuery) -> None:
    d = _draft_or_none(cb)
    if not d:
        await cb.answer("Черновик устарел — пришлите фото заново", show_alert=True)
        return
    await cb.answer()
    d.rendered = False
    await cb.message.answer(
        "Пришлите исправленный текст — карточки пересоберу с теми же фото.\n\nСейчас было:\n"
        f"<pre>{html.escape(d.text or '-')}</pre>"
    )


@router.callback_query(F.data == "story")
async def cb_story(cb: CallbackQuery) -> None:
    d = _draft_or_none(cb)
    if not d or not d.story:
        await cb.answer("Сначала выберите стиль", show_alert=True)
        return
    await cb.answer()
    await cb.message.answer_photo(BufferedInputFile(d.story, "story.jpg"), caption="Сторис 9:16")


@router.callback_query(F.data == "files")
async def cb_files(cb: CallbackQuery) -> None:
    d = _draft_or_none(cb)
    if not d or not d.slides:
        await cb.answer("Сначала выберите стиль", show_alert=True)
        return
    await cb.answer("Отправляю файлы…")
    files = [(b, f"{d.style}_{i + 1}.jpg") for i, b in enumerate(d.slides)]
    if d.story:
        files.append((d.story, f"{d.style}_story.jpg"))
    media = [InputMediaDocument(media=BufferedInputFile(b, name)) for b, name in files[:10]]
    await cb.message.answer_media_group(media)
    await cb.message.answer("Файлы без сжатия Telegram — их удобно загружать в Instagram.")


@router.callback_query(F.data == "insta")
async def cb_insta(cb: CallbackQuery) -> None:
    d = _draft_or_none(cb)
    if not d:
        await cb.answer("Черновик устарел", show_alert=True)
        return
    await cb.answer()
    await cb.message.answer("Нажмите на текст ниже — он скопируется:")
    await cb.message.answer(f"<pre>{html.escape(d.caption_plain)}</pre>")


@router.callback_query(F.data == "publish")
async def cb_publish(cb: CallbackQuery, bot: Bot) -> None:
    d = _draft_or_none(cb)
    if not d or not d.slides:
        await cb.answer("Сначала выберите стиль", show_alert=True)
        return
    if not settings.channel_id:
        await cb.answer("Канал не задан: /channel @канал", show_alert=True)
        return
    media = [
        InputMediaPhoto(media=BufferedInputFile(b, f"card_{i + 1}.jpg"),
                        caption=d.caption if i == 0 else None, parse_mode=ParseMode.HTML)
        for i, b in enumerate(d.slides)
    ]
    try:
        await bot.send_media_group(settings.channel_id, media)
    except TelegramAPIError as e:
        log.warning("Публикация в %s не удалась: %s", settings.channel_id, e)
        await cb.answer()
        await cb.message.answer(
            "Не получилось опубликовать 😔\nПроверьте, что бот добавлен в администраторы канала "
            f"{html.escape(settings.channel_id)} с правом публиковать сообщения.\n\n<code>{html.escape(str(e))}</code>"
        )
        return
    await cb.answer("Опубликовано ✅")
    await cb.message.answer(f"✅ Пост опубликован в {html.escape(settings.channel_id)}")


# ---------------- запуск ----------------

async def run() -> None:
    if not config.bot_token:
        raise SystemExit("Не задан CARD_BOT_TOKEN — получите токен у @BotFather и впишите в card_bot/.env")
    session = AiohttpSession()
    ca_file = os.environ.get("SSL_CERT_FILE")
    if ca_file and os.path.exists(ca_file):  # корпоративный прокси со своим сертификатом
        session._connector_init["ssl"] = ssl.create_default_context(cafile=ca_file)
    bot = Bot(config.bot_token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.message.outer_middleware(access_middleware)
    dp.callback_query.outer_middleware(access_middleware)
    dp.include_router(router)
    await bot.set_my_commands([
        BotCommand(command="start", description="Как пользоваться"),
        BotCommand(command="new", description="Начать новую карточку"),
        BotCommand(command="settings", description="Название магазина, контакт, канал"),
        BotCommand(command="logo", description="Поставить логотип на карточки"),
        BotCommand(command="help", description="Подсказка по формату текста"),
    ])
    asyncio.get_running_loop().run_in_executor(None, warmup)  # модель фона грузится в фоне
    me = await bot.get_me()
    log.info("Бот @%s запущен. Модели фона: %s + %s. Claude: %s", me.username, config.rembg_fast_model, config.rembg_model,
             "вкл" if ai.enabled() else "выкл")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
