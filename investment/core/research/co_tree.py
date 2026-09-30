"""ŷ_co_tree：独立浅树头，标签与 ŷ_co 相同（open[T+1]/close[T]−1）。

隔夜缺口 Z 面板 + Holdout；对照同窗 Ridge（Z-only）。
只写 ``co_tree_last_report.json``。不进 live / 回测；无启用研究/执行。
引擎仅 LightGBM。
"""

from __future__ import annotations

import logging
import math
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.research.co_panel import CO_Z_FEATURES
from core.research.co_ridge import (
    _stack_panels,
    _z_only_xs,
    build_co_panels_from_bars,
)
from core.research.tc_tree import (
    DEFAULT_LEARNING_RATE,
    DEFAULT_MAX_DEPTH,
    DEFAULT_N_ESTIMATORS,
    DEFAULT_SUBSAMPLE,
    _delta_oos,
    _design_matrix,
    _fit_lightgbm,
    _fit_ridge_oos,
    _importance_rows,
    _oos_pack,
    _predict_lightgbm,
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


def _feature_names_from_rows(
    xs: Sequence[dict],
    *,
    include_alpha158: bool,
) -> Tuple[List[str], List[str], List[str]]:
    """返回 (tree_feats, ridge_feats, a158_keys)。Ridge 对照仅 CO Z。"""
    present = set()
    for row in xs:
        if not isinstance(row, dict):
            continue
        for k, v in row.items():
            if v is None:
                continue
            try:
                float(v)
            except (TypeError, ValueError):
                continue
            present.add(str(k))
    ridge_feats = [n for n in CO_Z_FEATURES if n in present]
    a158_keys: List[str] = []
    if include_alpha158:
        from core.signal.factors.alpha158 import collect_alpha158_raw_keys_from_rows

        a158_keys = list(collect_alpha158_raw_keys_from_rows(xs))
    tree_feats = list(ridge_feats)
    for k in a158_keys:
        if k not in tree_feats:
            tree_feats.append(k)
    return tree_feats, ridge_feats, a158_keys


def _subset(
    xs: Sequence[dict],
    ys: Sequence[float],
    metas: Sequence[dict],
    idx: Sequence[int],
) -> Tuple[List[dict], List[float], List[dict]]:
    return (
        [xs[i] for i in idx],
        [ys[i] for i in idx],
        [metas[i] for i in idx],
    )


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
    """隔夜缺口面板拟合 ŷ_co_tree + Ridge OOS 对照。不写 live / 研究套。"""
    t0 = time.perf_counter()
    t_panel0 = time.perf_counter()
    enriched = build_co_panels_from_bars(
        stock_bars,
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        include_alpha158=include_alpha158,
    )
    xs_raw, ys, dates, metas = _stack_panels(enriched)
    # 树侧可含 a158；Ridge 对照压成 Z-only
    xs_tree = list(xs_raw)
    xs_ridge = _z_only_xs(xs_raw)
    panel_s = round(time.perf_counter() - t_panel0, 2)
    n_stocks = len(enriched)
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

    feat_names, ridge_feat_names, a158_keys = _feature_names_from_rows(
        xs_tree, include_alpha158=include_alpha158
    )
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
    xs_tr, ys_tr, metas_tr = _subset(xs_tree, ys, metas, train_idx)
    xs_te, ys_te, metas_te = _subset(xs_tree, ys, metas, test_idx)
    xs_tr_r, _, metas_tr_r = _subset(xs_ridge, ys, metas, train_idx)
    xs_te_r, _, _ = _subset(xs_ridge, ys, metas, test_idx)
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

    x_tr, means = _design_matrix(xs_tr, feat_names)
    x_te, _ = _design_matrix(xs_te, feat_names, means=means)
    y_tr = np.asarray(ys_tr, dtype=np.float64)
    w_tr = np.ones_like(y_tr)

    engine = resolve_tree_backend(backend)
    hyper = {
        "n_estimators": int(n_estimators),
        "max_depth": int(max_depth),
        "learning_rate": float(learning_rate),
        "subsample": float(subsample),
    }
    t_tree0 = time.perf_counter()
    model, gain = _fit_lightgbm(x_tr, y_tr, w_tr, **hyper)
    raw = _predict_lightgbm(model, x_te)
    boost_preds = [float(v) if math.isfinite(float(v)) else None for v in raw]
    tree_s = round(time.perf_counter() - t_tree0, 2)

    t_ridge0 = time.perf_counter()
    ridge_names = ridge_feat_names or list(CO_Z_FEATURES)
    _, ridge_preds = _fit_ridge_oos(
        xs_tr_r,
        ys_tr,
        xs_te_r,
        ys_te,
        metas_tr_r,
        ridge_names,
        ridge_lambda=ridge_lambda,
        theme_boost=theme_boost,
        use_theme_weights=True,
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
        "oos": oos_boost,
        "ridge_oos": oos_ridge,
        "delta_vs_ridge": _delta_oos(oos_boost, oos_ridge),
        "live_hook": False,
        "backtest_hook": False,
        "persisted": {"success": False, "skipped": True, "reason": "shadow_only"},
        "note": (
            "ŷ_co_tree 影子头：open[T+1]/close[T]−1 · 同 Holdout vs Ridge(Z)；"
            + (
                "树侧可含 raw_alpha158_*（≤T−1）；"
                if include_alpha158
                else ""
            )
            + "不写 live，不进交易执行 / 历史回测 / 主排序"
        ),
    }
    attach_holdout_meta(report, split_meta)
    return report
