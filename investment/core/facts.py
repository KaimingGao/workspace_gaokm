"""单票多层事实采集（领域层，直接调 engine / API，不经 Handler JSON 往返）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.ports.market import (
    build_kline_payload,
    build_peer_compare,
    build_relative,
    default_benchmark,
    query_quote,
    resolve_market_code,
)
from core.ports.signal import build_signal_pool


def _pick_signal_item(pool: List[dict], code: str) -> Optional[dict]:
    for item in pool or []:
        if str(item.get("stock_code")) == str(code):
            return item
    return pool[0] if pool else None


def collect_stock_facts(
    stock_code: str,
    *,
    horizon_days: int = 3,
    include_peer: bool = True,
    include_index: bool = True,
) -> Dict[str, Any]:
    """
    拉齐 quote / signal / kline / peer / index。
    供 advise、测试、未来 position 扩展共用。
    """
    raw = (stock_code or "").strip()
    quote = query_quote(raw)
    code = quote.get("stock_code") if quote.get("success") else raw
    name = quote.get("stock_name") if quote.get("success") else raw

    horizon = max(1, min(int(horizon_days or 3), 3))
    signal_res = build_signal_pool(
        {"stock_codes": [code], "horizon_days": horizon, "limit": 3}
    )
    signal_item = (
        _pick_signal_item(signal_res.get("observation_pool") or [], code)
        if signal_res.get("success")
        else None
    )

    kline = build_kline_payload({"stock_code": code, "limit": 10})

    peer: Dict[str, Any] = {"success": False}
    if include_peer:
        peer = build_peer_compare(code)

    index: Dict[str, Any] = {"success": False}
    if include_index:
        market, _ = resolve_market_code(code)
        index = build_relative(code, benchmark=default_benchmark(market), days=20)

    return {
        "stock_code": code,
        "stock_name": name,
        "horizon_days": horizon,
        "quote": quote,
        "signal": signal_res,
        "signal_item": signal_item or {},
        "kline": kline,
        "peer": peer,
        "index": index,
    }


def facts_summary(facts: Dict[str, Any]) -> Dict[str, Any]:
    """压缩 facts 供 advise 输出与 LLM 引用。"""
    quote = facts.get("quote") or {}
    signal_item = facts.get("signal_item") or {}
    kline = facts.get("kline") or {}
    peer = facts.get("peer") or {}
    index = facts.get("index") or {}

    return {
        "quote": {
            "success": quote.get("success"),
            "price": quote.get("price"),
            "change": quote.get("change"),
            "change_raw": quote.get("change_raw"),
        },
        "signal": {
            "success": (facts.get("signal") or {}).get("success"),
            "score": signal_item.get("score"),
            "hard_reject": signal_item.get("hard_reject"),
            "data_source": signal_item.get("data_source"),
            "reasons": signal_item.get("reasons"),
        },
        "kline": {
            "success": kline.get("success"),
            "summary": kline.get("summary") if kline.get("success") else None,
            "latest_tags": kline.get("latest_tags") if kline.get("success") else None,
            "data_source": kline.get("data_source"),
            "depth": kline.get("depth"),
        },
        "peer": {
            "success": peer.get("success"),
            "summary": peer.get("summary") if peer.get("success") else None,
            "relative": (peer.get("target") or {}).get("relative") if peer.get("success") else None,
        },
        "index": {
            "success": index.get("success"),
            "summary": index.get("summary") if index.get("success") else None,
            "excess_return_pct": index.get("excess_return_pct") if index.get("success") else None,
        },
    }
