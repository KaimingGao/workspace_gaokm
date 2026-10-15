"""ŷ_oo_rank 截面面板：按决策日聚合观察池 (X, y_oo)。

标签与 ŷ_oo 同源：open[T+h]/open[T]−1（默认 h=1，百分点）。
特征与 ŷ_oo 同口径：原始 sub_score，按当天截面 z-score。
建面板走 ŷ_oo 的 compact 矩阵（逐只折进 float64，丢掉行 dict）；
默认 4 进程（与 ``OO_RIDGE_WORKERS`` / ``_resolve_workers`` 同口径）。
不进 y_oo_fuse；过入场后作买序；可选 oo_rank_max 入场闸。
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


def _oo_rank_stock_job(
    job: Tuple[Any, ...],
) -> Optional[Tuple[str, np.ndarray, np.ndarray, List[str], List[str]]]:
    """子进程：一只股票 → compact 矩阵块 + code。"""
    from core.research.oo_panel import collect_oo_panel
    from core.research.oo_ridge_compact import OoPanelMatrix
    from core.research.panel_disk_cache import (
        bars_fingerprint,
        load_stock_shard,
        make_fingerprint,
        oo_rank_head_key,
        save_stock_shard,
    )

    (
        code,
        bars,
        horizon_days,
        min_history,
        max_window,
        index_bars,
        fundamentals,
        excess_mode,
        respect_regime,
        config,
    ) = job
    code_s = str(code or "").strip()
    bars_l = list(bars or [])
    if not code_s or len(bars_l) < int(min_history) + int(horizon_days) + 1:
        return None
    horizon_i = int(horizon_days)
    mh = int(min_history)
    mw = int(max_window)
    excess = str(excess_mode or "none")
    regime = bool(respect_regime)
    head_key = oo_rank_head_key(
        horizon_days=horizon_i,
        min_history=mh,
        max_window=mw,
        excess_mode=excess,
        respect_regime=regime,
    )
    fp = make_fingerprint(
        bars_fp=bars_fingerprint(bars_l),
        horizon=horizon_i,
        min_history=mh,
        max_window=mw,
        excess_mode=excess,
        respect_regime=int(regime),
    )
    cached = load_stock_shard(head_key, code_s, fp)
    if cached is not None and cached.get("X") is not None:
        x = np.asarray(cached["X"], dtype=np.float64)
        y = np.asarray(cached["y"], dtype=np.float64)
        if int(x.shape[0]) <= 0:
            return None
        return (
            code_s,
            x,
            y,
            list(cached.get("names") or []),
            list(cached.get("dates") or []),
        )
    try:
        xs, ys, dates = collect_oo_panel(
            bars_l,
            horizon_days=horizon_i,
            min_history=mh,
            max_window=mw,
            index_bars=index_bars if isinstance(index_bars, list) else None,
            fundamentals=fundamentals,
            stock_code=code_s,
            pit_fundamentals=False,
            respect_regime=regime,
            config=config,
            excess_mode=excess,
        )
    except Exception:  # noqa: BLE001
        logger.debug("oo_rank panel collect failed for %s", code_s, exc_info=True)
        return None
    panel = OoPanelMatrix()
    panel.add_stock_rows(xs, ys, dates)
    x, y, names, dates_out = panel.finalize()
    if int(x.shape[0]) <= 0:
        return None
    save_stock_shard(
        head_key,
        code_s,
        fp,
        X=x,
        y=y,
        names=names,
        dates=dates_out,
    )
    return code_s, x, y, names, dates_out


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
    workers: Optional[int] = None,
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
    from concurrent.futures import ProcessPoolExecutor

    from core.research.oo_blas_limit import limit_blas_threads
    from core.research.oo_ridge_compact import (
        _POOL_BATCH,
        _resolve_workers,
        _stack_blocks,
    )

    jobs: List[Tuple[Any, ...]] = []
    for item in stock_bars or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or item.get("stock_code") or "").strip()
        bars = list(item.get("bars") or [])
        if not code or len(bars) < int(min_history) + int(horizon_days) + 1:
            continue
        idx = index_bars if index_bars is not None else item.get("index_bars")
        jobs.append(
            (
                code,
                bars,
                int(horizon_days),
                int(min_history),
                int(max_window),
                idx if isinstance(idx, list) else None,
                item.get("fundamentals"),
                str(excess_mode or "none"),
                bool(respect_regime),
                config,
            )
        )

    n_jobs = len(jobs)
    n_workers = _resolve_workers(n_jobs, workers)
    results: List[Any] = [None] * n_jobs

    def _run_serial(start: int, end: int) -> None:
        for i in range(start, end):
            try:
                results[i] = _oo_rank_stock_job(jobs[i])
            except Exception:  # noqa: BLE001
                logger.debug(
                    "oo_rank stock %s failed", jobs[i][0], exc_info=True
                )
                results[i] = None

    if n_jobs:
        logger.info(
            "oo_rank compact workers=%s stocks=%s batch=%s",
            n_workers,
            n_jobs,
            _POOL_BATCH,
        )
    start = 0
    while start < n_jobs:
        end = min(n_jobs, start + _POOL_BATCH)
        if n_workers <= 1 or (end - start) < 4:
            _run_serial(start, end)
        else:
            try:
                with ProcessPoolExecutor(
                    max_workers=n_workers, initializer=limit_blas_threads
                ) as pool:
                    futs = {
                        pool.submit(_oo_rank_stock_job, jobs[i]): i
                        for i in range(start, end)
                    }
                    for fut, i in futs.items():
                        try:
                            results[i] = fut.result()
                        except Exception:  # noqa: BLE001
                            logger.debug(
                                "oo_rank stock %s failed in worker",
                                jobs[i][0],
                                exc_info=True,
                            )
                            results[i] = None
            except Exception:
                logger.exception("oo_rank process pool unavailable, using serial")
                _run_serial(start, n_jobs)
                break
        start = end

    parts: List[Tuple[np.ndarray, np.ndarray, List[str], List[str]]] = []
    codes_all: List[str] = []
    n_stocks = 0
    for pack in results:
        if not pack:
            continue
        code, x, y, names, dates = pack
        parts.append((x, y, names, dates))
        codes_all.extend([code] * int(x.shape[0]))
        n_stocks += 1

    if not parts:
        X = np.zeros((0, 0), dtype=np.float64)
        y = np.zeros((0,), dtype=np.float64)
        names: List[str] = []
        dates: List[str] = []
    else:
        X, y, names, dates = _stack_blocks(parts)

    logger.info(
        "compact panel head=y_oo_rank rows=%s cols=%s stocks=%s workers=%s",
        int(y.shape[0]),
        len(names),
        n_stocks,
        n_workers,
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


def resolve_oo_rank_universe() -> Dict[str, Any]:
    """ŷ_oo_rank 代码名单：整观察池。截面 z 与损失用同一批票。

    此处不截断（调用方再按 watching_limit 截）。
    """
    from core.research_universe import resolve_model_fit_codes
    from core.watching.store import MODEL_FIT_MAX_SIZE

    resolved = resolve_model_fit_codes(watching_limit=MODEL_FIT_MAX_SIZE)
    pool_n = int(resolved.get("watching_pool_size") or 0)
    if not resolved.get("ok", True):
        return {
            "success": False,
            "error": resolved.get("error") or "oo_rank_universe_failed",
            "codes": [],
            "universe_source": resolved.get("source") or "watching",
            "watching_pool_size": pool_n,
        }
    codes = list(resolved.get("codes") or [])
    return {
        "success": True,
        "codes": codes,
        "universe_source": resolved.get("source") or "watching",
        "watching_pool_size": pool_n or len(codes),
    }


__all__ = [
    "build_oo_rank_day_panels",
    "group_day_matrices",
    "resolve_oo_rank_universe",
    "stack_day_panels",
]
