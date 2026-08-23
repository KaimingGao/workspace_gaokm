"""市场代码解析（从 skills/common/history.py 下沉到 core 层）。

使用 register_symbol_resolver() 在应用启动时注册上层解析器（如 StockAPI.resolve_symbol）。
未注册时退回纯数字解析（A 股 6 位 / 港股 4-5 位）。
"""

from typing import Callable, Optional, Tuple

_symbol_resolver: Optional[Callable[[str], Optional[str]]] = None


def register_symbol_resolver(fn: Callable[[str], Optional[str]]) -> None:
    """注册上层 symbol 解析器（如 StockAPI.resolve_symbol）。"""
    global _symbol_resolver
    _symbol_resolver = fn


def resolve_market_code(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """
    返回 (market, code)：
    - market: CN / HK / US / None
    - code: 拉取日线用的裸代码（A 股 6 位，港股 5 位等）
    """
    if _symbol_resolver is not None:
        symbol = _symbol_resolver(raw)
        if symbol:
            s = symbol.lower()
            if s.startswith(("sh", "sz")):
                return "CN", s[2:]
            if s.startswith("hk"):
                return "HK", s[2:].zfill(5)
            if s.startswith("us"):
                return "US", s[2:].upper()

    text = (raw or "").strip()
    if text.isdigit() and len(text) == 6:
        return "CN", text
    if text.isdigit() and 4 <= len(text) <= 5:
        return "HK", text.zfill(5)
    return None, None
