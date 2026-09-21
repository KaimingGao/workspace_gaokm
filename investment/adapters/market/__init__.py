"""市场数据出站适配器（腾讯行情 · AkShare 日线/分钟 · 指数等）。"""

from adapters.market.history import (
    bars_from_quote_fallback,
    fetch_daily_bars,
    normalize_bars,
    resolve_market_code,
)
from adapters.market.quote_api import StockAPI

__all__ = [
    "StockAPI",
    "normalize_bars",
    "resolve_market_code",
    "fetch_daily_bars",
    "bars_from_quote_fallback",
]
