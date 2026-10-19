"""把因子行折成 float64 面板，再在矩阵上做 keep-all Ridge。

截面广度仍要读缺口和路径收益，所以每只股票先写入矩阵，只把广度用到的十几列
留在瘦 dict 里。alpha_oo 原始列不再在全池 dict 里停留。
ŷ_co 对齐 ŷ_oo：单票任务内折矩阵、在途≤4、批大小 24、算完 pop bars（8GB）。
Ridge 口径与 ``fit_factor_ols_from_panel`` 对齐：√w + keep_all；拟合用完整行。
``cross_section_zscore=True``（默认）且有决策日 → 当天截面 z；``False`` → 训练窗
全局 μ/σ。截面模型预测缺测填 z=0。
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_BREADTH_BASE_KEYS = (
    "gap_pct",
    "ret_open_to_tau",
    "sector_gap_breadth",
    "theme_day",
    "gap_vs_sector",
    "sector_ret_to_tau",
    "ret_vs_sector",
)


def _breadth_keys() -> Tuple[str, ...]:
    from core.research.tau_panel import HORIZON_SEQ_CS_SPECS

    keys = list(_BREADTH_BASE_KEYS)
    seen = set(keys)
    for ret_key, sector_key, vs_key in HORIZON_SEQ_CS_SPECS:
        for k in (ret_key, sector_key, vs_key):
            if k not in seen:
                seen.add(k)
                keys.append(k)
    return tuple(keys)


class PanelMatrix:
    """按列名累积 float64 行。``widen_prefix``（如 ``raw_atc_`` / ``raw_aoo_``）可在见到数值时加列。"""

    def __init__(self, names: Sequence[str], *, widen_prefix: str = ""):
        self.names: List[str] = [str(n) for n in names]
        self._index: Dict[str, int] = {n: i for i, n in enumerate(self.names)}
        self._widen_prefix = str(widen_prefix or "")
        self._chunks: List[np.ndarray] = []

    def _widen(self, name: str) -> int:
        have = self._index.get(name)
        if have is not None:
            return have
        j = len(self.names)
        self.names.append(name)
        self._index[name] = j
        pad_chunks: List[np.ndarray] = []
        for chunk in self._chunks:
            pad = np.full((chunk.shape[0], 1), np.nan, dtype=np.float64)
            pad_chunks.append(np.concatenate([chunk, pad], axis=1))
        self._chunks = pad_chunks
        return j

    def add_rows(self, rows: Sequence[dict]) -> None:
        extra: List[str] = []
        prefix = self._widen_prefix
        if prefix:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for k, v in row.items():
                    ks = str(k)
                    if ks in self._index or not ks.startswith(prefix):
                        continue
                    if v is None or v == "":
                        continue
                    try:
                        float(v)
                    except (TypeError, ValueError):
                        continue
                    if ks not in extra:
                        extra.append(ks)
        for name in extra:
            self._widen(name)
        n = len(rows)
        if n <= 0:
            return
        p = len(self.names)
        block = np.full((n, p), np.nan, dtype=np.float64)
        index = self._index
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            for k, v in row.items():
                j = index.get(str(k))
                if j is None or v is None or v == "":
                    continue
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(fv):
                    block[i, j] = fv
        self._chunks.append(block)

    def append_block(self, arr: np.ndarray) -> None:
        """追加已按 ``self.names`` 对齐的 float64 块（ŷ_co 单票折矩阵后用）。"""
        a = np.asarray(arr, dtype=np.float64)
        if a.ndim != 2 or a.shape[0] <= 0:
            return
        p = len(self.names)
        if a.shape[1] == p:
            self._chunks.append(np.ascontiguousarray(a))
            return
        aligned = np.full((a.shape[0], p), np.nan, dtype=np.float64)
        ncopy = min(p, int(a.shape[1]))
        if ncopy:
            aligned[:, :ncopy] = a[:, :ncopy]
        self._chunks.append(aligned)

    def finalize(self) -> np.ndarray:
        if not self._chunks:
            return np.zeros((0, len(self.names)), dtype=np.float64)
        if len(self._chunks) == 1:
            out = self._chunks[0]
        else:
            out = np.vstack(self._chunks)
        self._chunks.clear()
        return out


def _slim_rows(rows: Sequence[dict], keys: Sequence[str]) -> List[dict]:
    out: List[dict] = []
    for row in rows:
        src = row if isinstance(row, dict) else {}
        out.append({k: src.get(k) for k in keys})
    return out


def _panel_xs_count(panels: Sequence[Dict[str, Any]]) -> int:
    return sum(len(p.get("xs") or []) for p in panels)


def _write_breadth_columns(
    X: np.ndarray,
    names: Sequence[str],
    panels: Sequence[Dict[str, Any]],
    keys: Sequence[str],
) -> None:
    n_xs = _panel_xs_count(panels)
    if n_xs != int(X.shape[0]):
        raise RuntimeError(
            f"截面广度行数不一致 matrix={int(X.shape[0])} xs={n_xs}"
        )
    index = {str(n): i for i, n in enumerate(names)}
    cols = [(k, index[k]) for k in keys if k in index]
    row_i = 0
    for panel in panels:
        for src in panel.get("xs") or []:
            row = src if isinstance(src, dict) else {}
            for key, j in cols:
                v = row.get(key)
                if v is None or v == "":
                    X[row_i, j] = np.nan
                    continue
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    X[row_i, j] = np.nan
                    continue
                X[row_i, j] = fv if math.isfinite(fv) else np.nan
            row_i += 1


def _z_stamp_family(head: str) -> str:
    """compact 面板 Z 前缀族：τ→z_τc_*，隔夜→z_co_*。"""
    h = str(head or "").strip().lower()
    if h.startswith("y_co") or h.startswith("co"):
        return "co"
    return "tc"


def _finish_breadth(
    block: PanelMatrix,
    slim_panels: List[Dict[str, Any]],
    *,
    gap_trigger_pct: float,
    slim_keys: Sequence[str],
    head: str,
    n_stocks: int,
) -> Tuple[np.ndarray, List[str], List[float], List[str], List[dict]]:
    from core.research.tau_panel import attach_cross_section_breadth

    enriched = attach_cross_section_breadth(
        slim_panels, gap_trigger_pct=gap_trigger_pct
    )
    z_family = _z_stamp_family(head)
    # 截面写回裸名后再镜像 canon，矩阵列与拟合键对齐
    if z_family == "co":
        from core.signal.factors.z_co import (
            Z_CO_LEGACY_SET,
            stamp_z_co_rows,
            to_z_co_key,
        )

        for panel in enriched:
            stamp_z_co_rows(panel.get("xs") or [])
        legacy_set = Z_CO_LEGACY_SET
        to_canon = to_z_co_key
    else:
        from core.signal.factors.z_tc import (
            Z_TC_LEGACY_SET,
            stamp_z_tc_rows,
            to_z_tc_key,
        )

        for panel in enriched:
            stamp_z_tc_rows(panel.get("xs") or [])
        legacy_set = Z_TC_LEGACY_SET
        to_canon = to_z_tc_key
    write_keys = list(slim_keys)
    seen = set(write_keys)
    for k in slim_keys:
        if k in legacy_set:
            canon = to_canon(k)
            if canon not in seen:
                seen.add(canon)
                write_keys.append(canon)
    X = block.finalize()
    _write_breadth_columns(X, block.names, enriched, write_keys)
    ys: List[float] = []
    dates: List[str] = []
    metas: List[dict] = []
    for panel in enriched:
        ys.extend(float(y) for y in (panel.get("ys") or []))
        dates.extend(str(d)[:10] for d in (panel.get("dates") or []))
        metas.extend(panel.get("metas") or [])
        # 广度已写回矩阵；丢掉 slim dict 行，8GB 上满池 xs 很贵
        panel["xs"] = []
    n_rows = int(X.shape[0])
    if not (len(ys) == len(dates) == len(metas) == n_rows):
        raise RuntimeError(
            f"{head} 面板行数不一致 matrix={n_rows} y={len(ys)} "
            f"dates={len(dates)} metas={len(metas)}"
        )
    logger.info(
        "compact panel head=%s rows=%s cols=%s stocks=%s",
        head,
        int(X.shape[0]),
        int(X.shape[1]),
        int(n_stocks),
    )
    return X, list(block.names), ys, dates, metas


# 与 ŷ_oo 同批大小：每批结束后丢掉该批 bars，把峰值压在 8GB 内。
_CO_POOL_BATCH = 24
_TAU_POOL_BATCH = 24


def _tc_matrix_seed_names(
    names: Sequence[str],
    *,
    include_alpha_tc: bool,
    include_alpha_th: bool = False,
) -> List[str]:
    """预注册 raw_atc_* / 窗头列，与 ŷ_co 的 seed 策略对齐。"""
    out = [str(n) for n in names]
    seen = set(out)
    if include_alpha_tc:
        from core.signal.factors.alpha_tc import ALPHA_TC_FIELD_NAMES, ALPHA_TC_RAW_PREFIX

        for key in ALPHA_TC_FIELD_NAMES:
            col = f"{ALPHA_TC_RAW_PREFIX}{key}"
            if col not in seen:
                seen.add(col)
                out.append(col)
    if include_alpha_th:
        try:
            from core.research.materialize.horizon_names import horizon_l1_feature_names

            for col in horizon_l1_feature_names():
                if col not in seen:
                    seen.add(col)
                    out.append(col)
        except Exception:  # noqa: BLE001
            pass
    return out


def _tau_stock_job(
    item: Dict[str, Any],
    col_names: Sequence[str],
    slim_keys: Sequence[str],
    *,
    use_minute: bool,
    tau_hm: str,
    grid: Optional[Sequence[str]],
    min_history: int,
    include_alpha_tc: bool,
    include_alpha_th: bool = False,
    cache_head: str = "tc",
) -> Optional[Tuple[str, np.ndarray, List[float], List[str], List[dict], List[dict]]]:
    """单票 ŷ_τc：任务内折 float64 + slim；写穿 L1（不落整份 xs）。

    ``cache_head="tc_rank"`` → L1 family ``tc_rank`` / ``tc_rank_HHMM``。
    ``include_alpha_th``：按钟附加窗头 ``raw_ath_*`` / ``z_τh_*``（H 轨）。
    """
    from core.research.panel_fingerprint import bars_fingerprint, make_fingerprint
    from core.research.tau_panel import (
        collect_tau_intraday_panel,
        collect_tau_open_panel,
    )
    from core.signal.factors.z_tc import stamp_z_tc_rows

    if not isinstance(item, dict):
        return None
    code = str(item.get("code") or item.get("stock_code") or "").strip()
    bars = list(item.get("bars") or [])
    if len(bars) < int(min_history) + 2:
        item.pop("minute_bars", None)
        return None
    mh = int(min_history)
    atc = bool(include_alpha_tc)
    ath = bool(include_alpha_th)
    minute = list(item.get("minute_bars") or []) if use_minute else None
    rank_cache = str(cache_head or "tc") == "tc_rank"
    fp = make_fingerprint(
        bars_fp=bars_fingerprint(bars, minute_bars=minute),
        tau_hm=str(tau_hm),
        min_history=mh,
        include_alpha_tc=int(atc),
        include_alpha_th=int(ath),
        use_minute=int(bool(use_minute)),
    )
    from core.research.lake_bridge import family_for_tc

    l1_family = family_for_tc(tau_hm=str(tau_hm), rank=rank_cache)
    # L1 命中（含 tc_HHMM / tc_rank_*）
    if code:
        try:
            from core.research.lake_bridge import load_training_matrix

            hit = load_training_matrix(
                l1_family,
                code,
                fp,
                names=col_names,
            )
            if hit is not None:
                block, y_arr, _names, dates_l, _src = hit
                if block.shape[1] == len(col_names) and len(dates_l) >= 4:
                    ys_l = [float(y) for y in y_arr.tolist()]
                    slim = [{} for _ in dates_l]
                    metas_l = [{"date": d, "stock_code": code} for d in dates_l]
                    item.pop("minute_bars", None)
                    return code, block, ys_l, dates_l, metas_l, slim
        except Exception:  # noqa: BLE001
            logger.debug("tau training cache load failed code=%s", code, exc_info=True)
    from core.research.materialize.consumer import raise_if_consumer_rebuild

    raise_if_consumer_rebuild(l1_family, code)
    # 开盘 τ：仅末根变化时尾窗增量（分钟路径仍全量）
    inc_hit = None
    if code and not use_minute:
        try:
            from core.research.incremental_panel import try_incremental_merge
            from core.signal.factors.alpha_tc import ALPHA_TC_PANEL_WINDOW

            def _tail(keep_dates):
                xs_t, ys_t, dates_t, metas_t = collect_tau_open_panel(
                    bars,
                    min_history=min_history,
                    index_bars=item.get("index_bars"),
                    fundamentals=item.get("fundamentals"),
                    stock_code=code,
                    include_alpha_tc=include_alpha_tc,
                    keep_dates=keep_dates,
                )
                if len(ys_t) < 1:
                    return (
                        np.zeros((0, len(col_names)), dtype=np.float64),
                        np.zeros((0,), dtype=np.float64),
                        list(col_names),
                        [],
                    )
                stamp_z_tc_rows(xs_t)
                return (
                    _dict_rows_to_matrix(xs_t, col_names),
                    np.asarray([float(y) for y in ys_t], dtype=np.float64),
                    list(col_names),
                    [str(d)[:10] for d in dates_t],
                )

            inc_hit = try_incremental_merge(
                code,
                bars,
                pad=int(ALPHA_TC_PANEL_WINDOW) + 8,
                collect_tail=_tail,
                family=l1_family,
            )
        except Exception:  # noqa: BLE001
            logger.debug("tau incremental failed code=%s", code, exc_info=True)
            inc_hit = None
    if inc_hit is not None:
        item.pop("minute_bars", None)
        block, y_arr, _names, dates_l = inc_hit
        ys_l = [float(y) for y in y_arr.tolist()]
        slim = [{} for _ in dates_l]
        metas_l = [{"date": d, "stock_code": code} for d in dates_l]
        if code:
            try:
                from core.research.incremental_panel import stem_extra_meta
                from core.research.lake_bridge import sync_panel_to_lake

                sync_panel_to_lake(
                    l1_family,
                    code,
                    X=block,
                    dates=dates_l,
                    names=list(col_names),
                    fingerprint=fp,
                    y=np.asarray(ys_l, dtype=np.float64),
                    extra_meta=stem_extra_meta(bars),
                )
            except Exception:  # noqa: BLE001
                logger.debug("tau L1 sync (incremental) failed code=%s", code, exc_info=True)
        return code, block, ys_l, dates_l, metas_l, slim
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
            include_alpha_th=include_alpha_th,
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
    stamp_z_tc_rows(xs)
    if include_alpha_th:
        try:
            from core.signal.factors.z_th import stamp_z_th_aliases

            for row in xs:
                if isinstance(row, dict):
                    stamp_z_th_aliases(row)
        except Exception:  # noqa: BLE001
            logger.debug("stamp_z_th_aliases failed", exc_info=True)
    slim = _slim_rows(xs, slim_keys)
    block = _dict_rows_to_matrix(xs, col_names)
    del xs
    ys_l = [float(y) for y in ys]
    dates_l = [str(d)[:10] for d in dates]
    metas_l = list(metas)
    if code:
        try:
            from core.research.incremental_panel import stem_extra_meta
            from core.research.lake_bridge import sync_panel_to_lake

            sync_panel_to_lake(
                l1_family,
                code,
                X=block,
                dates=dates_l,
                names=list(col_names),
                fingerprint=fp,
                y=np.asarray(ys_l, dtype=np.float64),
                extra_meta=stem_extra_meta(bars),
            )
        except Exception:  # noqa: BLE001
            logger.debug("tau L1 sync failed code=%s", code, exc_info=True)
    return code, block, ys_l, dates_l, metas_l, slim


def collect_tau_compact(
    stock_bars: Sequence[Dict[str, Any]],
    names: Sequence[str],
    *,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    tau_hm: str = "open",
    tau_grid: Optional[Sequence[str]] = None,
    include_alpha_tc: bool = True,
    include_alpha_th: bool = False,
    head: str = "y_tc",
    workers: Optional[int] = None,
    cache_head: str = "tc",
    progress_cb: Optional[Any] = None,
) -> Tuple[np.ndarray, List[str], List[float], List[str], List[dict], int]:
    """逐股折成矩阵后再做截面广度。返回 (X, names, ys, dates, metas, n_stocks)。

    现网默认 ``include_alpha_tc=True``（raw_atc_*）。
    ``include_alpha_th``：写穿窗头列（H 轨 ``tc_HHMM``）。
    单票任务内折 float64 + slim（对齐 ŷ_co），写穿 L1。
    并行：默认/硬顶 4 线程（``TAU_RIDGE_WORKERS``），批大小 ``_TAU_POOL_BATCH``。
    ``progress_cb(done, total, message)``：可选，供 job 槽刷新进度。
    """
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    from core.research.tau_panel import normalize_minute_tau_grid

    use_minute = str(tau_hm or "open").strip().lower() not in ("", "open")
    grid = (
        normalize_minute_tau_grid(tau_hm=tau_hm, tau_grid=tau_grid)
        if use_minute
        else None
    )
    items = [it for it in (stock_bars or []) if isinstance(it, dict)]
    from core.research.lake_bridge import family_for_tc
    from core.research.materialize.coverage import enforce_consumer_coverage

    tau_family = family_for_tc(
        tau_hm=str(tau_hm),
        rank=str(cache_head or "tc") == "tc_rank",
    )
    tau_clock = "open" if str(tau_hm or "open").strip().lower() in ("", "open", "09:30", "0930") else str(tau_hm)
    enforce_consumer_coverage(items, families=[tau_family], clocks=[tau_clock])
    n_jobs = len(items)
    n_workers = _resolve_tau_workers(n_jobs, workers)
    col_names = _tc_matrix_seed_names(
        names,
        include_alpha_tc=include_alpha_tc,
        include_alpha_th=include_alpha_th,
    )
    block = PanelMatrix(col_names, widen_prefix="")
    slim_keys = _breadth_keys()
    slim_panels: List[Dict[str, Any]] = []
    n_stocks = 0
    processed = 0

    def _tick() -> None:
        nonlocal processed
        processed += 1
        if not callable(progress_cb):
            return
        if (
            processed == 1
            or processed >= n_jobs
            or processed % 8 == 0
            or processed % _TAU_POOL_BATCH == 0
        ):
            try:
                progress_cb(
                    processed,
                    max(1, n_jobs),
                    f"ŷ_τc 面板 {processed}/{n_jobs}",
                )
            except Exception:  # noqa: BLE001
                logger.debug("tau compact progress_cb failed", exc_info=True)

    def _fold(
        pack: Optional[
            Tuple[str, np.ndarray, List[float], List[str], List[dict], List[dict]]
        ],
    ) -> None:
        nonlocal n_stocks
        if not pack:
            _tick()
            return
        code, arr, ys, dates, metas, slim = pack
        block.append_block(arr)
        slim_panels.append(
            {
                "code": code,
                "xs": slim,
                "ys": ys,
                "dates": dates,
                "metas": metas,
            }
        )
        n_stocks += 1
        _tick()

    def _run_serial(start: int, end: int) -> None:
        for i in range(start, end):
            _fold(
                _tau_stock_job(
                    items[i],
                    col_names,
                    slim_keys,
                    use_minute=use_minute,
                    tau_hm=str(tau_hm),
                    grid=grid,
                    min_history=min_history,
                    include_alpha_tc=include_alpha_tc,
                    include_alpha_th=include_alpha_th,
                    cache_head=cache_head,
                )
            )
            it = items[i]
            if isinstance(it, dict):
                it.pop("bars", None)

    def _run_batch(start: int, end: int) -> None:
        if end <= start:
            return
        if n_workers <= 1 or (end - start) < 4:
            _run_serial(start, end)
            return
        inflight: Dict[Any, int] = {}
        next_i = start
        with ThreadPoolExecutor(max_workers=n_workers) as pool:

            def _submit(i: int) -> None:
                fut = pool.submit(
                    _tau_stock_job,
                    items[i],
                    col_names,
                    slim_keys,
                    use_minute=use_minute,
                    tau_hm=str(tau_hm),
                    grid=grid,
                    min_history=min_history,
                    include_alpha_tc=include_alpha_tc,
                    include_alpha_th=include_alpha_th,
                    cache_head=cache_head,
                )
                inflight[fut] = i

            while next_i < end and len(inflight) < n_workers:
                _submit(next_i)
                next_i += 1
            while inflight:
                done, _pending = wait(set(inflight), return_when=FIRST_COMPLETED)
                for fut in done:
                    i = inflight.pop(fut)
                    _fold(fut.result())
                    it = items[i]
                    if isinstance(it, dict):
                        it.pop("bars", None)
                while next_i < end and len(inflight) < n_workers:
                    _submit(next_i)
                    next_i += 1

    if n_jobs:
        logger.info(
            "tau compact workers=%s stocks=%s batch=%s cache_head=%s",
            n_workers,
            n_jobs,
            _TAU_POOL_BATCH,
            str(cache_head or "tc"),
        )
        if callable(progress_cb):
            try:
                progress_cb(0, max(1, n_jobs), f"ŷ_τc 面板 0/{n_jobs}")
            except Exception:  # noqa: BLE001
                logger.debug("tau compact progress_cb failed", exc_info=True)
        start = 0
        while start < n_jobs:
            end = min(n_jobs, start + _TAU_POOL_BATCH)
            _run_batch(start, end)
            start = end

    if callable(progress_cb) and n_jobs:
        try:
            progress_cb(n_jobs, max(1, n_jobs), "ŷ_τc 截面广度…")
        except Exception:  # noqa: BLE001
            logger.debug("tau compact progress_cb failed", exc_info=True)

    X, out_names, ys_out, dates_out, metas_out = _finish_breadth(
        block,
        slim_panels,
        gap_trigger_pct=gap_trigger_pct,
        slim_keys=slim_keys,
        head=head,
        n_stocks=n_stocks,
    )
    return X, out_names, ys_out, dates_out, metas_out, n_stocks


def _dict_rows_to_matrix(
    rows: Sequence[dict], names: Sequence[str]
) -> np.ndarray:
    """把因子 dict 行折成 float64；调用方随后应丢弃 ``rows``。"""
    n = len(rows)
    p = len(names)
    out = np.full((n, p), np.nan, dtype=np.float64)
    if n <= 0 or p <= 0:
        return out
    index = {str(name): j for j, name in enumerate(names)}
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        for key, val in row.items():
            j = index.get(str(key))
            if j is None or val is None or val == "":
                continue
            try:
                fv = float(val)
            except (TypeError, ValueError):
                continue
            if math.isfinite(fv):
                out[i, j] = fv
    return out


def _co_stock_job(
    item: Dict[str, Any],
    col_names: Sequence[str],
    slim_keys: Sequence[str],
    *,
    min_history: int,
    include_alpha_oo: bool,
) -> Optional[Tuple[str, np.ndarray, List[float], List[str], List[dict], List[dict]]]:
    """单票 ŷ_co：在任务内折成 float64 + slim，全量 dict 不回传（对齐 ŷ_oo）。"""
    from core.research.co_panel import collect_co_panel
    from core.research.lake_bridge import FAMILY_CO
    from core.research.panel_fingerprint import bars_fingerprint, make_fingerprint
    from core.signal.factors.z_co import stamp_z_co_rows

    if not isinstance(item, dict):
        return None
    code = str(item.get("code") or item.get("stock_code") or "").strip()
    bars = list(item.get("bars") or [])
    if len(bars) < int(min_history) + 3:
        return None
    mh = int(min_history)
    aoo = bool(include_alpha_oo)
    fp = make_fingerprint(
        bars_fp=bars_fingerprint(bars),
        min_history=mh,
        include_alpha_oo=int(aoo),
    )
    if code:
        try:
            from core.research.lake_bridge import load_training_matrix

            hit = load_training_matrix(FAMILY_CO, code, fp, names=col_names)
            if hit is not None:
                block, y_arr, _names, dates_l, _src = hit
                if block.shape[1] == len(col_names) and len(dates_l) >= 4:
                    ys_l = [float(y) for y in y_arr.tolist()]
                    slim = [{} for _ in dates_l]
                    metas_l = [{"date": d, "stock_code": code} for d in dates_l]
                    return code, block, ys_l, dates_l, metas_l, slim
        except Exception:  # noqa: BLE001
            logger.debug("co training cache load failed code=%s", code, exc_info=True)
    from core.research.materialize.consumer import raise_if_consumer_rebuild

    raise_if_consumer_rebuild(FAMILY_CO, code)
    if code:
        try:
            from core.research.incremental_panel import try_incremental_merge
            from core.signal.factors.alpha_oo import ALPHA_OO_PANEL_WINDOW

            def _co_tail(keep_dates):
                xs_t, ys_t, dates_t, _metas_t = collect_co_panel(
                    bars,
                    min_history=min_history,
                    stock_code=code,
                    include_alpha_oo=include_alpha_oo,
                    keep_dates=keep_dates,
                )
                if len(ys_t) < 1:
                    return (
                        np.zeros((0, len(col_names)), dtype=np.float64),
                        np.zeros((0,), dtype=np.float64),
                        list(col_names),
                        [],
                    )
                stamp_z_co_rows(xs_t)
                return (
                    _dict_rows_to_matrix(xs_t, col_names),
                    np.asarray([float(y) for y in ys_t], dtype=np.float64),
                    list(col_names),
                    [str(d)[:10] for d in dates_t],
                )

            inc = try_incremental_merge(
                code,
                bars,
                pad=int(ALPHA_OO_PANEL_WINDOW) + 8,
                collect_tail=_co_tail,
                family=FAMILY_CO,
            )
            if inc is not None:
                block, y_arr, _n, dates_l = inc
                ys_l = [float(y) for y in y_arr.tolist()]
                slim = [{} for _ in dates_l]
                metas_l = [{"date": d, "stock_code": code} for d in dates_l]
                try:
                    from core.research.incremental_panel import stem_extra_meta
                    from core.research.lake_bridge import sync_panel_to_lake

                    sync_panel_to_lake(
                        FAMILY_CO,
                        code,
                        X=block,
                        dates=dates_l,
                        names=list(col_names),
                        fingerprint=fp,
                        y=np.asarray(ys_l, dtype=np.float64),
                        extra_meta=stem_extra_meta(bars),
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("co L1 sync (incremental) failed code=%s", code, exc_info=True)
                return code, block, ys_l, dates_l, metas_l, slim
        except Exception:  # noqa: BLE001
            logger.debug("co incremental failed code=%s", code, exc_info=True)
    from core.research.incremental_panel import stem_extra_meta

    stem_meta = stem_extra_meta(bars)
    xs, ys, dates, metas = collect_co_panel(
        bars,
        min_history=min_history,
        stock_code=code,
        include_alpha_oo=include_alpha_oo,
    )
    del bars
    if len(ys) < 4:
        return None
    stamp_z_co_rows(xs)
    slim = _slim_rows(xs, slim_keys)
    block = _dict_rows_to_matrix(xs, col_names)
    del xs
    ys_l = [float(y) for y in ys]
    dates_l = [str(d)[:10] for d in dates]
    metas_l = list(metas)
    if code:
        try:
            from core.research.lake_bridge import sync_panel_to_lake

            sync_panel_to_lake(
                FAMILY_CO,
                code,
                X=block,
                dates=dates_l,
                names=list(col_names),
                fingerprint=fp,
                y=np.asarray(ys_l, dtype=np.float64),
                extra_meta=stem_meta,
            )
        except Exception:  # noqa: BLE001
            logger.debug("co L1 sync failed code=%s", code, exc_info=True)
    return (
        code,
        block,
        ys_l,
        dates_l,
        metas_l,
        slim,
    )


def _resolve_co_workers(n_jobs: int, workers: Optional[int]) -> int:
    """默认 4 线程（``CO_RIDGE_WORKERS`` 可覆盖；硬顶 4）。

    8GB 满池更紧时可 ``CO_RIDGE_WORKERS=2``。
    """
    if n_jobs <= 1:
        return 1
    if workers is None:
        if n_jobs < 4:
            return 1
        raw = os.environ.get("CO_RIDGE_WORKERS", "4")
        try:
            workers = int(raw)
        except (TypeError, ValueError):
            workers = 4
    return min(max(1, int(workers)), n_jobs, 4)


def _resolve_tau_workers(n_jobs: int, workers: Optional[int]) -> int:
    """默认 4 worker（``TAU_RIDGE_WORKERS`` 可覆盖；硬顶 4）。

    ``collect_tau_compact`` 用 ThreadPool；``build_tau_panels_from_bars`` 分钟路径用 ProcessPool。
    """
    if n_jobs <= 1:
        return 1
    if workers is None:
        if n_jobs < 4:
            return 1
        raw = os.environ.get("TAU_RIDGE_WORKERS", "4")
        try:
            workers = int(raw)
        except (TypeError, ValueError):
            workers = 4
    return min(max(1, int(workers)), n_jobs, 4)


def _co_matrix_seed_names(
    names: Sequence[str], *, include_alpha_oo: bool
) -> List[str]:
    """预注册 raw_aoo_*，避免逐票 widen 时反复拷贝已有 chunk。"""
    out = [str(n) for n in names]
    if not include_alpha_oo:
        return out
    seen = set(out)
    from core.signal.factors.alpha_oo import ALPHA_OO_FIELD_NAMES, ALPHA_OO_RAW_PREFIX

    for key in ALPHA_OO_FIELD_NAMES:
        col = f"{ALPHA_OO_RAW_PREFIX}{key}"
        if col not in seen:
            seen.add(col)
            out.append(col)
    return out


def collect_co_compact(
    stock_bars: Sequence[Dict[str, Any]],
    names: Sequence[str],
    *,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    include_alpha_oo: bool = True,
    head: str = "y_co",
    workers: Optional[int] = None,
    progress_cb: Optional[Any] = None,
) -> Tuple[np.ndarray, List[str], List[float], List[str], List[dict], int]:
    """隔夜 ŷ_co 紧凑面板（8GB / 对齐 ŷ_oo）。

    - 单票任务内折 float64，只回传矩阵块 + 截面 slim 列
    - 在途 ≤ worker（默认/上限 4），按 ``_CO_POOL_BATCH`` 批跑
    - 每只算完立刻 ``pop bars``，避免满池日线 + 因子双驻留
    - ``progress_cb(done, total, message)`` 可选，供 job 槽刷新进度
    """
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    items = [it for it in (stock_bars or []) if isinstance(it, dict)]
    from core.research.lake_bridge import FAMILY_CO
    from core.research.materialize.coverage import enforce_consumer_coverage

    enforce_consumer_coverage(items, families=[FAMILY_CO], clocks=["open"])
    n_jobs = len(items)
    n_workers = _resolve_co_workers(n_jobs, workers)
    col_names = _co_matrix_seed_names(names, include_alpha_oo=include_alpha_oo)
    block = PanelMatrix(col_names, widen_prefix="")
    slim_keys = _breadth_keys()
    slim_panels: List[Dict[str, Any]] = []
    n_stocks = 0
    processed = 0

    def _drop_bars(i: int) -> None:
        it = items[i]
        if isinstance(it, dict):
            it.pop("bars", None)

    def _tick() -> None:
        nonlocal processed
        processed += 1
        if not callable(progress_cb):
            return
        if (
            processed == 1
            or processed >= n_jobs
            or processed % 8 == 0
            or processed % _CO_POOL_BATCH == 0
        ):
            try:
                progress_cb(
                    processed,
                    n_jobs,
                    f"ŷ_co 面板 {processed}/{n_jobs}",
                )
            except Exception:  # noqa: BLE001
                logger.debug("co compact progress_cb failed", exc_info=True)

    def _fold(
        pack: Optional[
            Tuple[str, np.ndarray, List[float], List[str], List[dict], List[dict]]
        ],
    ) -> None:
        nonlocal n_stocks
        if not pack:
            _tick()
            return
        code, arr, ys, dates, metas, slim = pack
        block.append_block(arr)
        slim_panels.append(
            {
                "code": code,
                "xs": slim,
                "ys": ys,
                "dates": dates,
                "metas": metas,
            }
        )
        n_stocks += 1
        _tick()

    def _run_serial(start: int, end: int) -> None:
        for i in range(start, end):
            _fold(
                _co_stock_job(
                    items[i],
                    col_names,
                    slim_keys,
                    min_history=min_history,
                    include_alpha_oo=include_alpha_oo,
                )
            )
            _drop_bars(i)

    def _run_batch(start: int, end: int) -> None:
        if end <= start:
            return
        if n_workers <= 1 or (end - start) < 4:
            _run_serial(start, end)
            return
        inflight: Dict[Any, int] = {}
        next_i = start
        with ThreadPoolExecutor(max_workers=n_workers) as pool:

            def _submit(i: int) -> None:
                fut = pool.submit(
                    _co_stock_job,
                    items[i],
                    col_names,
                    slim_keys,
                    min_history=min_history,
                    include_alpha_oo=include_alpha_oo,
                )
                inflight[fut] = i

            while next_i < end and len(inflight) < n_workers:
                _submit(next_i)
                next_i += 1
            while inflight:
                done, _pending = wait(set(inflight), return_when=FIRST_COMPLETED)
                for fut in done:
                    i = inflight.pop(fut)
                    _fold(fut.result())
                    _drop_bars(i)
                while next_i < end and len(inflight) < n_workers:
                    _submit(next_i)
                    next_i += 1

    if n_jobs:
        logger.info(
            "co compact workers=%s stocks=%s batch=%s",
            n_workers,
            n_jobs,
            _CO_POOL_BATCH,
        )
        if callable(progress_cb):
            try:
                progress_cb(0, n_jobs, f"ŷ_co 面板 0/{n_jobs}")
            except Exception:  # noqa: BLE001
                logger.debug("co compact progress_cb failed", exc_info=True)
        start = 0
        while start < n_jobs:
            end = min(n_jobs, start + _CO_POOL_BATCH)
            _run_batch(start, end)
            start = end

    if callable(progress_cb):
        try:
            progress_cb(n_jobs, max(1, n_jobs), "ŷ_co 截面广度…")
        except Exception:  # noqa: BLE001
            logger.debug("co compact progress_cb failed", exc_info=True)

    X, out_names, ys_out, dates_out, metas_out = _finish_breadth(
        block,
        slim_panels,
        gap_trigger_pct=gap_trigger_pct,
        slim_keys=slim_keys,
        head=head,
        n_stocks=n_stocks,
    )
    slim_panels.clear()
    return X, out_names, ys_out, dates_out, metas_out, n_stocks


def named_columns(
    X: np.ndarray, names: Sequence[str], wanted: Sequence[str]
) -> Tuple[np.ndarray, List[str]]:
    """按名字取出列，得到一块连续 float64。缺列跳过。"""
    index = {str(n): i for i, n in enumerate(names)}
    cols: List[int] = []
    kept: List[str] = []
    for name in wanted:
        key = str(name)
        j = index.get(key)
        if j is None:
            continue
        cols.append(j)
        kept.append(key)
    if not cols:
        return np.zeros((int(X.shape[0]), 0), dtype=np.float64), []
    return np.ascontiguousarray(X[:, cols]), kept


def raw_aoo_finite(
    X: np.ndarray,
    names: Sequence[str],
    row_idx: Optional[Sequence[int]] = None,
) -> List[str]:
    """训练行（或全表）上至少有一个有限值的 ``raw_aoo_*``，按列序。"""
    if X.size == 0 or X.shape[0] == 0:
        return []
    if row_idx is None:
        view = X
    else:
        idx = np.asarray(list(row_idx), dtype=np.int64)
        if idx.size == 0:
            return []
        view = X[idx]
    from core.signal.factors.alpha_oo import is_alpha_oo_raw_key

    out: List[str] = []
    for j, name in enumerate(names):
        if not is_alpha_oo_raw_key(str(name)):
            continue
        if np.isfinite(view[:, j]).any():
            out.append(str(name))
    return out


def raw_atc_finite(
    X: np.ndarray,
    names: Sequence[str],
    row_idx: Optional[Sequence[int]] = None,
) -> List[str]:
    """训练行（或全表）上至少有一个有限值的 ``raw_atc_*``，按列序。"""
    if X.size == 0 or X.shape[0] == 0:
        return []
    if row_idx is None:
        view = X
    else:
        idx = np.asarray(list(row_idx), dtype=np.int64)
        if idx.size == 0:
            return []
        view = X[idx]
    from core.signal.factors.alpha_tc import is_alpha_tc_raw_key

    out: List[str] = []
    for j, name in enumerate(names):
        if not is_alpha_tc_raw_key(str(name)):
            continue
        if np.isfinite(view[:, j]).any():
            out.append(str(name))
    return out


def finite_name_set(X: np.ndarray, names: Sequence[str]) -> set:
    present = set()
    if X.size == 0 or X.shape[0] == 0:
        return present
    finite = np.isfinite(X)
    for j, name in enumerate(names):
        if bool(finite[:, j].any()):
            present.add(str(name))
    return present


def fill_rates_from_matrix(
    X: np.ndarray, names: Sequence[str], keys: Sequence[str]
) -> Dict[str, Any]:
    """与 ``feature_fill_rates`` 相同结构：有限值视为已填。"""
    n = int(X.shape[0]) if X.ndim == 2 else 0
    index = {str(name): i for i, name in enumerate(names)}
    finite = np.isfinite(X) if n and X.ndim == 2 else None
    out: Dict[str, Any] = {"n": n, "keys": {}}
    for key in keys:
        j = index.get(str(key))
        filled = int(finite[:, j].sum()) if finite is not None and j is not None else 0
        out["keys"][str(key)] = {
            "filled": filled,
            "rate": round(filled / float(n), 4) if n else None,
        }
    return out


def _ridge_solve_np(
    design: np.ndarray,
    y: np.ndarray,
    ridge_lambda: float,
) -> Optional[np.ndarray]:
    """Ridge QR（截距列不惩罚）。"""
    from core.research.factor_ols_fit import _qr_solve_np, clamp_ridge_lambda

    lam = clamp_ridge_lambda(ridge_lambda, 0.0)
    m = int(design.shape[1])
    xs: List[np.ndarray] = [design]
    ys: List[np.ndarray] = [np.asarray(y, dtype=np.float64)]
    if lam > 0 and m > 1:
        extra = np.zeros((m - 1, m), dtype=np.float64)
        extra[:, 1:] = np.eye(m - 1, dtype=np.float64) * math.sqrt(lam)
        xs.append(extra)
        ys.append(np.zeros(m - 1, dtype=np.float64))
    if len(xs) == 1:
        return _qr_solve_np(xs[0], ys[0])
    return _qr_solve_np(np.vstack(xs), np.concatenate(ys))



def _prepare_complete_matrix(
    X: np.ndarray,
    y: np.ndarray,
    names: Sequence[str],
    *,
    min_samples_over_p: int = 3,
    eps: float = 1e-6,
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], List[str], List[str], Dict[str, Any]]:
    """与 ``_prepare_complete_panel`` 同一套剔除：稀疏、常数、覆盖。"""
    n_raw = int(y.shape[0])
    meta: Dict[str, Any] = {
        "raw_sample_count": n_raw,
        "dropped_sparse": [],
        "dropped_constant": [],
        "dropped_for_coverage": [],
    }
    if n_raw < 4 or X.shape[1] == 0:
        return None, None, [], list(names), meta

    finite = np.isfinite(X)
    min_obs = max(8, min(n_raw // 4, 40))
    candidates: List[str] = []
    for j, name in enumerate(names):
        n_obs = int(finite[:, j].sum())
        if n_obs < min_obs:
            meta["dropped_sparse"].append(name)
            continue
        col = X[finite[:, j], j]
        if float(col.max() - col.min()) <= eps:
            meta["dropped_constant"].append(name)
            continue
        candidates.append(name)

    if not candidates:
        return None, None, [], list(names), meta

    active = list(candidates)
    index = {str(n): i for i, n in enumerate(names)}
    while active:
        cols = [index[n] for n in active]
        mask = finite[:, cols].all(axis=1)
        rows_idx = np.flatnonzero(mask)
        n = int(rows_idx.size)
        p = len(active)
        if n < max(4, p + min_samples_over_p):
            miss = sorted(
                (int((~finite[:, index[name]]).sum()), name) for name in active
            )
            drop = miss[-1][1]
            active.remove(drop)
            meta["dropped_for_coverage"].append(drop)
            continue

        kept: List[str] = []
        for name in active:
            vals = X[rows_idx, index[name]]
            if float(vals.max() - vals.min()) <= eps:
                meta["dropped_constant"].append(name)
                continue
            kept.append(name)
        if len(kept) < len(active):
            active = kept
            if not active:
                break
            continue

        excluded = (
            list(meta["dropped_sparse"])
            + list(meta["dropped_constant"])
            + list(meta["dropped_for_coverage"])
        )
        meta["complete_row_indices"] = [int(i) for i in rows_idx]
        meta["complete_sample_count"] = n
        meta["active_feature_count"] = len(active)
        x_c = np.ascontiguousarray(X[rows_idx][:, [index[n] for n in active]])
        y_c = np.ascontiguousarray(y[rows_idx])
        return x_c, y_c, active, excluded, meta

    excluded = (
        list(meta["dropped_sparse"])
        + list(meta["dropped_constant"])
        + list(meta["dropped_for_coverage"])
    )
    return None, None, [], excluded, meta


def fit_keepall_ridge_matrix(
    X: np.ndarray,
    y: Sequence[float],
    names: Sequence[str],
    *,
    ridge_lambda: float = 1.0,
    sample_weights: Optional[Sequence[float]] = None,
    feature_zscore: bool = True,
    row_dates: Optional[Sequence[str]] = None,
    cross_section_zscore: bool = True,
) -> Dict[str, Any]:
    """keep-all、标准化、可选 √w。系数四舍五入口径与 dict OLS 相同。

    ``cross_section_zscore=True``（且提供 ``row_dates``）：按日截面 z。
    ``False``：训练样本内全局 μ/σ（落盘 zscore_means/stds）。
    """
    from core.signal.factors.alpha_oo import (
        ALPHA_OO_FACTOR_KEY,
        is_alpha_oo_raw_key,
    )

    y_arr = np.asarray(y, dtype=np.float64)
    x_arr = np.asarray(X, dtype=np.float64)
    feat_names = [str(n) for n in names]
    if any(is_alpha_oo_raw_key(n) for n in feat_names):
        feat_names = [n for n in feat_names if n != ALPHA_OO_FACTOR_KEY]
        if x_arr.ndim == 2 and x_arr.shape[1] != len(feat_names):
            src_index = {str(n): i for i, n in enumerate(names)}
            cols = [src_index[n] for n in feat_names if n in src_index]
            x_arr = x_arr[:, cols] if cols else np.zeros((x_arr.shape[0], 0))
            feat_names = [n for n in feat_names if n in src_index]
    if x_arr.ndim != 2 or y_arr.ndim != 1 or x_arr.shape[0] != y_arr.shape[0]:
        return {"success": False, "error": "面板形状与标签不一致"}
    if x_arr.shape[1] != len(feat_names):
        return {"success": False, "error": "列名与矩阵宽度不一致"}

    x_c, y_c, active, excluded, prep_meta = _prepare_complete_matrix(
        x_arr,
        y_arr,
        feat_names,
    )
    row_weights = None
    idxs = prep_meta.get("complete_row_indices") or []
    if (
        sample_weights is not None
        and x_c is not None
        and len(sample_weights) == int(y_arr.shape[0])
        and len(idxs) == int(x_c.shape[0])
    ):
        row_weights = []
        for i in idxs:
            try:
                w = float(sample_weights[int(i)])
            except (TypeError, ValueError, IndexError):
                w = 1.0
            if not math.isfinite(w) or w <= 0:
                w = 1e-6
            row_weights.append(float(w))
        row_weights = np.asarray(row_weights, dtype=np.float64)

    z_means: Dict[str, float] = {}
    z_stds: Dict[str, float] = {}
    zscore_scope = ""
    beta = None
    if x_c is not None and y_c is not None and active:
        x_fit = x_c
        from core.research.feature_standardize import dates_for_complete_rows

        dates_c = (
            dates_for_complete_rows(row_dates, idxs if isinstance(idxs, list) else [])
            if cross_section_zscore
            else None
        )
        if (
            feature_zscore
            and cross_section_zscore
            and dates_c is not None
            and len(dates_c) == int(x_fit.shape[0])
        ):
            from core.research.materialize.cube_z import cs_z_matrix_prefer_cube

            x_fit = cs_z_matrix_prefer_cube(x_fit, active, dates_c)
            zscore_scope = "cross_section"
        elif feature_zscore:
            eps2 = 1e-12
            means = x_fit.mean(axis=0)
            var = np.mean((x_fit - means) ** 2, axis=0)
            stds = np.sqrt(np.maximum(var, 0.0))
            stds = np.where(var > eps2, stds, 1.0)
            x_fit = (x_fit - means) / stds
            for i, name in enumerate(active):
                z_means[name] = float(means[i])
                z_stds[name] = float(stds[i])
        n = int(x_fit.shape[0])
        p = len(active)
        if n >= max(4, p + 3):
            design = np.column_stack(
                [np.ones(n, dtype=np.float64), np.asarray(x_fit, dtype=np.float64)]
            )
            y_fit = np.asarray(y_c, dtype=np.float64)
            if row_weights is not None:
                sw = np.sqrt(row_weights)
                design_w = design * sw[:, None]
                y_w = y_fit * sw
                beta = _ridge_solve_np(design_w, y_w, ridge_lambda)
            else:
                beta = _ridge_solve_np(design, y_fit, ridge_lambda)
            if beta is not None:
                y_mean = float(y_fit.mean())
                ss_tot = float(np.sum((y_fit - y_mean) ** 2))
                pred = design @ beta
                ss_res = float(np.sum((y_fit - pred) ** 2))
                r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else None
                coef_map: Dict[str, Optional[float]] = {
                    name: round(float(beta[i + 1]), 6) for i, name in enumerate(active)
                }
                for name in excluded:
                    coef_map[name] = None
                from core.research.factor_ols_fit import clamp_ridge_lambda

                lam = clamp_ridge_lambda(ridge_lambda, 0.0)
                note_bits = [
                    (
                        "float64 面板 Ridge；系数基于当天截面 z-score。"
                        if zscore_scope == "cross_section"
                        else "float64 面板 Ridge；系数基于样本内 z-score。"
                    )
                ]
                if row_weights is not None:
                    note_bits.append(" 已按 sample_weights 做加权 OLS（√w 变换）。")
                out = {
                    "success": True,
                    "intercept": round(float(beta[0]), 6),
                    "coefficients": coef_map,
                    "active_features": list(active),
                    "excluded_features": list(excluded),
                    "zscore_means": {k: round(v, 4) for k, v in z_means.items()},
                    "zscore_stds": {k: round(v, 4) for k, v in z_stds.items()},
                    "zscore_scope": zscore_scope,
                    "feature_zscore": bool(feature_zscore),
                    "sample_count": n,
                    "n_obs": n,
                    "r_squared": round(r2, 4) if r2 is not None else None,
                    "ridge_lambda": lam,
                    "solver": "ridge" if lam > 0 else "qr",
                    "collinearity_policy": "keep_all",
                    "weighted_ols": bool(row_weights is not None),
                    "prep_meta": prep_meta,
                    "note": "".join(note_bits),
                }
                return out
    return {
        "success": False,
        "error": "样本不足或矩阵奇异，无法拟合 OLS",
        "sample_count": int(prep_meta.get("raw_sample_count") or y_arr.shape[0]),
        "excluded_features": excluded,
        "prep_meta": prep_meta,
        "feature_zscore": bool(feature_zscore),
    }


def predict_ridge_matrix(
    fit: Dict[str, Any],
    X: np.ndarray,
    names: Sequence[str],
    row_dates: Optional[Sequence[str]] = None,
) -> List[Optional[float]]:
    """缺测按训练均值填成 z=0。截面模型改为按 ``row_dates`` 当天标准化后再打分。"""
    coefs = fit.get("coefficients") or {}
    intercept = float(fit.get("intercept") or 0.0)
    means = fit.get("zscore_means") or fit.get("z_means") or {}
    stds = fit.get("zscore_stds") or fit.get("z_stds") or {}
    from core.research.feature_standardize import is_cross_section_zscore

    x_arr = np.asarray(X, dtype=np.float64)
    if (
        row_dates is not None
        and is_cross_section_zscore(fit)
        and x_arr.ndim == 2
        and len(row_dates) == int(x_arr.shape[0])
    ):
        name_list = [str(n) for n in names]
        try:
            from core.research.materialize.cube_z import cs_z_matrix_prefer_cube

            x_arr = cs_z_matrix_prefer_cube(x_arr, name_list, row_dates)
        except Exception:  # noqa: BLE001
            from core.research.feature_standardize import cross_section_zscore_matrix

            x_arr = cross_section_zscore_matrix(x_arr, row_dates)
            x_arr = np.where(np.isfinite(x_arr), x_arr, 0.0)
        means = {}
        stds = {}
    active = [
        n
        for n in (fit.get("active_features") or list(coefs.keys()))
        if coefs.get(n) is not None
    ]
    if x_arr.ndim != 2 or x_arr.shape[0] == 0 or not active:
        return []
    index = {str(n): i for i, n in enumerate(names)}
    impute = bool(means)
    out: List[Optional[float]] = []
    for i in range(int(x_arr.shape[0])):
        pred = intercept
        ok = True
        for name in active:
            j = index.get(str(name))
            raw = float(x_arr[i, j]) if j is not None else float("nan")
            if j is None or not math.isfinite(raw):
                if not impute:
                    ok = False
                    break
                z = 0.0
            else:
                mu = float(means.get(name, 0.0)) if means else 0.0
                sd = float(stds.get(name, 1.0)) if stds else 1.0
                if sd < 1e-9:
                    sd = 1.0
                z = (raw - mu) / sd if means else raw
            pred += float(coefs.get(name) or 0.0) * z
        out.append(round(pred, 6) if ok else None)
    return out


def keepall_ridge_oos(
    X_tr: np.ndarray,
    y_tr: Sequence[float],
    X_te: np.ndarray,
    names: Sequence[str],
    *,
    ridge_lambda: float,
    sample_weights: Optional[Sequence[float]] = None,
    row_dates_tr: Optional[Sequence[str]] = None,
    row_dates_te: Optional[Sequence[str]] = None,
    cross_section_zscore: bool = True,
) -> Tuple[Dict[str, Any], List[Optional[float]]]:
    """训练集 keep-all Ridge，测试集预测。失败时退回训练均值截距。"""
    y_arr = np.asarray(list(y_tr), dtype=np.float64)
    n = int(y_arr.shape[0])
    y_mean = sum(float(v) for v in y_arr) / max(1, n)
    fit = fit_keepall_ridge_matrix(
        X_tr,
        y_arr,
        names,
        ridge_lambda=ridge_lambda,
        sample_weights=sample_weights,
        feature_zscore=True,
        row_dates=row_dates_tr if cross_section_zscore else None,
        cross_section_zscore=bool(cross_section_zscore),
    )
    if not fit.get("success"):
        fit = {
            "success": True,
            "intercept": round(float(y_mean), 6),
            "coefficients": {},
            "active_features": [],
            "zscore_means": {},
            "zscore_stds": {},
        }
    te = np.asarray(X_te, dtype=np.float64)
    preds = (
        predict_ridge_matrix(fit, te, names, row_dates=row_dates_te)
        if te.ndim == 2 and te.shape[0]
        else []
    )
    return fit, preds
