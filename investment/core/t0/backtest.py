"""底仓做 T 日线代理回测。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional

from core.t0.config import load_t0_rules
from core.t0.rules import atr_pct_from_bars, simulate_t0_day


def derive_t0_quality_metrics(report: Dict[str, Any]) -> Dict[str, Any]:
    """从回测汇总字段推导决策向指标（单票 / 多持仓共用）。"""
    trades = int(report.get("t0_trade_days") or 0)
    covers = int(report.get("t0_cover_days") or 0)
    skips = int(report.get("skip_days") or 0)
    uncovers = int(report.get("uncover_days") or 0)
    pnl = float(report.get("t0_pnl_total") or 0)
    exposure = float(report.get("exposure_pnl_total") or 0)
    hold_mv = float(report.get("hold_mv_start") or 0)

    out: Dict[str, Any] = {
        "t0_pnl_with_exposure": round(pnl + exposure, 2),
        "cover_rate_pct": round(covers / trades * 100.0, 2) if trades else None,
        "uncover_rate_pct": round(uncovers / trades * 100.0, 2) if trades else None,
        "participate_rate_pct": (
            round(trades / (trades + skips) * 100.0, 2) if (trades + skips) else None
        ),
        "avg_pnl_per_trade_day": round(pnl / trades, 2) if trades else None,
        "pnl_vs_hold_mv_pct": round(pnl / hold_mv * 100.0, 4) if hold_mv > 0 else None,
    }
    opt = report.get("optimistic_compare")
    if isinstance(opt, dict) and opt.get("delta_pnl") is not None:
        delta = float(opt.get("delta_pnl") or 0)
        opt = dict(opt)
        if abs(pnl) > 1e-9:
            opt["delta_pnl_ratio_pct"] = round(delta / abs(pnl) * 100.0, 2)
        else:
            opt["delta_pnl_ratio_pct"] = None
        out["optimistic_compare"] = opt
        out["optimistic_delta_ratio_pct"] = opt.get("delta_pnl_ratio_pct")
    return out


def backtest_t0_on_bars(
    bars: List[dict],
    *,
    initial_shares: float = 1000,
    initial_cost: Optional[float] = None,
    initial_cash: float = 0.0,
    rules: Optional[dict] = None,
    cost_config: Optional[dict] = None,
    stock_code: str = "",
    compare_optimistic: bool = True,
    minute_by_date: Optional[Dict[str, List[dict]]] = None,
    compare_daily: bool = False,
) -> Dict[str, Any]:
    """Walk 日线：假设全程持有底仓，每日尝试做 T。

    默认 fill_mode=trigger；可选附带 optimistic 上界对照。
    minute_by_date：有则对应日走 5m 第一触达；缺分钟日回退日线 path_mode。
    compare_daily：在有分钟覆盖时，另跑纯日线 veto 作对照。
    """
    explicit_path = isinstance(rules, dict) and "path_mode" in rules
    explicit_dir = isinstance(rules, dict) and "direction" in rules
    # 若调用方已传入经 resolve 的规则（含 direction/path），直接用；否则走 Execution resolve
    if explicit_path and explicit_dir:
        cfg = load_t0_rules(rules)
    else:
        from core.execution import resolve_t0_rules, strip_execution_meta

        cfg = strip_execution_meta(
            resolve_t0_rules(
                rules=rules,
                channel="backtest",
                has_minute=bool(minute_by_date),
            )
        )
    # 兼容：旧调用只传部分规则时，resolve 已补 direction/path
    if not bars or len(bars) < 2:
        return {"success": False, "error": "日线不足"}

    primary = _walk_t0(
        bars,
        initial_shares=initial_shares,
        initial_cost=initial_cost,
        initial_cash=initial_cash,
        rules=cfg,
        cost_config=cost_config,
        stock_code=stock_code,
        minute_by_date=minute_by_date,
    )
    if not primary.get("success"):
        return primary

    if compare_optimistic and str(cfg.get("fill_mode") or "") != "optimistic":
        opt_rules = dict(cfg)
        opt_rules["fill_mode"] = "optimistic"
        opt = _walk_t0(
            bars,
            initial_shares=initial_shares,
            initial_cost=initial_cost,
            initial_cash=initial_cash,
            rules=opt_rules,
            cost_config=cost_config,
            stock_code=stock_code,
            minute_by_date=minute_by_date,
        )
        if opt.get("success"):
            delta = round(
                float(opt.get("t0_pnl_total") or 0) - float(primary.get("t0_pnl_total") or 0),
                2,
            )
            primary_pnl = float(primary.get("t0_pnl_total") or 0)
            primary["optimistic_compare"] = {
                "t0_pnl_total": opt.get("t0_pnl_total"),
                "t0_win_rate_pct": opt.get("t0_win_rate_pct"),
                "t0_trade_days": opt.get("t0_trade_days"),
                "exposure_pnl_total": opt.get("exposure_pnl_total"),
                "delta_pnl": delta,
                "delta_pnl_ratio_pct": (
                    round(delta / abs(primary_pnl) * 100.0, 2) if abs(primary_pnl) > 1e-9 else None
                ),
                "note": "optimistic 为同规则上界对照（卖 high / 买 low）",
            }

    if compare_daily and minute_by_date:
        daily_rules = dict(cfg)
        daily_rules["path_mode"] = "veto"
        daily = _walk_t0(
            bars,
            initial_shares=initial_shares,
            initial_cost=initial_cost,
            initial_cash=initial_cash,
            rules=daily_rules,
            cost_config=cost_config,
            stock_code=stock_code,
            minute_by_date=None,
        )
        if daily.get("success"):
            p_pnl = float(primary.get("t0_pnl_total") or 0)
            d_pnl = float(daily.get("t0_pnl_total") or 0)
            primary["daily_compare"] = {
                "t0_pnl_total": daily.get("t0_pnl_total"),
                "t0_trade_days": daily.get("t0_trade_days"),
                "t0_cover_days": daily.get("t0_cover_days"),
                "exposure_pnl_total": daily.get("exposure_pnl_total"),
                "path_mode": "veto",
                "delta_pnl": round(p_pnl - d_pnl, 2),
                "note": "同窗日线 veto 对照；主结果为分钟第一触达（有覆盖日）",
            }

    primary.update(derive_t0_quality_metrics(primary))
    path_mode = cfg.get("path_mode") or "veto"
    path_note = (
        "有分钟日：5m 第一触达；缺分钟日回退日线 path_mode；"
        if minute_by_date
        else f"日线代理 path_mode={path_mode}；"
    )
    primary["note"] = (
        "底仓做T回测；默认 trigger 成交；"
        f"direction={cfg.get('direction')} · dir_enter={cfg.get('dir_enter')}；"
        + path_note
        + "T+1（正T卖旧买回 / 反T买新卖旧换仓）；非实盘、不保证收益。"
        "主指标看含敞口净PnL / 完成往返率 / 参与率 / 敞口。"
    )
    return primary


def _walk_t0(
    bars: List[dict],
    *,
    initial_shares: float,
    initial_cost: Optional[float],
    initial_cash: float,
    rules: dict,
    cost_config: Optional[dict],
    stock_code: str,
    minute_by_date: Optional[Dict[str, List[dict]]] = None,
) -> Dict[str, Any]:
    cfg = load_t0_rules(rules)
    shares = float(initial_shares)
    cost = float(initial_cost if initial_cost is not None else bars[0].get("close") or 0)
    if shares <= 0 or cost <= 0:
        return {"success": False, "error": "无效初始仓位"}

    sellable = shares
    cash = float(initial_cash or 0)
    # 反T / signal 可能需要现金：给一笔与底仓市值相当的研究现金
    if cash <= 0 and str(cfg.get("direction") or "auto") in {"auto", "reverse_t", "signal"}:
        cash = shares * cost * float(cfg.get("t0_ratio") or 0.4)

    days: List[dict] = []
    pnls: List[float] = []
    exposures: List[float] = []
    trade_count = 0
    cover_count = 0
    uncover_days = 0
    skip_count = 0
    signal_skip_days = 0
    long_days = 0
    reverse_days = 0
    long_pnl = 0.0
    reverse_pnl = 0.0
    long_cover = 0
    reverse_cover = 0
    minute_days = 0
    daily_fallback_days = 0
    atr_window = int(cfg.get("atr_window") or 14)
    hold_mv_start = round(shares * cost, 2)
    fallback_cfg = dict(cfg)
    if str(cfg.get("path_mode") or "") == "first_touch":
        fallback_cfg["path_mode"] = "veto"

    for i, bar in enumerate(bars):
        # signal/auto/reverse_t 需现金；每日补足研究用现金（防前日半腿耗尽）
        if str(cfg.get("direction") or "") in {"auto", "reverse_t", "signal"}:
            px = float(bar.get("close") or bar.get("open") or cost or 0)
            if px > 0 and shares > 0:
                need = shares * px * float(cfg.get("t0_ratio") or 0.4)
                if cash < need * 0.25:
                    cash = max(cash, need)
        # auto/signal 依赖昨收；日线源未必带 prev_close
        bar_day = dict(bar)
        if i > 0 and not bar_day.get("prev_close"):
            prev_c = float(bars[i - 1].get("close") or 0)
            if prev_c > 0:
                bar_day["prev_close"] = prev_c
        hist_incl = bars[: i + 1]
        hist_prior = bars[:i]
        atr = atr_pct_from_bars(hist_incl, atr_window) if cfg.get("use_atr") else None
        # ATR 用到当日会略宽；选向只用 hist_prior（无前视）
        atr_for_dir = atr_pct_from_bars(hist_prior, atr_window) if hist_prior else None
        dkey = str(bar.get("date") or "")
        mins = None
        used_minute = False
        if minute_by_date and dkey:
            mins = minute_by_date.get(dkey)
            if mins and len(mins) >= 2:
                used_minute = True
        day_rules = cfg if used_minute else fallback_cfg
        day = simulate_t0_day(
            bar=bar_day,
            shares=shares,
            cost=cost,
            sellable_shares=sellable,
            rules=day_rules,
            cost_config=cost_config,
            stock_code=stock_code,
            cash=cash,
            atr_pct=atr if atr is not None else atr_for_dir,
            hist_bars=hist_prior,
            minute_bars=mins if used_minute else None,
        )
        if used_minute:
            minute_days += 1
        elif minute_by_date is not None:
            daily_fallback_days += 1
        if day.get("skipped"):
            skip_count += 1
            if day.get("signal_skip"):
                signal_skip_days += 1
            days.append(
                {
                    "date": bar.get("date"),
                    "skipped": True,
                    "reason": day.get("reason"),
                    "shares": shares,
                    "pnl": 0,
                    "exposure_pnl": 0,
                    "direction_score": day.get("direction_score"),
                    "direction_reason": day.get("direction_reason"),
                    "direction_features": day.get("direction_features"),
                    "signal_skip": bool(day.get("signal_skip")),
                    "path_mode": day.get("path_mode") or day_rules.get("path_mode"),
                    "minute_path": used_minute,
                }
            )
            continue
        if not day.get("success"):
            continue

        sold = int(day.get("sold_qty") or 0)
        covered = int(day.get("covered_qty") or 0)
        uncovered = int(day.get("uncovered_qty") or 0)
        bought = int(day.get("bought_qty") or 0)
        sold_back = int(day.get("sold_back_qty") or 0)
        direction = day.get("direction_used")
        day_pnl = float(day.get("pnl") or 0)

        shares = float(day["shares_end"])
        cash += float(day.get("cash_delta") or 0)
        if sold > 0 or bought > 0:
            trade_count += 1
            completed = (sold > 0 and covered >= sold) or (bought > 0 and sold_back >= bought)
            if direction == "long_t":
                long_days += 1
                if completed:
                    long_cover += 1
                    cover_count += 1
            elif direction == "reverse_t":
                reverse_days += 1
                if completed:
                    reverse_cover += 1
                    cover_count += 1
            if uncovered > 0 or (bought > 0 and sold_back == 0):
                uncover_days += 1
            if day_pnl:
                if direction == "long_t":
                    long_pnl += day_pnl
                elif direction == "reverse_t":
                    reverse_pnl += day_pnl
        sellable = shares

        if day_pnl:
            pnls.append(day_pnl)
        if day.get("exposure_pnl"):
            exposures.append(float(day["exposure_pnl"]))

        days.append(
            {
                "date": bar.get("date"),
                "direction": direction,
                "sold_qty": sold,
                "covered_qty": covered,
                "uncovered_qty": uncovered,
                "bought_qty": bought,
                "sold_back_qty": sold_back,
                "pnl": day_pnl,
                "exposure_pnl": day.get("exposure_pnl") or 0,
                "shares": shares,
                "fill_mode": day.get("fill_mode"),
                "atr_pct": day.get("atr_pct"),
                "direction_score": day.get("direction_score"),
                "direction_reason": day.get("direction_reason"),
                "direction_features": day.get("direction_features"),
                "path_mode": day.get("path_mode") or day_rules.get("path_mode"),
                "minute_path": used_minute,
                "touch_sell_at": day.get("touch_sell_at"),
                "touch_cover_at": day.get("touch_cover_at") or day.get("touch_buy_at"),
            }
        )

    win = sum(1 for p in pnls if p > 0)
    total_pnl = round(sum(pnls), 2)
    exposure_total = round(sum(exposures), 2)
    hold_end = shares * float(bars[-1].get("close") or 0)
    traded_days = [
        d
        for d in days
        if not d.get("skipped")
        and (
            int(d.get("sold_qty") or 0) > 0
            or int(d.get("bought_qty") or 0) > 0
            or float(d.get("pnl") or 0) != 0
            or float(d.get("exposure_pnl") or 0) != 0
        )
    ]
    report: Dict[str, Any] = {
        "success": True,
        "task": "t0_backtest",
        "stock_code": stock_code,
        "bar_count": len(bars),
        "initial_shares": initial_shares,
        "initial_cost": round(cost, 4),
        "hold_mv_start": hold_mv_start,
        "shares_end": shares,
        "cash_ledger": round(cash, 2),
        "hold_mv_end": round(hold_end, 2),
        "t0_trade_days": trade_count,
        "t0_cover_days": cover_count,
        "uncover_days": uncover_days,
        "skip_days": skip_count,
        "signal_skip_days": signal_skip_days,
        "long_t_days": long_days,
        "reverse_t_days": reverse_days,
        "long_t_pnl": round(long_pnl, 2),
        "reverse_t_pnl": round(reverse_pnl, 2),
        "long_t_cover_days": long_cover,
        "reverse_t_cover_days": reverse_cover,
        "minute_path_days": minute_days,
        "daily_fallback_days": daily_fallback_days,
        "t0_pnl_total": total_pnl,
        "exposure_pnl_total": exposure_total,
        "t0_pnl_with_exposure": round(total_pnl + exposure_total, 2),
        "t0_win_rate_pct": round(win / len(pnls) * 100.0, 2) if pnls else None,
        "rules": {
            "t0_ratio": cfg["t0_ratio"],
            "sell_trigger_pct": cfg["sell_trigger_pct"],
            "buy_trigger_pct": cfg["buy_trigger_pct"],
            "must_cover_same_day": cfg["must_cover_same_day"],
            "fill_mode": cfg["fill_mode"],
            "direction": cfg["direction"],
            "path_mode": cfg.get("path_mode"),
            "minute_period": cfg.get("minute_period"),
            "dir_enter": cfg.get("dir_enter"),
            "min_range_pct": cfg.get("min_range_pct"),
            "use_atr": cfg.get("use_atr"),
            "ref": cfg.get("ref"),
        },
        "days": days[-30:],
        # 成交样本：不限于最近 30 根日历日（避免近期全跳过时误以为全程无成交）
        "trade_days_sample": traded_days[-20:],
    }
    report.update(derive_t0_quality_metrics(report))
    return report
