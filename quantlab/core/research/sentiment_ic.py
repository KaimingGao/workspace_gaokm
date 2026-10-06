"""FS2：alt_sentiment as_of 面板 IC（与 live 闸独立）。"""

from typing import Any, Dict, List, Optional, Sequence

from core.research.panel import collect_subscore_forward_panel
from core.signal.factors.meta.corr import pearson_with_reason


def compute_alt_sentiment_ic(
    bars: List[dict],
    *,
    stock_code: str,
    horizon_days: int = 3,
    index_bars: Optional[List[dict]] = None,
    fundamentals: Optional[dict] = None,
    pit_fundamentals: bool = True,
) -> Dict[str, Any]:
    """单票时序：as_of alt_sentiment vs forward return。"""
    code = str(stock_code or "").strip()
    if not code:
        return {"success": False, "error": "缺 stock_code", "factor": "alt_sentiment"}
    xs, ys, _dates = collect_subscore_forward_panel(
        bars,
        horizon_days=horizon_days,
        index_bars=index_bars,
        fundamentals=fundamentals,
        stock_code=code,
        pit_fundamentals=pit_fundamentals,
        sentiment_pit=True,
    )
    sx: List[float] = []
    sy: List[float] = []
    missing = 0
    for row, y in zip(xs, ys):
        v = (row or {}).get("alt_sentiment")
        if v is None:
            missing += 1
            continue
        try:
            sx.append(float(v))
            sy.append(float(y))
        except (TypeError, ValueError):
            missing += 1
    ic, reason = pearson_with_reason(sx, sy)
    n = len(sx)
    n_total = len(ys)
    miss_ratio = round(missing / float(n_total), 4) if n_total else 1.0
    return {
        "success": True,
        "factor": "alt_sentiment",
        "mode": "ts_ic",
        "stock_code": code,
        "horizon_days": horizon_days,
        "sample_count": n,
        "missing_days": missing,
        "missing_day_ratio": miss_ratio,
        "ic": round(ic, 4) if ic is not None else None,
        "exclusion_reason": reason,
        "note": "PIT 标题历史 as_of；无日志日计 missing。不进 live ŷ（需 include_in_score）。",
    }


def summarize_alt_sentiment_ic_pool(
    panels: Sequence[Dict[str, Any]],
    *,
    horizon_days: int = 3,
    pit_fundamentals: bool = True,
) -> Dict[str, Any]:
    """多票：逐票 TS IC 再汇总；覆盖率进报告。"""
    rows: List[Dict[str, Any]] = []
    ics: List[float] = []
    for item in panels or []:
        code = str(item.get("code") or item.get("stock_code") or "").strip()
        bars = item.get("bars") or []
        if not code or not bars:
            continue
        one = compute_alt_sentiment_ic(
            bars,
            stock_code=code,
            horizon_days=horizon_days,
            index_bars=item.get("index_bars"),
            fundamentals=item.get("fundamentals"),
            pit_fundamentals=pit_fundamentals,
        )
        rows.append(one)
        if one.get("ic") is not None:
            try:
                ics.append(float(one["ic"]))
            except (TypeError, ValueError):
                pass
    mean_ic = round(sum(ics) / len(ics), 4) if ics else None
    covered = sum(1 for r in rows if int(r.get("sample_count") or 0) > 0)
    return {
        "success": True,
        "factor": "alt_sentiment",
        "mode": "pool_ts_ic",
        "horizon_days": horizon_days,
        "stock_count": len(rows),
        "covered_count": covered,
        "mean_ic": mean_ic,
        "rows": rows,
        "note": "池内单票 as_of TS IC 均值；晋升开闸前需人审。",
    }
