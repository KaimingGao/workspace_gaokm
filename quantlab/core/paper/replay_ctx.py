"""纸面回放上下文：覆盖会话日与批量行情，避免打网 / 误用真实「今天」。

供 ``core.backtest.paper_replay`` 与单测注入；live 路径不进入本上下文。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Dict, Iterator, List, Optional

BatchQueryFn = Callable[[List[str]], Dict[str, dict]]

_REPLAY_AS_OF: ContextVar[Optional[str]] = ContextVar("paper_replay_as_of", default=None)
_BATCH_QUERY: ContextVar[Optional[BatchQueryFn]] = ContextVar(
    "paper_replay_batch_query", default=None
)


def replay_as_of() -> Optional[str]:
    """回放会话日 ``YYYY-MM-DD``；非回放为 None。"""
    return _REPLAY_AS_OF.get()


def replay_batch_query() -> Optional[BatchQueryFn]:
    return _BATCH_QUERY.get()


@contextmanager
def paper_replay_context(
    *,
    as_of: str,
    batch_query: BatchQueryFn,
) -> Iterator[None]:
    """在回放日固定日历与行情源。"""
    day = str(as_of or "").strip()[:10]
    if len(day) != 10:
        raise ValueError(f"paper_replay_context: invalid as_of={as_of!r}")
    t_asof = _REPLAY_AS_OF.set(day)
    t_bq = _BATCH_QUERY.set(batch_query)
    try:
        yield
    finally:
        _BATCH_QUERY.reset(t_bq)
        _REPLAY_AS_OF.reset(t_asof)
