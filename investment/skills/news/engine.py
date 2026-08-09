"""新闻/资讯标题摘要（AkShare 等公开源）。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import requests

from core.ports.market import query_quote, resolve_market_code
from skills.screen.engine import _normalize_rows, _to_float


def _pick(row: dict, *keys: str) -> Any:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None


# --------------------------------------------------------------------------- #
# 正文抓取（供 LLM 情绪分析用；标题之外补充正文语义）
# --------------------------------------------------------------------------- #

_CONTENT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
}


def _extract_text(html: str, max_chars: int = 500) -> str:
    """从 HTML 提取纯文本：去 script/style → 去标签 → 压缩空白 → 截断。"""
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def fetch_content(url: str, *, timeout: int = 5, max_chars: int = 500) -> str:
    """抓取新闻正文，截取前 max_chars 字。失败返回空串（不抛异常）。"""
    if not url:
        return ""
    try:
        response = requests.get(url, headers=_CONTENT_HEADERS, timeout=timeout)
        response.raise_for_status()
        return _extract_text(response.text, max_chars)
    except Exception:
        return ""


def fetch_news_rows(symbol: str) -> List[dict]:
    """拉取资讯原始行。symbol 优先用 6 位 A 股代码或名称。"""
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()
    fn = getattr(ak, "stock_news_em", None)
    if fn is None:
        raise RuntimeError("当前 akshare 无 stock_news_em 接口")
    # 常见签名：symbol="600519" 或关键词
    try:
        df = fn(symbol=symbol)
    except TypeError:
        df = fn(symbol)
    return _normalize_rows(df)


def normalize_news_item(row: dict) -> Optional[dict]:
    title = _pick(row, "新闻标题", "标题", "title", "name")
    if not title:
        return None
    return {
        "title": str(title).strip(),
        "time": str(_pick(row, "发布时间", "时间", "time", "date") or ""),
        "source": str(_pick(row, "文章来源", "来源", "source") or ""),
        "url": str(_pick(row, "新闻链接", "链接", "url", "href") or ""),
        "content": "",
    }


def build_news(stock_code: str, limit: int = 8, *, with_content: bool = False) -> dict:
    limit = max(1, min(int(limit or 8), 15))
    quote = query_quote(stock_code)
    name = quote.get("stock_name") if quote.get("success") else stock_code
    code = quote.get("stock_code") if quote.get("success") else stock_code
    market, bare = resolve_market_code(stock_code)

    queries = []
    # 名称优先（港股代码在东方财富资讯接口上经常无效）
    if name:
        clean_name = str(name).replace("-W", "").replace("-w", "").strip()
        for q in (clean_name, name):
            if q and q not in queries:
                queries.append(q)
    if market == "CN" and bare:
        queries.append(bare)
    if market == "HK" and bare:
        # 尝试不带前导 0 的代码与常见中文别名
        queries.append(bare.lstrip("0") or bare)
        if "快手" in str(name) or bare in ("01024", "1024"):
            for q in ("快手", "Kuaishou"):
                if q not in queries:
                    queries.append(q)
    if code and str(code) not in queries:
        queries.append(str(code))
    if stock_code not in queries:
        queries.append(stock_code)

    items: List[dict] = []
    errors: List[str] = []
    used_query = None
    for q in queries:
        try:
            rows = fetch_news_rows(q)
            for row in rows:
                item = normalize_news_item(row)
                if item and item["title"]:
                    items.append(item)
            if items:
                used_query = q
                break
        except Exception as e:
            errors.append(f"{q}: {e}")

    # 去重标题
    seen = set()
    unique = []
    for it in items:
        t = it["title"]
        if t in seen:
            continue
        seen.add(t)
        unique.append(it)
    unique = unique[:limit]

    # 可选：抓取正文（供 LLM 情绪分析用，标题之外补充语义）
    if with_content:
        for it in unique:
            it["content"] = fetch_content(it.get("url", ""))

    if not unique:
        return {
            "success": False,
            "stock_code": code,
            "stock_name": name,
            "error": "未获取到相关资讯",
            "notes": errors[:3],
        }

    lines = [f"{name}({code}) 近期资讯摘要（查询键: {used_query}）："]
    for i, it in enumerate(unique, 1):
        meta = " / ".join(x for x in (it.get("time"), it.get("source")) if x)
        lines.append(f"{i}. {it['title']}" + (f"（{meta}）" if meta else ""))

    return {
        "success": True,
        "stock_code": code,
        "stock_name": name,
        "market": market,
        "count": len(unique),
        "items": unique,
        "summary": "\n".join(lines),
        "notes": errors[:2],
        "note": "资讯来自公开接口，可能延迟或不完整；市场有风险，不保证收益，不代客下单。",
    }
