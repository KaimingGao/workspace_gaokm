"""ŷ_oo_tree：独立树头，标签与 ŷ_oo 相同（open[T+1]/open[T]−1）。

日线因子面板 + Holdout；对照同窗 Ridge。卡头可调日截面 Z / 滑窗增量。
拟合写入 ``oo_tree_model.json``，供调仓回测「ŷ头=Tree」。不进交易执行。引擎仅 LightGBM。
"""

from __future__ import annotations

import logging
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
    DEFAULT_TREE_STEP_DAYS,
    DEFAULT_TREE_WINDOW_DAYS,
    _delta_oos,
    _importance_rows,
    _oos_pack,
    fit_lgb_on_matrices,
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
    from core.research.holdout import stamp_fitted_at

    stamp_fitted_at(report)
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


def _oo_column_names(*, include_alpha158: bool) -> List[str]:
    from core.signal.factors.meta.health import unsourced_factor_names
    from core.signal.factors.meta.registry import registered_factor_names

    banned = set(unsourced_factor_names())
    names = [n for n in registered_factor_names() if n not in banned]
    if include_alpha158:
        from core.research.oo_ridge_compact import alpha158_raw_names

        seen = set(names)
        for key in alpha158_raw_names():
            if key not in seen and key not in banned:
                seen.add(key)
                names.append(key)
    return names


def _stack_daily_panels(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    horizon_days: int = 1,
    min_history: int = 12,
    include_alpha158: bool = True,
) -> Tuple[np.ndarray, List[str], List[float], List[str], List[dict], int]:
    """逐股写入 float64 后丢掉行 dict。返回 (X, names, ys, dates, metas, n_stocks)。"""
    from core.research.panel import collect_subscore_forward_panel
    from core.research.panel_matrix import PanelMatrix

    block = PanelMatrix(
        _oo_column_names(include_alpha158=include_alpha158),
        widen_prefix="raw_alpha158_" if include_alpha158 else "",
    )
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
        n_align = min(len(rows), len(y_list), len(d_list))
        if n_align <= 0:
            continue
        block.add_rows(list(rows)[:n_align])
        del rows
        n_stocks += 1
        for y, d in zip(list(y_list)[:n_align], list(d_list)[:n_align]):
            ys.append(float(y))
            day = str(d)[:10]
            dates.append(day)
            metas.append({"code": code, "theme_day": 0, "date": day})
    X = block.finalize()
    logger.info(
        "compact panel head=y_oo_tree rows=%s cols=%s stocks=%s",
        int(X.shape[0]),
        int(X.shape[1]),
        n_stocks,
    )
    return X, list(block.names), ys, dates, metas, n_stocks


def _feature_names_from_matrix(
    names: Sequence[str],
    present: set,
    *,
    include_alpha158: bool,
) -> Tuple[List[str], List[str], List[str]]:
    """返回 (tree_feats, ridge_feats, a158_keys)。proxy 已在列名里剔除。"""
    from core.signal.factors.meta.health import unsourced_factor_names
    from core.signal.factors.meta.registry import registered_factor_names

    banned = set(unsourced_factor_names())
    base = [n for n in registered_factor_names() if n not in banned]
    ridge_feats = [n for n in base if n in present]
    a158_keys = [
        n
        for n in names
        if n in present and str(n).startswith("raw_alpha158_") and n not in banned
    ] if include_alpha158 else []
    tree_feats = list(ridge_feats)
    for k in a158_keys:
        if k not in tree_feats:
            tree_feats.append(k)
    return tree_feats, ridge_feats, a158_keys


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
    window_days: int = DEFAULT_TREE_WINDOW_DAYS,
    step_days: int = DEFAULT_TREE_STEP_DAYS,
    cross_section_zscore: bool = True,
) -> Dict[str, Any]:
    """日线面板拟合 ŷ_oo_tree + Ridge OOS 对照。不写 live / 研究套。

    ``include_alpha158=True`` 时树吃 Alpha158。标签仍是百分点。
    ``cross_section_zscore``：日截面 z（默认）或训练窗全局 μ/σ。
    LightGBM：深度 6、300 轮、叶子 64、λ₁=10、λ₂=20、学习率 0.2。
    ``window_days>0``：训练日滑窗，每窗 ``init_model`` 增量加树（总轮数均分）。
    """
    from core.research.panel_matrix import keepall_ridge_oos, finite_name_set, named_columns
    from core.research.tc_ridge import TAU_MIN_STD_EXEMPT

    t0 = time.perf_counter()
    t_panel0 = time.perf_counter()
    X, col_names, ys, dates, metas, n_stocks = _stack_daily_panels(
        stock_bars,
        horizon_days=horizon_days,
        min_history=min_history,
        include_alpha158=include_alpha158,
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

    feat_names, ridge_feat_names, a158_keys = _feature_names_from_matrix(
        col_names,
        finite_name_set(X, col_names),
        include_alpha158=include_alpha158,
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
    ys_tr = [ys[i] for i in train_idx]
    ys_te = [ys[i] for i in test_idx]
    metas_te = [metas[i] for i in test_idx]
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

    X_tree, feat_names = named_columns(X, col_names, feat_names)
    X_ridge, ridge_feat_names = named_columns(X, col_names, ridge_feat_names)
    del X
    row_tr = np.asarray(train_idx, dtype=np.int64)
    row_te = np.asarray(test_idx, dtype=np.int64)

    engine = resolve_tree_backend(backend)
    use_cs = bool(cross_section_zscore)
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
        cross_section_zscore=use_cs,
        train_dates=[dates[i] for i in train_idx],
        test_dates=[dates[i] for i in test_idx],
        window_days=window_days,
        step_days=step_days,
    )
    from core.research.horizon_tree import pack_tree_return_model

    return_model = pack_tree_return_model(
        head="oo",
        backend=engine,
        feature_names=feat_names,
        impute_means=means,
        model_obj=model,
        schema=TREE_SCHEMA,
        hyperparams=hyper,
        kind="return",
        y_label="open[T+1]/open[T]-1",
    )

    t_ridge0 = time.perf_counter()
    _, ridge_preds = keepall_ridge_oos(
        X_ridge[row_tr],
        ys_tr,
        X_ridge[row_te],
        ridge_feat_names,
        ridge_lambda=ridge_lambda,
        sample_weights=None,
        min_std_exempt=TAU_MIN_STD_EXEMPT,
        row_dates_tr=[dates[i] for i in train_idx],
        row_dates_te=[dates[i] for i in test_idx],
        cross_section_zscore=use_cs,
    )
    ridge_s = round(time.perf_counter() - t_ridge0, 2)

    oos_boost = _oos_pack(boost_preds, ys_te, metas_te, use_minute=False)
    oos_ridge = _oos_pack(ridge_preds, ys_te, metas_te, use_minute=False)
    oos_boost.update(
        {
            "n_train": len(ys_tr),
            "n_test": len(ys_te),
            "holdout_trading_days": hold_n,
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
        "tree_return_model": return_model,
        "oos": oos_boost,
        "ridge_oos": oos_ridge,
        "delta_vs_ridge": _delta_oos(oos_boost, oos_ridge),
        "live_hook": False,
        "backtest_hook": True,
        "persisted": {"success": False, "skipped": True, "reason": "fit_only"},
        "window_days": int(hyper.get("window_days") or 0),
        "step_days": int(hyper.get("step_days") or 0),
        "n_windows": int(hyper.get("n_windows") or 0),
        "cross_section_zscore": use_cs,
        "note": (
            "ŷ_oo_tree：open→open 标签 · "
            + ("特征日截面 z-score · " if use_cs else "特征训练窗全局 μ/σ · ")
            + "同 Holdout vs Ridge；"
            + (
                "树侧可含 raw_alpha158_*（Ridge 对照不含 a158）；"
                if include_alpha158
                else ""
            )
            + (
                f"训练日滑窗 {hyper.get('window_days')}／步 {hyper.get('step_days')}"
                f"（{hyper.get('n_windows')} 窗 · init_model 增量 · 共 {hyper.get('total_trees')} 树）；"
                if int(hyper.get("window_days") or 0) > 0
                else "全样本一次训；"
            )
            + "写入 oo_tree_model.json 后，调仓回测选 Tree 替换 ŷ_oo；不进交易执行"
        ),
    }
    attach_holdout_meta(report, split_meta)
    from core.research.horizon_tree import stamp_tree_fitted_at

    stamp_tree_fitted_at(report)
    return report


def oo_tree_model_path() -> str:
    from core.research.horizon_tree import tree_model_path

    return tree_model_path("oo")


def load_oo_tree_model() -> Optional[Dict[str, Any]]:
    from core.research.horizon_tree import load_tree_model_doc

    return load_tree_model_doc("oo")


def persist_oo_tree_model(
    report: Dict[str, Any],
    *,
    note: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    from core.research.horizon_tree import persist_tree_model_doc

    return persist_tree_model_doc(
        "oo", report, note=note or "oo_tree promote", force=force
    )


def predict_oo_tree_from_features(
    features: Dict[str, Optional[float]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    from core.research.horizon_tree import predict_tree_return

    doc = model_doc if model_doc is not None else load_oo_tree_model()
    if not doc:
        return None
    rm = doc.get("return_model") if isinstance(doc.get("return_model"), dict) else doc
    return predict_tree_return(features, rm)


def oo_tree_features_from_window(
    window: Sequence[dict],
    quote: Optional[dict] = None,
) -> Dict[str, Any]:
    """与拟合面板同一套研究因子行（含 raw_alpha158_*）。"""
    from core.research.panel import _research_sub_scores

    return _research_sub_scores(
        list(window or []),
        quote=quote if isinstance(quote, dict) else None,
    )
