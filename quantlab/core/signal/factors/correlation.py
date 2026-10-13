"""相关性风控模块：检测持仓股票之间的相关性，避免过度集中。"""

import math
from typing import Dict, List, Optional, Tuple


def calculate_correlation(returns1: List[float], returns2: List[float]) -> Optional[float]:
    """
    计算两组收益率的Pearson相关系数。
    
    Args:
        returns1: 第一组收益率
        returns2: 第二组收益率
    
    Returns:
        相关系数 (-1 到 1)，如果无法计算则返回None
    """
    n = len(returns1)
    if n < 3 or len(returns2) != n:
        return None

    # 计算均值
    mean1 = sum(returns1) / n
    mean2 = sum(returns2) / n

    # 计算协方差和标准差
    cov = sum((returns1[i] - mean1) * (returns2[i] - mean2) for i in range(n))
    std1 = math.sqrt(sum((r - mean1) ** 2 for r in returns1))
    std2 = math.sqrt(sum((r - mean2) ** 2 for r in returns2))

    if std1 == 0 or std2 == 0:
        return None

    # 计算相关系数
    correlation = cov / (std1 * std2)
    return round(correlation, 4)


def calculate_returns(bars: List[dict], window: int = 5) -> List[float]:
    """
    计算股票的收益率序列。
    
    Args:
        bars: 日线数据
        window: 计算窗口
    
    Returns:
        收益率列表
    """
    returns = []
    for i in range(-window, 0):
        if i - 1 >= -len(bars):
            prev_close = bars[i - 1]["close"]
            curr_close = bars[i]["close"]
            if prev_close > 0:
                ret = (curr_close / prev_close) - 1.0
                returns.append(ret)
    return returns


def suggest_replacement(
    new_code: str,
    holdings_bars: Dict[str, List[dict]],
    max_correlation: float = 0.8,
) -> Optional[str]:
    """
    建议是否需要替换新买入的股票。
    
    如果新股票与某只已有持仓高度相关，建议替换相关性较高的那只。
    
    Args:
        new_code: 新股票代码
        holdings_bars: 已有持仓的K线数据（不含新股票）
        max_correlation: 最大允许相关系数
    
    Returns:
        建议替换的股票代码，如果不需要替换则返回None
    """
    if new_code not in holdings_bars:
        return None

    new_returns = calculate_returns(holdings_bars[new_code], window=10)
    if len(new_returns) < 5:
        return None

    max_corr_found = 0.0
    stock_to_replace = None

    for code, bars in holdings_bars.items():
        if code == new_code:
            continue

        existing_returns = calculate_returns(bars, window=10)
        if len(existing_returns) >= 5:
            corr = calculate_correlation(new_returns, existing_returns)
            if corr is not None and abs(corr) > max_correlation:
                if abs(corr) > max_corr_found:
                    max_corr_found = abs(corr)
                    stock_to_replace = code

    return stock_to_replace
