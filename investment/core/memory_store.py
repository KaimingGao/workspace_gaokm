"""长期偏好 Memory（短对话仍由 Agent messages / session 承担）。

``effective_preferences`` 供研究枢纽 / 回测等读取已钳制默认值；
``horizon_days`` 驱动 IC · OLS · Top-K 请求默认（页内可临时覆盖）。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

from core.paths import MEMORY_PATH
from core.io_atomic import atomic_write_json

# 与 quant schemas（IC/OLS/回测）对齐：1～10 交易日
HORIZON_MIN = 1
HORIZON_MAX = 10
_RISK_STYLES = frozenset({"conservative", "balanced", "aggressive"})

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


def clamp_horizon_days(value: Any, *, default: int = 3) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = int(default)
    return max(HORIZON_MIN, min(HORIZON_MAX, n))


def effective_preferences(path: Optional[str] = None) -> Dict[str, Any]:
    """只读生效偏好（已钳制），供研究/回测默认值。"""
    mem = read_memory(path)
    prefs = dict(mem.get("preferences") or {})
    risk = str(prefs.get("risk_style") or "balanced").strip().lower()
    if risk not in _RISK_STYLES:
        risk = "balanced"
    return {
        "risk_style": risk,
        "horizon_days": clamp_horizon_days(prefs.get("horizon_days"), default=3),
        "focus_markets": list(prefs.get("focus_markets") or ["CN"]),
        "notes": str(prefs.get("notes") or ""),
        "memory_exists": bool(mem.get("exists")),
        "updated_at": mem.get("updated_at"),
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
        if k == "horizon_days":
            prefs[k] = clamp_horizon_days(v, default=int(prefs.get("horizon_days") or 3))
        elif k == "risk_style":
            rs = str(v or "balanced").strip().lower()
            prefs[k] = rs if rs in _RISK_STYLES else "balanced"
        else:
            prefs[k] = v
    payload = {
        "version": 1,
        "preferences": prefs,
        "updated_at": time.time(),
    }
    _ensure_parent(p)
    atomic_write_json(p, payload)
    out = read_memory(p)
    out["ok"] = True
    out["effective"] = effective_preferences(p)
    return out
