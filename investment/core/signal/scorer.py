"""短线因子评分：动量 / 量价 / 相对强弱 / 波动 / 反转 / 流动性，输出 1～3 天观察池。

v2 优化：
- 因子交互项（动量×量价、反转×波动）
- 非线性打分（Sigmoid压缩极端值）
- 前置风控检查集成
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from core.signal.config import get_rank_defaults, load_signal_config
from core.signal.factor_registry import compute_configured_factors
from core.signal.factors.momentum import pct_change
from core.signal.factors.pre_trade import pre_trade_check
from core.signal.regime import assess_regime


def _sigmoid_score(raw_score: float) -> float:
    """Sigmoid压缩：将线性分数压缩到更平滑的分布。"""
    # 中心在50，宽度决定压缩程度
    x = (raw_score - 50) / 15
    return 100 / (1 + math.exp(-x))


def _interaction_bonus(factor_scores: Dict[str, float]) -> float:
    """
    因子交互奖励：
    - 动量强 + 量价配合 → 额外加分（趋势确认）
    - 技术形态 + 均线斜率 → 额外加分（形态+趋势共振）
    - 周线确认 + 动量 → 额外加分（大小周期共振）
    - 反转信号 + 低波动 → 额外加分（高质量反转）
    - 相对强弱 + 动量 → 额外加分（双重确认）
    """
    bonus = 0.0
    
    mom = factor_scores.get("momentum", 50)
    vp = factor_scores.get("volume_price", 50)
    rev = factor_scores.get("reversal", 50)
    vol = factor_scores.get("volatility", 50)
    rs = factor_scores.get("relative_strength", 50)
    tech = factor_scores.get("technical_pattern", 50)
    weekly = factor_scores.get("weekly_confirm", 50)
    ma_slope = factor_scores.get("ma_slope", 50)
    qual = factor_scores.get("quality", 50)
    
    # 动量 × 量价 交互
    if mom > 65 and vp > 60:
        bonus += 4.0  # 趋势确认
    elif mom > 55 and vp > 55:
        bonus += 2.0
    
    # 技术形态 × 均线斜率 交互（形态+趋势共振）
    if tech > 65 and ma_slope > 60:
        bonus += 5.0  # 技术形态好且均线趋势向上
    elif tech > 55 and ma_slope > 55:
        bonus += 2.5
    
    # 周线确认 × 动量 交互（大小周期共振）
    if weekly > 65 and mom > 60:
        bonus += 4.0  # 周线和日线趋势共振
    elif weekly > 55 and mom > 55:
        bonus += 2.0
    
    # 反转 × 波动 交互
    if rev > 65 and vol > 55:
        bonus += 2.5  # 高质量反转
    
    # 相对强弱 × 动量 交互
    if rs > 65 and mom > 60:
        bonus += 3.0  # 双重确认
    
    # 质量 × 低波动（防守型）
    if qual > 60 and vol > 55:
        bonus += 1.5
    
    # 多因子共振加分（4+因子同时向好）
    bullish_count = sum(1 for s in [mom, tech, weekly, ma_slope, vp] if s > 60)
    if bullish_count >= 4:
        bonus += 3.0  # 多因子共振
    
    return bonus


def _interaction_penalty(factor_scores: Dict[str, float]) -> float:
    """
    因子冲突惩罚：
    - 动量强 + 反转强 → 信号矛盾
    - 技术形态弱 + 均线斜率弱 → 趋势恶化
    - 周线弱 + 动量强 → 大小周期背离
    - 高波动 + 动量弱 → 风险过高
    """
    penalty = 0.0
    
    mom = factor_scores.get("momentum", 50)
    rev = factor_scores.get("reversal", 50)
    vol = factor_scores.get("volatility", 50)
    liq = factor_scores.get("liquidity", 50)
    tech = factor_scores.get("technical_pattern", 50)
    weekly = factor_scores.get("weekly_confirm", 50)
    ma_slope = factor_scores.get("ma_slope", 50)
    vp = factor_scores.get("volume_price", 50)
    
    # 动量与反转矛盾
    if mom > 60 and rev < 35:
        penalty += 4.0  # 趋势向上但超买，有回调风险
    elif mom < 40 and rev > 60:
        penalty += 2.5  # 趋势向下但超卖，可能继续下跌
    
    # 技术形态 + 均线斜率同时弱势
    if tech < 35 and ma_slope < 40:
        penalty += 4.0  # 技术面全面恶化
    elif tech < 45 and ma_slope < 45:
        penalty += 2.0
    
    # 周线与日线背离
    if weekly < 40 and mom > 60:
        penalty += 3.0  # 周线弱势但日线强势，警惕回调
    elif weekly > 60 and mom < 40:
        penalty += 2.0  # 周线强势但日线弱势，可能继续调整
    
    # 高波动 + 低流动性
    if vol < 35 and liq < 40:
        penalty += 3.0  # 波动大且流动性差
    
    # 多因子共振减分（4+因子同时走弱）
    bearish_count = sum(1 for s in [mom, tech, weekly, ma_slope, vp] if s < 40)
    if bearish_count >= 4:
        penalty += 2.5
    
    return penalty


def score_bars(
    bars: List[dict],
    *,
    horizon_days: int = 3,
    quote: Optional[dict] = None,
    index_bars: Optional[List[dict]] = None,
    config: Optional[dict] = None,
    fundamentals: Optional[dict] = None,
    sentiment: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    对单票日线打分。v2版本集成因子交互和非线性打分。
    """
    cfg = config or load_signal_config()
    weights = cfg.get("weights") or {}

    hr = cfg.get("hard_reject") or {}
    min_bars = int(hr.get("min_bars", 2))
    gain_max = float(hr.get("mom3_gain_max_pct", 15))
    loss_min = float(hr.get("mom3_loss_min_pct", -12))
    stop_pct = float((cfg.get("invalidation") or {}).get("stop_pct", 0.03))

    horizon_days = max(1, min(int(horizon_days or 3), 3))

    if not bars or len(bars) < min_bars:
        return {
            "score": 0,
            "hard_reject": True,
            "reject_reason": "日线数据不足",
            "factors": {},
            "reasons": [],
            "invalidation": [],
        }

    mom3 = pct_change(bars, min(3, len(bars) - 1))

    # 检查是否配置了"硬拒绝改为降权"
    soft_reject = bool((cfg.get("hard_reject") or {}).get("soft_reject", False))
    
    if mom3 is not None and mom3 >= gain_max:
        if soft_reject:
            # 软拒绝：降权而非拒绝
            pass
        else:
            return {
                "score": 0,
                "hard_reject": True,
                "reject_reason": f"近3日涨幅过大({mom3:.1f}%)，短线追高风险高",
                "factors": {"momentum_3d": mom3},
                "reasons": [],
                "invalidation": [],
            }

    if mom3 is not None and mom3 <= loss_min:
        if soft_reject:
            pass
        else:
            return {
                "score": 0,
                "hard_reject": True,
                "reject_reason": f"近3日跌幅过大({mom3:.1f}%)，短线动能偏弱",
                "factors": {"momentum_3d": mom3},
                "reasons": [],
                "invalidation": [],
            }

    last_change = None
    if quote and quote.get("change_raw") is not None:
        last_change = float(quote["change_raw"])
    elif len(bars) >= 2:
        last_change = pct_change(bars, 1)

    # 前置风控检查
    risk_config = cfg.get("pre_trade_risk", {})
    if risk_config and risk_config.get("enabled", True):
        passed, risk_checks = pre_trade_check(bars, config=risk_config)
        if not passed:
            # 风控不通过，标记为低分但不硬拒绝
            risk_penalty = 15.0
        else:
            risk_penalty = 0.0
    else:
        risk_penalty = 0.0
        risk_checks = {}

    # 先评估市场状态，获取权重调整建议
    regime_info = assess_regime(index_bars, cfg.get("regime"))
    
    # 根据市场环境动态调整因子权重（因子择时）
    adjusted_weights = dict(weights)
    weight_adjustments = regime_info.get("adjustments", {}).get("weight_adjustments", {})
    
    # 获取因子择时配置
    enabled_factors = regime_info.get("adjustments", {}).get("enabled_factors", [])
    disabled_factors = regime_info.get("adjustments", {}).get("disabled_factors", [])
    
    # 应用因子择时：禁用的因子权重设为0
    for factor in disabled_factors:
        if factor in adjusted_weights:
            adjusted_weights[factor] = 0.0
    
    # 应用权重乘数调整
    for factor_name, multiplier in weight_adjustments.items():
        if factor_name in adjusted_weights:
            adjusted_weights[factor_name] = weights[factor_name] * multiplier
    
    # 只保留启用的因子
    if enabled_factors:
        filtered_weights = {k: v for k, v in adjusted_weights.items() if k in enabled_factors}
        adjusted_weights = filtered_weights
    
    # 归一化权重，确保总和为1
    total_weight = sum(adjusted_weights.values())
    if total_weight > 0:
        adjusted_weights = {k: v / total_weight for k, v in adjusted_weights.items()}
    else:
        adjusted_weights = dict(weights)

    sub_scores, factor_contrib, factors = compute_configured_factors(
        bars,
        weights=adjusted_weights,
        quote=quote,
        index_bars=index_bars,
        config=cfg,
        last_change=last_change,
        fundamentals=fundamentals,
        sentiment=sentiment,
    )

    # v2: 因子交互调整
    interaction_bonus = _interaction_bonus(sub_scores)
    interaction_penalty = _interaction_penalty(sub_scores)
    interaction_adj = interaction_bonus - interaction_penalty
    
    # v2: 非线性打分（先线性加总，再Sigmoid压缩）
    raw_total = sum(factor_contrib.values()) + interaction_adj
    
    # 基础分 + 交互调整
    total = raw_total
    
    # 应用市场状态惩罚
    penalty = float(regime_info.get("score_penalty") or 0)
    if penalty > 0:
        total -= penalty
    
    # 应用风控惩罚
    if risk_penalty > 0:
        total -= risk_penalty

    # 舆情已并入 alt_sentiment 因子，不再硬编码加减分（V2.1）
    total = round(max(0.0, min(100.0, total)), 1)

    if last_change is not None:
        factors["last_change"] = round(last_change, 2)
    factors["last_close"] = bars[-1]["close"]
    
    # 风控检查结果
    if risk_checks:
        factors["risk_check"] = risk_checks

    reasons = []
    if mom3 is not None:
        reasons.append(f"近3日涨跌 {mom3:+.2f}%")
    if factors.get("volume_ratio") is not None:
        reasons.append(f"量比约 {factors['volume_ratio']:.2f}")
    if factors.get("turnover_ratio") is not None:
        reasons.append(f"成交额比 {factors['turnover_ratio']:.2f}")
    if factors.get("reversal_rsi") is not None:
        reasons.append(f"RSI(14) {factors['reversal_rsi']:.1f}")
    if factors.get("value_pe") is not None:
        reasons.append(f"PE 约 {factors['value_pe']:.1f}")
    if factors.get("quality_roe") is not None:
        reasons.append(f"ROE 约 {factors['quality_roe']:.1f}%")
    if factors.get("excess_return_pct") is not None:
        reasons.append(f"相对基准超额 {factors['excess_return_pct']:+.2f}%")
    elif last_change is not None:
        reasons.append(f"当日涨跌 {last_change:+.2f}%")
    if factors.get("atr_pct") is not None:
        reasons.append(f"近5日波动(ATR%) {factors['atr_pct']:.2f}%")
    
    # 交互相关的reason
    if interaction_bonus > 0:
        reasons.append(f"因子交互加分 +{interaction_bonus:.1f}")
    if interaction_penalty > 0:
        reasons.append(f"因子冲突扣分 -{interaction_penalty:.1f}")
    
    if regime_info.get("reason"):
        reasons.append(regime_info["reason"])

    alt_label = factors.get("alt_sentiment_label")
    if alt_label and alt_label not in ("neutral", ""):
        reasons.append(f"舆情因子 {alt_label}")

    last_close = bars[-1]["close"]
    stop_ref = round(last_close * (1 - stop_pct), 2)
    invalidation = [
        f"跌破参考位约 {stop_ref}（相对最新价约 -{stop_pct*100:.0f}%）可视为短线观察失效",
        "若放量下跌且跌破近3日低点，降低关注优先级",
    ]
    if horizon_days <= 2:
        invalidation.append("1～2 天窗口内若无法站稳均价/缺口，观望优于追涨")

    contrib_out = dict(factor_contrib)
    if penalty:
        contrib_out["regime_penalty"] = round(-penalty, 2)
    if interaction_adj != 0:
        contrib_out["interaction_adj"] = round(interaction_adj, 2)
    if risk_penalty > 0:
        contrib_out["risk_penalty"] = round(-risk_penalty, 2)

    return {
        "score": total,
        "hard_reject": False,
        "reject_reason": "",
        "factors": factors,
        "reasons": reasons,
        "invalidation": invalidation,
        "sub_scores": sub_scores,
        "factor_contrib": contrib_out,
        "regime": regime_info,
        "interaction": {
            "bonus": round(interaction_bonus, 2),
            "penalty": round(interaction_penalty, 2),
        },
    }


def rank_candidates(
    scored: List[dict],
    *,
    limit: int = 8,
    min_score: float = 55.0,
    config: Optional[dict] = None,
) -> List[dict]:
    """过滤硬拒绝与低分，按分数排序截断。"""
    cfg = config or load_signal_config()
    defaults = get_rank_defaults(cfg)
    if min_score == 55.0:
        min_score = defaults["min_score"]
    if limit == 8:
        limit = int(defaults["default_limit"])

    limit = max(1, min(int(limit or 8), 15))
    kept = [
        s
        for s in scored
        if not s.get("hard_reject") and (s.get("score") or 0) >= min_score
    ]
    kept.sort(key=lambda x: x.get("score") or 0, reverse=True)
    return kept[:limit]
