"""Paper 持仓 stance 摘要（services 层隔离 skills.position）。"""

from __future__ import annotations

from typing import Any, Dict


def advise_paper_with_stance(
    paper_path: str,
    *,
    horizon_days: int = 3,
) -> Dict[str, Any]:
    """只读 stance 建议；供 quant portfolio_bridge 等调用。"""
    from skills.position.engine import PositionEngine

    return PositionEngine().advise(
        {
            "paper_path": paper_path,
            "include_stance": True,
            "horizon_days": horizon_days,
        }
    )
