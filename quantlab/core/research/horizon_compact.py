"""ŷ_τ* 面板紧凑化：enrich 后折 float64，对齐 y_oo / collect_tau_compact。

双训 Ridge+Tree 共用一份 ``HorizonCompact``，避免两次 ``_stack_panels`` +
胖 dict 行常驻；日线 ``bars`` 在抽出日历后即可释放。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.research.panel_matrix import PanelMatrix

HorizonCompact = Dict[str, Any]


def fold_horizon_enriched(
    enriched: Sequence[Dict[str, Any]],
    z_features: Sequence[str],
    *,
    clear_panels: bool = True,
) -> HorizonCompact:
    """逐票折入 float64，可选清空面板 ``xs``（及票级 dict）以降峰值。

    返回 ``{X, names, ys, dates, metas, stock_count}``。
    ``X`` 列序 = ``z_features``；缺测为 NaN。
    """
    names = [str(n) for n in z_features if str(n)]
    block = PanelMatrix(names, widen_prefix="")
    ys_all: List[float] = []
    dates_all: List[str] = []
    metas_all: List[dict] = []
    n_stocks = 0

    for p in enriched or []:
        if not isinstance(p, dict):
            continue
        xs = p.get("xs") or []
        ys = p.get("ys") or []
        dates = p.get("dates") or []
        metas = p.get("metas") or []
        n = min(len(xs), len(ys), len(dates), len(metas))
        if n <= 0:
            if clear_panels:
                p.clear()
            continue
        rows = xs[:n]
        block.add_rows(rows)
        for i in range(n):
            try:
                yf = float(ys[i])
            except (TypeError, ValueError):
                yf = float("nan")
            ys_all.append(yf)
            dates_all.append(str(dates[i] or "")[:10])
            meta = metas[i]
            metas_all.append(meta if isinstance(meta, dict) else {})
        n_stocks += 1
        if clear_panels:
            p.clear()

    X = block.finalize()
    # 与 ys 对齐：add_rows 可能因空行少写；以较短者为准
    n_y = len(ys_all)
    if int(X.shape[0]) != n_y:
        n_keep = min(int(X.shape[0]), n_y)
        X = X[:n_keep]
        ys_all = ys_all[:n_keep]
        dates_all = dates_all[:n_keep]
        metas_all = metas_all[:n_keep]

    if clear_panels and isinstance(enriched, list):
        enriched.clear()

    return {
        "X": np.ascontiguousarray(X, dtype=np.float64),
        "names": list(names),
        "ys": ys_all,
        "dates": dates_all,
        "metas": metas_all,
        "stock_count": int(n_stocks),
    }


def compact_rows(
    compact: HorizonCompact,
    *,
    idxs: Optional[Sequence[int]] = None,
    names: Optional[Sequence[str]] = None,
) -> List[dict]:
    """从紧凑矩阵物化薄 dict 行（仅入模列），供仍吃 panel 的 Ridge API。"""
    X = np.asarray(compact.get("X"), dtype=np.float64)
    col_names = [str(n) for n in (names if names is not None else (compact.get("names") or []))]
    if X.ndim != 2 or not col_names:
        return []
    name_index = {str(n): i for i, n in enumerate(compact.get("names") or [])}
    cols: List[int] = []
    use_names: List[str] = []
    for n in col_names:
        j = name_index.get(str(n))
        if j is None:
            continue
        cols.append(j)
        use_names.append(str(n))
    if not use_names:
        return []
    if idxs is None:
        row_ids = range(int(X.shape[0]))
    else:
        row_ids = idxs
    out: List[dict] = []
    for i in row_ids:
        ii = int(i)
        if ii < 0 or ii >= X.shape[0]:
            out.append({})
            continue
        row = X[ii]
        d: Dict[str, Any] = {}
        for name, j in zip(use_names, cols):
            v = float(row[j])
            if math.isfinite(v):
                d[name] = v
        out.append(d)
    return out


def compact_subset(
    compact: HorizonCompact,
    idxs: Sequence[int],
    *,
    names: Optional[Sequence[str]] = None,
) -> Tuple[List[dict], List[float], List[dict], List[str]]:
    """Holdout 子集：(xs, ys, metas, dates)。"""
    ys = list(compact.get("ys") or [])
    metas = list(compact.get("metas") or [])
    dates = list(compact.get("dates") or [])
    xs = compact_rows(compact, idxs=idxs, names=names)
    ys_o = [ys[i] for i in idxs]
    metas_o = [metas[i] if i < len(metas) else {} for i in idxs]
    dates_o = [dates[i] if i < len(dates) else "" for i in idxs]
    return xs, ys_o, metas_o, dates_o


def cs_groups_from_dates_metas(
    dates: Sequence[str],
    metas: Sequence[dict],
) -> List[str]:
    """拟合 CS-z 分组：``date|τ``（PIT）；开盘/缺钟 → 仅日期。"""
    from core.research.feature_standardize import cs_group_key

    out: List[str] = []
    for i, d in enumerate(dates or []):
        meta = metas[i] if i < len(metas) and isinstance(metas[i], dict) else {}
        tau = str(meta.get("tau") or meta.get("tau_hm") or "").strip()
        out.append(cs_group_key(d, tau))
    return out


def feature_fill_rates_matrix(
    X: np.ndarray,
    names: Sequence[str],
) -> Dict[str, Any]:
    """矩阵版特征非空率（对齐 ``oo_panel.feature_fill_rates``）。"""
    arr = np.asarray(X, dtype=np.float64)
    n = int(arr.shape[0]) if arr.ndim == 2 else 0
    out: Dict[str, Any] = {"n": n, "keys": {}}
    for j, k in enumerate(names):
        if n <= 0 or j >= int(arr.shape[1]):
            out["keys"][str(k)] = {"filled": 0, "rate": None}
            continue
        filled = int(np.sum(np.isfinite(arr[:, j])))
        out["keys"][str(k)] = {
            "filled": filled,
            "rate": round(filled / float(n), 4) if n else None,
        }
    return out


def release_stock_bar_payloads(stock_bars: Sequence[Any]) -> None:
    """抽出日历后丢掉日线 ``bars`` / 分钟线，只留 ``code`` 等瘦字段。"""
    for item in stock_bars or []:
        if not isinstance(item, dict):
            continue
        item.pop("bars", None)
        item.pop("minute_bars", None)


def take_calendar_and_release(
    stock_bars: Sequence[Any],
) -> List[str]:
    """``calendar_dates_from_stock_bars`` 后立刻 ``release_stock_bar_payloads``。"""
    from core.research.holdout import calendar_dates_from_stock_bars

    calendar = calendar_dates_from_stock_bars(stock_bars)
    release_stock_bar_payloads(stock_bars)
    return calendar
