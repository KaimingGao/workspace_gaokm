"""回测宇宙轻量过滤（T7）：ST 名 / 成交额分位（池内，非全 A）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Tuple


def _is_st_name(name: str) -> bool:
    n = str(name or "").upper().replace(" ", "")
    return "ST" in n or "退" in str(name or "")


def st_filter_name_map(watching: Optional[dict] = None) -> Dict[str, str]:
    """剔 ST 用的代码→名称。

    与成交行盖名同源：落盘名优先，空名用本地码表（``a_code_name``）补，不打行情。
    只读 ``watchlist_names`` 平行数组会漏掉空槽，码表里已是 ``*ST`` 的票会进回测。
    """
    if watching is None:
        from core.watching.store import read_watching

        watching = read_watching() or {}
    data = watching if isinstance(watching, dict) else {}
    wl = [str(c).strip() for c in (data.get("watchlist") or []) if str(c).strip()]
    if not wl:
        return {}
    from core.watching.store import watchlist_names_for

    names = watchlist_names_for(data, allow_live=False)
    out: Dict[str, str] = {}
    for i, raw in enumerate(wl):
        digits = "".join(ch for ch in raw if ch.isdigit())
        key = digits if len(digits) == 6 else raw
        nm = str(names[i] if i < len(names) else "").strip()
        if not key or not nm:
            continue
        out[key] = nm
        out[raw] = nm
    return out


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
    allow_live_names: bool = False,
) -> Tuple[Dict[str, List[dict]], List[Dict[str, Any]], Dict[str, Any]]:
    """
    返回 (filtered_bars, dropped, filters_meta)。
    min_avg_amount_pctile: 0–100，只保留成交额（优先 amount，否则价×量）≥该分位的票。

    剔 ST 默认只用 ``name_by_code``（观察池已存名）。``allow_live_names=True``
    才对缺名票打 ``get_quote``；回测大宇宙开 live 会逐只挂行情，极慢。
    缺名且不 live 时**保留**该票（不当 ST）。
    """
    dropped: List[Dict[str, Any]] = []
    names = dict(name_by_code or {})
    usable = dict(stock_bars or {})

    if exclude_st:
        keep: Dict[str, List[dict]] = {}
        for code, bars in usable.items():
            name = str(names.get(code) or "").strip()
            if not name and allow_live_names:
                # 尝试从 quote 补名（仅显式允许；回测勿开）
                try:
                    from core.data.facade import get_quote

                    q = get_quote(code)
                    if q.get("success"):
                        name = str(q.get("stock_name") or q.get("name") or "")
                        if name:
                            names[code] = name
                except Exception:  # noqa: BLE001 — best-effort / 非阻塞分支降级
                    logger.debug(
                        "universe ST name quote failed code=%s", code, exc_info=True
                    )
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
        "allow_live_names": bool(allow_live_names),
        "named": sum(1 for c in (stock_bars or {}) if str(names.get(c) or "").strip()),
        "kept": len(usable),
        "dropped_count": len(dropped),
    }
    return usable, dropped, meta
