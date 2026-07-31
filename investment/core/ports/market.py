"""行情 / 日线端口。默认经 skills.ports_bind 注入；单测可 set_adapter 覆盖。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from core.ports.adapters import call, set_adapter

# 供测试 / 启动显式绑定
set_market_adapter = set_adapter


def quote_price(quote: dict) -> Optional[float]:
    raw = quote.get("price_raw")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return None


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
    """日线 K 线（默认 skills.common.history；返回值透传）。支持 incremental。"""
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


def load_disk_spot(*, max_age_hours: float = 24.0 * 14) -> Optional[List[dict]]:
    """只读本地现货缓存（不触发远端）；供估值摘要等。"""
    return call("load_disk_spot", max_age_hours=float(max_age_hours))


def spot_row_get(row: Dict[str, Any], field: str) -> Any:
    return call("spot_row_get", row or {}, str(field or ""))


def spot_to_float(value: Any) -> Optional[float]:
    return call("spot_to_float", value)
