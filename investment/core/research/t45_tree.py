"""ŷ_τ45_tree：独立浅树头，标签与 ŷ_τ45 相同（mean(price(τ⊕40/45/50))/price(τ)−1）。

与 Ridge 同面板、同 Holdout，只写 ``t45_tree_last_report.json``。
影子报告 ``t45_tree_last_report.json``；可预测包 ``t45_tree_model.json``。
``horizon_prob_backend=tree`` 时做 T 回测可用。浅树引擎与 ŷ_τ_tree 相同。
X = Ridge Z + OC 路径形状；对照 Ridge 仍用原 Z。
"""

from __future__ import annotations

import logging
import math
import os
import time
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.research.tau_panel import (
    relabel_tau_panels_as_t45,
    theme_sample_weights,
)
from core.research.t45_ridge import (
    T45_MIN_STD_EXEMPT,
    T45_Z_FEATURES,
)
from core.research.tc_ridge import (
    TAU_FIT_DROP_ALIASES,
    TAU_HORIZON_TREE_SHAPE_FEATURES,
    _stack_panels,
    _subset,
    _theme_counts,
    build_tau_panels_from_bars,
    with_horizon_tree_shape,
)
from core.research.horizon_prob import binary_labels
from core.research.horizon_tree import (
    load_tree_model_doc,
    pack_tree_return_model,
    persist_tree_model_doc,
    predict_tree_p_up,
    tree_model_path,
)
from core.research.tc_tree import (
    DEFAULT_LEARNING_RATE,
    DEFAULT_MAX_DEPTH,
    DEFAULT_N_ESTIMATORS,
    DEFAULT_SUBSAMPLE,
    _delta_oos,
    _design_matrix,
    _fit_lightgbm,
    _fit_numpy_gbm,
    _fit_ridge_oos,
    _fit_xgboost,
    _importance_rows,
    _oos_pack,
    _predict_lightgbm,
    _predict_numpy_gbm,
    _predict_xgboost,
    resolve_tree_backend,
)
from core.signal.minute_tau_grid import DEFAULT_T45_TRAIN_TAU_GRID

TREE_SCHEMA = "t45_tree_shadow_v5"
TREE_HEAD = "y_t45_tree"
TREE_TARGET = "price_tau_plus_45"
T45_TREE_Z_FEATURES = with_horizon_tree_shape(T45_Z_FEATURES)


def t45_tree_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "t45_tree_last_report.json")


def save_t45_tree_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    path = t45_tree_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)


def load_t45_tree_last_report() -> Optional[Dict[str, Any]]:
    import json

    path = t45_tree_last_report_path()
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except OSError:
        return None
    except Exception:  # noqa: BLE001
        logger.debug("load t45 tree last_report failed", exc_info=True)
        return None
    if isinstance(doc, dict) and doc.get("success"):
        return doc
    return None


def fit_t45_tree_report(
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
    backend: Optional[str] = None,
    n_estimators: int = DEFAULT_N_ESTIMATORS,
    max_depth: int = DEFAULT_MAX_DEPTH,
    learning_rate: float = DEFAULT_LEARNING_RATE,
    subsample: float = DEFAULT_SUBSAMPLE,
) -> Dict[str, Any]:
    """同面板拟合树 + Ridge OOS 对照。不写 live / 研究套模型。"""
    t0 = time.perf_counter()
    live_hm = str(tau_hm or "10:30").strip() or "10:30"
    if live_hm.lower() in ("", "open"):
        live_hm = "10:30"
    grid = list(tau_grid) if tau_grid is not None else list(DEFAULT_T45_TRAIN_TAU_GRID)
    t_panel0 = time.perf_counter()
    raw = build_tau_panels_from_bars(
        stock_bars,
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        tau_hm=live_hm,
        tau_grid=grid,
    )
    enriched = relabel_tau_panels_as_t45(raw)
    xs, ys, dates, metas = _stack_panels(enriched)
    panel_s = round(time.perf_counter() - t_panel0, 2)
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"树样本不足 n={len(ys)}（需≥20 且需 τ⊕40/45/50 三根均价）",
            "task": "t45_tree",
            "head": TREE_HEAD,
            "sample_count": len(ys),
            "stock_count": len(enriched),
            "tau": live_hm,
            "tau_grid": list(grid),
            "schema": TREE_SCHEMA,
            "live_hook": False,
            "backtest_hook": False,
        }

    xs_z = [{k: (row or {}).get(k) for k in T45_TREE_Z_FEATURES} for row in xs]
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
    feat_names = [k for k in T45_TREE_Z_FEATURES if k not in TAU_FIT_DROP_ALIASES]
    ridge_feat_names = [k for k in T45_Z_FEATURES if k not in TAU_FIT_DROP_ALIASES]
    if len(ys_tr) < 16 or len(ys_te) < 8:
        return {
            "success": False,
            "error": f"Holdout 切分后样本不足 训={len(ys_tr)} 测={len(ys_te)}",
            "task": "t45_tree",
            "head": TREE_HEAD,
            "sample_count": len(ys),
            "schema": TREE_SCHEMA,
            "live_hook": False,
            "backtest_hook": False,
        }

    x_tr, means = _design_matrix(xs_tr, feat_names)
    x_te, _ = _design_matrix(xs_te, feat_names, means=means)
    y_tr = np.asarray(binary_labels(ys_tr), dtype=np.float64)
    w_tr = np.asarray(
        theme_sample_weights(metas_tr, theme_boost=theme_boost)
        if use_theme_weights
        else [1.0] * len(ys_tr),
        dtype=np.float64,
    )
    if w_tr.shape != y_tr.shape:
        w_tr = np.ones_like(y_tr)

    engine = resolve_tree_backend(backend)
    hyper = {
        "n_estimators": int(n_estimators),
        "max_depth": int(max_depth),
        "learning_rate": float(learning_rate),
        "subsample": float(subsample),
        "objective": "binary:logistic",
    }
    gain = np.zeros(len(feat_names), dtype=np.float64)
    boost_preds: List[Optional[float]]
    tree_obj: Any = None
    t_tree0 = time.perf_counter()
    model, gain = _fit_lightgbm(x_tr, y_tr, w_tr, **hyper)
    tree_obj = model
    raw_pred = np.clip(_predict_lightgbm(model, x_te), 1e-6, 1.0 - 1e-6)
    boost_preds = [float(v) if math.isfinite(float(v)) else None for v in raw_pred]
    tree_s = round(time.perf_counter() - t_tree0, 2)
    return_model = pack_tree_return_model(
        head="t45",
        backend=engine,
        feature_names=feat_names,
        impute_means=means,
        model_obj=tree_obj,
        schema=TREE_SCHEMA,
        hyperparams=hyper,
        target=TREE_TARGET,
    )

    t_ridge0 = time.perf_counter()
    _, ridge_preds = _fit_ridge_oos(
        xs_tr,
        ys_tr,
        xs_te,
        ys_te,
        metas_tr,
        ridge_feat_names,
        ridge_lambda=ridge_lambda,
        theme_boost=theme_boost,
        use_theme_weights=use_theme_weights,
        min_std_exempt=T45_MIN_STD_EXEMPT,
        kind="prob",
    )
    ridge_s = round(time.perf_counter() - t_ridge0, 2)
    oos_boost = _oos_pack(boost_preds, ys_te, metas_te, use_minute=True, kind="prob")
    oos_ridge = _oos_pack(ridge_preds, ys_te, metas_te, use_minute=True, kind="prob")
    oos_boost.update(
        {
            "n_train": len(ys_tr),
            "n_test": len(ys_te),
            "holdout_trading_days": hold_n,
            "theme_counts": {
                "train": _theme_counts(metas_tr),
                "oos": _theme_counts(metas_te),
                "all": _theme_counts(metas),
            },
            "y_label_mean": round(float(np.mean(y_tr)), 6) if y_tr.size else None,
            "tau": live_hm,
            "tau_grid": list(grid),
            "target": TREE_TARGET,
        }
    )
    oos_ridge["n_train"] = len(ys_tr)
    oos_ridge["n_test"] = len(ys_te)
    oos_ridge["holdout_trading_days"] = hold_n
    oos_ridge["target"] = TREE_TARGET

    report: Dict[str, Any] = {
        "success": True,
        "task": "t45_tree",
        "head": TREE_HEAD,
        "schema": TREE_SCHEMA,
        "head_kind": "prob",
        "stock_count": len(enriched),
        "sample_count": len(ys),
        "tau": live_hm,
        "tau_grid": list(grid),
        "target": TREE_TARGET,
        "backend": engine,
        "hyperparams": hyper,
        "timing": {
            "panel_s": panel_s,
            "tree_s": tree_s,
            "ridge_s": ridge_s,
            "fit_s": round(time.perf_counter() - t0, 2),
        },
        "feature_names": list(feat_names),
        "ridge_feature_names": list(ridge_feat_names),
        "tree_shape_features": list(TAU_HORIZON_TREE_SHAPE_FEATURES),
        "feature_importance": _importance_rows(feat_names, gain),
        "tree_return_model": return_model,
        "oos": oos_boost,
        "ridge_oos": oos_ridge,
        "delta_vs_ridge": _delta_oos(oos_boost, oos_ridge),
        "live_hook": False,
        "backtest_hook": True,
        "persisted": {"success": False, "skipped": True, "reason": "fit_only"},
        "note": (
            "ŷ_τ45_tree：X 含 OC 路径形状；对照 Ridge 仍用原 Z。"
            "写入 t45_tree_model.json 后可设 horizon_prob_backend=tree 进做 T 回测。"
        ),
    }
    attach_holdout_meta(report, split_meta)
    return report

def t45_tree_model_path() -> str:
    return tree_model_path("t45")


def load_t45_tree_model() -> Optional[Dict[str, Any]]:
    return load_tree_model_doc("t45")


def persist_t45_tree_model(
    report: Dict[str, Any],
    *,
    note: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    return persist_tree_model_doc("t45", report, note=note or "t45_tree promote", force=force)


def predict_t45_tree_from_features(
    features: Dict[str, Optional[float]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    doc = model_doc if model_doc is not None else load_t45_tree_model()
    if not doc:
        return None
    rm = doc.get("return_model") if isinstance(doc.get("return_model"), dict) else doc
    return predict_tree_p_up(features, rm)
