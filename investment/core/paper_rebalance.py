"""横截面驱动的纸面调仓模拟（P11.3，非实盘）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.paper import ORIGIN_STRATEGY, _now_iso, append_operation_log, build_ops_report
from core.paper_costs import (
    annotate_trade,
    apply_fill_price,
    calc_trade_fees,
    cost_params,
    resolve_cost_model,
)
from core.ports.market import quote_price as _quote_price


def compute_turnover_stats(
    sell_trades: List[dict],
    buy_trades: List[dict],
    *,
    equity_before: Optional[float],
    max_turnover_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """双边换手：``(买额+卖额)/2/净值``；可选对照 ``max_turnover_pct`` 软上限。"""
    sell_amount = round(sum(float(t.get("amount") or 0) for t in sell_trades or []), 2)
    buy_amount = round(sum(float(t.get("amount") or 0) for t in buy_trades or []), 2)
    eq = float(equity_before or 0)
    turnover_pct = (
        round((sell_amount + buy_amount) / 2.0 / eq * 100.0, 2) if eq > 0 else None
    )
    max_to = None
    if max_turnover_pct is not None:
        try:
            max_to = float(max_turnover_pct)
        except (TypeError, ValueError):
            max_to = None
    over = (
        turnover_pct is not None and max_to is not None and turnover_pct > max_to + 1e-9
    )
    return {
        "buy_amount": buy_amount,
        "sell_amount": sell_amount,
        "buy_count": len(buy_trades or []),
        "sell_count": len(sell_trades or []),
        "equity_before": round(eq, 2) if eq > 0 else None,
        "turnover_pct": turnover_pct,
        "max_turnover_pct": max_to,
        "over_limit": bool(over),
        "definition": "two_way=(buy+sell)/2/equity*100",
    }


def build_rebalance_cash_impact(
    *,
    cash_before: float,
    position_count_before: int,
    sell_trades: List[dict],
    buy_trades: List[dict],
    summary: Optional[dict],
    equity_before: Optional[float] = None,
    cost_model: Optional[str] = None,
    max_turnover_pct: Optional[float] = None,
    turnover_capped: bool = False,
) -> Dict[str, Any]:
    """资金影响 + 换手摘要（预演/落账共用）。"""
    turn = compute_turnover_stats(
        sell_trades,
        buy_trades,
        equity_before=equity_before,
        max_turnover_pct=max_turnover_pct,
    )
    cash_after = float((summary or {}).get("cash") or 0)
    return {
        "cash_before": round(float(cash_before or 0), 2),
        "buy_amount": turn["buy_amount"],
        "sell_amount": turn["sell_amount"],
        "net_cash_flow": round(turn["sell_amount"] - turn["buy_amount"], 2),
        "cash_after": round(cash_after, 2),
        "position_count_before": int(position_count_before or 0),
        "position_count_after": int((summary or {}).get("position_count") or 0),
        "equity_after": (summary or {}).get("equity"),
        "equity_before": turn.get("equity_before"),
        "cost_model": cost_model,
        "turnover_pct": turn.get("turnover_pct"),
        "max_turnover_pct": turn.get("max_turnover_pct"),
        "turnover_over_limit": turn.get("over_limit"),
        "turnover_capped": bool(turnover_capped),
        "turnover_definition": turn.get("definition"),
        "buy_count": turn.get("buy_count"),
        "sell_count": turn.get("sell_count"),
    }


def simulate_cross_section_rebalance(
    paper: dict,
    ranking: List[dict],
    *,
    top_k: Optional[int] = None,
    min_score: Optional[float] = None,
    respect_max_positions: bool = True,
    score_lookup: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    """
    按横截面 TopK 调仓：卖出不在 TopK 或分数低于 min_hold_score 的持仓，
    再从 TopK 中买入未持有且 score >= min_score 的标的。
    买入前强制 check_account_risk；超限则拦截加仓并写 risk_block 日志。

    ``respect_max_positions=False``：分池落账用，按候选簿长度持有，不被纸面 max_positions 截断。
    ``score_lookup``：可选全量打分行（含低于 min_score 未进簿的票），供卖出腿带分。
    """
    from core.ports.market import query_quote, quote_price

    rules = paper.get("rules") or {}
    cost_model = resolve_cost_model(paper)
    fee_params = cost_params(paper)
    max_pos = max(1, int(rules.get("max_positions") or 5))
    if top_k is None:
        top_k = max_pos
    else:
        top_k = max(1, int(top_k))
    if respect_max_positions:
        top_k = min(top_k, max_pos, 30)
    else:
        # 分池：按簿长持有；上限对齐 cluster max_names（80），勿再用 30 砍掉簿尾
        top_k = min(top_k, 80)
    if min_score is None:
        from core.signal.score_display import resolve_buy_floor, resolve_hold_floor

        min_score = resolve_buy_floor(paper, heuristic_default=55.0)
        min_hold_score = resolve_hold_floor(paper, heuristic_default=45.0)
    else:
        min_score = float(min_score)
        from core.signal.score_display import resolve_hold_floor

        min_hold_score = resolve_hold_floor(paper, heuristic_default=45.0)
    # 分池：持仓上限=簿长；买入门槛见 resolve_buy_floor（收益分可无下限）
    if not respect_max_positions:
        max_positions = top_k
    else:
        max_positions = max_pos
    position_pct = float(rules.get("position_pct") or 0.15)
    max_turnover_pct: Optional[float] = None
    raw_mto = rules.get("max_turnover_pct", rules.get("max_turnover"))
    if raw_mto is not None and raw_mto != "":
        try:
            max_turnover_pct = float(raw_mto)
        except (TypeError, ValueError):
            max_turnover_pct = None

    top_items = (ranking or [])[:top_k]
    top_codes = {str(x.get("stock_code") or "") for x in top_items if x.get("stock_code")}
    score_by_code: Dict[str, float] = {}
    for src in list(score_lookup or []) + list(ranking or []):
        code = str(src.get("stock_code") or "")
        if not code or code in score_by_code:
            continue
        try:
            if src.get("score") is not None:
                score_by_code[code] = float(src.get("score"))
        except (TypeError, ValueError):
            continue

    holdings = paper.get("holdings") or []
    cash_before = float(paper.get("cash") or 0)
    position_count_before = len(
        [h for h in holdings if float(h.get("shares") or 0) > 0]
    )
    equity_before: Optional[float] = None
    try:
        from core.paper import mark_to_market as _mtm0

        equity_before = float((_mtm0(paper) or {}).get("equity") or 0) or None
    except Exception:
        equity_before = None
    cash = cash_before
    sell_trades: List[dict] = []
    kept = []
    turnover_capped = False
    turnover_skipped: List[str] = []

    for h in holdings:
        code = str(h.get("stock_code") or "")
        shares = float(h.get("shares") or 0)
        cost = float(h.get("cost") or 0)
        if not code or shares <= 0:
            continue

        score = score_by_code.get(code)
        in_top = code in top_codes
        reason = None
        if not in_top:
            reason = (
                "不在分池目标簿（组权分全局排序截断之外）"
                if not respect_max_positions
                else "不在横截面 TopK"
            )
        elif score is not None and score < min_hold_score:
            reason = f"分数低于 min_hold_score({min_hold_score})"

        if not reason:
            kept.append(h)
            continue

        quote = query_quote(code)
        price = _quote_price(quote) if quote.get("success") else None
        if not price or price <= 0:
            kept.append(h)
            continue

        pnl_pct = round((price / cost - 1.0) * 100.0, 2) if cost else None
        fill_px = apply_fill_price(
            "sell", float(price), model=cost_model, params=fee_params
        )
        amount = round(shares * fill_px, 2)
        fee_info = calc_trade_fees(
            "sell", amount, model=cost_model, params=fee_params
        )
        trade = annotate_trade(
            {
                "ts": _now_iso(),
                "side": "sell",
                "stock_code": code,
                "stock_name": h.get("stock_name") or quote.get("stock_name"),
                "shares": shares,
                "price": round(fill_px, 4),
                "amount": amount,
                "pnl_pct": pnl_pct,
                "score": score,
                "origin": ORIGIN_STRATEGY,
                "note": f"横截面调仓卖出：{reason}",
            },
            fee_info,
        )
        paper.setdefault("trades", []).append(trade)
        sell_trades.append(trade)
        cash = round(cash + float(fee_info["net_cash_delta"]), 2)

    paper["holdings"] = kept
    paper["cash"] = round(cash, 2)

    buy_trades: List[dict] = []
    held_codes = {str(h.get("stock_code")) for h in kept}
    holdings = kept
    buys_blocked = False
    risk_budget_skips: List[dict] = []
    risk_gate: Dict[str, Any] = {"ok": True, "blocks": [], "warnings": []}
    mid_summary: Dict[str, Any] = {}
    risk_limits: Dict[str, Any] = {}

    # 卖出后、买入前：账户风控；回撤硬拦，单票/行业改走逐笔预算缩量（P1）
    try:
        from core.paper import mark_to_market
        from core.risk import check_account_risk

        mid_summary = mark_to_market(paper)
        risk_gate = check_account_risk(paper, mid_summary)
        risk_limits = dict(risk_gate.get("limits") or {})
    except Exception:
        risk_gate = {"ok": True, "blocks": [], "warnings": []}
        risk_limits = {}

    block_items = list((risk_gate or {}).get("block_items") or [])
    drawdown_blocks = [i for i in block_items if i.get("code") == "drawdown_limit"]
    soft_blocks = [i for i in block_items if i.get("code") != "drawdown_limit"]

    if drawdown_blocks:
        buys_blocked = True
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
                "path": "cluster" if not respect_max_positions else "cross_section",
            },
        )
    else:
        if soft_blocks:
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

        # 目标权重建议 + 逐笔风险预算
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
            )
            paper["last_optimize"] = opt
            target_w = opt.get("weights_pct") or {}
        except Exception:
            paper["last_optimize"] = None
            target_w = {}
            if not risk_limits:
                risk_limits = {"max_position_pct": 25.0, "max_sector_pct": 40.0}

        from core.portfolio_optimize import _sector_for, load_sector_map
        from core.risk.budget import (
            build_running_exposure_mv,
            clip_buy_to_risk_budget,
        )

        smap = load_sector_map()
        mid_holdings = list((mid_summary or {}).get("holdings") or holdings)
        name_mv, sector_mv = build_running_exposure_mv(mid_holdings, sector_map=smap)
        budget_equity = float(
            (mid_summary or {}).get("equity") or equity_before or 0
        ) or float(equity_before or 0)
        max_pos_pct = float(risk_limits.get("max_position_pct") or 25.0)
        max_sec_pct = float(risk_limits.get("max_sector_pct") or 40.0)

        for item in top_items:
            if len(holdings) >= max_positions:
                break
            code = str(item.get("stock_code") or "")
            if not code or code in held_codes:
                continue
            if item.get("hard_reject"):
                continue
            score = item.get("score")
            if score is None or float(score) < min_score:
                continue
            # 横截面：optimize 未分配则跳过；分池：合并簿即目标集，不因 optimize 漏配而整票跳过
            if (
                respect_max_positions
                and target_w
                and code not in target_w
            ):
                risk_budget_skips.append(
                    {
                        "stock_code": code,
                        "stock_name": item.get("stock_name"),
                        "reason": "不在风险预算目标仓（optimize 未分配权重）",
                        "score": score,
                    }
                )
                continue

            quote = query_quote(code)
            price = _quote_price(quote)
            if not price or price <= 0:
                continue

            ratio = position_pct
            # 仅横截面用目标仓缩量；分池用账户 position_pct + 单票/行业 clip
            if respect_max_positions and code in target_w:
                try:
                    ratio = min(ratio, float(target_w[code]) / 100.0)
                except (TypeError, ValueError):
                    pass
            budget = cash * ratio
            if budget < price * 100:
                continue
            shares = int(budget // price // 100) * 100
            if shares <= 0:
                continue

            sector = str(
                item.get("sector") or _sector_for(code, smap) or "其他"
            )
            clip = clip_buy_to_risk_budget(
                code=code,
                sector=sector,
                price=float(price),
                shares=shares,
                equity=budget_equity,
                name_mv=name_mv,
                sector_mv=sector_mv,
                max_position_pct=max_pos_pct,
                max_sector_pct=max_sec_pct,
            )
            if clip.get("skipped"):
                risk_budget_skips.append(
                    {
                        "stock_code": code,
                        "stock_name": item.get("stock_name"),
                        "reason": clip.get("reason") or "风险预算跳过",
                        "score": score,
                        "sector": sector,
                    }
                )
                continue
            shares = int(clip.get("shares") or shares)

            fill_px = apply_fill_price(
                "buy", float(price), model=cost_model, params=fee_params
            )
            amount = round(shares * fill_px, 2)
            fee_info = calc_trade_fees(
                "buy", amount, model=cost_model, params=fee_params
            )
            need = amount + float(fee_info.get("fees") or 0)
            if need > cash + 1e-6:
                continue

            # P0：换手软上限 — 再加一笔买会使双边换手超限则跳过该买
            if max_turnover_pct is not None and equity_before and equity_before > 0:
                sell_amt = sum(float(t.get("amount") or 0) for t in sell_trades)
                buy_amt = sum(float(t.get("amount") or 0) for t in buy_trades) + amount
                proj_to = (sell_amt + buy_amt) / 2.0 / equity_before * 100.0
                if proj_to > float(max_turnover_pct) + 1e-9:
                    turnover_capped = True
                    turnover_skipped.append(code)
                    continue

            tw_note = ""
            if code in target_w:
                tw_note = f" · 目标仓{float(target_w[code]):.1f}%"
            clip_note = f" · {clip['reason']}" if clip.get("clipped") else ""
            buy_label = (
                "分池目标簿买入"
                if not respect_max_positions
                else "横截面调仓买入（TopK）"
            )
            trade = annotate_trade(
                {
                    "ts": _now_iso(),
                    "side": "buy",
                    "stock_code": code,
                    "stock_name": item.get("stock_name") or quote.get("stock_name"),
                    "shares": shares,
                    "price": round(fill_px, 4),
                    "amount": amount,
                    "score": score,
                    "origin": ORIGIN_STRATEGY,
                    "note": f"{buy_label}{tw_note}{clip_note}",
                    "target_weight_pct": target_w.get(code),
                    "sector": sector,
                    "risk_budget_clipped": bool(clip.get("clipped")),
                },
                fee_info,
            )
            paper.setdefault("trades", []).append(trade)
            buy_trades.append(trade)
            holdings.append(
                {
                    "stock_code": code,
                    "stock_name": trade["stock_name"],
                    "shares": shares,
                    "cost": round(fill_px, 4),
                    "bought_at": trade["ts"],
                    "origin": ORIGIN_STRATEGY,
                    "sector": sector,
                    "market_value": amount,
                }
            )
            held_codes.add(code)
            cash = round(cash + float(fee_info["net_cash_delta"]), 2)
            name_mv[code] = float(name_mv.get(code) or 0.0) + amount
            sector_mv[sector] = float(sector_mv.get(sector) or 0.0) + amount

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
        except Exception:
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
    except Exception:
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
    except Exception:
        monitor_alerts = []
        health = {}
    metrics = health.get("metrics") if isinstance(health, dict) else None
    north_star = None
    source_audit = None
    try:
        from core.north_star import merge_north_star_into_metrics

        metrics, north_star = merge_north_star_into_metrics(paper, metrics)
        paper["last_north_star"] = north_star
    except Exception:
        pass
    try:
        from core.data_consistency import audit_code_sources

        codes_audit = [
            str(h.get("stock_code") or "").strip()
            for h in (paper.get("holdings") or [])
            if str(h.get("stock_code") or "").strip()
        ]
        source_audit = audit_code_sources(codes_audit)
    except Exception:
        source_audit = None
    if not summary:
        try:
            from core.paper import mark_to_market as _mtm1

            summary = _mtm1(paper) or {}
        except Exception:
            summary = {}

    attribution: Dict[str, Any] = {}
    try:
        from core.paper_attribution import build_paper_attribution_lite

        attribution = build_paper_attribution_lite(paper, summary) or {}
    except Exception:
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

    return {
        "success": True,
        "top_k": top_k,
        "target_codes": sorted(top_codes),
        "min_score": min_score,
        "min_hold_score": min_hold_score,
        "sell_trades": sell_trades,
        "buy_trades": buy_trades,
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
        "attribution": attribution,
        "note": "横截面调仓为纸面模拟，非实盘成交。",
    }
