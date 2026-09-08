"""观察池 + rank_lots 预演 / 落账。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _signal_item(**extra):
    base = {
        "stock_code": "600000",
        "stock_name": "测试",
        "y_trade": 1.5,
        "predicted_score_blend": 1.5,
        "predicted_score": 0.9,
        "predicted_score_eod": 0.9,
        "predicted_score_tau": 0.7,
        "score_rem": 0.7,
        "y_path": 2.5,
        "predicted_score_path": 2.5,
        "y_nowcast": 1.1,
        "predicted_score_nowcast": 1.1,
        "y_on": 0.2,
        "predicted_score_on": 0.2,
        "sector": "银行",
    }
    base.update(extra)
    return base


class TestWatchingMatrixPreview(unittest.TestCase):
    def test_preview_builds_open_trade(self):
        from core.paper.rebalance.watching_matrix import simulate_watching_matrix_preview

        paper = {
            "strategy_id": "short_conservative",
            "cash": 1_000_000,
            "holdings": [],
            "rules": {"max_positions": 5, "position_pct": 0.2},
        }
        with patch(
            "core.paper.rebalance.watching_matrix._watching_codes",
            return_value=(["600000"], {"n_watch": 1, "n_total": 1}),
        ), patch(
            "core.paper.rebalance.watching_matrix._score_pool",
            return_value=([_signal_item()], []),
        ), patch(
            "core.paper.rebalance.watching_matrix._quote_px",
            return_value=10.0,
        ), patch(
            "core.paper.ledger.mark_to_market",
            return_value={"equity": 1_000_000, "cash": 1_000_000},
        ):
            out = simulate_watching_matrix_preview(paper, dry_run=True)

        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("mode"), "watching_matrix")
        self.assertTrue(out.get("dry_run"))
        self.assertTrue(out.get("confirm_supported"))
        self.assertGreaterEqual(len(out.get("buy_trades") or []), 1)
        self.assertEqual(out["buy_trades"][0]["stock_code"], "600000")
        report = out.get("rebalance_report") or []
        self.assertEqual(len(report), len(out.get("buy_trades") or []) + len(out.get("sell_trades") or []))
        self.assertTrue(all(r.get("side") in ("buy", "sell") for r in report))
        self.assertEqual(report[0].get("decision"), "买入")
        self.assertEqual(report[0].get("predicted_score_eod"), 0.9)
        self.assertEqual(report[0].get("predicted_score_tau"), 0.7)
        self.assertEqual(report[0].get("predicted_score_on"), 0.2)
        self.assertEqual(float(out["buy_trades"][0].get("shares") or 0), 500)
        self.assertEqual(paper.get("cash"), 1_000_000)
        self.assertEqual(paper.get("holdings") or [], [])

    def test_confirm_applies_buy_to_paper(self):
        from core.paper.rebalance.watching_matrix import simulate_watching_matrix_preview

        paper = {
            "strategy_id": "short_conservative",
            "cash": 1_000_000,
            "holdings": [],
            "trades": [],
            "rules": {"max_positions": 5, "position_pct": 0.2},
            "cost_model": "zero",
        }
        with patch(
            "core.paper.rebalance.watching_matrix._watching_codes",
            return_value=(["600000"], {"n_watch": 1, "n_total": 1}),
        ), patch(
            "core.paper.rebalance.watching_matrix._score_pool",
            return_value=([_signal_item()], []),
        ), patch(
            "core.paper.rebalance.watching_matrix._quote_px",
            return_value=10.0,
        ), patch(
            "core.paper.ledger.mark_to_market",
            side_effect=lambda p: {
                "equity": float(p.get("cash") or 0)
                + sum(
                    float(h.get("shares") or 0) * float(h.get("cost") or 0)
                    for h in (p.get("holdings") or [])
                ),
                "cash": float(p.get("cash") or 0),
            },
        ):
            out = simulate_watching_matrix_preview(paper, dry_run=False)

        self.assertTrue(out.get("ok"))
        self.assertFalse(out.get("dry_run"))
        self.assertGreaterEqual(len(out.get("buy_trades") or []), 1)
        self.assertGreater(len(paper.get("holdings") or []), 0)
        self.assertEqual(paper["holdings"][0]["stock_code"], "600000")
        self.assertEqual(float(paper["holdings"][0].get("shares") or 0), 500)
        self.assertLess(float(paper.get("cash") or 0), 1_000_000)

    def test_adverse_path_still_opens(self):
        """低/负 y_path 不再拦开仓；ranking 过入场即可买。"""
        from core.paper.rebalance.watching_matrix import simulate_watching_matrix_preview

        paper = {
            "cash": 1_000_000,
            "holdings": [],
            "rules": {"max_positions": 5, "position_pct": 0.2},
        }
        item = _signal_item(y_path=-2.5, predicted_score_path=-2.5)
        with patch(
            "core.paper.rebalance.watching_matrix._watching_codes",
            return_value=(["600000"], {"n_watch": 1, "n_total": 1}),
        ), patch(
            "core.paper.rebalance.watching_matrix._score_pool",
            return_value=([item], []),
        ), patch(
            "core.paper.rebalance.watching_matrix._quote_px",
            return_value=10.0,
        ), patch(
            "core.paper.ledger.mark_to_market",
            return_value={"equity": 1_000_000, "cash": 1_000_000},
        ):
            out = simulate_watching_matrix_preview(paper)

        self.assertTrue(out.get("ok"))
        self.assertGreaterEqual(len(out.get("buy_trades") or []), 1)
        self.assertEqual(out["buy_trades"][0]["stock_code"], "600000")
        acts = (out.get("path_matrix") or {}).get("by_action") or {}
        self.assertEqual(int(acts.get("skip_window") or 0), 0)

    def test_held_fusion_nonneg_no_sell_at_cash_floor(self):
        """已持仓 y_fuse≥0：不因未进 Top 而卖；现金贴地板则不加仓。"""
        from core.paper.rebalance.watching_matrix import simulate_watching_matrix_preview

        paper = {
            "cash": 500_000,
            "holdings": [
                {
                    "stock_code": "601111",
                    "stock_name": "中国国航",
                    "shares": 1400,
                    "cost": 5.86,
                }
            ],
            "rules": {"max_positions": 5, "position_pct": 0.2, "horizon_days": 3},
        }
        item = _signal_item(
            stock_code="601111",
            stock_name="中国国航",
            y_path=0.05,
            predicted_score_path=0.05,
            y_trade=0.18,
            predicted_score_blend=0.18,
            predicted_score_eod=0.18,
        )
        with patch(
            "core.paper.rebalance.watching_matrix._watching_codes",
            return_value=(
                ["601111"],
                {"n_watch": 1, "n_total": 1, "name_by_code": {"601111": "中国国航"}},
            ),
        ), patch(
            "core.paper.rebalance.watching_matrix._score_pool",
            return_value=([item], []),
        ), patch(
            "core.paper.rebalance.watching_matrix._quote_px",
            return_value=5.94,
        ), patch(
            "core.paper.ledger.mark_to_market",
            return_value={
                "equity": 500_000 + 1400 * 5.94,
                "cash": 500_000,
                "holdings": [
                    {
                        "stock_code": "601111",
                        "stock_name": "中国国航",
                        "shares": 1400,
                        "price": 5.94,
                        "change_pct": 1.02,
                    }
                ],
            },
        ):
            out = simulate_watching_matrix_preview(paper, dry_run=True)

        self.assertTrue(out.get("ok"))
        self.assertEqual(len(out.get("buy_trades") or []), 0)
        self.assertEqual(len(out.get("sell_trades") or []), 0)
        self.assertEqual(len(out.get("rebalance_report") or []), 0)

    def test_oos_failed_excluded_from_buys(self):
        from core.paper.rebalance.watching_matrix import simulate_watching_matrix_preview

        paper = {
            "strategy_id": "short_conservative",
            "cash": 1_000_000,
            "holdings": [],
            "rules": {"max_positions": 5, "position_pct": 0.2},
        }
        oos_item = _signal_item(
            stock_code="600001",
            stock_name="OOS票",
            return_model_source="oos_failed_heuristic",
            oos_failed=True,
            y_trade=5.0,
            predicted_score_blend=5.0,
            y_path=3.0,
            predicted_score_path=3.0,
            y_nowcast=2.0,
        )
        ok_item = _signal_item(stock_code="600000", y_path=2.5, predicted_score_path=2.5)
        with patch(
            "core.paper.rebalance.watching_matrix._watching_codes",
            return_value=(["600000", "600001"], {"n_watch": 2, "n_total": 2}),
        ), patch(
            "core.paper.rebalance.watching_matrix._score_pool",
            return_value=([ok_item, oos_item], []),
        ), patch(
            "core.paper.rebalance.watching_matrix._quote_px",
            return_value=10.0,
        ), patch(
            "core.paper.ledger.mark_to_market",
            return_value={"equity": 1_000_000, "cash": 1_000_000},
        ):
            out = simulate_watching_matrix_preview(paper, dry_run=True)

        self.assertTrue(out.get("ok"))
        buy_codes = [t.get("stock_code") for t in (out.get("buy_trades") or [])]
        self.assertNotIn("600001", buy_codes)
        self.assertIn("600000", buy_codes)
        self.assertGreaterEqual(int((out.get("path_matrix") or {}).get("oos_excluded") or 0), 1)

    def test_matrix_mode_service_confirm_writes(self):
        from services.paper_trades import PaperTradesMixin

        paper = {
            "strategy_id": "short_conservative",
            "cash": 1_000_000,
            "holdings": [],
            "trades": [],
            "snapshots": [],
            "operation_log": [],
            "rules": {"max_positions": 5, "position_pct": 0.2},
            "cost_model": "zero",
            "updated_at": "2026-01-01T00:00:00",
        }
        preview_out = {
            "success": True,
            "ok": True,
            "mode": "watching_matrix",
            "dry_run": False,
            "matrix_mode": True,
            "buy_trades": [
                {
                    "side": "buy",
                    "stock_code": "600000",
                    "shares": 1000,
                    "price": 10.0,
                    "amount": 10000,
                }
            ],
            "sell_trades": [],
            "rebalance_report": [],
            "summary": {"equity": 990000, "cash": 990000},
            "cash_impact": {},
            "confirm_supported": True,
            "note": "观察池 rank_lots 落账（未建分池簿）",
        }

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            path = tf.name
        try:
            import json

            with open(path, "w", encoding="utf-8") as f:
                json.dump(paper, f)

            class _T(PaperTradesMixin):
                pass

            svc = _T()
            svc.path = path

            with patch(
                "core.paper.rebalance.watching_matrix.simulate_watching_matrix_preview",
            ) as sim, patch(
                "core.paper.open_fill.apply_next_open_commit",
                side_effect=lambda orig, work, result, **kw: (work, {**result, "fill_action": "immediate"}),
            ), patch(
                "services.paper_trades.capture_mark_snapshot",
            ), patch(
                "services.paper_trades.append_snapshot",
            ), patch(
                "services.paper_trades.append_trade_legs_to_operation_log",
            ), patch(
                "services.paper_trades.append_operation_log",
            ), patch(
                "services.paper_trades.mark_to_market",
                return_value={"equity": 990000, "cash": 990000},
            ):
                # Mutate work like real apply would
                def _sim(work, **kwargs):
                    work["cash"] = 990000
                    work["holdings"] = [
                        {
                            "stock_code": "600000",
                            "shares": 1000,
                            "cost": 10.0,
                        }
                    ]
                    out = dict(preview_out)
                    out["dry_run"] = kwargs.get("dry_run", False)
                    return out

                sim.side_effect = _sim
                out = svc.rebalance(dry_run=False)

            self.assertTrue(out.get("ok"))
            self.assertFalse(out.get("dry_run"))
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved.get("cash"), 990000)
            self.assertEqual(len(saved.get("holdings") or []), 1)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


class TestApplyLegOpenCost(unittest.TestCase):
    def test_buy_cost_equals_fill_sell_keeps_open_date(self):
        from core.paper.rebalance.watching_matrix import _apply_one_leg

        paper = {
            "cash": 1_000_000.0,
            "holdings": [],
            "trades": [],
            "cost_model": "zero",
        }
        buy, err = _apply_one_leg(
            paper,
            {
                "side": "buy",
                "stock_code": "600519",
                "stock_name": "茅台",
                "shares": 1000,
                "price": 10.0,
                "action": "open",
            },
            as_of="2026-03-10",
        )
        self.assertIsNone(err)
        self.assertIsNotNone(buy)
        self.assertEqual(buy["open_date"], "2026-03-10")
        self.assertEqual(buy["cost_price"], buy["price"])
        self.assertEqual(float(buy["price"]), 10.0)
        self.assertAlmostEqual(float(buy["cum_cost"]), 10000.0)

        add, err = _apply_one_leg(
            paper,
            {
                "side": "buy",
                "stock_code": "600519",
                "shares": 1000,
                "price": 12.0,
                "action": "add",
            },
            as_of="2026-03-11",
        )
        self.assertIsNone(err)
        self.assertEqual(add["open_date"], "2026-03-10")
        self.assertEqual(add["cost_price"], add["price"])
        self.assertEqual(float(add["price"]), 12.0)
        self.assertAlmostEqual(float(add["cum_cost"]), 22000.0)

        sell, err = _apply_one_leg(
            paper,
            {
                "side": "sell",
                "stock_code": "600519",
                "shares": 2000,
                "price": 13.0,
                "action": "exit",
            },
            as_of="2026-03-12",
        )
        self.assertIsNone(err, err)
        self.assertEqual(sell["open_date"], "2026-03-10")
        self.assertAlmostEqual(float(sell["cost_price"]), 11.0)
        self.assertAlmostEqual(float(sell["cum_cost"]), 22000.0)


if __name__ == "__main__":
    unittest.main()
