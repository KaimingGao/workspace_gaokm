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
    group_cs_ic_panel,
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
        self.assertEqual(clamp_n_clusters(99), 99)

    def test_merge_cluster_universe_modes(self):
        watch = ["A", "B", "C", "D", "E"]
        holdings = [
            {"stock_code": "C"},
            {"stock_code": "H1"},
            {"stock_code": "H2"},
        ]
        # watching：观察池前 N（watching_limit）
        out = merge_cluster_universe(
            watch, holdings, watching_limit=3, universe_mode="watching"
        )
        self.assertEqual(out["universe_mode"], "watching")
        self.assertEqual(out["codes"], ["A", "B", "C"])
        self.assertEqual(out["watching_codes"], ["A", "B", "C"])
        self.assertEqual(out["holdings_codes"], ["C", "H1", "H2"])
        self.assertEqual(out["universe_count"], 3)
        self.assertEqual(out["watching_limit"], 3)
        # watching_all：全部观察池（旧行为）
        full = merge_cluster_universe(
            watch, holdings, watching_limit=3, universe_mode="watching_all"
        )
        self.assertEqual(full["codes"], ["A", "B", "C", "D", "E"])
        self.assertEqual(full["watching_limit"], 5)
        # 旧 holdings 模式
        hold = merge_cluster_universe(
            watch, holdings, watching_limit=3, universe_mode="holdings"
        )
        self.assertEqual(hold["universe_mode"], "holdings")
        self.assertEqual(hold["watching_codes"], [])
        self.assertEqual(hold["codes"], ["C", "H1", "H2"])
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
        """τ 切树 API：两紧团 + 一远点 → 远点保持单票。"""
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

    def test_auto_target_k_keeps_few_groups(self):
        """默认自动 k：约 n/5，且超大组会被二分。"""
        from quant.research.factor_ols_clusters import (
            default_max_cluster_size,
            default_n_clusters,
        )

        self.assertEqual(default_n_clusters(40), 8)
        self.assertEqual(default_n_clusters(20), 4)
        self.assertEqual(default_max_cluster_size(40, 8), 8)
        rng = np.random.RandomState(2)
        x = rng.normal(size=(40, 5))
        out = cluster_beta_vectors(
            x,
            method="hierarchical",
            n_clusters=None,
            cluster_linkage="average",
        )
        self.assertFalse(out["cut_by_tau"])
        self.assertTrue(out["auto_k"])
        self.assertEqual(out["target_k"], 8)
        self.assertEqual(out["n_outliers"], 0)
        self.assertGreaterEqual(out["n_clusters"], 6)
        self.assertLessEqual(out["n_clusters"], 14)
        sizes = [
            int(np.sum(out["labels"] == c))
            for c in sorted(set(int(v) for v in out["labels"]))
        ]
        self.assertTrue(sizes)
        self.assertLessEqual(max(sizes), int(out["max_cluster_size"] or 99))

    def test_manual_k_stays_near_target(self):
        """显式 K：切到目标 k，不因直径/组β踢出升成单票堆。"""
        rng = np.random.RandomState(7)
        # 两团高斯，夹杂噪声点；旧逻辑会踢出后升单票组
        a = rng.normal(size=(12, 5)) * 0.2
        b = rng.normal(size=(12, 5)) * 0.2 + 3.0
        noise = rng.normal(size=(6, 5)) * 2.5
        x = np.vstack([a, b, noise])
        out = cluster_beta_vectors(
            x,
            method="hierarchical",
            n_clusters=2,
            cluster_linkage="complete",
        )
        self.assertEqual(out["target_k"], 2)
        self.assertFalse(out["auto_k"])
        self.assertEqual(out["n_outliers"], 0)
        self.assertEqual(out["n_clusters"], 2)

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
            rows = panel.get("rows") or []
            self.assertGreater(len(rows), 0)
            if cl.get("singleton"):
                self.assertEqual(panel.get("mode"), "group_ts_ic")
                self.assertTrue(any(r.get("sample_count") for r in rows))
            else:
                self.assertEqual(panel.get("mode"), "group_cs_ic", cl.get("label"))
                self.assertIn("max_within_dist", cl)
                # 合成短序列可能日截面稀疏；只要走了组内截面口径即可
                self.assertIn("组内按日截面", panel.get("note") or "")

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

    def test_group_cs_ic_panel_basic(self):
        stock_bars = {
            f"S{i:02d}": _bars_variant(50, 1.0 + i * 0.02, 0.001 * (i + 1))
            for i in range(4)
        }
        out = group_cs_ic_panel(
            stock_bars,
            ["momentum", "volatility"],
            horizon_days=3,
            pit_fundamentals=False,
        )
        self.assertTrue(out["success"])
        self.assertEqual(out["mode"], "group_cs_ic")
        self.assertEqual(out["stock_count"], 4)
        rows = out.get("rows") or []
        self.assertGreaterEqual(len(rows), 1)
        self.assertTrue(
            any(r.get("ic") is not None or int(r.get("sample_count") or 0) >= 0 for r in rows)
        )

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
        self.assertIn("quant-cluster-k", html)
        self.assertIn("quant-probe-fold", html)
        self.assertIn("观察池", html)
        self.assertIn("同组同建模", html)
        self.assertIn("quant-global-fold", html)
        self.assertIn("watching-section-title\">对照</", html)
        self.assertIn("watching-section-title\">探针</", html)
        self.assertIn("watching-section-title\">日报</", html)
        self.assertIn("watching-section-title\">分组</", html)

        def _read(*parts: str) -> str:
            with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
                return f.read()

        js = _read("web/static/js/quant.js")
        ols = _read("web/static/js/quant/ols_ui.js")
        land = _read("web/static/js/quant/cluster_landing.js")
        probe = _read("web/static/js/quant/probe_ui.js")
        ic = _read("web/static/js/quant/factor_ic_ui.js")
        grid = _read("web/static/js/quant/research_grid.js")
        cluster = _read("web/static/js/quant/domain_cluster.js")

        self.assertIn("readClusterK", js)
        self.assertIn("createOlsUi", js)
        self.assertIn("probeIcFieldsFromRow", ols)
        self.assertIn("组ICIR", ols)
        self.assertIn("formatMemberChipsHtml", ols)
        self.assertIn("quant-fold quant-cluster-group-fold", ols)
        self.assertNotIn("quant-cluster-group-fold\" open", ols)
        self.assertNotIn("quant-cluster-group-fold' open", ols)
        self.assertIn("sample_fingerprint", ols)
        self.assertIn("y_spec", ols)
        self.assertIn("quant-cluster-landing", ols)
        self.assertIn("probeStatusBadge", probe)
        self.assertIn("is-scan-hot", ols)
        self.assertIn("fmtOlsCell", ic)
        self.assertIn("quant-cell-empty", ic)
        self.assertIn("watching-react-grid quant-research-grid", grid)
        self.assertIn("gridTemplateColumns", grid)
        self.assertIn("grid-template-columns", grid)
        self.assertIn("clusterLandingHtml", land)
        self.assertIn("① 对照", land)
        self.assertIn("live-refit", land)
        self.assertIn("live-refit", cluster)
        self.assertNotIn("① 花名册", land)

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
                    "sync": True,
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
                    "return_model": {
                        "coefficients": {"momentum": 0.6, "value": 0.4},
                        "intercept": 0.0,
                        "sample_count": 40,
                    },
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
            "core.signal.weight_oos_gate.evaluate_research_oos",
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
