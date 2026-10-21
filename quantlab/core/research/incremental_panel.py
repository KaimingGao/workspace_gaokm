"""面板尾窗 / 历史前缀增量：末根变化修尾；更长 lookback 只补更早日期。

典型路径：日线 topup 只追加 1 日 → stem_fp 不变 → 只重算尾部决策日。
短窗湖 + 更长 bars 且旧窗是新 bars 的后缀 → 只重算更早决策日并 merge。
中间复权改写会使 stem miss → 全量重算（与 invalidate_l1 一致）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

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
    keep_from: str = "",
) -> Tuple[np.ndarray, np.ndarray, List[str], List[str]]:
    """按日 merge：新块覆盖旧行。

    ``keep_from`` 非空时只保留 ``date < keep_from`` 的旧行（尾窗追加）；
    为空则旧行全留，再被新块按日覆盖（历史前缀扩展）。
    """
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


def history_extend_prefix_bars(
    bars: Sequence[dict],
    meta: Optional[Dict[str, Any]],
) -> Optional[int]:
    """旧 ``bars_fp`` 是新 bars 的真后缀时，返回前面多出的根数；否则 None。"""
    if not isinstance(meta, dict):
        return None
    old_full = str(meta.get("bars_fp") or "")
    if not old_full:
        return None
    end = str(meta.get("end_date") or "")[:10]
    if end and bar_end_date(bars) != end:
        return None
    hints: List[int] = []
    try:
        n_bars = int(meta.get("n_bars") or 0)
    except (TypeError, ValueError):
        n_bars = 0
    if n_bars > 0:
        hints.append(n_bars)
        for pad in (1, 2, 5, 10, 20, 40, 61, 80, 100, 120):
            hints.append(n_bars + pad)
    from core.research.panel_fingerprint import find_bars_suffix_start

    return find_bars_suffix_start(bars, old_full, hint_lens=hints)


def history_keep_dates(
    old_dates: Sequence[str],
    bars: Sequence[dict],
    *,
    pad: int = DEFAULT_TAIL_PAD,
) -> List[str]:
    """历史扩展要重算的决策日：新前缀 + 切入旧窗的 pad 根。"""
    old_first = str(old_dates[0])[:10] if old_dates else ""
    bar_dates = [
        str(b.get("date") or b.get("trade_date") or "")[:10]
        for b in (bars or [])
        if isinstance(b, dict)
    ]
    bar_dates = [d for d in bar_dates if len(d) >= 10]
    if not bar_dates:
        return []
    if old_first and old_first in bar_dates:
        idx = bar_dates.index(old_first)
        end = min(len(bar_dates), idx + max(1, int(pad)) + 1)
        return list(dict.fromkeys(bar_dates[:end]))
    return list(dict.fromkeys(bar_dates))


def lake_row_covers_bar_end(
    dates: Sequence[str],
    bars: Sequence[dict],
    meta: Optional[Dict[str, Any]] = None,
) -> bool:
    """末根决策日已在面板里，或这份 bars 已经重算过、该日仍然没有特征行。"""
    end = bar_end_date(bars)
    if len(end) < 10:
        return True
    for raw in dates or []:
        if str(raw)[:10] == end:
            return True
    return str((meta or {}).get("scored_through") or "")[:10] == end


def stem_extra_meta(bars: Sequence[dict]) -> Dict[str, Any]:
    """写入 bars_fp / stem_fp / end_date / scored_through / n_bars，供下次增量判断。"""
    from core.research.panel_fingerprint import bars_fingerprint

    seq = [b for b in (bars or []) if isinstance(b, dict)]
    end = bar_end_date(seq)
    return {
        "bars_fp": bars_fingerprint(seq),
        "stem_fp": stem_bars_fingerprint(seq),
        "end_date": end,
        "scored_through": end,
        "n_bars": int(len(seq)),
    }


def _offline_daily_bars(code: str, limit: int) -> List[dict]:
    """只读本地日线，供短窗相对长窗 L1 的后缀校验。"""
    try:
        from core.data.service import get_research_service

        bars, _ = get_research_service().bars_and_source(
            str(code),
            limit=max(2, int(limit)),
            offline_ok=True,
            offline_only=True,
        )
    except Exception:  # noqa: BLE001
        logger.debug("offline daily bars failed code=%s", code, exc_info=True)
        return []
    return [dict(b) for b in (bars or []) if isinstance(b, dict)]


def try_slice_shorter_lookback(
    code: str,
    bars: Sequence[dict],
    *,
    family: str,
    spec_fp: Callable[[str], str],
) -> Optional[Tuple[np.ndarray, np.ndarray, List[str], List[str]]]:
    """物化长窗、拟合短窗：当前 bars 是湖序列真后缀时，切 L1 重叠决策日。

    只读切片，不重算。口径（horizon / 钟等）须与湖一致。
    """
    if not family or not code:
        return None
    prev = load_l1_any(family, code)
    if prev is None or prev.get("X") is None or prev.get("y") is None:
        return None
    old_x = np.asarray(prev["X"], dtype=np.float64)
    old_y = np.asarray(prev["y"], dtype=np.float64)
    old_dates = [str(d)[:10] for d in (prev.get("dates") or [])]
    if (
        old_x.ndim != 2
        or old_y.ndim != 1
        or old_x.shape[0] != old_y.shape[0]
        or len(old_dates) != old_x.shape[0]
    ):
        return None
    meta = prev.get("_meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    stored_fp = str(meta.get("fingerprint") or "")
    stored_bars = str(meta.get("bars_fp") or "")
    if not stored_fp or not stored_bars:
        return None
    try:
        fresh = spec_fp(stored_bars)
    except Exception:  # noqa: BLE001
        logger.debug("slice lookback spec_fp failed code=%s", code, exc_info=True)
        return None
    if fresh != stored_fp:
        return None
    seq = [b for b in (bars or []) if isinstance(b, dict)]
    if len(seq) < 4:
        return None
    try:
        n_old = int(meta.get("n_bars") or 0)
    except (TypeError, ValueError):
        n_old = 0
    if n_old <= len(seq):
        return None
    if bar_end_date(seq) != str(meta.get("end_date") or "")[:10]:
        return None
    from core.research.panel_fingerprint import bars_fingerprint

    full = _offline_daily_bars(code, n_old)
    if len(full) < len(seq) or bars_fingerprint(full) != stored_bars:
        return None
    if bars_fingerprint(full[-len(seq) :]) != bars_fingerprint(seq):
        return None
    bar_days = {
        str(b.get("date") or b.get("trade_date") or "")[:10]
        for b in seq
        if len(str(b.get("date") or b.get("trade_date") or "")[:10]) >= 10
    }
    idxs = [i for i, d in enumerate(old_dates) if d in bar_days]
    if len(idxs) < 4:
        return None
    names = [str(n) for n in (prev.get("names") or [])]
    logger.debug(
        "l1 lookback slice family=%s code=%s lake_rows=%s keep=%s",
        family,
        code,
        len(old_dates),
        len(idxs),
    )
    return (
        old_x[np.asarray(idxs, dtype=np.int64)],
        old_y[np.asarray(idxs, dtype=np.int64)],
        names,
        [old_dates[i] for i in idxs],
    )


def try_incremental_merge(
    code: str,
    bars: Sequence[dict],
    *,
    pad: int,
    collect_tail,  # Callable[..., Tuple[X,y,names,dates]]
    family: str,
    max_new: int = 16,
    spec_fp: Callable[[str], str],
) -> Optional[Tuple[np.ndarray, np.ndarray, List[str], List[str]]]:
    """尾部追加或历史前缀扩展时 ``collect_tail(keep_dates)`` 并与 L1 旧矩阵 merge；否则 None。

    ``spec_fp`` 接收湖里的旧 ``bars_fp``，按本次口径重算指纹。
    口径变了（换钟 / schema 等）则整票重算。更长 lookback 且旧窗是新 bars
    后缀时走历史扩展，不再整票推倒。
    """
    if not family:
        return None
    prev = load_l1_any(family, code)
    if prev is None or prev.get("X") is None:
        return None
    if prev.get("y") is None:
        return None
    from core.research.materialize.consumer import MaterializeRequired

    old_x = np.asarray(prev["X"], dtype=np.float64)
    old_y = np.asarray(prev["y"], dtype=np.float64)
    old_dates = [str(d)[:10] for d in (prev.get("dates") or [])]
    if (
        old_x.ndim != 2
        or old_y.ndim != 1
        or old_x.shape[0] != old_y.shape[0]
        or len(old_dates) != old_x.shape[0]
    ):
        logger.debug("incremental lake shape mismatch code=%s", code)
        return None
    meta = prev.get("_meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    stored_fp = str(meta.get("fingerprint") or "")
    stored_bars = str(meta.get("bars_fp") or "")
    try:
        fresh = spec_fp(stored_bars) if stored_bars else ""
    except MaterializeRequired:
        raise
    except Exception:  # noqa: BLE001
        logger.debug("incremental spec_fp failed code=%s", code, exc_info=True)
        return None
    if not stored_bars or fresh != stored_fp:
        return None
    n_new = tail_append_days(bars, meta, max_new=max_new)
    mode = "tail"
    keep: List[str] = []
    keep_from = ""
    if n_new is not None:
        eff_pad = int(pad) + max(0, int(n_new) - 1) * 2
        keep = tail_keep_dates(old_dates, bars, pad=eff_pad)
        if keep:
            keep_from = min(keep)
    else:
        prefix_n = history_extend_prefix_bars(bars, meta)
        if prefix_n is None or int(prefix_n) <= 0:
            return None
        mode = "history"
        keep = history_keep_dates(old_dates, bars, pad=int(pad))
        # 历史扩展：旧行全留，新块按日覆盖（前缀 + 接合 pad）；不砍掉未重算的旧日
    if not keep:
        return None
    try:
        nx, ny, nnames, ndates = collect_tail(keep)
    except MaterializeRequired:
        raise
    except Exception:  # noqa: BLE001
        logger.debug(
            "incremental collect_tail failed code=%s mode=%s",
            code,
            mode,
            exc_info=True,
        )
        return None
    if nx is None or int(getattr(nx, "shape", (0,))[0] or 0) <= 0:
        if mode == "tail":
            return old_x, old_y, list(prev.get("names") or []), old_dates
        return None
    nx = np.asarray(nx, dtype=np.float64)
    ny = np.asarray(ny, dtype=np.float64)
    ndates = [str(d)[:10] for d in (ndates or [])]
    if nx.ndim != 2 or ny.ndim != 1 or nx.shape[0] != ny.shape[0] or len(ndates) != nx.shape[0]:
        logger.debug("incremental tail shape mismatch code=%s mode=%s", code, mode)
        return None
    return merge_matrix_rows(
        old_x,
        old_y,
        old_dates,
        list(prev.get("names") or []),
        nx,
        ny,
        ndates,
        nnames,
        keep_from=keep_from,
    )
