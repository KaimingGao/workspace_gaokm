"""QuantService · ② 回溯（兼容入口）。

实现已迁至 ``quant_service_replay.QuantReplayMixin``；
本模块保留 ``QuantPortfolioMixin`` 别名，避免旧 import 断裂。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from quant.services.quant_service_replay import QuantPortfolioMixin, QuantReplayMixin

__all__ = ["QuantPortfolioMixin", "QuantReplayMixin"]
