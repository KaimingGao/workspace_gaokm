"""做 T 成交成本：与纸面调仓同源（``core.paper.costs`` / CostPort）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from core.paper.costs import (
    COST_SIMPLE_CN,
    COST_ZERO,
    annotate_trade,
    apply_fill_price,
    calc_trade_fees,
    cost_params,
    resolve_cost_model,
)


def t0_fee_side(side: str) -> str:
    s = str(side or "").strip().lower()
    return "sell" if s.endswith("sell") else "buy"


def resolve_t0_cost_context(
    *,
    paper: Optional[dict] = None,
    cost_config: Optional[dict] = None,
) -> Tuple[str, Dict[str, float]]:
    """解析做 T 成本上下文：纸面优先，其次回测 ``cost_config``，否则 zero。"""
    if paper is not None:
        return resolve_cost_model(paper), cost_params(paper)

    if cost_config:
        p = cost_params(None)
        p["commission_rate"] = float(cost_config.get("commission_bps", 2.5)) / 10000.0
        p["min_commission"] = float(cost_config.get("commission_min", 5.0))
        p["stamp_duty_sell"] = float(cost_config.get("stamp_duty_bps_sell", 5.0)) / 10000.0
        if "base_slippage_bps" in cost_config:
            p["slippage_bps"] = float(cost_config.get("base_slippage_bps") or 0)
        comm_bps = float(cost_config.get("commission_bps") or 0)
        stamp_bps = float(cost_config.get("stamp_duty_bps_sell") or 0)
        slip_bps = float(p.get("slippage_bps") or 0)
        if comm_bps <= 0 and stamp_bps <= 0 and slip_bps <= 0:
            return COST_ZERO, cost_params(None)
        return COST_SIMPLE_CN, p

    return COST_ZERO, cost_params(None)


def apply_t0_leg_costs(
    trade: dict,
    *,
    cost_model: str,
    cost_params: Dict[str, float],
) -> dict:
    """按纸面模型重算成交价/成交额/费用，写入 ``net_cash_delta``。"""
    side = t0_fee_side(trade.get("side"))
    shares = float(trade.get("shares") or 0)
    raw_px = float(trade.get("price") or 0)
    fill_px = apply_fill_price(side, raw_px, model=cost_model, params=cost_params)
    amount = round(shares * fill_px, 2)
    fee_info = calc_trade_fees(side, amount, model=cost_model, params=cost_params)
    row = dict(trade)
    row["price"] = round(fill_px, 4)
    row["amount"] = amount
    row["net_cash_delta"] = fee_info["net_cash_delta"]
    return annotate_trade(row, fee_info)


def append_t0_leg(
    trades: List[dict],
    *,
    cost_model: str,
    cost_params: Dict[str, float],
    side: str,
    stock_code: str,
    shares: float,
    price: float,
    trigger: float,
    at: Any,
    leg_kind: str,
    note: str,
) -> float:
    """追加一笔做 T leg，返回对现金的净影响。"""
    row = apply_t0_leg_costs(
        {
            "side": side,
            "stock_code": stock_code,
            "shares": shares,
            "price": round(float(price), 4),
            "amount": round(float(shares) * float(price), 2),
            "trigger": round(float(trigger), 4),
            "at": at,
            "leg_kind": leg_kind,
            "note": note,
        },
        cost_model=cost_model,
        cost_params=cost_params,
    )
    trades.append(row)
    return float(row.get("net_cash_delta") or 0)


def t0_leg_cash_delta(trade: dict) -> float:
    """读取 leg 对现金的净影响（兼容旧记录）。"""
    if trade.get("net_cash_delta") is not None:
        return float(trade.get("net_cash_delta") or 0)
    side = t0_fee_side(trade.get("side"))
    amt = float(trade.get("amount") or 0)
    return amt if side == "sell" else -amt


def t0_fees_total(trades: List[dict]) -> float:
    return round(sum(float(t.get("fees") or 0) for t in trades or []), 2)


def t0_pnl_from_trades(trades: List[dict]) -> float:
    """已完成往返的已实现 PnL = 各 leg 净现金之和。"""
    return round(sum(t0_leg_cash_delta(t) for t in trades or []), 2)
