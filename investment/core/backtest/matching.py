"""回测撮合约束（MatchPort · 研究近似，非交易所仿真）。

与 CostPort（`cost_port.py`）分工：本模块管**能否成交 / 何时成交 / 滑点档**；
费率与换手计费只走 `costs.py` ← CostPort。

- T+1：信号日不可当日卖出；默认次日开盘买、持有期满收盘卖
- 涨跌停：按板块阈值近似拦截买/卖；跌停日卖出可延后
- 滑点档：low / mid / high → 覆盖 cost_config base_slippage_bps
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


LIMIT_UP_PCT = 9.5
LIMIT_DOWN_PCT = -9.5
CHI_NEXT_LIMIT_UP_PCT = 19.5
CHI_NEXT_LIMIT_DOWN_PCT = -19.5
ST_LIMIT_UP_PCT = 4.5
ST_LIMIT_DOWN_PCT = -4.5

SLIPPAGE_TIERS: Dict[str, float] = {
    "low": 1.0,
    "mid": 3.0,
    "high": 8.0,
}


def _is_st_name(stock_name: Optional[str] = None) -> bool:
    n = str(stock_name or "").upper().replace(" ", "")
    return "ST" in n or "退" in str(stock_name or "")


def limit_up_threshold_for_code(
    stock_code: Optional[str] = None,
    *,
    stock_name: Optional[str] = None,
    is_st: Optional[bool] = None,
) -> float:
    """主板约 10%；创业板/科创约 20%；ST/退约 5%（研究近似用 9.5 / 19.5 / 4.5）。"""
    st = bool(is_st) if is_st is not None else _is_st_name(stock_name)
    if st:
        return ST_LIMIT_UP_PCT
    c = str(stock_code or "").strip()
    if c.startswith(("300", "301", "688", "689")):
        return CHI_NEXT_LIMIT_UP_PCT
    return LIMIT_UP_PCT


def limit_down_threshold_for_code(
    stock_code: Optional[str] = None,
    *,
    stock_name: Optional[str] = None,
    is_st: Optional[bool] = None,
) -> float:
    return -limit_up_threshold_for_code(
        stock_code, stock_name=stock_name, is_st=is_st
    )


def day_change_pct(prev_close: float, close: float) -> Optional[float]:
    try:
        p0 = float(prev_close)
        p1 = float(close)
    except (TypeError, ValueError):
        return None
    if p0 <= 0:
        return None
    return (p1 / p0 - 1.0) * 100.0


def is_limit_up(
    prev_close: float,
    close: float,
    *,
    threshold: Optional[float] = None,
    stock_code: Optional[str] = None,
    stock_name: Optional[str] = None,
    is_st: Optional[bool] = None,
) -> bool:
    if threshold is not None:
        thr = float(threshold)
    else:
        thr = limit_up_threshold_for_code(
            stock_code, stock_name=stock_name, is_st=is_st
        )
    ch = day_change_pct(prev_close, close)
    return ch is not None and ch >= thr


def is_limit_down(
    prev_close: float,
    close: float,
    *,
    threshold: Optional[float] = None,
    stock_code: Optional[str] = None,
    stock_name: Optional[str] = None,
    is_st: Optional[bool] = None,
) -> bool:
    if threshold is not None:
        thr = float(threshold)
    else:
        thr = limit_down_threshold_for_code(
            stock_code, stock_name=stock_name, is_st=is_st
        )
    ch = day_change_pct(prev_close, close)
    return ch is not None and ch <= thr


def bar_limit_flags(
    bars: List[dict],
    index: int,
    *,
    stock_code: Optional[str] = None,
    stock_name: Optional[str] = None,
    is_st: Optional[bool] = None,
    up_threshold: Optional[float] = None,
    down_threshold: Optional[float] = None,
) -> Dict[str, Any]:
    if index < 1 or index >= len(bars):
        return {
            "limit_up": False,
            "limit_down": False,
            "change_pct": None,
            "up_threshold": None,
            "down_threshold": None,
        }
    up = (
        float(up_threshold)
        if up_threshold is not None
        else limit_up_threshold_for_code(
            stock_code, stock_name=stock_name, is_st=is_st
        )
    )
    down = (
        float(down_threshold)
        if down_threshold is not None
        else limit_down_threshold_for_code(
            stock_code, stock_name=stock_name, is_st=is_st
        )
    )
    prev = bars[index - 1].get("close")
    cur = bars[index].get("close")
    ch = day_change_pct(prev, cur)
    return {
        "limit_up": bool(ch is not None and ch >= up),
        "limit_down": bool(ch is not None and ch <= down),
        "change_pct": None if ch is None else round(ch, 2),
        "up_threshold": up,
        "down_threshold": down,
    }


def cost_config_for_slippage_tier(
    tier: str = "mid",
    *,
    base: Optional[dict] = None,
) -> Dict[str, Any]:
    cfg = dict(base or {})
    key = (tier or "mid").strip().lower()
    if key not in SLIPPAGE_TIERS:
        key = "mid"
    cfg["base_slippage_bps"] = SLIPPAGE_TIERS[key]
    cfg["slippage_tier"] = key
    return cfg


def apply_match_filters(
    *,
    want_buy: bool,
    entry_bars: List[dict],
    entry_index: int,
    respect_limit: bool = True,
    stock_code: Optional[str] = None,
) -> Dict[str, Any]:
    """
    买入：涨停日不可买；卖出：跌停日不可卖（近似）。
    T+1 由调用方用 next_open / 持有期满体现。
    """
    flags = bar_limit_flags(entry_bars, entry_index, stock_code=stock_code)
    blocked = False
    reason = ""
    if want_buy and respect_limit and flags.get("limit_up"):
        blocked = True
        reason = "limit_up_no_buy"
    if not want_buy and respect_limit and flags.get("limit_down"):
        blocked = True
        reason = "limit_down_no_sell"
    return {
        "ok": not blocked,
        "blocked": blocked,
        "reason": reason,
        "t1": True,
        "flags": flags,
        "stock_code": stock_code,
        "note": "研究近似撮合：板别涨跌停阈值；跌停卖出可 resolve_exit_index 延后；非交易所仿真。",
    }


def resolve_exit_index(
    bars: List[dict],
    planned_exit_index: int,
    *,
    respect_limit: bool = True,
    stock_code: Optional[str] = None,
    max_defer: int = 3,
) -> Dict[str, Any]:
    """
    计划卖出日若跌停，向后延最多 max_defer 个交易日；仍跌停则 skipped。
    """
    n = len(bars or [])
    planned = int(planned_exit_index)
    out: Dict[str, Any] = {
        "ok": False,
        "exit_index": planned,
        "planned_exit_index": planned,
        "deferred": False,
        "deferred_days": 0,
        "skipped": False,
        "reason": "",
        "match": None,
    }
    if planned < 0 or planned >= n:
        out["skipped"] = True
        out["reason"] = "exit_out_of_range"
        return out

    max_defer = max(0, int(max_defer or 0))
    for d in range(0, max_defer + 1):
        idx = planned + d
        if idx >= n:
            out["skipped"] = True
            out["reason"] = "exit_no_more_bars"
            out["deferred_days"] = d
            return out
        match = apply_match_filters(
            want_buy=False,
            entry_bars=bars,
            entry_index=idx,
            respect_limit=respect_limit,
            stock_code=stock_code,
        )
        out["match"] = match
        if match.get("ok"):
            out["ok"] = True
            out["exit_index"] = idx
            out["deferred"] = d > 0
            out["deferred_days"] = d
            out["reason"] = "deferred" if d > 0 else "ok"
            return out

    out["skipped"] = True
    out["reason"] = "limit_down_no_sell"
    out["deferred_days"] = max_defer
    return out
