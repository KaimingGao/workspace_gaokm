"""纸面日循环 / 做 T 端口：core workers 只依赖本协议，由 Application 层注入。

绑定：``services.paper_cycle_bind.bind_paper_cycle``（web/deps · adapters.bind 钩子）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Protocol


class PaperCyclePort(Protocol):
    """Worker / schedule 所需的最小纸面用例面。"""

    def t0_auto_status(self) -> Dict[str, Any]: ...

    def fill_pending(self, *, now: Any = None) -> Dict[str, Any]: ...

    def rebalance(
        self,
        *,
        dry_run: bool = False,
        offline_only: bool = False,
    ) -> Dict[str, Any]: ...

    def run_t0_auto(
        self,
        *,
        dry_run: bool = False,
        force: bool = False,
        skip_open_fill_gate: bool = False,
    ) -> Dict[str, Any]: ...

    def run_t0_intraday_tick(
        self,
        *,
        skip_open_fill_gate: bool = False,
        force_session_close: bool = False,
    ) -> Dict[str, Any]: ...


_PaperCycleFactory = Callable[[], PaperCyclePort]
_factory: Optional[_PaperCycleFactory] = None


def set_paper_cycle_factory(fn: _PaperCycleFactory) -> None:
    """注册工厂（单测可注入假实现）。"""
    global _factory
    if not callable(fn):
        raise TypeError("paper cycle factory must be callable")
    _factory = fn


def clear_paper_cycle_factory() -> None:
    """测试用：清空绑定。"""
    global _factory
    _factory = None


def is_paper_cycle_bound() -> bool:
    return _factory is not None


def get_paper_cycle() -> PaperCyclePort:
    """取已绑定的纸面用例口；未绑定时触发出站 bind（含 paper_cycle 钩子）。"""
    if _factory is None:
        from core.ports.registry import ensure_bound

        ensure_bound()
    if _factory is None:
        raise RuntimeError(
            "PaperCyclePort not bound; call services.paper_cycle_bind.bind_paper_cycle()"
        )
    return _factory()
