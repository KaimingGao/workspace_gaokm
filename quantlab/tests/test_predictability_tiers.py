"""滚动可预测性票档：Holdout 前 60 日猜涨 lift 定档 + Holdout 评测。"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class TestPredictabilityTiers(unittest.TestCase):
    def test_accumulate_uses_full_lookback_with_research_model(self):
        """有研究套时也按 lookback 全窗拉日线（训练窗定档需要）。"""
        from core.research.predictability_tiers import accumulate_holdout_oos_by_code
        from core.signal.return_score import ReturnScoreModel

        captured = {}

        def _fake_load(codes, **kwargs):
            captured["lookback"] = kwargs.get("lookback")
            captured["n_codes"] = len(list(codes or []))
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
        self.assertEqual(int(captured["lookback"]), 600)
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("lookback_requested"), 600)

    def test_research_model_keeps_train_and_holdout_dates(self):
        """有研究套时特征行覆盖训练窗∪Holdout（定档+评测）。"""
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
            "core.research.predictability_tiers._lake_coverage_ok",
            return_value=False,
        ), patch(
            "core.research.portfolio_bars.load_portfolio_stock_bars",
            side_effect=_fake_load,
        ), patch(
            "core.research.oo_panel.collect_oo_panel",
            side_effect=_fake_collect,
        ), patch(
            "core.research.predictability_tiers._load_research_tau_model",
            return_value=(None, {"ok": False, "reason": "skip"}),
        ):
            out = accumulate_holdout_oos_by_code(
                ["000001", "000002"],
                holdout_n=10,
                lookback=600,
                use_research_model=True,
                tier_window_days=8,
                embargo_days=1,
            )
        self.assertTrue(out.get("ok"), out)
        all_days = {str(b["date"])[:10] for b in bars}
        last10 = {str(b["date"])[:10] for b in bars[-10:]}
        keep = seen.get("keep") or set()
        self.assertTrue(last10.issubset(keep), keep)
        self.assertGreater(len(keep - last10), 0)
        self.assertTrue(keep.issubset(all_days))
        self.assertEqual(
            out.get("holdout_split", {}).get("selection_window"), "tier60_quad_uplift"
        )
        self.assertGreater(int(out.get("n_train") or 0), 0)

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
            "core.research.predictability_tiers._lake_coverage_ok",
            return_value=False,
        ), patch(
            "core.research.portfolio_bars.load_portfolio_stock_bars",
            side_effect=_fake_load,
        ), patch(
            "core.research.oo_panel.collect_oo_panel",
            side_effect=_fake_collect,
        ), patch(
            "core.research.predictability_tiers._load_research_tau_model",
            return_value=(None, {"ok": False, "reason": "skip"}),
        ), patch(
            "core.research.predictability_tiers._score_tau_holdout_into_acc",
            return_value={"ok": False, "reason": "skip"},
        ):
            out = accumulate_holdout_oos_by_code(
                ["000001", "000002"],
                holdout_n=10,
                lookback=600,
                use_research_model=True,
                tier_window_days=8,
                embargo_days=1,
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
        self.assertNotIn("ic", rows[0])
        self.assertNotIn("hits", rows[0])

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
        self.assertIsNotNone(out.get("acc"))
        self.assertIsNotNone(out.get("f1"))
        empty_oo = summarize_tier_window_cs(acc, min_names=5, head="oo")
        self.assertIsNone(empty_oo["cs_rank_ic"])
        self.assertIsNone(empty_oo.get("acc"))
        self.assertIsNone(empty_oo.get("f1"))

    def test_tier_window_acc_f1_use_full_pairs(self):
        """标题 ACC / P / R / F1 池化传入窗口的成对样本；死区日不计入。与截面最少票数无关。"""
        from core.research.predictability_tiers import summarize_tier_window_cs

        acc = {
            "000001": {
                "yhats": [0.2, 0.2, -0.2, 0.01],
                "realized": [0.1, -0.1, -0.1, 0.2],
                "pair_dates": ["d1", "d2", "d3", "d4"],
            }
        }
        out = summarize_tier_window_cs(acc, min_names=5)
        self.assertIsNone(out["cs_rank_ic"])
        # 有效 3 日：两天符号对、一天看多看空。|ŷ|=0.01 死区剔除。
        # tp=1 fp=1 fn=0 → P=0.5 R=1 F1=2/3
        self.assertAlmostEqual(float(out["acc"]), round(2 / 3, 4))
        self.assertAlmostEqual(float(out["precision"]), 0.5)
        self.assertAlmostEqual(float(out["recall"]), 1.0)
        self.assertAlmostEqual(float(out["f1"]), round(2 / 3, 4))
        self.assertEqual(int(out["n_cls"]), 3)
        only_b = summarize_tier_window_cs(acc, codes=["000002"], min_names=1)
        self.assertIsNone(only_b.get("acc"))
        self.assertEqual(int(only_b.get("n_cls") or 0), 0)

    def test_tier_window_dates_filter_pairs(self):
        """评测日窗外的成对样本不进 Rank IC / ACC。"""
        from core.research.predictability_tiers import summarize_tier_window_cs

        acc = {}
        for i in range(6):
            code = f"{i:06d}"
            y = float(i) - 2.5
            acc[code] = {
                "yhats": [-y, -y, -y, y, y, y],
                "realized": [y, y, y, y, y, y],
                "pair_dates": ["d1", "d2", "d3", "d4", "d5", "d6"],
            }
        second = summarize_tier_window_cs(
            acc, dates=["d4", "d5", "d6"], min_names=5
        )
        first = summarize_tier_window_cs(
            acc, dates=["d1", "d2", "d3"], min_names=5
        )
        self.assertGreater(float(second["cs_rank_ic"]), 0.9)
        self.assertLess(float(first["cs_rank_ic"]), -0.9)
        self.assertAlmostEqual(float(second["acc"]), 1.0)
        self.assertAlmostEqual(float(first["acc"]), 0.0)
        empty = summarize_tier_window_cs(acc, dates=[], min_names=1)
        self.assertIsNone(empty.get("acc"))
        self.assertIsNone(empty.get("cs_rank_ic"))

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
    def test_build_lookback_holdout_split(self):
        from core.research.predictability_tiers import (
            PROTOCOL_HOLDOUT_OOS,
            TITLE_METRIC_WINDOW,
            build_lookback_holdout_split,
            holdout_eval_dates,
            tier_code_set,
        )

        self.assertEqual(PROTOCOL_HOLDOUT_OOS, "tier60_quad_uplift_holdout_eval")
        from datetime import date, timedelta

        start = date(2025, 1, 2)
        train = [(start + timedelta(days=i)).isoformat() for i in range(80)]
        test = [(start + timedelta(days=100 + i)).isoformat() for i in range(10)]
        split = build_lookback_holdout_split(train, test, holdout_n=10)
        self.assertTrue(split.get("ok"), split)
        self.assertEqual(split.get("embargo_before_test"), [train[-1]])
        self.assertEqual(split.get("tier_dates"), train[-61:-1])
        self.assertEqual(len(split.get("tier_dates") or []), 60)
        self.assertEqual(split.get("embargo_before_tier"), [train[-62]])
        self.assertEqual(split.get("fit_dates"), train[:-62])
        self.assertEqual(split.get("eval_dates"), test)
        self.assertEqual(split.get("test_days"), test)
        self.assertEqual(split.get("selection_window"), "tier60_quad_uplift")
        self.assertEqual(split.get("title_metric_window"), TITLE_METRIC_WINDOW)
        self.assertEqual(split.get("overlap_dropped"), [])
        self.assertEqual(holdout_eval_dates(split), test)
        self.assertEqual(holdout_eval_dates({"test_days": test}), test)
        self.assertEqual(holdout_eval_dates(None), [])

        # 训练∩Holdout 重叠日只留评测窗，不进定档
        train_overlap = train + test[:2]
        split_ov = build_lookback_holdout_split(train_overlap, test, holdout_n=10)
        self.assertTrue(split_ov.get("ok"), split_ov)
        self.assertEqual(split_ov.get("tier_dates"), train[-61:-1])
        self.assertEqual(split_ov.get("eval_dates"), test)
        self.assertEqual(split_ov.get("overlap_dropped"), test[:2])
        # 全重叠 → 训练窗空 → 失败
        bad = build_lookback_holdout_split(test, test, holdout_n=10)
        self.assertFalse(bad.get("ok"))
        self.assertEqual(len(bad.get("overlap_dropped") or []), len(test))
        short = build_lookback_holdout_split(train[:10], test, holdout_n=10)
        self.assertFalse(short.get("ok"), short)

        rep = {
            "rows": [
                {"code": "000001", "tier": "A"},
                {"code": "000002", "tier": "C"},
            ]
        }
        keep = tier_code_set(rep, ["A", "B"])
        self.assertIn("000001", keep)
        self.assertNotIn("000002", keep)

    def test_tier_progress_reports_phase_then_clears(self):
        from core.research.predictability_tiers import (
            finish_tier_progress,
            set_tier_progress,
            tier_progress_snapshot,
        )

        set_tier_progress("拟合 oo_rank", "只在内存，不写研究套")
        snap = tier_progress_snapshot()
        self.assertTrue(snap.get("running"))
        self.assertEqual(snap.get("phase"), "拟合 oo_rank")
        self.assertIn("不写研究套", snap.get("detail") or "")
        finish_tier_progress()
        done = tier_progress_snapshot()
        self.assertFalse(done.get("running"))
        self.assertEqual(done.get("phase"), "")

    def test_lift_from_panels_rank_backend_skips_abs(self):
        """排序头 LambdaRank 不产出 abs lift；收益头 Ridge 才有 by_code_abs。"""
        from unittest import mock

        from core.research import predictability_tiers as pt

        # 8 拟合日 + 10 定档日 × 4 票：A/B 每日截面中位以上 → ≥8 次说涨
        days = []
        for i in range(18):
            d = f"2024-01-{i + 1:02d}"
            days.append(
                {
                    "date": d,
                    "names": ["f1"],
                    "codes": ["A", "B", "C", "D"],
                    "ys": [1.0, 0.5, -0.5, -1.0],
                    "X": [[1.0], [0.5], [-0.5], [-1.0]],
                }
            )
        fit_dates = [d["date"] for d in days[:8]]
        tier_dates = [d["date"] for d in days[8:]]

        fake_lr = {
            "success": True,
            "solver": "lambdarank",
            "booster_b64": "x",
            "coefficients": {"f1": 1.0},
            "feature_zscore_mode": "cross_section",
        }
        fake_ridge = {
            "success": True,
            "solver": "ridge",
            "coefficients": {"f1": 0.5},
            "intercept": 0.0,
            "feature_zscore_mode": "cross_section",
            "zscore_means": {},
            "zscore_stds": {},
        }

        def fake_scores(day, model):
            ys = list(day.get("ys") or [])
            # 与 y 同序，便于 cs / abs 都说得通
            return list(ys), ys

        with mock.patch.object(pt, "_lambdarank_model_from_days", return_value={"success": True, "return_model": fake_lr}):
            with mock.patch.object(pt, "_ridge_model_from_days", return_value={"success": True, "return_model": fake_ridge}):
                with mock.patch(
                    "core.research.oo_rank_lambdarank._day_scores",
                    side_effect=fake_scores,
                ):
                    lr_pack = pt._lift_from_panels(
                        days,
                        fit_dates,
                        tier_dates,
                        backend="lambdarank",
                    )
                    ridge_pack = pt._lift_from_panels(
                        days,
                        fit_dates,
                        tier_dates,
                        backend="ridge",
                    )
        self.assertTrue(lr_pack.get("ok"))
        self.assertEqual(lr_pack.get("backend"), "lambdarank")
        self.assertEqual(lr_pack.get("by_code_abs") or {}, {})
        self.assertTrue(lr_pack.get("by_code_cs"))
        self.assertTrue(ridge_pack.get("ok"))
        self.assertEqual(ridge_pack.get("backend"), "ridge")
        self.assertTrue(ridge_pack.get("by_code_abs"))

    def test_display_metrics_from_day_rows_matches_absorb(self):
        from core.research.predictability_tiers import (
            attach_tier_head_display,
            display_metrics_from_day_rows,
            up_precision_lift_by_code,
        )

        # 8 次说涨全对 + 8 次看空且跌 → ACC=1、P=1、R=1、F1=1、R+>0
        skilled = [("SK", 1.0, 1.0)] * 8 + [("SK", -1.0, -1.0)] * 8
        # 天天说涨一半对：P=0.5
        always = [("ALW", 1.0, 1.0)] * 8 + [("ALW", 1.0, -1.0)] * 8
        day_rows = [("d", skilled + always)]
        disp = display_metrics_from_day_rows(day_rows)
        self.assertAlmostEqual(disp["SK"]["hit_rate"], 1.0)
        self.assertAlmostEqual(disp["SK"]["precision_pos"], 1.0)
        self.assertAlmostEqual(disp["SK"]["recall_pos"], 1.0)
        self.assertAlmostEqual(disp["SK"]["f1_pos"], 1.0)
        self.assertAlmostEqual(disp["SK"]["mean_y_pos"], 1.0)
        self.assertEqual(disp["SK"]["n_y_pos"], 8)
        self.assertAlmostEqual(disp["ALW"]["precision_pos"], 0.5)
        skill = up_precision_lift_by_code(day_rows)
        self.assertAlmostEqual(skill["SK"]["precision"], disp["SK"]["precision_pos"])
        self.assertAlmostEqual(skill["ALW"]["precision"], disp["ALW"]["precision_pos"])
        # 同分数不同说涨口径：abs vs 截面中位应能分开
        # A=0.20>ε；B=0.03∈(中位, ε]；中位≈0.01 → cs 说涨 A/B，abs 仅 A
        day_cs = [
            (
                f"d{i}",
                [
                    ("A", 0.20, 1.0),
                    ("B", 0.03, -1.0),
                    ("C", -0.01, 1.0),
                    ("D", -0.10, -1.0),
                ],
            )
            for i in range(8)
        ]
        abs_map = up_precision_lift_by_code(day_cs, min_up_calls=3, call_mode="abs")
        cs_map = up_precision_lift_by_code(day_cs, min_up_calls=3, call_mode="cs")
        self.assertIn("A", abs_map)
        self.assertIn("A", cs_map)
        self.assertIn("B", cs_map)
        self.assertNotIn("B", abs_map)
        rows = [
            {
                "code": "SK",
                "hit_rate": 0.1,
                "n_valid": 1,
                "n_days": 1,
                "mean_y_pos": 0.0,
                "mean_y_pos_raw": 0.0,
                "n_y_pos": 1,
                "f1_pos": 0.1,
                "precision_pos": 0.1,
                "recall_pos": 0.1,
                "hit_rate_tau": 0.9,
                "n_valid_tau": 9,
                "mean_y_pos_tau": 9.0,
                "mean_y_pos_tau_raw": 9.0,
                "n_y_pos_tau": 9,
                "f1_pos_tau": 0.9,
                "precision_pos_tau": 0.9,
                "recall_pos_tau": 0.9,
            }
        ]
        attach_tier_head_display(rows, {"SK": disp["SK"]}, {}, write_tc=False)
        self.assertAlmostEqual(rows[0]["hit_rate"], 1.0)
        self.assertAlmostEqual(rows[0]["precision_pos"], 1.0)
        self.assertIsNone(rows[0]["hit_rate_tau"])
        self.assertEqual(rows[0]["n_valid_tau"], 0)

    def test_up_precision_lift_ignores_return_size(self):
        from core.research.predictability_tiers import (
            assign_tiers_by_lift,
            combine_head_lifts,
            up_precision_lift_by_code,
        )

        def pack(pred_y):
            return [("d", pred_y)]

        # 8 次说涨全对，另外 8 日下跌且没说涨。精度 1，基准 0.5，lift 0.5。
        skilled = [("SK", 1.0, 1.0)] * 8 + [("SK", -1.0, -1.0)] * 8
        # 同样的涨跌方向，收益放大。lift 应与 SK 相同。
        loud = [("LOUD", 1.0, 20.0)] * 8 + [("LOUD", -1.0, -8.0)] * 8
        # 天天说涨，一半上涨。lift 约 0。
        always = [("ALW", 1.0, 1.0)] * 8 + [("ALW", 1.0, -1.0)] * 8
        # 说涨只有 3 次，不入队。
        short = [("SH", 1.0, 1.0)] * 3 + [("SH", -1.0, -1.0)] * 8
        oo = up_precision_lift_by_code(pack(skilled + loud + always + short))
        self.assertIn("SK", oo)
        self.assertIn("LOUD", oo)
        self.assertIn("ALW", oo)
        self.assertNotIn("SH", oo)
        self.assertAlmostEqual(oo["SK"]["lift"], 0.5)
        self.assertAlmostEqual(oo["LOUD"]["lift"], oo["SK"]["lift"])
        self.assertAlmostEqual(oo["ALW"]["lift"], 0.0)
        self.assertGreater(oo["SK"]["lift"], oo["ALW"]["lift"])
        combined = combine_head_lifts(
            {
                "oo_rank": oo,
                "tc_rank": {
                    "SK": {"lift": 0.0, "precision": 0.5, "base_rate": 0.5, "n_up": 8},
                },
            }
        )
        self.assertAlmostEqual(combined["SK"]["score"], oo["SK"]["lift"] / 2.0)
        self.assertAlmostEqual(combined["SK"]["lift_oo_rank"], oo["SK"]["lift"])
        self.assertAlmostEqual(combined["SK"]["lift_tc_rank"], 0.0)
        self.assertAlmostEqual(combined["LOUD"]["score"], oo["LOUD"]["lift"])

        quad = combine_head_lifts(
            {
                "oo_rank": {"SK": {"lift": 0.4, "precision": 0.9, "base_rate": 0.5, "n_up": 8}},
                "oo": {"SK": {"lift": 0.2, "precision": 0.7, "base_rate": 0.5, "n_up": 8}},
                "tc_rank": {"SK": {"lift": 0.0, "precision": 0.5, "base_rate": 0.5, "n_up": 8}},
                "tc": {},
            }
        )
        self.assertAlmostEqual(quad["SK"]["score"], (0.4 + 0.2 + 0.0) / 3.0)
        self.assertAlmostEqual(quad["SK"]["lift_oo"], 0.2)
        self.assertNotIn("lift_tc", quad["SK"])
        rows = [{"code": code} for code in ("SK", "LOUD", "ALW", "SH")]
        assign_tiers_by_lift(rows, combined, a_count=1, b_count=1)
        by = {r["code"]: r for r in rows}
        self.assertEqual(by["LOUD"]["tier"], "A")
        self.assertEqual(by["SK"]["tier"], "B")
        self.assertEqual(by["ALW"]["tier"], "C")
        self.assertEqual(by["SH"]["tier"], "C")
        self.assertIsNone(by["SH"]["score"])
        # combine 未入 tc 头时行上没有 lift_tc；有 tc_rank 时仍写 lift_tc_rank
        self.assertNotIn("lift_tc", by["SK"])
        self.assertAlmostEqual(by["SK"]["lift_tc_rank"], 0.0)

    def test_lake_rows_enough_and_strip_tau(self):
        from core.research.predictability_tiers import (
            _lake_rows_enough,
            _strip_tau_from_acc,
        )

        self.assertFalse(_lake_rows_enough(0, n_codes=10, n_dates=20))
        self.assertFalse(_lake_rows_enough(5, n_codes=10, n_dates=20))
        self.assertTrue(_lake_rows_enough(60, n_codes=10, n_dates=20))
        acc = {
            "000001": {
                "n_valid": 3,
                "hits_tau": 2,
                "n_valid_tau": 4,
                "tau_yhats": [1.0],
                "tau_realized": [0.5],
                "tau_pair_dates": ["2025-01-01"],
                "sum_y_pos_tau": 1.5,
                "n_y_pos_tau": 2,
                "tp_pos_tau": 1,
                "fp_pos_tau": 0,
                "fn_pos_tau": 1,
            }
        }
        _strip_tau_from_acc(acc)
        slot = acc["000001"]
        self.assertEqual(slot["n_valid"], 3)
        self.assertEqual(slot["hits_tau"], 0)
        self.assertEqual(slot["n_valid_tau"], 0)
        self.assertEqual(slot["tau_yhats"], [])
        self.assertEqual(slot["sum_y_pos_tau"], 0.0)

    def test_sparse_lake_falls_back_to_bars(self):
        """湖打分过稀时清空半残面板并改走 bars。"""
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
            return ([{"momentum": 1.0}] * len(use), [0.2] * len(use), use)

        research = ReturnScoreModel(
            intercept=0.0,
            coefficients={"momentum": 1.0},
            feature_zscore=False,
        )

        def _fake_load(codes, **kwargs):
            return ({str(c): list(bars) for c in codes}, [], {})

        def _fake_lake_score(acc, **kwargs):
            # 写入半残行后返回过稀行数
            from core.research.predictability_tiers import _absorb_holdout_row

            _absorb_holdout_row(
                acc,
                code="000001",
                date="2024-01-10",
                pred=1.0,
                y=0.2,
                tier_set=kwargs["tier_set"],
                metric_set=kwargs["metric_set"],
                yhat_eps=kwargs["yhat_eps"],
            )
            return 1

        with patch(
            "core.research.predictability_tiers._load_research_oo_model",
            return_value=(research, {"ok": True}),
        ), patch(
            "core.research.predictability_tiers._lake_coverage_ok",
            return_value=True,
        ), patch(
            "core.research.predictability_tiers._score_lake_days_into_acc",
            side_effect=_fake_lake_score,
        ), patch(
            "core.research.portfolio_bars.load_portfolio_stock_bars",
            side_effect=_fake_load,
        ), patch(
            "core.research.oo_panel.collect_oo_panel",
            side_effect=_fake_collect,
        ), patch(
            "core.research.predictability_tiers._load_research_tau_model",
            return_value=(None, {"ok": False, "reason": "skip"}),
        ):
            out = accumulate_holdout_oos_by_code(
                ["000001", "000002"],
                holdout_n=10,
                lookback=600,
                use_research_model=True,
                tier_window_days=8,
                embargo_days=1,
            )
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("panel_mode"), "bars_lookback")
        self.assertIn("000001", out.get("acc") or {})
        self.assertIn("000002", out.get("acc") or {})
        # bars 路径应覆盖两票；半残湖单票不得残留为唯一结果
        self.assertGreaterEqual(len(out["acc"]), 2)

    def test_metrics_use_holdout_hits_use_train_window(self):
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
        # OO_R+ 只计定档窗 ŷ>ε：d1/d2 的 y=2.0；评测日 d3/d4 不进
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

    def test_mean_y_pos_only_tier_window_when_yhat_positive(self):
        """OO_R+：仅定档窗（tier_set）；ŷ>ε 进 OO_R+（不再累计空头条件收益）。"""
        from core.research.predictability_tiers import (
            _absorb_holdout_row,
            _absorb_tau_holdout_row,
            _rows_from_acc,
        )

        acc = {}
        tier = {"d1", "d2", "d3"}
        whole = {"d1", "d2", "d3", "d4"}
        # d1: ŷ>0 y=+1；d2: ŷ<0 y=+3（不计 R+）；d3: ŷ>0 y=-0.5；d4 评测窗不计
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
        rows, _counts = _rows_from_acc(
            acc,
            pool_codes=["000001"],
            pad_missing=False,
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
        rows, _counts = _rows_from_acc(
            acc,
            pool_codes=["000001"],
            pad_missing=False,
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

    def test_build_predictability_tiers_oos(self):
        from core.research.predictability_tiers import (
            PROTOCOL_HOLDOUT_OOS,
            build_predictability_tiers,
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
                    "eval_dates": list(tier_dates),
                    "eval_n": 10,
                    "as_of_tier_last": tier_dates[-1],
                    "selection_window": "lookback_train",
                    "title_metric_window": "holdout",
                },
                "split_meta": {
                    "eval_start": "2025-08-01",
                    "train_days": [f"2025-07-{d:02d}" for d in range(1, 21)],
                    "test_days": list(tier_dates),
                },
                "n_train": 40,
                "n_test": 20,
                "panel_mode": "lookback_full",
                "horizon_days": 1,
                "source": "oo_research_model",
                "tau_meta": {"ok": True, "n_rows": 12, "path": "/tmp/tau_research.json"},
                "lift_by_code": {
                    "000001": {
                        "score": 1.0,
                        "lift_oo_rank": 1.0,
                        "n_up_oo_rank": 8,
                    },
                    "000002": {
                        "score": -1.0,
                        "lift_oo_rank": -1.0,
                        "n_up_oo_rank": 8,
                    },
                },
                # 000001 +2%、000002 -1%（对齐回测等权买持 close 首尾）
                "stock_bars": {
                    "000001": _fake_bars(100.0, 102.0),
                    "000002": _fake_bars(100.0, 99.0),
                },
            }

        with tempfile.TemporaryDirectory() as td:
            last_p = os.path.join(td, "predictability_tiers_last.json")
            active_p = os.path.join(td, "predictability_tiers_active.json")
            with patch(
                "core.research.predictability_tiers.accumulate_holdout_oos_by_code",
                side_effect=_fake_accumulate,
            ), patch(
                "core.research.predictability_tiers.predictability_tiers_last_path",
                return_value=last_p,
            ), patch(
                "core.research.predictability_tiers.predictability_tiers_active_path",
                return_value=active_p,
            ), patch(
                "core.live_config_manifest.write_live_config_manifest",
                return_value={},
            ), patch(
                "core.research.predictability_tiers._watching_name_map",
                return_value={"000001": "平安银行", "000002": "万科A", "600000": "浦发银行"},
            ):
                rep = build_predictability_tiers(
                    10,
                    pool="watching",
                    head="oo",
                    persist=True,
                    a_count=1,
                    b_count=0,
                    watching_codes=["000001", "000002", "600000"],
                )
                self.assertTrue(rep.get("success"), rep)
                self.assertEqual(rep.get("protocol"), PROTOCOL_HOLDOUT_OOS)
                self.assertEqual(rep.get("title_metric_window"), "holdout")
                self.assertEqual(rep.get("selection_window"), "tier60_quad_uplift")
                self.assertEqual(rep.get("source"), "oo_research_model")
                self.assertNotIn("live_hook", rep)
                self.assertNotIn("ic", (rep.get("rows") or [{}])[0])
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
                self.assertEqual(thr.get("score"), "up_precision_lift_quad")
                self.assertNotIn("top_frac", thr)
                self.assertEqual(int(thr.get("min_up_calls")), 8)
                self.assertNotIn("a_f1", thr)
                self.assertNotIn("a_hit", thr)
                self.assertNotIn("tau_a_hit", thr)
                self.assertNotIn("min_n", thr)
                self.assertNotIn("min_n", rep)
                self.assertNotIn("require_oo_r_pos", thr)
                self.assertNotIn("require_r_pos", thr)
                self.assertNotIn("r_pos_min", thr)
                self.assertAlmostEqual(float(by["000001"]["mean_y_pos"]), 2.0)
                self.assertAlmostEqual(float(by["000001"]["score"]), 1.0)
                self.assertIn("猜涨 lift", str(rep.get("note") or ""))
                self.assertIn("Lift_Score", str(rep.get("note") or ""))
                self.assertIn("上涨日比例", str(rep.get("note") or ""))
                self.assertIn("cs_by_tier_tau", rep)
                self.assertIn("hit_rate_tau", by["000001"])
                self.assertGreaterEqual(float(by["000001"]["hit_rate_tau"] or 0), 0.55)
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

                tau = build_predictability_tiers(10, head="tau", persist=False)
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
        from core.research.predictability_tiers import build_predictability_tiers

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
                    "test_days": list(tier_dates),
                    "eval_dates": list(tier_dates),
                    "eval_n": len(tier_dates),
                    "as_of_tier_last": tier_dates[-1],
                    "selection_window": "lookback_train",
                    "title_metric_window": "holdout",
                },
                "split_meta": {},
                "n_train": 50,
                "n_test": 20,
                "horizon_days": 1,
                "source": "oo_research_model",
                "panel_mode": "lookback_full",
                "model_meta": {"ok": True, "path": "/tmp/research.json"},
                "lift_by_code": {
                    "000001": {
                        "score": 2.0,
                        "lift_oo_rank": 2.0,
                        "n_up_oo_rank": 8,
                    },
                    "000002": {
                        "score": 1.0,
                        "lift_oo_rank": 1.0,
                        "n_up_oo_rank": 8,
                    },
                },
                "lift_holdout_by_code": {
                    "000001": {"score": -0.4},
                    "000002": {"score": 0.8},
                },
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
            rep = build_predictability_tiers(
                holdout_n=20,
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
            ordered = [r for r in rep.get("rows") or [] if r.get("score") is not None]
            self.assertEqual([r["code"] for r in ordered[:2]], ["000001", "000002"])
            self.assertGreater(float(ordered[0]["score"]), float(ordered[1]["score"]))
            self.assertAlmostEqual(float(by["000001"]["score_holdout"]), -0.4)
            self.assertAlmostEqual(float(by["000002"]["score_holdout"]), 0.8)
            self.assertIsNone(by["000003"]["score_holdout"])
            self.assertEqual(by["000001"]["tier"], "A")

            ru = build_predictability_tiers(
                holdout_n=20,
                pool="research_universe",
                head="oo",
                persist=False,
                a_count=1,
                b_count=1,
            )
            ru_codes = {r["code"] for r in ru.get("rows") or []}
            self.assertIn("000099", ru_codes)

            # ledger 池已废弃 → watching
            led = build_predictability_tiers(
                holdout_n=20,
                pool="ledger",
                head="oo",
                persist=False,
                a_count=1,
                b_count=1,
            )
            self.assertEqual(led.get("pool"), "watching")

    def test_promote_and_live_filter(self):
        from datetime import date

        from core.research.predictability_tiers import (
            PROTOCOL_HOLDOUT_OOS,
            TITLE_METRIC_WINDOW,
            clear_predictability_tiers_live,
            filter_codes_by_predictability_live,
            live_tier_status,
            pool_codes_fingerprint,
            promote_predictability_tiers_to_live,
            save_predictability_tiers_last,
        )

        pool = ["000001", "000002", "000003", "000099"]
        today = date.today().isoformat()
        report = {
            "success": True,
            "protocol": PROTOCOL_HOLDOUT_OOS,
            "title_metric_window": TITLE_METRIC_WINDOW,
            "holdout_n": 20,
            "counts": {"A": 1, "B": 1, "C": 1},
            "head": "oo",
            "head_label": "ŷ_oo",
            "tier_n": 20,
            "tier_dates": ["2025-01-01", today],
            "as_of_tier_last": today,
            "as_of_eval_last": today,
            "pool_codes_fp": pool_codes_fingerprint(pool),
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
                    pool,
                    keep=["000003"],
                )
                self.assertTrue(meta.get("predictability_live"))
                self.assertTrue(meta.get("gate_ok"))
                self.assertIn("000001", kept)
                self.assertNotIn("000002", kept)
                self.assertIn("000003", kept)  # 持仓仍进打分，待硬清仓
                self.assertNotIn("000099", kept)
                self.assertEqual(meta.get("force_exit_held"), ["000003"])
                self.assertEqual(meta.get("n_force_exit_held"), 1)
                self.assertEqual(meta.get("tradable_codes"), ["000001"])

                # 池变了 → 闸拒绝
                bad, meta_bad = filter_codes_by_predictability_live(
                    pool + ["000088"], keep=[]
                )
                self.assertFalse(meta_bad.get("gate_ok"))
                self.assertIn("不一致", str(meta_bad.get("gate_error") or ""))

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

        with tempfile.TemporaryDirectory() as td2, patch(
            "core.research.predictability_tiers.predictability_tiers_last_path",
            return_value=os.path.join(td2, "missing_last.json"),
        ), patch(
            "core.research.predictability_tiers.predictability_tiers_active_path",
            return_value=os.path.join(td2, "active.json"),
        ):
            fail = promote_predictability_tiers_to_live()
            self.assertFalse(fail.get("ok"))

    def test_validate_predictability_tiers_report(self):
        from datetime import date, timedelta

        from core.research.predictability_tiers import (
            PROTOCOL_HOLDOUT_OOS,
            TITLE_METRIC_WINDOW,
            pool_codes_fingerprint,
            validate_predictability_tiers_report,
        )

        today = date.today()
        codes = ["000001", "000002"]
        base = {
            "success": True,
            "protocol": PROTOCOL_HOLDOUT_OOS,
            "title_metric_window": TITLE_METRIC_WINDOW,
            "holdout_n": 20,
            "as_of_tier_last": (today - timedelta(days=25)).isoformat(),
            "as_of_eval_last": today.isoformat(),
            "pool_codes_fp": pool_codes_fingerprint(codes),
        }
        self.assertTrue(
            validate_predictability_tiers_report(
                base, holdout_n=20, pool_codes=codes
            ).get("ok")
        )
        self.assertFalse(
            validate_predictability_tiers_report(
                {**base, "protocol": "holdout_oos_half"},
                pool_codes=codes,
            ).get("ok")
        )
        self.assertFalse(
            validate_predictability_tiers_report(
                base, holdout_n=40, pool_codes=codes
            ).get("ok")
        )
        stale = {
            **base,
            "as_of_eval_last": (today - timedelta(days=30)).isoformat(),
        }
        self.assertFalse(
            validate_predictability_tiers_report(stale, pool_codes=codes).get("ok")
        )
        no_fp = {k: v for k, v in base.items() if k != "pool_codes_fp"}
        self.assertFalse(
            validate_predictability_tiers_report(no_fp, pool_codes=codes).get("ok")
        )
        # 定档末日可早于 Holdout；新鲜度只看评测末日
        train_old = {
            **base,
            "as_of_tier_last": (today - timedelta(days=25)).isoformat(),
            "as_of_eval_last": today.isoformat(),
        }
        self.assertTrue(
            validate_predictability_tiers_report(
                train_old, pool_codes=codes
            ).get("ok")
        )
        # 无 as_of_eval_last 时回落 holdout_split.eval_dates
        via_split = {
            k: v for k, v in base.items() if k != "as_of_eval_last"
        }
        via_split["holdout_split"] = {
            "eval_dates": [(today - timedelta(days=1)).isoformat()]
        }
        self.assertTrue(
            validate_predictability_tiers_report(
                via_split, pool_codes=codes
            ).get("ok")
        )
        # 禁止用定档末日冒充评测末日
        tier_only = {
            k: v for k, v in base.items() if k != "as_of_eval_last"
        }
        tier_only["as_of_tier_last"] = today.isoformat()
        self.assertFalse(
            validate_predictability_tiers_report(
                tier_only, pool_codes=codes
            ).get("ok")
        )
        # 缺协议 / 缺评测窗 → 拒绝
        self.assertFalse(
            validate_predictability_tiers_report(
                {**base, "protocol": ""}, pool_codes=codes
            ).get("ok")
        )
        self.assertFalse(
            validate_predictability_tiers_report(
                {**base, "title_metric_window": ""}, pool_codes=codes
            ).get("ok")
        )
        # 请求 Holdout 但报告缺 holdout_n
        no_hold = {k: v for k, v in base.items() if k != "holdout_n"}
        self.assertFalse(
            validate_predictability_tiers_report(
                no_hold, holdout_n=20, pool_codes=codes
            ).get("ok")
        )
        # 坏 holdout_n
        self.assertFalse(
            validate_predictability_tiers_report(
                {**base, "holdout_n": "xx"}, pool_codes=codes
            ).get("ok")
        )
        # 有指纹字段、无当前池 → 仍可通过（晋级路径）
        self.assertTrue(
            validate_predictability_tiers_report(base).get("ok")
        )

    def test_filter_fp_ignores_held_extra(self):
        """指纹只对观察池；持仓并入票不致误杀。"""
        from datetime import date

        from core.research.predictability_tiers import (
            PROTOCOL_HOLDOUT_OOS,
            TITLE_METRIC_WINDOW,
            filter_codes_by_predictability_live,
            pool_codes_fingerprint,
            promote_predictability_tiers_to_live,
            save_predictability_tiers_last,
        )

        today = date.today().isoformat()
        pool = ["000001", "000002"]
        report = {
            "success": True,
            "protocol": PROTOCOL_HOLDOUT_OOS,
            "title_metric_window": TITLE_METRIC_WINDOW,
            "holdout_n": 20,
            "as_of_tier_last": today,
            "as_of_eval_last": today,
            "pool_codes_fp": pool_codes_fingerprint(pool),
            "counts": {"A": 1, "B": 1, "C": 0},
            "rows": [
                {"code": "000001", "tier": "A"},
                {"code": "000002", "tier": "B"},
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
                self.assertTrue(promote_predictability_tiers_to_live().get("ok"))
                # 并入观察池外持仓 → 若用全量指纹会拒；显式 fp 池则过
                uni = pool + ["600000"]
                bad, meta_bad = filter_codes_by_predictability_live(
                    uni, keep=["600000"]
                )
                self.assertFalse(meta_bad.get("gate_ok"))
                self.assertIsNone(meta_bad.get("tradable_codes"))
                kept, meta = filter_codes_by_predictability_live(
                    uni, keep=["600000"], pool_codes_for_fp=pool
                )
                self.assertTrue(meta.get("gate_ok"))
                self.assertIn("000001", kept)
                self.assertIn("600000", kept)
                self.assertEqual(meta.get("force_exit_held"), ["600000"])

    def test_save_stamps_updated_at_and_attach_fills_mtime(self):
        import json

        from core.research.predictability_tiers import (
            attach_saved_at,
            save_predictability_tiers_last,
        )

        stamped = attach_saved_at(
            {"finished_at": "2026-11-03T04:14:00+00:00"}, "/no/such.json"
        )
        self.assertEqual(stamped.get("finished_at"), "2026-11-03T04:14:00+00:00")
        self.assertNotIn("saved_at", stamped)

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "predictability_tiers_last.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"success": True}, f)
            filled = attach_saved_at({"success": True}, path)
            self.assertTrue(str(filled.get("saved_at") or "").strip())
            kept = attach_saved_at(
                {"success": True, "updated_at": "2026-11-03T01:00:00+00:00"},
                path,
            )
            self.assertNotIn("saved_at", kept)

            with patch(
                "core.research.predictability_tiers.predictability_tiers_last_path",
                return_value=path,
            ), patch(
                "core.research.predictability_tiers.load_predictability_tiers_active",
                return_value=None,
            ):
                report = {"success": True, "protocol": "tier60_quad_uplift_holdout_eval"}
                save_predictability_tiers_last(report)
            self.assertTrue(str(report.get("updated_at") or "").strip())
            with open(path, encoding="utf-8") as f:
                disk = json.load(f)
            self.assertEqual(disk.get("updated_at"), report.get("updated_at"))
            skipped = {"success": False}
            save_predictability_tiers_last(skipped)
            self.assertNotIn("updated_at", skipped)


if __name__ == "__main__":
    unittest.main()
