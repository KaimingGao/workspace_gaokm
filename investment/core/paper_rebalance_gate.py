"""卖腿后的门禁桥：舆情摘要、回撤/软拦、目标权重。

由 ``simulate_cross_section_rebalance`` 在 ``run_sell_leg`` 与 ``run_buy_leg`` 之间调用；
原地更新 ``RebalanceState``，并返回 ``target_w``。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from core.paper import _now_iso, append_operation_log
from core.paper_rebalance_state import RebalanceState

logger = logging.getLogger(__name__)


def apply_post_sell_gate(state: RebalanceState) -> Dict[str, Any]:
    """应用卖后门禁与 optimize；写回 state，返回目标权重表。"""
    paper = state.paper
    ranking = state.ranking
    rules = state.rules
    kept = list(state.kept)
    risk_gate = state.risk_gate if isinstance(state.risk_gate, dict) else {"ok": True, "blocks": [], "warnings": []}
    risk_limits = dict(state.risk_limits or {})
    buys_blocked = bool(state.buys_blocked)
    prior_by_code = state.prior_by_code if isinstance(state.prior_by_code, dict) else {}
    prior_cfg_live = state.prior_cfg_live if isinstance(state.prior_cfg_live, dict) else {}
    sentiment_prior_summary = state.sentiment_prior_summary
    top_items = state.indexes.top_items
    respect_max_positions = bool(state.respect_max_positions)
    max_positions = int(state.max_positions)
    min_score = float(state.min_score)

    # 舆情先验预检摘要：复用开环批量结果（勿二次拉取）
    try:
        held_now = {str(h.get("stock_code")) for h in kept}
        cand_codes = [
            str(x.get("stock_code") or "").strip()
            for x in top_items
            if str(x.get("stock_code") or "").strip()
            and str(x.get("stock_code") or "").strip() not in held_now
            and not x.get("hard_reject")
        ]
        if str(prior_cfg_live.get("mode") or "off") == "off":
            sentiment_prior_summary = {
                "ok": True,
                "skipped": True,
                "warnings": [],
                "blocks": [],
                "note": "舆情先验关闭",
            }
        elif not prior_by_code and not cand_codes:
            sentiment_prior_summary = {
                "ok": True,
                "warnings": [],
                "blocks": [],
                "note": "无新开仓候选",
            }
        for w in sentiment_prior_summary.get("warnings") or []:
            warns = list(risk_gate.get("warnings") or [])
            if w and w not in warns:
                warns.append(w)
            risk_gate["warnings"] = warns
        for bi in sentiment_prior_summary.get("block_items") or []:
            msg = str(bi.get("message") or "")
            if msg and msg not in (risk_gate.get("warnings") or []):
                risk_gate.setdefault("warnings", []).append(msg)
        risk_gate["sentiment_prior"] = {
            "ok": sentiment_prior_summary.get("ok"),
            "warnings": sentiment_prior_summary.get("warnings") or [],
            "blocks": sentiment_prior_summary.get("blocks") or [],
            "candidate_count": len(cand_codes),
            "hold_check_count": len(held_now)
            if prior_cfg_live.get("scale_holds")
            else 0,
        }
    except Exception as exc:
        logger.exception("post-sell gate failed")
        sentiment_prior_summary = {"ok": True, "error": str(exc)}

    block_items = list((risk_gate or {}).get("block_items") or [])
    drawdown_blocks = [i for i in block_items if i.get("code") == "drawdown_limit"]
    soft_blocks = [i for i in block_items if i.get("code") != "drawdown_limit"]

    # 回撤恢复检测：current_drawdown_pct 已低于 target_dd → 解除加仓封锁
    recovery_info = (risk_gate or {}).get("recovery")
    if recovery_info and recovery_info.get("recovered") and not drawdown_blocks:
        prev_blocked = bool(paper.get("drawdown_blocked_since"))
        if prev_blocked:
            paper.pop("drawdown_blocked_since", None)
            append_operation_log(
                paper,
                "risk_recovery",
                detail=str(recovery_info.get("note") or "回撤恢复，加仓解锁"),
                meta={
                    "current_dd": recovery_info.get("current_dd"),
                    "recovery_threshold": recovery_info.get("recovery_threshold"),
                    "path": "cluster" if not respect_max_positions else "cross_section",
                },
            )

    if drawdown_blocks:
        buys_blocked = True
        paper["drawdown_blocked_since"] = _now_iso()
        blocks = [str(i.get("message") or i.get("code")) for i in drawdown_blocks]
        append_operation_log(
            paper,
            "risk_block",
            detail="横截面调仓风控拦截加仓："
            + ("；".join(blocks) or "回撤超限"),
            meta={
                "codes": ["drawdown_limit"],
                "block_items": drawdown_blocks,
                "blocks": blocks,
                "warnings": risk_gate.get("warnings") or [],
                "limits": risk_limits,
                "recovery": recovery_info,
                "path": "cluster" if not respect_max_positions else "cross_section",
            },
        )
    if soft_blocks and not drawdown_blocks:
        # 已有超限：不整批拦买，逐笔缩量；文案进 warnings（勿再标 blocks 以免 UI 显示整批拦截）
        soft_msgs = [
            str(i.get("message") or i.get("code")) for i in soft_blocks if i
        ]
        warns = list(risk_gate.get("warnings") or [])
        for m in soft_msgs:
            tip = f"已超限·买入缩量：{m}" if m else m
            if tip and tip not in warns:
                warns.append(tip)
        risk_gate["warnings"] = warns
        risk_gate["ok"] = True
        risk_gate["blocks"] = []
        risk_gate["block_items"] = []
        risk_gate["soft_block_items"] = soft_blocks
        append_operation_log(
            paper,
            "risk_budget",
            detail="持仓已触单票/行业上限 · 买入按剩余预算缩量："
            + ("；".join(soft_msgs[:4]) or "见 limits"),
            meta={
                "block_items": soft_blocks,
                "limits": risk_limits,
                "path": "cluster" if not respect_max_positions else "cross_section",
            },
        )

    # 目标权重建议 + 逐笔风险预算（restore 在回撤硬拦时仍执行）
    target_w: Dict[str, Any] = {}
    try:
        from core.portfolio_optimize import optimize_weights
        from core.strategy import get_strategy_spec

        sid = paper.get("strategy_id") or "short"
        rr = (get_strategy_spec(str(sid)).get("risk") or {})
        if not risk_limits:
            risk_limits = {
                "max_position_pct": float(rr.get("max_position_pct") or 25.0),
                "max_sector_pct": float(rr.get("max_sector_pct") or 40.0),
            }
        opt_cap = (
            max_positions
            if not respect_max_positions
            else int(rr.get("max_positions") or max_positions)
        )
        opt_min = float(min_score)
        weight_mode = str(
            rules.get("weight_mode")
            or rr.get("weight_mode")
            or "score_budget"
        ).strip() or "score_budget"
        opt = optimize_weights(
            list(ranking or []),
            max_position_pct=float(
                risk_limits.get("max_position_pct")
                or rr.get("max_position_pct")
                or 25.0
            ),
            max_sector_pct=float(
                risk_limits.get("max_sector_pct")
                or rr.get("max_sector_pct")
                or 40.0
            ),
            max_positions=opt_cap,
            min_score=opt_min,
            weight_mode=weight_mode,
        )
        paper["last_optimize"] = opt
        target_w = opt.get("weights_pct") or {}
    except Exception as e:
        logger.exception("post-sell gate failed")
        paper["last_optimize"] = None
        target_w = {}
        if not risk_limits:
            risk_limits = {"max_position_pct": 25.0, "max_sector_pct": 40.0}
        warn = f"optimize_weights 失败，已降级无目标仓约束: {type(e).__name__}: {e}"
        warns = list(risk_gate.get("warnings") or [])
        if warn not in warns:
            warns.append(warn)
        risk_gate["warnings"] = warns
        risk_gate["optimize_fallback"] = True



    state.risk_gate = risk_gate
    state.risk_limits = risk_limits
    state.buys_blocked = bool(buys_blocked)
    state.sentiment_prior_summary = sentiment_prior_summary
    state.prior_by_code = prior_by_code if isinstance(prior_by_code, dict) else {}
    state.prior_cfg_live = prior_cfg_live if isinstance(prior_cfg_live, dict) else {}
    return target_w if isinstance(target_w, dict) else {}
