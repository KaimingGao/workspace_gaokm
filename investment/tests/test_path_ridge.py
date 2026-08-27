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

    def test_z_rows_keep_yclose_loc_key(self):
        from core.research.path_panel import PATH_Z_FEATURES, build_path_panels_from_bars
        from core.research.path_ridge import _z_only_xs

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
        self.assertEqual(report.get("schema"), "path_ridge_v2")
        self.assertEqual(report.get("target"), "extreme_order_signed_range")
        self.assertEqual(report.get("sell_trig_pct"), 1.0)
        self.assertEqual(report.get("buy_trig_pct"), 1.0)
        rm = report["return_model"]
        self.assertTrue(rm.get("y_demeaned"))
        self.assertIn("y_label_mean", rm)
        self.assertEqual(rm.get("path_label_mode"), "extreme_order")
        self.assertEqual(
            (rm.get("y_spec") or {}).get("formula"), "extreme_order(low,high)"
        )
        oos = report.get("oos") or {}
        self.assertIn("buckets", oos)
        self.assertIn("abs_ge_2", oos["buckets"])
        self.assertIn("balance", oos)
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
                self.assertEqual(doc.get("sell_trig_pct"), 1.0)
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
        ), patch(
            "core.execution.resolve_t0_rules",
            return_value={"sell_trigger_pct": 1.2, "buy_trigger_pct": 0.9},
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
        self.assertTrue(out.get("triggers_from_paper"))
        self.assertAlmostEqual(float(out.get("sell_trig_pct")), 1.2)
        self.assertAlmostEqual(float(out.get("buy_trig_pct")), 0.9)
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

    def test_get_path_ridge_model_missing(self):
        from quant.services.quant_service_factors import QuantFactorMixin

        class Svc(QuantFactorMixin):
            pass

        with patch("quant.research.path_ridge.load_path_model", return_value=None):
            out = Svc().get_path_ridge_model()
        self.assertFalse(out.get("exists"))


if __name__ == "__main__":
    unittest.main()
