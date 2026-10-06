"""纸面组合健康度：暴露 · 过程跳过 · α 衰减告警（研究台，非实盘）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build_portfolio_health(
    *,
    paper: Optional[dict] = None,
    book: Optional[List[dict]] = None,
    book_constraints: Optional[dict] = None,
    book_skips: Optional[List[dict]] = None,
    risk_budget_skips: Optional[List[dict]] = None,
    rolling_ic: Optional[dict] = None,
) -> Dict[str, Any]:
    """聚合一页健康 KPI。

    ``alerts``：可展示告警；``ok`` 仅表示无硬阻断级告警（衰减等仍可能 warning）。
    """
    from core.risk.checks import check_account_risk
    from core.signal.book_constraints import resolve_book_risk_limits

    pap = paper
    if pap is None:
        try:
            from core.paper import load_paper

            pap = load_paper()
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in portfolio_health.py", exc_info=True)
            pap = {}
    limits = resolve_book_risk_limits(paper=pap if isinstance(pap, dict) else None)
    risk = check_account_risk(pap if isinstance(pap, dict) else {})
    exposure = (risk.get("exposure") or {}) if isinstance(risk, dict) else {}

    holdings = list((pap or {}).get("holdings") or []) if isinstance(pap, dict) else []
    book_rows = list(book or [])
    skips = list(book_skips or [])
    rb_skips = list(risk_budget_skips or [])

    # 簿/持仓行业集中
    from collections import Counter

    def _sector_hhi(rows: List[dict], key: str = "sector") -> Optional[float]:
        secs = [str(r.get(key) or r.get("sector") or "其他") for r in rows if r]
        if not secs:
            return None
        n = len(secs)
        c = Counter(secs)
        return round(sum((v / n) ** 2 for v in c.values()), 4)

    book_sector_counts = Counter(
        str(r.get("sector") or "其他") for r in book_rows if r.get("stock_code")
    )
    top_sector = None
    top_sector_pct = None
    if book_rows:
        sec, cnt = book_sector_counts.most_common(1)[0]
        top_sector, top_sector_pct = sec, round(100.0 * cnt / len(book_rows), 2)

    skip_by_stage: Dict[str, int] = {}
    for s in skips:
        st = str(s.get("skip_stage") or "other")
        skip_by_stage[st] = skip_by_stage.get(st, 0) + 1

    tau_fail_in_book = sum(1 for r in book_rows if r.get("tau_gate_fail"))
    alerts: List[Dict[str, Any]] = []
    max_sec = float(limits.get("max_sector_pct") or 40.0)
    if top_sector_pct is not None and top_sector_pct > max_sec + 1e-6:
        alerts.append(
            {
                "level": "warning",
                "code": "book_sector_over",
                "message": f"簿内行业 {top_sector} 占比 {top_sector_pct}% > 上限 {max_sec:g}%",
            }
        )

    # α 衰减：滚动 IC 显著为负
    ic_val = None
    if isinstance(rolling_ic, dict):
        ic_val = _f(rolling_ic.get("ic")) or _f(rolling_ic.get("mean_ic"))
        if ic_val is not None and ic_val < -0.02:
            alerts.append(
                {
                    "level": "warning",
                    "code": "alpha_decay",
                    "message": f"滚动 IC={ic_val:.3f} 偏低，考虑降 shadow / 暂缓 promote",
                }
            )

    bc = book_constraints if isinstance(book_constraints, dict) else {}
    hard = [a for a in alerts if a.get("level") == "error"]
    return {
        "success": True,
        "ok": len(hard) == 0,
        "limits": limits,
        "holdings_count": len(holdings),
        "book_count": len(book_rows),
        "exposure": {
            "over_limit": exposure.get("over_limit"),
            "name_top": (exposure.get("by_name") or [])[:5]
            if isinstance(exposure.get("by_name"), list)
            else exposure.get("by_name"),
            "sector_top": (exposure.get("by_sector") or [])[:5]
            if isinstance(exposure.get("by_sector"), list)
            else exposure.get("by_sector"),
            "book_hhi": _sector_hhi(book_rows),
            "book_top_sector": top_sector,
            "book_top_sector_pct": top_sector_pct,
        },
        "process": {
            "book_skips_count": len(skips),
            "book_skips_by_stage": skip_by_stage,
            "risk_budget_skips_count": len(rb_skips),
            "tau_fail_in_book": tau_fail_in_book,
            "constraint_stats": {
                k: bc.get(k)
                for k in (
                    "untradeable",
                    "tau_fail_deferred",
                    "tau_fail_excluded",
                    "sector_cap",
                    "filled",
                    "per_sector_cap",
                )
                if k in bc
            },
        },
        "alpha": {
            "rolling_ic": ic_val,
            "decay_alert": any(a.get("code") == "alpha_decay" for a in alerts),
        },
        "risk_check": {
            "ok": risk.get("ok") if isinstance(risk, dict) else None,
            "blocks": (risk.get("blocks") or [])[:5] if isinstance(risk, dict) else [],
        },
        "alerts": alerts,
        "note": "纸面组合健康度 · 约束/暴露/衰减告警；非实盘执行监控",
    }
