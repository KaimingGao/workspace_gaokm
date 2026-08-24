"""Deprecated alias: use ``core.research.tau_theme`` (τ 主题日口径)。"""
from core.research import tau_theme as _m
from core.research.tau_theme import *  # noqa: F401,F403

__all__ = [n for n in dir(_m) if not n.startswith("_")]
