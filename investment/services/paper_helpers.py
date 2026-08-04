"""PaperService 纯函数辅助（评分公式等）。"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _build_score_formula(score_info: dict) -> str:
    """生成收益分 ŷ 公式字符串（因子系数 β · z）。

    优先用 ``return_model`` / ``ReturnScoreModel``；无模型则空串（不再拼规则加权公式）。
    """
    if not score_info:
        return ""
    subs = score_info.get("sub_scores") or {}
    model = score_info.get("return_model")
    if model is None and score_info.get("coefficients"):
        model = {
            "intercept": score_info.get("intercept", 0.0),
            "coefficients": score_info.get("coefficients"),
            "z_means": score_info.get("z_means") or {},
            "z_stds": score_info.get("z_stds") or {},
            "standardized": score_info.get("standardized", True),
        }
    try:
        from core.signal.return_score import ReturnScoreModel

        rm = model if isinstance(model, ReturnScoreModel) else ReturnScoreModel.from_dict(model)
    except Exception:
        rm = None
    if rm is None:
        return ""
    try:
        return rm.format_formula(subs) or ""
    except Exception:
        return ""


def _active_return_model_payload(
    *,
    group_model: Any = None,
    global_model: Any = None,
    return_model_source: Optional[str] = None,
) -> Dict[str, Any]:
    """给前端悬浮注释用的精简模型快照。"""
    src = str(return_model_source or "")
    chosen = None
    if src.startswith("cluster") and group_model is not None:
        chosen = group_model
    elif global_model is not None:
        chosen = global_model
    elif group_model is not None:
        chosen = group_model
    if chosen is None:
        return {}
    try:
        d = chosen.to_dict() if hasattr(chosen, "to_dict") else dict(chosen)
    except Exception:
        return {}
    coefs = dict(d.get("coefficients") or {})
    coefs.pop("intercept", None)
    return {
        "intercept": d.get("intercept"),
        "coefficients": coefs,
        "z_means": d.get("z_means") or {},
        "z_stds": d.get("z_stds") or {},
        "standardized": d.get("standardized", True),
    }
