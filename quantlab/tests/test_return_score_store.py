"""ŷ_oo Ridge 草稿 / promote（实现在 ``core.research.oo_ridge``）。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.return_score import ReturnScoreModel
from core.research import oo_ridge as store


class TestReturnScoreStore(unittest.TestCase):
    def test_draft_promote_roundtrip(self):
        model = ReturnScoreModel(
            intercept=0.1,
            coefficients={"momentum": 0.5, "value": -0.2},
            feature_zscore=False,
            sample_count=40,
            horizon_days=3,
        )
        with tempfile.TemporaryDirectory() as td:
            draft = os.path.join(td, "draft.json")
            active = os.path.join(td, "active.json")
            with patch.object(store, "RETURN_SCORE_MODEL_DRAFT_PATH", draft), patch.object(
                store, "RETURN_SCORE_MODEL_ACTIVE_PATH", active
            ), patch.object(store, "QUANT_REPORTS_DIR", td), patch.object(
                store, "LIVE_DIR", td
            ):
                saved = store.save_return_model_draft(model, meta={"lookback": 60})
                self.assertTrue(saved["success"])
                self.assertTrue(os.path.isfile(draft))
                loaded, meta = store.load_return_model(prefer_active=True)
                self.assertIsNotNone(loaded)
                self.assertEqual(meta["role"], "draft")
                self.assertAlmostEqual(loaded.coefficients["momentum"], 0.5)
                prom = store.promote_return_model_draft(note="test")
                self.assertTrue(prom["success"])
                self.assertTrue(os.path.isfile(active))
                loaded2, meta2 = store.load_return_model(prefer_active=True)
                self.assertEqual(meta2["role"], "active")
                self.assertAlmostEqual(loaded2.intercept, 0.1)

    def test_promote_research_and_prefer_research(self):
        live = ReturnScoreModel(
            intercept=0.2,
            coefficients={"momentum": 0.3},
            feature_zscore=False,
            sample_count=20,
            horizon_days=1,
        )
        research_m = ReturnScoreModel(
            intercept=0.11,
            coefficients={"momentum": 0.25},
            feature_zscore=False,
            sample_count=12,
            horizon_days=1,
        )
        with tempfile.TemporaryDirectory() as td:
            draft = os.path.join(td, "draft.json")
            active = os.path.join(td, "active.json")
            research = os.path.join(td, "research.json")
            with patch.object(store, "RETURN_SCORE_MODEL_DRAFT_PATH", draft), patch.object(
                store, "RETURN_SCORE_MODEL_ACTIVE_PATH", active
            ), patch.object(
                store, "RETURN_SCORE_MODEL_RESEARCH_PATH", research
            ), patch.object(store, "QUANT_REPORTS_DIR", td), patch.object(
                store, "LIVE_DIR", td
            ):
                saved = store.save_return_model_draft(
                    live, research_model=research_m
                )
                self.assertTrue(saved["success"])
                self.assertTrue(saved.get("has_research_model"))
                # 无 research_model 的旧草稿不得启用研究套
                store.save_return_model_draft(live)
                bad = store.promote_return_model_draft(
                    note="research", role="research"
                )
                self.assertFalse(bad.get("success"))
                store.save_return_model_draft(live, research_model=research_m)
                prom = store.promote_return_model_draft(
                    note="research", role="research"
                )
                self.assertTrue(prom["success"])
                self.assertEqual(prom["role"], "research")
                self.assertTrue(os.path.isfile(research))
                loaded, meta = store.load_return_model(
                    prefer_active=True, prefer_research=True
                )
                self.assertIsNotNone(loaded)
                self.assertEqual(meta["role"], "research")
                self.assertAlmostEqual(float(loaded.intercept), 0.11)
                st = store.return_model_status()
                self.assertTrue(st["research"]["exists"])
                self.assertFalse(st["active"]["exists"])
                self.assertEqual(st["display_role"], "research")
                self.assertIsInstance(st.get("return_model"), dict)
                self.assertIn("momentum", (st["return_model"] or {}).get("coefficients") or {})
                self.assertTrue(store.promote_return_model_draft(note="live", role="live")["success"])
                st2 = store.return_model_status()
                self.assertEqual(st2["display_role"], "active")
                self.assertAlmostEqual(
                    float((st2["return_model"] or {}).get("intercept") or 0), 0.2
                )

    def test_promote_strips_money_flow_proxy(self):
        model = ReturnScoreModel(
            intercept=0.1,
            coefficients={"momentum": 0.5, "money_flow": -0.011},
            feature_zscore=False,
            sample_count=30,
            horizon_days=1,
        )
        research_m = ReturnScoreModel(
            intercept=0.05,
            coefficients={"momentum": 0.4, "money_flow": -0.02},
            feature_zscore=False,
            sample_count=18,
            horizon_days=1,
        )
        with tempfile.TemporaryDirectory() as td:
            draft = os.path.join(td, "draft.json")
            research = os.path.join(td, "research.json")
            with patch.object(store, "RETURN_SCORE_MODEL_DRAFT_PATH", draft), patch.object(
                store, "RETURN_SCORE_MODEL_RESEARCH_PATH", research
            ), patch.object(
                store, "RETURN_SCORE_MODEL_ACTIVE_PATH", os.path.join(td, "active.json")
            ), patch.object(store, "QUANT_REPORTS_DIR", td), patch.object(
                store, "LIVE_DIR", td
            ):
                self.assertTrue(
                    store.save_return_model_draft(
                        model, research_model=research_m
                    )["success"]
                )
                prom = store.promote_return_model_draft(note="", role="research")
                self.assertTrue(prom.get("success"), prom)
                raw = store.load_return_model_payload(research)
                coefs = (raw or {}).get("model", {}).get("coefficients") or {}
                self.assertNotIn("money_flow", coefs)
                self.assertIn("momentum", coefs)
                self.assertIn("money_flow", (raw or {}).get("meta", {}).get("stripped_unsourced") or [])
                self.assertEqual(
                    (raw or {}).get("meta", {}).get("coef_source"),
                    "return_model_research",
                )
                self.assertIn("return_model", raw or {})
                self.assertEqual(
                    ((raw or {}).get("return_model") or {}).get("intercept"),
                    ((raw or {}).get("model") or {}).get("intercept"),
                )

    def test_draft_writes_return_model_aliases_and_status_prefers_research(self):
        """草稿同时落 return_model*；状态系数表优先 Holdout 研究套 β。"""
        live = ReturnScoreModel(
            intercept=0.5,
            coefficients={"OPEN0": 4.4},
            feature_zscore=False,
            sample_count=100,
            horizon_days=1,
        )
        research = ReturnScoreModel(
            intercept=0.07,
            coefficients={"OPEN0": 2.7},
            feature_zscore=False,
            sample_count=80,
            horizon_days=1,
        )
        with tempfile.TemporaryDirectory() as td:
            draft = os.path.join(td, "draft.json")
            with patch.object(store, "RETURN_SCORE_MODEL_DRAFT_PATH", draft), patch.object(
                store, "RETURN_SCORE_MODEL_ACTIVE_PATH", os.path.join(td, "active.json")
            ), patch.object(
                store, "RETURN_SCORE_MODEL_RESEARCH_PATH", os.path.join(td, "research.json")
            ), patch.object(store, "QUANT_REPORTS_DIR", td), patch.object(
                store, "LIVE_DIR", td
            ):
                self.assertTrue(
                    store.save_return_model_draft(
                        live, research_model=research
                    )["success"]
                )
                raw = store.load_return_model_payload(draft)
                self.assertIsInstance((raw or {}).get("return_model"), dict)
                self.assertIsInstance((raw or {}).get("return_model_research"), dict)
                self.assertAlmostEqual(
                    float(((raw or {}).get("return_model") or {}).get("intercept") or 0),
                    0.5,
                )
                self.assertAlmostEqual(
                    float(
                        ((raw or {}).get("return_model_research") or {}).get("intercept")
                        or 0
                    ),
                    0.07,
                )
                st = store.return_model_status()
                self.assertEqual(st.get("display_role"), "draft")
                self.assertAlmostEqual(
                    float((st.get("return_model") or {}).get("intercept") or 0), 0.07
                )

    def test_draft_persists_oos_for_status(self):
        model = ReturnScoreModel(
            intercept=0.1,
            coefficients={"momentum": 0.4},
            feature_zscore=False,
            sample_count=40,
            horizon_days=1,
        )
        oos = {
            "n_train": 100,
            "n_test": 20,
            "holdout_trading_days": 10,
            "ic": 0.12,
            "sign_hit": 0.58,
        }
        with tempfile.TemporaryDirectory() as td:
            draft = os.path.join(td, "draft.json")
            with patch.object(store, "RETURN_SCORE_MODEL_DRAFT_PATH", draft), patch.object(
                store, "RETURN_SCORE_MODEL_ACTIVE_PATH", os.path.join(td, "active.json")
            ), patch.object(
                store, "RETURN_SCORE_MODEL_RESEARCH_PATH", os.path.join(td, "research.json")
            ), patch.object(store, "QUANT_REPORTS_DIR", td), patch.object(
                store, "LIVE_DIR", td
            ):
                saved = store.save_return_model_draft(model, oos=oos, meta={"r_squared": 0.05})
                self.assertTrue(saved["success"])
                st = store.return_model_status()
                self.assertEqual(st.get("display_role"), "draft")
                self.assertEqual((st.get("oos") or {}).get("ic"), 0.12)
                self.assertEqual((st.get("oos") or {}).get("sign_hit"), 0.58)
                self.assertEqual((st.get("oos") or {}).get("holdout_trading_days"), 10)

    def test_status_oos_falls_back_to_draft_when_active_empty(self):
        model = ReturnScoreModel(
            intercept=0.1,
            coefficients={"momentum": 0.4},
            feature_zscore=False,
            sample_count=40,
            horizon_days=1,
        )
        oos = {
            "n_train": 100,
            "n_test": 20,
            "holdout_trading_days": 10,
            "ic": 0.12,
            "sign_hit": 0.58,
        }
        with tempfile.TemporaryDirectory() as td:
            draft = os.path.join(td, "draft.json")
            active = os.path.join(td, "active.json")
            research = os.path.join(td, "research.json")
            with patch.object(store, "RETURN_SCORE_MODEL_DRAFT_PATH", draft), patch.object(
                store, "RETURN_SCORE_MODEL_ACTIVE_PATH", active
            ), patch.object(
                store, "RETURN_SCORE_MODEL_RESEARCH_PATH", research
            ), patch.object(store, "QUANT_REPORTS_DIR", td), patch.object(
                store, "LIVE_DIR", td
            ):
                self.assertTrue(store.save_return_model_draft(model, oos=oos)["success"])
                # 旧版 promote：写入 active 但 oos 为空
                self.assertTrue(
                    store.promote_return_model_draft(note="", role="live")["success"]
                )
                # 再写一份带 OOS 的新草稿（模拟重新拟合；隔秒避免与 source_saved_at 同戳）
                import time

                time.sleep(1.05)
                self.assertTrue(
                    store.save_return_model_draft(model, oos=oos)["success"]
                )
                # 清空 active.oos 模拟历史产物
                import json

                with open(active, encoding="utf-8") as f:
                    act = json.load(f)
                act["oos"] = {}
                with open(active, "w", encoding="utf-8") as f:
                    json.dump(act, f)
                st = store.return_model_status()
                # 新草稿晚于执行套 → 系数表与 OOS 都跟草稿
                self.assertEqual(st.get("display_role"), "draft")
                self.assertEqual((st.get("oos") or {}).get("ic"), 0.12)
                self.assertEqual((st.get("oos") or {}).get("sign_hit"), 0.58)

    def test_status_prefers_newer_draft_oos_over_stale_active(self):
        """拟合后草稿带 cs_ic；执行套仍是旧 oos 时，系数表与状态条跟草稿。"""
        model = ReturnScoreModel(
            intercept=0.1,
            coefficients={"momentum": 0.4},
            feature_zscore=False,
            sample_count=40,
            horizon_days=1,
        )
        old_oos = {"n_train": 100, "n_test": 20, "ic": -0.031, "sign_hit": 0.47}
        new_oos = {
            "n_train": 100,
            "n_test": 20,
            "ic": -0.031,
            "cs_ic": -0.018,
            "cs_rank_ic": -0.014,
            "sign_hit": 0.47,
        }
        with tempfile.TemporaryDirectory() as td:
            draft = os.path.join(td, "draft.json")
            active = os.path.join(td, "active.json")
            research = os.path.join(td, "research.json")
            with patch.object(store, "RETURN_SCORE_MODEL_DRAFT_PATH", draft), patch.object(
                store, "RETURN_SCORE_MODEL_ACTIVE_PATH", active
            ), patch.object(
                store, "RETURN_SCORE_MODEL_RESEARCH_PATH", research
            ), patch.object(store, "QUANT_REPORTS_DIR", td), patch.object(
                store, "LIVE_DIR", td
            ):
                self.assertTrue(
                    store.save_return_model_draft(model, oos=old_oos)["success"]
                )
                self.assertTrue(
                    store.promote_return_model_draft(note="", role="live")["success"]
                )
                import time

                time.sleep(1.05)
                self.assertTrue(
                    store.save_return_model_draft(model, oos=new_oos)["success"]
                )
                st = store.return_model_status()
                self.assertEqual(st.get("display_role"), "draft")
                self.assertEqual(st.get("oos_source"), "draft")
                self.assertEqual((st.get("oos") or {}).get("cs_ic"), -0.018)
                self.assertEqual((st.get("oos") or {}).get("cs_rank_ic"), -0.014)
                coefs = (st.get("return_model") or {}).get("coefficients") or {}
                self.assertIn("momentum", coefs)


if __name__ == "__main__":
    unittest.main()
