"""尾盘微观结构因子：Tail_Volume_Ratio + 尾盘价格斜率。"""


from typing import List, Optional, Tuple


def _tail_bars(minute_bars: List[dict], *, tail_minutes: int = 30) -> List[dict]:
    if not minute_bars:
        return []
    by_date: dict = {}
    for b in minute_bars:
        d = str(b.get("date") or str(b.get("datetime") or "")[:10])
        if d:
            by_date.setdefault(d, []).append(b)
    if not by_date:
        return []
    last_day = sorted(by_date.keys())[-1]
    day_bars = sorted(by_date[last_day], key=lambda x: str(x.get("datetime") or ""))
    if len(day_bars) <= 1:
        return day_bars
    # 最后 tail_minutes 分钟（5min K 线 → 6 根 ≈ 30min）
    n_tail = max(1, int(tail_minutes / 5))
    return day_bars[-n_tail:]


def tail_volume_ratio(minute_bars: List[dict], *, tail_minutes: int = 30) -> Optional[float]:
    if not minute_bars:
        return None
    by_date: dict = {}
    for b in minute_bars:
        d = str(b.get("date") or str(b.get("datetime") or "")[:10])
        if d:
            by_date.setdefault(d, []).append(b)
    if not by_date:
        return None
    last_day = sorted(by_date.keys())[-1]
    day_bars = by_date[last_day]
    total_vol = sum(float(b.get("volume") or 0.0) for b in day_bars)
    if total_vol <= 0:
        return None
    tail = _tail_bars(minute_bars, tail_minutes=tail_minutes)
    tail_vol = sum(float(b.get("volume") or 0.0) for b in tail)
    return round(tail_vol / total_vol, 4)


def tail_price_slope(minute_bars: List[dict], *, tail_minutes: int = 15) -> Optional[float]:
    tail = _tail_bars(minute_bars, tail_minutes=tail_minutes)
    if len(tail) < 2:
        return None
    try:
        p0 = float(tail[0].get("close") or tail[0].get("open") or 0.0)
        p1 = float(tail[-1].get("close") or 0.0)
    except (TypeError, ValueError):
        return None
    if p0 <= 0:
        return None
    return round((p1 / p0 - 1.0) * 100.0, 4)


def score_tail_anomaly(
    bars: List[dict],
    *,
    minute_bars: Optional[List[dict]] = None,
    tail_minutes: int = 30,
) -> Tuple[float, dict]:
    """尾盘抢跑/跳水因子：尾盘放量 + 下行斜率 → 低分。"""
    if not minute_bars or len(minute_bars) < 4:
        return 50.0, {
            "tail_volume_ratio": None,
            "tail_price_slope_pct": None,
            "omit_sub_score": True,
        }

    tvr = tail_volume_ratio(minute_bars, tail_minutes=tail_minutes)
    slope = tail_price_slope(minute_bars, tail_minutes=min(15, tail_minutes))

    score = 50.0
    if tvr is not None:
        if tvr >= 0.35:
            score -= 12.0
        elif tvr >= 0.25:
            score -= 6.0
        elif tvr <= 0.12:
            score += 4.0

    if slope is not None:
        if slope <= -1.0:
            score -= 15.0
        elif slope <= -0.4:
            score -= 8.0
        elif slope >= 0.6:
            score += 6.0

    score = max(0.0, min(100.0, score))
    return score, {
        "tail_volume_ratio": tvr,
        "tail_price_slope_pct": slope,
    }
