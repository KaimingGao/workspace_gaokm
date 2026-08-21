"""单票行情查询（经 DataService，附带质量/来源元数据）。"""


from typing import Callable, Optional


class QuoteEngine:
    def query(
        self,
        params: dict,
        *,
        quote_fn: Optional[Callable[[str], dict]] = None,
    ) -> dict:
        stock_code = (params.get("stock_code") or "").strip()
        if not stock_code:
            return {"success": False, "error": "请提供要查询的股票代码或名称"}
        if quote_fn is not None:
            return quote_fn(stock_code)
        from core.data_service import get_quote

        return get_quote(stock_code)
