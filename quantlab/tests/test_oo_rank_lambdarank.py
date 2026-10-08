"""ŷ_oo_rank LambdaRank 截面名次测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _has_lightgbm() -> bool:
    try:
        import lightgbm  # noqa: F401

        return True
    except ImportError:
        return False


def _pack_day(date_s, codes, xs, ys):
    names = list(xs[0].keys()) if xs else []
    X = np.array(
        [
            [float(row[n]) if row.get(n) is not None else np.nan for n in names]
            for row in xs
        ],
        dtype=np.float64,
    )
    return {"date": date_s, "codes": codes, "ys": ys, "X": X, "names": names}


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
        days.append(_pack_day(day.isoformat(), codes, xs, ys))
        day += timedelta(days=1)
        made += 1
        d += 1
    return days


class TestOoRankUniverse(unittest.TestCase):
    def test_resolve_full_watching_when_flag_off(self):
        from core.research.oo_rank_panel import resolve_oo_rank_universe

        with patch(
            "core.watching.store.read_watching",
            return_value={"watchlist": [f"{i:06d}" for i in range(12)]},
        ):
            out = resolve_oo_rank_universe(watching_tier_a_only=False)
        self.assertTrue(out.get("success"), out)
        self.assertEqual(out.get("universe_source"), "watching")
        self.assertEqual(len(out.get("codes") or []), 12)
        self.assertFalse(out.get("watching_tier_a_only"))

    def test_resolve_tier_a_intersects_watching(self):
        from core.research.oo_rank_panel import resolve_oo_rank_universe

        last = {
            "success": True,
            "rows": [
                {"code": "000001", "tier": "A"},
                {"code": "000002", "tier": "A"},
                {"code": "000003", "tier": "B"},
                {"code": "999999", "tier": "A"},
            ],
        }
        with patch(
            "core.watching.store.read_watching",
            return_value={"watchlist": ["000001", "000002", "000003", "000004"]},
        ), patch(
            "core.research.predictability_tiers.load_predictability_tiers_last",
            return_value=last,
        ):
            out = resolve_oo_rank_universe(watching_tier_a_only=True)
        self.assertTrue(out.get("success"), out)
        self.assertEqual(out.get("codes"), ["000001", "000002"])
        self.assertEqual(out.get("universe_source"), "watching_tier_a")
        self.assertEqual(out.get("n_tier_a"), 2)

    def test_resolve_tier_a_requires_last_report(self):
        from core.research.oo_rank_panel import resolve_oo_rank_universe

        with patch(
            "core.watching.store.read_watching",
            return_value={"watchlist": ["000001"] * 8},
        ), patch(
            "core.research.predictability_tiers.load_predictability_tiers_last",
            return_value=None,
        ):
            out = resolve_oo_rank_universe(watching_tier_a_only=True)
        self.assertFalse(out.get("success"))
        self.assertIn("分档", out.get("error") or "")


class TestOoRankCompactPanel(unittest.TestCase):
    def test_group_day_matrices_keeps_views_no_xs(self):
        from core.research.oo_rank_panel import group_day_matrices

        X = np.arange(16, dtype=np.float64).reshape(8, 2)
        y = np.asarray([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8])
        dates = ["2024-01-02"] * 4 + ["2024-01-03"] * 4
        codes = ["a", "b", "c", "d", "a", "b", "c", "d"]
        days = group_day_matrices(X, y, ["f0", "f1"], dates, codes, min_names=4)
        self.assertEqual(len(days), 2)
        self.assertNotIn("xs", days[0])
        self.assertEqual(days[0]["X"].shape, (4, 2))
        self.assertEqual(list(days[0]["codes"]), ["a", "b", "c", "d"])
        self.assertEqual(days[1]["date"], "2024-01-03")


class TestOoRankNdcg(unittest.TestCase):
    def test_ndcg_perfect_ranking_equals_one(self):
        from core.research.oo_rank_lambdarank import _ndcg_at_k

        ys = [float(i) for i in range(20)]
        pred = list(ys)  # 预测与真实同序
        nd = _ndcg_at_k(pred, ys, k=10)
        self.assertIsNotNone(nd)
        self.assertAlmostEqual(nd, 1.0, places=4)

    def test_ndcg_reverse_ranking_low(self):
        from core.research.oo_rank_lambdarank import _ndcg_at_k

        ys = [float(i) for i in range(20)]
        pred = [-y for y in ys]  # 完全反序
        nd = _ndcg_at_k(pred, ys, k=10)
        self.assertIsNotNone(nd)
        self.assertLess(nd, 0.5)

    def test_ndcg_returns_none_for_too_few(self):
        from core.research.oo_rank_lambdarank import _ndcg_at_k

        self.assertIsNone(_ndcg_at_k([1.0], [1.0], k=10))


def _synth_varying_pool_days(seed: int = 0):
    """每日池大小不一（22~55），专门复现 LightGBM LambdaRank label 映射 bug。

    旧版默认 label_gain=[0,1,3,...,2^30-1] 仅 31 项，label>=31 即抛
    "Label X is not less than the number of label mappings (31)"。
    """
    from datetime import date, timedelta

    pool_sizes = [31, 41, 55, 33, 28, 47, 39, 50, 22, 44,
                  36, 52, 30, 25, 49, 38, 42, 29, 35, 46]
    days = []
    day = date(2024, 1, 2)
    for n in pool_sizes:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        xs, ys, codes = [], [], []
        for i in range(n):
            sig = (i - (n - 1) / 2.0) / float(n)
            noise = (((i + 1) * (seed + 3) * 17) % 11) / 100.0 - 0.05
            ys.append(sig * 8.0 + noise * 3.0)
            xs.append({
                "mom3": 50.0 + sig * 40.0,
                "volume_ratio": 50.0 + sig * 20.0,
            })
            codes.append(f"{i:04d}")
        days.append(_pack_day(day.isoformat(), codes, xs, ys))
        day += timedelta(days=1)
    return days


class TestOoRankVaryingPool(unittest.TestCase):
    """回归测试：日池大小不齐时 LambdaRank 不应崩在 label 映射。"""

    @unittest.skipUnless(_has_lightgbm(), "lightgbm 未安装")
    def test_fit_lambdarank_varying_pool_sizes(self):
        from core.research.oo_rank_lambdarank import fit_lambdarank

        days = _synth_varying_pool_days()
        max_label = max(len(d["ys"]) for d in days) - 1
        self.assertGreater(max_label, 31, "测试前提：必须复现 label>=31")
        fit = fit_lambdarank(
            days,
            n_estimators=40,
            max_depth=3,
            learning_rate=0.08,
            subsample=1.0,
            min_child_samples=2,
        )
        self.assertTrue(fit.get("success"), fit)
        self.assertEqual(fit.get("n_groups"), len(days))
        self.assertNotIn("n_pair_days", fit)



class TestOoRankRidgeBaseline(unittest.TestCase):
    def test_oos_scores_ridge_coefficients(self):
        from core.research.oo_rank_lambdarank import _fit_ridge_baseline, _oos_day_metrics

        days = _synth_days(12, 8)
        names = ["mom3", "volume_ratio", "vol_penalty_score"]
        ridge = _fit_ridge_baseline(days[:8], feature_names=names, ridge_lambda=1.0)
        self.assertTrue(ridge.get("success"), ridge)
        metrics = _oos_day_metrics(days[8:], ridge, topk_track=3, ndcg_k=3)
        self.assertGreater(int(metrics.get("n_days") or 0), 0, metrics)
        self.assertIsNotNone(metrics.get("spearman"))
        self.assertIsNotNone(metrics.get("ndcg_at_k"))
        self.assertIsNotNone(metrics.get("topk_overlap"))
        self.assertIsNotNone(metrics.get("topk_mean_y_oo"))


class TestOoRankFit(unittest.TestCase):
    @unittest.skipUnless(_has_lightgbm(), "lightgbm 未安装")
    def test_fit_report_vs_ridge_shadow(self):
        from core.research.oo_rank_lambdarank import (
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
            stock_bars = [{"code": "000001", "bars": [{"date": d["date"]} for d in days]}]
            report = fit_oo_rank_report(
                stock_bars,
                holdout_trading_days=8,
                topk_track=5,
                ndcg_k=5,
                persist=False,
            )
        self.assertTrue(report.get("success"), report)
        self.assertEqual(report.get("schema"), "oo_rank_v1")
        self.assertEqual(report.get("backend"), "lambdarank")
        self.assertIn("oo_rank", (report.get("oos") or {}))
        self.assertIn("ridge_oo_baseline", (report.get("oos") or {}))
        oos_rank = (report.get("oos") or {}).get("oo_rank") or {}
        self.assertIn("ndcg_at_k", oos_rank)
        self.assertEqual(oos_rank.get("ndcg_k"), 5)
        oos_ridge = (report.get("oos") or {}).get("ridge_oo_baseline") or {}
        self.assertTrue(oos_ridge.get("success"), oos_ridge)
        self.assertGreater(int(oos_ridge.get("n_days") or 0), 0)
        self.assertIsNotNone(oos_ridge.get("spearman"))
        self.assertIsNotNone(oos_ridge.get("ndcg_at_k"))
        model = report.get("return_model") or {}
        self.assertTrue(model.get("shadow_only"))
        self.assertEqual(float(model.get("intercept") or 0), 0.0)
        self.assertTrue(report.get("fitted_at"))
        self.assertEqual(model.get("fitted_at"), report.get("fitted_at"))

        with patch(
            "core.research.oo_rank_panel.build_oo_rank_day_panels",
            side_effect=_fake_panels,
        ):
            track = compare_oo_rank_shadow_track(
                stock_bars,
                holdout_trading_days=8,
                topk_track=5,
                ndcg_k=5,
            )
        self.assertTrue(track.get("success"), track)
        self.assertIn("delta_topk_mean_y_oo", track)
        self.assertIn("delta_ndcg_at_k", track)

    @unittest.skipUnless(_has_lightgbm(), "lightgbm 未安装")
    def test_apply_and_aux_passthrough(self):
        from core.paper.rebalance.rank_lots import AUX_YHAT_KEYS, aux_yhat_fields
        from core.research.oo_rank_lambdarank import (
            apply_oo_rank_scores,
            fit_lambdarank,
        )

        self.assertIn("y_oo_rank", AUX_YHAT_KEYS)
        days = _synth_days(20, 16)
        fit = fit_lambdarank(
            days,
            n_estimators=40,
            max_depth=3,
            learning_rate=0.08,
            subsample=1.0,
            min_child_samples=2,
        )
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
        self.assertEqual(item.get("y_oo_rank"), 1)
        self.assertEqual(item.get("y_oo_rank_n"), 1)
        self.assertIsNotNone(item.get("y_oo_rank_score"))
        extra = aux_yhat_fields(item, include_tau_horizons=False)
        self.assertEqual(extra.get("y_oo_rank"), 1)
        self.assertEqual(extra.get("y_oo_rank_n"), 1)

    def test_assign_oo_rank_day_ranks(self):
        from core.research.oo_rank_lambdarank import assign_oo_rank_day_ranks

        items = [
            {"stock_code": "600002", "y_oo_rank": 0.1},
            {"stock_code": "600000", "y_oo_rank": 1.5},
            {"stock_code": "600001", "y_oo_rank": 1.5},
        ]
        assign_oo_rank_day_ranks(items)
        by = {it["stock_code"]: it for it in items}
        self.assertEqual(by["600000"]["y_oo_rank"], 1)  # 同分 code 更小优先
        self.assertEqual(by["600001"]["y_oo_rank"], 2)
        self.assertEqual(by["600002"]["y_oo_rank"], 3)
        self.assertEqual(by["600000"]["y_oo_rank_n"], 3)
        self.assertAlmostEqual(by["600000"]["y_oo_rank_score"], 1.5)

    def test_linear_coefficients_only_model_rejected(self):
        """旧线性 coefficients 包不再打分（gain 误当 β 会错）。"""
        from core.research.oo_rank_lambdarank import (
            _resolve_oo_rank_fit,
            predict_oo_rank_from_features,
        )

        legacy = {
            "solver": "ridge",
            "coefficients": {"mom3": 0.5, "volume_ratio": 0.2},
            "intercept": 0.0,
        }
        self.assertIsNone(_resolve_oo_rank_fit(fit=legacy))
        self.assertIsNone(
            predict_oo_rank_from_features({"mom3": 70.0}, fit=legacy)
        )

    @unittest.skipUnless(_has_lightgbm(), "lightgbm 未安装")
    def test_persist_research_sidecar(self):
        from core.research.holdout import research_model_path
        from core.research.oo_rank_lambdarank import (
            fit_lambdarank,
            load_oo_rank_model,
            persist_oo_rank_model,
        )

        days = _synth_days(25, 18)
        fit = fit_lambdarank(
            days,
            n_estimators=40,
            max_depth=3,
            learning_rate=0.08,
            subsample=1.0,
            min_child_samples=2,
        )
        report = {
            "success": True,
            "task": "oo_rank",
            "return_model": dict(fit, shadow_only=True, model_role="live"),
            "return_model_research": dict(fit, shadow_only=True, model_role="research"),
            "oos": {},
            "y_spec": {"formula": "rank"},
            "note": "test",
        }
        with tempfile.TemporaryDirectory() as td:
            live = os.path.join(td, "oo_rank_model.json")
            with patch(
                "core.research.oo_rank_lambdarank.oo_rank_model_path",
                return_value=live,
            ), patch(
                "core.research.oo_rank_lambdarank.oo_rank_last_report_path",
                return_value=os.path.join(td, "last.json"),
            ), patch(
                "core.research.oo_rank_lambdarank.oo_rank_model_path_legacy",
                return_value=os.path.join(td, "legacy_model.json"),
            ):
                paths = persist_oo_rank_model(report, also_research=True)
                self.assertTrue(os.path.isfile(live))
                self.assertTrue(os.path.isfile(research_model_path(live)))
                doc = load_oo_rank_model(prefer_research=True)
                self.assertIsNotNone(doc)
                self.assertIn("coefficients", (doc or {}).get("return_model") or {})
                self.assertTrue((doc or {}).get("fitted_at") or ((doc or {}).get("return_model") or {}).get("fitted_at"))
                self.assertEqual((doc or {}).get("schema"), "oo_rank_v1")
                self.assertEqual((doc or {}).get("task"), "oo_rank")

    def test_load_falls_back_to_legacy_pairwise_paths(self):
        import json

        from core.research.oo_rank_lambdarank import (
            load_oo_rank_last_report,
            load_oo_rank_model,
        )

        doc = {
            "success": True,
            "schema": "oo_rank_pairwise_v1",
            "task": "oo_rank_pairwise",
            "return_model": {"coefficients": {"mom3": 1.0}, "solver": "lambdarank"},
        }
        with tempfile.TemporaryDirectory() as td:
            new_live = os.path.join(td, "oo_rank_model.json")
            legacy_live = os.path.join(td, "oo_rank_pairwise_model.json")
            new_last = os.path.join(td, "oo_rank_last_report.json")
            legacy_last = os.path.join(td, "oo_rank_pairwise_last_report.json")
            with open(legacy_live, "w", encoding="utf-8") as f:
                json.dump(doc, f)
            with open(legacy_last, "w", encoding="utf-8") as f:
                json.dump(doc, f)
            with patch(
                "core.research.oo_rank_lambdarank.oo_rank_model_path",
                return_value=new_live,
            ), patch(
                "core.research.oo_rank_lambdarank.oo_rank_model_path_legacy",
                return_value=legacy_live,
            ), patch(
                "core.research.oo_rank_lambdarank.oo_rank_last_report_path",
                return_value=new_last,
            ), patch(
                "core.research.oo_rank_lambdarank.oo_rank_last_report_path_legacy",
                return_value=legacy_last,
            ):
                loaded = load_oo_rank_model(prefer_research=False)
                last = load_oo_rank_last_report()
            self.assertIsNotNone(loaded)
            self.assertEqual((loaded or {}).get("schema"), "oo_rank_pairwise_v1")
            self.assertIsNotNone(last)
            self.assertEqual((last or {}).get("task"), "oo_rank_pairwise")


class TestOoRankReport(unittest.TestCase):
    @unittest.skipUnless(_has_lightgbm(), "lightgbm 未安装")
    def test_apply_batch_writes_hats(self):
        """apply_oo_rank_scores 用原始 sub_scores 批打分（与 ŷ_oo 同口径）。"""
        from core.research.oo_rank_lambdarank import (
            apply_oo_rank_scores,
            fit_lambdarank,
        )

        days = _synth_days(20, 16)
        fit = fit_lambdarank(
            days,
            n_estimators=40,
            max_depth=3,
            learning_rate=0.08,
            subsample=1.0,
            min_child_samples=2,
        )
        self.assertTrue(fit.get("success"), fit)
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
        ranks = [int(it["y_oo_rank"]) for it in items]
        self.assertEqual(sorted(ranks), list(range(1, 13)))
        self.assertTrue(all(it.get("y_oo_rank_score") is not None for it in items))
        self.assertEqual(items[0].get("y_oo_rank_n"), 12)
        # 原始相对分 / 名次随 mom3 同向（1=最高）；不强求完美反序（小树偶有局部扰动）
        self.assertGreater(
            float(items[-1]["y_oo_rank_score"]), float(items[0]["y_oo_rank_score"])
        )
        self.assertLess(ranks[-1], ranks[0])
        spearman = float(np.corrcoef([40.0 + i for i in range(12)], [-r for r in ranks])[0, 1])
        self.assertGreater(spearman, 0.7)

    @unittest.skipUnless(_has_lightgbm(), "lightgbm 未安装")
    def test_fit_report_records_n_features(self):
        from core.research.oo_rank_lambdarank import fit_oo_rank_report

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
                topk_track=5,
                ndcg_k=5,
                persist=False,
            )
        self.assertTrue(report.get("success"), report)
        self.assertNotIn("feature_mode", report)
        self.assertNotIn("feature_meta", report)
        self.assertEqual(report.get("backend"), "lambdarank")
        self.assertEqual(report.get("ndcg_k"), 5)
        self.assertGreater(int(report.get("n_features") or 0), 0)
        self.assertNotIn("feature_mode", report.get("oos") or {})
        self.assertTrue(report.get("fitted_at"))


class TestOoRankFittedAt(unittest.TestCase):
    def test_save_and_persist_stamp_fitted_at(self):
        import json

        from core.research.oo_rank_lambdarank import (
            persist_oo_rank_model,
            save_oo_rank_last_report,
            stamp_oo_rank_fitted_at,
        )

        skipped = stamp_oo_rank_fitted_at({"success": False})
        self.assertIsNone(skipped.get("fitted_at"))

        report = {
            "success": True,
            "return_model": {"coefficients": {"mom3": 1.0}, "model_role": "live"},
            "return_model_research": {
                "coefficients": {"mom3": 1.0},
                "model_role": "research",
            },
        }
        stamp_oo_rank_fitted_at(report)
        self.assertTrue(report.get("fitted_at"))
        self.assertEqual(
            report["return_model"].get("fitted_at"), report["fitted_at"]
        )
        first = report["fitted_at"]
        stamp_oo_rank_fitted_at(report)
        self.assertEqual(report["fitted_at"], first)

        with tempfile.TemporaryDirectory() as td:
            live = os.path.join(td, "oo_rank_model.json")
            last = os.path.join(td, "oo_rank_last_report.json")
            with patch(
                "core.research.oo_rank_lambdarank.oo_rank_model_path",
                return_value=live,
            ), patch(
                "core.research.oo_rank_lambdarank.oo_rank_last_report_path",
                return_value=last,
            ):
                save_oo_rank_last_report(report)
                persist_oo_rank_model(report, also_research=True)
            with open(live, encoding="utf-8") as f:
                doc = json.load(f)
            with open(last, encoding="utf-8") as f:
                last_doc = json.load(f)
            self.assertEqual(doc.get("fitted_at"), first)
            self.assertEqual(last_doc.get("fitted_at"), first)


    def test_get_model_restores_last_report_without_persist(self):
        import json

        from quant.services.quant_service import QuantService

        last = {
            "success": True,
            "return_model": {
                "coefficients": {"mom3": 1.0},
                "fitted_at": "2026-10-06T01:00:00Z",
            },
            "oos": {"oo_rank": {"spearman": 0.12}, "ridge_oo_baseline": {}},
            "fitted_at": "2026-10-06T01:00:00Z",
            "sample_count": 80,
        }
        with tempfile.TemporaryDirectory() as td:
            live = os.path.join(td, "oo_rank_model.json")
            last_path = os.path.join(td, "oo_rank_last_report.json")
            legacy_live = os.path.join(td, "oo_rank_pairwise_model.json")
            legacy_last = os.path.join(td, "oo_rank_pairwise_last_report.json")
            with open(last_path, "w", encoding="utf-8") as f:
                json.dump(last, f)
            with patch(
                "core.research.oo_rank_lambdarank.oo_rank_model_path",
                return_value=live,
            ), patch(
                "core.research.oo_rank_lambdarank.oo_rank_last_report_path",
                return_value=last_path,
            ), patch(
                "core.research.oo_rank_lambdarank.oo_rank_model_path_legacy",
                return_value=legacy_live,
            ), patch(
                "core.research.oo_rank_lambdarank.oo_rank_last_report_path_legacy",
                return_value=legacy_last,
            ), patch(
                "core.research.oo_rank_lambdarank.load_oo_rank_model",
                return_value=None,
            ):
                out = QuantService().get_oo_rank_model()
            self.assertTrue(out.get("exists"), out)
            self.assertTrue(out.get("shadow"))
            self.assertTrue(out.get("from_last_report"))
            self.assertTrue(out.get("last_report_exists"))
            self.assertEqual(
                ((out.get("oos") or {}).get("oo_rank") or {}).get("spearman"),
                0.12,
            )
            self.assertEqual(out.get("fitted_at"), "2026-10-06T01:00:00Z")

        empty_dir = tempfile.TemporaryDirectory()
        with empty_dir as td:
            live = os.path.join(td, "oo_rank_model.json")
            last_path = os.path.join(td, "oo_rank_last_report.json")
            legacy_live = os.path.join(td, "oo_rank_pairwise_model.json")
            legacy_last = os.path.join(td, "oo_rank_pairwise_last_report.json")
            with patch(
                "core.research.oo_rank_lambdarank.oo_rank_model_path",
                return_value=live,
            ), patch(
                "core.research.oo_rank_lambdarank.oo_rank_last_report_path",
                return_value=last_path,
            ), patch(
                "core.research.oo_rank_lambdarank.oo_rank_model_path_legacy",
                return_value=legacy_live,
            ), patch(
                "core.research.oo_rank_lambdarank.oo_rank_last_report_path_legacy",
                return_value=legacy_last,
            ), patch(
                "core.research.oo_rank_lambdarank.load_oo_rank_model",
                return_value=None,
            ):
                missing = QuantService().get_oo_rank_model()
            self.assertFalse(missing.get("exists"))
            self.assertFalse(missing.get("last_report_exists"))


class TestOoRankPersistSkipsRefit(unittest.TestCase):
    def test_persist_writes_last_report_without_refit(self):
        from quant.services.quant_service import QuantService

        last = {
            "success": True,
            "return_model": {
                "coefficients": {"mom3": 1.0},
                "solver": "lambdarank",
                "booster_b64": "x",
                "model_role": "live",
            },
            "oos": {"oo_rank": {"spearman": 0.2}},
            "fitted_at": "2026-10-06T17:00:00Z",
        }
        with patch(
            "core.research.oo_rank_lambdarank.load_oo_rank_last_report",
            return_value=last,
        ), patch(
            "core.research.oo_rank_lambdarank.persist_oo_rank_model",
            return_value={"live": "/tmp/oo_rank_model.json"},
        ) as persist, patch(
            "core.research.oo_rank_lambdarank.fit_oo_rank_report",
        ) as fit, patch(
            "core.research.oo_rank_panel.resolve_oo_rank_universe",
        ) as universe, patch(
            "core.research.task.finish_research_run",
            return_value=None,
        ):
            out = QuantService().run_oo_rank_experiment(
                persist=True, note="ui oo_rank shadow persist"
            )
        persist.assert_called_once()
        fit.assert_not_called()
        universe.assert_not_called()
        self.assertTrue(out.get("success"))
        self.assertTrue(out.get("from_last_report"))
        self.assertEqual(out.get("fitted_at"), "2026-10-06T17:00:00Z")
        self.assertTrue((out.get("persisted") or {}).get("success"))
        self.assertEqual(out.get("api_note"), "ui oo_rank shadow persist")

    def test_persist_without_last_report_fails_fast(self):
        from quant.services.quant_service import QuantService

        with patch(
            "core.research.oo_rank_lambdarank.load_oo_rank_last_report",
            return_value=None,
        ), patch(
            "core.research.oo_rank_lambdarank.fit_oo_rank_report",
        ) as fit, patch(
            "core.research.oo_rank_panel.resolve_oo_rank_universe",
        ) as universe, patch(
            "core.research.task.finish_research_run",
            return_value=None,
        ):
            out = QuantService().run_oo_rank_experiment(persist=True)
        fit.assert_not_called()
        universe.assert_not_called()
        self.assertFalse(out.get("success"))
        self.assertIn("请先点拟合", str(out.get("error") or ""))


if __name__ == "__main__":
    unittest.main()
