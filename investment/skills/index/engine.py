"""相对大盘：个股 vs 基准指数近 N 日收益。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from core.data_service import bars_and_source
from core.ports.market import query_quote, resolve_market_code
from skills.common.history import normalize_bars


def _pct(start: float, end: float) -> Optional[float]:
    if not start:
        return None
    return round((end / start - 1.0) * 100.0, 2)


def fetch_index_bars(benchmark: str, limit: int = 30) -> Tuple[List[dict], str]:
    """拉取指数日线。返回 (bars, label)。"""
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()

    key = (benchmark or "").strip().lower()
    mapping = {
        "hs300": ("sh000300", "沪深300"),
        "沪深300": ("sh000300", "沪深300"),
        "csi300": ("sh000300", "沪深300"),
        "上证": ("sh000001", "上证指数"),
        "上证指数": ("sh000001", "上证指数"),
        "sh": ("sh000001", "上证指数"),
        "hsi": ("HSI", "恒生指数"),
        "恒生": ("HSI", "恒生指数"),
        "恒生指数": ("HSI", "恒生指数"),
    }
    symbol, label = mapping.get(key, (None, None))
    if not symbol:
        return [], ""

    end = datetime.now()
    start = end - timedelta(days=max(90, limit * 3))
    bars: List[dict] = []

    if symbol.startswith("sh"):
        code = symbol[2:]
        # 常见接口
        for fn_name, kwargs in (
            ("index_zh_a_hist", {
                "symbol": code,
                "period": "daily",
                "start_date": start.strftime("%Y%m%d"),
                "end_date": end.strftime("%Y%m%d"),
            }),
            ("stock_zh_index_daily", {"symbol": symbol}),
        ):
            fn = getattr(ak, fn_name, None)
            if not fn:
                continue
            try:
                df = fn(**kwargs)
                bars = normalize_bars(
                    df.to_dict(orient="records") if hasattr(df, "to_dict") else list(df)
                )
                if bars:
                    break
            except Exception:
                continue
    else:
        # 恒生：多接口 + 多 symbol 形态
        for fn_name in (
            "stock_hk_index_daily_em",
            "stock_hk_index_daily_sina",
            "stock_hk_index_spot_em",
            "index_global_hist_em",
        ):
            fn = getattr(ak, fn_name, None)
            if not fn:
                continue
            for sym in ("HSI", "hsi", "恒生指数", "HSIhk"):
                try:
                    try:
                        df = fn(symbol=sym)
                    except TypeError:
                        try:
                            df = fn(symbol=sym, start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))
                        except TypeError:
                            df = fn()
                    bars = normalize_bars(
                        df.to_dict(orient="records") if hasattr(df, "to_dict") else list(df)
                    )
                    if bars and len(bars) >= 3:
                        break
                except Exception:
                    continue
            if bars and len(bars) >= 3:
                break

    if limit and bars:
        bars = bars[-limit:]
    return bars, label


def default_benchmark(market: Optional[str]) -> str:
    if market == "HK":
        return "hsi"
    return "hs300"


def build_relative(stock_code: str, benchmark: Optional[str] = None, days: int = 20) -> dict:
    days = max(5, min(int(days or 20), 60))
    market, _ = resolve_market_code(stock_code)
    bench_key = benchmark or default_benchmark(market)

    quote = query_quote(stock_code)
    name = quote.get("stock_name") if quote.get("success") else stock_code
    code = quote.get("stock_code") if quote.get("success") else stock_code

    stock_bars, stock_src = bars_and_source(stock_code, limit=days + 5)
    if not stock_bars and code and str(code) != str(stock_code):
        stock_bars, stock_src = bars_and_source(str(code), limit=days + 5)
    # 带市场前缀再试（部分接口要 sz300750）
    if not stock_bars and quote.get("success") and quote.get("market") == "CN":
        qcode = str(quote.get("stock_code") or code or "")
        if qcode.isdigit() and len(qcode) == 6:
            prefix = "sh" if qcode.startswith(("5", "6", "9")) else "sz"
            stock_bars, stock_src = bars_and_source(f"{prefix}{qcode}", limit=days + 5)
    if not stock_bars:
        from skills.common.history import fetch_a_daily_bars

        detail = ""
        err = getattr(fetch_a_daily_bars, "last_errors", None)
        if err:
            detail = f"（{' | '.join(err[-2:])}）"
        return {
            "success": False,
            "stock_code": code,
            "stock_name": name,
            "error": f"无法获取个股日线，相对强弱无法计算{detail}",
        }

    try:
        index_bars, bench_label = fetch_index_bars(bench_key, limit=days + 5)
    except Exception as e:
        return {
            "success": False,
            "stock_code": code,
            "error": f"基准指数拉取失败: {e}",
        }

    if not index_bars:
        return {
            "success": False,
            "stock_code": code,
            "error": f"未能获取基准「{bench_key}」日线（需 AkShare）",
        }

    n = min(days, len(stock_bars) - 1, len(index_bars) - 1)
    if n < 5:
        return {"success": False, "error": "可比交易日不足"}

    s0, s1 = stock_bars[-(n + 1)]["close"], stock_bars[-1]["close"]
    i0, i1 = index_bars[-(n + 1)]["close"], index_bars[-1]["close"]
    stock_ret = _pct(s0, s1)
    index_ret = _pct(i0, i1)
    if stock_ret is None or index_ret is None:
        return {"success": False, "error": "收益计算失败"}

    excess = round(stock_ret - index_ret, 2)
    if excess >= 2:
        stance = "相对偏强"
    elif excess <= -2:
        stance = "相对偏弱"
    else:
        stance = "大致同步"

    summary = (
        f"{name}({code}) 近约 {n} 日涨跌 {stock_ret:+.2f}%；"
        f"基准{bench_label or bench_key} {index_ret:+.2f}%；"
        f"超额 {excess:+.2f}%（{stance}）。"
    )

    return {
        "success": True,
        "stock_code": code,
        "stock_name": name,
        "market": market,
        "benchmark": bench_key,
        "benchmark_label": bench_label or bench_key,
        "days": n,
        "stock_return_pct": stock_ret,
        "benchmark_return_pct": index_ret,
        "excess_return_pct": excess,
        "relative_stance": stance,
        "stock_data_source": stock_src,
        "summary": summary,
        "note": "以上为相对强弱事实，市场有风险，不保证收益，不代客下单。",
    }
