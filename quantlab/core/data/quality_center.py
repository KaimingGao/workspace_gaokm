"""D4 · 数据质量中心：聚合 coverage / 财务样本 / 源审计 / 日历状态。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Sequence


def build_data_quality_report(
    *,
    codes: Optional[Sequence[str]] = None,
    lookback: int = 40,
    include_source_audit: bool = True,
) -> Dict[str, Any]:
    """一页 DQ 快照（只读）。"""
    from core.data.coverage import build_data_coverage
    from core.market.calendar import calendar_status
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
            logger.exception('unexpected error in build_data_quality_report')
            coverage = {"ok": False, "error": str(e)}
    except Exception as e:
        logger.exception('unexpected error in build_data_quality_report')
        coverage = {"ok": False, "error": str(e)}

    fund = {}
    try:
        fund = fundamentals_history_coverage() or {}
    except Exception as e:
        logger.exception('unexpected error in build_data_quality_report')
        fund = {"error": str(e)}

    ss = {}
    try:
        ss = sample_status(paper=None) or {}
    except Exception as e:
        logger.exception('unexpected error in build_data_quality_report')
        ss = {"error": str(e)}

    audit = None
    if include_source_audit:
        try:
            from core.data.consistency import audit_code_sources
            from core.data.coverage import universe_codes

            audit_codes = code_list or list(universe_codes() or [])[:20]
            audit = audit_code_sources(audit_codes, lookback=lookback)
        except Exception as e:
            logger.exception('unexpected error in build_data_quality_report')
            audit = {"ok": False, "error": str(e)}

    cal = calendar_status()
    hygiene = None
    try:
        from core.validation_universe import build_validation_hygiene_report

        hygiene = build_validation_hygiene_report(codes=code_list or None)
    except Exception as e:
        logger.exception('unexpected error in build_data_quality_report')
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
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        ann_missing_top = []
    if fund.get("ann_missing_code_ratio") is not None:
        try:
            from core.data.policy import (
                ANN_MISSING_CODE_RATIO_BLOCK,
                ANN_MISSING_CODE_RATIO_WARN,
            )
            from core.signal.config import load_signal_config

            fund_cfg = (load_signal_config().get("fundamentals") or {})
            warn_th = float(
                fund_cfg.get("ann_missing_code_ratio_warn")
                or ANN_MISSING_CODE_RATIO_WARN
            )
            block_th = float(
                fund_cfg.get("ann_missing_code_ratio_block")
                or ANN_MISSING_CODE_RATIO_BLOCK
            )
            ratio = float(fund["ann_missing_code_ratio"])
            if ratio > block_th:
                status = "bad"
                warnings.append(
                    f"ann_missing 码占比={ratio} > {block_th} "
                    f"（{fund.get('ann_missing_codes')} 只）· DQ=bad · 补公告日"
                )
            elif ratio > warn_th:
                status = "warn" if status == "ok" else status
                warnings.append(
                    f"ann_missing 码占比={ratio} "
                    f"（{fund.get('ann_missing_codes')} 只）· 补公告日以免前视"
                )
        except (TypeError, ValueError):
            pass

    factor_health = None
    try:
        from core.signal.factors.meta.health import assess_factor_health

        factor_health = assess_factor_health()
        if factor_health.get("blockers"):
            status = "warn" if status == "ok" else status
            warnings.extend(list(factor_health.get("blockers") or [])[:4])
    except Exception as e:
        logger.exception('unexpected error in build_data_quality_report')
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
        logger.exception('unexpected error in build_data_quality_report')
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
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        outcome_nudge = None

    io_stats: Dict[str, Any] = {}
    try:
        from core.store import io_error_stats

        io_stats = io_error_stats()
        if int(io_stats.get("io_error_count") or 0) > 0:
            status = "warn" if status == "ok" else status
            warnings.append(
                f"store IO 错误累计 {io_stats['io_error_count']} 次（见日志 store_io_error）"
            )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        io_stats = {}

    sector_cov = None
    try:
        from core.data.coverage import universe_codes
        from core.sector_map_sync import coverage_report

        sector_cov = coverage_report(list(universe_codes() or [])[:80])
        cov_r = (
            float(sector_cov["coverage"])
            if sector_cov.get("coverage") is not None
            else 1.0
        )
        board_n = int(sector_cov.get("board_labeled") or 0)
        if board_n > 0:
            status = "warn" if status == "ok" else status
            warnings.append(
                f"sector_map 仍有 {board_n} 只板别伪主题 · 运行 scrub_board_labels"
            )
        if cov_r < 0.5 and int(sector_cov.get("total") or 0) >= 5:
            status = "warn" if status == "ok" else status
            warnings.append(
                f"真实行业覆盖 {sector_cov.get('mapped')}/{sector_cov.get('total')} "
                f"（{cov_r:.0%}）· 补 sector_map 主题"
            )
    except Exception as e:
        logger.exception('unexpected error in build_data_quality_report')
        sector_cov = {"ok": False, "error": str(e)}

    ds_metrics: Dict[str, Any] = {}
    try:
        from core.data.service import metrics_snapshot

        ds_metrics = metrics_snapshot()
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        ds_metrics = {}

    ss_metrics: Dict[str, Any] = {}
    try:
        from core.signal.service import metrics_snapshot as signal_metrics_snapshot

        ss_metrics = signal_metrics_snapshot()
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        ss_metrics = {}

    return {
        "ok": True,
        "kind": "data_quality_center",
        "status": status,
        "track": "D0-D4+X+B0+DC/FM/RK+DS-R+E+SS-E",
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
        "store_io": io_stats,
        "sector_coverage": sector_cov,
        "data_service_metrics": ds_metrics,
        "signal_service_metrics": ss_metrics,
        "warnings": warnings[:16],
        "note": (
            "数据质量中心：覆盖率/财务多期/ann_missing/因子健康/源审计/日历；"
            "DS-R/E 缓存锁/行业未分类/ann_missing 门禁/读口封装；"
            "SS-E 打分信封/metrics；运营项见 maturity-gate。"
        ),
    }
