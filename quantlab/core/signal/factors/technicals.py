"""技术指标增强模块。

提供MACD、KDJ、RSI等技术指标作为辅助信号：
- MACD：趋势确认
- KDJ：超买超卖
- RSI：动量判断
- 成交量异动检测
"""


import logging

logger = logging.getLogger(__name__)
from typing import Dict, List, Optional, Tuple


def calculate_ema(prices: List[float], period: int) -> List[float]:
    """
    计算指数移动平均线。
    
    Args:
        prices: 价格序列
        period: 周期
    
    Returns:
        EMA序列
    """
    if not prices:
        return []

    multiplier = 2 / (period + 1)
    ema = [prices[0]]

    for price in prices[1:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])

    return ema


def calculate_macd(
    closes: List[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Dict[str, any]:
    """
    计算MACD指标。
    
    Args:
        closes: 收盘价序列
        fast: 快线周期
        slow: 慢线周期
        signal: 信号线周期
    
    Returns:
        MACD指标值和信号
    """
    if len(closes) < slow + signal:
        return {"valid": False, "signal": "insufficient_data"}

    ema_fast = calculate_ema(closes, fast)
    ema_slow = calculate_ema(closes, slow)

    # DIF
    dif = [fast - slow for fast, slow in zip(ema_fast, ema_slow)]

    # DEA (Signal Line)
    dea = calculate_ema(dif, signal)

    # MACD Bar
    macd_bar = [(d - e) * 2 for d, e in zip(dif, dea)]

    # 最新值
    latest_dif = dif[-1]
    latest_dea = dea[-1]
    latest_macd = macd_bar[-1]
    prev_macd = macd_bar[-2] if len(macd_bar) >= 2 else 0

    # 判断信号
    signal_strength = 0
    signal_type = "neutral"

    if latest_dif > latest_dea and latest_macd > prev_macd:
        # 金叉且柱状图增大
        signal_strength = 2
        signal_type = "strong_buy"
    elif latest_dif > latest_dea:
        # 金叉
        signal_strength = 1
        signal_type = "buy"
    elif latest_dif < latest_dea and latest_macd < prev_macd:
        # 死叉且柱状图增大
        signal_strength = -2
        signal_type = "strong_sell"
    elif latest_dif < latest_dea:
        # 死叉
        signal_strength = -1
        signal_type = "sell"

    return {
        "valid": True,
        "dif": round(latest_dif, 4),
        "dea": round(latest_dea, 4),
        "macd": round(latest_macd, 4),
        "signal_type": signal_type,
        "signal_strength": signal_strength,
        "trend": "up" if latest_dif > latest_dea else "down",
    }


def calculate_kdj(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    n: int = 9,
    m1: int = 3,
    m2: int = 3,
) -> Dict[str, any]:
    """
    计算KDJ指标。
    
    Args:
        highs: 最高价序列
        lows: 最低价序列
        closes: 收盘价序列
        n: 周期
        m1: K值平滑周期
        m2: D值平滑周期
    
    Returns:
        KDJ指标值和信号
    """
    if len(closes) < n:
        return {"valid": False, "signal": "insufficient_data"}

    # 计算RSV
    k_list = []
    d_list = []
    j_list = []

    for i in range(len(closes)):
        start = max(0, i - n + 1)
        period_high = max(highs[start:i+1])
        period_low = min(lows[start:i+1])

        if period_high == period_low:
            rsv = 50
        else:
            rsv = (closes[i] - period_low) / (period_high - period_low) * 100

        if i == 0:
            k = 50
            d = 50
        else:
            k = (m1 - 1) / m1 * k_list[-1] + 1 / m1 * rsv
            d = (m2 - 1) / m2 * d_list[-1] + 1 / m2 * k

        j = 3 * k - 2 * d

        k_list.append(k)
        d_list.append(d)
        j_list.append(j)

    latest_k = k_list[-1]
    latest_d = d_list[-1]
    latest_j = j_list[-1]

    # 判断信号
    signal_strength = 0
    signal_type = "neutral"

    # 金叉/死叉
    if latest_k > latest_d and len(k_list) >= 2 and k_list[-2] <= d_list[-2]:
        if latest_k < 30:
            signal_strength = 2
            signal_type = "strong_buy"  # 超卖金叉
        else:
            signal_strength = 1
            signal_type = "buy"  # 普通金叉
    elif latest_k < latest_d and len(k_list) >= 2 and k_list[-2] >= d_list[-2]:
        if latest_k > 70:
            signal_strength = -2
            signal_type = "strong_sell"  # 超买死叉
        else:
            signal_strength = -1
            signal_type = "sell"  # 普通死叉

    # 超买超卖
    if latest_j < 0:
        signal_strength = max(signal_strength, 1)
        signal_type = signal_type if signal_strength > 0 else "oversold"
    elif latest_j > 100:
        signal_strength = min(signal_strength, -1)
        signal_type = signal_type if signal_strength < 0 else "overbought"

    return {
        "valid": True,
        "k": round(latest_k, 2),
        "d": round(latest_d, 2),
        "j": round(latest_j, 2),
        "signal_type": signal_type,
        "signal_strength": signal_strength,
        "overbought": latest_k > 80 or latest_j > 100,
        "oversold": latest_k < 20 or latest_j < 0,
    }


def calculate_rsi(
    closes: List[float],
    period: int = 14,
) -> Dict[str, any]:
    """
    计算RSI指标。
    
    Args:
        closes: 收盘价序列
        period: 周期
    
    Returns:
        RSI指标值和信号
    """
    if len(closes) < period + 1:
        return {"valid": False, "signal": "insufficient_data"}

    # 计算涨跌
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    # RSI
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi_list = []
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

        rsi_list.append(rsi)

    if not rsi_list:
        return {"valid": False, "signal": "calculation_error"}

    latest_rsi = rsi_list[-1]

    # 判断信号
    signal_strength = 0
    signal_type = "neutral"

    if latest_rsi < 30:
        signal_strength = 2
        signal_type = "strong_buy"  # 超卖
    elif latest_rsi < 40:
        signal_strength = 1
        signal_type = "buy"  # 偏弱
    elif latest_rsi > 70:
        signal_strength = -2
        signal_type = "strong_sell"  # 超买
    elif latest_rsi > 60:
        signal_strength = -1
        signal_type = "sell"  # 偏强

    return {
        "valid": True,
        "rsi": round(latest_rsi, 2),
        "signal_type": signal_type,
        "signal_strength": signal_strength,
        "overbought": latest_rsi > 70,
        "oversold": latest_rsi < 30,
    }


def detect_volume_anomaly(
    volumes: List[float],
    threshold: float = 2.0,
) -> Dict[str, any]:
    """
    检测成交量异动。
    
    Args:
        volumes: 成交量序列
        threshold: 异动倍数阈值
    
    Returns:
        成交量异动检测结果
    """
    if len(volumes) < 20:
        return {"valid": False, "signal": "insufficient_data"}

    # 计算平均成交量
    avg_volume = sum(volumes[-20:]) / 20
    current_volume = volumes[-1]

    if avg_volume == 0:
        return {"valid": False, "signal": "zero_volume"}

    ratio = current_volume / avg_volume

    # 判断异动类型
    anomaly_type = "normal"
    strength = 0

    if ratio >= threshold * 2:
        anomaly_type = "extreme_high"
        strength = 3
    elif ratio >= threshold:
        anomaly_type = "high"
        strength = 2
    elif ratio >= 1.5:
        anomaly_type = "slightly_high"
        strength = 1
    elif ratio < 0.3:
        anomaly_type = "extreme_low"
        strength = -2
    elif ratio < 0.5:
        anomaly_type = "low"
        strength = -1

    return {
        "valid": True,
        "current_volume": round(current_volume, 0),
        "avg_volume": round(avg_volume, 0),
        "ratio": round(ratio, 2),
        "anomaly_type": anomaly_type,
        "strength": strength,
    }


def get_combined_signal(
    bars: List[dict],
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, any]:
    """
    综合所有技术指标信号。
    
    Args:
        bars: K线数据
        weights: 各指标权重
    
    Returns:
        综合信号
    """
    weights = weights or {
        "macd": 0.35,
        "kdj": 0.25,
        "rsi": 0.25,
        "volume": 0.15,
    }

    if len(bars) < 30:
        return {"valid": False, "signal": "insufficient_data"}

    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    volumes = [float(b.get("volume", 0)) for b in bars]

    # 计算各指标
    macd = calculate_macd(closes)
    kdj = calculate_kdj(highs, lows, closes)
    rsi = calculate_rsi(closes)
    volume = detect_volume_anomaly(volumes)

    # 汇总信号
    signals = {
        "macd": macd.get("signal_strength", 0) if macd.get("valid") else 0,
        "kdj": kdj.get("signal_strength", 0) if kdj.get("valid") else 0,
        "rsi": rsi.get("signal_strength", 0) if rsi.get("valid") else 0,
        "volume": volume.get("strength", 0) if volume.get("valid") else 0,
    }

    # 加权综合得分
    total_weight = sum(
        w for k, w in weights.items()
        if k in signals and w > 0
    )

    if total_weight == 0:
        return {"valid": False, "signal": "no_valid_weights"}

    composite = sum(
        signals.get(k, 0) * w
        for k, w in weights.items()
        if k in signals
    ) / total_weight

    # 判断综合信号
    if composite >= 1.0:
        overall_signal = "strong_buy"
    elif composite >= 0.3:
        overall_signal = "buy"
    elif composite <= -1.0:
        overall_signal = "strong_sell"
    elif composite <= -0.3:
        overall_signal = "sell"
    else:
        overall_signal = "neutral"

    # 辅助打分（0-100）
    score = 50 + composite * 25
    score = max(0, min(100, score))

    return {
        "valid": True,
        "signals": signals,
        "composite_strength": round(composite, 4),
        "overall_signal": overall_signal,
        "technical_score": round(score, 1),
        "macd": macd,
        "kdj": kdj,
        "rsi": rsi,
        "volume_anomaly": volume,
    }


def check_technical_filters(
    bars: List[dict],
    min_technical_score: float = 40.0,
) -> Tuple[bool, Dict[str, any]]:
    """
    检查技术指标是否满足买入条件。
    
    Args:
        bars: K线数据
        min_technical_score: 最低技术分阈值
    
    Returns:
        (是否通过, 技术指标详情)
    """
    combined = get_combined_signal(bars)

    if not combined.get("valid"):
        return True, combined  # 数据不足时默认通过

    tech_score = combined.get("technical_score", 50)
    passed = tech_score >= min_technical_score

    combined["filter_passed"] = passed
    combined["min_threshold"] = min_technical_score

    return passed, combined
