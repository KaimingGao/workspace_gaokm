"""决策日缺口，以及因子序列窗口的按行视图。

价量序列 ``seq8|16|32|64`` 已下线。因子序列 ``seq_core`` / ``seq_full``
在 ``factor_panel.sequence_from_factor_panel`` 里建，``seq_len`` 含决策日。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

FACTOR_SEQ_SETS = frozenset({"seq_core", "seq_full"})
DEFAULT_SEQ_FEAT_SET = "seq_core"
GAP_FEATURE_NAME = "gap_t"


def resolve_seq_feat_set(feat_set: Optional[str] = None) -> str:
    """``seq_core`` / ``seq_full``。价量档 seq8/16/32/64 已下线。"""
    raw = str(feat_set if feat_set is not None else DEFAULT_SEQ_FEAT_SET).strip().lower()
    if raw in {"", DEFAULT_SEQ_FEAT_SET, "core", "seq_core"}:
        return "seq_core"
    if raw in {"full", "seq_full"}:
        return "seq_full"
    if raw in {"8", "16", "32", "64", "base", "seq8", "seq16", "seq32", "seq64"}:
        raise ValueError(f"价量序列 {raw} 已下线，只用 seq_core / seq_full")
    return DEFAULT_SEQ_FEAT_SET


def is_factor_seq(feat_set: Optional[str] = None) -> bool:
    """core / full：序列宽等于这一头的因子 core / full。"""
    return resolve_seq_feat_set(feat_set) in FACTOR_SEQ_SETS


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


class _WindowFlags:
    """让训练跳过 ``ascontiguousarray`` 整表展开。"""

    def __getitem__(self, key: str) -> bool:
        if key in ("C_CONTIGUOUS", "WRITEABLE", "OWNDATA"):
            return True
        raise KeyError(key)

    @property
    def c_contiguous(self) -> bool:
        return True

    @property
    def writeable(self) -> bool:
        return True

    @property
    def owndata(self) -> bool:
        return True


def sample_train_rows(row_ids: Sequence[int], seed: int) -> np.ndarray:
    """训练行样本，最多 2 万行。中位数和分位共用这一抽法。"""
    tr_np = np.asarray(row_ids, dtype=np.int64)
    if tr_np.size > 20000:
        rng = np.random.RandomState(int(seed) + 7)
        tr_np = tr_np[rng.choice(tr_np.size, size=20000, replace=False)]
    return tr_np


def fill_nonfinite_inplace(X: np.ndarray, med: np.ndarray) -> None:
    """按最后一维填非有限值。按行分块，避免整表布尔掩码。"""
    med_a = np.asarray(med, dtype=np.float32).reshape(-1)
    if X.size <= 0 or int(med_a.shape[0]) != int(X.shape[-1]):
        return
    step = 8192
    n = int(X.shape[0])
    for start in range(0, n, step):
        block = X[start : start + step]
        bad = ~np.isfinite(block)
        if not bool(bad.any()):
            continue
        fill = np.broadcast_to(
            med_a.reshape((1,) * (block.ndim - 1) + (int(med_a.shape[0]),)),
            block.shape,
        )
        block[bad] = fill[bad]


class FactorWindowView:
    """因子序列：``values`` 每个因子行只存一份，``windows`` 指向窗口内的因子行。

    逻辑形状是 ``(N, L, F)``。按行取数才拼窗口，不把 L 天铺成常驻张量。
    ``age`` 是 ``age_td``，形状 ``(N, L)``；没有这一列时为 None。
    """

    def __init__(
        self,
        values: np.ndarray,
        windows: np.ndarray,
        age: Optional[np.ndarray],
        feature_names: Tuple[str, ...],
    ) -> None:
        self.values = np.asarray(values, dtype=np.float32)
        self.windows = np.asarray(windows, dtype=np.int32)
        self.age = None if age is None else np.asarray(age, dtype=np.float32)
        self.feature_names = tuple(feature_names)
        self.flags = _WindowFlags()
        self.dtype = np.dtype(np.float32)
        self.ndim = 3
        # 与 values 行对齐的交易日。截面 z 用，不随样本 subset 缩短。
        self.value_dates: Optional[List[str]] = None
        self.cs_z = False

    @property
    def shape(self) -> Tuple[int, int, int]:
        n = int(self.windows.shape[0]) if self.windows.ndim == 2 else 0
        length = int(self.windows.shape[1]) if self.windows.ndim == 2 else 0
        width = int(self.values.shape[1]) if self.values.ndim == 2 else 0
        if self.age is not None:
            width += 1
        return (n, length, width)

    @property
    def size(self) -> int:
        n, length, width = self.shape
        return int(n) * int(length) * int(width)

    @property
    def nbytes(self) -> int:
        total = int(self.values.nbytes) + int(self.windows.nbytes)
        if self.age is not None:
            total += int(self.age.nbytes)
        return total

    def subset(self, idx: np.ndarray) -> "FactorWindowView":
        ii = np.asarray(idx, dtype=np.int64)
        out = FactorWindowView(
            self.values,
            np.ascontiguousarray(self.windows[ii]),
            None if self.age is None else np.ascontiguousarray(self.age[ii]),
            self.feature_names,
        )
        out.value_dates = self.value_dates
        out.cs_z = bool(self.cs_z)
        return out

    def gather(self, rows: Any) -> np.ndarray:
        """取出这些样本的窗口，形状 ``(L, F)`` 或 ``(B, L, F)``。"""
        idx = self.windows[rows]
        block = self.values[idx]
        if self.age is None:
            return np.ascontiguousarray(block, dtype=np.float32)
        age = np.asarray(self.age[rows], dtype=np.float32)
        if block.ndim == 2:
            out = np.empty((block.shape[0], block.shape[1] + 1), dtype=np.float32)
            out[:, :-1] = block
            out[:, -1] = age
            return out
        out = np.empty(block.shape[:-1] + (block.shape[-1] + 1,), dtype=np.float32)
        out[..., :-1] = block
        out[..., -1] = age
        return out

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, tuple):
            if not key:
                return self.gather(slice(None))
            block = self.gather(key[0])
            if isinstance(key[0], (int, np.integer)):
                return block[key[1:]]
            return block[(slice(None),) + key[1:]]
        return self.gather(key)

    def __array__(self, dtype: Any = None, copy: Any = None) -> np.ndarray:
        n, length, width = self.shape
        if int(n) * int(length) * int(width) > 4_000_000:
            raise MemoryError(
                f"因子序列是窗口视图 {self.shape}，按行 gather，不要整表展开"
            )
        rows = np.arange(int(n), dtype=np.int64)
        out = self.gather(rows) if n else np.zeros((0, length, width), dtype=np.float32)
        if dtype is None:
            return out
        return np.asarray(out, dtype=dtype)

    def impute_median(self, row_ids: Sequence[int], *, seed: int) -> Optional[List[float]]:
        """用训练窗口的逐列中位数填因子行和 age 里的非有限值。

        因子列已经是截面 z 时，缺测就是 0，不再用中位数改写。
        """
        if not row_ids or self.shape[0] <= 0:
            return None
        width = int(self.values.shape[1]) if self.values.ndim == 2 else 0
        if self.cs_z:
            fill_nonfinite_inplace(self.values, np.zeros((width,), dtype=np.float32))
            med = [0.0] * width
            if self.age is not None:
                tr_np = sample_train_rows(row_ids, int(seed))
                age = np.asarray(self.age[tr_np], dtype=np.float64).reshape(-1)
                age_med = float(np.nanmedian(age)) if age.size else 0.0
                if age_med != age_med:
                    age_med = 0.0
                fill_nonfinite_inplace(self.age[..., None], np.float32(age_med))
                med.append(age_med)
            return med
        tr_np = sample_train_rows(row_ids, int(seed))
        sample = np.asarray(self.gather(tr_np), dtype=np.float64)
        flat = sample.reshape(-1, sample.shape[-1])
        med = np.nanmedian(flat, axis=0).astype(np.float32)
        med = np.where(np.isfinite(med), med, np.float32(0.0)).astype(np.float32)
        fill_nonfinite_inplace(self.values, med[:width])
        if self.age is not None and int(med.shape[0]) == width + 1:
            fill_nonfinite_inplace(self.age[..., None], med[-1:])
        return [float(v) for v in med.tolist()]

    def clip_train(
        self,
        gap: np.ndarray,
        row_ids: Sequence[int],
        *,
        seed: int,
    ) -> Optional[Dict[str, Any]]:
        """分位按训练窗口估计，裁切写回因子行和 age。

        因子列已经是截面 z 时不再按分位裁，只保留 gap 的界。
        """
        if not row_ids:
            return None
        if self.cs_z:
            tr_np = sample_train_rows(row_ids, int(seed))
            g_s = np.asarray(gap, dtype=np.float64)[tr_np]
            g_s = g_s[np.isfinite(g_s)]
            if g_s.size:
                gap_lo = float(np.percentile(g_s, 1.0))
                gap_hi = float(np.percentile(g_s, 99.0))
                np.clip(gap, gap_lo, gap_hi, out=gap)
            else:
                gap_lo, gap_hi = 0.0, 0.0
            return {"seq_cs_z": True, "gap_lo": gap_lo, "gap_hi": gap_hi}
        tr_np = sample_train_rows(row_ids, int(seed))
        sample = self.gather(tr_np)
        flat = sample.reshape(-1, sample.shape[-1])
        lo = np.nanpercentile(flat, 1.0, axis=0).astype(np.float32)
        hi = np.nanpercentile(flat, 99.0, axis=0).astype(np.float32)
        width = int(self.values.shape[1]) if self.values.ndim == 2 else 0
        np.clip(self.values, lo[:width], hi[:width], out=self.values)
        if self.age is not None and int(lo.shape[0]) == width + 1:
            np.clip(self.age, float(lo[-1]), float(hi[-1]), out=self.age)
        g_s = np.asarray(gap, dtype=np.float64)[tr_np]
        gap_lo = float(np.nanpercentile(g_s, 1.0))
        gap_hi = float(np.nanpercentile(g_s, 99.0))
        np.clip(gap, gap_lo, gap_hi, out=gap)
        return {
            "x_lo": [float(v) for v in lo.tolist()],
            "x_hi": [float(v) for v in hi.tolist()],
            "gap_lo": gap_lo,
            "gap_hi": gap_hi,
        }

    def apply_saved_clip(self, gap: np.ndarray, clip: Optional[Mapping[str, Any]]) -> None:
        """按已保存的中位数和分位写回因子行、age 和 gap。

        ``seq_cs_z`` 时因子列已是截面 z，缺测写 0，不再套训练分位。
        """
        if not isinstance(clip, Mapping):
            return
        width = int(self.values.shape[1]) if self.values.ndim == 2 else 0
        if clip.get("seq_cs_z"):
            fill_nonfinite_inplace(self.values, np.zeros((width,), dtype=np.float32))
            try:
                gap_lo = float(clip.get("gap_lo"))
                gap_hi = float(clip.get("gap_hi"))
            except (TypeError, ValueError):
                return
            np.clip(gap, gap_lo, gap_hi, out=gap)
            return
        med = clip.get("x_median")
        if med is not None:
            try:
                med_a = np.asarray(med, dtype=np.float32).reshape(-1)
            except (TypeError, ValueError):
                med_a = None
            if med_a is not None and int(med_a.shape[0]) == int(self.shape[-1]):
                fill_nonfinite_inplace(self.values, med_a[:width])
                if self.age is not None:
                    fill_nonfinite_inplace(self.age[..., None], med_a[-1:])
        try:
            lo = np.asarray(clip.get("x_lo"), dtype=np.float32).reshape(-1)
            hi = np.asarray(clip.get("x_hi"), dtype=np.float32).reshape(-1)
            gap_lo = float(clip.get("gap_lo"))
            gap_hi = float(clip.get("gap_hi"))
        except (TypeError, ValueError):
            return
        if int(lo.shape[0]) != int(self.shape[-1]) or hi.shape != lo.shape:
            return
        np.clip(self.values, lo[:width], hi[:width], out=self.values)
        if self.age is not None:
            np.clip(self.age, float(lo[-1]), float(hi[-1]), out=self.age)
        np.clip(gap, gap_lo, gap_hi, out=gap)


@dataclass
class SeqPanel:
    """紧凑面板：X (N,L,F) · gap (N,) · y (N,) · dates/codes 等长。

    因子序列的 ``X`` 可以是 ``FactorWindowView``：因子行只存一份，窗口按行现取。
    """

    X: Any
    gap: np.ndarray
    y: np.ndarray
    dates: List[str]
    codes: List[str]
    seq_len: int
    feature_names: Tuple[str, ...] = ()

    @property
    def n(self) -> int:
        return int(self.y.shape[0])

    def subset(self, idx: Sequence[int]) -> "SeqPanel":
        ii = np.asarray(idx, dtype=np.int64)
        src = self.X
        if isinstance(src, FactorWindowView):
            taken = src.subset(ii)
        else:
            taken = src[ii]
        return SeqPanel(
            X=taken,
            gap=self.gap[ii],
            y=self.y[ii],
            dates=[self.dates[int(i)] for i in ii.tolist()],
            codes=[self.codes[int(i)] for i in ii.tolist()],
            seq_len=self.seq_len,
            feature_names=tuple(self.feature_names),
        )


def _bar_day(bar: Mapping[str, Any]) -> str:
    return str(bar.get("date") or bar.get("trade_date") or "")[:10]


def gaps_by_decision(
    stock_bars: Mapping[str, Sequence[dict]],
) -> Dict[Tuple[str, str], float]:
    """决策日缺口：今开 / 昨收 − 1，百分点。键是 ``(code, YYYY-MM-DD)``。"""
    out: Dict[Tuple[str, str], float] = {}
    for code, bars in (stock_bars or {}).items():
        code_s = str(code).strip()
        if not code_s:
            continue
        rows: List[Tuple[str, dict]] = []
        for bar in bars or []:
            if not isinstance(bar, dict):
                continue
            day = _bar_day(bar)
            if day:
                rows.append((day, bar))
        rows.sort(key=lambda item: item[0])
        for i in range(1, len(rows)):
            day, bar = rows[i]
            prev = rows[i - 1][1]
            cur_o = _f(bar.get("open"))
            prev_c = _f(prev.get("close"))
            if cur_o is None or cur_o <= 0 or prev_c is None or prev_c <= 0:
                continue
            gap = (float(cur_o) / float(prev_c) - 1.0) * 100.0
            if math.isfinite(gap):
                out[(code_s, day)] = float(gap)
    return out


def panel_metas(panel: SeqPanel) -> List[Dict[str, str]]:
    return [
        {"code": c, "date": d, "decision_date": d}
        for c, d in zip(panel.codes, panel.dates)
    ]


__all__ = [
    "DEFAULT_SEQ_FEAT_SET",
    "GAP_FEATURE_NAME",
    "resolve_seq_feat_set",
    "is_factor_seq",
    "gaps_by_decision",
    "FactorWindowView",
    "fill_nonfinite_inplace",
    "sample_train_rows",
    "SeqPanel",
    "panel_metas",
]
