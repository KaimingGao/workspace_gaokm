"""隔夜跳空风险因子（V2.1 有界扩面）。"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def score_gap_risk(bars: List[dict]) -> Tuple[float, Dict[str, Any]]:
    """
    近端隔夜跳空幅度：温和跳空中性偏正；过大跳空降分（缺口风险）。
    缺数据 → 中性 50。
    """
    if not bars or len(bars) < 3:
        return 50.0, {"ok": False, "reason": "thin_bars"}

    gaps = []
    for i in range(1, min(len(bars), 8)):
        prev = bars[-(i + 1)]
        cur = bars[-i]
        try:
            pc = float(prev.get("close"))
            o = float(cur.get("open") if cur.get("open") is not None else cur.get("close"))
        except (TypeError, ValueError):
            continue
        if pc <= 0:
            continue
        gaps.append((o / pc - 1.0) * 100.0)

    if not gaps:
        return 50.0, {"ok": False, "reason": "no_gap"}

    last = gaps[0]
    abs_last = abs(last)
    # 过大跳空降分
    if abs_last >= 7:
        score = 35.0
    elif abs_last >= 4:
        score = 42.0
    elif abs_last >= 2:
        score = 48.0
    else:
        score = 55.0
    return float(score), {
        "ok": True,
        "last_gap_pct": round(last, 3),
        "abs_gap_pct": round(abs_last, 3),
        "sample_gaps": [round(g, 3) for g in gaps[:5]],
    }
