"""PaperService 纯函数辅助（评分公式等）。"""

from __future__ import annotations


def _build_score_formula(score_info: dict) -> str:
    """根据 sub_scores 和 factor_contrib 生成评分公式字符串。"""
    sub_scores = score_info.get("sub_scores") or {}
    contrib = score_info.get("factor_contrib") or {}
    if not sub_scores or not contrib:
        return ""

    from core.signal.config import load_signal_config

    signal_cfg = load_signal_config()
    weights = signal_cfg.get("weights") or {}
    factor_labels = {
        "momentum": "动量",
        "technical_pattern": "技术形态",
        "volume_price": "量价",
        "ma_slope": "均线斜率",
        "weekly_confirm": "周线确认",
        "relative_strength": "相对强弱",
        "volatility": "波动",
        "reversal": "反转",
        "liquidity": "流动性",
        "value": "估值",
        "quality": "质量",
    }

    terms = []
    total_contrib = 0
    for factor_name in sub_scores:
        weight = weights.get(factor_name)
        if weight is None:
            continue
        label = factor_labels.get(factor_name, factor_name)
        sub_score = sub_scores[factor_name]
        contribution = contrib.get(factor_name, 0)
        total_contrib += contribution
        terms.append(f"{label}({sub_score:.1f}×{weight:.2f}={contribution:.2f})")

    if not terms:
        return ""

    formula = " + ".join(terms)
    penalty = contrib.get("regime_penalty")
    if penalty:
        penalty_val = abs(float(penalty))
        formula += f" - 环境惩罚({penalty_val:.2f})"
        total_contrib += float(penalty)

    interaction = contrib.get("interaction_adj")
    if interaction is not None:
        ival = float(interaction)
        if ival != 0:
            op = " + " if ival > 0 else " - "
            formula += f"{op}交互调整({abs(ival):.2f})"
            total_contrib += ival

    risk_pen = contrib.get("risk_penalty")
    if risk_pen is not None:
        rpval = abs(float(risk_pen))
        if rpval > 0:
            formula += f" - 风控惩罚({rpval:.2f})"
            total_contrib += float(risk_pen)

    sent_adj = contrib.get("sentiment_adj")
    if sent_adj is not None:
        sval = float(sent_adj)
        if sval != 0:
            op = " + " if sval > 0 else " - "
            formula += f"{op}舆情({abs(sval):.1f})"
            total_contrib += sval

    formula += f" = {round(total_contrib, 1)}"
    return formula
