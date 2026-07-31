"""长期偏好 Memory（短对话仍由 Agent messages / session 承担）。"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

from core.paths import MEMORY_PATH


_DEFAULT: Dict[str, Any] = {
    "version": 1,
    "preferences": {
        "risk_style": "balanced",  # conservative | balanced | aggressive
        "horizon_days": 3,
        "focus_markets": ["CN"],
        "notes": "",
    },
    "updated_at": None,
}


def _ensure_parent(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def read_memory(path: Optional[str] = None) -> Dict[str, Any]:
    p = path or MEMORY_PATH
    if not os.path.isfile(p):
        out = json.loads(json.dumps(_DEFAULT))
        out["path"] = p
        out["exists"] = False
        return out
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        data = {}
    prefs = data.get("preferences") if isinstance(data.get("preferences"), dict) else {}
    merged = json.loads(json.dumps(_DEFAULT))
    merged["preferences"].update(prefs)
    merged["updated_at"] = data.get("updated_at")
    merged["path"] = p
    merged["exists"] = True
    return merged


def write_memory(preferences: Dict[str, Any], path: Optional[str] = None) -> Dict[str, Any]:
    p = path or MEMORY_PATH
    current = read_memory(p)
    prefs = dict(current.get("preferences") or {})
    for k, v in (preferences or {}).items():
        prefs[k] = v
    payload = {
        "version": 1,
        "preferences": prefs,
        "updated_at": time.time(),
    }
    _ensure_parent(p)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    out = read_memory(p)
    out["ok"] = True
    return out
