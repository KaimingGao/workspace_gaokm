"""观察名单应用服务：Web / CLI 与 core.watching_store 的边界。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional


class WatchingService:
    """观察（watching）CRUD、行情、舆情、建仓预演。"""

    def search(self, q: str, *, limit: int = 8) -> Dict[str, Any]:
        from core.ports.market import search_stocks

        return search_stocks(q, limit=limit)

    def quotes(self, codes: Optional[List[str]] = None) -> Dict[str, Any]:
        from core.watching_store import list_watchlist_quotes

        return list_watchlist_quotes(codes=codes)

    def insights(self, codes: Optional[List[str]] = None) -> Dict[str, Any]:
        """观察摘要：评分/倾向/超额/量比/估值/同业/观察天数。"""
        from core.watching_insights import build_watching_insights
        from core.watching_store import read_watching, watchlist_added_map

        added_map: Dict[str, str] = {}
        try:
            uni = read_watching()
            added_map = watchlist_added_map(uni)
            # 历史名单无逐票加入日时，用名单 updated_at 兜底，避免「天」列全空
            fallback = str(uni.get("updated_at") or "").strip()
            if fallback:
                for c in uni.get("watchlist") or []:
                    key = str(c).strip()
                    if key and key not in added_map:
                        added_map[key] = fallback
            if codes is None:
                codes = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
        except FileNotFoundError:
            if codes is None:
                return {"ok": True, "count": 0, "items": [], "note": "尚未创建观察名单"}
        return build_watching_insights(list(codes or []), added_at_by_code=added_map)

    def sentiment_alerts(self) -> Dict[str, Any]:
        from core.sentiment import read_last_sentiment_alerts

        return read_last_sentiment_alerts()

    def sentiment_list(
        self,
        codes: Optional[List[str]] = None,
        *,
        limit: int = 3,
        force: bool = False,
    ) -> Dict[str, Any]:
        from core.sentiment import list_watchlist_sentiment

        return list_watchlist_sentiment(codes, limit=limit, force=force)

    def sentiment_one(self, code: str, *, limit: int = 8, force: bool = False) -> Dict[str, Any]:
        from core.sentiment import fetch_stock_headlines

        return fetch_stock_headlines(code, limit=limit, force=force)

    def sentiment_analysis(self, code: str) -> Dict[str, Any]:
        """基于舆情标题的 AI 分析（无 key 时降级提示）。"""
        from agent.llm_client import LLMClient
        from core.sentiment import fetch_stock_headlines

        headlines = fetch_stock_headlines(code, limit=10)
        if not headlines.get("ok") or not headlines.get("items"):
            return {"ok": False, "analysis": "暂无足够舆情数据进行分析"}

        titles = [item.get("title", "") for item in headlines["items"] if item.get("title")]
        stock_name = headlines.get("stock_name", code)
        sentiment = headlines.get("sentiment", {}) or {}
        sentiment_label = sentiment.get("label", "neutral")

        llm = LLMClient()
        if not llm.is_available():
            return {"ok": False, "analysis": "AI 服务暂不可用，请检查 DASHSCOPE_API_KEY 配置"}

        if sentiment_label == "bullish":
            focus = "重点解读看多的理由"
            structure = "## 看多理由\n…\n## 风险提示\n…"
        elif sentiment_label == "bearish":
            focus = "重点解读看空的理由"
            structure = "## 看空理由\n…\n## 机会提示\n…"
        else:
            focus = "分别列出看多与看空因素并给综合判断"
            structure = "## 看多理由\n…\n## 看空理由\n…\n## 综合判断\n…"

        prompt = (
            f"当前舆情情绪：{sentiment_label}\n"
            f"请分析股票「{stock_name}」的舆情标题，{focus}。\n\n"
            f"舆情标题：\n" + "\n".join(f"- {t}" for t in titles) + "\n\n"
            f"请按结构输出（中文，300–500 字）：\n{structure}"
        )
        response = llm.chat([{"role": "user", "content": prompt}], enable_search=False)
        choices = response.get("choices", [])
        if not choices:
            return {"ok": False, "analysis": "AI 分析失败"}
        content = choices[0].get("message", {}).get("content", "")
        return {"ok": True, "analysis": content.strip()}

    def add_watch(self, query: str, *, sync_paper: bool = False) -> Dict[str, Any]:
        from core.watching_store import add_watchlist_item

        return add_watchlist_item(query, sync_paper=sync_paper)

    def remove_watch(self, code: str, *, sync_paper: bool = False) -> Dict[str, Any]:
        from core.watching_store import remove_watchlist_item

        return remove_watchlist_item(code, sync_paper=sync_paper)

    def init(self) -> str:
        from core.watching_store import init_from_example

        return init_from_example()

    def save_file(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from core.watching_store import write_watching

        return write_watching(payload)
