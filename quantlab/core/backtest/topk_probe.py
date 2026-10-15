"""研究 TopK 探针门面（权建议 OOS / WF / BS 入口）。

默认走轻量 ``oos_topk_curve``。完整引擎仅 ``full_engine=True``
（实现已归档至 ``core.backtest.legacy``）。产品回测勿用本模块。
"""

from __future__ import annotations

from typing import Any, Dict, List


def run_oos_topk_curve(
    stock_bars: Dict[str, List[dict]],
    **kwargs: Any,
) -> Dict[str, Any]:
    """权建议 OOS / WF 默认入口：轻量等权 TopK 权益曲线。"""
    from core.backtest.topk_oos_curve import run_oos_topk_curve as _run

    return _run(stock_bars, **kwargs)


def run_topk_probe(
    stock_bars: Dict[str, List[dict]],
    **kwargs: Any,
) -> Dict[str, Any]:
    """研究探针入口。默认轻量曲线；``full_engine=True`` 才走归档完整引擎。"""
    full = bool(kwargs.pop("full_engine", False))
    if not full:
        return run_oos_topk_curve(stock_bars, **kwargs)
    from core.backtest.legacy import topk_backtest as tb

    kwargs.setdefault("probe_lite", True)
    return tb.backtest_topk_equal_weight(stock_bars, **kwargs)


def aggregate_stock_backtests(results: List[dict]) -> Dict[str, Any]:
    """将多票单策略回测结果等权聚合（非真实组合调仓）。"""
    ok_rows = [r for r in results if r.get("success")]
    if not ok_rows:
        return {
            "success": False,
            "error": "无有效单票回测结果",
        }

    totals = []
    win_rates = []
    trade_counts = []
    for row in ok_rows:
        m = row.get("metrics") or {}
        if m.get("total_return_pct") is not None:
            totals.append(float(m["total_return_pct"]))
        if m.get("win_rate_pct") is not None:
            win_rates.append(float(m["win_rate_pct"]))
        trade_counts.append(int(m.get("trade_count") or 0))

    avg_total = sum(totals) / len(totals) if totals else None
    avg_win = sum(win_rates) / len(win_rates) if win_rates else None

    return {
        "success": True,
        "stocks": len(ok_rows),
        "avg_total_return_pct": round(avg_total, 2) if avg_total is not None else None,
        "avg_win_rate_pct": round(avg_win, 2) if avg_win is not None else None,
        "total_trades": sum(trade_counts),
        "note": (
            "引擎=single_stock_wf_agg：等权聚合各票独立 walk-forward 累计收益；"
            "产品名含 Top-K 字样，非组合调仓、非 paper_replay 纸面回放。"
        ),
        "params": {"engine": "single_stock_wf_agg"},
    }
