"""历史回测四头读工厂启用版本，不再走套槽 LightGBM。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from core.backtest.replay_scoring import (
    pop_replay_arm_heads,
    push_replay_arm_heads,
    replay_uses_arm_t,
    stamp_replay_arm_day,
)
from core.research.holdout import MODEL_ROLE_LIVE, scoring_model_role_context


class TestReplayArmStamp(unittest.TestCase):
    def test_stamp_replaces_suite_fields_and_ranks(self) -> None:
        items = [
            {
                "stock_code": "600000",
                "y_oo": 9.0,
                "y_oo_source": "lightgbm",
                "formula_terms": {"total": 9.0, "terms": [{"key": "mom", "contrib": 9.0}]},
                "formula_terms_oo_rank": {"total": 1.0, "terms": [{"key": "mom", "contrib": 1.0}]},
                "y_τc": 8.0,
                "y_oo_rank_score": 1.0,
                "y_tc_rank_score": 1.0,
            },
            {
                "stock_code": "600001",
                "y_oo": 7.0,
                "y_τc": 6.0,
            },
        ]
        table = {
            "heads": ["oo", "tc", "oo_rank", "tc_rank"],
            "suite": "research",
            "cards": {
                "oo": {
                    "label": "11-17 02:23 · RankIC 0.105",
                    "run_id": "oo-1",
                    "rank_ic": 0.105,
                },
                "tc": {"label": "11-17 03:42 · RankIC 0.080", "run_id": "tc-1"},
            },
            "by_day": {
                "2026-11-17": {
                    "600000": {
                        "y_oo": 0.2,
                        "y_τc": 0.1,
                        "y_oo_rank_score": 0.4,
                        "y_tc_rank_score": 0.3,
                    },
                    "600001": {
                        "y_oo": 0.5,
                        "y_τc": 0.2,
                        "y_oo_rank_score": 0.9,
                        "y_tc_rank_score": 0.1,
                    },
                }
            },
        }
        stamp_replay_arm_day(items, table, "2026-11-17")
        by_code = {it["stock_code"]: it for it in items}
        self.assertEqual(by_code["600000"]["y_oo"], 0.2)
        self.assertEqual(by_code["600000"]["predicted_score"], 0.2)
        self.assertEqual(by_code["600000"]["y_oo_source"], "transformer")
        self.assertNotIn("formula_terms", by_code["600000"])
        self.assertNotIn("formula_terms_oo_rank", by_code["600000"])
        self.assertEqual(by_code["600000"]["y_oo_rank_source"], "transformer")
        self.assertEqual(by_code["600000"]["arm_t"]["suite"], "research")
        self.assertEqual(
            by_code["600000"]["arm_t"]["oo"]["label"], "11-17 02:23 · RankIC 0.105"
        )
        self.assertEqual(by_code["600000"]["arm_t"]["tc"]["run_id"], "tc-1")
        self.assertEqual(by_code["600000"]["y_tc_rank_source"], "transformer")
        self.assertEqual(by_code["600000"]["y_τc"], 0.1)
        self.assertEqual(by_code["600001"]["y_oo_rank"], 1)
        self.assertEqual(by_code["600000"]["y_oo_rank"], 2)
        self.assertEqual(by_code["600000"]["y_tc_rank"], 1)
        self.assertEqual(by_code["600001"]["y_tc_rank"], 2)
        self.assertEqual(by_code["600000"]["y_oo_rank_score"], 0.4)

    def test_missing_head_clears_suite_score(self) -> None:
        items = [{"stock_code": "600000", "y_oo": 3.0, "y_oo_source": "lightgbm"}]
        stamp_replay_arm_day(items, {"heads": [], "by_day": {}}, "2026-11-17")
        self.assertNotIn("y_oo", items[0])
        self.assertNotIn("y_oo_source", items[0])


class TestReplayArmRole(unittest.TestCase):
    def test_research_and_live_read_different_enabled_ids(self) -> None:
        from core.research.yhat_factory_backtest import enabled_replay_specs

        def listed(head, *, factory=None, suite=None):
            vid = f"{head}-{suite}"
            return {
                "enabled_id": vid,
                "items": [
                    {
                        "id": vid,
                        "weights_ref": f"{vid}.weights",
                        "holdout_trading_days": 0 if suite == "live" else 60,
                    }
                ],
            }

        def preview(head, vid, *, factory=None):
            return {"success": True, "version": {"fit_params": {"seq_len": 16}}}

        with patch(
            "core.research.yhat_factory_versions.list_factory_versions",
            side_effect=listed,
        ), patch(
            "core.research.yhat_factory_versions.preview_factory_version",
            side_effect=preview,
        ), patch(
            "core.research.yhat_factory_weights.version_weights_dir",
            return_value="/tmp/no-weights",
        ), patch(
            "core.research.yhat_factory_backtest._read_json",
            return_value={"backend": "transformer", "factor_names": ["a"]},
        ):
            with scoring_model_role_context("research"):
                research = {s["head"]: s["run_id"] for s in enabled_replay_specs()}
            with scoring_model_role_context(MODEL_ROLE_LIVE):
                live = {s["head"]: s["run_id"] for s in enabled_replay_specs()}
        self.assertEqual(research["oo"], "oo-research")
        self.assertEqual(live["tc"], "tc-live")
        self.assertNotEqual(research["oo_rank"], live["oo_rank"])

    def test_attach_skips_suite_loaders(self) -> None:
        from core.backtest.replay_scoring import _attach_open_yhat_heads

        token = push_replay_arm_heads(["oo"])
        try:
            self.assertTrue(replay_uses_arm_t())
            with patch(
                "core.research.tc_model.load_tau_model",
                side_effect=AssertionError("suite tau"),
            ), patch(
                "core.research.oo_rank_lambdarank.load_oo_rank_model",
                side_effect=AssertionError("suite oo_rank"),
            ), patch(
                "core.research.tc_rank.load_tc_rank_model",
                side_effect=AssertionError("suite tc_rank"),
            ), patch(
                "core.research.oo_model.load_scoring_return_model",
                side_effect=AssertionError("suite oo"),
            ):
                out = _attach_open_yhat_heads(
                    [{"stock_code": "600000"}],
                    quotes={},
                    windows={},
                )
        finally:
            pop_replay_arm_heads(token)
        self.assertFalse(replay_uses_arm_t())
        self.assertEqual(out[0]["stock_code"], "600000")


if __name__ == "__main__":
    unittest.main()
