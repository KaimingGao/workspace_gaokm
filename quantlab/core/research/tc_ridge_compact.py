"""ŷ_τc Ridge：面板拼装与矩阵拟合（对齐 ``oo_ridge_compact``）。

报告包装 / Holdout OOS / 落盘启用见 ``tc_ridge``。
底层 ``collect_tau_compact`` / ``fit_keepall_ridge_matrix`` 在 ``panel_matrix``。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.research.tau_panel import (
    attach_cross_section_breadth,
    collect_tau_intraday_panel,
    collect_tau_open_panel,
    normalize_minute_tau_grid,
    theme_sample_weights,
)
from core.signal.factors.z_tc import (
    Z_TC_FIT_DROP_ALIASES,
    Z_TC_MINUTE_KEYS,
    feature_get,
)
from core.yhat.keys import TAU_Z_FEATURES

# open_gap ≡ gap_pct，只拟合其一，避免 Ridge 双计
TAU_FIT_DROP_ALIASES = Z_TC_FIT_DROP_ALIASES
# ŷ_τ30/60/90 Ridge 不要吃 ŷ_τ 的 OC 路径形状。HL/回撤/振幅在 30–90m 前瞻上共线对冲，
# ŷ 被压到训练均值（做 T 回测里多数 |ŷ_τ30|/|ŷ_τ60|<0.1%）。
# 树头加回这些键 + t_hi/t_lo / 动量加速度 / 量价（分段交互，不走线性对冲）。
TAU_HORIZON_TREE_SHAPE_FEATURES = (
    "range_pct",
    "loc_hl",
    "up_extent",
    "down_extent",
    "path_sign",
    "pullback_from_high",
    "bounce_from_low",
    "realized_vol",
    "vol_last3_vs_avg",
    "tau_elapsed_min",
    "t_hi_frac",
    "t_lo_frac",
    "t_hi_minus_lo",
    "room_to_high",
    "room_to_low",
    "mom_accel_5_15",
    "mom_accel_5_30",
    "vol_down_up",
    "range_efficiency",
    "vp_confirm",
    "vol_up_share",
    "pullback_x_vol",
)
TAU_HORIZON_DROP_OC_SHAPE = frozenset(TAU_HORIZON_TREE_SHAPE_FEATURES)


def with_horizon_tree_shape(ridge_z_features: Sequence[str]) -> Tuple[str, ...]:
    """Ridge Z + OC 路径形状。仅 ŷ_τ*_tree；Ridge 对照仍用原 Z。"""
    have = set(ridge_z_features)
    extra = tuple(k for k in TAU_HORIZON_TREE_SHAPE_FEATURES if k not in have)
    return tuple(ridge_z_features) + extra


def _stack_panels(
    enriched: Sequence[Dict[str, Any]],
) -> tuple:
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


def _subset(xs, ys, metas, idxs):
    return (
        [xs[i] for i in idxs],
        [ys[i] for i in idxs],
        [metas[i] for i in idxs],
    )


def _predict_rows(
    fit: Dict[str, Any],
    xs: List[dict],
    *,
    impute_missing: bool = True,
) -> List[Optional[float]]:
    """用 return_model 对行打分。

    ``impute_missing=True``（默认）：缺特征填 z=0（截面模型=当天均值；旧模型=训练窗均值）。
    """
    from core.research.feature_standardize import is_cross_section_zscore

    coefs = fit.get("coefficients") or {}
    intercept = float(fit.get("intercept") or 0.0)
    means = fit.get("zscore_means") or fit.get("z_means") or {}
    stds = fit.get("zscore_stds") or fit.get("z_stds") or {}
    cs = is_cross_section_zscore(fit)
    active = [
        n
        for n in (fit.get("active_features") or coefs.keys())
        if coefs.get(n) is not None
    ]
    out: List[Optional[float]] = []
    for row in xs:
        pred = intercept
        ok = True
        for name in active:
            v = feature_get(row, name)
            if v is None:
                if not impute_missing or not (means or cs):
                    ok = False
                    break
                # 缺省 → 当天（或训练）均值 → z=0
                z = 0.0
            else:
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    if not impute_missing or not (means or cs):
                        ok = False
                        break
                    z = 0.0
                else:
                    if cs or not means:
                        z = fv
                    else:
                        mu = float(means.get(name, 0.0))
                        sd = float(stds.get(name, 1.0))
                        if sd < 1e-9:
                            sd = 1.0
                        z = (fv - mu) / sd
            pred += float(coefs.get(name) or 0.0) * z
        out.append(round(pred, 6) if ok else None)
    return out


def build_tau_panels_from_bars(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    tau_hm: str = "open",
    tau_grid: Optional[Sequence[str]] = None,
    include_alpha_tc: bool = False,
) -> List[Dict[str, Any]]:
    """``stock_bars``: ``[{code, bars, minute_bars?, index_bars?, fundamentals?}, ...]``。

    ``tau_hm=open``：开盘→收盘标签；否则用分钟价训 τ→收盘（无分钟则跳过该日）。
    ``tau_grid``：变长前缀少数时钟（共享 β）。
    分钟路径下每只票 ``collect`` 完会 ``pop`` 掉该票 ``minute_bars``（meta 已带
    ``price_tau30…90``），避免满池 5m 常驻到拟合结束。
    """
    use_minute = str(tau_hm or "open").strip().lower() not in ("", "open")
    grid = (
        normalize_minute_tau_grid(tau_hm=tau_hm, tau_grid=tau_grid)
        if use_minute
        else None
    )
    raw: List[Dict[str, Any]] = []
    for item in stock_bars:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or item.get("stock_code") or "").strip()
        bars = list(item.get("bars") or [])
        if len(bars) < min_history + 2:
            # 分钟线只供 collect；跳过时也丢掉，避免整池常驻
            item.pop("minute_bars", None)
            continue
        if use_minute:
            xs, ys, dates, metas = collect_tau_intraday_panel(
                bars,
                item.get("minute_bars"),
                tau_hm=str(tau_hm),
                tau_grid=grid,
                min_history=min_history,
                index_bars=item.get("index_bars"),
                fundamentals=item.get("fundamentals"),
                stock_code=code,
                include_alpha_tc=include_alpha_tc,
            )
            # meta 已带 price_tau30…90；relabel 不再需要分钟线
            item.pop("minute_bars", None)
        else:
            xs, ys, dates, metas = collect_tau_open_panel(
                bars,
                min_history=min_history,
                index_bars=item.get("index_bars"),
                fundamentals=item.get("fundamentals"),
                stock_code=code,
                include_alpha_tc=include_alpha_tc,
            )
        if len(ys) < 4:
            continue
        raw.append(
            {
                "code": code,
                "xs": xs,
                "ys": ys,
                "dates": dates,
                "metas": metas,
            }
        )
    return attach_cross_section_breadth(raw, gap_trigger_pct=gap_trigger_pct)



def fit_tau_ridge_matrices(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    ridge_lambda: float = 1.0,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    theme_boost: float = 1.5,
    holdout_trading_days: int = 20,
    use_theme_weights: bool = True,
    tau_hm: str = "open",
    tau_grid: Optional[Sequence[str]] = None,
    cross_section_zscore: bool = True,
) -> Dict[str, Any]:
    """拼面板 + Holdout 前训 Ridge（单套 β）。不含 OOS 报告包装。"""
    tau_key = str(tau_hm or "open").strip() or "open"
    use_minute = tau_key.lower() not in ("", "open")
    grid = (
        normalize_minute_tau_grid(tau_hm=tau_key, tau_grid=tau_grid)
        if use_minute
        else None
    )
    from core.research.panel_matrix import (
        collect_tau_compact,
        fill_rates_from_matrix,
        fit_keepall_ridge_matrix,
        named_columns,
        predict_ridge_matrix,
        raw_atc_finite,
    )

    X, col_names, ys, dates, metas, n_stocks = collect_tau_compact(
        stock_bars,
        list(TAU_Z_FEATURES),
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        tau_hm=tau_key,
        tau_grid=grid,
        include_alpha_tc=True,
        head="y_tc",
    )
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"rem 样本不足 n={len(ys)}（需≥20）",
            "sample_count": len(ys),
            "stock_count": n_stocks,
            "tau": tau_key,
            "tau_grid": grid,
        }

    ys_use, dates_use, metas_use = ys, dates, metas
    target = "tau_to_close_z"

    from core.research.holdout import (
        DEFAULT_HOLDOUT_TRADING_DAYS,
        calendar_dates_from_stock_bars,
        resolve_ridge_split,
    )

    hold_n = int(holdout_trading_days or DEFAULT_HOLDOUT_TRADING_DAYS)
    train_idx, test_idx, split_meta = resolve_ridge_split(
        dates_use,
        holdout_trading_days=hold_n,
        calendar_dates=calendar_dates_from_stock_bars(stock_bars),
    )
    ys_tr = [ys_use[i] for i in train_idx]
    ys_te = [ys_use[i] for i in test_idx]
    metas_tr = [metas_use[i] for i in train_idx]
    metas_te = [metas_use[i] for i in test_idx]

    theme_w_tr = (
        theme_sample_weights(metas_tr, theme_boost=theme_boost)
        if use_theme_weights
        else None
    )

    drop_opt = set() if use_minute else set(Z_TC_MINUTE_KEYS)
    feat_names = [
        k for k in TAU_Z_FEATURES if k not in drop_opt and k not in TAU_FIT_DROP_ALIASES
    ]
    seen = set(feat_names)
    for k in raw_atc_finite(X, col_names, train_idx):
        if k not in seen:
            seen.add(k)
            feat_names.append(k)

    from core.signal.factors.alpha_tc import is_alpha_tc_raw_key

    fill_keys = list(TAU_Z_FEATURES)
    seen_fill = set(fill_keys)
    for name in col_names:
        if is_alpha_tc_raw_key(str(name)) and name not in seen_fill:
            seen_fill.add(name)
            fill_keys.append(name)
    feature_fill = fill_rates_from_matrix(X, col_names, fill_keys)
    X_fit, feat_names = named_columns(X, col_names, feat_names)
    del X

    row_tr = np.asarray(train_idx, dtype=np.int64)
    row_te = np.asarray(test_idx, dtype=np.int64)
    y_tr = np.asarray(ys_tr, dtype=np.float64)
    dates_tr = [dates_use[i] for i in train_idx]
    dates_te = [dates_use[i] for i in test_idx]
    use_cs = bool(cross_section_zscore)
    fit = fit_keepall_ridge_matrix(
        X_fit[row_tr],
        y_tr,
        feat_names,
        ridge_lambda=ridge_lambda,
        sample_weights=theme_w_tr,
        row_dates=dates_tr if use_cs else None,
        cross_section_zscore=use_cs,
    )
    if not fit.get("success"):
        fit = {
            "success": True,
            "intercept": 0.0,
            "coefficients": {},
            "active_features": [],
            "zscore_means": {},
            "zscore_stds": {},
            "note": "Z 方差不足，ŷ_τ 用训练均值",
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
        "tau_key": tau_key,
        "use_minute": use_minute,
        "grid": grid,
        "target": target,
        "n_stocks": n_stocks,
        "ys": ys,
        "ys_use": ys_use,
        "dates_use": dates_use,
        "metas_use": metas_use,
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
