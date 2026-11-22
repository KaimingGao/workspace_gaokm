"""Arm·T 拟合曲线：jsonl 读写、版本归档、状态回看。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from core.research.yhat_factory_versions import (
    archive_factory_run,
    delete_factory_version,
    preview_factory_version,
)
from core.research.yhat_tf_desk import factory_scope
from core.research.yhat_transformer.fit_curve import (
    append_fit_curve,
    load_fit_curve,
    reset_fit_curve,
)


def _line(row):
    return json.dumps(row, separators=(",", ":"))


class TestFitCurveFile(unittest.TestCase):
    def test_roundtrip_skips_torn_line_and_keeps_last_step(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "oo_fit.curve.jsonl")
            reset_fit_curve(path)
            self.assertEqual(os.path.getsize(path), 0)
            self.assertIsNone(load_fit_curve(path))
            append_fit_curve({"k": "s", "step": 1, "avg": 1.5, "loss": float("nan")}, path)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write('{"k":"s","step":2,"avg":\n')
                for step in range(2, 1002):
                    handle.write(_line({"k": "s", "step": step, "ep": 1, "avg": 1 / step}) + "\n")
                handle.write(
                    _line({"k": "e", "ep": 1, "step": 1001, "train_loss": 0.2, "rank_ic": 0.03, "mse": 1.25, "slope": 0.8})
                    + "\n"
                )
            curve = load_fit_curve(path)
        self.assertLessEqual(len(curve["steps"]), 800)
        self.assertGreater(len(curve["steps"]), 100)
        self.assertEqual(curve["steps"][0]["step"], 1)
        self.assertNotIn("loss", curve["steps"][0])
        self.assertEqual(curve["steps"][-1]["step"], 1001)
        self.assertEqual(curve["epochs"], [{"ep": 1, "rank_ic": 0.03, "mse": 1.25}])
        self.assertIsNone(load_fit_curve(os.path.join(td, "missing.curve.jsonl")))

    def test_append_without_path_is_quiet(self):
        with mock.patch.dict(os.environ, {"YHAT_FIT_CURVE": ""}, clear=False):
            append_fit_curve({"k": "s", "step": 1, "avg": 1})


class TestFitCurveArchive(unittest.TestCase):
    def test_version_keeps_its_own_curve(self):
        with tempfile.TemporaryDirectory() as td:
            last_path = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last_path
            ):
                from core.research.yhat_tf_desk import head_status

                curve = os.path.join(td, "oo_fit.curve.jsonl")
                with factory_scope("T"):
                    with open(curve, "w", encoding="utf-8") as handle:
                        handle.write(_line({"k": "s", "step": 4, "avg": 0.4}) + "\n")
                        handle.write(_line({"k": "e", "ep": 1, "rank_ic": 0.11, "mse": 0.5, "slope": 0.8}) + "\n")
                    archive_factory_run(
                        "oo",
                        job={
                            "run_id": "oo-keep",
                            "pid": 1,
                            "params": {"holdout_trading_days": 60},
                            "started_at": "2026-11-22T01:00:00",
                        },
                        last={
                            "action": "holdout_result",
                            "metrics": {"cs_rank_ic": 0.11, "arm": "T"},
                            "updated_at": "2026-11-22T01:02:00",
                        },
                        status="done",
                    )
                    with open(curve, "w", encoding="utf-8") as handle:
                        handle.write(_line({"k": "s", "step": 9, "avg": 0.9}) + "\n")
                    archive_factory_run(
                        "oo",
                        job={
                            "run_id": "oo-gone",
                            "pid": 9,
                            "params": {"holdout_trading_days": 60},
                            "started_at": "2026-11-22T02:00:00",
                            "status": "done",
                            "running": False,
                        },
                        last={
                            "action": "holdout_result",
                            "metrics": {"cs_rank_ic": 0.99, "arm": "T"},
                            "updated_at": "2026-11-22T02:02:00",
                        },
                        status="done",
                    )
                    with open(os.path.join(td, "oo_job.json"), "w", encoding="utf-8") as handle:
                        json.dump(
                            {"run_id": "oo-gone", "pid": 9, "status": "done", "running": False},
                            handle,
                        )
                    with open(os.path.join(td, "oo_last.json"), "w", encoding="utf-8") as handle:
                        json.dump(
                            {
                                "action": "holdout_result",
                                "metrics": {"cs_rank_ic": 0.99, "arm": "T"},
                            },
                            handle,
                        )
                    kept = preview_factory_version("oo", "oo-keep-p1", factory="T")
                    delete_factory_version("oo", "oo-gone-p9", factory="T")
                    with open(curve, encoding="utf-8") as handle:
                        live = handle.read()
                shown = head_status("oo")
                self.assertFalse(
                    os.path.isfile(os.path.join(td, "versions", "oo", "oo-gone-p9.curve.jsonl"))
                )
        self.assertIn('"step":9', live)
        self.assertEqual(kept["fit_curve"]["steps"][0]["avg"], 0.4)
        self.assertEqual(kept["fit_curve"]["epochs"][0]["rank_ic"], 0.11)
        self.assertEqual(kept["fit_curve"]["epochs"][0]["mse"], 0.5)
        self.assertNotIn("slope", kept["fit_curve"]["epochs"][0])
        self.assertEqual(shown["fit_curve"]["steps"][0]["step"], 4)
        self.assertEqual(shown["last"]["metrics"]["cs_rank_ic"], 0.11)
