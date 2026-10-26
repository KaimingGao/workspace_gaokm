"""τ→收专用因子集 alpha_tc（约 224 列，前缀 ``raw_atc_``）。

PIT：``hist=≤T−1`` 完整日 + ``open_t`` + ≤τ 分钟前缀（可空）。
锚价 ``price[τ]``（τ=open ⇒ open_t）。标签对齐 close[T]/price[τ]−1。
单票可算；板块截面类不在此模块（留给面板 ``z_τc_*``）。

与 alpha_oo 平行重写（不以 ``raw_aoo_*`` 复用）；滚动序列用收盘价 + price[τ] 占位。
现网 ŷ_τc 特征包 **alpha_τc := z_τc_* ∪ raw_atc_***（见 ``tc_ridge``）。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_WINDOWS: Tuple[int, ...] = (5, 10, 20, 30, 60)
_MIN_BARS: int = 61
_EPS: float = 1e-12
_OPEN_CLOCK_MIN = 9 * 60 + 30

ALPHA_TC_FACTOR_KEY = "alpha_tc"
ALPHA_TC_RAW_PREFIX = "raw_atc_"
ALPHA_TC_MIN_BARS = _MIN_BARS
ALPHA_TC_PANEL_WINDOW = _MIN_BARS + 1

_YK_KEYS = (
    "YKMID",
    "YKLEN",
    "YKMID2",
    "YKUP",
    "YKUP2",
    "YKLOW",
    "YKLOW2",
    "YKSFT",
    "YKSFT2",
    "YBODY",
    "YUPPER_SHARE",
    "YLOWER_SHARE",
)

_GAP_KEYS = (
    "GAP",
    "GAP_ABS",
    "CLOSE0",
    "YHIGH0",
    "YLOW0",
    "YVWAP0",
    "YOPEN0",
    "TAU_IN_YRANGE",
    "GAP_VS_YRANGE",
    "GAP_ATR",
    "ABOVE_YHIGH",
    "BELOW_YLOW",
    "GAP_UP",
    "GAP_DN",
    "NEAR_UPLIMIT",
    "NEAR_DNLIMIT",
    "YCLOSE_LOC",
    "GAP_X_YVOL",
    "GAP_X_YRANGE",
    "YOPEN_RET",
    "YRANGE_PCT",
    "YVOL_Z20",
    "GAP_VS_TCMA5",
    "GAP_VS_TCMA20",
    "RET_OT",
    "RET_PT",
    "OPEN0",
    "TAU_IN_PREFIX",
)

_ROLL_OPS = (
    "ROC",
    "MA",
    "STD",
    "BETA",
    "RSQR",
    "RESI",
    "MAX",
    "LOW",
    "QTLU",
    "QTLD",
    "RANK",
    "RSV",
    "IMAX",
    "IMIN",
    "IMXD",
    "CORR",
    "CORD",
    "CNTP",
    "CNTN",
    "CNTD",
    "SUMP",
    "SUMN",
    "SUMD",
)

_VOL_OPS = (
    "VMA",
    "VSTD",
    "WVMA",
    "VSUMP",
    "VSUMN",
    "VSUMD",
    "VROC",
    "VRAMA",
)

_LAG_KEYS = (
    "TC_LAG1",
    "TC_LAG2",
    "TC_LAG3",
    "TC_MA5",
    "TC_STD5",
    "CC_LAG1",
    "OC_LAG1",
    "TC_SIGN_MA5",
    "MOM3_C",
    "MOM3_TC",
    "REV1_TC",
    "VOL_SHOCK",
    "RANGE_SHOCK",
    "TC_MA10",
    "TC_STD10",
)

_PREFIX_KEYS = (
    "RANGE_PCT",
    "LOC_HL",
    "UP_EXTENT",
    "DOWN_EXTENT",
    "PATH_SIGN",
    "PULLBACK",
    "BOUNCE",
    "RET_LAST_15M",
    "REALIZED_VOL",
    "VOL_LAST3_VS_AVG",
    "TAU_ELAPSED_MIN",
    "PREFIX_VWAP_DEV",
    "RANGE_EFF",
    "VP_CONFIRM",
)


def _prefix_neutral(tau_hm: str = "open") -> Dict[str, float]:
    """无分钟前缀时的中性值。逐日摘要和整段列共用这一份。"""
    return {
        "RANGE_PCT": 0.0,
        "LOC_HL": 0.5,
        "UP_EXTENT": 0.0,
        "DOWN_EXTENT": 0.0,
        "PATH_SIGN": 0.0,
        "PULLBACK": 0.0,
        "BOUNCE": 0.0,
        "RET_LAST_15M": 0.0,
        "REALIZED_VOL": 0.0,
        "VOL_LAST3_VS_AVG": 1.0,
        "TAU_ELAPSED_MIN": float(_tau_elapsed_min(tau_hm)),
        "PREFIX_VWAP_DEV": 0.0,
        "RANGE_EFF": 0.0,
        "VP_CONFIRM": 0.0,
    }


def _roll_key(op: str, d: int) -> str:
    return f"P{op}{d}"


def _vol_key(op: str, d: int) -> str:
    return f"P{op}{d}"


ALPHA_TC_FIELD_NAMES: Tuple[str, ...] = (
    *_GAP_KEYS,
    *_YK_KEYS,
    *(_roll_key(op, d) for d in _WINDOWS for op in _ROLL_OPS),
    *(_vol_key(op, d) for d in _WINDOWS for op in _VOL_OPS),
    *_LAG_KEYS,
    *_PREFIX_KEYS,
)


def is_alpha_tc_raw_key(name: str) -> bool:
    return str(name or "").startswith(ALPHA_TC_RAW_PREFIX)


def bump_window_for_alpha_tc(
    factor_names: Optional[Sequence[str]],
    *,
    min_history: int,
    max_window: int,
) -> Tuple[int, int]:
    """因子集含 alpha_tc / raw_atc_* 时抬高窗口。"""
    names = {str(n).strip() for n in (factor_names or []) if str(n).strip()}
    if ALPHA_TC_FACTOR_KEY not in names and not any(
        is_alpha_tc_raw_key(n) for n in names
    ):
        return int(min_history), int(max_window)
    need = int(ALPHA_TC_PANEL_WINDOW)
    return max(int(min_history), need), max(int(max_window), need)


def inject_raw_atc(
    dest: Optional[dict],
    bars: Optional[Sequence[dict]],
    *,
    open_t: Optional[float] = None,
    prev_close: Optional[float] = None,
    price_tau: Optional[float] = None,
    minute_bars: Optional[Sequence[dict]] = None,
    tau_hm: Optional[str] = None,
    quote: Optional[dict] = None,
) -> dict:
    """把 ``raw_atc_*`` 写入 ``dest``（就地；失败则原样返回）。"""
    out = dest if isinstance(dest, dict) else {}
    try:
        extras = raw_atc_from_bars(
            bars,
            open_t=open_t,
            prev_close=prev_close,
            price_tau=price_tau,
            minute_bars=minute_bars,
            tau_hm=tau_hm,
            quote=quote,
        )
    except Exception:  # noqa: BLE001
        logger.debug("alpha_tc inject failed", exc_info=True)
        return out
    if extras:
        out.update(extras)
    return out


def keep_atc_raw_in_row(
    src: Optional[dict],
    *,
    dest: Optional[dict] = None,
) -> dict:
    """把 ``src`` 中的 ``raw_atc_*`` 拷进 ``dest``。"""
    out = dest if isinstance(dest, dict) else {}
    for k, v in (src or {}).items():
        if is_alpha_tc_raw_key(str(k)) and v is not None and v != "":
            out[str(k)] = v
    return out


def _fpx(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        n = float(str(v).replace("元", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if n != n or n <= 0:
        return None
    return n


def resolve_open_t(
    *,
    open_t: Optional[float] = None,
    quote: Optional[dict] = None,
    bars: Optional[Sequence[dict]] = None,
) -> Optional[float]:
    ot = _fpx(open_t)
    if ot is not None:
        return ot
    if isinstance(quote, dict):
        for key in ("open", "open_raw", "today_open"):
            ot = _fpx(quote.get(key))
            if ot is not None:
                return ot
    if bars:
        ot = _fpx(bars[-1].get("open"))
        if ot is not None:
            return ot
    return None


def resolve_prev_close(
    *,
    prev_close: Optional[float] = None,
    quote: Optional[dict] = None,
    hist_complete: Optional[Sequence[dict]] = None,
) -> Optional[float]:
    pc = _fpx(prev_close)
    if pc is not None:
        return pc
    if isinstance(quote, dict):
        for key in ("prev_close", "pre_close", "yesterday_close"):
            pc = _fpx(quote.get(key))
            if pc is not None:
                return pc
    hist = [b for b in (hist_complete or []) if isinstance(b, dict)]
    if hist:
        return _fpx(hist[-1].get("close"))
    return None


def _normalize_tau_hm(raw: Any, default: str = "09:30") -> str:
    s = str(raw or "").strip()
    if not s or s.lower() in ("open", "o"):
        return "09:30"
    if ":" in s:
        return s[:5]
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 4:
        return f"{digits[:2]}:{digits[2:4]}"
    return default


def _tau_elapsed_min(tau_hm: str) -> float:
    hm = _normalize_tau_hm(tau_hm)
    try:
        hh, mm = hm.split(":")
        tmin = int(hh) * 60 + int(mm)
    except (TypeError, ValueError):
        return 0.0
    return float(max(0, tmin - _OPEN_CLOCK_MIN))


def _to_arrays(
    bars: List[dict],
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n = len(bars)
    close = np.zeros(n, dtype=np.float64)
    open_ = np.zeros(n, dtype=np.float64)
    high = np.zeros(n, dtype=np.float64)
    low = np.zeros(n, dtype=np.float64)
    volume = np.zeros(n, dtype=np.float64)
    for i, b in enumerate(bars):
        close[i] = float(b.get("close") or 0.0)
        open_[i] = float(b.get("open") or 0.0)
        high[i] = float(b.get("high") or 0.0)
        low[i] = float(b.get("low") or 0.0)
        volume[i] = float(b.get("volume") or 0.0)
    return close, open_, high, low, volume


def _safe_corr(x: np.ndarray, y: np.ndarray) -> Optional[float]:
    if len(x) < 2:
        return None
    sx = float(x.std())
    sy = float(y.std())
    if sx < _EPS or sy < _EPS:
        return None
    cov = float(((x - x.mean()) * (y - y.mean())).mean())
    return cov / (sx * sy)


def _linear_fit(y: np.ndarray) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    n = len(y)
    if n < 2:
        return None, None, None
    x = np.arange(n, dtype=np.float64)
    xm = x.mean()
    ym = y.mean()
    denom = float(((x - xm) ** 2).sum())
    if denom < _EPS:
        return None, None, None
    slope = float(((x - xm) * (y - ym)).sum() / denom)
    intercept = float(ym - slope * xm)
    y_pred = intercept + slope * x
    ss_res = float(((y - y_pred) ** 2).sum())
    ss_tot = float(((y - ym) ** 2).sum())
    rsquare = 1.0 - ss_res / (ss_tot + _EPS) if ss_tot > _EPS else 0.0
    residual = float(y[-1] - y_pred[-1])
    return slope, rsquare, residual


def _linear_quantiles(series: np.ndarray, qs: Sequence[float]) -> List[float]:
    """与 ``np.quantile(..., method='linear')`` 同值，小窗口只排一次序。"""
    ordered = np.sort(np.asarray(series, dtype=np.float64))
    n = int(ordered.shape[0])
    if n <= 0:
        return [float("nan")] * len(tuple(qs))
    if n == 1:
        v = float(ordered[0])
        return [v for _ in qs]
    out: List[float] = []
    last = n - 1
    for q in qs:
        pos = float(q) * last
        lo = int(pos)
        hi = lo + 1 if lo < last else last
        w = pos - lo
        out.append(float(ordered[lo] * (1.0 - w) + ordered[hi] * w))
    return out


def _rank_percentile(current: float, series: np.ndarray) -> Optional[float]:
    if len(series) == 0:
        return None
    return float((series < current).sum()) / len(series)


def _yk_fields(close: float, open_: float, high: float, low: float) -> dict:
    rng = high - low
    mx_oc = max(open_, close)
    mn_oc = min(open_, close)
    if rng > _EPS:
        mid2 = (close - open_) / rng
        up2 = (high - mx_oc) / rng
        low2 = (mn_oc - low) / rng
        sft2 = (2.0 * close - high - low) / rng
        upper = (high - mx_oc) / rng
        lower = (mn_oc - low) / rng
    else:
        mid2 = up2 = low2 = sft2 = upper = lower = 0.0
    return {
        "YKMID": (close - open_) / (open_ + _EPS),
        "YKLEN": (high - low) / (open_ + _EPS),
        "YKMID2": mid2,
        "YKUP": (high - mx_oc) / (open_ + _EPS),
        "YKUP2": up2,
        "YKLOW": (mn_oc - low) / (open_ + _EPS),
        "YKLOW2": low2,
        "YKSFT": (2.0 * close - high - low) / (open_ + _EPS),
        "YKSFT2": sft2,
        "YBODY": abs(close - open_) / (open_ + _EPS),
        "YUPPER_SHARE": upper,
        "YLOWER_SHARE": lower,
    }


def _atr_pct(high: np.ndarray, low: np.ndarray, close: np.ndarray, window: int = 14) -> float:
    n = len(close)
    if n < 2:
        return _EPS
    w = min(window, n - 1)
    trs = []
    for i in range(n - w, n):
        pc = float(close[i - 1])
        h = float(high[i])
        l = float(low[i])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr / (pc + _EPS))
    return float(np.mean(trs)) if trs else _EPS


def _hist_tc_returns(open_: np.ndarray, close: np.ndarray) -> np.ndarray:
    """完整日 close/open−1（不含决策占位）。"""
    n = len(open_)
    if n == 0:
        return np.zeros(0, dtype=np.float64)
    return close / (open_ + _EPS) - 1.0


def _prefix_summary(
    minute_bars: Optional[Sequence[dict]],
    *,
    trade_date: str,
    tau_hm: str,
    open_t: float,
    price_tau: float,
) -> Tuple[Dict[str, float], float, float, float, float]:
    """返回 (prefix 字段, hi, lo, cum_vol, last_px)。无分钟时中性缺省。"""
    defaults = _prefix_neutral(tau_hm)
    hi = float(open_t)
    lo = float(open_t)
    cum_vol = 0.0
    last_px = float(price_tau)

    if not minute_bars or not str(trade_date or "").strip():
        return defaults, hi, lo, cum_vol, last_px

    try:
        from core.signal.minute_tau_feats import _day_bars_upto_tau
    except Exception:  # noqa: BLE001
        return defaults, hi, lo, cum_vol, last_px

    bars = _day_bars_upto_tau(
        list(minute_bars),
        trade_date=str(trade_date)[:10],
        tau_hm=_normalize_tau_hm(tau_hm),
    )
    if len(bars) < 1:
        return defaults, hi, lo, cum_vol, last_px

    highs: List[Tuple[str, float]] = []
    lows: List[Tuple[str, float]] = []
    closes: List[float] = []
    vols: List[float] = []
    for b in bars:
        if not isinstance(b, dict):
            continue
        c = _fpx(b.get("close") if b.get("close") is not None else b.get("price"))
        if c is None:
            continue
        h = _fpx(b.get("high")) or c
        l = _fpx(b.get("low")) or c
        v = float(_fpx(b.get("volume")) or 0.0)
        hm = str(b.get("time") or b.get("hm") or b.get("clock") or "")
        highs.append((hm, float(h)))
        lows.append((hm, float(l)))
        closes.append(float(c))
        vols.append(max(0.0, v))

    if not closes:
        return defaults, hi, lo, cum_vol, last_px

    last_px = float(closes[-1])
    hi = float(max(v for _, v in highs))
    lo = float(min(v for _, v in lows))
    cum_vol = float(sum(vols))
    o = float(open_t)
    px = float(price_tau) if price_tau > _EPS else last_px
    rng = hi - lo
    ret_ot = px / (o + _EPS) - 1.0
    range_pct = rng / (o + _EPS)

    out = dict(defaults)
    out["RANGE_PCT"] = float(range_pct)
    out["UP_EXTENT"] = float((hi - o) / (o + _EPS))
    out["DOWN_EXTENT"] = float((o - lo) / (o + _EPS))
    if rng > _EPS:
        out["LOC_HL"] = float((px - lo) / rng)
    else:
        out["LOC_HL"] = 0.5
    t_hi = min((hm for hm, v in highs if v >= hi - 1e-12), default="")
    t_lo = min((hm for hm, v in lows if v <= lo + 1e-12), default="")
    if t_lo and t_hi:
        out["PATH_SIGN"] = 1.0 if t_lo < t_hi else -1.0
    if hi > _EPS:
        out["PULLBACK"] = float((hi - px) / hi)
    if lo > _EPS:
        out["BOUNCE"] = float((px - lo) / lo)
    n15 = min(3, len(closes))
    if n15 >= 2 and closes[-n15] > _EPS:
        out["RET_LAST_15M"] = float(closes[-1] / closes[-n15] - 1.0)
    rets: List[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] > _EPS:
            rets.append(closes[i] / closes[i - 1] - 1.0)
    if len(rets) >= 2:
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / max(1, len(rets) - 1)
        out["REALIZED_VOL"] = float(math.sqrt(max(0.0, var)))
    if len(vols) >= 3:
        last3 = sum(vols[-3:]) / 3.0
        avg = sum(vols) / len(vols)
        if avg > _EPS:
            out["VOL_LAST3_VS_AVG"] = float(last3 / avg)
    # session VWAP ≈ Σ typical*vol / Σ vol
    typ_sum = 0.0
    for i, c in enumerate(closes):
        h_i = highs[i][1] if i < len(highs) else c
        l_i = lows[i][1] if i < len(lows) else c
        typ_sum += ((h_i + l_i + c) / 3.0) * vols[i]
    if cum_vol > _EPS:
        vwap = typ_sum / cum_vol
        if vwap > _EPS:
            out["PREFIX_VWAP_DEV"] = float(px / vwap - 1.0)
    if range_pct > _EPS:
        out["RANGE_EFF"] = float(min(1.0, abs(ret_ot) / range_pct))
    else:
        out["RANGE_EFF"] = 0.0
    vol_r = float(out["VOL_LAST3_VS_AVG"])
    out["VP_CONFIRM"] = float(max(-0.3, min(0.3, ret_ot * (vol_r - 1.0))))
    out["TAU_ELAPSED_MIN"] = _tau_elapsed_min(tau_hm)
    return out, hi, lo, cum_vol, last_px


def _gap_fields(
    open_t: float,
    price_tau: float,
    prev_close: float,
    prev_open: float,
    prev_high: float,
    prev_low: float,
    prev_vol: float,
    open_arr: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    *,
    prefix_hi: float,
    prefix_lo: float,
) -> dict:
    """隔夜 + τ 位置；数组含决策日占位根；归一分母 = price_τ。"""
    p0 = float(price_tau)
    gap = open_t / (prev_close + _EPS) - 1.0
    y_rng = prev_high - prev_low
    y_vwap = (prev_high + prev_low + prev_close) / 3.0
    atr = _atr_pct(high[:-1], low[:-1], close[:-1], 14)
    o_hist = open_arr[:-1]
    c_hist = close[:-1]
    tc = _hist_tc_returns(o_hist, c_hist)
    tc_ma5 = float(tc[-5:].mean()) if len(tc) >= 5 else 0.0
    tc_ma20 = float(tc[-20:].mean()) if len(tc) >= 20 else tc_ma5
    vol_hist = volume[:-1]
    v_mean20 = float(vol_hist[-20:].mean()) if len(vol_hist) >= 20 else float(prev_vol)
    v_std20 = float(vol_hist[-20:].std()) if len(vol_hist) >= 20 else _EPS
    yvol_z = (prev_vol - v_mean20) / (v_std20 + _EPS)
    up_lim = prev_close * 1.10
    dn_lim = prev_close * 0.90
    y_rng_pct = y_rng / (prev_close + _EPS)
    if y_rng > _EPS:
        tau_in_yrange = (p0 - prev_low) / y_rng
        yclose_loc = (prev_close - prev_low) / y_rng
    else:
        tau_in_yrange = 0.5
        yclose_loc = 0.5
    if y_rng_pct > _EPS:
        gap_vs_yrange = gap / y_rng_pct
    else:
        gap_vs_yrange = 0.0
    pref_rng = float(prefix_hi) - float(prefix_lo)
    if pref_rng > _EPS:
        tau_in_prefix = (p0 - float(prefix_lo)) / pref_rng
    else:
        tau_in_prefix = 0.5
    ret_ot = p0 / (open_t + _EPS) - 1.0
    ret_pt = p0 / (prev_close + _EPS) - 1.0
    return {
        "GAP": gap,
        "GAP_ABS": abs(gap),
        "CLOSE0": prev_close / (p0 + _EPS),
        "YHIGH0": prev_high / (p0 + _EPS),
        "YLOW0": prev_low / (p0 + _EPS),
        "YVWAP0": y_vwap / (p0 + _EPS),
        "YOPEN0": prev_open / (p0 + _EPS),
        "TAU_IN_YRANGE": tau_in_yrange,
        "GAP_VS_YRANGE": gap_vs_yrange,
        "GAP_ATR": gap / (atr + _EPS),
        "ABOVE_YHIGH": 1.0 if p0 > prev_high else 0.0,
        "BELOW_YLOW": 1.0 if p0 < prev_low else 0.0,
        "GAP_UP": max(gap, 0.0),
        "GAP_DN": max(-gap, 0.0),
        "NEAR_UPLIMIT": (up_lim - p0) / (prev_close + _EPS),
        "NEAR_DNLIMIT": (p0 - dn_lim) / (prev_close + _EPS),
        "YCLOSE_LOC": yclose_loc,
        "GAP_X_YVOL": gap * yvol_z,
        "GAP_X_YRANGE": gap * y_rng_pct,
        "YOPEN_RET": prev_open / (close[-3] + _EPS) - 1.0 if len(close) >= 3 else 0.0,
        "YRANGE_PCT": y_rng_pct,
        "YVOL_Z20": yvol_z,
        "GAP_VS_TCMA5": gap - tc_ma5,
        "GAP_VS_TCMA20": gap - tc_ma20,
        "RET_OT": ret_ot,
        "RET_PT": ret_pt,
        "OPEN0": open_t / (p0 + _EPS),
        "TAU_IN_PREFIX": tau_in_prefix,
    }


def _rolling_price_fields(
    price: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
) -> dict:
    """价序列滚动；price[-1]=price_τ，归一分母 p0。"""
    out: dict = {}
    n = len(price)
    p0 = float(price[-1])
    v0 = float(volume[-1])
    px_ret = np.zeros(n, dtype=np.float64)
    px_ret[1:] = price[1:] / (price[:-1] + _EPS) - 1.0
    vol_delta = np.zeros(n, dtype=np.float64)
    vol_delta[1:] = volume[1:] - volume[:-1]
    log_vol = np.log(volume + 1.0)
    log_vol_ret = np.zeros(n, dtype=np.float64)
    log_vol_ret[1:] = np.log(volume[1:] / (volume[:-1] + _EPS) + 1.0)

    for d in _WINDOWS:
        if n < d + 1:
            for op in _ROLL_OPS:
                out[_roll_key(op, d)] = None
            for op in _VOL_OPS:
                out[_vol_key(op, d)] = None
            continue
        p_seg = price[-d:]
        h_seg = high[-d:]
        l_seg = low[-d:]
        v_seg = volume[-d:]
        pr_seg = px_ret[-d:]
        vd_seg = vol_delta[-d:]
        lv_seg = log_vol[-d:]
        lvr_seg = log_vol_ret[-d:]

        out[_roll_key("ROC", d)] = float(price[-(d + 1)] / (p0 + _EPS))
        out[_roll_key("MA", d)] = float(p_seg.mean() / (p0 + _EPS))
        out[_roll_key("STD", d)] = float(p_seg.std() / (p0 + _EPS))
        slope, rsq, resi = _linear_fit(p_seg)
        out[_roll_key("BETA", d)] = (
            float(slope / (p0 + _EPS)) if slope is not None else None
        )
        out[_roll_key("RSQR", d)] = rsq
        out[_roll_key("RESI", d)] = (
            float(resi / (p0 + _EPS)) if resi is not None else None
        )
        out[_roll_key("MAX", d)] = float(h_seg.max() / (p0 + _EPS))
        out[_roll_key("LOW", d)] = float(l_seg.min() / (p0 + _EPS))
        q_hi, q_lo = _linear_quantiles(p_seg, (0.8, 0.2))
        out[_roll_key("QTLU", d)] = float(q_hi / (p0 + _EPS))
        out[_roll_key("QTLD", d)] = float(q_lo / (p0 + _EPS))
        out[_roll_key("RANK", d)] = _rank_percentile(p0, p_seg)
        h_max = float(h_seg.max())
        l_min = float(l_seg.min())
        out[_roll_key("RSV", d)] = float((p0 - l_min) / (h_max - l_min + _EPS))
        out[_roll_key("IMAX", d)] = float(np.argmax(h_seg) / d)
        out[_roll_key("IMIN", d)] = float(np.argmin(l_seg) / d)
        out[_roll_key("IMXD", d)] = float(
            (np.argmax(h_seg) - np.argmin(l_seg)) / d
        )
        out[_roll_key("CORR", d)] = _safe_corr(p_seg, lv_seg)
        out[_roll_key("CORD", d)] = _safe_corr(pr_seg, lvr_seg)
        up = float((pr_seg > 0).mean())
        dn = float((pr_seg < 0).mean())
        out[_roll_key("CNTP", d)] = up
        out[_roll_key("CNTN", d)] = dn
        out[_roll_key("CNTD", d)] = up - dn
        pos_ret = np.where(pr_seg > 0, pr_seg, 0.0)
        neg_ret = np.where(pr_seg < 0, -pr_seg, 0.0)
        abs_sum = float(np.abs(pr_seg).sum())
        out[_roll_key("SUMP", d)] = float(pos_ret.sum() / (abs_sum + _EPS))
        out[_roll_key("SUMN", d)] = float(neg_ret.sum() / (abs_sum + _EPS))
        out[_roll_key("SUMD", d)] = float(
            (pos_ret.sum() - neg_ret.sum()) / (abs_sum + _EPS)
        )

        out[_vol_key("VMA", d)] = float(v_seg.mean() / (v0 + _EPS))
        out[_vol_key("VSTD", d)] = float(v_seg.std() / (v0 + _EPS))
        wvma_input = np.abs(pr_seg) * v_seg
        wvma_mean = float(wvma_input.mean())
        wvma_std = float(wvma_input.std())
        out[_vol_key("WVMA", d)] = (
            float(wvma_std / (wvma_mean + _EPS)) if wvma_mean > _EPS else None
        )
        pos_vd = np.where(vd_seg > 0, vd_seg, 0.0)
        neg_vd = np.where(vd_seg < 0, -vd_seg, 0.0)
        abs_vd_sum = float(np.abs(vd_seg).sum())
        out[_vol_key("VSUMP", d)] = float(pos_vd.sum() / (abs_vd_sum + _EPS))
        out[_vol_key("VSUMN", d)] = float(neg_vd.sum() / (abs_vd_sum + _EPS))
        out[_vol_key("VSUMD", d)] = float(
            (pos_vd.sum() - neg_vd.sum()) / (abs_vd_sum + _EPS)
        )
        v_ref = float(volume[-2]) if n >= 2 else v0
        out[_vol_key("VROC", d)] = float(volume[-(d + 1)] / (v_ref + _EPS))
        p_ma = float(p_seg.mean())
        out[_vol_key("VRAMA", d)] = float(
            (v_seg.mean() / (v_ref + _EPS)) / (p_ma / (p0 + _EPS) + _EPS)
        )

    return out


def _lag_fields(
    open_: np.ndarray,
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
) -> dict:
    """TC 滞后等；含决策占位，完整日为 [:-1]。今日已实现不进本块。"""
    o = open_[:-1]
    c = close[:-1]
    h = high[:-1]
    l = low[:-1]
    v = volume[:-1]
    if len(o) < 5:
        return {k: None for k in _LAG_KEYS}
    tc = c / (o + _EPS) - 1.0
    cc1 = c[-1] / (c[-2] + _EPS) - 1.0 if len(c) >= 2 else 0.0
    oc1 = c[-1] / (o[-1] + _EPS) - 1.0
    rng = (h - l) / (c + _EPS)
    v_ma20 = float(v[-20:].mean()) if len(v) >= 20 else float(v[-1])
    r_ma20 = float(rng[-20:].mean()) if len(rng) >= 20 else float(rng[-1])
    return {
        "TC_LAG1": float(tc[-1]),
        "TC_LAG2": float(tc[-2]) if len(tc) >= 2 else None,
        "TC_LAG3": float(tc[-3]) if len(tc) >= 3 else None,
        "TC_MA5": float(tc[-5:].mean()),
        "TC_STD5": float(tc[-5:].std()),
        "CC_LAG1": float(cc1),
        "OC_LAG1": float(oc1),
        "TC_SIGN_MA5": float(np.sign(tc[-5:]).mean()),
        "MOM3_C": (
            float(c[-1] / (c[-4] + _EPS) - 1.0) if len(c) >= 4 else None
        ),
        "MOM3_TC": float(tc[-3:].sum()) if len(tc) >= 3 else None,
        "REV1_TC": float(-tc[-1]),
        "VOL_SHOCK": float(v[-1] / (v_ma20 + _EPS)),
        "RANGE_SHOCK": float(rng[-1] / (r_ma20 + _EPS)),
        "TC_MA10": float(tc[-10:].mean()) if len(tc) >= 10 else None,
        "TC_STD10": float(tc[-10:].std()) if len(tc) >= 10 else None,
    }


def _resolve_trade_date(
    *,
    quote: Optional[dict],
    hist: Sequence[dict],
    minute_bars: Optional[Sequence[dict]],
) -> str:
    if isinstance(quote, dict):
        d = str(quote.get("date") or quote.get("trade_date") or "")[:10]
        if d:
            return d
    if minute_bars:
        for b in minute_bars:
            if isinstance(b, dict):
                d = str(b.get("trade_date") or b.get("date") or "")[:10]
                if d:
                    return d
    if hist:
        d = str(hist[-1].get("trade_date") or hist[-1].get("date") or "")[:10]
        if d:
            return d
    return ""


def _prepare_tau_pit_bars(
    bars: Sequence[dict],
    *,
    open_t: Optional[float],
    prev_close: Optional[float],
    price_tau: Optional[float],
    minute_bars: Optional[Sequence[dict]],
    tau_hm: Optional[str],
    quote: Optional[dict],
) -> Tuple[
    Optional[List[dict]],
    Optional[float],
    Optional[float],
    Optional[float],
    Dict[str, Any],
    Dict[str, float],
]:
    """返回 (prepared, open_t, prev_close, price_τ, flags, prefix_fields)。"""
    hist = [b for b in (bars or []) if isinstance(b, dict)]
    hm = _normalize_tau_hm(tau_hm)
    ot = resolve_open_t(open_t=open_t, quote=quote, bars=None)
    meta_flags: Dict[str, Any] = {}
    complete: List[dict]

    # 末根只有在它就是本会话时才当今天。hist 已停在 T−1 时不剥，避免退一天。
    if ot is None and hist:
        from core.signal.session_pit import pit_hist_when_open_missing

        action, complete = pit_hist_when_open_missing(
            hist,
            quote=quote,
            minute_bars=minute_bars,
            min_bars=_MIN_BARS,
        )
        if action == "refuse":
            return None, None, None, None, {"alpha_tc_need_open": True}, {}
        if action == "peel":
            ot = _fpx(hist[-1].get("open"))
        if ot is None:
            complete = list(hist)
            ot = resolve_open_t(open_t=open_t, quote=quote, bars=hist)
    else:
        complete = list(hist)

    if ot is None:
        return None, None, None, None, {"alpha_tc_need_open": True}, {}

    if len(complete) < _MIN_BARS - 1:
        return (
            None,
            None,
            None,
            None,
            {"alpha_tc_insufficient_history": True},
            {},
        )

    pc = resolve_prev_close(
        prev_close=prev_close, quote=quote, hist_complete=complete
    )
    if pc is None:
        return None, None, None, None, {"alpha_tc_need_prev_close": True}, {}

    day = _resolve_trade_date(quote=quote, hist=complete, minute_bars=minute_bars)
    # 先用临时 price 抽前缀，再定最终 price_τ
    pt_hint = _fpx(price_tau)
    prefix_fields, pref_hi, pref_lo, cum_vol, last_px = _prefix_summary(
        minute_bars,
        trade_date=day,
        tau_hm=hm,
        open_t=float(ot),
        price_tau=float(pt_hint or ot),
    )

    pt = pt_hint
    if pt is None and last_px > _EPS and minute_bars:
        pt = float(last_px)
    if pt is None:
        pt = float(ot)

    # 用最终 price_τ 重算依赖 px 的前缀交互（RANGE_EFF / VP_CONFIRM / LOC）
    if minute_bars and day:
        prefix_fields, pref_hi, pref_lo, cum_vol, last_px = _prefix_summary(
            minute_bars,
            trade_date=day,
            tau_hm=hm,
            open_t=float(ot),
            price_tau=float(pt),
        )
    else:
        pref_hi = float(ot)
        pref_lo = float(ot)
        cum_vol = 0.0

    y_vol = float(complete[-1].get("volume") or 1.0)
    syn_vol = float(cum_vol) if cum_vol > _EPS else y_vol
    syn = {
        "date": day,
        "trade_date": day,
        "open": float(ot),
        "high": float(max(pref_hi, ot, pt)),
        "low": float(min(pref_lo, ot, pt)),
        "close": float(pt),
        "volume": syn_vol,
        "session_tau_pit": True,
    }
    out_bars = list(complete) + [syn]
    if len(out_bars) < _MIN_BARS:
        return (
            None,
            None,
            None,
            None,
            {"alpha_tc_insufficient_history": True},
            {},
        )
    meta_flags["alpha_tc_tau_hm"] = hm
    meta_flags["prefix_hi"] = float(pref_hi)
    meta_flags["prefix_lo"] = float(pref_lo)
    return out_bars, float(ot), float(pc), float(pt), meta_flags, prefix_fields


def score_alpha_tc(
    bars: Optional[List[dict]] = None,
    *,
    open_t: Optional[float] = None,
    prev_close: Optional[float] = None,
    price_tau: Optional[float] = None,
    minute_bars: Optional[Sequence[dict]] = None,
    tau_hm: Optional[str] = None,
    quote: Optional[dict] = None,
    **_kw: Any,
) -> Tuple[float, dict]:
    """计算 alpha_tc；返回 ``(50.0, meta)``，``omit_sub_score=True``。"""
    prepared, ot, pc, pt, flags, prefix_fields = _prepare_tau_pit_bars(
        list(bars or []),
        open_t=open_t,
        prev_close=prev_close,
        price_tau=price_tau,
        minute_bars=minute_bars,
        tau_hm=tau_hm,
        quote=quote,
    )
    if prepared is None or ot is None or pc is None or pt is None:
        meta = {"omit_sub_score": True}
        meta.update(flags or {"alpha_tc_insufficient_history": True})
        return 50.0, meta

    try:
        close, open_arr, high, low, volume = _to_arrays(prepared)
    except Exception as e:  # noqa: BLE001
        logger.debug("alpha_tc to_arrays failed: %s", e, exc_info=True)
        return 50.0, {"omit_sub_score": True, "alpha_tc_parse_error": str(e)[:120]}

    if float(pt) < _EPS or float(open_arr[-1]) < _EPS:
        return 50.0, {"omit_sub_score": True, "alpha_tc_zero_price": True}
    if len(prepared) < 2:
        return 50.0, {"omit_sub_score": True, "alpha_tc_insufficient_history": True}

    y_o = float(open_arr[-2])
    y_c = float(close[-2])
    y_h = float(high[-2])
    y_l = float(low[-2])
    y_v = float(volume[-2])
    if y_c < _EPS or y_o < _EPS:
        return 50.0, {"omit_sub_score": True, "alpha_tc_bad_prev_bar": True}

    pref_hi = float(flags.get("prefix_hi") or ot)
    pref_lo = float(flags.get("prefix_lo") or ot)

    meta: dict = {
        "omit_sub_score": True,
        "alpha_tc_anchor": "price_tau",
        "alpha_tc_tau_hm": flags.get("alpha_tc_tau_hm") or _normalize_tau_hm(tau_hm),
    }
    try:
        meta.update(
            _gap_fields(
                float(ot),
                float(pt),
                float(pc),
                y_o,
                y_h,
                y_l,
                y_v,
                open_arr,
                high,
                low,
                close,
                volume,
                prefix_hi=pref_hi,
                prefix_lo=pref_lo,
            )
        )
        meta.update(_yk_fields(y_c, y_o, y_h, y_l))
        # 滚动用收盘序列（末根已是 price_τ）
        meta.update(_rolling_price_fields(close, high, low, volume))
        meta.update(_lag_fields(open_arr, close, high, low, volume))
        for k in _PREFIX_KEYS:
            if k in prefix_fields:
                meta[k] = float(prefix_fields[k])
    except Exception as e:  # noqa: BLE001
        logger.debug("alpha_tc compute failed: %s", e, exc_info=True)
        meta["alpha_tc_partial_error"] = str(e)[:120]

    return 50.0, meta


def raw_atc_from_bars(
    bars: Optional[Sequence[dict]],
    *,
    open_t: Optional[float] = None,
    prev_close: Optional[float] = None,
    price_tau: Optional[float] = None,
    minute_bars: Optional[Sequence[dict]] = None,
    tau_hm: Optional[str] = None,
    quote: Optional[dict] = None,
) -> dict:
    _score, meta = score_alpha_tc(
        list(bars or []),
        open_t=open_t,
        prev_close=prev_close,
        price_tau=price_tau,
        minute_bars=minute_bars,
        tau_hm=tau_hm,
        quote=quote,
    )
    if not isinstance(meta, dict):
        return {}
    out: dict = {}
    for mk, mv in meta.items():
        sk = str(mk)
        if sk in ("omit_sub_score", "ok") or sk.startswith("alpha_tc_"):
            continue
        if isinstance(mv, (int, float)) and not isinstance(mv, bool):
            out[f"{ALPHA_TC_RAW_PREFIX}{sk}"] = float(mv)
    return out


def ohlcv_from_bars(
    bars: Sequence[dict],
) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    """与 ``_to_arrays`` 同一套解析。有一根转不成数则返回 None，调用方改走逐日。"""
    n = len(bars or [])
    if n <= 0:
        return None
    open_ = np.empty(n, dtype=np.float64)
    high = np.empty(n, dtype=np.float64)
    low = np.empty(n, dtype=np.float64)
    close = np.empty(n, dtype=np.float64)
    volume = np.empty(n, dtype=np.float64)
    for i, bar in enumerate(bars):
        if not isinstance(bar, dict):
            return None
        try:
            close[i] = float(bar.get("close") or 0.0)
            open_[i] = float(bar.get("open") or 0.0)
            high[i] = float(bar.get("high") or 0.0)
            low[i] = float(bar.get("low") or 0.0)
            volume[i] = float(bar.get("volume") or 0.0)
        except (TypeError, ValueError):
            return None
    return open_, high, low, close, volume


def _rows_corr(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """逐行 ``_safe_corr``。标准差过小的行是 NaN。"""
    sx = x.std(axis=1)
    sy = y.std(axis=1)
    cov = ((x - x.mean(axis=1, keepdims=True)) * (y - y.mean(axis=1, keepdims=True))).mean(
        axis=1
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        out = cov / (sx * sy)
    return np.where((sx < _EPS) | (sy < _EPS), np.nan, out)


def _rows_quantiles(series: np.ndarray, qs: Sequence[float]) -> List[np.ndarray]:
    """逐行线性分位，索引只取决于窗口长度。"""
    ordered = np.sort(series, axis=1)
    n = int(series.shape[1])
    last = n - 1
    out: List[np.ndarray] = []
    for q in qs:
        pos = float(q) * last
        lo = int(pos)
        hi = lo + 1 if lo < last else last
        w = pos - lo
        out.append(ordered[:, lo] * (1.0 - w) + ordered[:, hi] * w)
    return out


def _rows_linear_fit(y: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """逐行 ``_linear_fit``。窗口长度固定且 ≥2。"""
    n = int(y.shape[1])
    x = np.arange(n, dtype=np.float64)
    xm = float(x.mean())
    ym = y.mean(axis=1)
    xc = x - xm
    denom = float((xc * xc).sum())
    slope = (xc * (y - ym[:, None])).sum(axis=1) / denom
    intercept = ym - slope * xm
    y_pred = intercept[:, None] + slope[:, None] * x
    ss_res = ((y - y_pred) ** 2).sum(axis=1)
    ss_tot = ((y - ym[:, None]) ** 2).sum(axis=1)
    rsquare = np.where(ss_tot > _EPS, 1.0 - ss_res / (ss_tot + _EPS), 0.0)
    residual = y[:, -1] - y_pred[:, -1]
    return slope, rsquare, residual


def _as_open_1d(values: Any, n: Optional[int] = None) -> Optional[np.ndarray]:
    """收成一维 float64。维数或长度不对就放弃整段，避免算出半截列。"""
    arr = np.asarray(values, dtype=np.float64)
    if arr.ndim != 1:
        return None
    if n is not None and int(arr.shape[0]) != int(n):
        return None
    if not arr.flags.c_contiguous:
        arr = np.ascontiguousarray(arr)
    return arr


def raw_atc_open_columns(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    *,
    hist: int,
) -> Dict[str, np.ndarray]:
    """开盘 τ、无分钟时，整段日线一次算出 ``raw_atc_*``。

    决策日 ``i >= hist`` 的历史是 ``[i-hist, i)``，再接一根合成 K 线：
    开高低收都是当日开盘价，成交量是昨日成交量。
    返回列与 ``i - hist`` 对齐。NaN 表示逐日路径不写这个键。
    输入不齐或计算中途失败时返回空字典，调用方改走逐日，不留下半截列。
    """
    try:
        cols = _raw_atc_open_columns_impl(
            open_, high, low, close, volume, hist=hist
        )
    except Exception:  # noqa: BLE001
        logger.debug("raw_atc open columns failed", exc_info=True)
        return {}
    return cols if isinstance(cols, dict) else {}


def raw_atc_tau_columns(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    *,
    hist: int,
    price_tau: np.ndarray,
    prefix_hi: np.ndarray,
    prefix_lo: np.ndarray,
    prefix_vol: np.ndarray,
    prefix_fields: Optional[Dict[str, np.ndarray]] = None,
    tau_hm: str = "10:00",
) -> Dict[str, np.ndarray]:
    """一个钟、整段决策日的 ``raw_atc_*``。

    ``price_tau`` / 前缀与 ``i - hist`` 对齐。失败或形状不对时返回空字典，
    调用方改走逐日，不留下半截列。
    """
    try:
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            cols = _raw_atc_open_columns_impl(
                open_,
                high,
                low,
                close,
                volume,
                hist=hist,
                price_tau=price_tau,
                prefix_hi=prefix_hi,
                prefix_lo=prefix_lo,
                prefix_vol=prefix_vol,
                prefix_fields=prefix_fields,
                tau_hm=tau_hm,
            )
    except Exception:  # noqa: BLE001
        logger.debug("raw_atc tau columns failed", exc_info=True)
        return {}
    return cols if isinstance(cols, dict) else {}


def _anchor_or(values: Any, fallback: np.ndarray, n: int) -> Optional[np.ndarray]:
    if values is None:
        return fallback
    return _as_open_1d(values, n)


def _raw_atc_open_columns_impl(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    *,
    hist: int,
    price_tau: Any = None,
    prefix_hi: Any = None,
    prefix_lo: Any = None,
    prefix_vol: Any = None,
    prefix_fields: Optional[Dict[str, Any]] = None,
    tau_hm: str = "open",
) -> Dict[str, np.ndarray]:
    from numpy.lib.stride_tricks import sliding_window_view

    hist_n = int(hist)
    if hist_n < 60:
        return {}
    close = _as_open_1d(close)
    if close is None:
        return {}
    n = int(close.shape[0])
    if n <= hist_n:
        return {}
    open_ = _as_open_1d(open_, n)
    high = _as_open_1d(high, n)
    low = _as_open_1d(low, n)
    volume = _as_open_1d(volume, n)
    if open_ is None or high is None or low is None or volume is None:
        return {}
    m = n - hist_n
    idx = np.arange(hist_n, n)
    open_t = open_[idx]
    y_open = open_[idx - 1]
    y_high = high[idx - 1]
    y_low = low[idx - 1]
    y_close = close[idx - 1]
    y_vol = volume[idx - 1]
    pt = _anchor_or(price_tau, open_t, m)
    pref_hi = _anchor_or(prefix_hi, open_t, m)
    pref_lo = _anchor_or(prefix_lo, open_t, m)
    cum_vol = _anchor_or(prefix_vol, np.zeros(m, dtype=np.float64), m)
    if pt is None or pref_hi is None or pref_lo is None or cum_vol is None:
        return {}
    valid = (open_t >= _EPS) & (pt >= _EPS) & (y_open >= _EPS) & (y_close >= _EPS)

    hist_o = sliding_window_view(open_, hist_n)[:m]
    hist_h = sliding_window_view(high, hist_n)[:m]
    hist_l = sliding_window_view(low, hist_n)[:m]
    hist_c = sliding_window_view(close, hist_n)[:m]
    hist_v = sliding_window_view(volume, hist_n)[:m]
    # 昨量为 0 时，无前缀成交量的合成根写成 1，与 ``_prepare_tau_pit_bars`` 一致。
    y_or_1 = np.where(y_vol != 0.0, y_vol, 1.0)
    syn_vol = np.where(cum_vol > _EPS, cum_vol, y_or_1)
    syn_high = np.maximum(np.maximum(pref_hi, open_t), pt)
    syn_low = np.minimum(np.minimum(pref_lo, open_t), pt)
    price = np.hstack([hist_c, pt[:, None]])
    hi = np.hstack([hist_h, syn_high[:, None]])
    lo = np.hstack([hist_l, syn_low[:, None]])
    vol = np.hstack([hist_v, syn_vol[:, None]])
    width = int(price.shape[1])
    p0 = price[:, -1]
    v0 = vol[:, -1]
    px_ret = np.zeros((m, width), dtype=np.float64)
    px_ret[:, 1:] = price[:, 1:] / (price[:, :-1] + _EPS) - 1.0
    vol_delta = np.zeros((m, width), dtype=np.float64)
    vol_delta[:, 1:] = vol[:, 1:] - vol[:, :-1]
    log_vol = np.log(vol + 1.0)
    log_vol_ret = np.zeros((m, width), dtype=np.float64)
    log_vol_ret[:, 1:] = np.log(vol[:, 1:] / (vol[:, :-1] + _EPS) + 1.0)

    cols: Dict[str, np.ndarray] = {}

    def _put(name: str, values: np.ndarray) -> None:
        cols[f"{ALPHA_TC_RAW_PREFIX}{name}"] = np.asarray(values, dtype=np.float64)

    gap = open_t / (y_close + _EPS) - 1.0
    y_rng = y_high - y_low
    y_rng_pct = y_rng / (y_close + _EPS)
    y_vwap = (y_high + y_low + y_close) / 3.0
    tr_num = np.maximum(
        hist_h[:, 1:] - hist_l[:, 1:],
        np.maximum(
            np.abs(hist_h[:, 1:] - hist_c[:, :-1]),
            np.abs(hist_l[:, 1:] - hist_c[:, :-1]),
        ),
    )
    atr = (tr_num / (hist_c[:, :-1] + _EPS))[:, -14:].mean(axis=1)
    tc_hist = hist_c / (hist_o + _EPS) - 1.0
    tc_ma5 = tc_hist[:, -5:].mean(axis=1)
    tc_ma20 = tc_hist[:, -20:].mean(axis=1)
    v_mean20 = hist_v[:, -20:].mean(axis=1)
    v_std20 = hist_v[:, -20:].std(axis=1)
    yvol_z = (y_vol - v_mean20) / (v_std20 + _EPS)
    up_lim = y_close * 1.10
    dn_lim = y_close * 0.90
    wide = y_rng > _EPS
    tau_in_yrange = np.where(wide, (p0 - y_low) / np.where(wide, y_rng, 1.0), 0.5)
    yclose_loc = np.where(wide, (y_close - y_low) / np.where(wide, y_rng, 1.0), 0.5)
    wide_pct = y_rng_pct > _EPS
    gap_vs_yrange = np.where(wide_pct, gap / np.where(wide_pct, y_rng_pct, 1.0), 0.0)
    _put("GAP", gap)
    _put("GAP_ABS", np.abs(gap))
    _put("CLOSE0", y_close / (p0 + _EPS))
    _put("YHIGH0", y_high / (p0 + _EPS))
    _put("YLOW0", y_low / (p0 + _EPS))
    _put("YVWAP0", y_vwap / (p0 + _EPS))
    _put("YOPEN0", y_open / (p0 + _EPS))
    _put("TAU_IN_YRANGE", tau_in_yrange)
    _put("GAP_VS_YRANGE", gap_vs_yrange)
    _put("GAP_ATR", gap / (atr + _EPS))
    _put("ABOVE_YHIGH", np.where(p0 > y_high, 1.0, 0.0))
    _put("BELOW_YLOW", np.where(p0 < y_low, 1.0, 0.0))
    _put("GAP_UP", np.maximum(gap, 0.0))
    _put("GAP_DN", np.maximum(-gap, 0.0))
    _put("NEAR_UPLIMIT", (up_lim - p0) / (y_close + _EPS))
    _put("NEAR_DNLIMIT", (p0 - dn_lim) / (y_close + _EPS))
    _put("YCLOSE_LOC", yclose_loc)
    _put("GAP_X_YVOL", gap * yvol_z)
    _put("GAP_X_YRANGE", gap * y_rng_pct)
    _put("YOPEN_RET", y_open / (hist_c[:, -2] + _EPS) - 1.0)
    _put("YRANGE_PCT", y_rng_pct)
    _put("YVOL_Z20", yvol_z)
    _put("GAP_VS_TCMA5", gap - tc_ma5)
    _put("GAP_VS_TCMA20", gap - tc_ma20)
    _put("RET_OT", p0 / (open_t + _EPS) - 1.0)
    _put("RET_PT", p0 / (y_close + _EPS) - 1.0)
    _put("OPEN0", open_t / (p0 + _EPS))
    pref_rng = pref_hi - pref_lo
    _put(
        "TAU_IN_PREFIX",
        np.where(
            pref_rng > _EPS,
            (p0 - pref_lo) / np.where(pref_rng > _EPS, pref_rng, 1.0),
            0.5,
        ),
    )

    y_rng_bar = y_high - y_low
    mx_oc = np.maximum(y_open, y_close)
    mn_oc = np.minimum(y_open, y_close)
    bar_wide = y_rng_bar > _EPS
    denom_rng = np.where(bar_wide, y_rng_bar, 1.0)
    mid2 = np.where(bar_wide, (y_close - y_open) / denom_rng, 0.0)
    up2 = np.where(bar_wide, (y_high - mx_oc) / denom_rng, 0.0)
    low2 = np.where(bar_wide, (mn_oc - y_low) / denom_rng, 0.0)
    sft2 = np.where(bar_wide, (2.0 * y_close - y_high - y_low) / denom_rng, 0.0)
    _put("YKMID", (y_close - y_open) / (y_open + _EPS))
    _put("YKLEN", (y_high - y_low) / (y_open + _EPS))
    _put("YKMID2", mid2)
    _put("YKUP", (y_high - mx_oc) / (y_open + _EPS))
    _put("YKUP2", up2)
    _put("YKLOW", (mn_oc - y_low) / (y_open + _EPS))
    _put("YKLOW2", low2)
    _put("YKSFT", (2.0 * y_close - y_high - y_low) / (y_open + _EPS))
    _put("YKSFT2", sft2)
    _put("YBODY", np.abs(y_close - y_open) / (y_open + _EPS))
    _put("YUPPER_SHARE", up2)
    _put("YLOWER_SHARE", low2)

    for d in _WINDOWS:
        p_seg = price[:, -d:]
        h_seg = hi[:, -d:]
        l_seg = lo[:, -d:]
        v_seg = vol[:, -d:]
        pr_seg = px_ret[:, -d:]
        vd_seg = vol_delta[:, -d:]
        lv_seg = log_vol[:, -d:]
        lvr_seg = log_vol_ret[:, -d:]
        p_mean = p_seg.mean(axis=1)
        _put(_roll_key("ROC", d), price[:, -(d + 1)] / (p0 + _EPS))
        _put(_roll_key("MA", d), p_mean / (p0 + _EPS))
        _put(_roll_key("STD", d), p_seg.std(axis=1) / (p0 + _EPS))
        slope, rsq, resi = _rows_linear_fit(p_seg)
        _put(_roll_key("BETA", d), slope / (p0 + _EPS))
        _put(_roll_key("RSQR", d), rsq)
        _put(_roll_key("RESI", d), resi / (p0 + _EPS))
        h_max = h_seg.max(axis=1)
        l_min = l_seg.min(axis=1)
        _put(_roll_key("MAX", d), h_max / (p0 + _EPS))
        _put(_roll_key("LOW", d), l_min / (p0 + _EPS))
        q_hi, q_lo = _rows_quantiles(p_seg, (0.8, 0.2))
        _put(_roll_key("QTLU", d), q_hi / (p0 + _EPS))
        _put(_roll_key("QTLD", d), q_lo / (p0 + _EPS))
        _put(_roll_key("RANK", d), (p_seg < p0[:, None]).sum(axis=1) / float(d))
        _put(_roll_key("RSV", d), (p0 - l_min) / (h_max - l_min + _EPS))
        _put(_roll_key("IMAX", d), np.argmax(h_seg, axis=1) / float(d))
        _put(_roll_key("IMIN", d), np.argmin(l_seg, axis=1) / float(d))
        _put(
            _roll_key("IMXD", d),
            (np.argmax(h_seg, axis=1) - np.argmin(l_seg, axis=1)) / float(d),
        )
        _put(_roll_key("CORR", d), _rows_corr(p_seg, lv_seg))
        _put(_roll_key("CORD", d), _rows_corr(pr_seg, lvr_seg))
        up = (pr_seg > 0).mean(axis=1)
        dn = (pr_seg < 0).mean(axis=1)
        _put(_roll_key("CNTP", d), up)
        _put(_roll_key("CNTN", d), dn)
        _put(_roll_key("CNTD", d), up - dn)
        pos_ret = np.where(pr_seg > 0, pr_seg, 0.0)
        neg_ret = np.where(pr_seg < 0, -pr_seg, 0.0)
        abs_sum = np.abs(pr_seg).sum(axis=1)
        _put(_roll_key("SUMP", d), pos_ret.sum(axis=1) / (abs_sum + _EPS))
        _put(_roll_key("SUMN", d), neg_ret.sum(axis=1) / (abs_sum + _EPS))
        _put(
            _roll_key("SUMD", d),
            (pos_ret.sum(axis=1) - neg_ret.sum(axis=1)) / (abs_sum + _EPS),
        )
        v_mean = v_seg.mean(axis=1)
        _put(_vol_key("VMA", d), v_mean / (v0 + _EPS))
        _put(_vol_key("VSTD", d), v_seg.std(axis=1) / (v0 + _EPS))
        wvma_input = np.abs(pr_seg) * v_seg
        wvma_mean = wvma_input.mean(axis=1)
        wvma_std = wvma_input.std(axis=1)
        wvma = np.where(wvma_mean > _EPS, wvma_std / (wvma_mean + _EPS), np.nan)
        _put(_vol_key("WVMA", d), wvma)
        pos_vd = np.where(vd_seg > 0, vd_seg, 0.0)
        neg_vd = np.where(vd_seg < 0, -vd_seg, 0.0)
        abs_vd = np.abs(vd_seg).sum(axis=1)
        _put(_vol_key("VSUMP", d), pos_vd.sum(axis=1) / (abs_vd + _EPS))
        _put(_vol_key("VSUMN", d), neg_vd.sum(axis=1) / (abs_vd + _EPS))
        _put(
            _vol_key("VSUMD", d),
            (pos_vd.sum(axis=1) - neg_vd.sum(axis=1)) / (abs_vd + _EPS),
        )
        v_ref = vol[:, -2]
        _put(_vol_key("VROC", d), vol[:, -(d + 1)] / (v_ref + _EPS))
        _put(
            _vol_key("VRAMA", d),
            (v_mean / (v_ref + _EPS)) / (p_mean / (p0 + _EPS) + _EPS),
        )

    tc = hist_c / (hist_o + _EPS) - 1.0
    c_hist = hist_c
    o_hist = hist_o
    h_hist = hist_h
    l_hist = hist_l
    v_hist = hist_v
    cc1 = c_hist[:, -1] / (c_hist[:, -2] + _EPS) - 1.0
    oc1 = c_hist[:, -1] / (o_hist[:, -1] + _EPS) - 1.0
    rng = (h_hist - l_hist) / (c_hist + _EPS)
    _put("TC_LAG1", tc[:, -1])
    _put("TC_LAG2", tc[:, -2])
    _put("TC_LAG3", tc[:, -3])
    _put("TC_MA5", tc[:, -5:].mean(axis=1))
    _put("TC_STD5", tc[:, -5:].std(axis=1))
    _put("CC_LAG1", cc1)
    _put("OC_LAG1", oc1)
    _put("TC_SIGN_MA5", np.sign(tc[:, -5:]).mean(axis=1))
    _put("MOM3_C", c_hist[:, -1] / (c_hist[:, -4] + _EPS) - 1.0)
    _put("MOM3_TC", tc[:, -3:].sum(axis=1))
    _put("REV1_TC", -tc[:, -1])
    _put("VOL_SHOCK", v_hist[:, -1] / (v_hist[:, -20:].mean(axis=1) + _EPS))
    _put("RANGE_SHOCK", rng[:, -1] / (rng[:, -20:].mean(axis=1) + _EPS))
    _put("TC_MA10", tc[:, -10:].mean(axis=1))
    _put("TC_STD10", tc[:, -10:].std(axis=1))

    prefix_defaults = _prefix_neutral(str(tau_hm or "open"))
    supplied = prefix_fields if isinstance(prefix_fields, dict) else {}
    for name, value in prefix_defaults.items():
        custom = supplied.get(name)
        if custom is None:
            _put(name, np.full(m, float(value)))
            continue
        arr = _as_open_1d(custom, m)
        if arr is None:
            logger.debug("raw_atc prefix %s dropped", name)
            _put(name, np.full(m, float(value)))
            continue
        _put(name, arr)

    if not np.all(valid):
        for key in cols:
            cols[key] = np.where(valid, cols[key], np.nan)
    return cols
