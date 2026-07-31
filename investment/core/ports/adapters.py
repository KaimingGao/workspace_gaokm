"""行情端口适配器注册表：业务只依赖本模块；skills 经 ports_bind 注入。"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

_adapters: Dict[str, Callable[..., Any]] = {}
_bound = False


def set_adapter(name: str, fn: Callable[..., Any]) -> None:
    """注册或覆盖命名适配器（单测可注入假实现）。"""
    key = str(name or "").strip()
    if not key:
        raise ValueError("adapter name required")
    if not callable(fn):
        raise TypeError("adapter must be callable")
    _adapters[key] = fn


def get_adapter(name: str) -> Optional[Callable[..., Any]]:
    return _adapters.get(str(name or "").strip())


def clear_adapters() -> None:
    """测试用：清空注册并允许重新 bind。"""
    global _bound
    _adapters.clear()
    _bound = False


def mark_bound() -> None:
    global _bound
    _bound = True


def ensure_bound() -> None:
    """首次调用时让 skills 侧注册默认适配器（打破 market→skills 硬依赖）。"""
    global _bound
    if _bound and _adapters:
        return
    from skills.ports_bind import bind_market_adapters

    bind_market_adapters()
    _bound = True


def call(name: str, *args: Any, **kwargs: Any) -> Any:
    ensure_bound()
    fn = _adapters.get(name)
    if fn is None:
        raise RuntimeError(f"market adapter not bound: {name}")
    return fn(*args, **kwargs)
