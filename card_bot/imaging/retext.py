"""Перевод надписей на картинках: находим текст, стираем его и пишем перевод в том же месте.

Где текст — определяет офлайн-распознавание (RapidOCR, точные рамки строк).
Что там написано и как это перевести — решает ИИ (ai.translate_blocks): он читает надписи прямо
с картинки, поэтому турецкие буквы и слитные слова ему не мешают.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from . import draw as D
from .fonts import font

log = logging.getLogger(__name__)
_ocr = None
_ocr_lock = threading.Lock()


@dataclass
class Line:
    box: tuple[int, int, int, int]  # x0, y0, x1, y1
    text: str


@dataclass
class Block:
    """Абзац: соседние строки одного размера и выравнивания."""
    lines: list[Line]

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (min(l.box[0] for l in self.lines), min(l.box[1] for l in self.lines),
                max(l.box[2] for l in self.lines), max(l.box[3] for l in self.lines))

    @property
    def text(self) -> str:
        return " ".join(l.text for l in self.lines)

    @property
    def line_h(self) -> float:
        return float(np.median([l.box[3] - l.box[1] for l in self.lines]))

    @property
    def align(self) -> str:
        if len(self.lines) < 2:
            return "l"
        lefts = [l.box[0] for l in self.lines]
        rights = [l.box[2] for l in self.lines]
        centers = [(l.box[0] + l.box[2]) / 2 for l in self.lines]
        spread = {"l": np.ptp(lefts), "r": np.ptp(rights), "m": np.ptp(centers)}
        return min(spread, key=spread.get)


def _get_ocr():
    global _ocr
    with _ocr_lock:
        if _ocr is None:
            from rapidocr_onnxruntime import RapidOCR

            _ocr = RapidOCR()
        return _ocr


def find_blocks(img: Image.Image, min_conf: float = 0.5) -> list[Block]:
    res, _ = _get_ocr()(np.asarray(img.convert("RGB")))
    lines = []
    for box, text, conf in res or []:
        if float(conf) < min_conf or len(text.strip()) < 2:
            continue
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        lines.append(Line((int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))), text.strip()))
    lines.sort(key=lambda l: (l.box[1], l.box[0]))
    blocks: list[Block] = []
    for ln in lines:
        h = ln.box[3] - ln.box[1]
        for b in blocks:
            last = b.lines[-1]
            lh = last.box[3] - last.box[1]
            gap = ln.box[1] - last.box[3]
            overlap_x = min(ln.box[2], last.box[2]) - max(ln.box[0], last.box[0])
            if -0.3 * h < gap < 0.8 * max(h, lh) and abs(h - lh) < 0.35 * max(h, lh) and overlap_x > 0:
                b.lines.append(ln)
                break
        else:
            blocks.append(Block([ln]))
    return blocks


def _text_mask(arr: np.ndarray, box: tuple[int, int, int, int], pad: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Маска букв внутри рамки строки + цвет фона и цвет букв."""
    h, w = arr.shape[:2]
    x0, y0, x1, y1 = max(0, box[0] - pad), max(0, box[1] - pad), min(w, box[2] + pad), min(h, box[3] + pad)
    patch = arr[y0:y1, x0:x1].astype(np.float32)
    border = np.concatenate([patch[0], patch[-1], patch[:, 0], patch[:, -1]])
    bg = np.median(border, axis=0)
    diff = np.abs(patch - bg).sum(axis=2)
    m = diff > 60
    ink = np.median(patch[m], axis=0) if m.sum() > 10 else (255 - bg)
    full = np.zeros((h, w), bool)
    full[y0:y1, x0:x1] = m
    return full, bg, ink


def replace_text(img: Image.Image, blocks: list[Block], translations: list[str | None]) -> Image.Image:
    """Стирает исходные надписи и пишет перевод тем же цветом, по тому же выравниванию."""
    import cv2

    arr = np.asarray(img.convert("RGB")).copy()
    h_img = arr.shape[0]
    todo = [(b, t.strip()) for b, t in zip(blocks, translations) if t and t.strip()]
    if not todo:
        return img
    inpaint_mask = np.zeros(arr.shape[:2], np.uint8)
    styles = []
    for b, _ in todo:
        inks, bgs = [], []
        for ln in b.lines:
            lh = ln.box[3] - ln.box[1]
            pad = max(3, int(lh * 0.2))
            m, bg, ink = _text_mask(arr, ln.box, pad=pad)
            inks.append(ink)
            bgs.append(bg)
            x0, y0 = max(0, ln.box[0] - pad), max(0, ln.box[1] - pad)
            x1, y1 = min(arr.shape[1], ln.box[2] + pad), min(h_img, ln.box[3] + pad)
            patch = arr[y0:y1, x0:x1].astype(np.float32)
            border = np.concatenate([patch[0], patch[-1], patch[:, 0], patch[:, -1]])
            if border.std(axis=0).sum() < 18:  # однотонный фон (плашка, белое поле) — просто заливаем
                arr[y0:y1, x0:x1] = bg.astype(np.uint8)
            else:  # текст поверх фото — аккуратно «закрашиваем» по соседним пикселям
                inpaint_mask[m] = 255
        styles.append((np.median(inks, axis=0), np.median(bgs, axis=0)))
    if inpaint_mask.any():
        inpaint_mask = cv2.dilate(inpaint_mask, np.ones((3, 3), np.uint8), iterations=3)
        arr = cv2.inpaint(arr, inpaint_mask, 6, cv2.INPAINT_TELEA)
    out = Image.fromarray(arr).convert("RGBA")

    all_boxes = [b.box for b in blocks]
    w_img = arr.shape[1]
    for (b, ru), (ink, bg) in zip(todo, styles):
        x0, y0, x1, y1 = b.box
        bh = y1 - y0
        # перевод обычно длиннее — разрешаем расшириться в свободное место (до соседней надписи, не больше 1.5×)
        others = [ob for ob in all_boxes if ob != b.box and min(ob[3], y1) - max(ob[1], y0) > 0]
        min_x = max([ob[2] for ob in others if ob[2] <= x0] + [0]) + 8
        max_x = min([ob[0] for ob in others if ob[0] >= x1] + [w_img]) - 8
        bw0 = x1 - x0
        # расширяться можно только пока под текстом тот же фон (иначе белый текст уедет с розовой плашки)
        bgc = np.asarray(bg, dtype=np.float32)

        def same_bg(x: int) -> bool:
            col = arr[y0:y1, x].astype(np.float32)
            return float(np.abs(np.median(col, axis=0) - bgc).sum()) < 40

        lx = x0
        while lx - 1 >= min_x and same_bg(lx - 1):
            lx -= 1
        rx = x1
        while rx + 1 <= max_x and rx + 1 < w_img and same_bg(rx + 1):
            rx += 1
        min_x, max_x = max(min_x, lx + 6), min(max_x, rx - 6)
        if b.align == "r":
            x0 = max(min_x, x1 - int(bw0 * 1.5))
        elif b.align == "m":
            cx = (x0 + x1) / 2
            half = min(cx - min_x, max_x - cx, bw0 * 0.75)
            x0, x1 = int(cx - half), int(cx + half)
        else:
            x1 = min(max_x, x0 + int(bw0 * 1.5))
        bw = x1 - x0
        # сколько места свободно ниже блока (до следующей надписи)
        below = [ob[1] for ob in all_boxes if ob[1] >= y1 and min(ob[2], x1) - max(ob[0], x0) > 0]
        room = min([*below, h_img]) - y0 - b.line_h * 0.3
        avail_h = max(bh, min(room, bh + b.line_h * 1.6, bh * 2.4))
        weight = 600
        size = max(8, int(b.line_h * 0.9))
        while True:
            f = font("sans", size, weight)
            lines, ok = D.wrap(ru, f, bw, 12)
            if ok and size * 1.22 * len(lines) <= avail_h or size <= 8:
                break
            size -= 1
        lh = size * 1.22
        total = lh * len(lines)
        top = y0 + (bh - total) / 2 if total <= bh else y0
        y = top + D.cap_h(f) + (lh - size) / 2
        color = tuple(int(c) for c in ink)
        for ln in lines:
            if b.align == "r":
                D.draw_text(out, x1, y, ln, f, color, anchor="rs")
            elif b.align == "m":
                D.draw_text(out, (x0 + x1) / 2, y, ln, f, color, anchor="ms")
            else:
                D.draw_text(out, x0, y, ln, f, color, anchor="ls")
            y += lh
    return out.convert("RGB")


def debug_boxes(img: Image.Image, blocks: list[Block]) -> Image.Image:
    out = img.convert("RGB").copy()
    d = ImageDraw.Draw(out)
    for i, b in enumerate(blocks):
        d.rectangle(b.box, outline=(255, 0, 0), width=2)
        d.text((b.box[0], b.box[1] - 12), str(i), fill=(255, 0, 0))
    return out
