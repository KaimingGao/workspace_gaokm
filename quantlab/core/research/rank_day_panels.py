"""排序头日截面统一壳：``oo_rank`` / ``τc_rank`` 同构出口。

两边最终都是 ``group_day_matrices`` 的日包
``{date, codes, ys, X, names}``；差别只在特征族 / L1 family。

- ``oo_rank`` → ``build_oo_rank_day_panels``（可复用 ``oo/`` L1）
- ``τc_rank`` / 开盘 ``τc`` → ``collect_tau_compact`` + 入模列裁剪
  （开盘钟 ``tc`` 与 ``tc_rank`` 指纹一致，X/y 同口径，定档可共建一次）
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)


def tau_compact_to_day_panels(
    X: np.ndarray,
    col_names: Sequence[str],
    ys: Sequence[float],
    dates: Sequence[str],
    metas: Sequence[dict],
    *,
    feature_dates: Sequence[str] = (),
    min_names: int = 8,
) -> List[Dict[str, Any]]:
    """``collect_tau_compact`` 矩阵 → 排序/定档日截面（入模列 + 按日切）。"""
    from core.research.oo_rank_panel import group_day_matrices
    from core.research.panel_matrix import raw_atc_finite
    from core.research.tc_ridge_compact import TAU_FIT_DROP_ALIASES, TAU_Z_FEATURES
    from core.signal.factors.z_tc import Z_TC_MINUTE_KEYS

    drop = set(Z_TC_MINUTE_KEYS) | set(TAU_FIT_DROP_ALIASES)
    col_set = set(col_names)
    feat_names = [k for k in TAU_Z_FEATURES if k not in drop and k in col_set]
    seen = set(feat_names)
    fit_set = {str(d)[:10] for d in feature_dates}
    if fit_set:
        train_row_idx = [
            i for i, d in enumerate(dates) if str(d)[:10] in fit_set
        ]
    else:
        train_row_idx = list(range(len(dates)))
    for key in raw_atc_finite(X, col_names, train_row_idx):
        if key not in seen:
            seen.add(key)
            feat_names.append(key)
    col_index = {str(n): i for i, n in enumerate(col_names)}
    cols = [col_index[n] for n in feat_names if n in col_index]
    feat_names = [n for n in feat_names if n in col_index]
    x_fit = (
        np.ascontiguousarray(X[:, cols])
        if cols
        else np.zeros((int(X.shape[0]), 0), dtype=np.float64)
    )
    codes_all = [
        str((metas[i] or {}).get("stock_code") or "") if i < len(metas) else ""
        for i in range(len(ys))
    ]
    return group_day_matrices(
        x_fit,
        np.asarray(ys, dtype=np.float64),
        feat_names,
        dates,
        codes_all,
        min_names=int(min_names),
    )


def build_tau_open_day_panels(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    fit_dates: Sequence[str] = (),
    min_history: int = 12,
    min_names: int = 8,
    head: str = "y_tc_rank",
    cache_head: str = "tc_rank",
    workers: Optional[int] = None,
    progress_cb: Optional[Any] = None,
    stats_out: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """开盘钟 τ 面板 → 日截面。``tc`` / ``tc_rank`` 同指纹，只换 L1 family / 日志 head。"""
    from core.research.panel_matrix import collect_tau_compact
    from core.yhat.keys import TAU_Z_FEATURES

    items = [it for it in (stock_bars or []) if isinstance(it, dict)]
    X, col_names, ys, dates, metas, n_stocks = collect_tau_compact(
        items,
        list(TAU_Z_FEATURES),
        min_history=int(min_history),
        tau_hm="open",
        include_alpha_tc=True,
        head=str(head or "y_tc_rank"),
        cache_head=str(cache_head or "tc_rank"),
        workers=workers,
        progress_cb=progress_cb,
        stats_out=stats_out,
    )
    if isinstance(stats_out, dict):
        stats_out.setdefault("n_stocks", int(n_stocks))
        stats_out.setdefault("n_rows", int(len(ys)))
    if len(ys) < 1:
        return []
    return tau_compact_to_day_panels(
        X,
        col_names,
        ys,
        dates,
        metas,
        feature_dates=fit_dates,
        min_names=min_names,
    )


def build_rank_day_panels(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    family: str,
    horizon_days: int = 1,
    fit_dates: Sequence[str] = (),
    min_history: int = 12,
    min_names: int = 8,
    workers: Optional[int] = None,
    progress_cb: Optional[Any] = None,
    stats_out: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    """统一入口：``family=oo_rank|tc_rank``（``tc`` 别名走同一开盘 τ 面板）。"""
    fam = str(family or "").strip().lower().replace("τ", "t").replace("tau", "tc")
    if fam in {"oo_rank", "y_oo_rank"}:
        from core.research.oo_rank_panel import build_oo_rank_day_panels

        return build_oo_rank_day_panels(
            stock_bars,
            horizon_days=int(horizon_days),
            min_history=int(min_history),
            min_names=int(min_names),
            workers=workers,
            progress_cb=progress_cb,
            **{
                k: v
                for k, v in kwargs.items()
                if k
                in {
                    "max_window",
                    "index_bars",
                    "excess_mode",
                    "respect_regime",
                    "config",
                }
            },
        )
    if fam in {"tc_rank", "y_tc_rank", "tc", "y_tc"}:
        head = "y_tc_rank" if "rank" in fam else "y_tc"
        cache_head = "tc_rank" if "rank" in fam else "tc"
        if "head" in kwargs:
            head = str(kwargs.pop("head") or head)
        if "cache_head" in kwargs:
            cache_head = str(kwargs.pop("cache_head") or cache_head)
        return build_tau_open_day_panels(
            stock_bars,
            fit_dates=fit_dates,
            min_history=int(min_history),
            min_names=int(min_names),
            head=head,
            cache_head=cache_head,
            workers=workers,
            progress_cb=progress_cb,
            stats_out=stats_out,
        )
    raise ValueError(
        f"unsupported rank panel family={family!r}；支持 oo_rank / tc_rank"
    )


__all__ = [
    "build_rank_day_panels",
    "build_tau_open_day_panels",
    "tau_compact_to_day_panels",
]
