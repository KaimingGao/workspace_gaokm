"""将 skills 行情实现绑定到 core.ports（单向：skills → ports，避免 market 硬 import skills）。"""

from __future__ import annotations

_BOUND = False


def bind_market_adapters(*, force: bool = False) -> None:
    """注册默认行情适配器。可重复调用；force=True 时覆盖。"""
    global _BOUND
    if _BOUND and not force:
        from core.ports import adapters as ad

        if ad.get_adapter("query_quote") is not None:
            return

    from core.ports.adapters import mark_bound, set_adapter
    from skills.common.history import (
        bars_from_quote_fallback,
        fetch_daily_bars,
        resolve_market_code,
    )
    from skills.common.minute_history import (
        fetch_minute_bars,
        group_minute_bars_by_date,
    )
    from skills.common.quote_api import StockAPI
    from skills.common.stock_search import search_stocks
    from skills.fundamentals.engine import build_fundamentals, fetch_cn_financial_series
    from skills.index.engine import build_relative, default_benchmark, fetch_index_bars
    from skills.kline.engine import KlineEngine
    from skills.news.engine import build_news
    from skills.peer.engine import build_peer_compare
    from skills.screen.engine import (
        StockScreener,
        fetch_a_spot,
        load_disk_spot,
        spot_row_get,
        spot_to_float,
    )
    from skills.signal.engine import SignalEngine

    set_adapter("query_quote", StockAPI.query)
    set_adapter("batch_query_quotes", StockAPI.batch_query)
    set_adapter("resolve_symbol", StockAPI.resolve_symbol)
    set_adapter("search_stocks", search_stocks)
    set_adapter("fetch_daily_bars", fetch_daily_bars)
    set_adapter("bars_from_quote_fallback", bars_from_quote_fallback)
    set_adapter("resolve_market_code", resolve_market_code)
    set_adapter("fetch_minute_bars", fetch_minute_bars)
    set_adapter("group_minute_bars_by_date", group_minute_bars_by_date)
    set_adapter("fetch_cn_financial_series", fetch_cn_financial_series)
    set_adapter("build_fundamentals", build_fundamentals)
    set_adapter("build_news", build_news)
    set_adapter("build_relative", build_relative)
    set_adapter("default_benchmark", default_benchmark)
    set_adapter("fetch_index_bars", fetch_index_bars)
    set_adapter("build_peer_compare", build_peer_compare)
    set_adapter(
        "build_kline_payload",
        lambda payload: KlineEngine().analyze(payload or {}),
    )
    set_adapter(
        "screen_stocks",
        lambda params: StockScreener().screen(params or {}),
    )
    set_adapter("fetch_a_spot", fetch_a_spot)
    set_adapter("load_disk_spot", load_disk_spot)
    set_adapter("spot_row_get", spot_row_get)
    set_adapter("spot_to_float", spot_to_float)
    set_adapter(
        "build_signal_pool",
        lambda payload, on_progress=None: SignalEngine().build_pool(
            payload or {}, on_progress=on_progress
        ),
    )
    mark_bound()
    _BOUND = True
