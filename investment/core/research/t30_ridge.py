"""ŷ_τ30 Ridge：开盘 Z + 开→τ 收益/截面 + 序列特征 → price(τ⊕30m)/price(τ)−1。

盘中写 y_τ30。做 T 破带后同号旁路闸；不进 C_τ / ranking。
τ⊕30 超出当日交易时段则不训、不预。
不含 ŷ_τ 的 OC 路径形状（HL/回撤/振幅），避免共线把 ŷ 压到 0。
序列键（ret_last_5m / ret_last_30m / session_* / crosses_lunch /
session_vwap_dev / vol_last_30m_vs_avg / sector_ret_last_30m /
ret_last_30m_vs_sector / t30_lag*）只进本头，不进 TAU_Z_FEATURES。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.research.factor_ols_fit import fit_factor_ols_from_panel
from core.research.tau_panel import (
    TAU_LAG_FEAT_LABELS,
    T30_LAG_FEAT_LABELS,
    T30_SEQ_FEATURES,
    relabel_tau_panels_as_t30,
    theme_sample_weights,
    y_t30_pct,
)
from core.research.tau_ridge import (
    TAU_FIT_DROP_ALIASES,
    TAU_HORIZON_DROP_OC_SHAPE,
    TAU_MIN_STD_EXEMPT,
    TAU_Z_FEATURES,
    _ic,
    _oos_by_tau,
    _oos_by_theme,
    _oos_sign_buckets,
    _predict_rows,
    _residual_var,
    _sign_hit,
    _stack_panels,
    _subset,
    _theme_counts,
    _theme_trigger_sensitivity,
    build_tau_panels_from_bars,
    tau_promote_gate,
)
from core.signal.minute_tau_feats import MINUTE_TAU_FEAT_LABELS
from core.signal.minute_tau_grid import (
    DEFAULT_T30_TRAIN_TAU_GRID,
    format_shared_tau_formula,
)

Y_T30_HAT_KEYS = ("predicted_score_t30", "y_t30_hat", "y_τ30", "y_t30")
Y_T30_LABEL_KEYS = ("t30_realized", "y_t30_realized")
FORMULA_T30 = "price[τ+30m]/price[τ]-1"
T30_Z_FEATURES = tuple(
    k for k in (TAU_Z_FEATURES + T30_SEQ_FEATURES) if k not in TAU_HORIZON_DROP_OC_SHAPE
)
T30_MIN_STD_EXEMPT = TAU_MIN_STD_EXEMPT + T30_SEQ_FEATURES

t30_promote_gate = tau_promote_gate


def _t30_z_only_row(row: Optional[dict]) -> Dict[str, Optional[float]]:
    src = row or {}
    return {k: src.get(k) for k in T30_Z_FEATURES}


def _t30_z_only_xs(xs: Sequence[dict]) -> List[dict]:
    return [_t30_z_only_row(r) for r in xs]


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return x


def pick_y_t30_hat(*objs: Any) -> Optional[float]:
    """盘中 ŷ_τ30（百分点）。"""
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        for k in Y_T30_HAT_KEYS:
            v = _f(obj.get(k))
            if v is not None:
                return v
    return None


def pick_y_t30_label(*objs: Any) -> Optional[float]:
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        for k in Y_T30_LABEL_KEYS:
            v = _f(obj.get(k))
            if v is not None:
                return v
    return None


def write_y_t30_hat(dest: Dict[str, Any], val: float, *, formula: Any = None) -> None:
    x = float(val)
    dest["predicted_score_t30"] = x
    dest["y_t30_hat"] = x
    dest["y_t30"] = x
    dest["y_τ30"] = x
    spec = str(formula or FORMULA_T30)
    dest["y_spec_τ30"] = {"formula": spec, "unit": "pct"}
    dest["y_spec_t30"] = {"formula": spec, "unit": "pct"}


def write_y_t30_label(dest: Dict[str, Any], val: float) -> None:
    x = float(val)
    dest["t30_realized"] = x
    dest["y_t30_realized"] = x


def pack_y_t30_fields(day: Optional[dict]) -> Dict[str, Any]:
    val = pick_y_t30_label(day) if isinstance(day, dict) else None
    hat = pick_y_t30_hat(day) if isinstance(day, dict) else None
    return {
        "t30_realized": val,
        "y_t30_realized": val,
        "y_τ30": hat,
        "y_t30": hat,
    }


def t30_realized_pct(price_tau: Any, price_tau30: Any) -> Optional[float]:
    v = y_t30_pct(price_tau, price_tau30)
    return round(float(v), 4) if v is not None else None


def fit_t30_ridge_report(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    ridge_lambda: float = 1.0,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    theme_boost: float = 1.5,
    holdout_trading_days: int = 10,
    use_theme_weights: bool = True,
    tau_hm: str = "10:30",
    tau_grid: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """池化拟合 ŷ_τ30 + 时间 OOS。X = ŷ_τ Z + 序列特征；标签 = price(τ⊕30m)/price(τ)−1。"""
    live_hm = str(tau_hm or "10:30").strip() or "10:30"
    if live_hm.lower() in ("", "open"):
        live_hm = "10:30"
    grid = list(tau_grid) if tau_grid is not None else list(DEFAULT_T30_TRAIN_TAU_GRID)
    raw = build_tau_panels_from_bars(
        stock_bars,
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        tau_hm=live_hm,
        tau_grid=grid,
    )
    enriched = relabel_tau_panels_as_t30(raw)
    xs, ys, dates, metas = _stack_panels(enriched)
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"t30 样本不足 n={len(ys)}（需≥20 且需 τ⊕30 分钟价）",
            "task": "t30_ridge",
            "sample_count": len(ys),
            "stock_count": len(enriched),
            "tau_grid": list(grid),
            "minute_tau_hm": live_hm,
        }

    xs_z = _t30_z_only_xs(xs)
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
        calendar_dates=calendar_dates_from_stock_bars(stock_bars),
    )
    xs_tr, ys_tr, metas_tr = _subset(xs_z, ys, metas, train_idx)
    xs_te, ys_te, metas_te = _subset(xs_z, ys, metas, test_idx)

    weights = (
        theme_sample_weights(metas_tr, theme_boost=theme_boost)
        if use_theme_weights
        else None
    )
    feat_names = [k for k in T30_Z_FEATURES if k not in TAU_FIT_DROP_ALIASES]

    y_mean = sum(float(y) for y in ys_tr) / max(1, len(ys_tr))
    ys_tr_dm = [float(y) - y_mean for y in ys_tr]

    fit = fit_factor_ols_from_panel(
        xs_tr,
        ys_tr_dm,
        feature_names=feat_names,
        ridge_lambda=ridge_lambda,
        standardize=True,
        sample_weights=weights,
        min_std_exempt=list(T30_MIN_STD_EXEMPT),
        collinearity_policy="drop_redundant",
    )
    if not fit.get("success"):
        fit = {
            "success": True,
            "intercept": 0.0,
            "coefficients": {},
            "active_features": [],
            "zscore_means": {},
            "zscore_stds": {},
            "note": "Z 方差不足，ŷ_τ30 用训练均值",
        }

    preds_dm = _predict_rows(fit, xs_te) if xs_te else []
    preds_te = [
        (float(p) + y_mean) if p is not None else None for p in (preds_dm or [])
    ]
    by_theme = _oos_by_theme(preds_te, ys_te, metas_te) if ys_te else {}
    by_tau = _oos_by_tau(preds_te, ys_te, metas_te) if ys_te else {}
    bucket_pack = _oos_sign_buckets(preds_te, ys_te) if ys_te else {}
    oos: Dict[str, Any] = {
        "n_train": len(ys_tr),
        "n_test": len(ys_te),
        "n_valid": bucket_pack.get("n_valid"),
        "ic": _ic(preds_te, ys_te) if ys_te else None,
        "sign_hit": _sign_hit(preds_te, ys_te) if ys_te else None,
        "residual_var": _residual_var(preds_te, ys_te) if ys_te else None,
        "by_theme": by_theme,
        "by_tau": by_tau,
        "theme_counts": {
            "train": _theme_counts(metas_tr),
            "oos": _theme_counts(metas_te),
            "all": _theme_counts(metas),
        },
        "theme_trigger_sensitivity": _theme_trigger_sensitivity(metas),
        "feature_fill": None,
        "buckets": bucket_pack.get("buckets") or {},
        "pos_recall": bucket_pack.get("pos_recall"),
        "neg_recall": bucket_pack.get("neg_recall"),
        "n_pos": bucket_pack.get("n_pos"),
        "n_neg": bucket_pack.get("n_neg"),
        "y_label_mean": round(y_mean, 6),
        "holdout_trading_days": hold_n,
        "theme_boost": theme_boost if use_theme_weights else None,
        "target": "price_tau_plus_30",
        "tau": live_hm,
        "tau_grid": list(grid),
        "minute_tau_hm": live_hm,
    }
    try:
        from core.research.path_panel import feature_fill_rates

        oos["feature_fill"] = feature_fill_rates(xs_z, T30_Z_FEATURES)
    except Exception:  # noqa: BLE001
        logger.debug("t30 feature_fill failed", exc_info=True)

    oos["label_dist"] = {
        "n_labeled": len(ys),
        "y_mean": round(sum(ys) / float(len(ys)), 4) if ys else None,
        "n_unique_days": len({str(d)[:10] for d in dates}),
        "rows_per_day": round(len(ys) / max(1, len({str(d)[:10] for d in dates})), 2),
    }

    research_model = make_research_model(fit, y_mean=y_mean)
    w_all = (
        theme_sample_weights(metas, theme_boost=theme_boost)
        if use_theme_weights
        else None
    )
    y_mean_all = sum(float(y) for y in ys) / max(1, len(ys))
    ys_all_dm = [float(y) - y_mean_all for y in ys]
    fit_full = fit_factor_ols_from_panel(
        xs_z,
        ys_all_dm,
        feature_names=feat_names,
        ridge_lambda=ridge_lambda,
        standardize=True,
        sample_weights=w_all,
        min_std_exempt=list(T30_MIN_STD_EXEMPT),
        collinearity_policy="drop_redundant",
    )
    model = dict(fit_full if fit_full.get("success") else fit)
    try:
        model["intercept"] = round(float(model.get("intercept") or 0.0) + y_mean_all, 6)
    except (TypeError, ValueError):
        model["intercept"] = round(y_mean_all, 6)
    model["intercept_demeaned"] = round(float(fit.get("intercept") or 0.0), 6)
    model["y_label_mean"] = round(y_mean_all, 6)
    model["y_demeaned"] = True
    model["horizon_mode"] = "price_tau_plus_30"
    model["target"] = "price_tau_plus_30"
    y_formula = format_shared_tau_formula(FORMULA_T30, grid or [live_hm])
    model["y_spec"] = {
        "formula": y_formula,
        "unit": "pct",
        "tau": live_hm,
        "tau_grid": list(grid),
        "note": (
            "X = 开盘 Z + 开→τ 收益/截面 + ŷ_τ30 序列特征"
            "（ret_last_5m / ret_last_30m / session_elapsed / session_remain / "
            "crosses_lunch / session_vwap_dev / vol_last_30m_vs_avg / "
            "sector_ret_last_30m / ret_last_30m_vs_sector / t30_lag1 / t30_ma5）；"
            "不含 ŷ_τ 的 OC 路径形状（HL/回撤/振幅，避免共线把 ŷ 压到 0）。"
            "标签=price(τ⊕30 交易分钟)/price(τ)−1。"
            "主字段 y_τ30；做 T 破带同号旁路；不进 C_τ / ranking。"
        ),
    }
    model["extra_features"] = list(T30_Z_FEATURES)
    model["feat_labels"] = {
        **dict(MINUTE_TAU_FEAT_LABELS),
        **dict(TAU_LAG_FEAT_LABELS),
        **dict(T30_LAG_FEAT_LABELS),
    }
    model["minute_tau_hm"] = live_hm
    model["tau_grid"] = list(grid)
    model["model_role"] = "live"
    for k in (
        "y_spec",
        "extra_features",
        "feat_labels",
        "horizon_mode",
        "target",
        "minute_tau_hm",
        "tau_grid",
    ):
        research_model[k] = model.get(k)

    report = {
        "success": True,
        "task": "t30_ridge",
        "stock_count": len(enriched),
        "sample_count": len(ys),
        "oos": oos,
        "return_model": model,
        "return_model_research": research_model,
        "schema": "t30_ridge_v1",
        "target": "price_tau_plus_30",
        "minute_tau_hm": live_hm,
        "tau_grid": list(grid),
        "dual_score_head": "y_t30",
        "note": (
            "ŷ_τ30：price(τ)→price(τ⊕30m)。做 T 破带同号旁路；不进 C_τ / ranking。"
        ),
    }
    attach_holdout_meta(report, split_meta)
    report["promote_gate"] = t30_promote_gate(report)
    return report


def t30_model_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "t30_ridge_model.json")


def t30_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "t30_ridge_last_report.json")


_T30_MODEL_CACHE: Optional[Tuple[Tuple[float, float], Optional[Dict[str, Any]]]] = None


def save_t30_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    if not isinstance(report.get("return_model"), dict):
        return
    path = t30_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)
    global _T30_MODEL_CACHE
    _T30_MODEL_CACHE = None


def _mtime_or_missing(path: str) -> float:
    try:
        return os.path.getmtime(path) if os.path.isfile(path) else -1.0
    except OSError:
        return -1.0


def load_t30_last_report() -> Optional[Dict[str, Any]]:
    path = t30_last_report_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001
        logger.debug("load_t30_last_report failed", exc_info=True)
        return None
    if not isinstance(doc, dict) or not doc.get("success"):
        return None
    if not isinstance(doc.get("return_model"), dict):
        return None
    return doc


def load_t30_model(*, role: Optional[str] = None) -> Optional[Dict[str, Any]]:
    from core.research.holdout import (
        MODEL_ROLE_RESEARCH,
        current_scoring_model_role,
        load_research_promoted_json,
    )

    role_n = role if role is not None else current_scoring_model_role()
    if role_n == MODEL_ROLE_RESEARCH:
        return load_research_promoted_json(t30_model_path())
    global _T30_MODEL_CACHE
    model_p = t30_model_path()
    report_p = t30_last_report_path()
    key = (_mtime_or_missing(model_p), _mtime_or_missing(report_p))
    if _T30_MODEL_CACHE is not None and _T30_MODEL_CACHE[0] == key:
        return _T30_MODEL_CACHE[1]
    doc: Optional[Dict[str, Any]] = None
    if os.path.isfile(model_p):
        try:
            with open(model_p, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get("return_model"), dict):
                doc = loaded
        except Exception:  # noqa: BLE001
            logger.debug("load_t30_model failed", exc_info=True)
    if doc is None:
        fallback = load_t30_last_report()
        if fallback:
            out = dict(fallback)
            out["_shadow"] = True
            doc = out
    _T30_MODEL_CACHE = (key, doc)
    return doc


def persist_t30_model(
    report: Dict[str, Any],
    *,
    note: str = "",
    force: bool = False,
    role: str = "live",
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
    gate = t30_promote_gate(report)
    if not force and not gate.get("ok"):
        return {
            "success": False,
            "error": "promote 未过闸：" + "；".join(gate.get("blockers") or []),
            "promote_gate": gate,
        }
    from core.numbers import now_iso_utc

    doc = {
        "success": True,
        "promoted_at": now_iso_utc(),
        "note": note or "t30_ridge promote",
        "return_model": rm,
        "oos": report.get("oos"),
        "sample_count": report.get("sample_count"),
        "stock_count": report.get("stock_count"),
        "schema": report.get("schema") or "t30_ridge_v1",
        "minute_tau_hm": report.get("minute_tau_hm") or rm.get("minute_tau_hm"),
        "tau_grid": report.get("tau_grid") or rm.get("tau_grid"),
        "promote_gate": gate,
        "model_role": role_n,
        "fit_end": report.get("fit_end"),
        "eval_start": report.get("eval_start"),
        "holdout_trading_days": report.get("holdout_trading_days"),
        "dual_score_head": "y_t30",
    }
    path = (
        research_model_path(t30_model_path())
        if role_n == MODEL_ROLE_RESEARCH
        else t30_model_path()
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, doc)
    global _T30_MODEL_CACHE
    _T30_MODEL_CACHE = None
    out = dict(doc)
    out["path"] = path
    return out


def predict_t30_from_features(
    features: Dict[str, Optional[float]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    """开盘 Z + 前缀分钟 → ŷ_τ30（百分点）。"""
    doc = model_doc if model_doc is not None else load_t30_model()
    if not doc:
        return None
    rm = doc.get("return_model") or {}
    preds = _predict_rows(rm, [features or {}])
    if not preds or preds[0] is None:
        return None
    return float(preds[0])


def explain_t30_prediction(
    features: Optional[Dict[str, Any]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    from core.research.tau_ridge import explain_tau_prediction

    doc = model_doc if model_doc is not None else load_t30_model()
    expl = explain_tau_prediction(features, model_doc=doc)
    if not expl:
        return None
    out = dict(expl)
    out["head"] = "t30"
    role = None
    if isinstance(doc, dict):
        role = doc.get("model_role")
        rm = doc.get("return_model") if isinstance(doc.get("return_model"), dict) else {}
        if not role:
            role = rm.get("model_role")
    if role:
        out["model_role"] = str(role)
    return out
