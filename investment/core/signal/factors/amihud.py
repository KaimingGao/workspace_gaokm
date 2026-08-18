"""Amihud 非流动性因子（V2.1）：高价格冲击降分，相对 liquidity（活跃度）正交。"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple


def _amihud_vals(bars: List[dict], window: int = 10) -> List[float]:
    from core.bar_fields import bar_amount

    if not bars or len(bars) < 2:
        return []
    window = min(window, len(bars) - 1)
    vals: List[float] = []
    for i in range(len(bars) - window, len(bars)):
        prev = float(bars[i - 1].get("close") or 0.0)
        cur = float(bars[i].get("close") or 0.0)
        if prev <= 0 or cur <= 0:
            continue
        ret = abs(cur / prev - 1.0)
        amt = bar_amount(bars[i])
        if amt <= 0:
            continue
        vals.append(ret / amt)
    return vals


def score_amihud(bars: List[dict], **_kw) -> Tuple[float, Dict[str, Any]]:
    """缺数据 → 不进 ŷ（omit）。冲击越大分越低。

    用 log1p(|ret|/amount) 的窗内相对值打分，避免 ``raw * 1e9`` 对成交额单位敏感。
    """
    vals = _amihud_vals(bars or [])
    if not vals:
        return 50.0, {"amihud": None, "ok": False, "omit_sub_score": True}

    raw = sum(vals) / len(vals)
    # 窗内中位作锚：ratio 无量纲，元/万元同源缩放后相对排序不变
    ordered = sorted(vals)
    mid = ordered[len(ordered) // 2]
    if mid <= 0:
        return 50.0, {
            "amihud": round(raw, 12),
            "ok": False,
            "omit_sub_score": True,
        }
    rel = raw / mid
    # rel≈1 中性；>1 偏不流动
    log_rel = math.log(rel) if rel > 0 else 0.0
    if log_rel <= -0.3:
        score = 72.0
    elif log_rel <= 0.0:
        score = 62.0
    elif log_rel <= 0.5:
        score = 52.0
    elif log_rel <= 1.0:
        score = 42.0
    else:
        score = 32.0

    return float(score), {
        "amihud": round(raw, 12),
        "amihud_rel_to_median": round(rel, 4),
        "ok": True,
        "omit_sub_score": False,
    }
