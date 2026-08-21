"""纸面盯市与模拟成交（从 paper.py 拆出）。

对外仍从 ``core.paper`` 再导出，保持原有 import 路径。
"""


import logging

logger = logging.getLogger(__name__)
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.paper import (  # noqa: E402
    ORIGIN_LABELS,
    ORIGIN_MANUAL,
    ORIGIN_MIXED,
    ORIGIN_STRATEGY,
    _now_iso,
    _quote_price,
    merge_origin,
)
from core.paper_costs import (
    annotate_trade,
    apply_fill_price,
    calc_trade_fees,
    cost_params,
    resolve_cost_model,
)
from core.paper_sizing import _lot_shares


def _bars_and_source(code: str, *, limit: int, **kwargs):
    from core.data_service import bars_and_source

    return bars_and_source(code, limit=limit, reject_quote_fallback=True, **kwargs)


def _query_quote(code: str) -> dict:
    from core.data_service import get_quote

    return get_quote(str(code or "").strip())


def _batch_query_quotes(codes: List[str]) -> Dict[str, Any]:
    from core.data.service import get_default_service

    return get_default_service().batch_get_quotes(codes) or {}


def _quote_open(quote: dict) -> Optional[float]:
    """从行情 dict 取开盘价数值（优先 open_raw，否则解析格式化 open）。"""
    raw = quote.get("open_raw")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    s = quote.get("open")
    if s is None or s == "":
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _prev_session_equity(paper: dict, today_str: str) -> Optional[float]:
    """上一交易日账本净值：snapshots 里日期早于今天的最后一点。"""
    last_eq: Optional[float] = None
    for snap in paper.get("snapshots") or []:
        if not isinstance(snap, dict):
            continue
        ts = str(snap.get("ts") or snap.get("date") or "").strip()
        day = ts[:10]
        if len(day) != 10 or day >= today_str:
            continue
        try:
            eq = float(snap.get("equity"))
        except (TypeError, ValueError):
            continue
        if eq > 1e-6:
            last_eq = eq
    return last_eq


def mark_to_market(paper: dict) -> Dict[str, Any]:
    cash = float(paper.get("cash") or 0)
    holdings = paper.get("holdings") or []
    rows = []
    stock_value = 0.0
    now = datetime.now()
    codes = [str(h.get("stock_code") or "").strip() for h in holdings if h.get("stock_code")]
    quotes_by_code: Dict[str, Any] = {}
    if codes:
        try:
            quotes_by_code = _batch_query_quotes(codes)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in paper_exec.py", exc_info=True)
            quotes_by_code = {}
    for h in holdings:
        code = h.get("stock_code")
        quote = quotes_by_code.get(str(code or "").strip()) if code else None
        if not isinstance(quote, dict):
            quote = _query_quote(str(code)) if code else {}
        price = _quote_price(quote) if quote.get("success") else None
        open_px = _quote_open(quote) if quote.get("success") else None
        shares = float(h.get("shares") or 0)
        cost = float(h.get("cost") or 0)
        mv = (price or cost) * shares
        stock_value += mv
        pnl_pct = None
        if price and cost:
            pnl_pct = round((price / cost - 1.0) * 100.0, 2)
        change_pct = None
        if quote.get("success"):
            raw_ch = quote.get("change_raw")
            if raw_ch is None and quote.get("change") is not None:
                try:
                    change_pct = float(str(quote.get("change")).replace("%", "").strip())
                except (TypeError, ValueError):
                    change_pct = None
            else:
                try:
                    change_pct = float(raw_ch) if raw_ch is not None else None
                except (TypeError, ValueError):
                    change_pct = None
            if change_pct is not None:
                change_pct = round(change_pct, 2)
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
                "open": open_px,
                "currency": quote.get("currency", "CNY"),
                "unit": quote.get("unit", "元"),
                "market_value": round(mv, 2),
                "pnl_pct": pnl_pct,
                "change_pct": change_pct,
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

    # 当前回撤（从峰值到当前净值；会随反弹收窄，用于风控恢复判断）
    current_dd = 0.0
    if peak > 0 and equity < peak:
        current_dd = (peak - equity) / peak

    # E0：策略纯净净值 = 策略仓市值 + 按仓位占比分摊现金（混仓时归因近似）
    strat_bucket = origin_buckets.get(ORIGIN_STRATEGY) or {}
    strategy_stock_value = round(float(strat_bucket.get("market_value") or 0), 2)
    equity_strategy = None
    if strategy_stock_value > 0 and stock_value > 0:
        cash_attr = cash * (strategy_stock_value / stock_value)
        equity_strategy = round(strategy_stock_value + cash_attr, 2)

    # 今日浮动：
    # - 当日回零后：相对回零价（去掉昨收），与累计同起点
    # - 有上一交易日快照：相对昨收账本净值（含卖出/费用/跳空，可与累计衔接）
    # - 否则：按涨跌幅反推昨收，汇总 (现价−昨收)×股数（只含当前持仓）
    today_pnl: Optional[float] = None
    today_pnl_pct: Optional[float] = None
    today_pnl_basis = "prev_close"
    anchor = paper.get("pnl_anchor") if isinstance(paper.get("pnl_anchor"), dict) else None
    today_str = now.strftime("%Y-%m-%d")
    anchor_date = str((anchor or {}).get("date") or "")[:10]
    anchor_prices = (anchor or {}).get("prices") if isinstance(anchor, dict) else None
    use_reset_anchor = (
        bool(anchor)
        and anchor_date == today_str
        and isinstance(anchor_prices, dict)
        and bool(anchor_prices)
    )
    prev_nav = _prev_session_equity(paper, today_str)

    if not rows:
        today_pnl = 0.0
        today_pnl_pct = 0.0
        if use_reset_anchor:
            today_pnl_basis = "reset"
        elif prev_nav is not None:
            today_pnl = round(float(equity) - prev_nav, 2)
            today_pnl_pct = round(today_pnl / prev_nav * 100.0, 2) if prev_nav > 1e-6 else 0.0
            today_pnl_basis = "prev_nav"
    elif use_reset_anchor:
        day_sum = 0.0
        n_anchored = 0
        for row in rows:
            code = str(row.get("stock_code") or "").strip()
            px = row.get("price")
            sh = row.get("shares")
            if not code or px is None or sh is None:
                continue
            ap = anchor_prices.get(code)
            if ap is None:
                continue
            try:
                day_sum += (float(px) - float(ap)) * float(sh)
                n_anchored += 1
            except (TypeError, ValueError):
                continue
        if n_anchored > 0:
            today_pnl = round(day_sum, 2)
            today_pnl_basis = "reset"
            try:
                base = float((anchor or {}).get("equity") or 0)
            except (TypeError, ValueError):
                base = 0.0
            if base <= 1e-6:
                base = float(equity) - today_pnl
            if base > 1e-6:
                today_pnl_pct = round(today_pnl / base * 100.0, 2)
    elif prev_nav is not None:
        today_pnl = round(float(equity) - prev_nav, 2)
        today_pnl_pct = round(today_pnl / prev_nav * 100.0, 2)
        today_pnl_basis = "prev_nav"
    else:
        day_sum = 0.0
        n_chg = 0
        for row in rows:
            chg = row.get("change_pct")
            mv = row.get("market_value")
            if chg is None or mv is None:
                continue
            try:
                chg_f = float(chg)
                mv_f = float(mv)
            except (TypeError, ValueError):
                continue
            denom = 100.0 + chg_f
            if abs(denom) < 1e-9:
                continue
            # change% = (P-P0)/P0*100 → ΔMV = MV * chg / (100+chg)
            day_sum += mv_f * chg_f / denom
            n_chg += 1
        if n_chg > 0:
            today_pnl = round(day_sum, 2)
            base = float(equity) - today_pnl
            if base > 1e-6:
                today_pnl_pct = round(today_pnl / base * 100.0, 2)

    return {
        "cash": round(cash, 2),
        "stock_value": round(stock_value, 2),
        "equity": round(equity, 2),
        "equity_strategy": equity_strategy,
        "strategy_stock_value": strategy_stock_value,
        "initial_cash": initial,
        "total_pnl_pct": total_pnl_pct,
        "today_pnl": today_pnl,
        "today_pnl_pct": today_pnl_pct,
        "today_pnl_basis": today_pnl_basis,
        "holdings": rows,
        "position_count": len(rows),
        "origin_summary": origin_summary,
        "invested_pct": invested_pct,
        "max_drawdown_pct": round(max_dd * 100.0, 2),
        "current_drawdown_pct": round(current_dd * 100.0, 2),
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

    quote = _query_quote(code)
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

        quote = _query_quote(code)
        # R3 涨跌停/停牌卖出侧检查：跌停/停牌无法成交则跳过（保留持仓）
        try:
            from core.paper_rebalance import _sell_match_block_reason
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in paper_exec.py", exc_info=True)
            _sell_match_block_reason = None
        if _sell_match_block_reason is not None:
            block_reason = _sell_match_block_reason(code, quote if isinstance(quote, dict) else {})
        else:
            block_reason = None
        if block_reason:
            # 保留整笔持仓不卖出（与 rebalance 路径一致：跌停/停牌不模拟成交）
            paper.setdefault("operation_log", []).append({
                "ts": _now_iso(),
                "op": "manual_sell_skip",
                "stock_code": code,
                "reason": block_reason,
            })
            continue
        raw_price = _quote_price(quote) if quote.get("success") else None
        if not raw_price or raw_price <= 0:
            raw_price = float(h.get("cost") or 0)
        if not raw_price or raw_price <= 0:
            raise ValueError(f"{code} 无法定价")
        price = apply_fill_price("sell", raw_price, model=model, params=params)

        amount = round(sell_shares * price, 2)
        fee_info = calc_trade_fees("sell", amount, model=model, params=params)
        cost = float(h.get("cost") or 0)
        pnl_pct = (
            round((float(price) / cost - 1.0) * 100.0, 2) if cost > 0 else None
        )
        trade = annotate_trade(
            {
                "ts": _now_iso(),
                "side": "sell",
                "stock_code": code,
                "stock_name": h.get("stock_name") or quote.get("stock_name"),
                "shares": sell_shares,
                "price": round(price, 4),
                "amount": amount,
                "pnl_pct": pnl_pct,
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
    from core.signal.factors.adaptive import adaptive_thresholds, get_risk_controls
    from core.signal.factors.dynamic_position import get_full_position_plan
    from core.signal.factors.kelly import adaptive_position_sizing
    from core.signal.factors.technicals import check_technical_filters

    rules = paper.get("rules") or {}
    from core.signal.score_display import (
        resolve_buy_floor,
        score_gates_use_heuristic_bands,
    )

    # 生产仅 ŷ：不用 0–100 的 min_score/加仓分档；入选靠池排序与 scoring 门槛
    base_min_score = resolve_buy_floor(paper)
    base_sell_score = float("-inf")
    max_positions = int(rules.get("max_positions") or 5)
    position_pct = float(rules.get("position_pct") or 0.15)
    add_size_pct = float(rules.get("add_size_pct") or 0.5)  # 加仓比例50%
    cost_model = resolve_cost_model(paper)
    fee_params = cost_params(paper)

    # 自适应仓位配置
    use_adaptive_position = rules.get("use_adaptive_position", True)

    # 0–100 加减仓分档已退役（score_gates_use_heuristic_bands=False）
    use_adaptive_threshold = (
        bool(rules.get("use_adaptive_threshold", True))
        and score_gates_use_heuristic_bands()
    )

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

        quote = _query_quote(code)
        price = _quote_price(quote)
        if not price or price <= 0:
            item["skip_reason"] = "行情获取失败，无法加仓"
            continue

        if code in held_codes:
            # 已有持仓：动态仓位管理；0–100 分档已退役
            holding = held_codes[code]
            current_shares = float(holding.get("shares") or 0)
            score_val = float(score)

            if not score_gates_use_heuristic_bands():
                item["skip_reason"] = "收益分模式：持仓加减仓不按 0–100 分档（靠调仓簿）"
                continue

            # 技术指标检查（提前获取）
            technical_info = None
            if use_technical_filter:
                try:
                    bars, _ = _bars_and_source(code, limit=60)
                    if bars:
                        tech_passed, tech_info = check_technical_filters(bars, min_technical_score)
                        technical_info = tech_info
                except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                    logger.debug("catch except Exception: in paper_exec.py", exc_info=True)
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

                new_bars, _ = _bars_and_source(code, limit=30)
                existing_bars = {}
                for h_code in held_codes:
                    try:
                        bars, _ = _bars_and_source(h_code, limit=30)
                        if bars:
                            existing_bars[h_code] = bars
                    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                        logger.debug("catch except Exception: in paper_exec.py", exc_info=True)
                        pass

                if new_bars and existing_bars:
                    temp_bars = {code: new_bars, **existing_bars}
                    stock_to_replace = suggest_replacement(
                        code, temp_bars, max_correlation=0.8
                    )
                    if stock_to_replace:
                        continue
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in paper_exec.py", exc_info=True)
                pass

            # 技术指标过滤
            if use_technical_filter:
                try:
                    bars, _ = _bars_and_source(code, limit=60)
                    if bars:
                        tech_passed, tech_info = check_technical_filters(bars, min_technical_score)
                        if not tech_passed:
                            continue
                        item["technical_info"] = tech_info
                except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                    logger.debug("catch except Exception: in paper_exec.py", exc_info=True)
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

    from core.signal.factors.dynamic_position import get_reduce_position_plan
    from core.signal.factors.risk import (
        should_stop_loss,
        update_peak_price,
    )
    from core.signal.score_display import (
        resolve_buy_floor,
        score_gates_use_heuristic_bands,
    )

    rules = paper.get("rules") or {}
    stop_loss_pnl = float(rules.get("stop_loss_pnl") or -8.0)
    max_hold_days = int(rules.get("max_hold_days") or 5)
    min_score = resolve_buy_floor(paper, heuristic_default=60.0)
    use_score_reduce = score_gates_use_heuristic_bands()

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

        quote = _query_quote(code)
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
            bars, _ = _bars_and_source(code, limit=30)
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
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in paper_exec.py", exc_info=True)
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

        # 4. 梯度减仓（根据评分等级；收益分模式下跳过 0–100 分档）
        if not reason and score is not None and use_score_reduce:
            reduce_plan = get_reduce_position_plan(score, shares, min_score=min_score)
            if reduce_plan["should_reduce"]:
                sell_shares = reduce_plan["reduce_shares"]
                reason = reduce_plan["reason"]

        if not reason:
            kept.append(h)
            continue

        # R3 涨跌停/停牌卖出侧检查：跌停/停牌无法成交则跳过（保留持仓）
        try:
            from core.paper_rebalance import _sell_match_block_reason
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in paper_exec.py", exc_info=True)
            _sell_match_block_reason = None
        if _sell_match_block_reason is not None:
            block_reason = _sell_match_block_reason(code, quote if isinstance(quote, dict) else {})
        else:
            block_reason = None
        if block_reason:
            paper.setdefault("operation_log", []).append({
                "ts": _now_iso(),
                "op": "simulate_sells_skip",
                "stock_code": code,
                "reason": block_reason,
                "note": f"纸面模拟卖出被跳过：{reason}",
            })
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
