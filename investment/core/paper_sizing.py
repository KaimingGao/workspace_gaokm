"""模拟建仓定量：金额 / 仓位% / 股数预演与执行。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional

from core.paper_costs import (
    annotate_trade,
    apply_fill_price,
    calc_trade_fees,
    resolve_cost_model,
    cost_params,
)
from core.ports.market import quote_price

# origin constants imported lazily-safe from paper facade via circular-avoid:
# define locally matching paper.py
ORIGIN_MANUAL = "manual"


def _now_iso():
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


DEFAULT_SYNC_LOT_SHARES = 200
DEFAULT_SYNC_AMOUNT = 20_000.0  # 默认按金额建仓（元/只）


def _resolve_lot_for_code(
    code: str,
    *,
    price: float,
    lot_shares: int,
    shares_by_code: Optional[Dict[str, Any]] = None,
    amount_by_code: Optional[Dict[str, Any]] = None,
    amount_per_code: Optional[float] = None,
    position_pct: Optional[float] = None,
    sizing_base: float = 0.0,
) -> tuple:
    """解析单只股数。优先序：shares_by_code > amount_by_code > amount_per_code > position_pct > lot_shares。

    返回 (shares, sizing_mode, budget_hint)。
    """
    if shares_by_code:
        raw = None
        if code in shares_by_code:
            raw = shares_by_code[code]
        else:
            for k, v in shares_by_code.items():
                if str(k).strip() == code:
                    raw = v
                    break
        if raw is not None:
            return _lot_shares(raw), "shares", None

    budget = None
    mode = "shares"
    if amount_by_code:
        raw_amt = None
        if code in amount_by_code:
            raw_amt = amount_by_code[code]
        else:
            for k, v in amount_by_code.items():
                if str(k).strip() == code:
                    raw_amt = v
                    break
        if raw_amt is not None:
            try:
                budget = float(raw_amt)
            except (TypeError, ValueError):
                budget = None
            mode = "amount"

    if budget is None and amount_per_code is not None:
        try:
            budget = float(amount_per_code)
        except (TypeError, ValueError):
            budget = None
        else:
            mode = "amount"

    if budget is None and position_pct is not None:
        try:
            pct = float(position_pct)
        except (TypeError, ValueError):
            pct = 0.0
        if pct > 0 and sizing_base > 0:
            budget = sizing_base * pct
            mode = "pct"

    if budget is not None:
        if budget <= 0 or price <= 0:
            return 0, mode, budget
        return _lot_shares(budget // price), mode, budget

    return _lot_shares(lot_shares), "shares", None


def plan_buy_codes(
    paper: dict,
    codes: List[str],
    *,
    lot_shares: int = DEFAULT_SYNC_LOT_SHARES,
    shares_by_code: Optional[Dict[str, Any]] = None,
    amount_per_code: Optional[float] = None,
    amount_by_code: Optional[Dict[str, Any]] = None,
    position_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """加入纸面预演：算出每只买多少、花多少、剩多少，不改账户。

    定量优先序（专业默认按金额）：
    1. shares_by_code / amount_by_code（按只覆盖）
    2. amount_per_code（每只固定金额）
    3. position_pct（相对可用现金比例）
    4. lot_shares（固定股数，兼容旧调用）

    与 buy_codes_direct 共用同一套逻辑，预览与实际执行不会漂移。
    """
    default_shares = _lot_shares(lot_shares)
    has_amount = (
        amount_per_code is not None
        or bool(amount_by_code)
        or position_pct is not None
    )
    if default_shares <= 0 and not shares_by_code and not has_amount:
        raise ValueError("请指定金额、仓位比例或股数（100 的整数倍）")
    if position_pct is not None:
        try:
            pct_val = float(position_pct)
        except (TypeError, ValueError) as e:
            raise ValueError("仓位比例无效") from e
        if pct_val <= 0 or pct_val > 1:
            raise ValueError("仓位比例须在 (0, 1] 之间，例如 0.1 表示 10%")

    held_codes = {str(h.get("stock_code")) for h in (paper.get("holdings") or [])}
    wanted: List[str] = []
    skipped: List[dict] = []
    for raw in codes or []:
        code = str(raw or "").strip()
        if not code:
            continue
        if code in held_codes:
            skipped.append({"stock_code": code, "reason": "已持仓"})
            continue
        if code not in wanted:
            wanted.append(code)

    cash = float(paper.get("cash") or 0)
    if wanted and cash <= 0:
        for code in wanted:
            skipped.append({"stock_code": code, "reason": "现金不足"})
        wanted = []

    quoted: List[tuple] = []
    for code in wanted:
        from core.data_service import get_quote

        quote = get_quote(code)
        price = quote_price(quote) if quote.get("success") else None
        if not price or price <= 0:
            skipped.append(
                {
                    "stock_code": code,
                    "reason": quote.get("error") or "无法取行情",
                }
            )
            continue
        quoted.append((code, float(price), quote))

    # 便宜的先买，提高多只都能进仓的概率
    quoted.sort(key=lambda x: x[1])
    items: List[dict] = []
    remain = cash
    sizing_mode = "shares"
    model = resolve_cost_model(paper)
    params = cost_params(paper)
    total_fees = 0.0
    for code, price, quote in quoted:
        fill = apply_fill_price("buy", price, model=model, params=params)
        shares, mode, budget = _resolve_lot_for_code(
            code,
            price=fill,
            lot_shares=default_shares or lot_shares,
            shares_by_code=shares_by_code,
            amount_by_code=amount_by_code,
            amount_per_code=amount_per_code,
            position_pct=position_pct,
            sizing_base=remain if position_pct is not None else cash,
        )
        sizing_mode = mode
        if shares <= 0:
            reason = "按现价不足一手（100 股）"
            if budget is not None:
                reason = f"金额约 {round(budget, 2)} 不足一手"
            skipped.append(
                {
                    "stock_code": code,
                    "stock_name": quote.get("stock_name"),
                    "reason": reason,
                }
            )
            continue
        amount = round(fill * shares, 2)
        fee_info = calc_trade_fees("buy", amount, model=model, params=params)
        need = amount + float(fee_info.get("fees") or 0)
        if remain < need:
            skipped.append(
                {
                    "stock_code": code,
                    "stock_name": quote.get("stock_name"),
                    "reason": f"现金不足（需约 {need}）",
                }
            )
            continue
        items.append(
            {
                "stock_code": code,
                "stock_name": quote.get("stock_name"),
                "price": round(fill, 4),
                "shares": shares,
                "amount": amount,
                "fees": fee_info.get("fees"),
                "sizing_mode": mode,
            }
        )
        remain -= need
        total_fees += float(fee_info.get("fees") or 0)

    total = round(sum(i["amount"] for i in items), 2)
    total_fees = round(total_fees, 2)
    return {
        "lot_shares": default_shares or DEFAULT_SYNC_LOT_SHARES,
        "amount_per_code": float(amount_per_code) if amount_per_code is not None else None,
        "position_pct": float(position_pct) if position_pct is not None else None,
        "sizing_mode": sizing_mode,
        "shares_by_code": {
            str(k).strip(): _lot_shares(v)
            for k, v in (shares_by_code or {}).items()
            if str(k).strip() and _lot_shares(v) > 0
        }
        or None,
        "amount_by_code": {
            str(k).strip(): round(float(v), 2)
            for k, v in (amount_by_code or {}).items()
            if str(k).strip()
        }
        or None,
        "items": items,
        "skipped": skipped,
        "buy_count": len(items),
        "skip_count": len(skipped),
        "total_amount": total,
        "total_fees": total_fees,
        "cash": round(cash, 2),
        "cash_after": round(cash - total - total_fees, 2),
        "cost_model": model,
    }


def buy_codes_direct(
    paper: dict,
    codes: List[str],
    *,
    lot_shares: int = DEFAULT_SYNC_LOT_SHARES,
    shares_by_code: Optional[Dict[str, Any]] = None,
    amount_per_code: Optional[float] = None,
    amount_by_code: Optional[Dict[str, Any]] = None,
    position_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """不按分数筛选，按现价假买进（非实盘）。已持仓跳过。

    观察建仓：按金额/仓位%/股数定量，不把现金买满。
    返回 {"trades": [...], "skipped": [...], "plan": {...}}。
    """
    plan = plan_buy_codes(
        paper,
        codes,
        lot_shares=lot_shares,
        shares_by_code=shares_by_code,
        amount_per_code=amount_per_code,
        amount_by_code=amount_by_code,
        position_pct=position_pct,
    )
    items = plan.get("items") or []
    skipped = plan.get("skipped") or []
    if not items:
        return {"trades": [], "skipped": skipped, "plan": plan}

    model = resolve_cost_model(paper)
    params = cost_params(paper)
    holdings = paper.get("holdings") or []
    cash = float(paper.get("cash") or 0)
    trades: List[dict] = []
    for it in items:
        amount = float(it["amount"])
        fee_info = calc_trade_fees("buy", amount, model=model, params=params)
        trade = annotate_trade(
            {
                "ts": _now_iso(),
                "side": "buy",
                "stock_code": it["stock_code"],
                "stock_name": it.get("stock_name"),
                "shares": it["shares"],
                "price": it["price"],
                "amount": amount,
                "origin": ORIGIN_MANUAL,
                "note": f"观察建仓（{it['shares']} 股）",
            },
            fee_info,
        )
        paper.setdefault("trades", []).append(trade)
        trades.append(trade)
        holdings.append(
            {
                "stock_code": it["stock_code"],
                "stock_name": trade["stock_name"],
                "shares": it["shares"],
                "cost": it["price"],
                "bought_at": trade["ts"],
                "origin": ORIGIN_MANUAL,
            }
        )
        cash += float(fee_info["net_cash_delta"])

    paper["holdings"] = holdings
    paper["cash"] = round(cash, 2)
    paper["updated_at"] = _now_iso()
    return {"trades": trades, "skipped": skipped, "plan": plan}


def _lot_shares(raw: float) -> int:
    """A 股一手 100 股（向下取整）。"""
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        return 0
    return max(0, (n // 100) * 100)

