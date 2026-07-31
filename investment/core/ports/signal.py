"""信号扫描端口。默认经 skills.ports_bind 注入；单测可 set_adapter 覆盖。"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from core.ports.adapters import call


def build_signal_pool(
    payload: Dict[str, Any],
    *,
    on_progress: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    return call("build_signal_pool", payload or {}, on_progress=on_progress)
