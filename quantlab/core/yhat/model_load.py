"""ŷ 模型加载薄封装：委托 research 落盘加载器，供 signal 统一入口。"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def load_tau_model(*, role: Optional[str] = None) -> Optional[Dict[str, Any]]:
    from core.research.tc_ridge import load_tau_model as _load

    return _load(role=role)


def load_co_model(*, role: Optional[str] = None) -> Optional[Dict[str, Any]]:
    from core.research.co_ridge import load_co_model as _load

    return _load(role=role)


def load_scoring_return_model() -> Tuple[Any, Dict[str, Any]]:
    """按 ``current_scoring_model_role`` 加载 ŷ_oo（研究套 / live 执行套）。"""
    from core.research.oo_ridge import load_scoring_return_model as _load

    return _load()


__all__ = [
    "load_co_model",
    "load_scoring_return_model",
    "load_tau_model",
]
