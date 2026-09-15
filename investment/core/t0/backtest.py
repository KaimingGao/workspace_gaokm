"""底仓做 T 回测（仅 5m 第一触达；已删除日线模拟）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional

from core.research.cx_panel import pack_y_complexity_fields, pack_y_tpd_fields
from core.research.r_ridge import pack_y_r_fields
from core.t0.config import T0_TRADE_DAYS_SAMPLE_UI_LIMIT, load_t0_rules
from core.t0.minute_path import T0_INTENTIONAL_ABANDON_EXITS
from core.t0.rules import _t0_qty_lots, simulate_t0_day


def _research_cash_for_buy_then_sell(
    shares: float,
    px: float,
    *,
    t0_ratio: float,
    lot: int = 100,
) -> float:
    """正T研究现金：至少够买「抬手后」目标股数（含简易佣金缓冲）。"""
    if shares <= 0 or px <= 0:
        return 0.0
    lot_i = max(int(lot or 100), 1)
    qty = _t0_qty_lots(shares, t0_ratio, lot_i, shares)
    # 相对触发价略低 + 佣金下限，避免 simple_cn 下刚好买不起
    return float(qty) * float(px) * 1.05 + 10.0


def summarize_t0_day_legs(day: Dict[str, Any]) -> Dict[str, Any]:
    """从单日 trades / touch_* 提炼 UI 校验字段（价必有；时仅分钟路径）。

    多轮混合向或同日多槽成交时不压成一对买卖价，交给 trades 过程列。
    """
    if str(day.get("direction_used") or day.get("direction") or "") == "mixed":
        return {}
    rows = day.get("t0_slot_results") if day.get("t0_slots_enabled") else None
    if isinstance(rows, list):
        filled = [
            r
            for r in rows
            if isinstance(r, dict)
            and not r.get("skipped")
            and int(r.get("trades") or 0) > 0
        ]
        if len(filled) > 1:
            return {}
    trades = [t for t in (day.get("trades") or []) if isinstance(t, dict)]
    direction = str(day.get("direction_used") or day.get("direction") or "")
    sells = [t for t in trades if str(t.get("side") or "").lower().endswith("sell")]
    buys = [t for t in trades if str(t.get("side") or "").lower().endswith("buy")]
    rev = direction == "buy_then_sell"
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
    """回测日明细：振幅/前缀审计字段。无成交日不落 forward_trace（明细只记成交）。"""
    out: Dict[str, Any] = {}
    keys = [
        "range_mode",
        "prefix_bars",
        "range_pct",
        "price_space",
        "price_space_scale",
        "price_space_mode",
        "close_band_scan",
    ]
    if not day.get("skipped"):
        keys.append("forward_trace")
    for k in keys:
        v = day.get(k)
        if v is not None:
            out[k] = v
    return out


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

    return {
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

def _slot_round_tally(day: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """多轮日：按槽位分向记账；缺槽位明细则回落单日口径。"""
    rows = day.get("t0_slot_results")
    if not day.get("t0_slots_enabled") or not isinstance(rows, list) or not rows:
        return None
    saw_stb = False
    saw_bts = False
    stb_pnl = 0.0
    bts_pnl = 0.0
    stb_complete = True
    bts_complete = True
    any_uncover = False
    any_fill = False
    for r in rows:
        if not isinstance(r, dict) or r.get("skipped"):
            continue
        direction = str(r.get("direction") or "")
        sold = int(r.get("sold_qty") or 0)
        covered = int(r.get("covered_qty") or 0)
        bought = int(r.get("bought_qty") or 0)
        sold_back = int(r.get("sold_back_qty") or 0)
        if sold <= 0 and bought <= 0:
            continue
        any_fill = True
        exit_r = str(r.get("exit_reason") or "")
        pnl = float(r.get("pnl") or 0)
        if direction == "sell_then_buy":
            saw_stb = True
            stb_pnl += pnl
            done = covered >= sold or (
                exit_r in T0_INTENTIONAL_ABANDON_EXITS and covered < sold
            )
            if not done:
                stb_complete = False
                any_uncover = True
        elif direction == "buy_then_sell":
            saw_bts = True
            bts_pnl += pnl
            done = sold_back >= bought or (
                exit_r in T0_INTENTIONAL_ABANDON_EXITS and sold_back < bought
            )
            if not done:
                bts_complete = False
                any_uncover = True
    if not any_fill:
        return None
    return {
        "saw_stb": saw_stb,
        "saw_bts": saw_bts,
        "stb_pnl": stb_pnl,
        "bts_pnl": bts_pnl,
        "stb_complete": (not saw_stb) or stb_complete,
        "bts_complete": (not saw_bts) or bts_complete,
        "all_complete": ((not saw_stb) or stb_complete) and ((not saw_bts) or bts_complete),
        "any_uncover": any_uncover,
        "mixed": bool(saw_stb and saw_bts),
    }


def backtest_t0_on_bars(
    bars: List[dict],
    *,
    initial_shares: float = 1000,
    initial_cost: Optional[float] = None,
    initial_cash: float = 0.0,
    rules: Optional[dict] = None,
    cost_config: Optional[dict] = None,
    stock_code: str = "",
    minute_by_date: Optional[Dict[str, List[dict]]] = None,
    require_minute: bool = False,
    bars_history: Optional[List[dict]] = None,
    eval_lookback: Optional[int] = None,
    tau_pool_by_date: Optional[Dict[str, Dict[str, Any]]] = None,
    stock_name: str = "",
) -> Dict[str, Any]:
    """Walk 底仓做 T。

    默认 fill_mode=trigger。仅 5m 第一触达；缺分钟覆盖日跳过（已删除日线模拟）。
    ``bars`` = 评估窗内交易日；``bars_history``（可选）= 含 warmup 的全量日线，
    供 dual_y / ATR 的 hist_prior，不改变评估窗长度。
    ``tau_pool_by_date``：按日截面缺口（与刷簿 ŷ_τ 特征对齐）。
    require_minute=True 且无 minute_by_date 时直接失败。
    ``cost_config`` 缺省时用 CostPort 研究费率（禁止隐式零成本抬高 PnL）。
    """
    from core.research.holdout import (
        MODEL_ROLE_RESEARCH,
        current_scoring_model_role,
        scoring_model_role_context,
    )

    if current_scoring_model_role() != MODEL_ROLE_RESEARCH:
        import inspect

        allowed = inspect.signature(backtest_t0_on_bars).parameters
        params = {
            k: v for k, v in locals().items() if k in allowed and k != "bars"
        }
        # 不在这里清模型缓存：逐票回测会进本函数多次，清缓存会把研究套反复从盘重载，
        # 6 票即可把 240s 时限吃光。_scoring_models 已按 role 分桶，不会误用 live。
        with scoring_model_role_context(MODEL_ROLE_RESEARCH):
            return backtest_t0_on_bars(bars, **params)
    if require_minute and not minute_by_date:
        return {
            "success": False,
            "error": "做T回测已删除日线模拟，需 5 分钟 K 线；请检查分钟源/缓存",
            "task": "t0_backtest",
        }
    if cost_config is None:
        from core.t0.costs import default_t0_research_cost_config

        cost_config = default_t0_research_cost_config()
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
    # 回测禁 live_book / ledger（当日簿与冻结账本会前视）
    if str(cfg.get("y_score_source") or "").strip().lower() not in {
        "compute",
        "pit",
        "live",
        "realtime",
        "on_the_fly",
        "",
    }:
        cfg = dict(cfg)
        cfg["y_score_source"] = "compute"
        cfg["_y_score_source_forced"] = "compute"
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
        stock_name=str(stock_name or ""),
    )
    if not primary.get("success"):
        return primary

    primary.update(derive_t0_quality_metrics(primary))
    if bars_history is not None and len(history) > len(bars):
        primary["eval_lookback"] = int(eval_lookback or len(bars))
        primary["score_warmup_bars"] = max(0, len(history) - len(bars))
    primary["note"] = (
        "底仓做T回测；默认 trigger 成交；"
        f"direction={cfg.get('direction')} · "
        f"y_score_source={cfg.get('y_score_source')} · "
        f"y_tau_enter=±{cfg.get('y_tau_enter')}%；"
        + "仅 5m 第一触达（缺分钟日跳过，已删除日线模拟）；"
        + (
            f"评估窗 {len(bars)} 日 · 因子缓冲 {max(0, len(history) - len(bars))} 日；"
            if bars_history is not None and len(history) > len(bars)
            else ""
        )
        + (
            "多轮槽位（默认开）与纸面同一套 ŷ+前缀确认 / 止损；"
            if cfg.get("t0_slots_enabled")
            else ""
        )
        + "T+1（反T卖旧买回 / 正T买新卖旧换仓）；非实盘、不保证收益。"
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
    stock_name: str = "",
) -> Dict[str, Any]:
    cfg = load_t0_rules(rules)
    # 回测禁 live_book / ledger 前视
    if str(cfg.get("y_score_source") or "").strip().lower() not in {
        "compute",
        "pit",
        "live",
        "realtime",
        "on_the_fly",
        "",
    }:
        cfg = dict(cfg)
        cfg["y_score_source"] = "compute"
        cfg["_y_score_source_forced"] = "compute"
    from core.t0.costs import resolve_t0_cost_context

    _cost_model_name, _ = resolve_t0_cost_context(cost_config=cost_config)
    history = list(bars_history or bars)
    hist_index_by_date = {
        str(b.get("date") or ""): i for i, b in enumerate(history) if b.get("date")
    }
    shares = float(initial_shares)
    cost = float(initial_cost if initial_cost is not None else bars[0].get("close") or 0)
    if shares <= 0 or cost <= 0:
        return {"success": False, "error": "无效初始仓位"}

    sellable = shares  # 回测：日初持仓均可卖；多轮引擎 merge/sequential 按 sellable 约束
    cash = float(initial_cash or 0)
    lot = max(int(cfg.get("lot_size") or 100), 1)
    ratio = float(cfg.get("t0_ratio") or 1.0)
    # 正T / dual_y：研究现金须覆盖「抬手后」目标股数（200×40%→抬到100股）
    if cash <= 0 and str(cfg.get("direction") or "auto") in {
        "auto",
        "buy_then_sell",
        "signal",
        "dual_y",
    }:
        cash = _research_cash_for_buy_then_sell(shares, cost, t0_ratio=ratio, lot=lot)
    tau_pool = tau_pool_by_date if isinstance(tau_pool_by_date, dict) else {}

    days: List[dict] = []
    pnls: List[float] = []
    exposures: List[float] = []
    trade_count = 0
    cover_count = 0
    uncover_days = 0
    skip_count = 0
    signal_skip_days = 0
    sell_then_buy_days = 0
    buy_then_sell_days = 0
    sell_then_buy_pnl = 0.0
    buy_then_sell_pnl = 0.0
    sell_then_buy_cover = 0
    buy_then_sell_cover = 0
    mixed_days = 0
    minute_days = 0
    missing_minute_days = 0
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
        # auto/signal 依赖昨收；日线源未必带 prev_close
        bar_day = dict(bar)
        if gi > 0 and not bar_day.get("prev_close"):
            prev_c = float(history[gi - 1].get("close") or 0)
            if prev_c > 0:
                bar_day["prev_close"] = prev_c
        # 研究现金：每日补足到正T目标股数所需（防半腿耗尽后永久停做）。
        # 会掩盖累积亏损下的真实资金约束；严格回测应关掉补足并记现金不足跳过。
        # 用 open/prev_close 定补足额，避免 T 日 close 前视
        if str(cfg.get("direction") or "") in {"auto", "buy_then_sell", "signal", "dual_y"}:
            px = float(
                bar_day.get("open") or bar_day.get("prev_close") or cost or 0
            )
            if px > 0 and shares > 0:
                need = _research_cash_for_buy_then_sell(shares, px, t0_ratio=ratio, lot=lot)
                if cash < need * 0.95:
                    cash = max(cash, need)
        hist_prior = history[:gi]
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
                    "open": float(bar_day.get("open") or 0) or None,
                    "close": float(bar.get("close") or bar.get("open") or 0) or None,
                    "prev_close": float(bar_day.get("prev_close") or 0) or None,
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
            from core.t0.score_policy import (
                resolve_fuse_intraday,
                resolve_scores_for_code,
                resolve_y_score_source,
            )

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
                fuse_intraday=resolve_fuse_intraday(cfg),
                allow_fallback=(src != "compute"),
                as_of=str(history[gi - 1].get("date") or "")[:10] if gi > 0 else dkey[:10],
                pool_gaps=pool_day.get("pool_gaps"),
                sector_gap_breadth=pool_day.get("sector_gap_breadth"),
                sector_gap_median=ref_map.get(stock_code),
                # 选向禁分钟前缀 / 开→τ；成交仍用下方 mins
                use_minute_tau=False,
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
                    "path_abandon": bool(day.get("path_abandon")),
                    "directional_amplitude": day.get("directional_amplitude"),
                    "prefix_bars": day.get("prefix_bars"),
                    "direction": day.get("direction_used") or day.get("direction"),
                    "path_realized": day.get("path_realized"),
                    "path_realized_reason": day.get("path_realized_reason"),
                    "path_realized_trig": day.get("path_realized_trig"),
                    **pack_y_complexity_fields(day),
                    **pack_y_tpd_fields(day),
                    **pack_y_r_fields(day),
                    "cx_efficiency": day.get("cx_efficiency"),
                    "eod_realized": day.get("eod_realized"),
                    "tau_realized": day.get("tau_realized"),
                    "open": day.get("open")
                    if day.get("open") is not None
                    else (float(bar.get("open") or 0) or None),
                    "prev_close": day.get("prev_close")
                    if day.get("prev_close") is not None
                    else (float(bar_day.get("prev_close") or 0) or None),
                    # 跳过日也保留各钟因果 ŷ，供分槽画像（对齐拟合 by_tau）
                    "t0_slots_enabled": bool(day.get("t0_slots_enabled")),
                    "t0_slot_results": day.get("t0_slot_results") or [],
                    "y_tau_portrait_oc": day.get("y_tau_portrait_oc"),
                    "y_path_portrait": day.get("y_path_portrait"),
                    "portrait_prefix_bars": day.get("portrait_prefix_bars"),
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
        slot_tally = _slot_round_tally(day)
        if slot_tally:
            trade_count += 1
            if slot_tally["saw_stb"]:
                sell_then_buy_pnl += float(slot_tally["stb_pnl"] or 0)
                if slot_tally["stb_complete"]:
                    sell_then_buy_cover += 1
            if slot_tally["saw_bts"]:
                buy_then_sell_pnl += float(slot_tally["bts_pnl"] or 0)
                if slot_tally["bts_complete"]:
                    buy_then_sell_cover += 1
            if slot_tally.get("mixed"):
                mixed_days += 1
            elif slot_tally["saw_stb"]:
                sell_then_buy_days += 1
            elif slot_tally["saw_bts"]:
                buy_then_sell_days += 1
            if slot_tally["all_complete"]:
                cover_count += 1
            if slot_tally["any_uncover"]:
                uncover_days += 1
        elif sold > 0 or bought > 0:
            trade_count += 1
            completed = (sold > 0 and covered >= sold) or (bought > 0 and sold_back >= bought)
            exit_r = str(day.get("exit_reason") or "")
            # 正/反 T 主动放弃回补（含现金不足 / 可卖旧仓不足）按设计完成，不计入 uncover
            intentional_abandon = exit_r in T0_INTENTIONAL_ABANDON_EXITS and (
                (direction == "sell_then_buy" and sold > 0 and covered < sold)
                or (direction == "buy_then_sell" and bought > 0 and sold_back < bought)
            )
            if direction == "sell_then_buy":
                sell_then_buy_days += 1
                if completed or intentional_abandon:
                    sell_then_buy_cover += 1
                    cover_count += 1
            elif direction == "buy_then_sell":
                buy_then_sell_days += 1
                if completed or intentional_abandon:
                    buy_then_sell_cover += 1
                    cover_count += 1
            if not intentional_abandon and (
                uncovered > 0 or (bought > 0 and sold_back == 0)
            ):
                uncover_days += 1
            if day_pnl:
                if direction == "sell_then_buy":
                    sell_then_buy_pnl += day_pnl
                elif direction == "buy_then_sell":
                    buy_then_sell_pnl += day_pnl
        sellable = shares  # 日末重置；不跟踪当日新买股的 T+1 冻结

        # 胜率分母含已开仓日（含 pnl=0 的 abandon），避免只统计有已实现盈亏的子集
        if sold > 0 or bought > 0:
            pnls.append(day_pnl)
        elif day_pnl:
            pnls.append(day_pnl)
        if day.get("exposure_pnl"):
            exposures.append(float(day["exposure_pnl"]))

        days.append(
            {
                "date": bar.get("date"),
                "open": float(bar.get("open") or 0) or None,
                "direction": direction,
                "sold_qty": sold,
                "covered_qty": covered,
                "uncovered_qty": uncovered,
                "bought_qty": bought,
                "sold_back_qty": sold_back,
                "pnl": day_pnl,
                "exposure_pnl": day.get("exposure_pnl") or 0,
                "day_return_pct": day.get("day_return_pct"),
                "leg1_notional": day.get("leg1_notional"),
                "shares": shares,
                "close": float(bar.get("close") or bar.get("open") or 0) or None,
                "fill_mode": day.get("fill_mode"),
                "atr_pct": day.get("atr_pct"),
                "direction_score": day.get("direction_score"),
                "direction_reason": day.get("direction_reason"),
                "direction_features": day.get("direction_features"),
                "scores": day.get("scores"),
                "cover_policy": day.get("cover_policy"),
                "exit_reason": day.get("exit_reason"),
                "must_cover_same_day": day.get("must_cover_same_day"),
                "path_mode": day.get("path_mode") or cfg.get("path_mode") or "first_touch",
                "minute_path": used_minute,
                "path_realized": day.get("path_realized"),
                "path_realized_reason": day.get("path_realized_reason"),
                "path_realized_trig": day.get("path_realized_trig"),
                **pack_y_complexity_fields(day),
                **pack_y_tpd_fields(day),
                **pack_y_r_fields(day),
                "cx_efficiency": day.get("cx_efficiency"),
                "eod_realized": day.get("eod_realized"),
                "tau_realized": day.get("tau_realized"),
                "prev_close": day.get("prev_close")
                if day.get("prev_close") is not None
                else (float(bar_day.get("prev_close") or 0) or None),
                "touch_sell_at": day.get("touch_sell_at"),
                "touch_cover_at": day.get("touch_cover_at") or day.get("touch_buy_at"),
                "touch_buy_at": day.get("touch_buy_at"),
                "trades": day.get("trades") or [],
                "t0_ratio_base": day.get("t0_ratio_base"),
                "t0_ratio": day.get("t0_ratio"),
                "t0_slots_enabled": bool(day.get("t0_slots_enabled")),
                "t0_slot_results": day.get("t0_slot_results") or [],
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
        stock_name=str(stock_name or ""),
        rules=cfg,
        initial_shares=initial_shares,
    )
    report: Dict[str, Any] = {
        "success": True,
        "task": "t0_backtest",
        "stock_code": stock_code,
        "score_model_role": "research",
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
        "sell_then_buy_days": sell_then_buy_days,
        "buy_then_sell_days": buy_then_sell_days,
        "mixed_days": mixed_days,
        "sell_then_buy_pnl": round(sell_then_buy_pnl, 2),
        "buy_then_sell_pnl": round(buy_then_sell_pnl, 2),
        "sell_then_buy_cover_days": sell_then_buy_cover,
        "buy_then_sell_cover_days": buy_then_sell_cover,
        "minute_path_days": minute_days,
        "missing_minute_days": missing_minute_days,
        "t0_pnl_total": total_pnl,
        "exposure_pnl_total": exposure_total,
        "t0_pnl_with_exposure": round(total_pnl + exposure_total, 2),
        "cost_model": _cost_model_name,
        "t0_win_rate_pct": round(win / len(pnls) * 100.0, 2) if pnls else None,
        "t0_win_days": win,
        "t0_loss_days": loss,
        "profit_factor": profit_factor,
        "rules": {
            "t0_ratio": cfg["t0_ratio"],
            "must_cover_same_day": cfg["must_cover_same_day"],
            "must_cover_same_day_sell_then_buy": cfg.get("must_cover_same_day_sell_then_buy"),
            "must_cover_same_day_buy_then_sell": cfg.get("must_cover_same_day_buy_then_sell"),
            "fill_mode": cfg["fill_mode"],
            "fill_mode_sell_then_buy": cfg.get("fill_mode_sell_then_buy"),
            "fill_mode_buy_then_sell": cfg.get("fill_mode_buy_then_sell"),
            "direction": cfg["direction"],
            "path_mode": cfg.get("path_mode"),
            "minute_period": cfg.get("minute_period"),
            "ref": cfg.get("ref"),
            "y_trade_enter": cfg.get("y_trade_enter") or cfg.get("y_trade_floor"),
            "y_trade_floor": cfg.get("y_trade_floor") or cfg.get("y_trade_enter"),
            "y_tau_enter": cfg.get("y_tau_enter"),
            "y_tau_enter_sell_then_buy": cfg.get("y_tau_enter_sell_then_buy"),
            "y_tau_enter_buy_then_sell": cfg.get("y_tau_enter_buy_then_sell"),
            "y_enter_enabled": cfg.get("y_enter_enabled"),
            "y_enter_alt_enabled": cfg.get("y_enter_alt_enabled"),
            "y_tau_enter_alt": cfg.get("y_tau_enter_alt"),
            "y_tc_enter": cfg.get("y_tc_enter")
            if cfg.get("y_tc_enter") not in (None, "")
            else cfg.get("y_τc_enter"),
            "y_τc_enter": cfg.get("y_τc_enter") or cfg.get("y_tc_enter"),
            "y_tc_enter_alt": cfg.get("y_tc_enter_alt")
            if cfg.get("y_tc_enter_alt") not in (None, "")
            else cfg.get("y_τc_enter_alt"),
            "y_τc_enter_alt": cfg.get("y_τc_enter_alt") or cfg.get("y_tc_enter_alt"),
            "y_path_enter_alt": cfg.get("y_path_enter_alt"),
            "y_on_risk": cfg.get("y_on_risk"),
            "y_on_allow": cfg.get("y_on_allow"),
            "y_use_path": cfg.get("y_use_path"),
            "t0_y_oc_target_scale": cfg.get("t0_y_oc_target_scale"),
            "t0_y_oc_l": cfg.get("t0_y_oc_l"),
            "t0_y_oc_u": cfg.get("t0_y_oc_u"),
            "y_path_enter": cfg.get("y_path_enter"),
            "y_path_enter_sell_then_buy": cfg.get("y_path_enter_sell_then_buy"),
            "y_path_enter_buy_then_sell": cfg.get("y_path_enter_buy_then_sell"),
            "y_path_strong": cfg.get("y_path_strong"),
            "y_complexity_max": (
                cfg.get("y_complexity_max")
                if cfg.get("y_complexity_max") not in (None, "")
                else cfg.get("y_cx_max")
            ),
            "y_cx_max": (
                cfg.get("y_complexity_max")
                if cfg.get("y_complexity_max") not in (None, "")
                else cfg.get("y_cx_max")
            ),
            "y_tpd_max": cfg.get("y_tpd_max"),
            "y_complexity_max_alt": cfg.get("y_complexity_max_alt"),
            "y_tpd_max_alt": cfg.get("y_tpd_max_alt"),
            "y_path_required": cfg.get("y_path_required"),
            "t0_close_band_delta_pct": cfg.get("t0_close_band_delta_pct"),
            "t0_price_space_gate": cfg.get("t0_price_space_gate"),
            "t0_price_space_max_dev_pct": cfg.get("t0_price_space_max_dev_pct"),
            "t0_price_space_prev_dev_pct": cfg.get("t0_price_space_prev_dev_pct"),
            "t0_round_ratio": cfg.get("t0_round_ratio"),
            "t0_max_position_pct": cfg.get("t0_max_position_pct"),
            "t0_slots_max_rounds": cfg.get("t0_slots_max_rounds"),
            "t0_slots_enabled": cfg.get("t0_slots_enabled"),
            "y_tau_exit_price_skip": cfg.get("y_tau_exit_price_skip"),
            "y_tau_exit_price_mult": cfg.get("y_tau_exit_price_mult"),
            "y_tau_exit_price_skip_buy_then_sell": cfg.get(
                "y_tau_exit_price_skip_buy_then_sell"
            ),
            "y_tau_exit_price_mult_buy_then_sell": cfg.get(
                "y_tau_exit_price_mult_buy_then_sell"
            ),
            "y_tau_exit_price_skip_sell_then_buy": cfg.get(
                "y_tau_exit_price_skip_sell_then_buy"
            ),
            "y_tau_exit_price_mult_sell_then_buy": cfg.get(
                "y_tau_exit_price_mult_sell_then_buy"
            ),
            "y_tau_exit_price_bias": cfg.get("y_tau_exit_price_bias"),
            "y_tau_exit_price_move_min": cfg.get("y_tau_exit_price_move_min"),
            "y_tau_exit_price_move_max": cfg.get("y_tau_exit_price_move_max"),
            "y_tau_exit_price_bias_buy_then_sell": cfg.get(
                "y_tau_exit_price_bias_buy_then_sell"
            ),
            "y_tau_exit_price_move_min_buy_then_sell": cfg.get(
                "y_tau_exit_price_move_min_buy_then_sell"
            ),
            "y_tau_exit_price_move_max_buy_then_sell": cfg.get(
                "y_tau_exit_price_move_max_buy_then_sell"
            ),
            "y_tau_exit_price_bias_sell_then_buy": cfg.get(
                "y_tau_exit_price_bias_sell_then_buy"
            ),
            "y_tau_exit_price_move_min_sell_then_buy": cfg.get(
                "y_tau_exit_price_move_min_sell_then_buy"
            ),
            "y_tau_exit_price_move_max_sell_then_buy": cfg.get(
                "y_tau_exit_price_move_max_sell_then_buy"
            ),
            "t0_pm_degrade": cfg.get("t0_pm_degrade"),
            "t0_pm_degrade_sell_then_buy": cfg.get("t0_pm_degrade_sell_then_buy"),
            "t0_pm_degrade_buy_then_sell": cfg.get("t0_pm_degrade_buy_then_sell"),
            "t0_pm_chase_interval_min": cfg.get("t0_pm_chase_interval_min"),
            "t0_pm_chase_interval_min_sell_then_buy": cfg.get("t0_pm_chase_interval_min_sell_then_buy"),
            "t0_pm_chase_interval_min_buy_then_sell": cfg.get("t0_pm_chase_interval_min_buy_then_sell"),
            "t0_stop_pct_buy_then_sell": cfg.get("t0_stop_pct_buy_then_sell"),
            "t0_stop_pct_sell_then_buy": cfg.get("t0_stop_pct_sell_then_buy"),
            "t0_stop_arm_bars": cfg.get("t0_stop_arm_bars"),
            "t0_giveback_pct_buy_then_sell": cfg.get("t0_giveback_pct_buy_then_sell"),
            "t0_giveback_pct_sell_then_buy": cfg.get("t0_giveback_pct_sell_then_buy"),
            "t0_giveback_arm_pct": cfg.get("t0_giveback_arm_pct"),
            "t0_stop_on_close": cfg.get("t0_stop_on_close"),
            "t0_slots_enabled": cfg.get("t0_slots_enabled"),
            "t0_slots": cfg.get("t0_slots"),
            "y_score_source": cfg.get("y_score_source"),
        },
        "days": days[-30:],
        # 成交样本：不限于最近 30 根日历日（避免近期全跳过时误以为全程无成交）
        "trade_days_sample": traded_days[-T0_TRADE_DAYS_SAMPLE_UI_LIMIT:],
        "viz": viz,
    }
    report.update(derive_t0_quality_metrics(report))
    from core.t0.viz import attach_summary_to_viz

    attach_summary_to_viz(report)
    return report
