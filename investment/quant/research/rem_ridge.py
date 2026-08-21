"""兼容再导出：canonical 在 core.research.rem_ridge。"""
from core.research import rem_ridge as _m
from core.research.rem_ridge import *  # noqa: F401,F403

__all__ = [n for n in dir(_m) if not n.startswith("__")]
