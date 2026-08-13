"""OOS 失败组 label 提取（无循环依赖，供 rank / live / 证据包共用）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


def oos_failed_cluster_labels(
    clusters: Optional[Sequence[Any]] = None,
    *,
    active: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """返回 OOS 门禁未过的组 label 列表（跳过 skipped）。"""
    if clusters is None:
        if active is None:
            from core.signal.cluster_live import load_active_cluster_weights

            art = load_active_cluster_weights()
        else:
            art = active
        clusters = list((art or {}).get("clusters") or [])
    failed: List[str] = []
    seen = set()
    for cl in clusters or []:
        if not isinstance(cl, dict):
            continue
        lab = str(cl.get("label") or cl.get("cluster_label") or "").strip()
        if not lab:
            continue
        gate = cl.get("oos_gate")
        failed_flag = False
        if isinstance(gate, dict) and gate:
            if gate.get("skipped"):
                continue
            if gate.get("passed") is False or gate.get("ok") is False:
                failed_flag = True
        elif "oos_passed" in cl and cl.get("oos_passed") is not None:
            failed_flag = not bool(cl.get("oos_passed"))
        if failed_flag and lab not in seen:
            seen.add(lab)
            failed.append(lab)
    return failed
