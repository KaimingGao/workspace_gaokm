"""兼容再导出：canonical 在 core.research.tau_tree。"""
from core.research import tau_tree as _m
from core.research.tau_tree import *  # noqa: F401,F403

__all__ = [n for n in dir(_m) if not n.startswith("_")]
