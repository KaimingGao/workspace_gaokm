"""凯利公式仓位管理模块。

凯利公式用于计算最优仓位比例，最大化长期几何增长率。
f = (胜率 × 盈亏比 - 1) / 盈亏比

其中：
- f: 最优仓位比例
- 胜率: 盈利交易占比
- 盈亏比: 平均盈利 / 平均亏损
"""


import logging

logger = logging.getLogger(__name__)
from typing import List, Optional, Tuple


def calculate_kelly_fraction(
    win_rate: float,
    profit_loss_ratio: float,
    max_fraction: float = 0.25,
    min_fraction: float = 0.05,
) -> float:
    """
    计算凯利公式最优仓位比例。
    
    Args:
        win_rate: 胜率 (0-1)
        profit_loss_ratio: 盈亏比 (平均盈利/平均亏损)
        max_fraction: 最大仓位比例上限
        min_fraction: 最小仓位比例下限
    
    Returns:
        建议的仓位比例
    """
    if win_rate <= 0 or win_rate >= 1 or profit_loss_ratio <= 0:
        return 0.15  # 默认仓位

    # 凯利公式: f = (p × b - q) / b
    # p = 胜率, q = 1 - 胜率, b = 盈亏比
    kelly = (win_rate * profit_loss_ratio - (1 - win_rate)) / profit_loss_ratio

    # 限制在合理范围内
    kelly = max(0.0, min(1.0, kelly))

    # 使用半凯利（更保守）
    half_kelly = kelly * 0.5

    # 限制在上下限之间
    position = max(min_fraction, min(max_fraction, half_kelly))

    return round(position, 4)


def estimate_strategy_stats(
    trade_history: List[dict],
    window: int = 30,
) -> Tuple[float, float]:
    """
    从交易历史估算策略的胜率和盈亏比。
    
    Args:
        trade_history: 交易历史记录
        window: 统计窗口（最近N笔交易）
    
    Returns:
        (胜率, 盈亏比)
    """
    if not trade_history:
        return 0.5, 1.5  # 默认假设

    # 只统计最近的交易
    recent_trades = trade_history[-window:] if len(trade_history) > window else trade_history

    wins = []
    losses = []

    for trade in recent_trades:
        if trade.get("pnl_pct") is not None:
            pnl = float(trade["pnl_pct"])
            if pnl > 0:
                wins.append(pnl)
            elif pnl < 0:
                losses.append(abs(pnl))

    if not wins:
        return 0.3, 1.5  # 没有盈利交易，保守估计

    if not losses:
        return 1.0, 2.0  # 没有亏损交易，乐观估计

    win_rate = len(wins) / len(recent_trades) if recent_trades else 0.5
    avg_win = sum(wins) / len(wins)
    avg_loss = sum(losses) / len(losses) if losses else 1.0

    profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 1.5

    return round(win_rate, 4), round(profit_loss_ratio, 4)


def adaptive_position_sizing(
    paper: dict,
    score: float,
    rules: Optional[dict] = None,
) -> float:
    """
    根据策略表现和股票评分自适应计算仓位比例。
    
    Args:
        paper: 纸面交易账户数据
        score: 股票评分
        rules: 策略规则
    
    Returns:
        建议的仓位比例
    """
    rules = rules or {}
    base_position = float(rules.get("position_pct") or 0.15)
    max_position = float(rules.get("max_position_pct") or 0.25)
    min_position = float(rules.get("min_position_pct") or 0.05)

    # 1. 基于凯利公式计算
    trades = paper.get("trades") or []
    if len(trades) >= 10:
        win_rate, pl_ratio = estimate_strategy_stats(trades)
        kelly_position = calculate_kelly_fraction(
            win_rate, pl_ratio, max_fraction=max_position, min_fraction=min_position
        )
    else:
        kelly_position = base_position

    # 2. 根据评分调整（高评分给更高仓位）
    score_adjustment = 1.0
    if score >= 80:
        score_adjustment = 1.3  # 高评分，加仓30%
    elif score >= 70:
        score_adjustment = 1.15  # 中高评分，加仓15%
    elif score < 55:
        score_adjustment = 0.7  # 低评分，减仓30%

    # 3. 考虑市场状态
    regime = rules.get("market_regime", "neutral")
    regime_adjustment = {
        "bull": 1.1,
        "strong": 1.05,
        "neutral": 1.0,
        "weak": 0.85,
        "bear": 0.7,
    }.get(regime, 1.0)

    # 综合计算
    final_position = kelly_position * score_adjustment * regime_adjustment

    # 限制范围
    final_position = max(min_position, min(max_position, final_position))

    return round(final_position, 4)


def calculate_position_for_portfolio(
    paper: dict,
    new_score: float,
    existing_scores: List[float],
    rules: Optional[dict] = None,
) -> float:
    """
    考虑组合分散化的仓位计算。
    
    Args:
        paper: 纸面交易账户数据
        new_score: 新股票评分
        existing_scores: 已有持仓的评分列表
        rules: 策略规则
    
    Returns:
        建议的仓位比例
    """
    rules = rules or {}
    base_position = float(rules.get("position_pct") or 0.15)
    max_positions = int(rules.get("max_positions") or 5)

    # 计算自适应基础仓位
    adaptive_base = adaptive_position_sizing(paper, new_score, rules)

    # 考虑已有持仓数量，分散化
    current_count = len(existing_scores)
    remaining_slots = max_positions - current_count

    if remaining_slots <= 0:
        return 0.0  # 已满仓

    # 剩余仓位平均分配
    remaining_capacity = 1.0 - (current_count * adaptive_base)
    if remaining_capacity <= 0:
        return 0.0

    # 如果剩余仓位足够，使用自适应仓位
    if adaptive_base <= remaining_capacity / remaining_slots:
        return adaptive_base

    # 否则使用剩余容量的一部分
    return round(remaining_capacity / remaining_slots * 0.8, 4)
