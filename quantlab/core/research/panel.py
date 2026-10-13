"""兼容入口：ŷ_oo 面板已迁至 ``oo_panel``（对齐 ``co_panel`` / ``tau_panel``）。

请优先 ``from core.research.oo_panel import collect_oo_panel``。
"""

from __future__ import annotations

from core.research.oo_panel import (
    collect_oo_panel,
    collect_subscore_forward_panel,
    feature_fill_rates,
)

__all__ = [
    "collect_oo_panel",
    "collect_subscore_forward_panel",
    "feature_fill_rates",
]
