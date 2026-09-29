"""ŷ_oo_rank pairwise LTR 影子头测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _has_lightgbm() -> bool:
    try:
        import lightgbm  # noqa: F401

        return True
    except ImportError:
        return False


def _synth_days(n_days: int = 40, n_names: int = 24, seed: int = 0):
    """特征与 y 正相关的合成截面日。"""
    days = []
    day = date(2024, 1, 2)
    made = 0
    d = 0
    while made < n_days:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        xs = []
        ys = []
        codes = []
        for i in range(n_names):
            signal = (i - (n_names - 1) / 2.0) / float(n_names)
            noise = (((i + 1) * (d + 3 + seed) * 17) % 11) / 100.0 - 0.05
            y = signal * 8.0 + noise * 3.0
            xs.append(
                {
                    "mom3": 50.0 + signal * 40.0,
                    "volume_ratio": 50.0 + signal * 20.0,
                    "vol_penalty_score": 50.0 - signal * 15.0,
                }
            )
            ys.append(y)
            codes.append(f"{i:06d}")
        days.append(
            {
                "date": day.isoformat(),
                "codes": codes,
                "xs": xs,
                "ys": ys,
            }
        )
        day += timedelta(days=1)
        made += 1
        d += 1
    return days


class TestOoRankPairSample(unittest.TestCase):
    def test_top_bottom_pairs_prefer_winners(self):
        from core.research.oo_rank_pairwise import sample_top_bottom_pairs

        ys = [float(i) for i in range(20)]
        pairs = sample_top_bottom_pairs(
            ys, top_k=5, bottom_k=5, top_frac=0.0, bottom_frac=0.0, extra_random=0
        )
        self.assertGreater(len(pairs), 10)
        for i, j, gap in pairs:
            self.assertGreater(ys[i], ys[j])
            self.assertGreater(gap, 0)

    def test_wide_frac_yields_more_pairs(self):
        from core.research.oo_rank_pairwise import sample_top_bottom_pairs

        ys = [float(i) for i in range(100)]
        narrow = sample_top_bottom_pairs(
            ys, top_k=10, bottom_k=10, top_frac=0.0, bottom_frac=0.0, extra_random=0
        )
        wide = sample_top_bottom_pairs(
            ys, top_k=10, bottom_k=10, top_frac=0.35, bottom_frac=0.35, extra_random=0
        )
        self.assertGreater(len(wide), len(narrow))


class TestOoRankFit(unittest.TestCase):
    def test_ranknet_recovers_direction(self):
        from core.research.oo_rank_pairwise import (
            fit_ranknet_linear,
            predict_oo_rank_from_features,
        )

        days = _synth_days(30, 20)
        fit = fit_ranknet_linear(days, top_k=5, bottom_k=5, epochs=60, l2=0.5, lr=0.08)
        self.assertTrue(fit.get("success"), fit)
        self.assertIn("mom3", fit.get("coefficients") or {})
        # 高 mom3 应对应更高分
        lo = predict_oo_rank_from_features(
            {"mom3": 20.0, "volume_ratio": 40.0, "vol_penalty_score": 60.0},
            fit=fit,
        )
        hi = predict_oo_rank_from_features(
            {"mom3": 80.0, "volume_ratio": 70.0, "vol_penalty_score": 30.0},
            fit=fit,
        )
        self.assertIsNotNone(lo)
        self.assertIsNotNone(hi)
        self.assertGreater(float(hi), float(lo))

    @unittest.skipUnless(_has_lightgbm(), "lightgbm 未安装")
    def test_fit_report_vs_ridge_shadow(self):
        from core.research.oo_rank_pairwise import (
            compare_oo_rank_shadow_track,
            fit_oo_rank_report,
        )

        days = _synth_days(45, 24)

        def _fake_panels(*_a, **_k):
            return days

        with patch(
            "core.research.oo_rank_panel.build_oo_rank_day_panels",
            side_effect=_fake_panels,
        ):
            # calendar from day dates
            stock_bars = [{"code": "000001", "bars": [{"date": d["date"]} for d in days]}]
            report = fit_oo_rank_report(
                stock_bars,
                holdout_trading_days=8,
                top_k=5,
                bottom_k=5,
                topk_track=5,
                persist=False,
            )
        self.assertTrue(report.get("success"), report)
        self.assertEqual(report.get("schema"), "oo_rank_pairwise_v1")
        self.assertEqual(report.get("backend"), "lambdarank")
        self.assertIn("oo_rank", (report.get("oos") or {}))
        self.assertIn("ridge_oo_baseline", (report.get("oos") or {}))
        model = report.get("return_model") or {}
        self.assertTrue(model.get("shadow_only"))
        self.assertEqual(float(model.get("intercept") or 0), 0.0)

        with patch(
            "core.research.oo_rank_panel.build_oo_rank_day_panels",
            side_effect=_fake_panels,
        ):
            track = compare_oo_rank_shadow_track(
                stock_bars,
                holdout_trading_days=8,
                top_k=5,
                bottom_k=5,
                topk_track=5,
            )
        self.assertTrue(track.get("success"), track)
        self.assertIn("delta_topk_mean_y_oo", track)

    def test_apply_and_aux_passthrough(self):
        from core.paper.rebalance.rank_lots import AUX_YHAT_KEYS, aux_yhat_fields
        from core.research.oo_rank_pairwise import (
            apply_oo_rank_scores,
            fit_ranknet_linear,
        )

        self.assertIn("y_oo_rank", AUX_YHAT_KEYS)
        days = _synth_days(20, 16)
        fit = fit_ranknet_linear(days, top_k=4, bottom_k=4, epochs=30)
        self.assertTrue(fit.get("success"), fit)
        item = {
            "stock_code": "000001",
            "sub_scores": {
                "mom3": 70.0,
                "volume_ratio": 60.0,
                "vol_penalty_score": 40.0,
            },
        }
        apply_oo_rank_scores([item], fit=fit)
        self.assertIsNotNone(item.get("y_oo_rank"))
        extra = aux_yhat_fields(item, include_tau_horizons=False)
        self.assertAlmostEqual(extra.get("y_oo_rank"), item["y_oo_rank"], places=5)

    def test_persist_research_sidecar(self):
        from core.research.holdout import research_model_path
        from core.research.oo_rank_pairwise import (
            fit_ranknet_linear,
            load_oo_rank_model,
            persist_oo_rank_model,
        )

        days = _synth_days(25, 18)
        fit = fit_ranknet_linear(days, top_k=5, bottom_k=5, epochs=25)
        report = {
            "success": True,
            "task": "oo_rank_pairwise",
            "return_model": dict(fit, shadow_only=True, model_role="live"),
            "return_model_research": dict(fit, shadow_only=True, model_role="research"),
            "oos": {},
            "y_spec": {"formula": "rank"},
            "pair_sampling": {},
            "note": "test",
        }
        with tempfile.TemporaryDirectory() as td:
            live = os.path.join(td, "oo_rank_pairwise_model.json")
            with patch(
                "core.research.oo_rank_pairwise.oo_rank_model_path",
                return_value=live,
            ), patch(
                "core.research.oo_rank_pairwise.oo_rank_last_report_path",
                return_value=os.path.join(td, "last.json"),
            ):
                paths = persist_oo_rank_model(report, also_research=True)
                self.assertTrue(os.path.isfile(live))
                self.assertTrue(os.path.isfile(research_model_path(live)))
                doc = load_oo_rank_model(prefer_research=True)
                self.assertIsNotNone(doc)
                self.assertIn("coefficients", (doc or {}).get("return_model") or {})


class TestOoRankFeatureMode(unittest.TestCase):
    def test_four_modes_column_sets(self):
        from core.research.oo_rank_panel import (
            FEATURE_MODES,
            enrich_day_panels_features,
        )

        days = _synth_days(3, 12)
        for mode in FEATURE_MODES:
            enriched = enrich_day_panels_features(days, feature_mode=mode)
            keys = set()
            for row in enriched[0]["xs"]:
                keys.update(row.keys())
            if mode == "raw":
                self.assertTrue(all(not k.endswith(("_cs_rank", "_cs_zscore")) for k in keys))
                self.assertIn("mom3", keys)
            elif mode == "cs_rank":
                self.assertTrue(keys)
                self.assertTrue(all(k.endswith("_cs_rank") for k in keys))
            elif mode == "cs_z":
                self.assertTrue(keys)
                self.assertTrue(all(k.endswith("_cs_zscore") for k in keys))
            else:
                self.assertIn("mom3", keys)
                self.assertTrue(any(k.endswith("_cs_rank") for k in keys))
                self.assertTrue(any(k.endswith("_cs_zscore") for k in keys))

    def test_apply_batch_attaches_cs(self):
        from core.research.oo_rank_panel import enrich_day_panels_features
        from core.research.oo_rank_pairwise import (
            apply_oo_rank_scores,
            fit_ranknet_linear,
        )

        days = enrich_day_panels_features(_synth_days(20, 16), feature_mode="cs_rank")
        fit = fit_ranknet_linear(days, top_k=4, bottom_k=4, epochs=30)
        self.assertTrue(fit.get("success"), fit)
        fit["feature_mode"] = "cs_rank"
        items = [
            {
                "stock_code": f"{i:06d}",
                "sub_scores": {
                    "mom3": 40.0 + i,
                    "volume_ratio": 50.0,
                    "vol_penalty_score": 50.0,
                },
            }
            for i in range(12)
        ]
        apply_oo_rank_scores(items, fit=fit)
        hats = [it.get("y_oo_rank") for it in items]
        self.assertTrue(all(h is not None for h in hats))
        # 批内 CS 后相对序应随 mom3 单调（合成方向）
        self.assertGreater(float(hats[-1]), float(hats[0]))

    def test_topk_focus_fewer_pairs_than_wide(self):
        from core.research.oo_rank_pairwise import (
            resolve_pair_preset,
            sample_top_bottom_pairs,
        )

        ys = [float(i) for i in range(100)]
        wide = resolve_pair_preset("wide")
        focus = resolve_pair_preset("topk_focus")
        n_wide = len(
            sample_top_bottom_pairs(
                ys,
                top_k=int(wide["top_k"]),
                bottom_k=int(wide["bottom_k"]),
                top_frac=float(wide["top_frac"]),
                bottom_frac=float(wide["bottom_frac"]),
                min_abs_gap=float(wide["min_abs_gap"]),
                extra_random=0,
            )
        )
        n_focus = len(
            sample_top_bottom_pairs(
                ys,
                top_k=int(focus["top_k"]),
                bottom_k=int(focus["bottom_k"]),
                top_frac=float(focus["top_frac"]),
                bottom_frac=float(focus["bottom_frac"]),
                min_abs_gap=float(focus["min_abs_gap"]),
                extra_random=0,
            )
        )
        self.assertGreater(n_wide, n_focus)

    @unittest.skipUnless(_has_lightgbm(), "lightgbm 未安装")
    def test_fit_report_records_mode_and_preset(self):
        from core.research.oo_rank_pairwise import fit_oo_rank_report

        days = _synth_days(45, 24)

        def _fake_panels(*_a, **_k):
            return days

        with patch(
            "core.research.oo_rank_panel.build_oo_rank_day_panels",
            side_effect=_fake_panels,
        ):
            stock_bars = [{"code": "000001", "bars": [{"date": d["date"]} for d in days]}]
            report = fit_oo_rank_report(
                stock_bars,
                holdout_trading_days=8,
                feature_mode="cs_rank",
                pair_preset="topk_focus",
                top_k=5,
                bottom_k=5,
                topk_track=5,
                persist=False,
            )
        self.assertTrue(report.get("success"), report)
        self.assertEqual(report.get("feature_mode"), "cs_rank")
        self.assertEqual(report.get("pair_preset"), "topk_focus")
        self.assertEqual(report.get("backend"), "lambdarank")
        self.assertIn("n_features", report.get("feature_meta") or {})
        meta = report.get("feature_meta") or {}
        self.assertGreater(int(meta.get("n_cs_rank_features") or 0), 0)


if __name__ == "__main__":
    unittest.main()
