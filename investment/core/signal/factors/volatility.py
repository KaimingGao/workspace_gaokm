"""波动因子。"""

from __future__ import annotations

from typing import List, Optional

from core.signal.factors.volume_price import _avg


def atr_pct(bars: List[dict], window: int = 5) -> Optional[float]:
    if len(bars) < 2:
        return None
    window = min(window, len(bars) - 1)
    trs = []
    for i in range(-window, 0):
        h = bars[i]["high"]
        l = bars[i]["low"]
        prev = bars[i - 1]["close"]
        tr = max(h - l, abs(h - prev), abs(l - prev))
        trs.append(tr)
    atr = _avg(trs)
    last = bars[-1]["close"]
    if atr is None or not last:
        return None
    return atr / last * 100.0


def score_volatility(bars: List[dict]) -> tuple[float, dict]:
    atr = atr_pct(bars)
    if atr is None:
        v_score = 55.0
    elif atr <= 2.5:
        v_score = 80.0
    elif atr <= 4.5:
        v_score = 60.0
    elif atr <= 7:
        v_score = 40.0
    else:
        v_score = 20.0
    return v_score, {
        "atr_pct": None if atr is None else round(atr, 2),
    }
