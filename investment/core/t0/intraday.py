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


def past_morning_close(*, now: Any = None) -> bool:
    """A 股上午收盘 11:30 及之后：不再因日线/算分未就绪一直等待。"""
    from core.signal.session_pit import shanghai_now

    n = shanghai_now(now)
    return (n.hour, n.minute) >= (11, 30)


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


def build_intraday_desk_status() -> Dict[str, Any]:
    """今日盘中盯盘桌面：逐票 phase / 原因（供 Follow 做 T 后台区展示）。"""
    from core.market.calendar import resolve_session_date
    from core.signal.session_pit import shanghai_now

    sess = str(resolve_session_date(now=shanghai_now()) or "")[:10]
    state = load_intraday_state()
    state_sess = str(state.get("session_date") or "")[:10]
    stocks_raw = state.get("stocks") if isinstance(state.get("stocks"), dict) else {}
    same_session = bool(sess and state_sess and sess == state_sess)

    phase_rank = {
        PHASE_SKIPPED: 0,
        PHASE_IDLE: 1,
        PHASE_AFTER_LEG1: 2,
        PHASE_DONE: 3,
    }
    rows: List[Dict[str, Any]] = []
    counts = {
        PHASE_IDLE: 0,
        PHASE_AFTER_LEG1: 0,
        PHASE_DONE: 0,
        PHASE_SKIPPED: 0,
        "other": 0,
    }
    locked_n = 0
    legs_total = 0

    if same_session:
        for code, raw in stocks_raw.items():
            if not isinstance(raw, dict):
                continue
            c = str(code or "").strip()
            if not c:
                continue
            phase = str(raw.get("phase") or PHASE_IDLE).strip().lower() or PHASE_IDLE
            if phase not in counts:
                counts["other"] += 1
            else:
                counts[phase] += 1
            snap = raw.get("day_snapshot") if isinstance(raw.get("day_snapshot"), dict) else {}
            reason = str(
                raw.get("reason")
                or raw.get("wait_reason")
                or snap.get("reason")
                or snap.get("direction_reason")
                or ""
            ).strip()
            legs = int(raw.get("legs_written") or 0)
            legs_total += max(0, legs)
            locked = phase == PHASE_SKIPPED
            if locked:
                locked_n += 1
            direction = str(
                raw.get("direction")
                or snap.get("direction_used")
                or snap.get("direction")
                or ""
            ).strip()
            signal_skip = bool(snap.get("signal_skip"))
            unlocked = str(raw.get("unlocked_from_skip") or "").strip()
            rows.append(
                {
                    "stock_code": c,
                    "stock_name": str(snap.get("stock_name") or raw.get("stock_name") or ""),
                    "phase": phase,
                    "locked": locked,
                    "signal_skip": signal_skip,
                    "legs_written": legs,
                    "reason": reason,
                    "direction": direction or None,
                    "last_bar_ts": str(raw.get("last_bar_ts") or "")[:19] or None,
                    "unlocked_from_skip": unlocked or None,
                    "ref": snap.get("ref"),
                    "range_pct": snap.get("range_pct"),
                }
            )

    rows.sort(
        key=lambda r: (
            phase_rank.get(str(r.get("phase")), 9),
            str(r.get("stock_code") or ""),
        )
    )
    return {
        "session_date": sess or state_sess or None,
        "state_session_date": state_sess or None,
        "same_session": same_session,
        "updated_at": state.get("updated_at"),
        "universe_count": len(rows),
        "counts": counts,
        "locked_count": locked_n,
        "legs_written_total": legs_total,
        "rows": rows,
        "note": (
            "skipped=终锁：≥11:30 无成交腿，或 dual_y 门槛 / stance；"
            "上午振幅/未触价可重试；idle=盯盘；after_leg1=已第一腿；done=当日完成"
            if same_session
            else "尚无今日盘中状态（Worker 未开或未进入交易时段）"
        ),
    }


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


def needs_midday_dual_y_gate(stock_state: Optional[dict]) -> bool:
    """午盘后：无成交腿则应终锁（含尚未定方向 / 未触价）。"""
    st = stock_state if isinstance(stock_state, dict) else {}
    phase = str(st.get("phase") or PHASE_IDLE)
    if phase in (PHASE_DONE, PHASE_SKIPPED):
        return False
    if int(st.get("legs_written") or 0) > 0:
        return False
    return True


def lock_zero_legs_after_morning(
    *,
    code: str,
    holding: Optional[dict] = None,
    session_date: Optional[str] = None,
    reason: str = "午盘后无成交腿，跳过",
) -> dict:
    """≥11:30 且腿数为 0：写入 skipped + score_locked。"""
    name = str((holding or {}).get("stock_name") or "")
    snap = {
        "success": True,
        "skipped": True,
        "signal_skip": True,
        "reason": reason,
        "date": str(session_date or "")[:10] or None,
        "stock_code": code,
        "stock_name": name,
        "trades": [],
    }
    return {
        "phase": PHASE_SKIPPED,
        "legs_written": 0,
        "score_locked": True,
        "reason": reason,
        "day_snapshot": snap,
    }


def _retryable_skip(reason: str) -> bool:
    """可重试跳过：振幅/未触价等盘口条件，等下一根 5m；不写入 skipped 终态。

    dual_y 门槛（横盘/y_check/幅度）在日线对齐且即时分就绪后应终锁，故不在此列。
    """
    r = str(reason or "")
    return any(
        x in r
        for x in ("振幅", "分钟", "未触及", "等待", "上移振幅", "下移振幅", "方向振幅")
    )


def _dual_y_threshold_skip(reason: str) -> bool:
    """dual_y 分数门槛不达标（对齐后可终锁）。"""
    r = str(reason or "")
    if "dual_y" not in r:
        return False
    return any(
        x in r
        for x in (
            "横盘跳过",
            "预期幅度不足",
            "缺 y_τ",
            "缺 y_eod",
            "即时算分失败",
            "即时算分仍未就绪",
            "禁止做T",
            "午盘后",
            "无成交腿",
            "异号",
        )
    )


def unlock_retryable_skipped(stock_state: Optional[dict]) -> Optional[dict]:
    """解锁：盘口可重试；或旧版 dual_y 软锁（无 score_locked）以便用即时分重评一次。"""
    st = stock_state if isinstance(stock_state, dict) else {}
    if str(st.get("phase") or "") != PHASE_SKIPPED:
        return None
    if st.get("score_locked"):
        return None
    snap = st.get("day_snapshot") if isinstance(st.get("day_snapshot"), dict) else {}
    reason = str(st.get("reason") or snap.get("reason") or snap.get("direction_reason") or "")
    if _retryable_skip(reason) or _dual_y_threshold_skip(reason):
        return {
            "phase": PHASE_IDLE,
            "legs_written": int(st.get("legs_written") or 0),
            "last_bar_ts": "",
            "unlocked_from_skip": reason[:160],
        }
    return None


def align_session_day_context(
    *,
    session_date: Optional[str],
    daily_bars: List[dict],
    minute_by_day: Optional[Dict[str, List[dict]]] = None,
) -> Dict[str, Any]:
    """日线/分钟对齐到交易日 ``session_date``。

    日线缓存未滚到当日时：hist=全部已收盘日线，day 用当日分钟开盘合成；
    分钟只取 session 当日，避免误用昨分钟。
    """
    sess = str(session_date or "")[:10]
    bars = [b for b in (daily_bars or []) if isinstance(b, dict)]
    by_day = minute_by_day if isinstance(minute_by_day, dict) else {}
    if not bars:
        return {
            "ok": False,
            "pending": True,
            "reason": "缺日线",
            "bar": None,
            "hist": [],
            "minute_bars": [],
        }

    last = dict(bars[-1])
    last_d = str(last.get("date") or "")[:10]
    minutes: List[dict] = []
    if sess and by_day.get(sess):
        minutes = list(by_day.get(sess) or [])
    elif last_d and (not sess or last_d == sess) and by_day.get(last_d):
        minutes = list(by_day.get(last_d) or [])

    if sess and last_d and last_d < sess:
        hist = list(bars)
        prev_c = float(last.get("close") or 0) or None
        day: Dict[str, Any] = {"date": sess}
        if prev_c and prev_c > 0:
            day["prev_close"] = prev_c
        if minutes:
            try:
                o = float((minutes[0] or {}).get("open") or 0)
                if o > 0:
                    day["open"] = o
                highs = [
                    float(m.get("high") or 0)
                    for m in minutes
                    if float(m.get("high") or 0) > 0
                ]
                lows = [
                    float(m.get("low") or 0)
                    for m in minutes
                    if float(m.get("low") or 0) > 0
                ]
                last_c = float((minutes[-1] or {}).get("close") or 0)
                if highs:
                    day["high"] = max(highs)
                if lows:
                    day["low"] = min(lows)
                if last_c > 0:
                    day["close"] = last_c
            except (TypeError, ValueError):
                pass
        if not day.get("open"):
            return {
                "ok": False,
                "pending": True,
                "reason": "日线未含当日且缺开盘价，等待分钟线",
                "bar": day,
                "hist": hist,
                "minute_bars": minutes,
            }
        return {
            "ok": True,
            "bar": day,
            "hist": hist,
            "minute_bars": minutes,
            "aligned": "minute_open",
        }

    hist = list(bars[:-1])
    bar = last
    if len(bars) >= 2 and not bar.get("prev_close"):
        prev_c = float(bars[-2].get("close") or 0)
        if prev_c > 0:
            bar["prev_close"] = prev_c
    return {
        "ok": True,
        "bar": bar,
        "hist": hist,
        "minute_bars": minutes,
        "aligned": "daily",
    }


def accept_intraday_dual_y_scores(
    scores: Optional[dict],
    *,
    source: str = "compute",
) -> Optional[dict]:
    """盘中 dual_y：compute 源拒绝 live_book 回退分（早盘小残差会误锁）。"""
    from core.t0.score_policy import resolve_y_score_source, scores_have_any

    if not scores_have_any(scores):
        return None
    src = resolve_y_score_source({"y_score_source": source})
    if src == "compute" and str((scores or {}).get("_score_source") or "") == "live_book":
        return None
    return scores if isinstance(scores, dict) else None


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
    paper: Optional[dict] = None,
    force_dual_y_gate: bool = False,
) -> Dict[str, Any]:
    from core.t0.costs import resolve_t0_cost_context
    from core.t0.minute_path import (
        _day_ohlc_from_minutes,
        _first_touch_long,
        _first_touch_reverse,
        prefix_directional_amplitude_ok,
        prefix_range_gate,
    )
    from core.t0.rules import (
        _skip_result,
        _t0_qty_lots,
        resolve_direction,
        scale_triggers_with_atr,
    )

    cost_model, cost_params = resolve_t0_cost_context(paper=paper)

    if coupling_mode == "skip_if_avoid" and stance_code == "avoid":
        return _skip_result(reason="stance=avoid 跳过做T", shares=float(holding.get("shares") or 0), bar=bar)
    if coupling_mode == "only_if_hold" and stance_code not in (None, "hold", "watch"):
        return _skip_result(reason=f"stance={stance_code} 非持有", shares=float(holding.get("shares") or 0), bar=bar)

    if len(minute_bars) < 2:
        return {"pending": True, "reason": "分钟线不足，等待下一根 5m"}

    if str(cfg.get("direction") or "") == "dual_y":
        from core.t0.score_policy import scores_have_any

        if not scores_have_any(scores):
            if force_dual_y_gate:
                return _skip_result(
                    reason="dual_y：午盘后即时算分仍未就绪，跳过",
                    shares=float(holding.get("shares") or 0),
                    bar=bar,
                    extra={"signal_skip": True},
                )
            return {"pending": True, "reason": "dual_y：即时算分未就绪，等待重试"}

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
    from core.t0.score_policy import scale_t0_ratio, scores_have_any

    cfg_exec = dict(cfg)
    base_ratio = float(cfg_exec.get("t0_ratio") or 0.4)
    t0_ratio = base_ratio
    if str(cfg_exec.get("direction") or "") == "dual_y" and scores_have_any(scores):
        t0_ratio = scale_t0_ratio(base_ratio, scores or {}, cfg_exec)
    cfg_exec["t0_ratio"] = t0_ratio
    fill_mode = str(cfg_exec.get("fill_mode") or "trigger")

    last_ts = str((minute_bars[-1] or {}).get("datetime") or "")
    at_session_end = "15:00" in last_ts or "14:55" in last_ts
    dir_amp = prefix_directional_amplitude_ok(
        minute_bars,
        direction=direction,
        ref=ref,
        sell_trig=sell_trig,
        buy_trig=buy_trig,
    )
    if not dir_amp.get("ok") and not at_session_end:
        return _skip_result(
            reason=str(dir_amp.get("reason") or "方向振幅未达标"),
            shares=shares,
            bar=bar_day,
            extra={
                "direction_used": direction,
                "range_mode": "rolling",
                "prefix_bars": gate.get("prefix_bars"),
                "range_pct": range_pct,
                "directional_amplitude": dir_amp,
            },
        )

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
            cfg=cfg_exec,
            cost_model=cost_model,
            cost_params=cost_params,
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
            cfg=cfg_exec,
            cost_model=cost_model,
            cost_params=cost_params,
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

    if isinstance(day, dict):
        day["t0_ratio_base"] = round(base_ratio, 4)
        day["t0_ratio"] = round(t0_ratio, 4)

    # 增量：只落尚未写入的腿
    return {
        "ready": True,
        "direction": direction,
        "bar_day": bar_day,
        "day_result": day,
        "trades": trades,
        "last_bar_ts": _bar_ts(minute_bars[-1]) if minute_bars else "",
        "t0_ratio_base": round(base_ratio, 4),
        "t0_ratio": round(t0_ratio, 4),
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

    from core.t0.costs import t0_leg_cash_delta

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
        cash += t0_leg_cash_delta(row)

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
    force_dual_y_gate: bool = False,
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
        paper=paper,
        force_dual_y_gate=bool(force_dual_y_gate),
    )
    if setup.get("pending"):
        st.setdefault("phase", PHASE_IDLE)
        st["wait_reason"] = str(setup.get("reason") or "等待重试")
        return st, [], {}
    if setup.get("skipped"):
        reason = str(setup.get("reason") or "")
        if _retryable_skip(reason) and phase in (PHASE_IDLE, PHASE_AFTER_LEG1):
            st["last_bar_ts"] = setup.get("last_bar_ts") or (
                _bar_ts(minute_bars[-1]) if minute_bars else last_ts
            )
            st["wait_reason"] = reason
            dir_used = setup.get("direction_used") or setup.get("direction")
            if dir_used:
                st["direction"] = str(dir_used)
            return st, [], {}
        st["phase"] = PHASE_SKIPPED
        st["reason"] = setup.get("reason")
        st.pop("wait_reason", None)
        if _dual_y_threshold_skip(reason):
            # 日线已对齐且有即时分才走到门槛；标记后不再被 unlock 成软锁循环
            st["score_locked"] = True
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
        if setup.get("direction"):
            st["direction"] = str(setup.get("direction"))
        st["wait_reason"] = "已定方向，等待触价 / 下一根 5m"
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
        unlocked = unlock_retryable_skipped(stocks.get(code))
        if unlocked is not None:
            stocks[code] = unlocked
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
            force_dual_y_gate=bool(ctx.get("force_dual_y_gate")),
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
            entry = {"phase": PHASE_SKIPPED, "legs_written": 0, "day_snapshot": r}
            if _dual_y_threshold_skip(str(r.get("reason") or "")):
                entry["score_locked"] = True
            stocks[code] = entry
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
