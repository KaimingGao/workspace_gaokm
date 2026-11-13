"""决策日因子面板（研究轨）：z_oo_* ∪ raw_aoo_*，带 code/date 便于与序列对齐。

加速：ProcessPool 并行（复用 ``_oo_stock_job`` / L1 湖）；可选磁盘缓存。
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def _note_stock_failures(label: str, failed: Sequence[str], total: int) -> None:
    """少数坏票丢掉；成片异常则中止，避免在残缺宇宙上接着训。"""
    n_fail = len(failed)
    if n_fail <= 0 or total <= 0:
        return
    shown = ",".join(str(c) for c in list(failed)[:6])
    print(f"  {label} 跳过 {n_fail}/{total} 只（异常）例如 {shown}", flush=True)
    if n_fail >= 3 and n_fail * 5 >= int(total):
        raise RuntimeError(f"{label} 异常过多 {n_fail}/{total}，中止")


def _run_stock_jobs(
    jobs: Sequence[Tuple[Any, ...]],
    fn: Any,
    *,
    n_workers: int,
    progress_every: int,
    label: str,
) -> List[Any]:
    chunks: List[Any] = []
    failed: List[str] = []
    done = 0
    total = len(jobs)
    t0 = time.time()

    def _one(code: Any, result_fn) -> None:
        nonlocal done
        try:
            chunks.append(result_fn())
        except BrokenProcessPool:
            raise
        except Exception:  # noqa: BLE001
            failed.append(str(code))
            logger.debug("%s job failed %s", label, code, exc_info=True)
        done += 1
        if progress_every and done % int(progress_every) == 0:
            print(
                f"  {label} {done}/{total} elapsed={time.time() - t0:.0f}s",
                flush=True,
            )

    if n_workers <= 1:
        for job in jobs:
            _one(job[0], lambda job=job: fn(job))
    else:
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            futs = {pool.submit(fn, job): job[0] for job in jobs}
            try:
                for fut in as_completed(futs):
                    _one(futs[fut], fut.result)
            except BrokenProcessPool as exc:
                print(
                    f"  {label} 进程池崩溃：{exc}；已完成 {done}/{total}",
                    flush=True,
                )
                raise
    _note_stock_failures(label, failed, total)
    return chunks


@dataclass
class FactorPanel:
    X: np.ndarray  # (N, F) float32
    y: np.ndarray
    dates: List[str]
    codes: List[str]
    feature_names: List[str]

    @property
    def n(self) -> int:
        return int(self.y.shape[0])

    def subset(self, idx: Sequence[int]) -> "FactorPanel":
        ii = np.asarray(list(idx), dtype=np.int64)
        return FactorPanel(
            X=self.X[ii],
            y=self.y[ii],
            dates=[self.dates[int(i)] for i in ii.tolist()],
            codes=[self.codes[int(i)] for i in ii.tolist()],
            feature_names=list(self.feature_names),
        )


def _factor_stock_job(
    job: Tuple[str, List[dict], int],
) -> Tuple[str, np.ndarray, np.ndarray, List[str], List[str]]:
    """子进程：单票全量矩阵（优先 L1 湖，否则 collect）。"""
    code, bars, horizon = job
    from core.research.oo_model_compact import _oo_stock_job

    x, y, names, dates = _oo_stock_job(
        (str(code), list(bars or []), int(horizon), False)
    )
    return str(code), x, y, list(names), [str(d)[:10] for d in dates]


def _cache_key(
    codes: Sequence[str],
    *,
    horizon_days: int,
    aoo_core_only: bool,
    lookback_hint: int,
) -> str:
    h = hashlib.sha1()
    h.update(f"h{horizon_days}|core{int(aoo_core_only)}|lb{lookback_hint}|".encode())
    for c in sorted(str(x) for x in codes):
        h.update(c.encode())
        h.update(b",")
    return h.hexdigest()[:20]


def _default_cache_dir() -> str:
    root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "experiments", "yhat_transformer", "cache")
    )
    return root


def _try_load_cache(path: str) -> Optional["FactorPanel"]:
    if not path or not os.path.isfile(path):
        return None
    try:
        z = np.load(path, allow_pickle=True)
        return FactorPanel(
            X=np.asarray(z["X"], dtype=np.float32),
            y=np.asarray(z["y"], dtype=np.float32),
            dates=[str(d) for d in z["dates"].tolist()],
            codes=[str(c) for c in z["codes"].tolist()],
            feature_names=[str(n) for n in z["feature_names"].tolist()],
        )
    except Exception:  # noqa: BLE001
        logger.debug("factor panel cache load failed %s", path, exc_info=True)
        return None


def _save_cache(path: str, panel: "FactorPanel") -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        np.savez_compressed(
            path,
            X=panel.X,
            y=panel.y,
            dates=np.asarray(panel.dates, dtype=object),
            codes=np.asarray(panel.codes, dtype=object),
            feature_names=np.asarray(panel.feature_names, dtype=object),
        )
    except Exception:  # noqa: BLE001
        logger.debug("factor panel cache save failed %s", path, exc_info=True)


def _column_index(names: Sequence[str]) -> Dict[str, int]:
    """同名列只留第一次出现的下标。"""
    out: Dict[str, int] = {}
    for i, name in enumerate(names or []):
        key = str(name)
        if key and key not in out:
            out[key] = i
    return out


def _take_columns(panel: "FactorPanel", cols: Sequence[int], names: Sequence[str]) -> "FactorPanel":
    """抽出独立 float32 块，避免 Arm·T 还指着全量矩阵。"""
    idx = np.asarray(list(cols), dtype=np.int64)
    X = np.ascontiguousarray(panel.X[:, idx], dtype=np.float32)
    return FactorPanel(
        X=X,
        y=np.array(panel.y, dtype=np.float32, copy=True),
        dates=list(panel.dates),
        codes=list(panel.codes),
        feature_names=[str(n) for n in names],
    )


def require_factor_panel(panel: "FactorPanel") -> None:
    """行数、列名和日期必须对齐，否则拒绝拿去拟合。"""
    if panel is None or getattr(panel, "X", None) is None or getattr(panel, "y", None) is None:
        raise ValueError("因子面板为空")
    if int(getattr(panel.X, "ndim", 0) or 0) != 2:
        raise ValueError(f"因子面板不是二维矩阵 shape={getattr(panel.X, 'shape', None)}")
    n_row = int(panel.X.shape[0])
    n_col = int(panel.X.shape[1])
    if int(panel.y.shape[0]) != n_row:
        raise ValueError(f"因子面板行列不一致 X={n_row} y={int(panel.y.shape[0])}")
    if len(panel.feature_names) != n_col:
        raise ValueError(
            f"因子列名数量不一致 names={len(panel.feature_names)} F={n_col}"
        )
    if len(panel.dates) != n_row or len(panel.codes) != n_row:
        raise ValueError(
            f"因子日期/代码数量不一致 dates={len(panel.dates)} "
            f"codes={len(panel.codes)} N={n_row}"
        )


def _select_atc_core_columns(panel: "FactorPanel") -> "FactorPanel":
    """从全量开盘包裁到 atc_core。列对不上则原样返回。"""
    from core.signal.factors.alpha_tc import select_atc_core_names

    want = select_atc_core_names(panel.feature_names)
    name_j = _column_index(panel.feature_names)
    cols: List[int] = []
    kept_names: List[str] = []
    for name in want:
        j = name_j.get(str(name))
        if j is None or j in cols:
            continue
        cols.append(j)
        kept_names.append(str(name))
    if len(cols) < 8:
        return panel
    print(f"  裁 atc_core 列 {panel.X.shape[1]}→{len(kept_names)}", flush=True)
    return _take_columns(panel, cols, kept_names)


def _select_core_columns(panel: "FactorPanel") -> "FactorPanel":
    """从全量 L1 列裁到 aoo_core（研究消融）；列名对不上则原样返回。"""
    from core.research.oo_model_compact import OoPanelMatrix

    want = list(OoPanelMatrix(aoo_core_only=True).names)
    name_j = _column_index(panel.feature_names)
    cols: List[int] = []
    kept_names: List[str] = []
    for name in want:
        j = name_j.get(str(name))
        if j is None or j in cols:
            continue
        cols.append(j)
        kept_names.append(panel.feature_names[j])
    if len(cols) < max(8, len(want) // 2):
        return panel
    return _take_columns(panel, cols, kept_names)


def slice_arm_t_panel(
    panel: "FactorPanel",
    head: Any,
    *,
    aoo_core_only: bool = False,
    atc_core_only: bool = False,
) -> "FactorPanel":
    """Arm·T 的因子方案。Arm·L 始终用全量，不走这里。

    点了 core 但裁不窄时直接失败，避免静默退回全量。
    """
    require_factor_panel(panel)
    hid = str(getattr(head, "id", head) or "oo").strip().lower()
    tc = hid in ("tc", "tc_open", "y_tc", "tc_rank", "y_tc_rank")
    if tc:
        if not atc_core_only:
            return panel
        sliced = _select_atc_core_columns(panel)
    elif aoo_core_only:
        sliced = _select_core_columns(panel)
    else:
        return panel
    n_in = int(panel.X.shape[1])
    n_out = int(sliced.X.shape[1]) if getattr(sliced.X, "ndim", 0) == 2 else 0
    if sliced is panel or n_out >= n_in or n_out < 8:
        raise ValueError(
            f"Arm·T core 裁列失败 head={hid or 'oo'}：全量 F={n_in}，结果 F={n_out}"
        )
    return sliced


def build_oo_factor_panel(
    stock_bars: Mapping[str, Sequence[dict]],
    *,
    horizon_days: int = 1,
    progress_every: int = 25,
    workers: Optional[int] = None,
    use_cache: bool = True,
    cache_dir: Optional[str] = None,
) -> FactorPanel:
    """并行堆叠全量因子面板，拉数指纹对齐生产 L1。

    Arm·T 的 core 不在这里裁，等 Arm·L 结束再切。
    """
    items = [(str(c).strip(), list(bars or [])) for c, bars in stock_bars.items() if str(c).strip() and bars]
    if not items:
        return FactorPanel(
            X=np.zeros((0, 1), dtype=np.float32),
            y=np.zeros((0,), dtype=np.float32),
            dates=[],
            codes=[],
            feature_names=[],
        )

    lookback_hint = max((len(b) for _, b in items), default=0)
    codes = [c for c, _ in items]
    cdir = cache_dir or _default_cache_dir()
    cache_path = ""
    # 缓存键固定全量（core0），与已有全量缓存兼容
    if use_cache:
        key = _cache_key(
            codes,
            horizon_days=horizon_days,
            aoo_core_only=False,
            lookback_hint=lookback_hint,
        )
        cache_path = os.path.join(cdir, f"fac_{key}.npz")
        hit = _try_load_cache(cache_path)
        if hit is not None and hit.n > 0:
            print(f"  因子面板缓存命中 N={hit.n} F={hit.X.shape[1]} ← {cache_path}", flush=True)
            return hit

    n_cpu = os.cpu_count() or 2
    if workers is None:
        # 8G 机器：默认 2；可用 OO_FAC_WORKERS 覆盖
        try:
            workers = int(os.environ.get("OO_FAC_WORKERS") or 2)
        except ValueError:
            workers = 2
    n_workers = max(1, min(int(workers), 4, len(items), n_cpu))

    # 与生产 L1 指纹一致：aoo_core_only=False（线字段仍为 ridge_core_only=0）
    jobs = [(code, bars, int(horizon_days)) for code, bars in items]
    print(
        f"  因子面板并行 workers={n_workers} stocks={len(jobs)} lake_fp=full_aoo",
        flush=True,
    )
    t0 = time.time()
    chunks = _run_stock_jobs(
        jobs,
        _factor_stock_job,
        n_workers=n_workers,
        progress_every=int(progress_every or 0),
        label="因子面板",
    )

    # 统一列名：取最宽的 names
    names: List[str] = []
    for _c, x, _y, nm, _d in chunks:
        if x is None or getattr(x, "size", 0) == 0:
            continue
        if len(nm) > len(names):
            names = list(nm)
    if not names:
        # fallback skeleton
        from core.research.oo_model_compact import OoPanelMatrix

        names = list(OoPanelMatrix(aoo_core_only=False).names)

    name_j = {n: i for i, n in enumerate(names)}
    xs: List[np.ndarray] = []
    ys: List[np.ndarray] = []
    dates: List[str] = []
    codes_all: List[str] = []
    for code, x, y, nm, ds in chunks:
        if x is None or int(getattr(x, "shape", [0])[0]) <= 0:
            continue
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        if list(nm) == names:
            block = x
        else:
            block = np.full((x.shape[0], len(names)), np.nan, dtype=np.float64)
            for j_src, n in enumerate(nm):
                j = name_j.get(n)
                if j is not None and j_src < x.shape[1]:
                    block[:, j] = x[:, j_src]
        xs.append(block)
        ys.append(y)
        dates.extend(ds[: int(x.shape[0])])
        codes_all.extend([code] * int(x.shape[0]))

    if not xs:
        return FactorPanel(
            X=np.zeros((0, len(names)), dtype=np.float32),
            y=np.zeros((0,), dtype=np.float32),
            dates=[],
            codes=[],
            feature_names=names,
        )

    X = np.concatenate(xs, axis=0).astype(np.float32)
    y = np.concatenate(ys, axis=0).astype(np.float32)
    X = np.where(np.isfinite(X), X, 0.0)
    keep = np.isfinite(y)
    if not bool(np.all(keep)):
        idx = np.where(keep)[0]
        X = X[idx]
        y = y[idx]
        dates = [dates[int(i)] for i in idx.tolist()]
        codes_all = [codes_all[int(i)] for i in idx.tolist()]

    panel = FactorPanel(
        X=X,
        y=y,
        dates=[str(d)[:10] for d in dates],
        codes=codes_all,
        feature_names=[str(n) for n in names],
    )
    print(
        f"  因子面板完成 N={panel.n} F={panel.X.shape[1]} "
        f"{time.time()-t0:.1f}s workers={n_workers}",
        flush=True,
    )
    if use_cache and cache_path:
        _save_cache(cache_path, panel)
    return panel


def _tc_stock_job(
    job: Tuple[str, List[dict]],
) -> Tuple[str, np.ndarray, np.ndarray, List[str], List[str]]:
    """单票 τ=open ŷ_τc 因子行：Z∪raw_atc（日线可算开盘包）。"""
    code, bars = job
    from core.research.tau_panel import collect_tau_open_panel

    xs, ys, dates, _metas = collect_tau_open_panel(
        list(bars or []),
        include_alpha_tc=True,
        min_history=12,
        max_window=60,
        stock_code=str(code),
    )
    if not xs or not ys:
        return str(code), np.zeros((0, 1), dtype=np.float32), np.zeros((0,), dtype=np.float32), [], []
    # 稳定列名：并集排序
    keys: List[str] = sorted({str(k) for row in xs for k in (row or {}).keys() if k})
    if not keys:
        return str(code), np.zeros((0, 1), dtype=np.float32), np.zeros((0,), dtype=np.float32), [], []
    mat = np.zeros((len(xs), len(keys)), dtype=np.float32)
    for i, row in enumerate(xs):
        for j, k in enumerate(keys):
            try:
                v = float((row or {}).get(k))
            except (TypeError, ValueError):
                v = float("nan")
            mat[i, j] = v if v == v else 0.0
    # y_r_pct 已是百分点，与序列 tc_open 标签同单位
    y = np.asarray([float(v) for v in ys], dtype=np.float32)
    ds = [str(d)[:10] for d in dates]
    return str(code), mat, y, keys, ds


def build_tc_factor_panel(
    stock_bars: Mapping[str, Sequence[dict]],
    *,
    progress_every: int = 25,
    workers: Optional[int] = None,
    use_cache: bool = True,
    cache_dir: Optional[str] = None,
) -> FactorPanel:
    """ŷ_τc（τ=开盘）因子面板：全量 z∪raw_atc。

    Arm·T 的 core 不在这里裁。
    """
    items = [
        (str(c).strip(), list(bars or []))
        for c, bars in stock_bars.items()
        if str(c).strip() and bars
    ]
    if not items:
        return FactorPanel(
            X=np.zeros((0, 1), dtype=np.float32),
            y=np.zeros((0,), dtype=np.float32),
            dates=[],
            codes=[],
            feature_names=[],
        )

    lookback_hint = max((len(b) for _, b in items), default=0)
    codes = [c for c, _ in items]
    cdir = cache_dir or _default_cache_dir()
    cache_path = ""
    if use_cache:
        key = _cache_key(
            codes,
            horizon_days=0,
            aoo_core_only=False,
            lookback_hint=lookback_hint,
        )
        cache_path = os.path.join(cdir, f"fac_tc_{key}.npz")
        hit = _try_load_cache(cache_path)
        if hit is not None and hit.n > 0:
            print(f"  τc 因子缓存命中 N={hit.n} F={hit.X.shape[1]} ← {cache_path}", flush=True)
            return hit

    n_cpu = os.cpu_count() or 2
    if workers is None:
        try:
            workers = int(os.environ.get("OO_FAC_WORKERS") or 2)
        except ValueError:
            workers = 2
    n_workers = max(1, min(int(workers), 4, len(items), n_cpu))
    print(
        f"  τc 因子面板 workers={n_workers} stocks={len(items)} pack=atc_open",
        flush=True,
    )
    t0 = time.time()
    jobs = [(c, b) for c, b in items]
    chunks = _run_stock_jobs(
        jobs,
        _tc_stock_job,
        n_workers=n_workers,
        progress_every=int(progress_every or 0),
        label="τc 因子",
    )

    names: List[str] = []
    for _c, x, _y, nm, _d in chunks:
        if x is None or getattr(x, "size", 0) == 0:
            continue
        if len(nm) > len(names):
            names = list(nm)
    if not names:
        return FactorPanel(
            X=np.zeros((0, 1), dtype=np.float32),
            y=np.zeros((0,), dtype=np.float32),
            dates=[],
            codes=[],
            feature_names=[],
        )

    xs_out: List[np.ndarray] = []
    ys_out: List[np.ndarray] = []
    dates_out: List[str] = []
    codes_out: List[str] = []
    name_idx = {n: i for i, n in enumerate(names)}
    for code, x, y, nm, ds in chunks:
        if x is None or x.size == 0 or not ds:
            continue
        aligned = np.zeros((x.shape[0], len(names)), dtype=np.float32)
        for j, n in enumerate(nm):
            dest = name_idx.get(n)
            if dest is None:
                continue
            aligned[:, dest] = x[:, j]
        xs_out.append(aligned)
        ys_out.append(np.asarray(y, dtype=np.float32))
        dates_out.extend(ds)
        codes_out.extend([code] * len(ds))
    if not xs_out:
        return FactorPanel(
            X=np.zeros((0, len(names)), dtype=np.float32),
            y=np.zeros((0,), dtype=np.float32),
            dates=[],
            codes=[],
            feature_names=names,
        )
    panel = FactorPanel(
        X=np.concatenate(xs_out, axis=0),
        y=np.concatenate(ys_out, axis=0),
        dates=dates_out,
        codes=codes_out,
        feature_names=names,
    )
    print(
        f"  τc 因子完成 N={panel.n} F={panel.X.shape[1]} {time.time()-t0:.1f}s",
        flush=True,
    )
    if use_cache and cache_path:
        _save_cache(cache_path, panel)
    return panel


def build_factor_panel(
    stock_bars: Mapping[str, Sequence[dict]],
    *,
    head: Any = "oo",
    horizon_days: int = 1,
    progress_every: int = 25,
    workers: Optional[int] = None,
    use_cache: bool = True,
    cache_dir: Optional[str] = None,
) -> FactorPanel:
    """按 HeadSpec / head id 构建因子面板。

    ``oo_rank`` / ``tc_rank`` 复用对应回归头因子包；排序在训练损失侧按日分组。
    """
    hid = str(getattr(head, "id", head) or "oo").strip().lower()
    if hid in ("tc", "tc_open", "y_tc", "tc_rank", "y_tc_rank"):
        return build_tc_factor_panel(
            stock_bars,
            progress_every=progress_every,
            workers=workers,
            use_cache=use_cache,
            cache_dir=cache_dir,
        )
    return build_oo_factor_panel(
        stock_bars,
        horizon_days=horizon_days,
        progress_every=progress_every,
        workers=workers,
        use_cache=use_cache,
        cache_dir=cache_dir,
    )


def align_seq_and_factor(
    seq: Any,
    fac: FactorPanel,
) -> Tuple[Any, FactorPanel]:
    """按 (code, date) 内连接。"""
    from core.research.yhat_transformer.seq_dataset import SeqPanel

    fac_map: Dict[Tuple[str, str], int] = {}
    for i, (c, d) in enumerate(zip(fac.codes, fac.dates)):
        fac_map[(str(c), str(d)[:10])] = i
    seq_keep: List[int] = []
    fac_keep: List[int] = []
    for i, (c, d) in enumerate(zip(seq.codes, seq.dates)):
        j = fac_map.get((str(c), str(d)[:10]))
        if j is None:
            continue
        seq_keep.append(i)
        fac_keep.append(j)
    if not seq_keep:
        empty_s = SeqPanel(
            X=np.zeros((0, seq.seq_len, seq.X.shape[-1]), dtype=np.float32),
            gap=np.zeros((0,), dtype=np.float32),
            y=np.zeros((0,), dtype=np.float32),
            dates=[],
            codes=[],
            seq_len=seq.seq_len,
        )
        empty_f = FactorPanel(
            X=np.zeros((0, max(1, fac.X.shape[-1])), dtype=np.float32),
            y=np.zeros((0,), dtype=np.float32),
            dates=[],
            codes=[],
            feature_names=list(fac.feature_names),
        )
        return empty_s, empty_f
    seq_out = seq.subset(seq_keep)
    fac_out = fac.subset(fac_keep)
    # 同 (code,date) 标签须一致；以序列 y 为准写回因子面板，避免双源漂移
    sy = np.asarray(seq_out.y, dtype=np.float64)
    fy = np.asarray(fac_out.y, dtype=np.float64)
    if sy.shape == fy.shape and sy.size:
        diff = np.nanmax(np.abs(sy - fy))
        if diff == diff and float(diff) > 1e-3:
            logger.warning(
                "align_seq_and_factor: seq/fac y 最大偏差=%.4g，以 seq.y 覆盖 fac.y",
                float(diff),
            )
        fac_out.y = np.asarray(seq_out.y, dtype=np.float32).copy()
    return seq_out, fac_out


__all__ = [
    "FactorPanel",
    "build_oo_factor_panel",
    "build_tc_factor_panel",
    "build_factor_panel",
    "require_factor_panel",
    "slice_arm_t_panel",
    "align_seq_and_factor",
]
