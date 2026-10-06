"""市场级快照读写：macro / market_sentiment / announcement（非 PIT 盘前上下文）。"""


import logging
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from core.paths import STORE_DIR
from core.store import load_snapshot_cache, save_snapshot_cache

logger = logging.getLogger(__name__)

KIND_MACRO = "macro"
KIND_MARKET_SENTIMENT = "market_sentiment"
KIND_ANNOUNCEMENT = "announcement"
SNAPSHOT_CODE = "latest"

DEFAULT_MAX_AGE_HOURS = 36.0


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def save_market_snapshot(
    kind: str,
    payload: Dict[str, Any],
    *,
    data_source: str,
    as_of: Optional[str] = None,
) -> str:
    body = dict(payload or {})
    body.setdefault("as_of", as_of or _today())
    body.setdefault("fetched_at", datetime.now().isoformat(timespec="seconds"))
    return save_snapshot_cache(
        kind,
        SNAPSHOT_CODE,
        body,
        data_source=data_source,
        as_of=body.get("as_of"),
    )


def load_market_snapshot(
    kind: str,
    *,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    cached = load_snapshot_cache(kind, SNAPSHOT_CODE, max_age_hours=max_age_hours)
    if not cached:
        return None, {"cache_hit": False, "kind": kind}
    payload, meta = cached
    data = payload if isinstance(payload, dict) else {}
    return data, {
        "cache_hit": True,
        "kind": kind,
        "data_source": (meta or {}).get("data_source"),
        "fetched_at": (meta or {}).get("fetched_at"),
        "as_of": data.get("as_of"),
    }


def load_macro_snapshot(**kw: Any) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    return load_market_snapshot(KIND_MACRO, **kw)


def load_market_sentiment_snapshot(**kw: Any) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    return load_market_snapshot(KIND_MARKET_SENTIMENT, **kw)


def load_announcement_snapshot(**kw: Any) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    return load_market_snapshot(KIND_ANNOUNCEMENT, **kw)


def market_context_dir(kind: str) -> str:
    import os

    safe = "".join(ch for ch in str(kind) if ch.isalnum() or ch in ("_", "-")).lower()
    return os.path.join(STORE_DIR, safe)


__all__ = [
    "KIND_ANNOUNCEMENT",
    "KIND_MACRO",
    "KIND_MARKET_SENTIMENT",
    "SNAPSHOT_CODE",
    "load_announcement_snapshot",
    "load_macro_snapshot",
    "load_market_sentiment_snapshot",
    "load_market_snapshot",
    "market_context_dir",
    "save_market_snapshot",
]
