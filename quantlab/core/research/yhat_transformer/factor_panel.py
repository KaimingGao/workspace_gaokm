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
) -> Tuple[List[Any], List[str]]:
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
    return chunks, failed


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


# 价量指纹进键。facv1 只含代码和最长根数，固定 lookback 会一直命中旧面板。
# facv3：oo 走枢纽 assemble_oo_panels（含 coverage），作废完成序/旧组面板缓存。
_FACTOR_CACHE_SCHEMA = "facv3"


def _cache_key(
    items: Sequence[Tuple[str, Sequence[dict]]],
    *,
    horizon_days: int,
    kind: str,
) -> str:
    """配方版本 + 每只股票的价量指纹。长度不变但价格修订时必须 miss。"""
    from core.research.panel_fingerprint import bars_fingerprint

    h = hashlib.sha1()
    h.update(f"{_FACTOR_CACHE_SCHEMA}|{kind}|h{int(horizon_days)}|".encode())
    rows = [
        (str(code).strip(), bars_fingerprint(list(bars or [])))
        for code, bars in items
        if str(code).strip()
    ]
    for code, fp in sorted(rows):
        h.update(code.encode())
        h.update(b"|")
        h.update(fp.encode())
        h.update(b"\n")
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


def reorder_panel_by_codes(
    panel: "FactorPanel",
    codes: Sequence[str],
) -> "FactorPanel":
    """按入池代码顺序重排行，票内按日期。对齐枢纽 ``assemble_oo_panels``。

    只保留 ``codes`` 里的票；入池外残留票丢掉（勿静默拼回）。
    """
    want = [str(c).strip() for c in codes if str(c).strip()]
    if panel is None or panel.n <= 0 or not want:
        return panel
    by: Dict[str, List[int]] = {}
    for i, c in enumerate(panel.codes):
        by.setdefault(str(c), []).append(int(i))
    idx: List[int] = []
    for c in want:
        rows = by.pop(c, None)
        if not rows:
            continue
        rows.sort(key=lambda i: str(panel.dates[i])[:10])
        idx.extend(rows)
    if not idx:
        return panel
    if len(idx) == panel.n and idx == list(range(panel.n)):
        return panel
    ii = np.asarray(idx, dtype=np.int64)
    return FactorPanel(
        X=np.ascontiguousarray(panel.X[ii], dtype=np.float32),
        y=np.asarray(panel.y, dtype=np.float32)[ii],
        dates=[str(panel.dates[int(i)])[:10] for i in ii.tolist()],
        codes=[str(panel.codes[int(i)]) for i in ii.tolist()],
        feature_names=list(panel.feature_names),
    )


def _cache_panel_matches_pool(
    panel: "FactorPanel",
    item_codes: Sequence[str],
) -> bool:
    """缓存可用：无入池外残留票。缺票（短历史 0 行）允许，与当场 assemble 一致。"""
    want = {str(c).strip() for c in item_codes if str(c).strip()}
    have = {str(c).strip() for c in (panel.codes or []) if str(c).strip()}
    if not have:
        return False
    return not bool(have - want)


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
    """全量因子面板：走枢纽 ``assemble_oo_panels``（代码序 + coverage）。

    Arm·T 的 core 不在这里裁，等 Arm·L 结束再切。
    """
    from core.research.oo_model_compact import assemble_oo_panels

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

    item_codes = [str(c) for c, _ in items]
    cdir = cache_dir or _default_cache_dir()
    cache_path = ""
    if use_cache:
        key = _cache_key(items, horizon_days=horizon_days, kind="oo")
        cache_path = os.path.join(cdir, f"fac_{key}.npz")
        hit = _try_load_cache(cache_path)
        if hit is not None and hit.n > 0:
            if not _cache_panel_matches_pool(hit, item_codes):
                print(
                    f"  因子面板缓存作废（票池不一致）N={hit.n} ← {cache_path}",
                    flush=True,
                )
            else:
                hit = reorder_panel_by_codes(hit, item_codes)
                print(
                    f"  因子面板缓存命中 N={hit.n} F={hit.X.shape[1]} "
                    f"assemble←hub 代码序 ← {cache_path}",
                    flush=True,
                )
                return hit

    # 与枢纽 fit_watching_return_model 同口：默认 OO_RIDGE_WORKERS；OO_FAC_WORKERS 可覆盖。
    n_workers = workers
    if n_workers is None:
        raw = (os.environ.get("OO_FAC_WORKERS") or "").strip()
        if raw:
            try:
                n_workers = int(raw)
            except ValueError:
                n_workers = None

    every = max(0, int(progress_every or 0))
    done_box = {"n": 0}

    def _prog(done: int, total: int, msg: str) -> None:
        if every <= 0:
            return
        if done != total and done % every != 0:
            return
        print(f"  因子面板 {done}/{total} {msg}", flush=True)

    print(
        f"  因子面板 assemble_oo_panels stocks={len(items)} lake_fp=full_aoo",
        flush=True,
    )
    t0 = time.time()
    x_mat, y_mat, names, dates, codes = assemble_oo_panels(
        items,
        horizon_days=int(horizon_days),
        workers=n_workers,
        aoo_core_only=False,
        progress_cb=_prog,
        return_codes=True,
    )
    del done_box
    names = [str(n) for n in names]
    if x_mat is None or int(getattr(x_mat, "shape", [0])[0] or 0) <= 0:
        return FactorPanel(
            X=np.zeros((0, max(1, len(names))), dtype=np.float32),
            y=np.zeros((0,), dtype=np.float32),
            dates=[],
            codes=[],
            feature_names=names,
        )

    # 枢纽 assemble 已把 float32 溢出收回 NaN。缺测留到 CS-z 之后再写成 0。
    panel = FactorPanel(
        X=np.ascontiguousarray(x_mat, dtype=np.float32),
        y=np.asarray(y_mat, dtype=np.float32),
        dates=[str(d)[:10] for d in dates],
        codes=[str(c) for c in codes],
        feature_names=names,
    )
    print(
        f"  因子面板完成 N={panel.n} F={panel.X.shape[1]} "
        f"{time.time()-t0:.1f}s assemble_oo 代码序",
        flush=True,
    )
    if use_cache and cache_path and panel.n > 0:
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
    mat = np.full((len(xs), len(keys)), np.nan, dtype=np.float32)
    for i, row in enumerate(xs):
        for j, k in enumerate(keys):
            try:
                v = float((row or {}).get(k))
            except (TypeError, ValueError):
                v = float("nan")
            if v == v:
                mat[i, j] = v
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

    item_codes = [str(c) for c, _ in items]
    cdir = cache_dir or _default_cache_dir()
    cache_path = ""
    if use_cache:
        key = _cache_key(items, horizon_days=0, kind="tc")
        cache_path = os.path.join(cdir, f"fac_tc_{key}.npz")
        hit = _try_load_cache(cache_path)
        if hit is not None and hit.n > 0:
            if not _cache_panel_matches_pool(hit, item_codes):
                print(
                    f"  τc 因子缓存作废（票池不一致）N={hit.n} ← {cache_path}",
                    flush=True,
                )
            else:
                hit = reorder_panel_by_codes(hit, item_codes)
                print(
                    f"  τc 因子缓存命中 N={hit.n} F={hit.X.shape[1]} "
                    f"代码序 ← {cache_path}",
                    flush=True,
                )
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
    chunks, failed = _run_stock_jobs(
        jobs,
        _tc_stock_job,
        n_workers=n_workers,
        progress_every=int(progress_every or 0),
        label="τc 因子",
    )

    by_code: Dict[str, Tuple[np.ndarray, np.ndarray, List[str], List[str]]] = {}
    for code, x, y, nm, ds in chunks:
        by_code[str(code)] = (x, y, list(nm), list(ds))

    names: List[str] = []
    for code, _bars in items:
        part = by_code.get(str(code))
        if part is None:
            continue
        _x, _y, nm, _d = part
        if _x is None or getattr(_x, "size", 0) == 0:
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
    for code, _bars in items:
        part = by_code.get(str(code))
        if part is None:
            continue
        x, y, nm, ds = part
        if x is None or getattr(x, "size", 0) == 0 or not ds:
            continue
        aligned = np.full((x.shape[0], len(names)), np.nan, dtype=np.float32)
        for j, n in enumerate(nm):
            dest = name_idx.get(n)
            if dest is None:
                continue
            aligned[:, dest] = x[:, j]
        xs_out.append(aligned)
        ys_out.append(np.asarray(y, dtype=np.float32))
        dates_out.extend(ds)
        codes_out.extend([str(code)] * len(ds))
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
        f"  τc 因子完成 N={panel.n} F={panel.X.shape[1]} "
        f"{time.time()-t0:.1f}s 代码序",
        flush=True,
    )
    if use_cache and cache_path and not failed and panel.n > 0:
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
