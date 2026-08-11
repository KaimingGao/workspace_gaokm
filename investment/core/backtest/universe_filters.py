"""回测宇宙轻量过滤（T7）：ST 名 / 成交额分位（池内，非全 A）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _is_st_name(name: str) -> bool:
    n = str(name or "").upper().replace(" ", "")
    return "ST" in n or "退" in str(name or "")


def _avg_amount(bars: List[dict], *, tail: int = 20) -> Optional[float]:
    from core.bar_fields import bar_amount

    amts: List[float] = []
    for b in (bars or [])[-tail:]:
        amt = bar_amount(b)
        if amt > 0:
            amts.append(amt)
    if not amts:
        return None
    return sum(amts) / len(amts)


def filter_universe_bars(
    stock_bars: Dict[str, List[dict]],
    *,
    exclude_st: bool = False,
    min_avg_amount_pctile: Optional[float] = None,
    name_by_code: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, List[dict]], List[Dict[str, Any]], Dict[str, Any]]:
    """
    返回 (filtered_bars, dropped, filters_meta)。
    min_avg_amount_pctile: 0–100，只保留成交额（优先 amount，否则价×量）≥该分位的票。
    """
    dropped: List[Dict[str, Any]] = []
    names = name_by_code or {}
    usable = dict(stock_bars or {})

    if exclude_st:
        keep: Dict[str, List[dict]] = {}
        for code, bars in usable.items():
            name = names.get(code) or ""
            if not name:
                # 尝试从 quote 补名
                try:
                    from core.data_service import get_quote

                    q = get_quote(code)
                    if q.get("success"):
                        name = str(q.get("stock_name") or q.get("name") or "")
                except Exception:
                    name = ""
            if _is_st_name(name):
                dropped.append(
                    {"stock_code": code, "reason": f"ST/退市名过滤（{name or code}）"}
                )
            else:
                keep[code] = bars
        usable = keep

    pct = min_avg_amount_pctile
    if pct is not None and usable:
        pct = max(0.0, min(100.0, float(pct)))
        scored = []
        for code, bars in usable.items():
            amt = _avg_amount(bars)
            if amt is None:
                dropped.append({"stock_code": code, "reason": "无成交额样本"})
                continue
            scored.append((code, amt, bars))
        if scored:
            scored.sort(key=lambda x: x[1])
            cut_i = int(len(scored) * pct / 100.0)
            cut_i = min(max(0, cut_i), max(0, len(scored) - 1))
            # 保留 ≥ 分位点
            threshold = scored[cut_i][1] if scored else 0
            keep2: Dict[str, List[dict]] = {}
            for code, amt, bars in scored:
                if amt >= threshold:
                    keep2[code] = bars
                else:
                    dropped.append(
                        {
                            "stock_code": code,
                            "reason": f"成交额低于池内 {pct:.0f} 分位",
                        }
                    )
            usable = keep2

    meta = {
        "exclude_st": bool(exclude_st),
        "min_avg_amount_pctile": min_avg_amount_pctile,
        "kept": len(usable),
        "dropped_count": len(dropped),
    }
    return usable, dropped, meta
