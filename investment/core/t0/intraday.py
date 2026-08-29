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


REBALANCE_T0_BLOCK_REVERSE = "做T正T进行中（已买待卖旧仓），调仓跳过卖出"
REBALANCE_T0_BLOCK_LONG = "做T反T已卖待回补，调仓跳过重复卖出"


def open_t0_leg_rebalance_block(st: Optional[dict]) -> Optional[str]:
    """未平做 T 腿 → 调仓卖出应跳过（正T防打断；反T防重复卖）。"""
    if not isinstance(st, dict):
        return None
    phase = str(st.get("phase") or "").strip().lower()
    if phase in (PHASE_DONE, PHASE_SKIPPED):
        return None
    legs = int(st.get("legs_written") or 0)
    if phase != PHASE_AFTER_LEG1 and legs <= 0:
        return None
    direction = str(st.get("direction") or "").strip().lower()
    if direction == "reverse_t":
        return REBALANCE_T0_BLOCK_REVERSE
    if direction == "long_t":
        return REBALANCE_T0_BLOCK_LONG
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

    dir_label = "正T" if direction == "reverse_t" else "反T" if direction == "long_t" else None
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
        if direction == "reverse_t":
            title = "正T · 已买待卖旧仓 · 调仓已跳过卖出"
        elif direction == "long_t":
            title = "反T · 已卖待回补 · 调仓已跳过重复卖出"
        else:
            title = wait_reason or "已落第一腿，等待第二触达 / 收盘回补"
    else:
        title = wait_reason or reason or "已定方向，等待触价 / 下一根 5m"

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
    from core.t0.minute_path import _day_ohlc_from_minutes, run_forward_first_touch
    from core.t0.rules import (
        _skip_result,
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
    # 与回测一致：高低用已有 5m；收盘价优先日线，供 eod/敞口标记（尤其 force_session_close）
    daily_close = float((bar or {}).get("close") or 0)
    bar_session = dict(bar_day)
    if daily_close > 0:
        bar_session["close"] = daily_close
    lot = int(cfg.get("lot_size") or 100)
    shares = float(holding.get("shares") or 0)
    scaled = scale_triggers_with_atr(cfg, atr_pct=atr_pct)
    cost = float(holding.get("cost") or 0)
    if shares <= 0:
        return _skip_result(reason="无效 bar 或持仓", shares=shares, bar=bar_day)

    cfg_pre = dict(cfg)
    try:
        ml = cfg.get("min_range_pct_long")
        mr = cfg.get("min_range_pct_reverse")
        if ml is not None and mr is not None:
            cfg_pre["min_range_pct"] = min(float(ml), float(mr))
    except (TypeError, ValueError):
        pass

    base_ratio = float(cfg.get("t0_ratio") or 1.0)
    out, dir_res = run_forward_first_touch(
        minute_bars=minute_bars,
        bar=bar,
        shares=shares,
        cost=cost,
        sellable_shares=sellable,
        cfg_day=cfg,
        cfg_pre=cfg_pre,
        cash=float(cash or 0),
        stock_code=code,
        lot=lot,
        cost_model=cost_model,
        cost_params=cost_params,
        atr_pct=scaled.get("atr_pct") if scaled.get("atr_pct") is not None else atr_pct,
        hist_bars=hist_bars,
        score_snap=scores,
        session_bar=bar_session,
        base_t0_ratio=base_ratio,
        # 强制收盘窗：不得再 defer，否则午前末根无法 eod/敞口入账
        defer_eod=not bool(force_session_close),
    )
    dir_res = dir_res or {}
    direction = str((out or {}).get("direction_used") or dir_res.get("direction") or "")

    if out is None:
        return _skip_result(
            reason="分钟线不足，无法第一触达",
            shares=shares,
            bar=bar_day,
            extra={"path_mode": "first_touch", "range_mode": "forward"},
        )

    if out.get("skipped"):
        from core.t0.score_policy import attach_day_scores

        return attach_day_scores(
            {
                **out,
                "direction_score": dir_res.get("direction_score"),
                "direction_reason": dir_res.get("direction_reason"),
            },
            scores,
            features=dir_res.get("features"),
        )

    day = out
    trades = list(day.get("trades") or [])
    if not trades:
        from core.t0.score_policy import attach_day_scores

        return attach_day_scores(
            {
                **day,
                "direction_score": dir_res.get("direction_score"),
                "direction_reason": dir_res.get("direction_reason"),
            },
            scores,
            features=dir_res.get("features"),
        )

    trigger_scale_meta = {
        "sell_trigger_pct_base": day.get("sell_trigger_pct_base"),
        "buy_trigger_pct_base": day.get("buy_trigger_pct_base"),
        "trigger_scale": day.get("trigger_scale"),
    }
    t0_ratio = float(day.get("t0_ratio") or base_ratio)
    cover_meta = day.get("cover_policy")
    if isinstance(day, dict):
        day["t0_ratio_base"] = round(base_ratio, 4)
        day["t0_ratio"] = round(t0_ratio, 4)
        day.update({k: v for k, v in trigger_scale_meta.items() if v is not None})
        day["direction_score"] = dir_res.get("direction_score")
        day["direction_reason"] = dir_res.get("direction_reason")
        if cover_meta:
            day["cover_policy"] = cover_meta
            day["must_cover_same_day"] = bool(cover_meta.get("must_cover"))
        from core.t0.score_policy import attach_day_scores

        day = attach_day_scores(day, scores, features=dir_res.get("features"))

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
        **trigger_scale_meta,
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
    st = dict(stock_state or {})
    phase = str(st.get("phase") or PHASE_IDLE)
    last_ts = str(st.get("last_bar_ts") or "")
    legs_written = int(st.get("legs_written") or 0)

    if phase == PHASE_DONE:
        return st, [], st.get("day_snapshot") or {}
    if phase == PHASE_SKIPPED:
        return st, [], {}

    name = str(holding.get("stock_name") or st.get("stock_name") or "").strip()
    if not name or name == code:
        name = resolve_stock_name(code, fallback=name)
    if name:
        st["stock_name"] = name
        if not str(holding.get("stock_name") or "").strip():
            holding["stock_name"] = name

    # 路径重放必须用日初仓/可卖/现金；落账后持仓已变，否则会重复开仓或 shares=0 误跳过
    if st.get("shares_day_start") is not None:
        sim_shares = float(st.get("shares_day_start") or 0)
        sim_sellable = float(
            st["sellable_day_start"]
            if st.get("sellable_day_start") is not None
            else sellable
        )
        sim_cash = float(
            st["cash_day_start"] if st.get("cash_day_start") is not None else cash
        )
        sim_holding = dict(holding)
        sim_holding["shares"] = sim_shares
    else:
        sim_shares = float(holding.get("shares") or 0)
        sim_sellable = float(sellable)
        sim_cash = float(cash)
        sim_holding = holding

    setup = _intraday_setup(
        code=code,
        holding=sim_holding,
        bar=bar,
        minute_bars=minute_bars,
        cfg=cfg,
        sellable=sim_sellable,
        cash=sim_cash,
        atr_pct=atr_pct,
        hist_bars=hist_bars,
        scores=scores,
        stance_code=stance_code,
        coupling_mode=coupling_mode,
        paper=paper,
        force_dual_y_gate=bool(force_dual_y_gate),
        force_session_close=bool(force_session_close),
    )
    if setup.get("pending"):
        st.setdefault("phase", PHASE_IDLE)
        st["wait_reason"] = str(setup.get("reason") or "等待重试")
        return st, [], {}
    if setup.get("skipped"):
        reason = str(setup.get("reason") or "")
        # path_abandon：回测已终态；实盘不得因文案含「待回落」等误判可重试
        if setup.get("path_abandon") or _path_abandon_skip(reason):
            st["phase"] = PHASE_SKIPPED
            st["reason"] = setup.get("reason")
            st.pop("wait_reason", None)
            st["day_snapshot"] = {
                **setup,
                "stock_code": code,
                "stock_name": holding.get("stock_name"),
            }
            return st, [], st["day_snapshot"]
        if _retryable_skip(reason) and phase in (PHASE_IDLE, PHASE_AFTER_LEG1):
            st["last_bar_ts"] = setup.get("last_bar_ts") or (
                _bar_ts(minute_bars[-1]) if minute_bars else last_ts
            )
            st["wait_reason"] = reason
            dir_used = setup.get("direction_used") or setup.get("direction")
            if dir_used:
                st["direction"] = str(dir_used)
            # 已有第一腿时保持 after_leg1，继续等第二触达 / 收盘回补
            if legs_written > 0 and phase != PHASE_AFTER_LEG1:
                st["phase"] = PHASE_AFTER_LEG1
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
    direction = str(setup.get("direction") or st.get("direction") or "")
    day_result = setup.get("day_result") or {}
    path_complete = bool(setup.get("path_complete")) or _roundtrip_complete(
        day_result, direction
    )
    new_trades = trades[legs_written:]
    if setup.get("direction"):
        st["direction"] = str(setup.get("direction"))

    if not new_trades:
        st["last_bar_ts"] = setup.get("last_bar_ts") or last_ts
        if path_complete and legs_written > 0:
            st["phase"] = PHASE_DONE
            st.pop("wait_reason", None)
        elif legs_written > 0:
            st["phase"] = PHASE_AFTER_LEG1
            st["wait_reason"] = "已开第一腿，等待第二触达 / 收盘回补"
        else:
            st["phase"] = PHASE_IDLE
            st["wait_reason"] = "已定方向，等待触价 / 下一根 5m"
        if day_result:
            st["day_snapshot"] = {
                **day_result,
                "stock_code": code,
                "stock_name": holding.get("stock_name"),
            }
        return st, [], day_result

    if st.get("shares_day_start") is None:
        st["shares_day_start"] = sim_shares
        st["sellable_day_start"] = sim_sellable
        st["cash_day_start"] = sim_cash

    applied = _apply_trades_to_paper(
        paper,
        holding,
        new_trades,
        as_of=as_of,
        log_source=log_source,
    )
    legs_written += len(applied)
    st["legs_written"] = legs_written
    st["last_bar_ts"] = setup.get("last_bar_ts") or last_ts
    # 仅往返完成才 done；单腿落账保持 after_leg1（勿用 len(trades) 误判）
    if path_complete:
        st["phase"] = PHASE_DONE
        st.pop("wait_reason", None)
    else:
        st["phase"] = PHASE_AFTER_LEG1
        st["wait_reason"] = "已开第一腿，等待第二触达 / 收盘回补"
    st["day_snapshot"] = {
        **day_result,
        "stock_code": code,
        "stock_name": holding.get("stock_name"),
    }
    if applied:
        cum = list((day_result.get("trades") or [])[:legs_written])
        if cum:
            st["day_snapshot"]["trades"] = cum
    return st, applied, st["day_snapshot"]


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
