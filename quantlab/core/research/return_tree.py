"""遗留：调仓「影子树头」开关（已下线，恒 ridge）。

ŷ_oo 生产路径是 ``oo_lgb`` LightGBM，与本 ContextVar 无关。
``normalize_rebalance_score_backend`` 恒为 ``ridge``。
Horizon τ30…90 正式路径为启用套包内 ``backend``（研究枢纽 AUC 择优）；
``horizon_prob_backend=tree`` 为遗留 API 开关。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

REBALANCE_SCORE_BACKEND_RIDGE = "ridge"

_REBALANCE_SCORE_BACKEND: ContextVar[str] = ContextVar(
    "rebalance_score_backend", default=REBALANCE_SCORE_BACKEND_RIDGE
)


def normalize_rebalance_score_backend(raw: Any = None) -> str:
    """恒返回 ridge（接受任意输入，忽略 tree）。"""
    _ = raw
    return REBALANCE_SCORE_BACKEND_RIDGE


def current_rebalance_score_backend() -> str:
    return normalize_rebalance_score_backend(_REBALANCE_SCORE_BACKEND.get())


@contextmanager
def rebalance_score_backend_context(backend: Any) -> Iterator[None]:
    token = _REBALANCE_SCORE_BACKEND.set(normalize_rebalance_score_backend(backend))
    try:
        yield
    finally:
        _REBALANCE_SCORE_BACKEND.reset(token)
