"""ŷ_τc_rank：与 ŷ_oo_rank 同协议的截面排序头。

标签：price[τ]→close[T]（开盘钟 τ=open 时即 close/open−1；百分点），embargo=0。
特征：alpha_τc（z_τc_* ∪ raw_atc_*），日截面 z-score。
面板按股落在 ``tc_rank/``；与 ŷ_τc 的 ``tc/`` 分片 fingerprint 一致时直接复制，不重算。
同窗 LambdaRank + Ridge，按 Holdout NDCG@K 选 backend。
研究枢纽启用只落盘；不进 y_oo_fuse，不改买序。
回测/live 写入当日截面名次；与 oo_rank 取 max 作「rank 入场」闸。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json

FORMULA_TC_RANK = "rank(price[τ]->close[T])"
SCHEMA = "tc_rank_v1"
TASK = "tc_rank"
DEFAULT_MIN_NAMES = 8
DEFAULT_L2 = 1.0
DEFAULT_TOPK_TRACK = 10
DEFAULT_NDCG_K = 10
DEFAULT_HOLDOUT_TRADING_DAYS_TC_RANK = 20

_MODEL_BASENAME = "tc_rank_model.json"
_LAST_REPORT_BASENAME = "tc_rank_last_report.json"


def tc_rank_model_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, _MODEL_BASENAME)


def tc_rank_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, _LAST_REPORT_BASENAME)


def _load_model_file(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001
        logger.debug("load tc_rank json failed: %s", path, exc_info=True)
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("return_model"), dict):
        return None
    if doc.get("success") is False:
        return None
    return doc


def load_tc_rank_model(*, prefer_research: bool = True) -> Optional[Dict[str, Any]]:
    """加载已启用模型。按槽严格取，缺文件返回 None（不跨槽回落）。

    - ``prefer_research=True``：只读研究套 sidecar
    - ``prefer_research=False``：只读执行套
    """
    live = tc_rank_model_path()
    if prefer_research:
        try:
            from core.research.holdout import load_research_promoted_json

            return load_research_promoted_json(live)
        except Exception:  # noqa: BLE001
            logger.debug("tc_rank research load failed", exc_info=True)
            return None
    return _load_model_file(live)


def save_tc_rank_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    if not isinstance(report.get("return_model"), dict):
        return
    from core.research.oo_rank_lambdarank import stamp_oo_rank_fitted_at

    stamp_oo_rank_fitted_at(report)
    path = tc_rank_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)


def load_tc_rank_last_report() -> Optional[Dict[str, Any]]:
    return _load_model_file(tc_rank_last_report_path()) or (
        _load_json_report(tc_rank_last_report_path())
    )


def _load_json_report(path: str) -> Optional[Dict[str, Any]]:
    """上次拟合草稿可以没有可推理包，只要 success。"""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001
        logger.debug("load tc_rank report failed: %s", path, exc_info=True)
        return None
    if not isinstance(doc, dict) or not doc.get("success"):
        return None
    return doc


def _relabel_metrics(raw: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(raw or {})
    if "topk_mean_y_oo" in out:
        out["topk_mean_y_tc"] = out.pop("topk_mean_y_oo")
    return out


def _slice_days(
    days: Sequence[Dict[str, Any]], feat_names: Sequence[str]
) -> List[Dict[str, Any]]:
    from core.research.panel_matrix import named_columns

    out: List[Dict[str, Any]] = []
    for day in days:
        x = day.get("X")
        names = day.get("names") or []
        if not isinstance(x, np.ndarray):
            continue
        sliced, kept = named_columns(x, names, feat_names)
        if not kept:
            continue
        row = dict(day)
        row["X"] = sliced
        row["names"] = kept
        out.append(row)
    return out


def _stamp_model(
    model: Dict[str, Any],
    *,
    backend: str,
    y_spec: Dict[str, Any],
    ndcg_k: int,
) -> Dict[str, Any]:
    out = dict(model)
    out["backend"] = backend
    out["y_spec"] = y_spec
    out["horizon_days"] = 0
    out["horizon_mode"] = "tc_rank"
    out["target"] = "tc_rank"
    out["model_role"] = "research"
    out["ndcg_k"] = int(ndcg_k)
    out.pop("shadow_only", None)
    if backend == "ridge":
        solver = str(out.get("solver") or "").strip().lower()
        if solver in {"", "lambdarank", "lightgbm_lambda"}:
            out["solver"] = "ridge"
    if backend == "lambdarank":
        out["intercept"] = 0.0
    return out


def fit_tc_rank_report(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    min_history: int = 12,
    min_names: int = DEFAULT_MIN_NAMES,
    holdout_trading_days: int = DEFAULT_HOLDOUT_TRADING_DAYS_TC_RANK,
    l2: float = DEFAULT_L2,
    topk_track: int = DEFAULT_TOPK_TRACK,
    ndcg_k: int = DEFAULT_NDCG_K,
    ridge_lambda: float = 1.0,
    backend: str = "auto",
    cross_section_zscore: bool = True,
) -> Dict[str, Any]:
    """拟合 ŷ_τc_rank。``stock_bars``: ``[{code, bars}, ...]``。不落盘。"""
    from core.research.holdout import (
        attach_holdout_meta,
        calendar_dates_from_stock_bars,
        make_research_model,
        resolve_ridge_split,
    )
    from core.research.oo_rank_lambdarank import (
        _fit_ridge_baseline,
        _oos_day_metrics,
        fit_lambdarank,
        select_oo_rank_backend,
        stamp_oo_rank_fitted_at,
    )
    from core.research.oo_rank_panel import group_day_matrices
    from core.research.panel_matrix import collect_tau_compact, raw_atc_finite
    from core.research.tc_ridge_compact import TAU_FIT_DROP_ALIASES, TAU_Z_FEATURES
    from core.signal.factors.z_tc import Z_TC_MINUTE_KEYS

    bars = [it for it in (stock_bars or []) if isinstance(it, dict)]
    calendar = calendar_dates_from_stock_bars(bars)
    X, col_names, ys, dates, metas, n_stocks = collect_tau_compact(
        bars,
        list(TAU_Z_FEATURES),
        min_history=int(min_history),
        tau_hm="open",
        include_alpha_tc=True,
        head="y_tc_rank",
        cache_head="tc_rank",
    )
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"rows_insufficient n={len(ys)}（需≥20）",
            "task": TASK,
            "n_rows": len(ys),
            "n_stocks": n_stocks,
        }

    _train_idx, _test_idx, split_meta = resolve_ridge_split(
        dates,
        holdout_trading_days=int(holdout_trading_days or DEFAULT_HOLDOUT_TRADING_DAYS_TC_RANK),
        label_horizon_days=0,
        calendar_dates=calendar,
    )
    train_days = set(split_meta.get("train_days") or [])
    test_days = set(split_meta.get("test_days") or [])
    drop = set(Z_TC_MINUTE_KEYS) | set(TAU_FIT_DROP_ALIASES)
    col_set = set(col_names)
    feat_names = [k for k in TAU_Z_FEATURES if k not in drop and k in col_set]
    seen = set(feat_names)
    train_row_idx = [i for i, d in enumerate(dates) if str(d)[:10] in train_days]
    for key in raw_atc_finite(X, col_names, train_row_idx):
        if key not in seen:
            seen.add(key)
            feat_names.append(key)

    codes_all = [
        str((metas[i] or {}).get("stock_code") or "") if i < len(metas) else ""
        for i in range(len(ys))
    ]
    days_all = _slice_days(
        group_day_matrices(
            X,
            np.asarray(ys, dtype=np.float64),
            col_names,
            dates,
            codes_all,
            min_names=min_names,
        ),
        feat_names,
    )
    days_tr = [d for d in days_all if str(d.get("date") or "")[:10] in train_days]
    days_te = [d for d in days_all if str(d.get("date") or "")[:10] in test_days]
    if len(days_tr) < 5 or len(days_te) < 1:
        return {
            "success": False,
            "error": f"split_insufficient train={len(days_tr)} test={len(days_te)}",
            "task": TASK,
            "n_days": len(days_all),
            "n_train_days": len(days_tr),
            "n_test_days": len(days_te),
        }

    force_backend = str(backend or "auto").strip().lower()
    if force_backend in {"lightgbm", "lgb", "lambdarank"}:
        force_backend = "lambdarank"
    if force_backend not in {"auto", "lambdarank", "ridge"}:
        return {
            "success": False,
            "error": f"unsupported backend={force_backend}",
            "task": TASK,
        }

    kept_names = list(days_tr[0].get("names") or feat_names)
    use_cs = bool(cross_section_zscore)
    fit_tr = fit_lambdarank(
        days_tr,
        feature_names=kept_names,
        l2=float(l2),
        cross_section_zscore=use_cs,
    )
    if not fit_tr.get("success"):
        return {
            "success": False,
            "error": fit_tr.get("error") or "lambdarank_fit_failed",
            "task": TASK,
            "n_days": len(days_all),
            "backend": "lambdarank",
        }

    oos_rank = _relabel_metrics(
        _oos_day_metrics(days_te, fit_tr, topk_track=topk_track, ndcg_k=ndcg_k)
    )
    ridge_tr = _fit_ridge_baseline(
        days_tr,
        feature_names=kept_names,
        ridge_lambda=float(ridge_lambda),
        cross_section_zscore=use_cs,
    )
    oos_ridge: Dict[str, Any] = {"success": bool(ridge_tr.get("success"))}
    if ridge_tr.get("success"):
        oos_ridge.update(
            _relabel_metrics(
                _oos_day_metrics(
                    days_te, ridge_tr, topk_track=topk_track, ndcg_k=ndcg_k
                )
            )
        )

    recommended = select_oo_rank_backend(oos_rank, oos_ridge)
    chosen = recommended if force_backend == "auto" else force_backend
    if chosen == "ridge" and not ridge_tr.get("success"):
        chosen = "lambdarank"

    y_spec_base = {
        "formula": FORMULA_TC_RANK,
        "unit": "score",
        "anchor": "open[T]",
        "label": "rank(price[τ]->close[T]) cross-section",
        "tau": "open",
    }

    def _pack(backend_key: str, holdout_src: Dict[str, Any]) -> Dict[str, Any]:
        note = (
            "Ridge 相对分；研究枢纽对照 y_τc 名次；不进 y_oo_fuse；不改买序；与 oo_rank 共用 rank 入场"
            if backend_key == "ridge"
            else "LambdaRank 相对分；研究枢纽对照 y_τc 名次；不进 y_oo_fuse；不改买序；与 oo_rank 共用 rank 入场"
        )
        y_spec = dict(y_spec_base, backend=backend_key, note=note)
        research = _stamp_model(
            make_research_model(holdout_src),
            backend=backend_key,
            y_spec=y_spec,
            ndcg_k=ndcg_k,
        )
        return {
            "return_model": research,
            "return_model_research": research,
            "y_spec": y_spec,
        }

    lambda_pack = _pack("lambdarank", dict(fit_tr))
    backends: Dict[str, Any] = {"lambdarank": lambda_pack}
    if ridge_tr.get("success"):
        backends["ridge"] = _pack("ridge", dict(ridge_tr))
    if chosen not in backends:
        chosen = "lambdarank"
    chosen_pack = backends[chosen]
    model = chosen_pack["return_model"]
    hold_n = int(split_meta.get("holdout_trading_days") or holdout_trading_days)

    def _delta(a: Any, b: Any) -> Optional[float]:
        if a is None or b is None:
            return None
        try:
            return round(float(a) - float(b), 4)
        except (TypeError, ValueError):
            return None

    report: Dict[str, Any] = {
        "success": True,
        "task": TASK,
        "schema": SCHEMA,
        "backend": chosen,
        "recommended_backend": recommended,
        "solver": str(model.get("solver") or ""),
        "n_days": len(days_all),
        "n_train_days": len(days_tr),
        "n_test_days": len(days_te),
        "n_stocks": n_stocks,
        "sample_count": int(model.get("sample_count") or 0),
        "n_features": len(kept_names),
        "ndcg_k": int(ndcg_k),
        "tau_hm": "open",
        "oos": {
            "tc_rank": oos_rank,
            "ridge_tc_baseline": oos_ridge,
            "holdout_trading_days": hold_n,
            "topk_track": int(topk_track),
            "ndcg_k": int(ndcg_k),
            "recommended_backend": recommended,
        },
        "shadow_track": {
            "delta_ndcg_at_k": _delta(oos_rank.get("ndcg_at_k"), oos_ridge.get("ndcg_at_k")),
            "delta_spearman": _delta(oos_rank.get("spearman"), oos_ridge.get("spearman")),
            "delta_cs_ic": _delta(oos_rank.get("cs_ic"), oos_ridge.get("cs_ic")),
            "delta_cs_icir": _delta(oos_rank.get("cs_icir"), oos_ridge.get("cs_icir")),
            "delta_topk_overlap": _delta(
                oos_rank.get("topk_overlap"), oos_ridge.get("topk_overlap")
            ),
            "delta_topk_mean_y_tc": _delta(
                oos_rank.get("topk_mean_y_tc"), oos_ridge.get("topk_mean_y_tc")
            ),
            "recommended_backend": recommended,
        },
        "backends": backends,
        "return_model": model,
        "return_model_research": chosen_pack["return_model_research"],
        "y_spec": chosen_pack["y_spec"],
        "cross_section_zscore": use_cs,
        "note": (
            f"ŷ_τc_rank backend={chosen}（NDCG@K 推荐 {recommended}）；"
            "只训 Holdout 前一套；启用研究→研究槽；启用执行=研究槽晋级；"
            "不进 y_oo_fuse；不改买序；与 oo_rank 共用 rank 入场"
        ),
    }
    attach_holdout_meta(report, split_meta)
    stamp_oo_rank_fitted_at(report)
    return report


def persist_tc_rank_model(
    report: Dict[str, Any], *, note: str = "", role: str = "live"
) -> Dict[str, Any]:
    """启用研究=报告→研究槽；启用执行=研究槽→执行槽。按 NDCG@K 选 backend。"""
    from core.numbers import now_iso_utc
    from core.research.holdout import (
        MODEL_ROLE_LIVE,
        MODEL_ROLE_RESEARCH,
        normalize_model_role,
        require_research_suite,
        research_model_path,
        select_fit_return_model,
        stamp_live_suite_doc,
    )
    from core.research.oo_rank_lambdarank import (
        _backend_pack_from_report,
        _oo_rank_model_valid,
        select_oo_rank_backend,
        stamp_oo_rank_fitted_at,
    )

    live = tc_rank_model_path()
    role_n = normalize_model_role(role)
    if role_n == MODEL_ROLE_LIVE:
        research_doc, err = require_research_suite(live)
        if err:
            return err
        doc = stamp_live_suite_doc(
            research_doc, note=note or "tc_rank research → live promote"
        )
        os.makedirs(os.path.dirname(live), exist_ok=True)
        atomic_write_json(live, doc)
        return {
            "success": True,
            "path": live,
            "promoted_at": doc.get("promoted_at"),
            "model_role": MODEL_ROLE_LIVE,
            "backend": doc.get("backend"),
            "recommended_backend": doc.get("recommended_backend") or doc.get("backend"),
            "promoted_from": "research",
            "live": live,
        }

    if not isinstance(report, dict) or not report.get("success"):
        return {
            "success": False,
            "error": report.get("error") if isinstance(report, dict) else "no report",
        }
    stamp_oo_rank_fitted_at(report)
    oos = report.get("oos") if isinstance(report.get("oos"), dict) else {}
    backend = select_oo_rank_backend(
        oos.get("tc_rank") if isinstance(oos, dict) else None,
        oos.get("ridge_tc_baseline") if isinstance(oos, dict) else None,
    )
    pack = _backend_pack_from_report(report, backend)
    view = dict(report)
    if isinstance(pack, dict):
        view["return_model"] = pack.get("return_model")
        if isinstance(pack.get("return_model_research"), dict):
            view["return_model_research"] = pack.get("return_model_research")
    model = select_fit_return_model(view)
    if not isinstance(model, dict):
        return {"success": False, "error": "return_model missing"}
    if not _oo_rank_model_valid(model):
        return {"success": False, "error": "return_model missing usable tc_rank pack"}
    model = dict(model)
    model["model_role"] = MODEL_ROLE_RESEARCH
    model["backend"] = backend
    model.pop("shadow_only", None)
    fitted_at = report.get("fitted_at") or model.get("fitted_at")
    promoted_at = now_iso_utc()
    y_spec = report.get("y_spec") or model.get("y_spec")
    if isinstance(y_spec, dict):
        y_spec = dict(y_spec)
        y_spec["backend"] = backend
    doc = {
        "success": True,
        "task": TASK,
        "schema": SCHEMA,
        "backend": backend,
        "recommended_backend": backend,
        "return_model": model,
        "return_model_research": dict(model),
        "oos": report.get("oos"),
        "y_spec": y_spec,
        "note": note or report.get("note") or f"tc_rank promote research backend={backend}",
        "n_days": report.get("n_days"),
        "n_train_days": report.get("n_train_days"),
        "n_test_days": report.get("n_test_days"),
        "sample_count": report.get("sample_count") or model.get("sample_count"),
        "shadow_track": report.get("shadow_track"),
        "fitted_at": fitted_at,
        "promoted_at": promoted_at,
        "model_role": MODEL_ROLE_RESEARCH,
    }
    backends = report.get("backends")
    if isinstance(backends, dict) and backends:
        doc["backends"] = backends
    path = research_model_path(live)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, doc)
    save_tc_rank_last_report(report)
    return {
        "success": True,
        "path": path,
        "promoted_at": promoted_at,
        "model_role": MODEL_ROLE_RESEARCH,
        "backend": backend,
        "recommended_backend": backend,
        "research": path,
    }


def _resolve_tc_rank_fit(model_doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """显式文档无效时不回落磁盘，避免误用别的头。"""
    from core.research.oo_rank_lambdarank import _oo_rank_model_valid

    if not isinstance(model_doc, dict):
        return None
    model = model_doc.get("return_model")
    if _oo_rank_model_valid(model):
        return model
    if _oo_rank_model_valid(model_doc):
        return model_doc
    return None


def apply_tc_rank_scores(
    items: Sequence[dict],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> List[dict]:
    """就地写入 y_tc_rank 当日截面名次。不改 y_oo_fuse / y_oo_rank / 买序。

    特征取 ``features_tau``（z_τc_* ∪ raw_atc_*），与训练同口径做日截面 z。
    缺模型或缺特征的行跳过。
    """
    from core.research.feature_standardize import (
        cross_section_zscore_dicts,
        is_cross_section_zscore,
    )
    from core.research.oo_rank_lambdarank import (
        _explain_oo_rank_lambdarank_rows,
        _explain_oo_rank_ridge,
        _oo_rank_feature_names,
        _oo_rank_use_booster,
        _oo_rank_use_ridge,
        _predict_oo_rank_rows,
    )

    model = _resolve_tc_rank_fit(model_doc)
    out = list(items or [])
    if model is None:
        return out
    names = _oo_rank_feature_names(model)
    raw_rows: List[dict] = []
    slots: List[int] = []
    for i, it in enumerate(out):
        if not isinstance(it, dict):
            continue
        feats = it.get("features_tau")
        if not isinstance(feats, dict) or not feats:
            continue
        raw_rows.append(dict(feats))
        slots.append(i)
    if not raw_rows:
        return out
    feat_rows = raw_rows
    if is_cross_section_zscore(model):
        z_names = names or list(raw_rows[0].keys())
        feat_rows = cross_section_zscore_dicts(raw_rows, [str(n) for n in z_names])
    hats = _predict_oo_rank_rows(model, feat_rows, impute_missing=True)
    for slot, hat in zip(slots, hats):
        if hat is None:
            continue
        try:
            v = round(float(hat), 6)
        except (TypeError, ValueError):
            continue
        out[slot]["y_tc_rank_score"] = v
    if feat_rows:
        try:
            if _oo_rank_use_booster(model):
                expls = _explain_oo_rank_lambdarank_rows(feat_rows, model, hats=hats)
                for slot, expl in zip(slots, expls):
                    _stamp_formula_terms_tc_rank(out[slot], expl)
            elif _oo_rank_use_ridge(model):
                for slot, feats, hat in zip(slots, feat_rows, hats):
                    expl = _explain_oo_rank_ridge(feats, model, y_hat=hat)
                    _stamp_formula_terms_tc_rank(out[slot], expl)
        except Exception:  # noqa: BLE001
            logger.debug("stamp formula_terms_tc_rank failed", exc_info=True)
    _assign_tc_rank_day_ranks(out)
    return out


def _stamp_formula_terms_tc_rank(item: dict, expl: Optional[Dict[str, Any]]) -> None:
    if not isinstance(item, dict) or not isinstance(expl, dict):
        return
    stamped = dict(expl)
    stamped["head"] = "tc_rank"
    item["formula_terms_tc_rank"] = stamped
    item["score_formula_terms_tc_rank"] = stamped


def _assign_tc_rank_day_ranks(items: Sequence[dict]) -> None:
    """相对分 → 1..n（1=最高）。只写 y_tc_rank*，不碰 y_oo_rank。"""
    rows: List[dict] = []
    for it in items or []:
        if isinstance(it, dict) and it.get("y_tc_rank_score") is not None:
            rows.append(it)

    def _key(it: dict) -> tuple:
        return (-float(it["y_tc_rank_score"]), str(it.get("stock_code") or ""))

    rows.sort(key=_key)
    n = len(rows)
    for i, it in enumerate(rows):
        rank = i + 1
        it["y_tc_rank"] = rank
        it["y_tc_rank_hat"] = rank
        it["predicted_score_tc_rank"] = rank
        it["y_tc_rank_n"] = n
