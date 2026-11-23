"""工厂版本权重：临时目录、归档改名、加载与删除。"""

from __future__ import annotations

import base64
import importlib.util
import os
import tempfile
import unittest
import unittest.mock as mock

from core.research.yhat_factory_versions import (
    KEEP,
    archive_factory_run,
    delete_factory_version,
)
from core.research.yhat_factory_weights import (
    ENV_RUN_ID,
    load_factory_weights,
    pending_weights_dir,
    save_lightgbm_pending,
    version_weights_dir,
)
from core.research.yhat_tf_desk import factory_scope
from core.research.yhat_transformer.train import _clip_inplace_rows

from tests.conftest import _yhat_factory_job as _job
from tests.conftest import _yhat_factory_last as _last


def _patch_store(td: str):
    return mock.patch.multiple(
        "core.research.yhat_tf_desk",
        YHAT_TF_DIR=td,
        YHAT_TF_LAST_PATH=os.path.join(td, "last_status.json"),
    )


class TestFactoryWeights(unittest.TestCase):
    def test_archive_publishes_matching_pending_and_load_reads_it(self):
        pid = os.getpid()
        run_id = "oo-20261116T010203"
        with tempfile.TemporaryDirectory() as td, _patch_store(td):
            with factory_scope("T"):
                os.environ[ENV_RUN_ID] = run_id
                try:
                    self.assertTrue(
                        save_lightgbm_pending(
                            "tree\n",
                            {"head": "oo", "factor_names": ["gap"], "solver": "lightgbm"},
                        )
                    )
                    vid = archive_factory_run(
                        "oo",
                        job=_job(run_id, pid=pid),
                        last=_last(),
                        status="done",
                    )
                finally:
                    os.environ.pop(ENV_RUN_ID, None)
            self.assertEqual(vid, f"{run_id}-p{pid}")
            self.assertFalse(os.path.isdir(pending_weights_dir("oo")))
            self.assertTrue(os.path.isdir(version_weights_dir("oo", vid)))
            loaded = load_factory_weights("oo", vid, factory="T")
            self.assertTrue(loaded["success"])
            self.assertEqual(loaded["model_text"], "tree\n")
            self.assertEqual(loaded["meta"]["factor_names"], ["gap"])
            self.assertEqual(loaded["weights_ref"], f"{vid}.weights")

    def test_mismatched_pid_does_not_publish(self):
        run_id = "oo-20261116T010203"
        with tempfile.TemporaryDirectory() as td, _patch_store(td):
            with factory_scope("T"):
                os.environ[ENV_RUN_ID] = run_id
                try:
                    save_lightgbm_pending("tree\n", {"head": "oo"})
                    vid = archive_factory_run(
                        "oo",
                        job=_job(run_id, pid=os.getpid() + 1),
                        last=_last(),
                        status="done",
                    )
                finally:
                    os.environ.pop(ENV_RUN_ID, None)
            self.assertFalse(os.path.isdir(pending_weights_dir("oo")))
            self.assertFalse(os.path.isdir(version_weights_dir("oo", vid)))
            loaded = load_factory_weights("oo", vid, factory="T")
            self.assertFalse(loaded["success"])
            self.assertEqual(loaded["error"], "权重不存在")

    def test_error_archive_discards_pending(self):
        run_id = "oo-err"
        with tempfile.TemporaryDirectory() as td, _patch_store(td):
            with factory_scope("T"):
                os.environ[ENV_RUN_ID] = run_id
                try:
                    save_lightgbm_pending("tree\n", {"head": "oo"})
                    vid = archive_factory_run(
                        "oo",
                        job=_job(run_id, pid=os.getpid()),
                        last=_last(metrics=None, action="holdout_failed"),
                        status="error",
                    )
                finally:
                    os.environ.pop(ENV_RUN_ID, None)
            self.assertFalse(os.path.isdir(pending_weights_dir("oo")))
            self.assertFalse(os.path.isdir(version_weights_dir("oo", vid)))

    def test_delete_and_keep_remove_weights_dir(self):
        pid = os.getpid()
        with tempfile.TemporaryDirectory() as td, _patch_store(td):
            with factory_scope("T"):
                os.environ[ENV_RUN_ID] = "oo-keep"
                try:
                    save_lightgbm_pending("keep\n", {"head": "oo"})
                    keep = archive_factory_run(
                        "oo",
                        job=_job("oo-keep", pid=pid),
                        last=_last(),
                        status="done",
                    )
                finally:
                    os.environ.pop(ENV_RUN_ID, None)
            self.assertTrue(os.path.isdir(version_weights_dir("oo", keep)))
            delete_factory_version("oo", keep, factory="T")
            self.assertFalse(os.path.isdir(version_weights_dir("oo", keep)))

            with factory_scope("T"):
                first = None
                for i in range(KEEP + 1):
                    run_id = f"oo-run-{i:02d}"
                    os.environ[ENV_RUN_ID] = run_id
                    save_lightgbm_pending(f"m{i}\n", {"head": "oo"})
                    vid = archive_factory_run(
                        "oo",
                        job=_job(run_id, pid=pid),
                        last=_last(),
                        status="done",
                    )
                    if first is None:
                        first = vid
                os.environ.pop(ENV_RUN_ID, None)
            self.assertFalse(os.path.isdir(version_weights_dir("oo", first)))
            self.assertTrue(
                os.path.isdir(version_weights_dir("oo", f"oo-run-{KEEP:02d}-p{pid}"))
            )

    def test_transformer_state_roundtrip(self):
        import torch

        pid = os.getpid()
        run_id = "oo-tf"
        weight = torch.zeros(2, 2)
        with tempfile.TemporaryDirectory() as td, _patch_store(td):
            with factory_scope("T"):
                os.environ[ENV_RUN_ID] = run_id
                try:
                    from core.research.yhat_factory_weights import save_transformer_pending

                    self.assertTrue(
                        save_transformer_pending(
                            {"w": weight},
                            {
                                "head": "oo",
                                "d_model": 2,
                                "seq_clip": {"x_lo": [0.0], "x_hi": [1.0], "gap_lo": 0.0, "gap_hi": 1.0},
                            },
                        )
                    )
                    vid = archive_factory_run(
                        "oo", job=_job(run_id, pid=pid), last=_last(), status="done"
                    )
                finally:
                    os.environ.pop(ENV_RUN_ID, None)
            loaded = load_factory_weights("oo", vid, factory="T")
            self.assertTrue(loaded["success"])
            self.assertTrue(torch.equal(loaded["state_dict"]["w"], weight))
            self.assertEqual(loaded["meta"]["d_model"], 2)

    def test_load_requires_version_snapshot(self):
        missing = load_factory_weights("oo", "oo-missing-p1", factory="T")
        self.assertFalse(missing["success"])
        self.assertEqual(missing["error"], "版本不存在")

    def test_arm_l_save_lands_in_l_store(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "scripts", "run_yhat_transformer_holdout.py")
        spec = importlib.util.spec_from_file_location("holdout_arm_l_save", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        pid = os.getpid()
        run_id = "oo-20261116T010203"
        blob = {"booster_b64": base64.b64encode(b"tree\n").decode("ascii")}
        with tempfile.TemporaryDirectory() as td:
            ldir = os.path.join(td, "L")
            tdir = os.path.join(td, "T")
            os.makedirs(ldir)
            os.makedirs(tdir)
            with mock.patch.multiple(
                "core.research.yhat_tf_desk",
                YHAT_L_DIR=ldir,
                YHAT_L_LAST_PATH=os.path.join(ldir, "last_status.json"),
                YHAT_TF_DIR=tdir,
                YHAT_TF_LAST_PATH=os.path.join(tdir, "last_status.json"),
            ):
                os.environ[ENV_RUN_ID] = run_id
                try:
                    mod._try_save_lightgbm(
                        blob,
                        {"head": "oo", "factor_names": ["gap"], "solver": "lightgbm"},
                    )
                    self.assertTrue(os.path.isdir(os.path.join(ldir, "oo.weights.pending")))
                    self.assertFalse(os.path.isdir(os.path.join(tdir, "oo.weights.pending")))
                    with factory_scope("L"):
                        vid = archive_factory_run(
                            "oo",
                            job=_job(run_id, pid=pid),
                            last=_last(),
                            status="done",
                        )
                finally:
                    os.environ.pop(ENV_RUN_ID, None)
                self.assertEqual(vid, f"{run_id}-p{pid}")
                loaded = load_factory_weights("oo", vid, factory="L")
        self.assertTrue(loaded["success"], loaded.get("error"))
        self.assertEqual(loaded["model_text"], "tree\n")

    def test_seq_clip_bounds_roundtrip_shape(self):
        import numpy as np

        x = np.arange(12, dtype=np.float32).reshape(4, 3)
        gap = np.array([0.0, 1.0, 2.0, 10.0], dtype=np.float32)
        bounds = _clip_inplace_rows(x, gap, [0, 1, 2, 3], seed=1)
        self.assertEqual(len(bounds["x_lo"]), 3)
        self.assertEqual(len(bounds["x_hi"]), 3)
        self.assertLessEqual(bounds["gap_lo"], bounds["gap_hi"])
        self.assertTrue(np.all(x >= np.asarray(bounds["x_lo"]) - 1e-5))
        self.assertTrue(np.all(x <= np.asarray(bounds["x_hi"]) + 1e-5))


if __name__ == "__main__":
    unittest.main()
