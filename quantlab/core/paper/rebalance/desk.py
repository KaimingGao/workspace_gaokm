"""自动调仓盯盘桌面：开盘窗阶段 + 逐票动作（对齐做 T 的今日盯盘状态）。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PHASE_STOPPED = "stopped"
PHASE_WAIT = "wait"
PHASE_WATCH = "watch"
PHASE_RUNNING = "running"
PHASE_DONE = "done"
PHASE_MISSED = "missed"
PHASE_HOLIDAY = "holiday"

MAX_DESK_ROWS = 80

_ACTION_RANK = {
    "exit": 0,
    "reduce": 1,
    "open": 2,
    "add": 3,
    "skip": 4,
    "hold": 5,
    "watch": 6,
}


def resolve_desk_phase(
    *,
    enabled: bool = False,
    inflight: bool = False,
    now: Any = None,
    paper: Optional[dict] = None,
) -> str:
    from core.paper.rebalance.auto_worker import (
        already_ran_today,
        after_auto_rebalance_window,
        in_auto_rebalance_window,
        is_trading_session,
    )

    if inflight:
        return PHASE_RUNNING
    if already_ran_today(now):
        return PHASE_DONE
    if not is_trading_session(now):
        return PHASE_HOLIDAY
    if in_auto_rebalance_window(now, paper=paper):
        return PHASE_WATCH if enabled else PHASE_STOPPED
    if after_auto_rebalance_window(now, paper=paper):
        return PHASE_MISSED
    return PHASE_WAIT if enabled else PHASE_STOPPED


def _f(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return v


def _round_px(x: Any) -> Optional[float]:
    v = _f(x)
    if v is None or v <= 0:
        return None
    return round(float(v), 4)


def _round_amt(x: Any) -> Optional[float]:
    v = _f(x)
    if v is None:
        return None
    return round(float(v), 2)


def _compact_row(
    *,
    stock_code: str,
    stock_name: str = "",
    action: Optional[str] = None,
    phase: str = PHASE_WATCH,
    shares: Optional[float] = None,
    ranking_score: Optional[float] = None,
    reason: str = "",
    held: bool = False,
    price: Any = None,
    amount: Any = None,
    prev_close: Any = None,
    day_open: Any = None,
    y_oo: Any = None,
    y_τc: Any = None,
    y_co: Any = None,
    ranking: Any = None,
    old_shares: Any = None,
    new_shares: Any = None,
    ts: Any = None,
    side: Any = None,
) -> Dict[str, Any]:
    code = str(stock_code or "").strip()
    act = str(action or "").strip().lower() or None
    sh = _f(shares)
    rs = _f(ranking_score)
    out = {
        "stock_code": code,
        "stock_name": str(stock_name or "").strip() or code,
        "action": act,
        "phase": str(phase or PHASE_WATCH),
        "shares": sh,
        "ranking_score": rs,
        "reason": str(reason or "").strip(),
        "held": bool(held),
    }
    px = _round_px(price)
    if px is not None:
        out["price"] = px
    amt = _round_amt(amount)
    if amt is not None:
        out["amount"] = amt
    pc = _round_px(prev_close)
    if pc is not None:
        out["prev_close"] = pc
    day_o = _round_px(day_open)
    if day_o is not None:
        out["day_open"] = day_o
    for key, raw in (("y_oo", y_oo), ("y_τc", y_τc), ("y_co", y_co), ("ranking", ranking)):
        v = _f(raw)
        if v is not None:
            out[key] = round(float(v), 6)
    old_sh = _f(old_shares)
    if old_sh is not None:
        out["old_shares"] = old_sh
    new_sh = _f(new_shares)
    if new_sh is not None:
        out["new_shares"] = new_sh
    ts_s = str(ts or "").strip()
    if ts_s:
        out["ts"] = ts_s
    side_s = str(side or "").strip().lower()
    if side_s in ("buy", "sell"):
        out["side"] = side_s
    return out


def _phase_for_action(action: Optional[str], *, filled: bool) -> str:
    act = str(action or "").strip().lower()
    if not filled:
        return PHASE_WATCH
    if act in ("open", "add", "exit", "reduce"):
        return PHASE_DONE
    if act == "hold":
        return "hold"
    if act == "skip":
        return "skip"
    return PHASE_DONE


def desk_rows_from_result(result: Optional[dict], *, filled: bool = True) -> List[dict]:
    """从 rank_lots 落账/预演结果抽盯盘行。"""
    src = result if isinstance(result, dict) else {}
    rows: List[dict] = []
    seen: set = set()

    def _add(row: dict) -> None:
        code = str(row.get("stock_code") or "").strip()
        if not code or code in seen:
            return
        seen.add(code)
        rows.append(row)

    for r in src.get("rebalance_report") or []:
        if not isinstance(r, dict):
            continue
        act = str(r.get("action") or "").strip().lower()
        sh = r.get("shares_change")
        if sh is None:
            sh = r.get("shares")
        _add(
            _compact_row(
                stock_code=str(r.get("stock_code") or ""),
                stock_name=str(r.get("stock_name") or ""),
                action=act or None,
                phase=_phase_for_action(act, filled=filled),
                shares=abs(float(sh)) if _f(sh) is not None else None,
                ranking_score=r.get("ranking_score"),
                reason=str(r.get("reason") or r.get("decision") or ""),
                held=float(r.get("old_shares") or 0) > 0,
                price=r.get("price"),
                amount=r.get("amount"),
                prev_close=r.get("prev_close"),
                day_open=r.get("day_open") if r.get("day_open") is not None else r.get("open"),
                y_oo=r.get("y_oo"),
                y_τc=r.get("y_τc"),
                y_co=r.get("y_co"),
                ranking=r.get("ranking") if r.get("ranking") is not None else r.get("y_fuse"),
                old_shares=r.get("old_shares"),
                new_shares=r.get("new_shares"),
                ts=r.get("ts"),
                side=r.get("side"),
            )
        )
    for t, act in (
        (src.get("sell_trades") or [], "exit"),
        (src.get("buy_trades") or [], None),
    ):
        for leg in t:
            if not isinstance(leg, dict):
                continue
            code = str(leg.get("stock_code") or "").strip()
            if not code or code in seen:
                continue
            use_act = str(leg.get("action") or act or "").strip().lower()
            if use_act not in ("open", "add", "exit", "reduce"):
                use_act = "add" if float(leg.get("old_shares") or 0) > 0 else "open"
                if act == "exit":
                    use_act = "exit"
            _add(
                _compact_row(
                    stock_code=code,
                    stock_name=str(leg.get("stock_name") or ""),
                    action=use_act,
                    phase=_phase_for_action(use_act, filled=filled),
                    shares=leg.get("shares"),
                    ranking_score=leg.get("ranking_score"),
                    reason=str(leg.get("reason") or ""),
                    held=use_act in ("add", "exit", "reduce"),
                    price=leg.get("price"),
                    amount=leg.get("amount"),
                    prev_close=leg.get("prev_close"),
                    day_open=leg.get("day_open") if leg.get("day_open") is not None else leg.get("open"),
                    y_oo=leg.get("y_oo"),
                    y_τc=leg.get("y_τc"),
                    y_co=leg.get("y_co"),
                    ranking=leg.get("ranking") if leg.get("ranking") is not None else leg.get("y_fuse"),
                    old_shares=leg.get("old_shares"),
                    new_shares=leg.get("new_shares"),
                    ts=leg.get("ts"),
                    side=leg.get("side") or ("sell" if act == "exit" else "buy"),
                )
            )
    for s in src.get("risk_budget_skips") or src.get("apply_skips") or []:
        if not isinstance(s, dict):
            continue
        _add(
            _compact_row(
                stock_code=str(s.get("stock_code") or ""),
                stock_name=str(s.get("stock_name") or ""),
                action="skip",
                phase=_phase_for_action("skip", filled=filled),
                shares=s.get("shares"),
                ranking_score=s.get("ranking_score"),
                reason=str(s.get("reason") or "跳过"),
                held=False,
                price=s.get("price"),
                ranking=s.get("ranking") if s.get("ranking") is not None else s.get("y_fuse"),
                y_oo=s.get("y_oo"),
                y_τc=s.get("y_τc"),
                y_co=s.get("y_co"),
            )
        )
    _overlay_trades_on_desk_rows(rows, src)
    rows.sort(
        key=lambda r: (
            _ACTION_RANK.get(str(r.get("action") or "watch"), 9),
            str(r.get("stock_code") or ""),
        )
    )
    return rows[:MAX_DESK_ROWS]


def _overlay_fields(
    row: dict,
    src: dict,
    *,
    overwrite_price: bool = False,
    overwrite_ranking: bool = False,
) -> None:
    """把成交/ŷ 补进盯盘行。落账价优先用 trade.price（报告价可能被盯市现价覆盖）。"""
    if not isinstance(row, dict) or not isinstance(src, dict):
        return
    if overwrite_price or row.get("price") is None:
        px = _round_px(src.get("price"))
        if px is not None:
            row["price"] = px
    if overwrite_price or row.get("amount") is None:
        amt = _round_amt(src.get("amount"))
        if amt is not None:
            row["amount"] = amt
    if row.get("prev_close") is None:
        pc = _round_px(src.get("prev_close"))
        if pc is not None:
            row["prev_close"] = pc
    if row.get("day_open") is None:
        opx = _round_px(src.get("day_open") if src.get("day_open") is not None else src.get("open"))
        if opx is not None:
            row["day_open"] = opx
    for key in ("y_oo", "y_τc", "y_co"):
        if row.get(key) is None and src.get(key) is not None:
            v = _f(src.get(key))
            if v is not None:
                row[key] = round(float(v), 6)
    rk = src.get("ranking") if src.get("ranking") is not None else src.get("y_fuse")
    if overwrite_ranking or row.get("ranking") is None:
        v = _f(rk)
        if v is not None:
            row["ranking"] = round(float(v), 6)
    if overwrite_ranking:
        rs = _f(src.get("ranking_score"))
        if rs is not None:
            row["ranking_score"] = float(rs)
    if row.get("ts") is None and src.get("ts"):
        row["ts"] = str(src.get("ts") or "").strip()
    if row.get("side") is None:
        side = str(src.get("side") or "").strip().lower()
        if side in ("buy", "sell"):
            row["side"] = side
    if row.get("old_shares") is None and src.get("old_shares") is not None:
        row["old_shares"] = _f(src.get("old_shares"))
    if row.get("new_shares") is None and src.get("new_shares") is not None:
        row["new_shares"] = _f(src.get("new_shares"))


def _overlay_trades_on_desk_rows(rows: List[dict], result: dict) -> None:
    """成交腿上的价/额/时覆盖报告行（避免 attach_change_pct 把 price 改成现价）。"""
    by = {str(r.get("stock_code") or "").strip(): r for r in rows if r.get("stock_code")}
    for t in list(result.get("sell_trades") or []) + list(result.get("buy_trades") or []):
        if not isinstance(t, dict):
            continue
        code = str(t.get("stock_code") or "").strip()
        row = by.get(code)
        if row is None:
            continue
        _overlay_fields(row, t, overwrite_price=True, overwrite_ranking=True)


def _session_trades(paper: Optional[dict], session: str) -> Dict[str, dict]:
    sess = str(session or "").strip()[:10]
    out: Dict[str, dict] = {}
    if not sess:
        return out
    for t in (paper or {}).get("trades") or []:
        if not isinstance(t, dict):
            continue
        ts = str(t.get("ts") or "")
        if not ts.startswith(sess):
            continue
        origin = str(t.get("origin") or "")
        if origin not in ("strategy", "mixed") and not t.get("matrix_action"):
            continue
        code = str(t.get("stock_code") or "").strip()
        if code:
            out[code] = t
    return out


def hydrate_desk_rows_from_paper(
    rows: List[dict],
    paper: Optional[dict],
    session: str,
) -> List[dict]:
    """用当日策略成交补盯盘行的成交价/金额/ranking。"""
    trades = _session_trades(paper, session)
    if not trades:
        return rows
    for row in rows:
        if not isinstance(row, dict):
            continue
        t = trades.get(str(row.get("stock_code") or "").strip())
        if t:
            _overlay_fields(
                row,
                t,
                overwrite_price=row.get("price") is None,
                overwrite_ranking=True,
            )
    return rows


def _resolve_row_open(
    quote: Optional[dict],
    bars: Optional[List[dict]],
    session: str,
    resolve_open_t: Any,
) -> Optional[float]:
    if resolve_open_t is None:
        return None
    try:
        got = resolve_open_t(quote or {}, bars or [], trade_day=session)
        return _round_px((got or {}).get("open"))
    except Exception:  # noqa: BLE001
        logger.debug("desk resolve_open_t failed", exc_info=True)
        return None


def attach_day_open_to_desk_rows(
    rows: List[dict],
    *,
    session: str = "",
) -> List[dict]:
    """补今开。不改成交价。行情今开（须为 T 日）优先，缺再读今日日 K。"""
    pending = [
        r
        for r in rows
        if isinstance(r, dict)
        and str(r.get("stock_code") or "").strip()
        and _round_px(r.get("day_open")) is None
    ]
    if not pending:
        return rows
    codes = list(
        dict.fromkeys(str(r.get("stock_code") or "").strip() for r in pending)
    )
    quotes: Dict[str, Any] = {}
    try:
        from core.paper.rebalance.match import _batch_query_quotes

        quotes = _batch_query_quotes(codes) or {}
    except Exception:  # noqa: BLE001 — 盯盘展示降级，不挡主流程
        logger.debug("desk day_open quotes failed", exc_info=True)
    try:
        from core.data.facade import get_bars
        from core.signal.session_pit import resolve_open_t
    except Exception:  # noqa: BLE001
        logger.debug("desk day_open resolve import failed", exc_info=True)
        get_bars = None  # type: ignore
        resolve_open_t = None  # type: ignore
    sess = str(session or "").strip()[:10]
    still: List[tuple] = []
    for row in pending:
        code = str(row.get("stock_code") or "").strip()
        q = quotes.get(code) if isinstance(quotes.get(code), dict) else {}
        opx = _resolve_row_open(q, [], sess, resolve_open_t)
        if opx is not None:
            row["day_open"] = opx
        else:
            still.append((row, code, q))
    if not still or get_bars is None:
        return rows
    for row, code, q in still:
        bars: List[dict] = []
        try:
            pack = (
                get_bars(
                    code,
                    limit=5,
                    offline_only=True,
                    reject_quote_fallback=True,
                )
                or {}
            )
            bars = [b for b in (pack.get("bars") or []) if isinstance(b, dict)]
        except Exception:  # noqa: BLE001
            logger.debug("desk day_open bars failed for %s", code, exc_info=True)
        opx = _resolve_row_open(q, bars, sess, resolve_open_t)
        if opx is not None:
            row["day_open"] = opx
    return rows


def desk_rows_from_holdings(
    paper: Optional[dict],
    *,
    phase: str = PHASE_WATCH,
    reason: str = "",
) -> List[dict]:
    rows: List[dict] = []
    for h in (paper or {}).get("holdings") or []:
        if not isinstance(h, dict):
            continue
        code = str(h.get("stock_code") or "").strip()
        if not code:
            continue
        sh = _f(h.get("shares")) or 0.0
        if sh <= 0:
            continue
        rows.append(
            _compact_row(
                stock_code=code,
                stock_name=str(h.get("stock_name") or ""),
                action="watch",
                phase=phase,
                shares=sh,
                reason=reason,
                held=True,
            )
        )
    rows.sort(key=lambda r: str(r.get("stock_code") or ""))
    return rows[:MAX_DESK_ROWS]


def save_last_desk(
    session: str,
    rows: List[dict],
    *,
    filled: bool = True,
    source: str = "auto",
    note: str = "",
) -> None:
    from core.paper.rebalance.auto_worker import _save_state

    sess = str(session or "").strip()[:10]
    if not sess:
        return
    compact = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        code = str(r.get("stock_code") or "").strip()
        if not code:
            continue
        compact.append(
            _compact_row(
                stock_code=code,
                stock_name=str(r.get("stock_name") or ""),
                action=r.get("action"),
                phase=str(r.get("phase") or PHASE_WATCH),
                shares=r.get("shares"),
                ranking_score=r.get("ranking_score"),
                reason=str(r.get("reason") or ""),
                held=bool(r.get("held")),
                price=r.get("price"),
                amount=r.get("amount"),
                prev_close=r.get("prev_close"),
                day_open=r.get("day_open"),
                y_oo=r.get("y_oo"),
                y_τc=r.get("y_τc"),
                y_co=r.get("y_co"),
                ranking=r.get("ranking"),
                old_shares=r.get("old_shares"),
                new_shares=r.get("new_shares"),
                ts=r.get("ts"),
                side=r.get("side"),
            )
        )
        if len(compact) >= MAX_DESK_ROWS:
            break
    _save_state(
        {
            "last_desk": {
                "session_date": sess,
                "filled": bool(filled),
                "source": str(source or "auto"),
                "note": str(note or ""),
                "rows": compact,
            }
        }
    )


def persist_desk_from_result(
    result: Optional[dict],
    session: str,
    *,
    source: str = "auto",
    filled: bool = True,
) -> None:
    rows = desk_rows_from_result(result, filled=filled)
    note = ""
    if isinstance(result, dict):
        note = str(result.get("note") or "")
    save_last_desk(session, rows, filled=filled, source=source, note=note)


def _load_paper_safe() -> dict:
    try:
        from core.paper import load_paper
        from core.paths import PAPER_PATH

        return load_paper(PAPER_PATH)
    except Exception:  # noqa: BLE001
        logger.debug("rebalance desk load paper failed", exc_info=True)
        return {}


def _counts(rows: List[dict]) -> Dict[str, int]:
    out = {
        "watch": 0,
        "open": 0,
        "add": 0,
        "exit": 0,
        "reduce": 0,
        "hold": 0,
        "skip": 0,
        "done": 0,
        "other": 0,
    }
    for r in rows:
        act = str(r.get("action") or "")
        phase = str(r.get("phase") or "")
        if act in out:
            out[act] += 1
        elif phase in out:
            out[phase] += 1
        else:
            out["other"] += 1
        if phase == PHASE_DONE:
            out["done"] += 1
    return out


def _note_for_phase(
    phase: str, *, aligned: bool, n: int, enabled: bool, fill_clock: str = "09:30"
) -> str:
    clock = str(fill_clock or "09:30")[:5]
    window = f"{clock}–10:00"
    if phase == PHASE_RUNNING:
        return "正在现价落账…"
    if phase == PHASE_DONE:
        return (
            "今日已调仓 · 开/加/减/清为已落账；持=ranking>入场未动；跳=地板/T+1/OOS 等"
            if aligned
            else "今日已调仓（明细未写入盯盘）"
        )
    if phase == PHASE_WATCH:
        return f"开盘窗内监视 · 现价成交一次后本表换成落账动作"
    if phase == PHASE_WAIT:
        return f"等待 {clock} 开盘窗 · 按已保存 rank_lots 规则现价成交一次"
    if phase == PHASE_MISSED:
        return "已过 10:00，今日不再补跑"
    if phase == PHASE_HOLIDAY:
        return "非交易日"
    if not enabled:
        return (
            f"后台未开；打开「运行」后将在 {window} 现价落账"
            if n
            else "尚无持仓或 Worker 未开"
        )
    return "等待下一开盘窗"


def build_rebalance_desk_status(
    *,
    worker: Optional[dict] = None,
    now: Any = None,
) -> Dict[str, Any]:
    """今日调仓盯盘：窗内按持仓占位监视，落账后展示开/加/减/清/持。"""
    from core.paper.rebalance.auto_worker import (
        _load_state,
        already_ran_today,
        last_run_session,
        load_enabled_flag,
        rebalance_fill_clock,
        rebalance_window_label,
        resolve_session,
    )

    w = worker if isinstance(worker, dict) else {}
    enabled = bool(w.get("enabled") if "enabled" in w else load_enabled_flag())
    inflight = bool(w.get("inflight"))
    sess = resolve_session(now)
    paper = _load_paper_safe()
    phase = resolve_desk_phase(enabled=enabled, inflight=inflight, now=now, paper=paper)
    persisted = _load_state().get("last_desk")
    persisted = persisted if isinstance(persisted, dict) else {}
    persisted_sess = str(persisted.get("session_date") or "")[:10]
    aligned = bool(sess and persisted_sess and sess == persisted_sess)
    filled_today = already_ran_today(now) or (
        aligned and bool(persisted.get("filled"))
    )
    fill_clock = rebalance_fill_clock(paper)
    window = rebalance_window_label(paper)
    live_start = window.split("–")[0]
    if aligned and (filled_today or phase == PHASE_DONE):
        rows = list(persisted.get("rows") or [])
        hydrate_desk_rows_from_paper(rows, paper, sess or persisted_sess)
        state_aligned = True
    elif phase == PHASE_MISSED:
        rows = desk_rows_from_holdings(
            paper, phase="skip", reason="错过开盘窗，今日不补跑"
        )
        state_aligned = False
    else:
        reason = {
            PHASE_WATCH: "开盘窗内监视",
            PHASE_WAIT: f"等待 {live_start} 开盘窗",
            PHASE_RUNNING: "正在落账",
            PHASE_HOLIDAY: "非交易日",
            PHASE_STOPPED: "等待 Worker 开盘窗落账",
        }.get(phase, "等待调仓")
        rows = desk_rows_from_holdings(paper, phase=PHASE_WATCH, reason=reason)
        state_aligned = False

    attach_day_open_to_desk_rows(rows, session=sess or persisted_sess)

    counts = _counts(rows)
    buy_amt = 0.0
    sell_amt = 0.0
    last_ts = ""
    for r in rows:
        amt = _f(r.get("amount")) or 0.0
        act = str(r.get("action") or "")
        side = str(r.get("side") or "")
        if side == "sell" or act in ("exit", "reduce"):
            sell_amt += amt
        elif side == "buy" or act in ("open", "add"):
            buy_amt += amt
        ts = str(r.get("ts") or "")
        if ts > last_ts:
            last_ts = ts
    return {
        "session_date": sess or persisted_sess or None,
        "state_session_date": persisted_sess or last_run_session() or None,
        "state_aligned": state_aligned,
        "same_session": bool(sess and rows),
        "phase": phase,
        "filled": bool(filled_today),
        "universe_count": len(rows),
        "counts": counts,
        "open_count": int(counts.get("open") or 0) + int(counts.get("add") or 0),
        "exit_count": int(counts.get("exit") or 0),
        "reduce_count": int(counts.get("reduce") or 0),
        "rows": rows,
        "note": _note_for_phase(
            phase,
            aligned=state_aligned,
            n=len(rows),
            enabled=enabled,
            fill_clock=live_start,
        ),
        "fill_clock": fill_clock,
        "live_fill_clock": fill_clock,
        "window_label": window,
        "source": str(persisted.get("source") or "") if aligned else "",
        "fill_ts": last_ts or None,
        "buy_amount": round(buy_amt, 2) if buy_amt else 0.0,
        "sell_amount": round(sell_amt, 2) if sell_amt else 0.0,
    }
