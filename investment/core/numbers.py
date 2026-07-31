"""数值解析工具（跨模块共用）。"""

from __future__ import annotations

from typing import Any, Optional


def to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None
