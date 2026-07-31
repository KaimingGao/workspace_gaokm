"""胜率自适应阈值调整模块。

根据策略近期表现动态调整买入/卖出阈值：
- 胜率下降时提高买入门槛
- 胜率上升时适当降低门槛
- 连续亏损时触发风控
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple


def calculate_rolling_win_rate(
    trade_history: List[dict],
    window: int = 20,
) -> float:
    """
    计算滚动窗口胜率。
    
    Args:
        trade_history: 交易历史
        window: 窗口大小
    
    Returns:
        胜率 (0-1)
    """
    if len(trade_history) < 5:
        return 0.5
    
    recent = trade_history[-window:] if len(trade_history) >= window else trade_history
    
    wins = sum(1 for t in recent if float(t.get("pnl_pct", 0)) > 0)
    return wins / len(recent) if recent else 0.5


def calculate_consecutive_losses(trade_history: List[dict]) -> int:
    """
    计算连续亏损次数。
    
    Args:
        trade_history: 交易历史
    
    Returns:
        连续亏损次数
    """
    if not trade_history:
        return 0
    
    count = 0
    for trade in reversed(trade_history):
        pnl = float(trade.get("pnl_pct", 0))
        if pnl < 0:
            count += 1
        else:
            break
    
    return count


def calculate_drawdown(equity_curve: List[float]) -> float:
    """
    计算当前回撤。
    
    Args:
        equity_curve: 权益曲线
    
    Returns:
        回撤比例 (0-1)
    """
    if len(equity_curve) < 2:
        return 0.0
    
    peak = max(equity_curve)
    current = equity_curve[-1]
    
    return max(0.0, (peak - current) / peak)


def calculate_sharpe_ratio(
    returns: List[float],
    risk_free_rate: float = 0.02,
) -> float:
    """
    计算夏普比率。
    
    Args:
        returns: 收益率序列
        risk_free_rate: 无风险利率
    
    Returns:
        夏普比率
    """
    if len(returns) < 2:
        return 0.0
    
    import statistics
    
    excess_returns = [r - risk_free_rate / 252 for r in returns]
    avg_return = statistics.mean(excess_returns)
    std_return = statistics.stdev(excess_returns)
    
    if std_return == 0:
        return 0.0
    
    # 年化夏普比率
    sharpe = (avg_return / std_return) * (252 ** 0.5)
    return round(sharpe, 4)


def adaptive_thresholds(
    paper: dict,
    base_min_score: float = 55.0,
    base_sell_score: float = 45.0,
    config: Optional[dict] = None,
) -> Tuple[float, float, Dict[str, any]]:
    """
    根据策略表现自适应调整阈值。
    
    Args:
        paper: 纸面账户数据
        base_min_score: 基础买入评分阈值
        base_sell_score: 基础卖出评分阈值
        config: 配置参数
    
    Returns:
        (调整后买入阈值, 调整后卖出阈值, 调整详情)
    """
    config = config or {}
    trades = paper.get("trades") or []
    
    # 默认不调整
    adjusted_min = base_min_score
    adjusted_sell = base_sell_score
    adjustments = {
        "win_rate": None,
        "consecutive_losses": 0,
        "drawdown": 0.0,
        "sharpe_ratio": 0.0,
        "threshold_adjustment": 0.0,
        "risk_level": "normal",
    }
    
    # 交易历史不足，不调整
    if len(trades) < 10:
        return adjusted_min, adjusted_sell, adjustments
    
    # 1. 计算滚动胜率
    window = config.get("rolling_window", 20)
    win_rate = calculate_rolling_win_rate(trades, window)
    adjustments["win_rate"] = round(win_rate, 4)
    
    # 2. 连续亏损检测
    max_consecutive = config.get("max_consecutive_losses", 3)
    consecutive = calculate_consecutive_losses(trades)
    adjustments["consecutive_losses"] = consecutive
    
    # 3. 回撤计算
    equities = paper.get("equity_curve") or []
    drawdown = calculate_drawdown(equities)
    adjustments["drawdown"] = round(drawdown, 4)
    
    # 4. 夏普比率
    returns = [float(t.get("pnl_pct", 0)) / 100 for t in trades]
    sharpe = calculate_sharpe_ratio(returns)
    adjustments["sharpe_ratio"] = sharpe
    
    # 计算调整量
    adjustment = 0.0
    risk_level = "normal"
    
    # 基于胜率调整
    if win_rate < 0.35:
        # 胜率过低，大幅提高门槛
        adjustment += 15.0
        risk_level = "high"
    elif win_rate < 0.45:
        # 胜率偏低，适当提高门槛
        adjustment += 8.0
        risk_level = "elevated"
    elif win_rate > 0.65:
        # 胜率较高，适当降低门槛
        adjustment -= 5.0
    
    # 连续亏损风控
    if consecutive >= max_consecutive:
        adjustment += 10.0
        risk_level = "high"
    elif consecutive >= 2:
        adjustment += 5.0
        if risk_level == "normal":
            risk_level = "elevated"
    
    # 回撤风控
    max_drawdown = config.get("max_drawdown", 0.15)
    if drawdown > max_drawdown:
        adjustment += 10.0
        risk_level = "high"
    elif drawdown > max_drawdown * 0.7:
        adjustment += 5.0
        if risk_level == "normal":
            risk_level = "elevated"
    
    # 夏普比率调整
    if sharpe < 0.5:
        adjustment += 5.0
    elif sharpe > 1.5:
        adjustment -= 3.0
    
    # 应用调整
    adjustments["threshold_adjustment"] = round(adjustment, 1)
    adjustments["risk_level"] = risk_level
    
    adjusted_min = max(40.0, min(85.0, base_min_score + adjustment))
    adjusted_sell = max(30.0, min(70.0, base_sell_score + adjustment * 0.5))
    
    return adjusted_min, adjusted_sell, adjustments


def get_risk_controls(
    paper: dict,
    config: Optional[dict] = None,
) -> Dict[str, any]:
    """
    获取风控状态和限制。
    
    Args:
        paper: 纸面账户数据
        config: 配置参数
    
    Returns:
        风控状态
    """
    config = config or {}
    trades = paper.get("trades") or []
    
    controls = {
        "trading_allowed": True,
        "position_limit": 1.0,
        "max_positions": config.get("max_positions", 5),
        "min_score": 55.0,
        "warning_messages": [],
    }
    
    # 交易不足，允许交易
    if len(trades) < 5:
        return controls
    
    # 获取自适应阈值
    min_score, sell_score, adjustments = adaptive_thresholds(
        paper,
        base_min_score=config.get("min_score", 55.0),
        config=config,
    )
    
    controls["min_score"] = min_score
    controls["sell_score"] = sell_score
    controls["risk_level"] = adjustments["risk_level"]
    controls["win_rate"] = adjustments["win_rate"]
    controls["consecutive_losses"] = adjustments["consecutive_losses"]
    controls["drawdown"] = adjustments["drawdown"]
    controls["sharpe_ratio"] = adjustments["sharpe_ratio"]
    
    # 高风险限制
    if adjustments["risk_level"] == "high":
        controls["warning_messages"].append(
            f"⚠️ 高风险状态：胜率{adjustments['win_rate']:.1%}，"
            f"连续亏损{adjustments['consecutive_losses']}次"
        )
        # 限制仓位
        controls["position_limit"] = 0.5
        controls["max_positions"] = min(3, config.get("max_positions", 5))
    elif adjustments["risk_level"] == "elevated":
        controls["warning_messages"].append(
            f"⚡ 中风险状态：胜率{adjustments['win_rate']:.1%}"
        )
        controls["position_limit"] = 0.75
    
    # 连续3次亏损暂停交易
    if adjustments["consecutive_losses"] >= config.get("pause_after_losses", 5):
        controls["trading_allowed"] = False
        controls["warning_messages"].append(
            f"🚫 连续亏损{adjustments['consecutive_losses']}次，暂停交易"
        )
    
    return controls


def generate_performance_report(paper: dict) -> Dict[str, any]:
    """
    生成策略表现报告。
    
    Args:
        paper: 纸面账户数据
    
    Returns:
        表现报告
    """
    trades = paper.get("trades") or []
    holdings = paper.get("holdings") or []
    
    report = {
        "total_trades": len(trades),
        "winning_trades": 0,
        "losing_trades": 0,
        "win_rate": 0.0,
        "avg_win_pct": 0.0,
        "avg_loss_pct": 0.0,
        "profit_loss_ratio": 0.0,
        "max_consecutive_wins": 0,
        "max_consecutive_losses": 0,
        "current_streak": 0,
        "holdings_count": len(holdings),
        "performance_score": 0,
    }
    
    if not trades:
        return report
    
    wins = []
    losses = []
    max_win_streak = 0
    max_loss_streak = 0
    current_streak = 0
    streak_type = None
    
    for trade in trades:
        pnl = float(trade.get("pnl_pct", 0))
        if pnl > 0:
            wins.append(pnl)
            current_streak = current_streak + 1 if streak_type == "win" else 1
            streak_type = "win"
            max_win_streak = max(max_win_streak, current_streak)
        elif pnl < 0:
            losses.append(abs(pnl))
            current_streak = current_streak + 1 if streak_type == "loss" else 1
            streak_type = "loss"
            max_loss_streak = max(max_loss_streak, current_streak)
    
    total_closed = len(wins) + len(losses)
    report["winning_trades"] = len(wins)
    report["losing_trades"] = len(losses)
    report["win_rate"] = len(wins) / total_closed if total_closed > 0 else 0.0
    report["avg_win_pct"] = round(sum(wins) / len(wins), 2) if wins else 0.0
    report["avg_loss_pct"] = round(sum(losses) / len(losses), 2) if losses else 0.0
    report["profit_loss_ratio"] = round(
        report["avg_win_pct"] / report["avg_loss_pct"], 2
    ) if report["avg_loss_pct"] > 0 else 0.0
    report["max_consecutive_wins"] = max_win_streak
    report["max_consecutive_losses"] = max_loss_streak
    report["current_streak"] = current_streak if streak_type == "loss" else 0
    
    # 综合评分（0-100）
    score = 50.0
    score += (report["win_rate"] - 0.5) * 40  # 胜率贡献
    score += min(report["profit_loss_ratio"], 3) * 5  # 盈亏比贡献
    score -= max(0, report["max_consecutive_losses"] - 2) * 3  # 连亏惩罚
    report["performance_score"] = max(0, min(100, round(score, 1)))
    
    return report
