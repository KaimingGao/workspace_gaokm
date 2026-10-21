"""ŷ_oo_rank 截面面板：按决策日聚合观察池 (X, y_oo)。

标签与 ŷ_oo 同源：open[T+h]/open[T]−1（默认 h=1，百分点）。
特征与 ŷ_oo 同口径：原始 sub_score，按当天截面 z-score。
建面板走 ŷ_oo 的 compact 矩阵（逐只折进 float64，丢掉行 dict）；
默认 4 进程（与 ``OO_RIDGE_WORKERS`` / ``_resolve_workers`` 同口径）。
默认口径（绝对收益、全注册因子、窗口不超过 alpha_oo 抬窗）直接读 L1 ``oo/``，
不重算。超额收益或无法复用 oo 指纹时才单独 collect。
不进 y_oo_fuse；过入场后作买序；可选 oo_rank_max 入场闸。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.research.oos_finite import as_float

logger = logging.getLogger(__name__)


def label_float(value: Any) -> float:
    """标签转 float。缺失、非数字、Inf 记 NaN。"""
    return as_float(value)


def label_floats(values: Optional[Sequence[Any]]) -> List[float]:
    return [as_float(v) for v in (values or [])]


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


# 与 collect_oo_panel 的默认入参一致（抬窗前）。


def _normalize_excess_mode(excess_mode: str) -> str:
    """与 ``collect_oo_panel`` 的 excess 归一相同。"""
    em = str(excess_mode or "none").strip().lower()
    if em in ("index", "excess", "vs_index", "benchmark"):
        return "index"
    return "none"


def _effective_oo_collect_window(
    min_history: int,
    max_window: int,
    *,
    respect_regime: bool,
    index_bars: Optional[List[dict]],
    config: Optional[dict],
) -> Tuple[int, int]:
    """``collect_oo_panel`` 钳制并抬窗之后实际使用的窗口。"""
    from core.research.oo_panel import _resolve_factor_names
    from core.signal.factors.alpha_oo import bump_window_for_alpha_oo

    mh = max(5, int(min_history or 12))
    names = _resolve_factor_names(
        respect_regime=bool(respect_regime),
        index_bars=index_bars if isinstance(index_bars, list) else None,
        config=config,
    )
    return bump_window_for_alpha_oo(
        names, min_history=mh, max_window=int(max_window)
    )


def _oo_rank_panel_fingerprint(
    bars: Sequence[dict],
    *,
    horizon_days: int,
    min_history: int,
    max_window: int,
    excess_mode: str,
    respect_regime: bool,
    index_bars: Optional[List[dict]],
    config: Optional[dict],
) -> Tuple[str, int, int, str]:
    """L1 键用抬窗后的窗口。不超过抬窗档的入参共用同一哈希。"""
    from core.research.panel_fingerprint import bars_fingerprint, make_fingerprint

    eff_mh, eff_mw = _effective_oo_collect_window(
        min_history,
        max_window,
        respect_regime=respect_regime,
        index_bars=index_bars,
        config=config,
    )
    excess = _normalize_excess_mode(excess_mode)
    fp = make_fingerprint(
        bars_fp=bars_fingerprint(bars),
        horizon=int(horizon_days),
        min_history=int(eff_mh),
        max_window=int(eff_mw),
        excess_mode=excess,
        respect_regime=int(bool(respect_regime)),
    )
    return fp, int(eff_mh), int(eff_mw), excess


def _can_adopt_oo_panel(
    *,
    eff_mh: int,
    eff_mw: int,
    excess_mode: str,
    respect_regime: bool,
    index_bars: Optional[List[dict]],
    fundamentals: Any,
) -> bool:
    """矩阵与 ŷ_oo 默认 L1 相同：绝对收益、不用指数/财务、窗口同档。"""
    if bool(respect_regime) or excess_mode != "none":
        return False
    if isinstance(index_bars, list) and index_bars:
        return False
    if fundamentals is not None:
        return False
    from core.research.oo_panel import OO_PANEL_MAX_WINDOW, OO_PANEL_MIN_HISTORY

    oo_mh, oo_mw = _effective_oo_collect_window(
        OO_PANEL_MIN_HISTORY,
        OO_PANEL_MAX_WINDOW,
        respect_regime=False,
        index_bars=None,
        config=None,
    )
    return (int(eff_mh), int(eff_mw)) == (int(oo_mh), int(oo_mw))


def _load_oo_panel_for_rank(
    code: str,
    bars: Sequence[dict],
    horizon_days: int,
):
    """按 ŷ_oo 的指纹读 L1 ``oo/``。``ridge_core_only=0`` 与默认 assemble 一致。"""
    from core.research.lake_bridge import FAMILY_OO, load_training_matrix
    from core.research.panel_fingerprint import bars_fingerprint, make_fingerprint

    oo_fp = make_fingerprint(
        bars_fp=bars_fingerprint(bars),
        horizon=int(horizon_days),
        ridge_core_only=0,
    )
    return load_training_matrix(FAMILY_OO, code, oo_fp)


def _sync_oo_rank_lake(
    code: str,
    bars: Sequence[dict],
    *,
    x: np.ndarray,
    y: np.ndarray,
    names: Sequence[str],
    dates: Sequence[str],
    fingerprint: str,
) -> None:
    from core.research.incremental_panel import stem_extra_meta
    from core.research.lake_bridge import FAMILY_OO_RANK, sync_panel_to_lake

    sync_panel_to_lake(
        FAMILY_OO_RANK,
        code,
        X=x,
        dates=dates,
        names=names,
        fingerprint=fingerprint,
        y=y,
        extra_meta=stem_extra_meta(bars),
    )


def _oo_rank_stock_job(
    job: Tuple[Any, ...],
) -> Optional[Tuple[str, np.ndarray, np.ndarray, List[str], List[str]]]:
    """子进程：一只股票 → compact 矩阵块 + code。"""
    from core.research.oo_panel import collect_oo_panel
    from core.research.oo_ridge_compact import OoPanelMatrix

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
    idx = index_bars if isinstance(index_bars, list) else None
    regime = bool(respect_regime)
    fp, eff_mh, eff_mw, excess = _oo_rank_panel_fingerprint(
        bars_l,
        horizon_days=horizon_i,
        min_history=mh,
        max_window=mw,
        excess_mode=str(excess_mode or "none"),
        respect_regime=regime,
        index_bars=idx,
        config=config,
    )
    try:
        from core.research.lake_bridge import FAMILY_OO_RANK, load_training_matrix

        hit = load_training_matrix(FAMILY_OO_RANK, code_s, fp)
        if hit is not None:
            x, y, names, dates_out, _src = hit
            if int(x.shape[0]) > 0:
                return code_s, x, y, names, dates_out
    except Exception:  # noqa: BLE001
        logger.debug("oo_rank training cache load failed for %s", code_s, exc_info=True)
    if _can_adopt_oo_panel(
        eff_mh=eff_mh,
        eff_mw=eff_mw,
        excess_mode=excess,
        respect_regime=regime,
        index_bars=idx,
        fundamentals=fundamentals,
    ):
        try:
            adopted = _load_oo_panel_for_rank(code_s, bars_l, horizon_i)
        except Exception:  # noqa: BLE001
            logger.debug("oo_rank adopt oo failed for %s", code_s, exc_info=True)
            adopted = None
        if adopted is not None:
            x, y, names, dates_out, _src = adopted
            if int(getattr(x, "shape", (0,))[0] or 0) > 0:
                try:
                    _sync_oo_rank_lake(
                        code_s,
                        bars_l,
                        x=x,
                        y=y,
                        names=names,
                        dates=dates_out,
                        fingerprint=fp,
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("oo_rank adopt sync failed for %s", code_s, exc_info=True)
                logger.debug(
                    "oo_rank adopt oo code=%s rows=%s", code_s, int(x.shape[0])
                )
                return code_s, x, y, names, dates_out

    def _rank_spec(bars_fp: str) -> str:
        from core.research.panel_fingerprint import make_fingerprint

        return make_fingerprint(
            bars_fp=bars_fp,
            horizon=horizon_i,
            min_history=int(eff_mh),
            max_window=int(eff_mw),
            excess_mode=excess,
            respect_regime=int(regime),
        )

    try:
        from core.research.incremental_panel import try_slice_shorter_lookback
        from core.research.lake_bridge import FAMILY_OO_RANK

        want_names = OoPanelMatrix().names
        sliced = try_slice_shorter_lookback(
            code_s,
            bars_l,
            family=FAMILY_OO_RANK,
            spec_fp=_rank_spec,
            names=want_names,
        )
        if sliced is None and _can_adopt_oo_panel(
            eff_mh=eff_mh,
            eff_mw=eff_mw,
            excess_mode=excess,
            respect_regime=regime,
            index_bars=idx,
            fundamentals=fundamentals,
        ):
            from core.research.lake_bridge import FAMILY_OO
            from core.research.panel_fingerprint import make_fingerprint

            def _oo_spec(bars_fp: str) -> str:
                return make_fingerprint(
                    bars_fp=bars_fp,
                    horizon=horizon_i,
                    ridge_core_only=0,
                )

            sliced = try_slice_shorter_lookback(
                code_s,
                bars_l,
                family=FAMILY_OO,
                spec_fp=_oo_spec,
                names=want_names,
            )
        if sliced is not None:
            x, y, names, dates_out = sliced
            if int(getattr(x, "shape", (0,))[0] or 0) > 0:
                return code_s, x, y, names, dates_out
    except Exception:  # noqa: BLE001
        logger.debug("oo_rank lookback slice failed for %s", code_s, exc_info=True)

    from core.research.materialize.consumer import MaterializeRequired, raise_if_consumer_rebuild

    raise_if_consumer_rebuild("oo_rank", code_s)
    x = y = None
    names: List[str] = []
    dates_out: List[str] = []
    try:
        from core.research.incremental_panel import try_incremental_merge
        from core.research.lake_bridge import FAMILY_OO_RANK
        from core.signal.factors.alpha_oo import ALPHA_OO_PANEL_WINDOW

        def _rank_tail(keep_dates):
            xs_t, ys_t, dates_t = collect_oo_panel(
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
                keep_dates=keep_dates,
            )
            panel_t = OoPanelMatrix()
            panel_t.add_stock_rows(xs_t, ys_t, dates_t)
            return panel_t.finalize()

        merged = try_incremental_merge(
            code_s,
            bars_l,
            pad=int(ALPHA_OO_PANEL_WINDOW) + max(5, horizon_i),
            collect_tail=_rank_tail,
            family=FAMILY_OO_RANK,
            spec_fp=_rank_spec,
        )
        if merged is not None:
            x, y, names, dates_out = merged
    except MaterializeRequired:
        raise
    except Exception:  # noqa: BLE001
        logger.debug("oo_rank incremental failed for %s", code_s, exc_info=True)
        x = None
    if x is None:
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
        except MaterializeRequired:
            raise
        except Exception:
            logger.exception("oo_rank panel collect failed for %s", code_s)
            raise
        panel = OoPanelMatrix()
        panel.add_stock_rows(xs, ys, dates)
        x, y, names, dates_out = panel.finalize()
    if int(x.shape[0]) <= 0:
        return None
    try:
        _sync_oo_rank_lake(
            code_s,
            bars_l,
            x=x,
            y=y,
            names=names,
            dates=dates_out,
            fingerprint=fp,
        )
    except Exception:  # noqa: BLE001
        logger.debug("oo_rank L1 sync failed for %s", code_s, exc_info=True)
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
    progress_cb: Optional[Any] = None,
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

    from core.research.materialize.consumer import context_initargs, research_pool_init
    from core.research.materialize.pool import consume_process_batch
    from core.research.oo_ridge_compact import (
        _POOL_BATCH,
        _resolve_workers,
        _stack_blocks,
    )

    from core.research.materialize.coverage import enforce_consumer_coverage
    from core.signal.factors.alpha_oo import ALPHA_OO_PANEL_WINDOW

    enforce_consumer_coverage(
        list(stock_bars or []),
        families=["oo_rank"],
        clocks=["open"],
        min_history=int(ALPHA_OO_PANEL_WINDOW),
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

    done_n = 0

    def _tick() -> None:
        nonlocal done_n
        done_n += 1
        if not callable(progress_cb):
            return
        if done_n != n_jobs and done_n != 1 and done_n % 10 != 0:
            return
        try:
            progress_cb(done_n, max(1, n_jobs), f"L1 oo_rank {done_n}/{n_jobs}")
        except Exception:  # noqa: BLE001
            logger.debug("oo_rank progress_cb failed", exc_info=True)

    def _run_serial(start: int, end: int) -> None:
        for i in range(start, end):
            results[i] = _oo_rank_stock_job(jobs[i])
            _tick()

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
                pool = ProcessPoolExecutor(
                    max_workers=n_workers,
                    initializer=research_pool_init,
                    initargs=context_initargs(),
                )
            except Exception:
                logger.exception("oo_rank process pool unavailable, using serial")
                _run_serial(start, n_jobs)
                break
            try:
                broke = consume_process_batch(
                    start=start,
                    end=end,
                    n_workers=n_workers,
                    results=results,
                    submit=lambda i, pool=pool: pool.submit(_oo_rank_stock_job, jobs[i]),
                    run_one=lambda i: _oo_rank_stock_job(jobs[i]),
                    label=lambda i: str(jobs[i][0]),
                    on_done=lambda _i: _tick(),
                )
            finally:
                try:
                    pool.shutdown(wait=True, cancel_futures=True)
                except Exception:
                    logger.exception("oo_rank pool shutdown failed")
            if broke:
                _run_serial(end, n_jobs)
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
        day_ys = label_floats(day.get("ys"))
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


def resolve_oo_rank_universe(
    *,
    zscore_universe: str = "pool",
) -> Dict[str, Any]:
    """ŷ_oo_rank / ŷ_τc_rank 代码名单。

    ``zscore_universe=pool``：整观察池（默认）。
    ``zscore_universe=tier_a``：观察池 ∩ 枢纽 A 档；截面 z / 损失 / 名次同一批票。
    此处不截断（调用方再按 watching_limit 截）。
    """
    import hashlib

    from core.research.feature_standardize import (
        ZSCORE_UNIVERSE_TIER_A,
        normalize_zscore_universe,
    )
    from core.research_universe import resolve_model_fit_codes
    from core.watching.store import MODEL_FIT_MAX_SIZE

    uni = normalize_zscore_universe(zscore_universe)
    resolved = resolve_model_fit_codes(watching_limit=MODEL_FIT_MAX_SIZE)
    pool_n = int(resolved.get("watching_pool_size") or 0)
    if not resolved.get("ok", True):
        return {
            "success": False,
            "error": resolved.get("error") or "oo_rank_universe_failed",
            "codes": [],
            "universe_source": resolved.get("source") or "watching",
            "watching_pool_size": pool_n,
            "zscore_universe": uni,
        }
    codes = [str(c).strip() for c in (resolved.get("codes") or []) if str(c or "").strip()]
    out: Dict[str, Any] = {
        "success": True,
        "codes": codes,
        "universe_source": resolved.get("source") or "watching",
        "watching_pool_size": pool_n or len(codes),
        "zscore_universe": uni,
    }
    if uni != ZSCORE_UNIVERSE_TIER_A:
        return out

    from core.research.predictability_tiers import (
        load_predictability_tiers_last,
        tier_code_set,
    )

    tier_rep = load_predictability_tiers_last()
    if not isinstance(tier_rep, dict) or not tier_rep.get("success"):
        return {
            "success": False,
            "error": "无枢纽分档报告；勾选「宇宙 A 档」前请先跑「观察池分档」",
            "codes": [],
            "universe_source": "watching∩tier_a",
            "watching_pool_size": pool_n,
            "zscore_universe": ZSCORE_UNIVERSE_TIER_A,
        }
    keep = tier_code_set(tier_rep, ["A"])
    filtered = [c for c in codes if c in keep]
    fp = hashlib.sha1(",".join(sorted(keep)).encode("utf-8")).hexdigest()[:12]
    out.update(
        {
            "codes": filtered,
            "universe_source": "watching∩tier_a",
            "zscore_universe": ZSCORE_UNIVERSE_TIER_A,
            "tier_codes": sorted(keep),
            "n_tier_codes": len(keep),
            "n_codes": len(filtered),
            "tier_fingerprint": fp,
            "tier_as_of_last": tier_rep.get("as_of_tier_last"),
            "holdout_n": tier_rep.get("holdout_n"),
            "tier_n": tier_rep.get("tier_n"),
            "tier_counts": tier_rep.get("counts"),
        }
    )
    if len(filtered) < 8:
        out["success"] = False
        out["error"] = (
            f"A 档与观察池交集不足 8 只（当前 {len(filtered)}；"
            f"枢纽 A={(tier_rep.get('counts') or {}).get('A', 0)}）"
        )
    return out


__all__ = [
    "build_oo_rank_day_panels",
    "group_day_matrices",
    "label_float",
    "label_floats",
    "resolve_oo_rank_universe",
    "stack_day_panels",
]
