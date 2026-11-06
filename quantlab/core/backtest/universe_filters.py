"""回测宇宙轻量过滤（T7）：ST 名 / 成交额绝对门槛或池内分位。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Tuple

from core.universe.st_name import is_st_name as _is_st_name

# 兼容旧单测 ``from ... import _is_st_name``
__all__ = [
    "_is_st_name",
    "st_filter_name_map",
    "filter_universe_bars",
    "apply_tradable_policy_to_bars",
]


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
    min_avg_amount: Optional[float] = None,
    drop_missing_names: bool = False,
    name_by_code: Optional[Dict[str, str]] = None,
    allow_live_names: bool = False,
    amount_lookback: int = 20,
) -> Tuple[Dict[str, List[dict]], List[Dict[str, Any]], Dict[str, Any]]:
    """
    返回 (filtered_bars, dropped, filters_meta)。

    - ``min_avg_amount``：近窗均成交额绝对下限（元），与入池 policy 对齐时优先用这个。
    - ``min_avg_amount_pctile``：0–100，池内分位（研究对照；实盘入池勿单靠此）。
    - ``drop_missing_names``：缺名则丢（严筛口径）；默认 False 保持旧回测兼容。

    剔 ST 默认只用 ``name_by_code``。``allow_live_names=True`` 才补行情名。
    """
    dropped: List[Dict[str, Any]] = []
    names = dict(name_by_code or {})
    usable = dict(stock_bars or {})

    if exclude_st or drop_missing_names:
        keep: Dict[str, List[dict]] = {}
        for code, bars in usable.items():
            name = str(names.get(code) or "").strip()
            if not name and allow_live_names:
                try:
                    from core.data.facade import get_quote

                    q = get_quote(code)
                    if q.get("success"):
                        name = str(q.get("stock_name") or q.get("name") or "")
                        if name:
                            names[code] = name
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "universe ST name quote failed code=%s", code, exc_info=True
                    )
                    name = ""
            if drop_missing_names and not name:
                dropped.append({"stock_code": code, "reason": "缺名称"})
                continue
            if exclude_st and name and _is_st_name(name):
                dropped.append(
                    {"stock_code": code, "reason": f"ST/退市名过滤（{name or code}）"}
                )
                continue
            if exclude_st and not name and not drop_missing_names:
                # 旧口径：缺名保留
                keep[code] = bars
                continue
            keep[code] = bars
        usable = keep

    min_abs = min_avg_amount
    if min_abs is not None:
        try:
            min_abs = float(min_abs)
        except (TypeError, ValueError):
            min_abs = None
    if min_abs is not None and min_abs > 0 and usable:
        keep_amt: Dict[str, List[dict]] = {}
        for code, bars in usable.items():
            amt = _avg_amount(bars, tail=int(amount_lookback or 20))
            if amt is None:
                dropped.append({"stock_code": code, "reason": "无成交额样本"})
                continue
            if amt < min_abs:
                dropped.append(
                    {
                        "stock_code": code,
                        "reason": f"成交额低于绝对门槛（{amt:.0f}<{min_abs:.0f}）",
                    }
                )
                continue
            keep_amt[code] = bars
        usable = keep_amt

    pct = min_avg_amount_pctile
    if pct is not None and usable:
        pct = max(0.0, min(100.0, float(pct)))
        scored = []
        for code, bars in usable.items():
            amt = _avg_amount(bars, tail=int(amount_lookback or 20))
            if amt is None:
                dropped.append({"stock_code": code, "reason": "无成交额样本"})
                continue
            scored.append((code, amt, bars))
        if scored:
            scored.sort(key=lambda x: x[1])
            cut_i = int(len(scored) * pct / 100.0)
            cut_i = min(max(0, cut_i), max(0, len(scored) - 1))
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
        "min_avg_amount": min_avg_amount,
        "drop_missing_names": bool(drop_missing_names),
        "allow_live_names": bool(allow_live_names),
        "named": sum(1 for c in (stock_bars or {}) if str(names.get(c) or "").strip()),
        "kept": len(usable),
        "dropped_count": len(dropped),
    }
    return usable, dropped, meta


def apply_tradable_policy_to_bars(
    stock_bars: Dict[str, List[dict]],
    *,
    name_by_code: Optional[Dict[str, str]] = None,
    policy: Optional[dict] = None,
) -> Tuple[Dict[str, List[dict]], List[Dict[str, Any]], Dict[str, Any]]:
    """用 ``tradable_universe_policy`` 过滤回测 bars（与入池绝对门槛对齐）。"""
    from core.universe.policy import load_tradable_policy

    pol = dict(policy or load_tradable_policy())
    kept, dropped, meta = filter_universe_bars(
        stock_bars,
        exclude_st=bool(pol.get("exclude_st", True)),
        min_avg_amount=_to_opt_float(pol.get("min_amount")),
        drop_missing_names=bool(pol.get("require_name", True)),
        name_by_code=name_by_code,
        allow_live_names=False,
        amount_lookback=int(pol.get("amount_lookback_days") or 20),
    )
    meta["policy_version"] = str(pol.get("version") or "")
    return kept, dropped, meta


def _to_opt_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
