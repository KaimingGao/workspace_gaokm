"""组合回测数据加载：日线 + 基本面批量（P51）；经 DataService。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.research.portfolio_bars import (
    load_portfolio_stock_bars,
    should_fetch_backtest_fundamentals,
)

__all__ = [
    "should_fetch_backtest_fundamentals",
    "load_portfolio_stock_bars",
    "summarize_portfolio_backtest",
]


def summarize_portfolio_backtest(
    *,
    codes: Optional[List[str]] = None,
    lookback: int = 90,
    top_k: int = 3,
    horizon_days: int = 3,
    min_score: Optional[float] = None,
    min_predicted_score: Optional[float] = None,
) -> Dict[str, Any]:
    """每日报告用的轻量 TopK 回测摘要（ŷ 排序；规则分 min_score 不再默认 55）。"""
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

    bt_kwargs: Dict[str, Any] = {
        "top_k": top_k,
        "horizon_days": horizon_days,
        "rank_mode": "predicted_score",
        "min_predicted_score": min_predicted_score,
    }
    # 仅显式传入时才带规则分门槛，避免日报 params 残留 min_score=55
    if min_score is not None:
        bt_kwargs["min_score"] = float(min_score)

    bt = backtest_topk_equal_weight(stock_bars, **bt_kwargs)
    if not bt.get("success"):
        return bt

    metrics = bt.get("metrics") or {}
    curve = bt.get("equity_curve") or []
    attr = bt.get("attribution") or {}
    oos = bt.get("oos_summary") or {}
    regime = bt.get("regime_summary") or {}
    rb = bt.get("regime_buckets") or {}
    pit = bt.get("pit_report") or {}
    params = dict(bt.get("params") or {})
    if params.get("rank_mode") == "predicted_score":
        # 规则分 0–100 门槛不适用；保留 min_predicted_score
        params["min_score"] = None
    return {
        "success": True,
        "loaded_stocks": list(stock_bars.keys()),
        "trade_count": metrics.get("trade_count"),
        "total_return_pct": metrics.get("total_return_pct"),
        "win_rate_pct": metrics.get("win_rate_pct"),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "params": params,
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
