"""调仓换手预算与资金影响摘要。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def clip_shares_to_turnover_budget(
    *,
    shares: int,
    fill_px: float,
    buy_amt_so_far: float,
    buy_budget_amt: float,
    sell_amt: float,
    equity_before: float,
    max_turnover_pct: float,
) -> int:
    """按剩余换手买入预算 + 双边总上限，手数向下取整到 100 股。

    半仓重试仍可能 > 剩余预算；先裁剪可成交部分，避免预算 residual 被永久跳过。
    """
    sh = int(shares or 0)
    px = float(fill_px or 0.0)
    eq = float(equity_before or 0.0)
    if sh <= 0 or px <= 0 or eq <= 0:
        return 0
    rem_buy = max(0.0, float(buy_budget_amt) - float(buy_amt_so_far))
    # 双边：(sell + buy_after)/2/equity*100 ≤ max_to
    # → buy_after ≤ 2*(max_to/100)*equity - sell
    rem_total = max(
        0.0,
        2.0 * (float(max_turnover_pct) / 100.0) * eq
        - float(sell_amt)
        - float(buy_amt_so_far),
    )
    rem = min(rem_buy, rem_total)
    if rem <= 0:
        return 0
    max_sh = int(rem // px // 100) * 100
    return max(0, min(sh, max_sh))


def resolve_buy_turnover_budget(
    *,
    sell_trades: List[dict],
    buy_trades: List[dict],
    equity_before: float,
    max_turnover_pct: float,
) -> Tuple[float, float, float]:
    """买侧换手预算。返回 (sell_amt, buy_amt_so_far, buy_budget_amt)。

    单边 50% 给买；卖未用可溢出给买（上限再 50%）。卖腿本身不受此帽。
    """
    sell_amt = sum(float(t.get("amount") or 0) for t in (sell_trades or []))
    buy_amt_so_far = sum(float(t.get("amount") or 0) for t in (buy_trades or []))
    eq = float(equity_before or 0.0)
    _max_to = float(max_turnover_pct)
    _single_side_amt = (_max_to / 2.0) / 100.0 * eq
    _sell_excess = max(0.0, _single_side_amt - sell_amt)
    buy_budget_amt = _single_side_amt + min(_sell_excess, _single_side_amt)
    return sell_amt, buy_amt_so_far, buy_budget_amt



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
    min_cash_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """资金影响 + 换手摘要（预演/落账共用）。"""
    from core.paper.rebalance.cash_reserve import (
        cash_floor,
        resolve_min_cash_pct,
        spendable_cash,
    )

    turn = compute_turnover_stats(
        sell_trades,
        buy_trades,
        equity_before=equity_before,
        max_turnover_pct=max_turnover_pct,
    )
    cash_after = float((summary or {}).get("cash") or 0)
    eq_after = float((summary or {}).get("equity") or equity_before or 0)
    reserve_pct = (
        float(min_cash_pct)
        if min_cash_pct is not None
        else resolve_min_cash_pct(None)
    )
    floor_after = cash_floor(eq_after, reserve_pct)
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
        "min_cash_pct": round(reserve_pct * 100.0, 2),
        "cash_floor_after": round(floor_after, 2),
        "spendable_after": round(
            spendable_cash(cash_after, eq_after, reserve_pct), 2
        ),
        "cash_reserve_ok": cash_after + 1e-6 >= floor_after,
    }
