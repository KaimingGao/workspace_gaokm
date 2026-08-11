"""模拟验证补强：amount 分离 · 验证宇宙卫生 · 舆情降级 prior。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.bar_fields import bar_amount
from core.sentiment import score_headlines
from core.sentiment_prior import build_sentiment_prior
from core.signal.config import signal_config_overlay
from core.signal.factors.amihud import score_amihud
from core.signal.factors.liquidity import turnover_proxy
from skills.common.history import normalize_bars


class TestAmountNormalize(unittest.TestCase):
    def test_volume_not_from_amount(self):
        bars = normalize_bars(
            [
                {
                    "日期": "2024-01-02",
                    "开盘": 10,
                    "最高": 11,
                    "最低": 9,
                    "收盘": 10.5,
                    "成交量": 1000,
                    "成交额": 1_050_000,
                }
            ]
        )
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["volume"], 1000.0)
        self.assertEqual(bars[0]["amount"], 1_050_000.0)

    def test_amount_not_swallowed_as_volume(self):
        bars = normalize_bars(
            [{"date": "2024-01-02", "close": 10, "成交额": 99999}]
        )
        self.assertEqual(bars[0]["volume"], 0.0)
        self.assertEqual(bars[0]["amount"], 99999.0)

    def test_bar_amount_prefers_explicit(self):
        self.assertEqual(bar_amount({"amount": 100, "volume": 1, "close": 9}), 100.0)
        self.assertEqual(bar_amount({"volume": 2, "close": 5}), 10.0)

    def test_amihud_and_liquidity_use_amount(self):
        bars = []
        px = 10.0
        for i in range(15):
            px *= 1.01
            bars.append(
                {
                    "date": f"2024-01-{i+1:02d}",
                    "close": px,
                    "volume": 1,
                    "amount": 1e8,
                }
            )
        score, meta = score_amihud(bars)
        self.assertTrue(meta.get("ok"))
        self.assertNotEqual(score, 50.0)
        self.assertEqual(turnover_proxy(bars[-1]), 1e8)


class TestValidationHygiene(unittest.TestCase):
    def test_exclude_empty_and_hygiene(self):
        from core import validation_universe as vu

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "validation_universe.json")
            vu.save_validation_universe(
                {
                    "exclude_codes": [],
                    "include_only": ["AAA001", "BBB002"],
                    "min_codes": 1,
                },
                path=path,
            )
            with patch.object(
                vu,
                "empty_fundamentals_report",
                return_value={
                    "ok": True,
                    "empty_codes": ["AAA001"],
                    "empty_count": 1,
                    "total": 2,
                    "real_multi_coverage": 0.5,
                },
            ), patch.object(
                vu,
                "load_validation_universe",
                side_effect=lambda path=None: vu.load_validation_universe(path=path or path),
            ):
                # load via real path
                pass

            # 直接用 path 参数走真实 load/save
            with patch(
                "core.sample_ops.fundamentals_history_coverage",
                return_value={
                    "empty_codes": ["AAA001"],
                    "total": 2,
                    "real_multi_point": 1,
                    "real_multi_coverage": 0.5,
                },
            ):
                out = vu.exclude_empty_fundamentals(write=True, path=path)
            self.assertTrue(out["wrote"])
            self.assertIn("AAA001", out["exclude_codes"])
            loaded = vu.load_validation_universe(path=path)
            self.assertIn("AAA001", loaded["exclude_codes"])

            with patch(
                "core.data_coverage.build_data_coverage",
                return_value={
                    "ok": True,
                    "items": [
                        {"stock_code": "BBB002", "quality_level": "good", "stale": False}
                    ],
                },
            ), patch(
                "core.sentiment.build_sentiment_panel_coverage",
                return_value={
                    "ok": True,
                    "missing_count": 0,
                    "covered": 1,
                    "total": 1,
                },
            ), patch(
                "core.sample_ops.fundamentals_history_coverage",
                return_value={
                    "empty_codes": [],
                    "total": 1,
                    "real_multi_coverage": 1.0,
                },
            ):
                # resolve uses include_only minus exclude
                rep = vu.build_validation_hygiene_report()
                # may use default universe path; call with explicit codes
                rep = vu.build_validation_hygiene_report(codes=["BBB002"])
            self.assertTrue(rep.get("ok"))
            self.assertIn("actions", rep)


class TestSentimentDegradedPrior(unittest.TestCase):
    def test_degraded_skips_gate(self):
        sent = score_headlines(
            [{"title": "公司减持公告"}],
            degraded=True,
            degrade_reason="timeout",
        )
        self.assertTrue(sent.get("degraded"))
        self.assertFalse(sent.get("prior_eligible"))
        with signal_config_overlay(
            {
                "sentiment": {
                    "include_in_score": False,
                    "prior": {"mode": "gate", "scale_buy_pct": 0.6},
                }
            }
        ):
            prior = build_sentiment_prior(sent, stock_code="600000")
        self.assertFalse(prior.get("active"))
        self.assertTrue(prior.get("degraded"))

    def test_fresh_bearish_still_gates(self):
        sent = score_headlines([{"title": "大股东减持"}])
        self.assertEqual(sent.get("label"), "bearish")
        self.assertTrue(sent.get("prior_eligible", True))
        with signal_config_overlay(
            {
                "sentiment": {
                    "include_in_score": False,
                    "prior": {"mode": "gate", "scale_buy_pct": 0.6},
                }
            }
        ):
            prior = build_sentiment_prior(sent, stock_code="600000")
        self.assertTrue(prior.get("active"))

    def test_sentiment_as_of_and_panel(self):
        from core import sentiment as sent_mod

        with tempfile.TemporaryDirectory() as td:
            hist = os.path.join(td, "history")
            os.makedirs(hist, exist_ok=True)
            path = os.path.join(hist, "600000.jsonl")
            rows = [
                {
                    "code": "600000",
                    "fetched_at": "2024-06-02T10:00:00",
                    "ok": True,
                    "titles": [
                        {"title": "回购注销", "time": "2024-06-01", "source": "em"}
                    ],
                    "rule_label": "bullish",
                    "rule_score": 0.2,
                    "n_titles": 1,
                }
            ]
            with open(path, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            with patch.object(sent_mod, "NEWS_HISTORY_DIR", hist):
                asof = sent_mod.sentiment_as_of("600000", "2024-06-01")
                self.assertTrue(asof.get("ok"))
                panel = sent_mod.build_sentiment_panel_coverage(
                    ["600000", "999999"], as_of="2024-06-01"
                )
                self.assertEqual(panel["covered"], 1)
                self.assertEqual(panel["missing_count"], 1)


class TestScheduleValidationPrepare(unittest.TestCase):
    def test_resolve_warmup_prefers_universe(self):
        from core.schedule_jobs import _resolve_warmup_codes

        with patch(
            "core.validation_universe.resolve_validation_codes",
            return_value={"codes": ["111111", "222222"]},
        ):
            out = _resolve_warmup_codes(None, cap=10)
        self.assertEqual(out, ["111111", "222222"])

    def test_validation_prepare_kind(self):
        from core.schedule_jobs import run_schedule

        with patch(
            "core.validation_universe.prepare_validation_universe",
            return_value={"ok": True, "kind": "validation_prepare"},
        ), patch("core.schedule_jobs.job_registry") as reg:
            slot = reg.slot.return_value
            slot.is_running.return_value = False
            slot.start.return_value = "jid"
            slot.get.return_value = {}
            out = run_schedule(
                "validation_prepare",
                write_excludes=False,
                warmup_bars=False,
                warmup_sentiment=False,
            )
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("kind"), "validation_prepare")


if __name__ == "__main__":
    unittest.main()
