"""TTM (time-to-market) event logging and metrics."""

import json
import os
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from core.paths import TTM_EVENTS_PATH
from core.risk_metrics import _parse_ts

TTM_EVENT_IDEA = "idea_opened"
TTM_EVENT_BACKTEST = "backtest_ready"
TTM_EVENT_PAPER = "paper_rule_live"


def append_ttm_event(
    event: str,
    *,
    ref: str = "",
    meta: Optional[Dict[str, Any]] = None,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """R0.3 · 轻量 TTM 事件（JSONL）。"""
    p = path or TTM_EVENTS_PATH
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "event": str(event),
        "ref": ref or "",
        "meta": meta or {},
    }
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def load_ttm_events(*, limit: int = 200, path: Optional[str] = None) -> List[Dict[str, Any]]:
    p = path or TTM_EVENTS_PATH
    if not os.path.isfile(p):
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows[-max(1, int(limit or 200)) :]


def compute_ttm_metrics(
    events: Optional[Sequence[dict]] = None,
    *,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """Idea→回测、回测→纸面规则 的中位耗时（小时）。

    配对优先同 meta.cycle_id；无 cycle 时回退「下一时间戳」启发式。
    """
    evs = list(events) if events is not None else load_ttm_events(path=path)
    if not evs:
        return {
            "ok": False,
            "status": "unavailable",
            "reason": "no_events",
            "median_idea_to_backtest_hours": None,
            "median_backtest_to_paper_hours": None,
            "median_idea_to_paper_hours": None,
            "sample_count": 0,
            "note": "尚无 TTM 打点；回测成功 / promote 后写入。",
        }

    def _hours(a: datetime, b: datetime) -> float:
        return (b - a).total_seconds() / 3600.0

    def _cycle(e: dict) -> str:
        meta = e.get("meta") if isinstance(e.get("meta"), dict) else {}
        return str(meta.get("cycle_id") or "").strip()

    ideas = [(_parse_ts(e.get("ts")), e) for e in evs if e.get("event") == TTM_EVENT_IDEA]
    bts = [(_parse_ts(e.get("ts")), e) for e in evs if e.get("event") == TTM_EVENT_BACKTEST]
    papers = [(_parse_ts(e.get("ts")), e) for e in evs if e.get("event") == TTM_EVENT_PAPER]
    ideas = [(t, e) for t, e in ideas if t]
    bts = [(t, e) for t, e in bts if t]
    papers = [(t, e) for t, e in papers if t]

    def _pair_hours(
        starts: List[tuple],
        ends: List[tuple],
    ) -> List[float]:
        used_end_idx: set = set()
        out: List[float] = []
        # 1) 同 cycle_id
        end_by_cycle: Dict[str, List[tuple]] = {}
        for i, (te, ee) in enumerate(ends):
            cid = _cycle(ee)
            if cid:
                end_by_cycle.setdefault(cid, []).append((i, te, ee))
        for ts, es in starts:
            cid = _cycle(es)
            if not cid:
                continue
            cands = [
                (i, te)
                for i, te, _ in end_by_cycle.get(cid, [])
                if te >= ts and i not in used_end_idx
            ]
            if not cands:
                continue
            i, te = min(cands, key=lambda x: x[1])
            used_end_idx.add(i)
            out.append(_hours(ts, te))
        # 2) 无 cycle：下一时间戳（不复用已占用 end）
        for ts, es in starts:
            if _cycle(es):
                continue
            later = [
                (i, te)
                for i, (te, _) in enumerate(ends)
                if te >= ts and i not in used_end_idx
            ]
            if not later:
                continue
            i, te = min(later, key=lambda x: x[1])
            used_end_idx.add(i)
            out.append(_hours(ts, te))
        return out

    i2b = _pair_hours(ideas, bts)
    b2p = _pair_hours(bts, papers)
    i2p = _pair_hours(ideas, papers)

    def _med(xs: List[float]) -> Optional[float]:
        if not xs:
            return None
        return round(statistics.median(xs), 2)

    med_i2b, med_b2p, med_i2p = _med(i2b), _med(b2p), _med(i2p)
    ok = any(v is not None for v in (med_i2b, med_b2p, med_i2p))
    cycle_n = sum(1 for _, e in ideas + bts + papers if _cycle(e))
    return {
        "ok": ok,
        "status": "ok" if ok else "unavailable",
        "reason": None if ok else "insufficient_pairs",
        "median_idea_to_backtest_hours": med_i2b,
        "median_backtest_to_paper_hours": med_b2p,
        "median_idea_to_paper_hours": med_i2p,
        "sample_count": len(evs),
        "pair_counts": {
            "idea_to_backtest": len(i2b),
            "backtest_to_paper": len(b2p),
            "idea_to_paper": len(i2p),
        },
        "cycle_tagged_events": cycle_n,
        "note": "中位小时；优先 meta.cycle_id 配对，否则下一时间戳。",
    }
