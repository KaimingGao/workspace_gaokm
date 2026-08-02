"""分组 live：晋升 / 回滚 / 健康 / 分池排序。"""

import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@contextmanager
def _live_tmp():
    with tempfile.TemporaryDirectory() as tmp:
        live = os.path.join(tmp, "live")
        hist = os.path.join(live, "cluster_weights_history")
        os.makedirs(hist, exist_ok=True)
        active = os.path.join(live, "cluster_weights_active.json")
        draft = os.path.join(live, "cluster_weights_draft.json")
        book = os.path.join(live, "cluster_book_active.json")
        cfg_path = os.path.join(tmp, "signal_config.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump({"cluster_scoring": {"enabled": True, "mode": "shadow"}}, f)
        with patch("core.paths.LIVE_DIR", live), patch(
            "core.paths.CLUSTER_WEIGHTS_ACTIVE_PATH", active
        ), patch(
            "core.paths.CLUSTER_WEIGHTS_HISTORY_DIR", hist
        ), patch(
            "core.paths.CLUSTER_WEIGHTS_DRAFT_PATH", draft
        ), patch(
            "core.paths.CLUSTER_BOOK_ACTIVE_PATH", book
        ), patch(
            "core.paths.SIGNAL_CONFIG_PATH", cfg_path
        ), patch.dict(
            os.environ, {"INVESTMENT_SIGNAL_CONFIG": cfg_path}
        ):
            import core.signal.config as cfg_mod

            cfg_mod._cached = None
            yield {
                "live": live,
                "active": active,
                "hist": hist,
                "draft": draft,
                "book": book,
                "cfg": cfg_path,
            }


class TestClusterLive(unittest.TestCase):
    def _artifact(self):
        return {
            "created_at": "2026-08-01T00:00:00Z",
            "n_clusters": 2,
            "code_map": {
                "600519": {
                    "cluster_label": "G1",
                    "cluster_id": 0,
                    "weights": {"momentum": 0.6, "value": 0.4},
                },
                "000001": {
                    "cluster_label": "G1",
                    "cluster_id": 0,
                    "weights": {"momentum": 0.6, "value": 0.4},
                },
                "601318": {
                    "cluster_label": "G2",
                    "cluster_id": 1,
                    "weights": {"momentum": 0.3, "value": 0.7},
                },
            },
        }

    def test_promote_and_rollback(self):
        from core.signal.cluster_live import (
            load_active_cluster_weights,
            promote_cluster_artifact,
            rollback_cluster_weights,
        )

        with _live_tmp() as paths:
            out = promote_cluster_artifact(self._artifact(), note="t1")
            self.assertTrue(out["success"])
            self.assertEqual(out["version"], 1)
            self.assertFalse(out["signal_config_touched"])
            act = load_active_cluster_weights()
            self.assertEqual(act["n_mapped_codes"], 3)

            out2 = promote_cluster_artifact(self._artifact(), note="t2")
            self.assertEqual(out2["version"], 2)
            self.assertTrue(os.listdir(paths["hist"]))

            rb = rollback_cluster_weights()
            self.assertTrue(rb["success"])
            act2 = load_active_cluster_weights()
            self.assertIsNotNone(act2)

    def test_set_mode_active_requires_health(self):
        from core.signal.cluster_live import (
            promote_cluster_artifact,
            set_cluster_scoring_mode,
        )

        with _live_tmp():
            # 无 active 时禁止 active
            bad = set_cluster_scoring_mode("active")
            self.assertFalse(bad["success"])

            promote_cluster_artifact(self._artifact())
            with patch(
                "core.signal.cluster_live.assess_cluster_live_health",
                return_value={
                    "allow_active": True,
                    "alerts": [],
                },
            ):
                ok = set_cluster_scoring_mode("shadow")
            self.assertTrue(ok["success"])
            self.assertEqual(ok["cluster_scoring"]["mode"], "shadow")
            self.assertFalse(ok["signal_config_weights_touched"])

    def test_apply_shortcut_promote_shadow_refresh(self):
        from core.signal.cluster_live import apply_cluster_live_shortcut

        with _live_tmp():
            with patch(
                "core.signal.cluster_live.refresh_cluster_book_daily",
                return_value={
                    "success": True,
                    "rank": {"name_count": 2, "success": True},
                },
            ), patch(
                "core.signal.cluster_live.assess_cluster_live_health",
                return_value={"allow_active": True, "alerts": []},
            ):
                out = apply_cluster_live_shortcut(
                    self._artifact(),
                    from_draft=False,
                    mode="shadow",
                )
            self.assertTrue(out["success"])
            self.assertEqual(out["version"], 1)
            self.assertTrue(out["promote"]["success"])
            self.assertEqual(
                (out["mode"].get("cluster_scoring") or {}).get("mode"), "shadow"
            )

    def test_rank_cluster_pools_no_cross_sort(self):
        from core.signal.cluster_live import promote_cluster_artifact
        from core.signal.cluster_rank import rank_cluster_pools

        with _live_tmp():
            promote_cluster_artifact(self._artifact())

            def fake_score(code, **kwargs):
                scores = {"600519": 80, "000001": 70, "601318": 90}
                lab = {
                    "600519": "G1",
                    "000001": "G1",
                    "601318": "G2",
                }.get(str(code), "?")
                return {
                    "success": True,
                    "signal_item": {
                        "stock_code": code,
                        "stock_name": code,
                        "score": scores.get(str(code), 50),
                        "hard_reject": False,
                        "cluster_label": lab,
                        "weight_source": f"cluster:{lab}",
                    },
                }

            with patch(
                "core.signal.score_stock.score_stock", side_effect=fake_score
            ), patch(
                "core.watching_store.read_watching",
                return_value={"watchlist": ["600519", "000001", "601318"]},
            ):
                out = rank_cluster_pools(
                    ["600519", "000001", "601318"],
                    top_n_per_group=1,
                    persist_book=True,
                )
            self.assertTrue(out["success"])
            self.assertFalse(out["cross_group_rank"])
            codes = [b["stock_code"] for b in out["book"]]
            # 每组 Top1：G1→600519，G2→601318；不是按 90/80/70 全局序
            self.assertEqual(set(codes), {"600519", "601318"})
            self.assertNotIn("000001", codes)

            # 组内相对 Top-N：低于全局 min_score 的票仍可进簿
            def fake_score_low(code, **kwargs):
                return {
                    "success": True,
                    "signal_item": {
                        "stock_code": code,
                        "stock_name": code,
                        "score": {"600519": 40, "000001": 30, "601318": 35}.get(
                            str(code), 10
                        ),
                        "hard_reject": False,
                        "cluster_label": {
                            "600519": "G1",
                            "000001": "G1",
                            "601318": "G2",
                        }.get(str(code), "?"),
                        "weight_source": "cluster:G1",
                    },
                }

            with patch(
                "core.signal.score_stock.score_stock", side_effect=fake_score_low
            ):
                low = rank_cluster_pools(
                    ["600519", "000001", "601318"],
                    top_n_per_group=2,
                    min_score=55,
                    persist_book=False,
                )
            self.assertTrue(low["success"])
            self.assertGreaterEqual(low.get("below_min_score_count") or 0, 1)
            self.assertEqual(len(low["book"]), 3)

    def test_auto_demote_stale_and_prepare_daily(self):
        from core.signal.cluster_live import (
            maybe_auto_demote_stale,
            prepare_cluster_for_daily,
            promote_cluster_artifact,
            set_cluster_scoring_mode,
        )

        with _live_tmp():
            promote_cluster_artifact(self._artifact())
            with patch(
                "core.signal.cluster_live.assess_cluster_live_health",
                return_value={
                    "allow_active": True,
                    "alerts": [],
                    "stale": False,
                    "suggest_demote": False,
                },
            ):
                ok = set_cluster_scoring_mode("active")
            self.assertTrue(ok["success"])

            with patch(
                "core.signal.cluster_live.assess_cluster_live_health",
                return_value={
                    "allow_active": False,
                    "alerts": ["映射陈旧"],
                    "stale": True,
                    "suggest_demote": True,
                },
            ):
                dem = maybe_auto_demote_stale()
            self.assertTrue(dem["success"])
            self.assertTrue(dem["demoted"])
            self.assertEqual(dem["cluster_scoring"]["mode"], "shadow")

            with patch(
                "core.signal.cluster_live.maybe_auto_demote_stale",
                return_value={"success": True, "demoted": False},
            ), patch(
                "core.signal.cluster_live.refresh_cluster_book_daily",
                return_value={
                    "success": True,
                    "rank": {"name_count": 2, "success": True},
                },
            ), patch(
                "core.signal.cluster_live.assess_cluster_live_health",
                return_value={"allow_active": True, "alerts": []},
            ):
                prep = prepare_cluster_for_daily()
            self.assertTrue(prep["success"])
            self.assertIsNotNone(prep.get("refresh"))

    def test_status_landing_fields(self):
        from core.signal.cluster_live import (
            cluster_status_public,
            promote_cluster_artifact,
            set_cluster_scoring_mode,
        )

        with _live_tmp():
            promote_cluster_artifact(self._artifact())
            with patch(
                "core.signal.cluster_live.assess_cluster_live_health",
                return_value={
                    "allow_active": True,
                    "alerts": [],
                    "coverage": 1.0,
                    "stale": False,
                },
            ), patch(
                "core.signal.cluster_live.cluster_score_audit_sample",
                return_value={"success": True, "rows": []},
            ):
                set_cluster_scoring_mode("shadow")
                st = cluster_status_public(include_audit=True)
            self.assertTrue(st["success"])
            land = st.get("landing") or {}
            self.assertEqual(land.get("next_step"), "enable_active")
            self.assertTrue(land.get("can_activate"))
            self.assertIn("audit_sample", st)

    def test_status_landing_after_paper_applied(self):
        from core.signal.cluster_live import (
            cluster_status_public,
            promote_cluster_artifact,
            set_cluster_scoring_mode,
        )

        with _live_tmp():
            promoted = promote_cluster_artifact(self._artifact())
            ver = promoted.get("version")
            with patch(
                "core.signal.cluster_live.assess_cluster_live_health",
                return_value={
                    "allow_active": True,
                    "alerts": [],
                    "coverage": 1.0,
                    "stale": False,
                },
            ), patch(
                "core.signal.cluster_live._paper_cluster_landed",
                return_value={
                    "applied": True,
                    "applied_at": "2026-08-01",
                    "cluster_version": ver,
                },
            ):
                set_cluster_scoring_mode("active")
                st = cluster_status_public(include_audit=False)
            land = st.get("landing") or {}
            self.assertEqual(land.get("next_step"), "go_follow")
            self.assertEqual(land.get("next_label"), "已启用 · 侧栏进交易执行")
            self.assertTrue(land.get("ready_for_follow"))
            self.assertTrue(land.get("paper_applied"))


if __name__ == "__main__":
    unittest.main()
