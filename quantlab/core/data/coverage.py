"""观察池 / 纸面日线采集覆盖率（M1.2）。"""

import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from core.data.policy import COVERAGE_STALE_HOURS
from core.paths import PAPER_PATH, WATCHING_PATH


def _codes_from_watching() -> List[str]:
    if not os.path.isfile(WATCHING_PATH):
        return []
    try:
        import json

        with open(WATCHING_PATH, encoding="utf-8") as f:
            uni = json.load(f)
        return [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        return []


def _codes_from_paper(paper_path: Optional[str] = None) -> List[str]:
    path = paper_path or PAPER_PATH
    if not os.path.isfile(path):
        return []
    try:
        from core.paper import load_paper

        paper = load_paper(path)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        return []
    out: List[str] = []
    seen = set()
    for h in paper.get("holdings") or []:
        c = str(h.get("stock_code") or "").strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def universe_codes(
    codes: Optional[Sequence[str]] = None,
    *,
    include_paper: bool = True,
) -> List[str]:
    if codes is not None:
        raw = [str(c).strip() for c in codes if str(c).strip()]
    else:
        raw = _codes_from_watching()
        if include_paper:
            raw.extend(_codes_from_paper())
    out: List[str] = []
    seen = set()
    for c in raw:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _parse_fetched(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).strip())
    except ValueError:
        return None


def build_data_coverage(
    codes: Optional[Sequence[str]] = None,
    *,
    stale_hours: float = COVERAGE_STALE_HOURS,
    include_paper: bool = True,
) -> Dict[str, Any]:
    """汇总观察池∪纸面日线缓存覆盖：good/thin/empty、stale、fallback。"""
    from core.ports.market import resolve_market_code
    from core.store import peek_daily_cache_meta

    watch = universe_codes(codes, include_paper=include_paper)
    items: List[Dict[str, Any]] = []
    levels = {"good": 0, "thin": 0, "empty": 0}
    stale_n = 0
    missing_n = 0
    oldest: Optional[datetime] = None
    now = datetime.now()

    for code in watch:
        market, bare = resolve_market_code(code)
        meta = None
        if market and bare:
            meta = peek_daily_cache_meta(market, bare)
        if not meta:
            missing_n += 1
            levels["empty"] += 1
            items.append(
                {
                    "stock_code": code,
                    "ok": False,
                    "quality_level": "empty",
                    "stale": True,
                    "fetched_at": None,
                    "bar_count": 0,
                }
            )
            continue
        q = meta.get("quality") or {}
        level = str(q.get("level") or "empty")
        levels[level] = levels.get(level, 0) + 1
        fetched = _parse_fetched(meta.get("fetched_at"))
        stale = False
        if fetched is None:
            stale = True
        else:
            if oldest is None or fetched < oldest:
                oldest = fetched
            if stale_hours > 0 and now - fetched > timedelta(hours=stale_hours):
                stale = True
        if stale:
            stale_n += 1
        items.append(
            {
                "stock_code": code,
                "ok": True,
                "quality_level": level,
                "stale": stale,
                "fetched_at": meta.get("fetched_at"),
                "bar_count": meta.get("bar_count"),
                "date_min": meta.get("date_min"),
                "date_max": meta.get("date_max"),
                "data_source": meta.get("data_source"),
            }
        )

    total = len(watch)
    covered = total - missing_n
    coverage = round(covered / total, 3) if total else None
    good_n = levels.get("good") or 0
    alerts: List[Dict[str, str]] = []
    if total > 0 and coverage is not None and coverage < 0.8:
        alerts.append(
            {
                "level": "warn",
                "code": "bars_coverage_thin",
                "message": f"日线缓存覆盖 {covered}/{total}（{coverage:.0%}）",
            }
        )
    if stale_n > 0:
        alerts.append(
            {
                "level": "info",
                "code": "bars_stale",
                "message": f"日线缓存过期 {stale_n}/{total}（>{stale_hours:g}h）",
            }
        )
    if levels.get("empty", 0) > 0:
        alerts.append(
            {
                "level": "info",
                "code": "bars_empty",
                "message": f"日线缺失/空 {levels['empty']}/{total}",
            }
        )

    return {
        "ok": True,
        "total": total,
        "covered": covered,
        "missing": missing_n,
        "coverage": coverage,
        "empty_universe": total == 0,
        "levels": levels,
        "good_count": good_n,
        "stale_count": stale_n,
        "oldest_fetched_at": oldest.isoformat(timespec="seconds") if oldest else None,
        "stale_hours": stale_hours,
        "alerts": alerts,
        "items": items[:40],
        "note": "M1.2 观察池日线覆盖；跑 bars_warmup 可抬升覆盖。不代客下单。",
    }


def coverage_alerts_for_outbound(coverage: Dict[str, Any]) -> List[Dict[str, str]]:
    return list((coverage or {}).get("alerts") or [])
