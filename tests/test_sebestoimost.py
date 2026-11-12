"""Tests for sebestoimost.py: no network. Run: python -m unittest discover -s tests"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sebestoimost as sb  # noqa: E402

RATES = {"UZS": 1.0, "RUB": 150.0, "USD": 12000.0}
SETTINGS = {"vat_pct": 12, "vat_payer": False, "sales_tax_pct": 4, "customs_fee_pct": 0.2,
            "target_margin_pct": 30}
PRODUCT = {"id": "tsefey", "name": "Маяк", "supplier_price": 8000, "supplier_currency": "RUB",
           "bank_fee_pct": 1, "delivery_per_unit": 5, "delivery_currency": "USD", "duty_pct": 10,
           "extra_per_unit": [{"label": "Сертификация", "cost": 20000}],
           "sale_price_uzs": 2_000_000, "sales_per_month": 10}


class CostTest(unittest.TestCase):
    def test_landed_cost_lines(self):
        c = sb.product_cost(PRODUCT, SETTINGS, RATES)
        purchase = 8000 * 150                     # 1 200 000
        delivery = 5 * 12000                      # 60 000
        customs_value = purchase + delivery       # 1 260 000
        duty = customs_value * 0.10               # 126 000
        fee = customs_value * 0.002               # 2 520
        vat = (customs_value + duty) * 0.12       # 166 320
        expected = purchase + purchase * 0.01 + delivery + duty + fee + vat + 20000
        self.assertAlmostEqual(c["total"], expected)
        self.assertAlmostEqual(c["customs_value"], customs_value)
        self.assertEqual(len(c["lines"]), 7)

    def test_vat_payer_excludes_import_vat_and_nets_price(self):
        settings = {**SETTINGS, "vat_payer": True}
        c_payer = sb.product_cost(PRODUCT, settings, RATES)
        c_not = sb.product_cost(PRODUCT, SETTINGS, RATES)
        self.assertAlmostEqual(c_not["total"] - c_payer["total"], c_not["import_vat"])
        a = sb.analyse_product(PRODUCT, settings, RATES)
        self.assertAlmostEqual(a["net"], 2_000_000 / 1.12)

    def test_profit_margin_and_prices(self):
        a = sb.analyse_product(PRODUCT, SETTINGS, RATES)
        cost = a["cost"]["total"]
        self.assertAlmostEqual(a["net"], 2_000_000 * 0.96)
        self.assertAlmostEqual(a["profit"], 2_000_000 * 0.96 - cost)
        self.assertAlmostEqual(a["margin_pct"], a["profit"] / 2_000_000 * 100)
        self.assertAlmostEqual(a["markup_pct"], (2_000_000 / cost - 1) * 100)
        self.assertAlmostEqual(a["breakeven_price"], cost / 0.96)
        self.assertAlmostEqual(a["target_price"], cost / (0.96 - 0.30))
        self.assertAlmostEqual(a["month_profit"], a["profit"] * 10)
        # at the target price the margin is exactly the target
        at_target = sb.analyse_product(PRODUCT, SETTINGS, RATES, price=a["target_price"])
        self.assertAlmostEqual(at_target["margin_pct"], 30)

    def test_flags(self):
        loss = sb.analyse_product(PRODUCT, SETTINGS, RATES, price=1_000_000)
        self.assertTrue(any("в убыток" in f for f in loss["flags"]))
        low = sb.analyse_product(PRODUCT, SETTINGS, RATES, price=2_100_000)
        self.assertTrue(any("ниже цели 30%" in f for f in low["flags"]))

    def test_what_ifs_move_the_right_way(self):
        rows = dict(sb.what_ifs(PRODUCT, SETTINGS, RATES))
        base = rows["Как сейчас"]
        for name in ("Курс валют +10%", "Доставка +20%", "Пошлина +5 п.п."):
            self.assertGreater(rows[name]["cost"]["total"], base["cost"]["total"], name)
        self.assertLess(rows["Цена −10%"]["profit"], base["profit"])

    def test_missing_field_is_an_error_not_a_guess(self):
        for field in ("supplier_price", "delivery_per_unit", "duty_pct"):
            p = {k: v for k, v in PRODUCT.items() if k != field}
            with self.assertRaises(sb.NumbersError):
                sb.product_cost(p, SETTINGS, RATES)
        with self.assertRaises(sb.NumbersError):
            sb.product_cost({**PRODUCT, "supplier_currency": "KZT"}, SETTINGS, RATES)
        with self.assertRaises(sb.NumbersError):
            sb.analyse({"settings": {"vat_pct": 12}, "products": [PRODUCT]}, RATES)  # no sales_tax_pct

    def test_business_breakeven(self):
        spec = {"settings": SETTINGS, "products": [PRODUCT],
                "fixed_monthly_uzs": [{"label": "Аренда", "cost": 1_000_000}]}
        a = sb.analyse(spec, RATES)
        b = a["business"]
        month = a["products"][0]["month_profit"]
        self.assertAlmostEqual(b["operating_profit"], month - 1_000_000)
        self.assertAlmostEqual(b["breakeven_share"], 1_000_000 / month)


class CliTest(unittest.TestCase):
    def test_report_and_what_if_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            path.write_text(json.dumps({"business": "Algiz", "settings": SETTINGS, "products": [PRODUCT]},
                                       ensure_ascii=False), encoding="utf-8")
            out = Path(tmp) / "r" / "report.md"
            with mock.patch.object(sb, "get_rates", return_value=(dict(RATES), "курс тест")), \
                    mock.patch("builtins.print"):
                code = sb.main([str(path), "--out", str(out), "--product", "tsefey",
                                "--price", "2500000", "--rate", "RUB=160"])
            self.assertEqual(code, 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("Закупка: 8 000 RUB × 160,00 | 1 280 000 сум", text)
            self.assertIn("Цена продажи 2 500 000 сум", text)
            self.assertIn("| Цена −10% |", text)

    def test_error_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("sys.stderr"):
            self.assertEqual(sb.main([str(Path(tmp) / "missing.json")]), 2)

    def test_repo_example_is_valid(self):
        spec = json.loads((Path(__file__).resolve().parent.parent / "sebestoimost.json").read_text(encoding="utf-8"))
        a = sb.analyse(spec, RATES)
        self.assertTrue(all(p["cost"]["total"] > 0 for p in a["products"]))


if __name__ == "__main__":
    unittest.main()
