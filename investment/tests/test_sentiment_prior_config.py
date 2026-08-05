"""舆情先验配置人审写盘。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestSentimentPriorConfigSave(unittest.TestCase):
    def test_save_mode_forces_include_false(self):
        from core.signal.sentiment_prior_config import (
            read_sentiment_prior_public,
            save_sentiment_prior,
        )

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "signal_config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "sentiment": {
                            "include_in_score": True,
                            "prior": {"mode": "off"},
                        }
                    },
                    f,
                )
            old = os.environ.get("INVESTMENT_SIGNAL_CONFIG")
            os.environ["INVESTMENT_SIGNAL_CONFIG"] = path
            try:
                out = save_sentiment_prior(mode="gate", block_new_buys=True, note="test")
                self.assertTrue(out.get("success"))
                self.assertFalse(out.get("include_in_score"))
                self.assertEqual(out["sentiment_prior"]["mode"], "gate")
                self.assertTrue(out["sentiment_prior"]["block_new_buys"])
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
                self.assertFalse(raw["sentiment"]["include_in_score"])
                self.assertEqual(raw["sentiment"]["role"], "prior")
                self.assertEqual(raw["sentiment"]["prior"]["mode"], "gate")
                pub = read_sentiment_prior_public()
                self.assertEqual(pub["mode"], "gate")
            finally:
                if old is None:
                    os.environ.pop("INVESTMENT_SIGNAL_CONFIG", None)
                else:
                    os.environ["INVESTMENT_SIGNAL_CONFIG"] = old
                import core.signal.config as cfg_mod

                cfg_mod._cached = None


if __name__ == "__main__":
    unittest.main()
