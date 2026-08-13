"""Y 轨：ŷ 生产硬化（门槛写盘 · 滞回卖出 · 闸门 · 验证包）。"""

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


class TestY0ScoringFloors(unittest.TestCase):
    def test_save_scoring_floors_only_scoring(self):
        from core.signal.scoring_floors import save_scoring_floors

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "signal_config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "weights": {"momentum": 0.5},
                        "scoring": {
                            "rank_mode": "predicted_score",
                            "min_predicted_score": 1.0,
                            "min_hold_predicted_score": -1.0,
                        },
                    },
                    f,
                )
            with patch.dict(os.environ, {"INVESTMENT_SIGNAL_CONFIG": path}):
                import core.signal.config as cfg_mod

                cfg_mod._cached = None
                out = save_scoring_floors(
                    min_predicted_score=0.5,
                    min_hold_predicted_score=-0.5,
                    note="test",
                )
                self.assertTrue(out["success"])
                self.assertFalse(out["signal_config_weights_touched"])
                with open(path, encoding="utf-8") as rf:
                    raw = json.loads(rf.read())
                self.assertEqual(raw["weights"]["momentum"], 0.5)
                self.assertEqual(raw["scoring"]["min_predicted_score"], 0.5)
                self.assertEqual(raw["scoring"]["min_hold_predicted_score"], -0.5)


class TestStanceSave(unittest.TestCase):
    def test_save_stance_only_thresholds(self):
        from core.signal.stance_save import save_stance_thresholds

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "signal_config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "weights": {"momentum": 0.5},
                        "scoring": {
                            "rank_mode": "predicted_score",
                            "min_predicted_score": 1.0,
                        },
                        "stance_thresholds": {
                            "avoid": -0.5,
                            "wait": 0.0,
                            "probe": 0.35,
                        },
                    },
                    f,
                )
            with patch.dict(os.environ, {"INVESTMENT_SIGNAL_CONFIG": path}):
                import core.signal.config as cfg_mod

                cfg_mod._cached = None
                out = save_stance_thresholds(
                    avoid=-0.5, wait=0.5, probe=0.85, note="test"
                )
                self.assertTrue(out["success"])
                self.assertFalse(out["signal_config_weights_touched"])
                self.assertFalse(out.get("scoring_touched"))
                with open(path, encoding="utf-8") as rf:
                    raw = json.loads(rf.read())
                self.assertEqual(raw["weights"]["momentum"], 0.5)
                self.assertEqual(raw["scoring"]["min_predicted_score"], 1.0)
                self.assertEqual(raw["stance_thresholds"]["wait"], 0.5)
                self.assertEqual(raw["stance_thresholds"]["probe"], 0.85)

    def test_save_stance_rejects_unordered(self):
        from core.signal.stance_save import save_stance_thresholds

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "signal_config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "stance_thresholds": {
                            "avoid": -0.5,
                            "wait": 0.0,
                            "probe": 0.35,
                        }
                    },
                    f,
                )
            with patch.dict(os.environ, {"INVESTMENT_SIGNAL_CONFIG": path}):
                out = save_stance_thresholds(avoid=1.0, wait=0.0, probe=0.5)
                self.assertFalse(out["success"])
                self.assertIn("avoid < wait < probe", out.get("error") or "")


class TestY2BuyBlock(unittest.TestCase):
    def test_limit_up_skip_reason(self):
        from core.paper_rebalance import _buy_match_block_reason

        reason = _buy_match_block_reason(
            "600519",
            {"prev_close": 100.0, "price_raw": 110.0, "change_raw": 10.0},
        )
        self.assertIsNotNone(reason)
        self.assertIn("涨停", reason or "")


class TestY4MaturityAndPack(unittest.TestCase):
    def test_maturity_has_y_track_items(self):
        from core.maturity_gate import evaluate_maturity_gate

        out = evaluate_maturity_gate()
        ids = {i["id"] for i in out.get("items") or []}
        self.assertIn("yhat_hysteresis_configured", ids)
        self.assertIn("yhat_live_health", ids)
        self.assertIn("paper_daily_streak", ids)
        self.assertEqual(out.get("track"), "Y0-Y5")

    def test_validation_pack_has_rank_mode_and_cluster(self):
        from core.validation_pack import build_validation_pack

        out = build_validation_pack(
            signal_config={
                "scoring": {
                    "rank_mode": "predicted_score",
                    "min_predicted_score": 1.0,
                    "min_hold_predicted_score": -1.0,
                },
                "weights": {},
            }
        )
        self.assertTrue(out.get("ok"))
        pack = out["pack"]
        self.assertEqual(pack.get("rank_mode"), "predicted_score")
        self.assertIn("scoring_floors", pack)
        self.assertGreaterEqual(int(pack.get("version") or 0), 3)


class TestY3StanceThresholds(unittest.TestCase):
    def test_yhat_thresholds_not_0_100_bands(self):
        from core.signal.config import get_stance_thresholds, get_stance_thresholds_with_meta

        cfg = {
            "scoring": {"rank_mode": "predicted_score"},
            "stance_thresholds": {"avoid": -0.5, "wait": 0.0, "probe": 0.35},
        }
        th = get_stance_thresholds(cfg, kind="predicted")
        self.assertLess(th["avoid"], 10.0)
        self.assertLess(th["wait"], 10.0)
        self.assertLess(th["probe"], 10.0)
        self.assertLess(th["avoid"], th["wait"])

    def test_legacy_stance_table_returns_yhat_defaults(self):
        from core.signal.config import get_stance_thresholds_with_meta

        cfg = {
            "stance_thresholds": {"avoid": 45, "wait": 55, "probe": 68},
            "stance_thresholds_heuristic": {"avoid": 45, "wait": 55, "probe": 68},
        }
        out = get_stance_thresholds_with_meta(cfg, kind="predicted")
        th = out["thresholds"]
        self.assertTrue(out.get("legacy_detected"))
        self.assertLess(th["avoid"], 10.0)
        self.assertNotEqual(th["avoid"], 45.0)
        self.assertNotEqual(th["wait"], 55.0)


class TestY1OosFailRate(unittest.TestCase):
    def test_max_oos_fail_rate_in_cfg(self):
        from core.signal.cluster_live import get_cluster_scoring_cfg

        cs = get_cluster_scoring_cfg(
            {
                "cluster_scoring": {
                    "enabled": True,
                    "mode": "shadow",
                    "max_oos_fail_rate": 0.5,
                }
            }
        )
        self.assertEqual(cs["max_oos_fail_rate"], 0.5)
        self.assertIn("min_yhat_rolling_ic", cs)
        self.assertIn("min_sector_map_coverage", cs)


if __name__ == "__main__":
    unittest.main()
