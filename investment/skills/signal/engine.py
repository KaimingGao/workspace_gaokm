"""短线观察池编排：候选 → 日线/行情 → 评分 → TopN。"""

from __future__ import annotations

import concurrent.futures
from typing import Callable, List, Optional

from core.signal.score_stock import score_stock
from core.signal.scorer import rank_candidates
from core.ports.market import batch_query_quotes, screen_stocks

ProgressCb = Optional[Callable[[int, int, str], None]]


class SignalEngine:
    def build_pool(self, params: dict, *, on_progress: ProgressCb = None) -> dict:
        horizon = int(params.get("horizon_days") or 3)
        horizon = max(1, min(horizon, 3))
        limit = int(params.get("limit") or 8)
        skip_fundamentals = bool(params.get("skip_fundamentals", False))
        codes = params.get("stock_codes") or []
        if isinstance(codes, str):
            codes = [c.strip() for c in codes.replace("，", ",").split(",") if c.strip()]

        sector = params.get("sector")
        candidates: List[str] = list(codes) if codes else []

        if not candidates:
            if on_progress:
                on_progress(0, 1, "拉取候选股…")
            candidates = self._bootstrap_candidates(sector=sector)

        if not candidates:
            return {
                "success": False,
                "error": "没有可用候选股，请提供 stock_codes 或 sector",
            }

        scored_items = []
        rejected = []
        batch = candidates[:30]
        total = len(batch)

        # 预获取所有股票的行情（批量查询，一次网络请求）
        if on_progress:
            on_progress(0, total, f"预获取 {len(batch)} 只股票行情…")
        quotes = batch_query_quotes(batch)

        # 并行评分：使用 ThreadPoolExecutor 并发处理多只股票
        max_workers = min(len(batch), 10)

        def _score_one(raw):
            try:
                # 使用预获取的行情，避免重复网络请求
                quote = quotes.get(raw)
                return score_stock(raw, horizon_days=horizon, quote=quote, skip_fundamentals=skip_fundamentals)
            except Exception as e:
                return {"success": False, "stock_code": raw, "error": str(e)}

        if on_progress:
            on_progress(0, total, f"并行评分 {len(batch)} 只股票…")

        completed = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_score_one, raw): raw for raw in batch}
            for future in concurrent.futures.as_completed(futures):
                raw = futures[future]
                completed += 1
                try:
                    result = future.result()
                except Exception as e:
                    result = {"success": False, "stock_code": raw, "error": str(e)}

                if on_progress:
                    on_progress(completed, total, f"评分 {raw}（{completed}/{total}）")

                if not result.get("success"):
                    rejected.append({"stock_code": raw, "reason": result.get("error", "行情失败")})
                    continue

                quote = result["quote"]
                name = quote.get("stock_name") or str(raw)
                code = result["stock_code"]
                if "ST" in str(name):
                    rejected.append({"stock_code": code, "reason": "ST 股票已排除"})
                    continue

                item = result["signal_item"]
                if item.get("hard_reject"):
                    rejected.append(
                        {"stock_code": code, "stock_name": name, "reason": item.get("reject_reason")}
                    )
                    # 仍保留分数供持仓/观察展示；选股 TopN 由 rank_candidates 过滤
                    scored_items.append(item)
                else:
                    scored_items.append(item)

        pool = rank_candidates(scored_items, limit=limit, min_score=50.0)
        if not pool and scored_items:
            scored_items.sort(key=lambda x: x.get("score") or 0, reverse=True)
            pool = scored_items[:limit]

        return {
            "success": True,
            "horizon_days": horizon,
            "count": len(pool),
            "observation_pool": pool,
            "scored_items": scored_items,  # 返回所有评分结果，用于调仓
            "rejected_count": len(rejected),
            "rejected_sample": rejected[:5],
            "note": (
                f"以下为未来约 {horizon} 天短线观察池（基于动量/量价/波动因子），"
                "以上为 AI 投顾短线评分依据，市场有风险，不保证收益；请关注失效条件。"
            ),
        }

    def _bootstrap_candidates(self, sector: Optional[str] = None) -> List[str]:
        if sector:
            try:
                result = screen_stocks(
                    {"sector": sector, "limit": 15, "change_min": -5}
                )
                if result.get("success") and result.get("stocks"):
                    return [s["stock_code"] for s in result["stocks"]]
            except Exception:
                pass

        return [
            "贵州茅台",
            "五粮液",
            "招商银行",
            "宁德时代",
            "比亚迪",
            "中国平安",
            "美的集团",
            "恒瑞医药",
            "东方财富",
            "海康威视",
            "快手",
        ]
