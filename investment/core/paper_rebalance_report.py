"""调仓模拟收尾：风控建议、运维报告、现金影响与返回载荷。

由 ``simulate_cross_section_rebalance`` 在卖/买腿完成后调用；副作用写入 ``paper``。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.paper import _now_iso, append_operation_log, build_ops_report
from core.paper_costs import resolve_cost_model
from core.paper_rebalance_turnover import (
    build_rebalance_cash_impact,
    compute_turnover_stats,
)

logger = logging.getLogger(__name__)


def finalize_cross_section_rebalance_report(
    paper: dict,
    *,
    ranking: Optional[List[dict]],
    holdings: list,
    cash: float,
    top_codes: set,
    top_k: int,
    min_score: float,
    min_hold_score: float,
    max_positions: int,
    buys_blocked: bool,
    risk_gate: dict,
    sell_trades: list,
    buy_trades: list,
    sentiment_restore_trades: list,
    turnover_capped: bool,
    turnover_skipped: list,
    max_turnover_pct: Optional[float],
    sell_match_skips: list,
    cash_before: float,
    position_count_before: int,
    equity_before: Optional[float],
    fee_params: dict,
    rules: Optional[dict],
    risk_budget_skips: list,
    skip_market_prior: bool,
    sentiment_prior_summary: Any,
    event_prior_soft_holds: list,
    respect_max_positions: bool = True,
    tau_floor_meta: Optional[dict] = None,
) -> Dict[str, Any]:
    """组装调仓结果并写回 paper 运维字段。"""
    _tau_floor_meta = tau_floor_meta or {}

    # 风控拦截时仍给出目标权重建议
    if buys_blocked and not paper.get("last_optimize"):
        try:
            from core.portfolio_optimize import optimize_weights
            from core.strategy import get_strategy_spec

            sid = paper.get("strategy_id") or "short"
            rr = (get_strategy_spec(str(sid)).get("risk") or {})
            paper["last_optimize"] = optimize_weights(
                list(ranking or []),
                max_position_pct=float(rr.get("max_position_pct") or 25.0),
                max_sector_pct=float(rr.get("max_sector_pct") or 40.0),
                max_positions=int(rr.get("max_positions") or max_positions),
                min_score=float(min_score),
            )
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
            paper["last_optimize"] = None

    paper["holdings"] = holdings
    paper["cash"] = round(cash, 2)
    paper["updated_at"] = _now_iso()

    if turnover_capped and turnover_skipped:
        append_operation_log(
            paper,
            "turnover_cap",
            detail=(
                f"换手软上限 {max_turnover_pct}% 跳过买入 "
                + "、".join(turnover_skipped[:8])
                + (f" 等{len(turnover_skipped)}只" if len(turnover_skipped) > 8 else "")
            ),
            meta={
                "max_turnover_pct": max_turnover_pct,
                "skipped_codes": turnover_skipped,
                "path": "cluster" if not respect_max_positions else "cross_section",
            },
        )

    if sell_match_skips:
        append_operation_log(
            paper,
            "sell_match_skips",
            detail=(
                "卖出侧涨跌停/停牌跳过 "
                + "、".join(s.get("stock_code", "?") for s in sell_match_skips[:8])
                + (f" 等{len(sell_match_skips)}只" if len(sell_match_skips) > 8 else "")
            ),
            meta={
                "skips": sell_match_skips,
                "path": "sell_match",
            },
        )

    dq = None
    try:
        from core.data_service import summarize_data_quality

        codes = [str(c) for c in sorted(top_codes) if c][:12]
        if codes:
            raw_dq = summarize_data_quality(codes, limit=40)
            dq = {
                "levels": raw_dq.get("levels"),
                "fallback_count": raw_dq.get("fallback_count"),
                "gated_count": raw_dq.get("gated_count"),
                "count": raw_dq.get("count"),
                "adjust_policy": raw_dq.get("adjust_policy"),
            }
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
        dq = None
    # 使用模块顶层 resolve_cost_model；勿在函数内再 import 同名，否则整函数变 local 未绑定
    cost_model = resolve_cost_model(paper)

    risk_blocks = (risk_gate or {}).get("blocks") or []
    monitor_alerts: list = []
    health: dict = {}
    summary: Dict[str, Any] = {}
    try:
        from core.paper import mark_to_market
        from core.strategy_monitor import assess_strategy_health

        summary = mark_to_market(paper)
        codes = [
            str(h.get("stock_code") or "").strip()
            for h in (paper.get("holdings") or [])
            if str(h.get("stock_code") or "").strip()
        ]
        for r in ranking or []:
            c = str(r.get("stock_code") or "").strip()
            if c and c not in codes:
                codes.append(c)
        health = assess_strategy_health(
            paper,
            summary=summary,
            compute_rolling_ic=True,
            codes=codes[:12],
        )
        monitor_alerts = list(health.get("alerts") or [])
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
        monitor_alerts = []
        health = {}
    metrics = health.get("metrics") if isinstance(health, dict) else None
    north_star = None
    source_audit = None
    try:
        from core.north_star import merge_north_star_into_metrics

        metrics, north_star = merge_north_star_into_metrics(paper, metrics)
        paper["last_north_star"] = north_star
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
        pass
    try:
        from core.data_consistency import audit_code_sources

        codes_audit = [
            str(h.get("stock_code") or "").strip()
            for h in (paper.get("holdings") or [])
            if str(h.get("stock_code") or "").strip()
        ]
        source_audit = audit_code_sources(codes_audit)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
        source_audit = None
    if not summary:
        try:
            from core.paper import mark_to_market as _mtm1

            summary = _mtm1(paper) or {}
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
            summary = {}

    attribution: Dict[str, Any] = {}
    try:
        from core.paper_attribution import build_paper_attribution_lite

        attribution = build_paper_attribution_lite(paper, summary) or {}
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
        attribution = {"ok": False, "reason": "attribution_error"}

    ops_report = build_ops_report(
        strategy_id=paper.get("strategy_id"),
        strategy_version=paper.get("strategy_version"),
        cost_model=cost_model,
        data_quality=dq,
        risk_blocks=risk_blocks,
        monitor_alerts=monitor_alerts,
        buys_blocked=buys_blocked,
        optimize=paper.get("last_optimize"),
        risk_limits=(risk_gate or {}).get("limits"),
        monitor_metrics=metrics,
        north_star=north_star,
        source_audit=source_audit,
        exposure=(risk_gate or {}).get("exposure"),
        risk_block_items=(risk_gate or {}).get("block_items"),
        attribution=attribution,
    )
    paper["last_ops_report"] = ops_report

    cash_impact = build_rebalance_cash_impact(
        cash_before=cash_before,
        position_count_before=position_count_before,
        sell_trades=sell_trades,
        buy_trades=buy_trades,
        summary=summary,
        equity_before=equity_before,
        cost_model=cost_model,
        max_turnover_pct=max_turnover_pct,
        turnover_capped=turnover_capped,
    )
    turnover = compute_turnover_stats(
        sell_trades,
        buy_trades,
        equity_before=equity_before,
        max_turnover_pct=max_turnover_pct,
    )

    from core.signal.score_display import json_safe_number

    cost_assumptions = {
        "cost_model": cost_model,
        "fee_params": {
            k: fee_params.get(k)
            for k in ("commission_rate", "min_commission", "stamp_duty_rate", "slippage_bps", "max_slippage_bps")
            if isinstance(fee_params, dict) and k in fee_params
        }
        if isinstance(fee_params, dict)
        else {},
        "turnover_pct": turnover.get("turnover_pct"),
        "max_turnover_pct": max_turnover_pct,
        "weight_mode": (paper.get("last_optimize") or {}).get("weight_mode")
        or (rules.get("weight_mode") if isinstance(rules, dict) else None)
        or "score_budget",
        "note": "分池/横截面调仓成本假设；与回测页对照见 fit-gap",
    }

    # Y3.1：持仓风格/规模暴露简表
    exposure_style = None
    try:
        from core.risk.exposure import build_exposure_matrix

        exposure_style = build_exposure_matrix(paper, summary)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
        exposure_style = None

    try:
        from core.signal.dual_score import get_dual_score_cfg

        _cfg_dual = get_dual_score_cfg()
        _dual_meta = {
            "fusion_mode": _cfg_dual.get("fusion_mode"),
            "min_predicted_score_tau": _cfg_dual.get("min_predicted_score_tau"),
            "tau_gate": _tau_floor_meta or None,
            "note": (
                "排序=ŷ_trade（raw）；买入门槛=ŷ_EOD≥min 且 ŷ_τ≥floor；校准 g 仅 tip 对照"
                + (
                    f"；{_tau_floor_meta.get('note')}"
                    if _tau_floor_meta.get("note")
                    else ""
                )
            ),
        }
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
        _dual_meta = {"fusion_mode": None, "note": "dual_score unavailable"}

    empty_reason = None
    if not buy_trades and not sell_trades:
        floor_skips = [
            s
            for s in (risk_budget_skips or [])
            if str(s.get("reason") or "")
            in ("below_eod_floor", "oos_failed_no_buy")
            or "floor" in str(s.get("reason") or "").lower()
            or "门槛" in str(s.get("reason") or "")
        ]
        if not ranking and not top_codes:
            empty_reason = "empty_ranking"
        elif floor_skips and not buy_trades:
            empty_reason = "all_below_eod_floor"
        elif buys_blocked:
            empty_reason = "buys_blocked"
        elif risk_blocks:
            empty_reason = "risk_blocked"
        else:
            empty_reason = "no_executable_changes"
        if empty_reason:
            warns = list(risk_gate.get("warnings") or [])
            msg = (
                f"无可执行变动（{empty_reason}）；"
                f"min_predicted_score={min_score}；"
                f"floor_skips={len(floor_skips)} / ranking={len(ranking or [])}"
            )
            if msg not in warns:
                warns.append(msg)
            risk_gate["warnings"] = warns

    market_prior_skips = [
        s for s in (risk_budget_skips or []) if isinstance(s, dict) and s.get("market_prior")
    ]
    if isinstance(risk_gate, dict):
        risk_gate["market_prior"] = {
            "skipped": bool(skip_market_prior),
            "blocks": len(market_prior_skips),
            "warnings": list(
                dict.fromkeys(
                    str(s.get("reason") or s.get("stock_code") or "")
                    for s in market_prior_skips
                    if s.get("reason") or s.get("stock_code")
                )
            )[:8],
        }

    market_context_summary: Dict[str, Any] = {}
    try:
        from core.market_context import summarize_market_context

        market_context_summary = summarize_market_context()
    except Exception:
        logger.debug("rebalance market_context summary skipped", exc_info=True)

    return {
        "success": True,
        "top_k": top_k,
        "target_codes": sorted(top_codes),
        "min_score": json_safe_number(min_score),
        "min_hold_score": json_safe_number(min_hold_score),
        "empty_reason": empty_reason,
        "sell_trades": sell_trades,
        "buy_trades": buy_trades,
        "sentiment_restore_trades": sentiment_restore_trades,
        "buys_blocked": buys_blocked,
        "risk_gate": risk_gate,
        "risk_blocks": risk_blocks,
        "data_quality": dq or {},
        "cost_model": cost_model,
        "ops_report": ops_report,
        "last_optimize": paper.get("last_optimize"),
        "target_weights": (paper.get("last_optimize") or {}).get("weights_pct"),
        "cash_impact": cash_impact,
        "turnover": turnover,
        "turnover_capped": turnover_capped,
        "turnover_skipped": turnover_skipped,
        "max_turnover_pct": max_turnover_pct,
        "risk_budget_skips": risk_budget_skips,
        "sentiment_prior": sentiment_prior_summary,
        "market_context": market_context_summary,
        "event_prior": {
            "soft_holds": event_prior_soft_holds,
            "count": len(event_prior_soft_holds),
            "note": "主题开盘缺口日：低 ŷ 卖出改为 soft hold（不改 ŷ_EOD）",
        },
        "dual_score": _dual_meta,
        "attribution": attribution,
        "cost_assumptions": cost_assumptions,
        "exposure_style": exposure_style,
        "note": "横截面/分池调仓为纸面模拟；排序=ŷ_trade（raw）；买入=EOD门槛且 τ 闸（均 raw）；卖出=ŷ_trade 低于 min_hold；校准 g 仅 tip。",
    }
