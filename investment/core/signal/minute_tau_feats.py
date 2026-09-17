"""分钟 τ 小包特征：开盘→τ 路径摘要（PIT，仅用 ≤τ 的 5m）。"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 单票可算（无截面）
MINUTE_TAU_PACK_KEYS = (
    "ret_open_to_tau",
    "ret_prev_to_tau",
    "range_pct",
    "loc_hl",
    "up_extent",
    "down_extent",
    "path_sign",
    "pullback_from_high",
    "bounce_from_low",
    "ret_last_15m",
    "realized_vol",
    "vol_last3_vs_avg",
    "tau_elapsed_min",
)

# 截面附着后可写
MINUTE_TAU_CS_KEYS = (
    "sector_ret_to_tau",
    "ret_vs_sector",
)

MINUTE_TAU_ALL_KEYS = MINUTE_TAU_PACK_KEYS + MINUTE_TAU_CS_KEYS

# 标签同构前缀形状：path 用 t_*_frac；不进 TAU_Z / 共用 PATH_Z
_OPEN_CLOCK_MIN = 9 * 60 + 30
MINUTE_TAU_SHAPE_KEYS = (
    "t_hi_frac",
    "t_lo_frac",
)
MINUTE_TAU_PATH_SHAPE_KEYS = ("t_hi_frac", "t_lo_frac")

MINUTE_TAU_FEAT_LABELS = {
    "ret_open_to_tau": "开盘→τ 收益 %",
    "ret_prev_to_tau": "昨收→τ 收益 %",
    "range_pct": "前缀振幅 %",
    "loc_hl": "HL 位置 0–1",
    "up_extent": "相对开盘上探 %",
    "down_extent": "相对开盘下探 %",
    "path_sign": "路径符号(+先低后高)",
    "pullback_from_high": "自高回撤 %",
    "bounce_from_low": "自低反弹 %",
    "ret_last_15m": "近15m 收益 %",
    "ret_last_5m": "近5m 收益 %",
    "ret_last_30m": "近30交易分钟收益 %",
    "ret_last_60m": "近60交易分钟收益 %",
    "ret_last_90m": "近90交易分钟收益 %",
    "session_elapsed": "已过交易分钟（09:30=0）",
    "session_remain": "距收盘剩余交易分钟",
    "crosses_lunch": "未来30m是否跨午休",
    "crosses_lunch_60": "未来60m是否跨午休",
    "crosses_lunch_90": "未来90m是否跨午休",
    "session_vwap_dev": "τ价相对会话VWAP %",
    "vol_last_30m_vs_avg": "近30m量/前缀均量",
    "vol_last_60m_vs_avg": "近60m量/前缀均量",
    "vol_last_90m_vs_avg": "近90m量/前缀均量",
    "sector_ret_last_30m": "板块中位近30m %",
    "ret_last_30m_vs_sector": "近30m相对板块 %",
    "sector_ret_last_60m": "板块中位近60m %",
    "ret_last_60m_vs_sector": "近60m相对板块 %",
    "sector_ret_last_90m": "板块中位近90m %",
    "ret_last_90m_vs_sector": "近90m相对板块 %",
    "realized_vol": "前缀已实现波动 %",
    "vol_last3_vs_avg": "近3根量/均量",
    "tau_elapsed_min": "τ距开盘分钟",
    "sector_ret_to_tau": "板块中位开→τ %",
    "ret_vs_sector": "开→τ 相对板块 %",
    "t_hi_frac": "最高点相对前缀进度",
    "t_lo_frac": "最低点相对前缀进度",
}

T30_SEQ_TRAIL_KEYS = (
    "ret_last_5m",
    "ret_last_30m",
    "session_elapsed",
    "session_remain",
    "crosses_lunch",
)
# 单票可算（无截面）；inject 时整包弹出再写，避免上一钟 leftover
T30_SEQ_PACK_KEYS = T30_SEQ_TRAIL_KEYS + (
    "session_vwap_dev",
    "vol_last_30m_vs_avg",
)
T30_SEQ_CS_KEYS = (
    "sector_ret_last_30m",
    "ret_last_30m_vs_sector",
)
T60_SEQ_TRAIL_KEYS = (
    "ret_last_5m",
    "ret_last_60m",
    "session_elapsed",
    "session_remain",
    "crosses_lunch_60",
)
T60_SEQ_PACK_KEYS = T60_SEQ_TRAIL_KEYS + (
    "session_vwap_dev",
    "vol_last_60m_vs_avg",
)
T60_SEQ_CS_KEYS = (
    "sector_ret_last_60m",
    "ret_last_60m_vs_sector",
)
T90_SEQ_TRAIL_KEYS = (
    "ret_last_5m",
    "ret_last_90m",
    "session_elapsed",
    "session_remain",
    "crosses_lunch_90",
)
T90_SEQ_PACK_KEYS = T90_SEQ_TRAIL_KEYS + (
    "session_vwap_dev",
    "vol_last_90m_vs_avg",
)
T90_SEQ_CS_KEYS = (
    "sector_ret_last_90m",
    "ret_last_90m_vs_sector",
)


def _hm_minutes(hm: str) -> Optional[int]:
    """HHMM / HH:MM → 当日分钟数。"""
    s = str(hm or "").replace(":", "")
    if len(s) < 4 or not s[:4].isdigit():
        return None
    return int(s[:2]) * 60 + int(s[2:4])


def _bar_hm(b: dict) -> str:
    t = str(b.get("time") or b.get("datetime") or b.get("date") or "")
    for part in t.replace("T", " ").split(" "):
        if ":" in part:
            return part[:5].replace(":", "")
    return ""


def _bar_date(b: dict) -> str:
    d = str(b.get("date") or "")[:10]
    if len(d) == 10:
        return d
    t = str(b.get("datetime") or "")
    return t[:10] if len(t) >= 10 else ""


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x


_MINUTE_BY_DATE_INDEX: Dict[int, Tuple[int, Dict[str, List[dict]]]] = {}
_MINUTE_BY_DATE_INDEX_MAX = 48


def _filter_day_bars_upto_tau(
    rows: Sequence[dict],
    *,
    day: str,
    target: str,
    match_date: bool,
) -> List[dict]:
    out: List[dict] = []
    for b in rows or []:
        if not isinstance(b, dict):
            continue
        if match_date and _bar_date(b) != day and day not in str(b.get("datetime") or ""):
            continue
        hm = _bar_hm(b)
        if not hm or hm > target:
            continue
        c = _f(b.get("close") if b.get("close") is not None else b.get("price"))
        if c is None or c <= 0:
            continue
        out.append(b)
    out.sort(key=lambda x: _bar_hm(x))
    return out


def _minute_bars_by_date(minute_bars: Sequence[dict]) -> Dict[str, List[dict]]:
    """按日切分钟线；同伴全历史反复抽 τ 包时避免每钟扫几千根。"""
    seq = minute_bars or []
    key = id(seq)
    n = len(seq)
    hit = _MINUTE_BY_DATE_INDEX.get(key)
    if hit is not None and hit[0] == n:
        return hit[1]
    by: Dict[str, List[dict]] = {}
    for b in seq:
        if not isinstance(b, dict):
            continue
        d = _bar_date(b)
        if len(d) < 10:
            dt = str(b.get("datetime") or "")
            d = dt[:10] if len(dt) >= 10 else d
        if len(d) < 10:
            continue
        by.setdefault(d, []).append(b)
    if len(_MINUTE_BY_DATE_INDEX) >= _MINUTE_BY_DATE_INDEX_MAX:
        _MINUTE_BY_DATE_INDEX.clear()
    _MINUTE_BY_DATE_INDEX[key] = (n, by)
    return by


def _day_bars_upto_tau(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str,
) -> List[dict]:
    day = str(trade_date or "")[:10]
    target = str(tau_hm or "").replace(":", "")
    if not day or not target:
        return []
    seq = minute_bars or []
    # 做T前缀通常 <1 日；同伴仓才是跨日全历史
    if len(seq) <= 96:
        return _filter_day_bars_upto_tau(seq, day=day, target=target, match_date=True)
    by = _minute_bars_by_date(seq)
    return _filter_day_bars_upto_tau(
        by.get(day) or [], day=day, target=target, match_date=False
    )


def _format_hm_colon(raw: str) -> str:
    s = str(raw or "").replace(":", "")
    if len(s) >= 4 and s[:4].isdigit():
        return f"{s[:2]}:{s[2:4]}"
    return str(raw or "")[:5]


def prefix_has_tau_clock(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str,
) -> bool:
    """当日 ≤τ 前缀是否含 τ 这一根（有根才可把 as_of 标成该钟）。"""
    target = str(tau_hm or "").replace(":", "")[:4]
    if len(target) < 4:
        return False
    for b in _day_bars_upto_tau(
        minute_bars, trade_date=trade_date, tau_hm=tau_hm
    ):
        if _bar_hm(b) == target:
            return True
    return False


_OPEN_T_HM_MIN = 9 * 60 + 30
_OPEN_T_HM_MAX = 9 * 60 + 35


def session_first_minute_open(
    minute_bars: Optional[Sequence[dict]] = None,
    trade_date: str = "",
) -> Optional[float]:
    """T 日 09:30–09:35 根的 ``open``。更晚才出现的第一根不当今开；不用 close。"""
    day = str(trade_date or "")[:10]
    if len(day) < 10:
        return None
    first_tmin: Optional[int] = None
    first_open: Optional[float] = None
    for b in _day_minute_bars(minute_bars or [], trade_date=day):
        tmin = _hm_minutes(_bar_hm(b))
        if tmin is None:
            continue
        o = _f(b.get("open"))
        if o is None or o <= 0:
            continue
        if first_tmin is None or tmin < first_tmin:
            first_tmin = tmin
            first_open = o
    if first_tmin is None or first_open is None:
        return None
    if first_tmin < _OPEN_T_HM_MIN or first_tmin > _OPEN_T_HM_MAX:
        return None
    return float(first_open)


def minutes_for_open_t(
    code: str,
    *,
    fetch: bool = False,
    max_age_hours: float = 36.0,
) -> List[dict]:
    """给 ``resolve_open_t`` 备 5m：先本地仓，缺且 fetch 再拉网。"""
    raw = str(code or "").strip()
    if not raw:
        return []
    bars = _load_minute_bars_from_cache(raw, max_age_hours=float(max_age_hours))
    if bars:
        return bars
    if fetch:
        return _fetch_minute_bars_for_tau(raw) or []
    return []


def _day_minute_bars(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
) -> List[dict]:
    day = str(trade_date or "")[:10]
    if len(day) < 10:
        return []
    out: List[dict] = []
    for b in minute_bars or []:
        if not isinstance(b, dict):
            continue
        d = _bar_date(b)
        if len(d) < 10:
            dt = str(b.get("datetime") or "")
            d = dt[:10] if len(dt) >= 10 else d
        if d == day:
            out.append(b)
    return out


def causal_rebalance_tau_hm(
    minute_bars: Optional[Sequence[dict]] = None,
    *,
    trade_date: str,
    cap_hm: Optional[str] = None,
) -> Optional[str]:
    """调仓 ŷ_oc 因果钟：当日已有 5m 末根，且不超过调仓窗结束（默认 10:00）。

    无当日根 / 尚无 09:30 根 → None（只用开盘 Z）。
    """
    from core.signal.minute_tau_grid import REBALANCE_TAU_CAP_HM

    day = str(trade_date or "")[:10]
    cap = str(cap_hm or REBALANCE_TAU_CAP_HM).replace(":", "")[:4]
    if len(day) < 10 or len(cap) < 4:
        return None
    last = ""
    for b in _day_minute_bars(minute_bars or [], trade_date=day):
        hm = _bar_hm(b)
        if not hm or hm > cap:
            continue
        if hm >= last:
            last = hm
    if not last:
        return None
    return _format_hm_colon(last)


def clear_minute_tau_pack_keys(
    feats: Dict[str, Any],
    *,
    include_cs: bool = True,
) -> Dict[str, Any]:
    """去掉开→τ 小包。缺根时必须清掉，禁止收盘 leftover 顶 10:30。

    ``include_cs=False``：只清单票路径键，保留已算的 ``sector_ret_to_tau``
    （做 T 前缀 inject 不得把打分用过的截面抹掉）。
    形状键始终清掉（与路径小包同生命周期）。
    """
    out = feats if isinstance(feats, dict) else {}
    keys = MINUTE_TAU_ALL_KEYS if include_cs else MINUTE_TAU_PACK_KEYS
    for k in keys:
        out.pop(k, None)
    for k in MINUTE_TAU_SHAPE_KEYS:
        out.pop(k, None)
    return out


def extract_minute_tau_pack(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str = "10:30",
    open_px: Optional[float] = None,
    prev_close: Optional[float] = None,
) -> Dict[str, float]:
    """从当日 ≤τ 分钟线提取小包特征；不足 1 根返回空 dict（09:30 无前缀）。"""
    bars = _day_bars_upto_tau(
        minute_bars, trade_date=trade_date, tau_hm=tau_hm
    )
    if len(bars) < 1:
        return {}

    o = _f(open_px)
    if o is None or o <= 0:
        o = _f(bars[0].get("open")) or _f(bars[0].get("close"))
    if o is None or o <= 0:
        return {}

    highs: List[Tuple[str, float]] = []
    lows: List[Tuple[str, float]] = []
    closes: List[float] = []
    vols: List[float] = []
    times_min: List[int] = []
    for b in bars:
        hm = _bar_hm(b)
        h = _f(b.get("high"))
        l = _f(b.get("low"))
        c = _f(b.get("close") if b.get("close") is not None else b.get("price"))
        v = _f(b.get("volume")) or 0.0
        if c is None or c <= 0:
            continue
        tmin = _hm_minutes(hm)
        if tmin is None:
            continue
        if h is None or h <= 0:
            h = c
        if l is None or l <= 0:
            l = c
        highs.append((hm, float(h)))
        lows.append((hm, float(l)))
        closes.append(float(c))
        vols.append(max(0.0, float(v)))
        times_min.append(int(tmin))

    if len(closes) < 1:
        return {}

    px = closes[-1]
    hi = max(v for _, v in highs)
    lo = min(v for _, v in lows)
    # 极值首次出现时刻（并列取更早）
    t_hi = min((hm for hm, v in highs if v >= hi - 1e-12), default="")
    t_lo = min((hm for hm, v in lows if v <= lo + 1e-12), default="")

    out: Dict[str, float] = {}
    out["ret_open_to_tau"] = round((px / o - 1.0) * 100.0, 6)
    pc = _f(prev_close)
    if pc is not None and pc > 0:
        out["ret_prev_to_tau"] = round((px / pc - 1.0) * 100.0, 6)

    rng = hi - lo
    out["range_pct"] = round((rng / o) * 100.0, 6)
    out["up_extent"] = round(((hi - o) / o) * 100.0, 6)
    out["down_extent"] = round(((o - lo) / o) * 100.0, 6)
    if rng > 1e-12:
        out["loc_hl"] = round((px - lo) / rng, 6)
    else:
        out["loc_hl"] = 0.5

    if t_lo and t_hi:
        out["path_sign"] = 1.0 if t_lo < t_hi else -1.0
    if hi > 1e-12:
        out["pullback_from_high"] = round(((hi - px) / hi) * 100.0, 6)
    if lo > 1e-12:
        out["bounce_from_low"] = round(((px - lo) / lo) * 100.0, 6)

    # 近 15m ≈ 3 根 5m
    n15 = min(3, len(closes))
    if n15 >= 2 and closes[-n15] > 0:
        out["ret_last_15m"] = round(
            (closes[-1] / closes[-n15] - 1.0) * 100.0, 6
        )

    rets: List[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            rets.append((closes[i] / closes[i - 1] - 1.0) * 100.0)
    if len(rets) >= 2:
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / max(1, len(rets) - 1)
        out["realized_vol"] = round(math.sqrt(max(0.0, var)), 6)

    if len(vols) >= 3:
        last3 = sum(vols[-3:]) / 3.0
        avg = sum(vols) / len(vols)
        if avg > 1e-12:
            out["vol_last3_vs_avg"] = round(last3 / avg, 6)

    last_min = times_min[-1] if times_min else None
    if last_min is not None and last_min > _OPEN_CLOCK_MIN:
        elapsed = float(last_min - _OPEN_CLOCK_MIN)
        thi = _hm_minutes(t_hi)
        tlo = _hm_minutes(t_lo)
        if thi is not None:
            out["t_hi_frac"] = round(
                max(0.0, min(1.0, (float(thi) - _OPEN_CLOCK_MIN) / elapsed)), 6
            )
        if tlo is not None:
            out["t_lo_frac"] = round(
                max(0.0, min(1.0, (float(tlo) - _OPEN_CLOCK_MIN) / elapsed)), 6
            )

    return out


def _typical_px(h: Optional[float], l: Optional[float], c: float) -> float:
    hh = float(h) if h is not None and h > 0 else c
    ll = float(l) if l is not None and l > 0 else c
    return (hh + ll + c) / 3.0


def _extract_multi_horizon_seq_packs(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str,
    open_px: Optional[float] = None,
    horizons: Sequence[Tuple[int, str, str, str]],
) -> Dict[str, float]:
    """共用 ≤τ 前缀，一次写出多视界序列键（交易时钟 + 近 5m + VWAP + trail 收益/量比）。"""
    from core.signal.minute_tau_grid import (
        horizon_crosses_lunch,
        session_elapsed,
        session_remain,
        sub_session_minutes,
    )

    out: Dict[str, float] = {}
    se = session_elapsed(tau_hm)
    if se is not None:
        out["session_elapsed"] = float(se)
        rem = session_remain(tau_hm)
        if rem is not None:
            out["session_remain"] = float(rem)
    for trail_min, _ret_key, _vol_key, lunch_key in horizons:
        cross = horizon_crosses_lunch(tau_hm, add_min=int(trail_min))
        if cross is not None:
            out[lunch_key] = float(cross)

    bars = _day_bars_upto_tau(
        minute_bars, trade_date=trade_date, tau_hm=tau_hm
    )
    rows: List[Tuple[str, float, float, float]] = []
    for b in bars:
        hm = _bar_hm(b)
        c = _f(b.get("close") if b.get("close") is not None else b.get("price"))
        if not hm or c is None or c <= 0:
            continue
        h = _f(b.get("high"))
        l = _f(b.get("low"))
        v = _f(b.get("volume")) or 0.0
        rows.append((hm, float(c), max(0.0, float(v)), _typical_px(h, l, float(c))))
    if len(rows) >= 2 and rows[-2][1] > 0:
        out["ret_last_5m"] = round(
            (rows[-1][1] / rows[-2][1] - 1.0) * 100.0, 6
        )
    px_now = rows[-1][1] if rows else None
    dollar = 0.0
    vol_sum = 0.0
    for _hm, _c, v, typ in rows:
        if v > 0 and typ > 0:
            dollar += typ * v
            vol_sum += v
    if px_now is not None and px_now > 0 and vol_sum > 1e-12:
        vwap = dollar / vol_sum
        out["session_vwap_dev"] = round((px_now - vwap) / px_now * 100.0, 6)

    prefix_vols = [v for _hm, _c, v, _typ in rows]
    open_fallback: Optional[float] = None
    for trail_min, ret_key, vol_key, _lunch_key in horizons:
        prev_hm = sub_session_minutes(tau_hm, int(trail_min))
        if not prev_hm:
            continue
        want = str(prev_hm).replace(":", "")[:4]
        px_prev: Optional[float] = None
        trail_vols: List[float] = []
        for hm, c, v, _typ in rows:
            if hm == want:
                px_prev = c
            if hm > want:
                trail_vols.append(v)
        if px_prev is None and want == "0930":
            if open_fallback is None:
                o = _f(open_px)
                if o is None or o <= 0:
                    o = _f(bars[0].get("open")) if bars else None
                open_fallback = float(o) if o is not None and o > 0 else 0.0
            if open_fallback > 0:
                px_prev = open_fallback
        if px_prev is not None and px_prev > 0 and px_now is not None and px_now > 0:
            out[ret_key] = round((px_now / px_prev - 1.0) * 100.0, 6)
        if trail_vols and prefix_vols:
            last_avg = sum(trail_vols) / float(len(trail_vols))
            avg = sum(prefix_vols) / float(len(prefix_vols))
            if avg > 1e-12:
                out[vol_key] = round(last_avg / avg, 6)
    return out


def _extract_horizon_seq_pack(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str,
    open_px: Optional[float] = None,
    trail_min: int = 30,
    ret_key: str = "ret_last_30m",
    vol_key: str = "vol_last_30m_vs_avg",
    lunch_key: str = "crosses_lunch",
) -> Dict[str, float]:
    """交易时钟 + 近 5m / trail + VWAP/量比（PIT，≤τ）。缺根留空。"""
    return _extract_multi_horizon_seq_packs(
        minute_bars,
        trade_date=trade_date,
        tau_hm=tau_hm,
        open_px=open_px,
        horizons=((int(trail_min), ret_key, vol_key, lunch_key),),
    )


_T30_T60_T90_HORIZONS: Tuple[Tuple[int, str, str, str], ...] = (
    (30, "ret_last_30m", "vol_last_30m_vs_avg", "crosses_lunch"),
    (60, "ret_last_60m", "vol_last_60m_vs_avg", "crosses_lunch_60"),
    (90, "ret_last_90m", "vol_last_90m_vs_avg", "crosses_lunch_90"),
)


def extract_t30_t60_t90_seq_packs(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str,
    open_px: Optional[float] = None,
) -> Dict[str, float]:
    """一次走 ≤τ 前缀，写出 ŷ_τ30 / ŷ_τ60 / ŷ_τ90 序列键。"""
    return _extract_multi_horizon_seq_packs(
        minute_bars,
        trade_date=trade_date,
        tau_hm=tau_hm,
        open_px=open_px,
        horizons=_T30_T60_T90_HORIZONS,
    )


def extract_t30_seq_pack(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str,
    open_px: Optional[float] = None,
) -> Dict[str, float]:
    """ŷ_τ30 序列特征：交易时钟 + 近 5m/30m + VWAP/量比（PIT，≤τ）。缺根留空。"""
    return _extract_horizon_seq_pack(
        minute_bars,
        trade_date=trade_date,
        tau_hm=tau_hm,
        open_px=open_px,
        trail_min=30,
        ret_key="ret_last_30m",
        vol_key="vol_last_30m_vs_avg",
        lunch_key="crosses_lunch",
    )


def extract_t60_seq_pack(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str,
    open_px: Optional[float] = None,
) -> Dict[str, float]:
    """ŷ_τ60 序列特征：交易时钟 + 近 5m/60m + VWAP/量比（PIT，≤τ）。缺根留空。"""
    return _extract_horizon_seq_pack(
        minute_bars,
        trade_date=trade_date,
        tau_hm=tau_hm,
        open_px=open_px,
        trail_min=60,
        ret_key="ret_last_60m",
        vol_key="vol_last_60m_vs_avg",
        lunch_key="crosses_lunch_60",
    )


def extract_t90_seq_pack(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str,
    open_px: Optional[float] = None,
) -> Dict[str, float]:
    """ŷ_τ90 序列特征：交易时钟 + 近 5m/90m + VWAP/量比（PIT，≤τ）。缺根留空。"""
    return _extract_horizon_seq_pack(
        minute_bars,
        trade_date=trade_date,
        tau_hm=tau_hm,
        open_px=open_px,
        trail_min=90,
        ret_key="ret_last_90m",
        vol_key="vol_last_90m_vs_avg",
        lunch_key="crosses_lunch_90",
    )


def attach_ret_vs_sector(feats: Dict[str, Any]) -> None:
    """有 ret_open_to_tau 与 sector_ret_to_tau 时写 ret_vs_sector。"""
    if not isinstance(feats, dict):
        return
    a = _f(feats.get("ret_open_to_tau"))
    b = _f(feats.get("sector_ret_to_tau"))
    if a is None or b is None:
        return
    feats["ret_vs_sector"] = round(float(a) - float(b), 6)


def attach_ret_last_30m_vs_sector(feats: Dict[str, Any]) -> None:
    """有 ret_last_30m 与 sector_ret_last_30m 时写相对板块。"""
    if not isinstance(feats, dict):
        return
    a = _f(feats.get("ret_last_30m"))
    b = _f(feats.get("sector_ret_last_30m"))
    if a is None or b is None:
        return
    feats["ret_last_30m_vs_sector"] = round(float(a) - float(b), 6)


def attach_ret_last_60m_vs_sector(feats: Dict[str, Any]) -> None:
    """有 ret_last_60m 与 sector_ret_last_60m 时写相对板块。"""
    if not isinstance(feats, dict):
        return
    a = _f(feats.get("ret_last_60m"))
    b = _f(feats.get("sector_ret_last_60m"))
    if a is None or b is None:
        return
    feats["ret_last_60m_vs_sector"] = round(float(a) - float(b), 6)


def attach_ret_last_90m_vs_sector(feats: Dict[str, Any]) -> None:
    """有 ret_last_90m 与 sector_ret_last_90m 时写相对板块。"""
    if not isinstance(feats, dict):
        return
    a = _f(feats.get("ret_last_90m"))
    b = _f(feats.get("sector_ret_last_90m"))
    if a is None or b is None:
        return
    feats["ret_last_90m_vs_sector"] = round(float(a) - float(b), 6)


def sector_ret_median(peer_rets: Sequence[Any]) -> Optional[float]:
    """同伴 ``ret_open_to_tau`` 中位数（与 ``tau_panel.attach_cross_section_breadth`` 同口径）。"""
    vals: List[float] = []
    for v in peer_rets or []:
        x = _f(v)
        if x is not None:
            vals.append(float(x))
    if not vals:
        return None
    vals.sort()
    n = len(vals)
    mid = n // 2
    if n % 2:
        return round(vals[mid], 6)
    return round(0.5 * (vals[mid - 1] + vals[mid]), 6)


def apply_sector_ret_cs(
    feats: Optional[Dict[str, Any]],
    sector_ret_to_tau: Optional[float],
    *,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """写入截面开→τ 中位，并派生 ``ret_vs_sector``。

    默认已有非空 ``sector_ret_to_tau`` 不覆盖；``overwrite=True`` 时重写并重算
    ``ret_vs_sector``（画像/前缀因果打分对齐训练截面）。
    """
    out = dict(feats or {})
    sret = _f(sector_ret_to_tau)
    if sret is not None and (overwrite or out.get("sector_ret_to_tau") is None):
        out["sector_ret_to_tau"] = round(float(sret), 6)
    if overwrite:
        out.pop("ret_vs_sector", None)
    attach_ret_vs_sector(out)
    return out


def apply_sector_ret_last_30m_cs(
    feats: Optional[Dict[str, Any]],
    sector_ret_last_30m: Optional[float],
    *,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """写入截面近 30 交易分钟中位，并派生 ``ret_last_30m_vs_sector``。"""
    out = dict(feats or {})
    sret = _f(sector_ret_last_30m)
    if sret is not None and (overwrite or out.get("sector_ret_last_30m") is None):
        out["sector_ret_last_30m"] = round(float(sret), 6)
    if overwrite:
        out.pop("ret_last_30m_vs_sector", None)
    attach_ret_last_30m_vs_sector(out)
    return out


def apply_sector_ret_last_60m_cs(
    feats: Optional[Dict[str, Any]],
    sector_ret_last_60m: Optional[float],
    *,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """写入截面近 60 交易分钟中位，并派生 ``ret_last_60m_vs_sector``。"""
    out = dict(feats or {})
    sret = _f(sector_ret_last_60m)
    if sret is not None and (overwrite or out.get("sector_ret_last_60m") is None):
        out["sector_ret_last_60m"] = round(float(sret), 6)
    if overwrite:
        out.pop("ret_last_60m_vs_sector", None)
    attach_ret_last_60m_vs_sector(out)
    return out


def apply_sector_ret_last_90m_cs(
    feats: Optional[Dict[str, Any]],
    sector_ret_last_90m: Optional[float],
    *,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """写入截面近 90 交易分钟中位，并派生 ``ret_last_90m_vs_sector``。"""
    out = dict(feats or {})
    sret = _f(sector_ret_last_90m)
    if sret is not None and (overwrite or out.get("sector_ret_last_90m") is None):
        out["sector_ret_last_90m"] = round(float(sret), 6)
    if overwrite:
        out.pop("ret_last_90m_vs_sector", None)
    attach_ret_last_90m_vs_sector(out)
    return out


# (trade_date, tau_hm) → 池中位；做 T 回测同日多票复用
_SECTOR_RET_CACHE: Dict[Tuple[str, str], Optional[float]] = {}
_SECTOR_RET_30M_CACHE: Dict[Tuple[str, str], Optional[float]] = {}
_SECTOR_RET_60M_CACHE: Dict[Tuple[str, str], Optional[float]] = {}
_SECTOR_RET_90M_CACHE: Dict[Tuple[str, str], Optional[float]] = {}
# 同伴分钟线进程缓存：回测 20 日×4 槽会反复 load_minute_cache，未缓存时单票可卡 ~1 分钟
_PEER_MINUTE_BARS: Dict[str, List[dict]] = {}
_PEER_CODES_MEMO: Optional[List[str]] = None
_SECTOR_RET_DEFAULT_CAP = 24


def clear_sector_ret_cache() -> None:
    global _PEER_CODES_MEMO
    _SECTOR_RET_CACHE.clear()
    _SECTOR_RET_30M_CACHE.clear()
    _SECTOR_RET_60M_CACHE.clear()
    _SECTOR_RET_90M_CACHE.clear()
    _PEER_MINUTE_BARS.clear()
    _MINUTE_BY_DATE_INDEX.clear()
    _PEER_CODES_MEMO = None


def _flush_sector_ret_medians() -> None:
    _SECTOR_RET_CACHE.clear()
    _SECTOR_RET_30M_CACHE.clear()
    _SECTOR_RET_60M_CACHE.clear()
    _SECTOR_RET_90M_CACHE.clear()


def seed_peer_minute_bars(
    code: str,
    bars: Optional[Sequence[dict]],
    *,
    flush_cs: bool = True,
) -> None:
    """把本轮已 hydrate 的 5m 写入同伴仓。

    预演补拉 / 回测刷新后，内存仓若仍顶着早盘残缺日，``sector_ret_*`` 会漂，
    ŷ_oc / ŷ_τ30/60/90 / Ĉ_τ 就和历史回测对不齐。
    """
    key = str(code or "").strip()
    rows = [b for b in (bars or []) if isinstance(b, dict)]
    if not key or not rows:
        return
    days = {d for b in rows if len(d := _bar_date(b)) >= 10}
    kept: List[dict] = []
    for b in _PEER_MINUTE_BARS.get(key) or []:
        if not isinstance(b, dict):
            continue
        d = _bar_date(b)
        if len(d) >= 10 and d in days:
            continue
        kept.append(b)
    _PEER_MINUTE_BARS[key] = kept + list(rows)
    if flush_cs:
        _flush_sector_ret_medians()


def seed_peer_minute_bars_map(
    bars_by_code: Optional[Dict[str, Sequence[dict]]],
) -> None:
    seeded = False
    for raw, bars in (bars_by_code or {}).items():
        rows = [b for b in (bars or []) if isinstance(b, dict)]
        if not rows:
            continue
        seed_peer_minute_bars(str(raw), rows, flush_cs=False)
        seeded = True
    if seeded:
        _flush_sector_ret_medians()


def _peer_minute_bars_for_tau(
    code_key: str,
    *,
    trade_date: str,
    tau_hm: str,
) -> List[dict]:
    """同伴 5m：内存仓若缺当日 τ 根则回磁盘重载，避免早盘空仓一直顶到收盘。"""
    key = str(code_key or "").strip()
    day = str(trade_date or "")[:10]
    hm = str(tau_hm or "").strip()[:5]
    if not key:
        return []

    def _covers(rows: Sequence[dict]) -> bool:
        if not rows:
            return False
        if len(day) < 10 or not hm:
            return True
        try:
            return prefix_has_tau_clock(rows, trade_date=day, tau_hm=hm)
        except Exception:
            return bool(rows)

    bars = _PEER_MINUTE_BARS.get(key)
    if bars is not None and _covers(bars):
        return list(bars)
    try:
        from core.ports.market import resolve_market_code
        from core.store import load_minute_cache

        mkt, pure = resolve_market_code(key)
        packed = load_minute_cache(
            mkt or "CN",
            pure or key,
            period="5",
            min_bars=2,
            ignore_age=True,
        )
    except Exception:
        packed = None
    rows = list((packed[0] if packed else None) or [])
    _PEER_MINUTE_BARS[key] = rows
    return rows


def resolve_sector_ret_to_tau(
    trade_date: str,
    tau_hm: str = "10:30",
    *,
    codes: Optional[Sequence[str]] = None,
    known_rets: Optional[Sequence[Any]] = None,
    cap: int = _SECTOR_RET_DEFAULT_CAP,
    use_cache: bool = True,
) -> Optional[float]:
    """解析 ``sector_ret_to_tau``：优先已知同伴收益，否则读活跃簿/观察池/分钟仓宇宙。

    与训练 ``attach_cross_section_breadth`` 一致：对 (date, τ) 下多票 ``ret_open_to_tau`` 取中位。
    """
    day = str(trade_date or "")[:10]
    hm = str(tau_hm or "10:30").strip() or "10:30"
    if len(day) < 10:
        return None

    if known_rets is not None:
        return sector_ret_median(known_rets)

    cache_key = (day, hm)
    if use_cache and cache_key in _SECTOR_RET_CACHE:
        return _SECTOR_RET_CACHE[cache_key]

    peer_codes = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    if not peer_codes:
        peer_codes = _peer_codes_for_sector_ret(cap=max(8, int(cap or _SECTOR_RET_DEFAULT_CAP)))
    if not peer_codes:
        if use_cache:
            _SECTOR_RET_CACHE[cache_key] = None
        return None

    rets: List[float] = []
    for raw in peer_codes[: max(8, int(cap or _SECTOR_RET_DEFAULT_CAP))]:
        code_key = str(raw or "").strip()
        if not code_key:
            continue
        bars = _peer_minute_bars_for_tau(code_key, trade_date=day, tau_hm=hm)
        if len(bars) < 2:
            continue
        pack = extract_minute_tau_pack(
            bars,
            trade_date=day,
            tau_hm=hm,
        )
        rot = _f(pack.get("ret_open_to_tau"))
        if rot is not None:
            rets.append(float(rot))

    med = sector_ret_median(rets)
    if use_cache:
        _SECTOR_RET_CACHE[cache_key] = med
    return med


def attach_sector_ret_cs_if_missing(
    feats: Optional[Dict[str, Any]],
    *,
    trade_date: str,
    tau_hm: str = "10:30",
) -> Dict[str, Any]:
    """live / 持仓补开→τ 截面。缺则 Ridge 把 CS 当 z=0，ŷ_τ 会比做 T 前缀矮一截。"""
    out = dict(feats or {})
    if out.get("ret_open_to_tau") is None:
        return out
    if out.get("sector_ret_to_tau") is not None:
        attach_ret_vs_sector(out)
        return out
    day = str(trade_date or "")[:10]
    hm = str(tau_hm or "10:30").strip()[:5] or "10:30"
    if len(day) < 10:
        return out
    try:
        sret = resolve_sector_ret_to_tau(day, hm)
    except Exception:
        sret = None
    if sret is None:
        attach_ret_vs_sector(out)
        return out
    return apply_sector_ret_cs(out, sret)


def resolve_sector_ret_last_30m(
    trade_date: str,
    tau_hm: str = "10:30",
    *,
    codes: Optional[Sequence[str]] = None,
    known_rets: Optional[Sequence[Any]] = None,
    cap: int = _SECTOR_RET_DEFAULT_CAP,
    use_cache: bool = True,
) -> Optional[float]:
    """解析 ``sector_ret_last_30m``：同伴 ``ret_last_30m`` 中位；复用开→τ 同伴分钟仓。"""
    day = str(trade_date or "")[:10]
    hm = str(tau_hm or "10:30").strip() or "10:30"
    if len(day) < 10:
        return None

    if known_rets is not None:
        return sector_ret_median(known_rets)

    cache_key = (day, hm)
    if use_cache and cache_key in _SECTOR_RET_30M_CACHE:
        return _SECTOR_RET_30M_CACHE[cache_key]

    peer_codes = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    if not peer_codes:
        peer_codes = _peer_codes_for_sector_ret(cap=max(8, int(cap or _SECTOR_RET_DEFAULT_CAP)))
    if not peer_codes:
        if use_cache:
            _SECTOR_RET_30M_CACHE[cache_key] = None
        return None

    rets: List[float] = []
    for raw in peer_codes[: max(8, int(cap or _SECTOR_RET_DEFAULT_CAP))]:
        code_key = str(raw or "").strip()
        if not code_key:
            continue
        bars = _peer_minute_bars_for_tau(code_key, trade_date=day, tau_hm=hm)
        if len(bars) < 2:
            continue
        pack = extract_t30_seq_pack(
            bars,
            trade_date=day,
            tau_hm=hm,
        )
        rot = _f(pack.get("ret_last_30m"))
        if rot is not None:
            rets.append(float(rot))

    med = sector_ret_median(rets)
    if use_cache:
        _SECTOR_RET_30M_CACHE[cache_key] = med
    return med


def attach_sector_ret_last_30m_cs_if_missing(
    feats: Optional[Dict[str, Any]],
    *,
    trade_date: str,
    tau_hm: str = "10:30",
) -> Dict[str, Any]:
    """live / 前缀补近 30m 截面。缺则 Ridge 把 CS 当 z=0。"""
    out = dict(feats or {})
    if out.get("ret_last_30m") is None:
        return out
    if out.get("sector_ret_last_30m") is not None:
        attach_ret_last_30m_vs_sector(out)
        return out
    day = str(trade_date or "")[:10]
    hm = str(tau_hm or "10:30").strip()[:5] or "10:30"
    if len(day) < 10:
        return out
    try:
        sret = resolve_sector_ret_last_30m(day, hm)
    except Exception:
        sret = None
    if sret is None:
        attach_ret_last_30m_vs_sector(out)
        return out
    return apply_sector_ret_last_30m_cs(out, sret)


def resolve_sector_ret_last_60m(
    trade_date: str,
    tau_hm: str = "10:30",
    *,
    codes: Optional[Sequence[str]] = None,
    known_rets: Optional[Sequence[Any]] = None,
    cap: int = _SECTOR_RET_DEFAULT_CAP,
    use_cache: bool = True,
) -> Optional[float]:
    """解析 ``sector_ret_last_60m``：同伴 ``ret_last_60m`` 中位；复用开→τ 同伴分钟仓。"""
    day = str(trade_date or "")[:10]
    hm = str(tau_hm or "10:30").strip() or "10:30"
    if len(day) < 10:
        return None

    if known_rets is not None:
        return sector_ret_median(known_rets)

    cache_key = (day, hm)
    if use_cache and cache_key in _SECTOR_RET_60M_CACHE:
        return _SECTOR_RET_60M_CACHE[cache_key]

    peer_codes = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    if not peer_codes:
        peer_codes = _peer_codes_for_sector_ret(cap=max(8, int(cap or _SECTOR_RET_DEFAULT_CAP)))
    if not peer_codes:
        if use_cache:
            _SECTOR_RET_60M_CACHE[cache_key] = None
        return None

    rets: List[float] = []
    for raw in peer_codes[: max(8, int(cap or _SECTOR_RET_DEFAULT_CAP))]:
        code_key = str(raw or "").strip()
        if not code_key:
            continue
        bars = _peer_minute_bars_for_tau(code_key, trade_date=day, tau_hm=hm)
        if len(bars) < 2:
            continue
        pack = extract_t60_seq_pack(
            bars,
            trade_date=day,
            tau_hm=hm,
        )
        rot = _f(pack.get("ret_last_60m"))
        if rot is not None:
            rets.append(float(rot))

    med = sector_ret_median(rets)
    if use_cache:
        _SECTOR_RET_60M_CACHE[cache_key] = med
    return med


def attach_sector_ret_last_60m_cs_if_missing(
    feats: Optional[Dict[str, Any]],
    *,
    trade_date: str,
    tau_hm: str = "10:30",
) -> Dict[str, Any]:
    """live / 前缀补近 60m 截面。缺则 Ridge 把 CS 当 z=0。"""
    out = dict(feats or {})
    if out.get("ret_last_60m") is None:
        return out
    if out.get("sector_ret_last_60m") is not None:
        attach_ret_last_60m_vs_sector(out)
        return out
    day = str(trade_date or "")[:10]
    hm = str(tau_hm or "10:30").strip()[:5] or "10:30"
    if len(day) < 10:
        return out
    try:
        sret = resolve_sector_ret_last_60m(day, hm)
    except Exception:
        sret = None
    if sret is None:
        attach_ret_last_60m_vs_sector(out)
        return out
    return apply_sector_ret_last_60m_cs(out, sret)


def resolve_sector_ret_last_90m(
    trade_date: str,
    tau_hm: str = "10:30",
    *,
    codes: Optional[Sequence[str]] = None,
    known_rets: Optional[Sequence[Any]] = None,
    cap: int = _SECTOR_RET_DEFAULT_CAP,
    use_cache: bool = True,
) -> Optional[float]:
    """解析 ``sector_ret_last_90m``：同伴 ``ret_last_90m`` 中位；复用开→τ 同伴分钟仓。"""
    day = str(trade_date or "")[:10]
    hm = str(tau_hm or "10:30").strip() or "10:30"
    if len(day) < 10:
        return None

    if known_rets is not None:
        return sector_ret_median(known_rets)

    cache_key = (day, hm)
    if use_cache and cache_key in _SECTOR_RET_90M_CACHE:
        return _SECTOR_RET_90M_CACHE[cache_key]

    peer_codes = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    if not peer_codes:
        peer_codes = _peer_codes_for_sector_ret(cap=max(8, int(cap or _SECTOR_RET_DEFAULT_CAP)))
    if not peer_codes:
        if use_cache:
            _SECTOR_RET_90M_CACHE[cache_key] = None
        return None

    rets: List[float] = []
    for raw in peer_codes[: max(8, int(cap or _SECTOR_RET_DEFAULT_CAP))]:
        code_key = str(raw or "").strip()
        if not code_key:
            continue
        bars = _peer_minute_bars_for_tau(code_key, trade_date=day, tau_hm=hm)
        if len(bars) < 2:
            continue
        pack = extract_t90_seq_pack(
            bars,
            trade_date=day,
            tau_hm=hm,
        )
        rot = _f(pack.get("ret_last_90m"))
        if rot is not None:
            rets.append(float(rot))

    med = sector_ret_median(rets)
    if use_cache:
        _SECTOR_RET_90M_CACHE[cache_key] = med
    return med


def attach_sector_ret_last_90m_cs_if_missing(
    feats: Optional[Dict[str, Any]],
    *,
    trade_date: str,
    tau_hm: str = "10:30",
) -> Dict[str, Any]:
    """live / 前缀补近 90m 截面。缺则 Ridge 把 CS 当 z=0。"""
    out = dict(feats or {})
    if out.get("ret_last_90m") is None:
        return out
    if out.get("sector_ret_last_90m") is not None:
        attach_ret_last_90m_vs_sector(out)
        return out
    day = str(trade_date or "")[:10]
    hm = str(tau_hm or "10:30").strip()[:5] or "10:30"
    if len(day) < 10:
        return out
    try:
        sret = resolve_sector_ret_last_90m(day, hm)
    except Exception:
        sret = None
    if sret is None:
        attach_ret_last_90m_vs_sector(out)
        return out
    return apply_sector_ret_last_90m_cs(out, sret)


def set_peer_codes_for_sector_ret(codes: Optional[Sequence[str]]) -> None:
    """盯盘/回测注入同伴宇宙（持仓码）；空则下次仍走持仓宇宙/观察池。"""
    global _PEER_CODES_MEMO
    out: List[str] = []
    seen = set()
    for raw in codes or []:
        c = str(raw or "").strip()
        if not c or c in seen:
            continue
        seen.add(c)
        out.append(c)
    _PEER_CODES_MEMO = out or None


def peer_codes_for_sector_ret(*, cap: int = _SECTOR_RET_DEFAULT_CAP) -> List[str]:
    """公开同伴宇宙：注入持仓码优先，不把观察池写进 memo。"""
    return _peer_codes_for_sector_ret(cap=cap)


def _peer_codes_for_sector_ret(*, cap: int = _SECTOR_RET_DEFAULT_CAP) -> List[str]:
    """同伴宇宙：注入码 / 持仓宇宙 → 观察池 → 本地分钟仓。兜底不写 memo。"""
    n = max(8, int(cap or _SECTOR_RET_DEFAULT_CAP))
    if _PEER_CODES_MEMO is not None:
        return list(_PEER_CODES_MEMO[:n])
    try:
        from core.t0.score_policy import current_t0_cs_universe_codes

        injected = current_t0_cs_universe_codes()
        if injected:
            return list(injected[:n])
    except Exception:
        pass
    try:
        from core.t0.score_policy import active_book_codes_for_tau_pool

        codes = active_book_codes_for_tau_pool(cap=n)
        if codes:
            return list(codes[:n])
    except Exception:
        pass
    try:
        from core.paths import WATCHING_PATH
        import json
        import os

        if os.path.isfile(WATCHING_PATH):
            with open(WATCHING_PATH, encoding="utf-8") as f:
                doc = json.load(f) or {}
            wl = doc.get("watchlist") if isinstance(doc, dict) else None
            out = []
            seen = set()
            if isinstance(wl, list):
                for x in wl:
                    c = str(x or "").strip()
                    if isinstance(x, dict):
                        c = str(x.get("stock_code") or x.get("code") or "").strip()
                    if not c or c in seen:
                        continue
                    seen.add(c)
                    out.append(c)
                    if len(out) >= n:
                        return list(out)
            if out:
                return list(out)
    except Exception:
        pass
    try:
        from core.store import get_store_dir
        import sqlite3
        import os

        db = os.path.join(get_store_dir(), "bars.db")
        if not os.path.isfile(db):
            return []
        conn = sqlite3.connect(db)
        try:
            rows = conn.execute(
                "SELECT DISTINCT code FROM minute_cache_meta ORDER BY code LIMIT ?",
                (n,),
            ).fetchall()
        finally:
            conn.close()
        return [str(r[0]).strip() for r in rows if r and str(r[0]).strip()]
    except Exception:
        return []


def _load_minute_bars_from_cache(
    code: str,
    *,
    max_age_hours: float,
) -> List[dict]:
    try:
        from core.ports.market import resolve_market_code
        from core.store import load_minute_cache

        mkt, pure = resolve_market_code(str(code))
        packed = load_minute_cache(
            mkt or "CN",
            pure or str(code),
            period="5",
            min_bars=1,
            max_age_hours=float(max_age_hours),
        )
        if packed:
            return [b for b in (packed[0] or []) if isinstance(b, dict)]
        # 超龄仓仍可能含当日 10:30 根；先 ignore_age 再决定要不要拉网
        packed = load_minute_cache(
            mkt or "CN",
            pure or str(code),
            period="5",
            min_bars=1,
            max_age_hours=float(max_age_hours),
            ignore_age=True,
        )
        if packed:
            return [b for b in (packed[0] or []) if isinstance(b, dict)]
    except Exception:
        return []
    return []


def _fetch_minute_bars_for_tau(code: str) -> List[dict]:
    """缺 τ 根时拉 5m。调用方已确认不是因果前缀（未传入 minute_bars）。"""
    try:
        from core.ports.market import fetch_minute_bars

        bars, _meta = fetch_minute_bars(
            str(code),
            period="5",
            use_cache=True,
            lookback_days=10,
            max_age_hours=0.01,
        )
        return [b for b in (bars or []) if isinstance(b, dict)]
    except Exception:
        return []


def merge_minute_tau_pack_into_feats(
    feats: Optional[Dict[str, Any]],
    *,
    code: str = "",
    trade_date: str,
    open_px: Optional[float] = None,
    prev_close: Optional[float] = None,
    tau_hm: Optional[str] = None,
    minute_bars: Optional[Sequence[dict]] = None,
    load_cache_if_missing: bool = True,
    max_age_hours: float = 36.0,
    fetch_if_missing: bool = False,
    causal_rebalance: bool = False,
) -> Tuple[Dict[str, Any], Optional[str], Optional[Dict[str, Any]]]:
    """把 ≤τ 分钟小包并入 feats。有根则按 τ **覆盖**旧开→τ（禁收盘 leftover）。

    返回 ``(feats, as_of_tau_override, y_spec_override)``。
    ``minute_bars`` 优先（因果前缀，不拉网）；缺则读本地 5m 仓。
    ``causal_rebalance``：钟 = 当日已有 5m 末根且 ≤10:00；无根则开盘 Z。
    ``fetch_if_missing``：无当日分钟时拉 5m；因果模式不为未来钟拉 K。
    切不出包：清掉分钟键，``as_of`` 不标 τ。
    """
    out = dict(feats or {})
    day = str(trade_date or "")[:10]
    if len(day) < 10:
        clear_minute_tau_pack_keys(out)
        return out, None, None

    passed_bars = minute_bars is not None
    bars = [b for b in (minute_bars or []) if isinstance(b, dict)]
    if not bars and load_cache_if_missing and code:
        bars = _load_minute_bars_from_cache(code, max_age_hours=max_age_hours)

    day_bars = _day_minute_bars(bars, trade_date=day)
    if fetch_if_missing and code and not passed_bars:
        need_fetch = not day_bars
        if not causal_rebalance:
            hm_req = str(tau_hm or "").strip()[:5]
            need_fetch = (not hm_req) or not prefix_has_tau_clock(
                bars, trade_date=day, tau_hm=hm_req
            )
        if need_fetch:
            fetched = _fetch_minute_bars_for_tau(code)
            if fetched:
                bars = fetched

    if causal_rebalance:
        hm = causal_rebalance_tau_hm(bars, trade_date=day)
        if not hm:
            clear_minute_tau_pack_keys(out)
            return out, None, None
    else:
        hm = str(tau_hm or "").strip()[:5]
        if not hm:
            clear_minute_tau_pack_keys(out)
            return out, None, None

    pack = extract_minute_tau_pack(
        bars,
        trade_date=day,
        tau_hm=hm,
        open_px=open_px,
        prev_close=prev_close,
    )
    clear_minute_tau_pack_keys(out)
    if pack.get("ret_open_to_tau") is None:
        return out, None, None

    for k, v in pack.items():
        if v is None or v == "":
            continue
        out[k] = v
    try:
        seq_all = extract_t30_t60_t90_seq_packs(
            bars,
            trade_date=day,
            tau_hm=hm,
            open_px=open_px,
        )
        for k, v in seq_all.items():
            if v is not None:
                out[k] = v
    except Exception:
        pass
    if _PEER_CODES_MEMO:
        out = attach_sector_ret_cs_if_missing(out, trade_date=day, tau_hm=hm)
        out = attach_sector_ret_last_30m_cs_if_missing(out, trade_date=day, tau_hm=hm)
        out = attach_sector_ret_last_60m_cs_if_missing(out, trade_date=day, tau_hm=hm)
        out = attach_sector_ret_last_90m_cs_if_missing(out, trade_date=day, tau_hm=hm)
    clock_ok = prefix_has_tau_clock(bars, trade_date=day, tau_hm=hm)
    prefix = _day_bars_upto_tau(bars, trade_date=day, tau_hm=hm)
    if clock_ok:
        stamp_hm = hm
    elif prefix:
        stamp_hm = _format_hm_colon(_bar_hm(prefix[-1])) or hm
    else:
        return out, None, None
    as_of = f"{day}T{stamp_hm}:00+08:00"
    y_spec = {
        "tau": stamp_hm,
        "formula": f"close[T]/price[{stamp_hm}]-1",
        "note": "分钟小包 ≤τ；缺 τ 根可拉 5m；无包不标该钟",
        "minute_pack": sorted(pack.keys()),
    }
    return out, as_of, y_spec

