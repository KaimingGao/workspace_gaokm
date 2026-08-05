"""FS 轨：舆情闸 · ŷ 键同构 · as_of · LLM 墙。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.config import load_signal_config, signal_config_overlay
from core.signal.factor_collinearity import trend_family_collinearity
from core.signal.return_score import ReturnScoreModel
from core.signal.scorer import score_bars
from core.signal.score_stock import score_stock


def _bars(n: int = 40):
    out = []
    px = 10.0
    for i in range(n):
        px *= 1.01
        out.append(
            {
                "date": f"2024-01-{(i % 28) + 1:02d}",
                "open": px,
                "high": px * 1.01,
                "low": px * 0.99,
                "close": px,
                "volume": 1e6,
            }
        )
    return out


class TestFsSentimentGate(unittest.TestCase):
    def test_default_gate_skips_alt_sentiment_in_score_bars(self):
        bars = _bars()
        cfg = load_signal_config(reload=True)
        self.assertFalse((cfg.get("sentiment") or {}).get("include_in_score", False))
        out = score_bars(
            bars,
            quote={"change_raw": 1.0},
            sentiment={"label": "bullish", "score": 0.1},
            config=cfg,
        )
        self.assertNotIn("alt_sentiment", out.get("sub_scores") or {})

    def test_gate_open_computes_alt_sentiment(self):
        bars = _bars()
        with signal_config_overlay({"sentiment": {"include_in_score": True}}):
            out = score_bars(
                bars,
                quote={"change_raw": 1.0},
                sentiment={"label": "bullish", "score": 0.1},
                config=load_signal_config(),
            )
        self.assertIn("alt_sentiment", out.get("sub_scores") or {})
        self.assertGreater(out["sub_scores"]["alt_sentiment"], 50)

    def test_score_stock_gate_pops_alt_from_yhat(self):
        quote = {
            "success": True,
            "stock_code": "600000",
            "stock_name": "测试",
            "price": 10.0,
            "change": 0.0,
        }
        bars = _bars()
        global_m = ReturnScoreModel(
            intercept=0.0,
            coefficients={"momentum": 0.1, "alt_sentiment": 0.5},
            standardized=False,
        )
        scored = {
            "score": 50.0,
            "hard_reject": False,
            "sub_scores": {"momentum": 60.0, "alt_sentiment": 80.0},
            "factors": {},
            "reasons": [],
            "factor_contrib": {},
        }
        with patch(
            "core.signal.score_stock.fetch_daily_bars", return_value=(bars, "akshare")
        ), patch(
            "core.signal.score_stock.allows_production_score", return_value=(True, "")
        ), patch(
            "core.signal.score_stock.score_bars", return_value=scored
        ) as mock_sb, patch(
            "core.sentiment.fetch_stock_headlines",
            return_value={
                "ok": True,
                "sentiment": {"label": "bullish", "score": 0.2},
                "items": [{"title": "利好"}],
            },
        ), patch(
            "core.signal.return_score_store.load_return_model",
            return_value=(global_m, {}),
        ), patch(
            "core.signal.score_stock.load_signal_config",
            return_value={
                "fundamentals": {"enabled": False},
                "sentiment": {"include_in_score": False},
                "cluster_scoring": {"enabled": False, "mode": "off"},
            },
        ):
            out = score_stock(
                "600000",
                quote=quote,
                skip_fundamentals=True,
                bypass_quality_gate=True,
                cluster_mode="off",
            )
        # 闸关：传给 score_bars 的 sentiment 为 None
        kwargs = mock_sb.call_args.kwargs
        self.assertIsNone(kwargs.get("sentiment"))
        item = out.get("signal_item") or {}
        self.assertFalse(item.get("sentiment_include_in_score"))
        self.assertFalse(item.get("alt_sentiment_in_yhat"))


class TestFsFactorPanelYhatKeys(unittest.TestCase):
    def test_required_keys_computed_even_if_weight_zero(self):
        bars = _bars()
        cfg = load_signal_config(reload=True)
        # 强制 momentum 权为 0
        cfg = dict(cfg)
        cfg["weights"] = dict(cfg.get("weights") or {})
        cfg["weights"]["momentum"] = 0.0
        out = score_bars(
            bars,
            quote={"change_raw": 1.0},
            config=cfg,
            required_factor_keys=["momentum"],
        )
        self.assertIn("momentum", out.get("sub_scores") or {})


class TestFsSentimentAsOf(unittest.TestCase):
    def test_history_and_as_of(self):
        from core import sentiment as sent_mod

        with tempfile.TemporaryDirectory() as td:
            with patch.object(sent_mod, "NEWS_HISTORY_DIR", td), signal_config_overlay(
                {"sentiment": {"append_history": True}}
            ):
                path = sent_mod.append_headline_history(
                    "600000",
                    {
                        "ok": True,
                        "updated_at": "2024-06-01T12:00:00",
                        "items": [
                            {"title": "业绩大增利好", "time": "2024-05-30"},
                            {"title": "涉嫌违规处罚", "time": "2024-06-02"},
                        ],
                        "sentiment": {"label": "mixed", "score": 0.5},
                    },
                )
                self.assertTrue(path and os.path.isfile(path))
                asof = sent_mod.sentiment_as_of("600000", "2024-06-01")
                self.assertTrue(asof.get("ok"))
                self.assertGreaterEqual(
                    int((asof.get("sentiment") or {}).get("n_titles") or 0), 1
                )
                self.assertEqual(
                    (asof.get("sentiment") or {}).get("coverage_status"), "ok"
                )
                # 仅用 ≤ as_of 的标题：6-02 处罚标题不应主导 as_of=6-01
                later = sent_mod.sentiment_as_of("600000", "2024-06-03")
                self.assertGreaterEqual(
                    int((later.get("sentiment") or {}).get("n_titles") or 0),
                    int((asof.get("sentiment") or {}).get("n_titles") or 0),
                )


class TestFsCollinearity(unittest.TestCase):
    def test_trend_family_high_corr(self):
        rows = [
            {
                "momentum": 50 + i,
                "ma_slope": 50 + i,
                "technical_pattern": 40,
                "weekly_confirm": 45,
                "idio_momentum": 30,
            }
            for i in range(10)
        ]
        out = trend_family_collinearity(rows)
        self.assertTrue(out.get("success"))
        pairs = out.get("high_corr_pairs") or []
        self.assertTrue(any({p["a"], p["b"]} == {"momentum", "ma_slope"} for p in pairs))


class TestFsLlmWall(unittest.TestCase):
    def test_analysis_not_in_sub_scores(self):
        """契约：LLM analysis 字符串不得成为 sub_scores 键/值来源。"""
        from services.watching_service import WatchingService

        svc = WatchingService()
        with patch(
            "core.sentiment.fetch_stock_headlines",
            return_value={
                "ok": True,
                "items": [{"title": "测试标题"}],
                "stock_name": "测",
                "sentiment": {"label": "neutral"},
            },
        ), patch("agent.llm_client.LLMClient") as MockLlm:
            inst = MockLlm.return_value
            inst.is_available.return_value = True
            inst.chat.return_value = {
                "choices": [{"message": {"content": "## 看多理由\n纯叙事"}}]
            }
            analysis = svc.sentiment_analysis("600000")
        self.assertTrue(analysis.get("ok"))
        text = analysis.get("analysis") or ""
        self.assertIn("叙事", text)
        # score_bars 路径不应接收 analysis
        bars = _bars()
        out = score_bars(
            bars,
            quote={"change_raw": 0.0},
            sentiment={"label": "neutral", "analysis": text},
            config=load_signal_config(reload=True),
        )
        for k, v in (out.get("sub_scores") or {}).items():
            self.assertNotEqual(k, "analysis")
            self.assertIsInstance(v, (int, float))


class TestFsRiskHints(unittest.TestCase):
    def test_bearish_risk_hint(self):
        quote = {
            "success": True,
            "stock_code": "600000",
            "stock_name": "测试",
            "price": 10.0,
            "change": 0.0,
        }
        bars = _bars()
        global_m = ReturnScoreModel(
            intercept=0.0, coefficients={"momentum": 0.1}, standardized=False
        )
        scored = {
            "score": 50.0,
            "hard_reject": False,
            "sub_scores": {"momentum": 55.0},
            "factors": {},
            "reasons": [],
            "factor_contrib": {},
        }
        with patch(
            "core.signal.score_stock.fetch_daily_bars", return_value=(bars, "akshare")
        ), patch(
            "core.signal.score_stock.allows_production_score", return_value=(True, "")
        ), patch(
            "core.signal.score_stock.score_bars", return_value=scored
        ), patch(
            "core.sentiment.fetch_stock_headlines",
            return_value={
                "ok": True,
                "sentiment": {"label": "bearish", "score": 0.85},
                "items": [],
            },
        ), patch(
            "core.signal.return_score_store.load_return_model",
            return_value=(global_m, {}),
        ), patch(
            "core.signal.score_stock.load_signal_config",
            return_value={
                "fundamentals": {"enabled": False},
                "sentiment": {"include_in_score": False},
                "cluster_scoring": {"enabled": False, "mode": "off"},
            },
        ):
            out = score_stock(
                "600000",
                quote=quote,
                skip_fundamentals=True,
                bypass_quality_gate=True,
                cluster_mode="off",
            )
        hints = (out.get("signal_item") or {}).get("risk_hints") or []
        self.assertTrue(any(h.get("kind") == "sentiment_bearish" for h in hints))


if __name__ == "__main__":
    unittest.main()
