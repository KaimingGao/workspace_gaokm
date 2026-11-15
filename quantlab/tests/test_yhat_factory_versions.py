"""模型工厂跑次版本：归档、覆盖、裁剪、L/T 隔离。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from core.research.yhat_factory_versions import (
    KEEP,
    archive_factory_run,
    delete_factory_version,
    list_factory_versions,
    preview_factory_version,
)
from core.research.yhat_tf_desk import factory_scope


def _job(run_id: str, pid: int = 7, **extra):
    job = {
        "run_id": run_id,
        "pid": pid,
        "head": "oo",
        "params": {
            "pool_n": 800,
            "lookback": 900,
            "holdout_trading_days": 60,
            "t_sample_pct": 10,
            "full_aoo": False,
            "loss": "smooth_l1",
        },
        "started_at": "2026-11-16T01:02:03",
        "finished_at": "2026-11-16T01:08:00",
        "cli": "python holdout.py --head oo",
    }
    job.update(extra)
    return job


def _last(**extra):
    last = {
        "action": "holdout_result",
        "head": "oo",
        "metrics": {
            "cs_rank_ic": 0.042,
            "arm": "T",
            "arms": {"T": {"success": True, "cs_rank_ic": 0.042}},
            "splits": {"T": {"n_train": 10, "n_test": 4, "eval_start": "2026-01-02"}},
        },
        "parity": {"formal": False, "eval_shared": True},
        "eval_universe": {"eval_shared": True, "eval_start": "2026-01-02", "n_shared": 4},
        "message": "Holdout 完成",
        "updated_at": "2026-11-16T01:08:00",
    }
    last.update(extra)
    return last


class TestYhatFactoryVersions(unittest.TestCase):
    def test_archive_list_preview_and_same_id_overwrites(self):
        with tempfile.TemporaryDirectory() as td:
            last_path = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last_path
            ):
                with factory_scope("T"):
                    with open(os.path.join(td, "oo_fit.log"), "w", encoding="utf-8") as f:
                        f.write("ArmT rankIC=0.0420\n")
                    vid = archive_factory_run(
                        "oo", job=_job("oo-20261116T010203"), last=_last(), status="done"
                    )
                    self.assertEqual(vid, "oo-20261116T010203-p7")
                    again = archive_factory_run(
                        "oo",
                        job=_job("oo-20261116T010203"),
                        last=_last(message="第二次"),
                        status="done",
                    )
                    self.assertEqual(again, vid)
                listed = list_factory_versions("oo", factory="T")
                self.assertTrue(listed["success"])
                self.assertEqual(len(listed["items"]), 1)
                self.assertIn("RankIC 0.042", listed["items"][0]["label"])
                self.assertIn("10%", listed["items"][0]["label"])
                self.assertIn("core", listed["items"][0]["label"])
                preview = preview_factory_version("oo", vid, factory="T")
                self.assertTrue(preview["success"])
                self.assertEqual(preview["fit_params"]["lookback"], 900)
                self.assertEqual(preview["metrics"]["cs_rank_ic"], 0.042)
                self.assertIn("eval_start", preview["eval_universe"])
                self.assertIn("rankIC=0.0420", preview["log_tail"])
                self.assertEqual(preview["message"], "第二次")
                self.assertFalse(preview["live_allowed"])
                with open(os.path.join(td, "oo_fit.log"), "w", encoding="utf-8") as f:
                    f.write("下一轮\n")
                kept = preview_factory_version("oo", vid, factory="T")
                self.assertIn("rankIC=0.0420", kept["log_tail"])
                self.assertNotIn("下一轮", kept["log_tail"])

    def test_keep_drops_oldest(self):
        with tempfile.TemporaryDirectory() as td:
            last_path = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last_path
            ):
                with factory_scope("T"):
                    first = None
                    for i in range(KEEP + 1):
                        vid = archive_factory_run(
                            "oo",
                            job=_job(f"oo-run-{i:02d}", pid=i + 1),
                            last=_last(),
                            status="done",
                        )
                        if first is None:
                            first = vid
                listed = list_factory_versions("oo", factory="T")
                ids = [it["id"] for it in listed["items"]]
                self.assertEqual(len(ids), KEEP)
                self.assertNotIn(first, ids)
                self.assertFalse(
                    os.path.isfile(os.path.join(td, "versions", "oo", f"{first}.json"))
                )
                self.assertEqual(ids[0], f"oo-run-{KEEP:02d}-p{KEEP + 1}")

    def test_factories_do_not_share_versions(self):
        with tempfile.TemporaryDirectory() as td_l, tempfile.TemporaryDirectory() as td_t:
            with mock.patch("core.research.yhat_tf_desk.YHAT_L_DIR", td_l), mock.patch(
                "core.research.yhat_tf_desk.YHAT_L_LAST_PATH",
                os.path.join(td_l, "last_status.json"),
            ), mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td_t), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH",
                os.path.join(td_t, "last_status.json"),
            ):
                with factory_scope("L"):
                    vid = archive_factory_run(
                        "oo",
                        job=_job("oo-l", pid=3, params={"loss": "l2", "pool_n": 100}),
                        last=_last(metrics={"cs_rank_ic": 0.2, "arm": "L"}),
                        status="done",
                    )
                self.assertTrue(vid)
                self.assertTrue(os.path.isfile(os.path.join(td_l, "versions", "oo", f"{vid}.json")))
                self.assertFalse(os.path.isdir(os.path.join(td_t, "versions")))
                listed = list_factory_versions("oo", factory="L")
                self.assertIn("l2", listed["items"][0]["label"])
                self.assertNotIn("10%", listed["items"][0]["label"])
                empty = list_factory_versions("oo", factory="T")
                self.assertEqual(empty["items"], [])

    def test_preview_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH",
                os.path.join(td, "last_status.json"),
            ):
                bad = preview_factory_version("oo", "../oo_last", factory="T")
                self.assertFalse(bad["success"])
                self.assertEqual(bad["error"], "invalid run_id")
                missing = preview_factory_version("oo", "oo-missing-p1", factory="T")
                self.assertFalse(missing["success"])

    def test_delete_removes_one_snapshot_and_leaves_the_rest(self):
        with tempfile.TemporaryDirectory() as td:
            last_path = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last_path
            ):
                with factory_scope("T"):
                    with open(os.path.join(td, "oo_fit.log"), "w", encoding="utf-8") as f:
                        f.write("live log stays\n")
                    with open(last_path, "w", encoding="utf-8") as f:
                        json.dump({"action": "holdout_result"}, f)
                    keep = archive_factory_run(
                        "oo", job=_job("oo-keep", pid=1), last=_last(), status="done"
                    )
                    drop = archive_factory_run(
                        "oo", job=_job("oo-drop", pid=2), last=_last(), status="done"
                    )
                bad = delete_factory_version("oo", "../oo_last", factory="T")
                self.assertFalse(bad["success"])
                self.assertEqual(bad["error"], "invalid run_id")
                missing = delete_factory_version("oo", "oo-missing-p1", factory="T")
                self.assertFalse(missing["success"])
                self.assertEqual(missing["error"], "版本不存在")
                out = delete_factory_version("oo", drop, factory="T")
                self.assertTrue(out["success"])
                self.assertEqual([it["id"] for it in out["items"]], [keep])
                self.assertFalse(os.path.isfile(os.path.join(td, "versions", "oo", f"{drop}.json")))
                self.assertFalse(os.path.isfile(os.path.join(td, "versions", "oo", f"{drop}.log")))
                self.assertTrue(os.path.isfile(os.path.join(td, "versions", "oo", f"{keep}.json")))
                self.assertEqual(preview_factory_version("oo", drop, factory="T")["error"], "版本不存在")
                self.assertTrue(preview_factory_version("oo", keep, factory="T")["success"])
                with open(os.path.join(td, "oo_fit.log"), encoding="utf-8") as f:
                    self.assertEqual(f.read(), "live log stays\n")
                with open(last_path, encoding="utf-8") as f:
                    self.assertEqual(json.load(f)["action"], "holdout_result")

    def test_delete_one_factory_leaves_the_other(self):
        with tempfile.TemporaryDirectory() as td_l, tempfile.TemporaryDirectory() as td_t:
            with mock.patch("core.research.yhat_tf_desk.YHAT_L_DIR", td_l), mock.patch(
                "core.research.yhat_tf_desk.YHAT_L_LAST_PATH",
                os.path.join(td_l, "last_status.json"),
            ), mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td_t), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH",
                os.path.join(td_t, "last_status.json"),
            ):
                with factory_scope("L"):
                    vid_l = archive_factory_run(
                        "oo", job=_job("oo-same", pid=3), last=_last(), status="done"
                    )
                with factory_scope("T"):
                    vid_t = archive_factory_run(
                        "oo", job=_job("oo-same", pid=3), last=_last(), status="done"
                    )
                out = delete_factory_version("oo", vid_l, factory="L")
                self.assertTrue(out["success"])
                self.assertEqual(out["items"], [])
                self.assertFalse(os.path.isfile(os.path.join(td_l, "versions", "oo", f"{vid_l}.json")))
                self.assertTrue(preview_factory_version("oo", vid_t, factory="T")["success"])

    def test_failed_run_does_not_keep_previous_gate(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH",
                os.path.join(td, "last_status.json"),
            ):
                with factory_scope("T"):
                    vid = archive_factory_run(
                        "oo",
                        job=_job("oo-fail", pid=9),
                        last={
                            "action": "holdout_failed",
                            "metrics": None,
                            "hard_gate_vs_arm_L": {"T": {"pass": True}},
                            "parity": {"formal": True},
                            "message": "启动即崩",
                        },
                        status="error",
                    )
                preview = preview_factory_version("oo", vid, factory="T")
                self.assertIsNone(preview["metrics"])
                self.assertIsNone(preview["hard_gate_vs_arm_L"])
                self.assertFalse(preview["badges"]["formal"])
                self.assertTrue(preview["badges"]["failed"])
                label = list_factory_versions("oo", factory="T")["items"][0]["label"]
                self.assertIn("失败", label)

    def test_ingest_archives_and_skip_does_not(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH",
                os.path.join(td, "last_status.json"),
            ):
                from core.research.yhat_tf_desk import ingest_holdout_summary

                with open(os.path.join(td, "oo_job.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "status": "starting",
                            "running": True,
                            "pid": 0,
                            "head": "oo",
                            "run_id": "oo-skip",
                        },
                        f,
                    )
                skipped = ingest_holdout_summary(
                    "oo",
                    {"arm_T": {"success": True, "cs_rank_ic": 0.2}},
                    factory="T",
                )
                self.assertTrue(skipped.get("skipped"))
                self.assertFalse(os.path.isdir(os.path.join(td, "versions")))

                with open(os.path.join(td, "oo_job.json"), "w", encoding="utf-8") as f:
                    json.dump(_job("oo-ok", pid=4), f)
                out = ingest_holdout_summary(
                    "oo",
                    {
                        "arm_T": {"success": True, "cs_rank_ic": 0.11, "cs_icir": 0.4},
                        "eval_universe": {"eval_shared": True, "eval_start": "2026-02-01"},
                        "factory": "T",
                    },
                    factory="T",
                )
                self.assertTrue(out.get("success"))
                self.assertEqual(out.get("version_id"), "oo-ok-p4")
                self.assertTrue(
                    os.path.isfile(os.path.join(td, "versions", "oo", "oo-ok-p4.json"))
                )

    def test_mark_fit_failed_archives(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH",
                os.path.join(td, "last_status.json"),
            ):
                from core.research.yhat_tf_desk import mark_fit_failed

                with open(os.path.join(td, "oo_job.json"), "w", encoding="utf-8") as f:
                    json.dump(_job("oo-boom", pid=8), f)
                out = mark_fit_failed("oo", "boom", factory="T")
                self.assertTrue(out.get("success"))
                self.assertEqual(out.get("version_id"), "oo-boom-p8")
                preview = preview_factory_version("oo", out["version_id"], factory="T")
                self.assertEqual(preview["status"], "error")
                self.assertIn("boom", preview["message"])


if __name__ == "__main__":
    unittest.main()
