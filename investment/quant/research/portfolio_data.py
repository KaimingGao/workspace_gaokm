"""组合回测数据加载：日线 + 基本面批量（P51）；经 DataService。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from core.signal.config import load_signal_config
from core.signal.fundamentals_bridge import fetch_fundamentals_batch


def should_fetch_backtest_fundamentals(config: Optional[dict] = None) -> bool:
    cfg = config or load_signal_config()
    fund_cfg = cfg.get("fundamentals") or {}
    if not fund_cfg.get("enabled", True):
        return False
    return bool(fund_cfg.get("use_in_backtest", True))


def load_portfolio_stock_bars(
    candidates: List[str],
    *,
    lookback: int = 120,
    fetch_fundamentals: Optional[bool] = None,
    min_bars: Optional[int] = None,
) -> Tuple[Dict[str, List[dict]], List[str], Dict[str, dict]]:
    """拉取组合回测用日线；可选批量基本面（快照，供 value/quality）。

    min_bars：过短序列直接丢弃（默认约 lookback 的一半，且不少于 16），
    避免单票把共同交易日交集压垮。
    """
    from core.data_service import bars_and_source, get_quote

    stock_bars: Dict[str, List[dict]] = {}
    failures: List[str] = []
    sym_by_raw: Dict[str, str] = {}
    need = int(min_bars) if min_bars is not None else max(16, min(40, int(lookback or 120) // 2))

    for raw in candidates[:15]:
        quote = get_quote(str(raw))
        sym = quote.get("stock_code") if quote.get("success") else str(raw)
        bars, _ = bars_and_source(raw, limit=lookback + 35)
        if not bars and quote.get("success"):
            bars, _ = bars_and_source(str(sym), limit=lookback + 35)
        if bars and len(bars) >= need:
            stock_bars[str(sym)] = bars
            sym_by_raw[str(raw)] = str(sym)
            sym_by_raw[str(sym)] = str(sym)
        elif bars:
            failures.append(f"{raw}(日线{len(bars)}<{need})")
        else:
            failures.append(str(raw))

    fundamentals_by_code: Dict[str, dict] = {}
    use_fund = (
        should_fetch_backtest_fundamentals()
        if fetch_fundamentals is None
        else bool(fetch_fundamentals)
    )
    if use_fund and stock_bars:
        batch = fetch_fundamentals_batch(list(stock_bars.keys()))
        fundamentals_by_code.update(batch)

    return stock_bars, failures, fundamentals_by_code


def summarize_portfolio_backtest(
    *,
    codes: Optional[List[str]] = None,
    lookback: int = 90,
    top_k: int = 3,
    horizon_days: int = 3,
    min_score: float = 55.0,
) -> Dict[str, Any]:
    """每日报告用的轻量 TopK 回测摘要（原 P12.3）。"""
    from core.backtest.topk_backtest import backtest_topk_equal_weight
    from core.watching_store import read_watching

    candidates = list(codes or [])
    if not candidates:
        try:
            uni = read_watching()
            candidates = uni.get("watchlist") or []
        except FileNotFoundError:
            return {"success": False, "error": "watching.json 不存在且无 codes"}

    if len(candidates) < 2:
        return {"success": False, "error": "候选标的不足"}

    stock_bars, failures, _fund = load_portfolio_stock_bars(candidates, lookback=lookback)
    if len(stock_bars) < 2:
        return {
            "success": False,
            "error": f"有效日线不足（{len(stock_bars)}）",
            "failures": failures,
        }

    bt = backtest_topk_equal_weight(
        stock_bars,
        top_k=top_k,
        horizon_days=horizon_days,
        min_score=min_score,
    )
    if not bt.get("success"):
        return bt

    metrics = bt.get("metrics") or {}
    curve = bt.get("equity_curve") or []
    attr = bt.get("attribution") or {}
    oos = bt.get("oos_summary") or {}
    regime = bt.get("regime_summary") or {}
    rb = bt.get("regime_buckets") or {}
    pit = bt.get("pit_report") or {}
    return {
        "success": True,
        "loaded_stocks": list(stock_bars.keys()),
        "trade_count": metrics.get("trade_count"),
        "total_return_pct": metrics.get("total_return_pct"),
        "win_rate_pct": metrics.get("win_rate_pct"),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "params": bt.get("params"),
        "equity_curve_tail": curve[-12:],
        "failures": failures,
        # R4.4：日报导出可读诊断块（与页内块对齐的子集）
        "metrics": metrics,
        "attribution": {
            "ok": attr.get("ok"),
            "selection_excess_pct": attr.get("selection_excess_pct"),
            "brinson": attr.get("brinson"),
            "factor_proxy": attr.get("factor_proxy"),
            "by_sector": (attr.get("by_sector") or [])[:6],
        }
        if attr
        else None,
        "oos_summary": oos,
        "regime_summary": regime,
        "regime_buckets": rb,
        "pit_report": pit,
        "signal_fill_sample": (bt.get("signal_fill_sample") or [])[-12:],
        "cost_model": bt.get("cost_model"),
    }

