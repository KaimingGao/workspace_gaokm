"""排序头日截面统一壳：``oo_rank`` / ``τc_rank`` 同构出口。

两边最终都是日包 ``{date, codes, ys, X, names[, clock]}``；差别只在特征族 / L1 family。

- ``oo_rank`` → ``build_oo_rank_day_panels``（可复用 ``oo/`` L1）
- ``τc_rank`` / ``τc`` → ``collect_tau_compact`` + 入模列裁剪
  （开盘钟 ``tc`` 与 ``tc_rank`` 指纹一致；非开盘前缀按 ``date|clock`` 成组）
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)


def _meta_tau_clock(meta: Any, *, default: str = "open") -> str:
    """行 meta → 训练钟；缺省 open。"""
    m = meta if isinstance(meta, dict) else {}
    raw = (
        m.get("tau")
        or m.get("tau_hm")
        or m.get("as_of_tau")
        or m.get("clock")
        or default
    )
    s = str(raw or default).strip()
    if not s or s.lower() in ("open", "0930"):
        return "open"
    if s == "09:30":
        return "open"
    if ":" not in s and len(s) == 4 and s.isdigit():
        return f"{s[:2]}:{s[2:]}"
    return s[:5] if len(s) >= 5 else s


def group_day_clock_matrices(
    X: np.ndarray,
    y: np.ndarray,
    names: Sequence[str],
    dates: Sequence[str],
    codes: Sequence[str],
    clocks: Sequence[str],
    *,
    min_names: int = 8,
) -> List[Dict[str, Any]]:
    """按 ``(date, clock)`` 切截面包；单钟 open 时与按日切等价。"""
    x = np.asarray(X, dtype=np.float64)
    yy = np.asarray(y, dtype=np.float64)
    n = int(x.shape[0])
    if n == 0 or yy.shape[0] != n or len(dates) != n:
        return []
    code_col = list(codes) if len(codes) == n else [""] * n
    clock_col = [
        _meta_tau_clock({"tau": clocks[i]} if i < len(clocks) else {}, default="open")
        if i < len(clocks)
        else "open"
        for i in range(n)
    ]
    # 稳定排序：日 → 钟 → 原序
    keys = np.asarray(
        [f"{str(dates[i])[:10]}|{clock_col[i]}" for i in range(n)], dtype=object
    )
    order = np.argsort(keys, kind="mergesort")
    x = np.ascontiguousarray(x[order])
    yy = np.ascontiguousarray(yy[order])
    date_col = [str(dates[int(i)])[:10] for i in order]
    code_col = [str(code_col[int(i)]) for i in order]
    clock_col = [clock_col[int(i)] for i in order]
    col_names = [str(nm) for nm in names]
    min_n = max(4, int(min_names or 8))
    out: List[Dict[str, Any]] = []
    i = 0
    while i < n:
        j = i + 1
        while (
            j < n
            and date_col[j] == date_col[i]
            and clock_col[j] == clock_col[i]
        ):
            j += 1
        if (j - i) >= min_n:
            sl = slice(i, j)
            pack: Dict[str, Any] = {
                "date": date_col[i],
                "codes": code_col[sl],
                "ys": yy[sl].tolist(),
                "X": x[sl],
                "names": col_names,
                "clock": clock_col[i],
            }
            out.append(pack)
        i = j
    return out


def tau_compact_to_day_panels(
    X: np.ndarray,
    col_names: Sequence[str],
    ys: Sequence[float],
    dates: Sequence[str],
    metas: Sequence[dict],
    *,
    feature_dates: Sequence[str] = (),
    min_names: int = 8,
    group_by_clock: bool = False,
) -> List[Dict[str, Any]]:
    """``collect_tau_compact`` 矩阵 → 排序/定档日截面（入模列 + 按日/钟切）。"""
    from core.research.oo_rank_panel import group_day_matrices
    from core.research.panel_matrix import raw_atc_finite
    from core.research.tc_model_compact import TAU_FIT_DROP_ALIASES, TAU_Z_FEATURES
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
    if not group_by_clock:
        days = group_day_matrices(
            x_fit,
            np.asarray(ys, dtype=np.float64),
            feat_names,
            dates,
            codes_all,
            min_names=int(min_names),
        )
        for d in days:
            d.setdefault("clock", "open")
        return days
    clocks = [_meta_tau_clock(metas[i] if i < len(metas) else {}) for i in range(len(ys))]
    return group_day_clock_matrices(
        x_fit,
        np.asarray(ys, dtype=np.float64),
        feat_names,
        dates,
        codes_all,
        clocks,
        min_names=int(min_names),
    )


def build_tau_day_panels(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    fit_dates: Sequence[str] = (),
    min_history: int = 12,
    min_names: int = 8,
    head: str = "y_tc_rank",
    cache_head: str = "tc_rank",
    tau_hm: str = "open",
    tau_grid: Optional[Sequence[str]] = None,
    workers: Optional[int] = None,
    progress_cb: Optional[Any] = None,
    stats_out: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """τ 面板 → 日/钟截面。``tau_hm=open`` 且无网格时与旧开盘路径一致。"""
    from core.research.panel_matrix import collect_tau_compact
    from core.yhat.keys import TAU_Z_FEATURES

    items = [it for it in (stock_bars or []) if isinstance(it, dict)]
    hm = str(tau_hm or "open").strip() or "open"
    grid = list(tau_grid) if tau_grid else None
    use_grid = bool(grid) and hm.lower() not in ("", "open")
    # 分钟钟无 grid：仍按该钟 collect，勿把 clock 默成 open
    effective_hm = hm if use_grid or hm.lower() not in ("", "open", "09:30", "0930") else "open"
    X, col_names, ys, dates, metas, n_stocks = collect_tau_compact(
        items,
        list(TAU_Z_FEATURES),
        min_history=int(min_history),
        tau_hm=effective_hm,
        tau_grid=grid if use_grid else None,
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
        stats_out["tau_hm"] = effective_hm
        stats_out["tau_grid"] = list(grid) if use_grid else None
    if len(ys) < 1:
        return []
    days = tau_compact_to_day_panels(
        X,
        col_names,
        ys,
        dates,
        metas,
        feature_dates=fit_dates,
        min_names=min_names,
        group_by_clock=bool(use_grid),
    )
    if not use_grid and effective_hm != "open":
        for d in days:
            d["clock"] = effective_hm
    return days


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
    """开盘钟 τ 面板 → 日截面（定档 / 兼容入口）。"""
    return build_tau_day_panels(
        stock_bars,
        fit_dates=fit_dates,
        min_history=int(min_history),
        min_names=int(min_names),
        head=head,
        cache_head=cache_head,
        tau_hm="open",
        tau_grid=None,
        workers=workers,
        progress_cb=progress_cb,
        stats_out=stats_out,
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
    """统一入口：``family=oo_rank|tc_rank``；τc_rank 可传 ``tau_hm`` / ``tau_grid``。"""
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
        tau_hm = str(kwargs.pop("tau_hm", "open") or "open")
        tau_grid = kwargs.pop("tau_grid", None)
        return build_tau_day_panels(
            stock_bars,
            fit_dates=fit_dates,
            min_history=int(min_history),
            min_names=int(min_names),
            head=head,
            cache_head=cache_head,
            tau_hm=tau_hm,
            tau_grid=tau_grid,
            workers=workers,
            progress_cb=progress_cb,
            stats_out=stats_out,
        )
    raise ValueError(
        f"unsupported rank panel family={family!r}；支持 oo_rank / tc_rank"
    )


__all__ = [
    "build_rank_day_panels",
    "build_tau_day_panels",
    "build_tau_open_day_panels",
    "group_day_clock_matrices",
    "tau_compact_to_day_panels",
]
