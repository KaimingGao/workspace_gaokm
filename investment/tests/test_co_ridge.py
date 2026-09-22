"""ON open 链面板与 Ridge 测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _bars(n: int = 40, start: float = 10.0):
    from datetime import date, timedelta

    out = []
    px = start
    d0 = date(2025, 6, 1)
    for i in range(n):
        o = px
        c = px * (1.01 if i % 3 else 0.99)
        day = d0 + timedelta(days=i)
        out.append(
            {
                "date": day.isoformat(),
                "open": round(o, 4),
                "high": round(max(o, c) * 1.01, 4),
                "low": round(min(o, c) * 0.99, 4),
                "close": round(c, 4),
                "volume": 1e6 + i * 1000,
            }
        )
        px = c
    return out


def _bars_gapped(n: int = 40, start: float = 10.0):
    from datetime import date, timedelta

    out = []
    px = start
    d0 = date(2025, 6, 1)
    for i in range(n):
        o = px * (1.0 + (0.012 if i % 4 == 0 else -0.008 if i % 4 == 1 else 0.003))
        c = o * (1.012 if i % 3 else 0.988)
        day = d0 + timedelta(days=i)
        out.append(
            {
                "date": day.isoformat(),
                "open": round(o, 4),
                "high": round(max(o, c) * 1.02, 4),
                "low": round(min(o, c) * 0.98, 4),
                "close": round(c, 4),
                "volume": 1e6 + i * 1e5,
            }
        )
        px = c
    return out


class TestCoPanel(unittest.TestCase):
    def test_co_panel_label_is_fwd_close_open(self):
        from core.research.co_panel import collect_co_panel

        bars = _bars(35)
        xs, ys, dates, metas = collect_co_panel(bars, stock_code="000001")
        self.assertGreater(len(ys), 5)
        self.assertEqual(len(xs), len(ys))
        i = 0
        b_t = bars[12 + i]
        b_next = bars[13 + i]
        # 标签 = 真实隔夜缺口 open[T+1]/close[T]-1
        expected = (float(b_next["open"]) / float(b_t["close"]) - 1.0) * 100.0
        self.assertAlmostEqual(ys[i], expected, places=4)
        self.assertIn("ret_oc", xs[0])
        self.assertIn("y_on_today", xs[0])
        self.assertIn("overnight_gap", metas[0])

    def test_co_panel_enriched_features_are_pit(self):
        from core.research.co_panel import CO_Z_FEATURES, collect_co_panel

        bars = _bars_gapped(40)
        xs, ys, _, _ = collect_co_panel(bars, stock_code="000001", min_history=12)
        self.assertGreater(len(xs), 5)
        row = xs[0]
        skip_cs = {"sector_gap_breadth", "theme_day", "gap_vs_sector"}
        for k in CO_Z_FEATURES:
            if k in skip_cs:
                continue
            self.assertIn(k, row, k)
        i = 12
        prev = bars[i - 1]
        prev2 = bars[i - 2]
        b_t = bars[i]
        gap = (float(b_t["open"]) / float(prev["close"]) - 1.0) * 100.0
        yest_gap = (float(prev["open"]) / float(prev2["close"]) - 1.0) * 100.0
        loc = (float(prev["close"]) - float(prev["low"])) / (
            float(prev["high"]) - float(prev["low"])
        )
        self.assertAlmostEqual(float(row["gap_pct"]), gap, places=4)
        self.assertAlmostEqual(float(row["yest_gap"]), yest_gap, places=4)
        self.assertNotAlmostEqual(float(row["yest_gap"]), float(row["gap_pct"]), places=2)
        self.assertAlmostEqual(float(row["yest_close_loc"]), loc, places=4)
        self.assertGreaterEqual(float(row["yest_close_loc"]), 0.0)
        self.assertLessEqual(float(row["yest_close_loc"]), 1.0)
        self.assertIsNotNone(row["yclose_loc"])
        self.assertIsNotNone(row["mom3_pct"])
        self.assertIsNotNone(row["yest_range_pct"])
        self.assertIsNotNone(row["yest_vol_ratio"])
        self.assertIsNotNone(row["on_ma5"])
        self.assertAlmostEqual(float(row["dist_to_up_limit"]), 10.0 - float(row["ret_cc"]), places=3)

    def test_dist_to_up_limit_uses_board(self):
        from core.research.co_panel import dist_to_up_limit_pct

        self.assertAlmostEqual(dist_to_up_limit_pct(8.0, "600000"), 2.0, places=3)
        self.assertAlmostEqual(dist_to_up_limit_pct(8.0, "300750"), 12.0, places=3)
        self.assertIsNone(dist_to_up_limit_pct(8.0, None))
        self.assertIsNone(dist_to_up_limit_pct(8.0, ""))

    def test_build_features_from_quote_bars(self):
        from core.research.co_panel import build_co_features_from_quote_bars

        bars = _bars(20)
        quote = {
            "date": bars[-1]["date"],
            "open": bars[-1]["open"],
            "price_raw": bars[-1]["close"],
        }
        feats = build_co_features_from_quote_bars(quote, bars, gap_pct=1.2)
        self.assertIn("ret_oc", feats)
        self.assertIn("ret_cc", feats)
        self.assertIn("y_on_today", feats)
        self.assertAlmostEqual(float(feats["gap_pct"]), 1.2)


    def test_build_features_parses_yuan_open(self):
        from core.research.co_panel import build_co_features_from_quote_bars

        bars = _bars(20)
        quote = {
            "date": bars[-1]["date"],
            "open": "1291.50元",
            "price_raw": bars[-1]["close"],
        }
        feats = build_co_features_from_quote_bars(quote, bars, gap_pct=1.2)
        self.assertIn("ret_oc", feats)
        self.assertIn("ret_cc", feats)
        self.assertIn("y_on_today", feats)
        self.assertAlmostEqual(float(feats["gap_pct"]), 1.2)

    def test_build_features_uses_open_t_when_cache_behind(self):
        from core.research.co_panel import build_co_features_from_quote_bars

        bars = _bars(12)
        yesterday = bars[-1]
        synth = {
            "date": yesterday["date"],
            "open": yesterday["open"],
            "price_raw": yesterday["close"],
        }
        empty = build_co_features_from_quote_bars(
            synth,
            bars,
            trade_date="2026-09-17",
        )
        self.assertEqual(empty, {})

        feats = build_co_features_from_quote_bars(
            synth,
            bars,
            open_t=12.5,
            prev_close=yesterday["close"],
            trade_date="2026-09-17",
        )
        self.assertAlmostEqual(float(feats["y_on_today"]), (12.5 / yesterday["open"] - 1.0) * 100.0, places=4)
        self.assertAlmostEqual(
            float(feats["gap_pct"]),
            (12.5 / yesterday["close"] - 1.0) * 100.0,
            places=4,
        )

    def test_build_features_uses_trade_day_quote_when_cache_behind(self):
        """回测：日线窗停在 T−1，但 quote.date=T、open=今开，必须出 ŷ_co 特征。"""
        from core.research.co_panel import build_co_features_from_quote_bars

        bars = _bars(12)
        yesterday = bars[-1]
        quote = {
            "date": "2026-09-17",
            "trade_date": "2026-09-17",
            "open": 12.5,
            "open_raw": 12.5,
            "prev_close": yesterday["close"],
            "change_raw": (12.5 / yesterday["close"] - 1.0) * 100.0,
        }
        feats = build_co_features_from_quote_bars(quote, bars, trade_date="2026-09-17")
        self.assertIn("y_on_today", feats)
        self.assertAlmostEqual(
            float(feats["y_on_today"]),
            (12.5 / yesterday["open"] - 1.0) * 100.0,
            places=4,
        )
        self.assertAlmostEqual(
            float(feats["gap_pct"]),
            (12.5 / yesterday["close"] - 1.0) * 100.0,
            places=4,
        )

    def test_fit_and_persist(self):
        from core.research.co_ridge import (
            fit_co_ridge_report,
            load_co_model,
            persist_co_model,
            predict_co_from_features,
        )

        stock_bars = [
            {"code": "A", "bars": _bars(40, 10)},
            {"code": "B", "bars": _bars(40, 12)},
            {"code": "C", "bars": _bars(40, 8)},
        ]
        report = fit_co_ridge_report(
            stock_bars, ridge_lambda=1.0, theme_boost=1.5, holdout_trading_days=5
        )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertEqual(report.get("target"), "overnight_gap")
        oos = report.get("oos") or {}
        self.assertEqual(oos.get("split_mode"), "holdout_days")
        self.assertEqual(int(oos.get("holdout_trading_days") or 0), 5)
        self.assertEqual(int(oos.get("n_test_days") or 0), 5)
        self.assertEqual(int(report.get("label_horizon_days") or 0), 1)
        self.assertEqual(report.get("schema"), "co_ridge_v1")
        from core.research.holdout import calendar_dates_from_stock_bars

        cal = calendar_dates_from_stock_bars(stock_bars)
        self.assertEqual(report.get("eval_start"), cal[-5])
        rm = report["return_model"]
        self.assertEqual(
            (rm.get("y_spec") or {}).get("formula"), "open[T+1]/close[T]-1"
        )
        extras = rm.get("extra_features") or []
        self.assertIn("yest_close_loc", extras)
        self.assertIn("yest_gap", extras)
        self.assertIn("dist_to_up_limit", extras)
        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            with patch("core.paths.LIVE_DIR", live):
                saved = persist_co_model(report, note="test")
                self.assertTrue(saved.get("success"))
                doc = load_co_model()
                self.assertIsNotNone(doc)
                self.assertEqual(doc.get("dual_score_head"), "predicted_score_on")
                yhat = predict_co_from_features(
                    {
                        "ret_oc": 0.5,
                        "gap_pct": 1.0,
                        "ret_cc": 0.8,
                        "y_on_today": 0.3,
                        "sector_gap_breadth": 0.4,
                        "theme_day": 1.0,
                    },
                    model_doc=doc,
                )
                self.assertIsNotNone(yhat)


class TestCoScoreAttach(unittest.TestCase):
    def test_apply_co_fields(self):
        from core.signal.dual_score.co import apply_co_score_fields

        item = {"stock_code": "600519", "predicted_score": 1.2}
        apply_co_score_fields(item, co_yhat=-0.35, feats={"ret_oc": 0.1, "gap_pct": 2.0})
        self.assertAlmostEqual(item["predicted_score_on"], -0.35)
        self.assertAlmostEqual(item["y_co"], -0.35)
        self.assertEqual(
            (item.get("y_spec_co") or {}).get("formula"), "open[T+1]/close[T]-1"
        )
        self.assertEqual(item["y_spec_on"], item["y_spec_co"])
        self.assertEqual(item["features_on"], item["features_co"])
        self.assertEqual(item["predicted_score"], 1.2)

    def test_attach_pit_without_model(self):
        from core.signal.dual_score.co import attach_co_score_pit

        bars = _bars(25)
        item = {"stock_code": "000001", "predicted_score": 0.5}
        with patch("core.research.co_ridge.load_co_model", return_value=None):
            attach_co_score_pit(
                item,
                quote={
                    "date": bars[-1]["date"],
                    "open": bars[-1]["open"],
                    "price_raw": bars[-1]["close"],
                },
                bars=bars,
            )
        self.assertIn("y_spec_co", item)
        self.assertIsNone(item.get("predicted_score_on"))

    def test_load_co_model_falls_back_to_last_report(self):
        from core.research.co_ridge import (
            fit_co_ridge_report,
            load_co_model,
            load_co_last_report,
            save_co_last_report,
        )

        stock_bars = [
            {"code": "A", "bars": _bars(40, 10)},
            {"code": "B", "bars": _bars(40, 12)},
            {"code": "C", "bars": _bars(40, 8)},
        ]
        report = fit_co_ridge_report(stock_bars, ridge_lambda=1.0, theme_boost=1.5)
        self.assertTrue(report.get("success"), report.get("error"))
        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            with patch("core.paths.LIVE_DIR", live):
                save_co_last_report(report)
                doc = load_co_model()
                self.assertIsNotNone(doc)
                self.assertTrue(doc.get("_shadow"))
                last = load_co_last_report()
                self.assertIsNotNone(last)
                self.assertEqual(last.get("return_model"), doc.get("return_model"))

    def test_load_co_model_falls_back_to_legacy_on_file(self):
        from core.research.co_ridge import (
            co_model_path,
            co_model_path_legacy,
            fit_co_ridge_report,
            load_co_model,
            persist_co_model,
        )

        stock_bars = [
            {"code": "A", "bars": _bars(40, 10)},
            {"code": "B", "bars": _bars(40, 12)},
            {"code": "C", "bars": _bars(40, 8)},
        ]
        report = fit_co_ridge_report(stock_bars, ridge_lambda=1.0, theme_boost=1.5)
        self.assertTrue(report.get("success"), report.get("error"))
        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            with patch("core.paths.LIVE_DIR", live):
                saved = persist_co_model(report, note="legacy")
                self.assertTrue(saved.get("success"))
                self.assertTrue(os.path.isfile(co_model_path()))
                self.assertTrue(os.path.isfile(co_model_path_legacy()))
                os.remove(co_model_path())
                doc = load_co_model()
                self.assertIsNotNone(doc)
                self.assertFalse(doc.get("_shadow"))
                self.assertIn("return_model", doc)

    def test_apply_reads_legacy_features_on(self):
        from core.signal.dual_score.co import apply_co_score_fields

        item = {"features_on": {"gap_pct": 3.0, "ret_oc": 0.2}}
        apply_co_score_fields(item, co_yhat=0.1)
        self.assertEqual((item.get("features_co") or {}).get("gap_pct"), 3.0)
        self.assertEqual(item["features_on"], item["features_co"])

    def test_dual_score_book_fields_passes_co(self):
        from core.signal.dual_score.book import dual_score_book_fields

        item = {
            "predicted_score": 1.0,
            "predicted_score_on": -0.42,
            "features_co": {"gap_pct": 1.2},
            "formula_terms_co": {"total": -0.42, "terms": []},
            "y_spec_co": {"formula": "open[T+1]/close[T]-1"},
        }
        out = dual_score_book_fields(item)
        self.assertAlmostEqual(out.get("predicted_score_on"), -0.42)
        self.assertEqual(out.get("features_co"), {"gap_pct": 1.2})

    def test_overlay_co_cross_section_copies_gap_vs_sector(self):
        from core.signal.dual_score.co import overlay_co_cross_section

        out = overlay_co_cross_section(
            {"ret_oc": 2.3, "gap_pct": -0.96},
            {"gap_vs_sector": -0.4076, "theme_day": 0.0, "sector_gap_breadth": 0.0},
            sector_gap_breadth=0.0,
        )
        self.assertEqual(out["gap_vs_sector"], -0.4076)
        self.assertEqual(out["theme_day"], 0.0)
        self.assertEqual(out["sector_gap_breadth"], 0.0)
        self.assertEqual(out["ret_oc"], 2.3)

    def test_attach_co_score_pit_uses_tau_gap_vs_sector(self):
        from core.signal.dual_score.co import attach_co_score_pit

        bars = _bars(25)
        captured = {}

        def _pred(feats, model_doc=None):
            captured["feats"] = dict(feats or {})
            return -0.256

        item = {
            "stock_code": "600183",
            "features_tau": {"gap_vs_sector": -0.4076, "theme_day": 0.0},
        }
        with patch("core.research.co_ridge.predict_co_from_features", side_effect=_pred):
            with patch(
                "core.research.co_ridge.load_co_model",
                return_value={"return_model": {"intercept": -0.12, "coefficients": {}}},
            ):
                attach_co_score_pit(
                    item,
                    quote={
                        "date": bars[-1]["date"],
                        "open": bars[-1]["open"],
                        "price_raw": bars[-1]["close"],
                    },
                    bars=bars,
                )
        self.assertEqual(captured["feats"].get("gap_vs_sector"), -0.4076)
        self.assertAlmostEqual(float(item.get("y_co")), -0.256)

    def test_ensure_formula_terms_co_refreshes_stale_z(self):
        from core.signal.dual_score.co import ensure_formula_terms_co

        item = {
            "predicted_score_on": -0.5,
            "features_co": {
                "ret_oc": 1.0,
                "gap_pct": 1.5,
                "ret_cc": 0.8,
                "y_on_today": 0.3,
                "sector_gap_breadth": 0.4,
                "gap_atr": 0.5,
            },
            "formula_terms_co": {
                "intercept": 0.01,
                "total": 0.01,
                "terms": [
                    {
                        "key": "ret_oc",
                        "beta": -0.1,
                        "z": 0.0,
                        "contrib": 0.0,
                        "note": "缺特征·z≈0",
                    }
                ],
            },
        }
        expl = ensure_formula_terms_co(item)
        self.assertIsNotNone(expl)
        terms = (expl or {}).get("terms") or []
        ret_oc = next((t for t in terms if t.get("key") == "ret_oc"), None)
        self.assertIsNotNone(ret_oc)
        self.assertNotEqual(float(ret_oc.get("z") or 0.0), 0.0)

    def test_holding_row_as_quote_parses_open(self):
        from core.signal.dual_score.co import holding_row_as_quote

        q = holding_row_as_quote(
            {
                "stock_code": "600519",
                "price": 1500.0,
                "open": 1490.5,
                "change_pct": 1.25,
                "unit": "元",
            }
        )
        self.assertTrue(q.get("success"))
        self.assertAlmostEqual(float(q.get("open_raw")), 1490.5)
        self.assertIn("元", str(q.get("open") or ""))

    def test_hydrate_holding_co_fields_fills_predicted_score_on(self):
        from core.signal.dual_score.co import hydrate_holding_co_fields

        row = {
            "stock_code": "600519",
            "price": 100.0,
            "open": 99.0,
            "change_pct": 1.0,
            "predicted_score": 0.5,
            "unit": "元",
        }
        bars = [
            {"date": "2026-01-01", "open": 98.0, "close": 99.0, "high": 100.0, "low": 97.0},
            {"date": "2026-01-02", "open": 99.0, "close": 100.0, "high": 101.0, "low": 98.0},
        ]
        with patch("core.data.facade.get_bars", return_value={"bars": bars}):
            with patch(
                "core.research.co_ridge.predict_co_from_features",
                return_value=-0.33,
            ):
                with patch(
                    "core.research.co_ridge.load_co_model",
                    return_value={"coef": {}, "return_model": {"y_spec": {}}},
                ):
                    hydrate_holding_co_fields(row)
        self.assertAlmostEqual(float(row.get("predicted_score_on")), -0.33)


if __name__ == "__main__":
    unittest.main()
