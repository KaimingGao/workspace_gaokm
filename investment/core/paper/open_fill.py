"""纸面 next_open：收盘后只挂单，开盘窗用开盘价成交。

与回测 ``execution_mode=next_open`` 对齐：信号日收盘决策，次日开盘成交。
盘中做 T / 收盘后即时成交不走这条路径。
"""

from __future__ import annotations

import logging
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

MODE_NEXT_OPEN = "next_open"
MODE_CLOSE = "close"

PHASE_OPEN = "open"
PHASE_SESSION = "session"
PHASE_CLOSED = "closed"

DEFAULT_TIMING: Dict[str, Any] = {
    "execution_mode": MODE_NEXT_OPEN,
    "open_fill_after_hm": "09:15",
    "open_fill_until_hm": "10:00",
}

_RESEARCH_KEYS = (
    "last_optimize",
    "last_cluster_pool",
    "last_north_star",
    "signal_log",
)


def _parse_hm(raw: Any, fallback: Tuple[int, int]) -> Tuple[int, int]:
    s = str(raw or "").strip()
    if ":" in s:
        parts = s.split(":")
        try:
            return max(0, min(int(parts[0]), 23)), max(0, min(int(parts[1]), 59))
        except (TypeError, ValueError, IndexError):
            return fallback
    return fallback


def get_rebalance_timing(paper: Optional[dict] = None) -> Dict[str, Any]:
    """合并 Spec / paper.rules 的成交时点。默认 next_open。"""
    out = dict(DEFAULT_TIMING)
    try:
        from core.execution import resolve_effective_execution

        bundle = resolve_effective_execution(
            strategy=(paper or {}).get("strategy_id") if isinstance(paper, dict) else None,
            paper=paper if isinstance(paper, dict) else None,
            channel="paper",
        )
        exe = (bundle.get("execution") or {}) if isinstance(bundle, dict) else {}
        timing = exe.get("rebalance_timing") if isinstance(exe, dict) else None
        if isinstance(timing, dict):
            out.update({k: v for k, v in timing.items() if v is not None})
    except Exception:  # noqa: BLE001 — best-effort
        logger.debug("resolve_effective_execution failed in get_rebalance_timing", exc_info=True)
    rules = (paper or {}).get("rules") if isinstance(paper, dict) else None
    if isinstance(rules, dict):
        if rules.get("execution_mode"):
            out["execution_mode"] = rules.get("execution_mode")
        nested = rules.get("execution") if isinstance(rules.get("execution"), dict) else {}
        rt = nested.get("rebalance_timing") if isinstance(nested, dict) else None
        if isinstance(rt, dict):
            out.update({k: v for k, v in rt.items() if v is not None})
        if nested.get("execution_mode"):
            out["execution_mode"] = nested.get("execution_mode")
    mode = str(out.get("execution_mode") or MODE_NEXT_OPEN).strip().lower()
    if mode not in (MODE_NEXT_OPEN, MODE_CLOSE):
        mode = MODE_NEXT_OPEN
    out["execution_mode"] = mode
    return out


def is_next_open_mode(paper: Optional[dict] = None) -> bool:
    return get_rebalance_timing(paper).get("execution_mode") == MODE_NEXT_OPEN


def require_open_fill(
    paper: Optional[dict] = None,
    *,
    now: Optional[datetime] = None,
    action: str = "交易",
) -> Optional[str]:
    """非 next_open 或已在开盘窗 → None；否则返回拒绝原因。"""
    if not is_next_open_mode(paper):
        return None
    timing = get_rebalance_timing(paper)
    phase = paper_fill_phase(now, timing=timing)
    if phase == PHASE_OPEN:
        return None
    after = timing.get("open_fill_after_hm") or "09:15"
    until = timing.get("open_fill_until_hm") or "10:00"
    when = "收盘后" if phase == PHASE_CLOSED else "非开盘窗"
    return (
        f"{when}不{action}（next_open）。"
        f"开盘窗 {after}–{until} 才成交；收盘后请确认调仓以挂次日开盘单。"
    )


def paper_fill_phase(
    now: Optional[datetime] = None,
    *,
    timing: Optional[dict] = None,
) -> str:
    """open=开盘窗可成交；session=连续竞价不再下新单；closed=收盘后/非交易日。"""
    from core.signal.session_pit import shanghai_now

    n = shanghai_now(now)
    day = n.strftime("%Y-%m-%d")
    try:
        from core.market.calendar import is_trading_day

        trading = is_trading_day(day)
    except Exception:  # noqa: BLE001
        logger.debug("is_trading_day failed", exc_info=True)
        trading = n.weekday() < 5
    if not trading:
        return PHASE_CLOSED
    cfg = timing or DEFAULT_TIMING
    after = _parse_hm(cfg.get("open_fill_after_hm"), (9, 15))
    until = _parse_hm(cfg.get("open_fill_until_hm"), (10, 0))
    hm = (n.hour, n.minute)
    if hm >= (15, 5) or hm < after:
        return PHASE_CLOSED
    if hm < until:
        return PHASE_OPEN
    return PHASE_SESSION


def _target_fill_date(now: datetime) -> str:
    from core.market.calendar import is_trading_day, next_trading_day
    from core.signal.session_pit import shanghai_now

    n = shanghai_now(now)
    day = n.strftime("%Y-%m-%d")
    phase = paper_fill_phase(n)
    if phase == PHASE_OPEN and is_trading_day(day):
        return day
    nxt = next_trading_day(day)
    return nxt or day


def pending_from_trades(
    sell_trades: Sequence[dict],
    buy_trades: Sequence[dict],
    *,
    now: Optional[datetime] = None,
    source: str = "rebalance",
) -> Dict[str, Any]:
    from core.signal.session_pit import shanghai_now

    n = shanghai_now(now)
    legs: List[dict] = []
    for t in list(sell_trades or []) + list(buy_trades or []):
        if not isinstance(t, dict):
            continue
        code = str(t.get("stock_code") or "").strip()
        try:
            shares = float(t.get("shares") or 0)
        except (TypeError, ValueError):
            shares = 0.0
        if not code or shares <= 0:
            continue
        side = str(t.get("side") or "").strip().lower()
        if side not in ("buy", "sell"):
            if t in (buy_trades or []):
                side = "buy"
            else:
                side = "sell"
        try:
            intent_px = float(t.get("price") or 0) or None
        except (TypeError, ValueError):
            intent_px = None
        legs.append(
            {
                "side": side,
                "stock_code": code,
                "stock_name": t.get("stock_name"),
                "shares": shares,
                "intent_price": intent_px,
                "note": t.get("note"),
                "score": t.get("score"),
                "origin": t.get("origin") or "strategy",
            }
        )
    return {
        "as_of": n.strftime("%Y-%m-%d"),
        "target_fill_date": _target_fill_date(n),
        "source": source,
        "staged_at": n.isoformat(timespec="seconds"),
        "legs": legs,
    }


def stage_pending(paper: dict, pending: dict) -> dict:
    """覆盖隔夜挂单；不改持仓。"""
    paper["pending_orders"] = pending
    return pending


def _quote_open_px(quote: Optional[dict]) -> Optional[float]:
    import re

    if not isinstance(quote, dict) or not quote.get("success"):
        return None
    raw = quote.get("open_raw")
    if raw is not None:
        try:
            f = float(raw)
            return f if f > 0 else None
        except (TypeError, ValueError):
            pass
    s = quote.get("open")
    if s is None or s == "":
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace(",", ""))
    if not m:
        return None
    try:
        f = float(m.group(0))
    except ValueError:
        return None
    return f if f > 0 else None


def _apply_leg_at_open(
    paper: dict,
    leg: dict,
    *,
    open_px: float,
) -> Tuple[Optional[dict], Optional[str]]:
    from core.paper.ledger import ORIGIN_STRATEGY, _now_iso, merge_origin
    from core.paper.costs import (
        annotate_trade,
        apply_fill_price,
        calc_trade_fees,
        cost_params,
        resolve_cost_model,
    )
    from core.paper.sizing import _lot_shares

    side = str(leg.get("side") or "").strip().lower()
    code = str(leg.get("stock_code") or "").strip()
    try:
        shares = float(leg.get("shares") or 0)
    except (TypeError, ValueError):
        shares = 0.0
    if side not in ("buy", "sell") or not code or shares <= 0 or open_px <= 0:
        return None, "invalid_leg"

    model = resolve_cost_model(paper)
    params = cost_params(paper)
    fill_px = apply_fill_price(side, float(open_px), model=model, params=params)
    cash = float(paper.get("cash") or 0)
    holdings = list(paper.get("holdings") or [])
    existing = next((h for h in holdings if str(h.get("stock_code")) == code), None)
    name = leg.get("stock_name") or (existing or {}).get("stock_name")

    if side == "sell":
        have = float((existing or {}).get("shares") or 0)
        sell_shares = min(shares, have)
        sell_shares = float(_lot_shares(sell_shares) or sell_shares)
        if sell_shares <= 0 or not existing:
            return None, "no_position"
        cost = float(existing.get("cost") or 0)
        amount = round(sell_shares * fill_px, 2)
        fee_info = calc_trade_fees("sell", amount, model=model, params=params)
        pnl_pct = round((fill_px / cost - 1.0) * 100.0, 2) if cost else None
        trade = annotate_trade(
            {
                "ts": _now_iso(),
                "side": "sell",
                "stock_code": code,
                "stock_name": name,
                "shares": sell_shares,
                "price": round(fill_px, 4),
                "amount": amount,
                "pnl_pct": pnl_pct,
                "score": leg.get("score"),
                "origin": leg.get("origin") or ORIGIN_STRATEGY,
                "note": f"开盘成交 · {leg.get('note') or '隔夜挂单'}",
                "fill_timing": MODE_NEXT_OPEN,
                "intent_price": leg.get("intent_price"),
            },
            fee_info,
        )
        paper.setdefault("trades", []).append(trade)
        paper["cash"] = round(cash + float(fee_info.get("net_cash_delta") or 0), 2)
        left = have - sell_shares
        if left > 1e-6:
            existing["shares"] = left
        else:
            paper["holdings"] = [h for h in holdings if str(h.get("stock_code")) != code]
        return trade, None

    buy_shares = float(_lot_shares(shares) or shares)
    if buy_shares <= 0:
        return None, "lot"
    amount = round(buy_shares * fill_px, 2)
    fee_info = calc_trade_fees("buy", amount, model=model, params=params)
    need = amount + float(fee_info.get("fees") or 0)
    if need > cash + 1e-6:
        return None, "cash"
    trade = annotate_trade(
        {
            "ts": _now_iso(),
            "side": "buy",
            "stock_code": code,
            "stock_name": name,
            "shares": buy_shares,
            "price": round(fill_px, 4),
            "amount": amount,
            "score": leg.get("score"),
            "origin": leg.get("origin") or ORIGIN_STRATEGY,
            "note": f"开盘成交 · {leg.get('note') or '隔夜挂单'}",
            "fill_timing": MODE_NEXT_OPEN,
            "intent_price": leg.get("intent_price"),
        },
        fee_info,
    )
    paper.setdefault("trades", []).append(trade)
    paper["cash"] = round(cash - need, 2)
    if existing:
        old_sh = float(existing.get("shares") or 0)
        old_cost = float(existing.get("cost") or 0)
        new_sh = old_sh + buy_shares
        existing["shares"] = new_sh
        if new_sh > 0:
            existing["cost"] = round((old_cost * old_sh + fill_px * buy_shares) / new_sh, 4)
        existing["origin"] = merge_origin(existing.get("origin"), trade.get("origin"))
    else:
        from core.paper.ledger import _now_iso as _ts

        holdings.append(
            {
                "stock_code": code,
                "stock_name": name,
                "shares": buy_shares,
                "cost": round(fill_px, 4),
                "bought_at": _ts(),
                "origin": trade.get("origin") or ORIGIN_STRATEGY,
            }
        )
        paper["holdings"] = holdings
    return trade, None


def fill_pending_at_open(
    paper: dict,
    *,
    now: Optional[datetime] = None,
    quotes: Optional[Dict[str, dict]] = None,
) -> Dict[str, Any]:
    """用开盘价成交 ``pending_orders``。未到目标日或非开盘窗则跳过。"""
    pending = paper.get("pending_orders") if isinstance(paper.get("pending_orders"), dict) else None
    if not pending or not (pending.get("legs") or []):
        return {"ok": True, "filled": False, "reason": "no_pending", "trades": []}
    timing = get_rebalance_timing(paper)
    phase = paper_fill_phase(now, timing=timing)
    if phase != PHASE_OPEN:
        return {
            "ok": True,
            "filled": False,
            "reason": f"not_open_window:{phase}",
            "phase": phase,
            "pending": pending,
            "trades": [],
        }
    from core.signal.session_pit import shanghai_now

    today = shanghai_now(now).strftime("%Y-%m-%d")
    target = str(pending.get("target_fill_date") or "")[:10]
    if target and today < target:
        return {
            "ok": True,
            "filled": False,
            "reason": f"too_early:{target}",
            "phase": phase,
            "pending": pending,
            "trades": [],
        }

    quotes = dict(quotes or {})
    missing = [
        str(leg.get("stock_code") or "").strip()
        for leg in (pending.get("legs") or [])
        if str(leg.get("stock_code") or "").strip() and str(leg.get("stock_code") or "").strip() not in quotes
    ]
    if missing:
        try:
            from core.data.service import get_default_service

            quotes.update(dict(get_default_service().batch_get_quotes(missing) or {}))
        except Exception:  # noqa: BLE001
            logger.debug("batch_get_quotes failed in fill_pending_at_open", exc_info=True)

    filled_trades: List[dict] = []
    leftover: List[dict] = []
    skips: List[dict] = []
    # 先卖后买，与调仓一致
    ordered = sorted(
        list(pending.get("legs") or []),
        key=lambda x: 0 if str(x.get("side") or "") == "sell" else 1,
    )
    for leg in ordered:
        code = str(leg.get("stock_code") or "").strip()
        q = quotes.get(code) if code else None
        if not isinstance(q, dict):
            try:
                from core.data.facade import get_quote

                q = get_quote(code)
            except Exception:  # noqa: BLE001
                logger.debug("get_quote failed for %s", code, exc_info=True)
                q = {}
        open_px = _quote_open_px(q)
        if not open_px:
            leftover.append(leg)
            skips.append({"stock_code": code, "reason": "no_open"})
            continue
        trade, err = _apply_leg_at_open(paper, leg, open_px=open_px)
        if err:
            leftover.append(leg)
            skips.append({"stock_code": code, "reason": err})
            continue
        if trade:
            filled_trades.append(trade)

    if leftover:
        pending = dict(pending)
        pending["legs"] = leftover
        paper["pending_orders"] = pending
    else:
        paper["pending_orders"] = None
    from core.paper.ledger import _now_iso

    paper["updated_at"] = _now_iso()
    return {
        "ok": True,
        "filled": bool(filled_trades),
        "phase": phase,
        "trades": filled_trades,
        "skips": skips,
        "leftover": leftover,
    }


def _copy_research_fields(dst: dict, src: dict) -> None:
    for k in _RESEARCH_KEYS:
        if k in src:
            dst[k] = deepcopy(src.get(k))


def apply_next_open_commit(
    original: dict,
    mutated: dict,
    result: dict,
    *,
    now: Optional[datetime] = None,
    dry_run: bool = False,
    source: str = "rebalance",
) -> Tuple[dict, dict]:
    """把「已按现价模拟」的调仓改成挂单或开盘成交。

    返回 ``(paper_to_save, result)``。``close`` 模式原样交还 mutated。
    """
    out = dict(result or {})
    timing = get_rebalance_timing(original)
    out["execution_mode"] = timing.get("execution_mode")
    phase = paper_fill_phase(now, timing=timing)
    out["fill_phase"] = phase
    if dry_run or timing.get("execution_mode") != MODE_NEXT_OPEN:
        out["fill_action"] = "preview" if dry_run else "immediate"
        return mutated, out

    sell_trades = list(out.get("sell_trades") or [])
    buy_trades = list(out.get("buy_trades") or out.get("new_trades") or [])
    paper = deepcopy(original)
    _copy_research_fields(paper, mutated)

    if phase == PHASE_OPEN:
        fill_old = fill_pending_at_open(paper, now=now)
        out["open_fill"] = fill_old
        if fill_old.get("filled"):
            # 隔夜单已成交：忽略本次按现价模拟的新腿，避免双重调仓
            out["fill_action"] = "open_fill_pending"
            out["sell_trades"] = [t for t in fill_old.get("trades") or [] if t.get("side") == "sell"]
            out["buy_trades"] = [t for t in fill_old.get("trades") or [] if t.get("side") == "buy"]
            out["new_trades"] = out["buy_trades"]
            out["note"] = "开盘窗：已按隔夜挂单以开盘价成交（未再套用本次现价模拟）"
            return paper, out
        pending = pending_from_trades(sell_trades, buy_trades, now=now, source=source)
        stage_pending(paper, pending)
        fill_new = fill_pending_at_open(paper, now=now)
        out["open_fill"] = fill_new
        out["fill_action"] = "open_fill"
        out["sell_trades"] = [t for t in fill_new.get("trades") or [] if t.get("side") == "sell"]
        out["buy_trades"] = [t for t in fill_new.get("trades") or [] if t.get("side") == "buy"]
        out["new_trades"] = out["buy_trades"]
        out["note"] = "开盘窗：以开盘价成交"
        return paper, out

    pending = pending_from_trades(sell_trades, buy_trades, now=now, source=source)
    stage_pending(paper, pending)
    out["fill_action"] = "staged"
    out["pending_orders"] = pending
    out["staged"] = True
    # 挂单不改仓，回报里的成交视为意图
    out["sell_trades"] = sell_trades
    out["buy_trades"] = buy_trades
    out["new_trades"] = buy_trades
    n_legs = len(pending.get("legs") or [])
    when = "收盘后" if phase == PHASE_CLOSED else "非开盘窗"
    out["note"] = (
        f"{when}不成交：已挂 {n_legs} 笔次日开盘单（目标 {pending.get('target_fill_date')}）"
    )
    return paper, out
