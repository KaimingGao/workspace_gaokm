"""投资宇宙 Watching：观察池定义与刷新（P9.1）。

薄 re-export 门面；实现见 ``policy`` / ``repository`` / ``sources`` / ``sync_paper``。
"""

from core.paths import WATCHING_EXAMPLE_PATH, WATCHING_PATH
from core.watching.policy import (
    DAILY_PORTFOLIO_MAX_NAMES,
    MODEL_FIT_MAX_SIZE,
    WATCHING_DEFAULT_SIZE,
    WATCHING_MAX_SIZE,
    WATCHING_MIN_SIZE,
    clamp_watching_max_size,
    watching_pool_limit,
)
from core.watching.repository import (
    _align_watch_meta,
    _clean_code_list,
    _fill_codes_for_expand,
    _pad_side_fields,
    _resize_watchlist,
    add_watchlist_item,
    init_from_example,
    list_watchlist_quotes,
    read_watching,
    remove_watchlist_item,
    set_watching_max_size,
    validate_watching,
    watchlist_added_at_for,
    watchlist_added_map,
    watchlist_names_for,
    watchlist_origins_for,
    write_watching,
)
from core.watching.sources import (
    _clean_name,
    _codes_from_source,
    _entries_from_source,
    _pull_source,
    _resolve_code,
    _resolve_entry,
    refresh_watchlist,
)
from core.watching.sync_paper import (
    _resolve_sync_targets,
    _sync_sizing_kwargs,
    plan_sync_to_paper,
    sync_paper_watchlist,
)

__all__ = [
    "DAILY_PORTFOLIO_MAX_NAMES",
    "MODEL_FIT_MAX_SIZE",
    "WATCHING_DEFAULT_SIZE",
    "WATCHING_MAX_SIZE",
    "WATCHING_MIN_SIZE",
    "add_watchlist_item",
    "clamp_watching_max_size",
    "init_from_example",
    "list_watchlist_quotes",
    "plan_sync_to_paper",
    "read_watching",
    "refresh_watchlist",
    "remove_watchlist_item",
    "set_watching_max_size",
    "sync_paper_watchlist",
    "validate_watching",
    "watchlist_added_at_for",
    "watchlist_added_map",
    "watchlist_names_for",
    "watchlist_origins_for",
    "watching_pool_limit",
    "write_watching",
]
