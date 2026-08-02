"""OLS β 聚类 → 组内共用权草案（研究探针）。"""

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.mock_context import rising_bars
from quant.research.factor_ols_clusters import (
    OUTLIER_LABEL,
    agglomerative_cut_by_tau,
    agglomerative_labels,
    apply_beta_scale_transform,
    beta_delta_mismatch,
    clamp_n_clusters,
    cluster_beta_vectors,
    cluster_diameter,
    compute_factor_ols_cluster_report,
    eject_by_group_beta_delta,
    eject_far_from_group_beta,
    fit_beta_scale_transform,
    group_ts_ic_panel,
    kmeans_labels,
    merge_cluster_universe,
    promote_outliers_to_singleton_clusters,
    refine_cluster_labels,
    resolve_beta_scale,
    scale_beta_matrix,
    within_dist_tau,
)
import numpy as np


def _bars_variant(n: int, scale: float, wobble: float) -> list:
    bars = rising_bars(n)
    for i, row in enumerate(bars):
        row["close"] = float(row["close"]) * scale * (1.0 + wobble * ((i % 5) - 2))
        row["volume"] = float(row.get("volume") or 1e6) * (1.0 + 0.01 * (i % 3))
    return bars


class TestFactorOlsClusters(unittest.TestCase):
    def test_clamp_n_clusters(self):
        self.assertEqual(clamp_n_clusters(3), 3)
        self.assertEqual(clamp_n_clusters(1), 2)
        self.assertEqual(clamp_n_clusters(99), 12)

    def test_merge_cluster_universe_holdings_only(self):
        watch = ["A", "B", "C", "D", "E"]
        holdings = [
            {"stock_code": "C"},
            {"stock_code": "H1"},
            {"stock_code": "H2"},
        ]
        out = merge_cluster_universe(
            watch, holdings, watching_limit=3, universe_mode="holdings"
        )
        self.assertEqual(out["universe_mode"], "holdings")
        self.assertEqual(out["watching_codes"], [])
        self.assertEqual(out["codes"], ["C", "H1", "H2"])
        self.assertEqual(out["universe_count"], 3)
        # 兼容旧并集
        uni = merge_cluster_universe(
            watch, holdings, watching_limit=3, universe_mode="union"
        )
        self.assertEqual(uni["codes"], ["A", "B", "C", "H1", "H2"])

    def test_promote_outliers_to_singleton_clusters(self):
        labels = np.array([0, 0, -1, 1, -1], dtype=int)
        out, promoted = promote_outliers_to_singleton_clusters(labels)
        self.assertEqual(promoted, [2, 4])
        self.assertEqual(int(out[2]), 2)
        self.assertEqual(int(out[4]), 3)
        self.assertEqual(int(out[0]), 0)
        self.assertTrue(all(int(v) >= 0 for v in out))

    def test_kmeans_separates_two_clouds(self):
        a = np.tile(np.array([1.0, 0.0]), (4, 1))
        b = np.tile(np.array([0.0, 1.0]), (4, 1))
        x = np.vstack([a, b])
        labels, _centers = kmeans_labels(x, n_clusters=2, seed=0)
        self.assertEqual(len(labels), 8)
        self.assertEqual(len(set(int(v) for v in labels)), 2)

    def test_complete_linkage_separates_two_clouds(self):
        a = np.tile(np.array([1.0, 0.0]), (4, 1))
        b = np.tile(np.array([0.0, 1.0]), (4, 1))
        x = np.vstack([a, b])
        labels, _centers = agglomerative_labels(
            x, n_clusters=2, linkage="complete"
        )
        self.assertEqual(len(set(int(v) for v in labels)), 2)
        self.assertEqual(sorted(int(np.sum(labels == c)) for c in (0, 1)), [4, 4])

    def test_tau_cut_two_clouds_plus_outlier(self):
        """两紧团 + 一远点 → 2 组 + 1 离群；组内直径 ≤ τ。"""
        rng0 = np.random.RandomState(0)
        rng1 = np.random.RandomState(1)
        a = np.tile(np.array([0.0, 0.0]), (5, 1)) + rng0.normal(0, 0.02, (5, 2))
        b = np.tile(np.array([2.0, 0.0]), (5, 1)) + rng1.normal(0, 0.02, (5, 2))
        outlier = np.array([[20.0, 20.0]])
        x = np.vstack([a, b, outlier])
        tau = within_dist_tau(x, 0.35)
        labels, _ = agglomerative_cut_by_tau(x, tau=tau, linkage="complete")
        # 切树后离群应仍是单票
        self.assertEqual(int(np.sum(labels == labels[-1])), 1)

        out = cluster_beta_vectors(
            x,
            method="hierarchical",
            n_clusters=None,
            cluster_linkage="complete",
            within_dist_quantile=0.25,
        )
        self.assertTrue(out["cut_by_tau"])
        self.assertEqual(out["cluster_linkage"], "complete")
        self.assertGreaterEqual(out["n_outliers"], 1)
        self.assertIn(10, out["outlier_indices"])
        # 至少两组、远点不入簇；入簇组直径 ≤ τ
        self.assertGreaterEqual(out["n_clusters"], 2)
        self.assertEqual(int(out["labels"][10]), OUTLIER_LABEL)
        for c in range(out["n_clusters"]):
            idx = [i for i, lab in enumerate(out["labels"]) if int(lab) == c]
            self.assertGreaterEqual(len(idx), 2)
            self.assertLessEqual(
                cluster_diameter(x, idx), float(out["within_dist_cap"]) + 1e-6
            )

    def test_tight_tau_yields_multiple_groups_on_grid(self):
        """分散点在紧 τ 下组数 > 1（相对塌成 1 团）。"""
        pts = []
        for i in range(4):
            for j in range(3):
                pts.append([float(i) * 1.5, float(j) * 1.5])
        x = np.asarray(pts, dtype=float)
        out = cluster_beta_vectors(
            x,
            method="hierarchical",
            n_clusters=None,
            cluster_linkage="complete",
            within_dist_quantile=0.25,
        )
        self.assertGreater(out["n_clusters"], 1)
        self.assertTrue(out["cut_by_tau"])

    def test_eject_far_from_group_beta(self):
        # 两近点 + 一点远：组β取近点中心时远点应被踢
        x = np.array(
            [[0.0, 0.0], [0.1, 0.0], [3.0, 0.0]],
            dtype=float,
        )
        labels = np.array([0, 0, 0], dtype=int)
        g = np.array([0.05, 0.0], dtype=float)
        out_lab, ejects = eject_far_from_group_beta(
            x, labels, group_beta_scaled_by_cluster={0: g}, tau=0.5
        )
        self.assertEqual(int(out_lab[2]), OUTLIER_LABEL)
        self.assertEqual(int(out_lab[0]), 0)
        self.assertTrue(any(e.get("reason") == "far_from_group_beta" for e in ejects))
        scaled, tf = fit_beta_scale_transform(
            np.array([[1.0, 0.0], [2.0, 0.0], [3.0, 1.0]]), "feature_zscore"
        )
        self.assertEqual(scaled.shape, (3, 2))
        v = apply_beta_scale_transform(np.array([2.0, 0.0]), tf)
        self.assertEqual(v.shape, (2,))

    def test_delta_beta_mismatch_like_probe(self):
        # 模拟 002594 vs G1：动量/波动 Δβ 很大 → 应 reject
        member = np.array([0.0054, -0.6349, -0.2626, 0.18], dtype=float)
        group = np.array([0.5934, 0.0240, 0.1578, -0.12], dtype=float)
        mask = np.array([True, True, True, True])
        st = beta_delta_mismatch(member, group, active_mask=mask)
        self.assertTrue(st["reject"])
        self.assertGreaterEqual(st["max_abs_delta"], 0.4)
        labels = np.array([0, 0], dtype=int)
        raw = np.vstack([member, group])
        out, ej = eject_by_group_beta_delta(
            raw,
            labels,
            group_raw_by_cluster={0: group},
            active_mask_by_cluster={0: mask},
        )
        self.assertEqual(int(out[0]), OUTLIER_LABEL)
        self.assertTrue(any("delta_beta" in str(e.get("reason")) for e in ej))

    def test_refine_ejects_far_singleton(self):
        main = np.random.RandomState(0).normal(0, 0.05, (21, 3))
        outlier = np.array([[8.0, 8.0, 8.0]])
        x = np.vstack([main, outlier])
        labels, _ = agglomerative_labels(x, n_clusters=2, linkage="complete")
        tau = within_dist_tau(x, 0.35)
        refined = refine_cluster_labels(x, labels, min_size=2, tau=tau)
        self.assertEqual(int(refined["labels"][-1]), OUTLIER_LABEL)
        self.assertGreaterEqual(refined["n_outliers"], 1)
        # 入簇点不得被硬并成「含离群的 22 人团」
        self.assertLess(refined["n_in_cluster"], 22)

    def test_cluster_report_groups_three_stocks(self):
        panels = [
            {"code": "600519", "bars": _bars_variant(50, 1.0, 0.002)},
            {"code": "000001", "bars": _bars_variant(50, 1.05, 0.003)},
            {"code": "601318", "bars": _bars_variant(50, 0.95, -0.004)},
        ]
        out = compute_factor_ols_cluster_report(
            panels,
            horizon_days=3,
            n_clusters=2,
            ridge_lambda=0.0,
            beta_scale="feature_zscore",
            cluster_method="hierarchical",
            cluster_linkage="complete",
        )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertEqual(out.get("task"), "factor_ols_clusters")
        self.assertEqual(out.get("mode"), "ols_beta_clusters")
        self.assertEqual(out.get("beta_scale"), "feature_zscore")
        self.assertEqual(out.get("cluster_method"), "hierarchical")
        self.assertEqual(out.get("cluster_linkage"), "complete")
        self.assertFalse(out.get("n_clusters_auto"))
        self.assertIn("feature_zscore", out.get("note") or "")
        self.assertGreaterEqual(out.get("stock_count"), 2)
        clusters = out.get("clusters") or []
        self.assertGreaterEqual(len(clusters), 1)
        member_total = sum(int(c.get("member_count") or 0) for c in clusters)
        self.assertEqual(member_total, out.get("stock_count"))
        self.assertIn("不写", out.get("note") or "")
        for cl in clusters:
            panel = cl.get("factor_ic_panel") or {}
            self.assertTrue(panel.get("success"), cl.get("label"))
            self.assertEqual(panel.get("mode"), "group_ts_ic")
            rows = panel.get("rows") or []
            self.assertGreater(len(rows), 0)
            self.assertTrue(any(r.get("sample_count") for r in rows))
            if not cl.get("singleton"):
                self.assertIn("max_within_dist", cl)

    def test_beta_scale_resolve_and_zscore(self):
        self.assertEqual(resolve_beta_scale("none"), "none")
        self.assertEqual(resolve_beta_scale(None, l2_normalize_betas=True), "l2")
        self.assertEqual(resolve_beta_scale("feature_zscore"), "feature_zscore")
        raw = np.array([[10.0, 0.0], [20.0, 0.0], [30.0, 1.0]], dtype=float)
        z = scale_beta_matrix(raw, "feature_zscore")
        self.assertAlmostEqual(float(z[:, 0].mean()), 0.0, places=6)
        self.assertAlmostEqual(float(z[:, 0].std()), 1.0, places=6)

    def test_group_ts_ic_panel_basic(self):
        xs = [{"momentum": float(i), "value": 1.0} for i in range(10)]
        ys = [float(i) * 0.5 for i in range(10)]
        out = group_ts_ic_panel(xs, ys, ["momentum", "value", "missing"])
        self.assertTrue(out["success"])
        by = {r["factor"]: r for r in out["rows"]}
        self.assertIsNotNone(by["momentum"]["ic"])
        self.assertEqual(by["momentum"]["sample_count"], 10)
        self.assertIsNone(by["momentum"]["icir"])
        self.assertEqual(by["missing"]["exclusion_reason"], "sparse")

    def test_cluster_needs_two_fits(self):
        out = compute_factor_ols_cluster_report(
            [{"code": "600519", "bars": rising_bars(50)}],
            horizon_days=3,
            n_clusters=3,
        )
        self.assertFalse(out.get("success"))

    def test_ui_has_clusters_button(self):
        path = os.path.join(ROOT, "web/static/partials/quant_panel.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("quant-ols-clusters-run", html)
        self.assertIn("跑分组", html)
        self.assertIn("quant-probe-fold", html)
        self.assertIn("纸面持仓", html)
        self.assertIn("同组同建模", html)
        self.assertIn("quant-global-fold", html)
        self.assertIn("全局对照 · 阈值 / 横截面", html)
        self.assertIn("探针 · 单票 vs 所在组", html)
        js_path = os.path.join(ROOT, "web/static/js/quant.js")
        with open(js_path, encoding="utf-8") as f:
            js = f.read()
        self.assertIn("IC·n日", js)
        self.assertIn("formatMemberChipsHtml", js)
        self.assertIn('quant-fold quant-cluster-group-fold', js)
        self.assertIn("probeStatusBadge", js)
        self.assertIn("is-scan-hot", js)
        self.assertIn("fmtOlsCell", js)
        self.assertIn("quant-cell-empty", js)
        self.assertIn("watching-react-grid quant-research-grid", js)
        self.assertNotIn("① 花名册", js)

    def test_api_clusters_mocked(self):
        try:
            from fastapi.testclient import TestClient
            from web import app as web_app
            from web import deps
        except Exception:
            self.skipTest("fastapi not installed")

        fake = {
            "success": True,
            "task": "factor_ols_clusters",
            "mode": "ols_beta_clusters",
            "n_clusters": 2,
            "stock_count": 3,
            "clusters": [],
            "note": "mock",
            "oos_summary": {"run": True, "passed": 1, "failed": 0, "skipped": 1},
        }
        client = TestClient(web_app.app)
        with patch.object(
            deps.quant, "run_factor_ols_cluster_experiment", return_value=fake
        ):
            res = client.post(
                "/api/quant/factor-ols-clusters",
                json={
                    "lookback": 80,
                    "horizon_days": 3,
                    "watching_limit": 8,
                    "n_clusters": 2,
                    "cluster_linkage": "complete",
                    "within_dist_quantile": 0.35,
                    "run_oos_gate": True,
                },
            )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json().get("success"))

    def test_attach_cluster_oos_gates(self):
        from quant.research.cluster_oos import attach_cluster_oos_gates

        report = {
            "success": True,
            "note": "base",
            "clusters": [
                {
                    "label": "G1",
                    "members": ["600519", "000001"],
                    "singleton": False,
                    "weight_suggest": {
                        "success": True,
                        "current_weights": {"momentum": 0.2},
                        "suggested_weights": {"momentum": 0.22},
                    },
                },
                {
                    "label": "G2",
                    "members": ["601318"],
                    "singleton": True,
                    "weight_suggest": {"success": True},
                },
            ],
        }
        fake_gate = {
            "ok": True,
            "passed": True,
            "skipped": False,
            "reason": "oos_not_worse",
            "delta_oos_pp": 0.5,
            "note": "ok",
        }
        with patch(
            "core.signal.weight_oos_gate.evaluate_weight_suggestion_oos",
            return_value=fake_gate,
        ):
            out = attach_cluster_oos_gates(report, run_oos_gate=True)
        self.assertTrue(out["oos_summary"]["run"])
        self.assertEqual(out["oos_summary"]["passed"], 1)
        self.assertEqual(out["oos_summary"]["skipped"], 1)
        self.assertTrue(out["clusters"][0]["oos_gate"]["passed"])
        self.assertEqual(out["clusters"][0]["oos_gate"]["scope"], "cluster_members")
        self.assertTrue(out["clusters"][1]["oos_gate"]["skipped"])
        self.assertTrue(out["clusters"][0]["config_diff"]["success"])
        self.assertFalse(out["clusters"][0]["config_diff"]["promote_ready"])
        self.assertEqual(
            out["clusters"][0]["config_diff"]["export_kind"], "ols_cluster_weights"
        )
        self.assertIsNotNone(out.get("preferred_cluster"))
        self.assertEqual(out["preferred_cluster"]["label"], "G1")
        self.assertTrue(out["preferred_cluster"]["oos_passed"])


if __name__ == "__main__":
    unittest.main()
