"""P0–P2 alpha/IC 补强单测。"""

from __future__ import annotations

import unittest


class TestAlphaExcess(unittest.TestCase):
    def test_ir_and_legs(self):
        from core.alpha_excess import align_excess_returns, ir_from_excesses, legs_summary

        s = [("2024-01-02", 1.0), ("2024-01-03", 2.0), ("2024-01-04", -0.5)]
        b = [("2024-01-02", 0.5), ("2024-01-03", 0.5), ("2024-01-04", 0.5)]
        ex = align_excess_returns(s, b)
        self.assertEqual(len(ex), 3)
        self.assertAlmostEqual(ex[0], 0.5)
        stats = ir_from_excesses(ex)
        self.assertTrue(stats["ok"])
        legs = legs_summary(total_return_pct=10.0, excess_pack=stats)
        self.assertIsNotNone(legs["alpha_leg_approx_pct"])
        self.assertIsNotNone(legs["beta_leg_approx_pct"])


class TestIcContract(unittest.TestCase):
    def test_primary_banner(self):
        from core.signal.ic_contract import PRIMARY_IC_KIND, annotate_ic_block, ic_contract_banner

        b = ic_contract_banner()
        self.assertEqual(b["primary_ic_kind"], PRIMARY_IC_KIND)
        row = annotate_ic_block({"ic": 0.05}, kind="chrono_pearson", primary=False)
        self.assertFalse(row["is_primary_ic"])
        self.assertEqual(row["ic_kind"], "chrono_pearson")


class TestYhatResidual(unittest.TestCase):
    def test_sector_demean(self):
        from core.signal.neutralize import residualize_rank_scores

        items = [
            {"stock_code": "a", "sector": "银行", "predicted_score": 2.0},
            {"stock_code": "b", "sector": "银行", "predicted_score": 0.0},
            {"stock_code": "c", "sector": "白酒", "predicted_score": 1.0},
        ]
        out = residualize_rank_scores(items, score_keys=["predicted_score"], by="sector")
        self.assertTrue(out["applied"])
        by = {it["stock_code"]: it["predicted_score"] for it in out["items"]}
        self.assertAlmostEqual(by["a"], 1.0)
        self.assertAlmostEqual(by["b"], -1.0)
        # 单票行业不残差
        self.assertAlmostEqual(by["c"], 1.0)


class TestYSpecExcess(unittest.TestCase):
    def test_build_and_apply(self):
        from core.research.beta_accuracy import apply_excess_to_forward_return, build_y_spec

        spec = build_y_spec(horizon_days=1, excess_mode="index")
        self.assertEqual(spec["excess_mode"], "index")
        self.assertIn("−", spec["formula"] + spec["note"])
        self.assertEqual(
            apply_excess_to_forward_return(3.0, 1.0, excess_mode="index"),
            2.0,
        )
        self.assertIsNone(
            apply_excess_to_forward_return(3.0, None, excess_mode="index")
        )


class TestPartitionLossIcKind(unittest.TestCase):
    def test_chrono_label(self):
        from quant.research.partition_loss import _metrics_from_pred_act

        preds = list(range(10))
        acts = [p + 0.1 for p in preds]
        m = _metrics_from_pred_act(preds, acts, holdout_ratio=0.3, n_full=10)
        self.assertTrue(m["ok"])
        self.assertEqual(m.get("ic_kind"), "chrono_pearson")
        self.assertFalse(m.get("is_primary_ic"))


class TestYhatResidualShadow(unittest.TestCase):
    def test_compare_overlap(self):
        from core.research.yhat_residual_shadow import compare_yhat_residual_shadow

        items = [
            {"stock_code": "a", "sector": "银行", "predicted_score": 3.0},
            {"stock_code": "b", "sector": "银行", "predicted_score": 1.0},
            {"stock_code": "c", "sector": "白酒", "predicted_score": 2.5},
            {"stock_code": "d", "sector": "白酒", "predicted_score": 0.5},
        ]
        out = compare_yhat_residual_shadow(items, top_k=2)
        self.assertTrue(out["success"])
        self.assertIn("jaccard_topk", out["compare"])
        self.assertEqual(len(out["arms"]["yhat_residual_off"]["codes"]), 2)


class TestBtExcessAttach(unittest.TestCase):
    def test_compare_arms_excess(self):
        from core.research.bt_excess_attach import compare_arms_excess

        cmp = compare_arms_excess(
            {"excess_pct": 1.0},
            {"excess_pct": 2.5},
        )
        self.assertEqual(cmp["delta_excess_pp"], 1.5)


class TestExcessModeShadowUnit(unittest.TestCase):
    def test_apply_path_and_winner_fields(self):
        from core.research.beta_accuracy import apply_excess_to_forward_return
        from core.research.excess_mode_shadow import compare_excess_mode_shadow

        self.assertEqual(
            apply_excess_to_forward_return(2.0, 0.5, excess_mode="index"), 1.5
        )
        # 空池应失败但结构完整
        out = compare_excess_mode_shadow([], horizon_days=1)
        self.assertIn("arms", out)
        self.assertIn("none", out["arms"])
        self.assertIn("index", out["arms"])


class TestReportExportLegs(unittest.TestCase):
    def test_markdown_includes_legs(self):
        from quant.services.quant_report_export import build_portfolio_backtest_markdown_lines

        lines = build_portfolio_backtest_markdown_lines(
            {
                "total_return_pct": 10.0,
                "win_rate_pct": 55.0,
                "max_drawdown_pct": 5.0,
                "trade_count": 3,
                "loaded_stocks": ["a"],
                "benchmark": {
                    "ok": True,
                    "benchmark_label": "沪深300",
                    "benchmark_return_pct": 8.0,
                    "excess_pct": 2.0,
                    "ann_ir": 0.5,
                },
                "alpha_beta_legs": {
                    "total_return_pct": 10.0,
                    "alpha_leg_approx_pct": 2.0,
                    "beta_leg_approx_pct": 8.0,
                    "ir": 0.5,
                },
            }
        )
        blob = "\n".join(lines)
        self.assertIn("收益分账", blob)
        self.assertIn("α腿", blob)


if __name__ == "__main__":
    unittest.main()
