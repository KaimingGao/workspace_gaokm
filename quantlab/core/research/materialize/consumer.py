"""拟合 / 回测 / 做 T / 调仓出分：只读 L1 与 L2。

默认关，单测与脚本仍可从日线现算。产品入口用 ``materialized_consumer`` 打开。
打开后缺湖或立方则抛 ``MaterializeRequired``，不再写 L1/L2。
"""

from __future__ import annotations

import contextvars
import functools
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

# 请求级标记。不用进程级环境变量：并发的回测/做 T 会把只读打到物化线程上。
# 3.10 的线程池、以及 spawn 出来的进程池，都不复制 contextvar，要在 initializer 再绑一次。
# L2 作废批次另见 day_cube_gc._ACTIVE_BATCH（同样因线程池不复制 contextvar）。
_writer: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "quantlab_materialize_writer",
    default=False,
)
_consumer: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "quantlab_materialize_consumer",
    default=False,
)


def consumer_read_only() -> bool:
    """产品消费路径：禁止从 L0 现算特征或冷建 DayCube。"""
    if _writer.get():
        return False
    return bool(_consumer.get())


def writer_active() -> bool:
    """当前上下文是物化写入（含从该线程提交出去的线程池）。"""
    return bool(_writer.get())


def context_initargs() -> Tuple[bool, bool]:
    """父进程采样，传给池 initializer。写入优先于只读。"""
    writing = writer_active()
    return (writing, (not writing) and consumer_read_only())


@contextmanager
def materialize_writer() -> Iterator[None]:
    """物化任务：本线程及它提交的线程池可以重算 L1，不受并发消费开关影响。"""
    token = _writer.set(True)
    try:
        yield
    finally:
        _writer.reset(token)


def bind_materialize_writer(writer: bool = False, consumer: bool = False) -> None:
    """池 initializer。两次都写上，避免工作线程复用时留下上一次的写入标记。"""
    if writer:
        _writer.set(True)
        _consumer.set(False)
        return
    _writer.set(False)
    _consumer.set(bool(consumer))


def research_pool_init(writer: bool = False, consumer: bool = False) -> None:
    """进程池启动：限制 BLAS，并绑上父进程采样的写入/只读。

    写入工人打开 L2 stale defer：作废日期由 job ``drain_deferred_stale`` 带回父进程，
    避免子进程看不到 ``l2_invalidation_batch`` 而每票扫盘。
    """
    from core.research.oo_blas_limit import limit_blas_threads

    limit_blas_threads()
    bind_materialize_writer(writer, consumer)
    if writer:
        try:
            from core.research.day_cube_gc import enable_stale_defer

            enable_stale_defer()
        except Exception:  # noqa: BLE001
            pass


@contextmanager
def materialized_consumer() -> Iterator[None]:
    token = _consumer.set(True)
    try:
        yield
    finally:
        _consumer.reset(token)


class MaterializeRequired(Exception):
    """L1/L2 不够覆盖本次消费。``payload`` 可直接当 API 结果。"""

    def __init__(self, payload: Dict[str, Any]):
        self.payload = dict(payload or {})
        self.payload.setdefault("error_code", "materialize_required")
        self.payload.setdefault("success", False)
        self.payload.setdefault("ok", False)
        super().__init__(str(self.payload.get("error") or "请先物化 L1/L2"))

    def __reduce__(self):
        # 进程池按 BaseException.args 还原。args 只剩错误文案，
        # 再进 __init__ 会被当成 payload 字典拆开。
        return (type(self), (dict(self.payload),))

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


def hint_for_family(family: str) -> str:
    fam = str(family or "")
    if fam in ("oo", "tc", "co", "oo_rank", "tc_rank"):
        return "日线物化"
    return "分钟线物化"


def raise_if_consumer_rebuild(family: str, code: str = "") -> None:
    """L1 指纹未命中时，消费方不得再扫日线重算。"""
    if not consumer_read_only():
        return
    hint = hint_for_family(family)
    who = f"{code} 的 " if code else ""
    raise MaterializeRequired(
        {
            "error_code": "materialize_required",
            "error": f"请先{hint}：{who}L1 {family} 缺失或与当前日线不一致",
            "layer": "l1",
            "families": [str(family or "")],
            "hint": hint,
            "stock_code": str(code or ""),
        }
    )


def consume_materialized(fn: Callable) -> Callable:
    """产品拟合入口：打开只读，并把缺湖异常收成返回体。"""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with materialized_consumer():
            try:
                return fn(*args, **kwargs)
            except MaterializeRequired as exc:
                return exc.to_dict()

    return wrapper
