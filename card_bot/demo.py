"""Проверка без Telegram: делает карточки из фото на диске.

    python -m card_bot.demo фото1.jpg фото2.jpg --text "Nike Air Force 1, 12900, 40-44" --out out/
    python -m card_bot.demo фото.jpg --style drop --no-cutout
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from .config import config
from .imaging import cards
from .imaging.photo import process
from .describe import russian_title, selling_text
from .imaging.logo import circle_logo
from .text import build_caption, parse_details, to_card_info


def main() -> None:
    ap = argparse.ArgumentParser(description="Сделать карточки товара из фото")
    ap.add_argument("photos", nargs="+", type=Path)
    ap.add_argument("--text", default="Новинка", help="название, цена, размеры — как написали бы боту")
    ap.add_argument("--style", choices=cards.STYLES + ["all"], default="all")
    ap.add_argument("--out", type=Path, default=Path("card_out"))
    ap.add_argument("--no-cutout", action="store_true", help="не вырезать фон")
    ap.add_argument("--shop", default=config.shop_name)
    ap.add_argument("--contact", default=config.contact or "@my_shop")
    ap.add_argument("--logo", type=Path, help="картинка логотипа (вырежется круг)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    args.out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    photos = [process(p.read_bytes(), remove_bg=not args.no_cutout) for p in args.photos]
    print(f"Фото обработаны за {time.time() - t0:.1f} с (фон вырезан: {[p.has_cutout for p in photos]})")

    d = parse_details(args.text, config.currency)
    d.title = russian_title(d.title)
    info = to_card_info(d, args.shop, args.contact)
    if args.logo:
        info.logo = circle_logo(args.logo.read_bytes())
    use_cut = not args.no_cutout
    rec = cards.recommend_style(info)
    cards.render_preview(photos[0], info, use_cut, rec).save(args.out / "preview.jpg", quality=92)
    styles = cards.STYLES if args.style == "all" else [args.style]
    for st in styles:
        t1 = time.time()
        res = cards.render_set(st, photos, info, use_cut)
        for i, im in enumerate(res["slides"], 1):
            im.save(args.out / f"{st}_{i}.jpg", quality=94, subsampling=0)
        res["story"].save(args.out / f"{st}_story.jpg", quality=94, subsampling=0)
        print(f"{st}: {len(res['slides'])} слайдов + сторис за {time.time() - t1:.1f} с")
    desc = selling_text(info.title, info.category)
    (args.out / "caption.txt").write_text(build_caption(d, info, desc, html_mode=False), encoding="utf-8")
    print(f"Готово: {args.out.resolve()}")


if __name__ == "__main__":
    main()
