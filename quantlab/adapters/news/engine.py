"""新闻/资讯标题摘要（东财搜索公开源）。"""


import json
import logging
import re
import time
from typing import Any, List, Optional
from urllib.parse import quote as url_quote

import requests

from core.ports.market import query_quote, resolve_market_code

logger = logging.getLogger(__name__)

# 单次 HTTP 超时；勿走 akshare.stock_news_em（其 requests 无 timeout，
# 挂死后会占住全局 ak_lock，导致整站资讯/部分行情链路卡死）。
_NEWS_HTTP_TIMEOUT_SEC = 8.0
# 整段 build_news 墙钟上限（含别名重试），须低于前端 AbortController(20s)
_NEWS_WALL_TIMEOUT_SEC = 16.0

_EM_SEARCH_URL = "https://search-api-web.eastmoney.com/search/jsonp"
_JSONP_CB = "jQuery3510876543210_1000000000000"


def _pick(row: dict, *keys: str) -> Any:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None


def _strip_em_tags(text: str) -> str:
    s = str(text or "")
    s = re.sub(r"\(</?em>\)", "", s)
    s = re.sub(r"</?em>", "", s)
    s = s.replace("\u3000", "").replace("\r\n", " ")
    return s.strip()


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
        logger.exception('unexpected error in fetch_content')
        return ""


def fetch_news_rows(symbol: str, *, timeout: float = _NEWS_HTTP_TIMEOUT_SEC) -> List[dict]:
    """拉取资讯原始行。symbol 优先用 6 位 A 股代码或名称。

    直连东财 search JSONP（与 akshare.stock_news_em 同源），并强制 HTTP timeout，
    避免占用 ak_lock。
    """
    keyword = str(symbol or "").strip()
    if not keyword:
        return []

    t_http = max(2.0, float(timeout or _NEWS_HTTP_TIMEOUT_SEC))
    inner_param = {
        "uid": "",
        "keyword": keyword,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": 1,
                "pageSize": 10,
                "preTag": "<em>",
                "postTag": "</em>",
            }
        },
    }
    params = {
        "cb": _JSONP_CB,
        "param": json.dumps(inner_param, ensure_ascii=False),
        "_": str(int(time.time() * 1000)),
    }
    headers = {
        "User-Agent": _CONTENT_HEADERS["User-Agent"],
        "Referer": f"https://so.eastmoney.com/news/s?keyword={url_quote(keyword)}",
        "Accept": "*/*",
    }
    r = requests.get(_EM_SEARCH_URL, params=params, headers=headers, timeout=t_http)
    r.raise_for_status()
    text = (r.text or "").strip()
    if text.startswith(_JSONP_CB + "(") and text.endswith(")"):
        payload = text[len(_JSONP_CB) + 1 : -1]
    else:
        # 兼容 cb 前缀变化：剥到首个 '(' … 末尾 ')'
        l = text.find("(")
        rparen = text.rfind(")")
        if l < 0 or rparen <= l:
            raise RuntimeError("东财资讯响应不是合法 JSONP")
        payload = text[l + 1 : rparen]
    data_json = json.loads(payload)
    articles = ((data_json or {}).get("result") or {}).get("cmsArticleWebOld") or []
    rows: List[dict] = []
    for art in articles:
        if not isinstance(art, dict):
            continue
        code = str(art.get("code") or "").strip()
        title = _strip_em_tags(art.get("title") or "")
        if not title:
            continue
        url = str(art.get("url") or "").strip()
        if not url and code:
            url = f"http://finance.eastmoney.com/a/{code}.html"
        rows.append(
            {
                "关键词": keyword,
                "新闻标题": title,
                "新闻内容": _strip_em_tags(art.get("content") or ""),
                "发布时间": str(art.get("date") or ""),
                "文章来源": str(art.get("mediaName") or ""),
                "新闻链接": url,
            }
        )
    return rows


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
    return _build_news_inner(stock_code, limit=limit, with_content=with_content)


def _build_news_inner(stock_code: str, limit: int = 8, *, with_content: bool = False) -> dict:
    t0 = time.monotonic()
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

    # 名称优先；超时后不再穷尽全部别名，尽快失败/回退（最多 2 次，控墙钟）
    items: List[dict] = []
    errors: List[str] = []
    used_query = None
    for q in queries[:2]:
        elapsed = time.monotonic() - t0
        remain = _NEWS_WALL_TIMEOUT_SEC - elapsed
        if remain < 2.0:
            errors.append("资讯拉取墙钟超时")
            break
        try:
            rows = fetch_news_rows(
                q, timeout=min(_NEWS_HTTP_TIMEOUT_SEC, remain)
            )
            for row in rows:
                item = normalize_news_item(row)
                if item and item["title"]:
                    items.append(item)
            if items:
                used_query = q
                break
        except Exception as e:
            logger.exception('unexpected error in _build_news_inner')
            errors.append(f"{q}: {e}")
            # 超时类错误：别名重试价值低，直接结束
            msg = str(e).lower()
            if "timed out" in msg or "timeout" in msg:
                break

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
            remain = _NEWS_WALL_TIMEOUT_SEC - (time.monotonic() - t0)
            if remain < 1.5:
                break
            it["content"] = fetch_content(
                it.get("url", ""), timeout=max(1, min(5, int(remain)))
            )

    if not unique:
        return {
            "success": False,
            "stock_code": code,
            "stock_name": name,
            "error": "未获取到相关资讯" + (f"（{errors[0]}）" if errors else ""),
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
