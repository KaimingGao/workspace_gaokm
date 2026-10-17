"""defer_cs_predict：只落特征、不写截面 ŷ。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _bars(n: int = 80):
    px = 10.0
    out = []
    for i in range(n):
        o = px
        c = o * (1.0 + ((i % 5) - 2) * 0.002)
        out.append(
            {
                "date": f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                "open": round(o, 4),
                "high": round(max(o, c) * 1.01, 4),
                "low": round(min(o, c) * 0.99, 4),
                "close": round(c, 4),
                "volume": 1_000_000 + i * 1000,
            }
        )
        px = c
    return out


class TestDeferCsPredict(unittest.TestCase):
    def test_defer_skips_yhat_keeps_features(self) -> None:
        bars = _bars(90)
        quote = {
            "success": True,
            "stock_code": "600000",
            "stock_name": "测试",
            "price": bars[-1]["close"],
            "open": bars[-1]["open"],
            "prev_close": bars[-2]["close"],
            "change": 0.5,
        }

        class _CSModel:
            feature_zscore = True
            zscore_scope = "cross_section"
            intercept = 0.0
            coefficients = {"momentum": 1.0}

            def predict(self, feats):
                raise AssertionError("defer 路径不应单票 predict")

            def explain_prediction(self, feats):
                return {"intercept": 0.0, "total": 0.0, "terms": []}

        with mock.patch(
            "core.signal.score_stock.fetch_daily_bars",
            return_value=(bars, "test"),
        ), mock.patch(
            "core.signal.score_stock.score_bars",
            return_value={
                "success": True,
                "score": 50.0,
                "sub_scores": {"momentum": 12.0, "gap_pct": 1.0},
                "factors": {},
                "warnings": [],
            },
        ), mock.patch(
            "core.research.oo_ridge.load_scoring_return_model",
            return_value=(_CSModel(), {}),
        ), mock.patch(
            "core.research.tc_ridge.load_tau_model",
            return_value={"return_model": {"feature_names": ["gap_pct"], "zscore_scope": "cross_section"}},
        ), mock.patch(
            "core.research.tc_ridge.predict_tau_from_features",
            side_effect=AssertionError("defer 不应 predict_tau"),
        ), mock.patch(
            "core.research.co_ridge.predict_co_from_features",
            side_effect=AssertionError("defer 不应 predict_co"),
        ):
            from core.signal.score_stock import score_stock

            out = score_stock(
                "600000",
                quote=quote,
                skip_fundamentals=True,
                skip_sentiment=True,
                offline_only=True,
                defer_cs_predict=True,
                use_minute_tau=False,
                bypass_quality_gate=True,
            )
        self.assertTrue(out.get("success"))
        item = out.get("signal_item") or out
        self.assertTrue(item.get("_defer_cs_predict"))
        self.assertIsNone(item.get("predicted_score"))
        self.assertEqual(item.get("return_model_source"), "deferred_cs")
        self.assertTrue(isinstance(item.get("sub_scores"), dict) and item["sub_scores"])
        self.assertTrue(isinstance(item.get("features_tau"), dict))


if __name__ == "__main__":
    unittest.main()
