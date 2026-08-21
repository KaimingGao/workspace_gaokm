"""Backward-compatible shim. Canonical location: core.research.split"""
from core.research.split import (  # noqa: F401
    TimeSeriesSplit,
    WalkForwardFold,
    rolling_walk_forward_slices,
    slice_bars,
    time_series_split,
)
