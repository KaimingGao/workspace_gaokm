"""流动性因子（P45）：近端成交额活跃度，过低或异常放量均降分。"""

from __future__ import annotations

from typing import List, Optional


def _avg(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def turnover_proxy(bar: dict) -> float:
    """成交额代理：volume × close（normalize_bars 已统一 volume 字段）。"""
    vol = float(bar.get("volume") or 0.0)
    close = float(bar.get("close") or 0.0)
    return max(0.0, vol * close)


def turnover_ratio(bars: List[dict], short: int = 3, long: int = 10) -> Optional[float]:
    if len(bars) < 2:
        return None
    long = min(long, len(bars))
    short = min(short, long)
    recent = _avg([turnover_proxy(b) for b in bars[-short:]])
    base = _avg([turnover_proxy(b) for b in bars[-long:]])
    if not recent or not base:
        return None
    return recent / base


def score_liquidity(bars: List[dict]) -> tuple[float, dict]:
    ratio = turnover_ratio(bars)
    if ratio is None:
        return 50.0, {"turnover_ratio": None}

    if 0.75 <= ratio <= 1.8:
        score = 62.0 + min(18.0, (ratio - 0.75) * 12.0)
    elif ratio < 0.75:
        score = max(25.0, 40.0 + (ratio - 0.75) * 30.0)
    elif ratio <= 3.0:
        score = 48.0
    else:
        score = 35.0

    return round(score, 1), {"turnover_ratio": round(ratio, 2)}
