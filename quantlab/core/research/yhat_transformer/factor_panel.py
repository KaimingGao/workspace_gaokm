"""决策日因子面板（研究轨）：z_oo_* ∪ raw_aoo_*，带 code/date 便于与序列对齐。

加速：ProcessPool 并行（复用 ``_oo_stock_job`` / L1 湖）；可选磁盘缓存。
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)


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
    job: Tuple[str, List[dict], int, bool],
) -> Tuple[str, np.ndarray, np.ndarray, List[str], List[str]]:
    """子进程：单票矩阵（优先 L1 湖，否则 collect）。"""
    code, bars, horizon, ridge_core_only = job
    from core.research.oo_model_compact import _oo_stock_job

    x, y, names, dates = _oo_stock_job(
        (str(code), list(bars or []), int(horizon), bool(ridge_core_only))
    )
    return str(code), x, y, list(names), [str(d)[:10] for d in dates]


def _cache_key(
    codes: Sequence[str],
    *,
    horizon_days: int,
    ridge_core_only: bool,
    lookback_hint: int,
) -> str:
    h = hashlib.sha1()
    h.update(f"h{horizon_days}|core{int(ridge_core_only)}|lb{lookback_hint}|".encode())
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


def _select_core_columns(panel: "FactorPanel") -> "FactorPanel":
    """从全量 L1 列裁到 aoo_core（研究消融）；列名对不上则原样返回。"""
    from core.research.oo_model_compact import OoPanelMatrix

    want = list(OoPanelMatrix(ridge_core_only=True).names)
    name_j = {n: i for i, n in enumerate(panel.feature_names)}
    cols = [name_j[n] for n in want if n in name_j]
    if len(cols) < max(8, len(want) // 2):
        return panel
    kept_names = [panel.feature_names[j] for j in cols]
    return FactorPanel(
        X=panel.X[:, cols],
        y=panel.y,
        dates=list(panel.dates),
        codes=list(panel.codes),
        feature_names=kept_names,
    )


def build_oo_factor_panel(
    stock_bars: Mapping[str, Sequence[dict]],
    *,
    horizon_days: int = 1,
    ridge_core_only: bool = True,
    progress_every: int = 25,
    workers: Optional[int] = None,
    use_cache: bool = True,
    cache_dir: Optional[str] = None,
) -> FactorPanel:
    """并行堆叠因子面板；**拉数指纹对齐生产 L1（全量 raw_aoo）** 以便命中湖。

    ``ridge_core_only=True`` 时仍先读全量 L1，再裁 aoo_core 列——避免 core 指纹
    与湖不一致导致全量重算。
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
    # 缓存键按「是否最终 core」区分；底层拉取恒用全量指纹
    if use_cache:
        key = _cache_key(
            codes,
            horizon_days=horizon_days,
            ridge_core_only=ridge_core_only,
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

    # 与生产 L1 指纹一致：ridge_core_only=False
    jobs = [
        (code, bars, int(horizon_days), False)
        for code, bars in items
    ]
    print(
        f"  因子面板并行 workers={n_workers} stocks={len(jobs)} "
        f"lake_fp=full_aoo slice_core={bool(ridge_core_only)}",
        flush=True,
    )
    t0 = time.time()

    chunks: List[Tuple[str, np.ndarray, np.ndarray, List[str], List[str]]] = []
    done = 0
    if n_workers <= 1:
        for job in jobs:
            try:
                chunks.append(_factor_stock_job(job))
            except Exception:  # noqa: BLE001
                logger.debug("factor job failed %s", job[0], exc_info=True)
            done += 1
            if progress_every and done % int(progress_every) == 0:
                print(f"  因子面板 {done}/{len(jobs)}", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            futs = {pool.submit(_factor_stock_job, job): job[0] for job in jobs}
            for fut in as_completed(futs):
                code = futs[fut]
                done += 1
                try:
                    chunks.append(fut.result())
                except Exception:  # noqa: BLE001
                    logger.debug("factor job failed %s", code, exc_info=True)
                if progress_every and done % int(progress_every) == 0:
                    print(
                        f"  因子面板 {done}/{len(jobs)} elapsed={time.time()-t0:.0f}s",
                        flush=True,
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

        names = list(OoPanelMatrix(ridge_core_only=bool(ridge_core_only)).names)

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
    if ridge_core_only:
        before = panel.X.shape[1]
        panel = _select_core_columns(panel)
        print(
            f"  裁 aoo_core 列 {before}→{panel.X.shape[1]}",
            flush=True,
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
    """ŷ_τc（τ=开盘）因子面板：alpha_τc ≈ z∪raw_atc（日线开盘包）。"""
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
            ridge_core_only=False,
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
    chunks: List[Tuple[str, np.ndarray, np.ndarray, List[str], List[str]]] = []
    done = 0
    jobs = [(c, b) for c, b in items]
    if n_workers <= 1:
        for job in jobs:
            try:
                chunks.append(_tc_stock_job(job))
            except Exception:  # noqa: BLE001
                logger.debug("tc factor job failed %s", job[0], exc_info=True)
            done += 1
            if progress_every and done % int(progress_every) == 0:
                print(f"  τc 因子 {done}/{len(jobs)}", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            futs = {pool.submit(_tc_stock_job, job): job[0] for job in jobs}
            for fut in as_completed(futs):
                code = futs[fut]
                done += 1
                try:
                    chunks.append(fut.result())
                except Exception:  # noqa: BLE001
                    logger.debug("tc factor job failed %s", code, exc_info=True)
                if progress_every and done % int(progress_every) == 0:
                    print(
                        f"  τc 因子 {done}/{len(jobs)} elapsed={time.time()-t0:.0f}s",
                        flush=True,
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
    ridge_core_only: bool = True,
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
        ridge_core_only=ridge_core_only,
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
    return seq.subset(seq_keep), fac.subset(fac_keep)


def column_standardize_train(
    X: np.ndarray,
    train_idx: Sequence[int],
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Train 列 μ/σ；返回 (Xz, mu, sd)。"""
    X = np.asarray(X, dtype=np.float32)
    tr = np.asarray(list(train_idx), dtype=np.int64)
    mu = np.nanmean(X[tr], axis=0)
    sd = np.nanstd(X[tr], axis=0)
    sd = np.where(sd > 1e-6, sd, 1.0).astype(np.float32)
    mu = np.where(np.isfinite(mu), mu, 0.0).astype(np.float32)
    Xz = (X - mu) / sd
    Xz = np.where(np.isfinite(Xz), Xz, 0.0).astype(np.float32)
    return Xz, mu, sd


__all__ = [
    "FactorPanel",
    "build_oo_factor_panel",
    "build_tc_factor_panel",
    "build_factor_panel",
    "align_seq_and_factor",
    "column_standardize_train",
]
