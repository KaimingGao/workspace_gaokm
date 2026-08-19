"""概念板块成分股日更缓存（减少 ingest 重复拉 AkShare）。"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.io_atomic import atomic_write_json
from core.paths import STORE_DIR

logger = logging.getLogger(__name__)

_CACHE_REL = os.path.join("concept_graph", "daily.json")
_DEFAULT_MAX_AGE_HOURS = 24.0


def _cache_path() -> str:
    return os.path.join(STORE_DIR, _CACHE_REL)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def load_concept_graph_cache(
    *,
    max_age_hours: float = _DEFAULT_MAX_AGE_HOURS,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    path = _cache_path()
    meta: Dict[str, Any] = {"cache_hit": False, "path": path}
    if not os.path.isfile(path):
        return None, meta
    try:
        import json

        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError, TypeError) as e:
        logger.debug("concept graph cache read failed: %s", e)
        return None, {**meta, "error": str(e)}

    if not isinstance(payload, dict):
        return None, meta

    fetched = str(payload.get("fetched_at") or "")
    age_h = None
    if fetched:
        try:
            ts = datetime.fromisoformat(fetched[:19])
            age_h = max(0.0, (datetime.now() - ts).total_seconds() / 3600.0)
        except ValueError:
            age_h = None
    stale = age_h is None or age_h > float(max_age_hours)
    meta.update(
        {
            "cache_hit": True,
            "as_of": payload.get("as_of"),
            "fetched_at": fetched,
            "age_hours": round(age_h, 2) if age_h is not None else None,
            "stale": stale,
            "concept_count": len(payload.get("concepts") or {}),
            "code_count": len(payload.get("code_index") or {}),
        }
    )
    if stale and payload.get("as_of") != _today():
        return None, meta
    return payload, meta


def save_concept_graph_cache(
    *,
    concepts: Dict[str, List[str]],
    code_index: Dict[str, List[str]],
    as_of: Optional[str] = None,
) -> str:
    path = _cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = {
        "as_of": as_of or _today(),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "concepts": {k: sorted(v) for k, v in (concepts or {}).items()},
        "code_index": {k: sorted(v) for k, v in (code_index or {}).items()},
    }
    atomic_write_json(path, body)
    return path


def merge_concept_into_index(
    index: Dict[str, List[str]],
    concept: str,
    codes: List[str],
) -> Dict[str, List[str]]:
    """将单个概念成员合并进 code_index。"""
    out: Dict[str, set] = {k: set(v) for k, v in index.items()}
    c = str(concept or "").strip()
    if not c:
        return {k: sorted(v) for k, v in out.items()}
    for raw in codes or []:
        digits = "".join(ch for ch in str(raw) if ch.isdigit())
        code = digits[-6:] if len(digits) >= 6 else ""
        if code:
            out.setdefault(code, set()).add(c)
    return {k: sorted(v) for k, v in out.items()}
