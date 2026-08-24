"""Deprecated alias: use ``core.research.tau_panel`` (τ 训练面板)。"""
from core.research import tau_panel as _m
from core.research.tau_panel import *  # noqa: F401,F403

__all__ = [n for n in dir(_m) if not n.startswith("_")]
