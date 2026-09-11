"""rank_lots：ranking 净收益百分数、200/500 股、ranking<0 清仓、现金约束。"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestRankingScore(unittest.TestCase):
    def test_formula_percent_points(self):
        from core.paper.rebalance.rank_lots import ranking_score

        self.assertAlmostEqual(ranking_score(0.9, 0.2), 0.9 / 100.0)
        self.assertAlmostEqual(
            ranking_score(0.9, 0.2, y_on_alpha=1.0),
            (1.0 + 0.9 / 100.0) * (1.0 + 0.2 / 100.0) - 1.0,
        )
        self.assertAlmostEqual(
            ranking_score(0.9, 0.2, y_on_alpha=10.0),
            (1.0 + 0.9 / 100.0) * (1.0 + 10.0 * 0.2 / 100.0) - 1.0,
        )
        self.assertAlmostEqual(ranking_score(1.0, None), 0.01)
        self.assertIsNone(ranking_score(None, 1.0))
        self.assertLess(ranking_score(-0.2, 0.0), 0.0)
        self.assertLess(ranking_score(-0.2, 1.0), 0.0)
        self.assertGreater(ranking_score(-0.2, 1.0, y_on_alpha=1.0), 0.0)
        self.assertAlmostEqual(ranking_score(0.9, 0.2, y_on_alpha=-3), 0.009)
        self.assertAlmostEqual(
            ranking_score(0.9, 0.2, y_on_alpha=99),
            ranking_score(0.9, 0.2, y_on_alpha=10),
        )

    def test_legacy_threshold_coerced_to_net(self):
        from core.paper.rebalance.rank_lots import coerce_rank_threshold

        self.assertAlmostEqual(coerce_rank_threshold(0.01, 0.01), 0.01)
        self.assertAlmostEqual(coerce_rank_threshold(0.20, 0.02), 0.02)
        self.assertAlmostEqual(coerce_rank_threshold(1.01, 0.01), 0.01)
        self.assertAlmostEqual(coerce_rank_threshold(1.0, 0.01), 0.01)
        self.assertAlmostEqual(coerce_rank_threshold(1.002, 0.02), 0.02)
        self.assertAlmostEqual(coerce_rank_threshold(1.02, 0.02), 0.02)

    def test_lot_shares_threshold(self):
        from core.paper.rebalance.rank_lots import lot_shares_for_rank

        self.assertEqual(lot_shares_for_rank(0.20, 0.20), 200)
        self.assertEqual(lot_shares_for_rank(0.21, 0.20), 500)
        self.assertEqual(lot_shares_for_rank(0.01, 0.20), 200)
        self.assertEqual(lot_shares_for_rank(None, 0.20), 0)
        self.assertEqual(
            lot_shares_for_rank(0.21, 0.20, lot_base=1000, lot_strong=2000),
            2000,
        )
        self.assertEqual(
            lot_shares_for_rank(0.01, 0.20, lot_base=1000, lot_strong=2000),
            1000,
        )


def _cfg(**extra):
    base = {
        "rank_enter": 0.01,
        "rank_strong": 0.02,
    "cash_floor": 500_000.0,
    "holdings_mv_cap": 0.0,
    "t0_sell_blocks": {},
    "top_k": 5,
        "fusion_w_trade": 0.5,
        "fusion_w_nowcast": 0.5,
    }
    base.update(extra)
    return base


class TestPlanRankLotDay(unittest.TestCase):
    def test_open_200_vs_500(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        scored = [
            {
                "stock_code": "600000",
                "stock_name": "弱",
                "y_fuse": 1.2,
                "y_on": 0.0,
            },
            {
                "stock_code": "600001",
                "stock_name": "强",
                "y_fuse": 3.0,
                "y_on": 0.0,
            },
        ]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=1_000_000,
            prices={"600000": 10.0, "600001": 10.0},
            cfg=_cfg(),
        )
        by = {t["stock_code"]: t for t in out["buys"]}
        self.assertEqual(float(by["600000"]["shares"]), 200)
        self.assertEqual(by["600000"]["action"], "open")
        self.assertEqual(float(by["600001"]["shares"]), 500)
        self.assertEqual(by["600001"]["action"], "open")
        self.assertEqual(int(by["600001"]["rank_i"]), 1)
        self.assertEqual(int(by["600000"]["rank_i"]), 2)
        self.assertEqual(int(by["600001"]["rank_n"]), 2)

    def test_stamps_y_trade_not_fuse_copy(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        scored = [
            {
                "stock_code": "600000",
                "stock_name": "分项",
                "y_trade": 2.0,
                "y_nowcast": 1.0,
                "y_on": 0.0,
            }
        ]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=1_000_000,
            prices={"600000": 10.0},
            cfg=_cfg(),
        )
        buy = out["buys"][0]
        self.assertAlmostEqual(float(buy["y_trade"]), 2.0)
        self.assertAlmostEqual(float(buy["y_nowcast"]), 1.0)
        self.assertAlmostEqual(float(buy["y_fuse"]), 1.5)
        self.assertEqual(int(buy["rank_i"]), 1)

    def test_fuse_weights_trade_nowcast(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        scored = [
            {
                "stock_code": "600000",
                "stock_name": "加权",
                "y_trade": 2.0,
                "y_nowcast": 1.0,
                "y_on": 0.0,
            }
        ]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=1_000_000,
            prices={"600000": 10.0},
            cfg=_cfg(fusion_w_trade=0.6, fusion_w_nowcast=0.4),
        )
        buy = out["buys"][0]
        self.assertAlmostEqual(float(buy["y_fuse"]), 1.6)

    def test_open_cfg_lot_1000_vs_2000(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        scored = [
            {
                "stock_code": "600000",
                "stock_name": "弱",
                "y_fuse": 1.2,
                "y_on": 0.0,
            },
            {
                "stock_code": "600001",
                "stock_name": "强",
                "y_fuse": 3.0,
                "y_on": 0.0,
            },
        ]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=1_000_000,
            prices={"600000": 10.0, "600001": 10.0},
            cfg=_cfg(lot_base=1000, lot_strong=2000),
        )
        by = {t["stock_code"]: t for t in out["buys"]}
        self.assertEqual(float(by["600000"]["shares"]), 1000)
        self.assertEqual(float(by["600001"]["shares"]), 2000)

    def test_strong_lot_falls_back_to_base_when_floor_blocks(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        scored = [{"stock_code": "600001", "stock_name": "强", "y_fuse": 3.0, "y_on": 0.0}]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=500_000,
            prices={"600001": 250.0},
            cfg=_cfg(lot_base=1000, lot_strong=2000, cash_floor=100_000, rank_enter=0.012, rank_strong=0.012),
        )
        self.assertEqual(len(out["buys"]), 1)
        self.assertEqual(float(out["buys"][0]["shares"]), 1000)
        self.assertEqual(out["buys"][0]["lot_kind"], "base")

    def test_fusion_negative_exits(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        held = {
            "stock_code": "600000",
            "stock_name": "测",
            "shares": 200,
            "lots": [
                {
                    "shares": 200,
                    "bought_at": "2026-03-09T09:30:00.000",
                    "bought_date": "2026-03-09",
                }
            ],
        }
        scored = [{"stock_code": "600000", "y_fuse": -0.2, "y_on": 0.0}]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[held],
            cash=800_000,
            prices={"600000": 10.0},
            cfg=_cfg(),
            as_of="2026-03-10",
        )
        self.assertEqual(len(out["sells"]), 1)
        self.assertEqual(out["sells"][0]["action"], "exit")
        self.assertEqual(float(out["sells"][0]["shares"]), 200)
        self.assertEqual(out["buys"], [])

    def test_negative_fuse_held_if_on_lifts_ranking(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        held = {
            "stock_code": "600000",
            "stock_name": "测",
            "shares": 200,
            "lots": [
                {
                    "shares": 200,
                    "bought_at": "2026-03-09T09:30:00.000",
                    "bought_date": "2026-03-09",
                }
            ],
        }
        scored = [{"stock_code": "600000", "y_fuse": -0.2, "y_on": 1.0}]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[held],
            cash=800_000,
            prices={"600000": 10.0},
            cfg=_cfg(y_on_alpha=1.0),
            as_of="2026-03-10",
        )
        self.assertEqual(out["sells"], [])
        self.assertIn("600000", [h["stock_code"] for h in out["holds"]])

    def test_alpha_zero_ignores_y_on_on_exit(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        held = {
            "stock_code": "600000",
            "stock_name": "测",
            "shares": 200,
            "lots": [
                {
                    "shares": 200,
                    "bought_at": "2026-03-09T09:30:00.000",
                    "bought_date": "2026-03-09",
                }
            ],
        }
        scored = [{"stock_code": "600000", "y_fuse": -0.2, "y_on": 1.0}]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[held],
            cash=800_000,
            prices={"600000": 10.0},
            cfg=_cfg(),
            as_of="2026-03-10",
        )
        self.assertEqual(len(out["sells"]), 1)
        self.assertEqual(out["sells"][0]["action"], "exit")

    def test_positive_fuse_exits_if_on_sinks_ranking(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        held = {
            "stock_code": "600000",
            "stock_name": "测",
            "shares": 200,
            "lots": [
                {
                    "shares": 200,
                    "bought_at": "2026-03-09T09:30:00.000",
                    "bought_date": "2026-03-09",
                }
            ],
        }
        scored = [{"stock_code": "600000", "y_fuse": 0.1, "y_on": -2.0}]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[held],
            cash=800_000,
            prices={"600000": 10.0},
            cfg=_cfg(y_on_alpha=1.0),
            as_of="2026-03-10",
        )
        self.assertEqual(len(out["sells"]), 1)
        self.assertEqual(out["sells"][0]["action"], "exit")

    def test_t1_skip_same_day_buy(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        held = {
            "stock_code": "600000",
            "stock_name": "测",
            "shares": 200,
            "lots": [
                {
                    "shares": 200,
                    "bought_at": "2026-03-10T09:30:00.000",
                    "bought_date": "2026-03-10",
                }
            ],
        }
        scored = [{"stock_code": "600000", "y_fuse": -0.2, "y_on": 0.0}]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[held],
            cash=800_000,
            prices={"600000": 10.0},
            cfg=_cfg(),
            as_of="2026-03-10",
        )
        self.assertEqual(out["sells"], [])
        self.assertTrue(
            any(
                "T+1" in str(s.get("reason") or "") or "不可卖" in str(s.get("reason") or "")
                for s in out["skips"]
            )
        )
        self.assertEqual(out["skips"][0].get("side"), "sell")

    def test_not_in_top_still_holds_if_fusion_nonneg(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        held = {
            "stock_code": "600000",
            "shares": 100,
            "lots": [
                {
                    "shares": 100,
                    "bought_at": "2026-03-09T09:30:00.000",
                    "bought_date": "2026-03-09",
                }
            ],
        }
        scored = [
            {"stock_code": "600001", "y_fuse": 3.0, "y_on": 0.0},
            {"stock_code": "600000", "y_fuse": 0.05, "y_on": 0.0},
        ]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[held],
            cash=1_000_000,
            prices={"600000": 10.0, "600001": 10.0},
            cfg=_cfg(top_k=1),
            as_of="2026-03-10",
        )
        self.assertEqual(out["sells"], [])
        holds = [h["stock_code"] for h in out["holds"]]
        self.assertIn("600000", holds)
        buy_codes = [b["stock_code"] for b in out["buys"]]
        self.assertEqual(buy_codes, ["600001"])

    def test_cash_floor_skips_buy(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        scored = [{"stock_code": "600000", "y_fuse": 2.0, "y_on": 0.0}]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=500_000,
            prices={"600000": 10.0},
            cfg=_cfg(),
        )
        self.assertEqual(out["buys"], [])
        self.assertTrue(
            any("地板" in str(s.get("reason") or "") for s in out["skips"])
        )
        self.assertEqual(out["skips"][0].get("side"), "buy")
        self.assertEqual(out["skips"][0].get("action"), "skip")

    def test_zero_floor_skips_when_cash_short(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        scored = [{"stock_code": "600000", "y_fuse": 2.0, "y_on": 0.0}]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=500,
            prices={"600000": 10.0},
            cfg=_cfg(cash_floor=0.0),
        )
        self.assertEqual(out["buys"], [])
        self.assertTrue(
            any("现金不足" in str(s.get("reason") or "") for s in out["skips"])
        )

    def test_oos_blocks_new_buys(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        scored = [
            {
                "stock_code": "600000",
                "y_fuse": 3.0,
                "y_on": 0.0,
                "oos_failed": True,
                "return_model_source": "oos_failed_heuristic",
            }
        ]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=1_000_000,
            prices={"600000": 10.0},
            cfg=_cfg(),
        )
        self.assertEqual(out["buys"], [])
        self.assertTrue(any("OOS" in str(s.get("reason") or "") for s in out["skips"]))

    def test_pool_cap_allows_more_than_paper_max_positions(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        n = 25
        scored = [
            {"stock_code": f"{600000 + i}", "y_fuse": 1.5, "y_on": 0.0} for i in range(n)
        ]
        prices = {f"{600000 + i}": 10.0 for i in range(n)}
        capped = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=2_000_000,
            prices=prices,
            cfg=_cfg(top_k=20),
        )
        full = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=2_000_000,
            prices=prices,
            cfg=_cfg(top_k=n),
        )
        self.assertEqual(len(capped["buys"]), 20)
        self.assertEqual(len(full["buys"]), n)

    def test_holdings_mv_cap_skips_buy_that_would_exceed(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        scored = [
            {"stock_code": "600000", "y_fuse": 1.2, "y_on": 0.0},
            {"stock_code": "600001", "y_fuse": 1.2, "y_on": 0.0},
        ]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=1_000_000,
            prices={"600000": 200.0, "600001": 200.0},
            cfg=_cfg(holdings_mv_cap=50_000),
        )
        self.assertEqual(len(out["buys"]), 1)
        self.assertTrue(
            any("持仓市值将超过上限" in str(s.get("reason") or "") for s in out["skips"])
        )

    def test_t0_open_leg_skips_exit(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        scored = [{"stock_code": "600000", "y_fuse": -1.0, "y_on": 0.0}]
        holdings = [{"stock_code": "600000", "stock_name": "测", "shares": 200}]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=holdings,
            cash=1_000_000,
            prices={"600000": 10.0},
            cfg=_cfg(t0_sell_blocks={"600000": "未平做 T 腿"}),
        )
        self.assertEqual(out["sells"], [])
        self.assertTrue(any(h.get("action") == "hold" for h in out["holds"]))
        self.assertTrue(
            any("未平做 T 腿" in str(s.get("reason") or "") for s in out["skips"])
        )


class TestGetRankLotCfg(unittest.TestCase):
    def test_live_top_k_is_watching_pool(self):
        from core.paper.rebalance.rank_lots import get_rank_lot_cfg
        from core.watching.store import WATCHING_MAX_SIZE

        cfg = get_rank_lot_cfg({"rules": {"max_positions": 20}})
        self.assertEqual(cfg["top_k"], WATCHING_MAX_SIZE)
        self.assertAlmostEqual(float(cfg["holdings_mv_cap"]), 150_000.0)

    def test_live_ignores_stored_cash_floor(self):
        from core.paper.rebalance.rank_lots import get_rank_lot_cfg, scale_cash_floor_to_account

        cfg = get_rank_lot_cfg(
            {
                "initial_cash": 198_854.75,
                "cash": 175_479.6,
                "holdings": [
                    {"stock_code": "600150", "shares": 400, "cost": 37.82}
                ],
                "rules": {
                    "execution": {
                        "rebalance_timing": {
                            "rank_lots": {"cash_floor": 500_000.0}
                        }
                    }
                },
            }
        )
        self.assertEqual(float(cfg["cash_floor"]), 0.0)
        self.assertFalse(cfg.get("cash_floor_scaled"))
        self.assertAlmostEqual(
            scale_cash_floor_to_account(500_000.0, {"initial_cash": 198_854.75}),
            198_854.75 * 0.20,
            places=1,
        )

    def test_live_cash_floor_is_zero(self):
        from core.paper.rebalance.rank_lots import get_rank_lot_cfg

        cfg = get_rank_lot_cfg({"initial_cash": 1_000_000, "cash": 1_000_000})
        self.assertEqual(float(cfg["cash_floor"]), 0.0)
        self.assertFalse(cfg.get("cash_floor_scaled"))

    def test_explicit_top_k_can_cover_watching_pool(self):
        from core.paper.rebalance.rank_lots import get_rank_lot_cfg
        from core.watching.store import WATCHING_MAX_SIZE

        cfg = get_rank_lot_cfg({"rules": {"max_positions": 20}}, top_k=100)
        self.assertEqual(cfg["top_k"], 100)
        cfg_full = get_rank_lot_cfg(None, top_k=WATCHING_MAX_SIZE)
        self.assertEqual(cfg_full["top_k"], WATCHING_MAX_SIZE)


class TestYTauOf(unittest.TestCase):
    def test_prefers_y_tau_then_predicted(self):
        from core.paper.rebalance.rank_lots import y_tau_of

        self.assertEqual(y_tau_of({"y_tau": 0.4, "predicted_score_tau": 0.9}), 0.4)
        self.assertEqual(y_tau_of({"predicted_score_tau": 0.9}), 0.9)
        self.assertEqual(y_tau_of({"score_rem": 0.3}), 0.3)
        self.assertIsNone(y_tau_of({}))


if __name__ == "__main__":
    unittest.main()
