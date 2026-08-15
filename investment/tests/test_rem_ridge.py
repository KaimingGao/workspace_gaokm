"""R0/R1/R2 rem 面板与事件/舆情扩展测试。"""

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


class TestRemPanel(unittest.TestCase):
    def test_open_panel_shapes(self):
        from core.research.rem_panel import collect_rem_open_panel

        xs, ys, dates, metas = collect_rem_open_panel(_bars(35), stock_code="000001")
        self.assertGreater(len(ys), 5)
        self.assertEqual(len(xs), len(ys))
        self.assertIn("gap_pct", xs[0])
        self.assertIn("gap_atr", xs[0])
        self.assertNotIn("momentum", xs[0])
        self.assertIn("y_rem", metas[0])

    def test_breadth_and_theme_weights(self):
        from core.research.rem_panel import (
            attach_cross_section_breadth,
            collect_rem_open_panel,
            theme_sample_weights,
        )

        panels = []
        for code, start in (("A", 10.0), ("B", 20.0), ("C", 15.0)):
            xs, ys, dates, metas = collect_rem_open_panel(
                _bars(30, start=start), stock_code=code
            )
            panels.append(
                {"code": code, "xs": xs, "ys": ys, "dates": dates, "metas": metas}
            )
        enriched = attach_cross_section_breadth(panels, gap_trigger_pct=0.5)
        self.assertIn("sector_gap_breadth", enriched[0]["xs"][0])
        self.assertIn("gap_vs_sector", enriched[0]["xs"][0])
        w = theme_sample_weights(enriched[0]["metas"], theme_boost=2.0)
        self.assertEqual(len(w), len(enriched[0]["metas"]))

    def test_sector_relative_gap_uses_peer_median(self):
        from core.research.rem_panel import (
            gap_vs_sector_value,
            sector_gap_reference_by_code,
        )

        gaps = {"600519": 3.0, "600036": 1.0, "601318": 2.0, "000001": 5.0}
        sm = {
            "600519": "银行",
            "600036": "银行",
            "601318": "银行",
            "000001": "地产",
        }
        ref = sector_gap_reference_by_code(gaps, sector_map=sm, min_sector_n=3)
        self.assertAlmostEqual(ref["600519"], 2.0)
        self.assertAlmostEqual(gap_vs_sector_value(3.0, ref["600519"]), 1.0)
        # 地产仅 1 只 → 回退全截面中位 2.5
        self.assertAlmostEqual(ref["000001"], 2.5)
        self.assertAlmostEqual(gap_vs_sector_value(5.0, ref["000001"]), 2.5)

    def test_gap_atr_scales_by_volatility(self):
        from core.research.rem_panel import gap_atr_from_hist

        quiet = [
            {"high": 10.1, "low": 9.9, "close": 10.0},
            {"high": 10.12, "low": 9.88, "close": 10.0},
        ] * 8
        noisy = [
            {"high": 12.0, "low": 8.0, "close": 10.0},
            {"high": 11.5, "low": 8.5, "close": 10.0},
        ] * 8
        q = gap_atr_from_hist(2.0, quiet)
        n = gap_atr_from_hist(2.0, noisy)
        self.assertIsNotNone(q)
        self.assertIsNotNone(n)
        self.assertGreater(q, n)


class TestRemRidgeFit(unittest.TestCase):
    def test_gap_pct_not_dropped_as_low_variance(self):
        """百分点 gap 标准差常 <5，不能按 0–100 分制 min_std 误剔。"""
        from core.research.factor_ols_fit import _prepare_complete_panel

        xs = []
        ys = []
        for i in range(40):
            gap = (i % 7) * 0.4 - 1.2  # std ≈ 0.8，远低于 min_std=5
            xs.append(
                {
                    "momentum": 40.0 + (i % 11) * 4.0,
                    "quality": 35.0 + (i % 9) * 5.0,
                    "relative_strength": 30.0 + (i % 8) * 4.5,
                    "gap_pct": gap,
                    "open_gap": gap,
                    "sector_gap_breadth": 0.02 + (i % 5) * 0.01,
                    "theme_day": 1.0 if i % 4 == 0 else 0.0,
                }
            )
            ys.append(0.1 * gap + 0.01 * (i % 3))
        feats = [
            "momentum",
            "quality",
            "relative_strength",
            "gap_pct",
            "open_gap",
            "sector_gap_breadth",
            "theme_day",
        ]
        # 默认门槛：分制因子够多 → 不放松 min_std → gap 被剔
        _, _, active0, _, meta0 = _prepare_complete_panel(
            xs, ys, feats, min_std=5.0
        )
        dropped = {
            (d["name"] if isinstance(d, dict) else d)
            for d in (meta0.get("dropped_low_variance") or [])
        }
        self.assertIn("gap_pct", dropped)
        self.assertNotIn("gap_pct", active0 or [])
        # rem 豁免：gap 保留
        _, _, active1, _, meta1 = _prepare_complete_panel(
            xs,
            ys,
            feats,
            min_std=5.0,
            min_std_exempt=[
                "gap_pct",
                "open_gap",
                "sector_gap_breadth",
                "theme_day",
                "gap_atr",
                "gap_vs_sector",
            ],
        )
        self.assertIn("gap_pct", active1)
        self.assertNotIn(
            "gap_pct",
            {
                (d["name"] if isinstance(d, dict) else d)
                for d in (meta1.get("dropped_low_variance") or [])
            },
        )
        self.assertIn("gap_pct", meta1.get("min_std_exempt") or [])

    def test_fit_synthetic_pool(self):
        from quant.research.rem_ridge import fit_rem_ridge_report, persist_rem_model

        stock_bars = [
            {"code": "A", "bars": _bars(40, 10)},
            {"code": "B", "bars": _bars(40, 12)},
            {"code": "C", "bars": _bars(40, 8)},
        ]
        report = fit_rem_ridge_report(
            stock_bars, ridge_lambda=1.0, theme_boost=1.5, train_frac=0.7
        )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertFalse(report.get("residualized"))
        self.assertEqual(report.get("target"), "open_to_close_z")
        self.assertIn("return_model", report)
        self.assertIn("oos", report)
        self.assertEqual(report.get("tau"), "open")
        self.assertEqual(report.get("schema"), "rem_ridge_v6")
        rm = report["return_model"]
        self.assertIn("coefficients", rm)
        self.assertEqual((rm.get("y_spec") or {}).get("tau"), "open")
        coefs = rm.get("coefficients") or {}
        self.assertNotIn("momentum", coefs)
        self.assertNotIn("quality", coefs)
        extras = rm.get("extra_features") or []
        self.assertIn("gap_atr", extras)
        self.assertIn("gap_vs_sector", extras)
        oos = report.get("oos") or {}
        self.assertIn("by_theme", oos)
        self.assertIn("theme", oos.get("by_theme") or {})
        self.assertIn("normal", oos.get("by_theme") or {})
        self.assertIn("residual_var", oos)
        # rem 豁免生效：即便合成 K 线缺口小，也不应因 low_variance 进 exclusion_reasons
        reasons = rm.get("exclusion_reasons") or {}
        self.assertNotEqual(reasons.get("gap_pct"), "low_variance")

        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            with patch("core.paths.LIVE_DIR", live):
                saved = persist_rem_model(report, note="test")
                self.assertTrue(saved.get("success"))
                self.assertEqual(saved.get("schema"), "rem_ridge_v6")
                from quant.research.rem_ridge import load_rem_model, predict_rem_from_features

                doc = load_rem_model()
                self.assertIsNotNone(doc)
                self.assertEqual(doc.get("schema"), "rem_ridge_v6")
                self.assertEqual(doc.get("tau"), "open")
                self.assertEqual(doc.get("dual_score_head"), "predicted_score_tau")
                yhat = predict_rem_from_features(
                    {"gap_pct": 2.5, "open_gap": 2.5, "sector_gap_breadth": 0.6, "theme_day": 1.0},
                    model_doc=doc,
                )
                # 缺特征按均值填 z=0，应能出数（不再因部分特征缺失整段 None）
                self.assertIsNotNone(yhat)
                self.assertTrue(doc.get("return_model"))
                from quant.research.rem_ridge import explain_rem_prediction

                expl = explain_rem_prediction(
                    {"gap_pct": 2.5, "open_gap": 2.5, "sector_gap_breadth": 0.6, "theme_day": 1.0},
                    model_doc=doc,
                )
                self.assertIsNotNone(expl)
                self.assertIn("terms", expl)
                self.assertAlmostEqual(float(expl["total"]), float(yhat), places=4)

    def test_persist_uses_last_report_without_refit(self):
        from quant.research.rem_ridge import (
            persist_rem_model,
            save_rem_last_report,
            load_rem_last_report,
        )

        dummy = {
            "success": True,
            "schema": "rem_ridge_v5",
            "return_model": {
                "coefficients": {"gap_pct": 0.1},
                "intercept": 0.0,
                "y_spec": {"formula": "close[T]/open[T]-1", "tau": "open", "unit": "pct"},
            },
            "oos": {"ic": 0.09},
            "sample_count": 10,
            "stock_count": 3,
        }
        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            with patch("core.paths.LIVE_DIR", live):
                save_rem_last_report(dummy)
                last = load_rem_last_report()
                self.assertIsNotNone(last)
                saved = persist_rem_model(last, note="from last")
                self.assertTrue(saved.get("success"))
                from quant.research.rem_ridge import load_rem_model

                doc = load_rem_model()
                self.assertAlmostEqual(
                    float((doc.get("return_model") or {}).get("coefficients")["gap_pct"]),
                    0.1,
                )


class TestSentimentBullish(unittest.TestCase):
    def test_bullish_soft_hold(self):
        from core.sentiment_prior import (
            build_sentiment_prior,
            should_soft_hold_from_sentiment,
        )

        prior = build_sentiment_prior(
            {"label": "bullish", "score": 0.8},
            config={
                "sentiment": {
                    "prior": {
                        "mode": "gate",
                        "reduce_avoid_on_bullish": True,
                        "warn_only": True,
                    }
                }
            },
        )
        self.assertTrue(prior.get("bullish_theme"))
        self.assertTrue(should_soft_hold_from_sentiment(prior))


class TestEventBreadth(unittest.TestCase):
    def test_sector_breadth_helper(self):
        from core.event_prior import sector_gap_breadth

        b = sector_gap_breadth({"a": 3.0, "b": 1.0, "c": 2.5}, gap_trigger_pct=2.0)
        self.assertAlmostEqual(b, 2 / 3, places=3)

    def test_sector_peers_mode(self):
        from core.event_prior import compute_sector_gap_breadth_live

        quotes = {
            "000001": {"success": True, "open": "10.2元", "price_raw": 10.5, "change_raw": 2.0},
            "000002": {"success": True, "open": "20.4元", "price_raw": 20.0, "change_raw": -1.0},
            "600000": {"success": True, "open": "8.2元", "price_raw": 8.0, "change_raw": 1.0},
        }
        with patch(
            "core.portfolio_optimize.load_sector_map",
            return_value={"000001": "银行", "000002": "银行", "600000": "银行", "300750": "新能源"},
        ), patch(
            "core.ports.market.batch_query_quotes", return_value={}
        ):
            out = compute_sector_gap_breadth_live(
                ["000001", "000002", "600000", "300750"],
                quotes=quotes,
                focus_code="000001",
                use_sector_peers=True,
                gap_trigger_pct=1.0,
            )
        self.assertEqual(out.get("universe_mode"), "sector_peers")
        self.assertIn("000001", out.get("peer_codes") or [])
        self.assertNotIn("300750", out.get("peer_codes") or [])

    def test_pool_shared_breadth_no_peers(self):
        """刷簿：整池 quotes 一次算共享 breadth（与 rem 按日广度同构）。"""
        from core.event_prior import compute_sector_gap_breadth_live

        quotes = {
            "000001": {"success": True, "open": "10.3元", "price_raw": 10.5, "change_raw": 2.0},
            "000002": {"success": True, "open": "20.0元", "price_raw": 20.0, "change_raw": 0.0},
            "600000": {"success": True, "open": "8.24元", "price_raw": 8.0, "change_raw": 1.0},
        }
        with patch("core.ports.market.batch_query_quotes", return_value={}):
            out = compute_sector_gap_breadth_live(
                ["000001", "000002", "600000"],
                quotes=quotes,
                use_sector_peers=False,
                gap_trigger_pct=2.0,
            )
        self.assertEqual(out.get("universe_mode"), "codes_universe")
        # gaps ≈ 2%, 0%, 3% → 2/3 hit trigger
        self.assertIsNotNone(out.get("breadth"))
        self.assertGreaterEqual(float(out["breadth"]), 0.3)


if __name__ == "__main__":
    unittest.main()
