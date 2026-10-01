"""ŷ_τ90 Ridge：开盘 Z + 开→τ 收益/截面 + 序列特征 → mean(price(τ⊕85/90/95))/price(τ)−1。

盘中写 y_τ90=p_up。进 ŷ_τw 投票；个股闸与入场已下线。不进 C_τ / ranking。
τ⊕95 超出当日交易时段则不训、不预。
不含 ŷ_τ 的 OC 路径形状（HL/回撤/振幅），避免共线把 ŷ 压到 0。
序列键（ret_last_5m / ret_last_90m / session_* / crosses_lunch_90 /
session_vwap_dev / vol_last_90m_vs_avg / sector_ret_last_90m /
ret_last_90m_vs_sector / t90_lag*）只进本头，不进 TAU_Z_FEATURES。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.research.tau_panel import (
    TAU_LAG_FEAT_LABELS,
    T90_LAG_FEAT_LABELS,
    T90_SEQ_FEATURES,
    relabel_tau_panels_as_t90,
    theme_sample_weights,
    y_t90_pct,
)
from core.research.horizon_prob import (
    horizon_promote_gate,
    predict_p_up_rows,
    stamp_horizon_explain,
    train_eval_horizon_prob,
)
from core.research.tc_ridge import (
    TAU_FIT_DROP_ALIASES,
    TAU_HORIZON_DROP_OC_SHAPE,
    TAU_MIN_STD_EXEMPT,
    TAU_Z_FEATURES,
    _stack_panels,
    _subset,
    _theme_counts,
    _theme_trigger_sensitivity,
    build_tau_panels_from_bars,
)
from core.signal.minute_tau_feats import MINUTE_TAU_FEAT_LABELS
from core.signal.minute_tau_grid import (
    DEFAULT_T90_TRAIN_TAU_GRID,
    format_shared_tau_formula,
)

Y_T90_HAT_KEYS = ("predicted_score_t90", "y_t90_hat", "y_τ90", "y_t90")
Y_T90_LABEL_KEYS = ("t90_realized", "y_t90_realized")
FORMULA_T90 = "mean(price[τ+85m],price[τ+90m],price[τ+95m])/price[τ]-1"
T90_Z_FEATURES = tuple(
    k for k in (TAU_Z_FEATURES + T90_SEQ_FEATURES) if k not in TAU_HORIZON_DROP_OC_SHAPE
)
T90_MIN_STD_EXEMPT = TAU_MIN_STD_EXEMPT + T90_SEQ_FEATURES

t90_promote_gate = horizon_promote_gate


def _t90_z_only_row(row: Optional[dict]) -> Dict[str, Optional[float]]:
    src = row or {}
    return {k: src.get(k) for k in T90_Z_FEATURES}


def _t90_z_only_xs(xs: Sequence[dict]) -> List[dict]:
    return [_t90_z_only_row(r) for r in xs]


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


def pick_y_t90_hat(*objs: Any) -> Optional[float]:
    """盘中 ŷ_τ90（p_up∈(0,1)）。"""
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        for k in Y_T90_HAT_KEYS:
            v = _f(obj.get(k))
            if v is not None:
                return v
    return None


def pick_y_t90_label(*objs: Any) -> Optional[float]:
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        for k in Y_T90_LABEL_KEYS:
            v = _f(obj.get(k))
            if v is not None:
                return v
    return None


def write_y_t90_hat(dest: Dict[str, Any], val: float, *, formula: Any = None) -> None:
    x = float(val)
    dest["predicted_score_t90"] = x
    dest["y_t90_hat"] = x
    dest["y_t90"] = x
    dest["y_τ90"] = x
    spec = str(formula or FORMULA_T90)
    dest["y_spec_τ90"] = {"formula": spec, "unit": "prob"}
    dest["y_spec_t90"] = {"formula": spec, "unit": "prob"}


def write_y_t90_label(dest: Dict[str, Any], val: float) -> None:
    x = float(val)
    dest["t90_realized"] = x
    dest["y_t90_realized"] = x


def pack_y_t90_fields(day: Optional[dict]) -> Dict[str, Any]:
    val = pick_y_t90_label(day) if isinstance(day, dict) else None
    hat = pick_y_t90_hat(day) if isinstance(day, dict) else None
    return {
        "t90_realized": val,
        "y_t90_realized": val,
        "y_τ90": hat,
        "y_t90": hat,
    }


def t90_realized_pct(price_tau: Any, price_tau90: Any) -> Optional[float]:
    v = y_t90_pct(price_tau, price_tau90)
    return round(float(v), 4) if v is not None else None


def fit_t90_ridge_report(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    ridge_lambda: float = 1.0,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    theme_boost: float = 1.5,
    holdout_trading_days: int = 20,
    use_theme_weights: bool = True,
    tau_hm: str = "10:30",
    tau_grid: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """池化拟合 ŷ_τ90 + 时间 OOS。X = ŷ_τ Z + 序列特征；标签 = mean(price(τ⊕85/90/95))/price(τ)−1。"""
    live_hm = str(tau_hm or "10:30").strip() or "10:30"
    if live_hm.lower() in ("", "open"):
        live_hm = "10:30"
    grid = list(tau_grid) if tau_grid is not None else list(DEFAULT_T90_TRAIN_TAU_GRID)
    raw = build_tau_panels_from_bars(
        stock_bars,
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        tau_hm=live_hm,
        tau_grid=grid,
    )
    enriched = relabel_tau_panels_as_t90(raw)
    xs, ys, dates, metas = _stack_panels(enriched)
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"t90 样本不足 n={len(ys)}（需≥20 且需 τ⊕85/90/95 三根均价）",
            "task": "t90_ridge",
            "sample_count": len(ys),
            "stock_count": len(enriched),
            "tau_grid": list(grid),
            "minute_tau_hm": live_hm,
        }

    xs_z = _t90_z_only_xs(xs)
    from core.research.holdout import (
        DEFAULT_HOLDOUT_TRADING_DAYS,
        attach_holdout_meta,
        calendar_dates_from_stock_bars,
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
    feat_names = [k for k in T90_Z_FEATURES if k not in TAU_FIT_DROP_ALIASES]
    w_all = (
        theme_sample_weights(metas, theme_boost=theme_boost)
        if use_theme_weights
        else None
    )
    packed = train_eval_horizon_prob(
        xs_tr,
        ys_tr,
        xs_te,
        ys_te,
        metas_te,
        xs_z,
        ys,
        feat_names=feat_names,
        ridge_lambda=ridge_lambda,
        weights=weights,
        weights_all=w_all,
        min_std_exempt=T90_MIN_STD_EXEMPT,
    )
    oos: Dict[str, Any] = dict(packed.get("oos_core") or {})
    oos.update(
        {
            "n_train": len(ys_tr),
            "n_test": len(ys_te),
            "theme_counts": {
                "train": _theme_counts(metas_tr),
                "oos": _theme_counts(metas_te),
                "all": _theme_counts(metas),
            },
            "theme_trigger_sensitivity": _theme_trigger_sensitivity(metas),
            "feature_fill": None,
            "holdout_trading_days": hold_n,
            "theme_boost": theme_boost if use_theme_weights else None,
            "target": "price_tau_plus_90",
            "tau": live_hm,
            "tau_grid": list(grid),
            "minute_tau_hm": live_hm,
        }
    )
    try:
        from core.research.panel import feature_fill_rates

        oos["feature_fill"] = feature_fill_rates(xs_z, T90_Z_FEATURES)
    except Exception:  # noqa: BLE001
        logger.debug("t90 feature_fill failed", exc_info=True)

    oos["label_dist"] = {
        "n_labeled": len(ys),
        "y_mean": round(sum(ys) / float(len(ys)), 4) if ys else None,
        "n_unique_days": len({str(d)[:10] for d in dates}),
        "rows_per_day": round(len(ys) / max(1, len({str(d)[:10] for d in dates})), 2),
    }

    research_model = dict(packed.get("research_model") or {})
    model = dict(packed.get("model") or {})
    model["horizon_mode"] = "price_tau_plus_90"
    model["target"] = "price_tau_plus_90"
    y_formula = format_shared_tau_formula(FORMULA_T90, grid or [live_hm])
    model["y_spec"] = {
        "formula": y_formula,
        "unit": "prob",
        "label": "I(mean(price(τ⊕85/90/95))/price(τ)−1 > 0)",
        "tau": live_hm,
        "tau_grid": list(grid),
        "note": (
            "X = 开盘 Z + 开→τ 收益/截面 + ŷ_τ90 序列特征"
            "（ret_last_5m / ret_last_90m / session_elapsed / session_remain / crosses_lunch_90 / session_vwap_dev / vol_last_90m_vs_avg / sector_ret_last_90m / ret_last_90m_vs_sector / t90_lag1 / t90_ma5）；"
            "不含 ŷ_τ 的 OC 路径形状。"
            "ŷ_τ90=P(窗收益>0)；做 T 用 p_agree（正T=p_up，反T=1−p_up）；不进 C_τ / ranking。"
        ),
    }
    model["extra_features"] = list(T90_Z_FEATURES)
    model["feat_labels"] = {
        **dict(MINUTE_TAU_FEAT_LABELS),
        **dict(TAU_LAG_FEAT_LABELS),
        **dict(T90_LAG_FEAT_LABELS),
    }
    model["minute_tau_hm"] = live_hm
    model["tau_grid"] = list(grid)
    model["model_role"] = "live"
    model["head_kind"] = "prob"
    for k in (
        "y_spec",
        "extra_features",
        "feat_labels",
        "horizon_mode",
        "target",
        "minute_tau_hm",
        "tau_grid",
        "head_kind",
    ):
        research_model[k] = model.get(k)

    report = {
        "success": True,
        "task": "t90_ridge",
        "stock_count": len(enriched),
        "sample_count": len(ys),
        "oos": oos,
        "return_model": model,
        "return_model_research": research_model,
        "schema": "t90_ridge_v2",
        "target": "price_tau_plus_90",
        "minute_tau_hm": live_hm,
        "tau_grid": list(grid),
        "dual_score_head": "y_t90",
        "head_kind": "prob",
        "note": (
            "ŷ_τ90=P(mean(price(τ⊕85/90/95))/price(τ)−1>0)。进 ŷ_τw 投票；个股旁路闸已下线。不进 C_τ / ranking。"
        ),
    }
    attach_holdout_meta(report, split_meta)
    report["promote_gate"] = t90_promote_gate(report)
    return report


def t90_model_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "t90_ridge_model.json")


def t90_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "t90_ridge_last_report.json")


_T90_MODEL_CACHE: Optional[Tuple[Tuple[float, float], Optional[Dict[str, Any]]]] = None


def save_t90_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    if not isinstance(report.get("return_model"), dict):
        return
    path = t90_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)
    global _T90_MODEL_CACHE
    _T90_MODEL_CACHE = None


def _mtime_or_missing(path: str) -> float:
    try:
        return os.path.getmtime(path) if os.path.isfile(path) else -1.0
    except OSError:
        return -1.0


def load_t90_last_report() -> Optional[Dict[str, Any]]:
    path = t90_last_report_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001
        logger.debug("load_t90_last_report failed", exc_info=True)
        return None
    if not isinstance(doc, dict) or not doc.get("success"):
        return None
    if not isinstance(doc.get("return_model"), dict):
        return None
    return doc


def load_t90_model(*, role: Optional[str] = None) -> Optional[Dict[str, Any]]:
    from core.research.holdout import (
        MODEL_ROLE_RESEARCH,
        current_scoring_model_role,
        load_research_promoted_json,
    )

    role_n = role if role is not None else current_scoring_model_role()
    if role_n == MODEL_ROLE_RESEARCH:
        return load_research_promoted_json(t90_model_path())
    global _T90_MODEL_CACHE
    model_p = t90_model_path()
    report_p = t90_last_report_path()
    key = (_mtime_or_missing(model_p), _mtime_or_missing(report_p))
    if _T90_MODEL_CACHE is not None and _T90_MODEL_CACHE[0] == key:
        return _T90_MODEL_CACHE[1]
    doc: Optional[Dict[str, Any]] = None
    if os.path.isfile(model_p):
        try:
            with open(model_p, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get("return_model"), dict):
                doc = loaded
        except Exception:  # noqa: BLE001
            logger.debug("load_t90_model failed", exc_info=True)
    if doc is None:
        fallback = load_t90_last_report()
        if fallback:
            out = dict(fallback)
            out["_shadow"] = True
            doc = out
    _T90_MODEL_CACHE = (key, doc)
    return doc


def persist_t90_model(
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
    gate = t90_promote_gate(report)
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
        "note": note or "t90_ridge promote",
        "return_model": rm,
        "oos": report.get("oos"),
        "sample_count": report.get("sample_count"),
        "stock_count": report.get("stock_count"),
        "schema": report.get("schema") or "t90_ridge_v2",
        "minute_tau_hm": report.get("minute_tau_hm") or rm.get("minute_tau_hm"),
        "tau_grid": report.get("tau_grid") or rm.get("tau_grid"),
        "promote_gate": gate,
        "model_role": role_n,
        "fit_end": report.get("fit_end"),
        "eval_start": report.get("eval_start"),
        "holdout_trading_days": report.get("holdout_trading_days"),
        "dual_score_head": "y_t90",
    }
    path = (
        research_model_path(t90_model_path())
        if role_n == MODEL_ROLE_RESEARCH
        else t90_model_path()
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, doc)
    global _T90_MODEL_CACHE
    _T90_MODEL_CACHE = None
    out = dict(doc)
    out["path"] = path
    return out


def predict_t90_from_features(
    features: Dict[str, Optional[float]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    """开盘 Z + 前缀分钟 → ŷ_τ90 = P(窗收益>0)。"""
    doc = model_doc if model_doc is not None else load_t90_model()
    if not doc:
        return None
    rm = doc.get("return_model") or {}
    preds = predict_p_up_rows(rm, [features or {}])
    if not preds or preds[0] is None:
        return None
    return float(preds[0])


def explain_t90_prediction(
    features: Optional[Dict[str, Any]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    from core.research.tc_ridge import explain_tau_prediction

    doc = model_doc if model_doc is not None else load_t90_model()
    expl = explain_tau_prediction(features, model_doc=doc)
    return stamp_horizon_explain(expl, head="t90", model_doc=doc)
