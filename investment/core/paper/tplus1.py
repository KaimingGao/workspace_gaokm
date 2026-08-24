"""A 股 T+1 结算锁：纸面持仓按买入批次 FIFO 可卖。

T 日买入的股份，到下一交易日才可卖。这是结算约束，不是策略偏好。
无批次的旧持仓视为开盘前底仓（当日可卖），避免冻结历史账本。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

TPLUS1_LOCK_REASON = "T+1锁定（当日买入不可卖）"


def _date_of(raw: Any) -> str:
    s = str(raw or "").strip().replace("Z", "")
    return s[:10] if len(s) >= 10 and s[4] == "-" and s[7] == "-" else ""


def session_date(now: Optional[datetime] = None) -> str:
    """当前会话交易日（上海时区；非交易日回退到最近已过交易日）。"""
    from core.market.calendar import resolve_session_date
    from core.signal.session_pit import shanghai_now

    return resolve_session_date(now=shanghai_now(now))


def _session_of(as_of: Optional[str] = None) -> str:
    """把日历日归一成交易会话日，避免周末 as_of 把旧仓误锁到下周一。"""
    d = _date_of(as_of)
    if not d:
        return session_date()
    from core.market.calendar import is_trading_day, prev_trading_day

    if is_trading_day(d):
        return d
    return prev_trading_day(d) or d


def _sum_lots(lots: List[dict]) -> float:
    total = 0.0
    for lot in lots:
        try:
            total += float(lot.get("shares") or 0)
        except (TypeError, ValueError):
            continue
    return total


def _refresh_bought_at(holding: dict) -> None:
    lots = holding.get("lots") if isinstance(holding.get("lots"), list) else []
    earliest = ""
    for lot in lots:
        ts = str(lot.get("bought_at") or "")
        if ts and (not earliest or ts < earliest):
            earliest = ts
    if earliest:
        holding["bought_at"] = earliest
    elif lots:
        holding["bought_at"] = str(lots[0].get("bought_date") or holding.get("bought_at") or "")


def _lot_sellable(lot: dict, as_of: str) -> bool:
    bought = _date_of(lot.get("bought_date") or lot.get("bought_at"))
    as_of = _session_of(as_of)
    if not bought:
        return True
    if as_of <= bought:
        return False
    from core.market.calendar import next_trading_day

    nxt = next_trading_day(bought)
    if not nxt:
        return as_of > bought
    return as_of >= nxt


def ensure_lots(holding: dict, *, as_of: Optional[str] = None) -> List[dict]:
    """保证持仓有 lots。缺批次时用 bought_at 迁一笔；再缺则视为已过 T+1 的旧仓。"""
    if not isinstance(holding, dict):
        return []
    lots = holding.get("lots")
    shares = float(holding.get("shares") or 0)
    if isinstance(lots, list) and lots:
        holding["shares"] = _sum_lots(lots)
        _refresh_bought_at(holding)
        return lots

    as_of = _session_of(as_of)
    bought_at = str(holding.get("bought_at") or "")
    bought_date = _date_of(bought_at)
    if not bought_date:
        from core.market.calendar import prev_trading_day

        bought_date = prev_trading_day(as_of) or as_of
    new_lots: List[dict] = []
    if shares > 1e-9:
        new_lots.append(
            {
                "shares": shares,
                "bought_at": bought_at or bought_date,
                "bought_date": bought_date,
            }
        )
    holding["lots"] = new_lots
    _refresh_bought_at(holding)
    return new_lots


def sellable_shares(holding: dict, *, as_of: Optional[str] = None) -> float:
    as_of = _session_of(as_of)
    lots = ensure_lots(holding, as_of=as_of)
    total = 0.0
    for lot in lots:
        if _lot_sellable(lot, as_of):
            total += float(lot.get("shares") or 0)
    return total


def locked_shares(holding: dict, *, as_of: Optional[str] = None) -> float:
    as_of = _session_of(as_of)
    ensure_lots(holding, as_of=as_of)
    total = float(holding.get("shares") or 0)
    return max(0.0, total - sellable_shares(holding, as_of=as_of))


def is_fully_sellable(holding: dict, *, as_of: Optional[str] = None) -> bool:
    shares = float(holding.get("shares") or 0)
    if shares <= 1e-9:
        return False
    return sellable_shares(holding, as_of=as_of) + 1e-9 >= shares


def clip_sell_shares(
    holding: dict,
    want: float,
    *,
    as_of: Optional[str] = None,
) -> Tuple[float, Dict[str, Any]]:
    """把想卖股数裁到当日可卖额度。"""
    as_of = _session_of(as_of)
    ensure_lots(holding, as_of=as_of)
    sellable = sellable_shares(holding, as_of=as_of)
    locked = locked_shares(holding, as_of=as_of)
    try:
        want_f = float(want)
    except (TypeError, ValueError):
        want_f = 0.0
    qty = min(max(0.0, want_f), sellable)
    return qty, {
        "sellable": sellable,
        "locked": locked,
        "clipped": qty + 1e-9 < want_f,
        "reason": TPLUS1_LOCK_REASON if qty <= 1e-9 and want_f > 1e-9 else None,
    }


def add_buy_lot(
    holding: dict,
    qty: float,
    *,
    ts: str,
    as_of: Optional[str] = None,
) -> None:
    """追加一笔买入批次，并回写 shares / bought_at。"""
    try:
        qty_f = float(qty)
    except (TypeError, ValueError):
        return
    if qty_f <= 1e-9:
        return
    as_of = _session_of(as_of or _date_of(ts))
    ensure_lots(holding, as_of=as_of)
    lots: List[dict] = holding["lots"]
    for lot in lots:
        if str(lot.get("bought_date") or "") == as_of:
            lot["shares"] = float(lot.get("shares") or 0) + qty_f
            if not lot.get("bought_at"):
                lot["bought_at"] = ts
            holding["shares"] = _sum_lots(lots)
            _refresh_bought_at(holding)
            return
    lots.append({"shares": qty_f, "bought_at": ts, "bought_date": as_of})
    holding["shares"] = _sum_lots(lots)
    _refresh_bought_at(holding)


def stamp_new_holding(holding: dict, *, ts: str, as_of: Optional[str] = None) -> None:
    """新开仓：按当前 shares 生成一笔当日批次。"""
    as_of = _session_of(as_of or _date_of(ts))
    holding["bought_at"] = ts
    holding["lots"] = []
    qty = float(holding.get("shares") or 0)
    holding["shares"] = 0
    add_buy_lot(holding, qty, ts=ts, as_of=as_of)


def consume_sell_lots(
    holding: dict,
    qty: float,
    *,
    as_of: Optional[str] = None,
) -> float:
    """FIFO 扣可卖批次。返回实际扣减股数。"""
    as_of = _session_of(as_of)
    ensure_lots(holding, as_of=as_of)
    try:
        remaining = float(qty)
    except (TypeError, ValueError):
        remaining = 0.0
    if remaining <= 1e-9:
        return 0.0
    new_lots: List[dict] = []
    sold = 0.0
    for lot in holding.get("lots") or []:
        lot_sh = float(lot.get("shares") or 0)
        if lot_sh <= 1e-9:
            continue
        if remaining <= 1e-9 or not _lot_sellable(lot, as_of):
            new_lots.append(lot)
            continue
        take = min(lot_sh, remaining)
        leftover = lot_sh - take
        remaining -= take
        sold += take
        if leftover > 1e-9:
            row = dict(lot)
            row["shares"] = leftover
            new_lots.append(row)
    holding["lots"] = new_lots
    holding["shares"] = _sum_lots(new_lots)
    if holding["shares"] <= 1e-9:
        holding["shares"] = 0.0
        holding["lots"] = []
    _refresh_bought_at(holding)
    return sold


def apply_t0_trades(holding: dict, trades: List[dict], *, as_of: str, ts: str = "") -> None:
    """按做 T 成交顺序更新批次：卖扣旧仓，买记当日新仓。"""
    for t in trades or []:
        if not isinstance(t, dict):
            continue
        side = str(t.get("side") or "")
        try:
            qty = float(t.get("shares") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty <= 1e-9:
            continue
        fill_ts = str(t.get("ts") or ts or "")
        if side in ("t0_sell", "sell"):
            consume_sell_lots(holding, qty, as_of=as_of)
        elif side in ("t0_buy", "buy"):
            add_buy_lot(holding, qty, ts=fill_ts or f"{as_of}T15:00:00", as_of=as_of)


def snapshot_tplus1(holding: dict, *, as_of: Optional[str] = None) -> Dict[str, Any]:
    as_of = _session_of(as_of)
    ensure_lots(holding, as_of=as_of)
    sellable = sellable_shares(holding, as_of=as_of)
    locked = locked_shares(holding, as_of=as_of)
    lots_out = []
    for lot in holding.get("lots") or []:
        lots_out.append(
            {
                "shares": float(lot.get("shares") or 0),
                "bought_date": lot.get("bought_date"),
                "bought_at": lot.get("bought_at"),
                "sellable": _lot_sellable(lot, as_of),
            }
        )
    return {
        "as_of": as_of,
        "sellable_shares": sellable,
        "locked_shares": locked,
        "lots": lots_out,
    }
