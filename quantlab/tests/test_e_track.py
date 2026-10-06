"""E 轨 · 北极星证据诚实（E0–E4）主干验收。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _synth_bars(n: int = 40, start: float = 10.0, step: float = 0.15):
    bars = []
    px = start
    d = datetime(2024, 1, 2)  # Tuesday
    for i in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        px = px + step * (1 if i % 3 else -0.5)
        bars.append(
            {
                "date": d.strftime("%Y-%m-%d"),
                "open": px,
                "high": px * 1.01,
                "low": px * 0.99,
                "close": px,
                "volume": 1000 + i * 10,
                "amount": (1000 + i * 10) * px,
            }
        )
        d += timedelta(days=1)
    return bars


class TestE0StrategyScope(unittest.TestCase):
    def test_strategy_sharpe_differs_from_all(self):
        from core.north_star import build_north_star_report, compute_paper_risk_metrics

        base = datetime(2026, 1, 5)  # Monday
        snaps = []
        eq_all = 1_000_000.0
        eq_st = 500_000.0
        # 全账户温和上涨；策略仓波动更大
        for i in range(25):
            d = base + timedelta(days=i)
            if d.weekday() >= 5:
                continue
            eq_all *= 1.005
            eq_st *= 1.02 if i % 2 == 0 else 0.99
            snaps.append(
                {
                    "ts": d.isoformat(timespec="seconds"),
                    "equity": round(eq_all, 2),
                    "equity_strategy": round(eq_st, 2),
                }
            )
        all_m = compute_paper_risk_metrics(snaps, scope="all")
        st_m = compute_paper_risk_metrics(snaps, scope="strategy")
        self.assertEqual(all_m["status"], "ok")
        self.assertEqual(st_m["status"], "ok")
        self.assertEqual(st_m.get("scope"), "strategy")
        self.assertNotEqual(all_m.get("rolling_sharpe"), st_m.get("rolling_sharpe"))

        report = build_north_star_report({"snapshots": snaps}, backtest_curve=[])
        self.assertIn("paper_risk_strategy", report)
        self.assertIn("scopes", report)
        self.assertEqual(report["scopes"]["strategy"]["paper_risk"]["scope"], "strategy")

    def test_no_strategy_equity_unavailable(self):
        from core.north_star import compute_paper_risk_metrics

        snaps = [
            {"ts": f"2026-01-{i+1:02d}T12:00:00", "equity": 100 + i} for i in range(20)
        ]
        out = compute_paper_risk_metrics(snaps, scope="strategy")
        self.assertEqual(out["status"], "unavailable")
        self.assertEqual(out["reason"], "no_strategy_equity")


class TestE1AlignCurve(unittest.TestCase):
    def test_save_aligns_to_paper_span(self):
        from core.north_star import load_last_backtest_curve, save_last_backtest_curve

        paper = []
        for i in range(15):
            d = datetime(2026, 3, 1) + timedelta(days=i)
            paper.append({"ts": d.isoformat(timespec="seconds"), "equity": 100 + i})
        # 回测曲线远长于纸面，含纸面前后
        curve = []
        for i in range(60):
            d = datetime(2026, 2, 1) + timedelta(days=i)
            curve.append({"date": d.strftime("%Y-%m-%d"), "equity": 90 + i * 0.5})

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "bt.json")
            save_last_backtest_curve(
                curve,
                path=path,
                align_to_paper=True,
                paper_snapshots=paper,
                meta={"test": True},
            )
            packed = load_last_backtest_curve(path)
            self.assertTrue(packed.get("ok"))
            align = (packed.get("meta") or {}).get("align") or {}
            self.assertTrue(align.get("aligned"))
            self.assertEqual(align.get("align_from"), "2026-03-01")
            pts = packed.get("curve") or []
            self.assertGreaterEqual(len(pts), 6)
            self.assertGreaterEqual(pts[0]["date"], "2026-03-01")
            self.assertLessEqual(pts[-1]["date"], "2026-03-15")

    def test_realization_diagnosis_on_no_overlap(self):
        from core.north_star import compute_realization

        paper = [
            {"ts": f"2026-01-{i+1:02d}T12:00:00", "equity": 100 + i} for i in range(10)
        ]
        bt = [{"date": f"2025-01-{i+1:02d}", "equity": 100 + i} for i in range(10)]
        out = compute_realization(paper, bt)
        self.assertEqual(out["reason"], "no_date_overlap")
        self.assertIn("diagnosis", out)
        self.assertIn("paper_span", out)


class TestE2FactorIcPit(unittest.TestCase):
    def test_ic_report_marks_pit_metadata(self):
        from quant.research.factor_report import compute_factor_ic_report

        bars = _synth_bars(50)
        with patch(
            "core.fundamentals_pit.resolve_fundamentals_for_score",
            return_value={"ok": False, "metrics": None},
        ):
            out = compute_factor_ic_report(
                bars,
                stock_code="600519",
                pit_fundamentals=True,
                fundamentals={"pe": 20},
            )
        self.assertTrue(out.get("success"))
        self.assertTrue(out.get("pit_fundamentals"))
        self.assertIn("pit_resolve_miss", out)

    def test_future_report_not_used_when_ann_missing(self):
        """决策日早于公告日 → resolve 返回空，不应静默用最新快照。"""
        from quant.research.factor_report import _resolve_fund_for_day

        cache = {}
        with patch(
            "core.fundamentals_pit.resolve_fundamentals_for_score",
            return_value={"ok": False, "metrics": None},
        ) as m:
            got = _resolve_fund_for_day(
                "600519",
                "2024-01-15",
                pit_fundamentals=True,
                fundamentals_fallback={"pe": 99},
                cache=cache,
            )
        self.assertIsNone(got)
        m.assert_called()


class TestE3Calendar(unittest.TestCase):
    def test_pool_ic_filters_weekend(self):
        from core.backtest.pool_ic import compute_pool_cross_section_ic
        from core.market.calendar import filter_trading_dates

        # 含周末的共同日期应被滤掉
        raw = ["2024-01-05", "2024-01-06", "2024-01-07", "2024-01-08"]  # Fri Sat Sun Mon
        filtered = filter_trading_dates(raw)
        self.assertNotIn("2024-01-06", filtered)
        self.assertNotIn("2024-01-07", filtered)
        self.assertIn("2024-01-05", filtered)

        stock_bars = {
            f"S{i:02d}": _synth_bars(45, start=10 + i, step=0.1 + i * 0.02)
            for i in range(6)
        }
        out = compute_pool_cross_section_ic(
            stock_bars,
            horizon_days=3,
            min_history=12,
            min_names=4,
            pit_fundamentals=False,
        )
        self.assertEqual(out.get("calendar"), "cn_lite")


class TestE4OutcomeDigest(unittest.TestCase):
    def test_unlabeled_digest(self):
        from core.risk.block_outcome import unlabeled_digest

        logs = [
            {
                "type": "risk_block",
                "ts": "2026-01-01T10:00:00",
                "detail": "单票超限",
                "meta": {"codes": ["600519"], "reason_code": "single_name"},
            },
            {
                "type": "risk_block",
                "ts": "2026-01-02T10:00:00",
                "detail": "已标",
                "meta": {"codes": ["000001"], "outcome": "true_positive"},
            },
        ]
        dig = unlabeled_digest(logs, limit=10)
        self.assertEqual(dig["unlabeled_count"], 1)
        self.assertEqual(dig["block_count"], 2)
        self.assertEqual(len(dig["items"]), 1)
        self.assertEqual(dig["items"][0]["codes"], ["600519"])

    def test_maturity_gate_strategy_scope_soft(self):
        from core.maturity_gate import evaluate_maturity_gate

        gate = evaluate_maturity_gate(
            sample_status={},
            north_star={
                "paper_risk_strategy": {
                    "status": "unavailable",
                    "reason": "no_strategy_equity",
                },
                "realization": {"status": "unavailable"},
            },
        )
        ids = {i["id"]: i for i in gate["items"]}
        self.assertIn("strategy_scope_readable", ids)
        self.assertTrue(ids["strategy_scope_readable"]["ok"])
        self.assertEqual(ids["strategy_scope_readable"]["severity"], "soft")


if __name__ == "__main__":
    unittest.main()
