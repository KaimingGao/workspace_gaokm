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

_CODE_NAME_CACHE: Dict[str, str] = {}
_CODE_NAME_CACHE_TS = 0.0
_CODE_NAME_CACHE_TTL = 3600.0


def _now_ts() -> float:
    return time.time()


def _load_a_code_name_map(*, ignore_ttl: bool = True) -> Dict[str, str]:
    """只读 a_code_name 磁盘索引；展示兜底可忽略过期。"""
    global _CODE_NAME_CACHE, _CODE_NAME_CACHE_TS
    now = _now_ts()
    if _CODE_NAME_CACHE and now - _CODE_NAME_CACHE_TS < _CODE_NAME_CACHE_TTL:
        return _CODE_NAME_CACHE
    out: Dict[str, str] = {}
    try:
        from core.paths import DATA_DIR
        import json

        path = os.path.join(DATA_DIR, "store", "a_code_name.json")
        if not os.path.isfile(path):
            return out
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        if not ignore_ttl:
            fetched = float(payload.get("fetched_at") or 0)
            if fetched and now - fetched > 86400 * 30:
                return out
        for it in payload.get("pairs") or []:
            if isinstance(it, (list, tuple)) and len(it) >= 2:
                code = str(it[0] or "").strip()
                name = str(it[1] or "").strip()
                if code and name and name != code:
                    out[code] = name
        _CODE_NAME_CACHE = out
        _CODE_NAME_CACHE_TS = now
    except Exception:  # noqa: BLE001
        logger.debug("load a_code_name map failed", exc_info=True)
    return out


def resolve_stock_name(
    code: str,
    *,
    fallback: str = "",
    name_by_code: Optional[Dict[str, str]] = None,
) -> str:
    """持仓名 → 传入 map → a_code_name 兜底。"""
    c = str(code or "").strip()
    for cand in (
        str(fallback or "").strip(),
        str((name_by_code or {}).get(c) or "").strip() if c else "",
    ):
        if cand and cand != c:
            return cand
    if not c:
        return str(fallback or "").strip()
    mapped = _load_a_code_name_map().get(c) or ""
    return mapped or str(fallback or "").strip()


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


REBALANCE_T0_BLOCK_BUY_THEN_SELL = "做T正T进行中（已买待卖旧仓），调仓跳过卖出"
REBALANCE_T0_BLOCK_SELL_THEN_BUY = "做T反T已卖待回补，调仓跳过重复卖出"


def open_t0_leg_rebalance_block(st: Optional[dict]) -> Optional[str]:
    """未平做 T 腿 → 调仓卖出应跳过（正T防打断；反T防重复卖）。"""
    if not isinstance(st, dict):
        return None
    rounds = st.get("rounds")
    if isinstance(rounds, dict) and rounds:
        open_dirs = []
        for rnd in rounds.values():
            if not isinstance(rnd, dict):
                continue
            ph = str(rnd.get("phase") or "")
            legs = int(rnd.get("legs_written") or 0)
            if ph == PHASE_AFTER_LEG1 or (legs > 0 and ph not in (PHASE_DONE, PHASE_SKIPPED)):
                open_dirs.append(str(rnd.get("direction") or ""))
        if open_dirs:
            if all(d == "buy_then_sell" for d in open_dirs):
                return REBALANCE_T0_BLOCK_BUY_THEN_SELL
            if all(d == "sell_then_buy" for d in open_dirs):
                return REBALANCE_T0_BLOCK_SELL_THEN_BUY
            return "做T未平腿进行中，调仓跳过卖出"
        return None
    phase = str(st.get("phase") or "").strip().lower()
    if phase in (PHASE_DONE, PHASE_SKIPPED):
        return None
    legs = int(st.get("legs_written") or 0)
    if phase != PHASE_AFTER_LEG1 and legs <= 0:
        return None
    direction = str(st.get("direction") or "").strip().lower()
    if direction == "buy_then_sell":
        return REBALANCE_T0_BLOCK_BUY_THEN_SELL
    if direction == "sell_then_buy":
        return REBALANCE_T0_BLOCK_SELL_THEN_BUY
    if legs > 0:
        return "做T未平腿进行中，调仓跳过卖出"
    return None


def load_rebalance_t0_sell_blocks(*, session_date: Optional[str] = None) -> Dict[str, str]:
    """当日盘中未平做 T → code → 调仓跳过原因。

    纸面回放上下文内直接返回空（回放关闭做 T，避免读盘中状态/打网）。
    """
    try:
        from core.paper.replay_ctx import replay_as_of

        if replay_as_of():
            return {}
    except Exception:  # noqa: BLE001 — 回放时钟不可用时走 live
        pass
    from core.market.calendar import resolve_session_date
    from core.signal.session_pit import shanghai_now

    sess = str(session_date or resolve_session_date(now=shanghai_now()) or "")[:10]
    if not sess:
        return {}
    state = load_intraday_state()
    if str(state.get("session_date") or "")[:10] != sess:
        return {}
    stocks = state.get("stocks") if isinstance(state.get("stocks"), dict) else {}
    out: Dict[str, str] = {}
    for code, raw in stocks.items():
        c = str(code or "").strip()
        if not c:
            continue
        reason = open_t0_leg_rebalance_block(raw if isinstance(raw, dict) else {})
        if reason:
            out[c] = reason
    return out


def holding_t0_intraday_status(st: Optional[dict]) -> Optional[Dict[str, Any]]:
    """持仓表用：当日实时做 T 状态摘要（无状态返回 None）。"""
    if not isinstance(st, dict):
        return None
    phase = str(st.get("phase") or PHASE_IDLE).strip().lower() or PHASE_IDLE
    snap = st.get("day_snapshot") if isinstance(st.get("day_snapshot"), dict) else {}
    direction = str(
        st.get("direction") or snap.get("direction_used") or snap.get("direction") or ""
    ).strip().lower()
    legs = int(st.get("legs_written") or 0)
    wait_reason = str(st.get("wait_reason") or "").strip()
    reason = str(st.get("reason") or snap.get("reason") or wait_reason or "").strip()

    dir_label = "正T" if direction == "buy_then_sell" else "反T" if direction == "sell_then_buy" else None
    rounds = st.get("rounds") if isinstance(st.get("rounds"), dict) else None
    if rounds:
        open_n = sum(
            1
            for r in rounds.values()
            if isinstance(r, dict) and str(r.get("phase") or "") == PHASE_AFTER_LEG1
        )
        filled = sum(int((r or {}).get("legs_written") or 0) for r in rounds.values() if isinstance(r, dict))
        n_slots = len(rounds)
        if open_n:
            phase_label = f"{open_n}轮未平"
            title = f"做T {n_slots} 轮独立 · {open_n} 轮待第二腿 · 调仓已跳过卖出"
            badge = f"多轮·{phase_label}"
            return {
                "phase": PHASE_AFTER_LEG1,
                "direction": direction or None,
                "legs_written": filled,
                "label": phase_label,
                "dir_label": dir_label,
                "badge": badge,
                "title": title,
            }
        if phase == PHASE_IDLE:
            title = wait_reason or f"做T {n_slots} 轮独立预估，等待确认根"
            badge = "多轮·盯"
            return {
                "phase": PHASE_IDLE,
                "direction": direction or None,
                "legs_written": filled,
                "label": "盯",
                "dir_label": dir_label,
                "badge": badge,
                "title": title,
            }

    phase_label = {
        PHASE_IDLE: "盯",
        PHASE_AFTER_LEG1: "一腿",
        PHASE_DONE: "完成",
        PHASE_SKIPPED: "终锁",
    }.get(phase)

    if phase == PHASE_IDLE and not direction:
        return None
    if phase == PHASE_SKIPPED:
        title = reason or "当日终态锁死"
    elif phase == PHASE_DONE:
        title = reason or "当日做 T 往返已完成"
    elif phase == PHASE_AFTER_LEG1:
        if direction == "buy_then_sell":
            title = "正T · 已买待卖旧仓 · 调仓已跳过卖出"
        elif direction == "sell_then_buy":
            title = "反T · 已卖待回补 · 调仓已跳过重复卖出"
        else:
            title = wait_reason or "已落第一腿，等待第二触达 / 收盘回补"
    else:
        title = wait_reason or reason or "已定方向，等待确认 / 下一根 5m"

    badge = f"{dir_label}·{phase_label}" if dir_label and phase_label else (phase_label or dir_label)
    if not badge:
        return None
    return {
        "phase": phase,
        "direction": direction or None,
        "legs_written": legs,
        "label": phase_label,
        "dir_label": dir_label,
        "badge": badge,
        "title": title,
    }


def load_holding_t0_status_by_code(*, session_date: Optional[str] = None) -> Dict[str, dict]:
    """当日盘中做 T 状态 → code → 持仓表摘要。"""
    from core.market.calendar import resolve_session_date
    from core.signal.session_pit import shanghai_now

    sess = str(session_date or resolve_session_date(now=shanghai_now()) or "")[:10]
    if not sess:
        return {}
    state = load_intraday_state()
    if str(state.get("session_date") or "")[:10] != sess:
        return {}
    stocks = state.get("stocks") if isinstance(state.get("stocks"), dict) else {}
    out: Dict[str, dict] = {}
    for code, raw in stocks.items():
        c = str(code or "").strip()
        if not c:
            continue
        status = holding_t0_intraday_status(raw if isinstance(raw, dict) else {})
        if status:
            out[c] = status
    return out


def _desk_row_from_stock_state(
    code: str,
    raw: Optional[dict],
    *,
    name_by_code: Dict[str, str],
    state_aligned: bool,
) -> Dict[str, Any]:
    """单票盯盘行；raw 为空时按持仓占位 idle。"""
    c = str(code or "").strip()
    raw = raw if isinstance(raw, dict) else {}
    snap = raw.get("day_snapshot") if isinstance(raw.get("day_snapshot"), dict) else {}
    phase = str(raw.get("phase") or PHASE_IDLE).strip().lower() or PHASE_IDLE
    reason = str(
        raw.get("reason")
        or raw.get("wait_reason")
        or snap.get("reason")
        or snap.get("direction_reason")
        or ""
    ).strip()
    if not reason and not raw:
        reason = (
            "等待 Worker 首根 5m K 线"
            if not state_aligned
            else "等待下一根 5m"
        )
    legs = int(raw.get("legs_written") or 0)
    if phase == PHASE_IDLE and legs <= 0:
        from core.t0.minute_path import tplus1_reason_is_terminal

        if tplus1_reason_is_terminal(reason):
            phase = PHASE_SKIPPED
    locked = phase == PHASE_SKIPPED
    direction = str(
        raw.get("direction")
        or snap.get("direction_used")
        or snap.get("direction")
        or ""
    ).strip()
    name = resolve_stock_name(
        c,
        fallback=str(snap.get("stock_name") or raw.get("stock_name") or ""),
        name_by_code=name_by_code,
    )
    return {
        "stock_code": c,
        "stock_name": name,
        "phase": phase,
        "locked": locked,
        "signal_skip": bool(snap.get("signal_skip")),
        "legs_written": legs,
        "reason": reason,
        "direction": direction or None,
        "last_bar_ts": str(raw.get("last_bar_ts") or "")[:19] or None,
        "unlocked_from_skip": str(raw.get("unlocked_from_skip") or "").strip() or None,
        "ref": snap.get("ref"),
        "range_pct": snap.get("range_pct"),
    }


def build_intraday_desk_status() -> Dict[str, Any]:
    """今日盘中盯盘桌面：逐票 phase / 原因（供 Follow 做 T 后台区展示）。"""
    from core.market.calendar import resolve_session_date
    from core.signal.session_pit import shanghai_now

    sess = str(resolve_session_date(now=shanghai_now()) or "")[:10]
    state = load_intraday_state()
    state_sess = str(state.get("session_date") or "")[:10]
    state_aligned = bool(sess and state_sess and sess == state_sess)
    stocks_raw = (
        state.get("stocks")
        if state_aligned and isinstance(state.get("stocks"), dict)
        else {}
    )

    # idle 态常缺 stock_name：纸面持仓 → a_code_name 兜底
    name_by_code: Dict[str, str] = {}
    holding_codes: List[str] = []
    try:
        from core.paths import PAPER_PATH
        from core.paper import load_paper

        paper = load_paper(PAPER_PATH)
        for h in paper.get("holdings") or []:
            if not isinstance(h, dict):
                continue
            c = str(h.get("stock_code") or "").strip()
            n = str(h.get("stock_name") or "").strip()
            if c:
                holding_codes.append(c)
            if c and n and n != c:
                name_by_code[c] = n
    except Exception:  # noqa: BLE001
        logger.debug("desk status name enrich from paper failed", exc_info=True)
    # 磁盘码表：持仓名为空时仍能显示（如 600029 南方航空）
    try:
        for c, n in _load_a_code_name_map().items():
            if c and n and c not in name_by_code:
                name_by_code[c] = n
    except Exception:  # noqa: BLE001
        logger.debug("desk status name enrich from a_code_name failed", exc_info=True)

    phase_rank = {
        PHASE_SKIPPED: 0,
        PHASE_IDLE: 1,
        PHASE_AFTER_LEG1: 2,
        PHASE_DONE: 3,
    }
    counts = {
        PHASE_IDLE: 0,
        PHASE_AFTER_LEG1: 0,
        PHASE_DONE: 0,
        PHASE_SKIPPED: 0,
        "other": 0,
    }
    locked_n = 0
    legs_total = 0

    codes_ordered: List[str] = []
    seen: set = set()
    for c in holding_codes:
        if c not in seen:
            seen.add(c)
            codes_ordered.append(c)
    for c in stocks_raw:
        cs = str(c or "").strip()
        if cs and cs not in seen:
            seen.add(cs)
            codes_ordered.append(cs)

    rows: List[Dict[str, Any]] = []
    for c in codes_ordered:
        raw = stocks_raw.get(c) if isinstance(stocks_raw.get(c), dict) else None
        row = _desk_row_from_stock_state(
            c, raw, name_by_code=name_by_code, state_aligned=state_aligned
        )
        phase = str(row.get("phase") or PHASE_IDLE)
        if phase not in counts:
            counts["other"] += 1
        else:
            counts[phase] += 1
        legs_total += max(0, int(row.get("legs_written") or 0))
        if row.get("locked"):
            locked_n += 1
        rows.append(row)

    # 按最近 K 线时间升序（无时间戳的排最后）；同刻再按阶段 / 代码
    rows.sort(
        key=lambda r: (
            str(r.get("last_bar_ts") or "") == "",
            str(r.get("last_bar_ts") or ""),
            phase_rank.get(str(r.get("phase")), 9),
            str(r.get("stock_code") or ""),
        )
    )
    desk_active = bool(sess and rows)
    return {
        "session_date": sess or state_sess or None,
        "state_session_date": state_sess or None,
        "state_aligned": state_aligned,
        "same_session": desk_active,
        "updated_at": state.get("updated_at"),
        "universe_count": len(rows),
        "counts": counts,
        "locked_count": locked_n,
        "legs_written_total": legs_total,
        "rows": rows,
        "note": (
            "skipped=终锁：≥11:30 无成交腿，或 dual_y 门槛 / stance；"
            "上午振幅/未触价可重试；idle=盯盘；after_leg1=已第一腿；done=当日完成"
            if state_aligned
            else (
                "盘中状态尚未对齐今日会话（已按持仓占位；打开 Worker 后将更新）"
                if rows
                else "尚无持仓或 Worker 未开"
            )
        ),
    }


def save_intraday_state(state: Dict[str, Any]) -> None:
    from core.io_atomic import atomic_write_json

    atomic_write_json(_state_path(), {**state, "updated_at": _now_ts()})


def _bar_ts(mb: dict) -> str:
    return str(mb.get("datetime") or mb.get("date") or "")


def _norm_bar_ts(ts: Any) -> str:
    s = str(ts or "").strip().replace("T", " ")
    if len(s) >= 19 and s[4:5] == "-":
        return s[:19]
    return s


def _bar_key(ts: Any) -> str:
    """对齐到分钟：``YYYY-MM-DD HH:MM``，忽略秒与 T 分隔。"""
    s = _norm_bar_ts(ts)
    if len(s) >= 16:
        return s[:16]
    return s


def live_new_leg1_in_window(
    trade_at: Any,
    *,
    last_bar_ts: str,
    latest_bar_ts: str,
) -> bool:
    """盘中新开第一腿：只认最新一根已闭合 5m。

    首 tick、漏跳、午间补扫都不回放中间已过的 K；那一根当时没成交就错过。
    """
    at = _bar_key(trade_at)
    latest = _bar_key(latest_bar_ts)
    last = _bar_key(last_bar_ts)
    if not at or not latest:
        return False
    if at != latest:
        return False
    if last and last >= latest:
        return False
    return True


def _overlay_applied_ts(legs: List[dict], applied: List[dict]) -> List[dict]:
    """把本 tick 落账的 ``ts`` 盖回快照腿（审计）；过程列仍用 5m 槽钟。"""
    unused = [dict(a) for a in applied or [] if isinstance(a, dict)]
    out: List[dict] = []
    for raw in legs or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        if row.get("ts"):
            out.append(row)
            continue
        side = str(row.get("side") or "")
        try:
            shares = int(row.get("shares") or 0)
        except (TypeError, ValueError):
            shares = 0
        try:
            px = round(float(row.get("price") or 0), 4)
        except (TypeError, ValueError):
            px = 0.0
        hit_i = None
        for i, a in enumerate(unused):
            if str(a.get("side") or "") != side:
                continue
            try:
                if int(a.get("shares") or 0) != shares:
                    continue
            except (TypeError, ValueError):
                continue
            try:
                if round(float(a.get("price") or 0), 4) != px:
                    continue
            except (TypeError, ValueError):
                continue
            hit_i = i
            break
        if hit_i is not None:
            a = unused.pop(hit_i)
            if a.get("ts") and not row.get("ts"):
                row["ts"] = a.get("ts")
        out.append(row)
    return out


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


def _path_abandon_skip(reason: str) -> bool:
    """前缀路径放弃：与回测 path_abandon 终态对齐，不可当盘口条件无限重试。"""
    r = str(reason or "")
    return "放弃正T" in r or "放弃反T" in r or r.startswith("前缀无")


def _retryable_skip(reason: str) -> bool:
    """可重试跳过：振幅/未触价等盘口条件，等下一根 5m；不写入 skipped 终态。

    dual_y 门槛（横盘/y_check/幅度）在日线对齐且即时分就绪后应终锁，故不在此列。
    path_abandon 文案含「待回落/待反弹/待固定前缀/振幅」子串，须先排除，否则永远 idle。
    """
    if _path_abandon_skip(reason):
        return False
    r = str(reason or "")
    return any(
        x in r
        for x in (
            "振幅",
            "分钟",
            "未触及",
            "等待",
            "上移振幅",
            "下移振幅",
            "方向振幅",
            "待回落",
            "待反弹",
            "待固定前缀",
            "固定前缀后半",
            "待确认",
            "确认根",
            "待触价",
            "待搜索窗",
            "空间用尽",
        )
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
            "|y_path|",
            "未过门槛",
            "预期幅度不足",
            "未过入场",
            "缺 y_τ",
            "缺 y_eod",
            "即时算分失败",
            "即时算分仍未就绪",
            "禁止做T",
            "午盘后",
            "无成交腿",
            "异号",
            "|y_eod|",
            "|y_trade|",
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
        out = {
            "phase": PHASE_IDLE,
            "legs_written": int(st.get("legs_written") or 0),
            "last_bar_ts": "",
            "unlocked_from_skip": reason[:160],
        }
        for k in ("shares_day_start", "sellable_day_start", "cash_day_start", "direction"):
            if st.get(k) is not None:
                out[k] = st.get(k)
        return out
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


def _minute_at_session_close(minute_bars: List[dict]) -> bool:
    """末根 5m 是否已进入收盘窗（≥14:55），用于真正的 eod_cover。"""
    if not minute_bars:
        return False
    from core.t0.minute_path import _hm_reached

    return bool(_hm_reached(_bar_ts(minute_bars[-1]), (14, 55)))


def _roundtrip_complete(day: Optional[dict], direction: Optional[str]) -> bool:
    """往返是否完成（含 eod_cover / 敞口入账）；未完成应保持 after_leg1。"""
    if not isinstance(day, dict) or not direction:
        return False
    from core.t0.minute_path import _touch_path_complete

    return bool(_touch_path_complete(day, str(direction)))


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
    force_session_close: bool = False,
) -> Dict[str, Any]:
    from core.t0.costs import resolve_t0_cost_context
    from core.t0.minute_path import _day_ohlc_from_minutes
    from core.t0.rules import _skip_result
    from core.t0.slots import simulate_t0_day_slots
    from core.t0.score_policy import attach_day_scores, scores_have_any

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
    bar_session = dict(bar_day)
    lot = int(cfg.get("lot_size") or 100)
    shares = float(holding.get("shares") or 0)
    cost = float(holding.get("cost") or 0)
    if shares <= 0:
        return _skip_result(reason="无效 bar 或持仓", shares=shares, bar=bar_day)

    base_ratio = float(cfg.get("t0_ratio") or 1.0)
    day_out = simulate_t0_day_slots(
        bar=bar,
        minute_bars=minute_bars,
        shares=shares,
        cost=cost,
        sellable_shares=sellable,
        cfg=cfg,
        cash=float(cash or 0),
        stock_code=code,
        lot=lot,
        cost_model=cost_model,
        cost_params=cost_params,
        atr_pct=atr_pct,
        hist_bars=hist_bars,
        score_snap=scores,
        session_bar=bar_session,
        defer_eod=not bool(force_session_close),
        daily_bar=dict(bar) if isinstance(bar, dict) else None,
    )
    out = day_out
    direction = str(out.get("direction_used") or "")

    def _snap_for_day(day_obj: Optional[dict]) -> Optional[dict]:
        """优先确认根因果重算分（与回测 _finish 同口径）。"""
        if isinstance(day_obj, dict) and isinstance(day_obj.get("_t0_score_snap"), dict):
            return day_obj.pop("_t0_score_snap")
        return scores

    if out.get("pending") and not (out.get("trades") or []):
        snap = _snap_for_day(out)
        return attach_day_scores(out, snap)

    if out.get("skipped") and not (out.get("trades") or []):
        snap = _snap_for_day(out)
        return attach_day_scores(out, snap)

    day = out
    trades = list(day.get("trades") or [])
    if not trades:
        snap = _snap_for_day(day)
        return attach_day_scores(day, snap)

    t0_ratio = float(day.get("t0_ratio") or base_ratio)
    cover_meta = day.get("cover_policy")
    if isinstance(day, dict):
        day["t0_ratio_base"] = round(base_ratio, 4)
        day["t0_ratio"] = round(t0_ratio, 4)
        if cover_meta:
            day["cover_policy"] = cover_meta
            day["must_cover_same_day"] = bool(cover_meta.get("must_cover"))
        snap = _snap_for_day(day)
        day = attach_day_scores(day, snap)

    # 增量：只落尚未写入的腿
    return {
        "ready": True,
        "direction": direction,
        "bar_day": bar_day,
        "day_result": day,
        "trades": list(day.get("trades") or []),
        "path_complete": _roundtrip_complete(day, direction),
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
) -> List[dict]:
    from core.paper.ledger import append_operation_log, append_trade_legs_to_operation_log
    from core.paper.tplus1 import apply_t0_trades, sellable_shares as t1_after
    from core.paper.ledger import _now_iso

    from core.t0.costs import t0_fee_side, t0_leg_cash_delta

    if not trades:
        return []
    ts = _now_iso()
    cash = float(paper.get("cash") or 0)
    legs: List[dict] = []
    for t in trades:
        delta = float(t0_leg_cash_delta(t) or 0)
        # 共享现金：买腿不得透支（他票已花掉本票卖出回笼时跳过买回）
        if t0_fee_side(t.get("side")) == "buy" and cash + delta < -1e-6:
            logger.info(
                "t0 apply skip buy: cash=%.2f need=%.2f code=%s",
                cash,
                -delta,
                holding.get("stock_code"),
            )
            continue
        row = dict(t)
        row["ts"] = ts
        row["stock_name"] = holding.get("stock_name")
        legs.append(row)
        paper.setdefault("trades", []).append(row)
        cash += delta

    if not legs:
        return []
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
    return legs


def _stock_phase_from_rounds(rounds: dict) -> Tuple[str, int, Optional[str]]:
    """汇总各轮 phase → 股票级 phase / legs / direction。"""
    if not rounds:
        return PHASE_IDLE, 0, None
    legs = 0
    dirs: List[str] = []
    phases = []
    for rnd in rounds.values():
        if not isinstance(rnd, dict):
            continue
        phases.append(str(rnd.get("phase") or PHASE_IDLE))
        legs += int(rnd.get("legs_written") or 0)
        d = str(rnd.get("direction") or "")
        if d:
            dirs.append(d)
    if any(p == PHASE_AFTER_LEG1 for p in phases):
        stock_phase = PHASE_AFTER_LEG1
    elif phases and all(p == PHASE_SKIPPED for p in phases):
        stock_phase = PHASE_SKIPPED
    elif phases and all(p in (PHASE_DONE, PHASE_SKIPPED) for p in phases):
        stock_phase = PHASE_DONE if legs > 0 else PHASE_SKIPPED
    else:
        stock_phase = PHASE_IDLE
    uniq = list(dict.fromkeys(dirs))
    direction = uniq[0] if len(uniq) == 1 else ("mixed" if uniq else None)
    return stock_phase, legs, direction


def _process_holding_slots(
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
    force_session_close: bool = False,
) -> Tuple[dict, List[dict], dict]:
    """多轮独立做 T 盘中增量（v6：全日收盘带宽扫描后按轮增量落账）。"""
    from core.t0.costs import resolve_t0_cost_context, t0_leg_cash_delta
    from core.t0.minute_path import _day_ohlc_from_minutes
    from core.t0.rules import _skip_result
    from core.t0.slots import _open_leg1_cash_lock, _slot_trade_legs, simulate_t0_day_slots
    from core.t0.score_policy import attach_day_scores, scores_have_any

    st = dict(stock_state or {})
    name = str(holding.get("stock_name") or st.get("stock_name") or "").strip()
    if name:
        st["stock_name"] = name

    if coupling_mode == "skip_if_avoid" and stance_code == "avoid":
        snap = _skip_result(reason="stance=avoid 跳过做T", shares=float(holding.get("shares") or 0), bar=bar)
        st["phase"] = PHASE_SKIPPED
        st["reason"] = snap.get("reason")
        st["day_snapshot"] = snap
        return st, [], snap
    if coupling_mode == "only_if_hold" and stance_code not in (None, "hold", "watch"):
        snap = _skip_result(
            reason=f"stance={stance_code} 非持有",
            shares=float(holding.get("shares") or 0),
            bar=bar,
        )
        st["phase"] = PHASE_SKIPPED
        st["reason"] = snap.get("reason")
        st["day_snapshot"] = snap
        return st, [], snap

    if not minute_bars:
        st.setdefault("phase", PHASE_IDLE)
        st["wait_reason"] = "分钟线不足，等待下一根 5m"
        return st, [], {}

    if str(cfg.get("direction") or "") == "dual_y" and not scores_have_any(scores):
        st.setdefault("phase", PHASE_IDLE)
        st["wait_reason"] = "dual_y：即时算分未就绪，等待重试"
        return st, [], {}

    if st.get("shares_day_start") is None:
        st["shares_day_start"] = float(holding.get("shares") or 0)
        st["sellable_day_start"] = float(sellable)
        st["cash_day_start"] = float(cash)
    sim_shares = float(st.get("shares_day_start") or 0)
    sim_sellable = float(st.get("sellable_day_start") if st.get("sellable_day_start") is not None else sellable)
    sim_cash = float(st.get("cash_day_start") if st.get("cash_day_start") is not None else cash)

    rounds = st.get("rounds") if isinstance(st.get("rounds"), dict) else {}
    lot = int(cfg.get("lot_size") or 100)
    cost_model, cost_params = resolve_t0_cost_context(paper=paper)
    bar_day = _day_ohlc_from_minutes(minute_bars, bar)
    bar_session = dict(bar_day)
    cost = float(holding.get("cost") or 0)
    cfg_day = dict(cfg)

    day_out = simulate_t0_day_slots(
        bar=bar,
        minute_bars=minute_bars,
        shares=sim_shares,
        cost=cost,
        sellable_shares=sim_sellable,
        cfg=cfg_day,
        cash=sim_cash,
        stock_code=code,
        lot=lot,
        cost_model=cost_model,
        cost_params=cost_params,
        atr_pct=atr_pct,
        hist_bars=hist_bars,
        score_snap=scores,
        session_bar=bar_session,
        defer_eod=not bool(force_session_close),
        daily_bar=dict(bar) if isinstance(bar, dict) else None,
    )
    slot_rows = list(day_out.get("t0_slot_results") or [])
    all_applied: List[dict] = []
    legs_total = 0
    last_seen = str(st.get("last_bar_ts") or "")
    latest_ts = latest_minute_bar_ts(minute_bars) if minute_bars else ""
    for row in slot_rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("t0_slot") or f"r{len(rounds) + 1}")
        rnd = dict(rounds.get(sid) or {"phase": PHASE_IDLE, "legs_written": 0})
        direction = str(row.get("direction_used") or rnd.get("direction") or "")
        if direction:
            rnd["direction"] = direction
        trades = _slot_trade_legs(row)
        legs_before = int(rnd.get("legs_written") or 0)
        written = legs_before
        new_trades = trades[written:]
        path_complete = _roundtrip_complete(row, direction)

        if row.get("skipped") and not new_trades and written <= 0:
            rnd["phase"] = PHASE_SKIPPED
            rnd["reason"] = str(row.get("reason") or "")
            rounds[sid] = rnd
            continue

        if written <= 0 and new_trades:
            first_at = new_trades[0].get("at") if isinstance(new_trades[0], dict) else ""
            if not live_new_leg1_in_window(
                first_at, last_bar_ts=last_seen, latest_bar_ts=latest_ts
            ):
                # 早盘已过的破带不回放成交；本轮不入 rounds，下一根 K 再看
                continue

        applied = []
        if new_trades:
            applied = _apply_trades_to_paper(
                paper,
                holding,
                new_trades,
                as_of=as_of,
                log_source=log_source,
            )
            written += len(applied)
            all_applied.extend(applied)
        rnd["legs_written"] = written
        if path_complete and written > 0:
            rnd["phase"] = PHASE_DONE
            rnd.pop("wait_reason", None)
        elif written > 0:
            rnd["phase"] = PHASE_AFTER_LEG1
            rnd["wait_reason"] = "已开第一腿，等待第二触达 / 收盘回补"
        else:
            rnd["phase"] = PHASE_IDLE
        rnd["day_snapshot"] = row
        rounds[sid] = rnd
        legs_total += written

    st["rounds"] = rounds
    stock_phase, legs_sum, direction = _stock_phase_from_rounds(rounds)
    skip_reason = str(
        day_out.get("reason") or day_out.get("direction_reason") or st.get("reason") or ""
    )
    if legs_sum <= 0 and not all_applied:
        from core.t0.minute_path import tplus1_reason_is_terminal

        if tplus1_reason_is_terminal(skip_reason):
            stock_phase = PHASE_SKIPPED
            st["reason"] = skip_reason
            st["score_locked"] = True
            st.pop("wait_reason", None)
    st["phase"] = stock_phase
    st["legs_written"] = legs_sum
    if direction:
        st["direction"] = direction
    if minute_bars:
        st["last_bar_ts"] = latest_minute_bar_ts(minute_bars)
    merged = attach_day_scores(day_out, scores) if isinstance(day_out, dict) else {}
    committed_slots: List[dict] = []
    committed_legs: List[dict] = []
    for row in slot_rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("t0_slot") or "")
        rnd = rounds.get(sid) if sid else None
        if not isinstance(rnd, dict):
            continue
        committed_slots.append(row)
        w = int(rnd.get("legs_written") or 0)
        if w > 0:
            committed_legs.extend(_slot_trade_legs(row)[:w])
    snap_trades = _overlay_applied_ts(committed_legs, all_applied)
    if all_applied or snap_trades or merged:
        snap_merged = dict(merged) if isinstance(merged, dict) else {}
        if committed_slots:
            snap_merged["t0_slot_results"] = committed_slots
        st["day_snapshot"] = {
            **snap_merged,
            "stock_code": code,
            "stock_name": holding.get("stock_name"),
            "trades": snap_trades,
            "t0_slots_enabled": True,
            "range_mode": "close_band",
        }
    return st, all_applied, st.get("day_snapshot") or {}


def _t0_qty_from_cfg(shares: float, ratio: float, lot: int, sellable: float) -> int:
    from core.t0.rules import _t0_qty_lots

    return int(_t0_qty_lots(shares, ratio, lot, sellable))


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
    force_session_close: bool = False,
) -> Tuple[dict, List[dict], dict]:
    """返回 (new_stock_state, new_trades, day_snapshot)。"""
    _ = applied_legs, force_dual_y_gate
    return _process_holding_slots(
        code=code,
        holding=holding,
        stock_state=stock_state,
        minute_bars=minute_bars,
        bar=bar,
        cfg=cfg,
        sellable=sellable,
        cash=cash,
        atr_pct=atr_pct,
        hist_bars=hist_bars,
        scores=scores,
        stance_code=stance_code,
        coupling_mode=coupling_mode,
        as_of=as_of,
        log_source=log_source,
        paper=paper,
        force_session_close=bool(force_session_close),
    )


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
        st, applied, snap = process_holding_intraday(
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
            force_session_close=bool(ctx.get("force_session_close")),
        )
        stocks[code] = st
        if applied:
            all_new.extend(applied)
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
            entry = {
                "phase": PHASE_SKIPPED,
                "legs_written": 0,
                "day_snapshot": r,
                "stock_name": resolve_stock_name(
                    code, fallback=str(r.get("stock_name") or "")
                ),
            }
            if _dual_y_threshold_skip(str(r.get("reason") or "")):
                entry["score_locked"] = True
            stocks[code] = entry
        elif trades:
            stocks[code] = {
                "phase": PHASE_DONE,
                "legs_written": len(trades),
                "day_snapshot": r,
                "stock_name": resolve_stock_name(
                    code, fallback=str(r.get("stock_name") or "")
                ),
            }
        else:
            stocks[code] = {
                "phase": PHASE_IDLE,
                "legs_written": 0,
                "stock_name": resolve_stock_name(
                    code, fallback=str(r.get("stock_name") or "")
                ),
            }
    state["session_date"] = sess
    state["stocks"] = stocks
    state["results"] = merged
    save_intraday_state(state)
