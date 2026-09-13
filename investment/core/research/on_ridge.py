"""隔夜缺口 Ridge：Z 上拟合 open[T+1]/close[T]-1；风控旁路 ŷ_ON。"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.research.factor_ols_fit import fit_factor_ols_from_panel
from core.research.on_panel import (
    ON_LAG_FEAT_LABELS,
    ON_Z_FEATURES,
    collect_on_panel,
    enrich_on_panel_breadth,
    theme_sample_weights,
)
from core.research.tau_ridge import (
    _ic,
    _oos_by_theme,
    _predict_rows,
    _residual_var,
    _sign_hit,
    _subset,
)

ON_MIN_STD_EXEMPT = ON_Z_FEATURES


def _stack_panels(enriched: Sequence[Dict[str, Any]]) -> tuple:
    xs_all: List[dict] = []
    ys_all: List[float] = []
    dates_all: List[str] = []
    metas_all: List[dict] = []
    for p in enriched:
        xs_all.extend(p.get("xs") or [])
        ys_all.extend(p.get("ys") or [])
        dates_all.extend(p.get("dates") or [])
        metas_all.extend(p.get("metas") or [])
    return xs_all, ys_all, dates_all, metas_all


def _z_only_row(row: Optional[dict]) -> Dict[str, Optional[float]]:
    """始终输出完整 ON_Z_FEATURES 键，避免 yest_gap / yclose_loc 被静默丢掉。"""
    src = row or {}
    return {k: src.get(k) for k in ON_Z_FEATURES}


def _z_only_xs(xs: Sequence[dict]) -> List[dict]:
    return [_z_only_row(r) for r in xs]


def build_on_panels_from_bars(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
) -> List[Dict[str, Any]]:
    raw: List[Dict[str, Any]] = []
    for item in stock_bars:
        code = str(item.get("code") or item.get("stock_code") or "").strip()
        bars = list(item.get("bars") or [])
        if len(bars) < min_history + 3:
            continue
        xs, ys, dates, metas = collect_on_panel(
            bars, min_history=min_history, stock_code=code
        )
        if len(ys) < 4:
            continue
        raw.append(
            {"code": code, "xs": xs, "ys": ys, "dates": dates, "metas": metas}
        )
    return enrich_on_panel_breadth(raw, gap_trigger_pct=gap_trigger_pct)


def fit_on_ridge_report(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    ridge_lambda: float = 1.0,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    theme_boost: float = 1.5,
    holdout_trading_days: int = 10,
    use_theme_weights: bool = True,
) -> Dict[str, Any]:
    """池化拟合 ŷ_ON(Z) + 时间 OOS。标签 = open[T+1]/close[T]-1（决策日 T 开盘）。"""
    enriched = build_on_panels_from_bars(
        stock_bars,
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
    )
    xs, ys, dates, metas = _stack_panels(enriched)
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"on 样本不足 n={len(ys)}（需≥20）",
            "task": "on_ridge",
            "sample_count": len(ys),
            "stock_count": len(enriched),
        }

    xs_z = _z_only_xs(xs)
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
    xs_tr, ys_tr, metas_tr = _subset(xs_z, ys, metas, train_idx)
    xs_te, ys_te, metas_te = _subset(xs_z, ys, metas, test_idx)

    weights = (
        theme_sample_weights(metas_tr, theme_boost=theme_boost)
        if use_theme_weights
        else None
    )

    feat_names: List[str] = []
    seen = set()
    for row in xs_tr:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                feat_names.append(k)
    if not feat_names:
        feat_names = list(ON_Z_FEATURES)

    fit = fit_factor_ols_from_panel(
        xs_tr,
        ys_tr,
        feature_names=feat_names,
        ridge_lambda=ridge_lambda,
        standardize=True,
        sample_weights=weights,
        min_std_exempt=list(ON_MIN_STD_EXEMPT),
        collinearity_policy="keep_all",
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
            "note": "Z 方差不足，ŷ_ON 用训练均值",
        }

    preds_te = _predict_rows(fit, xs_te) if xs_te else []
    by_theme = _oos_by_theme(preds_te, ys_te, metas_te) if ys_te else {}
    from core.research.path_panel import feature_fill_rates

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
        "feature_fill": feature_fill_rates(xs_z, ON_Z_FEATURES),
    }

    research_model = make_research_model(fit, y_mean=0.0)
    w_all = (
        theme_sample_weights(metas, theme_boost=theme_boost)
        if use_theme_weights
        else None
    )
    fit_full = fit_factor_ols_from_panel(
        xs_z,
        ys,
        feature_names=feat_names,
        ridge_lambda=ridge_lambda,
        standardize=True,
        sample_weights=w_all,
        min_std_exempt=list(ON_MIN_STD_EXEMPT),
        collinearity_policy="keep_all",
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
    model["extra_features"] = list(ON_Z_FEATURES)
    model["model_role"] = "live"
    for k in ("y_spec", "extra_features", "horizon_mode", "target"):
        research_model[k] = model.get(k)

    report = {
        "success": True,
        "task": "on_ridge",
        "stock_count": len(enriched),
        "sample_count": len(ys),
        "oos": oos,
        "return_model": model,
        "return_model_research": research_model,
        "y_spec": dict(model.get("y_spec") or {}),
        "schema": "on_ridge_v1",
        "target": "overnight_gap",
        "note": "ŷ_co(Z) 估 open[T+1]/close[T]-1；T-1 路径 + 开盘 Z + 昨 K 微观 + 隔夜滞后；与 EOD/τ 解耦",
    }
    attach_holdout_meta(report, split_meta)
    return report


def on_model_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "on_ridge_model.json")


def on_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "on_ridge_last_report.json")


def save_on_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    if not isinstance(report.get("return_model"), dict):
        return
    path = on_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)


def load_on_last_report() -> Optional[Dict[str, Any]]:
    path = on_last_report_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001
        logger.debug("load_on_last_report failed", exc_info=True)
        return None
    if not isinstance(doc, dict) or not doc.get("success"):
        return None
    if not isinstance(doc.get("return_model"), dict):
        return None
    return doc


def persist_on_model(
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

    schema = str(report.get("schema") or "on_ridge_v1")
    doc = {
        "success": True,
        "promoted_at": now_iso_utc(),
        "note": note or "on_ridge promote",
        "return_model": rm,
        "oos": report.get("oos"),
        "sample_count": report.get("sample_count"),
        "stock_count": report.get("stock_count"),
        "schema": schema,
        "target": rm["target"],
        "y_spec": y_spec,
        "y_spec_on": y_spec,
        "dual_score_head": "predicted_score_on",
        "model_role": role_n,
        "fit_end": report.get("fit_end"),
        "eval_start": report.get("eval_start"),
        "contract_note": "ŷ_ON 估 open[T+1]/close[T]-1 隔夜缺口；风控旁路，不覆盖 EOD/τ 字段。",
    }
    path = (
        research_model_path(on_model_path())
        if role_n == MODEL_ROLE_RESEARCH
        else on_model_path()
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, doc)
    return {
        "success": True,
        "path": path,
        "promoted_at": doc["promoted_at"],
        "schema": doc["schema"],
    }


def load_on_model(*, role: Optional[str] = None) -> Optional[Dict[str, Any]]:
    from core.research.holdout import (
        MODEL_ROLE_RESEARCH,
        current_scoring_model_role,
        research_model_path,
    )

    role_n = role if role is not None else current_scoring_model_role()
    if role_n == MODEL_ROLE_RESEARCH:
        path = research_model_path(on_model_path())
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    doc = json.load(f)
                if isinstance(doc, dict) and isinstance(doc.get("return_model"), dict):
                    return doc
            except Exception:  # noqa: BLE001
                logger.debug("load_on_model research failed", exc_info=True)
        return None
    path = on_model_path()
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            if isinstance(doc, dict) and isinstance(doc.get("return_model"), dict):
                return doc
        except Exception:  # noqa: BLE001
            logger.debug("load_on_model failed", exc_info=True)
    # 已跑 on Ridge 但未 promote 时，用 last report 影子推理（显式 promote 写 on_ridge_model.json）
    fallback = load_on_last_report()
    if fallback:
        out = dict(fallback)
        out["_shadow"] = True
        return out
    return None


def predict_on_from_features(
    features: Dict[str, Optional[float]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    doc = model_doc if model_doc is not None else load_on_model()
    if not doc:
        return None
    rm = doc.get("return_model") or {}
    preds = _predict_rows(rm, [features])
    return preds[0] if preds else None


_ON_FEAT_LABELS = {
    "ret_oc": "昨开→昨收 %",
    "gap_pct": "跳空 %",
    "ret_cc": "昨收→前收 %",
    "y_on_today": "今开/昨开 %",
    "sector_gap_breadth": "同业缺口广度",
    "theme_day": "主题日",
    "gap_atr": "缺口 / ATR",
    "gap_vs_sector": "行业相对缺口",
    "ret_open_to_tau": "开盘→τ 收益 %",
    "yclose_loc": "今开相对昨高低",
    "mom3_pct": "近3日动量 %",
    "yest_close_loc": "昨收位置",
    "yest_range_pct": "昨振幅 %",
    "yest_vol_ratio": "昨量/均量",
    "dist_to_up_limit": "距涨停 %",
}
_ON_FEAT_LABELS.update(ON_LAG_FEAT_LABELS)


def explain_on_prediction(
    features: Optional[Dict[str, Any]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    doc = model_doc if model_doc is not None else load_on_model()
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
    row = features or {}
    try:
        from core.signal.factors.meta.registry import factor_label
    except Exception:  # noqa: BLE001
        factor_label = lambda k: str(k)  # noqa: E731

    terms: List[Dict[str, Any]] = []
    total = intercept
    for name in active:
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
        label = _ON_FEAT_LABELS.get(name) or factor_label(name) or name
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
        "head": "on",
    }
