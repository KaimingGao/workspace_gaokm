"""纸面 next_open：交易时段内按现价成交；收盘后挂次日开盘单。

产品口径是准实盘（A 股连续竞价可买卖，T+1 管批次），不是「收盘决策、开盘才执行」。
回测默认 ``execution_mode=next_open`` 仍是研究侧防未来函数，与纸面盘中成交分开。
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
    # 开盘窗未成的挂单：盘中中点追价，尽量成交；近收盘改用现价强平
    "pending_chase_interval_min": 10,
    "pending_chase_eod_hm": "14:50",
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
    """收盘后拒绝即时成交；盘中（含连续竞价）放行。非 next_open 一律放行。"""
    if not is_next_open_mode(paper):
        return None
    timing = get_rebalance_timing(paper)
    phase = paper_fill_phase(now, timing=timing)
    if phase != PHASE_CLOSED:
        return None
    after = timing.get("open_fill_after_hm") or "09:15"
    until = timing.get("open_fill_until_hm") or "10:00"
    return (
        f"已收盘不{action}（next_open）。"
        f"手动买卖可挂次日开盘单；策略调仓请点「确认调仓」。"
        f"开盘窗 {after}–{until} 成交隔夜单；盘中可按现价即时成交。"
    )


def paper_fill_phase(
    now: Optional[datetime] = None,
    *,
    timing: Optional[dict] = None,
) -> str:
    """open=开盘窗（隔夜单按开盘价）；session=连续竞价按现价；closed=收盘后/非交易日。"""
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


def _target_fill_date(now: datetime, *, timing: Optional[dict] = None) -> str:
    """隔夜/盘前挂单的目标开盘日。

    - 交易日且尚未过开盘窗终点（含盘前 <09:15）→ 今日
    - 已过开盘窗（连续竞价或收盘后）或非交易日 → 下一交易日

    旧逻辑把盘前也当 closed 并 ``next_trading_day``，会把今日 08:00 的挂单标成「明日」。
    """
    from core.market.calendar import is_trading_day, next_trading_day
    from core.signal.session_pit import shanghai_now

    n = shanghai_now(now)
    day = n.strftime("%Y-%m-%d")
    cfg = timing or DEFAULT_TIMING
    until = _parse_hm(cfg.get("open_fill_until_hm"), (10, 0))
    hm = (n.hour, n.minute)
    if is_trading_day(day) and hm < until:
        return day
    nxt = next_trading_day(day)
    return nxt or day


def _heal_preopen_target(pending: dict, *, day: str, phase: str) -> str:
    """盘前误把目标标成次日时，开盘窗内按今日成交。"""
    target = str(pending.get("target_fill_date") or "")[:10]
    as_of = str(pending.get("as_of") or "")[:10]
    if (
        phase == PHASE_OPEN
        and target
        and target > day
        and as_of == day
    ):
        return day
    return target or day


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
        "target_fill_date": _target_fill_date(n, timing=get_rebalance_timing()),
        "source": source,
        "staged_at": n.isoformat(timespec="seconds"),
        "legs": legs,
    }


def stage_pending(paper: dict, pending: dict) -> dict:
    """覆盖隔夜挂单；不改持仓。"""
    paper["pending_orders"] = pending
    return pending


def merge_pending_orders(
    existing: Optional[dict],
    incoming: dict,
) -> Dict[str, Any]:
    """合并挂单：同 ``side+stock_code`` 以新腿覆盖；其余保留。"""
    base = dict(incoming or {})
    old_legs = list((existing or {}).get("legs") or []) if isinstance(existing, dict) else []
    new_legs = list(base.get("legs") or [])
    replace_keys = {
        (str(l.get("side") or "").lower(), str(l.get("stock_code") or "").strip())
        for l in new_legs
        if str(l.get("stock_code") or "").strip()
    }
    kept = [
        l
        for l in old_legs
        if (
            str(l.get("side") or "").lower(),
            str(l.get("stock_code") or "").strip(),
        )
        not in replace_keys
    ]
    base["legs"] = kept + new_legs
    if isinstance(existing, dict):
        # 保留更早的 as_of；目标成交日取 incoming（通常为下一开盘日）
        if existing.get("as_of") and not base.get("as_of"):
            base["as_of"] = existing.get("as_of")
    return base


def _quote_for_open_match(quote: Optional[dict], open_px: float) -> dict:
    """把开盘价写进撮合用行情，使涨跌停检查对开盘价而非最新价。"""
    q = dict(quote or {})
    q["price_raw"] = float(open_px)
    q["price"] = float(open_px)
    prev = q.get("prev_close") or q.get("pre_close") or q.get("yesterday_close")
    if prev is not None:
        try:
            p0 = float(prev)
            if p0 > 0:
                q["change_raw"] = (float(open_px) / p0 - 1.0) * 100.0
        except (TypeError, ValueError):
            pass
    return q


def _open_fill_block_reason(
    code: str,
    side: str,
    quote: Optional[dict],
    open_px: float,
) -> Optional[str]:
    """开盘成交：涨停买不到、跌停卖不出、停牌两边都不成。"""
    side_s = str(side or "").strip().lower()
    q = _quote_for_open_match(quote, open_px)
    try:
        from core.paper.rebalance.match import (
            _buy_match_block_reason,
            _sell_match_block_reason,
        )
    except Exception:  # noqa: BLE001
        logger.debug("open-fill match import failed", exc_info=True)
        return None
    if side_s == "buy":
        return _buy_match_block_reason(code, q)
    if side_s == "sell":
        return _sell_match_block_reason(code, q)
    return None


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


def _quote_last_px(quote: Optional[dict]) -> Optional[float]:
    """现价：优先 price_raw，其次解析 price 文案。"""
    if not isinstance(quote, dict):
        return None
    try:
        from core.ports.market import quote_price

        raw = quote_price(quote)
        if raw is not None and float(raw) > 0:
            return float(raw)
    except Exception:  # noqa: BLE001
        logger.debug("quote_price failed in open_fill", exc_info=True)
    s = quote.get("price")
    if s is None or s == "":
        return None
    import re

    m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace(",", ""))
    if not m:
        return None
    try:
        f = float(m.group(0))
    except ValueError:
        return None
    return f if f > 0 else None


def _leg_intent_px(leg: dict, last: float) -> float:
    for k in ("chase_target", "intent_price", "price"):
        try:
            v = float(leg.get(k) or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            return v
    return float(last)


def _chase_due(leg: dict, now: datetime, interval_min: int) -> bool:
    raw = str(leg.get("last_chase_at") or "").strip()
    if not raw:
        return True
    try:
        prev = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if prev.tzinfo is not None:
            prev = prev.replace(tzinfo=None)
    except ValueError:
        return True
    delta = (now.replace(tzinfo=None) - prev).total_seconds()
    return delta >= max(1, int(interval_min)) * 60


def _apply_leg_at_open(
    paper: dict,
    leg: dict,
    *,
    open_px: float,
    as_of: Optional[str] = None,
    note_prefix: str = "开盘成交",
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
    note_prefix = str(note_prefix or "开盘成交")
    cash = float(paper.get("cash") or 0)
    holdings = list(paper.get("holdings") or [])
    existing = next((h for h in holdings if str(h.get("stock_code")) == code), None)
    name = leg.get("stock_name") or (existing or {}).get("stock_name")

    if side == "sell":
        have = float((existing or {}).get("shares") or 0)
        from core.paper.tplus1 import TPLUS1_LOCK_REASON, clip_sell_shares, consume_sell_lots

        sell_shares, t1_meta = clip_sell_shares(
            existing or {}, min(shares, have), as_of=as_of
        )
        sell_shares = float(_lot_shares(sell_shares) or sell_shares)
        if sell_shares <= 0 or not existing:
            return None, t1_meta.get("reason") or TPLUS1_LOCK_REASON if have > 0 else "no_position"
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
                "note": f"{note_prefix} · {leg.get('note') or '隔夜挂单'}",
                "fill_timing": MODE_NEXT_OPEN,
                "intent_price": leg.get("intent_price"),
            },
            fee_info,
        )
        paper.setdefault("trades", []).append(trade)
        paper["cash"] = round(cash + float(fee_info.get("net_cash_delta") or 0), 2)
        consume_sell_lots(existing, sell_shares)
        if float(existing.get("shares") or 0) <= 1e-6:
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
            "note": f"{note_prefix} · {leg.get('note') or '隔夜挂单'}",
            "fill_timing": MODE_NEXT_OPEN,
            "intent_price": leg.get("intent_price"),
        },
        fee_info,
    )
    paper.setdefault("trades", []).append(trade)
    paper["cash"] = round(cash - need, 2)
    from core.paper.tplus1 import add_buy_lot, stamp_new_holding

    if existing:
        old_sh = float(existing.get("shares") or 0)
        old_cost = float(existing.get("cost") or 0)
        new_sh = old_sh + buy_shares
        if new_sh > 0:
            existing["cost"] = round((old_cost * old_sh + fill_px * buy_shares) / new_sh, 4)
        add_buy_lot(existing, buy_shares, ts=trade["ts"])
        existing["origin"] = merge_origin(existing.get("origin"), trade.get("origin"))
    else:
        row = {
            "stock_code": code,
            "stock_name": name,
            "shares": buy_shares,
            "cost": round(fill_px, 4),
            "bought_at": trade["ts"],
            "origin": trade.get("origin") or ORIGIN_STRATEGY,
        }
        stamp_new_holding(row, ts=trade["ts"])
        holdings.append(row)
        paper["holdings"] = holdings
    return trade, None


def fill_pending_at_open(
    paper: dict,
    *,
    now: Optional[datetime] = None,
    quotes: Optional[Dict[str, dict]] = None,
) -> Dict[str, Any]:
    """成交 ``pending_orders``：开盘窗按开盘价；盘中中点追价；近收盘现价强平。

    涨停买 / 跌停卖 / 停牌仍跳过并留单。收盘后（非交易时段）不再追，留到下一开盘窗。
    """
    pending = paper.get("pending_orders") if isinstance(paper.get("pending_orders"), dict) else None
    if not pending or not (pending.get("legs") or []):
        return {"ok": True, "filled": False, "reason": "no_pending", "trades": []}
    timing = get_rebalance_timing(paper)
    phase = paper_fill_phase(now, timing=timing)
    if phase == PHASE_CLOSED:
        return {
            "ok": True,
            "filled": False,
            "reason": f"not_open_window:{phase}",
            "phase": phase,
            "pending": pending,
            "trades": [],
        }
    from core.signal.session_pit import shanghai_now

    n = shanghai_now(now)
    today = n.strftime("%Y-%m-%d")
    target = _heal_preopen_target(pending, day=today, phase=phase)
    if target != str(pending.get("target_fill_date") or "")[:10]:
        pending = dict(pending)
        pending["target_fill_date"] = target
        paper["pending_orders"] = pending
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

    chase_iv = 10
    try:
        chase_iv = max(1, min(int(timing.get("pending_chase_interval_min") or 10), 60))
    except (TypeError, ValueError):
        chase_iv = 10
    eod_hm = _parse_hm(timing.get("pending_chase_eod_hm"), (14, 50))
    eod_force = phase == PHASE_SESSION and (n.hour, n.minute) >= eod_hm
    chase_ts = n.replace(tzinfo=None).isoformat(timespec="seconds")

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
        side = str(leg.get("side") or "")
        if phase == PHASE_OPEN:
            px = _quote_open_px(q)
            if not px:
                leftover.append({**leg, "skip_reason": "no_open"})
                skips.append({"stock_code": code, "side": side, "reason": "no_open"})
                continue
            block = _open_fill_block_reason(
                code, side, q if isinstance(q, dict) else {}, px
            )
            if block:
                leftover.append({**leg, "skip_reason": block})
                skips.append({"stock_code": code, "side": side, "reason": block})
                continue
            trade, err = _apply_leg_at_open(
                paper, leg, open_px=px, as_of=today, note_prefix="开盘成交"
            )
        else:
            last = _quote_last_px(q)
            if not last:
                leftover.append({**leg, "skip_reason": "no_last"})
                skips.append({"stock_code": code, "side": side, "reason": "no_last"})
                continue
            block = _open_fill_block_reason(
                code, side, q if isinstance(q, dict) else {}, last
            )
            if block:
                leftover.append(
                    {**leg, "skip_reason": block, "last_chase_at": chase_ts}
                )
                skips.append({"stock_code": code, "side": side, "reason": block})
                continue
            if not eod_force and not _chase_due(leg, n.replace(tzinfo=None), chase_iv):
                leftover.append(dict(leg))
                continue
            intent = _leg_intent_px(leg, last)
            fill_px = float(last) if eod_force else (float(intent) + float(last)) / 2.0
            note_prefix = "收盘追价成交" if eod_force else "中点追价成交"
            trade, err = _apply_leg_at_open(
                paper,
                {**leg, "chase_target": round(fill_px, 4)},
                open_px=fill_px,
                as_of=today,
                note_prefix=note_prefix,
            )
            if err:
                leftover.append(
                    {
                        **leg,
                        "skip_reason": err,
                        "chase_target": round(fill_px, 4),
                        "last_chase_at": chase_ts,
                    }
                )
                skips.append({"stock_code": code, "side": side, "reason": err})
                continue
            if trade:
                filled_trades.append(trade)
            continue
        if err:
            leftover.append({**leg, "skip_reason": err})
            skips.append({"stock_code": code, "side": side, "reason": err})
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
        "mode": "eod_force" if eod_force else ("chase" if phase == PHASE_SESSION else "open"),
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
    if phase == PHASE_SESSION:
        leftover_po = (
            original.get("pending_orders")
            if isinstance(original.get("pending_orders"), dict)
            else None
        )
        if leftover_po and (leftover_po.get("legs") or []):
            paper = deepcopy(original)
            _copy_research_fields(paper, mutated)
            fill_old = fill_pending_at_open(paper, now=now)
            out["open_fill"] = fill_old
            leftover = (
                paper.get("pending_orders")
                if isinstance(paper.get("pending_orders"), dict)
                else None
            )
            if leftover and (leftover.get("legs") or []):
                out["fill_action"] = "kept_pending"
                out["pending_orders"] = leftover
                out["note"] = "盘中：挂单中点追价尚未全部成交，本次未改仓"
                return paper, out
            if fill_old.get("filled"):
                out["fill_action"] = "session_chase_pending"
                out["sell_trades"] = [
                    t for t in fill_old.get("trades") or [] if t.get("side") == "sell"
                ]
                out["buy_trades"] = [
                    t for t in fill_old.get("trades") or [] if t.get("side") == "buy"
                ]
                out["new_trades"] = out["buy_trades"]
                out["note"] = "盘中：已按中点追价成交隔夜挂单（未再套用本次现价模拟）"
                return paper, out
        out["fill_action"] = "immediate"
        out["note"] = "盘中：按现价成交"
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
        leftover = paper.get("pending_orders") if isinstance(paper.get("pending_orders"), dict) else None
        if leftover and (leftover.get("legs") or []):
            out["fill_action"] = "kept_pending"
            out["pending_orders"] = leftover
            out["note"] = "开盘窗：隔夜单尚未成交，本次未改仓"
            return paper, out
        out["fill_action"] = "immediate"
        out["note"] = "开盘窗：无隔夜单，按现价成交"
        return mutated, out

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
    target = str(pending.get("target_fill_date") or "")[:10]
    as_of = str(pending.get("as_of") or "")[:10]
    if target and as_of and target == as_of:
        out["note"] = (
            f"盘前不成交：已挂 {n_legs} 笔今日开盘单（目标 {target}；涨停/跌停/停牌可能成交不了）"
        )
    else:
        out["note"] = (
            f"收盘后不成交：已挂 {n_legs} 笔次日开盘单（目标 {target}；涨停/跌停/停牌可能成交不了）"
        )
    return paper, out
