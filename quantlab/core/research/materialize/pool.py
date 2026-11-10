"""进程池收结果。job 的形状留在调用方。"""

from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)

# 单票失败：前几条带栈；磁盘类整池故障只打一次 warning，其余降 debug 防刷屏。
_STOCK_FAIL_FULL = 3


def _is_diskish(exc: BaseException) -> bool:
    if isinstance(exc, OSError):
        return True
    cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    return isinstance(cause, OSError)


def consume_process_batch(
    *,
    start: int,
    end: int,
    n_workers: int,
    results: List[Any],
    submit: Callable[[int], Any],
    run_one: Callable[[int], Any],
    label: Optional[Callable[[int], str]] = None,
    on_done: Optional[Callable[[int], None]] = None,
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

    if start < 0 or end < start or end > len(results):
        raise IndexError(f"process batch [{start}, {end}) outside results {len(results)}")
    inflight: dict = {}
    pending = set(range(start, end))
    next_i = start
    window = max(1, int(n_workers))
    stock_fails = 0
    disk_warned = False

    def _name(i: int) -> str:
        if label is None:
            return str(i)
        try:
            return str(label(i))
        except Exception:  # noqa: BLE001
            return str(i)

    def _log_stock_fail(i: int, exc: BaseException) -> None:
        nonlocal stock_fails, disk_warned
        stock_fails += 1
        who = _name(i)
        if _is_diskish(exc):
            if not disk_warned:
                disk_warned = True
                logger.warning(
                    "research pool disk/IO failure at %s (%s); "
                    "further per-stock traces at debug",
                    who,
                    type(exc).__name__,
                    exc_info=True,
                )
            else:
                logger.debug(
                    "research pool stock %s failed (disk/IO); retrying serially",
                    who,
                    exc_info=True,
                )
            return
        if stock_fails <= _STOCK_FAIL_FULL:
            logger.exception(
                "research pool stock %s failed in worker; retrying serially",
                who,
            )
        else:
            logger.debug(
                "research pool stock %s failed in worker; retrying serially "
                "(suppressed after %s)",
                who,
                _STOCK_FAIL_FULL,
                exc_info=True,
            )

    def _store(i: int, value: Any) -> None:
        results[i] = value
        pending.discard(i)
        if on_done is None:
            return
        try:
            on_done(i)
        except Exception:  # noqa: BLE001
            logger.debug("process batch on_done failed at %s", i, exc_info=True)

    def _submit(i: int) -> None:
        inflight[submit(i)] = i

    def _cancel_inflight() -> None:
        for left in list(inflight):
            left.cancel()
            inflight.pop(left, None)

    def _serial_fill() -> None:
        _cancel_inflight()
        for j in range(start, end):
            if j not in pending:
                continue
            _store(j, run_one(j))

    def _broken(fut: Any) -> bool:
        try:
            return isinstance(fut.exception(), BrokenProcessPool)
        except BaseException:  # noqa: BLE001 — 已取消的 future 不能挡住同批结果
            return False

    try:
        while next_i < end and len(inflight) < window:
            _submit(next_i)
            next_i += 1
        while inflight:
            done, _pending = wait(set(inflight), return_when=FIRST_COMPLETED)
            # 同一批里先收下成功结果，再处理池损坏，避免把已经算完的空结果再跑一遍。
            ordered = sorted(done, key=_broken)
            for fut in ordered:
                i = inflight.pop(fut)
                try:
                    _store(i, fut.result())
                except MaterializeRequired as exc:
                    try:
                        from core.research.day_cube_gc import apply_stale_from_exc

                        apply_stale_from_exc(exc)
                    except Exception:  # noqa: BLE001
                        logger.debug("apply stale from MaterializeRequired failed", exc_info=True)
                    raise
                except BrokenProcessPool:
                    logger.exception(
                        "research pool broke at %s; rest of batch is serial", _name(i)
                    )
                    _serial_fill()
                    return True
                except Exception as exc:
                    _log_stock_fail(i, exc)
                    _store(i, run_one(i))
            while next_i < end and len(inflight) < window:
                _submit(next_i)
                next_i += 1
    except Exception:
        _cancel_inflight()
        raise
    return False
