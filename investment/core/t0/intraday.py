"""做 T 盘中增量盯盘：5m K 线闭合后第一触达即落账（不再日终整段回放）。"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PHASE_IDLE = "idle"
PHASE_AFTER_LEG1 = "after_leg1"
PHASE_DONE = "done"
PHASE_SKIPPED = "skipped"


def _now_ts() -> float:
    return time.time()


def _state_path() -> str:
    from core.paths import T0_INTRADAY_STATE_PATH

    return T0_INTRADAY_STATE_PATH


def session_in_market(*, now: Any = None) -> Tuple[bool, Optional[str]]:
    """9:30–15:05 交易时段（含收盘后短窗做强制回补）。"""
    from core.market.calendar import is_trading_day, resolve_session_date
    from core.signal.session_pit import shanghai_now

    n = shanghai_now(now)
    sess = resolve_session_date(now=n)
    if not sess or not is_trading_day(sess):
        return False, sess
    t = (n.hour, n.minute)
    if t < (9, 30):
        return False, sess
    if t >= (15, 6):
        return False, sess
    return True, sess


def load_intraday_state() -> Dict[str, Any]:
    path = _state_path()
    if not os.path.isfile(path):
        return {}
    try:
        import json

        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except Exception:  # noqa: BLE001
        logger.debug("load t0 intraday state failed", exc_info=True)
        return {}


def save_intraday_state(state: Dict[str, Any]) -> None:
    from core.io_atomic import atomic_write_json

    atomic_write_json(_state_path(), {**state, "updated_at": _now_ts()})


def _bar_ts(mb: dict) -> str:
    return str(mb.get("datetime") or mb.get("date") or "")


def latest_minute_bar_ts(minute_bars: List[dict]) -> str:
    if not minute_bars:
        return ""
    return _bar_ts(max(minute_bars, key=_bar_ts))


def should_process_intraday_stock(
    stock_state: Optional[dict],
    latest_bar_ts: str,
    *,
    force_session_close: bool = False,
) -> bool:
    """无新 5m K 线且已终态则跳过，避免重复打网。"""
    if force_session_close:
        phase = str((stock_state or {}).get("phase") or PHASE_IDLE)
        return phase not in (PHASE_DONE, PHASE_SKIPPED)
    st = stock_state or {}
    phase = str(st.get("phase") or PHASE_IDLE)
    if phase in (PHASE_DONE, PHASE_SKIPPED):
        return False
    last = str(st.get("last_bar_ts") or "")
    if not latest_bar_ts:
        return True
    if not last:
        return True
    return latest_bar_ts > last


def _retryable_skip(reason: str) -> bool:
    r = str(reason or "")
    return any(x in r for x in ("振幅", "分钟", "未触及", "等待"))


def _intraday_setup(
    *,
    code: str,
    holding: dict,
    bar: dict,
    minute_bars: List[dict],
    cfg: dict,
    sellable: float,
    cash: float,
    atr_pct: Optional[float],
    hist_bars: Optional[List[dict]],
    scores: Optional[dict],
    stance_code: Optional[str],
    coupling_mode: str,
) -> Dict[str, Any]:
    from core.t0.minute_path import _day_ohlc_from_minutes, prefix_range_gate
    from core.t0.rules import (
        _skip_result,
        _t0_qty_lots,
        resolve_direction,
        scale_triggers_with_atr,
    )

    if coupling_mode == "skip_if_avoid" and stance_code == "avoid":
        return _skip_result(reason="stance=avoid 跳过做T", shares=float(holding.get("shares") or 0), bar=bar)
    if coupling_mode == "only_if_hold" and stance_code not in (None, "hold", "watch"):
        return _skip_result(reason=f"stance={stance_code} 非持有", shares=float(holding.get("shares") or 0), bar=bar)

    if len(minute_bars) < 2:
        return {"pending": True, "reason": "分钟线不足，等待下一根 5m"}

    bar_day = _day_ohlc_from_minutes(minute_bars, bar)
    lot = int(cfg.get("lot_size") or 100)
    shares = float(holding.get("shares") or 0)
    scaled = scale_triggers_with_atr(cfg, atr_pct=atr_pct)
    sell_trig = float(scaled["sell_trigger_pct"])
    buy_trig = float(scaled["buy_trigger_pct"])
    cost = float(holding.get("cost") or 0)
    if shares <= 0:
        return _skip_result(reason="无效 bar 或持仓", shares=shares, bar=bar_day)

    gate = prefix_range_gate(minute_bars, bar, cost=cost, cfg=cfg)
    ref = float(gate.get("ref") or 0)
    bar_day = gate.get("bar_day") or bar_day
    if ref <= 0:
        return _skip_result(reason="无效 bar 或持仓", shares=shares, bar=bar_day)

    if not gate.get("ok"):
        range_pct = gate.get("range_pct")
        min_range = gate.get("min_range_pct")
        reason = (
            f"振幅不足 {float(range_pct):.2f}% < {float(min_range):.2f}%"
            if range_pct is not None and not gate.get("flat")
            else "一字板/无波动"
            if gate.get("flat")
            else f"振幅不足 < {float(min_range):.2f}%"
        )
        return _skip_result(
            reason=reason,
            shares=shares,
            bar=bar_day,
            extra={
                "range_pct": range_pct,
                "min_range_pct": min_range,
                "range_mode": "rolling",
                "prefix_bars": gate.get("prefix_bars"),
            },
        )

    range_pct = float(gate.get("range_pct") or 0)

    dir_res = resolve_direction(
        bar=bar_day,
        ref=ref,
        cfg=cfg,
        cash=float(cash or 0),
        shares=shares,
        hist_bars=hist_bars,
        atr_pct=scaled.get("atr_pct") if scaled.get("atr_pct") is not None else atr_pct,
        scores=scores,
    )
    if dir_res.get("skip") or not dir_res.get("direction"):
        return _skip_result(
            reason=str(dir_res.get("direction_reason") or "选向跳过"),
            shares=shares,
            bar=bar_day,
            extra={"signal_skip": True},
        )

    direction = str(dir_res["direction"])
    fill_mode = str(cfg.get("fill_mode") or "trigger")
    t0_ratio = float(cfg.get("t0_ratio") or 0.4)

    from core.t0.minute_path import _first_touch_long, _first_touch_reverse

    if direction == "reverse_t":
        day = _first_touch_reverse(
            minute_bars=minute_bars,
            bar=bar_day,
            shares=shares,
            cash=float(cash or 0),
            sellable_shares=sellable,
            ref=ref,
            sell_trig=sell_trig,
            buy_trig=buy_trig,
            lot=lot,
            fill_mode=fill_mode,
            cfg=cfg,
            cost_config=None,
            stock_code=code,
            atr_pct=scaled.get("atr_pct"),
            range_pct=range_pct,
        )
    else:
        day = _first_touch_long(
            minute_bars=minute_bars,
            bar=bar_day,
            shares=shares,
            sellable_shares=sellable,
            ref=ref,
            sell_trig=sell_trig,
            buy_trig=buy_trig,
            lot=lot,
            fill_mode=fill_mode,
            cfg=cfg,
            cost_config=None,
            stock_code=code,
            atr_pct=scaled.get("atr_pct"),
            range_pct=range_pct,
            t0_ratio=t0_ratio,
        )

    if day.get("skipped"):
        return day
    trades = list(day.get("trades") or [])
    if not trades:
        return day

    # 增量：只落尚未写入的腿
    return {
        "ready": True,
        "direction": direction,
        "bar_day": bar_day,
        "day_result": day,
        "trades": trades,
        "last_bar_ts": _bar_ts(minute_bars[-1]) if minute_bars else "",
    }


def _apply_trades_to_paper(
    paper: dict,
    holding: dict,
    trades: List[dict],
    *,
    as_of: str,
    log_source: str,
) -> float:
    from core.paper.ledger import append_operation_log, append_trade_legs_to_operation_log
    from core.paper.tplus1 import apply_t0_trades, sellable_shares as t1_after
    from core.paper.ledger import _now_iso

    if not trades:
        return 0.0
    ts = _now_iso()
    pnl_delta = 0.0
    cash = float(paper.get("cash") or 0)
    legs: List[dict] = []
    for t in trades:
        row = dict(t)
        row["ts"] = ts
        row["stock_name"] = holding.get("stock_name")
        legs.append(row)
        paper.setdefault("trades", []).append(row)
        side = str(row.get("side") or "").lower()
        amt = float(row.get("amount") or 0)
        if side.endswith("sell"):
            cash += amt
        elif side.endswith("buy"):
            cash -= amt

    apply_t0_trades(holding, legs, as_of=as_of, ts=ts)
    paper["cash"] = round(cash, 2)
    holding["t0"] = {
        "enabled": True,
        "sellable_shares": t1_after(holding, as_of=as_of or None),
        "last_date": as_of,
    }
    src = str(log_source or "paper_t0_auto").strip() or "paper_t0_auto"
    append_trade_legs_to_operation_log(
        paper,
        sell_trades=[t for t in legs if str(t.get("side") or "").lower().endswith("sell")],
        buy_trades=[t for t in legs if str(t.get("side") or "").lower().endswith("buy")],
        origin="t0",
        source=src,
    )
    auto_tag = "自动" if src == "paper_t0_auto" else "手动"
    append_operation_log(
        paper,
        "t0_batch",
        detail=f"做T{auto_tag}·盘中 · 成交 {len(legs)} 笔",
        meta={"origin": "t0", "source": src, "trade_count": len(legs), "intraday": True},
    )
    return pnl_delta


def process_holding_intraday(
    *,
    code: str,
    holding: dict,
    stock_state: Optional[dict],
    minute_bars: List[dict],
    bar: dict,
    cfg: dict,
    sellable: float,
    cash: float,
    atr_pct: Optional[float],
    hist_bars: Optional[List[dict]],
    scores: Optional[dict],
    stance_code: Optional[str],
    coupling_mode: str,
    as_of: str,
    log_source: str,
    paper: dict,
    applied_legs: int,
) -> Tuple[dict, List[dict], dict]:
    """返回 (new_stock_state, new_trades, day_snapshot)。"""
    st = dict(stock_state or {})
    phase = str(st.get("phase") or PHASE_IDLE)
    last_ts = str(st.get("last_bar_ts") or "")
    legs_written = int(st.get("legs_written") or 0)

    if phase == PHASE_DONE:
        return st, [], st.get("day_snapshot") or {}
    if phase == PHASE_SKIPPED:
        return st, [], {}

    setup = _intraday_setup(
        code=code,
        holding=holding,
        bar=bar,
        minute_bars=minute_bars,
        cfg=cfg,
        sellable=sellable,
        cash=cash,
        atr_pct=atr_pct,
        hist_bars=hist_bars,
        scores=scores,
        stance_code=stance_code,
        coupling_mode=coupling_mode,
    )
    if setup.get("pending"):
        st.setdefault("phase", PHASE_IDLE)
        return st, [], {}
    if setup.get("skipped"):
        reason = str(setup.get("reason") or "")
        if _retryable_skip(reason) and phase in (PHASE_IDLE, PHASE_AFTER_LEG1):
            st["last_bar_ts"] = setup.get("last_bar_ts") or (
                _bar_ts(minute_bars[-1]) if minute_bars else last_ts
            )
            return st, [], {}
        st["phase"] = PHASE_SKIPPED
        st["reason"] = setup.get("reason")
        st["day_snapshot"] = {**setup, "stock_code": code, "stock_name": holding.get("stock_name")}
        return st, [], st["day_snapshot"]

    trades = list(setup.get("trades") or [])
    new_trades = trades[legs_written:]
    if not new_trades:
        st["last_bar_ts"] = setup.get("last_bar_ts") or last_ts
        st["phase"] = PHASE_IDLE if legs_written == 0 else (
            PHASE_DONE if legs_written >= len(trades) else PHASE_AFTER_LEG1
        )
        if legs_written >= len(trades) and trades:
            st["phase"] = PHASE_DONE
        return st, [], setup.get("day_result") or {}

    _apply_trades_to_paper(
        paper,
        holding,
        new_trades,
        as_of=as_of,
        log_source=log_source,
    )
    legs_written += len(new_trades)
    st["legs_written"] = legs_written
    st["last_bar_ts"] = setup.get("last_bar_ts") or last_ts
    st["phase"] = PHASE_DONE if legs_written >= len(trades) else PHASE_AFTER_LEG1
    st["day_snapshot"] = {
        **(setup.get("day_result") or {}),
        "stock_code": code,
        "stock_name": holding.get("stock_name"),
    }
    return st, new_trades, st["day_snapshot"]


def run_intraday_session_tick(
    paper: dict,
    *,
    holdings_ctx: List[dict],
    log_source: str = "paper_t0_auto",
) -> Dict[str, Any]:
    """对持仓跑一轮 5m 增量盯盘；返回 tick 摘要。"""
    from core.market.calendar import resolve_session_date
    from core.signal.session_pit import shanghai_now

    sess = resolve_session_date(now=shanghai_now())
    state = load_intraday_state()
    if str(state.get("session_date") or "") != str(sess or ""):
        state = {"session_date": sess, "stocks": {}, "results": []}

    stocks: Dict[str, Any] = dict(state.get("stocks") or {})
    results: List[dict] = list(state.get("results") or [])
    all_new: List[dict] = []
    skip_n = 0

    for ctx in holdings_ctx:
        code = str(ctx.get("code") or "")
        if not code:
            continue
        holding = ctx.get("holding")
        if not isinstance(holding, dict):
            continue
        st, new_trades, snap = process_holding_intraday(
            code=code,
            holding=holding,
            stock_state=stocks.get(code),
            minute_bars=list(ctx.get("minute_bars") or []),
            bar=ctx.get("bar") or {},
            cfg=ctx.get("cfg") or {},
            sellable=float(ctx.get("sellable") or 0),
            cash=float(paper.get("cash") or 0),
            atr_pct=ctx.get("atr_pct"),
            hist_bars=ctx.get("hist_bars"),
            scores=ctx.get("scores"),
            stance_code=ctx.get("stance_code"),
            coupling_mode=str(ctx.get("coupling_mode") or "independent"),
            as_of=str(sess or "")[:10],
            log_source=log_source,
            paper=paper,
            applied_legs=int((stocks.get(code) or {}).get("legs_written") or 0),
        )
        stocks[code] = st
        if new_trades:
            all_new.extend(new_trades)
        if snap:
            # 更新 results 里该票快照
            results = [r for r in results if str(r.get("stock_code") or "") != code]
            results.append(snap)
        if st.get("phase") == PHASE_SKIPPED:
            skip_n += 1

    state["session_date"] = sess
    state["stocks"] = stocks
    state["results"] = results
    save_intraday_state(state)

    return {
        "ok": True,
        "session_date": sess,
        "new_trades": all_new,
        "trade_count": len(all_new),
        "skip_count": skip_n,
        "results": results,
        "stocks": stocks,
    }


def sync_intraday_state_from_full_run(results: Optional[List[dict]], *, session_date: Optional[str] = None) -> None:
    """手动/日终整单 simulate 后标记盘中状态，避免 Worker 重复落账。"""
    from core.market.calendar import resolve_session_date
    from core.signal.session_pit import shanghai_now

    sess = session_date or resolve_session_date(now=shanghai_now())
    state = load_intraday_state()
    if str(state.get("session_date") or "") != str(sess or ""):
        state = {"session_date": sess, "stocks": {}, "results": []}
    stocks: Dict[str, Any] = dict(state.get("stocks") or {})
    merged: List[dict] = []
    for r in results or []:
        if not isinstance(r, dict):
            continue
        code = str(r.get("stock_code") or "")
        if not code:
            continue
        trades = list(r.get("trades") or [])
        merged.append(r)
        if r.get("skipped"):
            stocks[code] = {"phase": PHASE_SKIPPED, "legs_written": 0, "day_snapshot": r}
        elif trades:
            stocks[code] = {
                "phase": PHASE_DONE,
                "legs_written": len(trades),
                "day_snapshot": r,
            }
        else:
            stocks[code] = {"phase": PHASE_IDLE, "legs_written": 0}
    state["session_date"] = sess
    state["stocks"] = stocks
    state["results"] = merged
    save_intraday_state(state)
