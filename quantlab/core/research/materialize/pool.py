"""进程池收结果。job 的形状留在调用方。"""

from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)


def consume_process_batch(
    *,
    start: int,
    end: int,
    n_workers: int,
    results: List[Any],
    submit: Callable[[int], Any],
    run_one: Callable[[int], Any],
    label: Optional[Callable[[int], str]] = None,
) -> bool:
    """提交 ``[start, end)``。

    ``submit(i)`` 在父进程调用，返回 Future，投进去的函数必须能被子进程导入。
    ``run_one(i)`` 只在父进程跑：池损坏后的剩余项，以及单票失败后的一次重试。

    ``MaterializeRequired`` 原样抛出。池损坏时本批未完成的下标改串行，返回 True。
    其他异常串行重跑这一只；串行再失败则让异常离开。
    """
    from concurrent.futures import FIRST_COMPLETED, wait
    from concurrent.futures.process import BrokenProcessPool

    from core.research.materialize.consumer import MaterializeRequired

    inflight: dict = {}
    pending = set(range(start, end))
    next_i = start
    window = max(1, int(n_workers))

    def _name(i: int) -> str:
        if label is None:
            return str(i)
        try:
            return str(label(i))
        except Exception:  # noqa: BLE001
            return str(i)

    def _store(i: int, value: Any) -> None:
        results[i] = value
        pending.discard(i)

    def _submit(i: int) -> None:
        inflight[submit(i)] = i

    def _serial_fill() -> None:
        for left in list(inflight):
            left.cancel()
            inflight.pop(left, None)
        for j in range(start, end):
            if j not in pending:
                continue
            _store(j, run_one(j))

    while next_i < end and len(inflight) < window:
        _submit(next_i)
        next_i += 1
    while inflight:
        done, _pending = wait(set(inflight), return_when=FIRST_COMPLETED)
        # 同一批里先收下成功结果，再处理池损坏，避免把已经算完的空结果再跑一遍。
        ordered = sorted(
            done,
            key=lambda fut: isinstance(fut.exception(), BrokenProcessPool),
        )
        for fut in ordered:
            i = inflight.pop(fut)
            try:
                _store(i, fut.result())
            except MaterializeRequired:
                raise
            except BrokenProcessPool:
                logger.exception(
                    "research pool broke at %s; rest of batch is serial", _name(i)
                )
                _serial_fill()
                return True
            except Exception:
                logger.exception(
                    "research pool stock %s failed in worker; retrying serially",
                    _name(i),
                )
                _store(i, run_one(i))
        while next_i < end and len(inflight) < window:
            _submit(next_i)
            next_i += 1
    return False
