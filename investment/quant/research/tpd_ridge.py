"""兼容再导出：canonical 在 core.research.tpd_ridge。"""
from core.research import tpd_ridge as _m
from core.research.tpd_ridge import *  # noqa: F401,F403

__all__ = [n for n in dir(_m) if not n.startswith("_")]
