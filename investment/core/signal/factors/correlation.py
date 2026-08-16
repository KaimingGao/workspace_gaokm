"""相关性风控模块：检测持仓股票之间的相关性，避免过度集中。"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import math


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


def check_portfolio_correlation(
    holdings_bars: Dict[str, List[dict]],
    max_correlation: float = 0.8,
    max_same_sector: int = 2,
) -> Tuple[bool, List[Dict]]:
    """
    检查持仓组合的相关性。
    
    Args:
        holdings_bars: 持仓股票的K线数据 {code: bars}
        max_correlation: 最大允许相关系数
        max_same_sector: 同一行业最多持仓数量
    
    Returns:
        (是否通过检查, 问题列表)
    """
    codes = list(holdings_bars.keys())
    issues = []
    
    if len(codes) <= 1:
        return True, issues
    
    # 计算每只股票的收益率
    returns_map = {}
    for code, bars in holdings_bars.items():
        returns_map[code] = calculate_returns(bars, window=10)
    
    # 检查两两相关性
    high_correlation_pairs = []
    for i in range(len(codes)):
        for j in range(i + 1, len(codes)):
            code1, code2 = codes[i], codes[j]
            ret1, ret2 = returns_map.get(code1, []), returns_map.get(code2, [])
            
            if len(ret1) >= 5 and len(ret2) >= 5:
                corr = calculate_correlation(ret1, ret2)
                if corr is not None and abs(corr) > max_correlation:
                    high_correlation_pairs.append({
                        "stock1": code1,
                        "stock2": code2,
                        "correlation": corr,
                    })
    
    if high_correlation_pairs:
        issues.append({
            "type": "high_correlation",
            "message": f"存在 {len(high_correlation_pairs)} 对高相关性股票（>{max_correlation}）",
            "details": high_correlation_pairs,
        })
    
    # 检查行业集中度（DS-R2.2：真实 sector_map）
    from core.portfolio_optimize import _sector_for, load_sector_map

    smap = load_sector_map()
    sector_count: Dict[str, int] = {}
    for code in codes:
        sector = _sector_for(str(code), smap)
        sector_count[sector] = sector_count.get(sector, 0) + 1

    concentrated_sectors = [
        {"sector": s, "count": c}
        for s, c in sector_count.items()
        if c > max_same_sector
    ]

    if concentrated_sectors:
        issues.append({
            "type": "sector_concentration",
            "message": f"存在 {len(concentrated_sectors)} 个行业持仓过于集中",
            "details": concentrated_sectors,
        })

    return len(issues) == 0, issues


def get_diversification_score(holdings_bars: Dict[str, List[dict]]) -> float:
    """
    计算组合分散化分数（0-100）。
    
    分数越高表示分散化程度越好。
    
    Args:
        holdings_bars: 持仓股票的K线数据
    
    Returns:
        分散化分数
    """
    codes = list(holdings_bars.keys())
    if len(codes) <= 1:
        return 50.0  # 单只股票，中等分散
    
    # 计算所有两两相关系数
    returns_map = {}
    for code, bars in holdings_bars.items():
        returns_map[code] = calculate_returns(bars, window=10)
    
    correlations = []
    for i in range(len(codes)):
        for j in range(i + 1, len(codes)):
            ret1 = returns_map.get(codes[i], [])
            ret2 = returns_map.get(codes[j], [])
            if len(ret1) >= 5 and len(ret2) >= 5:
                corr = calculate_correlation(ret1, ret2)
                if corr is not None:
                    correlations.append(abs(corr))
    
    if not correlations:
        return 50.0
    
    # 平均相关系数
    avg_corr = sum(correlations) / len(correlations)
    
    # 分散化分数 = 100 * (1 - 平均相关系数)
    score = 100.0 * (1.0 - avg_corr)
    
    return round(max(0.0, min(100.0, score)), 1)


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
