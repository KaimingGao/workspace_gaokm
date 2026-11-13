"""ŷ_oo：回归 LightGBM 拟合 / 草稿 / 研究·执行套落盘。

正式路径只训、只落盘、只打分 LightGBM（``oo_lgb``）；旧 Ridge 包不可加载打分 / 晋级。
单票面板 ``oo_panel``；矩阵堆叠 ``oo_model_compact``；拟合 ``fit_oo_lgb_matrix``。
路径仍用 ``RETURN_SCORE_MODEL_*``；对外另提供 ``oo_model_path`` 等别名。
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.research.oos_finite import as_float, finite_pairs, finite_xy, n_label_nan

from core.io_atomic import atomic_write_json
from core.paths import (
    LIVE_DIR,
    QUANT_REPORTS_DIR,
    RETURN_SCORE_MODEL_ACTIVE_PATH,
    RETURN_SCORE_MODEL_DRAFT_PATH,
    RETURN_SCORE_MODEL_RESEARCH_PATH,
)
from core.signal.return_score import clamp_rank_mode

logger = logging.getLogger(__name__)


# --- 路径（与 τ 的 tau_model_path / research_model_path 对称）---


def oo_model_path() -> str:
    """执行套 ŷ_oo 主路径（``return_score_model_active.json``）。"""
    return RETURN_SCORE_MODEL_ACTIVE_PATH


def oo_model_research_path() -> str:
    """研究套 ŷ_oo（``return_score_model_research.json``）。"""
    return RETURN_SCORE_MODEL_RESEARCH_PATH


def oo_model_draft_path() -> str:
    """拟合草稿（``last_return_score_model.json``）。"""
    return RETURN_SCORE_MODEL_DRAFT_PATH


def _ensure_dirs() -> None:
    os.makedirs(QUANT_REPORTS_DIR, exist_ok=True)
    os.makedirs(LIVE_DIR, exist_ok=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_model_dict(src: Any) -> Optional[Dict[str, Any]]:
    if src is None:
        return None
    if hasattr(src, "to_dict"):
        blob = src.to_dict()
        return dict(blob) if isinstance(blob, dict) else None
    if isinstance(src, dict):
        return dict(src)
    return None


def _usable_oo_model_blob(cand: Any) -> bool:
    """只认 LightGBM booster 包（旧 Ridge β 当缺）。"""
    if not isinstance(cand, dict):
        return False
    from core.research.oo_lgb import is_oo_lightgbm_blob

    return is_oo_lightgbm_blob(cand)


def _draft_persist_view(raw: Dict[str, Any]) -> Dict[str, Any]:
    """草稿 → 单份模型视图（LightGBM；取首个可用块并镜像到两键）。"""
    blob = None
    for key in (
        "return_model_research",
        "return_model",
        "research_model",
        "model",
    ):
        cand = raw.get(key)
        if _usable_oo_model_blob(cand):
            blob = dict(cand)
            break
    return {
        "return_model": dict(blob) if blob else None,
        "return_model_research": dict(blob) if blob else None,
    }


def save_return_model_draft(
    model: Any,
    *,
    meta: Optional[Dict[str, Any]] = None,
    oos: Optional[Dict[str, Any]] = None,
    research_ols: Optional[Dict[str, Any]] = None,
    backends: Optional[Dict[str, Any]] = None,
    recommended_backend: Optional[str] = None,
) -> Dict[str, Any]:
    """写入研究草稿 ``data/reports/last_return_score_model.json``（ŷ_oo LightGBM）。"""
    from core.research.oo_lgb import is_oo_lightgbm_blob

    _ensure_dirs()
    blob = _as_model_dict(model) if not isinstance(model, dict) else dict(model)
    if not isinstance(blob, dict):
        blob = {}
    if not is_oo_lightgbm_blob(blob):
        return {
            "success": False,
            "error": "ŷ_oo 已切换为 LightGBM；草稿须含 booster（旧 Ridge 不可落盘）",
            "path": RETURN_SCORE_MODEL_DRAFT_PATH,
        }
    blob.setdefault("model_role", "research")
    backend = str(recommended_backend or "lightgbm")
    payload: Dict[str, Any] = {
        "version": 2,
        "kind": "return_score_model",
        "role": "draft",
        "schema": "oo_lgb_v1",
        "backend": backend,
        "recommended_backend": backend,
        "fitted_at": _utc_now(),
        "saved_at": None,
        "model": dict(blob),
        "return_model": dict(blob),
        "research_model": dict(blob),
        "return_model_research": dict(blob),
        "meta": dict(meta or {}),
        "oos": dict(oos or {}) if isinstance(oos, dict) else {},
        "note": (
            "研究草稿（ŷ_oo LightGBM；Holdout 前训）；启用研究→回测；启用执行=研究槽晋级。"
        ),
    }
    if isinstance(backends, dict) and backends:
        payload["backends"] = dict(backends)
    if isinstance(research_ols, dict) and research_ols:
        payload["research_ols"] = dict(research_ols)
    payload["saved_at"] = payload["fitted_at"]
    atomic_write_json(RETURN_SCORE_MODEL_DRAFT_PATH, payload)
    n_feats = len((blob or {}).get("feature_names") or [])
    return {
        "success": True,
        "path": RETURN_SCORE_MODEL_DRAFT_PATH,
        "role": "draft",
        "saved_at": payload["saved_at"],
        "backend": backend,
        "sample_count": int((blob or {}).get("sample_count") or 0),
        "n_features": n_feats,
    }


def load_return_model_payload(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _suite_role_for_load(role: str, raw: Dict[str, Any], *, prefer_research: bool) -> str:
    """文件角色 → tip/回测用的 research|live。"""
    mr = str(raw.get("model_role") or "").strip().lower()
    rr = str(raw.get("role") or role or "").strip().lower()
    if mr == "research" or rr == "research":
        return "research"
    if mr == "live" or rr in {"active", "live"}:
        return "live"
    # 草稿：按本次加载意图（研究回测优先 Holdout β）
    if rr == "draft":
        return "research" if prefer_research else "live"
    return "research" if prefer_research else "live"


def _model_blob_for_load(
    role: str, raw: Dict[str, Any], *, prefer_research: bool = False
) -> Optional[Dict[str, Any]]:
    """选模型块：草稿/槽位均为同一份 Holdout 前训（Ridge β 或 LightGBM）。"""
    del role, prefer_research  # 单套；形参保留兼容调用方
    view = _draft_persist_view(raw)
    for blob in (
        view.get("return_model_research"),
        view.get("return_model"),
        raw.get("model"),
    ):
        if _usable_oo_model_blob(blob):
            return dict(blob)
    return raw if isinstance(raw, dict) else None


def _hydrate_oo_scorer(
    blob: Optional[Dict[str, Any]], *, suite: str
) -> Optional[Any]:
    """LightGBM → OoLightgbmScorer；旧 Ridge 包拒绝加载（正式唯一后端）。"""
    if not isinstance(blob, dict):
        return None
    from core.research.oo_lgb import OoLightgbmScorer, is_oo_lightgbm_blob

    if not is_oo_lightgbm_blob(blob):
        logger.warning(
            "refuse non-LightGBM ŷ_oo blob suite=%s (Ridge scoring retired)",
            suite,
        )
        return None
    scorer = OoLightgbmScorer.from_dict(blob)
    if scorer is not None:
        scorer.model_role = suite
    return scorer


def load_scoring_return_model() -> Tuple[Optional[Any], Dict[str, Any]]:
    """按 ``current_scoring_model_role`` 加载 ŷ_oo（回测研究套 / live 执行套）。

    调仓截面重打、score_stock 等打分路径应走这里，勿写死 ``prefer_active=True``。
    返回 ``OoLightgbmScorer``（无 LightGBM 包则 None）。
    """
    from core.research.holdout import MODEL_ROLE_RESEARCH, current_scoring_model_role

    prefer_research = current_scoring_model_role() == MODEL_ROLE_RESEARCH
    return load_return_model(prefer_active=True, prefer_research=prefer_research)


def load_return_model(
    *,
    prefer_active: bool = True,
    prefer_research: bool = False,
) -> Tuple[Optional[Any], Dict[str, Any]]:
    """读全局 ŷ_oo LightGBM 模型。

    - 默认（执行）：active → draft
    - ``prefer_research=True``（研究）：research → draft；**不**回落 active
    - 旧 Ridge 包跳过（不 hydrate）
    """
    paths = []
    if prefer_research:
        paths.append(("research", RETURN_SCORE_MODEL_RESEARCH_PATH))
        paths.append(("draft", RETURN_SCORE_MODEL_DRAFT_PATH))
    else:
        if prefer_active:
            paths.append(("active", RETURN_SCORE_MODEL_ACTIVE_PATH))
        paths.append(("draft", RETURN_SCORE_MODEL_DRAFT_PATH))
        if not prefer_active:
            paths.append(("active", RETURN_SCORE_MODEL_ACTIVE_PATH))

    seen = set()
    for role, path in paths:
        if path in seen:
            continue
        seen.add(path)
        raw = load_return_model_payload(path)
        if not raw:
            continue
        blob = _model_blob_for_load(role, raw, prefer_research=prefer_research)
        suite = _suite_role_for_load(role, raw, prefer_research=prefer_research)
        model = _hydrate_oo_scorer(blob, suite=suite)
        if model is None:
            continue
        return model, {
            "ok": True,
            "role": raw.get("role") or role,
            "model_role": suite,
            "backend": str(
                raw.get("backend")
                or raw.get("recommended_backend")
                or getattr(model, "solver", "")
                or "lightgbm"
            ),
            "path": path,
            "saved_at": raw.get("saved_at") or raw.get("promoted_at"),
            "meta": raw.get("meta") or {},
        }
    return None, {"ok": False, "reason": "no_model_file"}


def _guard_promote_coefs(
    model_blob: Dict[str, Any], *, note: str = ""
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """LightGBM：校验 booster / feature_names。旧 Ridge 拒晋级。"""
    del note  # 旧 Ridge force=1 已下线
    from core.research.oo_lgb import is_oo_lightgbm_blob

    if not is_oo_lightgbm_blob(model_blob):
        return None, {
            "success": False,
            "error": "ŷ_oo 已切换为 LightGBM；旧 Ridge 不可晋级，请重新拟合",
        }
    out = dict(model_blob)
    names = out.get("feature_names") or []
    if not isinstance(names, list) or not names:
        return None, {
            "success": False,
            "error": "LightGBM 包缺少 feature_names",
        }
    return out, None


def promote_return_model_draft(
    *,
    note: str = "",
    role: str = "live",
    refit_full_sample: bool = False,
) -> Dict[str, Any]:
    """拟合研究 = 草稿→研究槽；晋级执行 = 研究槽→执行槽。

    ``refit_full_sample=True``（仅 live）：要求已有研究套，把**当前草稿**
    （应由 Holdout=0 全样本重训写入）写入执行槽，研究槽保持不动；门禁依据
    仍来自研究套 OOS。
    """
    from core.research.holdout import (
        MODEL_ROLE_LIVE,
        MODEL_ROLE_RESEARCH,
        parse_persist_role,
        refuse_ungated_full_sample,
        select_fit_return_model,
        stamp_live_suite_doc,
    )

    role_n, role_err = parse_persist_role(role)
    if role_err:
        return {"success": False, "error": role_err}
    _ensure_dirs()

    if role_n == MODEL_ROLE_LIVE:
        # ŷ_oo 研究槽是独立文件名，不是 active 的 *_research sidecar
        research_doc = load_return_model_payload(RETURN_SCORE_MODEL_RESEARCH_PATH)
        has_research = isinstance(research_doc, dict) and (
            isinstance(research_doc.get("model"), dict)
            or isinstance(research_doc.get("return_model"), dict)
        )
        from core.research.oo_lgb import is_oo_lightgbm_blob

        if refit_full_sample:
            # Holdout=0 全样本草稿 → 执行槽；必须已有研究套，用来拷贝样本外门禁。
            raw_draft = load_return_model_payload(RETURN_SCORE_MODEL_DRAFT_PATH)
            from core.research.holdout import require_full_sample_report

            blocked_fs = require_full_sample_report(
                raw_draft if isinstance(raw_draft, dict) else None
            )
            if blocked_fs:
                return blocked_fs
            if not has_research:
                blocked_gate = refuse_ungated_full_sample(False)
                if blocked_gate:
                    return blocked_gate
            view = _draft_persist_view(raw_draft or {})
            selected = select_fit_return_model(view or {})
            coef_source = "full_sample_draft"
            promoted_from = "full_sample_refit"
            base_doc = dict(research_doc)
            note_default = (
                "执行套 ŷ_oo（Holdout=0 全样本重训；研究套 OOS 作门禁溯源）"
                "；scoring.rank_mode=predicted_score 时按 ŷ 排序。"
            )
        else:
            if not has_research:
                return {
                    "success": False,
                    "error": "无研究套可晋级；请先在研究视图点「拟合」",
                    "research_path": RETURN_SCORE_MODEL_RESEARCH_PATH,
                    "live_path": RETURN_SCORE_MODEL_ACTIVE_PATH,
                }
            selected = select_fit_return_model(research_doc) or research_doc.get("model")
            coef_source = "research_suite"
            promoted_from = "research"
            base_doc = research_doc
            note_default = (
                "执行套 ŷ_oo（由研究槽晋级）；scoring.rank_mode=predicted_score 时按 ŷ 排序。"
            )

        if not is_oo_lightgbm_blob(selected):
            return {
                "success": False,
                "error": (
                    "ŷ_oo 已切换为 LightGBM；"
                    + (
                        "全样本草稿须为 booster 包，请先 Holdout=0 重训"
                        if refit_full_sample
                        else "研究套须为 booster 包（旧 Ridge 不可晋级，请重新拟合）"
                    )
                ),
                "research_path": RETURN_SCORE_MODEL_RESEARCH_PATH,
            }
        model_blob, blocked = _guard_promote_coefs(selected, note=note)
        if blocked:
            return blocked
        from core.research.model_versions import refuse_incomplete_suite_doc

        blocked_inc = refuse_incomplete_suite_doc(
            {
                "sample_count": (model_blob or {}).get("sample_count"),
                "return_model": model_blob,
            },
            sink="执行套",
        )
        if blocked_inc:
            return blocked_inc
        live_doc = stamp_live_suite_doc(
            base_doc,
            note=note or note_default,
        )
        live_doc["role"] = "active"
        live_doc["model_role"] = MODEL_ROLE_LIVE
        live_doc["model"] = dict(model_blob)
        live_doc["return_model"] = dict(model_blob)
        # 全样本部署：执行权重来自草稿；门禁 OOS 来自研究套。
        if refit_full_sample and isinstance(research_doc.get("oos"), dict):
            live_doc["oos"] = dict(research_doc["oos"])
            live_doc["gate_oos_source"] = "research"
        if refit_full_sample:
            from core.research.holdout import stamp_full_sample_live_fitted_at

            stamp_full_sample_live_fitted_at(
                live_doc,
                source=raw_draft if isinstance(raw_draft, dict) else model_blob,
            )
            live_doc["deploy_full_sample"] = True
            live_doc["promoted_from"] = "full_sample_refit"
        be = str(
            model_blob.get("backend")
            or model_blob.get("solver")
            or base_doc.get("backend")
            or base_doc.get("recommended_backend")
            or "lightgbm"
        )
        live_doc["backend"] = be
        live_doc["recommended_backend"] = be
        live_doc["schema"] = str(
            model_blob.get("schema")
            or base_doc.get("schema")
            or "oo_lgb_v1"
        )
        if isinstance(base_doc.get("backends"), dict) and not refit_full_sample:
            live_doc["backends"] = dict(base_doc["backends"])
        elif refit_full_sample:
            live_doc["backends"] = {
                "lightgbm": {
                    "return_model": dict(model_blob),
                    "oos": dict((research_doc or {}).get("oos") or {}),
                }
            }
        meta_out = dict(live_doc.get("meta") or {})
        meta_out["promote_note"] = str(note or "")
        meta_out["persist_role"] = MODEL_ROLE_LIVE
        meta_out["coef_source"] = coef_source
        meta_out["promoted_from"] = promoted_from
        if refit_full_sample:
            meta_out["holdout_trading_days_fit"] = 0
        live_doc["meta"] = meta_out
        atomic_write_json(RETURN_SCORE_MODEL_ACTIVE_PATH, live_doc)
        out_live = {
            "success": True,
            "path": RETURN_SCORE_MODEL_ACTIVE_PATH,
            "role": MODEL_ROLE_LIVE,
            "persist_role": MODEL_ROLE_LIVE,
            "fitted_at": live_doc.get("fitted_at"),
            "promoted_at": live_doc.get("promoted_at"),
            "sample_count": model_blob.get("sample_count"),
            "coef_source": coef_source,
            "promoted_from": promoted_from,
            "refit_full_sample": bool(refit_full_sample),
            "deploy_full_sample": bool(refit_full_sample),
            "note": (
                "已全样本重训并写入执行套（研究套未改）；请确认 scoring.rank_mode=predicted_score。"
                if refit_full_sample
                else "已由研究套晋级写入执行套；请确认 scoring.rank_mode=predicted_score。"
            ),
        }
        from core.research.model_versions import archive_persist_safe

        archive_persist_safe("oo", MODEL_ROLE_LIVE, out_live)
        return out_live

    raw = load_return_model_payload(RETURN_SCORE_MODEL_DRAFT_PATH)
    view = _draft_persist_view(raw or {})
    selected = select_fit_return_model(view or {})
    from core.research.oo_lgb import is_oo_lightgbm_blob

    if not is_oo_lightgbm_blob(selected):
        return {
            "success": False,
            "error": "ŷ_oo 已切换为 LightGBM；无 booster 草稿（旧 Ridge 不可启用，请重新拟合）",
            "draft_path": RETURN_SCORE_MODEL_DRAFT_PATH,
        }
    model_blob, blocked = _guard_promote_coefs(selected, note=note)
    if blocked:
        return blocked
    from core.research.model_versions import refuse_incomplete_suite_doc

    blocked_inc = refuse_incomplete_suite_doc(
        {
            "sample_count": (model_blob or {}).get("sample_count"),
            "return_model": model_blob,
            "oos": raw.get("oos") if isinstance(raw, dict) else None,
        },
        sink="研究套",
    )
    if blocked_inc:
        blocked_inc["draft_path"] = RETURN_SCORE_MODEL_DRAFT_PATH
        return blocked_inc
    promoted_at = _utc_now()
    meta_out = {
        **(raw.get("meta") or {}),
        "promote_note": str(note or ""),
        "persist_role": MODEL_ROLE_RESEARCH,
        "coef_source": "return_model_research",
    }
    if isinstance(raw.get("research_ols"), dict):
        meta_out["research_ols"] = dict(raw.get("research_ols") or {})
    fitted_at = raw.get("fitted_at") or raw.get("saved_at")
    model_blob = dict(model_blob)
    model_blob["model_role"] = MODEL_ROLE_RESEARCH
    be = str(
        raw.get("backend")
        or raw.get("recommended_backend")
        or model_blob.get("backend")
        or model_blob.get("solver")
        or "lightgbm"
    )
    payload = {
        "version": int(raw.get("version") or 2),
        "kind": "return_score_model",
        "schema": str(
            model_blob.get("schema") or raw.get("schema") or "oo_lgb_v1"
        ),
        "backend": be,
        "recommended_backend": be,
        "role": "research",
        "model_role": MODEL_ROLE_RESEARCH,
        "fitted_at": fitted_at,
        "promoted_at": promoted_at,
        "source_saved_at": raw.get("saved_at") or fitted_at,
        "model": model_blob,
        "return_model": dict(model_blob),
        "return_model_research": dict(model_blob),
        "meta": meta_out,
        "oos": dict(raw.get("oos") or {}) if isinstance(raw.get("oos"), dict) else {},
        "note": f"研究套 ŷ_oo（Holdout 前训；backend={be}）；供历史回测；验证后可晋级执行套。",
    }
    if isinstance(raw.get("backends"), dict):
        payload["backends"] = dict(raw["backends"])
    atomic_write_json(RETURN_SCORE_MODEL_RESEARCH_PATH, payload)
    out_res = {
        "success": True,
        "path": RETURN_SCORE_MODEL_RESEARCH_PATH,
        "role": MODEL_ROLE_RESEARCH,
        "persist_role": MODEL_ROLE_RESEARCH,
        "promoted_at": promoted_at,
        "sample_count": model_blob.get("sample_count"),
        "coef_source": "return_model_research",
        "note": "已写入研究套；历史回测优先加载。验证后再「启用执行」晋级。",
    }
    from core.research.model_versions import archive_persist_safe

    archive_persist_safe("oo", MODEL_ROLE_RESEARCH, out_res)
    return out_res


def _payload_oos(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """取有评测意义的 oos（含日频 IC / 命中 / n_test 等）。"""
    if not isinstance(payload, dict):
        return None
    candidates = []
    if isinstance(payload.get("oos"), dict):
        candidates.append(payload["oos"])
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    if isinstance(meta.get("oos"), dict):
        candidates.append(meta["oos"])
    for oos in candidates:
        if (
            oos.get("ic") is not None
            or oos.get("cs_ic") is not None
            or oos.get("cs_rank_ic") is not None
            or oos.get("cs_icir") is not None
            or oos.get("sign_hit") is not None
            or oos.get("sign_hit_rate") is not None
            or oos.get("n_test") is not None
            or oos.get("holdout_trading_days") is not None
        ):
            return dict(oos)
    return None


def return_model_status() -> Dict[str, Any]:
    draft = load_return_model_payload(RETURN_SCORE_MODEL_DRAFT_PATH)
    active = load_return_model_payload(RETURN_SCORE_MODEL_ACTIVE_PATH)
    research = load_return_model_payload(RETURN_SCORE_MODEL_RESEARCH_PATH)
    from core.signal.config import load_signal_config

    cfg = load_signal_config()
    scoring = cfg.get("scoring") or {}

    def _ts(payload: Optional[Dict[str, Any]]) -> str:
        if not isinstance(payload, dict):
            return ""
        return str(
            payload.get("saved_at")
            or payload.get("promoted_at")
            or payload.get("source_saved_at")
            or ""
        )

    def _source_ts(payload: Optional[Dict[str, Any]]) -> str:
        """启用套的「来源草稿时间」；用于判断草稿是否比已启用更新。"""
        if not isinstance(payload, dict):
            return ""
        return str(
            payload.get("source_saved_at")
            or payload.get("saved_at")
            or payload.get("promoted_at")
            or ""
        )

    def _has_model(payload: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(payload, dict):
            return False
        for key in ("model", "return_model", "return_model_research"):
            if _usable_oo_model_blob(payload.get(key)):
                return True
        return False

    def _is_full_sample_oos_pack(payload: Optional[Dict[str, Any]]) -> bool:
        """Holdout=0 全样本草稿：无评估窗，不能抢研究套因子表 OOS。"""
        oos = _payload_oos(payload)
        if not isinstance(oos, dict):
            return False
        try:
            hold_n = int(oos.get("holdout_trading_days"))
        except (TypeError, ValueError):
            hold_n = -1
        if hold_n == 0:
            return True
        n_test = oos.get("n_test")
        try:
            n_test_i = int(n_test) if n_test is not None else -1
        except (TypeError, ValueError):
            n_test_i = -1
        return n_test_i == 0 and oos.get("eval_start") in (None, "")

    # 研究枢纽模型表：
    # 1. 草稿 saved_at 晚于任一启用套的 source → 画草稿（刚拟合、尚未启用）
    # 2. 否则画较新的启用套。研究槽来源草稿新于执行槽时画研究套
    #    （刚「启用研究」、执行仍是上一轮）；来源相同则画执行套（研究已晋级）
    # 3. Holdout=0 全样本草稿不抢展示（晋级执行重训用；因子表仍用研究套 OOS）
    display_role = None
    display_raw = None
    draft_ts = _ts(draft) if _has_model(draft) else ""
    if draft_ts and _is_full_sample_oos_pack(draft):
        draft_ts = ""
    active_src = _source_ts(active) if _has_model(active) else ""
    research_src = _source_ts(research) if _has_model(research) else ""
    live_src = max(active_src, research_src)
    if draft_ts and (not live_src or draft_ts > live_src):
        display_role = "draft"
        display_raw = draft
    elif research_src and (not active_src or research_src > active_src):
        display_role = "research"
        display_raw = research
    elif active_src:
        display_role = "active"
        display_raw = active
    elif _has_model(research):
        display_role = "research"
        display_raw = research
    elif _has_model(draft) and not _is_full_sample_oos_pack(draft):
        display_role = "draft"
        display_raw = draft

    return_model = None
    if display_raw:
        # 草稿：因子表优先 Holdout 研究套（LightGBM gain）；启用套仍读 model
        view = _draft_persist_view(display_raw)
        if display_role == "draft" and isinstance(view.get("return_model_research"), dict):
            return_model = dict(view["return_model_research"])
        elif isinstance(display_raw.get("model"), dict):
            return_model = dict(display_raw["model"])
        elif isinstance(view.get("return_model"), dict):
            return_model = dict(view["return_model"])
        if return_model is not None:
            from core.research.oo_lgb import is_oo_lightgbm_blob

            if not is_oo_lightgbm_blob(return_model):
                return_model = None
            else:
                meta = (
                    display_raw.get("meta")
                    if isinstance(display_raw.get("meta"), dict)
                    else {}
                )
                if (
                    return_model.get("r_squared") is None
                    and meta.get("r_squared") is not None
                ):
                    return_model["r_squared"] = meta.get("r_squared")
                return_model.setdefault("backend", "lightgbm")
                return_model.setdefault("solver", "lightgbm")
                return_model.pop("ridge_lambda", None)

    # OOS：与系数表同源；草稿更新时用草稿评测
    draft_oos = _payload_oos(draft)
    display_oos = _payload_oos(display_raw)
    oos_out = display_oos
    oos_source = display_role
    if draft_oos is not None:
        display_ts = _ts(display_raw)
        if oos_out is None or (draft_ts and draft_ts >= display_ts):
            oos_out = draft_oos
            oos_source = "draft"
    if oos_out is None:
        for role_name, payload in (("research", research), ("active", active)):
            oos_out = _payload_oos(payload)
            if oos_out is not None:
                oos_source = role_name
                break

    def _cs_z_bit(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        bits: Dict[str, Any] = {}
        if "cross_section_zscore" in meta:
            bits["cross_section_zscore"] = bool(meta.get("cross_section_zscore"))
        else:
            model_blob = payload.get("model")
            if isinstance(model_blob, dict) and (
                model_blob.get("zscore_scope")
                or model_blob.get("zscore_means")
                or model_blob.get("feature_zscore") is not None
            ):
                from core.research.feature_standardize import is_cross_section_zscore

                bits["cross_section_zscore"] = is_cross_section_zscore(model_blob)
        return bits

    # 摘要优先草稿（刚拟合 / 待启用）；否则展示套
    fit_bits = _cs_z_bit(draft) if draft else {}
    if "cross_section_zscore" not in fit_bits:
        fit_bits = _cs_z_bit(display_raw)

    def _suite_return_model(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return None
        view = _draft_persist_view(payload)
        cand = None
        if isinstance(payload.get("model"), dict):
            cand = dict(payload["model"])
        elif isinstance(view.get("return_model"), dict):
            cand = dict(view["return_model"])
        elif isinstance(view.get("return_model_research"), dict):
            cand = dict(view["return_model_research"])
        if cand is None:
            return None
        from core.research.oo_lgb import is_oo_lightgbm_blob

        if not is_oo_lightgbm_blob(cand):
            return None
        cand.setdefault("backend", "lightgbm")
        cand.setdefault("solver", "lightgbm")
        return cand

    research_rm = _suite_return_model(research)
    active_rm = _suite_return_model(active)
    research_oos = _payload_oos(research)
    active_oos = _payload_oos(active)

    out: Dict[str, Any] = {
        "success": True,
        "rank_mode": clamp_rank_mode(scoring.get("rank_mode")),
        "draft": {
            "exists": bool(draft),
            "path": RETURN_SCORE_MODEL_DRAFT_PATH,
            "fitted_at": (draft or {}).get("fitted_at") or (draft or {}).get("saved_at"),
            "saved_at": (draft or {}).get("saved_at"),
            "sample_count": ((draft or {}).get("model") or {}).get("sample_count"),
        },
        "active": {
            "exists": bool(active),
            "path": RETURN_SCORE_MODEL_ACTIVE_PATH,
            "fitted_at": (active or {}).get("fitted_at")
            or (active or {}).get("source_saved_at"),
            "promoted_at": (active or {}).get("promoted_at"),
            "source_saved_at": (active or {}).get("source_saved_at"),
            "sample_count": ((active or {}).get("model") or {}).get("sample_count"),
            "return_model": active_rm,
            "oos": active_oos,
        },
        "research": {
            "exists": bool(research),
            "path": RETURN_SCORE_MODEL_RESEARCH_PATH,
            "fitted_at": (research or {}).get("fitted_at")
            or (research or {}).get("source_saved_at"),
            "promoted_at": (research or {}).get("promoted_at"),
            "source_saved_at": (research or {}).get("source_saved_at"),
            "sample_count": ((research or {}).get("model") or {}).get("sample_count"),
            "return_model": research_rm,
            "oos": research_oos,
        },
        "display_role": display_role,
        "oos_source": oos_source,
        "return_model": return_model,
        "oos": oos_out,
        "note": "草稿 / 研究槽 / 执行槽；回测 prefer research，交易 prefer active；执行=研究晋级。",
    }
    # 同页研究|执行切换：压缩双套（研究带 OOS；执行仅因子）
    if research_rm is not None:
        out["suite_research"] = {
            "return_model": research_rm,
            "oos": research_oos,
            "fitted_at": out["research"].get("fitted_at"),
            "promoted_at": out["research"].get("promoted_at"),
            "backend": "lightgbm",
        }
    if active_rm is not None:
        active_meta = (active or {}).get("meta") if isinstance(active, dict) else None
        active_meta = active_meta if isinstance(active_meta, dict) else {}
        promoted_from = str(
            (active or {}).get("promoted_from")
            or active_meta.get("promoted_from")
            or ""
        )
        deploy_full = bool((active or {}).get("deploy_full_sample")) or (
            promoted_from == "full_sample_refit"
        )
        gate_oos = (active or {}).get("gate_oos_source")
        if gate_oos is None and deploy_full and research_rm is not None:
            gate_oos = "research"
        out["suite_live"] = {
            "return_model": active_rm,
            "oos": None,
            "fitted_at": out["active"].get("fitted_at"),
            "promoted_at": out["active"].get("promoted_at"),
            "backend": "lightgbm",
            "deploy_full_sample": deploy_full,
            "gate_oos_source": gate_oos,
            "promoted_from": promoted_from or None,
        }
    out.update(fit_bits)
    return out


def _oo_ic(preds: List[Optional[float]], ys: List[float]) -> Optional[float]:
    """预测与标签都有限才进 OOS IC。尾部 NaN 不污染 Pearson。"""
    pairs = finite_pairs(preds, ys)
    if len(pairs) < 5:
        return None
    n = len(pairs)
    mx = sum(p for p, _ in pairs) / n
    my = sum(y for _, y in pairs) / n
    num = sum((p - mx) * (y - my) for p, y in pairs)
    dx = math.sqrt(sum((p - mx) ** 2 for p, _ in pairs))
    dy = math.sqrt(sum((y - my) ** 2 for _, y in pairs))
    if dx < 1e-12 or dy < 1e-12:
        return None
    return round(num / (dx * dy), 4)


def _oo_sign_hit(preds: List[Optional[float]], ys: List[float]) -> Optional[float]:
    hits = 0
    n = 0
    for p, y in zip(preds, ys):
        if not finite_xy(p, y):
            continue
        if abs(float(p)) < 0.05:
            continue
        n += 1
        if (float(p) > 0 and float(y) > 0) or (float(p) < 0 and float(y) < 0):
            hits += 1
    if n < 5:
        return None
    return round(hits / n, 4)


def _oo_side_oos(
    preds_te: List[Optional[float]],
    ys_te: List[Optional[float]],
    *,
    all_dates: Sequence[str],
    test_idx: Sequence[int],
    train_idx: Sequence[int],
    split_meta: Dict[str, Any],
    hold_n: int,
    horizon_days: int,
) -> Dict[str, Any]:
    oos: Dict[str, Any] = {
        "n_train": len(train_idx),
        "n_test": len(test_idx),
        "holdout_trading_days": split_meta.get("holdout_trading_days") or hold_n,
        "fit_end": split_meta.get("fit_end"),
        "eval_start": split_meta.get("eval_start"),
        "label_horizon_days": int(horizon_days or 1),
        "n_label_nan": n_label_nan(ys_te),
    }
    if ys_te:
        oos["ic"] = _oo_ic(preds_te, ys_te)
        oos["sign_hit"] = _oo_sign_hit(preds_te, ys_te)
        oos["n_valid"] = sum(1 for p in preds_te if p is not None)
        try:
            from core.research.daily_cs_ic import attach_daily_cs_ic

            metas_te = [{"date": str(all_dates[i])[:10]} for i in test_idx]
            attach_daily_cs_ic(oos, preds_te, ys_te, metas_te)
        except Exception:  # noqa: BLE001
            logger.debug("oo attach_daily_cs_ic failed", exc_info=True)
    return oos


def fit_watching_return_model(
    *,
    codes: Optional[list] = None,
    lookback: int = 600,
    horizon_days: int = 1,
    ridge_lambda: float = 0.0,
    watching_limit: int = 12,
    min_samples: int = 24,
    save_draft: bool = True,
    holdout_trading_days: int = 20,
    cross_section_zscore: bool = True,
    progress_cb: Optional[Any] = None,
    job_alive_cb: Optional[Any] = None,
) -> Dict[str, Any]:
    """研究池堆叠面板：只训回归 LightGBM + Holdout OOS，可选落草稿。

    近 ``holdout_trading_days`` 个交易日只测；只训 Holdout 前一套。
    ``ridge_lambda`` 保留形参兼容 API，不参与拟合。
    拟合固定日截面 Z；``cross_section_zscore=False`` 已下线。
    ``progress_cb(cur, tot, msg)`` / ``job_alive_cb()``：后台 Job 进度与孤儿保护。
    """
    del ridge_lambda  # ŷ_oo 已切 LightGBM；形参保留兼容旧请求体
    from core.research.feature_standardize import reject_global_zscore
    from core.research.holdout import (
        DEFAULT_HOLDOUT_TRADING_DAYS,
        attach_holdout_meta,
        refuse_insufficient_holdout,
        resolve_ridge_split,
    )
    from core.research.oo_lgb import fit_oo_lgb_matrix, predict_oo_lgb_matrix

    err_cs = reject_global_zscore(cross_section_zscore)
    if err_cs:
        return {"success": False, "error": err_cs}

    def _prog(cur: int, tot: int, msg: str) -> None:
        if not callable(progress_cb):
            return
        try:
            progress_cb(int(cur), int(tot), str(msg or ""))
        except Exception:  # noqa: BLE001
            logger.debug("oo progress_cb failed", exc_info=True)

    def _job_alive() -> bool:
        if not callable(job_alive_cb):
            return True
        try:
            return bool(job_alive_cb())
        except Exception:  # noqa: BLE001
            logger.debug("oo job_alive_cb failed", exc_info=True)
            return False

    from core.research.oo_model_compact import assemble_oo_panels
    from core.research.portfolio_bars import load_portfolio_stock_bars
    from core.research_universe import resolve_model_fit_codes
    from core.watching.store import MODEL_FIT_MAX_SIZE

    if codes:
        resolved = resolve_model_fit_codes(watching_limit=watching_limit, codes=codes)
    else:
        resolved = resolve_model_fit_codes(watching_limit=watching_limit)
    use_codes = list(resolved.get("codes") or [])
    limit = max(3, min(int(watching_limit or MODEL_FIT_MAX_SIZE), int(MODEL_FIT_MAX_SIZE)))
    use_codes = use_codes[:limit]
    if len(use_codes) < 2:
        return {"success": False, "error": "标的不足 2 只"}

    import time as _time

    t0 = _time.perf_counter()
    logger.info(
        "oo fit start stocks=%s lookback=%s holdout=%s",
        len(use_codes),
        lookback,
        holdout_trading_days,
    )
    _prog(0, max(1, len(use_codes)), f"ŷ_oo 拉日线 {len(use_codes)} 只…")
    stock_bars, failures, _fund = load_portfolio_stock_bars(
        use_codes,
        lookback=lookback,
        fetch_fundamentals=False,
    )
    if len(stock_bars) < 2:
        return {
            "success": False,
            "error": f"有效日线不足（失败 {len(failures)}）",
            "failures": failures[:8],
        }
    logger.info(
        "oo fit bars loaded stocks=%s elapsed=%.1fs",
        len(stock_bars),
        _time.perf_counter() - t0,
    )

    codes_used = list(stock_bars.keys())
    as_of = None
    cal_days = set()
    items: List[Tuple[str, list]] = []
    for code in codes_used:
        bars = stock_bars.pop(code, None) or []
        if bars and as_of is None:
            as_of = str(bars[-1].get("date") or "") or None
        for bar in bars:
            if not isinstance(bar, dict):
                continue
            day = str(bar.get("date") or bar.get("trade_date") or "")[:10]
            if len(day) >= 10:
                cal_days.add(day)
        items.append((code, bars))
    t_panel = _time.perf_counter()
    _prog(0, max(1, len(items)), f"ŷ_oo 组面板 0/{len(items)}")
    x_mat, y_mat, feat_names, all_dates = assemble_oo_panels(
        items,
        horizon_days=horizon_days,
        progress_cb=progress_cb,
    )
    del items
    shape = getattr(x_mat, "shape", None) or ()
    n_rows = int(shape[0]) if len(shape) >= 1 else 0
    n_cols = int(shape[1]) if len(shape) >= 2 else 0
    logger.info(
        "oo fit panel rows=%s cols=%s feats=%s elapsed=%.1fs",
        n_rows,
        n_cols,
        len(feat_names or []),
        _time.perf_counter() - t_panel,
    )

    if holdout_trading_days is None:
        hold_n = int(DEFAULT_HOLDOUT_TRADING_DAYS)
    else:
        try:
            hold_n = max(0, int(holdout_trading_days))
        except (TypeError, ValueError):
            hold_n = int(DEFAULT_HOLDOUT_TRADING_DAYS)
    train_idx, test_idx, split_meta = resolve_ridge_split(
        all_dates,
        holdout_trading_days=hold_n,
        label_horizon_days=int(horizon_days or 1),
        calendar_dates=sorted(cal_days),
    )
    blocked_split = refuse_insufficient_holdout(split_meta)
    if blocked_split:
        blocked_split["task"] = "oo_lgb"
        return blocked_split
    ys_te = [as_float(y_mat[i]) for i in test_idx]

    try:
        t_lgb = _time.perf_counter()
        logger.info(
            "oo fit lightgbm begin n_train=%s n_test=%s",
            len(train_idx),
            len(test_idx),
        )
        _prog(0, 1, f"ŷ_oo LightGBM · 训 {len(train_idx)} 行…")
        lgb_blob, lgb_report = fit_oo_lgb_matrix(
            x_mat,
            y_mat,
            feat_names,
            row_idx=train_idx,
            horizon_days=horizon_days,
            min_samples=min_samples,
            fitted_as_of=as_of,
            row_dates=all_dates,
            cross_section_zscore=True,
        )
        logger.info(
            "oo fit lightgbm done elapsed=%.1fs total=%.1fs",
            _time.perf_counter() - t_lgb,
            _time.perf_counter() - t0,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("oo lightgbm fit failed: %s", exc, exc_info=True)
        return {
            "success": False,
            "error": str(exc) or "lightgbm_fit_failed",
            "stock_count": len(codes_used),
        }

    if lgb_blob is None:
        return {
            "success": False,
            "error": (lgb_report or {}).get("error") or "LightGBM 拟合失败",
            "report": lgb_report,
            "stock_count": len(codes_used),
        }

    lgb_blob = dict(lgb_blob)
    lgb_blob["model_role"] = "research"
    lgb_blob["backend"] = "lightgbm"
    oos: Dict[str, Any] = {
        "n_train": len(train_idx),
        "n_test": len(test_idx),
        "holdout_trading_days": split_meta.get("holdout_trading_days") or hold_n,
        "fit_end": split_meta.get("fit_end"),
        "eval_start": split_meta.get("eval_start"),
        "label_horizon_days": int(horizon_days or 1),
        "n_label_nan": n_label_nan(ys_te),
        "recommended_backend": "lightgbm",
    }
    if ys_te:
        preds = predict_oo_lgb_matrix(
            lgb_blob,
            x_mat,
            feat_names,
            test_idx,
            row_dates=all_dates,
        )
        oos = _oo_side_oos(
            preds,
            ys_te,
            all_dates=all_dates,
            test_idx=test_idx,
            train_idx=train_idx,
            split_meta=split_meta,
            hold_n=hold_n,
            horizon_days=horizon_days,
        )
        oos["recommended_backend"] = "lightgbm"

    backends = {
        "lightgbm": {
            "return_model": dict(lgb_blob),
            "return_model_research": dict(lgb_blob),
            "oos": oos,
        }
    }
    out: Dict[str, Any] = {
        "success": True,
        "task": "fit_return_score_model",
        "promote_ready": False,
        "backend": "lightgbm",
        "recommended_backend": "lightgbm",
        "backends": backends,
        "model": lgb_blob,
        "research_model": lgb_blob,
        "return_model": lgb_blob,
        "return_model_research": lgb_blob,
        "sample_count": int(lgb_blob.get("sample_count") or 0),
        "stock_count": len(codes_used),
        "codes": codes_used,
        "failures": failures[:8],
        "oos": oos,
        "lgb": {
            "success": True,
            "n_features": (lgb_report or {}).get("n_features"),
            "sample_count": (lgb_report or {}).get("sample_count"),
            "excluded_features": (lgb_report or {}).get("excluded_features"),
        },
        "note": "已拟合 LightGBM（Holdout 前训）+ Holdout OOS；启用研究→回测；启用执行=研究槽晋级。",
    }
    out["cross_section_zscore"] = True
    attach_holdout_meta(out, split_meta)
    if not _job_alive():
        out["success"] = False
        out["error"] = "任务已中断或被接替"
        out["orphaned"] = True
        return out
    if save_draft:
        saved = save_return_model_draft(
            lgb_blob,
            meta={
                "lookback": lookback,
                "horizon_days": horizon_days,
                "codes": codes_used,
                "holdout_trading_days": oos.get("holdout_trading_days"),
                "cross_section_zscore": True,
                "backend": "lightgbm",
            },
            oos=oos,
            backends=backends,
            recommended_backend="lightgbm",
        )
        out["draft"] = saved
    return out


# --- τ 风格别名（新代码优先用这些名字）---

persist_oo_model = promote_return_model_draft
oo_model_status = return_model_status
fit_oo_model = fit_watching_return_model
fit_oo_lgb = fit_watching_return_model
