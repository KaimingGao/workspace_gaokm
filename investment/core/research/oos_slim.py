"""归档用精简 OOS 门禁结构（避免整份回测曲线落盘）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, Optional


def slim_oos_gate(gate: Any) -> Optional[Dict[str, Any]]:
    """归档用精简 OOS，供 live 证据包 / UI 悬浮注释读取（避免整份回测曲线）。"""
    if not isinstance(gate, dict) or not gate:
        return None
    keep = (
        "ok",
        "passed",
        "skipped",
        "reason",
        "note",
        "delta_oos_pp",
        "oos_tol_pp",
        "scope",
        "cluster_label",
        "stock_count",
        "lookback",
        "horizon_days",
        "top_k",
        "baseline_rank_mode",
        "research_rank_mode",
    )
    out = {k: gate[k] for k in keep if k in gate}

    def _arm_oos(arm: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(arm, dict):
            return None
        oos = arm.get("oos") if isinstance(arm.get("oos"), dict) else {}
        slim_arm: Dict[str, Any] = {}
        if oos.get("oos_return_pct") is not None:
            slim_arm["oos_return_pct"] = oos.get("oos_return_pct")
        if oos.get("is_return_pct") is not None:
            slim_arm["is_return_pct"] = oos.get("is_return_pct")
        if oos.get("failed") is not None:
            slim_arm["failed"] = oos.get("failed")
        if oos.get("fail_reason"):
            slim_arm["fail_reason"] = oos.get("fail_reason")
        return {"oos": slim_arm} if slim_arm else None

    for src_key, dst_key in (
        ("baseline", "baseline"),
        ("research", "research"),
        ("current", "baseline"),
        ("suggested", "research"),
    ):
        if dst_key in out:
            continue
        arm = _arm_oos(gate.get(src_key))
        if arm:
            out[dst_key] = arm
    return out or None
