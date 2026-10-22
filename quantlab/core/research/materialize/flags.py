"""物化 / 消费侧开关。"""

from __future__ import annotations

import os


def _truthy(raw: str, default: bool = False) -> bool:
    s = str(raw if raw is not None else ("1" if default else "0")).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return default


def require_day_cube() -> bool:
    """出分/回测：Cube miss 时禁止全池 bars 冷建（``QUANTLAB_MATERIALIZE_REQUIRE_CUBE``）。"""
    return _truthy(os.environ.get("QUANTLAB_MATERIALIZE_REQUIRE_CUBE", "0"), default=False)
