"""反转因子（重定义版）。

使用与动量因子完全不同的信号源，避免因子冗余：
1. 涨停统计 - 近期涨停次数和强度
2. 连板高度 - 连续涨停天数
3. 位置因子 - 当前价格在近期区间的相对位置
4. 振幅分布 - 近期振幅特征
5. 波动率特征 - 日内波动模式
"""

from __future__ import annotations

from typing import List, Optional


def _count_limit_up(bars: List[dict], window: int = 20) -> int:
    """统计近期涨停次数（假设涨停幅度为9.5%以上）。"""
    if len(bars) < 2:
        return 0
    
    count = 0
    for i in range(max(1, len(bars) - window), len(bars)):
        prev_close = bars[i-1]["close"]
        cur_close = bars[i]["close"]
        if prev_close > 0:
            change_pct = (cur_close / prev_close - 1) * 100
            if change_pct >= 9.5:
                count += 1
    return count


def _count_limit_down(bars: List[dict], window: int = 20) -> int:
    """统计近期跌停次数。"""
    if len(bars) < 2:
        return 0
    
    count = 0
    for i in range(max(1, len(bars) - window), len(bars)):
        prev_close = bars[i-1]["close"]
        cur_close = bars[i]["close"]
        if prev_close > 0:
            change_pct = (cur_close / prev_close - 1) * 100
            if change_pct <= -9.5:
                count += 1
    return count


def _calc_consecutive_limit_up(bars: List[dict]) -> int:
    """计算最大连板高度（连续涨停天数）。"""
    if len(bars) < 2:
        return 0
    
    max_consecutive = 0
    current_consecutive = 0
    
    for i in range(1, len(bars)):
        prev_close = bars[i-1]["close"]
        cur_close = bars[i]["close"]
        if prev_close > 0:
            change_pct = (cur_close / prev_close - 1) * 100
            if change_pct >= 9.5:
                current_consecutive += 1
                max_consecutive = max(max_consecutive, current_consecutive)
            else:
                current_consecutive = 0
    
    return max_consecutive


def _calc_position_in_range(bars: List[dict], window: int = 20) -> Optional[float]:
    """计算当前价格在近期区间的相对位置（0=最低，100=最高）。"""
    if len(bars) < 5:
        return None
    
    recent = bars[-window:]
    highs = [float(b["high"]) for b in recent]
    lows = [float(b["low"]) for b in recent]
    
    range_high = max(highs)
    range_low = min(lows)
    range_size = range_high - range_low
    
    if range_size == 0:
        return 50.0
    
    current_close = float(bars[-1]["close"])
    position = (current_close - range_low) / range_size * 100
    
    return position


def _calc_amplitude_feature(bars: List[dict], window: int = 10) -> Optional[dict]:
    """计算近期振幅特征。"""
    if len(bars) < 5:
        return None
    
    recent = bars[-window:]
    amplitudes = []
    for bar in recent:
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        if close > 0:
            amp = (high - low) / close * 100
            amplitudes.append(amp)
    
    if not amplitudes:
        return None
    
    avg_amp = sum(amplitudes) / len(amplitudes)
    max_amp = max(amplitudes)
    min_amp = min(amplitudes)
    amp_trend = amplitudes[-1] - amplitudes[0] if len(amplitudes) > 1 else 0
    
    return {
        "avg_amplitude": round(avg_amp, 2),
        "max_amplitude": round(max_amp, 2),
        "min_amplitude": round(min_amp, 2),
        "amplitude_trend": round(amp_trend, 2),
    }


def _count_gap(bars: List[dict], window: int = 10) -> dict:
    """统计跳空缺口（高开/低开）。"""
    if len(bars) < 2:
        return {"up_gaps": 0, "down_gaps": 0, "gap_ratio": 0.0}
    
    up_gaps = 0
    down_gaps = 0
    
    for i in range(max(1, len(bars) - window), len(bars)):
        prev_close = bars[i-1]["close"]
        cur_open = bars[i]["open"]
        
        if cur_open > prev_close * 1.01:  # 高开1%以上
            up_gaps += 1
        elif cur_open < prev_close * 0.99:  # 低开1%以上
            down_gaps += 1
    
    total_gaps = up_gaps + down_gaps
    gap_ratio = up_gaps / total_gaps if total_gaps > 0 else 0.5
    
    return {
        "up_gaps": up_gaps,
        "down_gaps": down_gaps,
        "gap_ratio": round(gap_ratio, 2),
    }


def score_reversal(bars: List[dict]) -> tuple[float, dict]:
    """
    反转因子评分（基于位置和统计特征，与动量解耦）。
    
    维度：
    1. 涨停统计 (25%) - 近期涨停次数
    2. 连板高度 (20%) - 最大连续涨停
    3. 位置因子 (25%) - 当前价在区间的位置
    4. 振幅特征 (15%) - 振幅变化趋势
    5. 跳空统计 (15%) - 高开/低开比例
    """
    if not bars or len(bars) < 5:
        return 50.0, {
            "rev_limit_up_count": None,
            "rev_consecutive_limit": None,
            "rev_position": None,
            "rev_amplitude": None,
        }
    
    # 1. 涨停统计评分
    limit_up_count = _count_limit_up(bars, 20)
    limit_down_count = _count_limit_down(bars, 20)
    
    limit_score = 50.0
    # 涨停次数多 = 过热，反转可能性大（向下反转）
    if limit_up_count >= 3:
        limit_score = 25.0  # 多次涨停，有回调风险
    elif limit_up_count >= 2:
        limit_score = 38.0  # 两次涨停，谨慎
    elif limit_up_count == 1:
        limit_score = 52.0  # 一次涨停，中性偏好
    # 跌停次数多 = 超跌，反转可能性大（向上反转）
    if limit_down_count >= 2:
        limit_score = min(limit_score, 72.0)  # 多次跌停，有反弹机会
    elif limit_down_count == 1:
        limit_score = min(limit_score, 58.0)  # 一次跌停，轻微超卖
    
    # 2. 连板高度评分
    max_consecutive = _calc_consecutive_limit_up(bars)
    
    consecutive_score = 50.0
    if max_consecutive >= 3:
        consecutive_score = 20.0  # 连板过多，高位风险大
    elif max_consecutive >= 2:
        consecutive_score = 38.0  # 连板2天，有分歧风险
    elif max_consecutive == 1:
        consecutive_score = 52.0  # 首板，相对安全
    else:
        consecutive_score = 60.0  # 无涨停，有补涨可能
    
    # 3. 位置因子评分
    position = _calc_position_in_range(bars, 20)
    
    position_score = 50.0
    if position is not None:
        if position > 90:
            position_score = 25.0  # 接近区间高点，有回调压力
        elif position > 75:
            position_score = 38.0  # 高位区间
        elif position < 10:
            position_score = 75.0  # 接近区间低点，有反弹空间
        elif position < 25:
            position_score = 62.0  # 低位区间
        else:
            position_score = 50.0  # 中间位置
    
    # 4. 振幅特征评分
    amp_feature = _calc_amplitude_feature(bars, 10)
    
    amp_score = 50.0
    if amp_feature:
        amp_trend = amp_feature["amplitude_trend"]
        avg_amp = amp_feature["avg_amplitude"]
        
        if amp_trend > 2 and avg_amp > 5:
            # 振幅放大，波动加剧
            amp_score = 35.0  # 不确定性增加
        elif amp_trend < -2 and avg_amp < 3:
            # 振幅收缩，可能变盘
            amp_score = 58.0  # 蓄势待发
        elif amp_trend > 0 and avg_amp < 4:
            # 温和放大
            amp_score = 52.0
        else:
            amp_score = 48.0
    
    # 5. 跳空统计评分
    gap_info = _count_gap(bars, 10)
    gap_score = 50.0
    
    if gap_info["gap_ratio"] > 0.7:
        gap_score = 42.0  # 高开多，追高风险
    elif gap_info["gap_ratio"] < 0.3:
        gap_score = 58.0  # 低开多，有反弹
    
    # 综合评分
    total = (
        0.25 * limit_score +
        0.20 * consecutive_score +
        0.25 * position_score +
        0.15 * amp_score +
        0.15 * gap_score
    )
    
    return round(total, 1), {
        "rev_limit_up_count": limit_up_count,
        "rev_limit_down_count": limit_down_count,
        "rev_consecutive_limit": max_consecutive,
        "rev_position": round(position, 1) if position is not None else None,
        "rev_amplitude": amp_feature,
        "rev_gap_info": gap_info,
    }
