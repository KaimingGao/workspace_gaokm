"""回测中心组合回测：成套校验、评测窗、收益与 T+1。不写套槽。"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import numpy as np

from types import SimpleNamespace

from core.research.yhat_factory_backtest import (
    _scores_for_head,
    apply_seq_clip,
    check_mix,
    check_suite,
    delete_factory_backtest,
    factory_backtest_status,
    finish_factory_backtest,
    normalize_backtest_rules,
    run_factory_backtest,
    save_factory_rules,
    simulate_scored_days,
    stamp_day_ranks,
    start_factory_backtest,
    take_factor_columns,
)


def _patch_store(td: str):
    return mock.patch.multiple(
        "core.research.yhat_tf_desk",
        YHAT_TF_DIR=td,
        YHAT_TF_LAST_PATH=os.path.join(td, "last_status.json"),
    )


def _spec(head, pack, *, start="2024-01-02", end="2024-01-05", sample=10.0, backend="transformer"):
    return {
        "head": head,
        "run_id": f"{head}-v1",
        "backend": backend,
        "meta": {
            "backend": backend,
            "factor_pack": pack,
            "eval_start": start,
            "eval_end": end,
            "d_model": 64,
            "nhead": 4,
            "nlayers": 3,
            "factor_hidden": 64,
            "d_seq_in": 16,
        },
        "fit_params": {
            "t_sample_pct": sample,
            "pool_n": 800,
            "watching_limit": 800,
            "lookback": 120,
            "seq_len": 20,
            "seq_feat_set": "seq16",
        },
    }


def _suite(**kwargs):
    specs = [
        _spec("oo", "aoo_core", **kwargs),
        _spec("tc", "atc_core", **kwargs),
        _spec("oo_rank", "aoo_core", **kwargs),
        _spec("tc_rank", "atc_core", **kwargs),
    ]
    out = check_suite(specs)
    out["cfg"] = _cfg()
    return out


def _cfg():
    return {
        "fuse_enter": 0.001,
        "fuse_strong": 0.05,
        "lot_base_amount": 10000,
        "lot_strong_amount": 10000,
        "fusion_w_oo": 0.6,
        "fusion_w_oc": 0.4,
        "fusion_w_oo_rank": 0.6,
        "fusion_w_tc_rank": 0.4,
        "cash_floor": 0,
        "y_oo_gt0": False,
        "oo_rank_max": 6,
        "morph_gate_enabled": False,
        "top_k": 15,
        "enabled": True,
    }


def _day(date, y_oo, y_tc, price, mark=None):
    code = "600000"
    close = price if mark is None else mark
    bar = {
        "date": date,
        "open": price,
        "close": close,
        "high": max(price, close),
        "low": min(price, close),
    }
    return {
        "date": date,
        "prices": {code: price},
        "marks": {code: close},
        "bars": {code: [bar]},
        "scored": [
            {
                "stock_code": code,
                "y_oo": y_oo,
                "predicted_score": y_oo,
                "y_τc": y_tc,
                "predicted_score_τc": y_tc,
                "y_oo_rank_score": 0.9,
                "y_tc_rank_score": 0.9,
            }
        ],
    }


class TestFactoryBacktestSuite(unittest.TestCase):
    def test_overlap_window_is_intersection(self):
        suite = _suite()
        self.assertEqual(suite["window_start"], "2024-01-02")
        self.assertEqual(suite["window_end"], "2024-01-05")
        self.assertNotIn("universe_note", suite)

    def test_pack_mismatch_rejected(self):
        specs = [
            _spec("oo", "aoo_core"),
            _spec("tc", "atc_core"),
            _spec("oo_rank", "aoo_full"),
            _spec("tc_rank", "atc_core"),
        ]
        with self.assertRaises(ValueError):
            check_suite(specs)

    def test_ridge_suite_accepted(self):
        suite = _suite(backend="ridge")
        self.assertEqual(suite["backend"], "ridge")

    def test_backend_mismatch_rejected(self):
        specs = [
            _spec("oo", "aoo_full", backend="lightgbm"),
            _spec("tc", "atc_full", backend="transformer"),
            _spec("oo_rank", "aoo_full", backend="lightgbm"),
            _spec("tc_rank", "atc_full", backend="lightgbm"),
        ]
        with self.assertRaises(ValueError):
            check_suite(specs)

    def test_ridge_pool_and_lookback_may_differ(self):
        specs = [
            _spec("oo", "aoo_full", backend="ridge"),
            _spec("tc", "atc_full", backend="ridge"),
            _spec("oo_rank", "aoo_full", backend="ridge"),
            _spec("tc_rank", "atc_full", backend="ridge"),
        ]
        specs[0]["fit_params"]["pool_n"] = 1600
        specs[0]["fit_params"]["watching_limit"] = 1600
        specs[0]["fit_params"]["lookback"] = 1000
        specs[1]["fit_params"]["pool_n"] = 1600
        specs[1]["fit_params"]["watching_limit"] = 1600
        specs[1]["fit_params"]["lookback"] = 100
        specs[3]["fit_params"]["pool_n"] = 800
        specs[3]["fit_params"]["watching_limit"] = 800
        specs[3]["fit_params"]["lookback"] = 700
        for spec in specs:
            for key in ("t_sample_pct", "seq_len", "seq_feat_set"):
                spec["fit_params"].pop(key, None)
        suite = check_suite(specs)
        self.assertEqual(suite["backend"], "ridge")
        self.assertEqual(suite["lookback"], 1000)

    def test_seq_len_mismatch_rejected(self):
        specs = [
            _spec("oo", "aoo_core"),
            _spec("tc", "atc_core"),
            _spec("oo_rank", "aoo_core"),
            _spec("tc_rank", "atc_core"),
        ]
        specs[1]["fit_params"]["seq_len"] = 40
        with self.assertRaises(ValueError):
            check_suite(specs)

    def test_sample_mismatch_rejected(self):
        specs = [
            _spec("oo", "aoo_core", sample=10),
            _spec("tc", "atc_core", sample=20),
            _spec("oo_rank", "aoo_core", sample=10),
            _spec("tc_rank", "atc_core", sample=10),
        ]
        with self.assertRaises(ValueError):
            check_suite(specs)

    def test_empty_overlap_rejected(self):
        specs = [
            _spec("oo", "aoo_core", start="2024-01-02", end="2024-01-05"),
            _spec("tc", "atc_core", start="2024-02-01", end="2024-02-10"),
            _spec("oo_rank", "aoo_core", start="2024-01-02", end="2024-01-05"),
            _spec("tc_rank", "atc_core", start="2024-01-02", end="2024-01-05"),
        ]
        with self.assertRaises(ValueError):
            check_suite(specs)


class TestFactoryBacktestSim(unittest.TestCase):
    def test_return_after_next_session_sell(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            report = simulate_scored_days(
                [
                    _day("2024-01-02", 2.0, 2.0, 10.0, 11.0),
                    _day("2024-01-03", -1.0, -1.0, 12.0, 12.0),
                ],
                cfg=_cfg(),
            )
        log = buf.getvalue()
        self.assertIn("成交明细", log)
        self.assertIn(
            "日期 · 动作 · 代码 · 名称 · 股数 · 开盘 · 收盘 · 成交 · fuse · y_oo · y_τc · oo_rank · τc_rank",
            log,
        )
        self.assertIn(
            "2024-01-02 · 开 · 600000 · 浦发银行 · 1000股 · 10.00 · 11.00 · 10.00 · "
            "+2.00% · +2.00%\u00a0(+20.00%) · +2.00%\u00a0(+10.00%)",
            log,
        )
        self.assertNotIn(" · 收益 ", log)
        self.assertNotIn("+2.00%\u00a0(+20.00%) · +2.00%\u00a0(+20.00%)", log)
        self.assertIn(
            "2024-01-03 · 清 · 600000 · 浦发银行 · 1000股 · 12.00 · 12.00 · 12.00 · -1.00%",
            log,
        )
        self.assertIn("-1.00%\u00a0(—)", log)
        self.assertNotIn("兑现", log)
        self.assertNotIn("同向", log)
        self.assertEqual(report["n_buys"], 1)
        self.assertEqual(report["n_sells"], 1)
        self.assertEqual(report["end_shares"], 0)
        self.assertGreater(report["cumulative_return"], 0)
        self.assertGreater(report["turnover"], 0)
        self.assertEqual(report["fill_clock"], "09:30")
        self.assertEqual(report["accuracy_pct"], 100.0)
        self.assertEqual(report["accuracy_n"], 1)
        self.assertEqual(report["decision_return_pct"], 20.0)
        self.assertEqual(report["hit_rate_pct"], 100.0)
        self.assertEqual(report["max_drawdown_pct"], 0.0)
        self.assertIsNone(report["excess_pct"])

    def test_same_day_sell_stays_locked(self):
        report = simulate_scored_days(
            [
                _day("2024-01-02", 2.0, 2.0, 10.0, 11.0),
                _day("2024-01-02", -1.0, -1.0, 12.0, 12.0),
            ],
            cfg=_cfg(),
        )
        self.assertEqual(report["n_buys"], 1)
        self.assertEqual(report["n_sells"], 0)
        self.assertGreater(report["end_shares"], 0)

    def test_dates_before_eval_start_are_not_return(self):
        suite = _suite(start="2024-01-03", end="2024-01-05")
        report = run_factory_backtest(
            "T",
            suite["versions"],
            days=[
                _day("2024-01-02", 2.0, 2.0, 10.0),
                _day("2024-01-03", -1.0, -1.0, 12.0),
            ],
            suite=suite,
            cfg=_cfg(),
        )
        self.assertEqual(report["n_days"], 1)
        self.assertEqual(report["n_buys"], 0)
        self.assertEqual(report["window_start"], "2024-01-03")

    def test_run_does_not_call_replay_or_suite_writer(self):
        suite = _suite()
        with mock.patch(
            "core.backtest.paper_replay.backtest_paper_replay",
            side_effect=AssertionError("replay"),
        ), mock.patch(
            "core.research.model_versions.archive_persist_safe",
            side_effect=AssertionError("suite"),
        ):
            report = run_factory_backtest(
                "T",
                suite["versions"],
                days=[
                    _day("2024-01-02", 2.0, 2.0, 10.0),
                    _day("2024-01-03", -1.0, -1.0, 12.0),
                ],
                suite=suite,
                cfg=_cfg(),
            )
        self.assertGreater(report["cumulative_return"], 0)
        self.assertEqual(report["fill_clock"], "09:30")


class TestFactoryBacktestJob(unittest.TestCase):
    def test_start_spawns_factory_script_only(self):
        suite = _suite()
        calls = []

        class _Proc:
            pid = 4242

        def _popen(cmd, **kwargs):
            calls.append(cmd)
            return _Proc()

        with tempfile.TemporaryDirectory() as td, mock.patch(
            "core.paths.LIVE_DIR", td
        ), mock.patch(
            "core.research.yhat_factory_backtest.load_mix", return_value=suite
        ), mock.patch(
            "core.research.yhat_factory_backtest._pid_alive", return_value=True
        ), mock.patch(
            "core.research.yhat_factory_backtest.subprocess.Popen", side_effect=_popen
        ):
            out = start_factory_backtest("BT", suite["versions"])
            again = start_factory_backtest("BT", suite["versions"])
            with open(os.path.join(td, "yhat_bt", "backtest_job.json"), encoding="utf-8") as f:
                job = json.load(f)
        self.assertTrue(out["success"])
        self.assertFalse(again["success"])
        cmd = " ".join(str(part) for part in calls[0])
        self.assertIn("run_yhat_factory_backtest.py", cmd)
        self.assertNotIn("paper_replay", cmd)
        self.assertEqual(job["status"], "running")
        self.assertFalse(job["live_allowed"])
        self.assertEqual(job["fill_clock"], "09:30")

    def test_finish_keeps_report_next_to_factory(self):
        with tempfile.TemporaryDirectory() as td, _patch_store(td):
            finish_factory_backtest(
                "T",
                report={
                    "cumulative_return": 0.1,
                    "turnover": 0.2,
                    "window_start": "2024-01-02",
                    "window_end": "2024-01-03",
                    "versions": {"oo": "oo-v1"},
                    "fill_clock": "09:30",
                },
            )
            with open(os.path.join(td, "backtest_job.json"), encoding="utf-8") as f:
                job = json.load(f)
            with open(os.path.join(td, "backtest_last.json"), encoding="utf-8") as f:
                last = json.load(f)
            status = factory_backtest_status("T")
        self.assertEqual(job["status"], "done")
        self.assertFalse(job["live_allowed"])
        self.assertEqual(last["cumulative_return"], 0.1)
        self.assertEqual(status["rules"]["name"], "rank_lots")
        self.assertEqual(status["rules"]["fill_clock"], "09:30")
        self.assertEqual(status["rules"]["start_cash"], 200000.0)
        self.assertEqual(status["log_tail"], "")
        self.assertEqual(status["runs"], [])


class TestFactoryBacktestRules(unittest.TestCase):
    def test_strong_gate_and_amount_lift_to_entry(self):
        brief = normalize_backtest_rules(
            {
                "fuse_enter": 0.02,
                "fuse_strong": 0.001,
                "lot_base_amount": 20000,
                "lot_strong_amount": 1000,
                "oo_rank_max": None,
                "oo_rank_strong_max": "",
                "w_oo": 3,
                "w_tc": 0,
            }
        )
        self.assertNotIn("tier_a_only", brief)
        self.assertNotIn("tier_a_only", normalize_backtest_rules({"tier_a_only": False}))
        self.assertEqual(brief["fuse_strong"], 0.02)
        self.assertEqual(brief["lot_strong_amount"], 20000)
        self.assertIsNone(brief["oo_rank_max"])
        self.assertIsNone(brief["oo_rank_strong_max"])
        self.assertEqual(brief["w_oo"], 1.0)
        self.assertEqual(brief["w_tc"], 0.0)
        self.assertEqual(brief["fill_clock"], "09:30")

    def test_page_rules_can_block_the_entry(self):
        suite = _suite()
        report = run_factory_backtest(
            "T",
            suite["versions"],
            days=[_day("2024-01-02", 2.0, 2.0, 10.0)],
            suite=suite,
            rules={"fuse_enter": 0.05, "fuse_strong": 0.05, "start_cash": 100000},
        )
        self.assertEqual(report["n_buys"], 0)
        self.assertEqual(report["start_cash"], 100000.0)
        self.assertEqual(report["rules"]["fuse_enter"], 0.05)
        self.assertEqual(report["fill_clock"], "09:30")

    def test_saved_rules_stay_in_the_factory_dir(self):
        suite = _suite()
        calls = []

        class _Proc:
            pid = 5151

        def _popen(cmd, **kwargs):
            calls.append(cmd)
            return _Proc()

        with tempfile.TemporaryDirectory() as td, mock.patch(
            "core.paths.LIVE_DIR", td
        ), mock.patch(
            "core.research.yhat_factory_backtest.load_mix", return_value=suite
        ), mock.patch(
            "core.research.yhat_factory_backtest._pid_alive", return_value=True
        ), mock.patch(
            "core.research.yhat_factory_backtest.subprocess.Popen", side_effect=_popen
        ):
            saved = save_factory_rules("BT", {"w_oo": 0.8, "y_oo_gt0": True, "start_cash": 300000})
            out = start_factory_backtest("BT", suite["versions"], {"w_oo": 0.85})
            root = os.path.join(td, "yhat_bt")
            with open(os.path.join(root, "backtest_rules.json"), encoding="utf-8") as f:
                disk = json.load(f)
            with open(os.path.join(root, "backtest_job.json"), encoding="utf-8") as f:
                job = json.load(f)
            names = set(os.listdir(root))
        self.assertEqual(saved["w_oo"], 0.8)
        self.assertTrue(saved["y_oo_gt0"])
        self.assertEqual(saved["start_cash"], 300000.0)
        self.assertTrue(out["success"])
        self.assertEqual(disk["w_oo"], 0.85)
        self.assertEqual(job["rules"]["w_oo"], 0.85)
        self.assertFalse(job["live_allowed"])
        self.assertIn("--rules", calls[0])
        self.assertNotIn("paper_replay", " ".join(str(part) for part in calls[0]))
        self.assertTrue(names <= {"backtest_rules.json", "backtest_job.json", "backtest.log"})


class TestFactoryBacktestHistory(unittest.TestCase):
    def _seed(self, td, run_id, started, text):
        job = {
            "status": "running",
            "run_id": run_id,
            "started_at": started,
            "versions": {"oo": run_id},
        }
        with open(os.path.join(td, "backtest_job.json"), "w", encoding="utf-8") as f:
            json.dump(job, f)
        with open(os.path.join(td, "backtest.log"), "w", encoding="utf-8") as f:
            f.write(text)

    def test_each_run_keeps_its_console(self):
        with tempfile.TemporaryDirectory() as td, _patch_store(td):
            self._seed(td, "20261118-140201", "2026-11-18T14:02:01", "成交明细 · 第一笔\n")
            finish_factory_backtest(
                "T",
                report={
                    "cumulative_return": 0.1,
                    "window_start": "2024-01-02",
                    "window_end": "2024-01-05",
                    "versions": {"oo": "oo-v1"},
                },
            )
            self._seed(td, "20261118-150000", "2026-11-18T15:00:00", "成交明细 · 第二笔\n")
            finish_factory_backtest(
                "T",
                report={
                    "cumulative_return": -0.02,
                    "window_start": "2024-02-01",
                    "window_end": "2024-02-05",
                    "versions": {"oo": "oo-v2"},
                },
            )
            latest = factory_backtest_status("T")
            older = factory_backtest_status("T", run_id="20261118-140201")
            kept_log = os.path.isfile(os.path.join(td, "backtests", "20261118-140201.log"))
        self.assertEqual(latest["run_id"], "20261118-150000")
        self.assertFalse(latest["viewing"])
        self.assertIn("第二笔", latest["log_tail"])
        self.assertEqual([it["id"] for it in latest["runs"]], ["20261118-150000", "20261118-140201"])
        self.assertIn("+10.00%", latest["runs"][1]["label"])
        self.assertIn("-2.00%", latest["runs"][0]["label"])
        self.assertTrue(older["viewing"])
        self.assertEqual(older["live_status"], "done")
        self.assertIn("第一笔", older["log_tail"])
        self.assertNotIn("第二笔", older["log_tail"])
        self.assertEqual(older["report"]["versions"]["oo"], "oo-v1")
        self.assertTrue(kept_log)

    def test_history_drops_the_oldest_past_the_keep_limit(self):
        with tempfile.TemporaryDirectory() as td, _patch_store(td), mock.patch(
            "core.research.yhat_factory_backtest._RUN_KEEP", 1
        ):
            self._seed(td, "20261118-140201", "2026-11-18T14:02:01", "第一笔\n")
            finish_factory_backtest("T", report={"cumulative_return": 0.1, "versions": {"oo": "a"}})
            self._seed(td, "20261118-150000", "2026-11-18T15:00:00", "第二笔\n")
            finish_factory_backtest("T", report={"cumulative_return": 0.2, "versions": {"oo": "b"}})
            status = factory_backtest_status("T")
            gone = factory_backtest_status("T", run_id="20261118-140201")
            old_log = os.path.isfile(os.path.join(td, "backtests", "20261118-140201.log"))
        self.assertEqual([it["id"] for it in status["runs"]], ["20261118-150000"])
        self.assertFalse(gone["viewing"])
        self.assertFalse(old_log)

    def test_status_backfills_a_finished_job_without_run_id(self):
        with tempfile.TemporaryDirectory() as td, _patch_store(td):
            job = {
                "status": "done",
                "started_at": "2026-11-18T13:53:49",
                "finished_at": "2026-11-18T14:28:24",
                "versions": {"oo": "oo-v1"},
                "window_start": "2026-08-18",
                "window_end": "2026-11-16",
                "report": {
                    "cumulative_return": -0.02058,
                    "window_start": "2026-08-18",
                    "window_end": "2026-11-16",
                    "versions": {"oo": "oo-v1"},
                },
            }
            with open(os.path.join(td, "backtest_job.json"), "w", encoding="utf-8") as f:
                json.dump(job, f)
            with open(os.path.join(td, "backtest.log"), "w", encoding="utf-8") as f:
                f.write("成交明细 · 旧记录\n")
            status = factory_backtest_status("T")
            again = factory_backtest_status("T")
            with open(os.path.join(td, "backtest_job.json"), encoding="utf-8") as f:
                saved = json.load(f)
        self.assertEqual(len(status["runs"]), 1)
        self.assertEqual(status["run_id"], "20261118-135349")
        self.assertIn("旧记录", status["log_tail"])
        self.assertIn("-2.06%", status["runs"][0]["label"])
        self.assertEqual(again["runs"], status["runs"])
        self.assertEqual(saved["run_id"], "20261118-135349")

    def test_delete_removes_one_record_and_does_not_restore_it(self):
        with tempfile.TemporaryDirectory() as td, _patch_store(td):
            self._seed(td, "20261118-140201", "2026-11-18T14:02:01", "成交明细 · 第一笔\n")
            finish_factory_backtest(
                "T",
                report={"cumulative_return": 0.1, "versions": {"oo": "oo-v1"}},
            )
            self._seed(td, "20261118-150000", "2026-11-18T15:00:00", "成交明细 · 第二笔\n")
            finish_factory_backtest(
                "T",
                report={"cumulative_return": -0.02, "versions": {"oo": "oo-v2"}},
            )
            removed = delete_factory_backtest("T", "20261118-140201")
            after = factory_backtest_status("T")
            missing = delete_factory_backtest("T", "20261118-140201")
            current = delete_factory_backtest("T", "20261118-150000")
            again = factory_backtest_status("T")
            started = factory_backtest_status("T")
            old_log = os.path.isfile(os.path.join(td, "backtests", "20261118-140201.log"))
            cur_log = os.path.isfile(os.path.join(td, "backtests", "20261118-150000.log"))
            with open(os.path.join(td, "backtest.log"), encoding="utf-8") as f:
                live = f.read()
        self.assertTrue(removed["success"])
        self.assertEqual([it["id"] for it in after["runs"]], ["20261118-150000"])
        self.assertIn("第二笔", after["log_tail"])
        self.assertFalse(old_log)
        self.assertFalse(missing["success"])
        self.assertEqual(missing["error"], "记录不存在")
        self.assertTrue(current["success"])
        self.assertEqual(again["runs"], [])
        self.assertEqual(started["runs"], [])
        self.assertIn("第二笔", again["log_tail"])
        self.assertIn("第二笔", live)
        self.assertFalse(cur_log)
        self.assertFalse(delete_factory_backtest("T", "../secret")["success"])

    def test_delete_failed_record_clears_the_error(self):
        with tempfile.TemporaryDirectory() as td, _patch_store(td):
            self._seed(td, "20261118-140201", "2026-11-18T14:02:01", "Traceback · 权重缺失\n")
            finish_factory_backtest("T", error="权重不存在")
            before = factory_backtest_status("T")
            removed = delete_factory_backtest("T", before["run_id"])
            after = factory_backtest_status("T")
            with open(os.path.join(td, "backtest_job.json"), encoding="utf-8") as f:
                job = json.load(f)
            with open(os.path.join(td, "backtest.log"), encoding="utf-8") as f:
                live = f.read()
        self.assertEqual(before["status"], "error")
        self.assertEqual(before["error"], "权重不存在")
        self.assertTrue(removed["success"])
        self.assertEqual(after["runs"], [])
        self.assertEqual(after["status"], "idle")
        self.assertEqual(after["error"], "")
        self.assertIsNone(after["report"])
        self.assertEqual(job["error"], "")
        self.assertEqual(job["status"], "idle")
        self.assertEqual(after["log_tail"], "")
        self.assertEqual(live, "")

    def test_older_failure_delete_keeps_the_current_error(self):
        with tempfile.TemporaryDirectory() as td, _patch_store(td):
            self._seed(td, "20261118-140201", "2026-11-18T14:02:01", "第一笔\n")
            finish_factory_backtest("T", error="第一笔失败")
            self._seed(td, "20261118-150000", "2026-11-18T15:00:00", "第二笔\n")
            finish_factory_backtest("T", error="第二笔失败")
            delete_factory_backtest("T", "20261118-140201")
            after = factory_backtest_status("T")
        self.assertEqual([it["id"] for it in after["runs"]], ["20261118-150000"])
        self.assertEqual(after["status"], "error")
        self.assertEqual(after["error"], "第二笔失败")
        self.assertIn("第二笔", after["log_tail"])

    def test_status_hides_error_when_failed_record_was_already_dropped(self):
        with tempfile.TemporaryDirectory() as td, _patch_store(td):
            job = {
                "status": "error",
                "error": "权重不存在",
                "history_dropped": True,
                "run_id": "",
                "running": False,
                "started_at": "2026-11-18T14:02:01",
                "report": None,
            }
            with open(os.path.join(td, "backtest_job.json"), "w", encoding="utf-8") as f:
                json.dump(job, f)
            with open(os.path.join(td, "backtest_last.json"), "w", encoding="utf-8") as f:
                json.dump({"cumulative_return": 0.1}, f)
            with open(os.path.join(td, "backtest.log"), "w", encoding="utf-8") as f:
                f.write("Traceback · 权重缺失\n")
            status = factory_backtest_status("T")
            with open(os.path.join(td, "backtest_job.json"), encoding="utf-8") as f:
                saved = json.load(f)
            with open(os.path.join(td, "backtest.log"), encoding="utf-8") as f:
                live = f.read()
        self.assertEqual(status["status"], "idle")
        self.assertEqual(status["error"], "")
        self.assertEqual(status["log_tail"], "")
        self.assertIsNone(status["report"])
        self.assertEqual(saved["status"], "idle")
        self.assertEqual(saved["error"], "")
        self.assertEqual(live, "")

    def test_running_record_cannot_be_deleted(self):
        with tempfile.TemporaryDirectory() as td, _patch_store(td):
            self._seed(td, "20261118-160000", "2026-11-18T16:00:00", "进行中\n")
            blocked = delete_factory_backtest("T", "20261118-160000")
            still = os.path.isfile(os.path.join(td, "backtest.log"))
        self.assertFalse(blocked["success"])
        self.assertEqual(blocked["error"], "进行中的回测不能删除")
        self.assertTrue(still)


class TestScoreHelpers(unittest.TestCase):
    def test_seq_clip_uses_saved_bounds(self):
        X = np.array([[[0.0, 9.0]]], dtype=np.float32)
        gap = np.array([5.0], dtype=np.float32)
        apply_seq_clip(X, gap, {"x_lo": [0.0, 0.0], "x_hi": [1.0, 1.0], "gap_lo": 0.0, "gap_hi": 1.0})
        self.assertEqual(float(X[0, 0, 1]), 1.0)
        self.assertEqual(float(gap[0]), 1.0)

    def test_missing_factor_column_rejected(self):
        X = np.ones((2, 1), dtype=np.float32)
        with self.assertRaises(ValueError):
            take_factor_columns(X, ["a"], ["b"])

    def test_z_keeps_full_pool_and_only_a_is_scored(self):
        panel = SimpleNamespace(
            dates=["2024-01-02", "2024-01-02", "2024-01-02"],
            feature_names=["f"],
            X=np.ones((3, 1), dtype=np.float64),
            codes=["600001", "600002", "600003"],
        )
        bundle = {"head": "oo", "backend": "lightgbm", "meta": {"factor_names": ["f"]}}
        seen = {}

        def _z(X, names, dates, z_clip):
            seen["z_rows"] = len(X)
            return X.astype(np.float32)

        def _pred(bundle, X, seq, gap):
            seen["pred_rows"] = len(X)
            return np.full(len(X), 1.5)

        with mock.patch(
            "core.research.yhat_factory_backtest._zscore", side_effect=_z
        ), mock.patch(
            "core.research.yhat_factory_backtest._predict_matrix", side_effect=_pred
        ):
            out = _scores_for_head(
                bundle,
                panel,
                None,
                "2024-01-02",
                "2024-01-02",
                score_codes={"600001"},
            )
        self.assertEqual(seen["z_rows"], 3)
        self.assertEqual(seen["pred_rows"], 1)
        self.assertEqual(list(out), [("600001", "2024-01-02")])

class TestFactoryBacktestPage(unittest.TestCase):
    def test_score_scope_is_full_pool(self):
        from tests.conftest import ROOT

        factory = os.path.join(ROOT, "web", "static", "partials", "yhat_factory_panel.html")
        center = os.path.join(ROOT, "web", "static", "partials", "yhat_bt_panel.html")
        css = os.path.join(ROOT, "web", "static", "css", "quant.css")
        js = os.path.join(ROOT, "web", "static", "js", "yhat_tf.js")
        router = os.path.join(ROOT, "web", "routers", "quant_yhat_tf.py")
        with open(factory, encoding="utf-8") as f:
            factory_html = f.read()
        with open(center, encoding="utf-8") as f:
            html = f.read()
        with open(css, encoding="utf-8") as f:
            style = f.read()
        with open(js, encoding="utf-8") as f:
            script = f.read()
        with open(router, encoding="utf-8") as f:
            routes = f.read()
        self.assertNotIn('id="yhat-factory-backtest"', factory_html)
        self.assertNotIn("离线回测", factory_html)
        self.assertNotIn("pageMix", script)
        self.assertNotIn("离线回测", script)
        for factory_name in ("l", "t", "r", "tf"):
            self.assertNotIn(f"/api/quant/yhat-{factory_name}/backtest", routes)
        self.assertIn("/api/quant/yhat-bt/backtest", routes)
        desk = script.split("export function initYhatTf", 1)[1]
        self.assertNotIn("bindBacktest()", desk)
        self.assertNotIn("pollBacktest()", desk)
        form_end = html.find("</form>")
        actions = html.find('class="yhat-tf-backtest-actions"')
        button = html.find('id="yhat-factory-backtest-run"')
        self.assertLess(form_end, actions)
        self.assertLess(actions, button)
        self.assertNotIn('data-bt-field="tier_a_only"', html)
        self.assertIn("打分全池", html)
        fill_css = style.split(".ytf-fill {", 1)[1].split("}", 1)[0]
        self.assertIn("grid-template-columns:", fill_css)
        metrics = html.split('class="yhat-tf-arm-metrics yhat-tf-bt-metrics"', 1)[1].split("<pre", 1)[0]
        for label in ("累计收益", "超额", "最大回撤", "决策收益", "准确率", "命中率", "窗口"):
            self.assertIn(label, metrics)
        self.assertNotIn("换手", html)
        self.assertNotIn("交易日", metrics)
        self.assertIn("gap: 24px;", style.split(".yhat-tf-backtest-actions {", 1)[1].split("}", 1)[0])
        self.assertNotIn("tier_a_only", script)
        self.assertIn("打分全池", script)
        self.assertNotIn("离线回测进程已退出", script)
        self.assertIn('deleteTitle: "删除该记录"', script)
        self.assertNotIn(
            "renderVersionMenu(menu, runs, btn.dataset.btValue, { deletable: false })",
            script,
        )
        self.assertNotIn("股票池按当前观察池重取", html)


def _mix_spec(head, pack, backend, factory, **kwargs):
    spec = _spec(head, pack, backend=backend, **kwargs)
    spec["factory"] = factory
    return spec


def _main_combo():
    return [
        _mix_spec("oo", "aoo_full", "ridge", "R"),
        _mix_spec("tc", "atc_full", "ridge", "R"),
        _mix_spec("oo_rank", "aoo_core", "lightgbm", "L"),
        _mix_spec("tc_rank", "atc_full", "lightgbm", "L"),
    ]


class TestMixBacktest(unittest.TestCase):
    def test_main_combo_keeps_raw_rank_blend(self):
        suite = check_mix(_main_combo())
        self.assertEqual(suite["rank_blend"], "raw")
        self.assertEqual(suite["picks"], "oo R · tc R · oo_rank L · tc_rank L")
        self.assertEqual(suite["factories"]["oo"], "R")
        self.assertEqual(suite["factories"]["oo_rank"], "L")

    def test_core_and_full_share_a_family(self):
        suite = check_mix(_main_combo())
        self.assertEqual(suite["window_start"], "2024-01-02")
        self.assertNotEqual(
            suite["specs"]["oo"]["meta"]["factor_pack"],
            suite["specs"]["oo_rank"]["meta"]["factor_pack"],
        )

    def test_cross_family_rejected(self):
        specs = _main_combo()
        specs[2] = _mix_spec("oo_rank", "atc_full", "lightgbm", "L")
        with self.assertRaises(ValueError):
            check_mix(specs)

    def test_different_rank_backends_use_day_rank(self):
        specs = _main_combo()
        specs[3] = _mix_spec("tc_rank", "atc_full", "ridge", "R")
        suite = check_mix(specs)
        self.assertEqual(suite["rank_blend"], "day_rank")

    def test_transformer_seq_only_compared_among_transformer_heads(self):
        specs = [
            _mix_spec("oo", "aoo_full", "transformer", "T", sample=10.0),
            _mix_spec("tc", "atc_full", "ridge", "R", sample=100.0),
            _mix_spec("oo_rank", "aoo_full", "lightgbm", "L", sample=100.0),
            _mix_spec("tc_rank", "atc_full", "lightgbm", "L", sample=100.0),
        ]
        specs[0]["fit_params"]["seq_len"] = 20
        suite = check_mix(specs)
        self.assertEqual(suite["rank_blend"], "raw")
        specs[2] = _mix_spec("oo_rank", "aoo_full", "transformer", "T", sample=10.0)
        specs[2]["fit_params"]["seq_len"] = 32
        with self.assertRaises(ValueError):
            check_mix(specs)

    def test_suite_still_rejects_mixed_backends(self):
        with self.assertRaises(ValueError):
            check_suite(_main_combo())

    def test_raw_blend_keeps_scores_and_writes_ordinals(self):
        day = {
            "scored": [
                {"stock_code": "B", "y_oo_rank_score": 1.0, "y_tc_rank_score": 3.0},
                {"stock_code": "A", "y_oo_rank_score": 2.0, "y_tc_rank_score": 1.0},
            ]
        }
        stamp_day_ranks([day], blend="raw")
        by = {row["stock_code"]: row for row in day["scored"]}
        self.assertEqual(by["A"]["y_oo_rank"], 1)
        self.assertEqual(by["B"]["y_oo_rank"], 2)
        self.assertEqual(by["A"]["y_oo_rank_score"], 2.0)
        self.assertEqual(by["B"]["y_tc_rank"], 1)
        self.assertEqual(by["A"]["y_tc_rank"], 2)

    def test_day_rank_blend_replaces_scores_with_percentile(self):
        day = {
            "scored": [
                {"stock_code": "B", "y_oo_rank_score": 1.0, "y_tc_rank_score": 9.0},
                {"stock_code": "A", "y_oo_rank_score": 5.0, "y_tc_rank_score": 1.0},
            ]
        }
        stamp_day_ranks([day], blend="day_rank")
        by = {row["stock_code"]: row for row in day["scored"]}
        self.assertEqual(by["A"]["y_oo_rank"], 1)
        self.assertEqual(by["A"]["y_oo_rank_score"], 1.0)
        self.assertEqual(by["B"]["y_oo_rank_score"], 0.5)
        self.assertEqual(by["B"]["y_tc_rank_score"], 1.0)
        self.assertEqual(by["A"]["y_tc_rank_score"], 0.5)

    def test_mix_run_records_the_combo(self):
        suite = check_mix(_main_combo())
        report = run_factory_backtest(
            "BT",
            suite["sources"],
            days=[
                _day("2024-01-02", 2.0, 2.0, 10.0),
                _day("2024-01-03", -1.0, -1.0, 12.0),
            ],
            suite=suite,
            cfg=_cfg(),
        )
        self.assertEqual(report["factory"], "BT")
        self.assertEqual(report["rank_blend"], "raw")
        self.assertEqual(report["fill_clock"], "09:30")
        self.assertIn("oo R", report["picks"])

    def test_console_records_version_marks_and_rules(self):
        suite = check_mix(_main_combo())
        suite["specs"]["oo"]["snapshot"] = {"started_at": "2026-11-18T14:02:01"}
        cfg = dict(_cfg())
        cfg["feature_set"] = "hs300"
        cfg["y_oo_gt0"] = True
        cfg["oo_rank_strong_max"] = None
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run_factory_backtest(
                "BT",
                suite["sources"],
                days=[
                    _day("2024-01-02", 2.0, 2.0, 10.0),
                    _day("2024-01-03", -1.0, -1.0, 12.0),
                ],
                suite=suite,
                cfg=cfg,
            )
        text = buf.getvalue()
        self.assertIn("组合 oo R 11-18 14:02 · tc R tc-v1 · oo_rank L oo_rank-v1 · tc_rank L tc_rank-v1 · 买序 原始分", text)
        self.assertIn("w_oo 0.60", text)
        self.assertIn("w_τc 0.40", text)
        self.assertIn("入场 fuse 0.10% 名次 6 金额 10000", text)
        self.assertIn("强档 fuse 5.00% 名次 关 金额 10000", text)
        self.assertIn("本金 200000", text)
        self.assertIn("y_*>0 开", text)
        self.assertIn("形态闸 关", text)
        self.assertIn("物化 沪深300", text)
        self.assertNotIn("组合 oo R · tc R · oo_rank L · tc_rank L", text)

    def test_mix_job_stays_out_of_the_transformer_dir(self):
        suite = check_mix(_main_combo())
        calls = []

        class _Proc:
            pid = 5151

        def _popen(cmd, **kwargs):
            calls.append(cmd)
            return _Proc()

        with tempfile.TemporaryDirectory() as td, mock.patch(
            "core.paths.LIVE_DIR", os.path.join(td, "live")
        ), mock.patch(
            "core.research.yhat_factory_backtest.load_mix", return_value=suite
        ), mock.patch(
            "core.research.yhat_factory_backtest._pid_alive", return_value=True
        ), mock.patch(
            "core.research.yhat_factory_backtest.subprocess.Popen", side_effect=_popen
        ):
            out = start_factory_backtest("BT", suite["sources"])
            job_path = os.path.join(td, "live", "yhat_bt", "backtest_job.json")
            with open(job_path, encoding="utf-8") as f:
                job = json.load(f)
        self.assertTrue(out["success"])
        self.assertEqual(job["factory"], "BT")
        self.assertEqual(job["rank_blend"], "raw")
        self.assertFalse(os.path.exists(os.path.join(td, "live", "yhat_t")))
        cmd = calls[0]
        self.assertIn("BT", cmd)
        self.assertIn("run_yhat_factory_backtest.py", " ".join(cmd))
        payload = json.loads(cmd[cmd.index("--versions") + 1])
        self.assertEqual(payload["oo"]["factory"], "R")
        self.assertEqual(payload["oo_rank"]["factory"], "L")

    def test_page_is_palette_only(self):
        from web.page_html import clear_html_cache, render_tool_html

        clear_html_cache()
        html = render_tool_html("yhat_bt")
        self.assertIn("<title>QuantLab · 回测中心</title>", html)
        self.assertNotIn('id="yhat-bt-main"', html)
        self.assertNotIn("main 组合", html)
        self.assertNotIn("<details", html)
        self.assertNotIn('href="/yhat-bt"', html)
        self.assertIn('href="/yhat-r"', html)


if __name__ == "__main__":
    unittest.main()
