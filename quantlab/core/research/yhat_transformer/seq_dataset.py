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

# 嵌套扩展：SEQ8 ⊂ SEQ16 ⊂ SEQ32 ⊂ SEQ64；每档都是「当日完整 K」上的互异 OHLCV 统计。
# 不做 lag 拼维（序列轴 L 已覆盖多日）；滚动窗一律止于当日 j，历史不足则降级用已有。

# 经典 8 维价量摘要（完整日 K）
SEQ8_FEATURE_NAMES: Tuple[str, ...] = (
    "oo_ret",
    "cc_ret",
    "oc_ret",
    "range_pct",
    "gap",
    "vol_log_rel20",
    "sign_oo",
    "body_pct",
)

# seq16 = seq8 + 影线/位置/量变/缺口回补
SEQ16_FEATURE_NAMES: Tuple[str, ...] = SEQ8_FEATURE_NAMES + (
    "upper_shadow",
    "lower_shadow",
    "close_loc",
    "range_vs_prev",
    "oc_sign",
    "vol_chg",
    "gap_fill",
    "gap_vs_oc",
)

# seq32 = seq16 + 短窗动量 / ATR·波动 / 量 z / K 结构 / 相对均线·高低点
SEQ32_FEATURE_NAMES: Tuple[str, ...] = SEQ16_FEATURE_NAMES + (
    "cc_ret_2",
    "cc_ret_5",
    "cc_ret_10",
    "atr14_pct",
    "range_vs_atr14",
    "ret_std_5",
    "ret_std_20",
    "vol_log_rel5",
    "vol_z20",
    "open_loc",
    "body_vs_range",
    "wick_ratio",
    "close_vs_ma5",
    "close_vs_ma20",
    "dist_hi20",
    "dist_lo20",
)

# seq64 = seq32 + 更长窗动量 / 波动结构 / 量能路径 / 缺口与效率 / 趋势计数
SEQ64_FEATURE_NAMES: Tuple[str, ...] = SEQ32_FEATURE_NAMES + (
    "cc_ret_3",
    "cc_ret_20",
    "oo_ret_5",
    "oo_ret_10",
    "ret_std_10",
    "atr5_pct",
    "parkinson20",
    "vol_ma5_vs_ma20",
    "vol_chg_5",
    "up_vol_share20",
    "eff_ratio_10",
    "max_cc_10",
    "min_cc_10",
    "gap_ma5",
    "overnight_share",
    "close_vs_ma10",
    "close_pctile20",
    "dist_hi10",
    "dist_lo10",
    "up_days_5",
    "up_days_20",
    "gk_vol_10",
    "up_range_share",
    "signed_vol_chg",
    "high_vs_prev",
    "low_vs_prev",
    "body_signed",
    "range_ma5_vs_ma20",
    "gap_std_10",
    "vol_z5",
    "oc_ret_5",
    "mid_loc",
)

SEQ_FEAT_SETS: Dict[str, Tuple[str, ...]] = {
    "seq8": SEQ8_FEATURE_NAMES,
    "seq16": SEQ16_FEATURE_NAMES,
    "seq32": SEQ32_FEATURE_NAMES,
    "seq64": SEQ64_FEATURE_NAMES,
}
DEFAULT_SEQ_FEAT_SET = "seq16"
# 兼容旧导入：默认指向当前默认集
SEQ_FEATURE_NAMES: Tuple[str, ...] = SEQ16_FEATURE_NAMES

GAP_FEATURE_NAME = "gap_t"


def resolve_seq_feat_set(feat_set: Optional[str] = None) -> str:
    """归一 ``seq8|seq16|seq32|seq64``；非法值回落默认。"""
    raw = str(feat_set if feat_set is not None else DEFAULT_SEQ_FEAT_SET).strip().lower()
    aliases = {
        "8": "seq8",
        "base": "seq8",
        "seq8": "seq8",
        "16": "seq16",
        "seq16": "seq16",
        "32": "seq32",
        "seq32": "seq32",
        "64": "seq64",
        "seq64": "seq64",
    }
    return aliases.get(raw, DEFAULT_SEQ_FEAT_SET)


def seq_feature_names(feat_set: Optional[str] = None) -> Tuple[str, ...]:
    key = resolve_seq_feat_set(feat_set)
    return SEQ_FEAT_SETS[key]


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


def _clip(x: float, hi: float) -> float:
    if x > hi:
        return hi
    if x < -hi:
        return -hi
    return x


def _safe_log_ratio(num: float, den: float) -> float:
    if den > 1e-12 and num > 0 and den > 0:
        return math.log(max(num, 1.0) / max(den, 1.0))
    return 0.0


def _mean(xs: Sequence[float]) -> float:
    if not xs:
        return 0.0
    return float(sum(xs) / len(xs))


def _std(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = _mean(xs)
    var = sum((x - m) * (x - m) for x in xs) / float(n)
    return math.sqrt(var) if var > 0 else 0.0


def _cc_rets_ending(
    ohlcv: Sequence[Optional[Tuple[float, float, float, float, float]]],
    j: int,
    n: int,
) -> List[float]:
    """止于 j 的近 n 个完整日 cc_ret%（需要 j 与 j-n 有效）。"""
    out: List[float] = []
    for k in range(max(1, j - n + 1), j + 1):
        if k < 1 or ohlcv[k] is None or ohlcv[k - 1] is None:
            continue
        c = float(ohlcv[k][3])  # type: ignore[index]
        pc = float(ohlcv[k - 1][3])  # type: ignore[index]
        if c > 0 and pc > 0:
            out.append((c / pc - 1.0) * 100.0)
    return out


def _close_ma(
    ohlcv: Sequence[Optional[Tuple[float, float, float, float, float]]],
    j: int,
    win: int,
) -> float:
    vals: List[float] = []
    start = max(0, j - win + 1)
    for k in range(start, j + 1):
        bar = ohlcv[k]
        if bar is None:
            continue
        c = float(bar[3])
        if c > 0:
            vals.append(c)
    return _mean(vals)


def _atr_pct(
    ohlcv: Sequence[Optional[Tuple[float, float, float, float, float]]],
    j: int,
    win: int,
) -> float:
    """近 win 日 (h-l)/c 均值。"""
    vals: List[float] = []
    start = max(0, j - win + 1)
    for k in range(start, j + 1):
        bar = ohlcv[k]
        if bar is None:
            continue
        h, lo, c = float(bar[1]), float(bar[2]), float(bar[3])
        if c > 0 and h >= lo:
            vals.append((h - lo) / c)
    return _mean(vals)


def _hi_lo(
    ohlcv: Sequence[Optional[Tuple[float, float, float, float, float]]],
    j: int,
    win: int,
) -> Tuple[float, float]:
    hs: List[float] = []
    ls: List[float] = []
    start = max(0, j - win + 1)
    for k in range(start, j + 1):
        bar = ohlcv[k]
        if bar is None:
            continue
        hs.append(float(bar[1]))
        ls.append(float(bar[2]))
    if not hs:
        return 0.0, 0.0
    return max(hs), min(ls)


def _vol_ma(vols: Sequence[float], j: int, win: int) -> float:
    start = max(0, j - win + 1)
    chunk = [float(v) for v in vols[start : j + 1] if v is not None and v > 0]
    return _mean(chunk)


def _feat64(
    ohlcv: Sequence[Optional[Tuple[float, float, float, float, float]]],
    vols: Sequence[float],
    j: int,
) -> Optional[List[float]]:
    """单日完整 K 上的 64 维；前缀 8/16/32 可直接截断。"""
    if j < 1 or ohlcv[j] is None or ohlcv[j - 1] is None:
        return None
    prev = ohlcv[j - 1]
    cur = ohlcv[j]
    assert prev is not None and cur is not None
    po, ph, pl, pc, pv = prev
    o, h, lo, c, v = cur

    oo = (o / po - 1.0) * 100.0 if po > 0 else 0.0
    cc = (c / pc - 1.0) * 100.0 if pc > 0 else 0.0
    oc = (c / o - 1.0) * 100.0 if o > 0 else 0.0
    rng = (h - lo) / c if c > 0 else 0.0
    gap = (o / pc - 1.0) * 100.0 if pc > 0 else 0.0
    vol_ma20 = _vol_ma(vols, j, 20)
    vol_rel20 = _safe_log_ratio(v, vol_ma20) if v > 0 else 0.0
    sign_oo = 1.0 if oo > 0 else (-1.0 if oo < 0 else 0.0)
    body = abs(c - o) / c if c > 0 else 0.0

    mid = max(o, c)
    mids = min(o, c)
    upper = (h - mid) / c if c > 0 else 0.0
    lower = (mids - lo) / c if c > 0 else 0.0
    close_loc = (c - lo) / (h - lo) if h > lo else 0.5
    prev_rng = (ph - pl) / pc if pc > 0 else 0.0
    range_vs_prev = _clip((rng / prev_rng) if prev_rng > 1e-8 else 0.0, 10.0)
    oc_sign = 1.0 if c > o else (-1.0 if c < o else 0.0)
    vol_chg = _safe_log_ratio(v, pv) if v > 0 and pv > 0 else 0.0
    if o > pc > 0:
        gap_fill = 1.0 if lo <= pc else 0.0
    elif 0 < o < pc:
        gap_fill = 1.0 if h >= pc else 0.0
    else:
        gap_fill = 0.0
    gap_vs_oc = _clip(abs(gap) / (abs(oc) + 1e-3), 20.0)

    def _ret_n(n: int, *, use_open: bool = False) -> float:
        k = j - n
        if k < 0 or ohlcv[k] is None:
            return 0.0
        base_px = float(ohlcv[k][0 if use_open else 3])  # type: ignore[index]
        px = o if use_open else c
        if base_px > 0 and px > 0:
            return (px / base_px - 1.0) * 100.0
        return 0.0

    cc2, cc5, cc10 = _ret_n(2), _ret_n(5), _ret_n(10)
    atr14 = _atr_pct(ohlcv, j, 14)
    range_vs_atr14 = _clip((rng / atr14) if atr14 > 1e-8 else 0.0, 10.0)
    rets5 = _cc_rets_ending(ohlcv, j, 5)
    rets20 = _cc_rets_ending(ohlcv, j, 20)
    ret_std5 = _std(rets5)
    ret_std20 = _std(rets20)
    vol_ma5 = _vol_ma(vols, j, 5)
    vol_rel5 = _safe_log_ratio(v, vol_ma5) if v > 0 else 0.0
    # 量相对 20 日均的 z（单点 / 粗 σ）
    vol_chunk20 = [
        float(x) for x in vols[max(0, j - 19) : j + 1] if x is not None and x > 0
    ]
    vol_z20 = 0.0
    if len(vol_chunk20) >= 2 and v > 0:
        s = _std(vol_chunk20)
        if s > 1e-12:
            vol_z20 = _clip((v - _mean(vol_chunk20)) / s, 10.0)
    open_loc = (o - lo) / (h - lo) if h > lo else 0.5
    body_vs_range = abs(c - o) / (h - lo) if h > lo else 0.0
    wick_ratio = (upper + lower) / rng if rng > 1e-8 else 0.0
    if wick_ratio > 10.0:
        wick_ratio = 10.0
    ma5 = _close_ma(ohlcv, j, 5)
    ma20 = _close_ma(ohlcv, j, 20)
    close_vs_ma5 = ((c / ma5 - 1.0) * 100.0) if ma5 > 0 else 0.0
    close_vs_ma20 = ((c / ma20 - 1.0) * 100.0) if ma20 > 0 else 0.0
    hi20, lo20 = _hi_lo(ohlcv, j, 20)
    dist_hi20 = ((c / hi20 - 1.0) * 100.0) if hi20 > 0 else 0.0
    dist_lo20 = ((c / lo20 - 1.0) * 100.0) if lo20 > 0 else 0.0

    # —— seq64 追加 ——
    cc3, cc20 = _ret_n(3), _ret_n(20)
    oo5, oo10 = _ret_n(5, use_open=True), _ret_n(10, use_open=True)
    ret_std10 = _std(_cc_rets_ending(ohlcv, j, 10))
    atr5 = _atr_pct(ohlcv, j, 5)
    # Parkinson：mean( ln(h/l)^2 ) / (4 ln2)
    park_vals: List[float] = []
    for k in range(max(0, j - 19), j + 1):
        bar = ohlcv[k]
        if bar is None:
            continue
        hk, lk = float(bar[1]), float(bar[2])
        if hk > lk > 0:
            park_vals.append(math.log(hk / lk) ** 2)
    parkinson20 = 0.0
    if park_vals:
        parkinson20 = _mean(park_vals) / (4.0 * math.log(2.0))
        parkinson20 = math.sqrt(max(parkinson20, 0.0)) * 100.0
    vol_ma5_vs_ma20 = _safe_log_ratio(vol_ma5, vol_ma20) if vol_ma5 > 0 else 0.0
    v5ago = float(vols[j - 5]) if j >= 5 else 0.0
    vol_chg_5 = _safe_log_ratio(v, v5ago) if v > 0 and v5ago > 0 else 0.0
    up_vol = 0.0
    all_vol = 0.0
    for k in range(max(1, j - 19), j + 1):
        if ohlcv[k] is None or ohlcv[k - 1] is None:
            continue
        vk = float(vols[k]) if k < len(vols) else 0.0
        if vk <= 0:
            continue
        ck = float(ohlcv[k][3])  # type: ignore[index]
        pck = float(ohlcv[k - 1][3])  # type: ignore[index]
        all_vol += vk
        if ck > pck:
            up_vol += vk
    up_vol_share20 = (up_vol / all_vol) if all_vol > 0 else 0.5
    rets10 = _cc_rets_ending(ohlcv, j, 10)
    path = sum(abs(x) for x in rets10)
    net = abs(sum(rets10)) if rets10 else 0.0
    eff_ratio_10 = (net / path) if path > 1e-8 else 0.0
    max_cc_10 = max(rets10) if rets10 else 0.0
    min_cc_10 = min(rets10) if rets10 else 0.0
    gaps5: List[float] = []
    for k in range(max(1, j - 4), j + 1):
        if ohlcv[k] is None or ohlcv[k - 1] is None:
            continue
        ok = float(ohlcv[k][0])  # type: ignore[index]
        pck = float(ohlcv[k - 1][3])  # type: ignore[index]
        if ok > 0 and pck > 0:
            gaps5.append((ok / pck - 1.0) * 100.0)
    gap_ma5 = _mean(gaps5)
    overnight_share = abs(gap) / (abs(gap) + abs(oc) + 1e-3)
    ma10 = _close_ma(ohlcv, j, 10)
    close_vs_ma10 = ((c / ma10 - 1.0) * 100.0) if ma10 > 0 else 0.0
    closes20: List[float] = []
    for k in range(max(0, j - 19), j + 1):
        bar = ohlcv[k]
        if bar is None:
            continue
        ck = float(bar[3])
        if ck > 0:
            closes20.append(ck)
    close_pctile20 = 0.5
    if len(closes20) >= 2:
        below = sum(1 for x in closes20 if x <= c)
        close_pctile20 = below / float(len(closes20))
    hi10, lo10 = _hi_lo(ohlcv, j, 10)
    dist_hi10 = ((c / hi10 - 1.0) * 100.0) if hi10 > 0 else 0.0
    dist_lo10 = ((c / lo10 - 1.0) * 100.0) if lo10 > 0 else 0.0
    up5 = sum(1 for x in rets5 if x > 0)
    up20 = sum(1 for x in rets20 if x > 0)
    up_days_5 = up5 / float(max(len(rets5), 1))
    up_days_20 = up20 / float(max(len(rets20), 1))
    # Garman–Klass 近似：0.5*ln(h/l)^2 - (2ln2-1)*ln(c/o)^2
    gk_vals: List[float] = []
    for k in range(max(0, j - 9), j + 1):
        bar = ohlcv[k]
        if bar is None:
            continue
        ok, hk, lk, ck = float(bar[0]), float(bar[1]), float(bar[2]), float(bar[3])
        if ok <= 0 or hk <= lk or ck <= 0:
            continue
        term = 0.5 * (math.log(hk / lk) ** 2) - (2.0 * math.log(2.0) - 1.0) * (
            math.log(ck / ok) ** 2
        )
        if term > 0:
            gk_vals.append(term)
    gk_vol_10 = math.sqrt(_mean(gk_vals)) * 100.0 if gk_vals else 0.0
    up_range_share = (h - o) / (h - lo) if h > lo else 0.5
    signed_vol_chg = (1.0 if cc > 0 else (-1.0 if cc < 0 else 0.0)) * vol_chg
    high_vs_prev = ((h / ph - 1.0) * 100.0) if ph > 0 else 0.0
    low_vs_prev = ((lo / pl - 1.0) * 100.0) if pl > 0 else 0.0
    body_signed = ((c - o) / c) if c > 0 else 0.0
    ranges5: List[float] = []
    ranges20: List[float] = []
    for k in range(max(0, j - 19), j + 1):
        bar = ohlcv[k]
        if bar is None:
            continue
        hk, lk, ck = float(bar[1]), float(bar[2]), float(bar[3])
        if ck > 0 and hk >= lk:
            rv = (hk - lk) / ck
            ranges20.append(rv)
            if k >= j - 4:
                ranges5.append(rv)
    r5m, r20m = _mean(ranges5), _mean(ranges20)
    range_ma5_vs_ma20 = _clip((r5m / r20m) if r20m > 1e-8 else 0.0, 10.0)
    gaps10: List[float] = []
    for k in range(max(1, j - 9), j + 1):
        if ohlcv[k] is None or ohlcv[k - 1] is None:
            continue
        ok = float(ohlcv[k][0])  # type: ignore[index]
        pck = float(ohlcv[k - 1][3])  # type: ignore[index]
        if ok > 0 and pck > 0:
            gaps10.append((ok / pck - 1.0) * 100.0)
    gap_std_10 = _std(gaps10)
    vol_chunk5 = [
        float(x) for x in vols[max(0, j - 4) : j + 1] if x is not None and x > 0
    ]
    vol_z5 = 0.0
    if len(vol_chunk5) >= 2 and v > 0:
        s5 = _std(vol_chunk5)
        if s5 > 1e-12:
            vol_z5 = _clip((v - _mean(vol_chunk5)) / s5, 10.0)
    # 5 日前开 → 今收（跨日 open-close 动量）
    oc_ret_5 = 0.0
    if j >= 5 and ohlcv[j - 5] is not None:
        o5 = float(ohlcv[j - 5][0])  # type: ignore[index]
        if o5 > 0 and c > 0:
            oc_ret_5 = (c / o5 - 1.0) * 100.0
    mid_px = (h + lo + c) / 3.0
    mid_loc = (mid_px - lo) / (h - lo) if h > lo else 0.5

    return [
        # 8
        oo,
        cc,
        oc,
        rng,
        gap,
        vol_rel20,
        sign_oo,
        body,
        # 16
        upper,
        lower,
        close_loc,
        range_vs_prev,
        oc_sign,
        vol_chg,
        gap_fill,
        gap_vs_oc,
        # 32
        cc2,
        cc5,
        cc10,
        atr14,
        range_vs_atr14,
        ret_std5,
        ret_std20,
        vol_rel5,
        vol_z20,
        open_loc,
        body_vs_range,
        wick_ratio,
        close_vs_ma5,
        close_vs_ma20,
        dist_hi20,
        dist_lo20,
        # 64
        cc3,
        cc20,
        oo5,
        oo10,
        ret_std10,
        atr5,
        parkinson20,
        vol_ma5_vs_ma20,
        vol_chg_5,
        up_vol_share20,
        eff_ratio_10,
        max_cc_10,
        min_cc_10,
        gap_ma5,
        overnight_share,
        close_vs_ma10,
        close_pctile20,
        dist_hi10,
        dist_lo10,
        up_days_5,
        up_days_20,
        gk_vol_10,
        up_range_share,
        signed_vol_chg,
        high_vs_prev,
        low_vs_prev,
        body_signed,
        range_ma5_vs_ma20,
        gap_std_10,
        vol_z5,
        oc_ret_5,
        mid_loc,
    ]


def _vector_at(
    ohlcv: Sequence[Optional[Tuple[float, float, float, float, float]]],
    vols: Sequence[float],
    j: int,
    n_feat: int,
) -> Optional[np.ndarray]:
    """当日完整 K 的前 ``n_feat`` 维（8/16/32/64 嵌套前缀）。"""
    vec = _feat64(ohlcv, vols, j)
    if vec is None:
        return None
    n = int(n_feat)
    if n <= 0 or len(vec) < n:
        return None
    return np.asarray(vec[:n], dtype=np.float32)


@dataclass
class SeqPanel:
    """紧凑面板：X (N,L,F) · gap (N,) · y (N,) · dates/codes 等长。"""

    X: np.ndarray
    gap: np.ndarray
    y: np.ndarray
    dates: List[str]
    codes: List[str]
    seq_len: int
    feature_names: Tuple[str, ...] = SEQ16_FEATURE_NAMES

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
            feature_names=tuple(self.feature_names),
        )


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
    feat_set: Optional[str] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[str]]:
    """单票：返回 ``(X, gap, y, dates)``；无样本则空数组。

    ``label``：``oo``（open→open）或 ``tc_open``（close/open，τ=开盘）。
    ``feat_set``：``seq8`` / ``seq16`` / ``seq32`` / ``seq64``（默认 ``seq16``）。
    """
    L = max(5, int(seq_len or 20))
    names = seq_feature_names(feat_set)
    F = len(names)
    h = max(1, int(horizon_days or 1))
    lab = str(label or "oo").strip().lower()
    # tc_open 标签在同日 close，不需要未来 bar
    tail = 0 if lab in ("tc", "tc_open", "y_tc", "oc") else h
    need = int(min_history) if min_history is not None else (L + 2)
    need = max(need, L + 2)
    n = len(bars or [])
    if n < need + tail:
        return (
            np.zeros((0, L, F), dtype=np.float32),
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

        win = np.zeros((L, F), dtype=np.float32)
        ok = True
        for k in range(L):
            j = i - L + k  # 完整日下标
            if j < 1 or ohlcv[j] is None or ohlcv[j - 1] is None:
                ok = False
                break
            got = _vector_at(ohlcv, vols, j, F)
            if got is None:
                ok = False
                break
            win[k] = got
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
            np.zeros((0, L, F), dtype=np.float32),
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
    """回归/排序共用底层收益标签；排序在 listwise 损失里按日截面 z-score 后做。"""
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
    feat_set: Optional[str] = None,
) -> SeqPanel:
    """多票堆叠序列面板（float32）；``head`` 可为 HeadSpec 或 id。

    ``feat_set``：``seq8`` / ``seq16`` / ``seq32`` / ``seq64``（默认 ``seq16``）。
    """
    L = max(5, int(seq_len or 20))
    names = seq_feature_names(feat_set)
    F = len(names)
    lab = _label_for_head(head)
    chunks_x: List[np.ndarray] = []
    chunks_g: List[np.ndarray] = []
    chunks_y: List[np.ndarray] = []
    dates: List[str] = []
    codes: List[str] = []
    failed: List[str] = []
    n_total = sum(1 for code in stock_bars if str(code).strip())
    print(f"序列面板 开始 stocks={n_total} L={L} F={F}", flush=True)
    seen = 0
    for code, bars in stock_bars.items():
        code_s = str(code).strip()
        seen += 1
        if seen == 1 or seen % 100 == 0 or seen == n_total:
            print(f"  序列面板 {seen}/{n_total}", flush=True)
        if not code_s:
            continue
        try:
            X, g, y, ds = samples_from_bars(
                code_s,
                bars,
                seq_len=L,
                horizon_days=horizon_days,
                label=lab,
                feat_set=feat_set,
            )
        except Exception:  # noqa: BLE001
            failed.append(code_s)
            logger.debug("seq samples failed code=%s head=%s", code_s, lab, exc_info=True)
            continue
        if X.shape[0] <= 0:
            continue
        chunks_x.append(X)
        chunks_g.append(g)
        chunks_y.append(y)
        dates.extend(ds)
        codes.extend([code_s] * len(ds))
    if failed:
        shown = ",".join(failed[:6])
        print(
            f"  序列面板 跳过 {len(failed)}/{n_total} 只（异常）例如 {shown}",
            flush=True,
        )
        if len(failed) >= 3 and len(failed) * 5 >= n_total:
            raise RuntimeError(
                f"序列面板 异常过多 {len(failed)}/{n_total}，中止"
            )
    if not chunks_x:
        return SeqPanel(
            X=np.zeros((0, L, F), dtype=np.float32),
            gap=np.zeros((0,), dtype=np.float32),
            y=np.zeros((0,), dtype=np.float32),
            dates=[],
            codes=[],
            seq_len=L,
            feature_names=names,
        )
    return SeqPanel(
        X=np.concatenate(chunks_x, axis=0),
        gap=np.concatenate(chunks_g, axis=0),
        y=np.concatenate(chunks_y, axis=0),
        dates=dates,
        codes=codes,
        seq_len=L,
        feature_names=names,
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
    "SEQ8_FEATURE_NAMES",
    "SEQ16_FEATURE_NAMES",
    "SEQ32_FEATURE_NAMES",
    "SEQ64_FEATURE_NAMES",
    "SEQ_FEATURE_NAMES",
    "SEQ_FEAT_SETS",
    "DEFAULT_SEQ_FEAT_SET",
    "GAP_FEATURE_NAME",
    "resolve_seq_feat_set",
    "seq_feature_names",
    "SeqPanel",
    "samples_from_bars",
    "build_seq_panel",
    "assert_pit_last_bar_before_decision",
    "panel_metas",
]
