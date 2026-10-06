"""PaperService 纯函数辅助（评分公式等）。

实现已迁 ``core.signal.score_view``；此处再导出保持 services 调用方兼容。
"""

from __future__ import annotations

from core.signal.score_view import (
    _build_score_formula,
    build_score_formula,
)
from core.signal.score_view import (
    active_return_model_payload as _active_return_model_payload,
)

__all__ = [
    "_active_return_model_payload",
    "_build_score_formula",
    "build_score_formula",
]
