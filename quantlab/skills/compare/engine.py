"""多股对比：并行拉 quote，按涨跌幅排序。"""


from typing import Callable, List, Optional

from core.ports.market import query_quote


class CompareEngine:
    def compare(
        self,
        params: dict,
        *,
        quote_fn: Optional[Callable[[str], dict]] = None,
    ) -> dict:
        query = quote_fn or query_quote
        codes = params.get("stock_codes") or []
        if isinstance(codes, str):
            codes = [c.strip() for c in codes.replace("，", ",").split(",") if c.strip()]

        if not isinstance(codes, list) or len(codes) < 2:
            return {
                "success": False,
                "error": "请至少提供 2 只股票进行对比",
            }
        if len(codes) > 5:
            return {
                "success": False,
                "error": "一次最多对比 5 只股票",
            }

        items: List[dict] = []
        errors: List[dict] = []
        for code in codes:
            result = query(str(code).strip())
            if result.get("success"):
                items.append(self._slim_quote(result))
            else:
                errors.append(
                    {"stock_code": code, "error": result.get("error", "查询失败")}
                )

        if not items:
            return {
                "success": False,
                "error": "所有股票均查询失败",
                "failures": errors,
            }

        ranked = sorted(items, key=lambda x: x.get("change_raw") or 0, reverse=True)

        return {
            "success": True,
            "count": len(items),
            "stocks": ranked,
            "failures": errors,
            "summary": self._build_summary(ranked),
        }

    @staticmethod
    def _slim_quote(result: dict) -> dict:
        return {
            "stock_code": result.get("stock_code"),
            "stock_name": result.get("stock_name"),
            "price": result.get("price"),
            "price_raw": result.get("price_raw"),
            "change": result.get("change"),
            "change_raw": result.get("change_raw"),
            "change_amount": result.get("change_amount"),
            "volume": result.get("volume"),
            "currency": result.get("currency"),
            "market": result.get("market"),
        }

    @staticmethod
    def _build_summary(stocks: List[dict]) -> str:
        if not stocks:
            return ""
        best = stocks[0]
        worst = stocks[-1]
        lines = [f"共对比 {len(stocks)} 只股票（按涨跌幅排序）："]
        for s in stocks:
            lines.append(
                f"- {s.get('stock_name')}({s.get('stock_code')}): "
                f"{s.get('price')} / {s.get('change')} / 量 {s.get('volume') or '-'}"
            )
        if len(stocks) >= 2:
            lines.append(
                f"今日涨幅相对较高：{best.get('stock_name')}（{best.get('change')}）；"
                f"相对较低：{worst.get('stock_name')}（{worst.get('change')}）。"
            )
        return "\n".join(lines)
