"""ŷ_oo_rank 截面面板：按决策日聚合观察池 (X, y_oo)。

标签与 ŷ_oo 同源：open[T+h]/open[T]−1（默认 h=1，百分点）。
特征与 ŷ_oo 同口径：原始 sub_score（fit_lambdarank 内部做样本内全局 z-score）。
建面板走 ŷ_oo 的 compact 矩阵（逐只折进 float64，丢掉行 dict）；旁路对照，不进 ranking / 买序。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def _day_matrix(day: Dict[str, Any]) -> Optional[Tuple[np.ndarray, List[str]]]:
    X = day.get("X")
    names = day.get("names")
    if not isinstance(X, np.ndarray) or X.ndim != 2 or not names:
        return None
    return np.asarray(X, dtype=np.float64), [str(n) for n in names]


def group_day_matrices(
    X: np.ndarray,
    y: np.ndarray,
    names: Sequence[str],
    dates: Sequence[str],
    codes: Sequence[str],
    *,
    min_names: int = 8,
) -> List[Dict[str, Any]]:
    """按日切 contiguous 视图；调用方须保持 ``X`` 存活。"""
    x = np.asarray(X, dtype=np.float64)
    yy = np.asarray(y, dtype=np.float64)
    n = int(x.shape[0])
    if n == 0 or yy.shape[0] != n or len(dates) != n:
        return []
    code_col = list(codes) if len(codes) == n else [""] * n
    order = np.argsort(np.asarray([str(d)[:10] for d in dates], dtype=object), kind="mergesort")
    x = np.ascontiguousarray(x[order])
    yy = np.ascontiguousarray(yy[order])
    date_col = [str(dates[int(i)])[:10] for i in order]
    code_col = [str(code_col[int(i)]) for i in order]
    col_names = [str(n) for n in names]
    min_n = max(4, int(min_names or 8))
    out: List[Dict[str, Any]] = []
    i = 0
    while i < n:
        j = i + 1
        while j < n and date_col[j] == date_col[i]:
            j += 1
        if (j - i) >= min_n:
            sl = slice(i, j)
            out.append(
                {
                    "date": date_col[i],
                    "codes": code_col[sl],
                    "ys": yy[sl].tolist(),
                    "X": x[sl],
                    "names": col_names,
                }
            )
        i = j
    return out


def build_oo_rank_day_panels(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    horizon_days: int = 1,
    min_history: int = 12,
    min_names: int = 8,
    max_window: int = 30,
    index_bars: Optional[List[dict]] = None,
    excess_mode: str = "none",
    respect_regime: bool = False,
    config: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    """返回按日截面（矩阵 ``X``，无整池因子 dict）。

    每项::
        {
          "date": "YYYY-MM-DD",
          "codes": [str, ...],
          "ys": [float, ...],
          "X": ndarray[n, p],
          "names": [str, ...],
        }
    """
    from core.research.oo_ridge_compact import OoPanelMatrix
    from core.research.panel import collect_subscore_forward_panel

    panel = OoPanelMatrix()
    codes_all: List[str] = []
    n_stocks = 0
    for item in stock_bars or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or item.get("stock_code") or "").strip()
        bars = list(item.get("bars") or [])
        if not code or len(bars) < int(min_history) + int(horizon_days) + 1:
            continue
        idx = index_bars if index_bars is not None else item.get("index_bars")
        try:
            xs, ys, dates = collect_subscore_forward_panel(
                bars,
                horizon_days=int(horizon_days),
                min_history=int(min_history),
                max_window=int(max_window),
                index_bars=idx if isinstance(idx, list) else None,
                fundamentals=item.get("fundamentals"),
                stock_code=code,
                pit_fundamentals=False,
                respect_regime=bool(respect_regime),
                config=config,
                excess_mode=str(excess_mode or "none"),
            )
        except Exception:  # noqa: BLE001
            logger.debug("oo_rank panel collect failed for %s", code, exc_info=True)
            continue
        n0 = len(panel.dates)
        panel.add_stock_rows(xs, ys, dates)
        n1 = len(panel.dates)
        codes_all.extend([code] * max(0, n1 - n0))
        del xs, ys, dates
        n_stocks += 1
    X, y, names, dates = panel.finalize()
    logger.info(
        "compact panel head=y_oo_rank rows=%s cols=%s stocks=%s",
        int(y.shape[0]),
        len(names),
        n_stocks,
    )
    return group_day_matrices(
        X,
        y,
        names,
        dates,
        codes_all,
        min_names=min_names,
    )


def stack_day_panels(
    days: Sequence[Dict[str, Any]],
) -> Tuple[List[dict], List[float], List[str], List[str]]:
    """展平为 pointwise 行（供脚本 Ridge）。矩阵日还原瘦 dict。"""
    xs: List[dict] = []
    ys: List[float] = []
    dates: List[str] = []
    codes: List[str] = []
    for day in days or []:
        d = str(day.get("date") or "")[:10]
        packed = _day_matrix(day)
        day_codes = list(day.get("codes") or [])
        day_ys = [float(v) for v in (day.get("ys") or [])]
        if packed is not None:
            X, names = packed
            n = int(X.shape[0])
            for i in range(n):
                row: Dict[str, float] = {}
                for j, name in enumerate(names):
                    v = X[i, j]
                    if np.isfinite(v):
                        row[name] = float(v)
                xs.append(row)
                ys.append(float(day_ys[i]) if i < len(day_ys) else float("nan"))
                dates.append(d)
                codes.append(str(day_codes[i]) if i < len(day_codes) else "")
    return xs, ys, dates, codes


def resolve_oo_rank_universe(*, watching_tier_a_only: bool = False) -> Dict[str, Any]:
    """ŷ_oo_rank 训练宇宙：默认整观察池；开启则只留分档 A 且仍在观察池内。"""
    from core.watching.store import read_watching

    try:
        uni = read_watching()
    except Exception as exc:  # noqa: BLE001
        return {
            "success": False,
            "error": f"读观察池失败：{exc}",
            "codes": [],
            "universe_source": "watching",
        }
    watch = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
    if not watching_tier_a_only:
        return {
            "success": True,
            "codes": watch,
            "universe_source": "watching",
            "watching_pool_size": len(watch),
            "watching_tier_a_only": False,
        }
    from core.research.predictability_tiers import (
        load_predictability_tiers_last,
        tier_code_set,
    )

    last = load_predictability_tiers_last()
    if not isinstance(last, dict) or not last.get("success"):
        return {
            "success": False,
            "error": "无观察池分档报告，无法只训 A 档（请先跑可预测性分档）",
            "codes": [],
            "universe_source": "watching_tier_a",
            "watching_pool_size": len(watch),
            "watching_tier_a_only": True,
        }
    a_set = tier_code_set(last, ("A",))
    codes = [c for c in watch if c in a_set]
    return {
        "success": True,
        "codes": codes,
        "universe_source": "watching_tier_a",
        "watching_pool_size": len(watch),
        "n_tier_a": len(codes),
        "watching_tier_a_only": True,
    }


__all__ = [
    "build_oo_rank_day_panels",
    "group_day_matrices",
    "resolve_oo_rank_universe",
    "stack_day_panels",
]
