"""ŷ_oo Ridge 草稿 / promote（实现在 ``core.research.oo_ridge``）。"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from core.research import oo_ridge as store
from core.signal.return_score import ReturnScoreModel


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
            research = os.path.join(td, "research.json")
            with patch.object(store, "RETURN_SCORE_MODEL_DRAFT_PATH", draft), patch.object(
                store, "RETURN_SCORE_MODEL_ACTIVE_PATH", active
            ), patch.object(
                store, "RETURN_SCORE_MODEL_RESEARCH_PATH", research
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
                # 启用执行须先有研究槽
                bad_live = store.promote_return_model_draft(note="test", role="live")
                self.assertFalse(bad_live.get("success"))
                prom_r = store.promote_return_model_draft(note="test", role="research")
                self.assertTrue(prom_r["success"])
                self.assertTrue(os.path.isfile(research))
                prom = store.promote_return_model_draft(note="test", role="live")
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
                saved = store.save_return_model_draft(research_m)
                self.assertTrue(saved["success"])
                # 无草稿不得启用
                os.remove(draft)
                bad = store.promote_return_model_draft(
                    note="research", role="research"
                )
                self.assertFalse(bad.get("success"))
                store.save_return_model_draft(research_m)
                # 直接启用执行（无研究槽）须失败
                bad_live = store.promote_return_model_draft(note="live", role="live")
                self.assertFalse(bad_live.get("success"))
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
                self.assertEqual(meta.get("model_role"), "research")
                self.assertEqual(getattr(loaded, "model_role", None), "research")
                self.assertAlmostEqual(float(loaded.intercept), 0.11)
                expl = loaded.explain_prediction({"momentum": 1.0})
                self.assertEqual((expl or {}).get("score_model_role"), "research")
                st = store.return_model_status()
                self.assertTrue(st["research"]["exists"])
                self.assertFalse(st["active"]["exists"])
                self.assertEqual(st["display_role"], "research")
                self.assertIsInstance(st.get("return_model"), dict)
                self.assertIn("momentum", (st["return_model"] or {}).get("coefficients") or {})
                # 晋级：执行槽 = 研究槽同一份 β
                self.assertTrue(store.promote_return_model_draft(note="live", role="live")["success"])
                st2 = store.return_model_status()
                self.assertEqual(st2["display_role"], "active")
                self.assertAlmostEqual(
                    float((st2["return_model"] or {}).get("intercept") or 0), 0.11
                )

    def test_status_shows_research_while_active_is_stale(self):
        """启用研究后、执行仍是上一轮时，系数表画研究槽，不画旧执行 β。"""
        import time

        old = ReturnScoreModel(
            intercept=0.05,
            coefficients={"YKLEN": -0.98, "OMA30": 0.78},
            feature_zscore=False,
            sample_count=40,
            horizon_days=1,
        )
        new = ReturnScoreModel(
            intercept=0.047,
            coefficients={"YKLEN": -0.78, "OMA30": 0.79},
            feature_zscore=False,
            sample_count=40,
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
                self.assertTrue(store.save_return_model_draft(old)["success"])
                self.assertTrue(
                    store.promote_return_model_draft(note="", role="research")["success"]
                )
                self.assertTrue(
                    store.promote_return_model_draft(note="", role="live")["success"]
                )
                time.sleep(1.05)
                self.assertTrue(store.save_return_model_draft(new)["success"])
                fitted = store.return_model_status()
                self.assertEqual(fitted.get("display_role"), "draft")
                self.assertAlmostEqual(
                    float((fitted.get("return_model") or {}).get("intercept") or 0),
                    0.047,
                )
                self.assertTrue(
                    store.promote_return_model_draft(note="", role="research")["success"]
                )
                researched = store.return_model_status()
                self.assertEqual(researched.get("display_role"), "research")
                self.assertAlmostEqual(
                    float((researched.get("return_model") or {}).get("intercept") or 0),
                    0.047,
                )
                self.assertAlmostEqual(
                    float(
                        ((researched.get("return_model") or {}).get("coefficients") or {}).get(
                            "OMA30"
                        )
                        or 0
                    ),
                    0.79,
                )
                self.assertTrue(
                    store.promote_return_model_draft(note="", role="live")["success"]
                )
                executed = store.return_model_status()
                self.assertEqual(executed.get("display_role"), "active")
                self.assertAlmostEqual(
                    float((executed.get("return_model") or {}).get("intercept") or 0),
                    0.047,
                )

    def test_prefer_research_draft_uses_holdout_blob(self):
        """草稿只落 Holdout 前训一份 β；研究/执行意图读到同一套系数。"""
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
                self.assertTrue(store.save_return_model_draft(research_m)["success"])
                loaded, meta = store.load_return_model(
                    prefer_active=True, prefer_research=True
                )
                self.assertIsNotNone(loaded)
                self.assertEqual(meta.get("role"), "draft")
                self.assertEqual(meta.get("model_role"), "research")
                self.assertAlmostEqual(float(loaded.intercept), 0.11)
                self.assertAlmostEqual(float(loaded.coefficients["momentum"]), 0.25)
                live_loaded, live_meta = store.load_return_model(
                    prefer_active=True, prefer_research=False
                )
                self.assertIsNotNone(live_loaded)
                self.assertEqual(live_meta.get("model_role"), "live")
                self.assertAlmostEqual(float(live_loaded.intercept), 0.11)

    def test_promote_strips_money_flow_proxy(self):
        model = ReturnScoreModel(
            intercept=0.1,
            coefficients={"momentum": 0.5, "money_flow": -0.011},
            feature_zscore=False,
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
        """草稿 return_model* 同为 Holdout 前训一份 β。"""
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
                self.assertTrue(store.save_return_model_draft(research)["success"])
                raw = store.load_return_model_payload(draft)
                self.assertIsInstance((raw or {}).get("return_model"), dict)
                self.assertIsInstance((raw or {}).get("return_model_research"), dict)
                self.assertAlmostEqual(
                    float(((raw or {}).get("return_model") or {}).get("intercept") or 0),
                    0.07,
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
                self.assertTrue(
                    store.promote_return_model_draft(note="", role="research")["success"]
                )
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
                    store.promote_return_model_draft(note="", role="research")["success"]
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

    def test_load_scoring_return_model_follows_context(self):
        """打分上下文 research 时须读研究套，不能静默落到 active。"""
        from core.research.feature_standardize import ZSCORE_SCOPE_CROSS_SECTION
        from core.research.holdout import scoring_model_role_context

        live = ReturnScoreModel(
            intercept=0.2,
            coefficients={"CLOSE0": 0.65},
            feature_zscore=True,
            zscore_scope=ZSCORE_SCOPE_CROSS_SECTION,
            sample_count=20,
            horizon_days=1,
        )
        research_m = ReturnScoreModel(
            intercept=0.07,
            coefficients={"CLOSE0": 0.31},
            feature_zscore=True,
            zscore_scope=ZSCORE_SCOPE_CROSS_SECTION,
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
                # v1 → 研究 → 执行；v2 只进研究槽，制造版本差
                self.assertTrue(store.save_return_model_draft(live)["success"])
                self.assertTrue(
                    store.promote_return_model_draft(note="research", role="research")[
                        "success"
                    ]
                )
                self.assertTrue(
                    store.promote_return_model_draft(note="live", role="live")["success"]
                )
                self.assertTrue(store.save_return_model_draft(research_m)["success"])
                self.assertTrue(
                    store.promote_return_model_draft(note="research", role="research")[
                        "success"
                    ]
                )
                live_loaded, live_meta = store.load_scoring_return_model()
                self.assertEqual(live_meta.get("model_role"), "live")
                self.assertAlmostEqual(float(live_loaded.coefficients["CLOSE0"]), 0.65)
                with scoring_model_role_context("research"):
                    r_loaded, r_meta = store.load_scoring_return_model()
                self.assertEqual(r_meta.get("model_role"), "research")
                self.assertAlmostEqual(float(r_loaded.coefficients["CLOSE0"]), 0.31)

    def test_prefer_research_does_not_fall_back_to_active(self):
        """研究槽缺失时不得静默用执行套；可回落草稿预览。"""
        live = ReturnScoreModel(
            intercept=0.9,
            coefficients={"momentum": 0.9},
            feature_zscore=False,
            sample_count=20,
            horizon_days=1,
        )
        draft_m = ReturnScoreModel(
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
                self.assertTrue(store.save_return_model_draft(live)["success"])
                self.assertTrue(
                    store.promote_return_model_draft(note="research", role="research")[
                        "success"
                    ]
                )
                self.assertTrue(
                    store.promote_return_model_draft(note="live", role="live")["success"]
                )
                os.remove(research)
                os.remove(draft)
                missing, meta = store.load_return_model(
                    prefer_active=True, prefer_research=True
                )
                self.assertIsNone(missing)
                self.assertFalse(meta.get("ok"))
                live_only, live_meta = store.load_return_model(
                    prefer_active=True, prefer_research=False
                )
                self.assertIsNotNone(live_only)
                self.assertAlmostEqual(float(live_only.intercept), 0.9)
                self.assertTrue(store.save_return_model_draft(draft_m)["success"])
                preview, preview_meta = store.load_return_model(
                    prefer_active=True, prefer_research=True
                )
                self.assertIsNotNone(preview)
                self.assertEqual(preview_meta.get("role"), "draft")
                self.assertAlmostEqual(float(preview.intercept), 0.11)

    def test_rescore_respects_research_scoring_context(self):
        """截面重打不能写死 prefer_active，否则会盖掉研究套 tip β。"""
        from core.research.feature_standardize import ZSCORE_SCOPE_CROSS_SECTION
        from core.research.holdout import scoring_model_role_context
        from core.signal.cross_section_rescore import rescore_fitted_cross_section

        live = ReturnScoreModel(
            intercept=0.0,
            coefficients={"CLOSE0": 0.65},
            feature_zscore=True,
            zscore_scope=ZSCORE_SCOPE_CROSS_SECTION,
            sample_count=20,
            horizon_days=1,
        )
        research_m = ReturnScoreModel(
            intercept=0.0,
            coefficients={"CLOSE0": 0.31},
            feature_zscore=True,
            zscore_scope=ZSCORE_SCOPE_CROSS_SECTION,
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
                self.assertTrue(store.save_return_model_draft(live)["success"])
                self.assertTrue(
                    store.promote_return_model_draft(note="research", role="research")[
                        "success"
                    ]
                )
                self.assertTrue(
                    store.promote_return_model_draft(note="live", role="live")["success"]
                )
                self.assertTrue(store.save_return_model_draft(research_m)["success"])
                self.assertTrue(
                    store.promote_return_model_draft(note="research", role="research")[
                        "success"
                    ]
                )
                items = [
                    {"code": "AAA", "sub_scores": {"CLOSE0": 1.0}},
                    {"code": "BBB", "sub_scores": {"CLOSE0": -1.0}},
                ]
                with scoring_model_role_context("research"):
                    rescore_fitted_cross_section(items)
                self.assertEqual(items[0].get("score_model_role"), "research")
                terms = items[0].get("score_formula_terms") or {}
                close_term = next(
                    (
                        t
                        for t in (terms.get("terms") or [])
                        if str(t.get("key") or "") == "CLOSE0"
                    ),
                    None,
                )
                self.assertIsNotNone(close_term)
                self.assertAlmostEqual(float(close_term.get("beta") or 0), 0.31)

if __name__ == "__main__":
    unittest.main()
