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

    def test_select_persist_return_model(self):
        report = {
            "return_model": {"coefficients": {"a": 1.0}},
            "return_model_research": {"coefficients": {"a": 0.5}},
        }
        role, rm = select_persist_return_model(report, role="research")
        self.assertEqual(role, MODEL_ROLE_RESEARCH)
        self.assertEqual(rm["coefficients"]["a"], 0.5)
        role, rm = select_persist_return_model(report, role="live")
        self.assertEqual(role, MODEL_ROLE_LIVE)
        self.assertEqual(rm["coefficients"]["a"], 1.0)

    def test_normalize_backtest_model_role_defaults_research(self):
        self.assertEqual(normalize_backtest_model_role(None), MODEL_ROLE_RESEARCH)
        self.assertEqual(normalize_backtest_model_role(""), MODEL_ROLE_RESEARCH)
        self.assertEqual(normalize_backtest_model_role("research"), MODEL_ROLE_RESEARCH)
        self.assertEqual(normalize_backtest_model_role("live"), MODEL_ROLE_LIVE)
        self.assertEqual(normalize_backtest_model_role("执行"), MODEL_ROLE_LIVE)
        self.assertEqual(normalize_backtest_model_role("execution"), MODEL_ROLE_LIVE)

    def test_scoring_context_does_not_leak(self):
        self.assertEqual(current_scoring_model_role(), MODEL_ROLE_LIVE)
        with scoring_model_role_context("research"):
            self.assertEqual(current_scoring_model_role(), MODEL_ROLE_RESEARCH)
        self.assertEqual(current_scoring_model_role(), MODEL_ROLE_LIVE)


class TestTauRoleLoad(unittest.TestCase):
    def test_load_tau_research_does_not_fallback_to_live(self):
        from core.research import tau_ridge as tr

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
        from quant.services.quant_service_factors import _tau_ridge_desk_key

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
            _tau_ridge_desk_key(live)[:3],
            _tau_ridge_desk_key(last)[:3],
        )
        self.assertNotEqual(_tau_ridge_desk_key(live), _tau_ridge_desk_key(last))

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


if __name__ == "__main__":
    unittest.main()
