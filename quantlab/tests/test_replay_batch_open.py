"""09:30 冷启动批量开盘分：整池 z，名单内才写 ŷ。"""

from __future__ import annotations

import inspect
import math
import unittest
from unittest.mock import patch

from core.backtest.replay_batch_open import (
    _BATCH_LGB_EXPLAIN_MAX,
    _RankModel,
    _apply_formula_map,
    _batch_lgb_explain,
    _overlay_open_tau,
    _rank_map,
    _ridge_pack,
    _stamp_oo_rank_formula,
    _stamp_tc_rank_formula,
    clear_open_slice,
    cross_section_ridge,
    merge_formula_tips_onto_legs,
    prepare_replay_features,
    score_open_day_from_slice,
    stamp_trade_formula_tips,
)
from core.backtest.replay_scoring import _score_open_day
from core.research.materialize.consumer import MaterializeRequired


class ReplayBatchOpenTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_open_slice()

    def test_ridge_uses_full_pool_z(self) -> None:
        rows = [{"x": 1.0}, {"x": 2.0}, {"x": 3.0}]
        sd = math.sqrt(2.0 / 3.0)
        got = cross_section_ridge(rows, ["x"], {"x": 1.0}, 0.0)
        self.assertEqual(got, [round(-1.0 / sd, 6), 0.0, round(1.0 / sd, 6)])
        # 少一个同伴，z 变了。分档不能拿来缩小截面。
        shrunk = cross_section_ridge(rows[:2], ["x"], {"x": 1.0}, 0.0)
        self.assertNotEqual(shrunk[0], got[0])

    def test_all_missing_row_follows_head_rule(self) -> None:
        rows = [{"x": 1.0}, {}]
        imputed = cross_section_ridge(rows, ["x"], {"x": 1.0}, 0.5, impute_all_missing=True)
        skipped = cross_section_ridge(rows, ["x"], {"x": 1.0}, 0.5, impute_all_missing=False)
        self.assertIsNotNone(imputed[1])
        self.assertIsNone(skipped[1])

    def test_score_without_slice_is_unset(self) -> None:
        clear_open_slice()
        self.assertIsNone(
            score_open_day_from_slice(
                "2024-01-03",
                dates=["2024-01-02", "2024-01-03"],
                day_i=1,
            )
        )

    def test_open_overlay_rounds_gap_and_writes_sector(self) -> None:
        row = {"z_τc_gap_pct": 0.14245014245015675}
        _overlay_open_tau(
            row,
            quote={"open": 10.01425, "prev_close": 10.0, "date": "2026-10-29"},
            pool={
                "sector_gap_breadth": 0.015,
                "pool_gaps": [0.1425],
                "ref_by_code": {"600000": -0.0429},
            },
            code="600000",
            day="2026-10-29",
            trigger=2.0,
        )
        self.assertEqual(row["gap_pct"], 0.1425)
        self.assertEqual(row["sector_gap_breadth"], 0.015)
        self.assertEqual(row["gap_vs_sector"], 0.1854)
        self.assertEqual(row["theme_day"], 0.0)

    def test_cancel_does_not_prefetch(self) -> None:
        with patch("core.research.lake_replay.prefetch_l1_coverage") as prefetch:
            prepare_replay_features(
                ["600000"],
                ["2026-10-29"],
                clock="09:30",
                cancel_cb=lambda: True,
            )
        prefetch.assert_not_called()
        self.assertIsNone(score_open_day_from_slice("2026-10-29", dates=["2026-10-29"]))

    def test_broken_rank_is_not_silently_empty(self) -> None:
        self.assertEqual(
            _rank_map("2026-10-29", ["a"], [{}], None, None, getter=lambda _r, _k: None),
            {},
        )
        broken = _RankModel({}, [], None, broken=True)
        self.assertIsNone(
            _rank_map("2026-10-29", ["a"], [{}], broken, None, getter=lambda _r, _k: None)
        )

    def test_foreign_slice_is_not_used(self) -> None:
        from core.backtest import replay_batch_open as mod

        mod._HOLDER = mod._OpenSlice(["600000"], ["2026-10-29"], {}, {})
        self.assertIsNone(
            score_open_day_from_slice(
                "2026-10-29",
                dates=["2026-10-28", "2026-10-29"],
                date_maps={"600000": {}},
            )
        )
        self.assertIsNone(
            score_open_day_from_slice(
                "2026-10-29",
                dates=["2026-10-29"],
                date_maps={"000001": {}},
            )
        )

    def test_materialize_required_is_not_swallowed(self) -> None:
        def _boom(*_a: object, **_k: object) -> None:
            raise MaterializeRequired({"error": "need l2"})

        with patch("core.backtest.replay_batch_open.score_open_day_from_slice", _boom):
            with self.assertRaises(MaterializeRequired):
                _score_open_day(
                    stock_bars={},
                    dates=["2024-01-02", "2024-01-03"],
                    date_maps={},
                    day_i=1,
                    max_window=30,
                    horizon_days=1,
                    cfg=None,
                )

    def test_ridge_pack_beta_z_matches_score(self) -> None:
        model = {
            "zscore_scope": "cross_section",
            "intercept": 0.2,
            "coefficients": {"x": 1.0},
            "model_role": "research",
        }
        rows = [{"x": 1.0}, {"x": 3.0}]
        scores, expls = _ridge_pack(
            ["A", "B"],
            rows,
            model,
            {"A"},
            impute_all_missing=False,
            getter=lambda row, name: row.get(name),
            head="eod",
        )
        self.assertEqual(set(scores), {"A"})
        expl = expls["A"]
        self.assertEqual(expl.get("head"), "eod")
        self.assertEqual(expl.get("score_model_role"), "research")
        self.assertAlmostEqual(float(expl["total"]), float(scores["A"]))
        term = (expl.get("terms") or [])[0]
        self.assertEqual(term.get("key"), "x")
        self.assertAlmostEqual(term["contrib"], term["beta"] * term["z"], places=4)
        items = [{"stock_code": "A"}, {"stock_code": "B"}]
        _apply_formula_map(items, expls, ("formula_terms", "score_formula_terms"))
        self.assertIs(items[0]["formula_terms"], items[0]["score_formula_terms"])
        self.assertNotIn("formula_terms", items[1])

        # τ 头拒旧 Ridge β（须走 LightGBM）；eod Ridge tip 已在上方覆盖。
        tau_scores, tau_expls = _ridge_pack(
            ["A", "B"],
            rows,
            model,
            None,
            impute_all_missing=True,
            getter=lambda row, name: row.get(name),
            head="tau",
        )
        self.assertIsNone(tau_scores)
        self.assertEqual(tau_expls, {})

    def test_oo_rank_formula_stamps_ridge_head(self) -> None:
        items = [{"stock_code": "A"}]
        model = {
            "success": True,
            "backend": "ridge",
            "solver": "ridge",
            "target": "oo_rank",
            "horizon_mode": "oo_rank",
            "intercept": 0.0,
            "coefficients": {"z_oo_momentum": 1.0},
            "feature_zscore": True,
            "zscore_scope": "cross_section",
            "zscore_means": {},
            "zscore_stds": {},
        }
        rank = _RankModel(model, ["z_oo_momentum"], None)
        _stamp_oo_rank_formula(
            items,
            ["A"],
            [{"z_oo_momentum": 0.5}],
            [0.5],
            rank,
            {"A": 0.5},
        )
        expl = items[0].get("formula_terms_oo_rank") or {}
        self.assertEqual(expl.get("head"), "oo_rank")
        self.assertEqual(expl.get("backend"), "ridge")
        self.assertTrue(expl.get("terms"))

    def test_tc_rank_formula_is_ridge_beta_z_for_scored_codes(self) -> None:
        items = [{"stock_code": "A"}, {"stock_code": "B"}]
        model = {
            "success": True,
            "backend": "ridge",
            "solver": "ridge",
            "target": "tc_rank",
            "horizon_mode": "tc_rank",
            "intercept": 0.1,
            "coefficients": {"z_τc_gap_pct": 1.0},
            "feature_zscore": True,
            "zscore_scope": "cross_section",
            "zscore_means": {},
            "zscore_stds": {},
        }
        rank = _RankModel(model, ["z_τc_gap_pct"], None)
        _stamp_tc_rank_formula(
            items,
            ["A", "B"],
            [{"z_τc_gap_pct": -1.0}, {"z_τc_gap_pct": 2.0}],
            [-0.9, 2.1],
            rank,
            {"A": -0.9},
        )
        expl = items[0].get("formula_terms_tc_rank") or {}
        self.assertEqual(expl.get("head"), "tc_rank")
        self.assertEqual(expl.get("backend"), "ridge")
        terms = expl.get("terms") or []
        self.assertTrue(any(t.get("key") == "z_τc_gap_pct" for t in terms))
        self.assertNotIn("formula_terms_tc_rank", items[1])
        self.assertIs(items[0].get("score_formula_terms_tc_rank"), expl)

    def test_tc_rank_formula_uses_lambdarank_pred_contrib(self) -> None:
        items = [{"stock_code": "A"}]
        model = {
            "backend": "lambdarank",
            "solver": "lambdarank",
            "booster_b64": "x",
        }
        rank = _RankModel(model, ["z_τc_gap_atr"], object(), broken=False)
        fake = {
            "intercept": -0.2,
            "terms": [{"key": "z_τc_gap_atr", "contrib": 0.12, "z": -1.0}],
            "total": 0.5,
            "head": "oo_rank",
            "backend": "lambdarank",
            "model_role": "tree",
        }
        with patch(
            "core.research.oo_rank_lambdarank._explain_oo_rank_lambdarank_rows",
            return_value=[fake],
        ) as explain:
            _stamp_tc_rank_formula(
                items,
                ["A"],
                [{"z_τc_gap_atr": -1.0}],
                [0.5],
                rank,
                {"A": 0.5},
            )
        explain.assert_called_once()
        expl = items[0].get("formula_terms_tc_rank") or {}
        self.assertEqual(expl.get("head"), "tc_rank")
        self.assertEqual(expl.get("model_role"), "tree")
        self.assertEqual(expl.get("backend"), "lambdarank")
        self.assertEqual((expl.get("terms") or [])[0].get("contrib"), 0.12)

    def test_replay_releases_slice_and_keeps_signature(self) -> None:
        from core.backtest import replay_batch_open as mod
        from core.backtest.paper_replay import backtest_paper_replay

        names = inspect.signature(backtest_paper_replay).parameters
        self.assertIn("stock_bars", names)
        self.assertIn("tradable_codes", names)
        mod._HOLDER = mod._OpenSlice(["600000"], ["2026-10-29"], {}, {})
        out = backtest_paper_replay({})
        self.assertFalse(out.get("success"))
        self.assertIsNone(mod._HOLDER)

    def test_batch_lgb_explain_gate(self) -> None:
        self.assertFalse(_batch_lgb_explain(None))
        self.assertTrue(_batch_lgb_explain({"A"}))
        big = {str(i) for i in range(_BATCH_LGB_EXPLAIN_MAX + 1)}
        self.assertFalse(_batch_lgb_explain(big))

    def test_tc_lgb_suite_doc_scores_without_full_pool_tip(self) -> None:
        """套件 JSON（return_model 嵌套）须走 LGB 批量头；大名单不写 tip。"""
        blob = {
            "feature_names": ["x"],
            "booster": {"payload_b64": "dGVzdA=="},
            "zscore_scope": "cross_section",
            "schema": "tau_lgb_v1",
            "coefficients": {},
        }
        suite = {
            "success": True,
            "schema": "tau_lgb_v1",
            "return_model": blob,
            "model_role": "research",
        }
        rows = [{"x": 1.0}, {"x": 3.0}]
        allow = {str(i) for i in range(_BATCH_LGB_EXPLAIN_MAX + 5)}
        allow |= {"A", "B"}
        with patch(
            "core.research.horizon_tree.load_lightgbm_booster", return_value=object()
        ), patch(
            "core.research.horizon_lgb._predict_lightgbm",
            return_value=[0.11, 0.22],
        ), patch(
            "core.research.horizon_tree.explain_tree_return",
            side_effect=AssertionError("large allow must skip tip"),
        ):
            scores, expls = _ridge_pack(
                ["A", "B"],
                rows,
                suite,
                allow,
                impute_all_missing=True,
                getter=lambda row, name: row.get(name),
                head="tau",
            )
        self.assertEqual(scores.get("A"), 0.11)
        self.assertEqual(scores.get("B"), 0.22)
        self.assertEqual(expls, {})

    def test_tc_lgb_small_allow_writes_tip(self) -> None:
        blob = {
            "feature_names": ["x"],
            "booster": {"payload_b64": "dGVzdA=="},
            "zscore_scope": "cross_section",
            "schema": "tau_lgb_v1",
        }
        suite = {"return_model": blob, "zscore_scope": "cross_section"}
        tip = {
            "intercept": 0.0,
            "terms": [{"key": "x", "contrib": 0.05}],
            "total": 0.2,
            "backend": "lightgbm",
        }
        with patch(
            "core.research.horizon_tree.load_lightgbm_booster", return_value=object()
        ), patch(
            "core.research.horizon_lgb._predict_lightgbm",
            return_value=[0.2],
        ), patch(
            "core.research.horizon_tree.explain_tree_return",
            return_value=tip,
        ):
            scores, expls = _ridge_pack(
                ["A"],
                [{"x": 1.0}],
                suite,
                {"A"},
                impute_all_missing=True,
                getter=lambda row, name: row.get(name),
                head="tau",
            )
        self.assertEqual(scores, {"A": 0.2})
        self.assertIn("A", expls)
        self.assertEqual(expls["A"].get("backend"), "lightgbm")

    def test_is_tc_lightgbm_scorer_unwraps_suite(self) -> None:
        from core.research.tc_lgb import is_tc_lightgbm_scorer

        blob = {
            "feature_names": ["x"],
            "booster": {"payload_b64": "dGVzdA=="},
        }
        self.assertTrue(is_tc_lightgbm_scorer({"return_model": blob}))
        self.assertFalse(is_tc_lightgbm_scorer({"return_model": {"coefficients": {"x": 1.0}}}))

    def test_large_rank_scores_skip_formula_tip(self) -> None:
        items = [{"stock_code": str(i)} for i in range(_BATCH_LGB_EXPLAIN_MAX + 2)]
        codes = [str(i) for i in range(_BATCH_LGB_EXPLAIN_MAX + 2)]
        z_rows = [{"z_oo_momentum": 0.1}] * len(codes)
        hats = [0.1] * len(codes)
        scores = {c: 0.1 for c in codes}
        model = {
            "backend": "lambdarank",
            "solver": "lambdarank",
            "booster_b64": "x",
        }
        rank = _RankModel(model, ["z_oo_momentum"], object())
        with patch(
            "core.research.oo_rank_lambdarank._explain_oo_rank_lambdarank_rows",
            side_effect=AssertionError("must skip full-pool rank tip"),
        ):
            _stamp_oo_rank_formula(items, codes, z_rows, hats, rank, scores)
        self.assertNotIn("formula_terms_oo_rank", items[0])

    def test_large_allow_explain_codes_writes_tip(self) -> None:
        """全池/大名单批量跳过 tip；explain_codes 懒补成交票。"""
        blob = {
            "feature_names": ["x"],
            "booster": {"payload_b64": "dGVzdA=="},
            "zscore_scope": "cross_section",
            "schema": "tau_lgb_v1",
        }
        suite = {"return_model": blob, "zscore_scope": "cross_section"}
        tip = {
            "intercept": 0.0,
            "terms": [{"key": "x", "contrib": 0.05}],
            "total": 0.2,
            "backend": "lightgbm",
        }
        allow = {str(i) for i in range(_BATCH_LGB_EXPLAIN_MAX + 5)}
        allow |= {"A", "B"}
        with patch(
            "core.research.horizon_tree.load_lightgbm_booster", return_value=object()
        ), patch(
            "core.research.horizon_lgb._predict_lightgbm",
            return_value=[0.11, 0.22],
        ), patch(
            "core.research.horizon_tree.explain_tree_return",
            return_value=tip,
        ):
            scores, expls = _ridge_pack(
                ["A", "B"],
                [{"x": 1.0}, {"x": 3.0}],
                suite,
                allow,
                impute_all_missing=True,
                getter=lambda row, name: row.get(name),
                head="tau",
                explain_codes={"A"},
            )
        self.assertEqual(scores.get("A"), 0.11)
        self.assertEqual(scores.get("B"), 0.22)
        self.assertIn("A", expls)
        self.assertNotIn("B", expls)
        self.assertEqual(expls["A"].get("backend"), "lightgbm")

    def test_rank_explain_codes_writes_tip(self) -> None:
        items = [{"stock_code": str(i)} for i in range(_BATCH_LGB_EXPLAIN_MAX + 2)]
        codes = [str(i) for i in range(_BATCH_LGB_EXPLAIN_MAX + 2)]
        z_rows = [{"z_oo_momentum": 0.1}] * len(codes)
        hats = [0.1] * len(codes)
        scores = {c: 0.1 for c in codes}
        model = {
            "backend": "lambdarank",
            "solver": "lambdarank",
            "booster_b64": "x",
        }
        rank = _RankModel(model, ["z_oo_momentum"], object())
        tip = {
            "terms": [{"key": "z_oo_momentum", "contrib": 0.1}],
            "total": 0.1,
            "backend": "lambdarank",
        }

        def _expl(rows, model_doc, hats=None):
            return [tip for _ in rows]

        with patch(
            "core.research.oo_rank_lambdarank._explain_oo_rank_lambdarank_rows",
            side_effect=_expl,
        ):
            _stamp_oo_rank_formula(
                items, codes, z_rows, hats, rank, scores, explain_codes={"0", "1"}
            )
        self.assertIn("formula_terms_oo_rank", items[0])
        self.assertIn("formula_terms_oo_rank", items[1])
        self.assertNotIn("formula_terms_oo_rank", items[2])

    def test_merge_formula_tips_onto_legs(self) -> None:
        scored = [
            {
                "stock_code": "A",
                "formula_terms": {
                    "terms": [{"key": "momentum", "contrib": 0.3}],
                    "total": 0.3,
                },
                "score_formula_terms": {
                    "terms": [{"key": "momentum", "contrib": 0.3}],
                    "total": 0.3,
                },
            }
        ]
        legs = [{"stock_code": "A", "action": "open"}]
        n = merge_formula_tips_onto_legs(legs, scored)
        self.assertEqual(n, 1)
        self.assertEqual(legs[0]["formula_terms"]["total"], 0.3)

    def test_stamp_trade_formula_tips_noop_without_holder(self) -> None:
        clear_open_slice()
        items = [{"stock_code": "A", "y_oo": 0.1}]
        self.assertEqual(stamp_trade_formula_tips("2026-10-29", {"A"}, items), 0)


if __name__ == "__main__":
    unittest.main()
