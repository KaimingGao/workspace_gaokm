"""纸面盯市与模拟成交（从 paper.py 拆出）。

对外仍从 ``core.paper`` 再导出，保持原有 import 路径。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from core.paper_costs import (
    annotate_trade,
    apply_fill_price,
    calc_trade_fees,
    cost_params,
    resolve_cost_model,
)
from core.paper_sizing import _lot_shares
from core.ports.market import fetch_daily_bars, query_quote

from core.paper import (  # noqa: E402
    ORIGIN_LABELS,
    ORIGIN_MANUAL,
    ORIGIN_MIXED,
    ORIGIN_STRATEGY,
    _now_iso,
    _quote_price,
    merge_origin,
)

def mark_to_market(paper: dict) -> Dict[str, Any]:
    cash = float(paper.get("cash") or 0)
    holdings = paper.get("holdings") or []
    rows = []
    stock_value = 0.0
    now = datetime.now()
    for h in holdings:
        code = h.get("stock_code")
        quote = query_quote(str(code))
        price = _quote_price(quote) if quote.get("success") else None
        shares = float(h.get("shares") or 0)
        cost = float(h.get("cost") or 0)
        mv = (price or cost) * shares
        stock_value += mv
        pnl_pct = None
        if price and cost:
            pnl_pct = round((price / cost - 1.0) * 100.0, 2)
        bought_at = h.get("bought_at")
        hold_days = None
        bought_date = None
        if bought_at:
            try:
                dt = datetime.fromisoformat(str(bought_at).replace("Z", ""))
                hold_days = max(0, (now.date() - dt.date()).days)
                bought_date = dt.strftime("%Y-%m-%d")
            except ValueError:
                bought_date = str(bought_at)[:10] or None
        rows.append(
            {
                "stock_code": code,
                "stock_name": h.get("stock_name") or quote.get("stock_name"),
                "shares": shares,
                "cost": cost,
                "price": price,
                "currency": quote.get("currency", "CNY"),
                "unit": quote.get("unit", "元"),
                "market_value": round(mv, 2),
                "pnl_pct": pnl_pct,
                "bought_at": bought_at,
                "bought_date": bought_date,
                "hold_days": hold_days,
                "origin": h.get("origin") or None,
                "origin_label": ORIGIN_LABELS.get(str(h.get("origin") or ""), ""),
            }
        )

    initial = float(paper.get("initial_cash") or paper.get("cash") or 0)
    equity = cash + stock_value
    total_pnl_pct = None
    if initial > 0:
        total_pnl_pct = round((equity / initial - 1.0) * 100.0, 2)

    origin_buckets: Dict[str, dict] = {}
    for row in rows:
        key = str(row.get("origin") or "").strip() or "unknown"
        bucket = origin_buckets.get(key)
        if not bucket:
            bucket = {
                "origin": key if key != "unknown" else None,
                "origin_label": ORIGIN_LABELS.get(key, "未标注" if key == "unknown" else key),
                "count": 0,
                "market_value": 0.0,
                "cost_value": 0.0,
            }
            origin_buckets[key] = bucket
        bucket["count"] += 1
        bucket["market_value"] += float(row.get("market_value") or 0)
        cost = float(row.get("cost") or 0)
        shares = float(row.get("shares") or 0)
        bucket["cost_value"] += cost * shares
    origin_summary = []
    for key in (ORIGIN_MANUAL, ORIGIN_STRATEGY, ORIGIN_MIXED, "unknown"):
        bucket = origin_buckets.get(key)
        if not bucket:
            continue
        mv = round(bucket["market_value"], 2)
        cv = round(bucket["cost_value"], 2)
        pnl_pct = round((mv / cv - 1.0) * 100.0, 2) if cv > 0 else None
        weight_pct = round(mv / stock_value * 100.0, 1) if stock_value > 0 else 0.0
        origin_summary.append(
            {
                "origin": bucket["origin"],
                "origin_label": bucket["origin_label"],
                "count": bucket["count"],
                "market_value": mv,
                "cost_value": cv,
                "pnl_pct": pnl_pct,
                "weight_pct": weight_pct,
            }
        )

    invested_pct = round(stock_value / equity * 100.0, 1) if equity > 0 else 0.0
    peak = max(initial, equity)
    max_dd = 0.0
    for snap in paper.get("snapshots") or []:
        eq = float(snap.get("equity") or 0)
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
    if peak > 0 and equity < peak:
        max_dd = max(max_dd, (peak - equity) / peak)

    # E0：策略纯净净值 = 策略仓市值 + 按仓位占比分摊现金（混仓时归因近似）
    strat_bucket = origin_buckets.get(ORIGIN_STRATEGY) or {}
    strategy_stock_value = round(float(strat_bucket.get("market_value") or 0), 2)
    equity_strategy = None
    if strategy_stock_value > 0 and stock_value > 0:
        cash_attr = cash * (strategy_stock_value / stock_value)
        equity_strategy = round(strategy_stock_value + cash_attr, 2)

    return {
        "cash": round(cash, 2),
        "stock_value": round(stock_value, 2),
        "equity": round(equity, 2),
        "equity_strategy": equity_strategy,
        "strategy_stock_value": strategy_stock_value,
        "initial_cash": initial,
        "total_pnl_pct": total_pnl_pct,
        "holdings": rows,
        "position_count": len(rows),
        "origin_summary": origin_summary,
        "invested_pct": invested_pct,
        "max_drawdown_pct": round(max_dd * 100.0, 2),
        "cost_model": resolve_cost_model(paper),
    }


def manual_buy(
    paper: dict,
    stock_code: str,
    *,
    amount: Optional[float] = None,
    shares: Optional[float] = None,
) -> dict:
    """手动加仓：按金额或股数以现价假买（非实盘）。可对新票开仓。"""
    code = str(stock_code or "").strip()
    if not code:
        raise ValueError("请指定股票代码")
    if amount is None and shares is None:
        raise ValueError("请填写金额或股数")
    if amount is not None and float(amount) <= 0:
        raise ValueError("金额须大于 0")
    if shares is not None and float(shares) <= 0:
        raise ValueError("股数须大于 0")

    quote = query_quote(code)
    if not quote.get("success"):
        raise ValueError(quote.get("error") or f"无法获取 {code} 行情")
    raw_price = _quote_price(quote)
    if not raw_price or raw_price <= 0:
        raise ValueError(f"{code} 现价无效")

    model = resolve_cost_model(paper)
    params = cost_params(paper)
    price = apply_fill_price("buy", raw_price, model=model, params=params)

    cash = float(paper.get("cash") or 0)
    if amount is not None:
        budget = min(float(amount), cash)
        buy_shares = _lot_shares(budget // price)
    else:
        buy_shares = _lot_shares(shares or 0)
    if buy_shares <= 0:
        raise ValueError("按现价不足一手（100 股），请加大金额或股数")
    trade_amount = round(buy_shares * price, 2)
    fee_info = calc_trade_fees("buy", trade_amount, model=model, params=params)
    need = trade_amount + float(fee_info.get("fees") or 0)
    if need > cash + 1e-6:
        raise ValueError(f"现金不足（需 {need}，可用 {round(cash, 2)}）")

    holdings = paper.get("holdings") or []
    existing = next((h for h in holdings if str(h.get("stock_code")) == code), None)
    name = (existing or {}).get("stock_name") or quote.get("stock_name")
    trade = annotate_trade(
        {
            "ts": _now_iso(),
            "side": "buy",
            "stock_code": code,
            "stock_name": name,
            "shares": buy_shares,
            "price": round(price, 4),
            "amount": trade_amount,
            "origin": ORIGIN_MANUAL,
            "note": "手动加仓",
        },
        fee_info,
    )
    paper.setdefault("trades", []).append(trade)
    if existing:
        old_shares = float(existing.get("shares") or 0)
        old_cost = float(existing.get("cost") or 0)
        new_shares = old_shares + buy_shares
        if new_shares > 0:
            existing["cost"] = round(
                (old_cost * old_shares + price * buy_shares) / new_shares, 4
            )
        existing["shares"] = new_shares
        existing["origin"] = merge_origin(existing.get("origin"), ORIGIN_MANUAL)
        if name and not existing.get("stock_name"):
            existing["stock_name"] = name
    else:
        holdings.append(
            {
                "stock_code": code,
                "stock_name": name,
                "shares": buy_shares,
                "cost": round(price, 4),
                "bought_at": trade["ts"],
                "origin": ORIGIN_MANUAL,
            }
        )
        paper["holdings"] = holdings

    paper["cash"] = round(cash + float(fee_info["net_cash_delta"]), 2)
    paper["updated_at"] = trade["ts"]
    return trade


def manual_sell(
    paper: dict,
    *,
    codes: Optional[List[str]] = None,
    stock_code: Optional[str] = None,
    shares: Optional[float] = None,
) -> List[dict]:
    """手动减仓 / 清仓。codes 批量时整仓卖出；单票可指定股数。"""
    if stock_code and not codes:
        targets = [str(stock_code).strip()]
    else:
        targets = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if not targets:
        raise ValueError("请勾选要卖出的持仓")
    if shares is not None and len(targets) != 1:
        raise ValueError("指定股数时只能卖一只")
    if shares is not None and float(shares) <= 0:
        raise ValueError("股数须大于 0")

    target_set = set(targets)
    holdings = list(paper.get("holdings") or [])
    by_code = {str(h.get("stock_code")): h for h in holdings if h.get("stock_code")}
    cash = float(paper.get("cash") or 0)
    trades: List[dict] = []
    remain_by_code: Dict[str, dict] = {}
    model = resolve_cost_model(paper)
    params = cost_params(paper)

    for code in targets:
        h = by_code.get(code)
        if not h:
            raise ValueError(f"持仓中没有 {code}")
        held = float(h.get("shares") or 0)
        if held <= 0:
            raise ValueError(f"{code} 持仓股数为 0")
        if shares is not None:
            want = min(held, float(shares))
            if want + 1e-9 < held:
                lot = _lot_shares(want)
                if lot <= 0:
                    raise ValueError("减仓至少 100 股（或点清仓）")
                sell_shares = float(lot)
            else:
                sell_shares = held
        else:
            sell_shares = held

        quote = query_quote(code)
        raw_price = _quote_price(quote) if quote.get("success") else None
        if not raw_price or raw_price <= 0:
            raw_price = float(h.get("cost") or 0)
        if not raw_price or raw_price <= 0:
            raise ValueError(f"{code} 无法定价")
        price = apply_fill_price("sell", raw_price, model=model, params=params)

        amount = round(sell_shares * price, 2)
        fee_info = calc_trade_fees("sell", amount, model=model, params=params)
        trade = annotate_trade(
            {
                "ts": _now_iso(),
                "side": "sell",
                "stock_code": code,
                "stock_name": h.get("stock_name") or quote.get("stock_name"),
                "shares": sell_shares,
                "price": round(price, 4),
                "amount": amount,
                "note": "手动清仓" if sell_shares >= held - 1e-9 else "手动减仓",
            },
            fee_info,
        )
        paper.setdefault("trades", []).append(trade)
        trades.append(trade)
        cash += float(fee_info["net_cash_delta"])
        left = held - sell_shares
        if left > 1e-6:
            row = dict(h)
            row["shares"] = left
            remain_by_code[code] = row

    kept: List[dict] = []
    for h in holdings:
        code = str(h.get("stock_code") or "")
        if code not in target_set:
            kept.append(h)
        elif code in remain_by_code:
            kept.append(remain_by_code[code])

    paper["holdings"] = kept
    paper["cash"] = round(cash, 2)
    paper["updated_at"] = _now_iso()
    return trades


def simulate_buys(paper: dict, pool: List[dict]) -> List[dict]:
    """按评分加仓：评分≥60分加仓50%，支持凯利公式+技术确认。
    
    加仓条件：
    - 评分 ≥ 60分
    - 凯利公式确定仓位大小
    - 技术指标确认（可配置）
    
    新买入：
    - 评分 ≥ min_score
    - 自适应仓位管理
    """
    from core.signal.factors.kelly import adaptive_position_sizing
    from core.signal.factors.adaptive import adaptive_thresholds, get_risk_controls
    from core.signal.factors.technicals import check_technical_filters
    from core.signal.factors.dynamic_position import get_full_position_plan
    
    rules = paper.get("rules") or {}
    base_min_score = float(rules.get("min_score") or 60.0)  # 与减仓阈值对齐
    base_sell_score = float(rules.get("add_score") or 60.0)  # 加仓阈值
    max_positions = int(rules.get("max_positions") or 5)
    position_pct = float(rules.get("position_pct") or 0.15)
    add_size_pct = float(rules.get("add_size_pct") or 0.5)  # 加仓比例50%
    cost_model = resolve_cost_model(paper)
    fee_params = cost_params(paper)
    
    # 自适应仓位配置
    use_adaptive_position = rules.get("use_adaptive_position", True)
    
    # 胜率自适应阈值
    use_adaptive_threshold = rules.get("use_adaptive_threshold", True)
    
    # 技术指标过滤（加仓时使用）
    use_technical_filter = rules.get("use_technical_filter", True)
    min_technical_score = float(rules.get("min_technical_score") or 40.0)
    
    # 获取风控状态
    risk_controls = get_risk_controls(paper, rules)
    
    # 自适应调整阈值
    if use_adaptive_threshold:
        min_score, sell_score, threshold_info = adaptive_thresholds(
            paper, base_min_score, base_sell_score, rules
        )
        paper.setdefault("adaptive_info", {})["threshold"] = threshold_info
    else:
        min_score = base_min_score
        sell_score = base_sell_score
    
    # 应用风控限制
    if risk_controls.get("trading_allowed") is False:
        return []  # 暂停交易
    
    max_positions = min(max_positions, risk_controls.get("max_positions", max_positions))

    holdings = paper.get("holdings") or []
    held_codes = {str(h.get("stock_code")): h for h in holdings}
    trades: List[dict] = []

    cash = float(paper.get("cash") or 0)
    new_positions_count = len(holdings)
    
    for item in pool:
        code = str(item.get("stock_code") or "")
        if not code:
            continue
        if item.get("hard_reject"):
            item["skip_reason"] = f"硬性拒绝：{item.get('reject_reason') or '未知原因'}"
            continue
        score = item.get("score")
        if score is None or float(score) < min_score:
            item["skip_reason"] = f"评分{score}<{min_score:.1f}，不满足加仓阈值"
            continue

        quote = query_quote(code)
        price = _quote_price(quote)
        if not price or price <= 0:
            item["skip_reason"] = "行情获取失败，无法加仓"
            continue

        if code in held_codes:
            # 已有持仓：使用动态仓位管理
            holding = held_codes[code]
            current_shares = float(holding.get("shares") or 0)
            score_val = float(score)
            
            # 技术指标检查（提前获取）
            technical_info = None
            if use_technical_filter:
                try:
                    bars, _ = fetch_daily_bars(code, limit=60)
                    if bars:
                        tech_passed, tech_info = check_technical_filters(bars, min_technical_score)
                        technical_info = tech_info
                except Exception:
                    pass
            
            # 获取完整仓位调整计划
            position_plan = get_full_position_plan(
                score_val,
                current_shares,
                add_threshold=sell_score,
                min_score=min_score,
                add_ratio=add_size_pct,
                use_kelly=use_adaptive_position,
                use_technical=use_technical_filter,
                technical_info=technical_info,
                min_technical_score=min_technical_score,
                paper=paper,
                rules=rules,
            )
            
            if position_plan["action"] == "add":
                add_shares = position_plan["shares"]
                fill_px = apply_fill_price(
                    "buy", float(price), model=cost_model, params=fee_params
                )
                amount = round(add_shares * fill_px, 2)
                fee_info = calc_trade_fees(
                    "buy", amount, model=cost_model, params=fee_params
                )
                need = amount + float(fee_info.get("fees") or 0)
                if need > cash + 1e-6:
                    item["skip_reason"] = (
                        f"现金不足（需{round(need,2)}，可用{round(cash,2)}）"
                    )
                    continue

                trade = annotate_trade(
                    {
                        "ts": _now_iso(),
                        "side": "buy",
                        "stock_code": code,
                        "stock_name": item.get("stock_name") or quote.get("stock_name"),
                        "shares": add_shares,
                        "price": round(fill_px, 4),
                        "amount": amount,
                        "score": score,
                        "origin": ORIGIN_STRATEGY,
                        "note": position_plan["reason"],
                        "position_plan": position_plan,
                    },
                    fee_info,
                )
                paper.setdefault("trades", []).append(trade)
                trades.append(trade)
                holding["shares"] = round(current_shares + add_shares, 0)
                holding["origin"] = merge_origin(holding.get("origin"), ORIGIN_STRATEGY)
                # 更新成本（加权平均）
                old_cost = float(holding.get("cost") or 0)
                new_avg_cost = (
                    old_cost * current_shares + fill_px * add_shares
                ) / (current_shares + add_shares)
                holding["cost"] = round(new_avg_cost, 4)
                cash = round(cash + float(fee_info["net_cash_delta"]), 2)
            else:
                # 评分达标但未加仓，记录真实原因（技术未确认/凯利仓位不足等）
                item["skip_reason"] = position_plan.get("reason") or f"action={position_plan.get('action')}"
            # reduce 和 hold 逻辑在 simulate_sells 中处理
        else:
            # 新标的，评分达标则买入（受max_positions限制）
            if new_positions_count >= max_positions:
                continue
            
            # 相关性风控
            try:
                from core.signal.factors.correlation import suggest_replacement
                
                new_bars, _ = fetch_daily_bars(code, limit=30)
                existing_bars = {}
                for h_code in held_codes:
                    try:
                        bars, _ = fetch_daily_bars(h_code, limit=30)
                        if bars:
                            existing_bars[h_code] = bars
                    except Exception:
                        pass
                
                if new_bars and existing_bars:
                    temp_bars = {code: new_bars, **existing_bars}
                    stock_to_replace = suggest_replacement(
                        code, temp_bars, max_correlation=0.8
                    )
                    if stock_to_replace:
                        continue
            except Exception:
                pass
            
            # 技术指标过滤
            if use_technical_filter:
                try:
                    bars, _ = fetch_daily_bars(code, limit=60)
                    if bars:
                        tech_passed, tech_info = check_technical_filters(bars, min_technical_score)
                        if not tech_passed:
                            continue
                        item["technical_info"] = tech_info
                except Exception:
                    pass
            
            new_positions_count += 1
            
            # 计算仓位比例
            if use_adaptive_position:
                position_ratio = adaptive_position_sizing(paper, score, rules)
            else:
                position_ratio = position_pct
            
            position_ratio = min(position_ratio, risk_controls.get("position_limit", 1.0))

            # P1：贪心目标权重上限（optimize_weights → last_optimize）
            opt_w = None
            last_opt = paper.get("last_optimize") or {}
            weights_pct = last_opt.get("weights_pct") or {}
            if code in weights_pct:
                try:
                    opt_w = float(weights_pct[code]) / 100.0
                    if opt_w > 0:
                        position_ratio = min(position_ratio, opt_w)
                except (TypeError, ValueError):
                    opt_w = None
            elif weights_pct:
                # 有优化结果但不在目标仓：跳过新开（建议不买）
                item["skip_reason"] = "不在 optimize_weights 目标仓"
                new_positions_count -= 1
                continue
            
            budget = cash * position_ratio
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

            note = f"买入（评分{score:.1f}分）"
            if opt_w is not None:
                note += f" · 目标仓{opt_w * 100:.1f}%"

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
                    "note": note,
                    "target_weight_pct": (
                        round(opt_w * 100.0, 4) if opt_w is not None else None
                    ),
                },
                fee_info,
            )

            paper.setdefault("trades", []).append(trade)
            trades.append(trade)
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
            held_codes[code] = holdings[-1]
            cash = round(cash + float(fee_info["net_cash_delta"]), 2)

    paper["holdings"] = holdings
    paper["cash"] = round(cash, 2)
    paper["updated_at"] = _now_iso()
    return trades


def simulate_sells(paper: dict, pool=None) -> List[dict]:
    """P8.1：纸面止损 / 超时卖出 / 梯度减仓（非实盘）。
    
    梯度减仓规则：
    - 评分 < 45：减仓80%
    - 45 ≤ 评分 < 55：减仓50%
    - 55 ≤ 评分 < 65：减仓30%
    
    优先级：止损 > 超时 > 梯度减仓
    """
    from datetime import datetime
    from core.signal.factors.risk import (
        should_stop_loss,
        update_peak_price,
    )
    from core.signal.factors.dynamic_position import get_reduce_position_plan

    rules = paper.get("rules") or {}
    stop_loss_pnl = float(rules.get("stop_loss_pnl") or -8.0)
    max_hold_days = int(rules.get("max_hold_days") or 5)
    min_score = int(rules.get("min_score") or 60)  # 与加仓阈值对齐
    
    # 动态止损配置
    stop_loss_mode = rules.get("stop_loss_mode", "adaptive")
    atr_multiplier = float(rules.get("atr_multiplier", 2.0))
    trail_pct = float(rules.get("trail_pct", 0.10))

    # 构建评分映射
    score_map = {}
    if pool:
        for item in pool:
            code = str(item.get("stock_code") or "")
            if code:
                score_map[code] = float(item.get("score") or 0)

    holdings = paper.get("holdings") or []
    if not holdings:
        return []

    cost_model = resolve_cost_model(paper)
    fee_params = cost_params(paper)
    cash = float(paper.get("cash") or 0)
    trades: List[dict] = []
    kept = []
    now = datetime.now()

    for h in holdings:
        code = str(h.get("stock_code") or "")
        shares = float(h.get("shares") or 0)
        cost = float(h.get("cost") or 0)
        if not code or shares <= 0:
            continue

        quote = query_quote(code)
        price = _quote_price(quote) if quote.get("success") else None
        if not price or cost <= 0:
            kept.append(h)
            continue

        pnl_pct = (price / cost - 1.0) * 100.0
        hold_days = None
        bought_at = h.get("bought_at")
        if bought_at:
            try:
                dt = datetime.fromisoformat(str(bought_at).replace("Z", ""))
                hold_days = (now - dt).days
            except ValueError:
                hold_days = None

        # 更新最高价跟踪
        peak_price = h.get("peak_price", cost)
        peak_price = update_peak_price(peak_price, price)
        h["peak_price"] = peak_price

        # 获取评分
        score = score_map.get(code)

        # 判断价格趋势
        is_trending_up = True
        if hold_days is not None and hold_days >= 2:
            if price < cost:
                is_trending_up = False

        reason = None
        sell_shares = shares  # 默认全部卖出
        
        # 1. 动态止损判断（最高优先级）
        try:
            bars, _ = fetch_daily_bars(code, limit=30)
            if bars:
                stop_triggered, _, stop_reason = should_stop_loss(
                    current_price=price,
                    entry_price=cost,
                    peak_price=peak_price,
                    bars=bars,
                    mode=stop_loss_mode,
                    atr_multiplier=atr_multiplier,
                    trail_pct=trail_pct,
                    fixed_stop_pct=0.12,
                )
                if stop_triggered:
                    reason = f"动态止损({stop_reason})"
        except Exception:
            if pnl_pct <= stop_loss_pnl:
                reason = f"固定止损({pnl_pct:.2f}%)"
        
        # 2. 固定止损（最后防线）
        if not reason and pnl_pct <= stop_loss_pnl:
            reason = f"固定止损({pnl_pct:.2f}%)"
        
        # 3. 时间止损（持有超时且趋势向下）
        if not reason and hold_days is not None and hold_days >= max_hold_days:
            if is_trending_up:
                kept.append(h)
                continue
            else:
                reason = f"持有超时({hold_days}天)且趋势向下"
        
        # 4. 梯度减仓（根据评分等级）
        if not reason and score is not None:
            reduce_plan = get_reduce_position_plan(score, shares, min_score=min_score)
            if reduce_plan["should_reduce"]:
                sell_shares = reduce_plan["reduce_shares"]
                reason = reduce_plan["reason"]
        
        if not reason:
            kept.append(h)
            continue

        fill_px = apply_fill_price(
            "sell", float(price), model=cost_model, params=fee_params
        )
        amount = round(sell_shares * fill_px, 2)
        fee_info = calc_trade_fees(
            "sell", amount, model=cost_model, params=fee_params
        )
        trade = annotate_trade(
            {
                "ts": _now_iso(),
                "side": "sell",
                "stock_code": code,
                "stock_name": h.get("stock_name") or quote.get("stock_name"),
                "shares": sell_shares,
                "price": round(fill_px, 4),
                "amount": amount,
                "pnl_pct": round(pnl_pct, 2),
                "origin": ORIGIN_STRATEGY,
                "note": f"纸面模拟卖出：{reason}",
            },
            fee_info,
        )
        paper.setdefault("trades", []).append(trade)
        trades.append(trade)
        cash = round(cash + float(fee_info["net_cash_delta"]), 2)

        # 如果是减仓（非清仓），保留剩余持仓
        if sell_shares < shares:
            h["shares"] = round(shares - sell_shares, 0)
            kept.append(h)

    paper["holdings"] = kept
    paper["cash"] = round(cash, 2)
    paper["updated_at"] = _now_iso()
    return trades
