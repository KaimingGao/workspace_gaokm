"""市场 prior 人审配置读写测试。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest


class TestMarketPriorConfig(unittest.TestCase):
    def test_save_and_read_market_prior(self):
        from core.signal.market_prior_config import (
            read_market_prior_public,
            save_market_prior,
        )

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "signal_config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"cross_market": {"mode": "off"}}, f)
            os.environ["QUANTLAB_SIGNAL_CONFIG"] = path
            try:
                out = save_market_prior(
                    cross_market_mode="gate",
                    tech_drag_trigger_pct=-2.0,
                    scale_buy_pct=0.4,
                    scale_holds=True,
                    merge_mode="min_scale",
                    note="test",
                )
                self.assertTrue(out.get("success"))
                pub = read_market_prior_public()
                self.assertEqual(pub["cross_market"]["mode"], "gate")
                self.assertAlmostEqual(pub["cross_market"]["tech_drag_trigger_pct"], -2.0)
                self.assertAlmostEqual(pub["cross_market"]["scale_buy_pct"], 0.4)
                self.assertTrue(pub["cross_market"]["scale_holds"])
                self.assertEqual(pub["market_prior_policy"]["merge_mode"], "min_scale")

                out2 = save_market_prior(
                    market_sentiment_mode="gate",
                    market_sentiment_scale_buy_pct=0.45,
                    market_sentiment_scale_holds=False,
                )
                self.assertTrue(out2.get("success"))
                pub2 = read_market_prior_public()
                self.assertEqual(pub2["market_sentiment_prior"]["mode"], "gate")
                self.assertAlmostEqual(pub2["market_sentiment_prior"]["scale_buy_pct"], 0.45)
                self.assertFalse(pub2["market_sentiment_prior"]["scale_holds"])

                out3 = save_market_prior(
                    regulatory_mode="gate",
                    regulatory_scale_buy_pct=0.4,
                    ipo_drain_mode="gate",
                    ipo_drain_scale_buy_pct=0.35,
                    ipo_drain_ratio_high=2.5,
                )
                self.assertTrue(out3.get("success"))
                pub3 = read_market_prior_public()
                self.assertEqual(pub3["regulatory_prior"]["mode"], "gate")
                self.assertAlmostEqual(pub3["regulatory_prior"]["scale_buy_pct"], 0.4)
                self.assertEqual(pub3["ipo_drain_prior"]["mode"], "gate")
                self.assertAlmostEqual(pub3["ipo_drain_prior"]["drain_ratio_high"], 2.5)
            finally:
                os.environ.pop("QUANTLAB_SIGNAL_CONFIG", None)
                import core.signal.config as cfg_mod

                cfg_mod._cached = None


if __name__ == "__main__":
    unittest.main()
