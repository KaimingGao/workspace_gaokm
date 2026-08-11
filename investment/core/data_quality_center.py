"""D4 · 数据质量中心：聚合 coverage / 财务样本 / 源审计 / 日历状态。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


def build_data_quality_report(
    *,
    codes: Optional[Sequence[str]] = None,
    lookback: int = 40,
    include_source_audit: bool = True,
) -> Dict[str, Any]:
    """一页 DQ 快照（只读）。"""
    from core.data_coverage import build_data_coverage
    from core.market_calendar import calendar_status
    from core.sample_ops import fundamentals_history_coverage, sample_status

    code_list: List[str] = [str(c).strip() for c in (codes or []) if str(c).strip()]
    coverage: Dict[str, Any] = {}
    try:
        coverage = build_data_coverage(codes=code_list or None) or {}
    except TypeError:
        # 兼容无 codes 参数的旧签名
        try:
            coverage = build_data_coverage() or {}
        except Exception as e:
            coverage = {"ok": False, "error": str(e)}
    except Exception as e:
        coverage = {"ok": False, "error": str(e)}

    fund = {}
    try:
        fund = fundamentals_history_coverage() or {}
    except Exception as e:
        fund = {"error": str(e)}

    ss = {}
    try:
        ss = sample_status(paper=None) or {}
    except Exception as e:
        ss = {"error": str(e)}

    audit = None
    if include_source_audit:
        try:
            from core.data_consistency import audit_code_sources
            from core.data_coverage import universe_codes

            audit_codes = code_list or list(universe_codes() or [])[:20]
            audit = audit_code_sources(audit_codes, lookback=lookback)
        except Exception as e:
            audit = {"ok": False, "error": str(e)}

    cal = calendar_status()
    hygiene = None
    try:
        from core.validation_universe import build_validation_hygiene_report

        hygiene = build_validation_hygiene_report(codes=code_list or None)
    except Exception as e:
        hygiene = {"ok": False, "error": str(e)}

    disc = (ss.get("discipline") or {}) if isinstance(ss, dict) else {}
    warnings = list(disc.get("warnings") or [])
    status = "ok"

    if isinstance(hygiene, dict) and hygiene.get("status") in ("warn", "fail"):
        status = hygiene.get("status") or status
        for a in hygiene.get("actions") or []:
            warnings.append(str(a))

    if fund.get("real_multi_coverage") is not None:
        try:
            if float(fund["real_multi_coverage"]) < 0.5:
                status = "warn"
                warnings.append(
                    f"real_multi_coverage={fund['real_multi_coverage']} < 0.5 · "
                    + str(fund.get("ingest_hint") or "请 ingest-history")
                )
        except (TypeError, ValueError):
            pass
    ann_missing_top = []
    try:
        from core.research.beta_accuracy import ann_missing_top_codes

        ann_missing_top = ann_missing_top_codes(fund, limit=15)
    except Exception:
        ann_missing_top = []
    if fund.get("ann_missing_code_ratio") is not None:
        try:
            if float(fund["ann_missing_code_ratio"]) > 0.3:
                status = "warn" if status == "ok" else status
                warnings.append(
                    f"ann_missing 码占比={fund['ann_missing_code_ratio']} "
                    f"（{fund.get('ann_missing_codes')} 只）· 补公告日以免前视"
                )
        except (TypeError, ValueError):
            pass

    factor_health = None
    try:
        from core.signal.factor_health import assess_factor_health

        factor_health = assess_factor_health()
        if factor_health.get("blockers"):
            status = "warn" if status == "ok" else status
            warnings.extend(list(factor_health.get("blockers") or [])[:4])
    except Exception as e:
        factor_health = {"ok": False, "error": str(e)}

    if audit and audit.get("status") in ("warn", "bad"):
        status = "bad" if audit.get("status") == "bad" else (status if status == "bad" else "warn")
    if warnings:
        status = "warn" if status == "ok" else status

    pit_depth = None
    ingest_nudge = None
    try:
        from core.pro_core import assess_pit_depth, ingest_nudge_payload

        fund_with_top = {**fund, "ann_missing_top": ann_missing_top}
        pit_depth = assess_pit_depth(
            sample_status=ss if isinstance(ss, dict) else {},
            fundamentals_history=fund_with_top,
        )
        if not pit_depth.get("soft_ok"):
            status = "warn" if status == "ok" else status
            warnings.extend(list(pit_depth.get("notes") or [])[:3])
        if not pit_depth.get("ok"):
            status = "bad" if status != "bad" else status
        ingest_nudge = ingest_nudge_payload(
            sample_status={
                **(ss if isinstance(ss, dict) else {}),
                "fundamentals_history": fund_with_top,
            }
        )
    except Exception as e:
        pit_depth = {"ok": False, "error": str(e)}
        ingest_nudge = None

    outcome_nudge = None
    try:
        from core.paper import load_paper
        from core.risk.block_outcome import unlabeled_digest

        paper = load_paper() or {}
        outcome_nudge = unlabeled_digest(paper.get("operation_log") or [])
        unlabeled_n = int((outcome_nudge or {}).get("unlabeled_count") or 0)
        if unlabeled_n > 0:
            status = "warn" if status == "ok" else status
            warnings.append(f"风险拦截未标注 outcome={unlabeled_n} 条 · 策略页催办")
    except Exception:
        outcome_nudge = None

    return {
        "ok": True,
        "kind": "data_quality_center",
        "status": status,
        "track": "D0-D4+X+B0+DC/FM/RK",
        "bars_coverage": coverage,
        "validation_hygiene": hygiene,
        "fundamentals_history": {
            **fund,
            "ann_missing_top": ann_missing_top,
        },
        "pit_depth": pit_depth,
        "ingest_nudge": ingest_nudge,
        "outcome_nudge": outcome_nudge,
        "factor_health": factor_health,
        "sample_discipline": disc,
        "source_audit": audit,
        "calendar": cal,
        "warnings": warnings[:16],
        "note": (
            "数据质量中心：覆盖率/财务多期/ann_missing/因子健康/源审计/日历；"
            "DC/FM/RK 见 pro-core-strengthen；运营项见 maturity-gate。"
        ),
    }
