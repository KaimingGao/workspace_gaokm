"""rank_lots：ranking 净收益百分数、已保存金额换手、未过入场清仓、现金约束。"""

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

        self.assertAlmostEqual(ranking_score(0.9, 0.2), 0.55 / 100.0)
        self.assertAlmostEqual(
            ranking_score(0.9, 0.2, w_oo=1.0, w_τc=0.0),
            0.9 / 100.0,
        )
        self.assertAlmostEqual(ranking_score(1.0, None), 0.01)
        self.assertAlmostEqual(ranking_score(None, 1.0), 0.01)
        self.assertIsNone(ranking_score(None, None))
        self.assertLess(ranking_score(-0.2, 0.0), 0.0)
        self.assertGreater(ranking_score(-0.2, 1.0), 0.0)
        right = ((1.01 * 1.01) - 1.0) * 100.0
        self.assertAlmostEqual(
            ranking_score(2.0, 1.0, y_co=1.0, w_co=1.0, w_oo=0.0, w_τc=1.0),
            right / 100.0,
        )
        self.assertAlmostEqual(
            ranking_score(2.0, 1.0, y_co=1.0, w_co=1.0),
            (0.5 * 2.0 + 0.5 * right) / 100.0,
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

        self.assertEqual(lot_shares_for_rank(0.20, 0.20, price=50.0), 200)
        self.assertEqual(lot_shares_for_rank(0.21, 0.20, price=50.0), 400)
        self.assertEqual(lot_shares_for_rank(0.01, 0.20, price=50.0), 200)
        self.assertEqual(lot_shares_for_rank(None, 0.20, price=50.0), 0)
        self.assertEqual(
            lot_shares_for_rank(
                0.21, 0.20, amount_base=10000, amount_strong=20000, price=10.0
            ),
            2000,
        )
        self.assertEqual(
            lot_shares_for_rank(
                0.01, 0.20, amount_base=10000, amount_strong=20000, price=10.0
            ),
            1000,
        )
        self.assertEqual(
            lot_shares_for_rank(
                0.01, 0.20, amount_base=10000, amount_strong=20000, price=200.0
            ),
            100,
        )

    def test_clip_lot_to_cash(self):
        from core.paper.rebalance.rank_lots import clip_lot_to_cash

        self.assertEqual(clip_lot_to_cash(2000, 10.0, 25_000), 2000)
        self.assertEqual(clip_lot_to_cash(2000, 10.0, 15_000), 1500)
        self.assertEqual(clip_lot_to_cash(2000, 150.0, 199_591.35), 1300)
        self.assertEqual(clip_lot_to_cash(200, 10.0, 50), 0)
        self.assertEqual(clip_lot_to_cash(2000, 250.0, 500_000, 100_000), 1600)


def _cfg(**extra):
    base = {
        "rank_enter": 0.01,
        "rank_strong": 0.02,
    "cash_floor": 500_000.0,
    "holdings_mv_cap": 0.0,
    "t0_sell_blocks": {},
    "top_k": 5,
        "fusion_w_oo": 0.5,
        "fusion_w_oc": 0.5,
    }
    base.update(extra)
    return base


class TestPlanRankLotDay(unittest.TestCase):
    def test_open_10000_vs_20000_amount(self):
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
        self.assertEqual(float(by["600000"]["shares"]), 1000)
        self.assertEqual(by["600000"]["action"], "open")
        self.assertEqual(float(by["600001"]["shares"]), 2000)
        self.assertEqual(by["600001"]["action"], "open")
        self.assertEqual(int(by["600001"]["rank_i"]), 1)
        self.assertEqual(int(by["600000"]["rank_i"]), 2)
        self.assertEqual(int(by["600001"]["rank_n"]), 2)

    def test_equal_notional_across_prices(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        scored = [
            {"stock_code": "600000", "stock_name": "便宜", "y_fuse": 1.2, "y_on": 0.0},
            {"stock_code": "600001", "stock_name": "贵", "y_fuse": 1.2, "y_on": 0.0},
        ]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=1_000_000,
            prices={"600000": 10.0, "600001": 50.0},
            cfg=_cfg(),
        )
        by = {t["stock_code"]: t for t in out["buys"]}
        self.assertEqual(float(by["600000"]["shares"]), 1000)
        self.assertEqual(float(by["600001"]["shares"]), 200)
        self.assertAlmostEqual(float(by["600000"]["shares"]) * 10.0, 10_000.0)
        self.assertAlmostEqual(float(by["600001"]["shares"]) * 50.0, 10_000.0)

    def test_amount_short_of_one_lot_buys_one_lot(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        scored = [
            {"stock_code": "600000", "stock_name": "贵", "y_fuse": 1.2, "y_on": 0.0},
        ]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=1_000_000,
            prices={"600000": 200.0},
            cfg=_cfg(lot_base_amount=10000, lot_strong_amount=10000),
        )
        self.assertEqual(len(out["buys"]), 1)
        self.assertEqual(float(out["buys"][0]["shares"]), 100)
        self.assertAlmostEqual(float(out["buys"][0]["shares"]) * 200.0, 20_000.0)

    def test_stamps_oo_oc_not_trade_nowcast(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        scored = [
            {
                "stock_code": "600000",
                "stock_name": "分项",
                "predicted_score": 2.0,
                "y_tau": 1.0,
                "y_co": 0.4,
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
        self.assertAlmostEqual(float(buy["y_oo"]), 2.0)
        self.assertAlmostEqual(float(buy["y_τc"]), 1.0)
        self.assertNotIn("y_oc", buy)
        self.assertAlmostEqual(float(buy["y_co"]), 0.4)
        self.assertNotIn("y_on", buy)
        self.assertAlmostEqual(float(buy["ranking"]), 1.5)
        self.assertEqual(int(buy["rank_i"]), 1)
        self.assertAlmostEqual(float(buy["fusion_w_oo"]), 0.5)
        self.assertAlmostEqual(float(buy["fusion_w_oc"]), 0.5)

    def test_stamps_rank_lots_fusion_weights_not_dual_score(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        scored = [
            {
                "stock_code": "600000",
                "predicted_score": 2.0,
                "y_tau": 1.0,
                "fusion_w_oo": 0.9,
                "fusion_w_oc": 0.1,
                "dual_score_weights": {"w_oo": 0.9, "w_tau": 0.1},
            }
        ]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=1_000_000,
            prices={"600000": 10.0},
            cfg=_cfg(fusion_w_oo=0.2, fusion_w_oc=0.8, fusion_w_co=1.0),
        )
        buy = out["buys"][0]
        self.assertAlmostEqual(float(buy["fusion_w_oo"]), 0.2)
        self.assertAlmostEqual(float(buy["fusion_w_oc"]), 0.8)
        self.assertAlmostEqual(float(buy["fusion_w_co"]), 1.0)

    def test_stamps_aux_yhat_after_ranking(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        scored = [
            {
                "stock_code": "600000",
                "stock_name": "分项",
                "predicted_score": 2.0,
                "y_tau": 1.0,
                "y_co": 0.4,
                "y_τ30": 0.2,
                "y_τ60": -0.1,
                "y_τ90": 0.3,
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
        self.assertAlmostEqual(float(buy["y_τ30"]), 0.2)
        self.assertAlmostEqual(float(buy["y_τ60"]), -0.1)
        self.assertAlmostEqual(float(buy["y_τ90"]), 0.3)
        self.assertNotIn("y_hl", buy)
        from core.t0.close_band import blend_y_tw

        self.assertAlmostEqual(float(buy["y_τw"]), blend_y_tw(0.2, -0.1, 0.3))
        self.assertNotIn("y_w", buy)

    def test_stamps_tip_explain_fields(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        scored = [
            {
                "stock_code": "600000",
                "stock_name": "分项",
                "predicted_score": 2.0,
                "y_tau": 1.0,
                "y_co": 0.4,
                "formula_terms_tau": {"total": 1.0, "terms": [{"key": "gap_pct", "contrib": 0.2}]},
                "formula_terms_path": {"total": 1.5, "terms": [{"key": "range_pct", "contrib": 0.1}]},
                "score_formula_terms": {"total": 2.0, "terms": [{"key": "momentum", "contrib": 0.3}]},
                "formula_terms_co": {"total": 0.4, "terms": [{"key": "overnight", "contrib": 0.4}]},
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
        self.assertEqual(buy["formula_terms_tau"]["total"], 1.0)
        self.assertEqual(buy["formula_terms_path"]["total"], 1.5)
        self.assertEqual(buy["score_formula_terms"]["total"], 2.0)
        self.assertEqual(buy["formula_terms_co"]["total"], 0.4)

    def test_tip_explain_fields_slims_terms(self):
        from core.paper.rebalance.rank_lots import tip_explain_fields

        terms = [{"key": f"f{i}", "contrib": float(i)} for i in range(15)]
        out = tip_explain_fields(
            {"score_formula_terms": {"total": 1.0, "terms": terms}}
        )
        slim = (out.get("score_formula_terms") or {}).get("terms") or []
        self.assertEqual(len(slim), 10)
        self.assertEqual(slim[0]["key"], "f14")

    def test_tip_explain_fields_keeps_tau_open_z(self):
        from core.paper.rebalance.rank_lots import tip_explain_fields

        terms = [{"key": f"f{i}", "contrib": 0.01 * i} for i in range(20)]
        terms.append({"key": "ret_open_to_tau", "contrib": 0.001})
        terms.append({"key": "tau_elapsed_min", "contrib": 0.002})
        terms.append({"key": "gap_pct", "contrib": 0.003})
        terms.append({"key": "loc_hl", "contrib": 0.0004})
        terms.append({"key": "sector_ret_to_tau", "contrib": 0.0003})
        out = tip_explain_fields(
            {
                "formula_terms_tau": {
                    "total": 1.0,
                    "missing_n": 8,
                    "missing_keys": ["前缀振幅 %"],
                    "terms": terms,
                }
            }
        )
        slim = (out.get("formula_terms_tau") or {}).get("terms") or []
        keys = {t["key"] for t in slim}
        self.assertIn("ret_open_to_tau", keys)
        self.assertIn("tau_elapsed_min", keys)
        self.assertIn("gap_pct", keys)
        self.assertIn("loc_hl", keys)
        self.assertIn("sector_ret_to_tau", keys)
        self.assertLessEqual(len(slim), 24)
        self.assertEqual((out.get("formula_terms_tau") or {}).get("missing_n"), 8)
        self.assertEqual(
            (out.get("formula_terms_tau") or {}).get("missing_keys"), ["前缀振幅 %"]
        )

    def test_fuse_weights_oo_oc(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        scored = [
            {
                "stock_code": "600000",
                "stock_name": "加权",
                "predicted_score": 2.0,
                "y_tau": 1.0,
            }
        ]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=1_000_000,
            prices={"600000": 10.0},
            cfg=_cfg(fusion_w_oo=0.6, fusion_w_oc=0.4),
        )
        buy = out["buys"][0]
        self.assertAlmostEqual(float(buy["ranking"]), 1.6)

    def test_ranking_ignores_gap_and_trade(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day, ranking_pct_of

        scored = [
            {
                "stock_code": "600000",
                "stock_name": "OC",
                "predicted_score": 2.0,
                "y_tau": 1.0,
                "gap_pct": 1.0,
            }
        ]
        self.assertAlmostEqual(ranking_pct_of(scored[0], _cfg()), 1.5)
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=1_000_000,
            prices={"600000": 10.0},
            cfg=_cfg(rank_enter=0.0, rank_strong=0.02),
        )
        buy = out["buys"][0]
        self.assertAlmostEqual(float(buy["ranking"]), 1.5)

    def test_ranking_pct_of_uses_fusion_w_co(self):
        from core.paper.rebalance.rank_lots import ranking_pct_of
        from core.signal.yhat_windows import oc_with_co

        item = {"predicted_score": 2.0, "y_tau": 1.0, "y_co": 1.0}
        self.assertAlmostEqual(ranking_pct_of(item, _cfg()), 1.5)
        right = oc_with_co(1.0, 1.0, 1.0)
        self.assertAlmostEqual(
            ranking_pct_of(item, _cfg(fusion_w_co=1.0)),
            0.5 * 2.0 + 0.5 * right,
        )
        self.assertAlmostEqual(
            ranking_pct_of(item, _cfg(y_on_alpha=1.0)),
            1.5,
        )

    def test_heads_override_stale_ranking_stamp(self):
        from core.paper.rebalance.rank_lots import ranking_pct_of

        item = {"ranking": -3.65, "predicted_score": 2.30, "y_oc": 5.69, "y_tau": 0.08}
        self.assertAlmostEqual(ranking_pct_of(item, _cfg()), 0.5 * 2.30 + 0.5 * 0.08)
        # 无 ŷ_oo/ŷ_oc 才信落盘 ranking
        self.assertAlmostEqual(ranking_pct_of({"ranking": 1.2}, _cfg()), 1.2)

    def test_y_fuse_fallback_when_heads_missing(self):
        from core.paper.rebalance.rank_lots import ranking_pct_of

        self.assertAlmostEqual(
            ranking_pct_of({"y_fuse": 1.2}, _cfg()), 1.2
        )
        # 有 ŷ_oo 时不得把 y_fuse 当 ranking（那是融合输出别名）
        self.assertAlmostEqual(
            ranking_pct_of({"predicted_score": 2.0, "y_fuse": 9.9}, _cfg()),
            2.0,
        )

    def test_ranking_remaining_maps_open_to_price_tau(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day, ranking_pct_of

        item = {"predicted_score": 2.0, "y_tau": 2.0}
        fused = ranking_pct_of(item, _cfg())
        self.assertAlmostEqual(fused, 2.0)
        self.assertAlmostEqual(
            ranking_pct_of(item, _cfg(), open_px=100.0, price_tau=100.0),
            fused,
        )
        rem = ranking_pct_of(item, _cfg(), open_px=100.0, price_tau=100.5)
        rot = (100.5 / 100.0 - 1.0) * 100.0
        self.assertAlmostEqual(rem, fused - rot)
        self.assertLess(rem, fused)

        cfg = _cfg(fusion_w_oo=1.0, fusion_w_oc=0.0, lot_base_amount=30000, lot_strong_amount=30000)
        scored_plain = [{"stock_code": "600000", "predicted_score": 2.0, "y_tau": 2.0}]
        no_open = plan_rank_lot_day(
            scored=list(scored_plain),
            holdings=[],
            cash=1_000_000,
            prices={"600000": 100.5},
            cfg=cfg,
        )
        with_open = plan_rank_lot_day(
            scored=[{"stock_code": "600000", "predicted_score": 2.0, "y_tau": 2.0}],
            holdings=[],
            cash=1_000_000,
            prices={"600000": 100.5},
            opens={"600000": 100.0},
            cfg=cfg,
        )
        self.assertAlmostEqual(float(no_open["buys"][0]["ranking"]), 2.0)
        self.assertAlmostEqual(float(with_open["buys"][0]["ranking"]), 2.0 - rot)

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
            cfg=_cfg(lot_base_amount=10000, lot_strong_amount=20000),
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
            cfg=_cfg(lot_base_amount=250000, lot_strong_amount=500000, cash_floor=100_000, rank_enter=0.012, rank_strong=0.012),
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

    def test_missing_ranking_exits_held(self):
        """当日打不上分：不能假装 ranking≥0 续持。"""
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
        out = plan_rank_lot_day(
            scored=[{"stock_code": "600000"}],
            holdings=[held],
            cash=800_000,
            prices={"600000": 10.0},
            cfg=_cfg(),
            as_of="2026-03-10",
        )
        self.assertEqual(len(out["sells"]), 1)
        self.assertEqual(out["sells"][0]["action"], "exit")
        self.assertIn("缺失", out["sells"][0]["reason"])
        self.assertEqual(out["holds"], [])

    def test_held_exit_without_price_keeps_skip_scores(self):
        """缺成交价：不能静默丢掉卖单，应跳过并带当日 ŷ。"""
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
        out = plan_rank_lot_day(
            scored=[
                {
                    "stock_code": "600000",
                    "y_oo": -0.5,
                    "y_oc": -0.2,
                    "predicted_score": -0.5,
                    "predicted_score_tau": -0.2,
                }
            ],
            holdings=[held],
            cash=800_000,
            prices={},
            cfg=_cfg(),
            as_of="2026-03-10",
        )
        self.assertEqual(out["sells"], [])
        self.assertEqual(out["holds"], [])
        skip = next(s for s in out["skips"] if s.get("stock_code") == "600000")
        self.assertEqual(skip.get("action"), "skip")
        self.assertIn("无有效报价未卖出", skip.get("reason") or "")
        self.assertIsNotNone(skip.get("y_oo"))
        self.assertAlmostEqual(float(skip["y_oo"]), -0.5)
        self.assertLess(float(skip.get("ranking") or 0), 0)

    def test_sell_prices_can_exit_when_buy_px_missing(self):
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
        scored = [
            {
                "stock_code": "600000",
                "predicted_score": -0.5,
                "y_tau": -0.2,
                "y_on": 0.0,
            },
            {
                "stock_code": "600036",
                "predicted_score": 2.0,
                "y_tau": 1.0,
                "y_on": 0.5,
                "y_hl": 1.0,
            },
        ]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[held],
            cash=800_000,
            prices={},
            sell_prices={"600000": 10.0},
            cfg=_cfg(),
            as_of="2026-03-10",
        )
        self.assertEqual(len(out["sells"]), 1)
        self.assertEqual(out["sells"][0]["action"], "exit")
        self.assertFalse(
            any(
                s.get("stock_code") == "600036" and s.get("action") != "skip"
                for s in (out.get("buys") or [])
            )
        )
        buy_skip = next(
            s
            for s in out["skips"]
            if s.get("stock_code") == "600036" and s.get("side") == "buy"
        )
        self.assertIn("无有效报价", buy_skip.get("reason") or "")

    def test_zero_ranking_exits(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        held = {
            "stock_code": "600000",
            "shares": 200,
            "lots": [
                {
                    "shares": 200,
                    "bought_at": "2026-03-09T09:30:00.000",
                    "bought_date": "2026-03-09",
                }
            ],
        }
        out = plan_rank_lot_day(
            scored=[{"stock_code": "600000", "y_fuse": 0.0, "y_on": 0.0}],
            holdings=[held],
            cash=800_000,
            prices={"600000": 10.0},
            cfg=_cfg(),
            as_of="2026-03-10",
        )
        self.assertEqual(len(out["sells"]), 1)
        self.assertEqual(out["sells"][0]["action"], "exit")
        self.assertEqual(float(out["sells"][0]["shares"]), 200)
        self.assertIn("清仓", out["sells"][0]["reason"])
        self.assertEqual(out["holds"], [])

    def test_fail_enter_clears_when_held_le_100(self):
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
        out = plan_rank_lot_day(
            scored=[{"stock_code": "600000", "y_fuse": 0.5, "y_on": 0.0}],
            holdings=[held],
            cash=800_000,
            prices={"600000": 10.0},
            cfg=_cfg(),
            as_of="2026-03-10",
        )
        self.assertEqual(len(out["sells"]), 1)
        self.assertEqual(out["sells"][0]["action"], "exit")
        self.assertEqual(float(out["sells"][0]["shares"]), 100)
        self.assertIn("清仓", out["sells"][0]["reason"])
        self.assertEqual(out["holds"], [])

    def test_hard_reject_exits_even_if_ranking_positive(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        held = {
            "stock_code": "600000",
            "shares": 200,
            "lots": [
                {
                    "shares": 200,
                    "bought_at": "2026-03-09T09:30:00.000",
                    "bought_date": "2026-03-09",
                }
            ],
        }
        out = plan_rank_lot_day(
            scored=[
                {
                    "stock_code": "600000",
                    "y_fuse": 2.0,
                    "y_on": 0.0,
                    "hard_reject": True,
                    "reject_reason": "日线数据不足",
                }
            ],
            holdings=[held],
            cash=800_000,
            prices={"600000": 10.0},
            cfg=_cfg(),
            as_of="2026-03-10",
        )
        self.assertEqual(len(out["sells"]), 1)
        self.assertEqual(out["sells"][0]["action"], "exit")
        self.assertEqual(out["sells"][0]["reason"], "hard_reject 清仓")

    def test_held_exit_reason_unit(self):
        from core.paper.rebalance.rank_lots import held_exit_reason

        self.assertEqual(held_exit_reason({}, None), "ranking 缺失 清仓")
        self.assertEqual(held_exit_reason({"hard_reject": True}, 0.02), "hard_reject 清仓")
        self.assertIsNone(held_exit_reason({}, 0.0))
        self.assertIsNone(held_exit_reason({}, 0.001))
        self.assertIn("<0%", held_exit_reason({}, -0.001) or "")

    def test_held_fail_enter_exits(self):
        """已持仓未过入场：清仓，不续持。"""
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
        scored = [{"stock_code": "600000", "predicted_score": 0.8, "y_tau": 0.8, "y_on": 0.0}]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[held],
            cash=800_000,
            prices={"600000": 10.0},
            cfg=_cfg(rank_enter=0.012, rank_strong=0.012),
            as_of="2026-03-10",
        )
        self.assertEqual(len(out["sells"]), 1)
        self.assertEqual(out["sells"][0]["action"], "exit")
        self.assertEqual(float(out["sells"][0]["shares"]), 200)
        self.assertEqual(out["buys"], [])
        self.assertEqual(out["holds"], [])

    def test_negative_oo_exits_if_ranking_below_enter(self):
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
        scored = [{"stock_code": "600000", "predicted_score": -0.2, "y_tau": 1.0}]
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

    def test_stale_paper_hard_reject_does_not_block_open(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        scored = [
            {
                "stock_code": "600869",
                "stock_name": "远东股份",
                "predicted_score": 2.588,
                "y_tau": 0.143,
                "paper_hard_reject": True,
                "paper_reject_reason": "近5日涨幅过大",
            }
        ]
        opened = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=1_000_000,
            prices={"600869": 22.0},
            cfg=_cfg(rank_enter=0.0),
        )
        self.assertTrue(opened["buys"])
        self.assertEqual(opened["buys"][0]["stock_code"], "600869")

        held = {
            "stock_code": "600869",
            "stock_name": "远东股份",
            "shares": 200,
            "lots": [
                {
                    "shares": 200,
                    "bought_at": "2026-09-07T09:30:00.000",
                    "bought_date": "2026-09-07",
                }
            ],
        }
        held_out = plan_rank_lot_day(
            scored=scored,
            holdings=[held],
            cash=800_000,
            prices={"600869": 22.0},
            cfg=_cfg(rank_enter=0.0),
            as_of="2026-09-09",
        )
        self.assertEqual(held_out["sells"], [])
        self.assertTrue(held_out["buys"])
        self.assertEqual(held_out["buys"][0]["stock_code"], "600869")

    def test_oc_weight_zero_ignores_tau_on_exit(self):
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
        scored = [{"stock_code": "600000", "predicted_score": -0.2, "y_tau": 1.0}]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[held],
            cash=800_000,
            prices={"600000": 10.0},
            cfg=_cfg(fusion_w_oo=1.0, fusion_w_oc=0.0),
            as_of="2026-03-10",
        )
        self.assertEqual(len(out["sells"]), 1)
        self.assertEqual(out["sells"][0]["action"], "exit")

    def test_positive_oo_exits_if_oc_sinks_ranking(self):
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
        scored = [{"stock_code": "600000", "predicted_score": 0.1, "y_tau": -2.0}]
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

    def test_not_in_top_still_holds_if_ranking_above_enter(self):
        """过入场但不进 top_k：不发「持」动作、不清仓，仓位继续留着。"""
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
            {"stock_code": "600000", "y_fuse": 1.5, "y_on": 0.0},
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
        self.assertEqual(out["holds"], [])
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

    def test_enter_skip_is_logged_on_plan(self):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        scored = [
            {
                "stock_code": "600000",
                "y_oo": -1.0,
                "y_oc": -1.0,
                "y_hl": -0.4,
            }
        ]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[],
            cash=1_000_000,
            prices={"600000": 10.0},
            cfg=_cfg(rank_enter=0.001, cash_floor=0.0),
        )
        self.assertEqual(out["buys"], [])
        self.assertTrue(
            any("ranking" in str(s.get("reason") or "") for s in out["skips"]),
            out["skips"],
        )

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

    def test_clips_buy_shares_when_cash_short_after_exit(self):
        """清仓进账后仍不够整手：缩到整百买入，不跳过。"""
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        held = {
            "stock_code": "601606",
            "stock_name": "长城军工",
            "shares": 2000,
            "lots": [
                {
                    "shares": 2000,
                    "bought_at": "2026-09-04T09:30:00.000",
                    "bought_date": "2026-09-04",
                }
            ],
        }
        scored = [
            {"stock_code": "601606", "y_fuse": 0.2},
            {"stock_code": "600183", "y_fuse": 3.0, "y_tau": 3.0, "y_hl": 1.0},
        ]
        out = plan_rank_lot_day(
            scored=scored,
            holdings=[held],
            cash=131_000.0,
            prices={"601606": 34.28, "600183": 150.0},
            cfg=_cfg(cash_floor=0.0, lot_base_amount=300000, lot_strong_amount=300000, rank_enter=0.012, rank_strong=0.018),
            as_of="2026-09-07",
        )
        self.assertEqual(len(out["sells"]), 1)
        self.assertEqual(out["sells"][0]["stock_code"], "601606")
        buys = [b for b in out["buys"] if b.get("stock_code") == "600183"]
        self.assertEqual(len(buys), 1, out)
        self.assertEqual(float(buys[0]["shares"]), 1300)
        self.assertEqual(buys[0].get("lot_kind"), "clipped")
        self.assertIn("现金不够2000", buys[0].get("reason") or "")
        self.assertFalse(
            any(
                s.get("stock_code") == "600183" and "现金不足" in str(s.get("reason") or "")
                for s in out["skips"]
            )
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
            cfg=_cfg(holdings_mv_cap=50_000, lot_base_amount=40000, lot_strong_amount=40000),
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
        self.assertTrue(
            any("未平做 T 腿" in str(s.get("reason") or "") for s in out["skips"])
        )
        self.assertFalse(any(h.get("action") == "hold" for h in out["holds"]))


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

    def test_live_reads_saved_lot_sizes(self):
        from core.paper.rebalance.rank_lots import get_rank_lot_cfg

        cfg = get_rank_lot_cfg(
            {
                "rules": {
                    "execution": {
                        "rebalance_timing": {
                            "rank_lots": {"lot_base_amount": 30000, "lot_strong_amount": 50000}
                        }
                    }
                }
            }
        )
        self.assertEqual(int(cfg["lot_base_amount"]), 30000)
        self.assertEqual(int(cfg["lot_strong_amount"]), 50000)

    def test_live_lot_default_is_10000_20000(self):
        from core.paper.rebalance.rank_lots import get_rank_lot_cfg

        cfg = get_rank_lot_cfg({"initial_cash": 1_000_000, "cash": 1_000_000})
        self.assertEqual(int(cfg["lot_base_amount"]), 10000)
        self.assertEqual(int(cfg["lot_strong_amount"]), 20000)


class TestYTauOf(unittest.TestCase):
    def test_prefers_y_tau_then_y_τc(self):
        from core.paper.rebalance.rank_lots import y_tau_of

        self.assertEqual(y_tau_of({"y_tau": 0.4, "y_τc": 0.9}), 0.4)
        self.assertEqual(y_tau_of({"y_τc": 0.9}), 0.9)
        self.assertIsNone(y_tau_of({"predicted_score_tau": 0.9}))
        self.assertIsNone(y_tau_of({"score_rem": 0.3}))
        self.assertIsNone(y_tau_of({}))


class TestEnterGates(unittest.TestCase):
    def _item(self, **extra):
        row = {
            "predicted_score": 2.0,
            "y_tau": 1.0,
            "y_hl": 1.0,
        }
        row.update(extra)
        return row

    def _cfg(self, **extra):
        base = {
            "rank_enter": 0.001,
            "y_enter_enabled": True,
            "y_enter_alt_enabled": True,
        }
        base.update(extra)
        return base

    def test_or_across_gates(self):
        from core.paper.rebalance.rank_lots import rank_lot_enter_skip_reason

        item = self._item()
        self.assertIsNone(
            rank_lot_enter_skip_reason(
                item,
                self._cfg(rank_enter=0.02, rank_enter_alt=0.01),
                rs=0.015,
            )
        )
        skip = rank_lot_enter_skip_reason(
            item,
            self._cfg(rank_enter=0.02, rank_enter_alt=0.02),
            rs=0.015,
        )
        self.assertIsNotNone(skip)
        self.assertIn("ranking", skip)

    def test_y_oo_enter_ignored(self):
        from core.paper.rebalance.rank_lots import rank_lot_enter_skip_reason

        self.assertIsNone(
            rank_lot_enter_skip_reason(
                self._item(predicted_score=0.05),
                self._cfg(y_oo_enter=0.1, y_oo_enter_alt=0.1, y_oc_enter=0.1),
                rs=0.02,
            )
        )

    def test_y_oo_gt0_blocks_when_enabled(self):
        from core.paper.rebalance.rank_lots import rank_lot_enter_skip_reason

        skip_oo = rank_lot_enter_skip_reason(
            self._item(predicted_score=-0.05, y_tau=1.0),
            self._cfg(y_oo_gt0=True),
            rs=0.02,
        )
        self.assertIsNotNone(skip_oo)
        self.assertIn("y_oo", skip_oo)
        skip_oc = rank_lot_enter_skip_reason(
            self._item(predicted_score=2.0, y_tau=-0.05),
            self._cfg(y_τc_gt0=True),
            rs=0.02,
        )
        self.assertIsNotNone(skip_oc)
        self.assertIn("y_τc", skip_oc)
        self.assertIsNone(
            rank_lot_enter_skip_reason(
                self._item(predicted_score=2.0, y_tau=1.0),
                self._cfg(y_oo_gt0=True, y_τc_gt0=True),
                rs=0.02,
            )
        )

    def test_y_oo_gt0_off_ignores_sign(self):
        from core.paper.rebalance.rank_lots import rank_lot_enter_skip_reason

        self.assertIsNone(
            rank_lot_enter_skip_reason(
                self._item(predicted_score=-0.05, y_tau=-0.05),
                self._cfg(y_oo_gt0=False, y_τc_gt0=False),
                rs=0.02,
            )
        )

    def test_missing_y_oo_does_not_block_when_gt0_on(self):
        from core.paper.rebalance.rank_lots import rank_lot_enter_skip_reason

        self.assertIsNone(
            rank_lot_enter_skip_reason(
                {"y_tau": 1.0, "y_hl": 1.0},
                self._cfg(y_oo_gt0=True),
                rs=0.02,
            )
        )

    def test_both_gates_off(self):
        from core.paper.rebalance.rank_lots import rank_lot_enter_skip_reason

        skip = rank_lot_enter_skip_reason(
            self._item(),
            self._cfg(y_enter_enabled=False, y_enter_alt_enabled=False),
            rs=0.02,
        )
        self.assertEqual(skip, "门槛1/2 均未启用")

    def test_missing_y_oo_does_not_block(self):
        from core.paper.rebalance.rank_lots import rank_lot_enter_skip_reason

        self.assertIsNone(
            rank_lot_enter_skip_reason(
                {"y_tau": 1.0, "y_hl": 1.0},
                self._cfg(y_oo_enter=0.1),
                rs=0.02,
            )
        )

    def test_y_hl_sign_does_not_block(self):
        from core.paper.rebalance.rank_lots import rank_lot_enter_skip_reason

        item = self._item(y_hl=-5.0)
        self.assertIsNone(
            rank_lot_enter_skip_reason(
                item,
                self._cfg(),
                rs=0.02,
            )
        )


if __name__ == "__main__":
    unittest.main()
