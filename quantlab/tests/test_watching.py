import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.cross_section import rank_cross_section
from core.signal.factors.meta.registry import list_factors, run_factor_experiment
from core.watching.store import (
    init_from_example,
    read_watching,
    refresh_watchlist,
    validate_watching,
    watchlist_names_for,
    watchlist_origins_for,
    write_watching,
)
from tests.test_signal import _rising_bars


class TestWatching(unittest.TestCase):
    def test_validate_watching(self):
        data = validate_watching(
            {
                "sources": [{"type": "static", "codes": ["茅台"]}],
                "watchlist": [],
            }
        )
        self.assertEqual(data["max_size"], 300)

    def test_clamp_and_set_watching_max_size(self):
        from core.watching.store import (
            WATCHING_DEFAULT_SIZE,
            WATCHING_MAX_SIZE,
            clamp_watching_max_size,
            set_watching_max_size,
            write_watching,
        )

        self.assertEqual(WATCHING_DEFAULT_SIZE, 300)
        self.assertEqual(WATCHING_MAX_SIZE, 1000)
        self.assertEqual(clamp_watching_max_size(None), 300)
        self.assertEqual(clamp_watching_max_size(2), 200)
        self.assertEqual(clamp_watching_max_size(1001), 1000)
        self.assertEqual(clamp_watching_max_size(500), 500)
        codes = [f"{i:06d}" for i in range(1, 221)]
        extra = [f"{i:06d}" for i in range(900000, 900010)]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "watching.json")
            write_watching(
                {
                    "sources": [],
                    "watchlist": list(codes),
                    "max_size": 220,
                },
                path,
            )
            with patch(
                "core.watching.store._fill_codes_for_expand",
                return_value=list(extra),
            ):
                shrunk = set_watching_max_size(200, path=path)
                self.assertTrue(shrunk["ok"])
                self.assertEqual(shrunk["max_size"], 200)
                self.assertEqual(shrunk["count"], 200)
                self.assertEqual(shrunk["dropped"], 20)
                self.assertEqual(shrunk["added"], 0)
                uni = read_watching(path)
                self.assertEqual(uni["watchlist"], codes[:200])
                self.assertEqual(uni["watchlist_reserve"], codes[200:])

                restored = set_watching_max_size(220, path=path)
                self.assertEqual(restored["count"], 220)
                self.assertEqual(restored["added"], 20)
                self.assertEqual(restored["dropped"], 0)
                uni = read_watching(path)
                self.assertEqual(uni["watchlist"], codes)
                self.assertEqual(uni.get("watchlist_reserve") or [], [])

                expanded = set_watching_max_size(230, path=path)
                self.assertEqual(expanded["count"], 230)
                self.assertEqual(expanded["added"], 10)
                uni = read_watching(path)
                self.assertEqual(uni["watchlist"], codes + extra)
                self.assertFalse(expanded["over_cap"])

    def test_init_and_refresh_mocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "watching.json")
            init_from_example(path)
            uni = read_watching(path)
            self.assertEqual(uni.get("sources"), [])
            self.assertEqual(uni.get("watchlist"), [])

            fake_screen = {
                "success": True,
                "stocks": [
                    {"stock_code": "600036", "stock_name": "招商银行"},
                    {"stock_code": "601398", "stock_name": "工商银行"},
                ],
            }
            fake_quote = {
                "success": True,
                "stock_code": "600519",
                "stock_name": "贵州茅台",
            }

            with patch(
                "adapters.screen.engine.StockScreener"
            ) as mock_cls, patch(
                "adapters.market.quote_api.StockAPI.query",
                side_effect=lambda code: {
                    "success": True,
                    "stock_code": str(code) if str(code).isdigit() else "600519",
                    "stock_name": str(code),
                },
            ), patch(
                "adapters.screen.engine.fetch_a_spot",
                return_value=[],
            ):
                mock_cls.return_value.screen.return_value = fake_screen
                uni["sources"] = [
                    {"type": "static", "codes": ["茅台"]},
                    {"type": "screen", "sector": "银行", "limit": 5},
                ]
                result = refresh_watchlist(uni, path=path)

            self.assertTrue(result["success"])
            self.assertGreaterEqual(result["count"], 1)
            self.assertEqual(len(result["watchlist"]), len(result["watchlist_origins"]))
            self.assertEqual(len(result["watchlist"]), len(result["watchlist_names"]))
            self.assertTrue(any(o.startswith("S1") for o in result["watchlist_origins"]))
            self.assertTrue(any(s.get("label", "").startswith("S") for s in result["source_stats"]))
            saved = read_watching(path)
            self.assertTrue(saved["watchlist"])
            self.assertEqual(len(saved["watchlist"]), len(saved.get("watchlist_origins") or []))
            self.assertEqual(len(saved["watchlist"]), len(saved.get("watchlist_names") or []))

    def test_watchlist_origins_resolve_static_names(self):
        uni = {
            "sources": [{"type": "static", "codes": ["贵州茅台", "招商银行"]}],
            "watchlist": ["600519", "600036"],
        }
        with patch(
            "adapters.market.quote_api.StockAPI.query",
            side_effect=lambda code: {
                "success": True,
                "stock_code": "600519" if "茅台" in str(code) else "600036",
                "stock_name": str(code),
            },
        ):
            origins = watchlist_origins_for(uni)
        self.assertEqual(origins, ["S1 static", "S1 static"])

    def test_watchlist_names_from_static(self):
        uni = {
            "sources": [{"type": "static", "codes": ["贵州茅台", "招商银行"]}],
            "watchlist": ["600519", "600036"],
        }
        with patch(
            "adapters.market.quote_api.StockAPI.query",
            side_effect=lambda code: {
                "success": True,
                "stock_code": "600519" if ("茅台" in str(code) or str(code) == "600519") else "600036",
                "stock_name": "贵州茅台" if ("茅台" in str(code) or str(code) == "600519") else "招商银行",
            },
        ):
            names = watchlist_names_for(uni)
        self.assertEqual(names, ["贵州茅台", "招商银行"])

    def test_search_stocks_mapping(self):
        from adapters.market.stock_search import search_stocks

        with patch("adapters.screen.engine.fetch_a_spot", return_value=[]):
            out = search_stocks("茅台", limit=5)
        self.assertTrue(out["success"])
        codes = [x["stock_code"] for x in out["items"]]
        self.assertTrue(any(c == "600519" for c in codes))

    def test_search_china_aluminum_via_mapping(self):
        from adapters.market.stock_search import search_stocks

        out = search_stocks("中国铝业", limit=5)
        self.assertTrue(out["success"])
        codes = [x["stock_code"] for x in out["items"]]
        self.assertIn("601600", codes)

    def test_search_uses_code_name_index(self):
        from adapters.market import stock_search as ss

        with patch.object(ss, "_spot_pairs_cheap", return_value=[]), patch.object(
            ss,
            "_code_name_pairs",
            return_value=[("601600", "中国铝业"), ("600519", "贵州茅台")],
        ):
            # 清掉热门映射命中路径：搜一个映射里没有的别名片段仍能走代码表
            out = ss.search_stocks("贵州茅", limit=5)
        codes = [x["stock_code"] for x in out["items"]]
        self.assertIn("600519", codes)

    def test_search_former_names_military(self):
        """曾用名：哈飞股份→中直；中船股份→中船科技。"""
        from adapters.market.stock_search import search_stocks

        with patch("adapters.screen.engine.fetch_a_spot", return_value=[]):
            hafei = search_stocks("哈飞股份", limit=5)
            cssc = search_stocks("中船股份", limit=5)
        self.assertTrue(hafei["success"])
        self.assertEqual(hafei["items"][0]["stock_code"], "600038")
        self.assertIn("中直", hafei["items"][0]["stock_name"])
        self.assertTrue(cssc["success"])
        self.assertEqual(cssc["items"][0]["stock_code"], "600072")
        self.assertIn("中船科技", cssc["items"][0]["stock_name"])

    def test_search_strips_corp_suffix(self):
        from adapters.market import stock_search as ss

        self.assertEqual(ss._query_variants("哈飞股份"), ["哈飞股份", "哈飞"])
        self.assertEqual(ss._query_variants("中船股份"), ["中船股份", "中船"])

    def test_list_watchlist_quotes(self):
        from core.watching.store import list_watchlist_quotes

        def fake_batch(codes):
            out = {}
            for code in codes:
                c = str(code)
                if c == "600519":
                    out[c] = {
                        "success": True,
                        "stock_code": "600519",
                        "stock_name": "贵州茅台",
                        "price": "1800.00元",
                        "price_raw": 1800.0,
                        "change_raw": 1.25,
                        "change_amount": "+22.00元",
                        "open": "1780.00元",
                        "high": "1810.00元",
                        "low": "1775.00元",
                        "volume": "1.20万",
                        "market": "CN",
                    }
                else:
                    out[c] = {"success": False, "error": "nope"}
            return out

        with patch("core.ports.market.batch_query_quotes", side_effect=fake_batch):
            out = list_watchlist_quotes(codes=["600519", "000001"])
        self.assertTrue(out["ok"])
        self.assertEqual(out["count"], 2)
        self.assertTrue(out["items"][0]["ok"])
        self.assertEqual(out["items"][0]["change_percent"], 1.25)
        self.assertEqual(out["items"][0]["change_amount"], "+22.00元")
        self.assertEqual(out["items"][0]["open"], "1780.00元")
        self.assertEqual(out["items"][0]["high"], "1810.00元")
        self.assertEqual(out["items"][0]["market"], "CN")
        self.assertFalse(out["items"][1]["ok"])
        self.assertIsNone(out["items"][1]["high"])

    def test_add_watchlist_item(self):
        from core.watching.store import add_watchlist_item, write_watching

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "watching.json")
            write_watching(
                {
                    "sources": [],
                    "watchlist": ["600519"],
                    "watchlist_origins": ["手动"],
                    "watchlist_names": ["贵州茅台"],
                },
                path,
            )
            with patch(
                "adapters.market.quote_api.StockAPI.query",
                return_value={
                    "success": True,
                    "stock_code": "000568",
                    "stock_name": "泸州老窖",
                },
            ):
                out = add_watchlist_item("泸州老窖", path=path)
            self.assertTrue(out["added"])
            self.assertIn("000568", out["watchlist"])
            uni = read_watching(path)
            self.assertIn("000568", uni["watchlist"])
            self.assertEqual(uni.get("sources") or [], [])

    def test_remove_watchlist_item(self):
        from core.watching.store import remove_watchlist_item, write_watching

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "watching.json")
            write_watching(
                {
                    "sources": [],
                    "watchlist": ["600519", "000568"],
                    "watchlist_origins": ["手动", "手动"],
                    "watchlist_names": ["贵州茅台", "泸州老窖"],
                },
                path,
            )
            out = remove_watchlist_item("000568", path=path)
            self.assertTrue(out["removed"])
            self.assertNotIn("000568", out["watchlist"])
            uni = read_watching(path)
            self.assertEqual(uni["watchlist"], ["600519"])
            self.assertEqual(uni.get("sources") or [], [])

    def test_sync_paper_watchlist_subset(self):
        from core.watching.store import sync_paper_watchlist, write_watching

        def _fake_query(code):
            return {
                "success": True,
                "stock_code": code,
                "stock_name": f"名{code}",
                "price_raw": 10.0,
            }

        with tempfile.TemporaryDirectory() as tmp:
            uni_path = os.path.join(tmp, "watching.json")
            paper_path = os.path.join(tmp, "paper.json")
            write_watching(
                {
                    "sources": [],
                    "watchlist": ["600519", "000568", "601318"],
                    "watchlist_origins": ["手动", "手动", "手动"],
                    "watchlist_names": ["贵州茅台", "泸州老窖", "中国平安"],
                },
                uni_path,
            )
            with open(paper_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "name": "test",
                        "cash": 100000,
                        "initial_cash": 100000,
                        "watchlist": ["600519"],
                        "holdings": [],
                        "rules": {"max_positions": 20},
                        "cost_model": "zero",
                    },
                    f,
                )
            with patch("core.watching.store.read_watching", return_value=read_watching(uni_path)):
                with patch("core.paper.open_fill.require_open_fill", return_value=None):
                    with patch("core.data.facade.get_quote", side_effect=_fake_query):
                        out = sync_paper_watchlist(
                            paper_path,
                            codes=["000568", "601318", "999999"],
                            buy=True,
                            lot_shares=100,
                        )
            self.assertTrue(out["success"])
            self.assertTrue(out["selected"])
            # buy=True：只写入持仓，不再维护 paper.watchlist
            self.assertEqual(sorted(out["holdings_codes"]), ["000568", "601318"])
            self.assertEqual(out["skipped"], ["999999"])
            self.assertEqual(out["bought_count"], 2)
            with open(paper_path, encoding="utf-8") as f:
                paper = json.load(f)
            self.assertNotIn("watchlist", paper)
            held = {h["stock_code"] for h in paper["holdings"]}
            self.assertEqual(held, {"000568", "601318"})
            for h in paper["holdings"]:
                self.assertEqual(h["shares"], 100)
                self.assertEqual(h["origin"], "manual")
            self.assertAlmostEqual(paper["cash"], 100000 - 2 * 100 * 10.0, places=2)
            with patch("core.watching.store.read_watching", return_value=read_watching(uni_path)):
                with self.assertRaises(ValueError):
                    sync_paper_watchlist(paper_path, codes=[])


class TestP9Quant(unittest.TestCase):
    def test_cross_section_with_codes(self):
        fake_item = {
            "stock_code": "600519",
            "stock_name": "茅台",
            "score": 70,
            "hard_reject": False,
            "data_source": "mock",
        }
        with patch(
            "core.signal.cross_section.score_stock",
            return_value={
                "success": True,
                "stock_code": "600519",
                "signal_item": fake_item,
            },
        ):
            result = rank_cross_section(["茅台"], limit=5, min_score=50)
        self.assertTrue(result["success"])
        self.assertEqual(result["ranked_count"], 1)

    def test_factor_registry_list(self):
        factors = list_factors()
        names = {f["name"] for f in factors}
        self.assertGreaterEqual(len(names), 8)
        self.assertIn("momentum", names)
        self.assertIn("relative_strength", names)
        self.assertIn("reversal", names)
        self.assertIn("liquidity", names)
        self.assertIn("value", names)
        self.assertIn("quality", names)
        self.assertIn("growth", names)
        self.assertIn("size", names)

    def test_factor_experiment_offline(self):
        bars = _rising_bars()
        while len(bars) < 35:
            bars = bars + _rising_bars()
        report = run_factor_experiment(bars, horizon_days=3)
        self.assertTrue(report["success"])
        self.assertGreaterEqual(len(report["factors"]), 8)


class TestWatchingQuotesApi(unittest.TestCase):
    def test_placeholder_shape(self):
        from web.routers.watching import _quotes_placeholder

        out = _quotes_placeholder(["600519"], note="行情拉取超时，请稍后刷新")
        self.assertTrue(out["ok"])
        self.assertEqual(out["count"], 1)
        self.assertFalse(out["items"][0]["ok"])
        self.assertIn("超时", out["note"])

    def test_quotes_timeout_returns_200_placeholder(self):
        import time

        from fastapi.testclient import TestClient

        import web.app as web_app
        from web.routers import watching as wr

        def slow(_codes):
            time.sleep(0.25)
            return {"ok": True, "count": 0, "items": []}

        with patch.object(wr, "_QUOTES_WAIT_SEC", 0.05), patch.object(
            wr.deps.watching, "quotes", side_effect=slow
        ):
            client = TestClient(web_app.app)
            r = client.get("/api/watching/quotes?codes=600519")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body.get("ok"))
        self.assertIn("超时", body.get("note") or "")
        self.assertEqual(body["items"][0]["stock_code"], "600519")
        self.assertFalse(body["items"][0]["ok"])


class TestWatchingMaxSizeApi(unittest.TestCase):
    def test_post_max_size(self):
        from fastapi.testclient import TestClient

        import web.app as web_app

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "watching.json")
            write_watching(
                {"sources": [], "watchlist": ["600519"], "max_size": 500},
                path,
            )
            with patch("core.watching.store.WATCHING_PATH", path), patch(
                "core.paths.WATCHING_PATH", path
            ), patch(
                "core.watching.store._fill_codes_for_expand",
                return_value=["000001", "000002", "000003", "000004"],
            ):
                client = TestClient(web_app.app)
                r = client.post("/api/watching/max-size", json={"max_size": 300})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("max_size"), 300)
        self.assertEqual(body.get("count"), 5)
        self.assertEqual(body.get("added"), 4)

    def test_post_max_size_rejects_over_hard_cap(self):
        from fastapi.testclient import TestClient

        import web.app as web_app

        client = TestClient(web_app.app)
        r = client.post("/api/watching/max-size", json={"max_size": 1001})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
