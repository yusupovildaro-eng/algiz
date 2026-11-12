"""
Составитель себестоимости импортных товаров Algiz.

Метод — из founder-cfo (founder-skill, Jake Schincariol, MIT): каждая цифра
отчёта выводится из входного файла и курса ЦБ; недостающее поле — ошибка,
а не догадка.

    python sebestoimost.py                         sebestoimost.json → отчёт в консоль
    python sebestoimost.py --out reports/sebestoimost/2026-11-12.md
    python sebestoimost.py --json                  цифры в JSON
    python sebestoimost.py --product tsefey --price 1400000    что если цена другая
    python sebestoimost.py --rate RUB=160          что если курс другой

Себестоимость одной штуки, в сумах:
    закупка          = цена поставщика × курс
    комиссия банка   = закупка × bank_fee_pct
    доставка         = доставка на 1 шт × курс
    там. стоимость   = закупка + доставка
    пошлина          = там. стоимость × duty_pct
    там. сбор        = там. стоимость × customs_fee_pct
    НДС при ввозе    = (там. стоимость + пошлина) × vat_pct  — в себестоимость, если вы не плательщик НДС
    прочее           = extra_per_unit (сертификация, упаковка, ...)

Продажа:
    не плательщик НДС: остаётся = цена − цена × sales_tax_pct (налог с оборота)
    плательщик НДС:    остаётся = цена / (1 + vat_pct)
    прибыль с 1 шт = остаётся − себестоимость;  маржа = прибыль / цена
"""

import argparse
import json
import sys
from pathlib import Path

from rates import get_rates

DEFAULT_PATH = Path(__file__).parent / "sebestoimost.json"


class NumbersError(Exception):
    pass


# ── Расчёт ───────────────────────────────────────────────────────────────────

def _num(obj: dict, field: str, where: str, required: bool = True, default=None) -> float | None:
    if field not in obj or obj[field] is None:
        if required:
            raise NumbersError(f"{where}: нет поля '{field}'")
        return default
    try:
        return float(obj[field])
    except (TypeError, ValueError):
        raise NumbersError(f"{where}: '{field}' — не число: {obj[field]!r}")


def _rate(rates: dict, currency: str, where: str) -> float:
    ccy = str(currency or "").upper()
    if ccy not in rates:
        raise NumbersError(f"{where}: нет курса для валюты '{currency}' (добавьте в settings.manual_rates)")
    return rates[ccy]


def product_cost(p: dict, settings: dict, rates: dict) -> dict:
    """Себестоимость одной штуки по статьям, в сумах."""
    where = f"товар '{p.get('id', '?')}'"
    vat = _num(settings, "vat_pct", "settings")
    vat_payer = bool(settings.get("vat_payer", False))
    fee_pct = _num(settings, "customs_fee_pct", "settings", required=False, default=0.0)

    supplier_price = _num(p, "supplier_price", where)
    if supplier_price <= 0:
        raise NumbersError(f"{where}: supplier_price должен быть больше нуля")
    sup_ccy = str(p.get("supplier_currency") or "").upper()
    sup_rate = _rate(rates, sup_ccy, where)
    purchase = supplier_price * sup_rate

    bank_pct = _num(p, "bank_fee_pct", where, required=False, default=0.0)
    delivery_raw = _num(p, "delivery_per_unit", where)
    del_ccy = str(p.get("delivery_currency") or "UZS").upper()
    delivery = delivery_raw * _rate(rates, del_ccy, where)
    duty_pct = _num(p, "duty_pct", where)

    customs_value = purchase + delivery
    duty = customs_value * duty_pct / 100
    customs_fee = customs_value * fee_pct / 100
    import_vat = (customs_value + duty) * vat / 100

    lines = [
        {"label": f"Закупка: {_fmt(supplier_price)} {sup_ccy}" + (f" × {_fmt(sup_rate, 2)}" if sup_ccy != "UZS" else ""),
         "uzs": purchase},
    ]
    if bank_pct:
        lines.append({"label": f"Комиссия банка {_pct(bank_pct)} от закупки", "uzs": purchase * bank_pct / 100})
    lines.append({"label": "Доставка до склада" + (f": {_fmt(delivery_raw)} {del_ccy}" if del_ccy != "UZS" else ""),
                  "uzs": delivery})
    lines.append({"label": f"Пошлина {_pct(duty_pct)} от там. стоимости {_fmt(customs_value)}", "uzs": duty})
    if fee_pct:
        lines.append({"label": f"Таможенный сбор {_pct(fee_pct)}", "uzs": customs_fee})
    if not vat_payer:
        lines.append({"label": f"НДС при ввозе {_pct(vat)} (не возмещается)", "uzs": import_vat})
    for i, extra in enumerate(p.get("extra_per_unit") or []):
        cost = _num(extra, "cost", f"{where}, extra_per_unit[{i}]")
        ccy = str(extra.get("currency") or "UZS").upper()
        label = extra.get("label") or "Прочее"
        if ccy != "UZS":
            label += f": {_fmt(cost)} {ccy}"
        lines.append({"label": label, "uzs": cost * _rate(rates, ccy, where)})

    return {
        "lines": lines,
        "total": sum(line["uzs"] for line in lines),
        "customs_value": customs_value,
        "import_vat": import_vat,
        "import_vat_recoverable": vat_payer,
    }


def _net_share(settings: dict) -> float:
    """Какая доля цены остаётся после налога с продажи."""
    vat = float(settings["vat_pct"])
    if settings.get("vat_payer", False):
        return 1 / (1 + vat / 100)
    return 1 - float(settings.get("sales_tax_pct") or 0) / 100


def price_for_margin(cost: float, settings: dict, margin_pct: float) -> float | None:
    """Минимальная цена, при которой маржа = margin_pct (0 — цена без убытка)."""
    denominator = _net_share(settings) - margin_pct / 100
    return cost / denominator if denominator > 0 else None


def analyse_product(p: dict, settings: dict, rates: dict, price: float | None = None) -> dict:
    c = product_cost(p, settings, rates)
    where = f"товар '{p.get('id', '?')}'"
    price = price if price is not None else _num(p, "sale_price_uzs", where, required=False)
    target = _num(p, "target_margin_pct", where, required=False,
                  default=settings.get("target_margin_pct"))
    sales = _num(p, "sales_per_month", where, required=False)

    a = {
        "id": p.get("id"), "name": p.get("name") or p.get("id"),
        "cost": c, "price": price, "target_margin_pct": target, "sales_per_month": sales,
        "breakeven_price": price_for_margin(c["total"], settings, 0),
        "target_price": price_for_margin(c["total"], settings, target) if target is not None else None,
        "net": None, "tax": None, "profit": None, "margin_pct": None, "markup_pct": None,
        "month_revenue": None, "month_profit": None, "flags": [],
    }
    if price:
        net = price * _net_share(settings)
        a["net"], a["tax"] = net, price - net
        a["profit"] = net - c["total"]
        a["margin_pct"] = a["profit"] / price * 100
        a["markup_pct"] = (price / c["total"] - 1) * 100
        if sales:
            a["month_revenue"] = price * sales
            a["month_profit"] = a["profit"] * sales
        if a["profit"] < 0:
            a["flags"].append(f"{a['name']}: продаётся в убыток ({_uzs(a['profit'])} с каждой штуки).")
        elif target is not None and a["margin_pct"] < target:
            a["flags"].append(f"{a['name']}: маржа {a['margin_pct']:.0f}% ниже цели {target:.0f}%.")
    return a


def what_ifs(p: dict, settings: dict, rates: dict) -> list[tuple[str, dict]]:
    """Сценарии, которые должна выдержать цена."""
    rows = [("Как сейчас", analyse_product(p, settings, rates))]
    dearer = {k: (v if k == "UZS" else v * 1.10) for k, v in rates.items()}
    rows.append(("Курс валют +10%", analyse_product(p, settings, dearer)))
    p_del = dict(p, delivery_per_unit=float(p["delivery_per_unit"]) * 1.20)
    rows.append(("Доставка +20%", analyse_product(p_del, settings, rates)))
    p_duty = dict(p, duty_pct=float(p["duty_pct"]) + 5)
    rows.append(("Пошлина +5 п.п.", analyse_product(p_duty, settings, rates)))
    if rows[0][1]["price"]:
        rows.append(("Цена −10%", analyse_product(p, settings, rates, price=rows[0][1]["price"] * 0.9)))
    return rows


def analyse(spec: dict, rates: dict) -> dict:
    settings = spec.get("settings") or {}
    if "vat_pct" not in settings:
        raise NumbersError("settings: нет поля 'vat_pct'")
    if not settings.get("vat_payer", False) and settings.get("sales_tax_pct") is None:
        raise NumbersError("settings: нет поля 'sales_tax_pct' (налог с оборота; если вы плательщик НДС — vat_payer: true)")
    products = [analyse_product(p, settings, rates) for p in spec.get("products") or []]
    if not products:
        raise NumbersError("в файле нет товаров (products)")

    fixed_items = spec.get("fixed_monthly_uzs") or []
    fixed = sum(_num(it, "cost", f"fixed_monthly_uzs[{i}]") for i, it in enumerate(fixed_items))
    planned = [a for a in products if a["month_profit"] is not None]
    month_profit = sum(a["month_profit"] for a in planned)
    business = {
        "fixed": fixed, "fixed_items": fixed_items, "products_in_plan": len(planned),
        "month_revenue": sum(a["month_revenue"] for a in planned),
        "month_profit_before_fixed": month_profit,
        "operating_profit": month_profit - fixed,
        "breakeven_share": (fixed / month_profit) if month_profit > 0 else None,
    }
    flags = [f for a in products for f in a["flags"]]
    if fixed and planned and business["operating_profit"] < 0:
        flags.append(f"По плану продаж бизнес в минусе: {_uzs(business['operating_profit'])} в месяц.")
    return {"business_name": spec.get("business", ""), "settings": settings,
            "products": products, "business": business, "flags": flags}


# ── Отчёт ────────────────────────────────────────────────────────────────────

def _fmt(value: float, digits: int = 0) -> str:
    text = f"{value:,.{digits}f}".replace(",", " ")
    return text.replace(".", ",") if digits else text


def _uzs(value: float) -> str:
    return ("−" if value < 0 else "") + _fmt(abs(value)) + " сум"


def _pct(value: float) -> str:
    return (f"{value:.1f}".rstrip("0").rstrip(".") + "%").replace(".", ",")


def _margin(value: float) -> str:
    return ("−" if round(value) < 0 else "") + f"{abs(value):.0f}%"


def report(spec: dict, a: dict, rates: dict, rates_label: str) -> str:
    s = a["settings"]
    used = sorted({str(p.get("supplier_currency") or "").upper() for p in spec["products"]} - {"UZS", ""})
    L = [f"# Себестоимость: {a['business_name'] or 'Algiz'}", ""]
    if used:
        L.append(f"{rates_label}: " + ", ".join(f"1 {c} = {_fmt(rates[c], 2)} сум" for c in used if c in rates) + ".")
    if s.get("vat_payer"):
        L.append(f"Налоги: плательщик НДС — НДС при ввозе {_pct(s['vat_pct'])} возмещается и в себестоимость не входит; "
                 f"цена продажи — с НДС.")
    else:
        L.append(f"Налоги: не плательщик НДС — НДС при ввозе {_pct(s['vat_pct'])} входит в себестоимость; "
                 f"налог с оборота {_pct(s['sales_tax_pct'])} от цены продажи.")
    L += ["Каждая цифра — из sebestoimost.json и курса ЦБ. Ставки налогов и пошлин сверьте с брокером и бухгалтером.", ""]

    for p, pa in zip(spec["products"], a["products"]):
        L += [f"## {pa['name']}", "", "| статья | на 1 шт |", "| --- | ---: |"]
        for line in pa["cost"]["lines"]:
            L.append(f"| {line['label']} | {_uzs(line['uzs'])} |")
        L += [f"| **Себестоимость** | **{_uzs(pa['cost']['total'])}** |", ""]

        if pa["price"]:
            tax_name = "НДС в цене" if s.get("vat_payer") else f"налог с оборота {_pct(s['sales_tax_pct'])}"
            L.append(f"- Цена продажи {_uzs(pa['price'])}, {tax_name} {_uzs(pa['tax'])}, остаётся {_uzs(pa['net'])}.")
            L.append(f"- **Прибыль с 1 шт: {_uzs(pa['profit'])}** — маржа {_margin(pa['margin_pct'])}, "
                     f"наценка на себестоимость {_margin(pa['markup_pct'])}.")
        else:
            L.append("- Цена продажи не указана (sale_price_uzs).")
        if pa["breakeven_price"]:
            L.append(f"- Цена без убытка: {_uzs(pa['breakeven_price'])}.")
        if pa["target_margin_pct"] is not None:
            tp = pa["target_price"]
            L.append(f"- Цена для маржи {pa['target_margin_pct']:.0f}%: "
                     + (_uzs(tp) if tp else "недостижима при этих налогах") + ".")
        if pa["month_profit"] is not None:
            L.append(f"- В месяц ({_fmt(pa['sales_per_month'], 1).rstrip('0').rstrip(',')} шт): "
                     f"выручка {_uzs(pa['month_revenue'])}, прибыль до постоянных расходов {_uzs(pa['month_profit'])}.")
        L += ["", "| что если | себестоимость | прибыль с 1 шт | маржа |", "| --- | ---: | ---: | ---: |"]
        for name, w in what_ifs(p, s, rates):
            profit = _uzs(w["profit"]) if w["profit"] is not None else "—"
            margin = _margin(w["margin_pct"]) if w["margin_pct"] is not None else "—"
            L.append(f"| {name} | {_uzs(w['cost']['total'])} | {profit} | {margin} |")
        L.append("")

    b = a["business"]
    if b["fixed"] or b["products_in_plan"]:
        L += ["## Весь бизнес в месяц", ""]
        if b["fixed_items"]:
            items = ", ".join(f"{it.get('label', 'расход')} {_uzs(float(it['cost']))}" for it in b["fixed_items"])
            L.append(f"- Постоянные расходы: {_uzs(b['fixed'])} ({items}).")
        if b["products_in_plan"]:
            L.append(f"- По плану продаж ({b['products_in_plan']} товаров): выручка {_uzs(b['month_revenue'])}, "
                     f"прибыль до постоянных расходов {_uzs(b['month_profit_before_fixed'])}.")
            L.append(f"- **Операционная прибыль: {_uzs(b['operating_profit'])} в месяц.**")
            if b["breakeven_share"] is not None and b["fixed"]:
                L.append(f"- Точка безубыточности: {b['breakeven_share'] * 100:.0f}% от плана продаж.")
        else:
            L.append("- План продаж не указан (sales_per_month) — прибыль бизнеса не посчитать.")
        L.append("")

    if a["flags"]:
        L += ["## Красные флаги", ""] + [f"- {f}" for f in a["flags"]]
    else:
        L.append("Красных флагов нет. Цифры хороши настолько, насколько точны входные данные: сверьте их с инвойсами.")
    return "\n".join(L) + "\n"


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Составитель себестоимости импортных товаров")
    ap.add_argument("file", nargs="?", default=str(DEFAULT_PATH), help="sebestoimost.json")
    ap.add_argument("--out", help="записать отчёт в этот .md файл")
    ap.add_argument("--json", action="store_true", help="вывести цифры в JSON")
    ap.add_argument("--product", help="только этот товар (id)")
    ap.add_argument("--price", type=float, help="что если цена такая (нужен --product)")
    ap.add_argument("--rate", action="append", default=[], metavar="CCY=RATE",
                    help="что если курс такой, например RUB=160 (можно несколько)")
    args = ap.parse_args(argv)

    try:
        try:
            with open(args.file, encoding="utf-8") as f:
                spec = json.load(f)
        except FileNotFoundError:
            raise NumbersError(f"нет файла {args.file}")
        except json.JSONDecodeError as e:
            raise NumbersError(f"{args.file}: ошибка JSON: {e}")

        if args.product:
            spec["products"] = [p for p in spec.get("products") or [] if p.get("id") == args.product]
            if not spec["products"]:
                raise NumbersError(f"нет товара с id '{args.product}'")
        if args.price is not None:
            if not args.product:
                raise NumbersError("--price работает только вместе с --product")
            spec["products"][0]["sale_price_uzs"] = args.price

        rates, label = get_rates((spec.get("settings") or {}).get("manual_rates"))
        for item in args.rate:
            ccy, _, value = item.partition("=")
            try:
                rates[ccy.strip().upper()] = float(value)
            except ValueError:
                raise NumbersError(f"--rate {item}: нужно ВАЛЮТА=ЧИСЛО")
            label += f", {ccy.strip().upper()} = {value} (что если)"

        a = analyse(spec, rates)
    except NumbersError as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"rates": rates, **a}, ensure_ascii=False, indent=1))
        return 0
    text = report(spec, a, rates, label)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"записано: {args.out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
