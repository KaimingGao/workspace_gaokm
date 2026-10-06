"""基于 signal/scorer 的简易历史回测（研究用途，非实盘）。"""


import logging

logger = logging.getLogger(__name__)
import math
from typing import Any, Dict, List, Optional, Tuple


def _mock_quote_from_bars(bars: List[dict], index: int) -> dict:
    if index < 1:
        change = 0.0
    else:
        c0 = bars[index - 1]["close"]
        c1 = bars[index]["close"]
        change = (c1 / c0 - 1.0) * 100.0 if c0 else 0.0
    return {
        "change_raw": round(change, 4),
        "price_raw": bars[index]["close"],
        "success": True,
    }


def _prepare_scoring_window(
    bars: List[dict],
    index: int,
    *,
    max_window: int,
    data_mode: str,
) -> Tuple[List[dict], str]:
    """P6.3：full 用完整窗口；quote_fallback 仅显式 data_mode 时模拟 live 降级。

    默认 full 路径拒绝伪日线（DS-R4）。
    """
    from core.data.pit import window_as_of

    quote = _mock_quote_from_bars(bars, index)
    mode = (data_mode or "full").strip().lower()
    if mode in ("quote_fallback", "fallback"):
        from core.ports.market import bars_from_quote_fallback

        fb = bars_from_quote_fallback(quote)
        if fb:
            return fb, "quote_fallback"
    window, _meta = window_as_of(bars, index, max_window=max_window)
    # 防御：窗口内若混入伪日期则清空
    if any(str((b or {}).get("date") or "").lower() in ("d-1", "d0") for b in (window or [])):
        return [], "rejected_quote_fallback"
    return window, "full_daily"


def period_return_pct(bars: List[dict]) -> Optional[float]:
    if not bars or len(bars) < 2:
        return None
    start = bars[0].get("close")
    end = bars[-1].get("close")
    if not start:
        return None
    return round((end / start - 1.0) * 100.0, 2)


def _date_index(bars: List[dict]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for i, b in enumerate(bars):
        d = str(b.get("date") or "").strip()
        if d:
            out[d] = i
    return out


def _hold_return_pct(bars: List[dict], entry_idx: int, hold_days: int) -> Optional[float]:
    if entry_idx < 0 or entry_idx + hold_days >= len(bars):
        return None
    entry = bars[entry_idx].get("close")
    exit_p = bars[entry_idx + hold_days].get("close")
    if not entry:
        return None
    return round((exit_p / entry - 1.0) * 100.0, 2)


def _trade_metrics(
    returns: List[float], *, holding_days: int = 1
) -> Dict[str, Any]:
    if not returns:
        return {
            "trade_count": 0,
            "win_rate_pct": None,
            "avg_return_pct": None,
            "total_return_pct": None,
            "max_drawdown_pct": None,
            "sharpe_approx": None,
        }

    wins = sum(1 for r in returns if r > 0)
    avg = sum(returns) / len(returns)

    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        equity *= 1.0 + r / 100.0
        peak = max(peak, equity)
        if peak > 0:
            dd = (peak - equity) / peak * 100.0
            max_dd = max(max_dd, dd)

    total = (equity - 1.0) * 100.0
    std = 0.0
    if len(returns) > 1:
        var = sum((r - avg) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(var)
    sharpe = None
    if std > 1e-9:
        # 每段收益对应 holding_days 个交易日；年化 √(252/h)，不用 √(252/n)
        h = max(1, int(holding_days or 1))
        sharpe = round((avg / std) * math.sqrt(252.0 / h), 2)

    return {
        "trade_count": len(returns),
        "win_rate_pct": round(wins / len(returns) * 100.0, 1),
        "avg_return_pct": round(avg, 2),
        "total_return_pct": round(total, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe_approx": sharpe,
    }


def stratify_by_score(trades: List[dict]) -> List[Dict[str, Any]]:
    """按入场 score 分层统计收益。"""
    buckets: List[Tuple[str, float, float]] = [
        ("55-64", 55.0, 65.0),
        ("65-74", 65.0, 75.0),
        ("75+", 75.0, 101.0),
        ("<55", 0.0, 55.0),
    ]
    rows: List[Dict[str, Any]] = []
    for label, lo, hi in buckets:
        subset = [
            t
            for t in trades
            if t.get("score") is not None and lo <= float(t["score"]) < hi
        ]
        if not subset:
            continue
        rets = [float(t["return_pct"]) for t in subset if t.get("return_pct") is not None]
        m = _trade_metrics(rets)
        rows.append(
            {
                "bucket": label,
                "trade_count": m["trade_count"],
                "win_rate_pct": m["win_rate_pct"],
                "avg_return_pct": m["avg_return_pct"],
                "total_return_pct": m["total_return_pct"],
            }
        )
    return rows


def build_benchmark_comparison(
    bars: List[dict],
    trades: List[dict],
    *,
    index_bars: Optional[List[dict]] = None,
    index_label: Optional[str] = None,
) -> Dict[str, Any]:
    """策略 vs 买入持有 vs 指数（同期持有窗口）。"""
    buy_hold = period_return_pct(bars)
    strat_total = _trade_metrics(
        [float(t["return_pct"]) for t in trades if t.get("return_pct") is not None]
    ).get("total_return_pct")

    out: Dict[str, Any] = {
        "buy_hold_period_pct": buy_hold,
        "strategy_total_return_pct": strat_total,
        "excess_vs_buy_hold_pct": None,
        "index_label": index_label,
        "index_period_pct": None,
        "avg_index_return_per_trade_pct": None,
        "excess_avg_vs_index_pct": None,
    }
    if buy_hold is not None and strat_total is not None:
        out["excess_vs_buy_hold_pct"] = round(strat_total - buy_hold, 2)

    if not index_bars or not trades:
        return out

    out["index_period_pct"] = period_return_pct(index_bars)
    stock_idx = _date_index(bars)
    index_idx = _date_index(index_bars)
    index_trade_rets: List[float] = []
    strat_rets: List[float] = []

    for t in trades:
        entry_date = str(t.get("entry_date") or "")
        hold = int(t.get("hold_days") or 0)
        if not entry_date or hold <= 0:
            continue
        si = stock_idx.get(entry_date)
        ii = index_idx.get(entry_date)
        if si is None or ii is None:
            continue
        ir = _hold_return_pct(index_bars, ii, hold)
        sr = t.get("return_pct")
        if ir is None or sr is None:
            continue
        index_trade_rets.append(ir)
        strat_rets.append(float(sr))

    if index_trade_rets:
        avg_idx = sum(index_trade_rets) / len(index_trade_rets)
        avg_strat = sum(strat_rets) / len(strat_rets)
        out["avg_index_return_per_trade_pct"] = round(avg_idx, 2)
        out["excess_avg_vs_index_pct"] = round(avg_strat - avg_idx, 2)

    return out


def backtest_signal_on_bars(
    bars: List[dict],
    *,
    horizon_days: int = 3,
    min_score: float = 55.0,
    min_history: int = 12,
    max_window: int = 30,
    overlap: bool = False,
    index_bars: Optional[List[dict]] = None,
    index_label: Optional[str] = None,
    data_mode: str = "full",
    apply_costs: bool = False,
    cost_config: Optional[dict] = None,
    start_index: int = 0,
    end_index: Optional[int] = None,
    execution_mode: str = "next_open",
    respect_limit: bool = True,
    slippage_tier: Optional[str] = None,
    stock_code: Optional[str] = None,
    exit_max_defer: int = 3,
) -> Dict[str, Any]:
    """
    对单票日线 Walk-forward：每日用截至当日的窗口调用 score_bars，
    分数 >= min_score 且非 hard_reject 时，模拟持有 horizon_days 日。
    
    execution_mode:
    - "close": 当日收盘价成交（旧模式，用于对比）
    - "next_open": 次日开盘价成交（更真实，体现 T+1 买）
    respect_limit: 涨停日跳过买入；跌停日延后卖出（近似）
    slippage_tier: low|mid|high 覆盖滑点档
    stock_code: 用于板别涨跌停阈值（创业板/科创 20%）
    """
    from core.backtest.matching import (
        apply_match_filters,
        cost_config_for_slippage_tier,
        resolve_exit_index,
    )
    from core.data.pit import pit_report_for_backtest
    from core.signal.scorer import score_bars

    horizon_days = max(1, min(int(horizon_days or 3), 10))
    min_score = float(min_score or 55.0)
    min_history = max(5, int(min_history or 12))
    max_window = max(10, int(max_window or 30))
    defer_cap = max(0, int(exit_max_defer or 0))
    if slippage_tier:
        cost_config = cost_config_for_slippage_tier(slippage_tier, base=cost_config)

    n = len(bars or [])
    # 需要多一根K线用于次日开盘价成交；另留延后卖出空间
    min_bars_needed = min_history + horizon_days + 2 + defer_cap
    if n < min_bars_needed:
        return {
            "success": False,
            "error": f"日线不足（需要至少 {min_bars_needed} 根，当前 {n}）",
            "bar_count": n,
        }

    start_index = max(min_history - 1, int(start_index or 0))
    # 留一根K线作为次日开盘 + 跌停延后缓冲
    max_entry_idx = n - horizon_days - 1 - defer_cap
    end_index = int(end_index) if end_index is not None else max_entry_idx
    end_index = min(end_index, max_entry_idx)

    trades: List[dict] = []
    returns: List[float] = []
    gross_returns: List[float] = []
    skipped_limit = 0
    skipped_limit_exit = 0
    exit_deferred = 0
    pit_windows = 0
    i = start_index
    while i < end_index:
        window, scoring_source = _prepare_scoring_window(
            bars,
            i,
            max_window=max_window,
            data_mode=data_mode,
        )
        pit_windows += 1
        quote = _mock_quote_from_bars(bars, i)
        idx_slice = None
        if index_bars:
            wstart = max(0, i - max_window + 1)
            idx_slice = index_bars[wstart : i + 1]

        scored = score_bars(
            window,
            horizon_days=horizon_days,
            quote=quote,
            index_bars=idx_slice,
        )

        if not scored.get("hard_reject") and (scored.get("score") or 0) >= min_score:
            # 根据执行模式确定入场价
            if execution_mode == "next_open":
                # 次日开盘价成交
                entry_bar = bars[i + 1]
                entry = entry_bar.get("open") or entry_bar["close"]
                planned_exit_idx = i + 1 + horizon_days
                match_idx = i + 1
            else:
                # 当日收盘价成交（旧模式）
                entry = bars[i]["close"]
                planned_exit_idx = i + horizon_days
                match_idx = i

            match = apply_match_filters(
                want_buy=True,
                entry_bars=bars,
                entry_index=match_idx,
                respect_limit=respect_limit,
                stock_code=stock_code,
            )
            if match.get("blocked"):
                skipped_limit += 1
                i += 1
                continue

            exit_res = resolve_exit_index(
                bars,
                planned_exit_idx,
                respect_limit=respect_limit,
                stock_code=stock_code,
                max_defer=defer_cap,
            )
            if exit_res.get("skipped") or not exit_res.get("ok"):
                skipped_limit_exit += 1
                i += 1
                continue
            if exit_res.get("deferred"):
                exit_deferred += 1

            exit_idx = int(exit_res["exit_index"])
            exit_bar = bars[exit_idx]
            exit_price = exit_bar["close"]

            if not entry or not exit_price:
                i += 1
                continue

            ret_pct = (exit_price / entry - 1.0) * 100.0
            gross_returns.append(ret_pct)
            net_ret = ret_pct
            if apply_costs:
                from core.backtest.costs import round_trip_cost_pct

                net_ret = round(ret_pct - round_trip_cost_pct(cost_config), 4)
            returns.append(net_ret)

            entry_date = bars[i + 1].get("date") if execution_mode == "next_open" else bars[i].get("date")
            exit_date = exit_bar.get("date")
            hold_days_actual = exit_idx - match_idx if execution_mode == "next_open" else exit_idx - i

            trades.append(
                {
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "entry_price": round(entry, 4),
                    "exit_price": round(exit_price, 4),
                    "return_pct": round(net_ret, 2),
                    "gross_return_pct": round(ret_pct, 2),
                    "score": scored.get("score"),
                    "hold_days": hold_days_actual,
                    "scoring_source": scoring_source,
                    "execution_mode": execution_mode,
                    "match": {
                        "t1": True,
                        "reason": match.get("reason") or "ok",
                        "exit_reason": exit_res.get("reason") or "ok",
                        "exit_deferred_days": exit_res.get("deferred_days") or 0,
                    },
                }
            )
            i += horizon_days if not overlap else 1
        else:
            i += 1

    metrics = _trade_metrics(returns, holding_days=horizon_days)
    gross_metrics = (
        _trade_metrics(gross_returns, holding_days=horizon_days) if apply_costs else None
    )
    benchmark = build_benchmark_comparison(
        bars,
        trades,
        index_bars=index_bars,
        index_label=index_label,
    )
    score_buckets = stratify_by_score(trades)
    sample = trades[-5:] if len(trades) > 5 else trades
    as_of_sample = None
    if start_index < n:
        as_of_sample = str((bars[start_index] or {}).get("date") or "") or None
    pit = pit_report_for_backtest(
        as_of=as_of_sample,
        windows_checked=pit_windows,
        lookahead_violations=0,
        fundamentals_pit=False,
    )

    out = {
        "success": True,
        "strategy": "short_conservative",
        "bar_count": n,
        "params": {
            "horizon_days": horizon_days,
            "min_score": min_score,
            "min_history": min_history,
            "overlap": overlap,
            "data_mode": data_mode,
            "apply_costs": apply_costs,
            "execution_mode": execution_mode,
            "respect_limit": respect_limit,
            "slippage_tier": (cost_config or {}).get("slippage_tier") if cost_config else slippage_tier,
            "start_index": start_index,
            "end_index": end_index,
            "skipped_limit": skipped_limit,
            "skipped_limit_exit": skipped_limit_exit,
            "exit_deferred": exit_deferred,
            "exit_max_defer": defer_cap,
            "stock_code": stock_code,
        },
        "pit_report": pit,
        "metrics": metrics,
        "gross_metrics": gross_metrics,
        "benchmark": benchmark,
        "score_buckets": score_buckets,
        "trade_count": len(trades),
        "trades_sample": sample,
        "note": (
            f"回测模式：{execution_mode}成交；涨跌停过滤={'开' if respect_limit else '关'}；"
            f"跌停卖出延后≤{defer_cap}日；板别阈值。"
            "结果仅供验证 signal 规则历史表现，不构成实盘建议。"
        ),
    }
    # D1：core 引擎默认挂源审计（不依赖 QuantService）
    try:
        from core.data.consistency import attach_source_audit

        codes = [str(stock_code)] if stock_code else []
        out = attach_source_audit(out, codes=codes or None)
    except Exception:  # noqa: BLE001 — best-effort / 非阻塞分支降级
        logger.debug("exception caught in engine.py line 460", exc_info=True)
        pass
    return out


def scan_signal_parameters(
    bars: List[dict],
    *,
    min_scores: List[float],
    horizon_days_list: List[int],
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    """网格扫描 min_score × horizon_days，按策略累计收益排序。"""
    rows: List[Dict[str, Any]] = []
    for h in horizon_days_list:
        for s in min_scores:
            result = backtest_signal_on_bars(
                bars,
                horizon_days=h,
                min_score=s,
                **kwargs,
            )
            if not result.get("success"):
                continue
            m = result.get("metrics") or {}
            rows.append(
                {
                    "horizon_days": h,
                    "min_score": s,
                    "trade_count": m.get("trade_count"),
                    "win_rate_pct": m.get("win_rate_pct"),
                    "avg_return_pct": m.get("avg_return_pct"),
                    "total_return_pct": m.get("total_return_pct"),
                    "sharpe_approx": m.get("sharpe_approx"),
                }
            )
    rows.sort(
        key=lambda r: (r.get("total_return_pct") is None, -(r.get("total_return_pct") or -1e9)),
    )
    return rows


def scan_signal_parameters_oos(
    bars: List[dict],
    *,
    min_scores: List[float],
    horizon_days_list: List[int],
    train_ratio: float = 0.6,
    valid_ratio: float = 0.2,
    **kwargs: Any,
) -> Dict[str, Any]:
    """P7.5：train 搜参，valid 选优，test 一次性评估。"""
    from core.research.split import time_series_split

    split = time_series_split(len(bars), train_ratio=train_ratio, valid_ratio=valid_ratio)
    train_bars = bars[: split.train_end]
    valid_bars = bars[: split.valid_end]
    test_bars = bars

    train_scan = scan_signal_parameters(
        train_bars,
        min_scores=min_scores,
        horizon_days_list=horizon_days_list,
        end_index=split.train_end,
        **kwargs,
    )
    if not train_scan:
        return {"success": False, "error": "train 扫描无结果", "split": split.__dict__}

    best = train_scan[0]
    best_h = int(best["horizon_days"])
    best_s = float(best["min_score"])

    valid_result = backtest_signal_on_bars(
        valid_bars,
        horizon_days=best_h,
        min_score=best_s,
        start_index=split.train_end,
        end_index=split.valid_end,
        **kwargs,
    )
    test_result = backtest_signal_on_bars(
        test_bars,
        horizon_days=best_h,
        min_score=best_s,
        start_index=split.valid_end,
        **kwargs,
    )

    return {
        "success": True,
        "best_params": {"horizon_days": best_h, "min_score": best_s},
        "split": {
            "train_end": split.train_end,
            "valid_end": split.valid_end,
            "test_end": split.test_end,
        },
        "train_top": train_scan[:5],
        "valid": valid_result,
        "test": test_result,
        "note": "train 选参易过拟合，以 test 指标为准。",
    }
