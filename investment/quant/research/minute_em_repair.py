"""东财补缺：只把比本地更齐的 5 分钟交易日写入，不打新浪。

强更会把东财返回的每一天整段替换。窗口边上的短日会把已经齐的本地日盖掉。
这里先读完整度格子，只对缺、头缺、尾缺、中缺的日子拉东财，
并且新的一天不少于本地已有根数才落盘。今天盘中不写。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from core.watching.store import WATCHING_MAX_SIZE
from quant.research.bars_integrity import (
    FIVE_MINUTE_SLOTS,
    classify_minute_day,
)

logger = logging.getLogger(__name__)

_SLOT_SET = set(FIVE_MINUTE_SLOTS)
# 越大越齐。头缺和尾缺同级，比「两头都缺」好，比「中间缺」差。
_KIND_RANK = {
    "miss": 0,
    "both": 1,
    "head": 2,
    "tail": 2,
    "gap": 3,
    "ok": 4,
}
_BAD_KINDS = frozenset({"miss", "both", "head", "tail", "gap"})


def present_slots(hms: Iterable[str]) -> Set[str]:
    return {str(h or "")[:5] for h in hms if str(h or "")[:5] in _SLOT_SET}


def should_replace_day(local_hms: Iterable[str], incoming_hms: Iterable[str]) -> bool:
    """incoming 比本地更齐才覆盖。一样齐、或更短，都不写。"""
    local = present_slots(local_hms)
    incoming = present_slots(incoming_hms)
    if not incoming:
        return False
    local_rank = _KIND_RANK.get(classify_minute_day(local, live=False), 0)
    incoming_rank = _KIND_RANK.get(classify_minute_day(incoming, live=False), 0)
    if incoming_rank > local_rank:
        return True
    if incoming_rank == local_rank and len(incoming) > len(local):
        return True
    return False


def bars_by_date(bars: Sequence[dict]) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        day = str(bar.get("date") or str(bar.get("datetime") or "")[:10])[:10]
        if len(day) != 10:
            continue
        out.setdefault(day, []).append(bar)
    return out


def hms_of_bars(bars: Sequence[dict]) -> Set[str]:
    hms: Set[str] = set()
    for bar in bars or []:
        raw = str(bar.get("datetime") or "")
        hm = raw[11:16] if len(raw) >= 16 else ""
        if hm in _SLOT_SET:
            hms.add(hm)
    return hms


def repair_cluster_minute_from_em(
    *,
    watching_limit: int = WATCHING_MAX_SIZE,
    lookback_days: int = 90,
    period: str = "5",
    progress_cb: Optional[Any] = None,
) -> Dict[str, Any]:
    """观察池里不齐的 5 分钟日，用东财补。只写更齐的日子。"""
    from adapters.market.minute_history import (
        _fetch_em_minute_bars,
        _throttle_minute_remote_fetch,
    )
    from core.data.policy import MINUTE_EM_LOOKBACK_MAX_DAYS
    from core.store import bars_backend, save_minute_cache
    from quant.research.bars_integrity import INTEGRITY_DAYS, build_minute_integrity

    if bars_backend() != "sqlite":
        return {"ok": False, "error": "分钟补缺只写 sqlite 仓", "kind": "minute_em_repair"}

    period_s = str(period or "5")
    if period_s != "5":
        return {"ok": False, "error": "东财补缺只处理 5 分钟", "kind": "minute_em_repair"}

    span = min(max(int(lookback_days or 90), 5), int(MINUTE_EM_LOOKBACK_MAX_DAYS))
    grid = build_minute_integrity(watching_limit=watching_limit, days=INTEGRITY_DAYS)
    if not grid.get("success"):
        return {
            "ok": False,
            "error": str(grid.get("error") or "完整度读取失败"),
            "kind": "minute_em_repair",
        }
    dates = list(grid.get("dates") or [])
    live = str(grid.get("live_date") or "")[:10]
    rows = list(grid.get("rows") or [])
    total = len(rows)
    written_days = 0
    written_names = 0
    skipped_names = 0
    fetched = 0
    errors: List[str] = []

    def _tick(cur: int, code: str) -> None:
        if not progress_cb:
            return
        try:
            progress_cb(cur, total, code)
        except Exception:  # noqa: BLE001
            logger.debug("minute em repair progress_cb failed", exc_info=True)

    for i, row in enumerate(rows, 1):
        code = str(row.get("stock_code") or "").strip()
        cells = list(row.get("cells") or [])
        bad = [
            dates[j]
            for j, kind in enumerate(cells)
            if j < len(dates) and kind in _BAD_KINDS and dates[j] != live
        ]
        if not code or not bad:
            skipped_names += 1
            _tick(i, f"跳过 {code}" if code else "跳过")
            continue
        local = _local_hms(code, bad)
        incoming, meta, err = _fetch_em_minute_bars(
            code,
            period=period_s,
            lookback_days=span,
            adjust="qfq",
            span_cap=span,
        )
        _throttle_minute_remote_fetch()
        fetched += 1
        if err or not incoming:
            errors.append(f"{code}:{err or 'empty'}")
            _tick(i, f"失败 {code}")
            continue
        by_day = bars_by_date(incoming)
        keep: List[dict] = []
        kept_days = 0
        for day in bad:
            got = by_day.get(day) or []
            if should_replace_day(local.get(day) or set(), hms_of_bars(got)):
                keep.extend(got)
                kept_days += 1
        if keep:
            save_minute_cache(
                "CN",
                code,
                keep,
                period=period_s,
                data_source=str((meta or {}).get("data_source") or "akshare:stock_zh_a_hist_min_em:5"),
                stock_code=code,
                adjust_policy="qfq",
            )
            written_days += kept_days
            written_names += 1
        _tick(i, f"补缺 {code}")

    return {
        "ok": True if (written_names or skipped_names or not errors) else False,
        "kind": "minute_em_repair",
        "mode": "repair",
        "period": period_s,
        "lookback_days": span,
        "window_days": len(dates),
        "live_date": live or None,
        "total": total,
        "fetched": fetched,
        "skipped_names": skipped_names,
        "written_names": written_names,
        "written_days": written_days,
        "errors": errors[:10],
        "note": (
            f"东财补缺 · 回看 {span} 日历日 · 只写更齐的交易日 · "
            f"跳过 {skipped_names} · 写入 {written_names} 只 / {written_days} 日 · 不打新浪"
        ),
    }


def _local_hms(code: str, days: Sequence[str]) -> Dict[str, Set[str]]:
    from core.store import get_store_dir
    from core.store_bars_sqlite import get_conn

    wanted = [str(d)[:10] for d in days if len(str(d)[:10]) == 10]
    if not wanted:
        return {}
    placeholders = ",".join("?" * len(wanted))
    conn = get_conn(get_store_dir())
    out: Dict[str, Set[str]] = {}
    sql = f"""
        SELECT date, substr(datetime, 12, 5) AS hm
        FROM minute_bars
        WHERE market='CN' AND period='5' AND code=?
          AND date IN ({placeholders})
    """
    for row in conn.execute(sql, (code, *wanted)):
        day = str(row["date"] or "")[:10]
        hm = str(row["hm"] or "")[:5]
        if hm in _SLOT_SET:
            out.setdefault(day, set()).add(hm)
    return out
