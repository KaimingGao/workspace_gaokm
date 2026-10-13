"""ŷ_co Ridge：面板拼装与矩阵拟合（对齐 ``oo_ridge_compact`` / ``tc_ridge_compact``）。

报告包装 / Holdout OOS / 落盘启用见 ``co_ridge``。
底层 ``collect_co_compact`` / ``fit_keepall_ridge_matrix`` 在 ``panel_matrix``。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from core.research.co_panel import (
    CO_Z_FEATURES,
    CO_Z_LEGACY,
    theme_sample_weights,
)

# canon + 裸名都豁免（旧模型 / 旁路）
CO_MIN_STD_EXEMPT = tuple(CO_Z_FEATURES) + tuple(CO_Z_LEGACY)


def fit_co_ridge_matrices(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    ridge_lambda: float = 1.0,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    theme_boost: float = 1.5,
    holdout_trading_days: int = 20,
    use_theme_weights: bool = True,
    cross_section_zscore: bool = True,
) -> Dict[str, Any]:
    """拼面板 + Holdout 前训 Ridge（单套 β）。不含 OOS 报告包装。"""
    from core.research.panel_matrix import (
        collect_co_compact,
        fill_rates_from_matrix,
        fit_keepall_ridge_matrix,
        named_columns,
        predict_ridge_matrix,
        raw_aoo_finite,
    )

    X, col_names, ys, dates, metas, n_stocks = collect_co_compact(
        stock_bars,
        list(CO_Z_FEATURES),
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        include_alpha_oo=True,
        head="y_co",
    )
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"on 样本不足 n={len(ys)}（需≥20）",
            "sample_count": len(ys),
            "stock_count": n_stocks,
        }

    from core.research.holdout import (
        DEFAULT_HOLDOUT_TRADING_DAYS,
        calendar_dates_from_stock_bars,
        resolve_ridge_split,
    )
    from core.signal.factors.alpha_oo import (
        is_alpha_oo_raw_key,
        merge_alpha_oo_min_std_exempt,
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

    theme_w_tr = (
        theme_sample_weights(metas_tr, theme_boost=theme_boost)
        if use_theme_weights
        else None
    )

    feat_names = list(CO_Z_FEATURES)
    for k in raw_aoo_finite(X, col_names, train_idx):
        if is_alpha_oo_raw_key(str(k)) and k not in feat_names:
            feat_names.append(k)

    fill_keys = list(CO_Z_FEATURES)
    seen_fill = set(fill_keys)
    for name in col_names:
        if is_alpha_oo_raw_key(str(name)) and name not in seen_fill:
            seen_fill.add(name)
            fill_keys.append(name)
    feature_fill = fill_rates_from_matrix(X, col_names, fill_keys)
    X_fit, feat_names = named_columns(X, col_names, feat_names)
    del X

    min_std_exempt = merge_alpha_oo_min_std_exempt(
        feat_names, list(CO_MIN_STD_EXEMPT)
    )

    row_tr = np.asarray(train_idx, dtype=np.int64)
    row_te = np.asarray(test_idx, dtype=np.int64)
    y_tr_arr = np.asarray(ys_tr, dtype=np.float64)
    dates_tr = [dates[i] for i in train_idx]
    dates_te = [dates[i] for i in test_idx]
    use_cs = bool(cross_section_zscore)
    fit = fit_keepall_ridge_matrix(
        X_fit[row_tr],
        y_tr_arr,
        feat_names,
        ridge_lambda=ridge_lambda,
        sample_weights=theme_w_tr,
        min_std_exempt=min_std_exempt,
        row_dates=dates_tr if use_cs else None,
        cross_section_zscore=use_cs,
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
        predict_ridge_matrix(
            fit,
            X_fit[row_te],
            feat_names,
            row_dates=dates_te if use_cs else None,
        )
        if len(test_idx)
        else []
    )
    del X_fit

    model = dict(fit)
    model["model_role"] = "research"

    return {
        "success": True,
        "target": "overnight_gap",
        "n_stocks": n_stocks,
        "ys": ys,
        "dates": dates,
        "metas": metas,
        "ys_tr": ys_tr,
        "ys_te": ys_te,
        "metas_tr": metas_tr,
        "metas_te": metas_te,
        "preds_te": preds_te,
        "train_idx": train_idx,
        "test_idx": test_idx,
        "split_meta": split_meta,
        "hold_n": hold_n,
        "feat_names": feat_names,
        "fill_keys": fill_keys,
        "feature_fill": feature_fill,
        "use_cs": use_cs,
        "theme_boost": theme_boost,
        "use_theme_weights": use_theme_weights,
        "research_model": model,
        "model": model,
    }
