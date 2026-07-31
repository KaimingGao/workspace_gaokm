"""live / 回测数据源一致性审计（R1.4）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


def audit_code_sources(
    codes: Sequence[str],
    *,
    lookback: int = 40,
) -> Dict[str, Any]:
    """对比观察池/持仓的日线质量与 fallback，供五问 / 回测报告。"""
    from core.data_service import get_bars, summarize_data_quality

    code_list = [str(c).strip() for c in (codes or []) if str(c).strip()]
    code_list = code_list[:20]
    if not code_list:
        return {
            "ok": True,
            "status": "empty",
            "codes": [],
            "fallback_codes": [],
            "thin_codes": [],
            "mismatch_hint": None,
            "note": "无标的可审计。",
        }

    fallback_codes: List[str] = []
    thin_codes: List[str] = []
    empty_codes: List[str] = []
    details: List[Dict[str, Any]] = []

    for code in code_list:
        try:
            pack = get_bars(code, limit=lookback)
        except Exception as e:
            empty_codes.append(code)
            details.append({"stock_code": code, "error": str(e), "level": "empty"})
            continue
        src = str(pack.get("data_source") or "")
        q = pack.get("quality") or {}
        level = str(q.get("level") or "empty")
        fallback = "fallback" in src.lower() or bool(pack.get("fallback"))
        row = {
            "stock_code": code,
            "data_source": src,
            "quality_level": level,
            "fallback": fallback,
            "bar_count": len(pack.get("bars") or []),
        }
        details.append(row)
        if fallback:
            fallback_codes.append(code)
        if level == "thin":
            thin_codes.append(code)
        if level == "empty" or not pack.get("bars"):
            empty_codes.append(code)

    dq = {}
    try:
        dq = summarize_data_quality(code_list, limit=lookback) or {}
    except Exception:
        dq = {}

    status = "ok"
    if fallback_codes or empty_codes:
        status = "warn"
    if len(empty_codes) >= max(1, len(code_list) // 2):
        status = "bad"

    hint = None
    if fallback_codes:
        hint = (
            f"{len(fallback_codes)} 只可能为 quote_fallback，"
            "live 与完整日线回测源可能不一致"
        )

    return {
        "ok": status != "bad",
        "status": status,
        "codes": code_list,
        "fallback_count": len(fallback_codes),
        "fallback_codes": fallback_codes[:12],
        "thin_codes": thin_codes[:12],
        "empty_codes": empty_codes[:12],
        "details": details[:20],
        "data_quality": {
            "levels": dq.get("levels"),
            "fallback_count": dq.get("fallback_count"),
            "gated_count": dq.get("gated_count"),
            "adjust_policy": dq.get("adjust_policy"),
        },
        "mismatch_hint": hint,
        "note": "R1.4 源一致性审计；非实盘核对。",
    }


def attach_source_audit(
    result: Dict[str, Any],
    codes: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """把 source_audit 挂到回测/调仓结果。"""
    out = dict(result or {})
    code_list = list(codes or out.get("loaded_stocks") or [])
    if not code_list:
        return out
    try:
        out["source_audit"] = audit_code_sources(code_list)
    except Exception as e:
        out["source_audit"] = {"ok": False, "error": str(e)}
    return out
