"""单日做 T 模拟（仅分钟第一触达；已删除日线 high/low 代理）。"""

import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Sequence

from core.numbers import now_iso_local as _now_iso
from core.t0.config import load_t0_rules
from core.t0.score_policy import (
    scores_have_any,
)


def _lot_floor(shares: float, lot: int) -> int:
    if shares <= 0:
        return 0
    return int(shares // lot) * lot


def _t0_qty_lots(
    shares: float,
    t0_ratio: float,
    lot: int,
    sellable: Optional[float] = None,
) -> int:
    """做T目标股数（整手）。

    ``shares * t0_ratio`` 取整不足 1 手、但可卖 ≥1 手时，抬到 1 手——
    否则小仓（100～200 股 × 40%）会整段回测静默 0 成交。
    """
    lot_i = max(int(lot or 0), 0)
    if lot_i <= 0 or shares <= 0 or float(t0_ratio or 0) <= 0:
        return 0
    cap = float(shares if sellable is None else sellable)
    cap = min(max(cap, 0.0), float(shares))
    raw = float(shares) * float(t0_ratio)
    qty = _lot_floor(min(cap, raw), lot_i)
    if qty <= 0 and cap >= lot_i and raw > 0:
        qty = lot_i
    return int(qty)


def _ref_price(bar: dict, cost: float, rules: dict) -> float:
    mode = str(rules.get("ref") or "cost")
    if mode == "open":
        return float(bar.get("open") or cost or 0)
    if mode == "prev_close":
        return float(bar.get("prev_close") or bar.get("open") or cost or 0)
    return float(cost or bar.get("open") or 0)


def atr_pct_from_bars(bars: Sequence[dict], window: int = 14) -> Optional[float]:
    """近 window 根的平均振幅占收盘价 %（简化 ATR%）。

    样本不足 ``min(window, 8)`` 时返回 None，避免过早用噪声 ATR 抬触发价。
    """
    min_n = min(max(2, int(window or 14)), 8)
    if not bars or len(bars) < min_n:
        return None
    w = max(min_n, min(int(window), len(bars)))
    slice_bars = list(bars)[-w:]
    ranges: List[float] = []
    for b in slice_bars:
        high = float(b.get("high") or 0)
        low = float(b.get("low") or 0)
        close = float(b.get("close") or 0)
        if high <= 0 or low <= 0 or close <= 0 or high < low:
            continue
        ranges.append((high - low) / close * 100.0)
    if len(ranges) < min_n:
        return None
    return round(sum(ranges) / len(ranges), 4)


def _fill_sell(optimistic_high: float, sell_level: float, mode: str) -> float:
    if mode == "optimistic":
        return optimistic_high
    if mode == "mid":
        return (optimistic_high + sell_level) / 2.0
    return sell_level


def _fill_buy(optimistic_low: float, buy_level: float, mode: str) -> float:
    if mode == "optimistic":
        return optimistic_low
    if mode == "mid":
        return (optimistic_low + buy_level) / 2.0
    return buy_level


def _error_result(msg: str, shares: float) -> Dict[str, Any]:
    return {
        "success": False,
        "error": msg,
        "trades": [],
        "pnl": 0.0,
        "shares_end": shares,
        "cash_delta": 0.0,
    }


def _skip_result(
    *,
    reason: str,
    shares: float,
    bar: Optional[dict] = None,
    extra: Optional[dict] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "success": True,
        "skipped": True,
        "reason": reason,
        "trades": [],
        "pnl": 0.0,
        "shares_end": shares,
        "cash_delta": 0.0,
        "sold_qty": 0,
        "covered_qty": 0,
        "uncovered_qty": 0,
        "bought_qty": 0,
        "sold_back_qty": 0,
        "exposure_pnl": 0.0,
        "direction_used": None,
    }
    if bar is not None:
        out["date"] = bar.get("date")
        try:
            o = float(bar.get("open") or 0)
            if o > 0:
                out["open"] = o
        except (TypeError, ValueError):
            pass
        try:
            c = float(bar.get("close") or bar.get("open") or 0)
            if c > 0:
                out["close"] = c
        except (TypeError, ValueError):
            pass
        try:
            pc = float(bar.get("prev_close") or 0)
            if pc > 0:
                out["prev_close"] = pc
        except (TypeError, ValueError):
            pass
    if extra:
        out.update(extra)
    return out



def _ensure_fixed_direction_path_y_tau(
    scores: Optional[dict], direction: Optional[str]
) -> Optional[dict]:
    """固定正/反 T 且无 ŷ 快照时：为 τ 出场 bound 注入符号化默认 ŷ（非 dual_y 选向）。"""
    from core.t0.minute_path import _score_y_tau

    d = str(direction or "").strip().lower()
    if d not in ("buy_then_sell", "sell_then_buy"):
        return scores
    snap = dict(scores) if isinstance(scores, dict) else {}
    if _score_y_tau(snap) is not None:
        return snap
    snap["y_tau"] = 0.5 if d == "buy_then_sell" else -0.5
    snap["y_tau_oc"] = snap["y_tau"]
    return snap


def simulate_t0_day(
    *,
    bar: dict,
    shares: float,
    cost: float,
    sellable_shares: Optional[float] = None,
    rules: Optional[dict] = None,
    cost_config: Optional[dict] = None,
    paper: Optional[dict] = None,
    stock_code: str = "",
    cash: float = 0.0,
    atr_pct: Optional[float] = None,
    hist_bars: Optional[Sequence[dict]] = None,
    minute_bars: Optional[Sequence[dict]] = None,
    scores: Optional[dict] = None,
    tau_pool_day: Optional[dict] = None,
    defer_eod: bool = False,
) -> Dict[str, Any]:
    """单票单日做 T：仅 5m 第一触达（已删除日线 high/low 代理）。

    ``defer_eod=True``：盘中前缀不强平（纸面整单/Worker）；回测完整日默认 False。
    """
    cfg = load_t0_rules(rules)
    cfg["path_mode"] = "first_touch"
    if not cfg.get("enabled", True):
        return _skip_result(reason="t0 disabled", shares=shares, bar=bar)

    score_snap = scores if isinstance(scores, dict) else None
    if str(cfg.get("direction") or "") == "dual_y" and not scores_have_any(score_snap):
        from core.t0.score_policy import (
            resolve_fuse_intraday,
            resolve_score_as_of,
            resolve_scores_for_code,
            resolve_y_score_source,
            tau_pool_day_score_kwargs,
        )

        as_of = resolve_score_as_of(
            hist_bars=hist_bars,
            day_bar=bar if isinstance(bar, dict) else None,
        )
        if stock_code:
            y_src = resolve_y_score_source(cfg)
            score_snap = resolve_scores_for_code(
                stock_code,
                hist_bars=hist_bars,
                day_bar=bar if isinstance(bar, dict) else None,
                as_of=as_of or None,
                source=y_src,
                fuse_intraday=resolve_fuse_intraday(cfg),
                allow_fallback=(y_src != "compute"),
                # 选向仅开盘信息集；分钟只用于第一触达成交
                use_minute_tau=False,
                **tau_pool_day_score_kwargs(tau_pool_day, stock_code),
            )

    score_snap = _ensure_fixed_direction_path_y_tau(score_snap, cfg.get("direction"))

    mins = list(minute_bars or [])
    from core.t0.config import t0_slots_enabled

    min_need = 1 if t0_slots_enabled(cfg) else 2
    if len(mins) < min_need:
        from core.t0.score_policy import attach_day_scores

        return attach_day_scores(
            _skip_result(
                reason="缺分钟线，跳过（已删除日线模拟）",
                shares=shares,
                bar=bar,
                extra={
                    "path_mode": "first_touch",
                    "skip_category": "missing_minute",
                    "signal_skip": False,
                },
            ),
            score_snap,
        )

    from core.t0.minute_path import simulate_t0_day_minute

    return simulate_t0_day_minute(
        bar=bar,
        minute_bars=mins,
        shares=shares,
        cost=cost,
        sellable_shares=sellable_shares,
        rules=cfg,
        cost_config=cost_config,
        paper=paper,
        stock_code=stock_code,
        cash=cash,
        atr_pct=atr_pct,
        hist_bars=hist_bars,
        scores=score_snap,
        tau_pool_day=tau_pool_day,
        defer_eod=bool(defer_eod),
    )



def _first_t0_leg_price(trades: Sequence[dict], side_suffix: str) -> float:
    for t in trades or []:
        if str(t.get("side") or "").endswith(side_suffix):
            px = float(t.get("price") or 0)
            if px > 0:
                return px
    return 0.0


def _bar_close_px(bar: dict) -> float:
    return float(bar.get("close") or bar.get("open") or 0)


def simulate_t0_on_holdings(
    paper: dict,
    *,
    bars_by_code: Dict[str, dict],
    rules: Optional[dict] = None,
    dry_run: bool = False,
    atr_by_code: Optional[Dict[str, float]] = None,
    hist_bars_by_code: Optional[Dict[str, List[dict]]] = None,
    minute_bars_by_code: Optional[Dict[str, List[dict]]] = None,
    stance_by_code: Optional[Dict[str, Any]] = None,
    coupling: Optional[dict] = None,
    scores_by_code: Optional[Dict[str, dict]] = None,
    log_source: str = "paper_t0",
    skip_codes: Optional[Any] = None,
    defer_eod: bool = False,
) -> Dict[str, Any]:
    """对纸面持仓逐票跑单日做 T（需传入当日 bar）。

    dry_run=True 时不改 paper，只返回预演结果。
    hist_bars_by_code：各票历史日线（不含当日），供 signal 选向。
    minute_bars_by_code：各票当日分钟线，有则第一触达。
    stance_by_code：code → stance_code 或 {stance_code,...}；配合 coupling.t0_vs_stance。
    scores_by_code：code → dual_y ŷ 快照。
    skip_codes：盘中已落账腿的代码，跳过以防整单回放重复落账。
    defer_eod：盘中整单为 True，避免半日分钟误强平。
    """
    from core.execution import stance_allows_t0

    # 显式 rules（含 Execution resolve 结果）优先；否则回退 paper.rules.t0
    if rules:
        cfg = load_t0_rules(rules)
    else:
        cfg = load_t0_rules((paper.get("rules") or {}).get("t0"))
    coup = coupling
    if coup is None:
        exe = ((paper.get("rules") or {}).get("execution") or {}) if isinstance(paper.get("rules"), dict) else {}
        coup = (exe.get("coupling") if isinstance(exe, dict) else None) or {}
    coup_mode = str((coup or {}).get("t0_vs_stance") or "independent")
    skip_set = {str(c) for c in (skip_codes or []) if str(c or "").strip()}

    holdings = []
    for h in paper.get("holdings") or []:
        row = dict(h)
        if isinstance(row.get("lots"), list):
            row["lots"] = [dict(x) for x in row["lots"] if isinstance(x, dict)]
        holdings.append(row)
    if not holdings:
        return {
            "success": True,
            "dry_run": dry_run,
            "trades": [],
            "pnl_total": 0.0,
            "exposure_pnl_total": 0.0,
            "results": [],
            "note": "无持仓，跳过做T",
        }

    cash = float(paper.get("cash") or 0)
    working_cash = cash
    all_trades: List[dict] = []
    results: List[dict] = []
    pnl_total = 0.0
    exposure_total = 0.0
    skip_count = 0
    signal_skip_count = 0
    coupling_skip_count = 0
    minute_path_count = 0

    # 预演用副本；确认时写回。按代码排序，共享现金池执行序确定可复现。
    work_holdings = sorted(
        holdings,
        key=lambda h: str((h or {}).get("stock_code") or ""),
    )

    for h in work_holdings:
        code = str(h.get("stock_code") or "")
        bar = bars_by_code.get(code)
        if not code or not bar:
            continue
        if code in skip_set:
            skip_count += 1
            results.append(
                {
                    "stock_code": code,
                    "stock_name": h.get("stock_name"),
                    "success": True,
                    "skipped": True,
                    "reason": "盘中已有成交腿，跳过整单回放（防重复落账）",
                    "skip_category": "intraday_legs_open",
                    "trades": [],
                    "pnl": 0.0,
                    "shares_end": float(h.get("shares") or 0),
                    "cash_delta": 0.0,
                }
            )
            continue
        shares = float(h.get("shares") or 0)
        cost = float(h.get("cost") or 0)
        from core.paper.tplus1 import (
            apply_t0_trades,
            sellable_shares as t1_sellable,
            session_date as t1_session,
        )

        # T+1 按「当前交易会话日」解冻。日线 bar.date 凌晨/盘前常停在昨收，
        # 若用 bar.date 会把昨日买入误判为当日锁定（可卖=0）。
        bar_day = str(bar.get("date") or "")[:10]
        sess = t1_session()
        as_of = sess or bar_day or None
        sellable = t1_sellable(h, as_of=as_of)

        # coupling vs stance
        stance_raw = None
        if stance_by_code and code in stance_by_code:
            stance_raw = stance_by_code.get(code)
        if isinstance(stance_raw, dict):
            stance_code = stance_raw.get("stance_code")
        else:
            stance_code = stance_raw
        allowed, coup_reason = stance_allows_t0(coup_mode, stance_code)
        if not allowed:
            coupling_skip_count += 1
            skip_count += 1
            results.append(
                {
                    "stock_code": code,
                    "stock_name": h.get("stock_name"),
                    "success": True,
                    "skipped": True,
                    "coupling_skip": True,
                    "reason": coup_reason,
                    "stance_code": stance_code,
                    "trades": [],
                    "pnl": 0.0,
                    "shares_end": shares,
                    "cash_delta": 0.0,
                }
            )
            continue

        atr = None
        if atr_by_code and code in atr_by_code:
            atr = atr_by_code.get(code)

        hist = None
        if hist_bars_by_code and code in hist_bars_by_code:
            hist = hist_bars_by_code.get(code)

        mins = None
        if minute_bars_by_code and code in minute_bars_by_code:
            mins = minute_bars_by_code.get(code)

        score_snap = None
        if scores_by_code and code in scores_by_code:
            score_snap = scores_by_code.get(code)

        day = simulate_t0_day(
            bar=bar,
            shares=shares,
            cost=cost,
            sellable_shares=float(sellable),
            rules=cfg,
            paper=paper,
            stock_code=code,
            cash=working_cash,
            atr_pct=atr,
            hist_bars=hist,
            minute_bars=mins,
            scores=score_snap,
            defer_eod=bool(defer_eod),
        )
        if mins and len(mins) >= 2 and day.get("path_mode") == "first_touch":
            minute_path_count += 1
        if day.get("skipped"):
            skip_count += 1
            if day.get("signal_skip"):
                signal_skip_count += 1
            results.append({"stock_code": code, "stock_name": h.get("stock_name"), **day})
            continue
        if not day.get("success"):
            results.append({"stock_code": code, "stock_name": h.get("stock_name"), **day})
            continue

        ts = _now_iso()
        from core.t0.costs import t0_fee_side, t0_leg_cash_delta

        applied_trades: List[dict] = []
        for t in day.get("trades") or []:
            row = dict(t)
            delta = float(t0_leg_cash_delta(row) or 0)
            if t0_fee_side(row.get("side")) == "buy" and working_cash + delta < -1e-6:
                continue
            row["ts"] = ts
            row["stock_name"] = h.get("stock_name")
            applied_trades.append(row)
            all_trades.append(row)

        apply_t0_trades(
            h,
            applied_trades,
            as_of=as_of or str(ts)[:10],
            ts=ts,
        )
        from core.paper.tplus1 import sellable_shares as t1_after

        day_out = dict(day)
        if len(applied_trades) != len(day.get("trades") or []):
            day_out["trades"] = applied_trades
            day_out["cash_delta"] = round(
                sum(float(t0_leg_cash_delta(t) or 0) for t in applied_trades), 2
            )
            # 买回被共享现金闸掉：按未回补敞口记账
            if str(day_out.get("direction_used") or "") == "sell_then_buy":
                sold_q = int(day_out.get("sold_qty") or 0)
                cov = sum(
                    int(t.get("shares") or 0)
                    for t in applied_trades
                    if str(t.get("side") or "").endswith("buy")
                )
                day_out["covered_qty"] = cov
                day_out["uncovered_qty"] = max(0, sold_q - cov)
                if sold_q > 0 and cov < sold_q:
                    day_out["exit_reason"] = "abandon_cover_cash"
                    day_out["pnl"] = 0.0
                    uncovered = sold_q - cov
                    sold_px = _first_t0_leg_price(applied_trades, "sell") or _first_t0_leg_price(
                        day.get("trades") or [], "sell"
                    )
                    close_px = _bar_close_px(bar)
                    if sold_px > 0 and close_px > 0 and uncovered > 0:
                        day_out["exposure_pnl"] = round(
                            (sold_px - close_px) * uncovered, 2
                        )
                elif cov >= sold_q > 0:
                    day_out["pnl"] = day_out["cash_delta"]
                    day_out["exposure_pnl"] = 0.0
            elif str(day_out.get("direction_used") or "") == "buy_then_sell":
                bought_q = int(day_out.get("bought_qty") or 0)
                sold_back = sum(
                    int(t.get("shares") or 0)
                    for t in applied_trades
                    if str(t.get("side") or "").endswith("sell")
                )
                # reverse: first leg is buy — if buy skipped, clear
                has_buy = any(
                    str(t.get("side") or "").endswith("buy") for t in applied_trades
                )
                if not has_buy:
                    day_out["bought_qty"] = 0
                    day_out["sold_back_qty"] = 0
                    day_out["uncovered_qty"] = 0
                    day_out["pnl"] = 0.0
                    day_out["skipped"] = True
                    day_out["reason"] = "共享现金不足，跳过正T加仓"
                else:
                    day_out["sold_back_qty"] = sold_back
                    uncovered = max(0, bought_q - sold_back)
                    day_out["uncovered_qty"] = uncovered
                    if sold_back >= bought_q > 0:
                        day_out["pnl"] = day_out["cash_delta"]
                        day_out["exposure_pnl"] = 0.0
                    elif uncovered > 0:
                        buy_px = _first_t0_leg_price(applied_trades, "buy") or _first_t0_leg_price(
                            day.get("trades") or [], "buy"
                        )
                        close_px = _bar_close_px(bar)
                        if buy_px > 0 and close_px > 0:
                            day_out["exposure_pnl"] = round(
                                (close_px - buy_px) * uncovered, 2
                            )
                        day_out["exit_reason"] = day_out.get("exit_reason") or "abandon_cover_cash"
                        day_out["pnl"] = (
                            day_out["cash_delta"] if sold_back > 0 else 0.0
                        )

        h["t0"] = {
            "enabled": True,
            "sellable_shares": t1_after(h, as_of=as_of or None),
            "day_sold": day_out.get("sold_qty") or 0,
            "day_bought": (day_out.get("covered_qty") or 0) + (day_out.get("bought_qty") or 0),
            "day_pnl": day_out.get("pnl") or 0,
            "day_exposure_pnl": day_out.get("exposure_pnl") or 0,
            "direction": day_out.get("direction_used"),
            "last_date": day_out.get("date"),
        }
        working_cash += float(day_out.get("cash_delta") or 0)
        pnl_total += float(day_out.get("pnl") or 0)
        exposure_total += float(day_out.get("exposure_pnl") or 0)
        results.append({"stock_code": code, "stock_name": h.get("stock_name"), **day_out})

    if not dry_run:
        for t in all_trades:
            paper.setdefault("trades", []).append(t)
        if all_trades:
            from core.paper.ledger import append_operation_log, append_trade_legs_to_operation_log

            src = str(log_source or "paper_t0").strip() or "paper_t0"
            append_trade_legs_to_operation_log(
                paper,
                sell_trades=[
                    t
                    for t in all_trades
                    if str(t.get("side") or "").strip().lower() in ("sell", "t0_sell")
                ],
                buy_trades=[
                    t
                    for t in all_trades
                    if str(t.get("side") or "").strip().lower() in ("buy", "t0_buy")
                ],
                origin="t0",
                source=src,
            )
            auto_tag = "自动" if src == "paper_t0_auto" else "手动"
            append_operation_log(
                paper,
                "t0_batch",
                detail=(
                    f"做T{auto_tag} · 成交 {len(all_trades)} 笔 · "
                    f"PnL {round(pnl_total, 2)} · 跳过 {skip_count}"
                ),
                meta={
                    "origin": "t0",
                    "source": src,
                    "trade_count": len(all_trades),
                    "pnl_total": round(pnl_total, 2),
                    "skip_count": skip_count,
                    "coupling_skip_count": coupling_skip_count,
                },
            )
        paper["holdings"] = [h for h in work_holdings if float(h.get("shares") or 0) > 0]
        paper["cash"] = round(working_cash, 2)
        paper["updated_at"] = _now_iso()

    return {
        "success": True,
        "task": "t0_simulate",
        "dry_run": dry_run,
        "trades": all_trades,
        "pnl_total": round(pnl_total, 2),
        "exposure_pnl_total": round(exposure_total, 2),
        "skip_count": skip_count,
        "signal_skip_count": signal_skip_count,
        "coupling_skip_count": coupling_skip_count,
        "coupling_mode": coup_mode,
        "minute_path_count": minute_path_count,
        "results": results,
        "cash_after": round(working_cash, 2),
        "cash_before": round(cash, 2),
        "note": (
            ("预演 · " if dry_run else "")
            + "底仓做T；仅 5m 第一触达（缺分钟跳过）；非实盘、不代客下单。"
            + (f" · 耦合跳过 {coupling_skip_count}" if coupling_skip_count else "")
        ),
    }
