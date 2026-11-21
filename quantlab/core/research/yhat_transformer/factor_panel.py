"""决策日因子面板（研究轨）：带 code/date，便于与序列对齐。

ŷ_oo 走枢纽 ``assemble_oo_panels``，ŷ_τc 走枢纽 ``collect_tau_compact``。
可选磁盘缓存。
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
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


# 价量指纹进键。facv1 只含代码和最长根数，固定 lookback 会一直命中旧面板。
# facv3：oo 走枢纽 assemble_oo_panels（含 coverage）。
# tc_compact 是指纹抬到 62 后整段重算的短面板，丢掉了长湖前缀。
# tc_compact_v2：短窗改回只读切片，能留下长湖里已经按 62 根预热写好的决策日。
# tc_compact_v3：开盘面板丢掉分钟常数列（15 个 z_τc 分钟键 + 14 个 raw 前缀）。
_FACTOR_CACHE_SCHEMA = "facv3"
_TC_CACHE_KIND = "tc_compact_v3"


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


def _panel_is_aligned(panel: "FactorPanel") -> bool:
    """行、列、代码对齐才允许进训练或落缓存。空代码行无法和序列按 (code, date) 对齐。"""
    x = getattr(panel, "X", None)
    y = getattr(panel, "y", None)
    if x is None or y is None or getattr(x, "ndim", 0) != 2 or getattr(y, "ndim", 0) != 1:
        return False
    n = int(x.shape[0])
    if int(y.shape[0]) != n or len(panel.dates) != n or len(panel.codes) != n:
        return False
    names = list(panel.feature_names or [])
    if names and int(x.shape[1]) != len(names):
        return False
    return all(str(c).strip() for c in panel.codes)


def _try_load_cache(path: str) -> Optional["FactorPanel"]:
    if not path or not os.path.isfile(path):
        return None
    try:
        z = np.load(path, allow_pickle=True)
        panel = FactorPanel(
            X=np.asarray(z["X"], dtype=np.float32),
            y=np.asarray(z["y"], dtype=np.float32),
            dates=[str(d) for d in z["dates"].tolist()],
            codes=[str(c) for c in z["codes"].tolist()],
            feature_names=[str(n) for n in z["feature_names"].tolist()],
        )
    except Exception:  # noqa: BLE001
        logger.debug("factor panel cache load failed %s", path, exc_info=True)
        return None
    if not _panel_is_aligned(panel):
        logger.debug("factor panel cache misaligned %s", path)
        return None
    return panel


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
        width = int(panel.X.shape[1]) if getattr(panel.X, "ndim", 0) == 2 else 1
        return FactorPanel(
            X=np.zeros((0, max(1, width)), dtype=np.float32),
            y=np.zeros((0,), dtype=np.float32),
            dates=[],
            codes=[],
            feature_names=list(panel.feature_names),
        )
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

    这里始终交回 aoo_full。Arm·T 的 core 由调用方在开跑前裁；Arm·L 继续用这份全量。
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
        key = _cache_key(items, horizon_days=horizon_days, kind="oo_open_v2")
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
        f"  因子面板 assemble_oo_panels stocks={len(items)} 源=aoo_full",
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


def _codes_from_tau_metas(metas: Sequence[dict], n: int) -> List[str]:
    out: List[str] = []
    for i in range(n):
        meta = metas[i] if i < len(metas) and isinstance(metas[i], dict) else {}
        out.append(str(meta.get("stock_code") or meta.get("code") or "").strip())
    return out


def build_tc_factor_panel(
    stock_bars: Mapping[str, Sequence[dict]],
    *,
    progress_every: int = 25,
    workers: Optional[int] = None,
    use_cache: bool = True,
    cache_dir: Optional[str] = None,
) -> FactorPanel:
    """ŷ_τc（τ=开盘）因子面板：走枢纽 ``collect_tau_compact``。

    截面广度在组装里写完。开盘 full 已去掉分钟常数列。
    这里始终交回这份开盘 atc_full；Arm·T 的 core 由调用方在开跑前裁。
    传入的是条目指针的副本，避免组装过程 pop 掉调用方的日线。
    """
    from core.research.panel_matrix import collect_tau_compact
    from core.yhat.keys import TAU_Z_FEATURES

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
        key = _cache_key(items, horizon_days=0, kind=_TC_CACHE_KIND)
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

    every = max(0, int(progress_every or 0))

    def _prog(done: int, total: int, msg: str) -> None:
        if every <= 0:
            return
        if int(done) != int(total) and int(done) % every != 0:
            return
        print(f"  τc 因子 {done}/{total} {msg}", flush=True)

    print(
        f"  τc 因子面板 collect_tau_compact stocks={len(items)} 源=atc_full",
        flush=True,
    )
    t0 = time.time()
    # 新 dict + 新 list，条目指针仍指向原 K 线。pop bars 只清这份副本。
    stock_items = [{"code": code, "bars": bars} for code, bars in items]
    x_mat, names, ys, dates, metas, _n_stocks = collect_tau_compact(
        stock_items,
        list(TAU_Z_FEATURES),
        min_history=12,
        gap_trigger_pct=2.0,
        tau_hm="open",
        include_alpha_tc=True,
        head="y_tc",
        cache_head="tc",
        workers=workers,
        progress_cb=_prog,
    )
    names = [str(n) for n in (names or [])]
    n = int(len(ys))
    dates_l = [str(d)[:10] for d in (dates or [])]
    metas_l = list(metas or [])
    x_rows = int(getattr(x_mat, "shape", (0,))[0] or 0) if x_mat is not None else 0
    x_cols = int(getattr(x_mat, "shape", (0, 0))[1] or 0) if getattr(x_mat, "ndim", 0) == 2 else -1
    aligned = (
        n >= 1
        and x_mat is not None
        and getattr(x_mat, "ndim", 0) == 2
        and x_rows == n
        and len(dates_l) == n
        and len(metas_l) == n
        and (not names or x_cols == len(names))
    )
    if not aligned:
        print(
            f"  τc 因子组装对不齐 y={n} rows={x_rows} cols={x_cols} "
            f"dates={len(dates_l)} metas={len(metas_l)} names={len(names)}，放弃这次结果",
            flush=True,
        )
        return FactorPanel(
            X=np.zeros((0, max(1, len(names))), dtype=np.float32),
            y=np.zeros((0,), dtype=np.float32),
            dates=[],
            codes=[],
            feature_names=names,
        )
    codes = _codes_from_tau_metas(metas_l, n)
    blank = sum(1 for c in codes if not c)
    if blank:
        print(
            f"  τc 因子行缺少股票代码 {blank}/{n}，放弃这次结果",
            flush=True,
        )
        return FactorPanel(
            X=np.zeros((0, max(1, len(names))), dtype=np.float32),
            y=np.zeros((0,), dtype=np.float32),
            dates=[],
            codes=[],
            feature_names=names,
        )
    panel = reorder_panel_by_codes(
        FactorPanel(
            X=np.asarray(x_mat, dtype=np.float32),
            y=np.asarray(ys, dtype=np.float32),
            dates=dates_l,
            codes=codes,
            feature_names=names,
        ),
        item_codes,
    )
    if not _panel_is_aligned(panel) or not _cache_panel_matches_pool(panel, item_codes):
        print(
            f"  τc 因子重排后不能入池 N={panel.n}，放弃这次结果",
            flush=True,
        )
        return FactorPanel(
            X=np.zeros((0, max(1, len(names))), dtype=np.float32),
            y=np.zeros((0,), dtype=np.float32),
            dates=[],
            codes=[],
            feature_names=names,
        )
    print(
        f"  τc 因子完成 N={panel.n} F={panel.X.shape[1]} "
        f"{time.time()-t0:.1f}s 代码序",
        flush=True,
    )
    if use_cache and cache_path and panel.n > 0:
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


def _trading_index(bars: Sequence[dict]) -> Dict[str, int]:
    """按日期去重后的下标。停牌缺日不会占位，相邻两根的下标差就是间隔的交易日数。"""
    found: List[str] = []
    seen = set()
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        day = str(bar.get("date") or bar.get("trade_date") or "")[:10]
        if len(day) < 10 or day in seen:
            continue
        seen.add(day)
        found.append(day)
    found.sort()
    return {day: i for i, day in enumerate(found)}


def sequence_from_factor_panel(
    panel: FactorPanel,
    stock_bars: Mapping[str, Sequence[dict]],
    *,
    seq_len: int = 20,
    with_age: bool = True,
) -> Any:
    """决策日 T 的序列是同一只股票的因子行 ``[T-L … T-1]``，列与这份面板一致。

    列宽就是因子 core / full 裁完后的宽：oo 65 / 222，开盘 τc 61 / 218。
    ``with_age`` 时再附一列 ``age_td`` = 该行距决策日的交易日数 / L。
    gap 仍是今开/昨收 − 1，不进这 L 列。非有限值留着，训练时用训练列中位数填。
    """
    from core.research.yhat_transformer.seq_dataset import SeqPanel, gaps_by_decision

    require_factor_panel(panel)
    L = max(5, int(seq_len or 20))
    F = int(panel.X.shape[1])
    names = tuple(str(n) for n in panel.feature_names)
    if with_age:
        names = names + ("age_td",)
    gaps = gaps_by_decision(stock_bars)
    age_index = {
        str(code): _trading_index(bars) for code, bars in (stock_bars or {}).items()
    }
    by_code: Dict[str, List[int]] = {}
    for i, code in enumerate(panel.codes):
        by_code.setdefault(str(code), []).append(i)

    plans: List[Tuple[str, int, List[int]]] = []
    for code, idxs in by_code.items():
        idxs.sort(key=lambda i: str(panel.dates[i])[:10])
        dedup: List[int] = []
        seen_day = ""
        for i in idxs:
            day = str(panel.dates[i])[:10]
            if day == seen_day and dedup:
                dedup[-1] = i
            else:
                dedup.append(i)
                seen_day = day
        for k in range(L, len(dedup)):
            decision = str(panel.dates[dedup[k]])[:10]
            window = dedup[k - L : k]
            if any(str(panel.dates[j])[:10] >= decision for j in window):
                continue
            if (code, decision) not in gaps:
                continue
            yv = float(panel.y[dedup[k]])
            if not np.isfinite(yv):
                continue
            plans.append((code, dedup[k], window))

    n = len(plans)
    width = F + (1 if with_age else 0)
    X = np.zeros((n, L, width), dtype=np.float32)
    gap = np.zeros((n,), dtype=np.float32)
    y = np.zeros((n,), dtype=np.float32)
    dates: List[str] = []
    codes: List[str] = []
    src = panel.X
    for row, (code, dest, window) in enumerate(plans):
        block = np.array(src[window], dtype=np.float32, copy=True)
        decision = str(panel.dates[dest])[:10]
        if with_age:
            aged = np.empty((L, width), dtype=np.float32)
            aged[:, :F] = block
            index = age_index.get(code) or {}
            i_dec = index.get(decision)
            for step, src_i in enumerate(window):
                token_day = str(panel.dates[src_i])[:10]
                i_tok = index.get(token_day)
                if i_dec is None or i_tok is None or i_dec <= i_tok:
                    age = float(L - step) / float(L)
                else:
                    age = float(i_dec - i_tok) / float(L)
                aged[step, F] = np.float32(age)
            X[row] = aged
        else:
            X[row] = block
        gap[row] = np.float32(gaps[(code, decision)])
        y[row] = np.float32(panel.y[dest])
        dates.append(decision)
        codes.append(code)
    return SeqPanel(
        X=X,
        gap=gap,
        y=y,
        dates=dates,
        codes=codes,
        seq_len=L,
        feature_names=names,
    )


__all__ = [
    "FactorPanel",
    "build_oo_factor_panel",
    "build_tc_factor_panel",
    "build_factor_panel",
    "require_factor_panel",
    "slice_arm_t_panel",
    "align_seq_and_factor",
    "sequence_from_factor_panel",
]
