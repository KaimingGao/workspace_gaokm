"""做 T 回测研究封装（供 QuantService / CLI）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from core.t0.backtest import backtest_t0_on_bars, derive_t0_quality_metrics


# 东财分钟接口偶发挂死；多持仓串行时会把整次「做T回测」拖成无响应。
_MINUTE_FETCH_TIMEOUT_SEC = 5.0


def _fetch_minute_by_date(
    code: str,
    *,
    period: str = "5",
    lookback_days: int = 90,
    timeout_sec: float = _MINUTE_FETCH_TIMEOUT_SEC,
) -> Tuple[Dict[str, List[dict]], Dict[str, Any]]:
    """返回 (minute_by_date, meta)；失败则 ({}, meta)。"""
    try:
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
        from core.ports.market import fetch_minute_bars, group_minute_bars_by_date

        def _load():
            return fetch_minute_bars(
                code, period=period, lookback_days=lookback_days, use_cache=True
            )

        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_load)
            try:
                bars, meta = fut.result(timeout=max(1.0, float(timeout_sec or 5.0)))
            except FuturesTimeout:
                return {}, {
                    "ok": False,
                    "error": f"minute fetch timeout ({timeout_sec}s)",
                    "period": period,
                }
        if not bars:
            return {}, meta or {"ok": False}
        return group_minute_bars_by_date(bars), meta
    except Exception as e:
        return {}, {"ok": False, "error": str(e), "period": period}


def run_t0_backtest_for_code(
    code: str = "茅台",
    *,
    lookback: int = 30,
    initial_shares: float = 1000,
    initial_cost: Optional[float] = None,
    rules: Optional[dict] = None,
    compare_optimistic: bool = True,
    use_minute: bool = True,
    compare_daily: bool = True,
    compare_no_t0: bool = True,
) -> Dict[str, Any]:
    from core.data_service import bars_and_source as fetch_daily_bars
    from core.data_service import get_quote

    quote = get_quote(code)
    sym = quote.get("stock_code") if quote.get("success") else code
    bars, src = fetch_daily_bars(code, limit=lookback + 5)
    if not bars and quote.get("success"):
        bars, src = fetch_daily_bars(sym, limit=lookback + 5)
    if not bars:
        return {"success": False, "error": f"无法获取 {code} 日线", "task": "t0_backtest"}

    cost = float(
        initial_cost
        if initial_cost is not None
        else (bars[0].get("close") or 0)
    )
    from core.execution import resolve_t0_rules, strip_execution_meta

    want_minute = bool(use_minute)
    resolved = resolve_t0_rules(
        rules=rules, channel="backtest", has_minute=want_minute
    )
    bt_rules = strip_execution_meta(resolved)
    exec_meta = resolved.get("_execution_meta")

    minute_by_date = None
    minute_meta: Dict[str, Any] = {}

    if want_minute:
        period = str(bt_rules.get("minute_period") or "5")
        # 东财 5m 约近 120 交易日；与 lookback 对齐截断
        span = min(max(int(lookback or 90), 20), 120)
        minute_by_date, minute_meta = _fetch_minute_by_date(
            str(sym), period=period, lookback_days=span
        )
        if not minute_by_date:
            minute_by_date = None
            if not rules or "path_mode" not in (rules or {}):
                resolved = resolve_t0_rules(
                    rules=rules, channel="backtest", has_minute=False
                )
                bt_rules = strip_execution_meta(resolved)
                exec_meta = resolved.get("_execution_meta")

    report = backtest_t0_on_bars(
        bars,
        initial_shares=initial_shares,
        initial_cost=cost,
        rules=bt_rules,
        stock_code=str(sym),
        compare_optimistic=compare_optimistic,
        minute_by_date=minute_by_date,
        compare_daily=bool(compare_daily and minute_by_date),
    )
    report["data_source"] = src
    report["stock_name"] = quote.get("stock_name") if quote.get("success") else None
    report["use_minute"] = bool(minute_by_date)
    report["minute_meta"] = {
        "period": minute_meta.get("period") or bt_rules.get("minute_period"),
        "data_source": minute_meta.get("data_source"),
        "from_cache": minute_meta.get("from_cache"),
        "bar_count": minute_meta.get("bar_count"),
        "date_min": minute_meta.get("date_min"),
        "date_max": minute_meta.get("date_max"),
        "covered_days": len(minute_by_date or {}),
        "error": minute_meta.get("error"),
    }
    if exec_meta:
        report["execution"] = {
            "effective_hash": exec_meta.get("effective_hash"),
            "t0_sources": exec_meta.get("t0_sources"),
            "notes": exec_meta.get("notes"),
            "summary": exec_meta.get("summary"),
            "channel": "backtest",
        }
    report["rules"] = {
        "direction": bt_rules.get("direction"),
        "path_mode": bt_rules.get("path_mode"),
        "fill_mode": bt_rules.get("fill_mode"),
        "t0_ratio": bt_rules.get("t0_ratio"),
        "dir_enter": bt_rules.get("dir_enter"),
        "min_range_pct": bt_rules.get("min_range_pct"),
        "sell_trigger_pct": bt_rules.get("sell_trigger_pct"),
        "buy_trigger_pct": bt_rules.get("buy_trigger_pct"),
    }
    if compare_no_t0 and report.get("success"):
        off_rules = dict(bt_rules)
        off_rules["enabled"] = False
        hold_only = backtest_t0_on_bars(
            bars,
            initial_shares=initial_shares,
            initial_cost=cost,
            rules=off_rules,
            stock_code=str(sym),
            compare_optimistic=False,
            minute_by_date=None,
            compare_daily=False,
        )
        if hold_only.get("success"):
            t0_pnl = float(report.get("t0_pnl_with_exposure") or report.get("t0_pnl_total") or 0)
            # enabled=False → 无做T腿；用期末相对持仓市值变化作底仓参照不在此引擎内，
            # 贡献近似 = 做T净PnL（含敞口）本身
            report["no_t0_compare"] = {
                "t0_pnl_with_exposure": t0_pnl,
                "t0_contribution": round(t0_pnl, 2),
                "t0_trade_days": report.get("t0_trade_days"),
                "note": "含T轨相对「不做T」：贡献≈做T含敞口净PnL（底仓涨跌另计在持仓市值）",
            }
    return report


def run_t0_backtest_for_holdings(
    holdings: List[dict],
    *,
    lookback: int = 30,
    rules: Optional[dict] = None,
    compare_optimistic: bool = True,
    cash: float = 0.0,
    use_minute: bool = True,
    compare_daily: bool = True,
) -> Dict[str, Any]:
    """对持仓列表逐票回测并汇总（研究用，不改账本）。"""
    if not holdings:
        return {
            "success": False,
            "error": "无持仓可回测",
            "task": "t0_backtest",
            "note": "请先在模拟页建仓，或指定 code",
        }

    from core.execution import resolve_t0_rules, strip_execution_meta

    resolved = resolve_t0_rules(
        rules=rules, channel="backtest", has_minute=bool(use_minute)
    )
    cfg = strip_execution_meta(resolved)
    exec_meta = resolved.get("_execution_meta")

    per: List[Dict[str, Any]] = []
    total_pnl = 0.0
    total_exposure = 0.0
    total_trades = 0
    total_covers = 0
    total_hold_mv = 0.0
    long_pnl = 0.0
    reverse_pnl = 0.0
    long_cover = 0
    reverse_cover = 0
    minute_path_days = 0
    daily_fallback_days = 0

    for h in holdings:
        code = str(h.get("stock_code") or "").strip()
        if not code:
            continue
        shares = float(h.get("shares") or 0)
        cost = float(h.get("cost") or 0) or None
        if shares < 100:
            per.append(
                {
                    "success": False,
                    "stock_code": code,
                    "stock_name": h.get("stock_name"),
                    "error": "持仓不足 100 股",
                }
            )
            continue
        one = run_t0_backtest_for_code(
            code,
            lookback=lookback,
            initial_shares=shares,
            initial_cost=cost,
            rules=cfg,
            compare_optimistic=compare_optimistic,
            use_minute=use_minute,
            compare_daily=compare_daily,
        )
        one["stock_name"] = one.get("stock_name") or h.get("stock_name")
        per.append(one)
        if one.get("success"):
            total_pnl += float(one.get("t0_pnl_total") or 0)
            total_exposure += float(one.get("exposure_pnl_total") or 0)
            total_trades += int(one.get("t0_trade_days") or 0)
            total_covers += int(one.get("t0_cover_days") or 0)
            total_hold_mv += float(one.get("hold_mv_start") or 0)
            long_pnl += float(one.get("long_t_pnl") or 0)
            reverse_pnl += float(one.get("reverse_t_pnl") or 0)
            long_cover += int(one.get("long_t_cover_days") or 0)
            reverse_cover += int(one.get("reverse_t_cover_days") or 0)
            minute_path_days += int(one.get("minute_path_days") or 0)
            daily_fallback_days += int(one.get("daily_fallback_days") or 0)

    ok = [x for x in per if x.get("success")]
    skip_days = sum(int(x.get("skip_days") or 0) for x in ok)
    signal_skip_days = sum(int(x.get("signal_skip_days") or 0) for x in ok)
    long_days = sum(int(x.get("long_t_days") or 0) for x in ok)
    reverse_days = sum(int(x.get("reverse_t_days") or 0) for x in ok)
    uncover_days = sum(int(x.get("uncover_days") or 0) for x in ok)

    # 乐观 / 日线对照合计
    opt_pnl = 0.0
    opt_trades = 0
    opt_exposure = 0.0
    has_opt = False
    daily_pnl = 0.0
    daily_trades = 0
    has_daily = False
    for x in ok:
        opt = x.get("optimistic_compare")
        if isinstance(opt, dict) and opt.get("t0_pnl_total") is not None:
            has_opt = True
            opt_pnl += float(opt.get("t0_pnl_total") or 0)
            opt_trades += int(opt.get("t0_trade_days") or 0)
            opt_exposure += float(opt.get("exposure_pnl_total") or 0)
        dc = x.get("daily_compare")
        if isinstance(dc, dict) and dc.get("t0_pnl_total") is not None:
            has_daily = True
            daily_pnl += float(dc.get("t0_pnl_total") or 0)
            daily_trades += int(dc.get("t0_trade_days") or 0)

    # 合并各票成交样本供 UI；最近按日 + 保留若干反T，避免「近一周全正」误以为没有反T
    # 注意：trade_days_sample=[] 时勿用 `or days`，否则会把全日跳过行灌进样本
    trade_sample: List[Dict[str, Any]] = []
    skip_reason_counts: Dict[str, int] = {}
    skip_sample: List[Dict[str, Any]] = []
    for x in ok:
        code = x.get("stock_code")
        tds = x.get("trade_days_sample")
        if not isinstance(tds, list):
            tds = []
        for d in tds:
            if d.get("skipped"):
                continue
            if (
                int(d.get("sold_qty") or 0) > 0
                or int(d.get("bought_qty") or 0) > 0
                or float(d.get("pnl") or 0) != 0
                or float(d.get("exposure_pnl") or 0) != 0
            ):
                row = dict(d)
                row["stock_code"] = code
                trade_sample.append(row)
        for d in x.get("days") or []:
            if not d.get("skipped"):
                continue
            reason = str(d.get("reason") or d.get("direction_reason") or "跳过").strip()
            reason_key = (reason[:69] + "…") if len(reason) > 72 else (reason or "跳过")
            skip_reason_counts[reason_key] = skip_reason_counts.get(reason_key, 0) + 1
            skip_sample.append(
                {
                    "date": d.get("date"),
                    "stock_code": code,
                    "reason": reason_key,
                    "direction_score": d.get("direction_score"),
                    "signal_skip": bool(d.get("signal_skip")),
                    "path_mode": d.get("path_mode"),
                }
            )
    trade_sample.sort(key=lambda r: str(r.get("date") or ""))
    recent = trade_sample[-15:]
    seen = {(r.get("stock_code"), r.get("date"), r.get("direction")) for r in recent}
    rev_extra: List[Dict[str, Any]] = []
    for r in reversed(trade_sample):
        if (r.get("direction") or r.get("direction_used")) != "reverse_t":
            continue
        key = (r.get("stock_code"), r.get("date"), r.get("direction"))
        if key in seen:
            continue
        rev_extra.append(r)
        seen.add(key)
        if len(rev_extra) >= 5:
            break
    trade_sample = sorted(recent + rev_extra, key=lambda r: str(r.get("date") or ""))

    skip_sample.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
    skip_reason_top = sorted(
        ({"reason": k, "count": v} for k, v in skip_reason_counts.items()),
        key=lambda r: (-int(r["count"]), str(r["reason"])),
    )[:8]

    out: Dict[str, Any] = {
        "success": bool(ok),
        "task": "t0_backtest",
        "from_holdings": True,
        "holding_count": len(holdings),
        "ok_count": len(ok),
        "t0_pnl_total": round(total_pnl, 2),
        "exposure_pnl_total": round(total_exposure, 2),
        "t0_trade_days": total_trades,
        "t0_cover_days": total_covers,
        "skip_days": skip_days,
        "signal_skip_days": signal_skip_days,
        "long_t_days": long_days,
        "reverse_t_days": reverse_days,
        "long_t_pnl": round(long_pnl, 2),
        "reverse_t_pnl": round(reverse_pnl, 2),
        "long_t_cover_days": long_cover,
        "reverse_t_cover_days": reverse_cover,
        "uncover_days": uncover_days,
        "minute_path_days": minute_path_days,
        "daily_fallback_days": daily_fallback_days,
        "hold_mv_start": round(total_hold_mv, 2),
        "results": per,
        "days": trade_sample,
        "trade_days_sample": trade_sample,
        "skip_reason_top": skip_reason_top,
        "skip_days_sample": skip_sample[:12],
        "path_mode": cfg.get("path_mode"),
        "direction": cfg.get("direction"),
        "use_minute": use_minute and minute_path_days > 0,
        "rules": {
            "direction": cfg.get("direction"),
            "path_mode": cfg.get("path_mode"),
            "fill_mode": cfg.get("fill_mode"),
            "t0_ratio": cfg.get("t0_ratio"),
            "dir_enter": cfg.get("dir_enter"),
            "min_range_pct": cfg.get("min_range_pct"),
            "sell_trigger_pct": cfg.get("sell_trigger_pct"),
            "buy_trigger_pct": cfg.get("buy_trigger_pct"),
        },
        "note": (
            "按模拟持仓底仓回测；"
            f"direction={cfg.get('direction')} · path={cfg.get('path_mode')} · "
            f"dir_enter={cfg.get('dir_enter')}；"
            + (
                "有分钟日 5m 第一触达，缺分钟回退日线 path；"
                if use_minute
                else f"日线 path_mode={cfg.get('path_mode')}；"
            )
            + "非实盘。主看含敞口净PnL / 完成往返率 / 参与率 / 日线Δ。"
        ),
    }
    if exec_meta:
        out["execution"] = {
            "effective_hash": exec_meta.get("effective_hash"),
            "t0_sources": exec_meta.get("t0_sources"),
            "notes": exec_meta.get("notes"),
            "summary": exec_meta.get("summary"),
            "channel": "backtest",
        }
    contrib = round(total_pnl + total_exposure, 2)
    out["no_t0_compare"] = {
        "t0_pnl_with_exposure": contrib,
        "t0_contribution": contrib,
        "t0_trade_days": total_trades,
        "note": "含T轨相对「不做T」：贡献≈做T含敞口净PnL合计",
    }
    if has_opt:
        delta = round(opt_pnl - total_pnl, 2)
        out["optimistic_compare"] = {
            "t0_pnl_total": round(opt_pnl, 2),
            "t0_trade_days": opt_trades,
            "exposure_pnl_total": round(opt_exposure, 2),
            "delta_pnl": delta,
            "delta_pnl_ratio_pct": (
                round(delta / abs(total_pnl) * 100.0, 2) if abs(total_pnl) > 1e-9 else None
            ),
            "note": "各票 optimistic 上界合计（卖 high / 买 low）；勿当真",
        }
    if has_daily:
        out["daily_compare"] = {
            "t0_pnl_total": round(daily_pnl, 2),
            "t0_trade_days": daily_trades,
            "delta_pnl": round(total_pnl - daily_pnl, 2),
            "path_mode": "veto",
            "note": "同窗日线 veto 对照合计；主结果含分钟第一触达",
        }
    out.update(derive_t0_quality_metrics(out))
    if len(ok) == 1:
        one = ok[0]
        out.update(
            {
                "stock_code": one.get("stock_code"),
                "stock_name": one.get("stock_name"),
                "optimistic_compare": one.get("optimistic_compare") or out.get("optimistic_compare"),
                "daily_compare": one.get("daily_compare") or out.get("daily_compare"),
                "days": one.get("trade_days_sample") or one.get("days") or trade_sample,
                "trade_days_sample": one.get("trade_days_sample") or trade_sample,
                "rules": one.get("rules"),
                "uncover_days": one.get("uncover_days"),
                "hold_mv_start": one.get("hold_mv_start"),
                "long_t_pnl": one.get("long_t_pnl"),
                "reverse_t_pnl": one.get("reverse_t_pnl"),
                "long_t_cover_days": one.get("long_t_cover_days"),
                "reverse_t_cover_days": one.get("reverse_t_cover_days"),
                "t0_cover_days": one.get("t0_cover_days"),
                "minute_path_days": one.get("minute_path_days"),
                "daily_fallback_days": one.get("daily_fallback_days"),
                "minute_meta": one.get("minute_meta"),
                "use_minute": one.get("use_minute"),
            }
        )
        out.update(derive_t0_quality_metrics(out))
    return out
