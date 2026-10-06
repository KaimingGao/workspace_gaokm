"""前置风控模块。

在买入前检查：
1. 波动率过滤：剔除波动率过高的股票
2. 最大回撤过滤：剔除历史回撤过大的股票
3. 流动性过滤：确保成交量足够
4. 信号冷却期：避免重复推荐同一股票
"""


import logging

logger = logging.getLogger(__name__)
from typing import Dict, List, Optional, Tuple


def _calc_volatility(bars: List[dict], window: int = 20) -> Optional[float]:
    """计算近期波动率（年化）。"""
    if len(bars) < window:
        return None

    closes = [float(b.get("close", 0)) for b in bars[-window:]]
    closes = [c for c in closes if c > 0]
    if len(closes) < 3:
        return None

    returns = [(closes[i] / closes[i-1] - 1) for i in range(1, len(closes))]
    mean_ret = sum(returns) / len(returns)
    variance = sum((r - mean_ret) ** 2 for r in returns) / max(1, len(returns) - 1)
    daily_vol = variance ** 0.5
    return daily_vol * (252 ** 0.5)


def _calc_max_drawdown(bars: List[dict], window: int = 60) -> Optional[float]:
    """计算近期最大回撤。"""
    if len(bars) < 5:
        return None

    closes = [float(b["close"]) for b in bars[-window:]]
    peak = closes[0]
    max_dd = 0.0

    for c in closes:
        if c > peak:
            peak = c
        dd = (peak - c) / peak
        max_dd = max(max_dd, dd)

    return max_dd * 100


def _check_liquidity(bars: List[dict], min_volume: float = 50000) -> bool:
    """检查流动性是否充足。"""
    if not bars:
        return False

    recent_vols = [float(b.get("volume", 0)) for b in bars[-10:]]
    recent_vols = [v for v in recent_vols if v > 0]
    if not recent_vols:
        return False

    avg_volume = sum(recent_vols) / len(recent_vols)
    return avg_volume >= min_volume


def _check_trend_stability(bars: List[dict]) -> Optional[str]:
    """
    检查趋势稳定性。
    返回：stable / volatile / choppy
    """
    if len(bars) < 20:
        return None

    closes = [float(b["close"]) for b in bars]

    # 计算线性回归的R²
    n = len(closes[-20:])
    x = list(range(n))
    y = closes[-20:]
    mean_x = sum(x) / n
    mean_y = sum(y) / n

    num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    den = sum((xi - mean_x) ** 2 for xi in x)
    if den == 0:
        return "volatile"

    slope = num / den
    intercept = mean_y - slope * mean_x

    # 计算R²
    pred = [slope * xi + intercept for xi in x]
    ss_res = sum((yi - pi) ** 2 for yi, pi in zip(y, pred))
    ss_tot = sum((yi - mean_y) ** 2 for yi in y)

    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0

    if r_squared > 0.7:
        return "stable"
    elif r_squared < 0.3:
        return "choppy"
    else:
        return "volatile"


def pre_trade_check(
    bars: List[dict],
    *,
    prev_signals: Optional[List[dict]] = None,
    config: Optional[dict] = None,
) -> Tuple[bool, Dict]:
    """
    买入前风控检查。
    
    Args:
        bars: K线数据
        prev_signals: 之前的信号记录（用于冷却期检查）
        config: 风控配置
    
    Returns:
        (是否通过, 检查详情)
    """
    config = config or {}
    checks = {
        "volatility": None,
        "max_drawdown": None,
        "liquidity": None,
        "trend": None,
        "cooldown": None,
        "passed": True,
        "reasons": [],
    }

    if not bars or len(bars) < 10:
        checks["passed"] = False
        checks["reasons"].append("K线数据不足")
        return False, checks

    # 1. 波动率检查
    max_vol = float(config.get("max_volatility", 0.6))  # 年化60%
    vol = _calc_volatility(bars)
    checks["volatility"] = round(vol, 4) if vol is not None else None
    if vol is not None and vol > max_vol:
        checks["passed"] = False
        checks["reasons"].append(f"波动率过高({vol:.1%} > {max_vol:.0%})")

    # 2. 最大回撤检查
    max_dd = float(config.get("max_drawdown", 0.30))  # 30%
    dd = _calc_max_drawdown(bars)
    checks["max_drawdown"] = round(dd, 2) if dd is not None else None
    if dd is not None and dd > max_dd * 100:
        checks["passed"] = False
        checks["reasons"].append(f"历史回撤过大({dd:.1f}% > {max_dd*100:.0f}%)")

    # 3. 流动性检查
    min_vol = float(config.get("min_avg_volume", 50000))
    liquid = _check_liquidity(bars, min_vol)
    checks["liquidity"] = "pass" if liquid else "fail"
    if not liquid:
        checks["passed"] = False
        checks["reasons"].append("成交量过低，流动性不足")

    # 4. 趋势稳定性
    trend = _check_trend_stability(bars)
    checks["trend"] = trend
    if config.get("require_stable_trend", False) and trend == "choppy":
        checks["passed"] = False
        checks["reasons"].append("趋势不稳定（震荡行情）")

    return checks["passed"], checks


def check_signal_cooldown(
    stock_code: str,
    prev_signals: List[dict],
    cooldown_days: int = 5,
) -> Tuple[bool, int]:
    """
    检查信号冷却期。
    
    Args:
        stock_code: 股票代码
        prev_signals: 之前的信号
        cooldown_days: 冷却天数
    
    Returns:
        (是否可以交易, 剩余冷却天数)
    """
    if not prev_signals:
        return True, 0

    recent = [s for s in prev_signals if s.get("stock_code") == stock_code]
    if not recent:
        return True, 0

    # 找最近一次信号
    last_signal = max(recent, key=lambda s: s.get("ts", ""))
    last_ts = last_signal.get("ts", "")

    if not last_ts:
        return True, 0

    from datetime import datetime

    try:
        last_date = datetime.fromisoformat(str(last_ts).replace("Z", ""))
        now = datetime.now()
        days_since = (now - last_date).days

        if days_since >= cooldown_days:
            return True, 0
        else:
            return False, cooldown_days - days_since
    except ValueError:
        return True, 0
