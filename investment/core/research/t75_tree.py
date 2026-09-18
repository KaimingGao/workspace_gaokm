"""ŷ_τ75_tree：独立浅树头，标签与 ŷ_τ75 相同（mean(price(τ⊕70/75/80))/price(τ)−1）。

与 Ridge 同面板、同 Holdout，只写 ``t75_tree_last_report.json``。
不提供 persist / 研究套 sidecar，不进 live 打分与历史回测。
浅树引擎与 ŷ_τ_tree 相同（XGBoost 或 numpy GBM）。
X = ŷ_τ Z + 序列特征（与 t75_ridge 同）。
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
    relabel_tau_panels_as_t75,
    theme_sample_weights,
)
from core.research.t75_ridge import (
    T75_MIN_STD_EXEMPT,
    T75_Z_FEATURES,
    _t75_z_only_xs,
)
from core.research.tau_ridge import (
    TAU_FIT_DROP_ALIASES,
    _stack_panels,
    _subset,
    _theme_counts,
    build_tau_panels_from_bars,
)
from core.research.tau_tree import (
    DEFAULT_LEARNING_RATE,
    DEFAULT_MAX_DEPTH,
    DEFAULT_N_ESTIMATORS,
    DEFAULT_SUBSAMPLE,
    _delta_oos,
    _design_matrix,
    _fit_numpy_gbm,
    _fit_ridge_oos,
    _fit_xgboost,
    _importance_rows,
    _oos_pack,
    _predict_numpy_gbm,
    _predict_xgboost,
    resolve_tree_backend,
)
from core.signal.minute_tau_grid import DEFAULT_T75_TRAIN_TAU_GRID

TREE_SCHEMA = "t75_tree_shadow_v1"
TREE_HEAD = "y_t75_tree"
TREE_TARGET = "price_tau_plus_75"


def t75_tree_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "t75_tree_last_report.json")


def save_t75_tree_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    path = t75_tree_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)


def load_t75_tree_last_report() -> Optional[Dict[str, Any]]:
    import json

    path = t75_tree_last_report_path()
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except OSError:
        return None
    except Exception:  # noqa: BLE001
        logger.debug("load t75 tree last_report failed", exc_info=True)
        return None
    if isinstance(doc, dict) and doc.get("success"):
        return doc
    return None


def fit_t75_tree_report(
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
    grid = list(tau_grid) if tau_grid is not None else list(DEFAULT_T75_TRAIN_TAU_GRID)
    t_panel0 = time.perf_counter()
    raw = build_tau_panels_from_bars(
        stock_bars,
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        tau_hm=live_hm,
        tau_grid=grid,
    )
    enriched = relabel_tau_panels_as_t75(raw)
    xs, ys, dates, metas = _stack_panels(enriched)
    panel_s = round(time.perf_counter() - t_panel0, 2)
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"树样本不足 n={len(ys)}（需≥20 且需 τ⊕70/75/80 三根均价）",
            "task": "t75_tree",
            "head": TREE_HEAD,
            "sample_count": len(ys),
            "stock_count": len(enriched),
            "tau": live_hm,
            "tau_grid": list(grid),
            "schema": TREE_SCHEMA,
            "live_hook": False,
            "backtest_hook": False,
        }

    xs_z = _t75_z_only_xs(xs)
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
    feat_names = [k for k in T75_Z_FEATURES if k not in TAU_FIT_DROP_ALIASES]
    if len(ys_tr) < 16 or len(ys_te) < 8:
        return {
            "success": False,
            "error": f"Holdout 切分后样本不足 训={len(ys_tr)} 测={len(ys_te)}",
            "task": "t75_tree",
            "head": TREE_HEAD,
            "sample_count": len(ys),
            "schema": TREE_SCHEMA,
            "live_hook": False,
            "backtest_hook": False,
        }

    x_tr, means = _design_matrix(xs_tr, feat_names)
    x_te, _ = _design_matrix(xs_te, feat_names, means=means)
    y_tr = np.asarray(ys_tr, dtype=np.float64)
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
    }
    gain = np.zeros(len(feat_names), dtype=np.float64)
    boost_preds: List[Optional[float]]
    t_tree0 = time.perf_counter()
    if engine == "xgboost":
        model, gain = _fit_xgboost(x_tr, y_tr, w_tr, **hyper)
        raw_pred = _predict_xgboost(model, x_te)
        boost_preds = [float(v) if math.isfinite(float(v)) else None for v in raw_pred]
    else:
        rng = np.random.default_rng(42)
        pack, gain = _fit_numpy_gbm(x_tr, y_tr, w_tr, rng=rng, **hyper)
        raw_pred = _predict_numpy_gbm(pack, x_te)
        boost_preds = [float(v) if math.isfinite(float(v)) else None for v in raw_pred]
        engine = "numpy_gbm"
    tree_s = round(time.perf_counter() - t_tree0, 2)

    t_ridge0 = time.perf_counter()
    _, ridge_preds = _fit_ridge_oos(
        xs_tr,
        ys_tr,
        xs_te,
        ys_te,
        metas_tr,
        feat_names,
        ridge_lambda=ridge_lambda,
        theme_boost=theme_boost,
        use_theme_weights=use_theme_weights,
        min_std_exempt=T75_MIN_STD_EXEMPT,
    )
    ridge_s = round(time.perf_counter() - t_ridge0, 2)
    oos_boost = _oos_pack(boost_preds, ys_te, metas_te, use_minute=True)
    oos_ridge = _oos_pack(ridge_preds, ys_te, metas_te, use_minute=True)
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
        "task": "t75_tree",
        "head": TREE_HEAD,
        "schema": TREE_SCHEMA,
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
        "feature_importance": _importance_rows(feat_names, gain),
        "oos": oos_boost,
        "ridge_oos": oos_ridge,
        "delta_vs_ridge": _delta_oos(oos_boost, oos_ridge),
        "live_hook": False,
        "backtest_hook": False,
        "persisted": {"success": False, "skipped": True, "reason": "shadow_only"},
        "note": (
            "ŷ_τ75_tree 影子头：同标签同 Holdout vs Ridge；不写 t75_ridge_model.json，"
            "不进交易执行 / 历史回测"
        ),
    }
    attach_holdout_meta(report, split_meta)
    return report
