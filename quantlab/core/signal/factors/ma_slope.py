"""均线斜率因子。

通过多条均线的斜率和发散度来量化趋势强度：
1. 短期均线斜率（MA5/MA10）
2. 中期均线斜率（MA20/MA60）
3. 均线发散度（多条均线的斜率差异）
4. 均线动量（均线的加速度）
"""

from typing import List, Optional


def _calc_ma_slope(closes: List[float], period: int, slope_window: int = 5) -> Optional[float]:
    """计算均线斜率（百分比变化）。"""
    if len(closes) < period + slope_window:
        return None

    current_ma = sum(closes[-period:]) / period
    past_ma = sum(closes[-(period + slope_window):-slope_window]) / period

    if past_ma == 0:
        return None

    return (current_ma / past_ma - 1) * 100


def _calc_ma_acceleration(closes: List[float], period: int, window: int = 10) -> Optional[float]:
    """计算均线加速度（斜率的变化率）。"""
    if len(closes) < period + window:
        return None

    # 近期斜率
    recent_slope = _calc_ma_slope(closes, period, min(5, window // 2))
    if recent_slope is None:
        return None

    # 远期斜率
    past_closes = closes[:-min(5, window // 2)]
    if len(past_closes) < period + min(5, window // 2):
        return None

    past_slope = _calc_ma_slope(past_closes, period, min(5, window // 2))
    if past_slope is None:
        return None

    return recent_slope - past_slope


def _calc_ma_divergence(closes: List[float]) -> Optional[float]:
    """计算均线发散度（短期均线斜率与长期均线斜率的差异）。"""
    short_slope = _calc_ma_slope(closes, 5, 5)
    long_slope = _calc_ma_slope(closes, 20, 5)

    if short_slope is None or long_slope is None:
        return None

    # 短期斜率 - 长期斜率 = 发散度
    # 正值表示短期均线上升快于长期（趋势加强）
    # 负值表示短期均线上升慢于长期（趋势减弱）
    return short_slope - long_slope


def _calc_multi_ma_slope(closes: List[float]) -> dict:
    """计算多条均线的斜率。"""
    slopes = {}
    for period in [5, 10, 20, 60]:
        slope = _calc_ma_slope(closes, period, 5)
        if slope is not None:
            slopes[f"ma{period}_slope"] = round(slope, 3)

    return slopes


def score_ma_slope(bars: List[dict]) -> tuple[float, dict]:
    """
    均线斜率综合评分。
    
    维度：
    1. 短期均线斜率 (30%) - MA5/MA10的斜率
    2. 中期均线斜率 (30%) - MA20/MA60的斜率
    3. 均线发散度 (20%) - 短期vs长期斜率差
    4. 均线加速度 (20%) - 斜率的变化率
    """
    if not bars or len(bars) < 10:
        return 50.0, {"ma_slope": "insufficient_data", "omit_sub_score": True}

    closes = [float(b["close"]) for b in bars]

    # 1. 短期均线斜率评分
    ma5_slope = _calc_ma_slope(closes, 5, 5)
    ma10_slope = _calc_ma_slope(closes, 10, 5)

    short_slope_score = 50.0
    if ma5_slope is not None and ma10_slope is not None:
        avg_short_slope = (ma5_slope + ma10_slope) / 2

        if avg_short_slope > 5:
            short_slope_score = 82.0  # 短期均线急速上升
        elif avg_short_slope > 3:
            short_slope_score = 72.0  # 短期均线明显上升
        elif avg_short_slope > 1:
            short_slope_score = 62.0  # 短期均线温和上升
        elif avg_short_slope > 0:
            short_slope_score = 55.0  # 短期均线微升
        elif avg_short_slope < -5:
            short_slope_score = 18.0  # 短期均线急速下降
        elif avg_short_slope < -3:
            short_slope_score = 28.0  # 短期均线明显下降
        elif avg_short_slope < -1:
            short_slope_score = 40.0  # 短期均线温和下降
        elif avg_short_slope < 0:
            short_slope_score = 46.0  # 短期均线微降

    # 2. 中期均线斜率评分
    ma20_slope = _calc_ma_slope(closes, 20, 5)
    ma60_slope = _calc_ma_slope(closes, 60, 5)

    mid_slope_score = 50.0
    if ma20_slope is not None:
        mid_slope = ma20_slope
        if ma60_slope is not None:
            mid_slope = (ma20_slope + ma60_slope) / 2

        if mid_slope > 4:
            mid_slope_score = 80.0  # 中期均线急速上升
        elif mid_slope > 2:
            mid_slope_score = 70.0  # 中期均线明显上升
        elif mid_slope > 0.5:
            mid_slope_score = 60.0  # 中期均线温和上升
        elif mid_slope > 0:
            mid_slope_score = 55.0  # 中期均线微升
        elif mid_slope < -4:
            mid_slope_score = 22.0  # 中期均线急速下降
        elif mid_slope < -2:
            mid_slope_score = 32.0  # 中期均线明显下降
        elif mid_slope < -0.5:
            mid_slope_score = 42.0  # 中期均线温和下降
        elif mid_slope < 0:
            mid_slope_score = 47.0  # 中期均线微降

    # 3. 均线发散度评分
    divergence = _calc_ma_divergence(closes)
    divergence_score = 50.0

    if divergence is not None:
        if divergence > 3:
            divergence_score = 72.0  # 短期大幅强于长期（趋势加速）
        elif divergence > 1:
            divergence_score = 62.0  # 短期略强于长期
        elif divergence > 0:
            divergence_score = 55.0  # 短期微强于长期
        elif divergence < -3:
            divergence_score = 25.0  # 短期大幅弱于长期（趋势减速）
        elif divergence < -1:
            divergence_score = 38.0  # 短期略弱于长期
        elif divergence < 0:
            divergence_score = 46.0  # 短期微弱于长期

    # 4. 均线加速度评分
    acceleration = _calc_ma_acceleration(closes, 20, 10)
    accel_score = 50.0

    if acceleration is not None:
        if acceleration > 1:
            accel_score = 68.0  # 均线上升加速
        elif acceleration > 0:
            accel_score = 56.0  # 均线上升但加速度不变
        elif acceleration < -1:
            accel_score = 30.0  # 均线下降加速
        elif acceleration < 0:
            accel_score = 44.0  # 均线下降但加速度不变

    # 综合评分
    total = (
        0.30 * short_slope_score +
        0.30 * mid_slope_score +
        0.20 * divergence_score +
        0.20 * accel_score
    )

    # 趋势一致性加分
    consistency_adj = 0.0
    if short_slope_score > 60 and mid_slope_score > 60:
        consistency_adj = 3.0  # 短期和中期均线同向向上
    elif short_slope_score < 40 and mid_slope_score < 40:
        consistency_adj = -2.0  # 短期和中期均线同向下

    total = max(0, min(100, total + consistency_adj))

    # 获取所有均线斜率
    all_slopes = _calc_multi_ma_slope(closes)

    return round(total, 1), {
        "ma_slope_ma5": all_slopes.get("ma5_slope"),
        "ma_slope_ma10": all_slopes.get("ma10_slope"),
        "ma_slope_ma20": all_slopes.get("ma20_slope"),
        "ma_slope_ma60": all_slopes.get("ma60_slope"),
        "ma_slope_divergence": round(divergence, 3) if divergence is not None else None,
        "ma_slope_acceleration": round(acceleration, 3) if acceleration is not None else None,
        "ma_slope_consistency_adj": consistency_adj,
    }
