"""日线/分钟本地缓存：data/store/daily|minute 或 bars.db；快照缓存 fundamentals/news。

Bars 后端由 ``INVESTMENT_BARS_BACKEND`` 选择：``sqlite``（默认）| ``json``。
配置/账本/基本面快照仍走 JSON 文件。
"""


import json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, Iterator, List, Optional, Tuple

from core.data.policy import (
    DAILY_BARS_MAX_KEEP,
    DAILY_CACHE_HOURS,
    FUNDAMENTALS_HISTORY_MAX_POINTS,
    MINUTE_BARS_MAX_KEEP,
    QUALITY_STALE_BAR_DAYS,
    THIN_MIN_BARS,
)
from core.file_lock import path_lock
from core.io_atomic import atomic_write_json
from core.paths import STORE_DIR

logger = logging.getLogger(__name__)

DAILY_SUBDIR = "daily"
MINUTE_SUBDIR = "minute"
CACHE_VERSION = 1
SNAPSHOT_VERSION = 1
MINUTE_CACHE_VERSION = 1

_IO_ERROR_COUNT = 0
_IO_ERROR_LOCK = threading.Lock()
_REFRESH_LOCKS: Dict[str, threading.Lock] = {}
_REFRESH_GUARD = threading.Lock()


def get_store_dir() -> str:
    return os.environ.get("INVESTMENT_STORE_DIR", STORE_DIR)


def bars_backend() -> str:
    """``sqlite`` | ``json``；非法值回退 sqlite。"""
    raw = str(os.environ.get("INVESTMENT_BARS_BACKEND") or "sqlite").strip().lower()
    if raw in ("json", "file", "files"):
        return "json"
    return "sqlite"


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


def _note_io_error(where: str, err: BaseException) -> None:
    global _IO_ERROR_COUNT
    with _IO_ERROR_LOCK:
        _IO_ERROR_COUNT += 1
        n = _IO_ERROR_COUNT
    logger.warning("store_io_error count=%s where=%s err=%s", n, where, err)


def io_error_stats() -> Dict[str, Any]:
    with _IO_ERROR_LOCK:
        return {"io_error_count": int(_IO_ERROR_COUNT)}


def reset_io_error_stats() -> None:
    global _IO_ERROR_COUNT
    with _IO_ERROR_LOCK:
        _IO_ERROR_COUNT = 0


@contextmanager
def code_refresh_lock(market: str, code: str, *, kind: str = "daily") -> Iterator[None]:
    """同票远端刷新互斥（防 thundering herd）；进程内有效。"""
    key = f"{kind}:{str(market).upper()}:{str(code).strip().lower()}"
    with _REFRESH_GUARD:
        lock = _REFRESH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _REFRESH_LOCKS[key] = lock
    with lock:
        yield


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
    *,
    lock_calendar_day: bool = True,
) -> List[dict]:
    """按 datetime 合并分钟线；升序。

    ``lock_calendar_day=True``（默认）：incoming 覆盖到的**交易日整段替换**，
    禁止同日跨源按时间戳缝合（上午东财、下午新浪）。无日期的旧日保留。
    ``False``：旧行为，同时刻以后到为准。
    """
    if not lock_calendar_day:
        by_ts: Dict[str, dict] = {}
        for b in list(existing or []) + list(incoming or []):
            ts = str((b or {}).get("datetime") or (b or {}).get("date") or "").strip()
            if not ts:
                continue
            by_ts[ts] = b
        return [by_ts[k] for k in sorted(by_ts.keys())]

    def _day(b: dict) -> str:
        d = str((b or {}).get("date") or "").strip()[:10]
        if len(d) == 10:
            return d
        ts = str((b or {}).get("datetime") or "").strip()
        return ts[:10] if len(ts) >= 10 else ""

    def _ts(b: dict) -> str:
        return str((b or {}).get("datetime") or (b or {}).get("date") or "").strip()

    by_day_old: Dict[str, Dict[str, dict]] = {}
    for b in existing or []:
        if not isinstance(b, dict):
            continue
        d, ts = _day(b), _ts(b)
        if not d or not ts:
            continue
        by_day_old.setdefault(d, {})[ts] = b

    touch_days: set = set()
    by_day_new: Dict[str, Dict[str, dict]] = {}
    for b in incoming or []:
        if not isinstance(b, dict):
            continue
        d, ts = _day(b), _ts(b)
        if not d or not ts:
            continue
        touch_days.add(d)
        by_day_new.setdefault(d, {})[ts] = b

    out_by_ts: Dict[str, dict] = {}
    for d, m in by_day_old.items():
        if d in touch_days:
            continue
        out_by_ts.update(m)
    for d in touch_days:
        out_by_ts.update(by_day_new.get(d) or {})
    return [out_by_ts[k] for k in sorted(out_by_ts.keys())]


def align_minute_volume_units(
    bars: List[dict],
    peer: List[dict],
    *,
    ratio_lo: float = 80.0,
    ratio_hi: float = 120.0,
) -> List[dict]:
    """若重叠时刻 peer/bars 成交量中位数 ≈100，视 bars 为「手」×100→股。

    无重叠或比值不在窗口内则原样返回（新列表浅拷贝 bar dict）。
    """
    if not bars or not peer:
        return [dict(b) for b in (bars or []) if isinstance(b, dict)]
    peer_by: Dict[str, dict] = {}
    for b in peer:
        if not isinstance(b, dict):
            continue
        ts = str(b.get("datetime") or b.get("date") or "").strip()
        if ts:
            peer_by[ts] = b
    ratios: List[float] = []
    for b in bars:
        if not isinstance(b, dict):
            continue
        ts = str(b.get("datetime") or b.get("date") or "").strip()
        p = peer_by.get(ts)
        if not p:
            continue
        try:
            va = float(b.get("volume") or 0)
            vb = float(p.get("volume") or 0)
        except (TypeError, ValueError):
            continue
        if va > 0 and vb > 0:
            ratios.append(vb / va)
    if len(ratios) < 5:
        return [dict(b) for b in bars if isinstance(b, dict)]
    ratios.sort()
    med = ratios[len(ratios) // 2]
    if not (ratio_lo <= med <= ratio_hi):
        return [dict(b) for b in bars if isinstance(b, dict)]
    out: List[dict] = []
    for b in bars:
        if not isinstance(b, dict):
            continue
        row = dict(b)
        try:
            v = float(row.get("volume") or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            row["volume"] = float(v) * 100.0
            row["volume_unit_scaled"] = "hand_to_share_x100"
        out.append(row)
    return out


def trim_daily_bars(bars: List[dict], *, max_keep: int = DAILY_BARS_MAX_KEEP) -> List[dict]:
    n = max(1, int(max_keep or DAILY_BARS_MAX_KEEP))
    if not bars or len(bars) <= n:
        return list(bars or [])
    return list(bars[-n:])


def trim_minute_bars(
    bars: List[dict], *, max_keep: int = MINUTE_BARS_MAX_KEEP
) -> List[dict]:
    n = max(1, int(max_keep or MINUTE_BARS_MAX_KEEP))
    if not bars or len(bars) <= n:
        return list(bars or [])
    return list(bars[-n:])


def _is_pseudo_bar_date(d: Any) -> bool:
    s = str(d or "").strip().lower()
    if not s:
        return True
    if s in ("d-1", "d0", "d+1", "today", "yesterday"):
        return True
    return _parse_date(s) is None


def assess_quality(
    bars: List[dict],
    *,
    data_source: str,
    fetched_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """质量标记：good / thin / empty；附 bar 区间与备注。"""
    notes: List[str] = []
    n = len(bars or [])
    src = str(data_source or "")
    if n == 0:
        return {
            "level": "empty",
            "bar_count": 0,
            "first_date": None,
            "last_date": None,
            "notes": ["无日线"],
            "pseudo_dates": False,
        }

    first = bars[0].get("date")
    last = bars[-1].get("date")
    sample = list(bars[:3]) + list(bars[-3:])
    pseudo = any(_is_pseudo_bar_date(b.get("date")) for b in sample)
    if pseudo or "quote_fallback" in src:
        notes.append("伪日期或 quote_fallback，不可作生产/回测日线")
        return {
            "level": "empty",
            "bar_count": n,
            "first_date": first,
            "last_date": last,
            "notes": notes,
            "pseudo_dates": True,
        }

    last_dt = _parse_date(str(last or ""))
    if last_dt:
        age_days = (datetime.now() - last_dt).days
        if age_days > QUALITY_STALE_BAR_DAYS:
            notes.append(f"末根 K 线偏旧({age_days}天前)")
    else:
        notes.append("末根日期无法解析")

    if src in ("empty",) or "fallback" in src:
        notes.append("非完整日线数据源")
        level = "thin"
    elif n < THIN_MIN_BARS:
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
        "pseudo_dates": False,
    }


def load_daily_cache(
    market: str,
    code: str,
    *,
    min_bars: int = 1,
    max_age_hours: float = DAILY_CACHE_HOURS,
    store_dir: Optional[str] = None,
    ignore_age: bool = False,
) -> Optional[Tuple[List[dict], Dict[str, Any]]]:
    base = store_dir or get_store_dir()
    if bars_backend() == "sqlite":
        from core import store_bars_sqlite as sq

        return sq.load_daily(
            market,
            code,
            min_bars=min_bars,
            max_age_hours=max_age_hours,
            store_dir=base,
            ignore_age=ignore_age,
            assess_quality=assess_quality,
        )
    return _load_daily_cache_json(
        market,
        code,
        min_bars=min_bars,
        max_age_hours=max_age_hours,
        store_dir=base,
        ignore_age=ignore_age,
    )


def _load_daily_cache_json(
    market: str,
    code: str,
    *,
    min_bars: int = 1,
    max_age_hours: float = DAILY_CACHE_HOURS,
    store_dir: Optional[str] = None,
    ignore_age: bool = False,
) -> Optional[Tuple[List[dict], Dict[str, Any]]]:
    path = daily_cache_path(market, code, store_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _note_io_error(f"load_daily:{path}", e)
        return None

    fetched_s = payload.get("fetched_at") or ""
    if not isinstance(fetched_s, str):
        fetched_s = str(fetched_s) if fetched_s else ""
    try:
        fetched_at = datetime.fromisoformat(fetched_s)
    except (ValueError, TypeError):
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
        "bars_backend": "json",
    }
    return bars, meta


def _write_daily_payload(
    path: str,
    *,
    market: str,
    code: str,
    bars: List[dict],
    data_source: str,
    stock_code: Optional[str],
    adjust_policy: Optional[str],
) -> str:
    fetched_at = datetime.now()
    bars = trim_daily_bars(bars)
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
    try:
        atomic_write_json(path, payload)
    except OSError as e:
        _note_io_error(f"save_daily:{path}", e)
        raise
    return path


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
    base = store_dir or get_store_dir()
    if bars_backend() == "sqlite":
        from core import store_bars_sqlite as sq

        return sq.save_daily(
            market,
            code,
            list(bars or []),
            data_source=data_source,
            stock_code=stock_code,
            store_dir=base,
            adjust_policy=adjust_policy,
            assess_quality=assess_quality,
            trim_daily_bars=trim_daily_bars,
        )
    path = daily_cache_path(market, code, base)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with path_lock(path):
        return _write_daily_payload(
            path,
            market=market,
            code=code,
            bars=list(bars or []),
            data_source=data_source,
            stock_code=stock_code,
            adjust_policy=adjust_policy,
        )


def merge_save_daily_cache(
    market: str,
    code: str,
    incoming: List[dict],
    *,
    data_source: str,
    stock_code: Optional[str] = None,
    store_dir: Optional[str] = None,
    adjust_policy: Optional[str] = "qfq",
) -> Tuple[str, List[dict]]:
    """持锁读盘 → 按 date 合并 → 裁剪 → 写回（DS-R0 防丢更新）。"""
    base = store_dir or get_store_dir()
    if bars_backend() == "sqlite":
        from core import store_bars_sqlite as sq

        return sq.merge_save_daily(
            market,
            code,
            list(incoming or []),
            data_source=data_source,
            stock_code=stock_code,
            store_dir=base,
            adjust_policy=adjust_policy,
            assess_quality=assess_quality,
            trim_daily_bars=trim_daily_bars,
            merge_bars_by_date=merge_bars_by_date,
        )
    path = daily_cache_path(market, code, base)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with path_lock(path):
        existing: List[dict] = []
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    payload = json.load(f)
                existing = list(payload.get("bars") or [])
            except (OSError, json.JSONDecodeError) as e:
                _note_io_error(f"merge_load_daily:{path}", e)
                existing = []
        merged = merge_bars_by_date(existing, list(incoming or []))
        _write_daily_payload(
            path,
            market=market,
            code=code,
            bars=merged,
            data_source=data_source,
            stock_code=stock_code,
            adjust_policy=adjust_policy,
        )
        return path, merged


def load_snapshot_cache(
    kind: str,
    code: str,
    *,
    max_age_hours: float = DAILY_CACHE_HOURS,
    store_dir: Optional[str] = None,
) -> Optional[Tuple[Any, Dict[str, Any]]]:
    path = snapshot_cache_path(kind, code, store_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _note_io_error(f"load_snapshot:{path}", e)
        return None
    fetched_s = payload.get("fetched_at") or ""
    if not isinstance(fetched_s, str):
        fetched_s = str(fetched_s) if fetched_s else ""
    try:
        fetched_at = datetime.fromisoformat(fetched_s)
    except (ValueError, TypeError):
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

    with path_lock(path):
        existing_history: List[dict] = []
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    old = json.load(f)
                existing_history = list(old.get("history") or [])
            except (OSError, json.JSONDecodeError) as e:
                _note_io_error(f"load_snapshot_hist:{path}", e)
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

        if str(kind).lower() == "fundamentals":
            try:
                from core.fundamentals_pit import (
                    _metrics_as_of_hint,
                    merge_history_point,
                )
                from core.numbers import date_key
                from core.signal.fundamentals_bridge import normalize_fundamentals_metrics

                metrics = normalize_fundamentals_metrics(data) or {}
                point_as_of = (
                    date_key(as_of)
                    or _metrics_as_of_hint(data)
                    or date_key(fetched_s)
                )
                if metrics and point_as_of:
                    hist = merge_history_point(
                        existing_history,
                        as_of=point_as_of,
                        metrics=metrics,
                        fetched_at=fetched_s,
                        data_source=data_source,
                        ann_date=metrics.get("ann_date"),
                        available_as_of=metrics.get("valuation_as_of")
                        or metrics.get("available_as_of"),
                    )
                    if len(hist) > FUNDAMENTALS_HISTORY_MAX_POINTS:
                        hist = hist[-FUNDAMENTALS_HISTORY_MAX_POINTS:]
                    payload["history"] = hist
                    payload["latest_as_of"] = point_as_of
                elif existing_history:
                    payload["history"] = existing_history[
                        -FUNDAMENTALS_HISTORY_MAX_POINTS:
                    ]
            except Exception as e:
                logger.warning("fundamentals history merge failed: %s", e)
                if existing_history:
                    payload["history"] = existing_history[
                        -FUNDAMENTALS_HISTORY_MAX_POINTS:
                    ]

        try:
            atomic_write_json(path, payload)
        except OSError as e:
            _note_io_error(f"save_snapshot:{path}", e)
            raise
    return path


def peek_daily_cache_meta(
    market: str,
    code: str,
    store_dir: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """读日线缓存元数据（不校验 TTL）。"""
    base = store_dir or get_store_dir()
    if bars_backend() == "sqlite":
        from core import store_bars_sqlite as sq

        return sq.peek_daily_meta(market, code, base)
    path = daily_cache_path(market, code, base)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _note_io_error(f"peek_daily:{path}", e)
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
        "bars_backend": "json",
    }


def list_cached_symbols(
    market: Optional[str] = None,
    store_dir: Optional[str] = None,
) -> List[Dict[str, Any]]:
    base = store_dir or get_store_dir()
    if bars_backend() == "sqlite":
        from core import store_bars_sqlite as sq

        return sq.list_daily_symbols(market, base)
    daily_base = os.path.join(base, DAILY_SUBDIR)
    if not os.path.isdir(daily_base):
        return []
    out: List[Dict[str, Any]] = []
    markets = [market.upper()] if market else os.listdir(daily_base)
    for mkt in markets:
        mdir = os.path.join(daily_base, mkt)
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
                        "bars_backend": "json",
                    }
                )
            except (OSError, json.JSONDecodeError):
                continue
    return out


def clear_daily_cache(
    market: Optional[str] = None,
    store_dir: Optional[str] = None,
) -> int:
    """清除日线缓存；返回删除条目数（sqlite=meta 行；json=文件数）。"""
    base = store_dir or get_store_dir()
    if bars_backend() == "sqlite":
        from core import store_bars_sqlite as sq

        return sq.clear_daily(market, base)
    daily_base = os.path.join(base, DAILY_SUBDIR)
    if not os.path.isdir(daily_base):
        return 0
    removed = 0
    markets = [market.upper()] if market else os.listdir(daily_base)
    for mkt in markets:
        mdir = os.path.join(daily_base, mkt)
        if not os.path.isdir(mdir):
            continue
        for name in os.listdir(mdir):
            if name.endswith(".json"):
                try:
                    os.remove(os.path.join(mdir, name))
                    removed += 1
                except OSError as e:
                    _note_io_error(f"clear_daily:{mdir}/{name}", e)
    return removed


def clear_minute_cache(
    market: Optional[str] = None,
    *,
    period: Optional[str] = None,
    store_dir: Optional[str] = None,
) -> int:
    base = store_dir or get_store_dir()
    if bars_backend() == "sqlite":
        from core import store_bars_sqlite as sq

        return sq.clear_minute(market, period=period, store_dir=base)
    minute_base = os.path.join(base, MINUTE_SUBDIR)
    if not os.path.isdir(minute_base):
        return 0
    removed = 0
    periods = [str(period)] if period else os.listdir(minute_base)
    for per in periods:
        pdir = os.path.join(minute_base, per)
        if not os.path.isdir(pdir):
            continue
        markets = [market.upper()] if market else os.listdir(pdir)
        for mkt in markets:
            mdir = os.path.join(pdir, mkt)
            if not os.path.isdir(mdir):
                continue
            for name in os.listdir(mdir):
                if name.endswith(".json"):
                    try:
                        os.remove(os.path.join(mdir, name))
                        removed += 1
                    except OSError as e:
                        _note_io_error(f"clear_minute:{mdir}/{name}", e)
    return removed


def load_minute_cache(
    market: str,
    code: str,
    period: str = "5",
    *,
    min_bars: int = 1,
    max_age_hours: float = DAILY_CACHE_HOURS,
    store_dir: Optional[str] = None,
    ignore_age: bool = False,
) -> Optional[Tuple[List[dict], Dict[str, Any]]]:
    base = store_dir or get_store_dir()
    if bars_backend() == "sqlite":
        from core import store_bars_sqlite as sq

        return sq.load_minute(
            market,
            code,
            period,
            min_bars=min_bars,
            max_age_hours=max_age_hours,
            store_dir=base,
            ignore_age=ignore_age,
        )
    path = minute_cache_path(market, code, period, base)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _note_io_error(f"load_minute:{path}", e)
        return None

    fetched_s = payload.get("fetched_at") or ""
    if not isinstance(fetched_s, str):
        fetched_s = str(fetched_s) if fetched_s else ""
    try:
        fetched_at = datetime.fromisoformat(fetched_s)
    except (ValueError, TypeError):
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
        "bars_backend": "json",
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
    base = store_dir or get_store_dir()
    if bars_backend() == "sqlite":
        from core import store_bars_sqlite as sq

        return sq.save_minute(
            market,
            code,
            list(bars or []),
            period=period,
            data_source=data_source,
            stock_code=stock_code,
            store_dir=base,
            adjust_policy=adjust_policy,
            trim_minute_bars=trim_minute_bars,
        )
    path = minute_cache_path(market, code, period, base)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bars = trim_minute_bars(list(bars or []))
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
    with path_lock(path):
        try:
            atomic_write_json(path, payload)
        except OSError as e:
            _note_io_error(f"save_minute:{path}", e)
            raise
    return path
