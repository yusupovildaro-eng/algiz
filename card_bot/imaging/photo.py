"""Подготовка фото: поворот по EXIF, удаление фона, цвет/свет/резкость, анализ цвета товара."""

from __future__ import annotations

import colorsys
import io
import logging
import threading
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from ..config import config

log = logging.getLogger(__name__)

try:  # фото с iPhone, присланные файлом (HEIC)
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass

MAX_SIDE = 2000

_session = None
_session_lock = threading.Lock()


@dataclass
class ProductPhoto:
    image: Image.Image  # улучшенное фото целиком (RGB)
    cutout: Image.Image | None  # товар без фона, обрезан по краям (RGBA) или None
    color: tuple[int, int, int]  # основной цвет товара
    luminance: float  # средняя яркость товара 0..1
    saturation: float  # насыщенность основного цвета 0..1
    cropped: bool = False  # товар обрезан краем кадра — вырезанным он будет смотреться странно
    source: Image.Image | None = None  # исходник целиком (для ИИ: прочитать название со скриншота)
    from_screenshot: bool = False

    @property
    def has_cutout(self) -> bool:
        return self.cutout is not None


def load_image(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    img = img.convert("RGB")
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    return img


def _get_session():
    global _session
    with _session_lock:
        if _session is None:
            from rembg import new_session

            log.info("Загружаю модель удаления фона %s (первый раз скачивается ~200 МБ)…", config.rembg_model)
            _session = new_session(config.rembg_model)
        return _session


def warmup() -> None:
    """Заранее скачать и загрузить модель, чтобы первое фото обработалось быстро."""
    try:
        _get_session()
    except Exception:  # noqa: BLE001
        log.exception("Не удалось загрузить модель удаления фона")


def _predict_mask(img: Image.Image) -> np.ndarray:
    from rembg import remove

    mask = remove(img, session=_get_session(), only_mask=True)
    if not isinstance(mask, Image.Image):
        mask = Image.open(io.BytesIO(mask)) if isinstance(mask, bytes) else Image.fromarray(mask)
    mask = mask.convert("L").resize(img.size, Image.BILINEAR)
    return np.asarray(mask, dtype=np.float32) / 255.0


def _clean_mask(alpha: np.ndarray) -> np.ndarray:
    """Убирает шум и мелкие «острова» фона, оставляя товар (в т.ч. пару обуви)."""
    from scipy import ndimage

    alpha = np.where(alpha < 0.05, 0.0, alpha)
    alpha = np.where(alpha > 0.95, 1.0, alpha)
    solid = alpha > 0.5
    labels, n = ndimage.label(solid)
    if n > 1:
        sizes = ndimage.sum(solid, labels, index=np.arange(1, n + 1))
        keep_ids = np.where(sizes >= sizes.max() * 0.04)[0] + 1
        keep = np.isin(labels, keep_ids)
        # мягкие края оставляем только рядом с сохранёнными областями
        near = ndimage.binary_dilation(keep, iterations=6)
        alpha = np.where(near, alpha, 0.0)
        alpha = np.where(solid & ~keep, 0.0, alpha)
    # заполнить дырки внутри товара (блики на белой коже и т.п.)
    filled = ndimage.binary_fill_holes(alpha > 0.5)
    holes = filled & (alpha <= 0.5)
    if holes.sum() < filled.sum() * 0.05:  # большие дыры — это реальные просветы, не трогаем
        alpha = np.where(holes, 1.0, alpha)
    return alpha


def _product_region(alpha: np.ndarray) -> tuple[int, int, int, int] | None:
    """Скриншот страницы магазина: находит сам товар среди кнопок, текста и панелей браузера.

    Берём крупнейший «нормальный» объект (не полосу-кнопку и не строку текста) и всё,
    что стоит с ним в один ряд (дуэты, наборы), и обрезаем кадр вокруг них.
    """
    from scipy import ndimage

    h, w = alpha.shape
    solid = alpha > 0.5
    lab, n = ndimage.label(solid)
    if n == 0:
        return None
    objs = ndimage.find_objects(lab)
    areas = ndimage.sum(solid, lab, index=np.arange(1, n + 1))
    comps = []
    for sl, ar in zip(objs, areas):
        y0, y1, x0, x1 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop
        bh, bw = y1 - y0, x1 - x0
        if ar < 0.002 * w * h or bw / bh > 5 or bh / bw > 9 or bw > 0.92 * w:
            continue  # мелочь, кнопки, строки текста, панели во всю ширину
        comps.append((float(ar), y0, y1, x0, x1))
    if not comps:
        return None
    main = max(comps)
    my0, my1 = main[1], main[2]
    sel = [c for c in comps
           if c[0] >= 0.03 * main[0] and min(c[2], my1) - max(c[1], my0) > 0.3 * min(c[2] - c[1], my1 - my0)]
    y0, y1 = min(c[1] for c in sel), max(c[2] for c in sel)
    x0, x1 = min(c[3] for c in sel), max(c[4] for c in sel)
    pad = int(0.08 * max(y1 - y0, x1 - x0))
    box = (max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad))
    if (box[2] - box[0]) * (box[3] - box[1]) > 0.8 * w * h:
        return None
    return box


def _touches_frame(alpha: np.ndarray) -> bool:
    edges = [alpha[0, :], alpha[-1, :], alpha[:, 0], alpha[:, -1]]
    fr = [float((e > 0.5).mean()) for e in edges]
    return max(fr) > 0.22 or sum(f > 0.06 for f in fr) >= 2


def _defringe(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Убирает ореол цвета старого фона по краям товара."""
    from scipy import ndimage

    core = (alpha > 0.92).astype(np.float32)
    if core.sum() < 50:
        return rgb
    sigma = 3.0
    w = ndimage.gaussian_filter(core, sigma) + 1e-6
    est = np.stack([ndimage.gaussian_filter(rgb[..., c] * core, sigma) for c in range(3)], axis=-1) / w[..., None]
    edge = ((alpha > 0.01) & (alpha < 0.92))[..., None]
    t = np.clip(1.0 - alpha, 0, 1)[..., None] * 0.85
    fixed = rgb * (1 - t) + est * t
    return np.where(edge, fixed, rgb)


def _white_balance(arr: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Мягкий баланс белого по нейтральным участкам (фон/стена/пол)."""
    if ref.size < 300:
        return arr
    lum = ref.mean(axis=1)
    sel = ref[(lum > 0.25) & (lum < 0.97)]
    if len(sel) < 200:
        return arr
    mean = sel.mean(axis=0)
    gray = mean.mean()
    gains = gray / np.maximum(mean, 1e-4)
    # если фон явно цветной (розовая стена) — корректируем совсем слабо
    strength = 0.75 if np.abs(gains - 1).max() < 0.2 else 0.3
    gains = 1 + (np.clip(gains, 0.86, 1.16) - 1) * strength
    return np.clip(arr * gains, 0, 1)


def _tone(arr: np.ndarray, weights: np.ndarray | None) -> np.ndarray:
    """Автоуровни + мягкий S-контраст + осветление тёмных фото (товар в фокусе)."""
    lum = arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    sample = lum[weights > 0.6] if weights is not None and (weights > 0.6).sum() > 500 else lum.ravel()
    lo, hi = np.percentile(sample, [0.7, 99.6])
    lo = min(lo, 0.08)
    hi = max(hi, 0.82)
    out = np.clip((arr - lo) / max(hi - lo, 1e-3), 0, 1)
    out = arr * 0.3 + out * 0.7
    med = float(np.median(sample))
    if med < 0.4:  # тёмное фото из квартиры — вытягиваем тени
        gamma = float(np.clip(np.log(0.45) / np.log(max(med, 0.05)), 0.78, 1.0))
        out = np.power(out, gamma)
    # лёгкая S-кривая
    out = out + 0.08 * (out - 0.5) * (1 - (2 * out - 1) ** 2)
    return np.clip(out, 0, 1)


def _vibrance(img: Image.Image, amount: float = 0.12) -> Image.Image:
    hsv = np.asarray(img.convert("HSV"), dtype=np.float32)
    s = hsv[..., 1] / 255.0
    s = s + amount * s * (1 - s) * 2  # сильнее для блеклых, слабее для насыщенных
    hsv[..., 1] = np.clip(s, 0, 1) * 255
    return Image.fromarray(hsv.astype(np.uint8), "HSV").convert("RGB")


def _analyze(rgb: np.ndarray, alpha: np.ndarray | None) -> tuple[tuple[int, int, int], float, float]:
    if alpha is not None and (alpha > 0.8).sum() > 200:
        px = rgb[alpha > 0.8]
    else:
        h, w = rgb.shape[:2]
        px = rgb[h // 4 : h * 3 // 4, w // 4 : w * 3 // 4].reshape(-1, 3)
    if len(px) > 40000:
        px = px[np.random.default_rng(0).choice(len(px), 40000, replace=False)]
    lum = float((px @ np.array([0.299, 0.587, 0.114])).mean())
    # основной цвет: самый частый кластер после квантования
    small = Image.fromarray((px.reshape(-1, 1, 3) * 255).astype(np.uint8))
    q = small.quantize(colors=5, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[:15]
    counts = sorted(q.getcolors(), reverse=True)
    idx = counts[0][1]
    color = tuple(pal[idx * 3 : idx * 3 + 3])
    _, s, _ = colorsys.rgb_to_hsv(*(c / 255 for c in color))
    return color, lum, s  # type: ignore[return-value]


def process(data: bytes, remove_bg: bool = True) -> ProductPhoto:
    img = load_image(data)
    source = img
    screenshot = False

    alpha = None
    if remove_bg:
        try:
            raw = _predict_mask(img)
            if img.height / img.width > 1.55:  # похоже на скриншот с телефона
                region = _product_region(raw)
                if region:
                    x0, y0, x1, y1 = region
                    img = img.crop(region)
                    raw = raw[y0:y1, x0:x1]
                    screenshot = True
                    log.info("Скриншот: товар найден в области %s", region)
            alpha = _clean_mask(raw)
            cover = float(alpha.mean())
            if cover < 0.015 or cover > 0.985:
                log.info("Фон не отделился (покрытие %.3f) — оставляю фото как есть", cover)
                alpha = None
        except Exception:  # noqa: BLE001
            log.exception("Ошибка удаления фона — оставляю фото как есть")
            alpha = None

    rgb = np.asarray(img, dtype=np.float32) / 255.0
    ref = rgb[alpha < 0.05] if alpha is not None else rgb.reshape(-1, 3)
    if len(ref) > 60000:
        ref = ref[np.random.default_rng(1).choice(len(ref), 60000, replace=False)]
    out = _white_balance(rgb, ref)
    out = _tone(out, alpha)
    enhanced = _vibrance(Image.fromarray((out * 255).astype(np.uint8)))

    cutout = None
    cropped = alpha is not None and not screenshot and _touches_frame(alpha)
    if alpha is not None:
        e = np.asarray(enhanced, dtype=np.float32) / 255.0
        e = _defringe(e, alpha)
        a = Image.fromarray((alpha * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6))
        rgba = Image.fromarray((e * 255).astype(np.uint8)).convert("RGBA")
        rgba.putalpha(a)
        bbox = a.point(lambda v: 255 if v > 8 else 0).getbbox()
        if bbox:
            cutout = rgba.crop(bbox)

    final_rgb = np.asarray(enhanced, dtype=np.float32) / 255.0
    color, lum, sat = _analyze(final_rgb, alpha)
    return ProductPhoto(image=enhanced, cutout=cutout, color=color, luminance=lum, saturation=sat, cropped=cropped,
                        source=source, from_screenshot=screenshot)
