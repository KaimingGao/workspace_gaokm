"""滚动可预测性票档：ŷ_oo Holdout OOS 前半。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestPredictabilityTiers(unittest.TestCase):
    def test_score_panel_lookback_is_short(self):
        from core.research.predictability_tiers import _score_panel_lookback

        lb = _score_panel_lookback(20, 1)
        self.assertGreaterEqual(lb, 40)
        self.assertLessEqual(lb, 200)
        self.assertLess(lb, 600)

    def test_accumulate_uses_short_lookback_with_research_model(self):
        """有研究套时不应按 ŷ_oo 全训练窗（600）拉面板。"""
        from core.research.predictability_tiers import accumulate_holdout_oos_by_code
        from core.signal.return_score import ReturnScoreModel

        captured = {}

        def _fake_load(codes, **kwargs):
            captured["lookback"] = kwargs.get("lookback")
            captured["n_codes"] = len(list(codes or []))
            # 最少两只、短序列即可让后续早退或走完；这里返回空导致 early fail 也够断言 lookback
            return {}, ["no_bars"], {}

        research = ReturnScoreModel(
            intercept=0.0, coefficients={"momentum": 1.0}, feature_zscore=False
        )
        with patch(
            "core.research.predictability_tiers._load_research_oo_model",
            return_value=(research, {"ok": True, "path": "/tmp/r.json"}),
        ), patch(
            "core.research.portfolio_bars.load_portfolio_stock_bars",
            side_effect=_fake_load,
        ):
            out = accumulate_holdout_oos_by_code(
                ["000001", "000002", "000003"],
                holdout_n=20,
                lookback=600,
                use_research_model=True,
            )
        self.assertIn("lookback", captured)
        self.assertLessEqual(int(captured["lookback"]), 200)
        self.assertLess(int(captured["lookback"]), 600)
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("lookback_requested"), 600)

    def test_research_model_keeps_only_holdout_dates(self):
        """有研究套时特征行只建在 Holdout 测试日上。"""
        from datetime import date, timedelta

        from core.research.predictability_tiers import accumulate_holdout_oos_by_code
        from core.signal.return_score import ReturnScoreModel

        start = date(2024, 1, 2)
        bars = []
        for i in range(40):
            d = (start + timedelta(days=i)).isoformat()
            bars.append(
                {"date": d, "open": 10.0, "close": 10.0, "high": 11.0, "low": 9.0}
            )
        seen = {}

        def _fake_collect(code_bars, **kwargs):
            keep = {str(d)[:10] for d in (kwargs.get("keep_dates") or [])}
            seen["keep"] = keep
            use = [str(b["date"])[:10] for b in code_bars if str(b["date"])[:10] in keep]
            return ([{"momentum": 1.0}] * len(use), [0.2] * len(use), use)

        research = ReturnScoreModel(
            intercept=0.0,
            coefficients={"momentum": 1.0},
            feature_zscore=False,
        )

        def _fake_load(codes, **kwargs):
            return (
                {str(c): list(bars) for c in codes},
                [],
                {},
            )

        with patch(
            "core.research.predictability_tiers._load_research_oo_model",
            return_value=(research, {"ok": True}),
        ), patch(
            "core.research.portfolio_bars.load_portfolio_stock_bars",
            side_effect=_fake_load,
        ), patch(
            "core.research.oo_panel.collect_oo_panel",
            side_effect=_fake_collect,
        ):
            out = accumulate_holdout_oos_by_code(
                ["000001", "000002"],
                holdout_n=10,
                lookback=600,
                use_research_model=True,
            )
        self.assertTrue(out.get("ok"), out)
        last10 = {str(b["date"])[:10] for b in bars[-10:]}
        self.assertEqual(seen.get("keep"), last10)
        self.assertEqual(out.get("n_test"), 20)
        self.assertIsNone(out.get("n_train"))

    def test_research_model_zscores_same_day_before_predict(self):
        """截面 ŷ_oo 用当天全体票的 z，不用 0–100 原始分直接乘 β。"""
        from datetime import date, timedelta

        from core.research.predictability_tiers import accumulate_holdout_oos_by_code
        from core.signal.return_score import ReturnScoreModel

        start = date(2024, 1, 2)
        bars = []
        for i in range(40):
            d = (start + timedelta(days=i)).isoformat()
            bars.append(
                {"date": d, "open": 10.0, "close": 10.0, "high": 11.0, "low": 9.0}
            )

        def _fake_collect(code_bars, **kwargs):
            keep = {str(d)[:10] for d in (kwargs.get("keep_dates") or [])}
            use = [str(b["date"])[:10] for b in code_bars if str(b["date"])[:10] in keep]
            raw = 80.0 if kwargs.get("stock_code") == "000001" else 40.0
            return ([{"relative_strength": raw}] * len(use), [0.2] * len(use), use)

        research = ReturnScoreModel(
            intercept=0.0,
            coefficients={"z_oo_relative_strength": 1.0},
            feature_zscore=True,
            zscore_scope="cross_section",
        )

        def _fake_load(codes, **kwargs):
            return ({str(c): list(bars) for c in codes}, [], {})

        with patch(
            "core.research.predictability_tiers._load_research_oo_model",
            return_value=(research, {"ok": True}),
        ), patch(
            "core.research.portfolio_bars.load_portfolio_stock_bars",
            side_effect=_fake_load,
        ), patch(
            "core.research.oo_panel.collect_oo_panel",
            side_effect=_fake_collect,
        ), patch(
            "core.research.predictability_tiers._score_tau_holdout_into_acc",
            return_value={"ok": False, "reason": "skip"},
        ):
            out = accumulate_holdout_oos_by_code(
                ["000001", "000002"],
                holdout_n=10,
                lookback=600,
                use_research_model=True,
            )
        self.assertTrue(out.get("ok"), out)
        hi = out["acc"]["000001"]["yhats"]
        lo = out["acc"]["000002"]["yhats"]
        self.assertTrue(hi)
        self.assertTrue(lo)
        self.assertTrue(all(abs(v - 1.0) < 1e-6 for v in hi))
        self.assertTrue(all(abs(v - (-1.0)) < 1e-6 for v in lo))

    def test_tau_predictions_zscore_by_day(self):
        from core.research.predictability_tiers import _tau_predictions

        doc = {
            "return_model": {
                "intercept": 0.0,
                "coefficients": {"z_τc_gap_pct": 1.0},
                "feature_zscore": True,
                "zscore_scope": "cross_section",
            }
        }
        preds = _tau_predictions(
            doc,
            [{"gap_pct": 2.0}, {"gap_pct": 0.0}, {"gap_pct": 9.0}],
            ["2026-08-13", "2026-08-13", "2026-08-14"],
        )
        self.assertAlmostEqual(preds[0], 1.0)
        self.assertAlmostEqual(preds[1], -1.0)
        # 当天只有一只票，截面标准差不足，落在均值上
        self.assertAlmostEqual(preds[2], 0.0)

    def test_keep_dates_skips_other_days(self):
        from datetime import date, timedelta

        from core.research.oo_panel import collect_oo_panel

        start = date(2024, 1, 2)
        bars = []
        for i in range(80):
            d = (start + timedelta(days=i)).isoformat()
            bars.append(
                {"date": d, "open": 10.0, "close": 10.0, "high": 11.0, "low": 9.0}
            )
        calls = {"n": 0}

        def _fake_row(*args, **kwargs):
            calls["n"] += 1
            return {"momentum": 1.0}

        keep = {bars[-2]["date"]}
        with patch(
            "core.research.oo_panel._research_sub_scores",
            side_effect=_fake_row,
        ):
            xs, ys, dates = collect_oo_panel(
                bars,
                horizon_days=1,
                min_history=5,
                max_window=5,
                pit_fundamentals=False,
                keep_dates=keep,
            )
        self.assertEqual(calls["n"], 1)
        self.assertEqual(dates, [bars[-2]["date"]])
        self.assertEqual(len(xs), 1)
        self.assertEqual(len(ys), 1)

    def test_normalize_head_is_tau_or_oo(self):
        from core.research.predictability_tiers import HEAD_OO, HEAD_TAU, _normalize_head

        self.assertEqual(_normalize_head("tau"), HEAD_TAU)
        self.assertEqual(_normalize_head("y_τc"), HEAD_TAU)
        self.assertEqual(_normalize_head("ŷ_τc"), HEAD_TAU)
        self.assertEqual(_normalize_head("y_oc"), HEAD_OO)
        self.assertEqual(_normalize_head("y_tau"), HEAD_OO)
        self.assertEqual(_normalize_head(None), HEAD_OO)

    def test_slim_tier_rows_drops_series(self):
        from core.research.predictability_tiers import slim_tier_rows

        rows = slim_tier_rows(
            [
                {
                    "code": "600519",
                    "name": "贵州茅台",
                    "tier": "A",
                    "hit_rate": 0.7,
                    "n_valid": 9,
                    "n_days": 10,
                    "ic": 0.12,
                    "realized_pct": 3.5,
                    "mean_y_pos": 0.4,
                    "n_y_pos": 5,
                    "f1_pos": 0.75,
                    "precision_pos": 0.8,
                    "recall_pos": 0.7,
                    "mean_y_pos_tau": 0.2,
                    "n_y_pos_tau": 4,
                    "f1_pos_tau": 0.6,
                    "precision_pos_tau": 0.7,
                    "recall_pos_tau": 0.55,
                    "yhats": [1, 2],
                    "realized": [1],
                    "dates": ["2026-01-01"],
                },
                "skip",
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tier"], "A")
        self.assertEqual(rows[0]["realized_pct"], 3.5)
        self.assertEqual(rows[0]["mean_y_pos"], 0.4)
        self.assertEqual(rows[0]["n_y_pos"], 5)
        self.assertEqual(rows[0]["f1_pos"], 0.75)
        self.assertEqual(rows[0]["precision_pos"], 0.8)
        self.assertEqual(rows[0]["recall_pos"], 0.7)
        self.assertEqual(rows[0]["mean_y_pos_tau"], 0.2)
        self.assertEqual(rows[0]["f1_pos_tau"], 0.6)
        self.assertNotIn("yhats", rows[0])
        self.assertNotIn("realized", rows[0])

    def test_code_holdout_ret_pct(self):
        from core.research.predictability_tiers import (
            attach_code_holdout_rets,
            code_holdout_ret_pct,
        )

        dates = ["d1", "d2", "d3"]
        bars = [
            {"date": "d1", "close": 100.0},
            {"date": "d2", "close": 105.0},
            {"date": "d3", "close": 110.0},
        ]
        self.assertAlmostEqual(code_holdout_ret_pct(bars, dates=dates), 10.0)
        self.assertAlmostEqual(
            code_holdout_ret_pct(bars, dates=["d1", "d2"]), 5.0
        )
        self.assertIsNone(code_holdout_ret_pct([], dates=dates))
        rows = [{"code": "000001"}, {"code": "000002"}]
        attach_code_holdout_rets(
            rows,
            {"000001": bars},
            dates=dates,
        )
        self.assertAlmostEqual(float(rows[0]["realized_pct"]), 10.0)
        self.assertIsNone(rows[1]["realized_pct"])

    def test_tier_window_cs_rank_ic_and_icir(self):
        from core.research.predictability_tiers import summarize_tier_window_cs

        days = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
        acc = {}
        for d_i, day in enumerate(days):
            for i in range(6):
                code = f"{i:06d}"
                slot = acc.setdefault(
                    code, {"yhats": [], "realized": [], "pair_dates": []}
                )
                y = float(i) - 2.5
                slot["realized"].append(y)
                slot["yhats"].append(y + 0.15 * d_i * ((i % 2) - 0.4))
                slot["pair_dates"].append(day)
        out = summarize_tier_window_cs(acc, min_names=5)
        self.assertGreater(float(out["cs_rank_ic"]), 0.5)
        self.assertIsNotNone(out["cs_icir"])
        self.assertGreaterEqual(int(out["cs_day_count"] or 0), 3)
        self.assertEqual(out.get("head"), "oo")
        thin = summarize_tier_window_cs(acc, codes=["000000", "000001"], min_names=5)
        self.assertIsNone(thin["cs_rank_ic"])

    def test_tier_window_cs_tau_head(self):
        from core.research.predictability_tiers import summarize_tier_window_cs

        days = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
        acc = {}
        for d_i, day in enumerate(days):
            for i in range(6):
                code = f"{i:06d}"
                slot = acc.setdefault(
                    code,
                    {
                        "yhats": [],
                        "realized": [],
                        "pair_dates": [],
                        "tau_yhats": [],
                        "tau_realized": [],
                        "tau_pair_dates": [],
                    },
                )
                y = float(i) - 2.5
                slot["tau_realized"].append(y)
                slot["tau_yhats"].append(y + 0.1 * d_i)
                slot["tau_pair_dates"].append(day)
        out = summarize_tier_window_cs(acc, min_names=5, head="tau")
        self.assertEqual(out.get("head"), "tau")
        self.assertGreater(float(out["cs_rank_ic"]), 0.5)
        empty_oo = summarize_tier_window_cs(acc, min_names=5, head="oo")
        self.assertIsNone(empty_oo["cs_rank_ic"])

    def test_watching_name_map_fills_gaps_offline(self):
        """落盘 names 半空时，用码表（a_code_name / spot）补齐，不依赖行情。"""
        from core.research.predictability_tiers import _watching_name_map

        watching = {
            "watchlist": ["000001", "600519", "300750"],
            "watchlist_names": ["平安银行", "", ""],
            "sources": [],
        }
        with patch(
            "core.watching.store.read_watching",
            return_value=watching,
        ), patch(
            "core.t0.intraday._load_a_code_name_map",
            return_value={
                "000001": "平安银行",
                "600519": "贵州茅台",
                "300750": "宁德时代",
            },
        ):
            out = _watching_name_map(["000001", "600519", "300750"])
        self.assertEqual(out.get("000001"), "平安银行")
        self.assertEqual(out.get("600519"), "贵州茅台")
        self.assertEqual(out.get("300750"), "宁德时代")

    def test_code_name_map_falls_back_to_spot_a_em(self):
        """无 a_code_name 时，用 spot_a_em 补名称（忽略现货 24h TTL）。"""
        import json
        import tempfile
        from core.t0 import intraday as mod

        spot = {
            "fetched_at": 1.0,
            "rows": [
                {"代码": "000779", "名称": "甘咨询"},
                {"代码": "000615", "名称": "奥园美谷"},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            store = os.path.join(tmp, "store")
            os.makedirs(store, exist_ok=True)
            with open(os.path.join(store, "spot_a_em.json"), "w", encoding="utf-8") as f:
                json.dump(spot, f)
            mod._CODE_NAME_CACHE = {}
            mod._CODE_NAME_CACHE_TS = 0.0
            with patch("core.paths.DATA_DIR", tmp):
                out = mod._load_a_code_name_map()
            self.assertEqual(out.get("000779"), "甘咨询")
            self.assertEqual(out.get("000615"), "奥园美谷")
            mod._CODE_NAME_CACHE = {}
            mod._CODE_NAME_CACHE_TS = 0.0

    def test_assign_tiers_by_rank_score(self):
        from core.research.predictability_tiers import assign_tiers_by_rank_score

        # 8 票：A=1、B=3 → 1 只 A、3 只 B、其余 C
        rows = [
            {
                "code": f"{i:06d}",
                "hit_rate": 0.9 - i * 0.05,
                "n_valid": 10,
                "hit_rate_tau": 0.85 - i * 0.04,
                "n_valid_tau": 10,
                "f1_pos": round(0.9 - i * 0.05, 4),
                "f1_pos_tau": round(0.85 - i * 0.04, 4),
                "mean_y_pos": 0.1,
                "mean_y_pos_tau": 0.1,
            }
            for i in range(8)
        ]
        assign_tiers_by_rank_score(rows, require_tau=True, a_count=1, b_count=3)
        by = {r["code"]: r for r in rows}
        self.assertEqual(by["000000"]["tier"], "A")
        self.assertEqual(by["000001"]["tier"], "B")
        self.assertEqual(by["000002"]["tier"], "B")
        self.assertEqual(by["000003"]["tier"], "B")
        self.assertEqual(by["000004"]["tier"], "C")
        self.assertEqual(by["000007"]["tier"], "C")
        self.assertGreater(by["000000"]["score"], by["000001"]["score"])
        self.assertAlmostEqual(by["000000"]["score"], 0.5 * 0.9 + 0.5 * 0.85)
        self.assertNotIn("rank_oo", by["000000"])
        self.assertNotIn("rank_tau", by["000000"])

        # 无 F1 → C，不占 A/B 名额
        small = [
            {
                "code": "a",
                "hit_rate": 0.8,
                "n_valid": 5,
                "f1_pos": 0.8,
                "mean_y_pos": 0.2,
            },
            {
                "code": "b",
                "hit_rate": 0.7,
                "n_valid": 5,
                "f1_pos": 0.7,
                "mean_y_pos": 0.1,
            },
            {"code": "c", "hit_rate": None, "n_valid": 0},
        ]
        assign_tiers_by_rank_score(small, require_tau=False, a_count=1, b_count=0)
        by2 = {r["code"]: r for r in small}
        self.assertEqual(by2["a"]["tier"], "A")
        self.assertEqual(by2["b"]["tier"], "C")
        self.assertEqual(by2["c"]["tier"], "C")
        self.assertIsNone(by2["c"]["score"])

        # A 档 F1 门槛：Score 最高但 OO_F1 < 20% 不占 A，名额顺延给过线的票
        gated = [
            {
                "code": "hi",
                "hit_rate": 0.90,
                "n_valid": 10,
                "hit_rate_tau": 0.80,
                "n_valid_tau": 10,
                "f1_pos": 0.19,
                "f1_pos_tau": 0.90,
                "mean_y_pos": 0.3,
                "mean_y_pos_tau": 0.3,
            },
            {
                "code": "ok",
                "hit_rate": 0.48,
                "n_valid": 10,
                "hit_rate_tau": 0.60,
                "n_valid_tau": 10,
                "f1_pos": 0.10,
                "f1_pos_tau": 0.50,
                "mean_y_pos": 0.2,
                "mean_y_pos_tau": 0.2,
            },
            {
                "code": "mid",
                "hit_rate": 0.55,
                "n_valid": 10,
                "hit_rate_tau": 0.55,
                "n_valid_tau": 10,
                "f1_pos": 0.30,
                "f1_pos_tau": 0.30,
                "mean_y_pos": 0.1,
                "mean_y_pos_tau": 0.1,
            },
        ]
        assign_tiers_by_rank_score(
            gated,
            require_tau=True,
            a_count=1,
            b_count=1,
            a_f1=0.20,
            tau_a_f1=0.20,
        )
        by3 = {r["code"]: r for r in gated}
        self.assertEqual(by3["mid"]["tier"], "A")
        self.assertEqual(by3["hi"]["tier"], "B")
        self.assertEqual(by3["ok"]["tier"], "C")
        self.assertGreater(by3["hi"]["score"], by3["mid"]["score"])

        # 无 ŷ_τc 时不看 TC_F1 门槛；恰好 20% 过线
        oo_only = [
            {
                "code": "edge",
                "hit_rate": 0.50,
                "n_valid": 10,
                "hit_rate_tau": 0.10,
                "n_valid_tau": 10,
                "f1_pos": 0.20,
                "f1_pos_tau": 0.01,
                "mean_y_pos": 0.05,
            },
            {
                "code": "low",
                "hit_rate": 0.40,
                "n_valid": 10,
                "hit_rate_tau": 0.90,
                "n_valid_tau": 10,
                "f1_pos": 0.15,
                "f1_pos_tau": 0.99,
                "mean_y_pos": 0.2,
            },
        ]
        assign_tiers_by_rank_score(
            oo_only, require_tau=False, a_count=1, b_count=1, a_f1=20, tau_a_f1=20
        )
        by4 = {r["code"]: r for r in oo_only}
        self.assertEqual(by4["edge"]["tier"], "A")
        self.assertEqual(by4["low"]["tier"], "B")

        # A 档另须 OO_R+>0（无 τc 时 TC 门槛不生效）：≤0 / 缺 不占 A
        rpos_gated = [
            {
                "code": "neg",
                "hit_rate": 0.90,
                "n_valid": 10,
                "f1_pos": 0.90,
                "mean_y_pos": -0.01,
            },
            {
                "code": "zero",
                "hit_rate": 0.80,
                "n_valid": 10,
                "f1_pos": 0.80,
                "mean_y_pos": 0.0,
            },
            {
                "code": "miss",
                "hit_rate": 0.70,
                "n_valid": 10,
                "f1_pos": 0.70,
                "mean_y_pos": None,
            },
            {
                "code": "pos",
                "hit_rate": 0.60,
                "n_valid": 10,
                "f1_pos": 0.60,
                "mean_y_pos": 0.02,
            },
        ]
        assign_tiers_by_rank_score(
            rpos_gated, require_tau=False, a_count=1, b_count=2, a_f1=0.20
        )
        by5 = {r["code"]: r for r in rpos_gated}
        self.assertEqual(by5["pos"]["tier"], "A")
        self.assertEqual(by5["neg"]["tier"], "B")
        self.assertEqual(by5["zero"]["tier"], "B")
        self.assertEqual(by5["miss"]["tier"], "C")

        # 仅开 TC：OO≤0 但 TC>0 且有 τc → 过 A
        rpos_tc_only = [
            {
                "code": "oo_neg_tc_pos",
                "hit_rate": 0.90,
                "n_valid": 10,
                "hit_rate_tau": 0.70,
                "n_valid_tau": 10,
                "f1_pos": 0.80,
                "f1_pos_tau": 0.70,
                "mean_y_pos": -0.01,
                "mean_y_pos_tau": 0.05,
            },
            {
                "code": "both_neg",
                "hit_rate": 0.80,
                "n_valid": 10,
                "hit_rate_tau": 0.70,
                "n_valid_tau": 10,
                "f1_pos": 0.60,
                "f1_pos_tau": 0.60,
                "mean_y_pos": -0.02,
                "mean_y_pos_tau": -0.01,
            },
        ]
        assign_tiers_by_rank_score(
            rpos_tc_only,
            require_tau=True,
            a_count=1,
            b_count=1,
            a_f1=0.20,
            tau_a_f1=0.20,
            require_oo_r_pos=False,
            require_tc_r_pos=True,
        )
        by5b = {r["code"]: r for r in rpos_tc_only}
        self.assertEqual(by5b["oo_neg_tc_pos"]["tier"], "A")
        self.assertEqual(by5b["both_neg"]["tier"], "B")

        # 两侧都开：须 OO 与 TC 都 >0
        rpos_both = [
            {
                "code": "oo_only",
                "hit_rate": 0.90,
                "n_valid": 10,
                "hit_rate_tau": 0.80,
                "n_valid_tau": 10,
                "f1_pos": 0.90,
                "f1_pos_tau": 0.80,
                "mean_y_pos": 0.05,
                "mean_y_pos_tau": -0.01,
            },
            {
                "code": "both_pos",
                "hit_rate": 0.70,
                "n_valid": 10,
                "hit_rate_tau": 0.70,
                "n_valid_tau": 10,
                "f1_pos": 0.50,
                "f1_pos_tau": 0.50,
                "mean_y_pos": 0.02,
                "mean_y_pos_tau": 0.03,
            },
        ]
        assign_tiers_by_rank_score(
            rpos_both,
            require_tau=True,
            a_count=1,
            b_count=1,
            a_f1=0.20,
            tau_a_f1=0.20,
            require_oo_r_pos=True,
            require_tc_r_pos=True,
        )
        by5c = {r["code"]: r for r in rpos_both}
        self.assertEqual(by5c["both_pos"]["tier"], "A")
        self.assertEqual(by5c["oo_only"]["tier"], "B")

        # 关闭两侧 R+ 门槛后，Score 最高且命中过线者进 A
        rpos_off = [
            {
                "code": "neg",
                "hit_rate": 0.90,
                "n_valid": 10,
                "f1_pos": 0.90,
                "mean_y_pos": -0.01,
            },
            {
                "code": "pos",
                "hit_rate": 0.60,
                "n_valid": 10,
                "f1_pos": 0.60,
                "mean_y_pos": 0.02,
            },
        ]
        assign_tiers_by_rank_score(
            rpos_off,
            require_tau=False,
            a_count=1,
            b_count=1,
            a_f1=0.20,
            require_oo_r_pos=False,
            require_tc_r_pos=False,
        )
        by6 = {r["code"]: r for r in rpos_off}
        self.assertEqual(by6["neg"]["tier"], "A")
        self.assertEqual(by6["pos"]["tier"], "B")

    def test_score_is_half_oo_f1_plus_half_tc_f1(self):
        from core.research.predictability_tiers import assign_tiers_by_rank_score

        rows = [
            {
                "code": "hi_oo",
                "hit_rate": 0.80,
                "n_valid": 10,
                "hit_rate_tau": 0.80,
                "n_valid_tau": 10,
                "f1_pos": 0.90,
                "f1_pos_tau": 0.10,
                "mean_y_pos": 0.1,
                "mean_y_pos_tau": 0.1,
            },
            {
                "code": "bal",
                "hit_rate": 0.60,
                "n_valid": 10,
                "hit_rate_tau": 0.60,
                "n_valid_tau": 10,
                "f1_pos": 0.60,
                "f1_pos_tau": 0.60,
                "mean_y_pos": 0.1,
                "mean_y_pos_tau": 0.1,
            },
            {
                "code": "oo_gap",
                "hit_rate": 0.70,
                "n_valid": 10,
                "hit_rate_tau": 0.70,
                "n_valid_tau": 10,
                "f1_pos": 0.80,
                "f1_pos_tau": None,
                "mean_y_pos": 0.1,
                "mean_y_pos_tau": 0.1,
            },
            {
                "code": "none",
                "hit_rate": 0.99,
                "n_valid": 10,
                "hit_rate_tau": 0.99,
                "n_valid_tau": 10,
                "f1_pos": None,
                "f1_pos_tau": None,
                "mean_y_pos": 0.2,
                "mean_y_pos_tau": 0.2,
            },
        ]
        assign_tiers_by_rank_score(
            rows, require_tau=True, a_count=1, b_count=2, a_f1=0.20, tau_a_f1=0.20
        )
        by = {r["code"]: r for r in rows}
        # 0.5*0.60+0.5*0.60=0.60 > 0.5*0.90+0.5*0.10=0.50 > 0.5*0.80+0=0.40
        self.assertAlmostEqual(by["bal"]["score"], 0.6)
        self.assertAlmostEqual(by["hi_oo"]["score"], 0.5)
        self.assertAlmostEqual(by["oo_gap"]["score"], 0.4)
        self.assertIsNone(by["none"]["score"])
        self.assertEqual(by["bal"]["tier"], "A")
        self.assertEqual(by["hi_oo"]["tier"], "B")
        self.assertEqual(by["oo_gap"]["tier"], "B")
        self.assertEqual(by["none"]["tier"], "C")

        # 无 ŷ_τc：Score = OO_F1，TC_F1 不计入
        oo_only = [
            {
                "code": "oo",
                "hit_rate": 0.80,
                "n_valid": 8,
                "f1_pos": 0.20,
                "f1_pos_tau": 0.99,
                "mean_y_pos": 0.1,
            },
            {
                "code": "better",
                "hit_rate": 0.70,
                "n_valid": 8,
                "f1_pos": 0.80,
                "f1_pos_tau": 0.01,
                "mean_y_pos": 0.1,
            },
        ]
        assign_tiers_by_rank_score(
            oo_only, require_tau=False, a_count=1, b_count=1, a_f1=0.20
        )
        by2 = {r["code"]: r for r in oo_only}
        self.assertAlmostEqual(by2["better"]["score"], 0.8)
        self.assertAlmostEqual(by2["oo"]["score"], 0.2)
        self.assertEqual(by2["better"]["tier"], "A")
        self.assertEqual(by2["oo"]["tier"], "B")

    def test_assign_tier_compat(self):
        from core.research.predictability_tiers import assign_tier

        self.assertEqual(assign_tier(0.60, 10), "B")
        self.assertEqual(assign_tier(None, 0), "C")
        self.assertEqual(assign_tier(0.8, 0), "C")
        self.assertEqual(
            assign_tier(0.6, 10, hit_rate_tau=0.5, n_valid_tau=5, require_tau=True),
            "B",
        )
        self.assertEqual(
            assign_tier(0.6, 10, hit_rate_tau=None, n_valid_tau=0, require_tau=True),
            "C",
        )

    def test_split_holdout_half_dates(self):
        from core.research.predictability_tiers import (
            split_holdout_half_dates,
            tier_code_set,
        )

        dates = [f"2025-08-{d:02d}" for d in range(1, 11)]
        split = split_holdout_half_dates(dates, holdout_n=10)
        self.assertTrue(split.get("ok"), split)
        self.assertEqual(split.get("n_test"), 10)
        self.assertEqual(split.get("tier_n"), 5)
        self.assertEqual(split["tier_dates"], dates[:5])
        self.assertNotIn("rank_dates", split)
        self.assertNotIn("rank_n", split)
        self.assertNotIn("n_ledger", split)
        self.assertNotIn("bt_dates", split)

        long = [f"2025-07-{d:02d}" for d in range(1, 21)]
        clipped = split_holdout_half_dates(long, holdout_n=10)
        self.assertEqual(clipped["test_days"], long[-10:])
        self.assertEqual(clipped["tier_dates"], long[-10:-5])

        odd = [f"2025-09-{d:02d}" for d in range(1, 10)]
        half_odd = split_holdout_half_dates(odd, holdout_n=20)
        self.assertEqual(half_odd.get("tier_n"), 4)
        self.assertEqual(half_odd["tier_dates"], odd[:4])

        rep = {
            "rows": [
                {"code": "000001", "tier": "A"},
                {"code": "000002", "tier": "C"},
            ]
        }
        keep = tier_code_set(rep, ["A", "B"])
        self.assertIn("000001", keep)
        self.assertNotIn("000002", keep)

    def test_metrics_use_full_holdout_hits_use_first_half(self):
        from core.research.predictability_tiers import (
            _absorb_holdout_row,
            _absorb_tau_holdout_row,
            summarize_tier_ew_hold,
        )

        acc = {}
        tier = {"d1", "d2"}
        whole = {"d1", "d2", "d3", "d4"}
        # y 与 forward_oo_pct 同为百分点（2.0 = +2%）
        for d, y in (("d1", 2.0), ("d2", 2.0), ("d3", -4.0), ("d4", -4.0)):
            _absorb_holdout_row(
                acc,
                code="000001",
                date=d,
                pred=1.0,
                y=y,
                tier_set=tier,
                metric_set=whole,
                yhat_eps=0.05,
            )
            _absorb_tau_holdout_row(
                acc,
                code="000001",
                date=d,
                pred=1.0,
                y=y,
                yhat_eps=0.05,
                tier_set=tier,
                metric_set=whole,
            )
        slot = acc["000001"]
        self.assertEqual(slot["n_valid"], 2)
        self.assertEqual(slot["hits"], 2)
        self.assertEqual(slot["dates"], ["d1", "d2"])
        self.assertEqual(len(slot["realized"]), 4)
        self.assertEqual(len(slot["pair_dates"]), 4)
        self.assertEqual(slot["n_valid_tau"], 2)
        self.assertEqual(len(slot["tau_realized"]), 4)
        # OO_R+ 只计前半 ŷ>ε：d1/d2 的 y=2.0；后半 d3/d4 不进
        self.assertEqual(slot["n_y_pos"], 2)
        self.assertAlmostEqual(float(slot["sum_y_pos"]), 4.0)
        # TC_R+ / TC_F1 同窗：ŷ_τc 与 y 同口径
        self.assertEqual(int(slot.get("n_y_pos_tau") or 0), 2)
        self.assertAlmostEqual(float(slot.get("sum_y_pos_tau") or 0.0), 4.0)
        # 等权买持：与回测基线同构（close 首尾）
        dates = ["d1", "d2", "d3", "d4"]
        bars = {
            "000001": [
                {"date": "d1", "close": 100.0},
                {"date": "d2", "close": 101.0},
                {"date": "d3", "close": 99.0},
                {"date": "d4", "close": 99.0},
            ],
            "000002": [
                {"date": "d1", "close": 50.0},
                {"date": "d2", "close": 50.0},
                {"date": "d3", "close": 50.0},
                {"date": "d4", "close": 55.0},
            ],
        }
        ew = summarize_tier_ew_hold(bars, codes=["000001", "000002"], dates=dates)
        self.assertEqual(ew["n_realized"], 2)
        self.assertEqual(ew.get("ret_method"), "ew_hold")
        # 000001: 99/100-1=-1%；000002: 55/50-1=+10%；等权 +4.5%
        self.assertAlmostEqual(ew["mean_realized_pct"], 4.5)

    def test_mean_y_pos_only_first_half_when_yhat_positive(self):
        """OO_R+：仅 Holdout 前半；ŷ>ε 进 OO_R+（不再累计空头条件收益）。"""
        from core.research.predictability_tiers import (
            _absorb_holdout_row,
            _absorb_tau_holdout_row,
            _rows_from_acc,
        )

        acc = {}
        tier = {"d1", "d2", "d3"}
        whole = {"d1", "d2", "d3", "d4"}
        # d1: ŷ>0 y=+1；d2: ŷ<0 y=+3（不计 R+）；d3: ŷ>0 y=-0.5；d4 后半不计
        samples = (
            ("d1", 1.0, 1.0),
            ("d2", -1.0, 3.0),
            ("d3", 0.5, -0.5),
            ("d4", 2.0, 9.0),
        )
        for d, pred, y in samples:
            _absorb_holdout_row(
                acc,
                code="000001",
                date=d,
                pred=pred,
                y=y,
                tier_set=tier,
                metric_set=whole,
                yhat_eps=0.05,
            )
            _absorb_tau_holdout_row(
                acc,
                code="000001",
                date=d,
                pred=pred,
                y=y,
                yhat_eps=0.05,
                tier_set=tier,
                metric_set=whole,
            )
        rows, _counts, _min_n = _rows_from_acc(
            acc,
            pool_codes=["000001"],
            watch_set={"000001"},
            pad_missing=False,
            a_count=1,
            b_count=0,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["n_y_pos"], 2)
        self.assertAlmostEqual(float(rows[0]["mean_y_pos"]), 0.25)
        # d1 TP (ŷ>0 y>0)；d2 FN (ŷ<0 y>0)；d3 FP (ŷ>0 y<0) → P=1/2 R=1/2 F1=0.5
        self.assertAlmostEqual(float(rows[0]["precision_pos"]), 0.5)
        self.assertAlmostEqual(float(rows[0]["recall_pos"]), 0.5)
        self.assertAlmostEqual(float(rows[0]["f1_pos"]), 0.5)
        # TC 同样本同口径
        self.assertEqual(rows[0]["n_y_pos_tau"], 2)
        self.assertAlmostEqual(float(rows[0]["mean_y_pos_tau"]), 0.25)
        self.assertAlmostEqual(float(rows[0]["f1_pos_tau"]), 0.5)

    def test_f1_pos_none_when_no_predicted_up(self):
        """全看空：P⁺/OO_F1 为 None（展示「无看多」）。"""
        from core.research.predictability_tiers import (
            _absorb_holdout_row,
            _f1_pos_from_counts,
            _rows_from_acc,
        )

        f1, p, r = _f1_pos_from_counts(0, 0, 3)
        self.assertIsNone(f1)
        self.assertIsNone(p)
        self.assertAlmostEqual(float(r), 0.0)

        acc = {}
        tier = {"d1", "d2"}
        for d, y in (("d1", 1.0), ("d2", -1.0)):
            _absorb_holdout_row(
                acc,
                code="000001",
                date=d,
                pred=-1.0,
                y=y,
                tier_set=tier,
                yhat_eps=0.05,
            )
        rows, _counts, _min_n = _rows_from_acc(
            acc,
            pool_codes=["000001"],
            watch_set={"000001"},
            pad_missing=False,
            a_count=1,
            b_count=0,
        )
        self.assertIsNone(rows[0]["f1_pos"])
        self.assertIsNone(rows[0]["precision_pos"])
        self.assertAlmostEqual(float(rows[0]["recall_pos"]), 0.0)
        self.assertEqual(rows[0]["n_y_pos"], 0)

    def test_ew_hold_skips_sparse_last_day(self):
        """末日仅少数票有 K 线时，不得用该稀疏截面作等权买持终点。"""
        from core.backtest.topk_benchmark import _pool_ew_equity_curve
        from core.research.predictability_tiers import summarize_tier_ew_hold

        dates = ["d1", "d2", "d3", "d4"]
        bars = {
            "000001": [
                {"date": "d1", "close": 100.0},
                {"date": "d2", "close": 100.0},
                {"date": "d3", "close": 96.0},
                {"date": "d4", "close": 50.0},  # 仅此票有 d4
            ],
            "000002": [
                {"date": "d1", "close": 100.0},
                {"date": "d2", "close": 100.0},
                {"date": "d3", "close": 96.0},
            ],
            "000003": [
                {"date": "d1", "close": 100.0},
                {"date": "d2", "close": 100.0},
                {"date": "d3", "close": 96.0},
            ],
            "000004": [
                {"date": "d1", "close": 100.0},
                {"date": "d2", "close": 100.0},
                {"date": "d3", "close": 96.0},
            ],
            "000005": [
                {"date": "d1", "close": 100.0},
                {"date": "d2", "close": 100.0},
                {"date": "d3", "close": 96.0},
            ],
        }
        curve = _pool_ew_equity_curve(bars, dates)
        self.assertEqual(curve[-1]["date"], "d3")
        self.assertAlmostEqual(curve[-1]["equity"], 96.0)
        ew = summarize_tier_ew_hold(bars, codes=list(bars), dates=dates)
        # 齐窗终点 d3：全池 -4%，而非被 d4 单票 -50% 拖垮
        self.assertAlmostEqual(ew["mean_realized_pct"], -4.0)

    def test_build_holdout_half_tiers_oos(self):
        from core.research.predictability_tiers import (
            PROTOCOL_HOLDOUT_OOS,
            build_holdout_half_tiers,
            load_predictability_tiers_last,
            save_predictability_tiers_last,
            tier_code_set,
        )

        tier_dates = [f"2025-08-{d:02d}" for d in range(1, 11)]

        def _fake_bars(c0: float, c1: float):
            rows = [{"date": tier_dates[0], "close": c0}]
            for d in tier_dates[1:-1]:
                rows.append({"date": d, "close": c0})
            rows.append({"date": tier_dates[-1], "close": c1})
            return rows

        def _fake_accumulate(codes, **kwargs):
            acc = {
                "000001": {
                    "hits": 7,
                    "n_valid": 7,
                    "n_days": 10,
                    "yhats": [1.0] * 10,
                    "realized": [2.0] * 10,  # 百分点，与 forward_oo_pct 一致
                    "sum_y_pos": 20.0,  # 10 日看多 × 2.0 → OO_R+=2.0
                    "n_y_pos": 10,
                    "tp_pos": 6,
                    "fp_pos": 2,
                    "fn_pos": 2,
                    "dates": list(tier_dates),
                    "hits_tau": 6,
                    "n_valid_tau": 7,
                    "tau_yhats": [1.0] * 10,
                    "tau_realized": [1.0] * 6 + [-1.0] * 4,
                    "tau_pair_dates": list(tier_dates),
                    "sum_y_pos_tau": 12.0,  # TC_R+>0
                    "n_y_pos_tau": 6,
                    "tp_pos_tau": 4,
                    "fp_pos_tau": 2,
                    "fn_pos_tau": 2,
                },
                "000002": {
                    "hits": 0,
                    "n_valid": 5,
                    "n_days": 10,
                    "yhats": [1.0] * 10,
                    "realized": [-1.0] * 10,
                    "sum_y_pos": -10.0,  # OO_R+=-1 → 不过 A
                    "n_y_pos": 10,
                    "dates": list(tier_dates),
                    "hits_tau": 0,
                    "n_valid_tau": 5,
                    "tau_yhats": [1.0] * 10,
                    "tau_realized": [-1.0] * 10,
                    "tau_pair_dates": list(tier_dates),
                    "sum_y_pos_tau": -5.0,
                    "n_y_pos_tau": 5,
                },
            }
            return {
                "ok": True,
                "acc": acc,
                "tier_dates": list(tier_dates),
                "tier_n": 10,
                "holdout_split": {
                    "ok": True,
                    "holdout_n": 10,
                    "n_test": 10,
                    "tier_dates": list(tier_dates),
                    "tier_n": 10,
                    "test_days": list(tier_dates),
                    "as_of_tier_last": tier_dates[-1],
                },
                "split_meta": {"eval_start": "2025-08-01", "test_days": list(tier_dates)},
                "n_train": 40,
                "n_test": 20,
                "horizon_days": 1,
                "source": "oo_holdout_oos",
                "tau_meta": {"ok": True, "n_rows": 12, "path": "/tmp/tau_research.json"},
                # 000001 +2%、000002 -1%（对齐回测等权买持 close 首尾）
                "stock_bars": {
                    "000001": _fake_bars(100.0, 102.0),
                    "000002": _fake_bars(100.0, 99.0),
                },
            }

        with tempfile.TemporaryDirectory() as td:
            last_p = os.path.join(td, "predictability_tiers_last.json")
            with patch(
                "core.research.predictability_tiers.accumulate_holdout_oos_by_code",
                side_effect=_fake_accumulate,
            ), patch(
                "core.research.predictability_tiers.predictability_tiers_last_path",
                return_value=last_p,
            ), patch(
                "core.research.predictability_tiers._watching_name_map",
                return_value={"000001": "平安银行", "000002": "万科A", "600000": "浦发银行"},
            ):
                rep = build_holdout_half_tiers(
                    10,
                    pool="watching",
                    min_n=3,
                    head="oo",
                    persist=True,
                    a_count=1,
                    b_count=0,
                    watching_codes=["000001", "000002", "600000"],
                )
                self.assertTrue(rep.get("success"), rep)
                self.assertEqual(rep.get("protocol"), PROTOCOL_HOLDOUT_OOS)
                self.assertEqual(rep.get("source"), "oo_holdout_oos")
                self.assertEqual(rep.get("holdout_n"), 10)
                # fake accumulate 未改 source 时仍透传；真实路径为 oo_research_model
                self.assertEqual(rep.get("tier_n"), 10)
                self.assertNotIn("bt_lookback", rep)
                self.assertNotIn("rank_threshold", rep)
                self.assertNotIn("rank_dates", rep)
                self.assertNotIn("ledger_dates", rep)
                self.assertNotIn("n_ledger_dates", rep)
                self.assertNotIn("lookback_dates", rep)
                self.assertNotIn("按档扫 ŷ_oo 入场", rep.get("note") or "")
                self.assertAlmostEqual(
                    ((rep.get("cs_by_tier") or {}).get("A") or {}).get("mean_realized_pct"),
                    2.0,
                )
                self.assertAlmostEqual(
                    ((rep.get("cs_by_tier") or {}).get("C") or {}).get("mean_realized_pct"),
                    -1.0,
                )
                by = {r["code"]: r for r in rep.get("rows") or []}
                self.assertAlmostEqual(float(by["000001"]["realized_pct"]), 2.0)
                self.assertAlmostEqual(float(by["000002"]["realized_pct"]), -1.0)
                self.assertIsNone(by["600000"].get("realized_pct"))
                self.assertEqual(by["000001"]["tier"], "A")
                self.assertEqual(by["000002"]["tier"], "C")
                self.assertTrue((rep.get("thresholds") or {}).get("require_tau"))
                thr = rep.get("thresholds") or {}
                self.assertEqual(int(thr.get("a_count")), 1)
                self.assertEqual(int(thr.get("b_count")), 0)
                self.assertAlmostEqual(float(thr.get("a_f1")), 0.2)
                self.assertAlmostEqual(float(thr.get("tau_a_f1")), 0.2)
                self.assertNotIn("a_hit", thr)
                self.assertNotIn("tau_a_hit", thr)
                self.assertTrue(thr.get("require_oo_r_pos"))
                self.assertTrue(thr.get("require_tc_r_pos"))
                self.assertNotIn("require_r_pos", thr)
                self.assertNotIn("r_pos_min", thr)
                self.assertAlmostEqual(float(by["000001"]["mean_y_pos"]), 2.0)
                self.assertIn("0.5*OO_F1", str(thr.get("score") or ""))
                self.assertIn("OO_R+>0", str(rep.get("note") or ""))
                self.assertIn("cs_by_tier_tau", rep)
                self.assertIn("hit_rate_tau", by["000001"])
                self.assertGreaterEqual(float(by["000001"]["hit_rate_tau"] or 0), 0.55)
                self.assertAlmostEqual(
                    float(by["000001"]["score"]),
                    0.5 * float(by["000001"]["f1_pos"])
                    + 0.5 * float(by["000001"]["f1_pos_tau"]),
                )
                self.assertIsNone(by["000001"].get("rank_oo"))
                self.assertEqual(by["600000"]["tier"], "C")  # pad missing
                self.assertEqual(by["000001"].get("name"), "平安银行")
                self.assertEqual(by["000002"].get("name"), "万科A")
                keep = tier_code_set(rep, ["A", "B"])
                self.assertIn("000001", keep)
                self.assertNotIn("000002", keep)

                save_predictability_tiers_last(rep)
                loaded = load_predictability_tiers_last()
                self.assertTrue(
                    loaded and loaded.get("protocol") == PROTOCOL_HOLDOUT_OOS
                )

                tau = build_holdout_half_tiers(10, head="tau", persist=False)
                self.assertFalse(tau.get("success"))

    def test_load_research_oo_model_skips_active(self):
        from core.research.predictability_tiers import _load_research_oo_model
        from core.signal.return_score import ReturnScoreModel

        research = {
            "role": "research",
            "model": ReturnScoreModel(
                intercept=0.1, coefficients={"momentum": 0.2}, feature_zscore=False
            ).to_dict(),
            "meta": {"lookback": 120},
        }
        with patch(
            "core.research.oo_ridge.load_return_model_payload",
            return_value=research,
        ):
            model, meta = _load_research_oo_model()
        self.assertIsNotNone(model)
        self.assertTrue(meta.get("ok"))
        self.assertEqual(meta.get("lookback"), 120)
        self.assertAlmostEqual(float(model.predict({"momentum": 1.0})), 0.3)

        with patch(
            "core.research.oo_ridge.load_return_model_payload",
            return_value=None,
        ):
            model2, meta2 = _load_research_oo_model()
        self.assertIsNone(model2)
        self.assertFalse(meta2.get("ok"))

    def test_build_tiers_defaults_to_watching(self):
        from core.research.predictability_tiers import build_holdout_half_tiers

        tier_dates = [f"2025-06-{d:02d}" for d in range(1, 11)]

        def _fake_accumulate(codes, **kwargs):
            self.assertEqual(kwargs.get("lookback"), 120)
            self.assertEqual(kwargs.get("holdout_n"), 20)
            self.assertTrue(kwargs.get("use_research_model", True))
            code_set = set(codes or [])
            acc = {}
            for code, hits, n in (
                ("000001", 10, 10),
                ("000002", 5, 10),
                ("000003", 0, 10),
                ("000099", 10, 10),
            ):
                if code not in code_set:
                    continue
                ry = 1.0 if hits else -1.0
                # 命中越多 OO_F1 越高，保证 000001 的 Score 排在最前
                tp = int(hits)
                acc[code] = {
                    "hits": hits,
                    "n_valid": n,
                    "n_days": n,
                    "yhats": [1.0] * n,
                    "realized": [ry] * n,
                    "sum_y_pos": ry * n,
                    "n_y_pos": n,
                    "tp_pos": tp,
                    "fp_pos": n - tp,
                    "fn_pos": 0 if tp else n,
                    "dates": list(tier_dates),
                }
            return {
                "ok": True,
                "acc": acc,
                "tier_dates": list(tier_dates),
                "tier_n": len(tier_dates),
                "lookback": 120,
                "holdout_split": {
                    "ok": True,
                    "holdout_n": 20,
                    "n_test": 20,
                    "tier_dates": list(tier_dates),
                    "tier_n": len(tier_dates),
                    "as_of_tier_last": tier_dates[-1],
                },
                "split_meta": {},
                "n_train": 50,
                "n_test": 20,
                "horizon_days": 1,
                "source": "oo_research_model",
                "model_meta": {"ok": True, "path": "/tmp/research.json"},
            }

        with patch(
            "core.research.predictability_tiers.accumulate_holdout_oos_by_code",
            side_effect=_fake_accumulate,
        ), patch(
            "core.research_universe.resolve_research_codes",
            return_value={
                "codes": ["000001", "000002", "000003", "000099"],
                "count": 4,
                "source": "research_universe",
            },
        ), patch(
            "core.watching.store.read_watching",
            return_value={"watchlist": ["000001", "000002", "000003", "600000"]},
        ):
            rep = build_holdout_half_tiers(
                holdout_n=20,
                min_n=5,
                head="oo",
                persist=False,
                a_count=1,
                b_count=1,
            )
            self.assertTrue(rep.get("success"), rep)
            self.assertEqual(rep.get("pool"), "watching")
            by = {r["code"]: r for r in rep.get("rows") or []}
            self.assertIn("000001", by)
            self.assertIn("600000", by)
            self.assertNotIn("000099", by)
            self.assertEqual(by["000001"]["tier"], "A")
            self.assertEqual(by["000003"]["tier"], "C")

            ru = build_holdout_half_tiers(
                holdout_n=20,
                pool="research_universe",
                min_n=5,
                head="oo",
                persist=False,
                a_count=1,
                b_count=1,
            )
            ru_codes = {r["code"] for r in ru.get("rows") or []}
            self.assertIn("000099", ru_codes)

            # ledger 池已废弃 → watching
            led = build_holdout_half_tiers(
                holdout_n=20,
                pool="ledger",
                min_n=5,
                head="oo",
                persist=False,
                a_count=1,
                b_count=1,
            )
            self.assertEqual(led.get("pool"), "watching")

    def test_promote_and_live_filter(self):
        from core.research.predictability_tiers import (
            clear_predictability_tiers_live,
            filter_codes_by_predictability_live,
            live_tier_status,
            promote_predictability_tiers_to_live,
            save_predictability_tiers_last,
        )

        report = {
            "success": True,
            "counts": {"A": 1, "B": 1, "C": 1},
            "head": "oo",
            "head_label": "ŷ_oo",
            "tier_n": 20,
            "tier_dates": ["2025-01-01", "2025-01-20"],
            "rows": [
                {"code": "000001", "tier": "A"},
                {"code": "000002", "tier": "B"},
                {"code": "000003", "tier": "C"},
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            last_p = os.path.join(td, "predictability_tiers_last.json")
            active_p = os.path.join(td, "predictability_tiers_active.json")
            with patch(
                "core.research.predictability_tiers.predictability_tiers_last_path",
                return_value=last_p,
            ), patch(
                "core.research.predictability_tiers.predictability_tiers_active_path",
                return_value=active_p,
            ), patch(
                "core.live_config_manifest.write_live_config_manifest",
                return_value={},
            ):
                save_predictability_tiers_last(report)
                ok = promote_predictability_tiers_to_live()
                self.assertTrue(ok.get("ok"))
                st = live_tier_status()
                self.assertTrue(st.get("enabled"))
                self.assertEqual(st.get("allowed_tiers"), ["A"])

                kept, meta = filter_codes_by_predictability_live(
                    ["000001", "000002", "000003", "000099"],
                    keep=["000003"],
                )
                self.assertTrue(meta.get("predictability_live"))
                self.assertIn("000001", kept)
                self.assertNotIn("000002", kept)
                self.assertIn("000003", kept)  # 持仓仍进打分，待硬清仓
                self.assertNotIn("000099", kept)
                self.assertEqual(meta.get("force_exit_held"), ["000003"])
                self.assertEqual(meta.get("n_force_exit_held"), 1)
                self.assertEqual(meta.get("tradable_codes"), ["000001"])

                clear_predictability_tiers_live()
                st2 = live_tier_status()
                self.assertFalse(st2.get("enabled"))
                kept2, meta2 = filter_codes_by_predictability_live(
                    ["000001", "000003"], keep=[]
                )
                self.assertTrue(meta2.get("unrestricted"))
                self.assertEqual(kept2, ["000001", "000003"])

                from core.research.predictability_tiers import sync_predictability_tiers_live

                syn = sync_predictability_tiers_live(["A"])
                self.assertTrue(syn.get("ok"))
                self.assertEqual(syn.get("allowed_tiers"), ["A"])
                syn_off = sync_predictability_tiers_live([])
                self.assertTrue(syn_off.get("synced"))
                self.assertFalse(syn_off.get("enabled"))

        with tempfile.TemporaryDirectory() as td2:
            with patch(
                "core.research.predictability_tiers.predictability_tiers_last_path",
                return_value=os.path.join(td2, "missing_last.json"),
            ), patch(
                "core.research.predictability_tiers.predictability_tiers_active_path",
                return_value=os.path.join(td2, "active.json"),
            ):
                fail = promote_predictability_tiers_to_live()
                self.assertFalse(fail.get("ok"))


if __name__ == "__main__":
    unittest.main()
