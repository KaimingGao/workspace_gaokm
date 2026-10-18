"""研究 / 执行两套模型：holdout 切分与加载角色。"""

from __future__ import annotations

import os
import tempfile
import unittest

from core.research.holdout import (
    MODEL_ROLE_LIVE,
    MODEL_ROLE_RESEARCH,
    current_scoring_model_role,
    model_fit_id,
    normalize_backtest_model_role,
    research_model_path,
    research_sidecar_flags,
    scoring_model_role_context,
    select_persist_return_model,
    split_by_holdout_days,
    stamp_fitted_at,
)


class TestHoldoutSplit(unittest.TestCase):
    def test_split_by_holdout_days_keeps_train_before_eval(self):
        dates = [f"2026-01-{d:02d}" for d in range(1, 21)]
        train_idx, test_idx, meta = split_by_holdout_days(
            dates, holdout_trading_days=10
        )
        self.assertEqual(meta["holdout_trading_days"], 10)
        self.assertEqual(meta["n_test_days"], 10)
        self.assertTrue(train_idx)
        self.assertTrue(test_idx)
        eval_start = str(meta["eval_start"])
        for i in train_idx:
            self.assertLess(dates[i], eval_start)
        for i in test_idx:
            self.assertGreaterEqual(dates[i], eval_start)

    def test_split_eod_horizon_embargo_drops_last_h_train_days(self):
        dates = [f"2026-01-{d:02d}" for d in range(1, 28)]
        train_idx, _test_idx, meta = split_by_holdout_days(
            dates, holdout_trading_days=10, label_horizon_days=3
        )
        eval_start = str(meta["eval_start"])
        self.assertEqual(meta["label_horizon_days"], 3)
        self.assertEqual(len(meta.get("embargo_days") or []), 3)
        embargo = set(meta.get("embargo_days") or [])
        for i in train_idx:
            self.assertLess(dates[i], eval_start)
            self.assertNotIn(dates[i], embargo)
        for i in _test_idx:
            self.assertGreaterEqual(dates[i], eval_start)
            self.assertNotIn(dates[i], embargo)

    def test_calendar_dates_keep_eval_start_when_panel_misses_last_day(self):
        cal = [f"2026-01-{d:02d}" for d in range(1, 21)]
        panel = cal[:-1]
        _tr, _te, meta_panel = split_by_holdout_days(panel, holdout_trading_days=5)
        _tr2, te_cal, meta_cal = split_by_holdout_days(
            panel, holdout_trading_days=5, calendar_dates=cal
        )
        self.assertEqual(meta_cal["eval_start"], cal[-5])
        self.assertEqual(meta_cal["holdout_trading_days"], 5)
        self.assertEqual(meta_cal["split_mode"], "holdout_days")
        self.assertNotEqual(meta_panel["eval_start"], meta_cal["eval_start"])
        for i in te_cal:
            self.assertGreaterEqual(panel[i], meta_cal["eval_start"])

    def test_resolve_ridge_split_does_not_fall_back_to_time_frac(self):
        from core.research.holdout import resolve_ridge_split

        dates = ["2026-01-01", "2026-01-02"]
        train_idx, test_idx, meta = resolve_ridge_split(dates, holdout_trading_days=10)
        self.assertNotEqual(meta.get("split_mode"), "time_frac")
        self.assertNotIn("train_frac", meta)
        self.assertEqual(len(train_idx), 2)
        self.assertEqual(test_idx, [])

    def test_research_model_path(self):
        self.assertEqual(
            research_model_path("/x/tau_ridge_model.json"),
            "/x/tau_ridge_model_research.json",
        )

    def test_research_sidecar_flags(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            live_path = os.path.join(tmp, "tau_ridge_model.json")
            empty = research_sidecar_flags(live_path)
            self.assertFalse(empty["research_exists"])
            self.assertTrue(str(empty["research_path"]).endswith("_research.json"))
            self.assertIsNone(empty["research_promoted_at"])
            research_path = research_model_path(live_path)
            with open(research_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "return_model": {"coefficients": {"a": 1.0}},
                        "promoted_at": "2026-09-10T12:00:00",
                    },
                    f,
                )
            flags = research_sidecar_flags(live_path)
            self.assertTrue(flags["research_exists"])
            self.assertEqual(flags["research_path"], research_path)
            self.assertEqual(flags["research_promoted_at"], "2026-09-10T12:00:00")
            self.assertEqual(flags["research_fitted_at"], "2026-09-10T12:00:00")

    def test_model_fit_id_prefers_fitted_at(self):
        self.assertEqual(
            model_fit_id(
                {
                    "fitted_at": "2026-10-04T05:46:01Z",
                    "promoted_at": "2026-10-04T06:30:10Z",
                }
            ),
            "2026-10-04T05:46:01Z",
        )
        self.assertEqual(
            model_fit_id({"source_saved_at": "2026-10-04T05:46:01Z"}),
            "2026-10-04T05:46:01Z",
        )
        self.assertIsNone(model_fit_id(None))

    def test_stamp_fitted_at_keeps_existing(self):
        doc = {"success": True, "fitted_at": "2026-10-01T00:00:00Z"}
        stamp_fitted_at(doc)
        self.assertEqual(doc["fitted_at"], "2026-10-01T00:00:00Z")
        fresh = {"success": True, "return_model": {"intercept": 0}}
        stamp_fitted_at(fresh)
        self.assertTrue(fresh.get("fitted_at"))

    def test_horizon_ridge_persist_keeps_fitted_at(self):
        import json
        from unittest.mock import patch

        from core.research.horizon_ridge import persist_ridge_model, save_ridge_last_report

        beta = {"coefficients": {"gap_pct": 0.05}, "intercept": 0.0}
        report = {
            "success": True,
            "fitted_at": "2026-10-04T09:35:00Z",
            "return_model": dict(beta),
            "return_model_research": dict(beta),
            "oos": {"n_train": 10, "n_test": 2},
        }
        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            with patch("core.paths.LIVE_DIR", live):
                save_ridge_last_report("t30", report)
                last_path = os.path.join(live, "t30_ridge_last_report.json")
                with open(last_path, encoding="utf-8") as f:
                    last = json.load(f)
                self.assertEqual(last["fitted_at"], "2026-10-04T09:35:00Z")
                saved = persist_ridge_model(
                    "t30", report, role="research", force=True, note="test"
                )
                self.assertTrue(saved.get("success"), saved)
                self.assertEqual(saved.get("fitted_at"), "2026-10-04T09:35:00Z")
                self.assertTrue(saved.get("promoted_at"))
                self.assertNotEqual(saved.get("promoted_at"), saved.get("fitted_at"))
                flags = research_sidecar_flags(os.path.join(live, "t30_ridge_model.json"))
                self.assertEqual(flags.get("research_fitted_at"), "2026-10-04T09:35:00Z")
                self.assertEqual(
                    flags.get("research_promoted_at"), saved.get("promoted_at")
                )

    def test_attach_ridge_role_flags_fitted_at_from_last_report(self):
        import json

        from quant.services.quant_service_factors import _attach_ridge_role_flags

        with tempfile.TemporaryDirectory() as tmp:
            live_path = os.path.join(tmp, "tc_ridge_model.json")
            last_path = os.path.join(tmp, "tc_ridge_last_report.json")
            with open(last_path, "w", encoding="utf-8") as f:
                json.dump({"success": True, "return_model": {"intercept": 0}}, f)
            kept = _attach_ridge_role_flags(
                {"fitted_at": "2026-09-01T00:00:00Z"},
                live_path,
                live_present=False,
            )
            self.assertEqual(kept["fitted_at"], "2026-09-01T00:00:00Z")
            from_mtime = _attach_ridge_role_flags(
                {"success": True}, live_path, live_present=False
            )
            self.assertTrue(from_mtime.get("fitted_at"))

    def test_attach_ridge_role_flags_splits_live_promoted_at(self):
        import json

        from quant.services.quant_service_factors import _attach_ridge_role_flags

        with tempfile.TemporaryDirectory() as tmp:
            live_path = os.path.join(tmp, "co_ridge_model.json")
            with open(live_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "success": True,
                        "fitted_at": "2026-10-04T09:35:00Z",
                        "promoted_at": "2026-10-04T11:41:00Z",
                        "return_model": {"intercept": 0},
                    },
                    f,
                )
            out = _attach_ridge_role_flags(
                {"success": True}, live_path, live_present=True
            )
            self.assertEqual(out["live_fitted_at"], "2026-10-04T09:35:00Z")
            self.assertEqual(out["live_promoted_at"], "2026-10-04T11:41:00Z")

    def test_select_persist_return_model(self):
        from core.research.holdout import (
            require_research_suite,
            select_fit_return_model,
            stamp_live_suite_doc,
        )

        beta = {"coefficients": {"a": 0.5}}
        report = {
            "return_model": dict(beta),
            "return_model_research": dict(beta),
        }
        role, rm = select_persist_return_model(report, role="research")
        self.assertEqual(role, MODEL_ROLE_RESEARCH)
        self.assertEqual(rm["coefficients"]["a"], 0.5)
        role, rm = select_persist_return_model(report, role="live")
        self.assertEqual(role, MODEL_ROLE_LIVE)
        self.assertEqual(rm["coefficients"]["a"], 0.5)
        only_one = {"return_model": {"coefficients": {"a": 1.0}}}
        self.assertEqual(select_fit_return_model(only_one)["coefficients"]["a"], 1.0)
        tree_only = {
            "return_model": {
                "feature_names": ["gap_pct"],
                "booster": {"model": "x"},
                "backend": "lightgbm",
            }
        }
        self.assertEqual(
            select_fit_return_model(tree_only)["feature_names"], ["gap_pct"]
        )
        self.assertIsNone(
            select_fit_return_model({"return_model": {"feature_names": ["a"]}})
        )
        with tempfile.TemporaryDirectory() as tmp:
            live_path = os.path.join(tmp, "tau_ridge_model.json")
            doc, err = require_research_suite(live_path)
            self.assertIsNone(doc)
            self.assertFalse(err.get("success"))
            research_path = research_model_path(live_path)
            import json

            with open(research_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "return_model": {"coefficients": {"a": 0.5}, "intercept": 0.0},
                        "fitted_at": "2026-10-01T00:00:00Z",
                        "model_role": "research",
                    },
                    f,
                )
            doc, err = require_research_suite(live_path)
            self.assertEqual(err, {})
            live_doc = stamp_live_suite_doc(doc, note="test promote")
            self.assertEqual(live_doc.get("model_role"), MODEL_ROLE_LIVE)
            self.assertEqual(live_doc.get("promoted_from"), "research")
            self.assertEqual(
                (live_doc.get("return_model") or {}).get("coefficients", {}).get("a"),
                0.5,
            )

    def test_normalize_backtest_model_role_defaults_research(self):
        self.assertEqual(normalize_backtest_model_role(None), MODEL_ROLE_RESEARCH)
        self.assertEqual(normalize_backtest_model_role(""), MODEL_ROLE_RESEARCH)
        self.assertEqual(normalize_backtest_model_role("research"), MODEL_ROLE_RESEARCH)
        self.assertEqual(normalize_backtest_model_role("live"), MODEL_ROLE_LIVE)
        self.assertEqual(normalize_backtest_model_role("执行"), MODEL_ROLE_LIVE)
        self.assertEqual(normalize_backtest_model_role("execution"), MODEL_ROLE_LIVE)
        # 旧全样本别名已废弃，不再映射执行套
        self.assertEqual(normalize_backtest_model_role("full"), MODEL_ROLE_RESEARCH)

    def test_scoring_context_does_not_leak(self):
        self.assertEqual(current_scoring_model_role(), MODEL_ROLE_LIVE)
        with scoring_model_role_context("research"):
            self.assertEqual(current_scoring_model_role(), MODEL_ROLE_RESEARCH)
        self.assertEqual(current_scoring_model_role(), MODEL_ROLE_LIVE)


class TestTauRoleLoad(unittest.TestCase):
    def test_load_tau_research_does_not_fallback_to_live(self):
        from core.research import tc_ridge as tr

        live_doc = {
            "success": True,
            "return_model": {"coefficients": {"gap_pct": 1.0}, "intercept": 0.1},
        }
        with tempfile.TemporaryDirectory() as tmp:
            live_path = os.path.join(tmp, "tau_ridge_model.json")
            with open(live_path, "w", encoding="utf-8") as f:
                import json

                json.dump(live_doc, f)
            orig = tr.tau_model_path
            tr.tau_model_path = lambda: live_path  # type: ignore[method-assign]
            try:
                live = tr.load_tau_model(role="live")
                research = tr.load_tau_model(role="research")
                self.assertIsNotNone(live)
                self.assertIsNone(research)
            finally:
                tr.tau_model_path = orig  # type: ignore[method-assign]


class TestTauDeskPrefersHoldout(unittest.TestCase):
    def test_desk_key_changes_with_holdout_not_sample_count(self):
        from quant.services.quant_service_factors import _ridge_desk_key

        live = {
            "tau": "10:30",
            "target": "open_to_close_z",
            "sample_count": 113579,
            "holdout_trading_days": 10,
            "oos": {"n_test": 17717, "n_train": 95862},
        }
        last = dict(live)
        last["holdout_trading_days"] = 5
        last["oos"] = {"n_test": 9405, "n_train": 104174}
        self.assertEqual(
            _ridge_desk_key(live)[:3],
            _ridge_desk_key(last)[:3],
        )
        self.assertNotEqual(_ridge_desk_key(live), _ridge_desk_key(last))

    def test_select_ridge_desk_prefers_last_when_holdout_differs(self):
        from quant.services.quant_service_factors import _select_ridge_desk_doc

        live = {
            "return_model": {"coefficients": {"a": 1.0}},
            "holdout_trading_days": 5,
            "oos": {"n_test": 500, "holdout_trading_days": 5},
        }
        last = {
            "return_model": {"coefficients": {"a": 0.5}},
            "holdout_trading_days": 10,
            "oos": {"n_test": 1000, "holdout_trading_days": 10},
        }
        doc, use_last = _select_ridge_desk_doc(live, last)
        self.assertTrue(use_last)
        self.assertEqual(doc.get("holdout_trading_days"), 10)

    def test_select_ridge_desk_prefers_newer_last_when_holdout_same(self):
        """同 Holdout 时：last 拟合新于 live → 画 last（启用研究、执行未晋级）。"""
        from quant.services.quant_service_factors import _select_ridge_desk_doc

        live = {
            "return_model": {"coefficients": {"a": 1.0}, "intercept": 0.05},
            "fitted_at": "2026-10-14T04:28:00Z",
            "holdout_trading_days": 40,
            "oos": {"n_test": 30249, "holdout_trading_days": 40},
        }
        last = {
            "return_model": {"coefficients": {"a": 0.8}, "intercept": 0.047},
            "fitted_at": "2026-10-14T06:00:00Z",
            "holdout_trading_days": 40,
            "oos": {"n_test": 30249, "holdout_trading_days": 40},
        }
        doc, use_last = _select_ridge_desk_doc(live, last)
        self.assertTrue(use_last)
        self.assertAlmostEqual(float((doc.get("return_model") or {}).get("intercept") or 0), 0.047)

        # 晋级后 live 与 last 同 fitted_at → 画执行套
        live2 = dict(last)
        live2["promoted_at"] = "2026-10-14T06:05:00Z"
        doc2, use_last2 = _select_ridge_desk_doc(live2, last)
        self.assertFalse(use_last2)
        self.assertAlmostEqual(
            float((doc2.get("return_model") or {}).get("intercept") or 0), 0.047
        )


if __name__ == "__main__":
    unittest.main()
