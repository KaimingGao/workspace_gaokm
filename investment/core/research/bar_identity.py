"""回测加载后核对 K 线是不是这个代码自己的。

日线键和分钟键如果串了，成交价会整段变成另一只股票（例如 600036 的账上出现 600519 的开盘）。
本地仓没有这一天时不拦截，避免测试夹具被误删。
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_MAX_REL = 0.02


def _rel_diff(left: float, right: float) -> float:
    denom = max(abs(left), abs(right), 1e-9)
    return abs(left - right) / denom


def _last_daily_anchor(bars: List[dict]) -> Optional[Tuple[str, float]]:
    for bar in reversed(bars or []):
        if not isinstance(bar, dict) or bar.get("session_overlay"):
            continue
        day = str(bar.get("date") or "")[:10]
        try:
            close = float(bar.get("close") or 0)
        except (TypeError, ValueError):
            continue
        if day and close > 0:
            return day, close
    return None


def _minute_anchor(by_date: Dict[str, List[dict]]) -> Optional[Tuple[str, float]]:
    days = sorted(d for d in (by_date or {}) if d)
    if not days:
        return None
    for bar in by_date.get(days[-1]) or []:
        if not isinstance(bar, dict):
            continue
        stamp = str(bar.get("datetime") or "").strip()
        if len(stamp) < 16:
            day = str(bar.get("date") or days[-1])[:10]
            hm = str(bar.get("time") or "")[:5]
            stamp = f"{day} {hm}:00" if hm else ""
        try:
            px = float(bar.get("open") or bar.get("close") or 0)
        except (TypeError, ValueError):
            continue
        if stamp and px > 0:
            return stamp, px
    return None


def _resolve_cn(code: str) -> Optional[Tuple[str, str]]:
    text = str(code or "").strip()
    if not text:
        return None
    try:
        from core.market.symbols import resolve_market_code

        market, bare = resolve_market_code(text)
    except Exception:  # noqa: BLE001
        logger.debug("resolve market code failed for %s", text, exc_info=True)
        return None
    if market != "CN" or not bare:
        return None
    return str(market), str(bare)


def _store_db_path() -> Optional[str]:
    try:
        from core.store import bars_backend, get_store_dir
        from core.store_bars_sqlite import db_path
        import os

        if bars_backend() != "sqlite":
            return None
        path = db_path(get_store_dir())
        if not os.path.isfile(path):
            return None
        return path
    except Exception:  # noqa: BLE001
        logger.debug("bars db path failed", exc_info=True)
        return None


def _query_one(sql: str, args: tuple) -> Optional[sqlite3.Row]:
    path = _store_db_path()
    if not path:
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    try:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


def lookup_daily_close(code: str, day: str) -> Optional[float]:
    """本地仓这一天的前复权收盘。行里的 code 对不上请求就当没有。"""
    resolved = _resolve_cn(code)
    day_s = str(day or "")[:10]
    if not resolved or not day_s:
        return None
    market, bare = resolved
    try:
        row = _query_one(
            """
            SELECT close, code FROM daily_bars
            WHERE market=? AND code=? AND date=?
            ORDER BY CASE adjust_policy WHEN 'qfq' THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (market, bare, day_s),
        )
    except Exception:  # noqa: BLE001
        logger.debug("lookup daily close failed %s %s", code, day_s, exc_info=True)
        return None
    if row is None or str(row["code"] or "") != bare:
        return None
    try:
        px = float(row["close"] or 0)
    except (TypeError, ValueError):
        return None
    return px if px > 0 else None


def lookup_minute_open(code: str, stamp: str) -> Optional[float]:
    """本地 5 分钟仓这一根的开盘。行里的 code 对不上请求就当没有。"""
    resolved = _resolve_cn(code)
    when = str(stamp or "").strip()
    if not resolved or len(when) < 16:
        return None
    market, bare = resolved
    try:
        row = _query_one(
            """
            SELECT open, code FROM minute_bars
            WHERE market=? AND code=? AND datetime=? AND period='5'
            ORDER BY CASE adjust_policy WHEN 'qfq' THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (market, bare, when),
        )
    except Exception:  # noqa: BLE001
        logger.debug("lookup minute open failed %s %s", code, when, exc_info=True)
        return None
    if row is None or str(row["code"] or "") != bare:
        return None
    try:
        px = float(row["open"] or 0)
    except (TypeError, ValueError):
        return None
    return px if px > 0 else None


def drop_daily_series_mismatch(
    stock_bars: Dict[str, List[dict]],
    *,
    lookup_close: Optional[Callable[[str, str], Optional[float]]] = None,
    max_rel: float = _MAX_REL,
) -> Tuple[Dict[str, List[dict]], List[str]]:
    """末根收盘和本地仓不是同一只时丢掉整段日线。"""
    lookup = lookup_close or lookup_daily_close
    kept: Dict[str, List[dict]] = {}
    dropped: List[str] = []
    for code, bars in (stock_bars or {}).items():
        key = str(code or "").strip()
        anchor = _last_daily_anchor(list(bars or []))
        if not key or anchor is None:
            if key:
                kept[key] = list(bars or [])
            continue
        day, close = anchor
        try:
            stored = lookup(key, day)
        except Exception:  # noqa: BLE001
            logger.debug("daily identity lookup failed %s", key, exc_info=True)
            stored = None
        if stored is not None and _rel_diff(close, float(stored)) > float(max_rel):
            logger.warning(
                "drop daily series code=%s date=%s close=%s store=%s",
                key,
                day,
                close,
                stored,
            )
            dropped.append(key)
            continue
        kept[key] = list(bars or [])
    return kept, dropped


def drop_minute_maps_mismatch(
    minute_maps: Dict[str, Dict[str, List[dict]]],
    *,
    lookup_open: Optional[Callable[[str, str], Optional[float]]] = None,
    max_rel: float = _MAX_REL,
) -> Tuple[Dict[str, Dict[str, List[dict]]], List[str]]:
    """末日第一根 5 分钟开盘和本地仓不是同一只时丢掉这只的分钟线。"""
    lookup = lookup_open or lookup_minute_open
    kept: Dict[str, Dict[str, List[dict]]] = {}
    dropped: List[str] = []
    for code, by_date in (minute_maps or {}).items():
        key = str(code or "").strip()
        anchor = _minute_anchor(by_date if isinstance(by_date, dict) else {})
        if not key or anchor is None:
            if key and isinstance(by_date, dict):
                kept[key] = by_date
            continue
        stamp, px = anchor
        try:
            stored = lookup(key, stamp)
        except Exception:  # noqa: BLE001
            logger.debug("minute identity lookup failed %s", key, exc_info=True)
            stored = None
        if stored is not None and _rel_diff(px, float(stored)) > float(max_rel):
            logger.warning(
                "drop minute series code=%s stamp=%s open=%s store=%s",
                key,
                stamp,
                px,
                stored,
            )
            dropped.append(key)
            continue
        kept[key] = by_date
    return kept, dropped
