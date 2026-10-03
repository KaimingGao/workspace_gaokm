"""收益打分模型草稿 / promote（不写 weights）。"""

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
from core.signal import return_score_store as store


class TestReturnScoreStore(unittest.TestCase):
    def test_draft_promote_roundtrip(self):
        model = ReturnScoreModel(
            intercept=0.1,
            coefficients={"momentum": 0.5, "value": -0.2},
            standardized=False,
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
        model = ReturnScoreModel(
            intercept=0.2,
            coefficients={"momentum": 0.3},
            standardized=False,
            sample_count=20,
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
                self.assertTrue(store.save_return_model_draft(model)["success"])
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
            standardized=False,
            sample_count=30,
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
                self.assertTrue(store.save_return_model_draft(model)["success"])
                prom = store.promote_return_model_draft(note="", role="research")
                self.assertTrue(prom.get("success"), prom)
                raw = store.load_return_model_payload(research)
                coefs = (raw or {}).get("model", {}).get("coefficients") or {}
                self.assertNotIn("money_flow", coefs)
                self.assertIn("momentum", coefs)
                self.assertIn("money_flow", (raw or {}).get("meta", {}).get("stripped_unsourced") or [])

    def test_draft_persists_oos_for_status(self):
        model = ReturnScoreModel(
            intercept=0.1,
            coefficients={"momentum": 0.4},
            standardized=False,
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
            standardized=False,
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
                # 再写一份带 OOS 的新草稿（模拟重新拟合）
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
                self.assertEqual(st.get("display_role"), "active")
                self.assertEqual((st.get("oos") or {}).get("ic"), 0.12)
                self.assertEqual((st.get("oos") or {}).get("sign_hit"), 0.58)

    def test_status_prefers_newer_draft_oos_over_stale_active(self):
        """拟合后草稿带 cs_ic；执行套仍是旧 oos 时，状态条用草稿评测。"""
        model = ReturnScoreModel(
            intercept=0.1,
            coefficients={"momentum": 0.4},
            standardized=False,
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
                self.assertTrue(
                    store.save_return_model_draft(model, oos=new_oos)["success"]
                )
                st = store.return_model_status()
                self.assertEqual(st.get("display_role"), "active")
                self.assertEqual(st.get("oos_source"), "draft")
                self.assertEqual((st.get("oos") or {}).get("cs_ic"), -0.018)
                self.assertEqual((st.get("oos") or {}).get("cs_rank_ic"), -0.014)


if __name__ == "__main__":
    unittest.main()
