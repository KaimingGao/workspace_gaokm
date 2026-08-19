"""动态加减仓模块。

实现基于评分的动态仓位管理：
- 加仓：评分≥60分加仓50%，凯利公式确定仓位大小，技术指标确认
- 梯度减仓：按评分分档减仓（<45减80%，45-50减50%，50-60减30%）
  
  分界线统一为60分：
  - ≥60分：不减仓，可加仓
  - <60分：梯度减仓
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from typing import Dict, Optional, Tuple


def _check_technical_confirmation(
    score: float,
    technical_info: Optional[dict] = None,
    min_technical_score: float = 40.0,
) -> Tuple[bool, str]:
    """
    检查技术指标是否确认加仓。
    
    Args:
        score: 综合评分
        technical_info: 技术指标信息
        min_technical_score: 最低技术分
    
    Returns:
        (是否确认, 原因)
    """
    if technical_info is None:
        # 如果没有技术指标数据，根据综合评分判断
        if score >= 70:
            return True, "综合评分高，无需额外技术确认"
        elif score >= 60:
            return False, "需要技术指标确认"
        else:
            return False, "评分不足以加仓"
    
    tech_score = technical_info.get("technical_score", 0)
    if tech_score >= min_technical_score:
        return True, f"技术分{tech_score:.1f}≥{min_technical_score:.0f}，确认加仓"
    else:
        return False, f"技术分{tech_score:.1f}<{min_technical_score:.0f}，不确认加仓"


def get_add_position_plan(
    score: float,
    current_shares: float,
    *,
    add_threshold: float = 60.0,
    add_ratio: float = 0.5,
    use_kelly: bool = True,
    use_technical: bool = True,
    technical_info: Optional[dict] = None,
    min_technical_score: float = 40.0,
    paper: Optional[dict] = None,
    rules: Optional[dict] = None,
) -> Dict:
    """
    获取加仓计划。
    
    Args:
        score: 当前评分
        current_shares: 当前持仓股数
        add_threshold: 加仓评分阈值
        add_ratio: 加仓比例（默认50%）
        use_kelly: 是否使用凯利公式
        use_technical: 是否需要技术指标确认
        technical_info: 技术指标信息
        min_technical_score: 最低技术分
        paper: 纸面账户数据（用于凯利公式）
        rules: 规则配置
    
    Returns:
        加仓计划详情
    """
    plan = {
        "should_add": False,
        "add_shares": 0,
        "add_ratio": 0,
        "price": 0,
        "reason": "",
        "kelly_position": None,
        "technical_confirmed": None,
    }
    
    # 检查评分是否达标
    if score < add_threshold:
        plan["reason"] = f"评分{score:.1f}<{add_threshold}，不满足加仓条件"
        return plan
    
    # 技术指标确认
    if use_technical:
        tech_confirmed, tech_reason = _check_technical_confirmation(
            score, technical_info, min_technical_score
        )
        plan["technical_confirmed"] = tech_confirmed
        if not tech_confirmed:
            plan["reason"] = f"技术指标未确认：{tech_reason}"
            return plan
        plan["reason"] = tech_reason
    
    # 计算加仓比例
    actual_ratio = add_ratio
    
    # 使用凯利公式调整
    if use_kelly and paper:
        from core.signal.factors.kelly import adaptive_position_sizing
        kelly_pos = adaptive_position_sizing(paper, score, rules or {})
        plan["kelly_position"] = kelly_pos
        
        # 凯利仓位作为加仓比例的上限
        # 实际加仓比例 = min(配置的加仓比例, 凯利建议)
        adjusted_ratio = min(add_ratio, max(0.1, kelly_pos * 2))
        actual_ratio = adjusted_ratio
    
    # 计算加仓股数
    add_shares = int(current_shares * actual_ratio)
    add_shares = (add_shares // 100) * 100  # 向下取整到100股
    
    if add_shares <= 0:
        add_shares = 100  # 至少100股
    
    plan["should_add"] = True
    plan["add_shares"] = add_shares
    plan["add_ratio"] = actual_ratio
    plan["reason"] = f"加仓{add_ratio*100:.0f}%（评分{score:.1f}分）"
    
    return plan


def get_reduce_position_plan(
    score: float,
    current_shares: float,
    *,
    min_score: float = 60.0,
) -> Dict:
    """
    获取减仓计划（梯度减仓）。
    
    分界线统一为60分：
    - 评分 ≥ 60：不减仓（交给加仓逻辑处理）
    - 评分 < 60：梯度减仓
      - < 45：减仓80%（极弱）
      - 45 ≤ 评分 < 50：减仓50%（偏弱）
      - 50 ≤ 评分 < 60：减仓30%（中性偏弱）
    
    Args:
        score: 当前评分
        current_shares: 当前持仓股数
        min_score: 减仓触发阈值（60分以上不减仓）
    
    Returns:
        减仓计划详情
    """
    plan = {
        "should_reduce": False,
        "reduce_shares": 0,
        "reduce_ratio": 0,
        "reason": "",
    }
    
    # 评分≥60分不减仓（与加仓阈值对齐）
    if score >= min_score:
        plan["reason"] = f"评分{score:.1f}≥{min_score:.0f}，不减仓"
        return plan
    
    # 根据评分确定减仓比例
    if score < 45:
        reduce_ratio = 0.8
        tier = "极弱"
    elif score < 50:
        reduce_ratio = 0.5
        tier = "偏弱"
    elif score < 60:
        reduce_ratio = 0.3
        tier = "中性偏弱"
    else:
        plan["reason"] = f"评分{score:.1f}，无需减仓"
        return plan
    
    # 持仓不足100股，无法减仓
    if current_shares < 100:
        plan["reason"] = f"持仓{current_shares}股不足100股，无法减仓"
        return plan
    
    # 计算目标减仓股数
    target_reduce = int(current_shares * reduce_ratio)
    target_reduce = (target_reduce // 100) * 100

    # 如果取整后减仓股数为0，说明持仓太少无法按比例减仓
    if target_reduce <= 0:
        # 极弱（<45分）直接清仓，其他档位保持不动
        if score < 45:
            reduce_shares = current_shares
            action_type = "清仓"
        else:
            plan["reason"] = f"持仓{int(current_shares)}股不足按{reduce_ratio*100:.0f}%减仓，保持不动（评分{score:.1f}，{tier}）"
            return plan
    elif current_shares - target_reduce < 100:
        # 减仓后剩余不足1手，全部清仓
        reduce_shares = current_shares
        action_type = "清仓"
    else:
        reduce_shares = target_reduce
        action_type = "减仓"
    
    plan["should_reduce"] = True
    plan["reduce_shares"] = reduce_shares
    plan["reduce_ratio"] = reduce_ratio
    plan["reason"] = f"{action_type}{reduce_ratio*100:.0f}%（评分{score:.1f}，{tier}）"
    plan["tier"] = tier
    plan["action_type"] = action_type
    
    return plan


def get_full_position_plan(
    score: float,
    current_shares: float,
    *,
    add_threshold: float = 60.0,
    min_score: float = 45.0,
    **kwargs,
) -> Dict:
    """
    获取完整的仓位调整计划（加仓/减仓/持有）。
    
    优先级：
    1. 评分≥60 → 尝试加仓
    2. 评分<65 → 考虑减仓
    3. 其他 → 持有
    
    Args:
        score: 当前评分
        current_shares: 当前持仓股数
        add_threshold: 加仓阈值
        min_score: 减仓阈值
        **kwargs: 传递给加仓计划的参数
    
    Returns:
        完整仓位调整计划
    """
    # 优先考虑加仓
    if score >= add_threshold:
        add_plan = get_add_position_plan(
            score, current_shares, add_threshold=add_threshold, **kwargs
        )
        if add_plan["should_add"]:
            return {
                "action": "add",
                "shares": add_plan["add_shares"],
                "reason": add_plan["reason"],
                "details": add_plan,
            }
    
    # 考虑减仓
    reduce_plan = get_reduce_position_plan(score, current_shares, min_score=min_score)
    if reduce_plan["should_reduce"]:
        return {
            "action": "reduce",
            "shares": reduce_plan["reduce_shares"],
            "reason": reduce_plan["reason"],
            "details": reduce_plan,
        }
    
    # 持有
    return {
        "action": "hold",
        "shares": 0,
        "reason": f"持有（评分{score:.1f}）",
    }
