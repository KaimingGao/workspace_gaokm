"""Deprecated alias: use ``quant.research.tau_ridge``。"""
from quant.research import tau_ridge as _m
from quant.research.tau_ridge import *  # noqa: F401,F403

__all__ = [n for n in dir(_m) if not n.startswith("_")]
