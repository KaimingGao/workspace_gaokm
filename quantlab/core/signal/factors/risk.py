"""动态止损模块：ATR止损、移动止损、波动率调整止损。"""

from typing import List, Optional, Tuple

from core.signal.factors.volatility import atr_pct


def calculate_atr_stop_loss(
    bars: List[dict],
    entry_price: float,
    atr_multiplier: float = 2.0,
    window: int = 14,
) -> Optional[float]:
    """
    计算ATR止损价格。
    
    ATR止损 = 入场价 - ATR × 倍数
    
    Args:
        bars: 日线数据
        entry_price: 入场价格
        atr_multiplier: ATR倍数，默认2.0
        window: ATR计算周期，默认14日
    
    Returns:
        止损价格，如果无法计算则返回None
    """
    if not bars or len(bars) < 2:
        return None

    # 计算ATR
    atr_pct_value = atr_pct(bars, window)
    if atr_pct_value is None:
        return None

    # 将ATR百分比转换为绝对值
    last_close = bars[-1]["close"]
    atr_absolute = last_close * atr_pct_value / 100

    # 计算止损价
    stop_price = entry_price - atr_absolute * atr_multiplier

    return round(stop_price, 4)


def calculate_moving_stop_loss(
    bars: List[dict],
    peak_price: float,
    trail_pct: float = 0.10,
) -> float:
    """
    计算移动止损价格（跟踪止损）。
    
    当价格上涨时，止损价跟随上移；当价格下跌时，止损价不变。
    止损价 = 最高价 × (1 - 跟踪比例)
    
    Args:
        bars: 日线数据
        peak_price: 持仓期间的最高价
        trail_pct: 跟踪比例，默认10%
    
    Returns:
        止损价格
    """
    return round(peak_price * (1 - trail_pct), 4)


def calculate_volatility_adjusted_stop_loss(
    bars: List[dict],
    entry_price: float,
    base_stop_pct: float = 0.08,
    high_volatility_adj: float = 1.5,
    low_volatility_adj: float = 0.7,
) -> float:
    """
    波动率调整止损。
    
    高波动时放宽止损，低波动时收紧止损。
    避免在震荡市中被洗出，同时在趋势市中锁定利润。
    
    Args:
        bars: 日线数据
        entry_price: 入场价格
        base_stop_pct: 基础止损比例，默认8%
        high_volatility_adj: 高波动调整系数
        low_volatility_adj: 低波动调整系数
    
    Returns:
        止损价格
    """
    atr_pct_value = atr_pct(bars, window=5)

    if atr_pct_value is None:
        adjusted_pct = base_stop_pct
    elif atr_pct_value > 5.0:
        # 高波动：放宽止损
        adjusted_pct = base_stop_pct * high_volatility_adj
    elif atr_pct_value < 2.0:
        # 低波动：收紧止损
        adjusted_pct = base_stop_pct * low_volatility_adj
    else:
        # 正常波动：使用基础止损
        adjusted_pct = base_stop_pct

    return round(entry_price * (1 - adjusted_pct), 4)


def should_stop_loss(
    current_price: float,
    entry_price: float,
    peak_price: float,
    bars: List[dict],
    mode: str = "adaptive",
    **kwargs,
) -> Tuple[bool, float, str]:
    """
    综合判断是否触发止损。
    
    Args:
        current_price: 当前价格
        entry_price: 入场价格
        peak_price: 持仓期间最高价
        bars: 日线数据
        mode: 止损模式
            - "fixed": 固定百分比止损
            - "atr": ATR止损
            - "trailing": 移动止损
            - "adaptive": 自适应止损（推荐）
        **kwargs: 各模式的参数
    
    Returns:
        (是否触发止损, 止损价, 触发原因)
    """
    if mode == "fixed":
        stop_pct = kwargs.get("stop_pct", 0.08)
        stop_price = entry_price * (1 - stop_pct)
        if current_price <= stop_price:
            return True, stop_price, f"固定止损({stop_pct*100:.0f}%)"
        return False, stop_price, ""

    elif mode == "atr":
        atr_multiplier = kwargs.get("atr_multiplier", 2.0)
        stop_price = calculate_atr_stop_loss(bars, entry_price, atr_multiplier)
        if stop_price and current_price <= stop_price:
            return True, stop_price, f"ATR止损({atr_multiplier}倍ATR)"
        return False, stop_price or 0, ""

    elif mode == "trailing":
        trail_pct = kwargs.get("trail_pct", 0.10)
        stop_price = calculate_moving_stop_loss(bars, peak_price, trail_pct)
        if current_price <= stop_price:
            return True, stop_price, f"移动止损(跟踪{trail_pct*100:.0f}%)"
        return False, stop_price, ""

    elif mode == "adaptive":
        # 自适应模式：组合多种止损
        reasons = []
        min_stop_price = float('-inf')

        # 1. 固定止损（最后防线）
        fixed_pct = kwargs.get("fixed_stop_pct", 0.12)  # 12% 硬止损
        fixed_stop = entry_price * (1 - fixed_pct)
        if current_price <= fixed_stop:
            return True, fixed_stop, f"硬止损({fixed_pct*100:.0f}%)"

        # 2. ATR止损
        atr_mult = kwargs.get("atr_multiplier", 2.0)
        atr_stop = calculate_atr_stop_loss(bars, entry_price, atr_mult)
        if atr_stop:
            min_stop_price = max(min_stop_price, atr_stop)
            if current_price <= atr_stop:
                reasons.append("ATR止损")

        # 3. 移动止损（盈利保护）
        if peak_price > entry_price * 1.05:  # 盈利超过5%时启用
            trail_pct = kwargs.get("trail_pct", 0.10)
            trail_stop = calculate_moving_stop_loss(bars, peak_price, trail_pct)
            min_stop_price = max(min_stop_price, trail_stop)
            if current_price <= trail_stop and trail_stop > entry_price:
                reasons.append("移动止损(锁定利润)")

        # 4. 波动率调整止损
        vol_adj_stop = calculate_volatility_adjusted_stop_loss(bars, entry_price)
        min_stop_price = max(min_stop_price, vol_adj_stop)
        if current_price <= vol_adj_stop:
            reasons.append("波动调整止损")

        if reasons:
            return True, min_stop_price, "+".join(reasons)

        return False, min_stop_price, ""

    else:
        raise ValueError(f"Unknown stop loss mode: {mode}")


def update_peak_price(peak_price: float, current_price: float) -> float:
    """更新持仓期间的最高价。"""
    return max(peak_price, current_price)
