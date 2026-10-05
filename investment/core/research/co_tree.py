"""ŷ_co_tree：独立树头，标签与 ŷ_co 相同（open[T+1]/close[T]−1）。

隔夜缺口 Z 面板 + Holdout；对照同窗 Ridge（Z-only）。
拟合写入 ``co_tree_model.json``，供调仓回测「ŷ头=Tree」。不进交易执行。
引擎仅 LightGBM。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.research.co_panel import CO_Z_FEATURES
from core.research.tc_ridge import TAU_MIN_STD_EXEMPT
from core.research.tc_tree import (
    DEFAULT_LEARNING_RATE,
    DEFAULT_MAX_DEPTH,
    DEFAULT_N_ESTIMATORS,
    DEFAULT_SUBSAMPLE,
    _delta_oos,
    _importance_rows,
    _oos_pack,
    fit_lgb_on_matrices,
    resolve_tree_backend,
)

TREE_SCHEMA = "co_tree_shadow_v1"
TREE_HEAD = "y_co_tree"


def co_tree_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "co_tree_last_report.json")


def save_co_tree_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    from core.research.holdout import stamp_fitted_at

    stamp_fitted_at(report)
    path = co_tree_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)


def load_co_tree_last_report() -> Optional[Dict[str, Any]]:
    import json

    path = co_tree_last_report_path()
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except OSError:
        return None
    except Exception:  # noqa: BLE001
        logger.debug("load co tree last_report failed", exc_info=True)
        return None
    if isinstance(doc, dict) and doc.get("success"):
        return doc
    return None


def fit_co_tree_report(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    ridge_lambda: float = 1.0,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    theme_boost: float = 1.5,
    holdout_trading_days: int = 20,
    backend: Optional[str] = None,
    n_estimators: int = DEFAULT_N_ESTIMATORS,
    max_depth: int = DEFAULT_MAX_DEPTH,
    learning_rate: float = DEFAULT_LEARNING_RATE,
    subsample: float = DEFAULT_SUBSAMPLE,
    include_alpha158: bool = True,
) -> Dict[str, Any]:
    """隔夜缺口面板拟合 ŷ_co_tree + Ridge OOS 对照。不写 live / 研究套。

    ``include_alpha158=True`` 时树吃 Alpha158。特征按训练集做总体 z-score，与 ŷ_oo_tree 同口径；标签仍是百分点。
    LightGBM：深度 6、300 轮、叶子 64、λ₁=10、λ₂=20、学习率 0.2。
    """
    t0 = time.perf_counter()
    from core.research.panel_matrix import (
        collect_co_compact,
        demeaned_ridge_oos,
        finite_name_set,
        named_columns,
        raw_alpha158_finite,
    )
    from core.research.tau_panel import theme_sample_weights

    t_panel0 = time.perf_counter()
    X, col_names, ys, dates, metas, n_stocks = collect_co_compact(
        stock_bars,
        list(CO_Z_FEATURES),
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        include_alpha158=include_alpha158,
        head="y_co_tree",
    )
    panel_s = round(time.perf_counter() - t_panel0, 2)
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"树样本不足 n={len(ys)}（需≥20）",
            "task": "co_tree",
            "head": TREE_HEAD,
            "sample_count": len(ys),
            "stock_count": n_stocks,
            "schema": TREE_SCHEMA,
            "live_hook": False,
            "backtest_hook": False,
        }

    present = finite_name_set(X, col_names)
    ridge_feat_names = [n for n in CO_Z_FEATURES if n in present]
    a158_keys = raw_alpha158_finite(X, col_names) if include_alpha158 else []
    feat_names = list(ridge_feat_names)
    for k in a158_keys:
        if k not in feat_names:
            feat_names.append(k)
    if len(feat_names) < 2:
        return {
            "success": False,
            "error": "可用因子不足",
            "task": "co_tree",
            "head": TREE_HEAD,
            "sample_count": len(ys),
            "schema": TREE_SCHEMA,
            "live_hook": False,
            "backtest_hook": False,
        }

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
        label_horizon_days=1,
        calendar_dates=calendar_dates_from_stock_bars(stock_bars),
    )
    ys_tr = [ys[i] for i in train_idx]
    ys_te = [ys[i] for i in test_idx]
    metas_tr = [metas[i] for i in train_idx]
    metas_te = [metas[i] for i in test_idx]
    if len(ys_tr) < 16 or len(ys_te) < 8:
        return {
            "success": False,
            "error": f"Holdout 切分后样本不足 训={len(ys_tr)} 测={len(ys_te)}",
            "task": "co_tree",
            "head": TREE_HEAD,
            "sample_count": len(ys),
            "schema": TREE_SCHEMA,
            "live_hook": False,
            "backtest_hook": False,
        }

    X_tree, feat_names = named_columns(X, col_names, feat_names)
    ridge_names = ridge_feat_names or list(CO_Z_FEATURES)
    X_ridge, ridge_names = named_columns(X, col_names, ridge_names)
    del X
    row_tr = np.asarray(train_idx, dtype=np.int64)
    row_te = np.asarray(test_idx, dtype=np.int64)

    engine = resolve_tree_backend(backend)
    model, gain, hyper, means, boost_preds, tree_s = fit_lgb_on_matrices(
        X_tree[row_tr],
        ys_tr,
        X_tree[row_te],
        feat_names,
        n_estimators=n_estimators,
        max_depth=max_depth,
        learning_rate=learning_rate,
        subsample=subsample,
        feature_zscore=True,
    )
    y_tr = np.asarray(ys_tr, dtype=np.float64)
    from core.research.horizon_tree import pack_tree_return_model

    return_model = pack_tree_return_model(
        head="co",
        backend=engine,
        feature_names=feat_names,
        impute_means=means,
        model_obj=model,
        schema=TREE_SCHEMA,
        hyperparams=hyper,
        kind="return",
        y_label="open[T+1]/close[T]-1",
    )

    t_ridge0 = time.perf_counter()
    _, ridge_preds = demeaned_ridge_oos(
        X_ridge[row_tr],
        ys_tr,
        X_ridge[row_te],
        ridge_names,
        ridge_lambda=ridge_lambda,
        sample_weights=theme_sample_weights(metas_tr, theme_boost=theme_boost),
        min_std_exempt=TAU_MIN_STD_EXEMPT,
    )
    ridge_s = round(time.perf_counter() - t_ridge0, 2)

    oos_boost = _oos_pack(boost_preds, ys_te, metas_te, use_minute=False)
    oos_ridge = _oos_pack(ridge_preds, ys_te, metas_te, use_minute=False)
    oos_boost.update(
        {
            "n_train": len(ys_tr),
            "n_test": len(ys_te),
            "holdout_trading_days": hold_n,
            "y_label_mean": round(float(np.mean(y_tr)), 6) if y_tr.size else None,
            "label": "open[T+1]/close[T]-1",
        }
    )
    oos_ridge["n_train"] = len(ys_tr)
    oos_ridge["n_test"] = len(ys_te)
    oos_ridge["holdout_trading_days"] = hold_n

    report: Dict[str, Any] = {
        "success": True,
        "task": "co_tree",
        "head": TREE_HEAD,
        "schema": TREE_SCHEMA,
        "stock_count": n_stocks,
        "sample_count": len(ys),
        "backend": engine,
        "hyperparams": hyper,
        "timing": {
            "panel_s": panel_s,
            "tree_s": tree_s,
            "ridge_s": ridge_s,
            "fit_s": round(time.perf_counter() - t0, 2),
        },
        "feature_names": list(feat_names),
        "ridge_feature_names": list(ridge_names),
        "include_alpha158": bool(include_alpha158),
        "n_alpha158_features": len(a158_keys),
        "feature_importance": _importance_rows(feat_names, gain),
        "tree_return_model": return_model,
        "oos": oos_boost,
        "ridge_oos": oos_ridge,
        "delta_vs_ridge": _delta_oos(oos_boost, oos_ridge),
        "live_hook": False,
        "backtest_hook": True,
        "persisted": {"success": False, "skipped": True, "reason": "fit_only"},
        "note": (
            "ŷ_co_tree：open[T+1]/close[T]−1 · 特征训练集 z-score（与 ŷ_oo_tree 同口径）· 同 Holdout vs Ridge(Z)；"
            + ("树侧可含 raw_alpha158_*（≤T−1）；" if include_alpha158 else "")
            + "写入 co_tree_model.json 后，调仓回测选 Tree 替换 ŷ_co；不进交易执行"
        ),
    }
    attach_holdout_meta(report, split_meta)
    from core.research.horizon_tree import stamp_tree_fitted_at

    stamp_tree_fitted_at(report)
    return report


def co_tree_model_path() -> str:
    from core.research.horizon_tree import tree_model_path

    return tree_model_path("co")


def load_co_tree_model() -> Optional[Dict[str, Any]]:
    from core.research.horizon_tree import load_tree_model_doc

    return load_tree_model_doc("co")


def persist_co_tree_model(
    report: Dict[str, Any],
    *,
    note: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    from core.research.horizon_tree import persist_tree_model_doc

    return persist_tree_model_doc(
        "co", report, note=note or "co_tree promote", force=force
    )


def predict_co_tree_from_features(
    features: Dict[str, Optional[float]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    from core.research.horizon_tree import predict_tree_return

    doc = model_doc if model_doc is not None else load_co_tree_model()
    if not doc:
        return None
    rm = doc.get("return_model") if isinstance(doc.get("return_model"), dict) else doc
    return predict_tree_return(features, rm)
