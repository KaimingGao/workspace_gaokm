"""面板尾窗增量：仅末根（或末几根）变化时，用 keep_dates 重算并 merge。

典型路径：日线 topup 只追加 1 日 → stem_fp 不变 → 只重算尾部决策日。
中间复权改写会使 stem miss → 全量重算（与 invalidate_l1 一致）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# 尾窗决策日重算上界（α_oo/α_τc 窗 + 余量）
DEFAULT_TAIL_PAD = 80


def stem_bars_fingerprint(bars: Sequence[dict]) -> str:
    """去掉最后一根后的 bars 指纹；长度 ≤1 时为 empty stem。"""
    from core.research.panel_fingerprint import bars_fingerprint

    seq = [b for b in (bars or []) if isinstance(b, dict)]
    if len(seq) <= 1:
        return bars_fingerprint([])
    return bars_fingerprint(seq[:-1])


def bar_end_date(bars: Sequence[dict]) -> str:
    seq = [b for b in (bars or []) if isinstance(b, dict)]
    if not seq:
        return ""
    return str(seq[-1].get("date") or seq[-1].get("trade_date") or "")[:10]


def load_l1_any(family: str, code: str) -> Optional[Dict[str, Any]]:
    """忽略 fingerprint，读 L1 整票（含 ``_meta`` 的 stem_fp / bars_fp）。"""
    try:
        from core.research.feature_lake import enabled, load_stock_panel

        if not enabled() or not code or not family:
            return None
        pack = load_stock_panel(family, code, fingerprint=None)
        if pack is None or pack.get("X") is None:
            return None
        return pack
    except Exception:  # noqa: BLE001
        logger.debug("load_l1_any failed code=%s family=%s", code, family, exc_info=True)
        return None


def is_pure_tail_append(bars: Sequence[dict], meta: Optional[Dict[str, Any]]) -> bool:
    """仅末根变化：追加一日（prefix=旧全量）或改写末根（prefix=旧 stem）。"""
    return tail_append_days(bars, meta) is not None


def tail_append_days(
    bars: Sequence[dict],
    meta: Optional[Dict[str, Any]],
    *,
    max_new: int = 16,
) -> Optional[int]:
    """若新 bars 相对旧全量只是尾部追加（或仅改末根），返回新增根数（改末根=0）。

    多日 topup：``bars_fingerprint(bars[:-k]) == meta.bars_fp`` 时返回 k。
    找不到则 None（需全量重算）。
    """
    if not isinstance(meta, dict):
        return None
    from core.research.panel_fingerprint import bars_fingerprint

    old_full = str(meta.get("bars_fp") or "")
    old_stem = str(meta.get("stem_fp") or "")
    seq = [b for b in (bars or []) if isinstance(b, dict)]
    if not seq:
        return None
    # 同长度：只改末根
    if old_stem and stem_bars_fingerprint(seq) == old_stem:
        return 0
    if not old_full:
        return None
    # 追加 k 根：prefix = bars[:-k] 等于旧全量
    limit = min(max(1, int(max_new)), max(0, len(seq) - 1))
    for k in range(1, limit + 1):
        prefix = seq[:-k]
        if not prefix:
            break
        if bars_fingerprint(prefix) == old_full:
            return k
    return None


def tail_keep_dates(
    old_dates: Sequence[str],
    bars: Sequence[dict],
    *,
    pad: int = DEFAULT_TAIL_PAD,
) -> List[str]:
    """增量重算的决策日集合：旧末日之前 pad 根 bar 日起 → 新末。"""
    old_last = str(old_dates[-1])[:10] if old_dates else ""
    bar_dates = [
        str(b.get("date") or b.get("trade_date") or "")[:10]
        for b in (bars or [])
        if isinstance(b, dict)
    ]
    bar_dates = [d for d in bar_dates if len(d) >= 10]
    if not bar_dates:
        return []
    # 从旧末日往前数 pad 个交易日作为起点（没有旧末日则整段）
    if old_last and old_last in bar_dates:
        idx = bar_dates.index(old_last)
        start = max(0, idx - max(1, int(pad)))
    else:
        start = max(0, len(bar_dates) - max(1, int(pad)))
    return list(dict.fromkeys(bar_dates[start:]))


def merge_matrix_rows(
    old_X: np.ndarray,
    old_y: np.ndarray,
    old_dates: Sequence[str],
    old_names: Sequence[str],
    new_X: np.ndarray,
    new_y: np.ndarray,
    new_dates: Sequence[str],
    new_names: Sequence[str],
    *,
    keep_from: str,
) -> Tuple[np.ndarray, np.ndarray, List[str], List[str]]:
    """保留 ``date < keep_from`` 的旧行，再拼新块（按日去重，新覆盖旧）。"""
    cut = str(keep_from or "")[:10]
    name_list = [str(n) for n in new_names] if new_names else [str(n) for n in old_names]
    old_name_list = [str(n) for n in old_names]

    def _align(X: np.ndarray, names_src: Sequence[str]) -> np.ndarray:
        x = np.asarray(X, dtype=np.float64)
        if list(names_src) == name_list:
            return x
        out = np.full((x.shape[0], len(name_list)), np.nan, dtype=np.float64)
        idx = {n: i for i, n in enumerate(names_src)}
        for j, n in enumerate(name_list):
            i = idx.get(n)
            if i is not None and i < x.shape[1]:
                out[:, j] = x[:, i]
        return out

    by_x: Dict[str, np.ndarray] = {}
    by_y: Dict[str, float] = {}
    ox = _align(old_X, old_name_list)
    for i, d in enumerate(old_dates):
        ds = str(d)[:10]
        if cut and ds >= cut:
            continue
        if i < ox.shape[0]:
            by_x[ds] = ox[i]
            by_y[ds] = float(np.asarray(old_y)[i])
    nx = _align(new_X, new_names)
    for i, d in enumerate(new_dates):
        ds = str(d)[:10]
        if i < nx.shape[0]:
            by_x[ds] = nx[i]
            by_y[ds] = float(np.asarray(new_y)[i])
    ordered = sorted(by_x.keys())
    if not ordered:
        return (
            np.zeros((0, len(name_list)), dtype=np.float64),
            np.zeros((0,), dtype=np.float64),
            name_list,
            [],
        )
    X_m = np.vstack([by_x[d] for d in ordered])
    y_m = np.asarray([by_y[d] for d in ordered], dtype=np.float64)
    return X_m, y_m, name_list, ordered


def stem_extra_meta(bars: Sequence[dict]) -> Dict[str, str]:
    """写入 bars_fp / stem_fp / end_date，供下次增量判断。"""
    from core.research.panel_fingerprint import bars_fingerprint

    return {
        "bars_fp": bars_fingerprint(bars),
        "stem_fp": stem_bars_fingerprint(bars),
        "end_date": bar_end_date(bars),
    }


def try_incremental_merge(
    code: str,
    bars: Sequence[dict],
    *,
    pad: int,
    collect_tail,  # Callable[..., Tuple[X,y,names,dates]]
    family: str,
    max_new: int = 16,
) -> Optional[Tuple[np.ndarray, np.ndarray, List[str], List[str]]]:
    """尾部追加/改末根时调用 ``collect_tail(keep_dates)`` 并与 L1 旧矩阵 merge；否则 None。"""
    if not family:
        return None
    prev = load_l1_any(family, code)
    if prev is None or prev.get("X") is None:
        return None
    if prev.get("y") is None:
        return None
    n_new = tail_append_days(bars, prev.get("_meta"), max_new=max_new)
    if n_new is None:
        return None
    old_dates = list(prev.get("dates") or [])
    # 新增越多，尾窗 pad 略放大
    eff_pad = int(pad) + max(0, int(n_new) - 1) * 2
    keep = tail_keep_dates(old_dates, bars, pad=eff_pad)
    if not keep:
        return None
    try:
        nx, ny, nnames, ndates = collect_tail(keep)
    except Exception:  # noqa: BLE001
        logger.debug("incremental collect_tail failed code=%s", code, exc_info=True)
        return None
    if nx is None or int(getattr(nx, "shape", (0,))[0] or 0) <= 0:
        # 尾窗无新决策日：直接返回旧矩阵（仍会用新 fp 重写）
        return (
            np.asarray(prev["X"], dtype=np.float64),
            np.asarray(prev["y"], dtype=np.float64),
            list(prev.get("names") or []),
            old_dates,
        )
    keep_from = min(keep)
    return merge_matrix_rows(
        np.asarray(prev["X"], dtype=np.float64),
        np.asarray(prev["y"], dtype=np.float64),
        old_dates,
        list(prev.get("names") or []),
        nx,
        ny,
        ndates,
        nnames,
        keep_from=keep_from,
    )
