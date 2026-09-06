"""ŷ_path 路径头面板与 Ridge 测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _bars(n: int = 40, start: float = 10.0):
    from datetime import date, timedelta

    out = []
    px = start
    d0 = date(2025, 6, 1)
    for i in range(n):
        o = px
        c = px * (1.01 if i % 3 else 0.99)
        day = d0 + timedelta(days=i)
        out.append(
            {
                "date": day.isoformat(),
                "open": round(o, 4),
                "high": round(max(o, c) * 1.01, 4),
                "low": round(min(o, c) * 0.99, 4),
                "close": round(c, 4),
                "volume": 1e6 + i * 1000,
            }
        )
        px = c
    return out


def _minute_low_then_high(open_px: float, dkey: str):
    """先 low 后 high：label 为正 (H−L)/ref%。"""
    return [
        {
            "date": dkey,
            "time": "09:35",
            "open": open_px,
            "high": round(open_px * 1.01, 4),
            "low": round(open_px * 0.98, 4),
            "close": open_px,
        },
        {
            "date": dkey,
            "time": "09:40",
            "open": open_px,
            "high": round(open_px * 1.05, 4),
            "low": round(open_px * 0.99, 4),
            "close": round(open_px * 1.02, 4),
        },
    ]


def _minute_high_then_low(open_px: float, dkey: str):
    """先 high 后 low：label 为负 (L−H)/ref%。"""
    return [
        {
            "date": dkey,
            "time": "09:35",
            "open": open_px,
            "high": round(open_px * 1.02, 4),
            "low": round(open_px * 0.99, 4),
            "close": round(open_px * 1.01, 4),
        },
        {
            "date": dkey,
            "time": "09:40",
            "open": open_px,
            "high": round(open_px * 1.01, 4),
            "low": round(open_px * 0.95, 4),
            "close": open_px,
        },
    ]


def _minute_sell_first(open_px: float, dkey: str):
    sell_level = open_px * 1.02
    return [
        {
            "date": dkey,
            "time": "09:35",
            "open": open_px,
            "high": round(sell_level * 1.002, 4),
            "low": round(open_px * 0.999, 4),
            "close": round(open_px * 1.005, 4),
        }
    ]


def _minute_buy_first(open_px: float, dkey: str):
    buy_level = open_px * 0.985
    return [
        {
            "date": dkey,
            "time": "09:35",
            "open": open_px,
            "high": round(open_px * 1.001, 4),
            "low": round(buy_level * 0.998, 4),
            "close": round(open_px * 0.995, 4),
        }
    ]


def _minute_map_for_bars(bars, *, low_then_high: bool = True):
    out = {}
    for b in bars:
        dkey = str(b.get("date") or "")[:10]
        op = float(b["open"])
        out[dkey] = (
            _minute_low_then_high(op, dkey)
            if low_then_high
            else _minute_high_then_low(op, dkey)
        )
    return out


class TestPathPanel(unittest.TestCase):
    def test_extreme_order_low_then_high(self):
        from core.research.path_panel import extreme_order_path_label

        mins = _minute_low_then_high(10.0, "2025-06-01")
        label, reason = extreme_order_path_label(mins, ref=10.0)
        self.assertEqual(reason, "low_then_high")
        self.assertGreater(label, 0)
        self.assertAlmostEqual(label, 7.0, places=2)

    def test_extreme_order_high_then_low(self):
        from core.research.path_panel import extreme_order_path_label

        mins = _minute_high_then_low(10.0, "2025-06-01")
        label, reason = extreme_order_path_label(mins, ref=10.0)
        self.assertEqual(reason, "high_then_low")
        self.assertLess(label, 0)
        self.assertAlmostEqual(label, -7.0, places=2)

    def test_first_touch_label_sell_first(self):
        from core.research.path_panel import first_touch_path_label

        label, reason = first_touch_path_label(
            _minute_sell_first(10.0, "2025-06-01"),
            ref=10.0,
            sell_trig_pct=2.0,
            buy_trig_pct=1.5,
        )
        self.assertEqual(label, 100.0)
        self.assertEqual(reason, "sell_first")

    def test_attach_path_realized(self):
        from core.research.path_panel import attach_path_realized

        day = attach_path_realized(
            {"scores": {"y_path": -1.2}},
            _minute_low_then_high(10.0, "2025-06-01"),
            ref=10.0,
        )
        self.assertGreater(day.get("path_realized"), 0)
        self.assertEqual(day.get("path_realized_reason"), "low_then_high")
        self.assertEqual(day["scores"].get("path_realized"), day.get("path_realized"))

    def test_collect_prefers_minute_open_ref(self):
        from core.research.path_panel import collect_path_day_sample

        # 日线 open=10，分钟 open=9.85（−1.5%）；用日线锚会误判，用分钟锚应 sell_first
        day = {
            "date": "2025-06-10",
            "open": 10.0,
            "high": 10.2,
            "low": 9.8,
            "close": 10.1,
            "prev_close": 9.9,
        }
        hist = _bars(20, 9.5)
        # 相对分钟 open 9.85：先 low 后 high
        mins = [
            {
                "date": "2025-06-10",
                "open": 9.85,
                "high": 9.85 * 1.01,
                "low": 9.85 * 0.98,
                "close": 9.88,
            },
            {
                "date": "2025-06-10",
                "open": 9.9,
                "high": 9.85 * 1.05,
                "low": 9.9,
                "close": 9.95,
            },
        ]
        sample = collect_path_day_sample(
            code="T",
            day_bar=day,
            hist_bars=hist,
            minute_bars=mins,
        )
        self.assertIsNotNone(sample)
        self.assertEqual(sample["ref_src"], "minute_open")
        self.assertLess(float(sample["ref_mismatch_pct"]), -0.5)
        self.assertGreater(float(sample["label"]), 0)
        self.assertEqual(sample["label_reason"], "low_then_high")

    def test_audit_flags_short_minute_span(self):
        from core.research.path_panel import audit_path_minute_coverage

        bars = _bars(30, 10.0)
        # 只给最近 5 日分钟
        minute_map = {"A": {b["date"]: _minute_low_then_high(float(b["open"]), b["date"]) for b in bars[-5:]}}
        audit = audit_path_minute_coverage(
            [{"code": "A", "bars": bars}],
            minute_map,
            sell_trig_pct=2.0,
            buy_trig_pct=1.5,
        )
        self.assertEqual(audit["minute_span_days_med"], 5)
        self.assertTrue(any("过短" in w or "跨度" in w for w in (audit.get("warnings") or [])))

    def test_build_path_panels_from_bars(self):
        from core.research.path_panel import build_path_panels_from_bars

        bars_a = _bars(35, 10.0)
        bars_b = _bars(35, 12.0)
        stock_bars = [
            {"code": "A", "bars": bars_a},
            {"code": "B", "bars": bars_b},
        ]
        minute_map = {
            "A": _minute_map_for_bars(bars_a, low_then_high=True),
            "B": _minute_map_for_bars(bars_b, low_then_high=False),
        }
        panels = build_path_panels_from_bars(stock_bars, minute_by_code_date=minute_map)
        self.assertGreaterEqual(len(panels), 2)
        total = sum(len(p.get("ys") or []) for p in panels)
        self.assertGreaterEqual(total, 20)

    def test_cross_section_breadth_uses_full_pool(self):
        from core.research.path_panel import build_path_panels_from_bars

        bars_a = _bars(35, 10.0)
        bars_b = _bars(35, 12.0)
        minute_map = {
            "A": _minute_map_for_bars(bars_a, low_then_high=True),
            "B": _minute_map_for_bars(bars_b, low_then_high=False),
        }
        panels = build_path_panels_from_bars(
            [{"code": "A", "bars": bars_a}, {"code": "B", "bars": bars_b}],
            minute_by_code_date=minute_map,
        )
        # 同一交易日两票应共享截面 breadth（非逐票 0/1）
        by_date: dict = {}
        for p in panels:
            for i, d in enumerate(p.get("dates") or []):
                xs = (p.get("xs") or [])[i] if i < len(p.get("xs") or []) else {}
                if isinstance(xs, dict) and xs.get("sector_gap_breadth") is not None:
                    by_date.setdefault(str(d)[:10], []).append(float(xs["sector_gap_breadth"]))
        shared = [d for d, vals in by_date.items() if len(vals) >= 2]
        self.assertTrue(shared, "expected overlapping path sample dates")
        for d in shared[:5]:
            self.assertEqual(len(set(by_date[d])), 1, f"breadth should match on {d}")

    def test_multi_tau_grid_expands_rows(self):
        from core.research.path_panel import build_path_panels_from_bars
        from core.research.tau_panel import DEFAULT_MINUTE_TAU_GRID

        bars = _bars(25, 10.0)
        panels_1 = build_path_panels_from_bars(
            [{"code": "A", "bars": bars}],
            minute_by_code_date={"A": _minute_map_for_bars(bars, low_then_high=True)},
            tau_grid=None,
            minute_tau_hm="10:30",
        )
        panels_n = build_path_panels_from_bars(
            [{"code": "A", "bars": bars}],
            minute_by_code_date={"A": _minute_map_for_bars(bars, low_then_high=True)},
            tau_grid=list(DEFAULT_MINUTE_TAU_GRID),
        )
        n1 = sum(len(p.get("ys") or []) for p in panels_1)
        nn = sum(len(p.get("ys") or []) for p in panels_n)
        self.assertGreater(n1, 0)
        self.assertEqual(nn, n1 * len(DEFAULT_MINUTE_TAU_GRID))
        metas = []
        for p in panels_n:
            metas.extend(p.get("metas") or [])
        taus = {str(m.get("tau")) for m in metas}
        self.assertEqual(taus, set(DEFAULT_MINUTE_TAU_GRID))

    def test_z_rows_keep_yclose_loc_key(self):
        from core.research.path_panel import PATH_Z_FEATURES, build_path_panels_from_bars
        from core.research.path_ridge import _z_only_xs
        from core.signal.minute_tau_feats import MINUTE_TAU_PACK_KEYS

        bars_a = _bars(35, 10.0)
        bars_b = _bars(35, 12.0)
        panels = build_path_panels_from_bars(
            [{"code": "A", "bars": bars_a}, {"code": "B", "bars": bars_b}],
            minute_by_code_date={
                "A": _minute_map_for_bars(bars_a, low_then_high=True),
                "B": _minute_map_for_bars(bars_b, low_then_high=False),
            },
        )
        xs = []
        for p in panels:
            xs.extend(p.get("xs") or [])
        z = _z_only_xs(xs)
        self.assertTrue(z)
        for row in z[:5]:
            for k in PATH_Z_FEATURES:
                self.assertIn(k, row)
            self.assertIn("yclose_loc", row)
        # 分钟小包应写入（夹具 09:35/09:40 ≤ 默认 10:30）
        filled = sum(1 for row in z if row.get("ret_open_to_tau") is not None)
        self.assertGreater(filled, 0, "expected ret_open_to_tau from path minute pack")
        for k in ("range_pct", "path_sign"):
            self.assertIn(k, MINUTE_TAU_PACK_KEYS)
            self.assertTrue(any(row.get(k) is not None for row in z))


class TestPathRidgeHelpers(unittest.TestCase):
    def test_balance_signed_train(self):
        from core.research.path_ridge import _balance_signed_train

        xs = [{"i": i} for i in range(10)]
        ys = [100.0] * 7 + [-100.0] * 3
        metas = [{"m": i} for i in range(10)]
        xs2, ys2, metas2, info = _balance_signed_train(xs, ys, metas, seed=1)
        self.assertTrue(info.get("balanced"))
        self.assertEqual(info.get("n_keep"), 6)
        self.assertEqual(sum(1 for y in ys2 if y > 0), 3)
        self.assertEqual(sum(1 for y in ys2 if y < 0), 3)
        self.assertEqual(len(xs2), len(ys2))
        self.assertEqual(len(ys2), len(metas2))

    def test_path_promote_gate(self):
        from core.research.path_ridge import path_promote_gate

        # 全样本差但无强桶 → 硬拦
        bad = path_promote_gate({"oos": {"sign_hit": 0.5, "n_valid": 100}})
        self.assertFalse(bad["ok"])
        self.assertTrue(any("≥2" in b or "abs" in b.lower() or "桶" in b for b in bad["blockers"]))

        # 强桶过闸：全样本可软
        ok = path_promote_gate(
            {
                "oos": {
                    "sign_hit": 0.54,
                    "n_valid": 100,
                    "buckets": {"abs_ge_2": {"n": 45, "sign_hit": 0.70}},
                }
            }
        )
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["blockers"], [])
        self.assertTrue(any("全样本" in w for w in (ok.get("warnings") or [])))

    def test_path_label_triggers(self):
        from core.research.path_ridge import path_label_triggers

        sell, buy = path_label_triggers(
            {
                "return_model": {"sell_trig_pct": 1.0, "buy_trig_pct": 1.0},
            }
        )
        self.assertEqual(sell, 1.0)
        self.assertEqual(buy, 1.0)
        sell2, buy2 = path_label_triggers({})
        self.assertEqual(sell2, 2.0)
        self.assertEqual(buy2, 1.5)


class TestPathRidgeFit(unittest.TestCase):
    def test_fit_and_persist(self):
        from quant.research.path_ridge import (
            fit_path_ridge_report,
            load_path_model,
            persist_path_model,
            predict_path_from_features,
        )

        bars_a = _bars(40, 10.0)
        bars_b = _bars(40, 12.0)
        bars_c = _bars(40, 8.0)
        stock_bars = [
            {"code": "A", "bars": bars_a},
            {"code": "B", "bars": bars_b},
            {"code": "C", "bars": bars_c},
        ]
        minute_map = {
            "A": _minute_map_for_bars(bars_a, low_then_high=True),
            "B": _minute_map_for_bars(bars_b, low_then_high=False),
            "C": _minute_map_for_bars(bars_c, low_then_high=True),
        }
        report = fit_path_ridge_report(
            stock_bars,
            minute_by_code_date=minute_map,
            ridge_lambda=1.0,
            sell_trig_pct=1.0,
            buy_trig_pct=1.0,
        )
        self.assertTrue(report.get("success"), report.get("error"))
        self.assertEqual(report.get("schema"), "path_ridge_v4")
        self.assertTrue(report.get("tau_grid"))
        self.assertIn("by_tau", report.get("oos") or {})
        self.assertEqual(report.get("target"), "extreme_order_signed_range")
        self.assertEqual(report.get("sell_trig_pct"), 1.0)
        self.assertEqual(report.get("buy_trig_pct"), 1.0)
        rm = report["return_model"]
        self.assertTrue(rm.get("y_demeaned"))
        self.assertIn("y_label_mean", rm)
        self.assertEqual(rm.get("path_label_mode"), "extreme_order")
        formula = str((rm.get("y_spec") or {}).get("formula") or "")
        self.assertIn("extreme_order(low,high)", formula)
        self.assertIn("5m τ", formula)
        extras = rm.get("extra_features") or []
        self.assertIn("path_lag1", extras)
        self.assertIn("path_ma5", extras)
        self.assertIn("t_hi_frac", extras)
        self.assertIn("t_lo_frac", extras)
        prep = rm.get("prep_meta") or {}
        imputed = set(prep.get("imputed_keys") or [])
        self.assertTrue({"t_hi_frac", "t_lo_frac"} & imputed)
        self.assertNotIn("prefix_complexity", extras)
        self.assertNotIn("prefix_tpd", extras)
        self.assertNotIn("path_range_lag1", extras)
        self.assertNotIn("path_sign_streak", extras)
        self.assertIn("path_lag1", rm.get("feat_labels") or {})
        self.assertIn("09:30", formula)
        self.assertIn("11:00", formula)
        self.assertNotIn("τ∈{", formula)
        self.assertNotIn("11:30", formula)
        self.assertEqual((rm.get("y_spec") or {}).get("tau_grid"), report.get("tau_grid"))
        oos = report.get("oos") or {}
        self.assertIn("buckets", oos)
        self.assertIn("by_tau", oos)
        self.assertEqual(oos.get("train_frac"), 0.9)
        self.assertIn("n_train", oos)
        self.assertGreaterEqual(len(oos.get("by_tau") or {}), 2)
        gate = report.get("promote_gate") or {}
        self.assertIn("ok", gate)

        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            with patch("core.paths.LIVE_DIR", live):
                blocked = persist_path_model(report, note="test")
                # 强桶硬闸：小样本通常缺 |ŷ|≥2% n，默认应拦 promote
                if not gate.get("ok"):
                    self.assertFalse(blocked.get("success"))
                    self.assertIn("promote_gate", blocked)
                forced = persist_path_model(report, note="test", force=True)
                self.assertTrue(forced.get("success"), forced)
                doc = load_path_model()
                self.assertIsNotNone(doc)
                self.assertEqual(doc.get("dual_score_head"), "y_path")
                self.assertEqual(doc.get("schema"), "path_ridge_v4")
                self.assertEqual(doc.get("sell_trig_pct"), 1.0)
                self.assertEqual(doc.get("tau_grid"), report.get("tau_grid"))
                self.assertEqual(doc.get("minute_tau_hm"), report.get("minute_tau_hm"))
                yhat = predict_path_from_features(
                    {"gap_pct": 1.2, "gap_atr": 0.5, "theme_day": 0.1},
                    model_doc=doc,
                )
                self.assertIsNotNone(yhat)

    def test_load_path_model_falls_back_to_last_report(self):
        from quant.research.path_ridge import (
            fit_path_ridge_report,
            load_path_model,
            save_path_last_report,
        )

        bars = _bars(40, 10.0)
        minute_map = _minute_map_for_bars(bars, low_then_high=True)
        report = fit_path_ridge_report(
            [{"code": "A", "bars": bars}, {"code": "B", "bars": _bars(40, 12.0)}],
            minute_by_code_date={
                "A": minute_map,
                "B": _minute_map_for_bars(_bars(40, 12.0), low_then_high=False),
            },
            ridge_lambda=1.0,
        )
        self.assertTrue(report.get("success"), report.get("error"))
        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            with patch("core.paths.LIVE_DIR", live):
                save_path_last_report(report)
                doc = load_path_model()
                self.assertIsNotNone(doc)
                self.assertTrue(doc.get("_shadow"))
                self.assertIn("return_model", doc)


class TestPathRidgeService(unittest.TestCase):
    def test_run_path_ridge_experiment_with_mock_minutes(self):
        from quant.services.quant_service_factors import QuantFactorMixin

        class Svc(QuantFactorMixin):
            pass

        bars = _bars(40, 10.0)
        minute_map = _minute_map_for_bars(bars, low_then_high=True)

        with patch("core.watching.store.read_watching") as rw, patch(
            "core.data.facade.bars_and_source", return_value=(bars, "mock")
        ), patch(
            "core.store.load_minute_cache",
            return_value=(list(minute_map.values())[0], {}),
        ), patch(
            "core.ports.market.group_minute_bars_by_date",
            return_value=minute_map,
        ):
            rw.return_value = {"watchlist": ["A", "B"]}
            svc = Svc()
            out = svc.run_path_ridge_experiment(
                lookback=40,
                watching_limit=2,
                persist=False,
            )
        self.assertTrue(out.get("success"), out.get("error"))
        self.assertGreaterEqual(int(out.get("minute_codes_hit") or 0), 1)
        self.assertIn("return_model", out)
        self.assertAlmostEqual(float(out.get("sell_trig_pct")), 2.0)
        self.assertAlmostEqual(float(out.get("buy_trig_pct")), 1.5)
        self.assertIn("promote_gate", out)

    def test_explain_path_prediction_terms(self):
        from core.research.path_ridge import explain_path_prediction

        model = {
            "return_model": {
                "intercept": 1.0,
                "coefficients": {"gap_pct": 2.0, "mom3_pct": -1.0},
                "active_features": ["gap_pct", "mom3_pct"],
                "zscore_means": {"gap_pct": 0.0, "mom3_pct": 0.0},
                "zscore_stds": {"gap_pct": 1.0, "mom3_pct": 1.0},
            }
        }
        expl = explain_path_prediction(
            {"gap_pct": 1.0, "mom3_pct": 2.0}, model_doc=model
        )
        self.assertIsNotNone(expl)
        self.assertEqual(expl["head"], "path")
        # 1 + 2*1 + (-1)*2 = 1
        self.assertAlmostEqual(float(expl["total"]), 1.0, places=4)
        keys = [t["key"] for t in expl["terms"]]
        self.assertEqual(keys[0], "gap_pct")  # |contrib| 2 > 2? wait both 2 and 2
        self.assertIn("mom3_pct", keys)
        by_key = {t["key"]: t for t in expl["terms"]}
        self.assertEqual(by_key["gap_pct"]["label"], "跳空 %")
        self.assertEqual(by_key["mom3_pct"]["label"], "近3日动量 %")

    def test_explain_path_minute_feat_labels_zh(self):
        from core.research.path_ridge import explain_path_prediction

        model = {
            "return_model": {
                "intercept": 0.0,
                "coefficients": {
                    "ret_open_to_tau": 1.0,
                    "range_pct": 0.5,
                    "pullback_from_high": -0.2,
                    "t_hi_frac": 0.3,
                },
                "active_features": [
                    "ret_open_to_tau",
                    "range_pct",
                    "pullback_from_high",
                    "t_hi_frac",
                ],
                "zscore_means": {},
                "zscore_stds": {},
            }
        }
        expl = explain_path_prediction(
            {
                "ret_open_to_tau": 1.0,
                "range_pct": 2.0,
                "pullback_from_high": 1.0,
                "t_hi_frac": 0.4,
            },
            model_doc=model,
        )
        by_key = {t["key"]: t["label"] for t in (expl or {}).get("terms") or []}
        self.assertEqual(by_key["ret_open_to_tau"], "开盘→τ 收益 %")
        self.assertEqual(by_key["range_pct"], "前缀振幅 %")
        self.assertEqual(by_key["pullback_from_high"], "自高回撤 %")
        self.assertEqual(by_key["t_hi_frac"], "最高点相对前缀进度")

    def test_get_path_ridge_model_missing(self):
        from quant.services.quant_service_factors import QuantFactorMixin

        class Svc(QuantFactorMixin):
            pass

        with patch("quant.research.path_ridge.load_path_model", return_value=None):
            out = Svc().get_path_ridge_model()
        self.assertFalse(out.get("exists"))


class PathLagFeatureTests(unittest.TestCase):
    def test_lag1_and_ma5_are_pit(self):
        from core.research.path_panel import (
            PATH_Z_FEATURES,
            extreme_order_path_label,
            path_lag_features,
            realized_path_by_date,
        )

        self.assertNotIn("path_lag1", PATH_Z_FEATURES)
        self.assertNotIn("path_range_lag1", PATH_Z_FEATURES)
        self.assertNotIn("path_sign_streak", PATH_Z_FEATURES)
        self.assertNotIn("t_hi_frac", PATH_Z_FEATURES)
        self.assertNotIn("prefix_complexity", PATH_Z_FEATURES)
        days = ["2025-06-02", "2025-06-03", "2025-06-04", "2025-06-05", "2025-06-06", "2025-06-09"]
        minute_by_date = {}
        daily = []
        labels = []
        px = 10.0
        for i, dkey in enumerate(days):
            if i % 2:
                bars_m = _minute_high_then_low(px, dkey)
            else:
                bars_m = _minute_low_then_high(px, dkey)
            minute_by_date[dkey] = bars_m
            y, reason = extreme_order_path_label(bars_m, ref=px)
            self.assertNotEqual(reason, "invalid_ref_or_empty")
            labels.append(float(y))
            daily.append({"date": dkey, "open": px, "close": bars_m[-1]["close"]})
            px = bars_m[-1]["close"]
        path_map = realized_path_by_date(minute_by_date)
        asof = days[-1]
        lags = path_lag_features(
            hist_bars=daily,
            path_by_date=path_map,
            asof_date=asof,
        )
        self.assertAlmostEqual(lags["path_lag1"], labels[-2], places=5)
        self.assertNotAlmostEqual(lags["path_lag1"], labels[-1], places=2)
        expected_ma = sum(labels[-6:-1][-5:]) / 5.0
        self.assertAlmostEqual(lags["path_ma5"], expected_ma, places=5)
        self.assertNotIn("path_range_lag1", lags)
        self.assertNotIn("path_sign_streak", lags)

    def test_lag_skips_days_without_minutes(self):
        from core.research.path_panel import path_lag_features

        hist = [
            {"date": "2025-06-02"},
            {"date": "2025-06-03"},
            {"date": "2025-06-04"},
            {"date": "2025-06-05"},
        ]
        path_map = {"2025-06-02": 1.10, "2025-06-04": 2.40}
        lags = path_lag_features(
            hist_bars=hist,
            path_by_date=path_map,
            asof_date="2025-06-05",
        )
        self.assertAlmostEqual(lags["path_lag1"], 2.40)
        self.assertAlmostEqual(lags["path_ma5"], 1.75)

    def test_attach_path_lag_from_minute_map(self):
        from core.research.path_panel import (
            attach_path_lag_features,
            extreme_order_path_label,
        )

        d0, d1, d2 = "2025-06-02", "2025-06-03", "2025-06-04"
        m0 = _minute_low_then_high(10.0, d0)
        m1 = _minute_high_then_low(10.0, d1)
        y1, _reason = extreme_order_path_label(m1, ref=10.0)
        feats = attach_path_lag_features(
            {},
            hist_bars=[{"date": d0}, {"date": d1}, {"date": d2}],
            asof_date=d2,
            minute_by_date={d0: m0, d1: m1, d2: m0},
        )
        self.assertAlmostEqual(feats["path_lag1"], float(y1), places=5)
        self.assertIsNotNone(feats["path_ma5"])
        y2, _reason2 = extreme_order_path_label(m0, ref=10.0)
        self.assertNotAlmostEqual(feats["path_lag1"], float(y2), places=2)

    def test_t_day_path_label_not_used_as_factor(self):
        from core.research.path_panel import (
            attach_path_lag_features,
            extreme_order_path_label,
            path_lag_features,
        )

        d0, d_t = "2025-06-02", "2025-06-03"
        m0 = _minute_low_then_high(10.0, d0)
        mt = _minute_high_then_low(10.0, d_t)
        y0, _ = extreme_order_path_label(m0, ref=10.0)
        yt, _ = extreme_order_path_label(mt, ref=10.0)
        feats = attach_path_lag_features(
            {},
            hist_bars=[{"date": d0}, {"date": d_t}],
            asof_date=d_t,
            minute_by_date={d0: m0, d_t: mt},
        )
        self.assertAlmostEqual(feats["path_lag1"], float(y0), places=5)
        self.assertNotAlmostEqual(feats["path_lag1"], float(yt), places=2)
        empty = path_lag_features(
            hist_bars=[{"date": d0}, {"date": d_t}],
            path_by_date={d0: y0, d_t: yt},
            asof_date="",
        )
        self.assertIsNone(empty["path_lag1"])

    def test_attach_does_not_write_withdrawn_hist_keys(self):
        from core.research.path_panel import attach_path_lag_features

        d0, d_t = "2025-06-02", "2025-06-03"
        m0 = _minute_low_then_high(10.0, d0)
        mt = _minute_high_then_low(10.0, d_t)
        feats = attach_path_lag_features(
            {},
            hist_bars=[{"date": d0}, {"date": d_t}],
            asof_date=d_t,
            minute_by_date={d0: m0, d_t: mt},
        )
        self.assertNotIn("path_range_lag1", feats)
        self.assertNotIn("path_sign_streak", feats)


if __name__ == "__main__":
    unittest.main()
