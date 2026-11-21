"""两套物化截面：沪深 300 成分，以及沪深 300 打底扩到 1600 的全池。

L1 按票存，两套共用。L2 的 μ/σ 按名单哈希分开。模型中心和回测中心用
``QUANTLAB_FEATURE_SET`` 选择读哪一套；不设时仍读当前观察池。
历史回测的调仓在本线程用 ``feature_set_scope`` 钉所选截面（默认沪深300），不改进程环境。
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, List, Optional, Sequence, Tuple

from core.paths import DATA_DIR

logger = logging.getLogger(__name__)

FEATURE_HS300 = "hs300"
FEATURE_POOL = "pool"
FEATURE_POOL1600 = "pool1600"
FEATURE_SET_NAMES = (FEATURE_HS300, FEATURE_POOL)
CSI300_INDEX = "000300"
_FEATURE_SET: contextvars.ContextVar[str] = contextvars.ContextVar(
    "quantlab_feature_set",
    default="",
)
POOL1600_SIZE = 1600
_CACHE_NAME = "csi300_constituents.json"
_CACHE_MAX_AGE_S = 7 * 24 * 3600
_CODE_COLUMNS = ("成分券代码", "品种代码", "股票代码", "code", "代码")


def cache_path() -> str:
    return os.path.join(DATA_DIR, _CACHE_NAME)


def normalize_feature_set(raw: object) -> str:
    token = str(raw or "").strip().lower()
    if token in ("hs300", "csi300", "沪深300", "000300"):
        return FEATURE_HS300
    if token in ("pool", "pool1600", "1600", "full", "大池"):
        return FEATURE_POOL
    return ""


def _digits(raw: object) -> str:
    text = str(raw or "").strip().split(".")[0]
    code = "".join(ch for ch in text if ch.isdigit())
    if len(code) >= 6:
        return code[-6:]
    return ""


def _unique(codes: Sequence[object]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in codes or []:
        code = _digits(raw)
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _read_cache() -> List[str]:
    path = cache_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        logger.debug("csi300 cache read failed", exc_info=True)
        return []
    if not isinstance(payload, dict):
        return []
    return _unique(payload.get("codes") or [])


def _cache_fresh() -> bool:
    path = cache_path()
    try:
        age = datetime.now(timezone.utc).timestamp() - os.path.getmtime(path)
    except OSError:
        return False
    return age >= 0 and age <= _CACHE_MAX_AGE_S and len(_read_cache()) >= 50


def _codes_from_frame(frame: object) -> List[str]:
    if frame is None:
        return []
    columns = getattr(frame, "columns", None)
    if columns is None:
        return []
    names = [str(c) for c in list(columns)]
    col = next((name for name in _CODE_COLUMNS if name in names), "")
    if not col:
        return []
    try:
        series = frame[col]
        values = series.tolist() if hasattr(series, "tolist") else list(series)
    except Exception:  # noqa: BLE001
        logger.debug("csi300 frame column failed", exc_info=True)
        return []
    return _unique(values)


def _fetch_constituents() -> List[str]:
    try:
        from core.data.ak_lock import import_akshare

        ak = import_akshare()
        fn = getattr(ak, "index_stock_cons_csindex", None)
        frame = fn(symbol=CSI300_INDEX) if fn is not None else None
        codes = _codes_from_frame(frame)
        if len(codes) < 50:
            weight = getattr(ak, "index_stock_cons_weight_csindex", None)
            if weight is not None:
                codes = _codes_from_frame(weight(symbol=CSI300_INDEX))
    except Exception:  # noqa: BLE001
        logger.warning("csi300 constituent fetch failed", exc_info=True)
        return []
    if len(codes) < 50:
        return []
    payload = {
        "index": CSI300_INDEX,
        "codes": codes,
        "count": len(codes),
        "fetched_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    try:
        from core.io_atomic import atomic_write_json

        atomic_write_json(cache_path(), payload)
    except Exception:  # noqa: BLE001
        logger.warning("csi300 cache write failed", exc_info=True)
    return codes


def load_csi300_codes(*, fetch: bool = False) -> List[str]:
    """沪深 300 成分。有新鲜缓存就不再打网络；``fetch`` 才在缺失或过期时拉取。"""
    if _cache_fresh():
        return _read_cache()
    cached = _read_cache()
    if not fetch:
        return cached
    fresh = _fetch_constituents()
    return fresh or cached


def canonical_pool1600(*, fetch: bool = False) -> List[str]:
    """沪深 300 在前，其余按研究宇宙现成顺序补到 1600。"""
    base = load_csi300_codes(fetch=fetch)
    if len(base) < 50:
        return []
    out = list(base)
    seen = set(out)
    try:
        from core.research_universe import load_research_universe

        uni = load_research_universe() or {}
        extra = uni.get("codes") or []
    except Exception:  # noqa: BLE001
        logger.debug("feature set research universe failed", exc_info=True)
        extra = []
    for raw in extra:
        if len(out) >= POOL1600_SIZE:
            break
        code = _digits(raw)
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out[:POOL1600_SIZE]


def watching_codes() -> List[str]:
    """当前观察池。滑条把左端固定成沪深 300，再往右加，只数可调。"""
    try:
        from core.watching.store import read_watching

        raw = (read_watching() or {}).get("watchlist") or []
    except Exception:  # noqa: BLE001
        logger.debug("feature set watching read failed", exc_info=True)
        return []
    return _unique(raw)


def codes_for_feature_set(name: object, *, fetch: bool = False) -> List[str]:
    token = normalize_feature_set(name)
    if token == FEATURE_HS300:
        return load_csi300_codes(fetch=fetch)
    if token == FEATURE_POOL:
        return watching_codes()
    return []


def watching_prefix(size: int, *, fetch: bool = False) -> List[str]:
    """滑条名单：左端整份沪深 300，再按全池顺序放到 ``size``。成分未就绪则空。"""
    hs = load_csi300_codes(fetch=fetch)
    if len(hs) < 50:
        return []
    try:
        n = int(size)
    except (TypeError, ValueError):
        n = len(hs)
    if n <= max(len(hs), 300):
        return list(hs)
    full = canonical_pool1600(fetch=False)
    if len(full) < len(hs):
        full = list(hs)
    return full[: min(n, POOL1600_SIZE)]


def materialize_universes(*, fetch: bool = False) -> List[Tuple[str, List[str]]]:
    """物化要写的 L2：固定的沪深 300，以及当前可调的大观察池。

    两套名单相同时只返回一份。成分未就绪则空，调用方继续只写当前观察池。
    """
    hs = load_csi300_codes(fetch=fetch)
    pool = watching_codes()
    out: List[Tuple[str, List[str]]] = []
    if len(hs) >= 50:
        out.append((FEATURE_HS300, hs))
    if len(pool) >= 2 and set(pool) != set(hs):
        out.append((FEATURE_POOL, pool))
    return out


def active_feature_set() -> str:
    """本线程钉住的截面优先，否则看进程环境变量。都没有则空。"""
    pinned = normalize_feature_set(_FEATURE_SET.get())
    if pinned:
        return pinned
    return normalize_feature_set(os.environ.get("QUANTLAB_FEATURE_SET"))


@contextmanager
def feature_set_scope(name: object) -> Iterator[str]:
    """只钉当前上下文的物化截面。其它线程仍走原来的观察池或环境变量。"""
    token = normalize_feature_set(name)
    reset = _FEATURE_SET.set(token)
    try:
        yield token
    finally:
        _FEATURE_SET.reset(reset)


def active_feature_codes() -> Optional[List[str]]:
    """选定的截面名单。未设置或名单不够则 None，调用方用观察池。"""
    token = active_feature_set()
    if not token:
        return None
    codes = codes_for_feature_set(token, fetch=False)
    if len(codes) < 2:
        return None
    return codes
