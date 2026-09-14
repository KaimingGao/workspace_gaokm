"""薄 SignalService 门面（Domain Facade · SS）：上层打分窗口。

委托 core.signal.SignalService。文档称 Domain Facade，与 Application Service 不同层。
历史兼容：本模块函数仍返回 dict（``.as_dict()``）。
类型化 API：``from core.signal.service import SignalService, ScoreResult``。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.signal.gate import (
    SCALE_HEURISTIC,
    SCALE_UNKNOWN,
    SCALE_YHAT,
    allows_production_yhat,
    infer_score_scale,
)
from core.signal.service import (
    get_default_signal_service,
    metrics_snapshot,
    reset_metrics,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SCALE_HEURISTIC",
    "SCALE_UNKNOWN",
    "SCALE_YHAT",
    "allows_production_yhat",
    "book_fields",
    "infer_score_scale",
    "metrics_snapshot",
    "pack_holding_row",
    "rank_cluster_pools",
    "rank_cross_section",
    "reset_metrics",
    "score_one",
]


def score_one(stock_code: str, **kw: Any) -> Dict[str, Any]:
    return get_default_signal_service().score_one(stock_code, **kw).as_dict()


def rank_cross_section(
    codes: Optional[List[str]] = None,
    **kw: Any,
) -> Dict[str, Any]:
    return get_default_signal_service().rank_cross_section(codes, **kw).as_dict()


def rank_cluster_pools(
    codes: Optional[List[str]] = None,
    **kw: Any,
) -> Dict[str, Any]:
    return get_default_signal_service().rank_cluster_pools(codes, **kw).as_dict()


def pack_holding_row(
    item: Optional[dict],
    *,
    cluster_mode: Any = None,
    rank_cfg: Optional[dict] = None,
) -> Dict[str, Any]:
    return get_default_signal_service().pack_holding_row(
        item, cluster_mode=cluster_mode, rank_cfg=rank_cfg
    )


def book_fields(item: Optional[dict]) -> Dict[str, Any]:
    return get_default_signal_service().book_fields(item)
