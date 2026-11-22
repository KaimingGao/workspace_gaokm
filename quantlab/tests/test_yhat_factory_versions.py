"""模型工厂跑次版本：归档、覆盖、裁剪、L/T 隔离。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from core.research.yhat_factory_versions import (
    KEEP,
    _shown_label,
    archive_factory_run,
    delete_factory_version,
    enable_factory_version,
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
            "loss": "mse",
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
            "mse": 6.5,
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
    def test_holdout_zero_is_live_only(self):
        with tempfile.TemporaryDirectory() as td:
            last_path = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last_path
            ):
                with factory_scope("T"):
                    archive_factory_run(
                        "oo", job=_job("oo-research"), last=_last(), status="done"
                    )
                    live_job = _job("oo-live")
                    live_job["params"] = dict(live_job["params"], holdout_trading_days=0)
                    archive_factory_run("oo", job=live_job, last=_last(), status="done")
                research = list_factory_versions("oo", factory="T", suite="research")
                live = list_factory_versions("oo", factory="T", suite="live")
                both = list_factory_versions("oo", factory="T")
                denied = enable_factory_version(
                    "oo", "oo-research-p7", suite="live", factory="T"
                )
                turned = enable_factory_version(
                    "oo", "oo-research-p7", suite="research", factory="T"
                )
                research_on = list_factory_versions("oo", factory="T", suite="research")
                live_on = list_factory_versions("oo", factory="T", suite="live")
        self.assertEqual([it["id"] for it in research["items"]], ["oo-research-p7"])
        self.assertEqual(research["items"][0]["holdout_trading_days"], 60)
        self.assertEqual([it["id"] for it in live["items"]], ["oo-live-p7"])
        self.assertEqual(live["items"][0]["holdout_trading_days"], 0)
        self.assertEqual({it["id"] for it in both["items"]}, {"oo-research-p7", "oo-live-p7"})
        self.assertFalse(denied["success"])
        self.assertTrue(turned["success"])
        self.assertEqual(research_on["enabled_id"], "oo-research-p7")
        self.assertEqual(live_on["enabled_id"], "")

    def test_pretrain_archive_uses_mse(self):
        with tempfile.TemporaryDirectory() as td:
            last_path = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last_path
            ):
                job = _job("oo-pre")
                job["params"] = dict(job["params"], train_stage="pretrain", feature_set="pool")
                with factory_scope("T"):
                    archive_factory_run("oo", job=job, last=_last(), status="done")
                listed = list_factory_versions("oo", factory="T")
        label = listed["items"][0]["label"]
        self.assertIn("MSE 6.500", label)
        self.assertNotIn("RankIC", label)
        self.assertIn("预训练", label)

    def test_live_suite_stays_out_of_research_list(self):
        with tempfile.TemporaryDirectory() as td:
            last_path = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last_path
            ):
                with factory_scope("T"):
                    archive_factory_run(
                        "oo",
                        job=_job("oo-exec", suite="live"),
                        last=_last(),
                        status="done",
                    )
                research = list_factory_versions("oo", factory="T", suite="research")
                live = list_factory_versions("oo", factory="T", suite="live")
                denied = enable_factory_version(
                    "oo", "oo-exec-p7", suite="research", factory="T"
                )
        self.assertEqual(research["items"], [])
        self.assertEqual([it["id"] for it in live["items"]], ["oo-exec-p7"])
        self.assertEqual(live["items"][0]["suite"], "live")
        self.assertEqual(live["items"][0]["holdout_trading_days"], 0)
        self.assertFalse(denied["success"])

    def test_shown_label_keeps_time_rankic_factory(self):
        self.assertEqual(
            _shown_label("11-19 01:36 · RankIC 0.010 · λ1", "R"),
            "11-19 01:36 · RankIC 0.010 · R",
        )
        self.assertEqual(
            _shown_label("11-19 02:14 · RankIC 0.098 · lambdarank", "L"),
            "11-19 02:14 · RankIC 0.098 · L",
        )
        self.assertEqual(
            _shown_label("11-18 06:15 · RankIC 0.083 · 100% · core", "T"),
            "11-18 06:15 · RankIC 0.083 · T",
        )
        self.assertEqual(
            _shown_label("11-16 01:02 · RankIC 0.042 · 10% · full · l2", "L"),
            "11-16 01:02 · RankIC 0.042 · L",
        )

    def test_rank_head_label_keeps_rank_ic(self):
        with tempfile.TemporaryDirectory() as td:
            last_path = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last_path
            ):
                job = _job("oo_rank-1")
                job["head"] = "oo_rank"
                with factory_scope("L"):
                    archive_factory_run(
                        "oo_rank",
                        job=job,
                        last=_last(metrics={"cs_rank_ic": 0.098, "mse": 9.0, "arm": "L"}),
                        status="done",
                    )
                listed = list_factory_versions("oo_rank", factory="L")
        label = listed["items"][0]["label"]
        self.assertIn("RankIC 0.098", label)
        self.assertNotIn("MSE", label)
        self.assertTrue(label.endswith(" · L"))

    def test_old_regression_label_swaps_rank_ic_for_mse(self):
        with tempfile.TemporaryDirectory() as td:
            last_path = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last_path
            ):
                last = _last()
                last["metrics"] = {"cs_rank_ic": 0.042, "arm": "T"}
                with factory_scope("T"):
                    vid = archive_factory_run("oo", job=_job("oo-old"), last=last, status="done")
                    snap = os.path.join(td, "versions", "oo", f"{vid}.json")
                    with open(snap, encoding="utf-8") as handle:
                        doc = json.load(handle)
                    doc["metrics"]["mse"] = 6.641899
                    doc["badges"].pop("mse", None)
                    with open(snap, "w", encoding="utf-8") as handle:
                        json.dump(doc, handle)
                    index = os.path.join(td, "versions", "oo", "index.json")
                    with open(index, encoding="utf-8") as handle:
                        bag = json.load(handle)
                    bag["items"][0]["label"] = "11-16 01:02 · RankIC 0.042"
                    bag["items"][0]["badges"].pop("mse", None)
                    with open(index, "w", encoding="utf-8") as handle:
                        json.dump(bag, handle)
                listed = list_factory_versions("oo", factory="T")
                bare = _last()
                bare["metrics"] = {"cs_rank_ic": 0.01, "arm": "T"}
                with factory_scope("T"):
                    bare_id = archive_factory_run("tc", job=_job("tc-bare"), last=bare, status="done")
                    index = os.path.join(td, "versions", "tc", "index.json")
                    with open(index, encoding="utf-8") as handle:
                        bag = json.load(handle)
                    bag["items"][0]["label"] = "11-16 01:02 · RankIC 0.010"
                    with open(index, "w", encoding="utf-8") as handle:
                        json.dump(bag, handle)
                kept = list_factory_versions("tc", factory="T")
        self.assertEqual(bare_id, "tc-bare-p7")
        self.assertEqual(
            listed["items"][0]["label"],
            "11-16 01:02 · MSE 6.642 · T",
        )
        self.assertEqual(kept["items"][0]["label"], "11-16 01:02 · RankIC 0.010 · T")

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
                self.assertIn("MSE 6.500", listed["items"][0]["label"])
                self.assertNotIn("RankIC", listed["items"][0]["label"])
                self.assertTrue(listed["items"][0]["label"].endswith(" · T"))
                self.assertNotIn("10%", listed["items"][0]["label"])
                self.assertNotIn("core", listed["items"][0]["label"])
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
                self.assertTrue(listed["items"][0]["label"].endswith(" · L"))
                self.assertNotIn("l2", listed["items"][0]["label"])
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

    def test_deleted_failure_is_not_shown_as_last_fit(self):
        with tempfile.TemporaryDirectory() as td:
            last_path = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last_path
            ):
                from core.research.yhat_tf_desk import head_status, mark_fit_failed

                kept = _last()
                kept["metrics"] = {
                    "cs_rank_ic": 0.11,
                    "arm": "T",
                    "arms": {"T": {"success": True, "cs_rank_ic": 0.11}},
                }
                with factory_scope("T"):
                    archive_factory_run(
                        "oo",
                        job=_job("oo-keep", pid=1),
                        last=kept,
                        status="done",
                    )
                    with open(os.path.join(td, "oo_job.json"), "w", encoding="utf-8") as f:
                        json.dump(_job("oo-fail", pid=0), f)
                    failed = mark_fit_failed("oo", "拟合进程被终止", factory="T")
                self.assertEqual(failed.get("version_id"), "oo-fail-p0")
                while_listed = head_status("oo")
                self.assertEqual(while_listed["last"]["action"], "holdout_failed")
                self.assertEqual(while_listed["job"]["status"], "error")

                # 删掉的是另一条成功版：这次失败还在列表里，概览仍是失败
                other = archive_factory_run(
                    "oo",
                    job=_job("oo-other", pid=2),
                    last=_last(),
                    status="done",
                )
                # archive 插到列表头，但上次拟合仍是失败跑次
                delete_factory_version("oo", other, factory="T")
                still_failed = head_status("oo")
                self.assertEqual(still_failed["last"]["action"], "holdout_failed")

                delete_factory_version("oo", "oo-fail-p0", factory="T")
                shown = head_status("oo")
                self.assertEqual(shown["last"]["action"], "holdout_result")
                self.assertEqual(shown["job"]["status"], "done")
                self.assertEqual(shown["last"]["metrics"]["cs_rank_ic"], 0.11)
                self.assertNotEqual(shown["job"]["status"], "error")
                with open(os.path.join(td, "oo_last.json"), encoding="utf-8") as f:
                    self.assertEqual(json.load(f)["action"], "holdout_failed")
                with open(os.path.join(td, "oo_job.json"), encoding="utf-8") as f:
                    self.assertEqual(json.load(f)["status"], "error")

                delete_factory_version("oo", "oo-keep-p1", factory="T")
                empty = head_status("oo")
                self.assertIsNone(empty["last"])
                self.assertIsNone(empty["job"])

    def test_deleted_success_log_follows_remaining_version(self):
        with tempfile.TemporaryDirectory() as td:
            last_path = os.path.join(td, "last_status.json")
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH", last_path
            ):
                from core.research.yhat_tf_desk import head_status

                kept = _last()
                kept["metrics"] = {
                    "cs_rank_ic": 0.11,
                    "arm": "T",
                    "arms": {"T": {"success": True, "cs_rank_ic": 0.11}},
                }
                log_path = os.path.join(td, "oo_fit.log")
                with factory_scope("T"):
                    with open(log_path, "w", encoding="utf-8") as f:
                        f.write("==== fit start kept run=oo-keep ====\n")
                    archive_factory_run(
                        "oo",
                        job=_job("oo-keep", pid=1),
                        last=kept,
                        status="done",
                    )
                    with open(log_path, "w", encoding="utf-8") as f:
                        f.write(
                            "==== fit start 2026-11-17T23:36:55 head=oo run=oo-gone ====\n"
                        )
                    gone = _job("oo-gone", pid=9, status="done", running=False)
                    gone_last = _last()
                    gone_last["metrics"] = {
                        "cs_rank_ic": 0.99,
                        "arm": "T",
                        "arms": {"T": {"success": True, "cs_rank_ic": 0.99}},
                    }
                    with open(os.path.join(td, "oo_job.json"), "w", encoding="utf-8") as f:
                        json.dump(gone, f)
                    with open(os.path.join(td, "oo_last.json"), "w", encoding="utf-8") as f:
                        json.dump(gone_last, f)
                    archive_factory_run("oo", job=gone, last=gone_last, status="done")
                    delete_factory_version("oo", "oo-gone-p9", factory="T")
                shown = head_status("oo")
                self.assertEqual(shown["last"]["action"], "holdout_result")
                self.assertEqual(shown["last"]["metrics"]["cs_rank_ic"], 0.11)
                self.assertIn("oo-keep", shown["log_tail"])
                self.assertNotIn("oo-gone", shown["log_tail"])
                with open(os.path.join(td, "oo_job.json"), encoding="utf-8") as f:
                    self.assertEqual(json.load(f)["run_id"], "oo-gone")
                with open(log_path, encoding="utf-8") as f:
                    self.assertIn("oo-gone", f.read())

                delete_factory_version("oo", "oo-keep-p1", factory="T")
                empty = head_status("oo")
                self.assertIsNone(empty["last"])
                self.assertEqual(empty["log_tail"], "")

    def test_failure_without_version_index_stays_visible(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("core.research.yhat_tf_desk.YHAT_TF_DIR", td), mock.patch(
                "core.research.yhat_tf_desk.YHAT_TF_LAST_PATH",
                os.path.join(td, "last_status.json"),
            ):
                from core.research.yhat_tf_desk import head_status

                with open(os.path.join(td, "oo_job.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "head": "oo",
                            "run_id": "oo-unlisted",
                            "pid": 0,
                            "status": "error",
                            "running": False,
                            "error": "启动即崩",
                        },
                        f,
                    )
                with open(os.path.join(td, "oo_last.json"), "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "head": "oo",
                            "action": "holdout_failed",
                            "metrics": None,
                            "message": "启动即崩",
                        },
                        f,
                    )
                shown = head_status("oo")
                self.assertEqual(shown["last"]["action"], "holdout_failed")
                self.assertEqual(shown["job"]["status"], "error")


if __name__ == "__main__":
    unittest.main()
