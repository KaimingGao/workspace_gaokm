"""ŷ_oo 观察池 Ridge：按只折进 float64 矩阵，避免整池 Python 字典常驻。

口径与观察池 Ridge 默认项对齐：Ridge（截距不惩罚）、趋势族 drop_redundant、
完整行（剔除缺测过多的列和真常数）；特征 z 由 ``cross_section_zscore``
选择日截面或训练窗全局 μ/σ。标签仍是百分点，不做 z-score。
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# 每批结束后关掉进程，把该批的字典堆还给系统。默认 4 进程 × 批内在途≤进程数。
_POOL_BATCH = 24

from core.research.factor_ols_fit import _qr_solve_np, clamp_ridge_lambda
from core.signal.factors.alpha_oo import (
    Z_OO_DROP_WHEN_AOO_CORE,
    alpha_oo_ridge_raw_names,
    is_alpha_oo_raw_key,
    is_alpha_oo_ridge_raw_key,
)


def _banned_factor_names() -> frozenset:
    try:
        from core.signal.factors.meta.health import unsourced_factor_names

        return unsourced_factor_names()
    except Exception:  # noqa: BLE001
        return frozenset()


def alpha_oo_raw_names(*, ridge_core_only: bool = False) -> List[str]:
    """入模 ``raw_aoo_*``。默认全量；``ridge_core_only=True`` 收 aoo_core（消融）。"""
    if ridge_core_only:
        return list(alpha_oo_ridge_raw_names())
    from core.signal.factors.alpha_oo import ALPHA_OO_FIELD_NAMES, ALPHA_OO_RAW_PREFIX

    return [f"{ALPHA_OO_RAW_PREFIX}{k}" for k in ALPHA_OO_FIELD_NAMES]


def _registered_ridge_names(*, drop_aoo_overlap: bool = False) -> List[str]:
    """入模列：注册日线 → ``z_oo_*``；不含 alpha_oo/alpha_tc 包。"""
    from core.signal.factors.z_oo import Z_OO_SKIP_PACKS, to_z_oo_key, z_oo_legacy_keys

    out: List[str] = []
    seen = set()
    for name in z_oo_legacy_keys():
        if drop_aoo_overlap and name in Z_OO_DROP_WHEN_AOO_CORE:
            continue
        k = to_z_oo_key(name)
        if not k or k in Z_OO_SKIP_PACKS or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


class OoPanelMatrix:
    """逐只追加子因子行，只保留 Ridge 会用到的列。"""

    def __init__(self, *, ridge_core_only: bool = False) -> None:
        self.names: List[str] = []
        self._index: Dict[str, int] = {}
        self._chunks: List[np.ndarray] = []
        self._ys: List[np.ndarray] = []
        self.dates: List[str] = []
        self._banned = _banned_factor_names()
        self._ridge_core_only = bool(ridge_core_only)
        # 面板必带 raw_aoo_*：重叠 z_oo（gap_risk/momentum/overheat）一律去掉，
        # 与 Tree / apply_aoo_core 去重策略一致（不再绑死 ridge_core_only）。
        self._widen(_registered_ridge_names(drop_aoo_overlap=True))
        self._widen(alpha_oo_raw_names(ridge_core_only=self._ridge_core_only))

    def _widen(self, new_names: Sequence[str]) -> None:
        fresh = [str(n) for n in new_names if str(n) not in self._index]
        if not fresh:
            return
        add = len(fresh)
        for i, ch in enumerate(self._chunks):
            extra = np.full((ch.shape[0], add), np.nan, dtype=np.float64)
            self._chunks[i] = np.concatenate([ch, extra], axis=1)
        for name in fresh:
            self._index[name] = len(self.names)
            self.names.append(name)

    def add_stock_rows(
        self,
        rows: Sequence[Dict[str, Any]],
        ys: Sequence[float],
        dates: Sequence[str],
    ) -> None:
        """折进一只股票的面板后，调用方应丢掉 ``rows``。"""
        from core.signal.factors.z_oo import stamp_z_oo_aliases, to_legacy_key

        n_in = min(len(rows), len(ys), len(dates))
        if n_in <= 0:
            return
        discovered: List[str] = []
        seen = set()
        for i in range(n_in):
            row = rows[i]
            if not isinstance(row, dict):
                continue
            accept = (
                is_alpha_oo_ridge_raw_key
                if self._ridge_core_only
                else is_alpha_oo_raw_key
            )
            for key, val in row.items():
                sk = str(key)
                # ridge_core_only 时只发现 aoo_core；默认全量 raw_aoo_*
                if sk in self._index or sk in seen or not accept(sk):
                    continue
                if val is None:
                    continue
                try:
                    fv = float(val)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(fv):
                    continue
                seen.add(sk)
                discovered.append(sk)
        self._widen(discovered)

        kept_rows: List[Dict[str, Any]] = []
        kept_y: List[float] = []
        kept_d: List[str] = []
        for i in range(n_in):
            row = rows[i]
            if not isinstance(row, dict):
                continue
            try:
                yf = float(ys[i])
            except (TypeError, ValueError):
                yf = float("nan")
            if not math.isfinite(yf):
                yf = float("nan")
            stamp_z_oo_aliases(row)
            kept_rows.append(row)
            kept_y.append(yf)
            kept_d.append(str(dates[i] or "")[:10])
        if not kept_rows:
            return
        width = len(self.names)
        block = np.full((len(kept_rows), width), np.nan, dtype=np.float64)
        y_arr = np.asarray(kept_y, dtype=np.float64)
        banned = self._banned
        index = self._index
        for r, row in enumerate(kept_rows):
            for key, val in row.items():
                sk = str(key)
                j = index.get(sk)
                leg = to_legacy_key(sk)
                if j is None or sk in banned or leg in banned or val is None:
                    continue
                try:
                    fv = float(val)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(fv):
                    block[r, j] = fv
        self._chunks.append(block)
        self._ys.append(y_arr)
        self.dates.extend(kept_d)

    def finalize(self) -> Tuple[np.ndarray, np.ndarray, List[str], List[str]]:
        """拼成一整块矩阵并释放分块。"""
        width = len(self.names)
        if not self._chunks:
            x = np.zeros((0, width), dtype=np.float64)
            y = np.zeros((0,), dtype=np.float64)
        else:
            x = np.vstack(self._chunks)
            y = np.concatenate(self._ys)
        self._chunks.clear()
        self._ys.clear()
        return x, y, list(self.names), list(self.dates)


def _fill_stock(
    panel: OoPanelMatrix,
    code: str,
    bars: Sequence[dict],
    horizon_days: int,
    *,
    keep_dates: Optional[Sequence[str]] = None,
) -> None:
    from core.research.oo_panel import collect_oo_panel

    xs, ys, dates = collect_oo_panel(
        list(bars),
        horizon_days=horizon_days,
        stock_code=code,
        pit_fundamentals=False,
        keep_dates=keep_dates,
    )
    panel.add_stock_rows(xs, ys, dates)


def _oo_stock_job(
    job: Tuple[Any, ...],
) -> Tuple[np.ndarray, np.ndarray, List[str], List[str]]:
    """子进程入口：一只股票折成矩阵后返回，字典不回传父进程。

    兼容 3 元组 ``(code, bars, horizon)`` 与 4 元组 ``(..., ridge_core_only)``：
    父进程未重启、子进程已加载新代码时，旧 3 元组不再炸 unpack。
    L1 fingerprint 命中则跳过 ``collect_oo_panel``；
    尾窗追加或历史前缀扩展时 keep_dates + merge。
    """
    from core.research.materialize.consumer import MaterializeRequired, raise_if_consumer_rebuild
    from core.research.panel_fingerprint import bars_fingerprint, make_fingerprint

    if not isinstance(job, (tuple, list)) or len(job) < 3:
        raise ValueError(f"ŷ_oo stock job 形状异常: {type(job)!r} len={len(job) if isinstance(job, (tuple, list)) else '?'}")
    if len(job) == 3:
        code, bars, horizon = job
        ridge_core_only = False
    else:
        code, bars, horizon, ridge_core_only = job[0], job[1], job[2], job[3]
    code_s = str(code)
    bars_l = list(bars or [])
    core = bool(ridge_core_only)
    try:
        horizon_i = int(horizon)
        fp = make_fingerprint(
            bars_fp=bars_fingerprint(bars_l),
            horizon=horizon_i,
            ridge_core_only=int(core),
        )
        try:
            from core.research.lake_bridge import FAMILY_OO, load_training_matrix

            hit = load_training_matrix(FAMILY_OO, code_s, fp)
            if hit is not None:
                x, y, names, dates, src = hit
                from core.research.feature_lake import read_stock_meta
                from core.research.incremental_panel import lake_row_covers_bar_end

                meta = read_stock_meta(FAMILY_OO, code_s)
                if lake_row_covers_bar_end(dates, bars_l, meta):
                    logger.debug(
                        "oo cache hit source=%s code=%s rows=%s",
                        src,
                        code_s,
                        len(dates),
                    )
                    return x, y, names, dates
                logger.debug(
                    "oo lake misses bar end code=%s rows=%s", code_s, len(dates)
                )
        except Exception:  # noqa: BLE001
            logger.debug("oo training cache load failed code=%s", code_s, exc_info=True)
        from core.signal.factors.alpha_oo import ALPHA_OO_PANEL_WINDOW

        # 次新凑不满垫窗+horizon：没有可训行；勿因缺 L1 打断整池。
        if len(bars_l) < int(ALPHA_OO_PANEL_WINDOW) + max(1, horizon_i):
            names = OoPanelMatrix(ridge_core_only=core).names
            return (
                np.zeros((0, len(names)), dtype=np.float64),
                np.zeros((0,), dtype=np.float64),
                names,
                [],
            )
        raise_if_consumer_rebuild("oo", code_s)
        # 尾窗增量：stem 命中则只重算尾部决策日（读 L1 meta）
        x = y = None
        names: List[str] = []
        dates: List[str] = []
        try:
            from core.research.incremental_panel import try_incremental_merge
            from core.research.lake_bridge import FAMILY_OO
            from core.signal.factors.alpha_oo import ALPHA_OO_PANEL_WINDOW

            def _oo_tail(keep_dates):
                panel_t = OoPanelMatrix(ridge_core_only=core)
                _fill_stock(
                    panel_t, code_s, bars_l, horizon_i, keep_dates=keep_dates
                )
                return panel_t.finalize()

            def _spec(bars_fp: str) -> str:
                return make_fingerprint(
                    bars_fp=bars_fp,
                    horizon=horizon_i,
                    ridge_core_only=int(core),
                )

            merged = try_incremental_merge(
                code_s,
                bars_l,
                pad=int(ALPHA_OO_PANEL_WINDOW) + max(5, horizon_i),
                collect_tail=_oo_tail,
                family=FAMILY_OO,
                spec_fp=_spec,
            )
            if merged is not None:
                x, y, names, dates = merged
                logger.debug(
                    "oo incremental tail code=%s rows=%s", code_s, len(dates)
                )
        except MaterializeRequired:
            raise
        except Exception:  # noqa: BLE001
            logger.debug("oo incremental path failed code=%s", code_s, exc_info=True)
            x = None
        if x is None:
            panel = OoPanelMatrix(ridge_core_only=core)
            _fill_stock(panel, code_s, bars_l, horizon_i)
            x, y, names, dates = panel.finalize()
    except MaterializeRequired:
        raise
    except Exception as exc:
        raise RuntimeError(f"ŷ_oo 面板失败 {code}") from exc
    if x.shape[0] != y.shape[0] or x.shape[0] != len(dates):
        raise RuntimeError(
            f"ŷ_oo 面板行数不一致 {code}: X={x.shape[0]} y={y.shape[0]} dates={len(dates)}"
        )
    try:
        from core.research.incremental_panel import stem_extra_meta
        from core.research.lake_bridge import FAMILY_OO, sync_panel_to_lake

        sync_panel_to_lake(
            FAMILY_OO,
            code_s,
            X=x,
            dates=dates,
            names=names,
            fingerprint=fp,
            y=y,
            extra_meta=stem_extra_meta(bars_l),
        )
    except Exception:  # noqa: BLE001
        logger.debug("oo L1 sync failed code=%s", code_s, exc_info=True)
    return x, y, names, dates


def _resolve_workers(n_jobs: int, workers: Optional[int]) -> int:
    """默认 4 个进程（``OO_RIDGE_WORKERS`` 可覆盖；硬顶 4）。

    不满 4 只且未指定进程数时走串行，避免小样本也付一次进程启动。
    8GB 机器满池时内存更紧，若换页严重可 ``OO_RIDGE_WORKERS=2``。
    """
    if n_jobs <= 1:
        return 1
    if workers is None:
        if n_jobs < 4:
            return 1
        raw = os.environ.get("OO_RIDGE_WORKERS", "4")
        try:
            workers = int(raw)
        except (TypeError, ValueError):
            workers = 4
    return min(max(1, int(workers)), n_jobs, 4)


def _stack_blocks(
    parts: Sequence[Tuple[np.ndarray, np.ndarray, List[str], List[str]]],
    *,
    ridge_core_only: bool = False,
) -> Tuple[np.ndarray, np.ndarray, List[str], List[str]]:
    names = OoPanelMatrix(ridge_core_only=ridge_core_only).names
    seen = set(names)
    for _x, _y, names_i, _d in parts:
        for name in names_i:
            if name not in seen:
                seen.add(name)
                names.append(name)
    name_j = {name: i for i, name in enumerate(names)}
    chunks_x: List[np.ndarray] = []
    chunks_y: List[np.ndarray] = []
    dates: List[str] = []
    for x, y, names_i, d in parts:
        if x.shape[0] == 0:
            continue
        if y.shape[0] != x.shape[0] or len(d) != x.shape[0]:
            raise RuntimeError(
                f"ŷ_oo 分块行数不一致: X={x.shape[0]} y={y.shape[0]} dates={len(d)}"
            )
        if x.ndim != 2:
            raise RuntimeError(f"ŷ_oo 分块维度异常: {x.shape}")
        if list(names_i) != names:
            aligned = np.full((x.shape[0], len(names)), np.nan, dtype=np.float64)
            for src, name in enumerate(names_i):
                dst = name_j.get(name)
                if dst is not None:
                    aligned[:, dst] = x[:, src]
            x = aligned
        chunks_x.append(x)
        chunks_y.append(y)
        dates.extend(d)
    width = len(names)
    if not chunks_x:
        return (
            np.zeros((0, width), dtype=np.float64),
            np.zeros((0,), dtype=np.float64),
            names,
            dates,
        )
    return np.vstack(chunks_x), np.concatenate(chunks_y), names, dates


def _maybe_materialize_day_cubes(
    parts: Sequence[Any],
    items: Sequence[Tuple[str, Sequence[dict]]],
    x: np.ndarray,
    names: Sequence[str],
    dates: Sequence[str],
) -> None:
    """按股块还原 code 列，物化 L2 DayCube（供回测/live 复用日 μ/σ）。"""
    from core.research.materialize.consumer import consumer_read_only

    if consumer_read_only():
        return
    try:
        from core.research.day_cube import cubes_from_stacked_panel

        code_rows: List[str] = []
        for part, item in zip(parts, items):
            if part is None:
                continue
            x_i = part[0]
            code = str(item[0] if isinstance(item, (tuple, list)) else "").strip()
            n_i = int(getattr(x_i, "shape", (0,))[0] or 0)
            if not code or n_i <= 0:
                continue
            code_rows.extend([code] * n_i)
        if code_rows and len(code_rows) == int(x.shape[0]):
            cubes_from_stacked_panel(
                dates, code_rows, x, names, clock="open", persist=True
            )
    except Exception:  # noqa: BLE001
        logger.debug("oo assemble day_cube materialize failed", exc_info=True)


def _assemble_serial(
    items: Sequence[Tuple[str, Sequence[dict]]],
    horizon_days: int,
    *,
    ridge_core_only: bool = False,
    progress_cb: Optional[Any] = None,
) -> Tuple[np.ndarray, np.ndarray, List[str], List[str]]:
    # 与并行路径统一走 ``_oo_stock_job``，以便命中按股磁盘缓存。
    core = bool(ridge_core_only)
    parts = []
    for i, (code, bars) in enumerate(items, start=1):
        parts.append(
            _oo_stock_job(
                (str(code), list(bars or []), int(horizon_days), core)
            )
        )
        if progress_cb:
            progress_cb(i)
    x, y, names, dates = _stack_blocks(parts, ridge_core_only=core)
    logger.info(
        "oo ridge compact panel rows=%s cols=%s stocks=%s workers=1",
        int(y.shape[0]),
        len(names),
        len(items),
    )
    _maybe_materialize_day_cubes(parts, items, x, names, dates)
    return x, y, names, dates


def _finish_serial_from(
    items: Sequence[Tuple[str, Sequence[dict]]],
    horizon_days: int,
    results: List[Any],
    start: int,
    *,
    ridge_core_only: bool = False,
    progress_cb: Optional[Any] = None,
) -> None:
    done = sum(1 for part in results if part is not None)
    for i in range(start, len(items)):
        if results[i] is not None:
            continue
        code, bars = items[i]
        results[i] = _oo_stock_job(
            (str(code), list(bars or []), int(horizon_days), bool(ridge_core_only))
        )
        done += 1
        if progress_cb:
            try:
                progress_cb(done)
            except Exception:  # noqa: BLE001
                logger.debug("oo assemble progress_cb failed", exc_info=True)


def _drain_stock_batch(
    pool: Any,
    items: Sequence[Tuple[str, Sequence[dict]]],
    horizon_days: int,
    start: int,
    end: int,
    results: List[Any],
    n_workers: int,
    *,
    ridge_core_only: bool = False,
    on_done: Optional[Any] = None,
) -> bool:
    """提交 ``[start, end)``。进程挂了则本批改串行，并返回 True。"""
    from core.research.materialize.pool import consume_process_batch

    core = bool(ridge_core_only)

    def _job(i: int) -> Tuple[Any, ...]:
        code, bars = items[i]
        return (str(code), list(bars or []), int(horizon_days), core)

    def _submit(i: int) -> Any:
        return pool.submit(_oo_stock_job, _job(i))

    def _run(i: int) -> Any:
        return _oo_stock_job(_job(i))

    return consume_process_batch(
        start=start,
        end=end,
        n_workers=n_workers,
        results=results,
        submit=_submit,
        run_one=_run,
        label=lambda i: str(items[i][0]),
        on_done=on_done,
    )


def _assemble_parallel(
    items: Sequence[Tuple[str, Sequence[dict]]],
    horizon_days: int,
    n_workers: int,
    *,
    ridge_core_only: bool = False,
    progress_cb: Optional[Any] = None,
) -> Tuple[np.ndarray, np.ndarray, List[str], List[str]]:
    from concurrent.futures import ProcessPoolExecutor

    from core.research.materialize.consumer import context_initargs, research_pool_init

    core = bool(ridge_core_only)
    results: List[Any] = [None] * len(items)
    done_n = 0

    def _on_done(_i: int) -> None:
        nonlocal done_n
        done_n += 1
        if progress_cb:
            progress_cb(done_n)

    logger.info("oo ridge workers=%s stocks=%s", n_workers, len(items))
    start = 0
    while start < len(items):
        end = min(len(items), start + _POOL_BATCH)
        try:
            pool = ProcessPoolExecutor(
                max_workers=n_workers,
                initializer=research_pool_init,
                initargs=context_initargs(),
            )
        except Exception:
            logger.exception("oo ridge process pool unavailable, using serial")
            _finish_serial_from(
                items,
                horizon_days,
                results,
                start,
                ridge_core_only=core,
                progress_cb=progress_cb,
            )
            break
        broke = False
        try:
            broke = _drain_stock_batch(
                pool,
                items,
                horizon_days,
                start,
                end,
                results,
                n_workers,
                ridge_core_only=core,
                on_done=_on_done,
            )
        finally:
            try:
                pool.shutdown(wait=True, cancel_futures=True)
            except Exception:
                logger.exception("oo ridge pool shutdown failed")
        if broke:
            break
        start = end
    if any(part is None for part in results):
        _finish_serial_from(
            items,
            horizon_days,
            results,
            0,
            ridge_core_only=core,
            progress_cb=progress_cb,
        )
    x, y, names, dates = _stack_blocks(results, ridge_core_only=core)
    logger.info(
        "oo ridge compact panel rows=%s cols=%s stocks=%s workers=%s",
        int(y.shape[0]),
        len(names),
        len(items),
        n_workers,
    )
    _maybe_materialize_day_cubes(results, items, x, names, dates)
    return x, y, names, dates


def assemble_oo_panels(
    items: Sequence[Tuple[str, Sequence[dict]]],
    *,
    horizon_days: int,
    workers: Optional[int] = None,
    ridge_core_only: bool = False,
    progress_cb: Optional[Any] = None,
) -> Tuple[np.ndarray, np.ndarray, List[str], List[str]]:
    """按股票顺序堆叠。满池默认 4 子进程，在途任务不超过进程数。

    ``ridge_core_only=False``（默认）：面板 ``z_oo_* ∪ raw_aoo_*`` 全量；
    ``True`` 时只留 aoo_core + 去重叠 z_oo（消融）。
    """
    seq = list(items)
    from core.research.materialize.coverage import enforce_consumer_coverage
    from core.signal.factors.alpha_oo import ALPHA_OO_PANEL_WINDOW

    # 垫窗只供特征，不要求这些日子已物化；截面人数不够的早期日同样跳过。
    enforce_consumer_coverage(
        seq,
        families=["oo"],
        clocks=["open"],
        min_history=int(ALPHA_OO_PANEL_WINDOW),
    )
    n_workers = _resolve_workers(len(seq), workers)
    core = bool(ridge_core_only)
    def _tick(done: int) -> None:
        if not callable(progress_cb):
            return
        total = max(1, len(seq))
        if done != total and done != 1 and done % 10 != 0:
            return
        try:
            progress_cb(done, total, f"L1 oo {done}/{total}")
        except Exception:  # noqa: BLE001
            logger.debug("oo assemble progress_cb failed", exc_info=True)

    if n_workers <= 1:
        return _assemble_serial(
            seq, horizon_days, ridge_core_only=core, progress_cb=_tick
        )
    return _assemble_parallel(
        seq,
        horizon_days,
        n_workers,
        ridge_core_only=core,
        progress_cb=_tick,
    )


def _column_finite_stats(col: np.ndarray) -> Tuple[int, float, float]:
    finite = np.isfinite(col)
    n_obs = int(finite.sum())
    if n_obs <= 0:
        return 0, 0.0, 0.0
    vals = col[finite]
    return n_obs, float(vals.max()), float(vals.min())


def _take_column(x: np.ndarray, row_pos: Optional[np.ndarray], j: int) -> np.ndarray:
    if row_pos is None:
        return x[:, j]
    return x[row_pos, j]


def _take_columns(
    x: np.ndarray, row_pos: Optional[np.ndarray], cols: Sequence[int]
) -> np.ndarray:
    col_idx = list(cols)
    if row_pos is None:
        return x[:, col_idx]
    return x[np.ix_(row_pos, col_idx)]


def _prepare_matrix(
    x: np.ndarray,
    y: np.ndarray,
    names: Sequence[str],
    *,
    row_pos: Optional[np.ndarray] = None,
    eps: float = 1e-6,
    min_samples_over_p: int = 3,
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], List[str], List[str], Dict[str, Any]]:
    """与 ``_prepare_complete_panel`` 同一套剔除，只在 ndarray 上做。

    ``row_pos`` 指定训练行。按列取值，避免先把全部列复制成训练子矩阵。
    剔除缺测过多的列和真常数，不按原始标准差设下限。
    """
    if row_pos is not None:
        y = np.asarray(y, dtype=np.float64)[row_pos]
    n_raw = int(y.shape[0])
    meta: Dict[str, Any] = {
        "raw_sample_count": n_raw,
        "dropped_sparse": [],
        "dropped_constant": [],
        "dropped_for_coverage": [],
    }
    feature_names = [str(n) for n in names]
    if n_raw < 4:
        return None, None, [], feature_names[:], meta

    min_obs = max(8, min(n_raw // 4, 40))
    name_j = {n: i for i, n in enumerate(feature_names)}
    candidates: List[str] = []
    for name in feature_names:
        n_obs, vmax, vmin = _column_finite_stats(_take_column(x, row_pos, name_j[name]))
        if n_obs < min_obs:
            meta["dropped_sparse"].append(name)
            continue
        if vmax - vmin <= eps:
            meta["dropped_constant"].append(name)
            continue
        candidates.append(name)
    if not candidates:
        return None, None, [], feature_names[:], meta

    active = list(candidates)
    while active:
        cols = [name_j[n] for n in active]
        sub = _take_columns(x, row_pos, cols)
        rows_mask = np.isfinite(sub).all(axis=1) & np.isfinite(y)
        rows_idx = np.flatnonzero(rows_mask)
        n = int(rows_idx.size)
        p = len(active)
        if n < max(4, p + min_samples_over_p):
            miss = sorted(
                (
                    int((~np.isfinite(_take_column(x, row_pos, name_j[name]))).sum()),
                    name,
                )
                for name in active
            )
            drop = miss[-1][1]
            active.remove(drop)
            meta["dropped_for_coverage"].append(drop)
            continue

        kept: List[str] = []
        local_j = {name: i for i, name in enumerate(active)}
        for name in active:
            col = sub[rows_idx, local_j[name]]
            if float(col.max()) - float(col.min()) <= eps:
                meta["dropped_constant"].append(name)
                continue
            kept.append(name)
        if len(kept) < len(active):
            active = kept
            if not active:
                break
            continue

        x_c = np.ascontiguousarray(sub[rows_idx], dtype=np.float64)
        y_c = np.ascontiguousarray(y[rows_idx], dtype=np.float64)
        excluded = (
            list(meta["dropped_sparse"])
            + list(meta["dropped_constant"])
            + list(meta["dropped_for_coverage"])
        )
        meta["complete_sample_count"] = n
        meta["active_feature_count"] = len(active)
        meta["complete_row_indices"] = [int(i) for i in rows_idx.tolist()]
        return x_c, y_c, active, excluded, meta

    excluded = (
        list(meta["dropped_sparse"])
        + list(meta["dropped_constant"])
        + list(meta["dropped_for_coverage"])
    )
    return None, None, [], excluded, meta


def _zscore_matrix(
    x: np.ndarray,
    names: Sequence[str],
    *,
    eps: float = 1e-6,
) -> Tuple[np.ndarray, Dict[str, float], Dict[str, float]]:
    n = int(x.shape[0])
    means: Dict[str, float] = {}
    stds: Dict[str, float] = {}
    if n == 0 or not names:
        return x, means, stds
    out = np.empty_like(x)
    floor = eps * eps
    for j, name in enumerate(names):
        col = x[:, j]
        mean = float(col.sum()) / n
        var = float(((col - mean) ** 2).sum()) / n
        std = math.sqrt(var) if var > floor else 1.0
        means[str(name)] = mean
        stds[str(name)] = std
        out[:, j] = (col - mean) / std
    return out, means, stds


def _fit_design(
    design_z: np.ndarray,
    y: np.ndarray,
    *,
    ridge_lambda: float,
    sample_weights: Optional[np.ndarray] = None,
) -> Optional[Dict[str, Any]]:
    n, p_aug = design_z.shape
    p = p_aug - 1
    if n < 4 or p < 1 or n < p + 3:
        return None
    lam = clamp_ridge_lambda(ridge_lambda, 0.0)
    x_fit = design_z
    y_fit = y
    if sample_weights is not None and int(sample_weights.shape[0]) == n:
        sw = np.sqrt(np.maximum(sample_weights.astype(np.float64), 0.0))
        keep = np.isfinite(sw) & (sw > 0)
        if int(keep.sum()) < max(4, p + 3):
            return None
        x_fit = design_z[keep] * sw[keep, None]
        y_fit = y[keep] * sw[keep]
    xs: List[np.ndarray] = [x_fit]
    ys: List[np.ndarray] = [y_fit]
    if lam > 0:
        sqrt_l = math.sqrt(lam)
        extra = np.zeros((p, p_aug), dtype=np.float64)
        extra[:, 1:] = np.eye(p, dtype=np.float64) * sqrt_l
        xs.append(extra)
        ys.append(np.zeros(p, dtype=np.float64))
    if len(xs) == 1:
        beta = _qr_solve_np(xs[0], ys[0])
    else:
        beta = _qr_solve_np(np.vstack(xs), np.concatenate(ys))
    if beta is None:
        return None
    y_mean = float(y.sum()) / n
    ss_tot = float(((y - y_mean) ** 2).sum())
    pred = design_z @ beta
    ss_res = float(((y - pred) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else None
    return {
        "intercept": round(float(beta[0]), 6),
        "beta": beta,
        "r_squared": round(r2, 4) if r2 is not None else None,
        "sample_count": n,
        "solver": "ridge" if lam > 0 else "qr",
        "ridge_lambda": lam,
    }


def _ols_matrix(
    x_z: np.ndarray,
    y: np.ndarray,
    active: List[str],
    excluded: List[str],
    *,
    ridge_lambda: float,
    sample_weights: Optional[np.ndarray] = None,
) -> Optional[Dict[str, Any]]:
    lam = clamp_ridge_lambda(ridge_lambda, 0.0)
    pos = {name: i for i, name in enumerate(active)}
    work = list(active)
    dropped_collinear: List[str] = []
    fit = None
    while work:
        cols = [pos[name] for name in work]
        design = np.empty((y.shape[0], len(work) + 1), dtype=np.float64)
        design[:, 0] = 1.0
        design[:, 1:] = x_z[:, cols]
        fit = _fit_design(
            design,
            y,
            ridge_lambda=lam,
            sample_weights=sample_weights,
        )
        if fit is not None:
            break
        if lam > 0:
            break
        dropped_collinear.append(work.pop())
    if fit is None:
        return None
    final_active = work
    all_excluded = list(excluded) + list(reversed(dropped_collinear))
    coef_map: Dict[str, Optional[float]] = {
        name: round(float(fit["beta"][i + 1]), 6) for i, name in enumerate(final_active)
    }
    for name in all_excluded:
        coef_map[name] = None
    return {
        "intercept": fit["intercept"],
        "coefficients": coef_map,
        "active_features": final_active,
        "excluded_features": all_excluded,
        "r_squared": fit["r_squared"],
        "sample_count": fit["sample_count"],
        "solver": fit["solver"],
        "ridge_lambda": lam,
        "dropped_collinear": dropped_collinear,
    }


def _align_row_weights(
    sample_weights: Optional[Sequence[float]],
    *,
    n_full: int,
    row_pos: Optional[np.ndarray],
    complete_idx: Optional[Sequence[int]],
) -> Optional[np.ndarray]:
    if sample_weights is None:
        return None
    w = np.asarray(list(sample_weights), dtype=np.float64)
    if w.ndim != 1:
        return None
    if row_pos is None:
        if int(w.shape[0]) != int(n_full):
            return None
        sliced = w
    else:
        if int(w.shape[0]) == int(row_pos.size):
            sliced = w
        elif int(w.shape[0]) == int(n_full):
            sliced = w[row_pos]
        else:
            return None
    if complete_idx is None:
        out = sliced
    else:
        idxs = [int(i) for i in complete_idx]
        if not idxs or max(idxs) >= int(sliced.shape[0]):
            return None
        out = sliced[np.asarray(idxs, dtype=np.int64)]
    out = np.where(np.isfinite(out) & (out > 0), out, 1e-6)
    return out.astype(np.float64, copy=False)


def fit_oo_ridge_matrix(
    x: np.ndarray,
    y: np.ndarray,
    names: Sequence[str],
    *,
    row_idx: Optional[Sequence[int]] = None,
    horizon_days: int = 1,
    ridge_lambda: float = 1.0,
    min_samples: int = 24,
    fitted_as_of: Optional[str] = None,
    sample_weights: Optional[Sequence[float]] = None,
    row_dates: Optional[Sequence[str]] = None,
    cross_section_zscore: bool = True,
) -> Tuple[Any, Dict[str, Any]]:
    """在矩阵上拟合 ŷ_oo，返回 ``(ReturnScoreModel | None, report)``。

    ``sample_weights`` 与全表 ``y`` 等长（或与 ``row_idx`` 等长）；√w 加权最小二乘。
    ``cross_section_zscore=False``：训练样本内全局 μ/σ（不用日截面）。
    日截面 z 与损失用同一批行。真常数仍剔除。
    """
    from core.signal.return_score import ReturnScoreModel

    y = np.asarray(y, dtype=np.float64)
    n_full = int(y.shape[0])
    row_pos = None if row_idx is None else np.asarray(row_idx, dtype=np.int64)
    n_raw = int(n_full if row_pos is None else row_pos.size)
    need = max(8, int(min_samples or 24))
    if n_raw < need:
        return None, {
            "success": False,
            "error": f"训练样本不足（{n_raw} < {min_samples}）",
            "sample_count": n_raw,
        }

    lam = clamp_ridge_lambda(ridge_lambda, 0.0)
    feature_names = [str(n) for n in names]
    x_c, y_c, active, excluded, prep_meta = _prepare_matrix(
        x,
        y,
        feature_names,
        row_pos=row_pos,
    )
    z_means: Dict[str, float] = {}
    z_stds: Dict[str, float] = {}
    fit = None
    if x_c is not None and y_c is not None and active:
        from core.research.beta_accuracy import apply_collinearity_on_columns
        from core.signal.factors.meta.collinearity import TREND_FAMILY

        from core.signal.factors.z_oo import is_z_oo_key, to_legacy_key

        trend_set = set(TREND_FAMILY)

        def _in_trend(name: str) -> bool:
            if name in trend_set:
                return True
            if is_z_oo_key(name) and to_legacy_key(name) in trend_set:
                return True
            return False

        family = [n for n in active if _in_trend(n)]
        if len(family) >= 2:
            pos = {name: i for i, name in enumerate(active)}
            columns = {name: x_c[:, pos[name]] for name in family}
            kept, dropped_red, _meta = apply_collinearity_on_columns(
                columns,
                list(active),
                y_c,
                policy="drop_redundant",
            )
        else:
            kept, dropped_red = list(active), []
        if dropped_red:
            excluded = list(excluded) + list(dropped_red)
            keep_pos = [active.index(n) for n in kept]
            x_c = np.ascontiguousarray(x_c[:, keep_pos], dtype=np.float64)
            active = list(kept)
        if active:
            from core.research.feature_standardize import dates_for_complete_rows

            use_cs = bool(cross_section_zscore)
            date_src = row_dates if use_cs else None
            if date_src is not None and row_pos is not None:
                date_src = [str(row_dates[int(i)])[:10] for i in row_pos.tolist()]
            dates_c = dates_for_complete_rows(
                date_src, prep_meta.get("complete_row_indices") or []
            )
            if (
                use_cs
                and dates_c is not None
                and len(dates_c) == int(x_c.shape[0])
            ):
                from core.research.materialize.cube_z import cs_z_matrix_prefer_cube

                x_z = cs_z_matrix_prefer_cube(x_c, active, dates_c)
                z_means, z_stds = {}, {}
            else:
                x_z, z_means, z_stds = _zscore_matrix(x_c, active)
            row_w = _align_row_weights(
                sample_weights,
                n_full=n_full,
                row_pos=row_pos,
                complete_idx=prep_meta.get("complete_row_indices"),
            )
            fit = _ols_matrix(
                x_z,
                y_c,
                active,
                excluded,
                ridge_lambda=lam,
                sample_weights=row_w,
            )

    if not fit:
        raw_n = prep_meta.get("raw_sample_count", n_raw)
        return None, {
            "success": False,
            "error": (
                "样本不足或矩阵奇异，无法拟合 OLS"
                f"（对齐样本 {raw_n}，注册因子 {len(feature_names)}；"
                "缺测因子已尽量剔除仍不够，可加大 lookback / 研究池 / ridge λ）"
            ),
            "sample_count": raw_n,
            "horizon_days": int(horizon_days),
            "excluded_features": excluded,
            "feature_zscore": True,
            "ridge_lambda": lam,
            "solver": "ridge" if lam > 0 else "qr",
        }

    report: Dict[str, Any] = {
        "success": True,
        "horizon_days": int(horizon_days),
        "sample_count": fit["sample_count"],
        "r_squared": fit["r_squared"],
        "intercept": fit["intercept"],
        "coefficients": fit["coefficients"],
        "active_features": fit["active_features"],
        "excluded_features": fit["excluded_features"],
        "feature_zscore": True,
        "zscore_scope": "cross_section" if not z_means else "",
        "zscore_means": {k: round(v, 4) for k, v in z_means.items()},
        "zscore_stds": {k: round(v, 4) for k, v in z_stds.items()},
        "solver": fit["solver"],
        "ridge_lambda": lam,
        "ridge_lambda_selected": lam,
        "prep_meta": prep_meta,
        "weighted_ols": bool(sample_weights is not None),
    }
    if sample_weights is not None:
        aligned = _align_row_weights(
            sample_weights,
            n_full=n_full,
            row_pos=row_pos,
            complete_idx=prep_meta.get("complete_row_indices"),
        )
        if aligned is not None and aligned.size:
            report["mean_sample_weight"] = round(
                float(aligned.sum() / max(1, int(aligned.size))), 4
            )
    model = ReturnScoreModel.from_ols_report(report, fitted_as_of=fitted_as_of)
    return model, report


def predict_oo_matrix(
    model: Any,
    x: np.ndarray,
    names: Sequence[str],
    row_idx: Sequence[int],
    row_dates: Optional[Sequence[str]] = None,
) -> List[Optional[float]]:
    """与 ``ReturnScoreModel.predict`` 相同：缺测跳过，不把缺测当成 z=0。

    ``row_dates`` 与整表 ``x`` 对齐。截面模型只在被打分的这些行里、按当天做 z-score。
    """
    idx = np.asarray(list(row_idx), dtype=np.int64)
    n = int(idx.size)
    if n == 0 or model is None or not getattr(model, "coefficients", None):
        return [None] * n
    name_j = {str(name): i for i, name in enumerate(names)}
    total = np.full(n, float(model.intercept), dtype=np.float64)
    used = np.zeros(n, dtype=bool)
    feature_zscore = bool(getattr(model, "feature_zscore", True))
    scope = str(getattr(model, "zscore_scope", "") or "")
    x_rows = x
    row_at = idx
    if (
        scope == "cross_section"
        and row_dates is not None
        and len(row_dates) == int(np.asarray(x).shape[0])
    ):
        from core.research.materialize.cube_z import cs_z_matrix_prefer_cube

        picked = np.asarray(x, dtype=np.float64)[idx]
        dates_sub = [str(row_dates[int(i)])[:10] for i in idx.tolist()]
        name_list = [str(n) for n in names]
        x_rows = cs_z_matrix_prefer_cube(picked, name_list, dates_sub)
        row_at = np.arange(n, dtype=np.int64)
        feature_zscore = False
    for name, beta in model.coefficients.items():
        j = name_j.get(str(name))
        if j is None:
            continue
        col = x_rows[row_at, j]
        ok = np.isfinite(col)
        if not bool(ok.any()):
            continue
        vals = col
        if feature_zscore:
            mu = float(model.z_means.get(name) or 0.0)
            sd = float(model.z_stds.get(name) or 1.0)
            if sd < 1e-12:
                sd = 1.0
            vals = (col - mu) / sd
        total[ok] += float(beta) * vals[ok]
        used |= ok
    out: List[Optional[float]] = []
    for i in range(n):
        out.append(round(float(total[i]), 6) if used[i] else None)
    return out
