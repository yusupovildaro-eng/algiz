"""
Build and post a product card to the Telegram channel.

Supports two sources:
  - armaselektronik.com (Turkish) → Claude translates to Russian
  - elina.ru (Russian) → Claude formats existing Russian text
"""

import os
import re
import requests
from claude_client import generate
from telegram_client import send_photo_bytes, send_text
from db import is_posted, mark_posted

SHOP_LINK = "https://algiz.uz/ru"
MANAGER_LINK = "https://t.me/www_aloqa_uz"

COUNTRY = {
    "armas": "🇹🇷 Ishlab chiqaruvchi: Turkiya",
    "elina": "🇷🇺 Ishlab chiqaruvchi: Rossiya",
    "wlb":   "🇨🇳 Ishlab chiqaruvchi: Xitoy",
}

# Telegram caption limit is 1024 chars
CAPTION_LIMIT = 1024

BRAND_NAME = {
    "armas": "ARMAS ELEKTRONİK",
    "elina": 'ПК "ЭЛИНА"',
    "wlb":   "WARNING LIGHT BARS",
}


def _clean(text: str) -> str:
    """Remove all markdown formatting from Claude output."""
    text = text.replace('**', '').replace('__', '')
    text = re.sub(r'^#{1,3}\s+', '', text, flags=re.MULTILINE)
    # Remove orphaned single * that aren't part of ✅ or emoji
    text = re.sub(r'(?<!\S)\*(?!\s)', '', text)
    text = re.sub(r'(?<!\*)\*(?!\S)', '', text)
    return text.strip()


def _build_caption(name_ru: str, body: str, source: str = "") -> str:
    country = COUNTRY.get(source, "")
    footer = (
        (f"\n\n{country}" if country else "")
        + "\n\n🚚 O'zbekiston bo'ylab yetkazib beramiz. Xabar yozing — tez javob beramiz!"
        + f'\n\n💬 <a href="{MANAGER_LINK}">Menejer bilan bog\'lanish</a>'
        + f'\n🔗 <a href="{SHOP_LINK}">Batafsil / buyurtma</a>'
    )
    header = f"<b>{name_ru}</b>\n\n"
    max_body = CAPTION_LIMIT - len(header) - len(footer) - 10
    if len(body) > max_body:
        body = body[:max_body].rsplit("\n", 1)[0]
    return header + body + footer


def _parse_features(raw: str) -> list[str]:
    """Extract XUSUSIYATLAR line → list of 3 short strings."""
    for line in raw.splitlines():
        if line.upper().startswith("XUSUSIYATLAR:") or line.upper().startswith("ОСОБЕННОСТИ:"):
            parts = line.split(":", 1)[1]
            items = [p.strip() for p in parts.split("|") if p.strip()]
            return (items + [""] * 3)[:3]
    return ["", "", ""]


def _generate_card_armas(product: dict) -> tuple[str, str, list[str]]:
    """Turkish source → translate + format."""
    features_block = "\n".join(f"- {f}" for f in product.get("features_tr", [])[:10])
    prompt = f"""Tarjima qilib, Telegram kanal uchun mahsulot kartochkasini yoz. Mahsulot turk ishlab chiqaruvchisining saytidan.

Turk nomi: {product.get('name_tr', '')}
Tavsif (TR): {product.get('description_tr', '')[:500]}
Xususiyatlari (TR):
{features_block}

Quyidagi formatda yoz:
NOMI: <o'zbek tilida qisqa tijorat nomi, 3-7 so'z>
MATN: <mahsulot haqida 2-3 ta jonli jumla + ✅ belgili 2-4 ta asosiy xususiyat>
XUSUSIYATLAR: <| bilan ajratilgan 3 ta asosiy afzallik, har biri 2-4 so'z>

Talablar:
- hamma narsa o'zbek tilida, aniq, suvsiz
- ✅ belgisi bilan boshlanadigan bandlar
- narxni UMUMAN eslatma
- butun matn 600 belgidan oshmasin"""
    raw = generate(prompt, max_tokens=500)
    name, body = _parse_response(raw, product.get("name_tr", "Товар"))
    return name, body, _parse_features(raw)


def _generate_card_elina(product: dict) -> tuple[str, str, list[str]]:
    """Russian source → improve formatting."""
    specs_block = "\n".join(f"- {s}" for s in product.get("specs_ru", [])[:8])
    prompt = f"""Ishlab chiqaruvchi saytidagi ma'lumotlar asosida Telegram kanal uchun mahsulot kartochkasini yoz.

Nomi: {product.get('name_ru', '')}
Tavsif: {product.get('description_ru', '')[:500]}
Texnik xususiyatlar:
{specs_block}

Quyidagi formatda yoz:
NOMI: <o'zbek tilida nomi, 3-7 so'z>
MATN: <mahsulot haqida 2-3 ta jonli jumla + ✅ belgili 2-4 ta asosiy xususiyat>
XUSUSIYATLAR: <| bilan ajratilgan 3 ta asosiy afzallik, har biri 2-4 so'z>

Talablar:
- aniq, professional
- ✅ belgisi bilan bandlar
- narxni UMUMAN eslatma
- harakatga chaqiruv, sayt havolasi, buyurtma yoki aloqa ma'lumotlarini QO'SHMA — ular avtomatik qo'shiladi
- butun matn 600 belgidan oshmasin"""
    raw = generate(prompt, max_tokens=500)
    name, body = _parse_response(raw, product.get("name_ru", "Товар"))
    return name, body, _parse_features(raw)


def _generate_card_wlb(product: dict) -> tuple[str, str, list[str]]:
    """English source → translate + format."""
    specs_block = "\n".join(f"- {s}" for s in product.get("specs_en", [])[:10])
    prompt = f"""Tarjima qilib, Telegram kanal uchun mahsulot kartochkasini yoz. Mahsulot ingliz tilidagi ishlab chiqaruvchi saytidan.

Inglizcha nomi: {product.get('name_en', '')}
Tavsif (EN): {product.get('description_en', '')[:500]}
Xususiyatlari (EN):
{specs_block}

Quyidagi formatda yoz:
NOMI: <o'zbek tilida qisqa tijorat nomi, 3-7 so'z>
MATN: <mahsulot haqida 2-3 ta jonli jumla + ✅ belgili 2-4 ta asosiy xususiyat>
XUSUSIYATLAR: <| bilan ajratilgan 3 ta asosiy afzallik, har biri 2-4 so'z>

Talablar:
- hamma narsa o'zbek tilida, aniq, suvsiz
- ✅ belgisi bilan boshlanadigan bandlar
- narxni UMUMAN eslatma
- butun matn 600 belgidan oshmasin"""
    raw = generate(prompt, max_tokens=500)
    name, body = _parse_response(raw, product.get("name_en", "Товар"))
    return name, body, _parse_features(raw)


def _parse_response(raw: str, fallback_name: str) -> tuple[str, str]:
    name = fallback_name
    body = raw
    if "NOMI:" in raw:
        parts = raw.split("NOMI:", 1)[1]
        if "MATN:" in parts:
            name = parts.split("MATN:", 1)[0].strip()
            body = parts.split("MATN:", 1)[1].strip()
        else:
            name = parts.strip().split("\n")[0].strip()
    elif "НАЗВАНИЕ:" in raw:
        parts = raw.split("НАЗВАНИЕ:", 1)[1]
        if "ТЕКСТ:" in parts:
            name = parts.split("ТЕКСТ:", 1)[0].strip()
            body = parts.split("ТЕКСТ:", 1)[1].strip()
        else:
            name = parts.strip().split("\n")[0].strip()
    return _clean(name), _clean(body)


def post_product_card(product: dict, force: bool = False) -> bool:
    """Post product card. Returns True if posted, False if skipped."""
    if not force and is_posted(product["id"]):
        return False

    source = product.get("source", "armas")
    if source == "elina":
        name_ru, body, features = _generate_card_elina(product)
    elif source == "wlb":
        name_ru, body, features = _generate_card_wlb(product)
    else:
        name_ru, body, features = _generate_card_armas(product)

    caption = _build_caption(name_ru, body, source)

    images = product.get("gallery_images") or (
        [product["image_url"]] if product.get("image_url") else []
    )

    posted = False
    for img_url in images:
        try:
            resp = requests.get(
                img_url,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.elina.ru/"},
                timeout=15,
            )
            resp.raise_for_status()
            if len(resp.content) < 1000:
                continue

            send_photo_bytes(resp.content, "photo", caption)
            posted = True
            break
        except Exception as e:
            print(f"  [img fail] {img_url}: {e}")
            continue

    if not posted:
        send_text(caption)

    mark_posted(product["id"], product.get("product_url", ""))
    return True
