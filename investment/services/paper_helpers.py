"""PaperService 纯函数辅助（评分公式等）。

实现已迁 ``core.signal.score_view``；此处再导出保持 services 调用方兼容。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from core.signal.score_view import (  # noqa: F401
    active_return_model_payload as _active_return_model_payload,
    build_score_formula as _build_score_formula,
)
