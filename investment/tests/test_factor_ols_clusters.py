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
            auto_k_candidates,
            default_max_cluster_size,
            default_n_clusters,
        )

        self.assertEqual(default_n_clusters(40), 8)
        self.assertEqual(default_n_clusters(20), 4)
        self.assertEqual(auto_k_candidates(40), [7, 8, 9])
        self.assertEqual(auto_k_candidates(20), [4, 5])
        self.assertEqual(auto_k_candidates(3), [default_n_clusters(3)])
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

    def test_auto_postprocess_on_explicit_k(self):
        """显式 k + auto_postprocess：complete + 超大组二分（供邻域搜 k）。"""
        rng = np.random.RandomState(3)
        x = rng.normal(size=(30, 4))
        out = cluster_beta_vectors(
            x,
            method="hierarchical",
            n_clusters=6,
            cluster_linkage="average",
            auto_postprocess=True,
        )
        self.assertFalse(out["auto_k"])
        self.assertTrue(out["auto_postprocess"])
        self.assertEqual(out["target_k"], 6)
        self.assertEqual(out["cluster_linkage"], "complete")
        self.assertIsNotNone(out.get("max_cluster_size"))

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

    def test_delta_beta_relative_catches_small_factor(self):
        # 估值绝对差大 / 动量相对差大（β≈0）都应踢
        group = np.array([1.2, 0.010], dtype=float)
        ticket_b = np.array([1.70, 0.012], dtype=float)  # |Δ估值|=0.5
        ticket_c = np.array([1.10, 0.000], dtype=float)  # 动量暴露归零
        st_b = beta_delta_mismatch(ticket_b, group)
        st_c = beta_delta_mismatch(ticket_c, group)
        self.assertTrue(st_b["reject"])
        self.assertEqual(st_b.get("reason"), "max_abs_delta")
        self.assertTrue(st_c["reject"])
        self.assertEqual(st_c.get("reason"), "max_rel_delta")
        # 纯绝对阈值会漏掉票 C
        st_c_abs = beta_delta_mismatch(
            ticket_c, group, use_relative=False, max_abs_delta=0.40
        )
        self.assertFalse(st_c_abs["reject"])

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
        ks = out.get("k_selection") or {}
        self.assertEqual(ks.get("mode"), "manual")
        self.assertEqual(ks.get("chosen_k"), 2)
        self.assertEqual(ks.get("candidates"), [])
        self.assertEqual(ks.get("reason"), "n_clusters_explicit")
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

    def test_pick_best_k_selection_row(self):
        from quant.research.cluster_oos import pick_best_k_selection_row

        rows = [
            {"k": 4, "sort_key": (-1.2, 1, 0.1, 0.2)},
            {"k": 5, "sort_key": (-0.8, 2, -0.5, 0.1)},
            {"k": 6, "sort_key": (-0.5, 2, 0.3, 0.0)},
        ]
        best = pick_best_k_selection_row(rows)
        self.assertIsNotNone(best)
        self.assertEqual(best["k"], 6)

    def test_align_cluster_labels_to_previous(self):
        from quant.research.cluster_label_align import (
            align_cluster_labels,
            previous_code_cluster_ids,
        )

        # 旧：A,B→0(G1)，C,D→1(G2)；新标签对调成 A,B→1，C,D→0
        codes = ["A", "B", "C", "D"]
        prev = {"A": 0, "B": 0, "C": 1, "D": 1}
        new = np.array([1, 1, 0, 0], dtype=int)
        aligned, info = align_cluster_labels(codes, new, prev)
        self.assertTrue(info.get("aligned"), info)
        self.assertEqual(aligned.tolist(), [0, 0, 1, 1])
        self.assertGreaterEqual(float(info.get("stability") or 0), 0.99)

        active = {
            "code_map": {
                "A": {"cluster_id": 2, "cluster_label": "G3"},
                "B": {"cluster_label": "G3"},
            },
            "clusters": [{"cluster_id": 2, "label": "G3", "members": ["A", "B"]}],
        }
        prev2 = previous_code_cluster_ids(active)
        self.assertEqual(prev2.get("A"), 2)
        self.assertEqual(prev2.get("B"), 2)

        # 无重叠 → 不对齐
        aligned2, info2 = align_cluster_labels(codes, new, {})
        self.assertFalse(info2.get("aligned"))
        self.assertEqual(aligned2.tolist(), new.tolist())

    def test_soft_hetero_weights_and_weighted_ols(self):
        from quant.research.cluster_soft_hetero import (
            soft_hetero_member_weight,
            expand_member_weights_to_rows,
        )
        from core.research.factor_ols_fit import fit_factor_ols_from_panel

        self.assertAlmostEqual(soft_hetero_member_weight(0.0), 1.0, places=4)
        self.assertLess(soft_hetero_member_weight(0.5), soft_hetero_member_weight(0.1))
        self.assertGreaterEqual(soft_hetero_member_weight(10.0), 0.2)

        mw = {
            "A": {"weight": 1.0},
            "B": {"weight": 0.3},
        }
        sw = expand_member_weights_to_rows(["A", "A", "B", "B"], mw)
        self.assertEqual(sw, [1.0, 1.0, 0.3, 0.3])

        # 加权 OLS：高权行应主导斜率
        xs = [{"momentum": float(i)} for i in range(20)]
        ys = [float(i) for i in range(20)]
        # 前 10 行权重大且 y=x；后 10 行权重极低且 y 故意反号
        ys2 = [float(i) if i < 10 else -float(i) for i in range(20)]
        w = [1.0] * 10 + [0.01] * 10
        fit_w = fit_factor_ols_from_panel(
            xs,
            ys2,
            horizon_days=3,
            standardize=False,
            ridge_lambda=0.0,
            select_ridge=False,
            sample_weights=w,
        )
        self.assertTrue(fit_w.get("success"), fit_w.get("error"))
        self.assertTrue(fit_w.get("weighted_ols"))
        coef = float((fit_w.get("coefficients") or {}).get("momentum") or 0)
        self.assertGreater(coef, 0.5)

    def test_yhat_holdout_and_partition_loss(self):
        from quant.research.partition_loss import (
            compute_partition_loss,
            yhat_group_holdout_metrics,
            yhat_holdout_metrics,
        )

        rm = {
            "intercept": 0.0,
            "coefficients": {"momentum": 1.0},
            "z_means": {},
            "z_stds": {},
            "standardized": False,
        }
        xs = [{"momentum": float(i)} for i in range(20)]
        ys = [float(i) + 0.1 for i in range(20)]
        hold = yhat_holdout_metrics(rm, xs, ys, holdout_ratio=0.3)
        self.assertTrue(hold.get("ok"), hold)
        self.assertIsNotNone(hold.get("ic"))
        self.assertGreater(float(hold["ic"]), 0.9)

        # 有符号 IC：负相关应比正相关损失更大
        loss_pos = compute_partition_loss(
            groups=[{"member_count": 5, "pooled_r2": 0.5, "ic_mean": 0.3}],
            ic_use_abs=False,
        )
        loss_neg = compute_partition_loss(
            groups=[{"member_count": 5, "pooled_r2": 0.5, "ic_mean": -0.3}],
            ic_use_abs=False,
        )
        self.assertGreater(loss_neg["loss"], loss_pos["loss"])

        loss = compute_partition_loss(
            groups=[
                {"member_count": 5, "pooled_r2": 0.8, "ic_mean": 0.2},
                {"member_count": 5, "pooled_r2": 0.2, "ic_mean": 0.0},
            ],
            ic_use_abs=False,
        )
        self.assertLess(loss["loss"], 1e9)
        better = compute_partition_loss(
            groups=[
                {"member_count": 5, "pooled_r2": 0.9, "ic_mean": 0.3},
                {"member_count": 5, "pooled_r2": 0.85, "ic_mean": 0.25},
            ],
            ic_use_abs=False,
        )
        self.assertLess(better["loss"], loss["loss"])

        # 默认有符号 IC：|IC| 口径不得把反向组评得更好
        part_good = compute_partition_loss(
            groups=[
                {"member_count": 2, "pooled_r2": 0.15, "ic_mean": 0.04},
                {"member_count": 2, "pooled_r2": 0.12, "ic_mean": 0.03},
            ]
        )
        part_rev = compute_partition_loss(
            groups=[
                {"member_count": 2, "pooled_r2": 0.20, "ic_mean": -0.05},
                {"member_count": 2, "pooled_r2": 0.10, "ic_mean": 0.02},
            ]
        )
        self.assertLess(part_good["loss"], part_rev["loss"])
        self.assertFalse(part_good.get("ic_use_abs"))

        # 空壳多票组不得优于全拆单票
        shell = compute_partition_loss(
            groups=[
                {
                    "member_count": 10,
                    "pooled_r2": None,
                    "ic_mean": 0.0,
                    "has_return_model": False,
                    "fit_ok": False,
                }
            ]
        )
        all_sing = compute_partition_loss(
            groups=[{"member_count": 1} for _ in range(10)]
        )
        self.assertGreaterEqual(shell["loss"], all_sing["loss"] - 1e-9)
        self.assertGreater(shell.get("penalty_unusable", 0), 0)

        # 漏传 fit_ok 时会漏 unusable 罚（贪心旧路径）；带标志应 ≈2.5
        shell_silent = compute_partition_loss(
            groups=[{"member_count": 10, "pooled_r2": None, "ic_mean": 0.0}]
        )
        self.assertAlmostEqual(shell_silent["loss"], 1.0, places=4)
        self.assertEqual(shell_silent.get("penalty_unusable", 0), 0)
        self.assertAlmostEqual(shell["loss"], 2.5, places=4)

        # singleton_count=组数；penalty 用票数占比 singleton_share
        mixed = compute_partition_loss(
            groups=[
                {"member_count": 8, "pooled_r2": 0.2, "ic_mean": 0.05},
                {"member_count": 8, "pooled_r2": 0.2, "ic_mean": 0.05},
                {"member_count": 1},
                {"member_count": 1},
                {"member_count": 1},
                {"member_count": 1},
            ]
        )
        self.assertEqual(mixed["singleton_count"], 4)
        self.assertEqual(mixed["singleton_members"], 4)
        self.assertAlmostEqual(float(mixed["singleton_share"]), 0.2, places=4)
        self.assertAlmostEqual(float(mixed["penalty_singleton"]), 0.15, places=4)

        # 单票计入质量先验：踢成单票不应比显式计入差组更「好看」
        hide = compute_partition_loss(
            groups=[
                {"member_count": 3, "pooled_r2": 0.2, "ic_mean": 0.05},
                *[{"member_count": 1} for _ in range(7)],
            ]
        )
        hide_skip = compute_partition_loss(
            groups=[
                {"member_count": 3, "pooled_r2": 0.2, "ic_mean": 0.05},
                *[{"member_count": 1} for _ in range(7)],
            ],
            include_singleton_quality=False,
        )
        self.assertGreater(hide["loss"], hide_skip["loss"])

        # IC 量纲：典型 IC 差应能压过小幅 R² 优势（ic_ref_scale 生效）
        high_r2_neg_ic = compute_partition_loss(
            groups=[{"member_count": 8, "pooled_r2": 0.25, "ic_mean": -0.04}]
        )
        mid_r2_pos_ic = compute_partition_loss(
            groups=[{"member_count": 8, "pooled_r2": 0.15, "ic_mean": 0.04}]
        )
        self.assertLess(mid_r2_pos_ic["loss"], high_r2_neg_ic["loss"])
        self.assertAlmostEqual(float(mid_r2_pos_ic.get("ic_ref_scale") or 0), 0.05, places=4)

        # 组级：每票切尾；票顺序颠倒结果应一致
        panel_ab = {
            "AAA": {
                "xs": [{"momentum": float(i)} for i in range(20)],
                "ys": [float(i) for i in range(20)],
            },
            "BBB": {
                "xs": [{"momentum": float(i)} for i in range(20)],
                "ys": [0.0] * 20,
            },
        }
        hold_ab = yhat_group_holdout_metrics(
            ["AAA", "BBB"], panel_ab, return_model=rm, holdout_ratio=0.3
        )
        hold_ba = yhat_group_holdout_metrics(
            ["BBB", "AAA"], panel_ab, return_model=rm, holdout_ratio=0.3
        )
        self.assertTrue(hold_ab.get("ok"), hold_ab)
        self.assertEqual(hold_ab.get("ic"), hold_ba.get("ic"))
        self.assertEqual(hold_ab.get("n"), hold_ba.get("n"))

        # 前段重拟合：尾段评估用的 β 不应看尾段
        def _refit(tx, ty):
            # 故意拟合成 momentum→y 的近似单位斜率
            return {
                "intercept": 0.0,
                "coefficients": {"momentum": 1.0},
                "z_means": {},
                "z_stds": {},
                "standardized": False,
            }

        hold_refit = yhat_group_holdout_metrics(
            ["AAA"],
            panel_ab,
            return_model={"coefficients": {"momentum": -1.0}, "intercept": 0.0,
                          "standardized": False, "z_means": {}, "z_stds": {}},
            refit_fn=_refit,
            holdout_ratio=0.3,
        )
        self.assertTrue(hold_refit.get("refit"))
        self.assertTrue(hold_refit.get("ok"), hold_refit)
        self.assertGreater(float(hold_refit["ic"]), 0.9)

        # 有 refit_fn 但拟合失败：不得退回全样本坏模型
        hold_fail = yhat_group_holdout_metrics(
            ["AAA"],
            panel_ab,
            return_model=rm,
            refit_fn=lambda *_a, **_k: None,
            holdout_ratio=0.3,
        )
        self.assertFalse(hold_fail.get("ok"))
        self.assertEqual(hold_fail.get("reason"), "refit_failed")
        self.assertIsNone(hold_fail.get("ic"))

    def test_calendar_cut_holdout_and_train_window(self):
        """宇宙日历 cut_date：跨票对齐；缺日期退回按票比例。"""
        from quant.research.partition_loss import (
            collect_group_chrono_holdout,
            resolve_calendar_cut_date,
            split_panel_by_cut_date,
            yhat_group_holdout_metrics,
        )
        from quant.research.factor_ols_clusters import _stock_train_xy

        # 两票日历重叠：全局 cut 后各自都有足够前/尾段
        dates = [f"2024-01-{i:02d}" for i in range(1, 31)]
        panel = {
            "AAA": {
                "xs": [{"momentum": float(i)} for i in range(30)],
                "ys": [float(i) for i in range(30)],
                "dates": list(dates),
            },
            "BBB": {
                "xs": [{"momentum": float(i) * 0.5} for i in range(30)],
                "ys": [float(i) * 0.5 for i in range(30)],
                "dates": list(dates),
            },
        }
        cut = resolve_calendar_cut_date(panel, holdout_ratio=0.3)
        self.assertIsNotNone(cut)
        self.assertEqual(len(cut), 10)
        self.assertGreaterEqual(cut, dates[int(0.5 * len(dates))])
        self.assertLessEqual(cut, dates[int(0.85 * len(dates))])

        tx, ty, hx, hy = split_panel_by_cut_date(
            panel["AAA"]["xs"],
            panel["AAA"]["ys"],
            panel["AAA"]["dates"],
            cut_date=cut,
        )
        self.assertGreaterEqual(len(ty), 4)
        self.assertGreaterEqual(len(hy), 4)
        self.assertEqual(len(ty) + len(hy), 30)

        split = collect_group_chrono_holdout(
            ["AAA", "BBB"], panel, holdout_ratio=0.3, cut_date=cut
        )
        self.assertEqual(split.get("split_mode"), "calendar")
        self.assertEqual(split.get("cut_date"), cut)
        self.assertGreaterEqual(int(split["n_train"]), 8)
        self.assertGreaterEqual(int(split["n_hold"]), 8)

        train_xs, train_ys, used_full = _stock_train_xy(
            panel["AAA"], holdout_ratio=0.3, cut_date=cut
        )
        self.assertFalse(used_full)
        self.assertEqual(len(train_ys), len(ty))

        rm = {
            "intercept": 0.0,
            "coefficients": {"momentum": 1.0},
            "z_means": {},
            "z_stds": {},
            "standardized": False,
        }
        hold = yhat_group_holdout_metrics(
            ["AAA", "BBB"],
            panel,
            return_model=rm,
            holdout_ratio=0.3,
            cut_date=cut,
        )
        self.assertTrue(hold.get("ok"), hold)
        self.assertEqual(hold.get("split_mode"), "calendar")
        self.assertEqual(hold.get("cut_date"), cut)

        # 无 dates → 退回 per_stock_ratio
        bare = {
            "AAA": {"xs": panel["AAA"]["xs"], "ys": panel["AAA"]["ys"]},
            "BBB": {"xs": panel["BBB"]["xs"], "ys": panel["BBB"]["ys"]},
        }
        self.assertIsNone(resolve_calendar_cut_date(bare, holdout_ratio=0.3))
        split_ratio = collect_group_chrono_holdout(
            ["AAA", "BBB"], bare, holdout_ratio=0.3
        )
        self.assertEqual(split_ratio.get("split_mode"), "per_stock_ratio")

    def test_cluster_report_auto_k_selection(self):
        """auto 路径写入 k_selection，胜出 k 落在候选邻域内。"""
        from quant.research.factor_ols_clusters import auto_k_candidates

        panels = [
            {"code": "600519", "bars": _bars_variant(50, 1.0, 0.002)},
            {"code": "000001", "bars": _bars_variant(50, 1.05, 0.003)},
            {"code": "601318", "bars": _bars_variant(50, 0.95, -0.004)},
        ]
        out = compute_factor_ols_cluster_report(
            panels,
            horizon_days=3,
            n_clusters=None,
            ridge_lambda=0.0,
            beta_scale="feature_zscore",
            cluster_method="hierarchical",
            cluster_linkage="average",
            select_ridge=False,
        )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertTrue(out.get("n_clusters_auto"))
        ks = out.get("k_selection") or {}
        self.assertEqual(ks.get("mode"), "auto_fit_ic")
        fitted = int(out.get("fitted_count") or 0)
        expect = auto_k_candidates(fitted)
        self.assertEqual(ks.get("candidate_ks"), expect)
        self.assertIn(int(ks.get("chosen_k")), expect)
        cand_ks = sorted({int(r["k"]) for r in (ks.get("candidates") or [])})
        self.assertTrue(set(cand_ks).issubset(set(expect)))
        self.assertIn("拟合+IC选k", out.get("note") or "")
        self.assertIn("partition_loss", (ks.get("candidates") or [{}])[0])
        self.assertIn("cluster_beta", ks)
        self.assertIn("chosen_partition_kind", ks)
        wf = out.get("walk_forward") or {}
        self.assertIn(wf.get("split_mode"), ("calendar", "per_stock_ratio"))
        self.assertEqual(ks.get("walk_forward"), wf)
        if wf.get("split_mode") == "calendar":
            self.assertTrue(wf.get("cut_date"))
            self.assertEqual(
                (ks.get("cluster_beta") or {}).get("cut_date"), wf.get("cut_date")
            )
            # auto-k 多折打分（小宇宙）
            self.assertTrue(ks.get("expanding_score") in (True, False))
            cand0 = (ks.get("candidates") or [{}])[0]
            if ks.get("expanding_score"):
                self.assertGreaterEqual(int(ks.get("n_expanding_folds") or 0), 2)
                self.assertIn("expanding_folds", cand0)
                self.assertGreaterEqual(len(cand0.get("expanding_folds") or []), 2)
                self.assertIn("partition_loss_primary", cand0)
            exp = wf.get("expanding") or {}
            self.assertIn("ok", exp)
            if exp.get("ok"):
                self.assertGreaterEqual(int(exp.get("n_folds") or 0), 1)
                self.assertTrue(exp.get("folds"))
        gr = out.get("greedy_refine") or {}
        self.assertIn("ok", gr)
        self.assertEqual(ks.get("greedy_refine"), gr)

    def test_light_greedy_swap_refine_unit(self):
        """holdout 贪心换组：标签长度不变；可改进则 loss 下降。"""
        from quant.research.cluster_greedy_refine import (
            evaluate_labels_holdout_loss,
            light_greedy_swap_refine,
        )

        dates = [f"2024-01-{i:02d}" for i in range(1, 31)]
        # 两团：A/B 动量→收益，C/D 负相关动量
        panel = {}
        for code, sign in (("A", 1.0), ("B", 1.0), ("C", -1.0), ("D", -1.0)):
            xs = [{"momentum": float(sign * i)} for i in range(30)]
            ys = [float(sign * i) * 0.01 for i in range(30)]
            panel[code] = {"xs": xs, "ys": ys, "dates": list(dates)}
        codes = ["A", "B", "C", "D"]
        # 错配：A+C / B+D
        bad = np.array([0, 1, 0, 1], dtype=int)
        base = evaluate_labels_holdout_loss(
            bad,
            codes,
            panel,
            holdout_ratio=0.3,
            cut_date="2024-01-21",
            use_pit=False,
            select_ridge=False,
            respect_regime=False,
        )
        self.assertLess(float(base["loss"]), 1e9)
        refined, diag = light_greedy_swap_refine(
            bad,
            codes,
            panel,
            holdout_ratio=0.3,
            cut_date="2024-01-21",
            use_pit=False,
            select_ridge=False,
            respect_regime=False,
            max_rounds=3,
            max_evals=40,
        )
        self.assertTrue(diag.get("ok"), diag)
        self.assertEqual(len(refined), 4)
        # 返回标签应连续非负（组掏空后重编号）
        labs = sorted({int(v) for v in refined if int(v) >= 0})
        self.assertEqual(labs, list(range(len(labs))))
        self.assertEqual(int(diag.get("n_evals") or 0) >= 0, True)
        if diag.get("improved"):
            self.assertLessEqual(
                float(diag["loss_after"]), float(diag["loss_before"])
            )
            # 理想：同号票同组
            groups = {}
            for i, lab in enumerate(refined):
                groups.setdefault(int(lab), []).append(codes[i])
            for members in groups.values():
                if len(members) >= 2:
                    signs = {1 if m in ("A", "B") else -1 for m in members}
                    self.assertEqual(len(signs), 1, groups)
    def test_aggregate_expanding_auto_k_score(self):
        from quant.research.factor_ols_clusters import (
            _aggregate_expanding_auto_k_score,
        )

        primary = {
            "partition_loss": 0.4,
            "mean_yhat_ic": 0.2,
            "passed": 1,
            "failed": 0,
            "skipped": 0,
            "mean_delta_oos_pp": 0.5,
            "silhouette": 0.3,
            "mean_holdout_r2": 0.1,
            "mean_holdout_rmse": 0.2,
        }
        folds = [
            {**primary, "partition_loss": 0.4, "mean_yhat_ic": 0.2},
            {"partition_loss": 0.6, "mean_yhat_ic": 0.0},
        ]
        agg = _aggregate_expanding_auto_k_score(folds, primary_score=primary)
        self.assertAlmostEqual(float(agg["partition_loss"]), 0.5, places=5)
        self.assertEqual(agg["partition_loss_primary"], 0.4)
        self.assertEqual(agg["n_expanding_folds"], 2)
        self.assertEqual(agg["sort_key"][0], -0.5)
    def test_expanding_cluster_wf_audit_unit(self):
        """多折切点重聚类审计：日历对齐 + 相邻折稳定度字段。"""
        from quant.research.cluster_label_align import pair_label_stability
        from quant.research.cluster_wf_audit import (
            expanding_cluster_wf_audit,
            resolve_expanding_cut_dates,
        )

        stab = pair_label_stability(
            ["A", "B", "C", "D"],
            [0, 0, 1, 1],
            [1, 1, 0, 0],  # 置换后应对齐为高稳定度
        )
        self.assertTrue(stab.get("aligned"))
        self.assertGreaterEqual(float(stab.get("stability") or 0), 0.99)

        dates = [f"2024-01-{i:02d}" for i in range(1, 31)] + [
            f"2024-02-{i:02d}" for i in range(1, 29)
        ]
        # 4 票 × 长面板，便于两折 cut 都有 holdout
        codes = ["S1", "S2", "S3", "S4"]
        panel = {}
        per_stock = []
        for j, code in enumerate(codes):
            xs = [{"momentum": float(i + j), "value": float(j)} for i in range(len(dates))]
            ys = [0.01 * (i + j) for i in range(len(dates))]
            panel[code] = {"xs": xs, "ys": ys, "dates": list(dates), "bars": []}
            per_stock.append(
                {
                    "success": True,
                    "stock_code": code,
                    "coefficients": {"momentum": 1.0 + 0.1 * j, "value": 0.1 * j},
                    "active_features": ["momentum", "value"],
                }
            )
        cuts = resolve_expanding_cut_dates(
            panel, codes=codes, train_fractions=(0.55, 0.7)
        )
        self.assertGreaterEqual(len(cuts), 2)
        audit = expanding_cluster_wf_audit(
            codes,
            panel,
            ["momentum", "value"],
            n_clusters=2,
            partition_kind="hierarchical_average",
            method="hierarchical",
            linkage="average",
            scale_mode="none",
            ridge_lambda=0.0,
            select_ridge=False,
            use_pit=False,
            respect_regime=False,
            train_fractions=(0.55, 0.7),
            per_stock=per_stock,
        )
        self.assertTrue(audit.get("ok"), audit)
        self.assertGreaterEqual(int(audit.get("n_folds") or 0), 2)
        # 第二折应有 vs 前折稳定度
        fold2 = next(f for f in audit["folds"] if f.get("fold") == 2 and f.get("ok"))
        self.assertIn("stability_vs_prev", fold2)

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
        self.assertIn("quant-section-factors-title", html)
        self.assertIn(">分组</", html)
        self.assertIn("quant-section-global-title", html)
        self.assertIn(">对照</", html)
        self.assertIn("quant-section-probe-title", html)
        self.assertIn(">探针</", html)
        self.assertIn("quant-section-daily-title", html)
        self.assertIn(">日报</", html)

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
