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


def simulate_cross_section_rebalance(
    paper: dict,
    ranking: List[dict],
    *,
    top_k: Optional[int] = None,
    min_score: Optional[float] = None,
) -> Dict[str, Any]:
    """
    按横截面 TopK 调仓：卖出不在 TopK 或分数低于 min_hold_score 的持仓，
    再从 TopK 中买入未持有且 score >= min_score 的标的。
    买入前强制 check_account_risk；超限则拦截加仓并写 risk_block 日志。
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
    top_k = min(top_k, max_pos, 30)
    if min_score is None:
        min_score = float(rules.get("min_score") or 55.0)
    min_hold_score = float(rules.get("min_hold_score") or 45.0)
    max_positions = max_pos
    position_pct = float(rules.get("position_pct") or 0.15)

    top_items = (ranking or [])[:top_k]
    top_codes = {str(x.get("stock_code") or "") for x in top_items if x.get("stock_code")}
    score_by_code = {
        str(x.get("stock_code") or ""): float(x.get("score") or 0)
        for x in ranking or []
        if x.get("stock_code")
    }

    holdings = paper.get("holdings") or []
    cash = float(paper.get("cash") or 0)
    sell_trades: List[dict] = []
    kept = []

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
            reason = "不在横截面 TopK"
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
    risk_gate: Dict[str, Any] = {"ok": True, "blocks": [], "warnings": []}

    # 卖出后、买入前：强制账户风控（单票 + 行业）
    try:
        from core.paper import mark_to_market
        from core.risk import check_account_risk

        mid_summary = mark_to_market(paper)
        risk_gate = check_account_risk(paper, mid_summary)
    except Exception:
        risk_gate = {"ok": True, "blocks": [], "warnings": []}

    if risk_gate and not risk_gate.get("ok"):
        buys_blocked = True
        blocks = risk_gate.get("blocks") or []
        block_items = risk_gate.get("block_items") or []
        append_operation_log(
            paper,
            "risk_block",
            detail="横截面调仓风控拦截加仓："
            + ("；".join(str(b) for b in blocks) or "超限"),
            meta={
                "codes": risk_gate.get("block_codes")
                or [i.get("code") for i in block_items if isinstance(i, dict)],
                "block_items": block_items,
                "blocks": blocks,
                "warnings": risk_gate.get("warnings") or [],
                "limits": risk_gate.get("limits"),
                "path": "cross_section",
            },
        )
    else:
        # P1：目标权重建议进横截面调仓
        try:
            from core.portfolio_optimize import optimize_weights
            from core.strategy import get_strategy_spec

            sid = paper.get("strategy_id") or "short"
            rr = (get_strategy_spec(str(sid)).get("risk") or {})
            opt = optimize_weights(
                list(ranking or []),
                max_position_pct=float(rr.get("max_position_pct") or 25.0),
                max_sector_pct=float(rr.get("max_sector_pct") or 40.0),
                max_positions=int(rr.get("max_positions") or max_positions),
                min_score=float(min_score),
            )
            paper["last_optimize"] = opt
            target_w = opt.get("weights_pct") or {}
        except Exception:
            paper["last_optimize"] = None
            target_w = {}

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
            # 有优化结果时：不在目标仓则跳过
            if target_w and code not in target_w:
                continue

            quote = query_quote(code)
            price = _quote_price(quote)
            if not price or price <= 0:
                continue

            ratio = position_pct
            if code in target_w:
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

            tw_note = ""
            if code in target_w:
                tw_note = f" · 目标仓{float(target_w[code]):.1f}%"
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
                    "note": f"横截面调仓买入（TopK）{tw_note}",
                    "target_weight_pct": target_w.get(code),
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
                }
            )
            held_codes.add(code)
            cash = round(cash + float(fee_info["net_cash_delta"]), 2)

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

    dq = None
    try:
        from core.data_service import summarize_data_quality
        from core.paper_costs import resolve_cost_model

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
        cost_model = resolve_cost_model(paper)
    except Exception:
        from core.paper_costs import resolve_cost_model

        cost_model = resolve_cost_model(paper)

    risk_blocks = (risk_gate or {}).get("blocks") or []
    monitor_alerts: list = []
    health: dict = {}
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
    )
    paper["last_ops_report"] = ops_report

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
        "note": "横截面调仓为纸面模拟，非实盘成交。",
    }
