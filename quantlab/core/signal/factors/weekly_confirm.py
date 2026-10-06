"""周线确认因子。

将日线聚合为周线级别，判断大周期趋势，用于确认日线信号：
1. 周线趋势方向（5周/10周均线）
2. 周线均线位置（价格在周线均线之上/下）
3. 周线动量（周线级别涨跌幅）
4. 周线量能（周成交量变化）
"""

from typing import List, Optional


def _aggregate_to_weekly(bars: List[dict]) -> List[dict]:
    """将日线数据聚合为周线数据（简化版，按每5根K线聚合）。"""
    if not bars:
        return []

    weekly_bars = []
    week_size = 5

    for i in range(0, len(bars), week_size):
        week_slice = bars[i:i + week_size]
        if len(week_slice) < 1:
            continue

        week_open = week_slice[0]["open"]
        week_close = week_slice[-1]["close"]
        week_high = max(float(b["high"]) for b in week_slice)
        week_low = min(float(b["low"]) for b in week_slice)
        week_volume = sum(float(b.get("volume", 0)) for b in week_slice)
        week_date = week_slice[-1].get("date", "")

        weekly_bars.append({
            "open": week_open,
            "close": week_close,
            "high": week_high,
            "low": week_low,
            "volume": week_volume,
            "date": week_date,
        })

    return weekly_bars


def _calc_weekly_ma(weekly_bars: List[dict], period: int) -> Optional[float]:
    """计算周线均线。"""
    if len(weekly_bars) < period:
        return None
    closes = [float(b["close"]) for b in weekly_bars[-period:]]
    return sum(closes) / period


def _calc_weekly_momentum(weekly_bars: List[dict], weeks: int = 4) -> Optional[float]:
    """计算周线动量（最近N周的涨跌幅）。"""
    if len(weekly_bars) < weeks + 1:
        return None

    start_price = float(weekly_bars[-(weeks + 1)]["close"])
    end_price = float(weekly_bars[-1]["close"])

    if start_price == 0:
        return None

    return (end_price / start_price - 1) * 100


def _calc_weekly_volume_trend(weekly_bars: List[dict], period: int = 4) -> Optional[float]:
    """计算周线成交量趋势（最近N周成交量相对之前的变化）。"""
    if len(weekly_bars) < period * 2:
        return None

    recent_vol = sum(float(b.get("volume", 0)) for b in weekly_bars[-period:])
    prev_vol = sum(float(b.get("volume", 0)) for b in weekly_bars[-(period * 2):-period])

    if prev_vol == 0:
        return None

    return (recent_vol / prev_vol - 1) * 100


def score_weekly_confirmation(bars: List[dict]) -> tuple[float, dict]:
    """
    周线确认评分。
    
    维度：
    1. 周线趋势 (35%) - 5周/10周均线方向
    2. 周线位置 (25%) - 价格在周线均线之上/下
    3. 周线动量 (25%) - 周线级别涨跌幅
    4. 周线量能 (15%) - 周成交量变化
    """
    if not bars or len(bars) < 10:
        return 50.0, {
            "weekly_trend": None,
            "weekly_position": None,
            "omit_sub_score": True,
        }

    # 聚合为周线
    weekly_bars = _aggregate_to_weekly(bars)

    if len(weekly_bars) < 3:
        return 50.0, {
            "weekly_trend": "insufficient_data",
            "weekly_score": 50.0,
            "omit_sub_score": True,
        }

    # 1. 周线趋势评分
    ma5w = _calc_weekly_ma(weekly_bars, 5)
    ma10w = _calc_weekly_ma(weekly_bars, 10)
    current_week_close = float(weekly_bars[-1]["close"])

    trend_score = 50.0
    if ma5w and ma10w:
        if ma5w > ma10w and current_week_close > ma5w:
            trend_score = 80.0  # 周线多头趋势
        elif ma5w < ma10w and current_week_close < ma5w:
            trend_score = 25.0  # 周线空头趋势
        elif current_week_close > ma5w:
            trend_score = 65.0  # 价格在5周均线上
        elif current_week_close < ma5w:
            trend_score = 38.0  # 价格在5周均线下
        else:
            trend_score = 50.0  # 震荡

    # 2. 周线位置评分
    position_score = 50.0
    if ma5w:
        if current_week_close > ma5w * 1.05:
            position_score = 72.0  # 明显高于5周均线
        elif current_week_close > ma5w:
            position_score = 60.0  # 略高于5周均线
        elif current_week_close < ma5w * 0.95:
            position_score = 28.0  # 明显低于5周均线
        elif current_week_close < ma5w:
            position_score = 40.0  # 略低于5周均线

    # 3. 周线动量评分
    weekly_mom4 = _calc_weekly_momentum(weekly_bars, 4)
    weekly_mom8 = _calc_weekly_momentum(weekly_bars, 8)

    momentum_score = 50.0
    if weekly_mom4 is not None:
        if weekly_mom4 > 8:
            momentum_score = 78.0  # 4周涨幅超过8%
        elif weekly_mom4 > 4:
            momentum_score = 68.0  # 4周涨幅4-8%
        elif weekly_mom4 > 0:
            momentum_score = 56.0  # 4周微涨
        elif weekly_mom4 < -8:
            momentum_score = 22.0  # 4周跌幅超过8%
        elif weekly_mom4 < -4:
            momentum_score = 34.0  # 4周跌幅4-8%
        elif weekly_mom4 < 0:
            momentum_score = 44.0  # 4周微跌

    # 4. 周线量能评分
    vol_trend = _calc_weekly_volume_trend(weekly_bars, 4)
    vol_score = 50.0

    if vol_trend is not None:
        if vol_trend > 30:
            vol_score = 70.0  # 周成交量放大
        elif vol_trend > 10:
            vol_score = 58.0  # 周成交量温和放大
        elif vol_trend < -20:
            vol_score = 35.0  # 周成交量萎缩
        elif vol_trend < -5:
            vol_score = 44.0  # 周成交量轻微萎缩

    # 综合评分
    total = (
        0.35 * trend_score +
        0.25 * position_score +
        0.25 * momentum_score +
        0.15 * vol_score
    )

    # 周线共振确认加分/减分
    resonance_adj = 0.0
    if trend_score > 65 and momentum_score > 60:
        resonance_adj = 5.0  # 周线趋势和动量共振
    elif trend_score < 35 and momentum_score < 40:
        resonance_adj = -3.0  # 周线趋势和动量共振下跌

    total = max(0, min(100, total + resonance_adj))

    return round(total, 1), {
        "weekly_trend": "bullish" if trend_score > 65 else ("bearish" if trend_score < 35 else "neutral"),
        "weekly_trend_score": round(trend_score, 1),
        "weekly_position_score": round(position_score, 1),
        "weekly_momentum_4w": round(weekly_mom4, 2) if weekly_mom4 is not None else None,
        "weekly_momentum_8w": round(weekly_mom8, 2) if weekly_mom8 is not None else None,
        "weekly_volume_trend": round(vol_trend, 1) if vol_trend is not None else None,
        "weekly_resonance": resonance_adj,
        "weekly_bars_count": len(weekly_bars),
    }
