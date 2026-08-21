"""IC 与 Top-K 净值时段对齐（T13）：正/负 IC 窗下的组合期收益对照。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional


def _ic_map(score_ic: Dict[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for row in (score_ic or {}).get("ic_series_tail") or []:
        d = str(row.get("date") or "").strip()
        if not d:
            continue
        try:
            out[d] = float(row.get("ic"))
        except (TypeError, ValueError):
            continue
    return out


def _ic_asof(ic_by_date: Dict[str, float], date: str) -> Optional[float]:
    """取 date 当日或之前最近的 IC。"""
    if date in ic_by_date:
        return ic_by_date[date]
    prev = None
    for d in sorted(ic_by_date.keys()):
        if d > date:
            break
        prev = d
    if prev is None:
        return None
    return ic_by_date[prev]


def align_ic_to_equity_periods(
    score_ic: Dict[str, Any],
    equity_curve: List[dict],
) -> Dict[str, Any]:
    """
    将各调仓期收益按「期初日 as-of IC」分桶：
    正 IC 窗 vs 非正 IC 窗的平均期收益 / 胜率。
    """
    if not (score_ic or {}).get("ok"):
        return {
            "ok": False,
            "reason": (score_ic or {}).get("reason") or "无有效 IC",
        }
    ic_by = _ic_map(score_ic)
    if len(ic_by) < 3:
        return {"ok": False, "reason": "IC 序列过短"}

    curve = list(equity_curve or [])
    if len(curve) < 2:
        return {"ok": False, "reason": "净值点不足"}

    pos: List[float] = []
    neg: List[float] = []
    periods: List[dict] = []

    for i in range(1, len(curve)):
        prev = curve[i - 1]
        cur = curve[i]
        d0 = str(prev.get("date") or "")
        d1 = str(cur.get("date") or "")
        try:
            ret = float(cur.get("return_pct"))
        except (TypeError, ValueError):
            continue
        # 用期初（上一净值日）as-of IC，贴近「决策后持有」
        ic = _ic_asof(ic_by, d0) if d0 else None
        if ic is None:
            continue
        bucket = "pos" if ic > 0 else "neg"
        (pos if bucket == "pos" else neg).append(ret)
        periods.append(
            {
                "start_date": d0,
                "end_date": d1,
                "return_pct": round(ret, 2),
                "ic_asof": round(ic, 4),
                "bucket": bucket,
            }
        )

    if len(periods) < 2:
        return {"ok": False, "reason": "可对齐期不足", "period_count": len(periods)}

    def _stats(xs: List[float]) -> Dict[str, Any]:
        if not xs:
            return {
                "count": 0,
                "avg_return_pct": None,
                "win_rate_pct": None,
                "total_return_compound_pct": None,
            }
        avg = sum(xs) / len(xs)
        wins = sum(1 for x in xs if x > 0)
        eq = 1.0
        for x in xs:
            eq *= 1.0 + x / 100.0
        return {
            "count": len(xs),
            "avg_return_pct": round(avg, 2),
            "win_rate_pct": round(100.0 * wins / len(xs), 1),
            "total_return_compound_pct": round((eq - 1.0) * 100.0, 2),
        }

    pos_s = _stats(pos)
    neg_s = _stats(neg)
    spread = None
    if pos_s["avg_return_pct"] is not None and neg_s["avg_return_pct"] is not None:
        spread = round(float(pos_s["avg_return_pct"]) - float(neg_s["avg_return_pct"]), 2)

    # 期望：正 IC 窗平均收益 > 非正；否则警示
    aligned_ok = spread is not None and spread > 0
    return {
        "ok": True,
        "period_count": len(periods),
        "pos_ic": pos_s,
        "neg_ic": neg_s,
        "avg_return_spread_pp": spread,
        "aligned_favor_pos_ic": aligned_ok,
        "periods_tail": periods[-40:],
        "note": (
            "按调仓期初 as-of 截面 IC 分桶：正 IC 窗 vs 非正 IC 窗的期收益。"
            "若正 IC 窗更赚 → 打分与 Top-K 时段同向；反之可能头部噪声或成本吞噬。"
        ),
    }
