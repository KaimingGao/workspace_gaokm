"""昨日复盘 · score ledger / outcomes / review。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestScoreLedger(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.ledger_root = os.path.join(self._tmpdir.name, "score_ledger")
        os.makedirs(self.ledger_root, exist_ok=True)

    def _patch_dir(self):
        return patch("core.score_ledger.io.ledger_dir", return_value=self.ledger_root)

    def test_upsert_and_load(self):
        from core.score_ledger import load_ledger, upsert_ledger_rows

        with self._patch_dir():
            out = upsert_ledger_rows(
                "2026-08-05",
                [
                    {
                        "stock_code": "600519",
                        "stock_name": "茅台",
                        "predicted_score": 1.2,
                        "cluster_label": "G1",
                        "score_formula_terms": {
                            "terms": [
                                {"key": "momentum", "contrib": 0.8, "z": 1.0, "beta": 0.8},
                                {"key": "value", "contrib": -0.2, "z": -0.5, "beta": 0.4},
                            ]
                        },
                    },
                    {
                        "stock_code": "000001",
                        "predicted_score": -0.5,
                        "score_formula_terms": {
                            "terms": [
                                {"key": "momentum", "contrib": -0.4, "z": -1.0, "beta": 0.4},
                            ]
                        },
                    },
                ],
                source="test",
            )
            self.assertTrue(out["success"])
            self.assertEqual(out["n_rows"], 2)
            loaded = load_ledger("2026-08-05")
            self.assertFalse(loaded["empty"])
            codes = {r["code"] for r in loaded["rows"]}
            self.assertEqual(codes, {"600519", "000001"})
            top = next(r for r in loaded["rows"] if r["code"] == "600519")
            self.assertEqual(top["formula_terms_top"][0]["key"], "momentum")

    def test_fill_outcomes_and_review(self):
        from core.score_ledger import (
            build_score_review,
            fill_outcomes,
            upsert_ledger_rows,
        )

        def fake_bars(code, limit=40, offline_ok=True):
            # as_of 08-05 → next 1d = 08-06 (assume weekday calendar lite)
            bars = [
                {"date": "2026-08-04", "close": 100.0},
                {"date": "2026-08-05", "close": 100.0},
                {"date": "2026-08-06", "close": 102.0},  # +2%
                {"date": "2026-08-07", "close": 103.0},
                {"date": "2026-08-08", "close": 104.0},
            ]
            if str(code).endswith("001"):
                # wrong direction for negative yhat if we use +2%
                bars = [
                    {"date": "2026-08-04", "close": 100.0},
                    {"date": "2026-08-05", "close": 100.0},
                    {"date": "2026-08-06", "close": 98.0},  # -2%
                    {"date": "2026-08-07", "close": 97.0},
                    {"date": "2026-08-08", "close": 96.0},
                ]
            return bars, "test"

        with self._patch_dir(), patch(
            "core.data.facade.bars_and_source", side_effect=fake_bars
        ), patch(
            "core.market.calendar.next_trading_day",
            side_effect=lambda d, n=1, **kw: {
                1: "2026-08-06",
                3: "2026-08-08",
            }.get(int(n), "2026-08-06"),
        ), patch(
            "core.market.calendar.is_trading_day", return_value=True
        ):
            upsert_ledger_rows(
                "2026-08-05",
                [
                    {
                        "stock_code": "600519",
                        "predicted_score": 1.5,
                        "score_formula_terms": {
                            "terms": [{"key": "momentum", "contrib": 1.0, "z": 1.0, "beta": 1.0}]
                        },
                    },
                    {
                        "stock_code": "000001",
                        "predicted_score": 1.0,  # predicts up, but -2% → wrong
                        "score_formula_terms": {
                            "terms": [{"key": "momentum", "contrib": 0.9, "z": 0.9, "beta": 1.0}]
                        },
                    },
                ],
                source="test",
            )
            filled = fill_outcomes("2026-08-05", horizon_days=1)
            self.assertTrue(filled["success"])
            self.assertEqual(filled["filled"], 2)
            rev = build_score_review("2026-08-05", horizon_days=1, autofill=False)
            self.assertTrue(rev["success"])
            self.assertFalse(rev["empty"])
            self.assertEqual(rev["summary"]["n_scored"], 2)
            self.assertEqual(rev["summary"]["hits"], 1)
            self.assertEqual(rev["summary"]["wrong"], 1)
            wrong_codes = {r["code"] for r in rev["wrong_rows"]}
            self.assertIn("000001", wrong_codes)
            self.assertTrue(isinstance(rev.get("industry_blame"), list))
            self.assertTrue(isinstance(rev.get("cluster_blame"), list))
            self.assertTrue(len(rev.get("scored_rows") or []) >= 2)

    def test_code_yhat_and_hit_series(self):
        from core.score_ledger import (
            code_yhat_series,
            fill_outcomes,
            hit_rate_series,
            upsert_ledger_rows,
        )

        def fake_bars(code, limit=40, offline_ok=True):
            return (
                [
                    {"date": "2026-08-04", "close": 100.0},
                    {"date": "2026-08-05", "close": 100.0},
                    {"date": "2026-08-06", "close": 102.0},
                ],
                "test",
            )

        with self._patch_dir(), patch(
            "core.data.facade.bars_and_source", side_effect=fake_bars
        ), patch(
            "core.market.calendar.next_trading_day",
            side_effect=lambda d, n=1, **kw: "2026-08-06",
        ), patch("core.market.calendar.is_trading_day", return_value=True):
            for d, y in (("2026-08-04", 0.5), ("2026-08-05", 1.2)):
                upsert_ledger_rows(
                    d,
                    [{"stock_code": "600519", "predicted_score": y, "stock_name": "茅台"}],
                    source="test",
                )
                fill_outcomes(d, horizon_days=1)
            ser = code_yhat_series("600519", limit=10)
            self.assertTrue(ser["success"])
            self.assertGreaterEqual(ser["n"], 2)
            self.assertEqual(ser["code"], "600519")
            hit = hit_rate_series(horizon_days=1, limit=10, autofill=False)
            self.assertTrue(hit["success"])
            self.assertGreaterEqual(hit["n"], 1)
            self.assertIn("hit_rate", hit["points"][0])

    def test_stock_panel_aligns_close_chg_yhat(self):
        from core.score_ledger import stock_panel_series, upsert_ledger_rows

        def fake_bars(code, limit=60, **kwargs):
            return (
                [
                    {"date": "2026-08-04", "close": 100.0},
                    {"date": "2026-08-05", "close": 101.0},
                    {"date": "2026-08-06", "close": 99.0},
                ],
                "test",
            )

        with self._patch_dir(), patch(
            "core.data.facade.bars_and_source", side_effect=fake_bars
        ):
            upsert_ledger_rows(
                "2026-08-05",
                [{"stock_code": "600519", "predicted_score": 1.5, "stock_name": "茅台"}],
                source="test",
            )
            out = stock_panel_series("600519", lookback=10)
            self.assertTrue(out["success"])
            self.assertLessEqual(out["n_close"], 10)
            self.assertEqual(out["lookback"], 10)
            self.assertEqual(out["n_close"], 3)
            self.assertEqual(out["n_change"], 2)
            self.assertEqual(out["n_yhat"], 1)
            chg = {p["date"]: p["value"] for p in out["change_points"]}
            self.assertAlmostEqual(chg["2026-08-05"], 1.0, places=2)
            self.assertAlmostEqual(chg["2026-08-06"], round((99 / 101 - 1) * 100, 2), places=2)
            self.assertEqual(out["yhat_points"][0]["date"], "2026-08-05")
            self.assertAlmostEqual(out["yhat_points"][0]["value"], 1.5, places=3)
            # 无账本日 yhat 为 None，不插值
            by_d = {p["date"]: p["yhat"] for p in out["points"]}
            self.assertIsNone(by_d["2026-08-04"])
            self.assertIsNone(by_d["2026-08-06"])

    def test_sector_persisted_and_industry_blame(self):
        from core.score_ledger import build_score_review, fill_outcomes, upsert_ledger_rows

        def fake_bars(code, limit=40, offline_ok=True):
            return (
                [
                    {"date": "2026-08-04", "close": 100.0},
                    {"date": "2026-08-05", "close": 100.0},
                    {"date": "2026-08-06", "close": 98.0},
                ],
                "test",
            )

        with self._patch_dir(), patch(
            "core.data.facade.bars_and_source", side_effect=fake_bars
        ), patch(
            "core.market.calendar.next_trading_day",
            side_effect=lambda d, n=1, **kw: "2026-08-06",
        ), patch("core.market.calendar.is_trading_day", return_value=True):
            upsert_ledger_rows(
                "2026-08-05",
                [
                    {
                        "stock_code": "600519",
                        "predicted_score": 1.0,
                        "sector": "白酒",
                        "cluster_label": "G1",
                        "score_formula_terms": {
                            "terms": [{"key": "momentum", "contrib": 1.0, "z": 1.0}]
                        },
                    },
                    {
                        "stock_code": "000858",
                        "predicted_score": 0.8,
                        "sector": "白酒",
                        "cluster_label": "G1",
                        "score_formula_terms": {
                            "terms": [{"key": "value", "contrib": 0.5, "z": 0.5}]
                        },
                    },
                ],
                source="test",
            )
            fill_outcomes("2026-08-05", horizon_days=1)
            rev = build_score_review("2026-08-05", horizon_days=1, autofill=False)
            self.assertEqual(rev["summary"]["wrong"], 2)
            self.assertTrue(rev["industry_blame"])
            self.assertEqual(rev["industry_blame"][0]["sector"], "白酒")
            self.assertEqual(rev["industry_blame"][0]["wrong_count"], 2)
            self.assertEqual(rev["cluster_blame"][0]["cluster_label"], "G1")

    def test_run_score_ledger_daily_fill_mature(self):
        from core.score_ledger import (
            load_outcomes,
            run_score_ledger_daily,
            upsert_ledger_rows,
        )

        def fake_bars(code, limit=40, offline_ok=True):
            return (
                [
                    {"date": "2026-08-01", "close": 100.0},
                    {"date": "2026-08-04", "close": 100.0},
                    {"date": "2026-08-05", "close": 101.0},
                ],
                "test",
            )

        with self._patch_dir(), patch(
            "core.market.calendar.resolve_session_date", return_value="2026-08-05"
        ), patch(
            "core.market.calendar.prev_trading_day",
            side_effect=lambda d, n=1, **kw: {
                1: "2026-08-04",
                2: "2026-08-01",
                3: "2026-07-31",
            }.get(int(n), "2026-08-04"),
        ), patch(
            "core.market.calendar.next_trading_day",
            side_effect=lambda d, n=1, **kw: "2026-08-05",
        ), patch(
            "core.data.facade.bars_and_source", side_effect=fake_bars
        ):
            upsert_ledger_rows(
                "2026-08-04",
                [{"stock_code": "600519", "predicted_score": 1.0}],
                source="test",
            )
            out = run_score_ledger_daily(
                as_of="2026-08-05", horizon_days=1, fill_lookback=2
            )
            self.assertTrue(out["success"])
            self.assertTrue((out.get("freeze") or {}).get("skipped"))
            self.assertGreaterEqual(out["filled_days"], 1)
            oc = load_outcomes("2026-08-04")
            self.assertFalse(oc.get("empty"))

    def test_row_from_book_shape(self):
        from core.score_ledger import row_from_scored_item

        row = row_from_scored_item(
            {
                "stock_code": "300750",
                "score": 0.8,
                "predicted_score": 0.8,
                "sector": "电池",
            },
            as_of="2026-08-05",
            source="book",
        )
        self.assertEqual(row["code"], "300750")
        self.assertAlmostEqual(row["yhat"], 0.8)
        self.assertEqual(row["sector"], "电池")

    def test_row_rejects_heuristic_as_yhat_eod(self):
        from core.score_ledger import row_from_scored_item

        row = row_from_scored_item(
            {
                "stock_code": "600519",
                "score": 80.0,
                "heuristic_score": 80.0,
                # 无 predicted_score_eod / 合理 predicted_score
            },
            as_of="2026-08-05",
            source="book",
        )
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row["yhat"], 80.0)
        self.assertIsNone(row.get("yhat_eod"))

    def test_delete_ledger(self):
        import json

        from core.score_ledger import (
            delete_ledger,
            list_ledger_dates,
            load_ledger,
            load_outcomes,
            outcomes_path,
            upsert_ledger_rows,
        )

        with self._patch_dir():
            upsert_ledger_rows(
                "2026-08-05",
                [
                    {
                        "stock_code": "600519",
                        "stock_name": "茅台",
                        "predicted_score": 1.2,
                    }
                ],
                source="test",
            )
            with open(outcomes_path("2026-08-05"), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "success": True,
                        "as_of": "2026-08-05",
                        "by_code": {"600519": {"realized_h": 1.0, "sign_hit": True}},
                    },
                    f,
                )
            self.assertIn("2026-08-05", list_ledger_dates())
            self.assertFalse(load_outcomes("2026-08-05").get("empty"))
            out = delete_ledger("2026-08-05", include_outcomes=True)
            self.assertTrue(out["success"])
            self.assertNotIn("2026-08-05", list_ledger_dates())
            self.assertTrue(load_ledger("2026-08-05").get("empty"))
            self.assertTrue(load_outcomes("2026-08-05").get("empty"))
            miss = delete_ledger("2026-08-05")
            self.assertFalse(miss["success"])

    def test_list_ledger_dates_skips_sidecar_files(self):
        from core.score_ledger import list_ledger_dates, upsert_ledger_rows

        with self._patch_dir():
            upsert_ledger_rows(
                "2026-08-13",
                [{"stock_code": "600519", "predicted_score": 1.0}],
                source="test",
            )
            root = self.ledger_root
            for name in (
                "2026-08-14.nowcast_shadow.json",
                "2026-08-14.tau_shadow.json",
                "2026-08-13.outcomes.json",
                "2026-08-14.nowcast_shadow.outcomes.json",
            ):
                with open(os.path.join(root, name), "w", encoding="utf-8") as f:
                    f.write("{}")
            dates = list_ledger_dates()
            self.assertEqual(dates, ["2026-08-13"])
            self.assertFalse(any("shadow" in d for d in dates))

    def test_list_ledger_entries_marks_pending_close(self):
        from core.score_ledger import list_ledger_entries, upsert_ledger_rows

        with self._patch_dir(), patch(
            "core.market.calendar.resolve_session_date", return_value="2026-08-17"
        ):
            upsert_ledger_rows(
                "2026-08-14",
                [{"stock_code": "600519", "predicted_score": 0.2}],
                source="test",
            )
            ents = list_ledger_entries()
            row = next(e for e in ents if e["as_of"] == "2026-08-14")
            self.assertEqual(row["need_bar_date"], "2026-08-17")
            self.assertTrue(row["pending_close"])
            self.assertFalse(row["immature"])

    def test_resolve_freeze_as_of_clamps_to_feature(self):
        from core.score_ledger import resolve_freeze_as_of

        with patch(
            "core.score_ledger.asof.infer_feature_as_of", return_value="2026-08-11"
        ), patch(
            "core.score_ledger.asof.default_as_of", return_value="2026-08-11"
        ), patch(
            "core.market.calendar.resolve_session_date", return_value="2026-08-12"
        ):
            out = resolve_freeze_as_of("2026-08-12")
            self.assertEqual(out["as_of"], "2026-08-11")
            self.assertTrue(out["remapped"])
            self.assertEqual(out["feature_as_of"], "2026-08-11")

    def test_resolve_freeze_as_of_defaults_prev_without_feature(self):
        from core.score_ledger import resolve_freeze_as_of

        with patch(
            "core.score_ledger.asof.infer_feature_as_of", return_value=None
        ), patch(
            "core.score_ledger.asof.default_as_of", return_value="2026-08-11"
        ), patch(
            "core.market.calendar.resolve_session_date", return_value="2026-08-12"
        ):
            out = resolve_freeze_as_of(None)
            self.assertEqual(out["as_of"], "2026-08-11")
            out2 = resolve_freeze_as_of("2026-08-12")
            self.assertEqual(out2["as_of"], "2026-08-11")
            self.assertTrue(out2["remapped"])

    def test_freeze_from_cluster_book_deprecated(self):
        from core.score_ledger import freeze_from_cluster_book

        out = freeze_from_cluster_book(
            as_of="2026-08-12",
            book_doc={"book": [{"stock_code": "600519", "predicted_score": 1.5}]},
            force=True,
        )
        self.assertFalse(out.get("success"))
        self.assertTrue(out.get("deprecated"))
        self.assertIn("停用", str(out.get("error") or ""))

    def test_tau_shadow_freeze_and_review(self):
        from core.score_ledger import (
            build_tau_shadow_review,
            fill_outcomes,
            freeze_from_tau_shadow_book,
            load_tau_shadow_membership,
            row_from_scored_item,
            upsert_ledger_rows,
        )

        row = row_from_scored_item(
            {
                "stock_code": "600519",
                "predicted_score": 1.2,
                "predicted_score_eod": 1.2,
                "predicted_score_tau": 0.4,
            },
            as_of="2026-08-05",
            source="book",
        )
        self.assertAlmostEqual(row["yhat_tau"], 0.4)
        self.assertAlmostEqual(row["yhat_eod"], 1.2)

        def fake_bars(code, limit=40, offline_ok=True):
            return [
                {"date": "2026-08-05", "open": 100.0, "close": 100.0},
                {"date": "2026-08-06", "open": 100.0, "close": 101.0},  # +1% open→close
            ], "test"

        shadow_doc = {
            "updated_at": "2026-08-05T09:00:00",
            "book": [
                {
                    "stock_code": "600519",
                    "predicted_score": 1.2,
                    "predicted_score_eod": 1.2,
                    "predicted_score_tau": 0.5,
                    "rank": 1,
                },
                {
                    "stock_code": "000001",
                    "predicted_score": 0.8,
                    "predicted_score_eod": 0.8,
                    "predicted_score_tau": -0.2,
                    "rank": 2,
                },
            ],
            "meta": {
                "version": 9,
                "vs_eod_book": {"overlap": 1, "jaccard": 0.5},
            },
        }
        with self._patch_dir(), patch(
            "core.score_ledger.freeze.resolve_freeze_as_of",
            return_value={
                "as_of": "2026-08-05",
                "feature_as_of": "2026-08-05",
                "remapped": False,
                "note": None,
            },
        ), patch(
            "core.data.facade.bars_and_source", side_effect=fake_bars
        ), patch(
            "core.market.calendar.next_trading_day",
            side_effect=lambda d, n=1, **kw: "2026-08-06",
        ):
            fr = freeze_from_tau_shadow_book(
                as_of="2026-08-05", shadow_doc=shadow_doc, force=True
            )
            self.assertTrue(fr["success"])
            mem = load_tau_shadow_membership("2026-08-05")
            self.assertEqual(len(mem["rows"]), 2)
            upsert_ledger_rows(
                "2026-08-05",
                [
                    {
                        "stock_code": "600519",
                        "predicted_score": 1.2,
                        "predicted_score_tau": 0.5,
                    },
                    {
                        "stock_code": "000001",
                        "predicted_score": 0.8,
                        "predicted_score_tau": -0.2,
                    },
                ],
                source="test",
            )
            # 000001: need bars with negative open→close for opposite hit
            def fake_bars2(code, limit=40, offline_ok=True):
                if str(code).endswith("001"):
                    return [
                        {"date": "2026-08-05", "open": 100.0, "close": 100.0},
                        {"date": "2026-08-06", "open": 100.0, "close": 99.0},
                    ], "test"
                return fake_bars(code, limit=limit, offline_ok=offline_ok)

            with patch(
                "core.data.facade.bars_and_source", side_effect=fake_bars2
            ):
                filled = fill_outcomes("2026-08-05", horizon_days=1)
                self.assertEqual(filled.get("filled_tau"), 2)
            rev = build_tau_shadow_review(
                "2026-08-05", horizon_days=1, autofill=False
            )
            self.assertTrue(rev["success"])
            self.assertEqual(rev["tau_n"], 2)
            self.assertEqual(rev["tau_sign_hit_rate"], 1.0)
            self.assertEqual(
                rev["shadow_membership"]["vs_eod_book"]["overlap"], 1
            )
            from core.score_ledger import build_score_review

            full = build_score_review(
                "2026-08-05", horizon_days=1, autofill=False
            )
            self.assertIn("tau_shadow", full)
            self.assertEqual(full["tau_shadow"].get("tau_n"), 2)
            self.assertTrue(full["tau_shadow"].get("shadow_exists"))

    def test_nowcast_shadow_freeze_and_review(self):
        from core.score_ledger import (
            build_nowcast_shadow_review,
            build_score_review,
            fill_outcomes,
            freeze_from_nowcast_shadow_book,
            load_nowcast_shadow_membership,
            upsert_ledger_rows,
        )

        def fake_bars(code, limit=40, offline_ok=True):
            return [
                {"date": "2026-08-05", "open": 100.0, "close": 100.0},
                {"date": "2026-08-06", "open": 100.0, "close": 101.0},
            ], "test"

        shadow_doc = {
            "updated_at": "2026-08-05T09:00:00",
            "book": [
                {
                    "stock_code": "600519",
                    "predicted_score": 1.2,
                    "predicted_score_nowcast": 0.8,
                    "nowcast_as_of": "open",
                    "nowcast_x_prior": 0.5,
                    "rank": 1,
                },
                {
                    "stock_code": "000001",
                    "predicted_score": 0.8,
                    "predicted_score_nowcast": -0.3,
                    "nowcast_as_of": "open",
                    "nowcast_x_prior": 0.0,
                    "rank": 2,
                },
            ],
            "meta": {
                "version": 9,
                "nordhaus_revision_slope": 0.05,
                "vs_eod_book": {"overlap": 2, "jaccard": 1.0},
            },
        }
        with self._patch_dir(), patch(
            "core.score_ledger.freeze.resolve_freeze_as_of",
            return_value={
                "as_of": "2026-08-05",
                "feature_as_of": "2026-08-05",
                "remapped": False,
                "note": None,
            },
        ), patch(
            "core.data.facade.bars_and_source", side_effect=fake_bars
        ), patch(
            "core.market.calendar.next_trading_day",
            side_effect=lambda d, n=1, **kw: "2026-08-06",
        ):
            fr = freeze_from_nowcast_shadow_book(
                as_of="2026-08-05", shadow_doc=shadow_doc, force=True
            )
            self.assertTrue(fr["success"])
            mem = load_nowcast_shadow_membership("2026-08-05")
            self.assertEqual(len(mem["rows"]), 2)
            self.assertEqual(mem["meta"].get("nordhaus_revision_slope"), 0.05)
            upsert_ledger_rows(
                "2026-08-05",
                [
                    {
                        "stock_code": "600519",
                        "predicted_score": 1.2,
                        "predicted_score_nowcast": 0.8,
                        "nowcast_as_of": "open",
                        "nowcast_x_prior": 0.5,
                    },
                    {
                        "stock_code": "000001",
                        "predicted_score": 0.8,
                        "predicted_score_nowcast": -0.3,
                        "nowcast_as_of": "open",
                        "nowcast_x_prior": 0.0,
                    },
                ],
                source="test",
            )
            filled = fill_outcomes("2026-08-05", horizon_days=1)
            self.assertGreaterEqual(filled.get("filled_tau") or 0, 1)
            rev = build_nowcast_shadow_review(
                "2026-08-05", horizon_days=1, autofill=False
            )
            self.assertTrue(rev["success"])
            self.assertEqual(rev["nowcast_n"], 2)
            self.assertEqual(rev["nordhaus_revision_slope"], 0.05)
            full = build_score_review(
                "2026-08-05", horizon_days=1, autofill=False
            )
            self.assertIn("nowcast_shadow", full)
            self.assertTrue(full["nowcast_shadow"].get("shadow_exists"))

    def test_nowcast_review_jaccard_from_ledger_without_snapshot(self):
        from core.score_ledger import build_nowcast_shadow_review, upsert_ledger_rows

        with self._patch_dir():
            upsert_ledger_rows(
                "2026-08-05",
                [
                    {
                        "stock_code": "600519",
                        "predicted_score": 2.0,
                        "predicted_score_nowcast": 0.1,
                        "predicted_score_tau": 0.1,
                        "in_book": True,
                    },
                    {
                        "stock_code": "000001",
                        "predicted_score": 1.0,
                        "predicted_score_nowcast": 1.5,
                        "predicted_score_tau": 0.4,
                        "in_book": True,
                    },
                    {
                        "stock_code": "000002",
                        "predicted_score": 0.5,
                        "predicted_score_nowcast": 0.2,
                        "predicted_score_tau": 0.2,
                        "in_book": True,
                    },
                ],
                source="test",
                meta={"n_book": 2},
            )
            rev = build_nowcast_shadow_review(
                "2026-08-05", horizon_days=1, autofill=False
            )
            vs = (rev.get("shadow_membership") or {}).get("vs_eod_book") or {}
            self.assertIsNotNone(vs.get("jaccard"))
            self.assertEqual(vs.get("source"), "ledger_topk")
            self.assertFalse((rev.get("shadow_membership") or {}).get("exists"))

    def test_hydrate_ledger_yhat_tau(self):
        from core.score_ledger import (
            hydrate_ledger_yhat_tau,
            load_ledger,
            upsert_ledger_rows,
        )

        with self._patch_dir():
            upsert_ledger_rows(
                "2026-08-10",
                [
                    {
                        "stock_code": "600519",
                        "predicted_score": 1.5,
                        "rank": 1,
                    }
                ],
                source="test",
            )
            led0 = load_ledger("2026-08-10")
            self.assertIsNone((led0["rows"][0]).get("yhat_tau"))

            def fake_attach(item, **kwargs):
                item["predicted_score_tau"] = 0.33
                return item

            with patch(
                "core.research.tau_ridge.load_tau_model",
                return_value={"coef": {"gap_pct": 0.1}, "intercept": 0.0},
            ), patch(
                "core.data.facade.bars_and_source",
                return_value=(
                    [
                        {
                            "date": f"2026-07-{i:02d}",
                            "open": 10.0,
                            "high": 10.5,
                            "low": 9.5,
                            "close": 10.0 + i * 0.01,
                            "volume": 1e6,
                        }
                        for i in range(1, 28)
                    ]
                    + [
                        {
                            "date": "2026-08-10",
                            "open": 10.2,
                            "high": 10.4,
                            "low": 10.0,
                            "close": 10.3,
                            "volume": 1e6,
                        }
                    ],
                    "test",
                ),
            ), patch(
                "core.signal.cross_section_batch.score_window_as_item",
                return_value={
                    "stock_code": "600519",
                    "predicted_score": 1.5,
                    "sub_scores": {"momentum": 0.2},
                },
            ), patch(
                "core.signal.dual_score.attach_dual_score_pit",
                side_effect=fake_attach,
            ):
                out = hydrate_ledger_yhat_tau("2026-08-10", persist=True)
            self.assertTrue(out.get("success"))
            self.assertEqual(out.get("hydrated"), 1)
            led1 = load_ledger("2026-08-10")
            self.assertAlmostEqual(led1["rows"][0]["yhat_tau"], 0.33)
            self.assertTrue(led1["meta"].get("yhat_tau_hydrated"))


class TestLedgerFreezeGate(unittest.TestCase):
    def test_auto_freeze_skipped_intraday(self):
        from datetime import datetime, timezone, timedelta
        from core.score_ledger.asof import session_allows_ledger_freeze

        cn = timezone(timedelta(hours=8))
        noon = datetime(2026, 8, 24, 11, 0, tzinfo=cn)
        with patch(
            "core.market.calendar.resolve_session_date", return_value="2026-08-24"
        ), patch(
            "core.signal.session_pit.shanghai_now", return_value=noon
        ):
            gate = session_allows_ledger_freeze(auto=True)
            self.assertFalse(gate["ok"])
            self.assertIn("盘中", gate.get("reason") or "")

    def test_auto_freeze_ok_after_close(self):
        from datetime import datetime, timezone, timedelta
        from core.score_ledger.asof import session_allows_ledger_freeze

        cn = timezone(timedelta(hours=8))
        after = datetime(2026, 8, 24, 16, 0, tzinfo=cn)
        with patch(
            "core.market.calendar.resolve_session_date", return_value="2026-08-24"
        ), patch(
            "core.signal.session_pit.shanghai_now", return_value=after
        ):
            gate = session_allows_ledger_freeze(auto=True)
            self.assertTrue(gate["ok"])

    def test_historical_as_of_ok_intraday(self):
        from datetime import datetime, timezone, timedelta
        from core.score_ledger.asof import session_allows_ledger_freeze

        cn = timezone(timedelta(hours=8))
        noon = datetime(2026, 8, 24, 11, 0, tzinfo=cn)
        with patch(
            "core.market.calendar.resolve_session_date", return_value="2026-08-24"
        ), patch(
            "core.signal.session_pit.shanghai_now", return_value=noon
        ):
            gate = session_allows_ledger_freeze(as_of="2026-08-21", auto=False)
            self.assertTrue(gate["ok"])

    def test_infer_feature_as_of_skips_incomplete_session_bar(self):
        from datetime import datetime, timezone, timedelta
        from core.score_ledger.asof import infer_feature_as_of

        cn = timezone(timedelta(hours=8))
        noon = datetime(2026, 8, 24, 11, 0, tzinfo=cn)
        dates = ["2026-08-24", "2026-08-24", "2026-08-21", "2026-08-21"]

        def fake_last(code):
            return dates.pop(0) if dates else None

        with patch(
            "core.market.calendar.resolve_session_date", return_value="2026-08-24"
        ), patch(
            "core.signal.session_pit.asof_session_final", return_value=False
        ), patch(
            "core.score_ledger.asof._last_bar_date_for_code", side_effect=fake_last
        ):
            out = infer_feature_as_of(codes=["a", "b", "c", "d"])
            self.assertEqual(out, "2026-08-21")

    def test_cluster_auto_skip_does_not_write(self):
        from core.score_ledger import freeze_from_cluster_book

        out = freeze_from_cluster_book(
            book_doc={"book": [{"stock_code": "600519", "predicted_score": 1.2}]},
            auto=True,
        )
        self.assertFalse(out.get("success"))
        self.assertTrue(out.get("deprecated"))
        self.assertEqual(out.get("n_rows"), 0)


if __name__ == "__main__":
    unittest.main()
