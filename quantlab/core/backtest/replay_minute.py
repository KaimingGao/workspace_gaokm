"""Replay minute-bar load and rebalance-window filter."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.backtest.replay_fill import REPLAY_FILL_CLOCKS, _minute_bar_hm

logger = logging.getLogger(__name__)

def _replay_window_minutes(
    minute_bars: Optional[Sequence[dict]] = None,
) -> List[dict]:
    """只留调仓窗 09:30–10:00 的 5m；尾盘残缺仓不当开盘锚。"""
    out: List[dict] = []
    for b in minute_bars or []:
        if not isinstance(b, dict):
            continue
        if _minute_bar_hm(b) in REPLAY_FILL_CLOCKS:
            out.append(b)
    out.sort(key=lambda b: str(b.get("datetime") or b.get("date") or ""))
    return out

def _local_minute_by_date(
    code: str, period: str = "5"
) -> Tuple[Dict[str, List[dict]], Dict[str, Any]]:
    """优先读本地 5m 缓存（忽略 TTL），与做 T 回测同口径。"""
    try:
        from core.ports.market import group_minute_bars_by_date, resolve_market_code
        from core.store import load_minute_cache

        market, bare = resolve_market_code(code)
        if market != "CN" or not bare:
            raw = str(code or "").strip()
            if raw.isdigit() and len(raw) == 6:
                market, bare = "CN", raw
            else:
                return {}, {"ok": False, "error": f"仅支持 A 股分钟线: {code}"}
        packed = load_minute_cache(
            market,
            bare,
            str(period or "5"),
            min_bars=10,
            max_age_hours=0,
            ignore_age=True,
        )
        if not packed or not packed[0]:
            return {}, {"ok": False, "from_cache": False, "period": period}
        bars, meta = packed
        meta = dict(meta or {})
        meta["ok"] = True
        meta["from_cache"] = True
        meta["cache_stale"] = True
        src = meta.get("data_source") or "cache"
        meta["data_source"] = f"cache:stale:{src}"
        meta["period"] = period
        return group_minute_bars_by_date(bars), meta
    except Exception as e:  # noqa: BLE001
        logger.debug("local minute cache failed for replay %s", code, exc_info=True)
        return {}, {"ok": False, "error": str(e), "period": period}


def load_replay_minute_bars(
    codes: Sequence[str],
    *,
    lookback_days: int = 50,
    period: str = "5",
    max_workers: int = 8,
) -> Tuple[Dict[str, Dict[str, List[dict]]], Dict[str, Any]]:
    """观察池 5m：先本地仓，缺则短超时补拉（跳过东财，避免拖死整次回测）。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    cleaned = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    out: Dict[str, Dict[str, List[dict]]] = {}
    missing: List[Dict[str, Any]] = []
    if not cleaned:
        return {}, {"ok": True, "period": period, "covered": 0, "missing": []}

    lb = max(10, min(int(lookback_days or 50), 120))
    workers = max(1, min(int(max_workers or 8), 16, len(cleaned)))

    def _one(code: str) -> Tuple[str, Dict[str, List[dict]], Dict[str, Any]]:
        by_date, meta = _local_minute_by_date(code, period)
        if by_date:
            return code, by_date, meta
        try:
            from core.ports.market import fetch_minute_bars, group_minute_bars_by_date

            bars, meta = fetch_minute_bars(
                code,
                period=period,
                use_cache=True,
                max_age_hours=48.0,
            )
            by_date = group_minute_bars_by_date(bars) if bars else {}
            return code, by_date or {}, dict(meta or {})
        except Exception as e:  # noqa: BLE001
            logger.debug("replay minute fetch failed %s", code, exc_info=True)
            return code, {}, {"ok": False, "error": str(e), "period": period}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_one, c): c for c in cleaned}
        for fut in as_completed(futs):
            code = futs[fut]
            try:
                _c, by_date, meta = fut.result()
            except Exception as e:  # noqa: BLE001
                logger.debug("replay minute worker failed %s", code, exc_info=True)
                missing.append({"stock_code": code, "error": str(e)})
                continue
            if by_date:
                out[code] = by_date
            else:
                missing.append(
                    {
                        "stock_code": code,
                        "error": (meta or {}).get("error") or "无分钟 K",
                    }
                )
    from core.research.bar_identity import drop_minute_maps_mismatch

    out, mismatched = drop_minute_maps_mismatch(out)
    for code in mismatched:
        missing.append({"stock_code": code, "error": "分钟线与代码不符"})
    return out, {
        "ok": True,
        "period": period,
        "lookback_days": lb,
        "covered": len(out),
        "missing": missing,
        "universe": len(cleaned),
    }

