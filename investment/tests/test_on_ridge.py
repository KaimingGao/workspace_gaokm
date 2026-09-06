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


class TestOnPanel(unittest.TestCase):
    def test_on_panel_label_is_fwd_open_open(self):
        from core.research.on_panel import collect_on_panel

        bars = _bars(35)
        xs, ys, dates, metas = collect_on_panel(bars, stock_code="000001")
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

    def test_build_features_from_quote_bars(self):
        from core.research.on_panel import build_on_features_from_quote_bars

        bars = _bars(20)
        quote = {
            "date": bars[-1]["date"],
            "open": bars[-1]["open"],
            "price_raw": bars[-1]["close"],
        }
        feats = build_on_features_from_quote_bars(quote, bars, gap_pct=1.2)
        self.assertIn("ret_oc", feats)
        self.assertIn("ret_cc", feats)
        self.assertIn("y_on_today", feats)
        self.assertAlmostEqual(float(feats["gap_pct"]), 1.2)


    def test_build_features_parses_yuan_open(self):
        from core.research.on_panel import build_on_features_from_quote_bars

        bars = _bars(20)
        quote = {
            "date": bars[-1]["date"],
            "open": "1291.50元",
            "price_raw": bars[-1]["close"],
        }
        feats = build_on_features_from_quote_bars(quote, bars, gap_pct=1.2)
        self.assertIn("ret_oc", feats)
        self.assertIn("ret_cc", feats)
        self.assertIn("y_on_today", feats)
        self.assertAlmostEqual(float(feats["gap_pct"]), 1.2)


class TestOnRidgeFit(unittest.TestCase):
    def test_fit_and_persist(self):
        from quant.research.on_ridge import (
            fit_on_ridge_report,
            load_on_model,
            persist_on_model,
            predict_on_from_features,
        )

        stock_bars = [
            {"code": "A", "bars": _bars(40, 10)},
            {"code": "B", "bars": _bars(40, 12)},
            {"code": "C", "bars": _bars(40, 8)},
        ]
        report = fit_on_ridge_report(stock_bars, ridge_lambda=1.0, theme_boost=1.5)
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertEqual(report.get("target"), "overnight_gap")
        self.assertEqual(report.get("schema"), "on_ridge_v1")
        rm = report["return_model"]
        self.assertEqual(
            (rm.get("y_spec") or {}).get("formula"), "open[T+1]/close[T]-1"
        )
        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            with patch("core.paths.LIVE_DIR", live):
                saved = persist_on_model(report, note="test")
                self.assertTrue(saved.get("success"))
                doc = load_on_model()
                self.assertIsNotNone(doc)
                self.assertEqual(doc.get("dual_score_head"), "predicted_score_on")
                yhat = predict_on_from_features(
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


class TestOnScoreAttach(unittest.TestCase):
    def test_apply_on_fields(self):
        from core.signal.dual_score.on import apply_on_score_fields

        item = {"stock_code": "600519", "predicted_score": 1.2}
        apply_on_score_fields(item, on_yhat=-0.35, feats={"ret_oc": 0.1, "gap_pct": 2.0})
        self.assertAlmostEqual(item["predicted_score_on"], -0.35)
        self.assertEqual(
            (item.get("y_spec_on") or {}).get("formula"), "open[T+1]/close[T]-1"
        )
        self.assertEqual(item["predicted_score"], 1.2)

    def test_attach_pit_without_model(self):
        from core.signal.dual_score.on import attach_on_score_pit

        bars = _bars(25)
        item = {"stock_code": "000001", "predicted_score": 0.5}
        with patch("core.research.on_ridge.load_on_model", return_value=None):
            attach_on_score_pit(
                item,
                quote={
                    "date": bars[-1]["date"],
                    "open": bars[-1]["open"],
                    "price_raw": bars[-1]["close"],
                },
                bars=bars,
            )
        self.assertIn("y_spec_on", item)
        self.assertIsNone(item.get("predicted_score_on"))

    def test_load_on_model_falls_back_to_last_report(self):
        from quant.research.on_ridge import (
            fit_on_ridge_report,
            load_on_model,
            load_on_last_report,
            save_on_last_report,
        )

        stock_bars = [
            {"code": "A", "bars": _bars(40, 10)},
            {"code": "B", "bars": _bars(40, 12)},
            {"code": "C", "bars": _bars(40, 8)},
        ]
        report = fit_on_ridge_report(stock_bars, ridge_lambda=1.0, theme_boost=1.5)
        self.assertTrue(report.get("success"), report.get("error"))
        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            with patch("core.paths.LIVE_DIR", live):
                save_on_last_report(report)
                doc = load_on_model()
                self.assertIsNotNone(doc)
                self.assertEqual(load_on_last_report(), doc)

    def test_dual_score_book_fields_passes_on(self):
        from core.signal.dual_score.book import dual_score_book_fields

        item = {
            "predicted_score": 1.0,
            "predicted_score_on": -0.42,
            "features_on": {"gap_pct": 1.2},
            "formula_terms_on": {"total": -0.42, "terms": []},
            "y_spec_on": {"formula": "open[T+1]/close[T]-1"},
        }
        out = dual_score_book_fields(item)
        self.assertAlmostEqual(out.get("predicted_score_on"), -0.42)
        self.assertEqual(out.get("features_on"), {"gap_pct": 1.2})

    def test_ensure_formula_terms_on_refreshes_stale_z(self):
        from core.signal.dual_score.on import ensure_formula_terms_on

        item = {
            "predicted_score_on": -0.5,
            "features_on": {
                "ret_oc": 1.0,
                "gap_pct": 1.5,
                "ret_cc": 0.8,
                "y_on_today": 0.3,
                "sector_gap_breadth": 0.4,
                "gap_atr": 0.5,
            },
            "formula_terms_on": {
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
        expl = ensure_formula_terms_on(item)
        self.assertIsNotNone(expl)
        terms = (expl or {}).get("terms") or []
        ret_oc = next((t for t in terms if t.get("key") == "ret_oc"), None)
        self.assertIsNotNone(ret_oc)
        self.assertNotEqual(float(ret_oc.get("z") or 0.0), 0.0)

    def test_holding_row_as_quote_parses_open(self):
        from core.signal.dual_score.on import holding_row_as_quote

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

    def test_hydrate_holding_on_fields_fills_predicted_score_on(self):
        from core.signal.dual_score.on import hydrate_holding_on_fields

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
                "core.research.on_ridge.predict_on_from_features",
                return_value=-0.33,
            ):
                with patch(
                    "core.research.on_ridge.load_on_model",
                    return_value={"coef": {}, "return_model": {"y_spec": {}}},
                ):
                    hydrate_holding_on_fields(row)
        self.assertAlmostEqual(float(row.get("predicted_score_on")), -0.33)


if __name__ == "__main__":
    unittest.main()
