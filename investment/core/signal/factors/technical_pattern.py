"""技术形态因子。

多维度技术形态识别：
1. 均线排列（多头/空头/缠绕）
2. 突破形态（箱顶突破、均线突破）
3. K线形态（阳线、阴线、十字星、锤子线）
4. 趋势强度（ADX、均线斜率）
"""

from __future__ import annotations
from core.numbers import calc_sma as _calc_ma

from typing import List, Optional


def _calc_ema(closes: List[float], period: int) -> Optional[float]:
    """计算指数移动均线。"""
    if len(closes) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def _calc_adx(bars: List[dict], period: int = 14) -> Optional[float]:
    """计算ADX（平均趋向指数）。"""
    if len(bars) < period + 2:
        return None
    
    trs = []
    plus_dms = []
    minus_dms = []
    
    for i in range(1, len(bars)):
        high = bars[i]["high"]
        low = bars[i]["low"]
        prev_high = bars[i-1]["high"]
        prev_low = bars[i-1]["low"]
        prev_close = bars[i-1]["close"]
        
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
        
        up_move = high - prev_high
        down_move = prev_low - low
        
        plus_dm = up_move if up_move > down_move and up_move > 0 else 0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0
        
        plus_dms.append(plus_dm)
        minus_dms.append(minus_dm)
    
    if len(trs) < period:
        return None
    
    atr = sum(trs[:period]) / period
    smoothed_plus_dm = sum(plus_dms[:period]) / period
    smoothed_minus_dm = sum(minus_dms[:period]) / period
    
    dx_list = []
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
        smoothed_plus_dm = (smoothed_plus_dm * (period - 1) + plus_dms[i]) / period
        smoothed_minus_dm = (smoothed_minus_dm * (period - 1) + minus_dms[i]) / period
        
        if atr == 0:
            continue
        
        plus_di = 100 * smoothed_plus_dm / atr
        minus_di = 100 * smoothed_minus_dm / atr
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di) if (plus_di + minus_di) > 0 else 0
        dx_list.append(dx)
    
    if not dx_list:
        return None
    
    adx = sum(dx_list) / len(dx_list)
    return adx


def _calc_ma_slope(closes: List[float], period: int = 20, slope_period: int = 5) -> Optional[float]:
    """计算均线斜率（百分比）。"""
    if len(closes) < period + slope_period:
        return None
    
    # 计算当前均线
    current_ma = sum(closes[-period:]) / period
    
    # slope_period期前的均线
    past_ma = sum(closes[-(period + slope_period):-slope_period]) / period
    
    if past_ma == 0:
        return None
    
    return (current_ma / past_ma - 1) * 100


def _detect_ma_alignment(closes: List[float]) -> tuple[str, float]:
    """
    检测均线排列。
    
    Returns:
        (pattern, score): 排列类型和分数
    """
    ma5 = _calc_ma(closes, 5)
    ma10 = _calc_ma(closes, 10)
    ma20 = _calc_ma(closes, 20)
    ma60 = _calc_ma(closes, 60)
    
    if ma5 is None or ma10 is None or ma20 is None:
        return "insufficient_data", 50.0
    
    current = closes[-1]
    
    # 多头排列: MA5 > MA10 > MA20 > MA60
    if ma60 and ma5 > ma10 > ma20 > ma60:
        return "bullish_alignment", 82.0
    # 多头排列 (无MA60)
    elif ma5 > ma10 > ma20:
        return "bullish_alignment_short", 72.0
    # 空头排列: MA5 < MA10 < MA20
    elif ma60 and ma5 < ma10 < ma20 < ma60:
        return "bearish_alignment", 18.0
    elif ma5 < ma10 < ma20:
        return "bearish_alignment_short", 28.0
    # 价格在所有均线上方
    elif ma60 and current > ma5 > ma10 > ma20:
        return "price_above_all_ma", 75.0
    elif current > ma5 and current > ma10 and current > ma20:
        return "price_above_ma", 65.0
    # 价格在所有均线下方
    elif ma60 and current < ma5 < ma10 < ma20:
        return "price_below_all_ma", 22.0
    elif current < ma5 and current < ma10 and current < ma20:
        return "price_below_ma", 35.0
    # 缠绕
    else:
        # 计算均线间距离
        avg_diff = abs(ma5 - ma20) / ma20 * 100
        if avg_diff < 2:
            return "ma_entangled", 52.0  # 缠绕
        elif avg_diff < 5:
            return "ma_loosely_entangled", 48.0
        else:
            return "ma_mixed", 45.0


def _detect_breakout(closes: List[float], highs: List[float], lows: List[float], volumes: List[float]) -> tuple[str, float]:
    """
    检测突破形态。
    
    Returns:
        (pattern, score): 突破类型和分数
    """
    if len(closes) < 20:
        return "insufficient_data", 50.0
    
    current_close = closes[-1]
    current_volume = volumes[-1] if volumes else 0
    
    # 计算箱体区间
    box_high = max(highs[-20:])
    box_low = min(lows[-20:])
    box_width = box_high - box_low
    
    if box_width == 0:
        return "no_box", 50.0
    
    # 向上突破箱顶
    if current_close > box_high:
        # 检查是否放量突破
        avg_volume = sum(volumes[-20:]) / 20 if volumes else 1
        if current_volume > avg_volume * 1.5:
            return "volume_breakout_up", 88.0  # 放量突破
        elif current_volume > avg_volume * 1.2:
            return "moderate_breakout_up", 78.0  # 温和放量突破
        else:
            return "weak_breakout_up", 65.0  # 缩量突破
    
    # 向下跌破箱底
    if current_close < box_low:
        avg_volume = sum(volumes[-20:]) / 20 if volumes else 1
        if current_volume > avg_volume * 1.5:
            return "volume_breakdown", 15.0  # 放量跌破
        else:
            return "weak_breakdown", 30.0  # 缩量跌破
    
    # 均线突破
    ma20 = sum(closes[-20:]) / 20
    prev_close = closes[-2] if len(closes) > 1 else closes[-1]
    
    if current_close > ma20 and prev_close <= ma20:
        return "ma_breakout", 68.0  # 突破MA20
    elif current_close < ma20 and prev_close >= ma20:
        return "ma_breakdown", 32.0  # 跌破MA20
    
    return "no_breakout", 50.0


def _detect_candlestick_pattern(open_price: float, high: float, low: float, close: float) -> tuple[str, float]:
    """
    检测K线形态。
    
    Returns:
        (pattern, score): K线形态和分数
    """
    body = close - open_price
    body_size = abs(body)
    total_range = high - low
    
    if total_range == 0:
        return "doji", 48.0
    
    # 十字星 (上下影线长，实体小)
    if body_size / total_range < 0.1:
        return "doji", 50.0
    
    upper_shadow = high - max(open_price, close)
    lower_shadow = min(open_price, close) - low
    
    # 锤子线 (下影线长，上影线短)
    if lower_shadow > body_size * 2 and upper_shadow < body_size * 0.5 and body > 0:
        return "hammer", 65.0
    
    # 倒锤子线
    if upper_shadow > body_size * 2 and lower_shadow < body_size * 0.5 and body > 0:
        return "inverted_hammer", 55.0
    
    # 大阳线
    if body > 0 and body_size / total_range > 0.7:
        # 收盘接近最高价
        if (high - close) / total_range < 0.1:
            return "strong_bullish", 75.0
        else:
            return "bullish", 65.0
    
    # 大阴线
    if body < 0 and body_size / total_range > 0.7:
        # 收盘接近最低价
        if (close - low) / total_range < 0.1:
            return "strong_bearish", 25.0
        else:
            return "bearish", 35.0
    
    # 小阳线
    if body > 0 and body_size / total_range < 0.3:
        return "small_bullish", 58.0
    
    # 小阴线
    if body < 0 and body_size / total_range < 0.3:
        return "small_bearish", 42.0
    
    # 上影线长 (看空信号)
    if upper_shadow > body_size * 1.5 and body > 0:
        return "long_upper_shadow", 38.0
    
    # 下影线长 (看多信号)
    if lower_shadow > body_size * 1.5 and body < 0:
        return "long_lower_shadow", 55.0
    
    return "neutral", 50.0


def _calc_trend_strength(bars: List[dict]) -> tuple[str, float]:
    """
    计算趋势强度。
    
    Returns:
        (trend_type, score): 趋势类型和分数
    """
    closes = [float(b["close"]) for b in bars]
    
    if len(closes) < 20:
        return "insufficient_data", 50.0
    
    # ADX趋势强度
    adx = _calc_adx(bars)
    adx_score = 50.0
    if adx is not None:
        if adx > 40:
            adx_score = 78.0  # 强趋势
        elif adx > 30:
            adx_score = 65.0  # 中等趋势
        elif adx > 20:
            adx_score = 55.0  # 弱趋势
        else:
            adx_score = 45.0  # 震荡
    
    # 均线斜率
    ma5_slope = _calc_ma_slope(closes, 5, 5)
    ma20_slope = _calc_ma_slope(closes, 20, 5)
    
    slope_score = 50.0
    if ma20_slope is not None:
        if ma20_slope > 5:
            slope_score = 78.0  # 均线陡升
        elif ma20_slope > 2:
            slope_score = 68.0  # 均线温和上升
        elif ma20_slope > 0:
            slope_score = 58.0  # 均线微升
        elif ma20_slope < -5:
            slope_score = 25.0  # 均线陡降
        elif ma20_slope < -2:
            slope_score = 35.0  # 均线温和下降
        elif ma20_slope < 0:
            slope_score = 45.0  # 均线微降
    
    # 综合趋势强度
    total = 0.5 * adx_score + 0.5 * slope_score
    
    if adx and adx > 30 and ma20_slope and ma20_slope > 2:
        trend_type = "strong_uptrend"
    elif adx and adx > 30 and ma20_slope and ma20_slope < -2:
        trend_type = "strong_downtrend"
    elif adx and adx > 25:
        trend_type = "moderate_trend"
    elif adx and adx < 20:
        trend_type = "range_bound"
    else:
        trend_type = "weak_trend"
    
    return trend_type, round(total, 1)


def score_technical_pattern(bars: List[dict]) -> tuple[float, dict]:
    """
    技术形态综合评分。
    
    维度：
    1. 突破形态 (35%) - 箱顶突破、均线突破
    2. K线形态 (30%) - 阳线、阴线、十字星、锤子线
    3. 均线排列 (20%) - 多头/空头/缠绕（降权，与ma_slope差异化）
    4. 趋势强度 (15%) - ADX趋势强度（降权，避免与ma_slope重叠）
    """
    if not bars or len(bars) < 5:
        return 50.0, {"tech_ma_pattern": None, "tech_breakout": None, "tech_candlestick": None}
    
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    volumes = [float(b.get("volume", 0)) for b in bars]
    
    # 1. 突破形态（高权重，这是技术形态独有的）
    breakout_pattern, breakout_score = _detect_breakout(closes, highs, lows, volumes)
    
    # 2. 最新K线形态（高权重，这也是独有的）
    last_bar = bars[-1]
    cs_pattern, cs_score = _detect_candlestick_pattern(
        float(last_bar["open"]),
        float(last_bar["high"]),
        float(last_bar["low"]),
        float(last_bar["close"]),
    )
    
    # 3. 均线排列（降权，与ma_slope差异化）
    ma_pattern, ma_score = _detect_ma_alignment(closes)
    
    # 4. 趋势强度（降权，避免与ma_slope重叠）
    trend_type, trend_score = _calc_trend_strength(bars)
    
    # 综合评分 - 调整权重使技术形态更关注形态而非趋势
    total = (
        0.35 * breakout_score +  # 突破形态：核心
        0.30 * cs_score +         # K线形态：核心
        0.20 * ma_score +         # 均线排列：辅助
        0.15 * trend_score        # 趋势强度：辅助
    )
    
    return round(total, 1), {
        "tech_ma_pattern": ma_pattern,
        "tech_ma_score": round(ma_score, 1),
        "tech_breakout": breakout_pattern,
        "tech_breakout_score": round(breakout_score, 1),
        "tech_candlestick": cs_pattern,
        "tech_candlestick_score": round(cs_score, 1),
        "tech_trend_type": trend_type,
        "tech_trend_score": round(trend_score, 1),
    }
