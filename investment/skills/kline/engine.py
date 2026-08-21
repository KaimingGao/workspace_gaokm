"""K 线 Skill：经 DataService 拉日线 + 形态摘要；失败时用现价构造可解释 K。"""


import logging

from core.data_service import get_bars, get_quote
from core.ports.market import bars_from_quote_fallback
from skills.kline.analyzer import summarize_bars

logger = logging.getLogger(__name__)


class KlineEngine:
    def analyze(self, params: dict) -> dict:
        stock_code = (params.get("stock_code") or "").strip()
        if not stock_code:
            return {"success": False, "error": "请提供 stock_code"}

        limit = int(params.get("limit") or 15)
        limit = max(3, min(limit, 40))

        quote = get_quote(stock_code)
        name = quote.get("stock_name") if quote.get("success") else stock_code
        code = quote.get("stock_code") if quote.get("success") else stock_code

        bars = []
        data_source = "empty"
        quality = None
        production_ok = None
        fetch_notes = []
        try:
            pack = get_bars(stock_code, limit=max(limit, 20))
            bars = list(pack.get("bars") or [])
            data_source = str(pack.get("data_source") or "empty")
            quality = pack.get("quality")
            production_ok = pack.get("production_ok")
            if not bars and code and str(code) != stock_code:
                pack = get_bars(str(code), limit=max(limit, 20))
                bars = list(pack.get("bars") or [])
                data_source = str(pack.get("data_source") or "empty")
                quality = pack.get("quality")
                production_ok = pack.get("production_ok")
            if not bars:
                fetch_notes.append("完整日线暂不可用，已尝试多接口")
        except Exception as e:
            logger.exception('unexpected error in analyze')
            fetch_notes.append(f"日线拉取异常: {e}")
            bars, data_source = [], "empty"

        if not bars and quote.get("success"):
            bars = bars_from_quote_fallback(quote)
            data_source = "quote_fallback"
            production_ok = False
            quality = {"level": "thin"}
            fetch_notes.append(
                "已用当日腾讯行情构造近似 K（仅 1～2 根，深度有限，勿当成完整日线）"
            )

        if not bars:
            return {
                "success": False,
                "stock_code": code,
                "stock_name": name,
                "error": "无法获取 K 线数据，请换用代码重试或检查网络/AkShare",
                "notes": fetch_notes,
                "quality": quality or {"level": "empty"},
                "production_ok": False,
            }

        # 只分析最近 limit 根
        bars = bars[-limit:]
        analysis = summarize_bars(bars)
        slim_candles = []
        for c in (analysis.get("candles") or [])[-limit:]:
            slim_candles.append(
                {
                    "date": c.get("date"),
                    "open": c.get("open"),
                    "high": c.get("high"),
                    "low": c.get("low"),
                    "close": c.get("close"),
                    "change_pct": c.get("change_pct"),
                    "direction": c.get("direction"),
                    "tags": c.get("tags"),
                }
            )

        latest = analysis.get("latest") or {}
        depth = "full_daily" if data_source.startswith(("akshare_", "cache:")) else "intraday_proxy"

        return {
            "success": True,
            "stock_code": code,
            "stock_name": name,
            "limit": limit,
            "bar_count": len(slim_candles),
            "data_source": data_source,
            "quality": quality,
            "production_ok": production_ok,
            "depth": depth,
            "summary": analysis.get("summary"),
            "latest": latest,
            "latest_tags": latest.get("tags") if isinstance(latest, dict) else [],
            "trend_note": analysis.get("trend_note"),
            "recent_cum_change_pct": analysis.get("recent_cum_change_pct"),
            "candles": slim_candles,
            "notes": fetch_notes,
            "note": "以上为 K 线形态事实，供投顾建议使用；市场有风险，不保证收益。",
        }
