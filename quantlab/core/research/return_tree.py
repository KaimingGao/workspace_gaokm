"""调仓 ŷ_oo / ŷ_τc / ŷ_co 打分后端（仅 Ridge）。

影子树头已下线；``normalize_rebalance_score_backend`` 恒为 ``ridge``。
Horizon τ30…90 正式路径为启用套包内 ``backend``（研究枢纽 AUC 择优）；
``horizon_prob_backend=tree`` 为遗留 API 开关。``finish_return_tree_persist`` 供树包落盘剥 booster。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator

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


def finish_return_tree_persist(report: Dict[str, Any], saved: Dict[str, Any]) -> Dict[str, Any]:
    """拟合落盘后剥掉 booster，避免实验记录和「上次」接口带上整棵树。"""
    ok = bool((saved or {}).get("success"))
    report["persisted"] = {
        "success": ok,
        "path": (saved or {}).get("path"),
        "error": (saved or {}).get("error"),
        "skipped": not ok,
    }
    if (saved or {}).get("path"):
        report["model_path"] = saved["path"]
    report.pop("tree_return_model", None)
    report["backtest_hook"] = ok
    report["live_hook"] = False
    return report
