"""生产 ŷ 门禁（纯函数）：启发式 0–100 不得冒充 predicted_score。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Mapping, Optional, Tuple

SCALE_YHAT = "predicted_yhat"
SCALE_HEURISTIC = "heuristic_0_100"
SCALE_UNKNOWN = "unknown"

__all__ = [
    "SCALE_HEURISTIC",
    "SCALE_UNKNOWN",
    "SCALE_YHAT",
    "allows_production_yhat",
    "infer_score_scale",
]


def infer_score_scale(item: Optional[Mapping[str, Any]]) -> str:
    """主分标尺：predicted_yhat | heuristic_0_100 | unknown。"""
    if not isinstance(item, Mapping):
        return SCALE_UNKNOWN
    explicit = str(item.get("score_scale") or "").strip()
    if explicit in (SCALE_YHAT, SCALE_HEURISTIC):
        return explicit
    try:
        from core.signal.dual_score import is_heuristic_score_scale

        if is_heuristic_score_scale(dict(item)):
            return SCALE_HEURISTIC
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in gate.py", exc_info=True)
        if str(item.get("return_model_source") or "") == "oos_failed_heuristic":
            return SCALE_HEURISTIC
    if (
        item.get("predicted_score") is not None
        or item.get("predicted_score_oo") is not None
        or item.get("y_oo") is not None
        or item.get("predicted_score_eod") is not None
    ):
        return SCALE_YHAT
    return SCALE_UNKNOWN


def allows_production_yhat(
    item: Optional[Mapping[str, Any]] = None,
    *,
    fetch_ok: bool = True,
    quality_gated: bool = False,
) -> Tuple[bool, str]:
    """生产买入/主簿：须为回归 ŷ，且未被质量门禁/硬拒绝。

    不改打分结果，只给信封 ``production_ok``。启发式仍可出现在对照字段。
    """
    if not fetch_ok:
        return False, "score_fetch_failed"
    row = dict(item or {})
    gated = bool(quality_gated or row.get("quality_gate"))
    if gated:
        return False, str(row.get("gate_reason") or "data_quality_gate")
    if row.get("hard_reject"):
        return False, str(row.get("reject_reason") or row.get("gate_reason") or "hard_reject")
    scale = infer_score_scale(row)
    if scale == SCALE_HEURISTIC:
        return False, "score_scale:heuristic_0_100"
    if scale != SCALE_YHAT:
        return False, f"score_scale:{scale or SCALE_UNKNOWN}"
    y = row.get("predicted_score")
    if y is None:
        y = row.get("predicted_score_oo")
    if y is None:
        y = row.get("y_oo")
    if y is None:
        y = row.get("predicted_score_eod")
    if y is None:
        return False, "missing_predicted_score"
    return True, ""
