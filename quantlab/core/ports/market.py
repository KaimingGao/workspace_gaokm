"""行情 / 日线端口。默认经 adapters.bind 注入；单测可 set_adapter 覆盖。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from core.ports.registry import call, set_adapter

# 供测试 / 启动显式绑定
set_market_adapter = set_adapter


def _positive_px(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def quote_price(quote: dict) -> Optional[float]:
    """最新价。≤0 视为无有效现价（盘后/凌晨源常把现价清零）。"""
    if not isinstance(quote, dict):
        return None
    return _positive_px(quote.get("price_raw"))


def quote_prev_close(quote: dict) -> Optional[float]:
    """昨收。凌晨现价为 0 时，这就是上一交易日收盘价。"""
    if not isinstance(quote, dict):
        return None
    for key in ("prev_close", "pre_close", "yesterday_close"):
        px = _positive_px(quote.get(key))
        if px is not None:
            return px
    return None


def quote_mark_price(quote: dict) -> Optional[float]:
    """盯市价：有效现价，否则昨收。不成交、只估值。"""
    return quote_price(quote) or quote_prev_close(quote)


def query_quote(code: str) -> Dict[str, Any]:
    """现价查询（默认腾讯行情适配）。"""
    return call("query_quote", str(code or "").strip())


def batch_query_quotes(codes: Sequence[str]) -> Dict[str, dict]:
    """批量现价；返回 {原始查询串: quote_dict}。"""
    batch = [str(c or "").strip() for c in (codes or []) if str(c or "").strip()]
    if not batch:
        return {}
    return call("batch_query_quotes", batch) or {}


def resolve_symbol(code: str) -> Optional[str]:
    """名称/代码 → 腾讯行情符号；无法解析返回 None。"""
    return call("resolve_symbol", str(code or "").strip())


def search_stocks(query: str, *, limit: int = 8) -> Dict[str, Any]:
    """股票搜索（观察页 / CLI）。"""
    return call("search_stocks", str(query or "").strip(), limit=int(limit or 8))


def fetch_daily_bars(
    code: str,
    limit: int = 120,
    **kwargs: Any,
) -> Any:
    """日线 K 线（默认 adapters.market.history；返回值透传）。支持 incremental。"""
    return call("fetch_daily_bars", str(code or "").strip(), limit=limit, **kwargs)


def bars_from_quote_fallback(quote: dict, **kwargs: Any) -> Any:
    """无日线时用现价构造伪序列；入参为 quote dict。"""
    return call("bars_from_quote_fallback", quote, **kwargs)


def resolve_market_code(code: str) -> Any:
    return call("resolve_market_code", str(code or "").strip())


def build_fundamentals(code: str, **kwargs: Any) -> Any:
    return call("build_fundamentals", str(code or "").strip(), **kwargs)


def build_news(code: str, **kwargs: Any) -> Any:
    return call("build_news", str(code or "").strip(), **kwargs)


def build_relative(code: str, **kwargs: Any) -> Any:
    return call("build_relative", str(code or "").strip(), **kwargs)


def default_benchmark(market: str = "CN") -> Any:
    return call("default_benchmark", market)


def fetch_index_bars(code: str, **kwargs: Any) -> Any:
    return call("fetch_index_bars", str(code or "").strip(), **kwargs)


def build_peer_compare(code: str, **kwargs: Any) -> Any:
    return call("build_peer_compare", str(code or "").strip(), **kwargs)


def build_kline_payload(payload: Dict[str, Any]) -> Any:
    return call("build_kline_payload", payload or {})


def screen_stocks(params: Dict[str, Any]) -> Any:
    return call("screen_stocks", params or {})


def fetch_a_spot(**kwargs: Any) -> Any:
    return call("fetch_a_spot", **kwargs)


def load_disk_spot(*, max_age_hours: Optional[float] = None) -> Optional[List[dict]]:
    """只读本地现货缓存（不触发远端）；供估值摘要等。"""
    from core.data.policy import SPOT_DISK_MAX_AGE_HOURS

    age = SPOT_DISK_MAX_AGE_HOURS if max_age_hours is None else float(max_age_hours)
    return call("load_disk_spot", max_age_hours=age)


def spot_row_get(row: Dict[str, Any], field: str) -> Any:
    return call("spot_row_get", row or {}, str(field or ""))


def spot_to_float(value: Any) -> Optional[float]:
    return call("spot_to_float", value)


def fetch_minute_bars(code: str, **kwargs: Any) -> Any:
    """分钟线（默认 adapters.market.minute_history）。"""
    return call("fetch_minute_bars", str(code or "").strip(), **kwargs)


def group_minute_bars_by_date(bars: Any) -> Any:
    return call("group_minute_bars_by_date", bars)


def fetch_cn_financial_series(code: str, **kwargs: Any) -> Any:
    """A 股财务指标时间序列。"""
    return call("fetch_cn_financial_series", str(code or "").strip(), **kwargs)


def batch_map(fn, items, **kwargs: Any) -> List[Any]:
    """批量并发映射（进程池隔离 AkShare py_mini_racer，避免串行卡顿）。

    fn 须为顶层函数（可 pickle）；单项异常返回 None，不拖垮整批。
    默认经 adapters.bind 注入 ak_worker 进程池；未绑定时 ensure_bound 自动注入。
    """
    items = list(items or [])
    if not items:
        return []
    return call("batch_map", fn, items, **kwargs)


def fetch_cn_valuation_latest(code: str, **kwargs: Any) -> Any:
    """A 股估值最新一条（乐咕 / 东财）。"""
    return call("fetch_cn_valuation_latest", str(code or "").strip(), **kwargs)


def build_macro_snapshot(**kwargs: Any) -> Any:
    """宏观快照构建（跨境/利率等）。"""
    return call("build_macro_snapshot", **kwargs)


def build_market_sentiment_snapshot(**kwargs: Any) -> Any:
    """市场情绪快照。"""
    return call("build_market_sentiment_snapshot", **kwargs)


def build_announcement_snapshot(**kwargs: Any) -> Any:
    """监管/公告扫描快照。"""
    return call("build_announcement_snapshot", **kwargs)


def build_code_concept_index(concepts: Sequence[str], **kwargs: Any) -> Any:
    """概念提示 → code 所属概念索引（可走磁盘缓存）。"""
    return call("build_code_concept_index", list(concepts or []), **kwargs)
