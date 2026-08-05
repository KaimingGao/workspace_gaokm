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
    disc = (ss.get("discipline") or {}) if isinstance(ss, dict) else {}
    warnings = list(disc.get("warnings") or [])
    status = "ok"

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

    return {
        "ok": True,
        "kind": "data_quality_center",
        "status": status,
        "track": "D0-D4+X+B0",
        "bars_coverage": coverage,
        "fundamentals_history": {
            **fund,
            "ann_missing_top": ann_missing_top,
        },
        "factor_health": factor_health,
        "sample_discipline": disc,
        "source_audit": audit,
        "calendar": cal,
        "warnings": warnings[:16],
        "note": (
            "数据质量中心：覆盖率/财务多期/ann_missing/因子健康/源审计/日历；"
            "B 轨回归准确性见 beta-regression-strengthen；运营项见 maturity-gate。"
        ),
    }
