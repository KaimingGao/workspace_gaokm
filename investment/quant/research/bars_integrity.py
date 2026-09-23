"""观察池日线 / 5 分钟仓完整度（研究枢纽格子图）。只读本地仓，不拉行情。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

INTEGRITY_DAYS = 60
_DAY_MIN = 10
_DAY_MAX = 90

# A 股 5 分钟：上午 09:35–11:30、下午 13:05–15:00，共 48 根。标的是该根结束时刻。
def five_minute_slots() -> List[str]:
    out: List[str] = []

    def _walk(start: Tuple[int, int], end: Tuple[int, int]) -> None:
        h, m = start
        while (h, m) <= end:
            out.append(f"{h:02d}:{m:02d}")
            m += 5
            if m >= 60:
                h += 1
                m -= 60

    _walk((9, 35), (11, 30))
    _walk((13, 5), (15, 0))
    return out


FIVE_MINUTE_SLOTS = five_minute_slots()
MORNING_SLOTS = FIVE_MINUTE_SLOTS[:24]
AFTERNOON_SLOTS = FIVE_MINUTE_SLOTS[24:]
_SLOT_SET = set(FIVE_MINUTE_SLOTS)


def clamp_integrity_days(raw: Any, default: int = INTEGRITY_DAYS) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = int(default)
    return max(_DAY_MIN, min(_DAY_MAX, n))


def recent_trading_days(n: int, *, end: str) -> List[str]:
    """含 end 在内、向前 n 个交易日，旧→新。"""
    from core.market.calendar import is_trading_day, prev_trading_day

    end_s = str(end or "")[:10]
    if not end_s:
        return []
    days: List[str] = []
    cur = end_s if is_trading_day(end_s) else prev_trading_day(end_s)
    guard = 0
    want = max(1, int(n))
    while cur and len(days) < want and guard < 400:
        days.append(cur)
        cur = prev_trading_day(cur)
        guard += 1
    days.reverse()
    return days


def classify_daily_cell(*, present: bool, live: bool) -> str:
    if live:
        return "live"
    return "ok" if present else "miss"


def classify_minute_day(hms: Iterable[str], *, live: bool) -> str:
    """一天 5 分钟的格子。

    live 盘中不算缺口。ok=48 根且首 09:35、末 ≥14:55。
    head=缺开头；tail=缺收盘；both=两头都缺；gap=两头在、中间缺；miss=无 K。
    """
    if live:
        return "live"
    present = {str(h or "")[:5] for h in hms if str(h or "")[:5] in _SLOT_SET}
    if not present:
        return "miss"
    head_bad = "09:35" not in present
    last = max(present)
    tail_bad = last < "14:55"
    if head_bad and tail_bad:
        return "both"
    if head_bad:
        return "head"
    if tail_bad:
        return "tail"
    if len(present) < len(FIVE_MINUTE_SLOTS):
        return "gap"
    return "ok"


def _problem_rank_daily(cells: Sequence[str]) -> Tuple[int, int]:
    miss = sum(1 for c in cells if c == "miss")
    return (0 if miss else 1, -miss)


def _problem_rank_minute(cells: Sequence[str]) -> Tuple[int, int, int, int]:
    head = sum(1 for c in cells if c in ("head", "both"))
    tail = sum(1 for c in cells if c in ("tail", "both"))
    miss = sum(1 for c in cells if c == "miss")
    gap = sum(1 for c in cells if c == "gap")
    bad = head + tail + miss + gap
    return (0 if bad else 1, -head, -miss, -tail)


def build_daily_grid(
    *,
    codes: Sequence[str],
    names: Dict[str, str],
    dates: Sequence[str],
    present: Dict[str, Set[str]],
    live_date: str = "",
) -> Dict[str, Any]:
    live = str(live_date or "")[:10]
    rows: List[Dict[str, Any]] = []
    miss_names = 0
    for code in codes:
        c = str(code or "").strip()
        if not c:
            continue
        have = present.get(c) or set()
        cells = [
            classify_daily_cell(present=d in have, live=bool(live) and d == live)
            for d in dates
        ]
        miss_days = sum(1 for x in cells if x == "miss")
        if miss_days:
            miss_names += 1
        rows.append(
            {
                "stock_code": c,
                "stock_name": str(names.get(c) or ""),
                "cells": cells,
                "miss_days": miss_days,
            }
        )
    rows.sort(key=lambda r: (_problem_rank_daily(r["cells"]), r["stock_code"]))
    return {
        "dates": list(dates),
        "rows": rows,
        "miss_names": miss_names,
        "universe_count": len(rows),
    }


def build_minute_grid(
    *,
    codes: Sequence[str],
    names: Dict[str, str],
    dates: Sequence[str],
    hms_by_code_date: Dict[str, Dict[str, Set[str]]],
    live_date: str = "",
) -> Dict[str, Any]:
    live = str(live_date or "")[:10]
    rows: List[Dict[str, Any]] = []
    head_names = tail_names = miss_names = 0
    for code in codes:
        c = str(code or "").strip()
        if not c:
            continue
        by_day = hms_by_code_date.get(c) or {}
        cells: List[str] = []
        for d in dates:
            cells.append(
                classify_minute_day(by_day.get(d) or set(), live=bool(live) and d == live)
            )
        head_days = sum(1 for x in cells if x in ("head", "both"))
        tail_days = sum(1 for x in cells if x in ("tail", "both"))
        miss_days = sum(1 for x in cells if x == "miss")
        gap_days = sum(1 for x in cells if x == "gap")
        if head_days:
            head_names += 1
        if tail_days:
            tail_names += 1
        settled = [x for x in cells if x != "live"]
        if settled and all(x == "miss" for x in settled):
            miss_names += 1
        rows.append(
            {
                "stock_code": c,
                "stock_name": str(names.get(c) or ""),
                "cells": cells,
                "head_days": head_days,
                "tail_days": tail_days,
                "miss_days": miss_days,
                "gap_days": gap_days,
            }
        )
    rows.sort(key=lambda r: (_problem_rank_minute(r["cells"]), r["stock_code"]))
    return {
        "dates": list(dates),
        "rows": rows,
        "head_names": head_names,
        "tail_names": tail_names,
        "miss_names": miss_names,
        "universe_count": len(rows),
    }


def minute_day_detail(hms: Iterable[str], *, live: bool) -> Dict[str, Any]:
    present = {str(h or "")[:5] for h in hms if str(h or "")[:5] in _SLOT_SET}
    kind = classify_minute_day(present, live=live)
    ordered = sorted(present)

    def _row(slots: Sequence[str]) -> List[Dict[str, Any]]:
        return [{"hm": hm, "ok": hm in present} for hm in slots]

    return {
        "kind": kind,
        "n": len(present),
        "expected": len(FIVE_MINUTE_SLOTS),
        "first_hm": ordered[0] if ordered else "",
        "last_hm": ordered[-1] if ordered else "",
        "morning": _row(MORNING_SLOTS),
        "afternoon": _row(AFTERNOON_SLOTS),
    }


def _watch_codes(watching_limit: int) -> Tuple[List[str], Dict[str, str]]:
    from core.t0.intraday import resolve_stock_name
    from quant.research.factor_ols_clusters import clamp_watching_limit, merge_cluster_universe

    limit = clamp_watching_limit(watching_limit, 200)
    watchlist: List[Any] = []
    try:
        from core.watching.store import read_watching

        watchlist = list((read_watching() or {}).get("watchlist") or [])
    except Exception:  # noqa: BLE001
        logger.debug("integrity watchlist read failed", exc_info=True)
    uni = merge_cluster_universe(
        watchlist, [], watching_limit=limit, universe_mode="watching"
    )
    codes = [str(c).strip() for c in (uni.get("codes") or []) if str(c).strip()]
    names = {c: resolve_stock_name(c) for c in codes}
    return codes, names


def _live_date(*, now: Optional[datetime] = None) -> str:
    """交易日 15:05 前，会话日尚未收盘，格子标 live。"""
    from core.market.calendar import expected_latest_daily_bar_date, resolve_session_date

    dt = now or datetime.now()
    session = str(resolve_session_date(now=dt) or "")[:10]
    expected = str(expected_latest_daily_bar_date(now=dt) or "")[:10]
    if session and expected and session > expected:
        return session
    return ""


def _bare(code: str) -> str:
    raw = str(code or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:]
    return raw


def _sqlite_sets(
    codes: Sequence[str],
    dates: Sequence[str],
    *,
    minute: bool,
) -> Dict[str, Any]:
    from core.store import bars_backend, get_store_dir
    from core.store_bars_sqlite import get_conn

    if bars_backend() != "sqlite":
        return {}
    if not codes or not dates:
        return {}
    bare_codes = [_bare(c) for c in codes]
    d0, d1 = dates[0], dates[-1]
    conn = get_conn(get_store_dir())
    placeholders = ",".join("?" * len(bare_codes))
    if minute:
        sql = f"""
            SELECT code, date, substr(datetime, 12, 5) AS hm
            FROM minute_bars
            WHERE market='CN' AND period='5'
              AND date >= ? AND date <= ?
              AND code IN ({placeholders})
        """
        out: Dict[str, Dict[str, Set[str]]] = {}
        for row in conn.execute(sql, (d0, d1, *bare_codes)):
            code = str(row["code"] or "")
            day = str(row["date"] or "")[:10]
            hm = str(row["hm"] or "")[:5]
            out.setdefault(code, {}).setdefault(day, set()).add(hm)
        return out
    sql = f"""
        SELECT DISTINCT code, date
        FROM daily_bars
        WHERE market='CN' AND date >= ? AND date <= ?
          AND code IN ({placeholders})
    """
    out_days: Dict[str, Set[str]] = {}
    for row in conn.execute(sql, (d0, d1, *bare_codes)):
        code = str(row["code"] or "")
        day = str(row["date"] or "")[:10]
        out_days.setdefault(code, set()).add(day)
    return out_days


def minute_windows_clean(
    codes: Sequence[str],
    *,
    days: int = 40,
    now: Optional[datetime] = None,
) -> Dict[str, bool]:
    """最近 ``days`` 个交易日是否都齐。盘中当日不算缺。

    缺指 miss / head / tail / both / gap。没有收盘日可查时为 False。
    """
    from core.market.calendar import resolve_session_date
    from core.store import bars_backend

    bare = [b for b in (_bare(c) for c in codes) if b]
    orig_of: Dict[str, str] = {}
    for c in codes:
        b = _bare(c)
        if b:
            orig_of.setdefault(b, str(c).strip())
    if bars_backend() != "sqlite" or not bare:
        return {b: False for b in bare}
    n = max(1, min(int(days or 40), 120))
    dt = now or datetime.now()
    session = str(resolve_session_date(now=dt) or "")[:10]
    dates = recent_trading_days(n, end=session)
    live = _live_date(now=dt)
    closed = [d for d in dates if not (live and d == live)]
    hms = _sqlite_sets(bare, dates, minute=True)
    by_code = hms if isinstance(hms, dict) else {}
    out: Dict[str, bool] = {}
    for code in bare:
        by_day = by_code.get(code) or {}
        if not closed:
            out[code] = False
            continue
        out[code] = all(
            classify_minute_day(by_day.get(d) or set(), live=False) == "ok"
            for d in closed
        )
        orig = orig_of.get(code)
        if orig and orig != code:
            out[orig] = out[code]
    return out


def build_daily_integrity(
    *,
    watching_limit: int = 200,
    days: int = INTEGRITY_DAYS,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    from core.market.calendar import resolve_session_date
    from core.store import bars_backend

    n = clamp_integrity_days(days)
    dt = now or datetime.now()
    session = str(resolve_session_date(now=dt) or "")[:10]
    dates = recent_trading_days(n, end=session)
    live = _live_date(now=dt)
    codes, names = _watch_codes(watching_limit)
    if bars_backend() != "sqlite":
        return {
            "success": False,
            "error": "完整度格子只读 sqlite 仓",
            "dates": dates,
            "rows": [],
        }
    present = _sqlite_sets(codes, dates, minute=False)
    grid = build_daily_grid(
        codes=codes,
        names=names,
        dates=dates,
        present=present if isinstance(present, dict) else {},
        live_date=live,
    )
    grid["success"] = True
    grid["session_date"] = session
    grid["live_date"] = live or None
    grid["kind"] = "daily"
    return grid


def build_minute_integrity(
    *,
    watching_limit: int = 200,
    days: int = INTEGRITY_DAYS,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    from core.market.calendar import resolve_session_date
    from core.store import bars_backend

    n = clamp_integrity_days(days)
    dt = now or datetime.now()
    session = str(resolve_session_date(now=dt) or "")[:10]
    dates = recent_trading_days(n, end=session)
    live = _live_date(now=dt)
    codes, names = _watch_codes(watching_limit)
    if bars_backend() != "sqlite":
        return {
            "success": False,
            "error": "完整度格子只读 sqlite 仓",
            "dates": dates,
            "rows": [],
        }
    hms = _sqlite_sets(codes, dates, minute=True)
    grid = build_minute_grid(
        codes=codes,
        names=names,
        dates=dates,
        hms_by_code_date=hms if isinstance(hms, dict) else {},
        live_date=live,
    )
    grid["success"] = True
    grid["session_date"] = session
    grid["live_date"] = live or None
    grid["kind"] = "minute"
    return grid


def build_minute_day_slots(
    code: str,
    date: str,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    from core.store import bars_backend, get_store_dir
    from core.store_bars_sqlite import get_conn
    from core.t0.intraday import resolve_stock_name

    bare = _bare(code)
    day = str(date or "")[:10]
    if len(bare) < 6 or len(day) < 10:
        return {"success": False, "error": "缺少代码或日期"}
    if bars_backend() != "sqlite":
        return {"success": False, "error": "完整度格子只读 sqlite 仓"}
    live = _live_date(now=now) == day
    conn = get_conn(get_store_dir())
    hms: List[str] = []
    for row in conn.execute(
        """
        SELECT substr(datetime, 12, 5) AS hm
        FROM minute_bars
        WHERE market='CN' AND period='5' AND code=? AND date=?
        """,
        (bare, day),
    ):
        hm = str(row["hm"] or "")[:5]
        if hm:
            hms.append(hm)
    detail = minute_day_detail(hms, live=live)
    detail["success"] = True
    detail["stock_code"] = bare
    detail["stock_name"] = resolve_stock_name(bare)
    detail["date"] = day
    return detail
