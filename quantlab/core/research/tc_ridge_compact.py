"""ŷ_τc Ridge：面板拼装与矩阵拟合（对齐 ``oo_ridge_compact``）。

报告包装 / Holdout OOS / 落盘启用见 ``tc_ridge``。
底层 ``collect_tau_compact`` / ``fit_keepall_ridge_matrix`` 在 ``panel_matrix``。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

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
    feature_get as _z_tc_feature_get,
)
from core.yhat.keys import TAU_Z_FEATURES

logger = logging.getLogger(__name__)
ProgressCb = Optional[Callable[[int, int, str], None]]
# 与 panel_matrix._TAU_POOL_BATCH 对齐；对外避免 import 私有常量
TAU_PANEL_POOL_BATCH = 24


def feature_get(row: Optional[Dict[str, Any]], name: str) -> Any:
    """ŷ_τc 用 z_τc_*；未命中再走 z_th（裸名 ↔ z_τh_* ↔ z_τc_*）。

    覆盖：新模 ``z_τh_*`` 系数读裸名；旧模裸名/``z_τc_*`` 系数读仅有 ``z_τh_*`` 的行。
    """
    v = _z_tc_feature_get(row, name)
    if v is not None and v != "":
        return v
    from core.signal.factors.z_th import feature_get as _z_th_feature_get

    return _z_th_feature_get(row, name)

# open_gap ≡ gap_pct，只拟合其一，避免 Ridge 双计
TAU_FIT_DROP_ALIASES = Z_TC_FIT_DROP_ALIASES
# ŷ_τ*（Ridge/Tree 同 X）不吃 OC 路径形状：HL/回撤/振幅在 30–90m 前瞻上共线对冲，
# 易把 ŷ 压到训练均值。面板仍可算这些键，入模时剔除。
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


def _matrix_rows_to_dicts(
    X: np.ndarray,
    names: Sequence[str],
) -> List[dict]:
    out: List[dict] = []
    arr = np.asarray(X, dtype=np.float64)
    if arr.ndim != 2:
        return out
    for i in range(int(arr.shape[0])):
        row: Dict[str, Any] = {}
        for j, name in enumerate(names):
            if j >= arr.shape[1]:
                break
            v = float(arr[i, j])
            if v == v:  # finite
                row[str(name)] = v
        out.append(row)
    return out


def _try_stitch_l1_tau_panel(
    code: str,
    bars: Sequence[dict],
    minute_bars: Sequence[dict],
    clocks: Sequence[str],
    *,
    min_history: int,
    include_alpha_tc: bool,
    include_alpha_th: bool,
) -> Optional[Dict[str, Any]]:
    """H3：按钟拼 L1 ``tc_HHMM`` 特征行（指纹与 materialize_l1_tau_grid 对齐）。

    命中时保留 ``minute_bars`` 供 relabel 补 ``price_tau`` / 窗标签。
    """
    if not code or not clocks or len(bars) < int(min_history) + 2:
        return None
    try:
        from core.research.lake_bridge import family_for_tc, load_training_matrix
        from core.research.panel_fingerprint import bars_fingerprint, make_fingerprint
        from core.research.panel_matrix import _tc_matrix_seed_names
    except Exception:  # noqa: BLE001
        return None

    seed = list(TAU_Z_FEATURES)
    if include_alpha_th:
        try:
            from core.research.materialize.horizon_names import horizon_l1_feature_names

            seed = horizon_l1_feature_names()
        except Exception:  # noqa: BLE001
            pass
    col_names = _tc_matrix_seed_names(
        seed,
        include_alpha_tc=bool(include_alpha_tc),
        include_alpha_th=bool(include_alpha_th),
    )
    fp_bars = bars_fingerprint(bars, minute_bars=minute_bars or None)
    xs_all: List[dict] = []
    ys_all: List[float] = []
    dates_all: List[str] = []
    metas_all: List[dict] = []
    for hm in clocks:
        clk = str(hm or "").strip()
        if not clk or clk.lower() in ("open", "09:30", "0930"):
            continue
        fp = make_fingerprint(
            bars_fp=fp_bars,
            tau_hm=clk,
            min_history=int(min_history),
            include_alpha_tc=int(bool(include_alpha_tc)),
            include_alpha_th=int(bool(include_alpha_th)),
            use_minute=1,
        )
        fam = family_for_tc(tau_hm=clk)
        try:
            hit = load_training_matrix(
                fam, code, fp, names=col_names, require_y=False
            )
        except Exception:  # noqa: BLE001
            return None
        if hit is None:
            return None
        X, y_arr, got_names, dates_l, _src = hit
        if X is None or len(dates_l or []) < 2:
            return None
        names = [str(n) for n in (got_names or col_names)]
        rows = _matrix_rows_to_dicts(X, names)
        n = min(len(rows), len(dates_l))
        if n < 2:
            return None
        y_list = []
        if y_arr is not None:
            try:
                y_list = [float(v) for v in np.asarray(y_arr).reshape(-1).tolist()]
            except Exception:  # noqa: BLE001
                y_list = []
        for i in range(n):
            day = str(dates_l[i] or "")[:10]
            xs_all.append(rows[i])
            ys_all.append(float(y_list[i]) if i < len(y_list) else float("nan"))
            dates_all.append(day)
            metas_all.append(
                {
                    "date": day,
                    "stock_code": code,
                    "tau": clk,
                    "l1_family": fam,
                    "l1_hit": True,
                }
            )
    if len(ys_all) < 4:
        return None
    return {
        "code": code,
        "xs": xs_all,
        "ys": ys_all,
        "dates": dates_all,
        "metas": metas_all,
        "minute_bars": list(minute_bars or []),
        "l1_stitched": True,
    }


def _build_one_tau_panel(
    item: Dict[str, Any],
    *,
    use_minute: bool,
    tau_hm: str,
    grid: Optional[Sequence[str]],
    min_history: int,
    include_alpha_tc: bool,
    include_alpha_th: bool,
    label_heads: Optional[Sequence[str]],
) -> Optional[Dict[str, Any]]:
    """单票 raw 面板；结束后 pop ``minute_bars``（L1 stitch 时留给 relabel）。"""
    code = str(item.get("code") or item.get("stock_code") or "").strip()
    bars = list(item.get("bars") or [])
    if len(bars) < min_history + 2:
        item.pop("minute_bars", None)
        return None
    if use_minute:
        clocks = list(grid) if grid else [str(tau_hm)]
        # H3：窗头优先按钟 stitch L1（与 materialize_l1_tau_grid 同指纹）
        if include_alpha_th and clocks:
            stitched = _try_stitch_l1_tau_panel(
                code,
                bars,
                list(item.get("minute_bars") or []),
                clocks,
                min_history=min_history,
                # 与物化 Job 对齐，避免 fingerprint miss
                include_alpha_tc=True,
                include_alpha_th=True,
            )
            if stitched is not None:
                item.pop("minute_bars", None)
                return stitched
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
            include_alpha_th=include_alpha_th,
            label_heads=label_heads,
        )
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
        return None
    return {
        "code": code,
        "xs": xs,
        "ys": ys,
        "dates": dates,
        "metas": metas,
    }


def build_tau_panels_from_bars(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    tau_hm: str = "open",
    tau_grid: Optional[Sequence[str]] = None,
    include_alpha_tc: bool = False,
    include_alpha_th: bool = False,
    attach_breadth: bool = True,
    label_heads: Optional[Sequence[str]] = None,
    workers: Optional[int] = None,
    progress_cb: ProgressCb = None,
) -> List[Dict[str, Any]]:
    """``stock_bars``: ``[{code, bars, minute_bars?, index_bars?, fundamentals?}, ...]``。

    ``tau_hm=open``：开盘→收盘标签；否则用分钟价训 τ→收盘（无分钟则跳过该日）。
    ``tau_grid``：变长前缀少数时钟（共享 β）。
    ``include_alpha_th``：ŷ_τ* 专用 ``raw_ath_*``（窗收益头）。
    ``label_heads``：只写这些头的 ``price_tau{N}``（默认五档）。
    ``workers``：并行灌票（``TAU_RIDGE_WORKERS``，硬顶 4）；分钟路径用
    **进程池**（绕过 GIL），开盘路径仍串行；``None`` 按池大小自动。
    ``progress_cb(done, total, msg)``：可选进度。
    分钟路径下每只票 ``collect`` 完会 ``pop`` 掉该票 ``minute_bars``（meta 已带
    请求头的 ``price_tau{N}``），避免满池 5m 常驻到拟合结束。
    ``attach_breadth=False``：跳过截面广度（流式逐票攒 raw 时用，最后再挂一次）。
    """
    from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
    from concurrent.futures.process import BrokenProcessPool

    from core.research.panel_matrix import _resolve_tau_workers

    use_minute = str(tau_hm or "open").strip().lower() not in ("", "open")
    grid = (
        normalize_minute_tau_grid(tau_hm=tau_hm, tau_grid=tau_grid)
        if use_minute
        else None
    )
    items = [it for it in (stock_bars or []) if isinstance(it, dict)]
    n_jobs = len(items)
    # 开盘路径几乎无 CPU；分钟路径才值得起进程
    n_workers = _resolve_tau_workers(n_jobs, workers) if use_minute else 1
    batch_size = max(4, int(TAU_PANEL_POOL_BATCH))
    raw_slots: List[Optional[Dict[str, Any]]] = [None] * n_jobs
    done_n = 0

    def _emit(done: int) -> None:
        if not callable(progress_cb):
            return
        try:
            progress_cb(done, max(1, n_jobs), f"建面板 {done}/{n_jobs}")
        except Exception:  # noqa: BLE001
            logger.debug("build_tau_panels progress_cb failed", exc_info=True)

    def _job_kw() -> Dict[str, Any]:
        return dict(
            use_minute=use_minute,
            tau_hm=str(tau_hm),
            grid=list(grid) if grid is not None else None,
            min_history=min_history,
            include_alpha_tc=include_alpha_tc,
            include_alpha_th=include_alpha_th,
            label_heads=tuple(label_heads) if label_heads is not None else None,
        )

    def _run_serial(start: int, end: int) -> None:
        nonlocal done_n
        kw = _job_kw()
        for i in range(start, end):
            raw_slots[i] = _build_one_tau_panel(items[i], **kw)
            done_n += 1
            if done_n == 1 or done_n % 8 == 0 or done_n >= n_jobs:
                _emit(done_n)

    def _run_batch(start: int, end: int) -> None:
        nonlocal done_n
        if end <= start:
            return
        if n_workers <= 1 or (end - start) < 4:
            _run_serial(start, end)
            return
        kw = _job_kw()
        try:
            from core.research.materialize.consumer import (
                context_initargs,
                research_pool_init,
            )

            init = research_pool_init
            initargs = context_initargs()
        except Exception:  # noqa: BLE001
            init = None
            initargs = ()
        try:
            pool = ProcessPoolExecutor(
                max_workers=n_workers,
                initializer=init,
                initargs=initargs,
            )
        except Exception:  # noqa: BLE001
            logger.exception("tau panels process pool unavailable; serial")
            _run_serial(start, end)
            return
        inflight: Dict[Any, int] = {}
        next_i = start
        try:

            def _submit(i: int) -> None:
                inflight[pool.submit(_build_one_tau_panel, items[i], **kw)] = i

            while next_i < end and len(inflight) < n_workers:
                _submit(next_i)
                next_i += 1
            while inflight:
                finished, _pending = wait(set(inflight), return_when=FIRST_COMPLETED)
                pool_broken = False
                for fut in finished:
                    i = inflight.pop(fut)
                    try:
                        raw_slots[i] = fut.result()
                    except BrokenProcessPool:
                        pool_broken = True
                        continue
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "tau panels worker failed code=%s; serial retry",
                            items[i].get("code"),
                        )
                        raw_slots[i] = _build_one_tau_panel(items[i], **kw)
                    items[i].pop("minute_bars", None)
                    done_n += 1
                    if done_n == 1 or done_n % 8 == 0 or done_n >= n_jobs:
                        _emit(done_n)
                if pool_broken:
                    logger.exception(
                        "tau panels process pool broken; finishing serial"
                    )
                    for fut in list(inflight):
                        i = inflight.pop(fut)
                        try:
                            if fut.done() and not fut.cancelled():
                                raw_slots[i] = fut.result()
                                items[i].pop("minute_bars", None)
                                done_n += 1
                        except Exception:  # noqa: BLE001
                            pass
                    try:
                        pool.shutdown(wait=False, cancel_futures=True)
                    except Exception:  # noqa: BLE001
                        pass
                    for j in range(start, end):
                        if raw_slots[j] is not None:
                            continue
                        raw_slots[j] = _build_one_tau_panel(items[j], **kw)
                        items[j].pop("minute_bars", None)
                        done_n += 1
                        if done_n == 1 or done_n % 8 == 0 or done_n >= n_jobs:
                            _emit(done_n)
                    return
                while next_i < end and len(inflight) < n_workers:
                    _submit(next_i)
                    next_i += 1
        finally:
            try:
                pool.shutdown(wait=True, cancel_futures=True)
            except Exception:  # noqa: BLE001
                logger.debug("tau panels pool shutdown failed", exc_info=True)

    if n_jobs:
        if n_workers > 1:
            logger.info(
                "tau panels process_workers=%s stocks=%s batch=%s label_heads=%s",
                n_workers,
                n_jobs,
                batch_size,
                list(label_heads) if label_heads is not None else "all",
            )
        start = 0
        while start < n_jobs:
            end = min(n_jobs, start + batch_size)
            _run_batch(start, end)
            start = end

    raw = [p for p in raw_slots if isinstance(p, dict)]
    if not attach_breadth:
        return raw
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
    progress_cb: Optional[Any] = None,
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

    def _prog(cur: int, tot: int, msg: str) -> None:
        if not callable(progress_cb):
            return
        try:
            progress_cb(int(cur), int(tot), str(msg or ""))
        except Exception:  # noqa: BLE001
            logger.debug("fit_tau_ridge_matrices progress_cb failed", exc_info=True)

    panel_stats: Dict[str, Any] = {}
    X, col_names, ys, dates, metas, n_stocks = collect_tau_compact(
        stock_bars,
        list(TAU_Z_FEATURES),
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        tau_hm=tau_key,
        tau_grid=grid,
        include_alpha_tc=True,
        head="y_tc",
        progress_cb=progress_cb,
        stats_out=panel_stats,
    )
    minute_attached = int(panel_stats.get("minute_attached") or 0)
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"rem 样本不足 n={len(ys)}（需≥20）",
            "sample_count": len(ys),
            "stock_count": n_stocks,
            "tau": tau_key,
            "tau_grid": grid,
            "minute_attached": minute_attached,
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
    _prog(max(1, n_stocks), max(1, n_stocks), "ŷ_τc Ridge 拟合…")
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
        "minute_attached": minute_attached,
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
