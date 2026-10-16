"""Bars 持久化门面：日线/分钟缓存 + ``QUANTLAB_BARS_BACKEND`` 路由。

实现仍在 ``core.store`` / ``core.store_bars_sqlite``；此处统一对外导出常用 API。
"""

from core.store import (
    align_minute_volume_units,
    assess_quality,
    bars_backend,
    clear_daily_cache,
    code_refresh_lock,
    daily_bar_complete,
    daily_cache_path,
    drop_thinner_minute_days,
    get_store_dir,
    io_error_stats,
    list_cached_symbols,
    load_daily_cache,
    load_daily_cache_many,
    load_minute_cache,
    load_minute_since,
    load_minute_span_snapshot,
    merge_bars_by_date,
    merge_minute_bars_by_time,
    merge_save_daily_cache,
    minute_cache_path,
    peek_daily_cache_meta,
    save_daily_cache,
    save_minute_cache,
    trim_daily_bars,
    trim_minute_bars,
)

# 对外常用别名（文档/计划中的 bars 语义名）
load_daily_bars = load_daily_cache
save_daily_bars = save_daily_cache
load_minute_bars = load_minute_cache
save_minute_bars = save_minute_cache

__all__ = [
    "align_minute_volume_units",
    "assess_quality",
    "bars_backend",
    "clear_daily_cache",
    "code_refresh_lock",
    "daily_bar_complete",
    "daily_cache_path",
    "drop_thinner_minute_days",
    "get_store_dir",
    "io_error_stats",
    "list_cached_symbols",
    "load_daily_bars",
    "load_daily_cache",
    "load_daily_cache_many",
    "load_minute_bars",
    "load_minute_cache",
    "load_minute_since",
    "load_minute_span_snapshot",
    "merge_bars_by_date",
    "merge_minute_bars_by_time",
    "merge_save_daily_cache",
    "minute_cache_path",
    "peek_daily_cache_meta",
    "save_daily_bars",
    "save_daily_cache",
    "save_minute_bars",
    "save_minute_cache",
    "trim_daily_bars",
    "trim_minute_bars",
]
