"""Tests for price_agent: no network, no API key. Run: python -m unittest discover -s tests"""

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import price_agent as pa  # noqa: E402

RATES = {"UZS": 1.0, "USD": 12000.0, "RUB": 150.0}
SETTINGS = {"own_domains": ["algiz.uz", "aloqa.uz"], "landed_cost_pct": None, "target_offers": 8}


def offer(**kw):
    base = {"seller": "Shop", "title": "Маяк", "price": 1_000_000, "currency": "UZS",
            "url": "https://shop.uz/p/1", "match": "exact", "in_stock": "yes", "note": ""}
    base.update(kw)
    return base


def usage(inp=1000, out=200, searches=0):
    return SimpleNamespace(input_tokens=inp, output_tokens=out, cache_creation_input_tokens=0,
                           cache_read_input_tokens=0,
                           server_tool_use=SimpleNamespace(web_search_requests=searches))


def response(stop_reason, content, u=None):
    return SimpleNamespace(stop_reason=stop_reason, content=content, usage=u or usage(), model=pa.MODEL)


def text(t):
    return SimpleNamespace(type="text", text=t)


def submit(data, id_="toolu_1"):
    return SimpleNamespace(type="tool_use", name="submit_offers", input=data, id=id_)


class FakeClient:
    """Mimics client.beta.messages.create and records every request."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        # snapshot messages: the agent keeps appending to the same list
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self._responses.pop(0)


ITEM = {"id": "tsefey", "name": "Проблесковый маяк «Цефей»", "query": "маяк Цефей",
        "analogs": "маячок LED", "our_price_uzs": 1_500_000,
        "supplier_price": 8250, "supplier_currency": "RUB"}


class CleanOffersTest(unittest.TestCase):
    def test_filters_converts_and_dedupes(self):
        raw = [
            offer(url="https://shop.uz/a", price=100, currency="USD"),
            offer(url="https://shop.uz/a", price=100, currency="USD"),       # duplicate
            offer(url="https://www.algiz.uz/item", price=5),                   # our own site
            offer(url="shop.uz/no-scheme"),                                    # not a link
            offer(url="https://shop.uz/zero", price=0),                        # no price
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
        self.assertEqual(s["cost_uzs"], 8250 * 150)
        self.assertIsNone(s["landed_uzs"])
        self.assertAlmostEqual(s["margin_pct"], (1_500_000 - 1_237_500) / 1_500_000 * 100)
        self.assertAlmostEqual(s["market_vs_cost"], 1_200_000 / 1_237_500)
        self.assertEqual(s["cheapest"][0]["url"], "https://d.uz/1")  # cheapest list includes analogs

    def test_analogs_only_and_landed_cost(self):
        offers = pa.clean_offers([offer(price=2_000_000, match="analog")], RATES, [])
        s = pa.summarize(ITEM, offers, RATES, {**SETTINGS, "landed_cost_pct": 20})
        self.assertEqual(s["basis"], "analog")
        self.assertAlmostEqual(s["landed_uzs"], 1_237_500 * 1.2)
        self.assertAlmostEqual(s["margin_pct"], (1_500_000 - 1_485_000) / 1_500_000 * 100)

    def test_no_offers_and_no_our_price(self):
        item = {**ITEM, "our_price_uzs": None, "supplier_price": None}
        s = pa.summarize(item, [], RATES, SETTINGS)
        self.assertEqual(s["count"], 0)
        self.assertIsNone(s["median"])
        self.assertIsNone(s["margin_pct"])
        pa.apply_change(s, 1_000_000)
        self.assertIsNone(s["change_pct"])

    def test_change_since_last_run(self):
        s = pa.summarize(ITEM, pa.clean_offers([offer(price=1_100_000)], RATES, []), RATES, SETTINGS)
        pa.apply_change(s, 1_000_000)
        self.assertAlmostEqual(s["change_pct"], 10.0)


class ResearchItemTest(unittest.TestCase):
    def test_pause_then_nudge_then_submit(self):
        data = {"offers": [offer()], "summary": "Рынок узкий.", "sources_checked": ["olx.uz"]}
        client = FakeClient([
            response("pause_turn", [text("searching")], usage(searches=3)),
            response("end_turn", [text("Found two shops.")]),
            response("tool_use", [submit(data)], usage(searches=1)),
        ])
        res = pa.research_item(client, ITEM, SETTINGS)
        self.assertIsNone(res["error"])
        self.assertEqual(res["offers"], data["offers"])
        self.assertEqual(res["summary"], "Рынок узкий.")
        self.assertEqual(res["usage"]["searches"], 4)
        self.assertEqual(res["usage"]["input"], 3000)
        self.assertEqual(len(client.calls), 3)
        # pause_turn: resend without a new user message; end_turn: a nudge is added
        self.assertEqual(client.calls[1]["messages"][-1]["role"], "assistant")
        self.assertEqual(client.calls[2]["messages"][-1], {"role": "user", "content": pa.NUDGE})

    def test_request_shape(self):
        client = FakeClient([response("tool_use", [submit({"offers": [], "summary": "", "sources_checked": []})])])
        pa.research_item(client, ITEM, SETTINGS)
        call = client.calls[0]
        self.assertEqual(call["model"], pa.MODEL)
        self.assertEqual(call["output_config"], {"effort": pa.EFFORT})
        names = [t["name"] for t in call["tools"]]
        self.assertEqual(names, ["web_search", "web_fetch", "submit_offers"])
        self.assertEqual(call["tools"][0]["blocked_domains"], ["algiz.uz", "aloqa.uz"])
        self.assertTrue(call["tools"][2]["strict"])
        self.assertNotIn("tool_choice", call)  # forced tool choice is rejected on this model
        if pa.MODEL in pa.FALLBACK_MODELS:
            self.assertEqual(call["fallbacks"], "default")
            self.assertEqual(call["betas"], ["server-side-fallback-2026-07-01"])
        self.assertIn("Цефей", call["messages"][0]["content"])

    def test_truncated_submit_is_not_trusted(self):
        client = FakeClient([response("max_tokens", [submit({"offers": [offer()]})])])
        res = pa.research_item(client, ITEM, SETTINGS)
        self.assertEqual(res["offers"], [])
        self.assertIn("обрезан", res["error"])

    def test_refusal(self):
        client = FakeClient([response("refusal", [])])
        res = pa.research_item(client, ITEM, SETTINGS)
        self.assertIn("отказалась", res["error"])

    def test_gives_up_after_nudges(self):
        client = FakeClient([response("end_turn", [text("done")]) for _ in range(pa.MAX_NUDGES + 1)])
        res = pa.research_item(client, ITEM, SETTINGS)
        self.assertEqual(res["error"], "исследователь не сдал результат")
        self.assertEqual(len(client.calls), pa.MAX_NUDGES + 1)


class ReportTest(unittest.TestCase):
    def test_report_escapes_and_splits(self):
        offers = pa.clean_offers([offer(seller="<Shop & Co>", url="https://a.uz/?a=1&b=2")], RATES, [])
        s = pa.summarize(ITEM, offers, RATES, SETTINGS)
        res = {"error": None, "summary": "Цены <выросли>", "sources_checked": []}
        rows = [(ITEM, res, s)] * 40
        total = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "searches": 0}
        msgs = pa.build_report(rows, "курс ЦБ РУз на 12.11.2026", total, datetime(2026, 11, 12), SETTINGS)
        self.assertGreater(len(msgs), 1)
        self.assertTrue(all(len(m) <= pa.TELEGRAM_LIMIT for m in msgs))
        joined = "\n".join(msgs)
        self.assertIn("&lt;Shop &amp; Co&gt;", joined)
        self.assertIn('href="https://a.uz/?a=1&amp;b=2"', joined)
        self.assertIn("Цены &lt;выросли&gt;", joined)
        self.assertIn("Цены конкурентов — 12.11.2026", msgs[0])

    def test_market_below_cost_wording(self):
        offers = pa.clean_offers([offer(price=1_000_000)], RATES, [])
        s = pa.summarize(ITEM, offers, RATES, SETTINGS)  # cost 1 237 500 > median 1 000 000
        block = pa.format_item(1, ITEM, {"error": None, "summary": ""}, s, SETTINGS)
        self.assertIn("Медиана рынка ниже закупки на 19%", block)
        self.assertNotIn("выше закупки", block)

    def test_error_item(self):
        s = pa.summarize(ITEM, [], RATES, SETTINGS)
        block = pa.format_item(1, ITEM, {"error": "ошибка API 529: overloaded"}, s, SETTINGS)
        self.assertIn("Не удалось проверить", block)

    def test_send_refuses_channel(self):
        with mock.patch.dict("os.environ", {"ADMIN_CHAT_ID": "@algiz_uz", "CHANNEL_ID": "@algiz_uz"}):
            self.assertFalse(pa.send_report(["x"]))
        with mock.patch.dict("os.environ", {"ADMIN_CHAT_ID": ""}):
            self.assertFalse(pa.send_report(["x"]))


class RunTest(unittest.TestCase):
    def test_end_to_end_with_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            watch = {"settings": SETTINGS, "items": [ITEM, {**ITEM, "id": "other", "name": "Другой"}]}
            (tmp / "watch.json").write_text(json.dumps(watch, ensure_ascii=False), encoding="utf-8")

            def run_once(price):
                data = {"offers": [offer(price=price)], "summary": "ok", "sources_checked": []}
                client = FakeClient([response("tool_use", [submit(data)])])
                return pa.run(item_ids=["tsefey"], send=False, client=client,
                              watchlist_path=tmp / "watch.json", report_dir=tmp / "reports")

            with mock.patch.object(db, "DB_PATH", tmp / "test.db"), \
                    mock.patch.object(pa, "get_rates", return_value=(RATES, "курс тест")):
                first = run_once(1_000_000)
                second = run_once(1_100_000)

            self.assertNotIn("С прошлой проверки", first[0])
            self.assertIn("С прошлой проверки: медиана +10%", second[0])
            self.assertNotIn("Другой", second[0])
            saved = json.loads(sorted((tmp / "reports").glob("*.json"))[-1].read_text(encoding="utf-8"))
            self.assertEqual(saved["items"][0]["offers"][0]["price_uzs"], 1_100_000)


if __name__ == "__main__":
    unittest.main()
