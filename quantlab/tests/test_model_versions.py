"""模型版本归档 / 列表 / 恢复。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock


def _complete_rm(sample_count: int, *, tag: str = "a") -> dict:
    return {
        "sample_count": int(sample_count),
        "backend": "lightgbm",
        "feature_names": [f"{tag}1", f"{tag}2", f"{tag}3"],
        "feature_importance": [
            {"key": f"{tag}1", "gain": 1.0},
            {"key": f"{tag}2", "gain": 0.5},
            {"key": f"{tag}3", "gain": 0.2},
        ],
    }


class TestModelVersions(unittest.TestCase):
    def test_archive_list_restore_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            research = os.path.join(live, "return_score_model_research.json")
            active = os.path.join(live, "return_score_model_active.json")
            with mock.patch("core.paths.LIVE_DIR", live), mock.patch(
                "core.research.model_versions.LIVE_DIR", live
            ), mock.patch(
                "core.research.model_versions.MODEL_VERSIONS_DIR",
                os.path.join(live, "model_versions"),
            ), mock.patch(
                "core.paths.RETURN_SCORE_MODEL_RESEARCH_PATH", research
            ), mock.patch(
                "core.paths.RETURN_SCORE_MODEL_ACTIVE_PATH", active
            ):
                from core.research import model_versions as mv

                doc_a = {
                    "fitted_at": "2026-01-01T10:00:00Z",
                    "return_model": _complete_rm(1000, tag="a"),
                    "model_role": "research",
                }
                doc_b = {
                    "fitted_at": "2026-01-02T11:00:00Z",
                    "return_model": _complete_rm(2000, tag="b"),
                    "model_role": "research",
                }
                with open(research, "w", encoding="utf-8") as f:
                    json.dump(doc_a, f)
                vid_a = mv.archive_suite_version("oo", "research", doc_a)
                self.assertTrue(vid_a)
                with open(research, "w", encoding="utf-8") as f:
                    json.dump(doc_b, f)
                vid_b = mv.archive_suite_version("oo", "research", doc_b)
                self.assertNotEqual(vid_a, vid_b)

                listed = mv.list_suite_versions("oo", "research")
                self.assertTrue(listed["success"])
                ids = [it["id"] for it in listed["items"]]
                self.assertEqual(ids[0], vid_b)
                self.assertIn(vid_a, ids)
                self.assertEqual(listed["current_id"], vid_b)

                out = mv.restore_suite_version("oo", "research", vid_a)
                self.assertTrue(out["success"])
                with open(research, encoding="utf-8") as f:
                    cur = json.load(f)
                self.assertEqual(cur.get("fitted_at"), "2026-01-01T10:00:00Z")
                self.assertEqual(
                    (cur.get("return_model") or {}).get("sample_count"), 1000
                )

    def test_restore_invalidates_tau_json_model_cache(self):
        """启用旧版本后须清掉进程内 τ 缓存，避免仍读旧包。"""
        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            with mock.patch("core.paths.LIVE_DIR", live), mock.patch(
                "core.research.model_versions.LIVE_DIR", live
            ), mock.patch(
                "core.research.model_versions.MODEL_VERSIONS_DIR",
                os.path.join(live, "model_versions"),
            ):
                from core.research import model_versions as mv
                from core.research import tc_model

                slot = mv.suite_slot_path("tau", "research")
                doc_a = {
                    "fitted_at": "2026-03-01T10:00:00Z",
                    "return_model": _complete_rm(1000, tag="t"),
                    "model_role": "research",
                }
                doc_b = {
                    "fitted_at": "2026-03-02T11:00:00Z",
                    "return_model": _complete_rm(2000, tag="u"),
                    "model_role": "research",
                }
                os.makedirs(os.path.dirname(slot) or ".", exist_ok=True)
                with open(slot, "w", encoding="utf-8") as f:
                    json.dump(doc_a, f)
                vid_a = mv.archive_suite_version("tau", "research", doc_a)
                with open(slot, "w", encoding="utf-8") as f:
                    json.dump(doc_b, f)
                mv.archive_suite_version("tau", "research", doc_b)

                tc_model._JSON_MODEL_CACHE[slot] = (1.0, {"stale": True})
                out = mv.restore_suite_version("tau", "research", vid_a)
                self.assertTrue(out.get("success"))
                self.assertNotIn(slot, tc_model._JSON_MODEL_CACHE)

    def test_preview_strips_booster_and_does_not_write_slot(self):
        """预览瘦身 booster，且不改当前槽。"""
        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            research = os.path.join(live, "return_score_model_research.json")
            active = os.path.join(live, "return_score_model_active.json")
            with mock.patch("core.paths.LIVE_DIR", live), mock.patch(
                "core.research.model_versions.LIVE_DIR", live
            ), mock.patch(
                "core.research.model_versions.MODEL_VERSIONS_DIR",
                os.path.join(live, "model_versions"),
            ), mock.patch(
                "core.paths.RETURN_SCORE_MODEL_RESEARCH_PATH", research
            ), mock.patch(
                "core.paths.RETURN_SCORE_MODEL_ACTIVE_PATH", active
            ):
                from core.research import model_versions as mv

                slot_doc = {
                    "fitted_at": "2026-04-01T10:00:00Z",
                    "return_model": {
                        **_complete_rm(900, tag="a"),
                        "booster": {"payload_b64": "HEAVY"},
                    },
                    "oos": {"cs_ic": 0.1},
                    "model_role": "research",
                }
                other = {
                    "fitted_at": "2026-04-02T11:00:00Z",
                    "return_model": {
                        **_complete_rm(1100, tag="b"),
                        "booster": {"payload_b64": "OTHER"},
                    },
                    "oos": {"cs_ic": 0.2},
                    "model_role": "research",
                    "y_spec": {"formula": "open[T+1]/open[T]-1"},
                    "n_train_days": 40,
                    "n_test_days": 5,
                    "holdout_trading_days": 5,
                }
                with open(research, "w", encoding="utf-8") as f:
                    json.dump(slot_doc, f)
                vid = mv.archive_suite_version("oo", "research", other)
                self.assertTrue(vid)
                with open(research, encoding="utf-8") as f:
                    before = f.read()
                out = mv.preview_suite_version("oo", "research", vid)
                self.assertTrue(out.get("success"))
                self.assertTrue(out.get("preview"))
                rm = out.get("return_model") or {}
                self.assertNotIn("booster", rm)
                self.assertEqual(rm.get("sample_count"), 1100)
                self.assertEqual((out.get("feature_importance") or [])[0]["key"], "b1")
                self.assertEqual(
                    (out.get("y_spec") or {}).get("formula"),
                    "open[T+1]/open[T]-1",
                )
                self.assertEqual(out.get("n_train_days"), 40)
                self.assertEqual(out.get("n_test_days"), 5)
                self.assertEqual(out.get("holdout_trading_days"), 5)
                with open(research, encoding="utf-8") as f:
                    after = f.read()
                self.assertEqual(before, after)

    def test_delete_keeps_current_and_at_least_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            research = os.path.join(live, "return_score_model_research.json")
            with mock.patch("core.paths.LIVE_DIR", live), mock.patch(
                "core.research.model_versions.LIVE_DIR", live
            ), mock.patch(
                "core.research.model_versions.MODEL_VERSIONS_DIR",
                os.path.join(live, "model_versions"),
            ), mock.patch(
                "core.paths.RETURN_SCORE_MODEL_RESEARCH_PATH", research
            ), mock.patch(
                "core.paths.RETURN_SCORE_MODEL_ACTIVE_PATH",
                os.path.join(live, "return_score_model_active.json"),
            ):
                from core.research import model_versions as mv

                doc_a = {
                    "fitted_at": "2026-06-01T10:00:00Z",
                    "return_model": _complete_rm(1000, tag="a"),
                    "model_role": "research",
                }
                doc_b = {
                    "fitted_at": "2026-06-02T11:00:00Z",
                    "return_model": _complete_rm(2000, tag="b"),
                    "model_role": "research",
                }
                with open(research, "w", encoding="utf-8") as f:
                    json.dump(doc_b, f)
                vid_a = mv.archive_suite_version("oo", "research", doc_a)
                vid_b = mv.archive_suite_version("oo", "research", doc_b)
                # 当前槽是 b，不可删
                blocked = mv.delete_suite_version("oo", "research", vid_b)
                self.assertFalse(blocked.get("success"))
                self.assertTrue(blocked.get("current"))
                # 可删 a
                ok = mv.delete_suite_version("oo", "research", vid_a)
                self.assertTrue(ok.get("success"))
                # 只剩一个，不可再删空
                last = mv.delete_suite_version("oo", "research", vid_b)
                self.assertFalse(last.get("success"))
                listed = mv.list_suite_versions("oo", "research")
                self.assertEqual(len(listed["items"]), 1)
                self.assertEqual(listed["items"][0]["id"], vid_b)

    def test_restore_refuses_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            research = os.path.join(live, "return_score_model_research.json")
            with mock.patch("core.paths.LIVE_DIR", live), mock.patch(
                "core.research.model_versions.LIVE_DIR", live
            ), mock.patch(
                "core.research.model_versions.MODEL_VERSIONS_DIR",
                os.path.join(live, "model_versions"),
            ), mock.patch(
                "core.paths.RETURN_SCORE_MODEL_RESEARCH_PATH", research
            ), mock.patch(
                "core.paths.RETURN_SCORE_MODEL_ACTIVE_PATH",
                os.path.join(live, "return_score_model_active.json"),
            ):
                from core.research import model_versions as mv

                good = {
                    "fitted_at": "2026-07-02T11:00:00Z",
                    "return_model": _complete_rm(2000, tag="g"),
                    "model_role": "research",
                }
                stub = {
                    "fitted_at": "2026-07-01T10:00:00Z",
                    "return_model": {
                        "sample_count": 40,
                        "backend": "lightgbm",
                        "feature_names": ["only"],
                    },
                    "model_role": "research",
                }
                with open(research, "w", encoding="utf-8") as f:
                    json.dump(good, f)
                mv.archive_suite_version("oo", "research", good)
                vid_stub = mv.archive_suite_version(
                    "oo", "research", stub, allow_incomplete=True
                )
                out = mv.restore_suite_version("oo", "research", vid_stub)
                self.assertFalse(out.get("success"))
                self.assertTrue(out.get("incomplete"))
                with open(research, encoding="utf-8") as f:
                    cur = json.load(f)
                self.assertEqual(
                    (cur.get("return_model") or {}).get("sample_count"), 2000
                )

    def test_incomplete_not_archived_and_can_purge(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            with mock.patch("core.paths.LIVE_DIR", live), mock.patch(
                "core.research.model_versions.LIVE_DIR", live
            ), mock.patch(
                "core.research.model_versions.MODEL_VERSIONS_DIR",
                os.path.join(live, "model_versions"),
            ):
                from core.research import model_versions as mv

                stub = {
                    "fitted_at": "2026-05-01T10:00:00Z",
                    "return_model": {
                        "sample_count": 40,
                        "backend": "lightgbm",
                        "feature_names": ["only"],
                    },
                    "model_role": "research",
                }
                toy = {
                    "fitted_at": "2026-05-01T12:00:00Z",
                    "return_model": {
                        "sample_count": 2000,
                        "backend": "lambdarank",
                        "booster_b64": "dGVzdA==",
                        "coefficients": {"mom3": 1.0},
                    },
                    "model_role": "research",
                }
                missing_n = {
                    "fitted_at": "2026-05-01T13:00:00Z",
                    "return_model": {
                        "backend": "lambdarank",
                        "booster_b64": "dGVzdA==",
                        "coefficients": {"mom3": 1.0},
                    },
                    "model_role": "research",
                }
                good = {
                    "fitted_at": "2026-05-02T11:00:00Z",
                    "return_model": _complete_rm(2000, tag="g"),
                    "model_role": "research",
                }
                self.assertIsNone(mv.archive_suite_version("oo", "research", stub))
                self.assertIsNone(mv.archive_suite_version("oo", "research", toy))
                self.assertIsNone(mv.archive_suite_version("oo", "research", missing_n))
                # 强制写入残缺（模拟历史污染）后再 purge
                vid_stub = mv.archive_suite_version(
                    "oo", "research", stub, allow_incomplete=True
                )
                vid_toy = mv.archive_suite_version(
                    "oo", "research", toy, allow_incomplete=True
                )
                vid_good = mv.archive_suite_version("oo", "research", good)
                self.assertTrue(vid_stub)
                self.assertTrue(vid_toy)
                self.assertTrue(vid_good)
                purged = mv.purge_incomplete_versions("oo", "research")
                self.assertTrue(purged.get("success"))
                removed = purged.get("removed") or []
                self.assertIn(vid_stub, removed)
                self.assertIn(vid_toy, removed)
                listed = mv.list_suite_versions("oo", "research")
                ids = [it["id"] for it in listed["items"]]
                self.assertIn(vid_good, ids)
                self.assertNotIn(vid_stub, ids)
                self.assertNotIn(vid_toy, ids)

    def test_purge_sole_incomplete_removes_orphan(self):
        """仅剩残缺包时 purge 仍去文件+索引（不受至少保留 1 个保护）。"""
        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            research = os.path.join(live, "return_score_model_research.json")
            active = os.path.join(live, "return_score_model_active.json")
            with mock.patch("core.paths.LIVE_DIR", live), mock.patch(
                "core.research.model_versions.LIVE_DIR", live
            ), mock.patch(
                "core.research.model_versions.MODEL_VERSIONS_DIR",
                os.path.join(live, "model_versions"),
            ), mock.patch(
                "core.paths.RETURN_SCORE_MODEL_RESEARCH_PATH", research
            ), mock.patch(
                "core.paths.RETURN_SCORE_MODEL_ACTIVE_PATH", active
            ):
                from core.research import model_versions as mv

                stub = {
                    "fitted_at": "2026-08-01T10:00:00Z",
                    "return_model": {
                        "sample_count": 40,
                        "backend": "lightgbm",
                        "feature_names": ["only"],
                    },
                    "model_role": "research",
                }
                vid = mv.archive_suite_version(
                    "oo", "research", stub, allow_incomplete=True
                )
                self.assertTrue(vid)
                path = os.path.join(
                    live, "model_versions", "oo", "research", f"{vid}.json"
                )
                self.assertTrue(os.path.isfile(path))
                purged = mv.purge_incomplete_versions("oo", "research")
                self.assertTrue(purged.get("success"))
                self.assertIn(vid, purged.get("removed") or [])
                self.assertFalse(os.path.isfile(path))
                listed = mv.list_suite_versions("oo", "research")
                self.assertEqual(listed.get("items") or [], [])

    def test_preview_rejects_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = os.path.join(tmp, "live")
            os.makedirs(live, exist_ok=True)
            research = os.path.join(live, "return_score_model_research.json")
            active = os.path.join(live, "return_score_model_active.json")
            with mock.patch("core.paths.LIVE_DIR", live), mock.patch(
                "core.research.model_versions.LIVE_DIR", live
            ), mock.patch(
                "core.research.model_versions.MODEL_VERSIONS_DIR",
                os.path.join(live, "model_versions"),
            ), mock.patch(
                "core.paths.RETURN_SCORE_MODEL_RESEARCH_PATH", research
            ), mock.patch(
                "core.paths.RETURN_SCORE_MODEL_ACTIVE_PATH", active
            ):
                from core.research import model_versions as mv

                stub = {
                    "fitted_at": "2026-08-02T10:00:00Z",
                    "return_model": {
                        "sample_count": 40,
                        "backend": "lightgbm",
                        "feature_names": ["only"],
                    },
                    "model_role": "research",
                }
                vid = mv.archive_suite_version(
                    "oo", "research", stub, allow_incomplete=True
                )
                out = mv.preview_suite_version("oo", "research", vid)
                self.assertFalse(out.get("success"))
                self.assertTrue(out.get("incomplete"))


if __name__ == "__main__":
    unittest.main()
