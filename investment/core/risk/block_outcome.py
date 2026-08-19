"""risk_block 人工标注 outcome（R3.2 / 运营收尾）：供有效率计算。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Sequence

VALID_OUTCOMES = frozenset(
    {
        "true_positive",
        "false_positive",
        "effective",
        "false_block",
        "tp",
        "fp",
        "true",
        "false",
        "unknown",
        "clear",
    }
)


def _norm_outcome(raw: str) -> Optional[str]:
    s = str(raw or "").strip().lower()
    if not s or s == "clear":
        return None
    if s in ("tp", "true", "effective"):
        return "true_positive"
    if s in ("fp", "false", "false_block"):
        return "false_positive"
    if s in ("true_positive", "false_positive", "unknown"):
        return s
    return None


def list_risk_blocks(
    operation_log: Sequence[dict],
    *,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """列出最近 risk_block，附 index（相对全文 operation_log）。"""
    logs = list(operation_log or [])
    out: List[Dict[str, Any]] = []
    for i in range(len(logs) - 1, -1, -1):
        e = logs[i] or {}
        if e.get("type") != "risk_block":
            continue
        meta = dict(e.get("meta") or {})
        out.append(
            {
                "index": i,
                "ts": e.get("ts"),
                "detail": e.get("detail"),
                "codes": meta.get("codes") or meta.get("block_codes"),
                "outcome": meta.get("outcome") or meta.get("label"),
                "meta": meta,
            }
        )
        if len(out) >= max(1, int(limit or 50)):
            break
    return out


def annotate_risk_block(
    paper: dict,
    *,
    index: Optional[int] = None,
    ts: Optional[str] = None,
    outcome: str,
    note: str = "",
) -> Dict[str, Any]:
    """
    给一条 risk_block 写 meta.outcome。
    定位：优先 index；否则匹配 ts。
    outcome=clear 清除标注。
    """
    logs = paper.setdefault("operation_log", [])
    target_i: Optional[int] = None
    if index is not None:
        i = int(index)
        if 0 <= i < len(logs) and (logs[i] or {}).get("type") == "risk_block":
            target_i = i
    elif ts:
        for i, e in enumerate(logs):
            if (e or {}).get("type") == "risk_block" and str((e or {}).get("ts") or "") == str(ts):
                target_i = i
                break
    if target_i is None:
        return {"ok": False, "success": False, "error": "未找到 risk_block"}

    raw = str(outcome or "").strip().lower()
    entry = dict(logs[target_i])
    meta = dict(entry.get("meta") or {})
    if raw in ("clear", "none", ""):
        meta.pop("outcome", None)
        meta.pop("label", None)
        meta.pop("outcome_note", None)
        meta.pop("outcome_at", None)
        normalized = None
    else:
        normalized = _norm_outcome(raw)
        if normalized is None:
            return {
                "ok": False,
                "success": False,
                "error": f"无效 outcome；允许 true_positive|false_positive|unknown|clear",
            }
        meta["outcome"] = normalized
        if note:
            meta["outcome_note"] = str(note)[:200]
        try:
            from core.paper import _now_iso

            meta["outcome_at"] = _now_iso()
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in block_outcome.py", exc_info=True)
            pass
    entry["meta"] = meta
    logs[target_i] = entry
    paper["operation_log"] = logs[-200:]
    return {
        "ok": True,
        "success": True,
        "index": target_i,
        "outcome": normalized,
        "entry": {
            "ts": entry.get("ts"),
            "detail": entry.get("detail"),
            "outcome": normalized,
        },
        "note": "已标注；复算北极星 risk_blocks 有效率请 refresh /api/north-star",
    }


def unlabeled_digest(
    operation_log: Sequence[dict],
    *,
    limit: int = 20,
) -> Dict[str, Any]:
    """E4 · 未标注 risk_block 摘要，供 sample-status / 日更催办。"""
    blocks = list_risk_blocks(operation_log, limit=200)
    unlabeled = [b for b in blocks if not b.get("outcome")]
    items: List[Dict[str, Any]] = []
    for b in unlabeled[: max(1, int(limit or 20))]:
        meta = b.get("meta") or {}
        codes = b.get("codes") or meta.get("codes") or meta.get("block_codes") or []
        if isinstance(codes, str):
            codes = [codes]
        reason = (
            meta.get("reason_code")
            or meta.get("block_reason")
            or meta.get("reason")
            or (b.get("detail") or "")[:80]
        )
        items.append(
            {
                "index": b.get("index"),
                "ts": b.get("ts"),
                "codes": list(codes)[:8] if isinstance(codes, (list, tuple)) else [],
                "reason": reason,
                "detail": (b.get("detail") or "")[:120],
            }
        )
    return {
        "ok": True,
        "unlabeled_count": len(unlabeled),
        "block_count": len(blocks),
        "items": items,
        "note": (
            f"待标注 {len(unlabeled)}/{len(blocks)}；"
            "策略中心拦截流水点真拦/误拦后有效率可算。"
            if blocks
            else "无 risk_block 流水。"
        ),
    }
