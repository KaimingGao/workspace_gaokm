"""四头 ŷ_* PIT 日线短序列面板（研究轨；默认标签 y_oo）。

决策日 T：序列 = 完整日 ``[T-L … T-1]``；附加 ``gap_t``（今开/昨收−1）；
标签 ``open[T+h]/open[T]−1``。今收 / 今高 / 今低 / 今量 **不进** 序列。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple  # noqa: I001 — Any used by head dispatch

import numpy as np

logger = logging.getLogger(__name__)

SEQ_FEATURE_NAMES: Tuple[str, ...] = (
    "oo_ret",
    "cc_ret",
    "oc_ret",
    "range_pct",
    "gap",
    "vol_log_rel20",
    "sign_oo",
    "body_pct",
)

GAP_FEATURE_NAME = "gap_t"


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x


def _bar_ohlcv(bar: Optional[dict]) -> Optional[Tuple[float, float, float, float, float]]:
    if not isinstance(bar, dict):
        return None
    o = _f(bar.get("open"))
    h = _f(bar.get("high"))
    lo = _f(bar.get("low"))
    c = _f(bar.get("close"))
    v = _f(bar.get("volume") if bar.get("volume") is not None else bar.get("vol"))
    if o is None or h is None or lo is None or c is None:
        return None
    if o <= 0 or h <= 0 or lo <= 0 or c <= 0:
        return None
    vol = float(v) if v is not None and v >= 0 else 0.0
    return float(o), float(h), float(lo), float(c), vol


def _step_features(
    prev: Tuple[float, float, float, float, float],
    cur: Tuple[float, float, float, float, float],
    vol_ma20: float,
) -> np.ndarray:
    """单日完整 K 相对昨：8 维 float32。"""
    po, _ph, _pl, pc, _pv = prev
    o, h, lo, c, v = cur
    oo = (o / po - 1.0) * 100.0 if po > 0 else 0.0
    cc = (c / pc - 1.0) * 100.0 if pc > 0 else 0.0
    oc = (c / o - 1.0) * 100.0
    rng = (h - lo) / c if c > 0 else 0.0
    gap = (o / pc - 1.0) * 100.0 if pc > 0 else 0.0
    if vol_ma20 > 1e-12 and v > 0:
        vol_rel = math.log(max(v, 1.0) / vol_ma20)
    else:
        vol_rel = 0.0
    sign_oo = 1.0 if oo > 0 else (-1.0 if oo < 0 else 0.0)
    body = abs(c - o) / c if c > 0 else 0.0
    return np.asarray(
        [oo, cc, oc, rng, gap, vol_rel, sign_oo, body],
        dtype=np.float32,
    )


@dataclass
class SeqPanel:
    """紧凑面板：X (N,L,F) · gap (N,) · y (N,) · dates/codes 等长。"""

    X: np.ndarray
    gap: np.ndarray
    y: np.ndarray
    dates: List[str]
    codes: List[str]
    seq_len: int
    feature_names: Tuple[str, ...] = SEQ_FEATURE_NAMES

    @property
    def n(self) -> int:
        return int(self.y.shape[0])

    def subset(self, idx: Sequence[int]) -> "SeqPanel":
        ii = np.asarray(list(idx), dtype=np.int64)
        return SeqPanel(
            X=self.X[ii],
            gap=self.gap[ii],
            y=self.y[ii],
            dates=[self.dates[int(i)] for i in ii.tolist()],
            codes=[self.codes[int(i)] for i in ii.tolist()],
            seq_len=self.seq_len,
            feature_names=self.feature_names,
        )


def _vol_ma20(vols: Sequence[float], j: int) -> float:
    """含 j 在内的近 20 日均量；不足则用已有。"""
    start = max(0, j - 19)
    chunk = [float(v) for v in vols[start : j + 1] if v is not None and v > 0]
    if not chunk:
        return 0.0
    return float(sum(chunk) / len(chunk))


def _forward_label_pct(
    bars: Sequence[dict],
    i: int,
    *,
    label: str,
    horizon_days: int,
) -> Optional[float]:
    """按 head 标签取 y（百分点）。``tc_open`` = close[T]/open[T]−1（τ=开盘）。"""
    lab = str(label or "oo").strip().lower()
    if lab in ("tc", "tc_open", "y_tc", "oc"):
        # τ=open：同日 close/open；horizon 对日线 τc 开盘头无意义，忽略
        o0 = _f((bars[i] or {}).get("open"))
        c0 = _f((bars[i] or {}).get("close"))
        if o0 is None or c0 is None or o0 <= 0 or c0 <= 0:
            return None
        return (float(c0) / float(o0) - 1.0) * 100.0
    from core.signal.yhat_windows import forward_oo_pct

    return forward_oo_pct(bars, i, max(1, int(horizon_days or 1)))


def samples_from_bars(
    code: str,
    bars: Sequence[dict],
    *,
    seq_len: int = 20,
    horizon_days: int = 1,
    min_history: Optional[int] = None,
    label: str = "oo",
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[str]]:
    """单票：返回 ``(X, gap, y, dates)``；无样本则空数组。

    ``label``：``oo``（open→open）或 ``tc_open``（close/open，τ=开盘）。
    """
    L = max(5, int(seq_len or 20))
    h = max(1, int(horizon_days or 1))
    lab = str(label or "oo").strip().lower()
    # tc_open 标签在同日 close，不需要未来 bar
    tail = 0 if lab in ("tc", "tc_open", "y_tc", "oc") else h
    need = int(min_history) if min_history is not None else (L + 2)
    need = max(need, L + 2)
    n = len(bars or [])
    if n < need + tail:
        return (
            np.zeros((0, L, len(SEQ_FEATURE_NAMES)), dtype=np.float32),
            np.zeros((0,), dtype=np.float32),
            np.zeros((0,), dtype=np.float32),
            [],
        )

    ohlcv: List[Optional[Tuple[float, float, float, float, float]]] = []
    vols: List[float] = []
    for b in bars:
        got = _bar_ohlcv(b)
        ohlcv.append(got)
        vols.append(float(got[4]) if got is not None else 0.0)

    xs: List[np.ndarray] = []
    gaps: List[float] = []
    ys: List[float] = []
    dates: List[str] = []

    # 决策下标 i：完整日用到 i-1；序列窗口 [i-L, i)
    end_i = n if tail <= 0 else n - tail
    for i in range(max(need - 1, L), end_i):
        fr = _forward_label_pct(bars, i, label=lab, horizon_days=h)
        if fr is None or not math.isfinite(float(fr)):
            continue
        cur_o = _f((bars[i] or {}).get("open"))
        prev = ohlcv[i - 1] if i >= 1 else None
        if cur_o is None or cur_o <= 0 or prev is None:
            continue
        gap_t = (float(cur_o) / float(prev[3]) - 1.0) * 100.0
        if not math.isfinite(gap_t):
            continue

        win = np.zeros((L, len(SEQ_FEATURE_NAMES)), dtype=np.float32)
        ok = True
        for k in range(L):
            j = i - L + k  # 完整日下标
            if j < 1 or ohlcv[j] is None or ohlcv[j - 1] is None:
                ok = False
                break
            win[k] = _step_features(
                ohlcv[j - 1],  # type: ignore[arg-type]
                ohlcv[j],  # type: ignore[arg-type]
                _vol_ma20(vols, j),
            )
        if not ok:
            continue
        # PIT：窗口末日必须是 T-1；今收不进序列（仅可进 y）
        last_date = str((bars[i - 1] or {}).get("date") or "")[:10]
        decision = str((bars[i] or {}).get("date") or "")[:10]
        if not decision or not last_date or last_date >= decision:
            continue

        xs.append(win)
        gaps.append(float(gap_t))
        ys.append(float(fr))
        dates.append(decision)

    if not xs:
        return (
            np.zeros((0, L, len(SEQ_FEATURE_NAMES)), dtype=np.float32),
            np.zeros((0,), dtype=np.float32),
            np.zeros((0,), dtype=np.float32),
            [],
        )
    return (
        np.stack(xs, axis=0),
        np.asarray(gaps, dtype=np.float32),
        np.asarray(ys, dtype=np.float32),
        dates,
    )


def _label_for_head(head: Any) -> str:
    """回归/排序共用底层收益标签；排序在 listwise 损失里按日截面做。"""
    if head is None:
        return "oo"
    if hasattr(head, "id"):
        hid = str(getattr(head, "id") or "oo")
    else:
        hid = str(head).strip().lower()
    if hid in ("tc", "tc_open", "y_tc", "tc_rank", "y_tc_rank"):
        return "tc_open"
    return "oo"


def build_seq_panel(
    stock_bars: Mapping[str, Sequence[dict]],
    *,
    head: Any = "oo",
    seq_len: int = 20,
    horizon_days: int = 1,
) -> SeqPanel:
    """多票堆叠序列面板（float32）；``head`` 可为 HeadSpec 或 id。"""
    L = max(5, int(seq_len or 20))
    F = len(SEQ_FEATURE_NAMES)
    lab = _label_for_head(head)
    chunks_x: List[np.ndarray] = []
    chunks_g: List[np.ndarray] = []
    chunks_y: List[np.ndarray] = []
    dates: List[str] = []
    codes: List[str] = []
    for code, bars in stock_bars.items():
        code_s = str(code).strip()
        if not code_s:
            continue
        try:
            X, g, y, ds = samples_from_bars(
                code_s,
                bars,
                seq_len=L,
                horizon_days=horizon_days,
                label=lab,
            )
        except Exception:  # noqa: BLE001
            logger.debug("seq samples failed code=%s head=%s", code_s, lab, exc_info=True)
            continue
        if X.shape[0] <= 0:
            continue
        chunks_x.append(X)
        chunks_g.append(g)
        chunks_y.append(y)
        dates.extend(ds)
        codes.extend([code_s] * len(ds))
    if not chunks_x:
        return SeqPanel(
            X=np.zeros((0, L, F), dtype=np.float32),
            gap=np.zeros((0,), dtype=np.float32),
            y=np.zeros((0,), dtype=np.float32),
            dates=[],
            codes=[],
            seq_len=L,
        )
    return SeqPanel(
        X=np.concatenate(chunks_x, axis=0),
        gap=np.concatenate(chunks_g, axis=0),
        y=np.concatenate(chunks_y, axis=0),
        dates=dates,
        codes=codes,
        seq_len=L,
    )


def assert_pit_last_bar_before_decision(
    bars: Sequence[dict],
    decision_idx: int,
    seq_len: int,
) -> bool:
    """单测辅助：序列末日日期 < 决策日。"""
    L = int(seq_len)
    if decision_idx < L or decision_idx >= len(bars):
        return False
    last = str((bars[decision_idx - 1] or {}).get("date") or "")[:10]
    dec = str((bars[decision_idx] or {}).get("date") or "")[:10]
    return bool(last and dec and last < dec)


def panel_metas(panel: SeqPanel) -> List[Dict[str, str]]:
    return [
        {"code": c, "date": d, "decision_date": d}
        for c, d in zip(panel.codes, panel.dates)
    ]


__all__ = [
    "SEQ_FEATURE_NAMES",
    "GAP_FEATURE_NAME",
    "SeqPanel",
    "samples_from_bars",
    "build_seq_panel",
    "assert_pit_last_bar_before_decision",
    "panel_metas",
]
