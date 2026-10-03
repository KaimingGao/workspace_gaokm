"""隔夜缺口 Ridge：Z 上拟合 open[T+1]/close[T]-1；风控旁路 ŷ_co。"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.research.co_panel import (
    CO_LAG_FEAT_LABELS,
    CO_Z_FEATURES,
    theme_sample_weights,
)
from core.research.tc_ridge import (
    _ic,
    _oos_by_theme,
    _predict_rows,
    _residual_var,
    _sign_hit,
)

CO_MIN_STD_EXEMPT = CO_Z_FEATURES


def fit_co_ridge_report(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    ridge_lambda: float = 1.0,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    theme_boost: float = 1.5,
    holdout_trading_days: int = 20,
    use_theme_weights: bool = True,
    include_alpha158: bool = True,
) -> Dict[str, Any]:
    """池化拟合 ŷ_co(Z) + 时间 OOS。标签 = open[T+1]/close[T]-1（决策日 T 开盘）。"""
    from core.research.panel_matrix import (
        collect_co_compact,
        fill_rates_from_matrix,
        fit_keepall_ridge_matrix,
        named_columns,
        predict_ridge_matrix,
        raw_alpha158_finite,
    )

    X, col_names, ys, dates, metas, n_stocks = collect_co_compact(
        stock_bars,
        list(CO_Z_FEATURES),
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        include_alpha158=include_alpha158,
        head="y_co",
    )
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"on 样本不足 n={len(ys)}（需≥20）",
            "task": "co_ridge",
            "sample_count": len(ys),
            "stock_count": n_stocks,
        }

    from core.research.holdout import (
        DEFAULT_HOLDOUT_TRADING_DAYS,
        attach_holdout_meta,
        calendar_dates_from_stock_bars,
        make_research_model,
        resolve_ridge_split,
    )

    hold_n = int(holdout_trading_days or DEFAULT_HOLDOUT_TRADING_DAYS)
    train_idx, test_idx, split_meta = resolve_ridge_split(
        dates,
        holdout_trading_days=hold_n,
        label_horizon_days=1,
        calendar_dates=calendar_dates_from_stock_bars(stock_bars),
    )
    ys_tr = [ys[i] for i in train_idx]
    ys_te = [ys[i] for i in test_idx]
    metas_tr = [metas[i] for i in train_idx]
    metas_te = [metas[i] for i in test_idx]

    weights = (
        theme_sample_weights(metas_tr, theme_boost=theme_boost)
        if use_theme_weights
        else None
    )

    feat_names = list(CO_Z_FEATURES)
    if include_alpha158:
        for k in raw_alpha158_finite(X, col_names, train_idx):
            if k not in feat_names:
                feat_names.append(k)
    fill_keys = list(CO_Z_FEATURES)
    seen_fill = set(fill_keys)
    for name in col_names:
        if str(name).startswith("raw_alpha158_") and name not in seen_fill:
            seen_fill.add(name)
            fill_keys.append(name)
    feature_fill = fill_rates_from_matrix(X, col_names, fill_keys)
    X_fit, feat_names = named_columns(X, col_names, feat_names)
    del X

    from core.signal.factors.alpha158 import merge_alpha158_min_std_exempt

    min_std_exempt = merge_alpha158_min_std_exempt(
        feat_names, list(CO_MIN_STD_EXEMPT)
    )

    row_tr = np.asarray(train_idx, dtype=np.int64)
    row_te = np.asarray(test_idx, dtype=np.int64)
    fit = fit_keepall_ridge_matrix(
        X_fit[row_tr],
        ys_tr,
        feat_names,
        ridge_lambda=ridge_lambda,
        sample_weights=weights,
        min_std_exempt=min_std_exempt,
    )
    if not fit.get("success"):
        mu = sum(float(y) for y in ys_tr) / max(1, len(ys_tr))
        fit = {
            "success": True,
            "intercept": round(mu, 6),
            "coefficients": {},
            "active_features": [],
            "zscore_means": {},
            "zscore_stds": {},
            "note": "Z 方差不足，ŷ_co 用训练均值",
        }

    preds_te = (
        predict_ridge_matrix(fit, X_fit[row_te], feat_names) if len(test_idx) else []
    )
    by_theme = _oos_by_theme(preds_te, ys_te, metas_te) if ys_te else {}
    oos = {
        "n_train": len(ys_tr),
        "n_test": len(ys_te),
        "ic": _ic(preds_te, ys_te) if ys_te else None,
        "sign_hit": _sign_hit(preds_te, ys_te) if ys_te else None,
        "residual_var": _residual_var(preds_te, ys_te) if ys_te else None,
        "by_theme": by_theme,
        "holdout_trading_days": hold_n,
        "theme_boost": theme_boost if use_theme_weights else None,
        "target": "overnight_gap",
        "feature_fill": feature_fill,
        "include_alpha158": bool(include_alpha158),
    }
    if ys_te:
        try:
            from core.research.daily_cs_ic import attach_daily_cs_ic

            attach_daily_cs_ic(oos, preds_te, ys_te, metas_te)
        except Exception:  # noqa: BLE001
            logger.debug("co attach_daily_cs_ic failed", exc_info=True)

    research_model = make_research_model(fit, y_mean=0.0)
    w_all = (
        theme_sample_weights(metas, theme_boost=theme_boost)
        if use_theme_weights
        else None
    )
    fit_full = fit_keepall_ridge_matrix(
        X_fit,
        ys,
        feat_names,
        ridge_lambda=ridge_lambda,
        sample_weights=w_all,
        min_std_exempt=min_std_exempt,
    )
    model = fit_full if fit_full.get("success") else fit
    model = dict(model)
    model["horizon_mode"] = "overnight_gap"
    model["target"] = "overnight_gap"
    y_formula = "open[T+1]/close[T]-1"
    model["y_spec"] = {
        "formula": y_formula,
        "unit": "pct",
        "anchor": "close[T]",
        "note": "真实隔夜缺口（T 收盘→T+1 开盘）；风控旁路，不进主排序",
    }
    model["extra_features"] = list(fill_keys)
    model["include_alpha158"] = bool(include_alpha158)
    model["model_role"] = "live"
    for k in ("y_spec", "extra_features", "horizon_mode", "target", "include_alpha158"):
        research_model[k] = model.get(k)

    n_a158 = sum(
        1 for k in (model.get("extra_features") or []) if "alpha158" in str(k).lower()
    )
    report = {
        "success": True,
        "task": "co_ridge",
        "stock_count": n_stocks,
        "sample_count": len(ys),
        "oos": oos,
        "return_model": model,
        "return_model_research": research_model,
        "y_spec": dict(model.get("y_spec") or {}),
        "schema": "co_ridge_v1",
        "target": "overnight_gap",
        "include_alpha158": bool(include_alpha158),
        "n_alpha158_features": n_a158,
        "note": (
            "ŷ_co(Z[+Alpha158]) 估 open[T+1]/close[T]-1；"
            "T-1 路径 + 开盘 Z + 昨 K 微观 + 隔夜滞后 + raw_alpha158_*；与 EOD/τ 解耦"
            if include_alpha158
            else "ŷ_co(Z) 估 open[T+1]/close[T]-1；T-1 路径 + 开盘 Z + 昨 K 微观 + 隔夜滞后；与 EOD/τ 解耦"
        ),
    }
    attach_holdout_meta(report, split_meta)
    return report


def co_model_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "co_ridge_model.json")


def co_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "co_ridge_last_report.json")


def _load_model_file(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001
        logger.debug("load co json failed: %s", path, exc_info=True)
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("return_model"), dict):
        return None
    if doc.get("success") is False:
        return None
    return doc


def save_co_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    if not isinstance(report.get("return_model"), dict):
        return
    path = co_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)


def load_co_last_report() -> Optional[Dict[str, Any]]:
    doc = _load_model_file(co_last_report_path())
    if doc and doc.get("success"):
        return doc
    return None


def persist_co_model(
    report: Dict[str, Any], *, note: str = "", role: str = "live"
) -> Dict[str, Any]:
    from core.research.holdout import (
        MODEL_ROLE_RESEARCH,
        research_model_path,
        select_persist_return_model,
    )

    if not report.get("success"):
        return {"success": False, "error": report.get("error") or "no report"}
    role_n, rm = select_persist_return_model(report, role=role)
    if not isinstance(rm, dict):
        return {"success": False, "error": "return_model missing"}
    if not rm.get("coefficients") and rm.get("intercept") is None:
        return {"success": False, "error": "return_model missing"}
    from core.numbers import now_iso_utc

    y_spec = dict(rm.get("y_spec") or {})
    if not y_spec:
        y_spec = {
            "formula": "open[T+1]/close[T]-1",
            "unit": "pct",
            "anchor": "close[T]",
            "note": "真实隔夜缺口",
        }
    y_spec.setdefault("unit", "pct")
    y_spec.setdefault("anchor", "close[T]")
    rm = dict(rm)
    rm["y_spec"] = y_spec
    rm["horizon_mode"] = rm.get("horizon_mode") or "overnight_gap"
    rm["target"] = str(report.get("target") or rm.get("target") or "overnight_gap")

    schema = str(report.get("schema") or "co_ridge_v1")
    doc = {
        "success": True,
        "promoted_at": now_iso_utc(),
        "note": note or "co_ridge promote",
        "return_model": rm,
        "oos": report.get("oos"),
        "sample_count": report.get("sample_count"),
        "stock_count": report.get("stock_count"),
        "schema": schema,
        "target": rm["target"],
        "y_spec": y_spec,
        "y_spec_co": y_spec,
        "y_spec_on": y_spec,
        "dual_score_head": "predicted_score_on",
        "model_role": role_n,
        "fit_end": report.get("fit_end"),
        "eval_start": report.get("eval_start"),
        "contract_note": "ŷ_co 估 open[T+1]/close[T]-1 隔夜缺口；风控旁路，不覆盖 EOD/τ 字段。",
    }
    path = (
        research_model_path(co_model_path())
        if role_n == MODEL_ROLE_RESEARCH
        else co_model_path()
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, doc)
    out = {
        "success": True,
        "path": path,
        "promoted_at": doc["promoted_at"],
        "schema": doc["schema"],
    }
    return out


def load_co_model(*, role: Optional[str] = None) -> Optional[Dict[str, Any]]:
    from core.research.holdout import (
        MODEL_ROLE_RESEARCH,
        current_scoring_model_role,
        research_model_path,
    )

    role_n = role if role is not None else current_scoring_model_role()
    if role_n == MODEL_ROLE_RESEARCH:
        doc = _load_model_file(research_model_path(co_model_path()))
        if doc:
            return doc
        return None
    doc = _load_model_file(co_model_path())
    if doc:
        return doc
    # 已跑 co Ridge 但未 promote 时，用 last report 影子推理（显式 promote 写 co_ridge_model.json）
    fallback = load_co_last_report()
    if fallback:
        out = dict(fallback)
        out["_shadow"] = True
        return out
    return None


# 分钟前缀不进 ŷ_co。旧模型若仍带系数，打分时丢掉该列（缺特征 → z=0），避免开盘→τ 挪动隔夜预估。
_CO_EXCLUDED_FEATURES = ("ret_open_to_tau",)


def _co_score_row(features: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    row = dict(features or {})
    for key in _CO_EXCLUDED_FEATURES:
        row.pop(key, None)
    return row


def predict_co_from_features(
    features: Dict[str, Optional[float]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    doc = model_doc if model_doc is not None else load_co_model()
    if not doc:
        return None
    rm = doc.get("return_model") or {}
    preds = _predict_rows(rm, [_co_score_row(features)])
    return preds[0] if preds else None


_CO_FEAT_LABELS = {
    "ret_oc": "昨开→昨收 %",
    "gap_pct": "跳空 %",
    "ret_cc": "昨收→前收 %",
    "y_on_today": "今开/昨开 %",
    "sector_gap_breadth": "同业缺口广度",
    "theme_day": "主题日",
    "gap_atr": "缺口 / ATR",
    "gap_vs_sector": "行业相对缺口",
    "yclose_loc": "今开相对昨高低",
    "mom3_pct": "近3日动量 %",
    "yest_close_loc": "昨收位置",
    "yest_range_pct": "昨振幅 %",
    "yest_vol_ratio": "昨量/均量",
    "dist_to_up_limit": "距涨停 %",
}
_CO_FEAT_LABELS.update(CO_LAG_FEAT_LABELS)


def explain_co_prediction(
    features: Optional[Dict[str, Any]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    doc = model_doc if model_doc is not None else load_co_model()
    if not doc:
        return None
    fit = doc.get("return_model") or {}
    coefs = fit.get("coefficients") or {}
    intercept = float(fit.get("intercept") or 0.0)
    if not coefs and abs(intercept) < 1e-12:
        return None
    means = fit.get("zscore_means") or fit.get("z_means") or {}
    stds = fit.get("zscore_stds") or fit.get("z_stds") or {}
    active = [
        n
        for n in (fit.get("active_features") or coefs.keys())
        if coefs.get(n) is not None
    ]
    row = _co_score_row(features)
    try:
        from core.signal.factors.meta.registry import factor_label
    except Exception:  # noqa: BLE001
        factor_label = lambda k: str(k)  # noqa: E731

    terms: List[Dict[str, Any]] = []
    total = intercept
    for name in active:
        if name in _CO_EXCLUDED_FEATURES:
            continue
        beta = float(coefs.get(name) or 0.0)
        v = row.get(name)
        imputed = False
        if v is None:
            z = 0.0
            imputed = True
        else:
            try:
                fv = float(v)
            except (TypeError, ValueError):
                z = 0.0
                imputed = True
            else:
                mu = float(means.get(name, 0.0)) if means else 0.0
                sd = float(stds.get(name, 1.0)) if stds else 1.0
                if sd < 1e-9:
                    sd = 1.0
                z = (fv - mu) / sd if means else fv
        contrib = beta * z
        total += contrib
        label = _CO_FEAT_LABELS.get(name) or factor_label(name) or name
        term: Dict[str, Any] = {
            "key": str(name),
            "label": str(label),
            "beta": round(beta, 6),
            "z": round(float(z), 4),
            "contrib": round(float(contrib), 6),
        }
        if imputed:
            term["note"] = "缺特征·z≈0"
        terms.append(term)
    if not terms and abs(intercept) < 1e-12:
        return None
    terms.sort(key=lambda t: -abs(float(t.get("contrib") or 0)))
    return {
        "intercept": round(intercept, 6),
        "terms": terms[:24],
        "total": round(total, 6),
        "head": "co",
    }
