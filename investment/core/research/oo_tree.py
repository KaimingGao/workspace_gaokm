"""ŷ_oo_tree：独立浅树头，标签与 ŷ_oo 相同（open[T+1]/open[T]−1）。

日线因子面板 + Holdout；对照同窗 Ridge。只写 ``oo_tree_last_report.json``。
不进 live / 回测；无启用研究/执行。引擎仅 LightGBM。
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

TREE_SCHEMA = "oo_tree_shadow_v1"
TREE_HEAD = "y_oo_tree"


def oo_tree_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "oo_tree_last_report.json")


def save_oo_tree_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    path = oo_tree_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)


def load_oo_tree_last_report() -> Optional[Dict[str, Any]]:
    import json

    path = oo_tree_last_report_path()
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except OSError:
        return None
    except Exception:  # noqa: BLE001
        logger.debug("load oo tree last_report failed", exc_info=True)
        return None
    if isinstance(doc, dict) and doc.get("success"):
        return doc
    return None


def _stack_daily_panels(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    horizon_days: int = 1,
    min_history: int = 12,
) -> Tuple[List[dict], List[float], List[str], List[dict], int]:
    from core.research.panel import collect_subscore_forward_panel

    xs: List[dict] = []
    ys: List[float] = []
    dates: List[str] = []
    metas: List[dict] = []
    n_stocks = 0
    for item in stock_bars or []:
        if not isinstance(item, dict):
            continue
        bars = item.get("bars") or []
        if not bars:
            continue
        code = str(item.get("code") or item.get("stock_code") or "").strip()
        try:
            rows, y_list, d_list = collect_subscore_forward_panel(
                bars,
                horizon_days=horizon_days,
                min_history=min_history,
                stock_code=code or None,
                pit_fundamentals=False,
            )
        except Exception:  # noqa: BLE001
            logger.debug("oo_tree panel failed code=%s", code, exc_info=True)
            continue
        if not rows:
            continue
        n_stocks += 1
        for row, y, d in zip(rows, y_list, d_list):
            xs.append(dict(row or {}))
            ys.append(float(y))
            dates.append(str(d)[:10])
            metas.append({"code": code, "theme_day": 0, "date": str(d)[:10]})
    return xs, ys, dates, metas, n_stocks


def _feature_names_from_rows(
    xs: Sequence[dict],
    *,
    include_alpha158: bool,
) -> Tuple[List[str], List[str], List[str]]:
    """返回 (tree_feats, ridge_feats, a158_keys)。proxy 剔除。"""
    from core.signal.factors.meta.health import unsourced_factor_names
    from core.signal.factors.meta.registry import registered_factor_names

    banned = set(unsourced_factor_names())
    base = [n for n in registered_factor_names() if n not in banned]
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
    ridge_feats = [n for n in base if n in present]
    a158_keys: List[str] = []
    if include_alpha158:
        from core.signal.factors.alpha158 import collect_alpha158_raw_keys_from_rows

        a158_keys = [
            k for k in collect_alpha158_raw_keys_from_rows(xs) if k not in banned
        ]
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


def fit_oo_tree_report(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    horizon_days: int = 1,
    ridge_lambda: float = 1.0,
    min_history: int = 12,
    holdout_trading_days: int = 20,
    backend: Optional[str] = None,
    n_estimators: int = DEFAULT_N_ESTIMATORS,
    max_depth: int = DEFAULT_MAX_DEPTH,
    learning_rate: float = DEFAULT_LEARNING_RATE,
    subsample: float = DEFAULT_SUBSAMPLE,
    include_alpha158: bool = True,
) -> Dict[str, Any]:
    """日线面板拟合 ŷ_oo_tree + Ridge OOS 对照。不写 live / 研究套。"""
    t0 = time.perf_counter()
    t_panel0 = time.perf_counter()
    xs, ys, dates, metas, n_stocks = _stack_daily_panels(
        stock_bars,
        horizon_days=horizon_days,
        min_history=min_history,
    )
    panel_s = round(time.perf_counter() - t_panel0, 2)
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"树样本不足 n={len(ys)}（需≥20）",
            "task": "oo_tree",
            "head": TREE_HEAD,
            "sample_count": len(ys),
            "stock_count": n_stocks,
            "schema": TREE_SCHEMA,
            "live_hook": False,
            "backtest_hook": False,
        }

    feat_names, ridge_feat_names, a158_keys = _feature_names_from_rows(
        xs, include_alpha158=include_alpha158
    )
    if len(feat_names) < 2:
        return {
            "success": False,
            "error": "可用因子不足（proxy 已剔除）",
            "task": "oo_tree",
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
        label_horizon_days=int(horizon_days or 1),
        calendar_dates=calendar_dates_from_stock_bars(stock_bars),
    )
    xs_tr, ys_tr, metas_tr = _subset(xs, ys, metas, train_idx)
    xs_te, ys_te, metas_te = _subset(xs, ys, metas, test_idx)
    if len(ys_tr) < 16 or len(ys_te) < 8:
        return {
            "success": False,
            "error": f"Holdout 切分后样本不足 训={len(ys_tr)} 测={len(ys_te)}",
            "task": "oo_tree",
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
    _, ridge_preds = _fit_ridge_oos(
        xs_tr,
        ys_tr,
        xs_te,
        ys_te,
        metas_tr,
        ridge_feat_names,
        ridge_lambda=ridge_lambda,
        theme_boost=1.0,
        use_theme_weights=False,
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
            "label": "open[T+1]/open[T]-1",
        }
    )
    oos_ridge["n_train"] = len(ys_tr)
    oos_ridge["n_test"] = len(ys_te)
    oos_ridge["holdout_trading_days"] = hold_n

    report: Dict[str, Any] = {
        "success": True,
        "task": "oo_tree",
        "head": TREE_HEAD,
        "schema": TREE_SCHEMA,
        "stock_count": n_stocks,
        "sample_count": len(ys),
        "horizon_days": int(horizon_days or 1),
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
            "ŷ_oo_tree 影子头：open→open 标签 · 同 Holdout vs Ridge；"
            + (
                "树侧可含 raw_alpha158_*（Ridge 对照不含 proxy / 可选不含 a158）；"
                if include_alpha158
                else ""
            )
            + "不写 return_score_model_*.json，不进交易执行 / 历史回测"
        ),
    }
    attach_holdout_meta(report, split_meta)
    return report
