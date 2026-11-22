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

    def test_stamp_uses_the_arm_on_each_head(self) -> None:
        items = [{"stock_code": "600000", "y_oo": 1.0, "y_τc": 1.0}]
        table = {
            "heads": ["oo", "tc"],
            "cards": {
                "oo": {"factory": "R", "run_id": "oo-r"},
                "tc": {"factory": "L", "run_id": "tc-l"},
            },
            "by_day": {
                "2026-11-17": {
                    "600000": {"y_oo": 0.2, "y_τc": 0.1},
                }
            },
        }
        stamp_replay_arm_day(items, table, "2026-11-17")
        self.assertEqual(items[0]["y_oo_source"], "arm_r")
        self.assertEqual(items[0]["y_τc_source"], "arm_l")
        self.assertEqual(items[0]["_score_source"], "arm_l_open")
        self.assertEqual(items[0]["arm_t"]["oo"]["factory"], "R")

    def test_missing_head_clears_suite_score(self) -> None:
        items = [{"stock_code": "600000", "y_oo": 3.0, "y_oo_source": "lightgbm"}]
        stamp_replay_arm_day(items, {"heads": [], "by_day": {}}, "2026-11-17")
        self.assertNotIn("y_oo", items[0])
        self.assertNotIn("y_oo_source", items[0])


class TestHubArms(unittest.TestCase):
    def test_missing_file_defaults_to_arm_t(self) -> None:
        from core.research.yhat_hub_arms import read_hub_arms

        with patch(
            "core.research.yhat_hub_arms.arms_path",
            return_value="/tmp/quantlab-missing-hub-arms.json",
        ):
            arms = read_hub_arms()
        self.assertEqual(arms, {"oo": "T", "tc": "T", "oo_rank": "T", "tc_rank": "T"})

    def test_write_keeps_unspecified_heads(self) -> None:
        import os
        import tempfile

        from core.research.yhat_hub_arms import read_hub_arms, write_hub_arms

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "arms.json")
            with patch("core.research.yhat_hub_arms.arms_path", return_value=path):
                saved = write_hub_arms({"oo": "R", "tc_rank": "lightgbm"})
                again = read_hub_arms()
        self.assertEqual(saved["oo"], "R")
        self.assertEqual(saved["tc"], "T")
        self.assertEqual(saved["tc_rank"], "L")
        self.assertEqual(again, saved)


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

    def test_each_head_reads_its_chosen_arm(self) -> None:
        from core.research.yhat_factory_backtest import enabled_replay_specs

        seen = []

        def listed(head, *, factory=None, suite=None):
            seen.append((head, factory, suite))
            vid = f"{head}-{factory}"
            return {
                "enabled_id": vid,
                "items": [{"id": vid, "weights_ref": f"{vid}.weights"}],
            }

        def preview(head, vid, *, factory=None):
            return {"success": True, "version": {"fit_params": {}}}

        arms = {"oo": "R", "tc": "L", "oo_rank": "T", "tc_rank": "R"}
        with patch(
            "core.research.yhat_hub_arms.read_hub_arms",
            return_value=arms,
        ), patch(
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
            return_value={"backend": "ridge", "factor_names": ["a"]},
        ):
            specs = {s["head"]: s for s in enabled_replay_specs()}
        self.assertEqual(specs["oo"]["factory"], "R")
        self.assertEqual(specs["tc"]["factory"], "L")
        self.assertEqual(specs["oo_rank"]["factory"], "T")
        self.assertEqual([factory for _head, factory, _suite in seen], ["R", "L", "T", "R"])

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

    def test_replay_score_drops_unfinished_session_day(self) -> None:
        from core.research.yhat_factory_backtest import _bars_for_replay_score

        bars = {
            "000001": [{"date": "2026-11-19"}, {"date": "2026-11-20"}],
            "600519": [{"date": "2026-11-19"}, {"date": "2026-11-20"}],
        }
        with patch(
            "core.research.materialize.consumer.consumer_read_only",
            return_value=True,
        ), patch(
            "core.research.materialize.coverage.complete_daily_as_of",
            return_value="2026-11-19",
        ):
            clipped = _bars_for_replay_score(bars)
        self.assertEqual([row["date"] for row in clipped["000001"]], ["2026-11-19"])
        with patch(
            "core.research.materialize.consumer.consumer_read_only",
            return_value=False,
        ):
            self.assertEqual(_bars_for_replay_score(bars), bars)


class TestReplayRankBlend(unittest.TestCase):
    def _items(self):
        return [
            {
                "stock_code": "A",
                "y_oo": 2.0,
                "y_τc": 2.0,
                "y_oo_rank_score": 100.0,
                "y_tc_rank_score": 0.0,
            },
            {
                "stock_code": "B",
                "y_oo": 2.0,
                "y_τc": 2.0,
                "y_oo_rank_score": 10.0,
                "y_tc_rank_score": 9.0,
            },
            {
                "stock_code": "C",
                "y_oo": 2.0,
                "y_τc": 2.0,
                "y_oo_rank_score": 0.0,
                "y_tc_rank_score": 8.0,
            },
        ]

    def _table(self, oo_backend, tc_backend):
        return {
            "heads": ["oo", "tc", "oo_rank", "tc_rank"],
            "cards": {
                "oo_rank": {"backend": oo_backend},
                "tc_rank": {"backend": tc_backend},
            },
        }

    def _plan_order(self, items):
        from core.paper.rebalance.rank_lots import plan_rank_lot_day

        out = plan_rank_lot_day(
            scored=items,
            holdings=[],
            cash=1_000_000,
            prices={"A": 10.0, "B": 10.0, "C": 10.0},
            cfg={
                "fuse_enter": 0.01,
                "fuse_strong": 0.02,
                "cash_floor": 0.0,
                "top_k": 5,
                "oo_rank_max": None,
                "t0_sell_blocks": {},
            },
        )
        return [row["stock_code"] for row in out["buys"]], out

    def test_same_backend_keeps_raw_buy_order(self) -> None:
        from core.backtest.replay_scoring import (
            apply_replay_day_rank_blend,
            replay_rank_blend,
        )

        items = self._items()
        table = self._table("lightgbm", "lightgbm")
        self.assertEqual(replay_rank_blend(table), "raw")
        self.assertIsNone(apply_replay_day_rank_blend(items, table))
        order, _plan = self._plan_order(items)
        self.assertEqual(order, ["A", "B", "C"])
        self.assertEqual(items[0]["y_oo_rank_score"], 100.0)

    def test_different_backends_buy_by_day_percentile_then_restore_raw(self) -> None:
        from core.backtest.replay_scoring import (
            apply_replay_day_rank_blend,
            replay_rank_blend,
            restore_rank_scores,
        )

        items = self._items()
        table = self._table("lightgbm", "ridge")
        self.assertEqual(replay_rank_blend(table), "day_rank")
        snap = apply_replay_day_rank_blend(items, table)
        self.assertIsNotNone(snap)
        self.assertEqual(items[0]["y_oo_rank"], 1)
        self.assertEqual(items[1]["y_oo_rank"], 2)
        self.assertAlmostEqual(items[0]["y_oo_rank_score"], 1.0)
        self.assertAlmostEqual(items[1]["y_tc_rank_score"], 1.0)
        order, plan = self._plan_order(items)
        self.assertEqual(order, ["B", "A", "C"])
        restore_rank_scores(items, snap)
        restore_rank_scores(plan["buys"], snap)
        self.assertEqual(items[0]["y_oo_rank_score"], 100.0)
        self.assertEqual(items[1]["y_tc_rank_score"], 9.0)
        self.assertEqual(items[0]["y_oo_rank"], 1)
        by = {row["stock_code"]: row for row in plan["buys"]}
        self.assertEqual(by["A"]["y_oo_rank_score"], 100.0)
        self.assertEqual(by["B"]["y_tc_rank_score"], 9.0)

    def test_missing_rank_head_stays_raw(self) -> None:
        from core.backtest.replay_scoring import replay_rank_blend

        table = {
            "heads": ["oo_rank"],
            "cards": {"oo_rank": {"backend": "lightgbm"}},
        }
        self.assertEqual(replay_rank_blend(table), "raw")
        self.assertEqual(replay_rank_blend(None), "raw")


if __name__ == "__main__":
    unittest.main()
