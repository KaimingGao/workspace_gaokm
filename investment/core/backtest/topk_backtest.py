"""横截面 TopK 调仓回测（研究用，非模拟仓账本）。

默认等权；可选 score_budget / risk_parity_lite（与纸面同源）。
历史文件名曾为 portfolio.py；勿与 paper.json 持仓混淆。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.backtest.engine import _mock_quote_from_bars, _trade_metrics


WEIGHT_MODES = ("equal", "score_budget", "risk_parity_lite")


def _vol_from_window(bars: List[dict], *, window: int = 20) -> Optional[float]:
    closes: List[float] = []
    for b in (bars or [])[-max(window + 2, 5) :]:
        try:
            closes.append(float(b.get("close")))
        except (TypeError, ValueError):
            continue
    if len(closes) < 5:
        return None
    rets = []
    for i in range(1, len(closes)):
        a, b = closes[i - 1], closes[i]
        if a > 0 and b > 0:
            rets.append(b / a - 1.0)
    if len(rets) < 4:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
    return var**0.5


def allocate_topk_weights(
    legs: List[dict],
    *,
    weight_mode: str = "equal",
    max_position_pct: float = 40.0,
    max_sector_pct: float = 60.0,
    stock_bars: Optional[Dict[str, List[dict]]] = None,
) -> Tuple[Dict[str, float], str]:
    """为已成交腿分配目标权重（%）。失败回退等权。"""
    mode = (weight_mode or "equal").strip().lower()
    if mode not in WEIGHT_MODES:
        mode = "equal"
    n = len(legs)
    if n <= 0:
        return {}, mode
    if mode == "equal":
        w = round(100.0 / n, 4)
        return {str(leg["stock_code"]): w for leg in legs}, "equal"

    ranked: List[dict] = []
    for leg in legs:
        code = str(leg.get("stock_code") or "")
        row = {
            "stock_code": code,
            "score": float(leg.get("score") or 50.0),
            "sector": leg.get("sector") or "未知",
        }
        if mode == "risk_parity_lite" and stock_bars:
            vol = _vol_from_window(stock_bars.get(code) or [])
            if vol is not None:
                row["vol"] = vol
        ranked.append(row)

    try:
        if mode == "score_budget":
            from core.risk.budget import score_budget_weights

            weights, _, _ = score_budget_weights(
                ranked,
                max_position_pct=max_position_pct,
                max_sector_pct=max_sector_pct,
                max_positions=n,
            )
        else:
            from core.risk.budget import risk_parity_lite_weights

            weights, _, _ = risk_parity_lite_weights(
                ranked,
                max_position_pct=max_position_pct,
                max_sector_pct=max_sector_pct,
                max_positions=n,
            )
    except Exception:
        weights = {}

    # 只保留本腿；归一到 100%
    filtered = {
        str(leg["stock_code"]): float(weights.get(str(leg["stock_code"])) or 0.0)
        for leg in legs
    }
    total = sum(filtered.values())
    if total <= 1e-6:
        w = round(100.0 / n, 4)
        return {str(leg["stock_code"]): w for leg in legs}, "equal"
    return {c: round(100.0 * v / total, 4) for c, v in filtered.items() if v > 0}, mode


def _weighted_port_return(legs: List[dict], weights: Dict[str, float]) -> float:
    if not legs:
        return 0.0
    total_w = sum(float(weights.get(str(leg["stock_code"])) or 0.0) for leg in legs)
    if total_w <= 1e-9:
        return sum(float(leg.get("return_pct") or 0.0) for leg in legs) / len(legs)
    acc = 0.0
    for leg in legs:
        code = str(leg["stock_code"])
        w = float(weights.get(code) or 0.0)
        acc += (w / total_w) * float(leg.get("return_pct") or 0.0)
    return acc


def _bars_by_date(bars: List[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for b in bars or []:
        d = str(b.get("date") or "").strip()
        if d:
            out[d] = b
    return out


def _common_dates(stock_bars: Dict[str, List[dict]]) -> List[str]:
    common: Optional[set] = None
    for bars in stock_bars.values():
        keys = set(_bars_by_date(bars).keys())
        common = keys if common is None else common & keys
    return sorted(common or [])


def _filter_stock_bars_for_calendar(
    stock_bars: Dict[str, List[dict]],
    *,
    min_bars: int,
) -> Tuple[Dict[str, List[dict]], List[Dict[str, Any]]]:
    """剔除日线过短的票，避免单票把共同交易日交集压垮。"""
    usable: Dict[str, List[dict]] = {}
    dropped: List[Dict[str, Any]] = []
    need = max(2, int(min_bars))
    for code, bars in (stock_bars or {}).items():
        n = len(_bars_by_date(bars))
        if n >= need:
            usable[str(code)] = bars
        else:
            dropped.append(
                {
                    "stock_code": str(code),
                    "bars": n,
                    "reason": f"日线不足 {n}<{need}，已排除以免压垮共同交易日",
                }
            )
    return usable, dropped


def apply_topk_dropout(
    picks: List[Tuple[str, float]],
    prev_codes: Sequence[str],
    *,
    top_k: int,
    dropout_n: int,
) -> List[Tuple[str, float]]:
    """TopK-Dropout：掉出 K+N 才卖；冲进前 max(K-N,1) 才新买；目标持仓约 K。"""
    k = max(1, int(top_k))
    n_buf = max(0, int(dropout_n or 0))
    if n_buf <= 0 or not picks:
        return list(picks[:k])

    score_by = {str(c): float(s) for c, s in picks}
    rank = {str(c): i for i, (c, _) in enumerate(picks)}
    keep_rank = k + n_buf  # 0-based: keep if rank < keep_rank
    enter_rank = max(1, k - n_buf)

    selected: List[Tuple[str, float]] = []
    selected_set = set()
    # 先保留仍在缓冲区内的旧持仓
    for code in prev_codes:
        c = str(code)
        r = rank.get(c)
        if r is None:
            continue
        if r < keep_rank and c not in selected_set:
            selected.append((c, score_by[c]))
            selected_set.add(c)
        if len(selected) >= k:
            break
    # 再从顶尖补齐新票
    for c, s in picks:
        if len(selected) >= k:
            break
        if c in selected_set:
            continue
        if rank[c] < enter_rank:
            selected.append((c, s))
            selected_set.add(c)
    # 若仍不足（缓冲导致空档），按排名补满到 K
    if len(selected) < k:
        for c, s in picks:
            if len(selected) >= k:
                break
            if c not in selected_set:
                selected.append((c, s))
                selected_set.add(c)
    return selected


def _window_for_code(
    code: str,
    dates: List[str],
    date_maps: Dict[str, Dict[str, dict]],
    end_idx: int,
    max_window: int,
) -> List[dict]:
    """决策日 end_idx 及以前，最多 max_window 根（PIT as_of）。"""
    start = max(0, end_idx - max_window + 1)
    dm = date_maps.get(code) or {}
    return [dm[d] for d in dates[start : end_idx + 1] if d in dm]


def backtest_topk_equal_weight(
    stock_bars: Dict[str, List[dict]],
    *,
    top_k: int = 3,
    horizon_days: int = 3,
    min_score: float = 55.0,
    min_history: int = 12,
    max_window: int = 30,
    apply_costs: bool = False,
    cost_config: Optional[dict] = None,
    neutralize: Optional[bool] = None,
    fundamentals_by_code: Optional[Dict[str, dict]] = None,
    execution_mode: str = "next_open",
    respect_limit: bool = True,
    slippage_tier: Optional[str] = None,
    exit_max_defer: int = 3,
    weight_mode: str = "equal",
    max_position_pct: float = 40.0,
    max_sector_pct: float = 60.0,
    dropout_n: int = 0,
) -> Dict[str, Any]:
    """
    多票横截面：每个调仓日对 watching 打分，持有 TopK，持有 horizon_days。
    weight_mode: equal | score_budget | risk_parity_lite（与纸面 optimize 同源）。
    dropout_n>0 时启用 TopK-Dropout（K±N 缓冲）。
    """
    from core.backtest.attribution import attribute_portfolio_trades
    from core.backtest.matching import (
        apply_match_filters,
        cost_config_for_slippage_tier,
        resolve_exit_index,
    )
    from core.data_pit import pit_report_for_backtest
    from core.portfolio_optimize import _sector_for, load_sector_map
    from core.signal.config import load_signal_config
    from core.signal.cross_section_batch import score_and_rank_watching, score_window_as_item

    if not stock_bars:
        return {"success": False, "error": "无标的日线"}

    cfg = load_signal_config()
    cs_cfg = cfg.get("cross_section") or {}
    use_neutral = cs_cfg.get("neutralize", True) if neutralize is None else bool(neutralize)
    dropout_n = max(0, min(int(dropout_n or 0), 10))

    top_k = max(1, min(int(top_k or 3), 10))
    horizon_days = max(1, min(int(horizon_days or 3), 10))
    min_score = float(min_score or 55.0)
    min_history = max(5, int(min_history or 12))
    defer_cap = max(0, int(exit_max_defer or 0))
    mode = (execution_mode or "next_open").strip().lower()
    if mode not in ("close", "next_open"):
        mode = "next_open"
    if slippage_tier:
        cost_config = cost_config_for_slippage_tier(slippage_tier, base=cost_config)

    # next_open 需要信号日后再留 1 + horizon + 跌停延后
    extra = 1 if mode == "next_open" else 0
    need = min_history + horizon_days + extra + defer_cap

    stock_bars, dropped_thin = _filter_stock_bars_for_calendar(stock_bars, min_bars=need)
    if len(stock_bars) < 2:
        return {
            "success": False,
            "error": f"有效日线标的不足（{len(stock_bars)}，已排除短序列 {len(dropped_thin)} 只）",
            "dropped_stocks": dropped_thin,
            "common_dates": 0,
        }

    date_maps = {code: _bars_by_date(bars) for code, bars in stock_bars.items()}
    dates = _common_dates(stock_bars)
    n = len(dates)
    if n < need:
        return {
            "success": False,
            "error": f"共同交易日不足（{n} 根，需要 {need}+）",
            "common_dates": n,
            "dropped_stocks": dropped_thin,
            "loaded_stocks": list(stock_bars.keys()),
        }

    returns: List[float] = []
    trades: List[dict] = []
    equity_curve: List[dict] = []
    equity = 100.0
    neutralized_rebalances = 0
    skipped_limit = 0
    skipped_limit_exit = 0
    exit_deferred = 0
    pit_windows = 0
    fund_resolves: List[dict] = []
    impact_cost_sum_bps = 0.0
    impact_legs = 0
    turnover_cost_sum_pct = 0.0
    prev_codes: List[str] = []
    prev_weights: Dict[str, float] = {}
    resolved_weight_mode = (weight_mode or "equal").strip().lower()
    if resolved_weight_mode not in WEIGHT_MODES:
        resolved_weight_mode = "equal"
    signal_fill_sample: List[dict] = []
    smap = load_sector_map()
    fund_cfg = cfg.get("fundamentals") or {}
    use_fund_pit = bool(fund_cfg.get("enabled", True)) and bool(
        fund_cfg.get("use_in_backtest", True)
    )
    pit_mode = str(fund_cfg.get("pit_mode") or "as_of").strip().lower()
    i = min_history - 1
    if dates:
        equity_curve.append({"date": dates[i], "equity": equity, "return_pct": 0.0})
    last_signal_i = n - horizon_days - extra - defer_cap - 1
    while i <= last_signal_i:
        entries: List[dict] = []
        signal_as_of = dates[i]
        for code in stock_bars:
            window = _window_for_code(code, dates, date_maps, i, max_window)
            pit_windows += 1
            if len(window) < 2:
                continue
            quote = _mock_quote_from_bars(window, len(window) - 1)
            fund = None
            if use_fund_pit:
                if pit_mode == "as_of":
                    try:
                        from core.fundamentals_pit import resolve_fundamentals_for_score

                        resolved = resolve_fundamentals_for_score(
                            code,
                            as_of=signal_as_of,
                            fund_cfg=fund_cfg,
                            live_fallback=False,
                        )
                        fund_resolves.append(resolved)
                        fund = resolved.get("metrics")
                    except Exception:
                        fund = None
                else:
                    fund = (fundamentals_by_code or {}).get(code)
            item = score_window_as_item(
                code,
                window,
                horizon_days=horizon_days,
                quote=quote,
                config=cfg,
                fundamentals=fund,
            )
            if item:
                entries.append(item)

        picks, neut_meta = score_and_rank_watching(
            entries,
            min_score=min_score,
            config=cfg,
            neutralize=use_neutral,
        )
        if neut_meta.get("applied"):
            neutralized_rebalances += 1
        selected = apply_topk_dropout(
            picks, prev_codes, top_k=top_k, dropout_n=dropout_n
        )
        if not selected:
            i += 1
            continue

        if mode == "next_open":
            entry_date = dates[i + 1]
            planned_exit_date = dates[i + 1 + horizon_days]
        else:
            entry_date = dates[i]
            planned_exit_date = dates[i + horizon_days]

        leg_returns: List[float] = []
        legs: List[dict] = []
        leg_exit_dates: List[str] = []
        signal_date = dates[i]
        for code, score in selected:
            dm = date_maps[code]
            entry_bar = dm.get(entry_date)
            if not entry_bar:
                continue

            # 涨跌停过滤：用该标的按日期序的 bars 序列
            code_bars = stock_bars.get(code) or []
            code_dates = [str(b.get("date") or "") for b in code_bars]
            try:
                match_i = code_dates.index(entry_date)
            except ValueError:
                match_i = -1
            if match_i >= 0:
                match = apply_match_filters(
                    want_buy=True,
                    entry_bars=code_bars,
                    entry_index=match_i,
                    respect_limit=respect_limit,
                    stock_code=code,
                )
                if match.get("blocked"):
                    skipped_limit += 1
                    # R4.2：跳过也进对照样本
                    intent = entry_bar.get("open") or entry_bar.get("close")
                    signal_fill_sample.append(
                        {
                            "signal_date": signal_date,
                            "entry_date": entry_date,
                            "stock_code": code,
                            "score": score,
                            "intent_price": round(float(intent), 4) if intent else None,
                            "fill_price": None,
                            "exit_price": None,
                            "return_pct": None,
                            "skipped_limit": True,
                            "skipped_limit_exit": False,
                            "exit_deferred_days": 0,
                            "execution_mode": mode,
                            "status": "skipped_limit_entry",
                        }
                    )
                    continue

            try:
                planned_exit_i = code_dates.index(planned_exit_date)
            except ValueError:
                planned_exit_i = -1
            if planned_exit_i < 0:
                continue
            exit_res = resolve_exit_index(
                code_bars,
                planned_exit_i,
                respect_limit=respect_limit,
                stock_code=code,
                max_defer=defer_cap,
            )
            if exit_res.get("skipped") or not exit_res.get("ok"):
                skipped_limit_exit += 1
                intent = (
                    entry_bar.get("open")
                    if mode == "next_open"
                    else entry_bar.get("close")
                ) or entry_bar.get("close")
                signal_fill_sample.append(
                    {
                        "signal_date": signal_date,
                        "entry_date": entry_date,
                        "stock_code": code,
                        "score": score,
                        "intent_price": round(float(intent), 4) if intent else None,
                        "fill_price": round(float(intent), 4) if intent else None,
                        "exit_price": None,
                        "return_pct": None,
                        "skipped_limit": False,
                        "skipped_limit_exit": True,
                        "exit_deferred_days": 0,
                        "execution_mode": mode,
                        "status": "skipped_limit_exit",
                    }
                )
                continue
            if exit_res.get("deferred"):
                exit_deferred += 1
            exit_idx = int(exit_res["exit_index"])
            exit_bar = code_bars[exit_idx]
            exit_date = str(exit_bar.get("date") or planned_exit_date)

            if mode == "next_open":
                entry = entry_bar.get("open") or entry_bar.get("close")
                intent = entry_bar.get("open") or entry_bar.get("close")
            else:
                entry = entry_bar.get("close")
                intent = entry_bar.get("close")
            exit_p = exit_bar.get("close")
            if not entry:
                continue
            ret = (exit_p / entry - 1.0) * 100.0
            leg_returns.append(ret)
            leg_exit_dates.append(exit_date)
            try:
                entry_f = float(entry)
                exit_f = float(exit_p) if exit_p is not None else None
                intent_f = float(intent) if intent is not None else entry_f
            except (TypeError, ValueError):
                entry_f, exit_f, intent_f = None, None, None
            legs.append(
                {
                    "stock_code": code,
                    "score": score,
                    "return_pct": round(ret, 2),
                    "sector": _sector_for(code, smap),
                    "exit_date": exit_date,
                    "exit_deferred_days": exit_res.get("deferred_days") or 0,
                    "intent_price": round(intent_f, 4) if intent_f is not None else None,
                    "fill_price": round(entry_f, 4) if entry_f is not None else None,
                    "exit_price": round(exit_f, 4) if exit_f is not None else None,
                    "signal_date": signal_date,
                    "entry_date": entry_date,
                    "execution_mode": mode,
                }
            )
            signal_fill_sample.append(
                {
                    "signal_date": signal_date,
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "stock_code": code,
                    "score": score,
                    "intent_price": round(intent_f, 4) if intent_f is not None else None,
                    "fill_price": round(entry_f, 4) if entry_f is not None else None,
                    "exit_price": round(exit_f, 4) if exit_f is not None else None,
                    "return_pct": round(ret, 2),
                    "skipped_limit": False,
                    "skipped_limit_exit": False,
                    "exit_deferred_days": exit_res.get("deferred_days") or 0,
                    "execution_mode": mode,
                    "status": "filled",
                }
            )

        if not leg_returns:
            i += 1
            continue

        weights_pct, used_mode = allocate_topk_weights(
            legs,
            weight_mode=resolved_weight_mode,
            max_position_pct=max_position_pct,
            max_sector_pct=max_sector_pct,
            stock_bars=stock_bars,
        )
        for leg in legs:
            code = str(leg.get("stock_code") or "")
            leg["weight_pct"] = round(float(weights_pct.get(code) or 0.0), 4)
            leg["weight_mode"] = used_mode

        port_ret = _weighted_port_return(legs, weights_pct)
        gross_ret = port_ret
        curr_codes = [str(leg.get("stock_code") or "") for leg in legs if leg.get("stock_code")]
        curr_weights = {
            str(leg["stock_code"]): float(leg.get("weight_pct") or 0.0) for leg in legs
        }
        cost_pct = 0.0
        if apply_costs:
            from core.backtest.costs import (
                estimate_impact_cost,
                rebalance_cost_pct,
            )

            sample_code = (legs[0].get("stock_code") if legs else None) or ""
            sample_bars = stock_bars.get(sample_code) or []
            entry_bar = (date_maps.get(sample_code) or {}).get(entry_date) or {}
            try:
                px = float(entry_bar.get("close") or entry_bar.get("open") or 0)
                vol = float(entry_bar.get("volume") or 0)
            except (TypeError, ValueError):
                px, vol = 0.0, 0.0
            order_value = float(equity) / max(1, len(leg_returns)) * 1000.0
            daily_turnover = px * vol if px > 0 and vol > 0 else 0.0
            impact_bps = estimate_impact_cost(
                order_value, daily_turnover, config=cost_config
            )
            if impact_bps > 0:
                impact_cost_sum_bps += impact_bps
                impact_legs += 1
            cost_pct = rebalance_cost_pct(
                prev_codes,
                curr_codes,
                config=cost_config,
                bars=sample_bars[-40:] if sample_bars else None,
                order_value=order_value,
                daily_volume=daily_turnover,
                prev_weights=prev_weights,
                curr_weights=curr_weights,
            )
            turnover_cost_sum_pct += cost_pct
            port_ret = round(gross_ret - cost_pct, 4)

        prev_codes = list(curr_codes)
        prev_weights = dict(curr_weights)

        # 组合退出日取各腿最晚卖出日（研究近似）
        exit_date = max(leg_exit_dates) if leg_exit_dates else planned_exit_date

        returns.append(port_ret)
        equity *= 1.0 + port_ret / 100.0
        trades.append(
            {
                "signal_date": dates[i],
                "entry_date": entry_date,
                "exit_date": exit_date,
                "hold_days": horizon_days,
                "return_pct": round(port_ret, 2),
                "gross_return_pct": round(gross_ret, 2),
                "cost_pct": round(cost_pct, 4) if apply_costs else 0.0,
                "legs": legs,
                "top_k": len(legs),
                "neutralization_applied": bool(neut_meta.get("applied")),
                "execution_mode": mode,
                "weight_mode": used_mode,
            }
        )
        equity_curve.append(
            {
                "date": exit_date,
                "equity": round(equity, 2),
                "return_pct": round(port_ret, 2),
            }
        )
        i += horizon_days

    metrics = _trade_metrics(returns)
    strategy = "cross_section_topk_neutral" if use_neutral else "cross_section_topk"
    fund_map = fundamentals_by_code or {}
    attribution = attribute_portfolio_trades(trades)
    as_of_sample = dates[min_history - 1] if dates and min_history - 1 < n else None
    from core.fundamentals_pit import fundamentals_pit_summary

    fund_pit_meta = fundamentals_pit_summary(fund_resolves)
    pit = pit_report_for_backtest(
        as_of=as_of_sample,
        windows_checked=pit_windows,
        lookahead_violations=0,
        fundamentals_pit=bool(fund_pit_meta.get("fundamentals_pit")),
        fundamentals_meta=fund_pit_meta,
    )
    avg_impact = (
        round(impact_cost_sum_bps / impact_legs, 4) if impact_legs else 0.0
    )
    raw = {
        "success": True,
        "strategy": strategy,
        "params": {
            "top_k": top_k,
            "horizon_days": horizon_days,
            "min_score": min_score,
            "min_history": min_history,
            "apply_costs": apply_costs,
            "stock_count": len(stock_bars),
            "common_dates": n,
            "neutralize": use_neutral,
            "neutralized_rebalances": neutralized_rebalances,
            "fundamentals_used": bool(fund_map) or bool(fund_resolves),
            "fundamentals_count": len(fund_map) or len(
                {i for i, r in enumerate(fund_resolves) if r.get("ok")}
            ),
            "fundamentals_pit_mode": pit_mode if use_fund_pit else "off",
            "avg_impact_bps": avg_impact,
            "impact_legs": impact_legs,
            "cost_mode": "turnover" if apply_costs else "zero",
            "turnover_cost_sum_pct": round(turnover_cost_sum_pct, 4) if apply_costs else 0.0,
            "execution_mode": mode,
            "respect_limit": respect_limit,
            "slippage_tier": (cost_config or {}).get("slippage_tier") if cost_config else slippage_tier,
            "skipped_limit": skipped_limit,
            "skipped_limit_exit": skipped_limit_exit,
            "exit_deferred": exit_deferred,
            "exit_max_defer": defer_cap,
            "dropped_thin_count": len(dropped_thin),
            "weight_mode": resolved_weight_mode,
            "max_position_pct": float(max_position_pct),
            "max_sector_pct": float(max_sector_pct),
            "dropout_n": dropout_n,
        },
        "dropped_stocks": dropped_thin,
        "pit_report": pit,
        "attribution": attribution,
        "metrics": metrics,
        "trade_count": len(trades),
        "equity_curve": equity_curve,
        "trades": trades,
        "trades_sample": trades[-20:],
        "signal_fill_sample": signal_fill_sample[-40:],
        "note": (
            f"横截面 TopK 回测（权重={resolved_weight_mode}）"
            + ("（调仓日截面中性化）" if use_neutral else "")
            + f"；成交={mode}；涨跌停过滤={'开' if respect_limit else '关'}；"
            f"跌停卖出延后≤{defer_cap}日；板别阈值；"
            + ("成本按换手计费（续持不扣往返）；" if apply_costs else "")
            + "非交易所仿真，仅供研究。"
        ),
    }
    from core.backtest.oos_report import attach_robustness_fields

    cost_model = "simple_cn" if apply_costs else "zero"
    sample = None
    for bars in stock_bars.values():
        if bars:
            sample = bars
            break
    # D1：TopK 默认挂源审计
    try:
        from core.data_consistency import attach_source_audit

        raw = attach_source_audit(raw, codes=list(stock_bars.keys()))
    except Exception:
        pass
    return attach_robustness_fields(
        raw,
        cost_model=cost_model,
        sample_bars=sample,
    )


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
        "note": "等权聚合各票独立 walk-forward 累计收益；产品名 Top-K 回测，非真实模拟仓回放。",
    }
