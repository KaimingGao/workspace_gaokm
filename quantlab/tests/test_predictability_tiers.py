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
                    "yhats": [1, 2],
                    "realized": [1],
                    "dates": ["2026-01-01"],
                },
                "skip",
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tier"], "A")
        self.assertNotIn("yhats", rows[0])
        self.assertNotIn("realized", rows[0])

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
        """落盘 names 半空时，用 a_code_name 补齐，不依赖行情。"""
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
            "core.t0.intraday.resolve_stock_name",
            side_effect=lambda c, **_k: {
                "000001": "平安银行",
                "600519": "贵州茅台",
                "300750": "宁德时代",
            }.get(str(c)),
        ):
            out = _watching_name_map(["000001", "600519", "300750"])
        self.assertEqual(out.get("000001"), "平安银行")
        self.assertEqual(out.get("600519"), "贵州茅台")
        self.assertEqual(out.get("300750"), "宁德时代")

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
        self.assertEqual(by["000000"]["rank_oo"], 1)
        self.assertEqual(by["000000"]["rank_tau"], 1)

        # 无样本 → C，不占 A/B 名额
        small = [
            {"code": "a", "hit_rate": 0.8, "n_valid": 5},
            {"code": "b", "hit_rate": 0.7, "n_valid": 5},
            {"code": "c", "hit_rate": None, "n_valid": 0},
        ]
        assign_tiers_by_rank_score(small, require_tau=False, a_count=1, b_count=0)
        by2 = {r["code"]: r for r in small}
        self.assertEqual(by2["a"]["tier"], "A")
        self.assertEqual(by2["b"]["tier"], "C")
        self.assertEqual(by2["c"]["tier"], "C")
        self.assertIsNone(by2["c"]["score"])

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
            summarize_tier_mean_realized,
        )

        acc = {}
        tier = {"d1", "d2"}
        whole = {"d1", "d2", "d3", "d4"}
        for d, y in (("d1", 0.02), ("d2", 0.02), ("d3", -0.04), ("d4", -0.04)):
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
        mean = summarize_tier_mean_realized(acc, codes=["000001"])
        self.assertEqual(mean["n_realized"], 4)
        self.assertAlmostEqual(mean["mean_realized"], -0.01)

    def test_build_holdout_half_tiers_oos(self):
        from core.research.predictability_tiers import (
            PROTOCOL_HOLDOUT_OOS,
            build_holdout_half_tiers,
            load_predictability_tiers_last,
            save_predictability_tiers_last,
            tier_code_set,
        )

        tier_dates = [f"2025-08-{d:02d}" for d in range(1, 11)]

        def _fake_accumulate(codes, **kwargs):
            acc = {
                "000001": {
                    "hits": 7,
                    "n_valid": 7,
                    "n_days": 10,
                    "yhats": [1.0] * 10,
                    "realized": [0.02] * 10,
                    "dates": list(tier_dates),
                    "hits_tau": 6,
                    "n_valid_tau": 7,
                    "tau_yhats": [1.0] * 10,
                    "tau_realized": [1.0] * 6 + [-1.0] * 4,
                    "tau_pair_dates": list(tier_dates),
                },
                "000002": {
                    "hits": 0,
                    "n_valid": 5,
                    "n_days": 10,
                    "yhats": [1.0] * 10,
                    "realized": [-0.01] * 10,
                    "dates": list(tier_dates),
                    "hits_tau": 0,
                    "n_valid_tau": 5,
                    "tau_yhats": [1.0] * 10,
                    "tau_realized": [-1.0] * 10,
                    "tau_pair_dates": list(tier_dates),
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
                    "as_of_tier_last": tier_dates[-1],
                },
                "split_meta": {"eval_start": "2025-08-01", "test_days": list(tier_dates)},
                "n_train": 40,
                "n_test": 20,
                "horizon_days": 1,
                "source": "oo_holdout_oos",
                "tau_meta": {"ok": True, "n_rows": 12, "path": "/tmp/tau_research.json"},
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
                self.assertEqual(by["000001"]["tier"], "A")
                self.assertEqual(by["000002"]["tier"], "C")
                self.assertTrue((rep.get("thresholds") or {}).get("require_tau"))
                thr = rep.get("thresholds") or {}
                self.assertEqual(int(thr.get("a_count")), 1)
                self.assertEqual(int(thr.get("b_count")), 0)
                self.assertIn("1/rank", str(thr.get("score") or ""))
                self.assertIn("cs_by_tier_tau", rep)
                self.assertIn("hit_rate_tau", by["000001"])
                self.assertGreaterEqual(float(by["000001"]["hit_rate_tau"] or 0), 0.55)
                self.assertIsNotNone(by["000001"].get("score"))
                self.assertEqual(by["000001"].get("rank_oo"), 1)
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
                acc[code] = {
                    "hits": hits,
                    "n_valid": n,
                    "n_days": n,
                    "yhats": [1.0] * n,
                    "realized": [1.0 if hits else -1.0] * n,
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
