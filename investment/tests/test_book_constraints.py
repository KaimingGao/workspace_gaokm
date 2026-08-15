"""建簿约束 · theme_day 对齐 · 组合健康度。"""

from __future__ import annotations

import unittest
from unittest.mock import patch


class TestRemTheme(unittest.TestCase):
    def test_theme_from_breadth(self):
        from core.research.rem_theme import resolve_theme_day

        self.assertEqual(
            resolve_theme_day(sector_breadth=0.6, gap_pct=0.1, gap_trigger_pct=2.0),
            1.0,
        )
        self.assertEqual(
            resolve_theme_day(
                sector_breadth=0.1,
                pool_gaps=[2.5, 3.0, 0.4],
                gap_trigger_pct=2.0,
            ),
            1.0,
        )
        self.assertEqual(
            resolve_theme_day(sector_breadth=0.1, pool_gaps=[0.5, 0.4], gap_trigger_pct=2.0),
            0.0,
        )


class TestBookConstraints(unittest.TestCase):
    def test_sector_cap_and_tau_defer(self):
        from core.signal.book_constraints import fill_book_with_constraints

        rows = []
        for i, (code, sec, score, tau) in enumerate(
            [
                ("000001", "银行", 3.0, 0.5),
                ("000002", "银行", 2.9, 0.4),
                ("000003", "银行", 2.8, 0.3),
                ("600000", "银行", 2.7, -0.2),
                ("300750", "新能源", 2.6, 0.5),
                ("601318", "保险", 2.5, 0.2),
            ]
        ):
            rows.append(
                {
                    "stock_code": code,
                    "stock_name": code,
                    "score": score,
                    "predicted_score": score,
                    "predicted_score_blend": score,
                    "predicted_score_tau": tau,
                    "sector": sec,
                }
            )
        with patch(
            "core.signal.book_constraints.tradeable_block_reason", return_value=None
        ), patch(
            "core.signal.dual_score.get_dual_score_cfg",
            return_value={
                "min_predicted_score_tau": 0.0,
                "block_buy_if_tau_missing": False,
                "w_eod": 0.5,
                "w_tau": 0.5,
                "fusion_mode": "blend",
            },
        ):
            out = fill_book_with_constraints(
                rows,
                max_names=4,
                risk_limits={"max_sector_pct": 50.0, "max_position_pct": 25.0},
                constraints={
                    "enabled": True,
                    "filter_untradeable": True,
                    "enforce_sector_cap": True,
                    "tau_fail_mode": "defer",
                },
                dual_cfg={"fusion_mode": "blend", "w_eod": 0.5, "w_tau": 0.5},
            )
        book = out["book"]
        self.assertLessEqual(len(book), 4)
        bank = sum(1 for b in book if b.get("sector") == "银行")
        # max_names=4, 50% → per_sec_cap=2
        self.assertLessEqual(bank, 2)
        self.assertGreaterEqual(out["stats"]["sector_cap"], 1)
        # τ 负的应排在后面或带标记
        codes = [b["stock_code"] for b in book]
        if "600000" in codes:
            self.assertTrue(
                any(b.get("tau_gate_fail") for b in book if b["stock_code"] == "600000")
            )


class TestPortfolioHealth(unittest.TestCase):
    def test_health_smoke(self):
        from core.risk.portfolio_health import build_portfolio_health

        out = build_portfolio_health(
            paper={"holdings": [], "cash": 1e6, "rules": {}},
            book=[
                {"stock_code": "000001", "sector": "银行", "score": 1.0},
                {"stock_code": "000002", "sector": "银行", "score": 1.0},
                {"stock_code": "300750", "sector": "新能源", "score": 1.0},
            ],
            book_constraints={"sector_cap": 1, "filled": 3},
            book_skips=[{"skip_stage": "sector_cap"}],
            rolling_ic={"mean_ic": -0.05},
        )
        self.assertTrue(out.get("success"))
        self.assertTrue(out.get("alpha", {}).get("decay_alert"))
        self.assertEqual(out.get("process", {}).get("book_skips_count"), 1)


if __name__ == "__main__":
    unittest.main()
