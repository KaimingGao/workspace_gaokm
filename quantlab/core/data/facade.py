"""薄 DataService 门面（Domain Facade · DS）：上层唯一读窗口。

委托 core.data.MarketDataService。文档称 Domain Facade，与 Application Service 不同层。
历史兼容：本模块函数仍返回 dict（``.as_dict()``）。
类型化 API：``from core.data import MarketDataService, BarsResult``；
便捷 dict API：``from core.data.facade import get_quote, get_bars``。
"""

from typing import Any, Dict, List, Optional, Tuple

from core.data.gate import (
    DEFAULT_ADJUST_POLICY,
    allows_production_score,
    infer_adjust,
    normalize_adjust_policy,
)
from core.data.service import get_default_service, get_research_service
from core.data.policy import (
    DAILY_CACHE_HOURS,
    FUNDAMENTALS_CACHE_HOURS,
    MINUTE_CACHE_HOURS,
    NEWS_CACHE_HOURS,
)

__all__ = [
    "DEFAULT_ADJUST_POLICY",
    "DAILY_CACHE_HOURS",
    "FUNDAMENTALS_CACHE_HOURS",
    "MINUTE_CACHE_HOURS",
    "NEWS_CACHE_HOURS",
    "allows_production_score",
    "bars_and_source",
    "bars_and_source_research",
    "get_bars",
    "get_bars_batch",
    "get_fundamentals",
    "get_index_bars",
    "get_macro_snapshot",
    "get_market_sentiment_snapshot",
    "get_announcement_snapshot",
    "index_bars_and_source",
    "get_minute_bars",
    "get_news",
    "get_quote",
    "batch_get_quotes",
    "get_spot",
    "infer_adjust",
    "normalize_adjust_policy",
    "summarize_data_quality",
]


def get_quote(code: str) -> Dict[str, Any]:
    return get_default_service().get_quote(code).as_dict()


def batch_get_quotes(codes: Optional[List[str]] = None) -> Dict[str, dict]:
    return get_default_service().batch_get_quotes(codes)


def get_index_bars(code: str, *, limit: int = 120) -> Dict[str, Any]:
    return get_default_service().get_index_bars(code, limit=limit).as_dict()


def index_bars_and_source(code: str, *, limit: int = 120) -> Tuple[List[dict], str]:
    """指数日线 + 来源标签（兼容旧 ``fetch_index_bars`` 二元组）。"""
    return get_default_service().index_bars_and_source(code, limit=limit)


def get_minute_bars(
    code: str,
    *,
    period: str = "5",
    lookback_days: int = 30,
    use_cache: bool = True,
    max_age_hours: float = MINUTE_CACHE_HOURS,
) -> Dict[str, Any]:
    return get_default_service().get_minute_bars(
        code,
        period=period,
        lookback_days=lookback_days,
        use_cache=use_cache,
        max_age_hours=max_age_hours,
    ).as_dict()


def get_bars(
    code: str,
    *,
    limit: int = 120,
    use_cache: bool = True,
    cache_max_age_hours: float = DAILY_CACHE_HOURS,
    as_of: Optional[str] = None,
    incremental: bool = True,
    adjust: Optional[str] = None,
    offline_ok: bool = False,
    offline_only: bool = False,
    reject_quote_fallback: Optional[bool] = None,
) -> Dict[str, Any]:
    """返回 dict 信封（兼容旧调用）。类型化请用 ``MarketDataService.get_bars``。"""
    return get_default_service().get_bars(
        code,
        limit=limit,
        use_cache=use_cache,
        cache_max_age_hours=cache_max_age_hours,
        as_of=as_of,
        incremental=incremental,
        adjust=adjust,
        offline_ok=offline_ok,
        offline_only=offline_only,
        reject_quote_fallback=reject_quote_fallback,
    ).as_dict()


def get_bars_batch(
    codes: Optional[List[str]] = None,
    *,
    limit: int = 120,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    """批量日线 dict 信封（研究请用 ``get_research_service().get_bars_batch``）。"""
    return get_default_service().get_bars_batch(codes, limit=limit, **kwargs)


def bars_and_source(
    code: str,
    *,
    limit: int = 120,
    **kwargs: Any,
) -> Tuple[List[dict], str]:
    return get_default_service().bars_and_source(code, limit=limit, **kwargs)


def bars_and_source_research(
    code: str,
    *,
    limit: int = 120,
    **kwargs: Any,
) -> Tuple[List[dict], str]:
    return get_research_service().bars_and_source(code, limit=limit, **kwargs)


def get_fundamentals(
    code: str,
    *,
    use_cache: bool = True,
    cache_max_age_hours: float = FUNDAMENTALS_CACHE_HOURS,
    as_of: Optional[str] = None,
    live: bool = True,
    **kwargs: Any,
) -> Dict[str, Any]:
    return get_default_service().get_fundamentals(
        code,
        use_cache=use_cache,
        cache_max_age_hours=cache_max_age_hours,
        as_of=as_of,
        live=live,
        **kwargs,
    ).as_dict()


def get_news(
    code: str,
    *,
    limit: int = 8,
    use_cache: bool = True,
    cache_max_age_hours: float = NEWS_CACHE_HOURS,
    **kwargs: Any,
) -> Dict[str, Any]:
    return get_default_service().get_news(
        code,
        limit=limit,
        use_cache=use_cache,
        cache_max_age_hours=cache_max_age_hours,
        **kwargs,
    ).as_dict()


def get_spot(*, force: bool = False, disk_only: bool = False) -> Dict[str, Any]:
    return get_default_service().get_spot(force=force, disk_only=disk_only).as_dict()


def get_macro_snapshot(*, max_age_hours: float = 36.0) -> Dict[str, Any]:
    return get_default_service().get_macro_snapshot(max_age_hours=max_age_hours).as_dict()


def get_market_sentiment_snapshot(*, max_age_hours: float = 36.0) -> Dict[str, Any]:
    return get_default_service().get_market_sentiment_snapshot(
        max_age_hours=max_age_hours
    ).as_dict()


def get_announcement_snapshot(*, max_age_hours: float = 36.0) -> Dict[str, Any]:
    return get_default_service().get_announcement_snapshot(
        max_age_hours=max_age_hours
    ).as_dict()


def summarize_data_quality(
    codes: Optional[List[str]] = None,
    *,
    limit: int = 60,
) -> Dict[str, Any]:
    return get_default_service().summarize_data_quality(codes, limit=limit)
