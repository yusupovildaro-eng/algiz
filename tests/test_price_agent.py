"""Tests for price_agent: no network, no Claude. Run: python -m unittest discover -s tests"""

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import price_agent as pa  # noqa: E402

RATES = {"UZS": 1.0, "USD": 12000.0, "RUB": 150.0}
SETTINGS = {"own_domains": ["algiz.uz", "aloqa.uz"], "landed_cost_pct": None, "target_offers": 8,
            "company": "Algiz", "sells": "маяки"}
ITEM = {"id": "tsefey", "name": "Проблесковый маяк «Цефей»", "query": "маяк Цефей",
        "analogs": "маячок LED", "our_price_uzs": 1_500_000,
        "supplier_price": 8250, "supplier_currency": "RUB"}


def offer(**kw):
    base = {"seller": "Shop", "title": "Маяк", "price": 1_000_000, "currency": "UZS",
            "url": "https://shop.uz/p/1", "match": "exact", "in_stock": "yes", "note": ""}
    base.update(kw)
    return base


class CleanOffersTest(unittest.TestCase):
    def test_filters_converts_and_dedupes(self):
        raw = [
            offer(url="https://shop.uz/a", price=100, currency="USD"),
            offer(url="https://shop.uz/a", price=100, currency="USD"),       # duplicate
            offer(url="https://www.algiz.uz/item", price=5),                   # our own site
            offer(url="shop.uz/no-scheme"),                                    # not a link
            offer(url="https://shop.uz/zero", price=0),                        # no price
            offer(url="https://shop.uz/text", price="договорная"),             # not a number
            offer(url="https://shop.uz/cny", price=10, currency="CNY"),        # no CNY rate
        ]
        out = pa.clean_offers(raw, RATES, SETTINGS["own_domains"])
        self.assertEqual([o["url"] for o in out], ["https://shop.uz/a", "https://shop.uz/cny"])
        self.assertEqual(out[0]["price_uzs"], 1_200_000)
        self.assertIsNone(out[1]["price_uzs"])


class SummarizeTest(unittest.TestCase):
    def test_exact_matches_are_the_basis(self):
        offers = pa.clean_offers([
            offer(url="https://a.uz/1", price=1_000_000),
            offer(url="https://b.uz/1", price=1_200_000),
            offer(url="https://c.uz/1", price=1_400_000),
            offer(url="https://d.uz/1", price=300_000, match="analog"),
        ], RATES, [])
        s = pa.summarize(ITEM, offers, RATES, SETTINGS)
        self.assertEqual((s["count"], s["basis"]), (3, "exact"))
        self.assertEqual((s["min"], s["median"], s["max"]), (1_000_000, 1_200_000, 1_400_000))
        self.assertAlmostEqual(s["our_vs_median_pct"], 25.0)
        self.assertEqual((s["cost_uzs"], s["cost_source"]), (8250 * 150, "purchase"))
        self.assertAlmostEqual(s["margin_pct"], (1_500_000 - 1_237_500) / 1_500_000 * 100)
        self.assertAlmostEqual(s["market_vs_cost"], 1_200_000 / 1_237_500)
        self.assertEqual(s["cheapest"][0]["url"], "https://d.uz/1")  # cheapest list includes analogs

    def test_landed_pct(self):
        offers = pa.clean_offers([offer(price=2_000_000, match="analog")], RATES, [])
        s = pa.summarize(ITEM, offers, RATES, {**SETTINGS, "landed_cost_pct": 20})
        self.assertEqual((s["basis"], s["cost_source"]), ("analog", "pct"))
        self.assertAlmostEqual(s["landed_uzs"], 1_237_500 * 1.2)
        self.assertAlmostEqual(s["market_vs_cost"], 2_000_000 / 1_485_000)

    def test_cost_from_sebestoimost_wins(self):
        info = {"cost": 1_300_000, "margin_pct": 9.5, "sale_price": 1_500_000}
        s = pa.summarize({**ITEM, "our_price_uzs": None}, [], RATES, SETTINGS, info)
        self.assertEqual((s["landed_uzs"], s["cost_source"], s["margin_pct"]), (1_300_000, "sebestoimost", 9.5))
        self.assertEqual(s["our_price"], 1_500_000)  # price taken from sebestoimost.json
        self.assertIsNone(s["cost_uzs"])

    def test_no_offers_and_change(self):
        s = pa.summarize({**ITEM, "supplier_price": None}, [], RATES, SETTINGS)
        self.assertEqual(s["count"], 0)
        pa.apply_change(s, 1_000_000)
        self.assertIsNone(s["change_pct"])
        s = pa.summarize(ITEM, pa.clean_offers([offer(price=1_100_000)], RATES, []), RATES, SETTINGS)
        pa.apply_change(s, 1_000_000)
        self.assertAlmostEqual(s["change_pct"], 10.0)


class RunFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        watch = {"settings": SETTINGS, "items": [ITEM, {**ITEM, "id": "other", "name": "Другой"}]}
        self.watch = self.root / "watch.json"
        self.watch.write_text(json.dumps(watch, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_writes_briefs(self):
        with mock.patch("builtins.print"):
            run_dir = pa.init_run(["tsefey", "nope"], self.watch, self.root / "reports")
        run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(run["items"], ["tsefey"])
        brief = (run_dir / "briefs" / "tsefey.md").read_text(encoding="utf-8")
        self.assertIn("маяк Цефей", brief)
        self.assertIn(str(run_dir / "offers" / "tsefey.json"), brief)
        self.assertIn("algiz.uz, aloqa.uz", brief)
        self.assertIn('"offers": [', brief)  # JSON example survived str.format

    def test_read_offers(self):
        path = self.root / "x.json"
        self.assertEqual(pa.read_offers(path), (None, "исследователь не сдал результат"))
        path.write_text("{oops", encoding="utf-8")
        self.assertIn("ошибкой JSON", pa.read_offers(path)[1])
        path.write_text('{"summary": "x"}', encoding="utf-8")
        self.assertEqual(pa.read_offers(path)[1], "в файле нет списка offers")
        path.write_text('﻿{"offers": [{"price": 1}, "junk"], "summary": "ok"}', encoding="utf-8")
        data, err = pa.read_offers(path)  # BOM tolerated, non-dict offers dropped
        self.assertIsNone(err)
        self.assertEqual((data["offers"], data["summary"], data["sources_checked"]), ([{"price": 1}], "ok", []))

    def test_check_lists_missing(self):
        with mock.patch("builtins.print"):
            run_dir = pa.init_run(None, self.watch, self.root / "reports")
            (run_dir / "offers" / "tsefey.json").write_text('{"offers": []}', encoding="utf-8")
            problems = pa.check_run(run_dir)
        self.assertEqual([p[0] for p in problems], ["other"])

    def test_finish_end_to_end_with_history(self):
        def one_run(price):
            run_dir = pa.init_run(["tsefey"], self.watch, self.root / "reports")
            data = {"offers": [offer(price=price)], "summary": "ok", "sources_checked": []}
            (run_dir / "offers" / "tsefey.json").write_text(json.dumps(data), encoding="utf-8")
            msgs = pa.finish_run(run_dir, send=False, watchlist_path=self.watch,
                                 costs_path=self.root / "no-costs.json")
            return run_dir, msgs

        with mock.patch.object(db, "DB_PATH", self.root / "test.db"), \
                mock.patch.object(pa, "get_rates", return_value=(RATES, "курс тест")), \
                mock.patch("builtins.print"), \
                mock.patch.object(pa, "datetime") as fake_dt:
            fake_dt.now.side_effect = [datetime(2026, 11, 12, 9, 0, s) for s in range(10)]
            _, first = one_run(1_000_000)
            run_dir, second = one_run(1_100_000)
            again = pa.finish_run(run_dir, send=False, watchlist_path=self.watch,
                                  costs_path=self.root / "no-costs.json")

        self.assertNotIn("С прошлой проверки", first[0])
        self.assertIn("С прошлой проверки: медиана +10%", second[0])
        self.assertNotIn("С прошлой проверки", again[0])  # re-report does not compare with itself
        self.assertNotIn("Другой", second[0])
        self.assertTrue((run_dir / "report.txt").exists())
        saved = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["items"][0]["offers"][0]["price_uzs"], 1_100_000)
        with mock.patch.object(db, "DB_PATH", self.root / "test.db"):
            self.assertEqual(db.last_price_median("tsefey", "exact"), 1_100_000)

    def test_finish_uses_sebestoimost(self):
        costs = {"settings": {"vat_pct": 12, "vat_payer": False, "sales_tax_pct": 4},
                 "products": [{"id": "tsefey", "supplier_price": 8250, "supplier_currency": "RUB",
                               "delivery_per_unit": 0, "duty_pct": 0}]}
        costs_path = self.root / "costs.json"
        costs_path.write_text(json.dumps(costs), encoding="utf-8")
        with mock.patch.object(db, "DB_PATH", self.root / "test.db"), \
                mock.patch.object(pa, "get_rates", return_value=(RATES, "курс тест")), \
                mock.patch("builtins.print"):
            run_dir = pa.init_run(["tsefey"], self.watch, self.root / "reports")
            (run_dir / "offers" / "tsefey.json").write_text('{"offers": []}', encoding="utf-8")
            msgs = pa.finish_run(run_dir, send=False, watchlist_path=self.watch, costs_path=costs_path)
        # 8250 × 150 = 1 237 500; + 12% import VAT = 1 386 000; margin = (1 500 000 × 0.96 − 1 386 000) / 1 500 000
        self.assertIn("Себестоимость (sebestoimost.json): 1 386 000 сум", msgs[0])
        self.assertIn("Маржа при нашей цене: 4% (с налогами)", msgs[0])


class ReportTest(unittest.TestCase):
    def test_report_escapes_and_splits(self):
        offers = pa.clean_offers([offer(seller="<Shop & Co>", url="https://a.uz/?a=1&b=2")], RATES, [])
        s = pa.summarize(ITEM, offers, RATES, SETTINGS)
        res = {"error": None, "summary": "Цены <выросли>", "sources_checked": []}
        msgs = pa.build_report([(ITEM, res, s)] * 40, "курс ЦБ РУз на 12.11.2026",
                               datetime(2026, 11, 12), SETTINGS)
        self.assertGreater(len(msgs), 1)
        self.assertTrue(all(len(m) <= pa.TELEGRAM_LIMIT for m in msgs))
        joined = "\n".join(msgs)
        self.assertIn("&lt;Shop &amp; Co&gt;", joined)
        self.assertIn('href="https://a.uz/?a=1&amp;b=2"', joined)
        self.assertIn("Цены &lt;выросли&gt;", joined)
        self.assertIn("Цены конкурентов — 12.11.2026", msgs[0])

    def test_market_below_cost_and_error(self):
        s = pa.summarize(ITEM, pa.clean_offers([offer(price=1_000_000)], RATES, []), RATES, SETTINGS)
        block = pa.format_item(1, ITEM, {"error": None, "summary": ""}, s, SETTINGS)
        self.assertIn("Медиана рынка ниже закупки на 19%", block)
        block = pa.format_item(1, ITEM, {"error": "исследователь не сдал результат"}, s, SETTINGS)
        self.assertIn("Не удалось проверить", block)

    def test_send_refuses_channel(self):
        with mock.patch("builtins.print"):
            with mock.patch.dict("os.environ", {"ADMIN_CHAT_ID": "@algiz_uz", "CHANNEL_ID": "@algiz_uz"}):
                self.assertFalse(pa.send_report(["x"]))
            with mock.patch.dict("os.environ", {"ADMIN_CHAT_ID": ""}):
                self.assertFalse(pa.send_report(["x"]))


if __name__ == "__main__":
    unittest.main()
