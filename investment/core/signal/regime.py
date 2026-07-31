"""市场状态门控（P6.5）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.signal.factors.momentum import pct_change


def assess_regime(
    index_bars: Optional[List[dict]],
    regime_cfg: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    评估市场状态，返回状态信息和参数调整建议。
    
    状态等级：
    - bear: 熊市（大幅下跌）
    - weak: 弱势（小幅下跌）
    - neutral: 中性（震荡）
    - strong: 强势（小幅上涨）
    - bull: 牛市（大幅上涨）
    """
    cfg = regime_cfg or {}
    if not cfg.get("enabled", True):
        return {
            "regime": "neutral",
            "index_return_pct": None,
            "score_penalty": 0.0,
            "reason": None,
            "adjustments": {},
        }

    if not index_bars or len(index_bars) < 2:
        return {
            "regime": "neutral",
            "index_return_pct": None,
            "score_penalty": 0.0,
            "reason": "无基准日线",
            "adjustments": {},
        }

    days = int(cfg.get("weak_trend_days") or 20)
    threshold = float(cfg.get("weak_trend_threshold_pct") or -3.0)
    penalty = float(cfg.get("score_penalty") or 5.0)

    idx_ret = pct_change(index_bars, min(days, len(index_bars) - 1))
    if idx_ret is None:
        return {
            "regime": "neutral",
            "index_return_pct": None,
            "score_penalty": 0.0,
            "reason": "基准收益不可算",
            "adjustments": {},
        }

    # 计算波动率（最近5日波动幅度）
    recent_bars = index_bars[-5:] if len(index_bars) >= 5 else index_bars
    volatility = 0.0
    if len(recent_bars) >= 2:
        changes = []
        for i in range(1, len(recent_bars)):
            prev_close = recent_bars[i-1]["close"]
            curr_close = recent_bars[i]["close"]
            if prev_close > 0:
                changes.append(abs((curr_close - prev_close) / prev_close * 100))
        if changes:
            volatility = sum(changes) / len(changes)

    # 根据收益和波动率判断状态
    regime = "neutral"
    score_penalty = 0.0
    reason = None
    adjustments = {}

    # 定义阈值
    bear_threshold = -8.0  # 熊市阈值
    weak_threshold = -3.0  # 弱势阈值
    strong_threshold = 3.0  # 强势阈值
    bull_threshold = 8.0  # 牛市阈值

    if idx_ret <= bear_threshold:
        regime = "bear"
        score_penalty = penalty * 2  # 熊市加倍惩罚
        reason = f"基准近{days}日下跌({idx_ret:+.2f}%)，熊市"
        adjustments = {
            "min_score": 75,  # 大幅提高买入门槛
            "max_positions": 3,  # 减少持仓数量
            "position_pct": 0.10,  # 降低单只仓位
            "weight_adjustments": {
                "momentum": 0.8,  # 降低动量权重
                "quality": 1.3,   # 提高质量权重
                "liquidity": 1.3, # 提高流动性权重
            },
        }
    elif idx_ret <= weak_threshold:
        regime = "weak"
        score_penalty = penalty
        reason = f"基准近{days}日偏弱({idx_ret:+.2f}%)"
        adjustments = {
            "min_score": 70,
            "max_positions": 4,
            "position_pct": 0.12,
            "weight_adjustments": {
                "momentum": 0.9,
                "quality": 1.15,
                "liquidity": 1.15,
            },
        }
    elif idx_ret >= bull_threshold:
        regime = "bull"
        score_penalty = 0.0
        reason = f"基准近{days}日上涨({idx_ret:+.2f}%)，牛市"
        adjustments = {
            "min_score": 60,  # 降低买入门槛
            "max_positions": 5,
            "position_pct": 0.15,
            "weight_adjustments": {
                "momentum": 1.1,   # 提高动量权重
                "volume_price": 1.1, # 提高量价权重
            },
        }
    elif idx_ret >= strong_threshold:
        regime = "strong"
        score_penalty = 0.0
        reason = f"基准近{days}日偏强({idx_ret:+.2f}%)"
        adjustments = {
            "min_score": 62,
            "max_positions": 5,
            "position_pct": 0.15,
            "weight_adjustments": {
                "momentum": 1.05,
                "volume_price": 1.05,
            },
        }
    else:
        regime = "neutral"
        score_penalty = 0.0
        reason = None
        adjustments = {
            "min_score": 65,
            "max_positions": 5,
            "position_pct": 0.15,
            "weight_adjustments": {},
        }

    # 高波动环境额外调整
    if volatility > 5.0:
        adjustments["min_score"] = adjustments.get("min_score", 65) + 3
        adjustments["position_pct"] = min(0.15, adjustments.get("position_pct", 0.15) * 0.9)

    # 因子择时：根据市场环境选择有效因子
    factor_selection = _select_factors_by_regime(regime, volatility)
    adjustments["enabled_factors"] = factor_selection["enabled"]
    adjustments["disabled_factors"] = factor_selection["disabled"]

    return {
        "regime": regime,
        "index_return_pct": round(idx_ret, 2),
        "volatility_pct": round(volatility, 2),
        "score_penalty": score_penalty,
        "reason": reason,
        "adjustments": adjustments,
    }


def _select_factors_by_regime(regime: str, volatility: float) -> Dict[str, List[str]]:
    """
    根据市场环境选择有效因子。
    
    因子择时逻辑：
    - 牛市/强势：优先动量、量价、相对强弱，降低估值/质量
    - 熊市/弱势：优先质量、流动性、估值，降低动量
    - 高波动：优先流动性、反转，降低动量
    """
    all_factors = [
        "momentum", "volume_price", "relative_strength", 
        "volatility", "reversal", "liquidity", 
        "value", "quality"
    ]
    
    if regime in ("bull", "strong"):
        # 牛市/强势：
        # 启用：动量、量价、相对强弱、波动
        # 降权/禁用：估值、质量（牛市中估值因子效果差）
        enabled = ["momentum", "volume_price", "relative_strength", "volatility", "reversal"]
        disabled = ["value", "quality"]
        
    elif regime in ("bear", "weak"):
        # 熊市/弱势：
        # 启用：质量、流动性、估值、反转
        # 降权/禁用：动量、量价（熊市中动量因子失效）
        enabled = ["quality", "liquidity", "value", "reversal", "volatility"]
        disabled = ["momentum", "volume_price", "relative_strength"]
        
    elif volatility > 5.0:
        # 高波动环境：
        # 启用：流动性、反转、波动、质量
        # 降权/禁用：动量（高波动中动量不可靠）
        enabled = ["liquidity", "reversal", "volatility", "quality", "value"]
        disabled = ["momentum", "volume_price", "relative_strength"]
        
    elif volatility < 2.0:
        # 低波动环境：
        # 启用：动量、相对强弱、量价
        # 降权/禁用：波动（低波动中波动因子无区分度）
        enabled = ["momentum", "relative_strength", "volume_price", "value", "quality"]
        disabled = ["volatility", "reversal"]
        
    else:
        # 中性环境：全部启用
        enabled = all_factors
        disabled = []
    
    return {"enabled": enabled, "disabled": disabled}