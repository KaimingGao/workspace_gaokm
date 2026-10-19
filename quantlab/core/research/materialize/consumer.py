"""拟合 / 回测 / 做 T / 调仓出分：只读 L1 与 L2。

默认关，单测与脚本仍可从日线现算。产品入口用 ``materialized_consumer`` 打开。
打开后缺湖或立方则抛 ``MaterializeRequired``，不再写 L1/L2。
"""

from __future__ import annotations

import functools
import os
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, Optional

_ENV = "QUANTLAB_MATERIALIZE_CONSUMER"


def _truthy(raw: Optional[str], *, default: bool = False) -> bool:
    s = str(raw if raw is not None else ("1" if default else "0")).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return default


def consumer_read_only() -> bool:
    """产品消费路径：禁止从 L0 现算特征或冷建 DayCube。"""
    return _truthy(os.environ.get(_ENV), default=False)


@contextmanager
def materialized_consumer() -> Iterator[None]:
    old = os.environ.get(_ENV)
    os.environ[_ENV] = "1"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(_ENV, None)
        else:
            os.environ[_ENV] = old


class MaterializeRequired(Exception):
    """L1/L2 不够覆盖本次消费。``payload`` 可直接当 API 结果。"""

    def __init__(self, payload: Dict[str, Any]):
        self.payload = dict(payload or {})
        self.payload.setdefault("error_code", "materialize_required")
        self.payload.setdefault("success", False)
        self.payload.setdefault("ok", False)
        super().__init__(str(self.payload.get("error") or "请先物化 L1/L2"))

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
