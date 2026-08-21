"""模拟成交成本模型。

- zero：现价成交，零佣金/滑点/印花税（默认，练手）
- simple_cn：A 股简化；费率来自 CostPort（`core.backtest.cost_port`）
"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, Optional

from core.backtest.cost_port import COST_MODELS, paper_simple_cn_params

COST_ZERO = "zero"
COST_SIMPLE_CN = "simple_cn"
# 与 CostPort.COST_MODELS 保持同一元组语义
assert COST_MODELS == (COST_ZERO, COST_SIMPLE_CN)


def resolve_cost_model(paper: Optional[dict] = None) -> str:
    raw = str((paper or {}).get("cost_model") or COST_ZERO).strip().lower()
    return raw if raw in COST_MODELS else COST_ZERO


def cost_params(paper: Optional[dict] = None) -> Dict[str, float]:
    cfg = paper_simple_cn_params()
    extra = (paper or {}).get("cost_params") or {}
    for k, v in extra.items():
        if k in cfg:
            try:
                cfg[k] = float(v)
            except (TypeError, ValueError):
                pass
    return cfg


def apply_fill_price(side: str, price: float, *, model: str, params: Dict[str, float]) -> float:
    """按滑点调整成交价：买贵卖便宜。"""
    if model == COST_ZERO or price <= 0:
        return float(price)
    bps = float(params.get("slippage_bps") or 0)
    if bps <= 0:
        return float(price)
    slip = bps / 10000.0
    if side == "buy":
        return round(price * (1.0 + slip), 4)
    return round(price * (1.0 - slip), 4)


def calc_trade_fees(
    side: str,
    amount: float,
    *,
    model: str,
    params: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """返回 fees / commission / stamp_duty / net_cash_delta（对现金的影响，买入为负）。"""
    amt = float(amount or 0)
    model = model if model in COST_MODELS else COST_ZERO
    if model == COST_ZERO or amt <= 0:
        delta = -amt if side == "buy" else amt
        return {
            "cost_model": COST_ZERO,
            "commission": 0.0,
            "stamp_duty": 0.0,
            "fees": 0.0,
            "gross_amount": round(amt, 2),
            "net_cash_delta": round(delta, 2),
        }

    p = params or paper_simple_cn_params()
    rate = float(p.get("commission_rate") or 0)
    min_c = float(p.get("min_commission") or 0)
    commission = max(amt * rate, min_c) if rate > 0 or min_c > 0 else 0.0
    stamp = 0.0
    if side == "sell":
        stamp = amt * float(p.get("stamp_duty_sell") or 0)
    fees = round(commission + stamp, 2)
    if side == "buy":
        delta = -(amt + fees)
    else:
        delta = amt - fees
    return {
        "cost_model": COST_SIMPLE_CN,
        "commission": round(commission, 2),
        "stamp_duty": round(stamp, 2),
        "fees": fees,
        "gross_amount": round(amt, 2),
        "net_cash_delta": round(delta, 2),
    }


def annotate_trade(trade: dict, fee_info: Dict[str, Any]) -> dict:
    trade = dict(trade)
    trade["cost_model"] = fee_info.get("cost_model")
    trade["fees"] = fee_info.get("fees")
    trade["commission"] = fee_info.get("commission")
    trade["stamp_duty"] = fee_info.get("stamp_duty")
    return trade


def fee_fields_from_trade(trade: Optional[dict]) -> Dict[str, Any]:
    """从成交记录抽出可展示的费用字段（含 strategy 路径的 transaction_costs）。"""
    if not isinstance(trade, dict):
        return {}
    out: Dict[str, Any] = {}
    tc = trade.get("transaction_costs")
    if isinstance(tc, dict):
        if tc.get("commission") is not None:
            out["commission"] = tc.get("commission")
        stamp = tc.get("stamp_duty")
        if stamp is None:
            stamp = tc.get("stamp_tax")
        if stamp is not None:
            out["stamp_duty"] = stamp
        if tc.get("total_cost") is not None:
            out["fees"] = tc.get("total_cost")
    for k in ("commission", "stamp_duty", "fees", "cost_model"):
        if trade.get(k) is not None:
            out[k] = trade.get(k)
    if out.get("fees") is None and (
        out.get("commission") is not None or out.get("stamp_duty") is not None
    ):
        try:
            out["fees"] = round(
                float(out.get("commission") or 0) + float(out.get("stamp_duty") or 0), 2
            )
        except (TypeError, ValueError):
            pass
    return out


def pnl_fields_from_trade(trade: Optional[dict]) -> Dict[str, Any]:
    """卖出腿已实现收益字段（相对成本价 %；可选绝对额）。"""
    if not isinstance(trade, dict):
        return {}
    out: Dict[str, Any] = {}
    if trade.get("pnl_pct") is not None:
        try:
            out["pnl_pct"] = round(float(trade["pnl_pct"]), 2)
        except (TypeError, ValueError):
            pass
    if trade.get("pnl") is not None:
        try:
            out["pnl"] = round(float(trade["pnl"]), 2)
        except (TypeError, ValueError):
            pass
    return out


def _match_trade_for_log(
    pool: list,
    used: set,
    *,
    code: str,
    want_side: str,
    shares: Optional[float],
) -> Optional[tuple]:
    """返回 (index, trade) 或 None。"""
    if not code:
        return None
    for i, trade in enumerate(pool):
        if i in used:
            continue
        if str(trade.get("stock_code") or "").strip() != code:
            continue
        side = str(trade.get("side") or "").strip().lower()
        if side and side != want_side:
            continue
        if shares is not None and trade.get("shares") is not None:
            try:
                if abs(float(trade.get("shares")) - shares) > 1e-6:
                    continue
            except (TypeError, ValueError):
                continue
        return i, trade
    return None


def enrich_operation_log_with_trade_fees(
    logs: Optional[list],
    trades: Optional[list],
    *,
    cost_model: Optional[str] = None,
) -> list:
    """把 trades 上的费用/卖出收益补进 operation_log.meta，供交易记录页展示。

    策略旧成交若未落盘费用：在 simple_cn 下按金额估显（fees_estimated=True），
    不改历史现金，仅方便对照。卖出收益（pnl_pct）从对应 trades 回填。
    """
    entries = [dict(x) if isinstance(x, dict) else x for x in (logs or [])]
    pool = [t for t in (trades or []) if isinstance(t, dict)]
    used: set = set()
    model = resolve_cost_model({"cost_model": cost_model} if cost_model else None)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        meta = dict(entry.get("meta") or {})
        op = str(entry.get("type") or "")
        if op not in ("buy", "sell", "sync_paper"):
            entry["meta"] = meta
            continue
        code = str(meta.get("stock_code") or "").strip()
        want_side = "sell" if op == "sell" else "buy"
        try:
            shares = float(meta["shares"]) if meta.get("shares") is not None else None
        except (TypeError, ValueError):
            shares = None
        need_fees = meta.get("fees") is None and meta.get("commission") is None
        need_pnl = op == "sell" and meta.get("pnl_pct") is None and meta.get("pnl") is None
        matched = _match_trade_for_log(
            pool, used, code=code, want_side=want_side, shares=shares
        )
        fees_from_trade = False
        if matched is not None:
            i, trade = matched
            consumed = False
            if need_pnl:
                pnl = pnl_fields_from_trade(trade)
                if pnl:
                    meta.update(pnl)
                    consumed = True
            if need_fees:
                fees = fee_fields_from_trade(trade)
                if fees:
                    meta.update(fees)
                    fees_from_trade = True
                    consumed = True
                else:
                    try:
                        amt = float(trade.get("amount") or 0)
                    except (TypeError, ValueError):
                        amt = 0.0
                    if amt > 0 and model == COST_SIMPLE_CN:
                        est = calc_trade_fees(want_side, amt, model=model)
                        meta.update(
                            {
                                "commission": est.get("commission"),
                                "stamp_duty": est.get("stamp_duty"),
                                "fees": est.get("fees"),
                                "cost_model": est.get("cost_model"),
                                "fees_estimated": True,
                            }
                        )
                        fees_from_trade = True
                        consumed = True
            if consumed:
                used.add(i)
        if need_fees and not fees_from_trade:
            try:
                amt = float(meta.get("amount") or 0)
            except (TypeError, ValueError):
                amt = 0.0
            if amt > 0 and model == COST_SIMPLE_CN:
                est = calc_trade_fees(want_side, amt, model=model)
                meta.update(
                    {
                        "commission": est.get("commission"),
                        "stamp_duty": est.get("stamp_duty"),
                        "fees": est.get("fees"),
                        "cost_model": est.get("cost_model"),
                        "fees_estimated": True,
                    }
                )
        entry["meta"] = meta
    return entries
