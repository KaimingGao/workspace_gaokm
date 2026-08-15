import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.stance import compute_buy_stance, STANCE_LABELS
from skills.advise.handler import AdviseHandler

_YHAT_THRESHOLDS = {"avoid": -0.5, "wait": 0.0, "probe": 0.35}


class TestStance(unittest.TestCase):
    def test_hard_reject_avoid(self):
        s = compute_buy_stance(
            quote={"success": True, "change_raw": -1},
            signal_item={"hard_reject": True, "reject_reason": "过热", "score": 0},
        )
        self.assertEqual(s["stance_code"], "avoid")
        self.assertEqual(s["stance_label"], STANCE_LABELS["avoid"])

    def test_high_score_with_penalty_downgrade(self):
        with patch("core.stance.get_stance_thresholds", return_value=_YHAT_THRESHOLDS):
            s = compute_buy_stance(
                quote={"success": True, "change_raw": -6},
                signal_item={
                    "predicted_score": 1.0,
                    "hard_reject": False,
                    "data_source": "akshare_cn_daily",
                },
                kline={"success": True, "latest_tags": ["大阴", "放量"]},
            )
        self.assertEqual(s["stance_code"], "avoid")

    def test_heuristic_score_only_insufficient(self):
        s = compute_buy_stance(
            quote={"success": True, "change_raw": 1.0},
            signal_item={"score": 72, "hard_reject": False},
        )
        self.assertEqual(s["stance_code"], "insufficient")

    def test_advise_handler_mocked(self):
        facts = {
            "stock_code": "600519",
            "stock_name": "茅台",
            "horizon_days": 3,
            "quote": {
                "success": True,
                "stock_code": "600519",
                "stock_name": "茅台",
                "price": "100",
                "change_raw": 1.0,
            },
            "signal": {"success": True},
            "signal_item": {
                "stock_code": "600519",
                "predicted_score": 1.0,
                "hard_reject": False,
                "data_source": "mock",
                "invalidation": ["跌破参考位"],
            },
            "kline": {
                "success": True,
                "latest_tags": [],
                "data_source": "mock",
                "summary": "震荡",
            },
            "peer": {"success": False},
            "index": {"success": False},
        }

        with patch("core.advise.collect_stock_facts", return_value=facts), patch(
            "core.stance.get_stance_thresholds", return_value=_YHAT_THRESHOLDS
        ):
            raw = AdviseHandler().execute(
                {"name": "advise", "parameters": {"stock_code": "茅台"}}
            )
        data = json.loads(raw)
        self.assertTrue(data["success"])
        self.assertEqual(data["stance_code"], "buy_light")
        self.assertIn("stance_label", data)
        self.assertIn("facts", data)


if __name__ == "__main__":
    unittest.main()
