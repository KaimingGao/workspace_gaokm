"""底仓做 T 回测（仅 5m 第一触达；已删除日线模拟）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional

from core.t0.config import load_t0_rules
from core.t0.rules import _t0_qty_lots, atr_pct_from_bars, simulate_t0_day


def _research_cash_for_reverse(
    shares: float,
    px: float,
    *,
    t0_ratio: float,
    lot: int = 100,
) -> float:
    """反T研究现金：至少够买「抬手后」目标股数（小仓 100～200 股会抬到 1 手）。"""
    if shares <= 0 or px <= 0:
        return 0.0
    lot_i = max(int(lot or 100), 1)
    qty = _t0_qty_lots(shares, t0_ratio, lot_i, shares)
    # 相对触发价略低，预留一点缓冲
    return float(qty) * float(px) * 1.05


def summarize_t0_day_legs(day: Dict[str, Any]) -> Dict[str, Any]:
    """从单日 trades / touch_* 提炼 UI 校验字段（价必有；时仅分钟路径）。"""
    trades = [t for t in (day.get("trades") or []) if isinstance(t, dict)]
    direction = str(day.get("direction_used") or day.get("direction") or "")
    sells = [t for t in trades if str(t.get("side") or "").lower().endswith("sell")]
    buys = [t for t in trades if str(t.get("side") or "").lower().endswith("buy")]
    rev = direction == "reverse_t"
    if rev:
        sell_t = sells[-1] if sells else None
        buy_t = buys[0] if buys else None
        sell_at = (sell_t or {}).get("at") or day.get("touch_sell_at")
        buy_at = (buy_t or {}).get("at") or day.get("touch_buy_at")
    else:
        sell_t = sells[0] if sells else None
        buy_t = buys[-1] if buys else None
        sell_at = (sell_t or {}).get("at") or day.get("touch_sell_at")
        buy_at = (buy_t or {}).get("at") or day.get("touch_cover_at") or day.get("touch_buy_at")

    out: Dict[str, Any] = {}
    if sell_t:
        if sell_t.get("price") is not None:
            out["sell_price"] = sell_t.get("price")
        if sell_t.get("shares") is not None:
            out["sell_shares"] = sell_t.get("shares")
    if buy_t:
        if buy_t.get("price") is not None:
            out["buy_price"] = buy_t.get("price")
        if buy_t.get("shares") is not None:
            out["buy_shares"] = buy_t.get("shares")
    if sell_at:
        out["sell_at"] = sell_at
    if buy_at:
        out["buy_at"] = buy_at
    return out


def _t0_range_fields(day: Dict[str, Any]) -> Dict[str, Any]:
    """回测日明细：滚动振幅审计字段（与 Worker / simulate_t0_day_minute 同口径）。"""
    out: Dict[str, Any] = {}
    for k in ("range_mode", "prefix_bars", "range_pct", "min_range_pct"):
        v = day.get(k)
        if v is not None:
            out[k] = v
    return out


def _optimistic_delta_ratio_pct(
    delta: float,
    primary_pnl: float,
    *,
    trade_days: int = 0,
) -> Optional[float]:
    """乐观Δ占比；样本过薄或比值爆炸时返回 None（避免 -15→+15 显示 200%）。"""
    if int(trade_days or 0) < 3:
        return None
    base = abs(float(primary_pnl or 0))
    if base < 1e-9:
        return None
    ratio = float(delta) / base * 100.0
    if abs(ratio) > 150.0:
        return None
    return round(ratio, 2)


def derive_t0_quality_metrics(report: Dict[str, Any]) -> Dict[str, Any]:
    """从回测汇总字段推导决策向指标（单票 / 多持仓共用）。"""
    trades = int(report.get("t0_trade_days") or 0)
    covers = int(report.get("t0_cover_days") or 0)
    skips = int(report.get("skip_days") or 0)
    signal_skips = int(report.get("signal_skip_days") or 0)
    uncovers = int(report.get("uncover_days") or 0)
    pnl = float(report.get("t0_pnl_total") or 0)
    exposure = float(report.get("exposure_pnl_total") or 0)
    hold_mv = float(report.get("hold_mv_start") or 0)
    eval_days = trades + skips

    out: Dict[str, Any] = {
        "t0_pnl_with_exposure": round(pnl + exposure, 2),
        "cover_rate_pct": round(covers / trades * 100.0, 2) if trades else None,
        "uncover_rate_pct": round(uncovers / trades * 100.0, 2) if trades else None,
        "participate_rate_pct": (
            round(trades / eval_days * 100.0, 2) if eval_days else None
        ),
        "signal_skip_rate_pct": (
            round(signal_skips / eval_days * 100.0, 2) if eval_days else None
        ),
        "eval_days": eval_days,
        "avg_pnl_per_trade_day": round(pnl / trades, 2) if trades else None,
        "pnl_vs_hold_mv_pct": round(pnl / hold_mv * 100.0, 4) if hold_mv > 0 else None,
        "win_rate_pct": report.get("t0_win_rate_pct"),
        "profit_factor": report.get("profit_factor"),
    }
    opt = report.get("optimistic_compare")
    if isinstance(opt, dict) and opt.get("delta_pnl") is not None:
        delta = float(opt.get("delta_pnl") or 0)
        opt = dict(opt)
        opt["delta_pnl_ratio_pct"] = _optimistic_delta_ratio_pct(
            delta, pnl, trade_days=trades
        )
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
    require_minute: bool = False,
    bars_history: Optional[List[dict]] = None,
    eval_lookback: Optional[int] = None,
    tau_pool_by_date: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Walk 底仓做 T。

    默认 fill_mode=trigger；可选附带 optimistic 上界对照。
    仅 5m 第一触达；缺分钟覆盖日跳过（已删除日线模拟）。
    ``bars`` = 评估窗内交易日；``bars_history``（可选）= 含 warmup 的全量日线，
    供 dual_y / ATR 的 hist_prior，不改变评估窗长度。
    ``tau_pool_by_date``：按日截面缺口（与刷簿 ŷ_τ 特征对齐）。
    compare_daily / require_minute 形参保留兼容；require_minute=True 且无 minute_by_date 时直接失败。
    """
    _ = compare_daily  # 保留形参兼容旧调用
    if require_minute and not minute_by_date:
        return {
            "success": False,
            "error": "做T回测已删除日线模拟，需 5 分钟 K 线；请检查分钟源/缓存",
            "task": "t0_backtest",
        }
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
    if not bars:
        return {"success": False, "error": "日线不足"}
    history = list(bars_history or bars)
    if len(history) < 2:
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
        require_minute=require_minute,
        bars_history=history,
        tau_pool_by_date=tau_pool_by_date,
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
            require_minute=require_minute,
            bars_history=history,
            tau_pool_by_date=tau_pool_by_date,
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
                "delta_pnl_ratio_pct": _optimistic_delta_ratio_pct(
                    delta, primary_pnl, trade_days=int(opt.get("t0_trade_days") or primary.get("t0_trade_days") or 0)
                ),
                "note": "optimistic 为同规则上界对照（卖 high / 买 low）",
            }

    primary.update(derive_t0_quality_metrics(primary))
    if bars_history is not None and len(history) > len(bars):
        primary["eval_lookback"] = int(eval_lookback or len(bars))
        primary["score_warmup_bars"] = max(0, len(history) - len(bars))
    primary["note"] = (
        "底仓做T回测；默认 trigger 成交；"
        f"direction={cfg.get('direction')} · y_tau_map={cfg.get('y_tau_map')} · "
        f"y_score_source={cfg.get('y_score_source')} · "
        f"y_tau_enter=±{cfg.get('y_tau_enter')}%；"
        "仅 5m 第一触达（缺分钟日跳过，已删除日线模拟）；"
        + (
            f"评估窗 {len(bars)} 日 · 因子缓冲 {max(0, len(history) - len(bars))} 日；"
            if bars_history is not None and len(history) > len(bars)
            else ""
        )
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
    require_minute: bool = False,
    bars_history: Optional[List[dict]] = None,
    tau_pool_by_date: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    cfg = load_t0_rules(rules)
    history = list(bars_history or bars)
    hist_index_by_date = {
        str(b.get("date") or ""): i for i, b in enumerate(history) if b.get("date")
    }
    shares = float(initial_shares)
    cost = float(initial_cost if initial_cost is not None else bars[0].get("close") or 0)
    if shares <= 0 or cost <= 0:
        return {"success": False, "error": "无效初始仓位"}

    sellable = shares  # 回测简化：日初持仓均可卖；实盘 Worker 用 tplus1 FIFO 批次
    cash = float(initial_cash or 0)
    lot = max(int(cfg.get("lot_size") or 100), 1)
    ratio = float(cfg.get("t0_ratio") or 0.4)
    # 反T / dual_y：研究现金须覆盖「抬手后」目标股数（200×40%→抬到100股）
    if cash <= 0 and str(cfg.get("direction") or "auto") in {
        "auto",
        "reverse_t",
        "signal",
        "dual_y",
    }:
        cash = _research_cash_for_reverse(shares, cost, t0_ratio=ratio, lot=lot)
    tau_pool = tau_pool_by_date if isinstance(tau_pool_by_date, dict) else {}

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
    missing_minute_days = 0
    atr_window = int(cfg.get("atr_window") or 14)
    hold_mv_start = round(shares * cost, 2)
    # 已删除日线模拟：无分钟覆盖的交易日一律跳过
    if str(cfg.get("direction") or "") == "dual_y" and stock_code:
        try:
            from core.t0.score_policy import _scoring_models

            _scoring_models()
        except Exception:  # noqa: BLE001
            logger.debug("t0 score model warm failed", exc_info=True)

    for i, bar in enumerate(bars):
        dkey = str(bar.get("date") or "")
        gi = hist_index_by_date.get(dkey, i if history is bars else None)
        if gi is None:
            skip_count += 1
            days.append(
                {
                    "date": bar.get("date"),
                    "skipped": True,
                    "reason": "评估日不在因子历史索引",
                    "skip_category": "missing_history",
                    "shares": shares,
                    "close": float(bar.get("close") or bar.get("open") or 0) or None,
                    "pnl": 0,
                    "exposure_pnl": 0,
                    "signal_skip": False,
                    "path_mode": "first_touch",
                }
            )
            continue
        # signal/auto/reverse_t 需现金；每日补足研究用现金（防前日半腿耗尽）
        if str(cfg.get("direction") or "") in {"auto", "reverse_t", "signal", "dual_y"}:
            px = float(bar.get("close") or bar.get("open") or cost or 0)
            if px > 0 and shares > 0:
                need = _research_cash_for_reverse(shares, px, t0_ratio=ratio, lot=lot)
                if cash < need * 0.95:
                    cash = max(cash, need)
        # auto/signal 依赖昨收；日线源未必带 prev_close
        bar_day = dict(bar)
        if gi > 0 and not bar_day.get("prev_close"):
            prev_c = float(history[gi - 1].get("close") or 0)
            if prev_c > 0:
                bar_day["prev_close"] = prev_c
        hist_incl = history[: gi + 1]
        hist_prior = history[:gi]
        atr = atr_pct_from_bars(hist_incl, atr_window) if cfg.get("use_atr") else None
        # ATR 用到当日会略宽；选向只用 hist_prior（无前视）
        atr_for_dir = atr_pct_from_bars(hist_prior, atr_window) if hist_prior else None
        mins = None
        used_minute = False
        if minute_by_date and dkey:
            mins = minute_by_date.get(dkey)
            if mins and len(mins) >= 2:
                used_minute = True

        if not used_minute:
            missing_minute_days += 1
            skip_count += 1
            days.append(
                {
                    "date": bar.get("date"),
                    "skipped": True,
                    "reason": "缺分钟线，跳过（已删除日线模拟）",
                    "skip_category": "missing_minute",
                    "shares": shares,
                    "close": float(bar.get("close") or bar.get("open") or 0) or None,
                    "pnl": 0,
                    "exposure_pnl": 0,
                    "signal_skip": False,
                    "path_mode": "first_touch",
                }
            )
            continue

        score_snap = None
        pool_day = None
        if str(cfg.get("direction") or "") == "dual_y" and stock_code and dkey:
            from core.t0.score_policy import resolve_scores_for_code, resolve_y_score_source

            src = resolve_y_score_source(cfg)
            pool_day = tau_pool.get(dkey) if isinstance(tau_pool.get(dkey), dict) else {}
            ref_map = (
                pool_day.get("ref_by_code")
                if isinstance(pool_day.get("ref_by_code"), dict)
                else {}
            )
            score_snap = resolve_scores_for_code(
                stock_code,
                hist_bars=hist_prior,
                day_bar=bar_day,
                source=src,
                fuse_intraday=True,
                allow_fallback=(src != "compute"),
                as_of=str(history[gi - 1].get("date") or "")[:10] if gi > 0 else dkey[:10],
                pool_gaps=pool_day.get("pool_gaps"),
                sector_gap_breadth=pool_day.get("sector_gap_breadth"),
                sector_gap_median=ref_map.get(stock_code),
            )
        day = simulate_t0_day(
            bar=bar_day,
            shares=shares,
            cost=cost,
            sellable_shares=sellable,
            rules=cfg,
            cost_config=cost_config,
            stock_code=stock_code,
            cash=cash,
            atr_pct=atr if atr is not None else atr_for_dir,
            hist_bars=hist_prior,
            minute_bars=mins,
            scores=score_snap,
            tau_pool_day=pool_day,
        )
        if used_minute:
            minute_days += 1
        if day.get("skipped"):
            skip_count += 1
            if day.get("signal_skip"):
                signal_skip_days += 1
            skip_reason = str(day.get("reason") or day.get("direction_reason") or "")
            from core.t0.viz import classify_t0_skip_reason

            days.append(
                {
                    "date": bar.get("date"),
                    "skipped": True,
                    "reason": day.get("reason"),
                    "skip_category": classify_t0_skip_reason(skip_reason),
                    "shares": shares,
                    "close": float(bar.get("close") or bar.get("open") or 0) or None,
                    "pnl": 0,
                    "exposure_pnl": 0,
                    "direction_score": day.get("direction_score"),
                    "direction_reason": day.get("direction_reason"),
                    "direction_features": day.get("direction_features"),
                    "scores": day.get("scores"),
                    "signal_skip": bool(day.get("signal_skip")),
                    "path_mode": day.get("path_mode") or cfg.get("path_mode") or "first_touch",
                    "minute_path": used_minute,
                    **_t0_range_fields(day),
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
        sellable = shares  # 日末重置；不跟踪当日新买股的 T+1 冻结

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
                "close": float(bar.get("close") or bar.get("open") or 0) or None,
                "fill_mode": day.get("fill_mode"),
                "atr_pct": day.get("atr_pct"),
                "direction_score": day.get("direction_score"),
                "direction_reason": day.get("direction_reason"),
                "direction_features": day.get("direction_features"),
                "scores": day.get("scores"),
                "cover_policy": day.get("cover_policy"),
                "path_mode": day.get("path_mode") or cfg.get("path_mode") or "first_touch",
                "minute_path": used_minute,
                "touch_sell_at": day.get("touch_sell_at"),
                "touch_cover_at": day.get("touch_cover_at") or day.get("touch_buy_at"),
                "touch_buy_at": day.get("touch_buy_at"),
                "trades": day.get("trades") or [],
                "t0_ratio_base": day.get("t0_ratio_base"),
                "t0_ratio": day.get("t0_ratio"),
                **_t0_range_fields(day),
                **summarize_t0_day_legs(day),
            }
        )

    win = sum(1 for p in pnls if p > 0)
    loss = sum(1 for p in pnls if p < 0)
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = sum(abs(p) for p in pnls if p < 0)
    profit_factor = (
        round(gross_win / gross_loss, 2) if gross_loss > 1e-9 else None
    )
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
    from core.t0.viz import build_t0_viz_payload

    viz = build_t0_viz_payload(
        days,
        stock_code=str(stock_code or ""),
        stock_name="",
        rules=cfg,
        initial_shares=initial_shares,
    )
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
        "missing_minute_days": missing_minute_days,
        "t0_pnl_total": total_pnl,
        "exposure_pnl_total": exposure_total,
        "t0_pnl_with_exposure": round(total_pnl + exposure_total, 2),
        "t0_win_rate_pct": round(win / len(pnls) * 100.0, 2) if pnls else None,
        "t0_win_days": win,
        "t0_loss_days": loss,
        "profit_factor": profit_factor,
        "rules": {
            "t0_ratio": cfg["t0_ratio"],
            "sell_trigger_pct": cfg["sell_trigger_pct"],
            "buy_trigger_pct": cfg["buy_trigger_pct"],
            "must_cover_same_day": cfg["must_cover_same_day"],
            "fill_mode": cfg["fill_mode"],
            "direction": cfg["direction"],
            "path_mode": cfg.get("path_mode"),
            "minute_period": cfg.get("minute_period"),
            "min_range_pct": cfg.get("min_range_pct"),
            "use_atr": cfg.get("use_atr"),
            "ref": cfg.get("ref"),
            "y_trade_floor": cfg.get("y_trade_floor"),
            "y_eod_prior": cfg.get("y_eod_prior"),
            "y_tau_enter": cfg.get("y_tau_enter"),
            "y_on_risk": cfg.get("y_on_risk"),
            "y_on_allow": cfg.get("y_on_allow"),
            "y_block_tau_nowcast_sign": cfg.get("y_block_tau_nowcast_sign"),
            "y_tau_nowcast_sign_eps": cfg.get("y_tau_nowcast_sign_eps"),
            "y_tau_map": cfg.get("y_tau_map"),
            "t0_pm_degrade": cfg.get("t0_pm_degrade"),
            "t0_pm_chase_interval_min": cfg.get("t0_pm_chase_interval_min"),
            "y_ratio_tau_soft_band": cfg.get("y_ratio_tau_soft_band"),
            "y_score_source": cfg.get("y_score_source"),
        },
        "days": days[-30:],
        # 成交样本：不限于最近 30 根日历日（避免近期全跳过时误以为全程无成交）
        "trade_days_sample": traded_days[-50:],
        "viz": viz,
    }
    report.update(derive_t0_quality_metrics(report))
    from core.t0.viz import attach_compare_to_viz

    attach_compare_to_viz(report)
    return report
