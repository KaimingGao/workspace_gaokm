"""指数日线出站适配器（AkShare / 宏观辅助源）。"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from adapters.market.history import normalize_bars

logger = logging.getLogger(__name__)


def fetch_index_bars(benchmark: str, limit: int = 30) -> Tuple[List[dict], str]:
    """拉取指数日线。返回 (bars, label)。"""
    from core.data.ak_lock import import_akshare

    ak = import_akshare()

    key = (benchmark or "").strip().lower()
    mapping = {
        "hs300": ("sh000300", "沪深300"),
        "沪深300": ("sh000300", "沪深300"),
        "csi300": ("sh000300", "沪深300"),
        "sh000300": ("sh000300", "沪深300"),
        "上证": ("sh000001", "上证指数"),
        "上证指数": ("sh000001", "上证指数"),
        "sh": ("sh000001", "上证指数"),
        "sh000001": ("sh000001", "上证指数"),
        "深证": ("sz399001", "深证成指"),
        "深证成指": ("sz399001", "深证成指"),
        "sz399001": ("sz399001", "深证成指"),
        "创业板": ("sz399006", "创业板指"),
        "创业板指": ("sz399006", "创业板指"),
        "sz399006": ("sz399006", "创业板指"),
        "hsi": ("HSI", "恒生指数"),
        "恒生": ("HSI", "恒生指数"),
        "恒生指数": ("HSI", "恒生指数"),
        "sox": ("index", "费城半导体"),
        "费城半导体": ("index", "费城半导体"),
        "ndx": ("index", "纳斯达克"),
        "纳斯达克": ("index", "纳斯达克"),
        "qqq": ("us_etf", "QQQ"),
        "kweb": ("us_etf", "KWEB"),
    }
    raw_key = (benchmark or "").strip()
    symbol, label = mapping.get(key, (None, None))
    if not symbol and raw_key.lower() in mapping:
        symbol, label = mapping[raw_key.lower()]
    if not symbol:
        return [], ""

    # 全球指数 / 美股 ETF：复用 adapters.macro 拉数
    if symbol == "index" and label:
        from adapters.macro.engine import _fetch_global_index

        bars, src = _fetch_global_index(label, limit=limit)
        return bars, label or src

    if symbol == "us_etf" and label:
        from adapters.macro.engine import _fetch_us_etf

        bars, src = _fetch_us_etf(label, limit=limit)
        return bars, label or src

    bars: List[dict] = []

    if symbol.startswith(("sh", "sz")):
        # A 股指数只走新浪 stock_zh_index_daily。
        fn = getattr(ak, "stock_zh_index_daily", None)
        if fn:
            try:
                df = fn(symbol=symbol)
                bars = normalize_bars(
                    df.to_dict(orient="records") if hasattr(df, "to_dict") else list(df)
                )
            except Exception as e:
                logger.warning("sina index daily %s: %s", symbol, e)
                bars = []
    else:
        fn = getattr(ak, "stock_hk_index_daily_sina", None)
        if fn:
            for sym in ("HSI", "hsi"):
                try:
                    df = fn(symbol=sym)
                    bars = normalize_bars(
                        df.to_dict(orient="records") if hasattr(df, "to_dict") else list(df)
                    )
                    if bars and len(bars) >= 3:
                        break
                except Exception as e:
                    logger.warning("sina hk index %s: %s", sym, e)
                    bars = []

    if bars:
        if limit:
            bars = bars[-limit:]
    else:
        logger.error("index bars empty for benchmark=%s symbol=%s", label, symbol)

    return bars, label


def default_benchmark(market: Optional[str]) -> str:
    if market == "HK":
        return "hsi"
    return "hs300"
