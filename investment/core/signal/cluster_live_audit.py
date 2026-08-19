"""分组 live · 双分对照审计样本（从 cluster_live 按用例拆出）。"""

from __future__ import annotations
import logging

logger = logging.getLogger(__name__)
from core.numbers import now_iso_utc

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def pick_audit_codes(
    cmap: Dict[str, Any],
    book_rows: List[Any],
    n: int,
    *,
    offset: int = 0,
) -> List[str]:
    """选双分对照样本：优先合并簿，再按组轮询；``offset`` 旋转起点。"""
    n = max(1, min(int(n), 12))
    off = abs(int(offset or 0))
    book_codes: List[str] = []
    for row in book_rows or []:
        code = str((row or {}).get("stock_code") or "").strip()
        if code and code in cmap and code not in book_codes:
            book_codes.append(code)

    by_label: Dict[str, List[str]] = {}
    seed_order = list(book_codes)
    for code in cmap.keys():
        c = str(code).strip()
        if c and c not in seed_order:
            seed_order.append(c)
    for c in seed_order:
        meta = cmap.get(c) or {}
        lab = str(meta.get("cluster_label") or "?")
        by_label.setdefault(lab, []).append(c)

    if not by_label:
        return []

    labels = sorted(by_label.keys())
    label_start = off % len(labels)
    labels = labels[label_start:] + labels[:label_start]
    for lab in labels:
        members = by_label[lab]
        if not members:
            continue
        m0 = off % len(members)
        by_label[lab] = members[m0:] + members[:m0]

    codes: List[str] = []
    idx = 0
    while len(codes) < n:
        progressed = False
        for lab in labels:
            members = by_label.get(lab) or []
            if idx < len(members):
                c = members[idx]
                if c not in codes:
                    codes.append(c)
                    progressed = True
                if len(codes) >= n:
                    break
        if not progressed:
            break
        idx += 1
    return codes


def cluster_score_audit_sample(
    *,
    limit: int = 8,
    offset: Optional[int] = None,
    rotate: bool = False,
) -> Dict[str, Any]:
    """对照审计：优先分池簿样本，按组轮询取票，算 score_global vs score_cluster。"""
    from core.signal.cluster_live import (
        get_cluster_scoring_cfg,
        load_active_cluster_book,
        load_active_cluster_weights,
    )

    active = load_active_cluster_weights()
    cs = get_cluster_scoring_cfg()
    mode = cs.get("mode") or "off"
    if not active or mode not in ("shadow", "active"):
        return {
            "success": True,
            "task": "cluster_score_audit",
            "rows": [],
            "mode": mode,
            "note": "非 shadow/active 或无 active 映射，跳过双分样本",
        }
    cmap = active.get("code_map") or {}
    n = max(1, min(int(limit), 12))
    if offset is None and rotate:
        offset = int(datetime.now(timezone.utc).timestamp() * 1000) % 10_000_000
    elif offset is None:
        offset = 0
    book = load_active_cluster_book() or {}
    codes = pick_audit_codes(
        cmap,
        list(book.get("book") or []),
        n,
        offset=int(offset),
    )
    rows: List[Dict[str, Any]] = []
    fails: List[str] = []
    try:
        from core.signal.service import get_default_signal_service

        svc = get_default_signal_service()
    except Exception as exc:
        return {
            "success": False,
            "task": "cluster_score_audit",
            "error": str(exc),
            "rows": [],
        }
    for code in codes:
        try:
            scored = svc.score_one(
                code,
                cluster_mode="shadow",
                skip_fundamentals=True,
            )
            result = scored.as_dict()
        except Exception as exc:
            fails.append(f"{code}:{exc}")
            continue
        if not isinstance(result, dict) or not result.get("success"):
            fails.append(
                f"{code}:{result.get('error') if isinstance(result, dict) else 'fail'}"
            )
            continue
        item = result.get("signal_item") or {}
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "stock_code": code,
                "stock_name": item.get("stock_name")
                or result.get("stock_name")
                or scored.stock_code
                or code,
                "production_ok": scored.production_ok,
                "gate_reason": scored.gate_reason,
                "scale": scored.scale,
                "cluster_label": item.get("cluster_label")
                or (cmap.get(code) or {}).get("cluster_label"),
                "score_global": item.get("score_global"),
                "score_cluster": item.get("score_cluster"),
                "delta_vs_global": item.get("delta_vs_global"),
                "weight_source": item.get("weight_source"),
                "score": item.get("score"),
            }
        )
    out: Dict[str, Any] = {
        "success": True,
        "task": "cluster_score_audit",
        "mode": mode,
        "version": active.get("version"),
        "rows": rows,
        "sample_offset": int(offset),
        "sample_codes": list(codes),
        "sampled_at": now_iso_utc(),
        "signal_config_touched": False,
    }
    if not rows and fails:
        out["success"] = False
        out["error"] = f"打分失败 {len(fails)} 只：" + "；".join(fails[:3])
    elif fails:
        out["note"] = f"部分失败 {len(fails)}/{len(codes)}"
    elif rotate or int(offset) != 0:
        out["note"] = f"轮换样本 offset={int(offset)}"
    return out
