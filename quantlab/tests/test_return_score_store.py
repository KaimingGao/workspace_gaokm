"""ŷ_oo LightGBM 草稿 / promote（实现在 ``core.research.oo_model``）。"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from core.research import oo_model as store
from core.signal.return_score import ReturnScoreModel


def _lgb_blob(
    *,
    sample_count: int = 40,
    horizon_days: int = 1,
    names: tuple = ("momentum", "value"),
    mark: float = 0.1,
) -> dict:
    """最小可晋级 LightGBM 包；``mark`` 写入包内便于断言身份。"""
    ns = [str(n) for n in names]
    return {
        "feature_names": ns,
        "booster": {"format": "lightgbm_string", "payload_b64": "bW9kZWw="},
        "schema": "oo_lgb_v1",
        "backend": "lightgbm",
        "solver": "lightgbm",
        "zscore_scope": "cross_section",
        "feature_zscore": True,
        "sample_count": int(sample_count),
        "horizon_days": int(horizon_days),
        "impute_means": {n: 0.0 for n in ns},
        "mark": float(mark),
        "feature_importance": [
            {"key": n, "gain": 1.0, "share": round(1.0 / max(1, len(ns)), 4)} for n in ns
        ],
    }


def _patch_paths(td: str):
    draft = os.path.join(td, "draft.json")
    active = os.path.join(td, "active.json")
    research = os.path.join(td, "research.json")
    return (
        patch.object(store, "RETURN_SCORE_MODEL_DRAFT_PATH", draft),
        patch.object(store, "RETURN_SCORE_MODEL_ACTIVE_PATH", active),
        patch.object(store, "RETURN_SCORE_MODEL_RESEARCH_PATH", research),
        patch.object(store, "QUANT_REPORTS_DIR", td),
        patch.object(store, "LIVE_DIR", td),
        draft,
        active,
        research,
    )


class TestReturnScoreStore(unittest.TestCase):
    def test_draft_promote_roundtrip(self):
        blob = _lgb_blob(sample_count=40, mark=0.1, names=("momentum", "value"))
        with tempfile.TemporaryDirectory() as td:
            p_draft, p_active, p_research, p_rep, p_live, draft, active, research = (
                _patch_paths(td)
            )
            with p_draft, p_active, p_research, p_rep, p_live:
                saved = store.save_return_model_draft(blob, meta={"lookback": 60})
                self.assertTrue(saved["success"])
                self.assertTrue(os.path.isfile(draft))
                loaded, meta = store.load_return_model(prefer_active=True)
                self.assertIsNotNone(loaded)
                self.assertEqual(meta["role"], "draft")
                self.assertEqual(getattr(loaded, "solver", ""), "lightgbm")
                self.assertIn("momentum", getattr(loaded, "feature_names", []))
                bad_live = store.promote_return_model_draft(note="test", role="live")
                self.assertFalse(bad_live.get("success"))
                prom_r = store.promote_return_model_draft(note="test", role="research")
                self.assertTrue(prom_r["success"], prom_r)
                self.assertTrue(os.path.isfile(research))
                prom = store.promote_return_model_draft(note="test", role="live")
                self.assertTrue(prom["success"], prom)
                self.assertTrue(os.path.isfile(active))
                loaded2, meta2 = store.load_return_model(prefer_active=True)
                self.assertEqual(meta2["role"], "active")
                self.assertEqual(int(getattr(loaded2, "sample_count", 0)), 40)

    def test_promote_rejects_ridge_draft(self):
        ridge = ReturnScoreModel(
            intercept=0.1,
            coefficients={"momentum": 0.5},
            feature_zscore=False,
            sample_count=10,
            horizon_days=1,
        )
        with tempfile.TemporaryDirectory() as td:
            p_draft, p_active, p_research, p_rep, p_live, draft, _a, _r = _patch_paths(td)
            with p_draft, p_active, p_research, p_rep, p_live:
                # 草稿落盘即拒旧 Ridge（与打分 / 晋级同口径）
                saved = store.save_return_model_draft(ridge)
                self.assertFalse(saved.get("success"))
                self.assertIn("LightGBM", str(saved.get("error") or ""))
                loaded, meta = store.load_return_model(prefer_active=True)
                self.assertIsNone(loaded)
                self.assertFalse(meta.get("ok"))
                bad = store.promote_return_model_draft(note="x", role="research")
                self.assertFalse(bad.get("success"))
                self.assertIn("LightGBM", str(bad.get("error") or ""))

    def test_promote_research_and_prefer_research(self):
        blob = _lgb_blob(sample_count=12, mark=0.11, names=("momentum",))
        with tempfile.TemporaryDirectory() as td:
            p_draft, p_active, p_research, p_rep, p_live, draft, active, research = (
                _patch_paths(td)
            )
            with p_draft, p_active, p_research, p_rep, p_live:
                self.assertTrue(store.save_return_model_draft(blob)["success"])
                os.remove(draft)
                bad = store.promote_return_model_draft(note="research", role="research")
                self.assertFalse(bad.get("success"))
                store.save_return_model_draft(blob)
                bad_live = store.promote_return_model_draft(note="live", role="live")
                self.assertFalse(bad_live.get("success"))
                prom = store.promote_return_model_draft(note="research", role="research")
                self.assertTrue(prom["success"])
                self.assertEqual(prom["role"], "research")
                loaded, meta = store.load_return_model(
                    prefer_active=True, prefer_research=True
                )
                self.assertIsNotNone(loaded)
                self.assertEqual(meta["role"], "research")
                self.assertEqual(getattr(loaded, "model_role", None), "research")
                self.assertEqual(int(getattr(loaded, "sample_count", 0)), 12)
                st = store.return_model_status()
                self.assertTrue(st["research"]["exists"])
                self.assertFalse(st["active"]["exists"])
                self.assertEqual(st["display_role"], "research")
                self.assertEqual(
                    (st.get("return_model") or {}).get("backend")
                    or (st.get("return_model") or {}).get("solver"),
                    "lightgbm",
                )
                self.assertTrue(
                    store.promote_return_model_draft(note="live", role="live")["success"]
                )
                st2 = store.return_model_status()
                self.assertEqual(st2["display_role"], "active")

    def test_status_shows_research_while_active_is_stale(self):
        old = _lgb_blob(sample_count=40, mark=0.05, names=("YKLEN", "OMA30"))
        new = _lgb_blob(sample_count=41, mark=0.047, names=("YKLEN", "OMA30"))
        with tempfile.TemporaryDirectory() as td:
            p_draft, p_active, p_research, p_rep, p_live, draft, active, research = (
                _patch_paths(td)
            )
            with p_draft, p_active, p_research, p_rep, p_live:
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
                self.assertEqual(
                    int((fitted.get("return_model") or {}).get("sample_count") or 0), 41
                )
                self.assertTrue(
                    store.promote_return_model_draft(note="", role="research")["success"]
                )
                researched = store.return_model_status()
                self.assertEqual(researched.get("display_role"), "research")
                self.assertEqual(
                    int((researched.get("return_model") or {}).get("sample_count") or 0),
                    41,
                )
                self.assertTrue(
                    store.promote_return_model_draft(note="", role="live")["success"]
                )
                executed = store.return_model_status()
                self.assertEqual(executed.get("display_role"), "active")
                self.assertEqual(
                    int((executed.get("return_model") or {}).get("sample_count") or 0),
                    41,
                )

    def test_prefer_research_draft_uses_holdout_blob(self):
        blob = _lgb_blob(sample_count=12, mark=0.11, names=("momentum",))
        with tempfile.TemporaryDirectory() as td:
            p_draft, p_active, p_research, p_rep, p_live, *_ = _patch_paths(td)
            with p_draft, p_active, p_research, p_rep, p_live:
                self.assertTrue(store.save_return_model_draft(blob)["success"])
                loaded, meta = store.load_return_model(
                    prefer_active=True, prefer_research=True
                )
                self.assertIsNotNone(loaded)
                self.assertEqual(meta.get("role"), "draft")
                self.assertEqual(meta.get("model_role"), "research")
                self.assertEqual(int(getattr(loaded, "sample_count", 0)), 12)
                live_loaded, live_meta = store.load_return_model(
                    prefer_active=True, prefer_research=False
                )
                self.assertIsNotNone(live_loaded)
                self.assertEqual(live_meta.get("model_role"), "live")
                self.assertEqual(int(getattr(live_loaded, "sample_count", 0)), 12)

    def test_draft_writes_return_model_aliases_and_status_prefers_research(self):
        blob = _lgb_blob(sample_count=80, mark=0.07, names=("OPEN0",))
        with tempfile.TemporaryDirectory() as td:
            p_draft, p_active, p_research, p_rep, p_live, draft, *_ = _patch_paths(td)
            with p_draft, p_active, p_research, p_rep, p_live:
                self.assertTrue(store.save_return_model_draft(blob)["success"])
                raw = store.load_return_model_payload(draft)
                self.assertIsInstance((raw or {}).get("return_model"), dict)
                self.assertIsInstance((raw or {}).get("return_model_research"), dict)
                self.assertEqual(
                    int(((raw or {}).get("return_model") or {}).get("sample_count") or 0),
                    80,
                )
                self.assertEqual((raw or {}).get("backend"), "lightgbm")
                st = store.return_model_status()
                self.assertEqual(st.get("display_role"), "draft")
                self.assertEqual(
                    int((st.get("return_model") or {}).get("sample_count") or 0), 80
                )

    def test_draft_persists_oos_for_status(self):
        blob = _lgb_blob(sample_count=40, names=("momentum",))
        oos = {
            "n_train": 100,
            "n_test": 20,
            "holdout_trading_days": 10,
            "ic": 0.12,
            "sign_hit": 0.58,
            "cs_rank_ic": 0.03,
        }
        with tempfile.TemporaryDirectory() as td:
            p_draft, p_active, p_research, p_rep, p_live, *_ = _patch_paths(td)
            with p_draft, p_active, p_research, p_rep, p_live:
                saved = store.save_return_model_draft(
                    blob, oos=oos, meta={"backend": "lightgbm"}
                )
                self.assertTrue(saved["success"])
                st = store.return_model_status()
                self.assertEqual(st.get("display_role"), "draft")
                self.assertAlmostEqual(float((st.get("oos") or {}).get("ic") or 0), 0.12)

    def test_status_oos_falls_back_after_promote(self):
        blob = _lgb_blob(sample_count=40, names=("momentum",))
        oos = {"ic": 0.2, "sign_hit": 0.55, "n_test": 15}
        with tempfile.TemporaryDirectory() as td:
            p_draft, p_active, p_research, p_rep, p_live, draft, active, research = (
                _patch_paths(td)
            )
            with p_draft, p_active, p_research, p_rep, p_live:
                self.assertTrue(store.save_return_model_draft(blob, oos=oos)["success"])
                self.assertTrue(
                    store.promote_return_model_draft(note="", role="research")["success"]
                )
                self.assertTrue(
                    store.promote_return_model_draft(note="", role="live")["success"]
                )
                time.sleep(1.05)
                self.assertTrue(store.save_return_model_draft(blob, oos=oos)["success"])
                with open(active, encoding="utf-8") as f:
                    live_doc = json.load(f)
                live_doc.pop("oos", None)
                with open(active, "w", encoding="utf-8") as f:
                    json.dump(live_doc, f)
                st = store.return_model_status()
                self.assertIsInstance(st.get("oos"), dict)

    def test_load_scoring_return_model_follows_context(self):
        live = _lgb_blob(sample_count=30, mark=0.2, names=("momentum",))
        research_m = _lgb_blob(sample_count=12, mark=0.11, names=("momentum",))
        with tempfile.TemporaryDirectory() as td:
            p_draft, p_active, p_research, p_rep, p_live, draft, active, research = (
                _patch_paths(td)
            )
            with p_draft, p_active, p_research, p_rep, p_live:
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
                self.assertEqual(int(getattr(live_loaded, "sample_count", 0)), 30)
                from core.research.holdout import scoring_model_role_context

                with scoring_model_role_context("research"):
                    r_loaded, r_meta = store.load_scoring_return_model()
                    self.assertEqual(int(getattr(r_loaded, "sample_count", 0)), 12)
                    self.assertEqual(r_meta.get("model_role"), "research")

    def test_prefer_research_missing_falls_to_draft_not_active(self):
        live = _lgb_blob(sample_count=30, mark=0.2, names=("momentum",))
        draft_m = _lgb_blob(sample_count=9, mark=0.09, names=("momentum",))
        with tempfile.TemporaryDirectory() as td:
            p_draft, p_active, p_research, p_rep, p_live, draft, active, research = (
                _patch_paths(td)
            )
            with p_draft, p_active, p_research, p_rep, p_live:
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
                live_only, live_meta = store.load_return_model(
                    prefer_active=True, prefer_research=False
                )
                self.assertEqual(int(getattr(live_only, "sample_count", 0)), 30)
                self.assertTrue(store.save_return_model_draft(draft_m)["success"])
                preview, preview_meta = store.load_return_model(
                    prefer_active=True, prefer_research=True
                )
                self.assertEqual(int(getattr(preview, "sample_count", 0)), 9)
                self.assertEqual(preview_meta.get("role"), "draft")


if __name__ == "__main__":
    unittest.main()
