"""Persist and load last portfolio backtest equity curve for north-star realization."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.paths import NORTH_STAR_LAST_BACKTEST_PATH
from core.risk_metrics import _MIN_ALIGN, _parse_ts, _safe_float
from core.io_atomic import atomic_write_json


def _curve_points(curve: Sequence[dict]) -> List[Tuple[str, float]]:
    """归一化为 (date_key, equity)。"""
    out: List[Tuple[str, float]] = []
    for pt in curve or []:
        eq = _safe_float((pt or {}).get("equity") or (pt or {}).get("value"))
        if eq is None or eq <= 0:
            continue
        raw = (pt or {}).get("date") or (pt or {}).get("ts") or ""
        ts = _parse_ts(raw)
        key = ts.strftime("%Y-%m-%d") if ts else str(raw)[:10]
        if len(key) < 8:
            continue
        out.append((key, eq))
    return out


def _paper_daily_equities(snapshots: Sequence[dict]) -> List[Tuple[str, float]]:
    """纸面快照按日取末日净值（同日多次盯市取最后一次）。"""
    by_day: Dict[str, float] = {}
    order: List[str] = []
    for snap in snapshots or []:
        eq = _safe_float((snap or {}).get("equity"))
        if eq is None or eq <= 0:
            continue
        ts = _parse_ts((snap or {}).get("ts"))
        if not ts:
            continue
        key = ts.strftime("%Y-%m-%d")
        if key not in by_day:
            order.append(key)
        by_day[key] = eq
    return [(k, by_day[k]) for k in order]


def paper_date_span(
    snapshots: Optional[Sequence[dict]] = None,
) -> Optional[Tuple[str, str]]:
    """纸面快照日期跨度 (first, last)；不足则 None。"""
    pts = _paper_daily_equities(snapshots or [])
    if len(pts) < 2:
        return None
    return pts[0][0], pts[-1][0]


def save_last_backtest_curve(
    curve: Sequence[dict],
    *,
    path: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    align_to_paper: bool = True,
    paper_snapshots: Optional[Sequence[dict]] = None,
) -> str:
    """组合回测成功后落盘，供 realization 与 API 读取。

    E1：默认按纸面日期跨度裁剪曲线，降低 no_date_overlap。
    """
    p = path or NORTH_STAR_LAST_BACKTEST_PATH
    pts = _curve_points(curve)
    meta_out: Dict[str, Any] = dict(meta or {})
    align_meta: Dict[str, Any] = {"align_to_paper": bool(align_to_paper)}

    snaps = list(paper_snapshots) if paper_snapshots is not None else None
    if snaps is None and align_to_paper:
        try:
            from core.paper import load_paper
            from core.paths import PAPER_PATH

            snaps = list((load_paper(PAPER_PATH) or {}).get("snapshots") or [])
        except Exception:
            snaps = []

    span = paper_date_span(snaps) if align_to_paper and snaps else None
    if span and pts:
        lo, hi = span
        clipped = [(d, e) for d, e in pts if lo <= d <= hi]
        align_meta["align_from"] = lo
        align_meta["align_to"] = hi
        align_meta["paper_span"] = f"{lo}→{hi}"
        align_meta["curve_before"] = len(pts)
        if len(clipped) >= _MIN_ALIGN + 1:
            pts = clipped
            align_meta["aligned"] = True
            align_meta["curve_after"] = len(pts)
        else:
            # 交集不足：保留原曲线后段，但写诊断，便于 UI / fit_gap
            align_meta["aligned"] = False
            align_meta["curve_after"] = len(clipped)
            align_meta["warn"] = (
                f"纸面 span {lo}→{hi} 与回测交集仅 {len(clipped)} 日；"
                "已保留原曲线，请加长 lookback 覆盖纸面窗口。"
            )
    elif align_to_paper:
        align_meta["aligned"] = False
        align_meta["warn"] = "无纸面日期跨度，未裁剪回测曲线"

    meta_out["align"] = align_meta
    slim = [{"date": d, "equity": e} for d, e in pts[-240:]]
    payload = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "point_count": len(slim),
        "curve": slim,
        "meta": meta_out,
    }
    atomic_write_json(p, payload)
    return p


def load_last_backtest_curve(path: Optional[str] = None) -> Dict[str, Any]:
    p = path or NORTH_STAR_LAST_BACKTEST_PATH
    if not os.path.isfile(p):
        return {"ok": False, "empty": True, "curve": [], "path": p}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {"ok": False, "empty": True, "error": str(e), "curve": [], "path": p}
    return {
        "ok": True,
        "empty": False,
        "path": p,
        "curve": data.get("curve") or [],
        "saved_at": data.get("saved_at"),
        "meta": data.get("meta") or {},
        "point_count": data.get("point_count"),
    }
