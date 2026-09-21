"""将出站适配器绑定到 core.ports.registry。

I/O 与快照构建一律来自 ``adapters.*``；skills 仅作 Agent 薄包装。
"""


_BOUND = False


def bind_market_adapters(*, force: bool = False) -> None:
    """注册默认出站适配器。可重复调用；force=True 时覆盖。"""
    global _BOUND
    if _BOUND and not force:
        from core.ports import registry as ad

        if ad.get_adapter("query_quote") is not None:
            return

    from adapters.announcement.concept_graph import build_code_concept_index
    from adapters.announcement.engine import build_announcement_snapshot
    from adapters.fundamentals.engine import (
        build_fundamentals,
        fetch_cn_financial_series,
        fetch_cn_valuation_latest,
    )
    from adapters.index.engine import build_relative
    from adapters.kline.engine import KlineEngine
    from adapters.macro.engine import build_macro_snapshot
    from adapters.market.ak_worker import get_pool as _get_ak_pool
    from adapters.market.history import (
        bars_from_quote_fallback,
        fetch_daily_bars,
        resolve_market_code,
    )
    from adapters.market.index_bars import default_benchmark, fetch_index_bars
    from adapters.market.minute_history import (
        fetch_minute_bars,
        group_minute_bars_by_date,
    )
    from adapters.market.quote_api import StockAPI
    from adapters.market.stock_search import search_stocks
    from adapters.news.engine import build_news
    from adapters.peer.engine import build_peer_compare
    from adapters.screen.engine import (
        StockScreener,
        fetch_a_spot,
        load_disk_spot,
        spot_row_get,
        spot_to_float,
    )
    from adapters.sentiment.engine import build_market_sentiment_snapshot
    from adapters.signal.engine import SignalEngine
    from core.ports.registry import mark_bound, set_adapter

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
    set_adapter("fetch_cn_valuation_latest", fetch_cn_valuation_latest)
    set_adapter("build_fundamentals", build_fundamentals)
    set_adapter("build_news", build_news)
    set_adapter("build_relative", build_relative)
    set_adapter("default_benchmark", default_benchmark)
    set_adapter("fetch_index_bars", fetch_index_bars)
    set_adapter("build_peer_compare", build_peer_compare)
    set_adapter("build_macro_snapshot", build_macro_snapshot)
    set_adapter("build_market_sentiment_snapshot", build_market_sentiment_snapshot)
    set_adapter("build_announcement_snapshot", build_announcement_snapshot)
    set_adapter("build_code_concept_index", build_code_concept_index)
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
    set_adapter(
        "batch_map",
        lambda fn, items, **kwargs: _get_ak_pool().map(fn, items, **kwargs),
    )
    mark_bound()
    _BOUND = True
