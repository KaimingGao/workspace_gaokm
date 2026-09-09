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
    "open": 1,
    "add": 2,
    "skip": 3,
    "hold": 4,
    "watch": 5,
}


def resolve_desk_phase(
    *,
    enabled: bool = False,
    inflight: bool = False,
    now: Any = None,
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
    if in_auto_rebalance_window(now):
        return PHASE_WATCH if enabled else PHASE_STOPPED
    if after_auto_rebalance_window(now):
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
) -> Dict[str, Any]:
    code = str(stock_code or "").strip()
    act = str(action or "").strip().lower() or None
    sh = _f(shares)
    rs = _f(ranking_score)
    return {
        "stock_code": code,
        "stock_name": str(stock_name or "").strip() or code,
        "action": act,
        "phase": str(phase or PHASE_WATCH),
        "shares": sh,
        "ranking_score": rs,
        "reason": str(reason or "").strip(),
        "held": bool(held),
    }


def _phase_for_action(action: Optional[str], *, filled: bool) -> str:
    act = str(action or "").strip().lower()
    if not filled:
        return PHASE_WATCH
    if act in ("open", "add", "exit"):
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
            if use_act not in ("open", "add", "exit"):
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
                    held=use_act in ("add", "exit"),
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
            )
        )
    rows.sort(
        key=lambda r: (
            _ACTION_RANK.get(str(r.get("action") or "watch"), 9),
            str(r.get("stock_code") or ""),
        )
    )
    return rows[:MAX_DESK_ROWS]


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


def _note_for_phase(phase: str, *, aligned: bool, n: int, enabled: bool) -> str:
    if phase == PHASE_RUNNING:
        return "正在现价落账…"
    if phase == PHASE_DONE:
        return (
            "今日已调仓 · 开/加/清为已落账；持=ranking≥0 未动；跳=地板/T+1/OOS 等"
            if aligned
            else "今日已调仓（明细未写入盯盘）"
        )
    if phase == PHASE_WATCH:
        return "开盘窗内监视 · 现价成交一次后本表换成落账动作"
    if phase == PHASE_WAIT:
        return "等待 09:30 开盘窗 · 按已保存 rank_lots 规则现价成交一次"
    if phase == PHASE_MISSED:
        return "已过 10:00，今日不再补跑"
    if phase == PHASE_HOLIDAY:
        return "非交易日"
    if not enabled:
        return (
            "后台未开；打开「运行」后将在 09:30–10:00 现价落账"
            if n
            else "尚无持仓或 Worker 未开"
        )
    return "等待下一开盘窗"


def build_rebalance_desk_status(
    *,
    worker: Optional[dict] = None,
    now: Any = None,
) -> Dict[str, Any]:
    """今日调仓盯盘：窗内按持仓占位监视，落账后展示开/加/清/持。"""
    from core.paper.rebalance.auto_worker import (
        _load_state,
        already_ran_today,
        last_run_session,
        load_enabled_flag,
        resolve_session,
    )

    w = worker if isinstance(worker, dict) else {}
    enabled = bool(w.get("enabled") if "enabled" in w else load_enabled_flag())
    inflight = bool(w.get("inflight"))
    sess = resolve_session(now)
    phase = resolve_desk_phase(enabled=enabled, inflight=inflight, now=now)
    persisted = _load_state().get("last_desk")
    persisted = persisted if isinstance(persisted, dict) else {}
    persisted_sess = str(persisted.get("session_date") or "")[:10]
    aligned = bool(sess and persisted_sess and sess == persisted_sess)
    filled_today = already_ran_today(now) or (
        aligned and bool(persisted.get("filled"))
    )

    paper = _load_paper_safe()
    if aligned and (filled_today or phase == PHASE_DONE):
        rows = list(persisted.get("rows") or [])
        state_aligned = True
    elif phase == PHASE_MISSED:
        rows = desk_rows_from_holdings(
            paper, phase="skip", reason="错过开盘窗，今日不补跑"
        )
        state_aligned = False
    else:
        reason = {
            PHASE_WATCH: "开盘窗内监视",
            PHASE_WAIT: "等待 09:30 开盘窗",
            PHASE_RUNNING: "正在落账",
            PHASE_HOLIDAY: "非交易日",
            PHASE_STOPPED: "等待 Worker 开盘窗落账",
        }.get(phase, "等待调仓")
        rows = desk_rows_from_holdings(paper, phase=PHASE_WATCH, reason=reason)
        state_aligned = False

    counts = _counts(rows)
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
        "rows": rows,
        "note": _note_for_phase(
            phase, aligned=state_aligned, n=len(rows), enabled=enabled
        ),
        "source": str(persisted.get("source") or "") if aligned else "",
    }
