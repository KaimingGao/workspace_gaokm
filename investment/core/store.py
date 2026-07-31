"""日线/分钟本地缓存：data/store/daily|minute；快照缓存 fundamentals/news。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.paths import STORE_DIR

DAILY_SUBDIR = "daily"
MINUTE_SUBDIR = "minute"
CACHE_VERSION = 1
SNAPSHOT_VERSION = 1
MINUTE_CACHE_VERSION = 1


def get_store_dir() -> str:
    return os.environ.get("INVESTMENT_STORE_DIR", STORE_DIR)


def daily_cache_path(market: str, code: str, store_dir: Optional[str] = None) -> str:
    base = store_dir or get_store_dir()
    safe_code = "".join(ch for ch in str(code) if ch.isalnum()).lower()
    return os.path.join(base, DAILY_SUBDIR, market.upper(), f"{safe_code}.json")


def minute_cache_path(
    market: str,
    code: str,
    period: str = "5",
    store_dir: Optional[str] = None,
) -> str:
    base = store_dir or get_store_dir()
    safe_code = "".join(ch for ch in str(code) if ch.isalnum()).lower()
    safe_period = "".join(ch for ch in str(period) if ch.isalnum()) or "5"
    return os.path.join(
        base, MINUTE_SUBDIR, safe_period, market.upper(), f"{safe_code}.json"
    )


def snapshot_cache_path(kind: str, code: str, store_dir: Optional[str] = None) -> str:
    base = store_dir or get_store_dir()
    safe_kind = "".join(ch for ch in str(kind) if ch.isalnum() or ch in ("_", "-")).lower()
    safe_code = "".join(ch for ch in str(code) if ch.isalnum()).lower() or "unknown"
    return os.path.join(base, safe_kind, f"{safe_code}.json")


def _parse_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    text = str(s).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def merge_bars_by_date(
    existing: List[dict],
    incoming: List[dict],
) -> List[dict]:
    """按 date 合并日线；同日以后到为准；升序。"""
    by_date: Dict[str, dict] = {}
    for b in list(existing or []) + list(incoming or []):
        d = str((b or {}).get("date") or "").strip()
        if not d:
            continue
        by_date[d] = b
    return [by_date[k] for k in sorted(by_date.keys())]


def merge_minute_bars_by_time(
    existing: List[dict],
    incoming: List[dict],
) -> List[dict]:
    """按 datetime 合并分钟线；同时刻以后到为准；升序。"""
    by_ts: Dict[str, dict] = {}
    for b in list(existing or []) + list(incoming or []):
        ts = str((b or {}).get("datetime") or (b or {}).get("date") or "").strip()
        if not ts:
            continue
        by_ts[ts] = b
    return [by_ts[k] for k in sorted(by_ts.keys())]


def assess_quality(
    bars: List[dict],
    *,
    data_source: str,
    fetched_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """质量标记：good / thin / empty；附 bar 区间与备注。"""
    notes: List[str] = []
    n = len(bars or [])
    if n == 0:
        return {
            "level": "empty",
            "bar_count": 0,
            "first_date": None,
            "last_date": None,
            "notes": ["无日线"],
        }

    first = bars[0].get("date")
    last = bars[-1].get("date")
    last_dt = _parse_date(str(last or ""))
    if last_dt:
        age_days = (datetime.now() - last_dt).days
        if age_days > 10:
            notes.append(f"末根 K 线偏旧({age_days}天前)")
    else:
        notes.append("末根日期无法解析")

    if data_source in ("empty", "quote_fallback"):
        notes.append("非完整日线数据源")
        level = "thin"
    elif n < 15:
        notes.append("样本偏少")
        level = "thin"
    elif notes:
        level = "thin"
    else:
        level = "good"

    if fetched_at:
        notes.append(f"缓存于 {fetched_at.isoformat(timespec='seconds')}")

    return {
        "level": level,
        "bar_count": n,
        "first_date": first,
        "last_date": last,
        "notes": notes,
    }


def load_daily_cache(
    market: str,
    code: str,
    *,
    min_bars: int = 1,
    max_age_hours: float = 24.0,
    store_dir: Optional[str] = None,
    ignore_age: bool = False,
) -> Optional[Tuple[List[dict], Dict[str, Any]]]:
    path = daily_cache_path(market, code, store_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    fetched_s = payload.get("fetched_at") or ""
    try:
        fetched_at = datetime.fromisoformat(fetched_s)
    except ValueError:
        fetched_at = datetime.fromtimestamp(os.path.getmtime(path))

    if not ignore_age and max_age_hours > 0:
        if datetime.now() - fetched_at > timedelta(hours=max_age_hours):
            return None

    bars = payload.get("bars") or []
    if not isinstance(bars, list) or len(bars) < min_bars:
        return None

    meta = {
        "market": payload.get("market") or market,
        "code": payload.get("code") or code,
        "stock_code": payload.get("stock_code"),
        "data_source": payload.get("data_source") or "cache",
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
        "quality": payload.get("quality")
        or assess_quality(
            bars, data_source=payload.get("data_source") or "cache", fetched_at=fetched_at
        ),
        "from_cache": True,
        "date_min": payload.get("date_min") or (bars[0].get("date") if bars else None),
        "date_max": payload.get("date_max") or (bars[-1].get("date") if bars else None),
        "bar_count": payload.get("bar_count") or len(bars),
        "adjust_policy": payload.get("adjust_policy"),
    }
    return bars, meta


def save_daily_cache(
    market: str,
    code: str,
    bars: List[dict],
    *,
    data_source: str,
    stock_code: Optional[str] = None,
    store_dir: Optional[str] = None,
    adjust_policy: Optional[str] = "qfq",
) -> str:
    path = daily_cache_path(market, code, store_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fetched_at = datetime.now()
    quality = assess_quality(bars, data_source=data_source, fetched_at=fetched_at)
    date_min = bars[0].get("date") if bars else None
    date_max = bars[-1].get("date") if bars else None
    payload = {
        "version": CACHE_VERSION,
        "market": market.upper(),
        "code": code,
        "stock_code": stock_code or code,
        "data_source": data_source,
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
        "quality": quality,
        "bars": bars,
        "bar_count": len(bars or []),
        "date_min": date_min,
        "date_max": date_max,
        "adjust_policy": adjust_policy,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def load_snapshot_cache(
    kind: str,
    code: str,
    *,
    max_age_hours: float = 24.0,
    store_dir: Optional[str] = None,
) -> Optional[Tuple[Any, Dict[str, Any]]]:
    path = snapshot_cache_path(kind, code, store_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    fetched_s = payload.get("fetched_at") or ""
    try:
        fetched_at = datetime.fromisoformat(fetched_s)
    except ValueError:
        fetched_at = datetime.fromtimestamp(os.path.getmtime(path))
    if max_age_hours > 0 and datetime.now() - fetched_at > timedelta(hours=max_age_hours):
        return None
    data = payload.get("data")
    meta = {
        "kind": kind,
        "code": payload.get("code") or code,
        "data_source": payload.get("data_source") or kind,
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
        "from_cache": True,
        "non_pit": True,
    }
    return data, meta


def save_snapshot_cache(
    kind: str,
    code: str,
    data: Any,
    *,
    data_source: str,
    store_dir: Optional[str] = None,
    as_of: Optional[str] = None,
) -> str:
    path = snapshot_cache_path(kind, code, store_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fetched_at = datetime.now()
    fetched_s = fetched_at.isoformat(timespec="seconds")

    existing_history: List[dict] = []
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                old = json.load(f)
            existing_history = list(old.get("history") or [])
        except (OSError, json.JSONDecodeError):
            existing_history = []

    payload: Dict[str, Any] = {
        "version": SNAPSHOT_VERSION,
        "kind": kind,
        "code": code,
        "data_source": data_source,
        "fetched_at": fetched_s,
        "non_pit": True,
        "data": data,
    }

    # R1：fundamentals 维护 as_of history 面板（最小 PIT）
    if str(kind).lower() == "fundamentals":
        try:
            from core.fundamentals_pit import (
                _date_key,
                _metrics_as_of_hint,
                merge_history_point,
            )
            from core.signal.fundamentals_bridge import normalize_fundamentals_metrics

            metrics = normalize_fundamentals_metrics(data) or {}
            point_as_of = (
                _date_key(as_of)
                or _metrics_as_of_hint(data)
                or _date_key(fetched_s)
            )
            if metrics and point_as_of:
                payload["history"] = merge_history_point(
                    existing_history,
                    as_of=point_as_of,
                    metrics=metrics,
                    fetched_at=fetched_s,
                    data_source=data_source,
                )
                payload["latest_as_of"] = point_as_of
            elif existing_history:
                payload["history"] = existing_history
        except Exception:
            if existing_history:
                payload["history"] = existing_history

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def peek_daily_cache_meta(
    market: str,
    code: str,
    store_dir: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """读日线缓存元数据（不校验 TTL）。"""
    path = daily_cache_path(market, code, store_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    bars = payload.get("bars") or []
    return {
        "path": path,
        "market": payload.get("market") or market,
        "code": payload.get("code") or code,
        "stock_code": payload.get("stock_code"),
        "data_source": payload.get("data_source"),
        "fetched_at": payload.get("fetched_at"),
        "quality": payload.get("quality"),
        "bar_count": payload.get("bar_count") or len(bars),
        "date_min": payload.get("date_min") or (bars[0].get("date") if bars else None),
        "date_max": payload.get("date_max") or (bars[-1].get("date") if bars else None),
        "adjust_policy": payload.get("adjust_policy"),
    }


def list_cached_symbols(
    market: Optional[str] = None,
    store_dir: Optional[str] = None,
) -> List[Dict[str, Any]]:
    base = os.path.join(store_dir or get_store_dir(), DAILY_SUBDIR)
    if not os.path.isdir(base):
        return []
    out: List[Dict[str, Any]] = []
    markets = [market.upper()] if market else os.listdir(base)
    for mkt in markets:
        mdir = os.path.join(base, mkt)
        if not os.path.isdir(mdir):
            continue
        for name in os.listdir(mdir):
            if not name.endswith(".json"):
                continue
            path = os.path.join(mdir, name)
            try:
                with open(path, encoding="utf-8") as f:
                    payload = json.load(f)
                out.append(
                    {
                        "market": mkt,
                        "code": payload.get("code") or name[:-5],
                        "stock_code": payload.get("stock_code"),
                        "data_source": payload.get("data_source"),
                        "fetched_at": payload.get("fetched_at"),
                        "quality": payload.get("quality"),
                        "path": path,
                    }
                )
            except (OSError, json.JSONDecodeError):
                continue
    return out


def load_minute_cache(
    market: str,
    code: str,
    period: str = "5",
    *,
    min_bars: int = 1,
    max_age_hours: float = 24.0,
    store_dir: Optional[str] = None,
    ignore_age: bool = False,
) -> Optional[Tuple[List[dict], Dict[str, Any]]]:
    path = minute_cache_path(market, code, period, store_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    fetched_s = payload.get("fetched_at") or ""
    try:
        fetched_at = datetime.fromisoformat(fetched_s)
    except ValueError:
        fetched_at = datetime.fromtimestamp(os.path.getmtime(path))

    if not ignore_age and max_age_hours > 0:
        if datetime.now() - fetched_at > timedelta(hours=max_age_hours):
            return None

    bars = payload.get("bars") or []
    if not isinstance(bars, list) or len(bars) < min_bars:
        return None

    meta = {
        "market": payload.get("market") or market,
        "code": payload.get("code") or code,
        "stock_code": payload.get("stock_code"),
        "period": payload.get("period") or period,
        "data_source": payload.get("data_source") or "cache",
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
        "from_cache": True,
        "date_min": payload.get("date_min"),
        "date_max": payload.get("date_max"),
        "bar_count": payload.get("bar_count") or len(bars),
        "adjust_policy": payload.get("adjust_policy"),
    }
    return bars, meta


def save_minute_cache(
    market: str,
    code: str,
    bars: List[dict],
    *,
    period: str = "5",
    data_source: str,
    stock_code: Optional[str] = None,
    store_dir: Optional[str] = None,
    adjust_policy: Optional[str] = "qfq",
) -> str:
    path = minute_cache_path(market, code, period, store_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fetched_at = datetime.now()
    date_min = None
    date_max = None
    if bars:
        date_min = bars[0].get("date") or str(bars[0].get("datetime") or "")[:10]
        date_max = bars[-1].get("date") or str(bars[-1].get("datetime") or "")[:10]
    payload = {
        "version": MINUTE_CACHE_VERSION,
        "market": market.upper(),
        "code": code,
        "stock_code": stock_code or code,
        "period": str(period),
        "data_source": data_source,
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
        "bars": bars,
        "bar_count": len(bars or []),
        "date_min": date_min,
        "date_max": date_max,
        "adjust_policy": adjust_policy,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path
