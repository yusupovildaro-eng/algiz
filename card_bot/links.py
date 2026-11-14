"""Ссылка на товар → оригинальные фото в высоком разрешении и название со страницы магазина.

Скриншот с телефона даёт ~450 px на товар, а карточке нужно 1080 px. На странице товара почти
всегда есть официальное фото 1500–2000 px (тег og:image или разметка schema.org Product).
"""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import aiohttp

from .text import find_brand

log = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
}
MAX_HTML = 6 * 1024 * 1024
MAX_IMAGE = 15 * 1024 * 1024
MAX_IMAGES = 4


@dataclass
class LinkProduct:
    url: str
    title: str = ""
    brand: str = ""
    description: str = ""
    images: list[bytes] = field(default_factory=list)


async def _read(r: aiohttp.ClientResponse, limit: int) -> bytes:
    """Читает ответ целиком, но не больше limit байт."""
    buf = bytearray()
    async for chunk in r.content.iter_chunked(1 << 16):
        buf += chunk
        if len(buf) >= limit:
            break
    return bytes(buf[:limit])


def find_url(text: str) -> str | None:
    m = URL_RE.search(text or "")
    return m.group(0).rstrip(".,);]") if m else None


def _meta(page: str, *names: str) -> list[str]:
    out = []
    for tag in re.findall(r"<meta\b[^>]*>", page, re.I):
        key = re.search(r"(?:property|name|itemprop)\s*=\s*[\"']([^\"']+)", tag, re.I)
        val = re.search(r"content\s*=\s*[\"']([^\"']*)", tag, re.I)
        if key and val and key.group(1).lower() in names:
            out.append(html.unescape(val.group(1)).strip())
    return out


def _jsonld_products(page: str) -> list[dict]:
    found = []
    for block in re.findall(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", page, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            it = stack.pop()
            if isinstance(it, list):
                stack.extend(it)
            elif isinstance(it, dict):
                if "@graph" in it:
                    stack.extend(it["@graph"] if isinstance(it["@graph"], list) else [it["@graph"]])
                t = it.get("@type")
                if t == "Product" or (isinstance(t, list) and "Product" in t):
                    found.append(it)
    return found


def _image_urls(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return _image_urls(value.get("url") or value.get("contentUrl") or "")
    if isinstance(value, list):
        return [u for v in value for u in _image_urls(v)]
    return []


def clean_title(title: str, site: str = "") -> str:
    """«45% Off* Airbrush Flawless Setting Spray Duo | Charlotte Tilbury» → «Airbrush Flawless Setting Spray Duo»."""
    t = re.split(r"\s+[|•]\s+", html.unescape(title or "").strip())[0]
    if site:
        t = re.sub(rf"\s*[-–—:]\s*{re.escape(site)}\s*$", "", t, flags=re.I)
    t = re.sub(r"^\s*(?:up to\s*)?\d+\s*%\s*off\*?\s*[:\-–]?\s*", "", t, flags=re.I)
    t = re.sub(r"^\s*(?:sale|new|скидка)\s*[:\-–!]\s*", "", t, flags=re.I)
    return t.strip(" *")


async def fetch_product(url: str) -> LinkProduct:
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("нужна ссылка http(s)")
    res = LinkProduct(url=url)
    timeout = aiohttp.ClientTimeout(total=40, sock_read=20)
    async with aiohttp.ClientSession(headers=HEADERS, timeout=timeout) as s:
        async with s.get(url, allow_redirects=True, max_redirects=5) as r:
            r.raise_for_status()
            raw = await _read(r, MAX_HTML)
            page = raw.decode(r.charset or "utf-8", errors="ignore")
            base = str(r.url)

        site = (_meta(page, "og:site_name") or [""])[0]
        products = _jsonld_products(page)
        title = ""
        if products and products[0].get("name"):
            title = str(products[0]["name"])
        title = title or (_meta(page, "og:title", "twitter:title") or [""])[0]
        if not title:
            m = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
            title = m.group(1) if m else ""
        res.title = clean_title(title, site)
        desc = str(products[0].get("description") or "") if products else ""
        desc = desc or (_meta(page, "og:description", "description", "twitter:description") or [""])[0]
        res.description = re.sub(r"<[^>]+>", " ", html.unescape(desc))[:1500]
        brand = products[0].get("brand") if products else None
        if isinstance(brand, dict):
            brand = brand.get("name")
        res.brand = find_brand(f"{brand or ''} {res.title} {site}")

        urls: list[str] = []
        for p in products:
            urls += _image_urls(p.get("image"))
        urls += _meta(page, "og:image:secure_url", "og:image", "twitter:image", "twitter:image:src")
        seen, ordered = set(), []
        for u in urls:
            u = urljoin(base, u.strip())
            if u.startswith("//"):
                u = "https:" + u
            if u.startswith("http") and u not in seen:
                seen.add(u)
                ordered.append(u)

        from io import BytesIO

        from PIL import Image

        for u in ordered:
            if len(res.images) >= MAX_IMAGES:
                break
            try:
                async with s.get(u, headers={"Referer": base}) as r:
                    if r.status != 200 or not (r.content_type or "").startswith("image/"):
                        continue
                    data = await _read(r, MAX_IMAGE)
                with Image.open(BytesIO(data)) as im:
                    if min(im.size) < 500:
                        continue  # иконки и превью нам не нужны
                res.images.append(data)
            except Exception as e:  # noqa: BLE001
                log.info("Картинка %s не скачалась: %s", u, e)
    return res
