"""单日做 T 模拟（仅分钟第一触达；已删除日线 high/low 代理）。"""

import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.numbers import now_iso_local as _now_iso
from core.t0.config import load_t0_rules, resolve_min_range_pct
from core.t0.score_policy import (
    resolve_cover_policy,
    resolve_dual_y_direction,
    scores_from_item,
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


def scale_triggers_with_atr(
    cfg: dict, *, atr_pct: Optional[float]
) -> Dict[str, float]:
    sell = float(cfg["sell_trigger_pct"])
    buy = float(cfg["buy_trigger_pct"])
    if not cfg.get("use_atr") or atr_pct is None or atr_pct <= 0:
        return {"sell_trigger_pct": sell, "buy_trigger_pct": buy, "atr_pct": atr_pct}
    sell = max(sell, atr_pct * float(cfg["atr_sell_mult"]))
    buy = max(buy, atr_pct * float(cfg["atr_buy_mult"]))
    sell = min(sell, 20.0)
    buy = min(buy, 20.0)
    return {
        "sell_trigger_pct": round(sell, 4),
        "buy_trigger_pct": round(buy, 4),
        "atr_pct": atr_pct,
    }


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score_t0_direction(
    *,
    bar: dict,
    cfg: dict,
    hist_bars: Optional[Sequence[dict]] = None,
    atr_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """开盘可用特征打方向分（约 -1～+1），无前视。

    特征：跳空%、昨收位置、近3日动量、gap/ATR%。
    """
    open_px = float(bar.get("open") or 0)
    prev_close = float(bar.get("prev_close") or 0)
    hist = list(hist_bars or [])
    # hist 应为「不含当日」的历史；若误含当日则去掉最后一根与 bar 同日的
    if hist and str(hist[-1].get("date") or "") == str(bar.get("date") or ""):
        hist = hist[:-1]
    if prev_close <= 0 and hist:
        prev_close = float(hist[-1].get("close") or 0)

    features: Dict[str, Any] = {
        "gap_pct": None,
        "yclose_loc": None,
        "mom3_pct": None,
        "gap_atr": None,
        "atr_pct": atr_pct,
    }
    if open_px <= 0 or prev_close <= 0:
        return {
            "score": 0.0,
            "features": features,
            "reason": "缺今开或昨收，无法打分",
            "ok": False,
        }

    gap_pct = (open_px / prev_close - 1.0) * 100.0
    features["gap_pct"] = round(gap_pct, 4)
    # 跳空映射到 [-1,1]：约 ±2% 饱和
    gap_n = _clip(gap_pct / 2.0)

    yclose_loc_n = 0.0
    if hist:
        y = hist[-1]
        yh = float(y.get("high") or 0)
        yl = float(y.get("low") or 0)
        yc = float(y.get("close") or prev_close)
        yr = yh - yl
        if yr > 1e-9 and yc > 0:
            loc = (yc - yl) / yr
            features["yclose_loc"] = round(loc, 4)
            # 昨收偏高 → 偏正T延续；偏低 → 偏反T
            yclose_loc_n = _clip((loc - 0.5) * 2.0)

    mom3_n = 0.0
    if len(hist) >= 4:
        c0 = float(hist[-4].get("close") or 0)
        c1 = float(hist[-1].get("close") or 0)
        if c0 > 0 and c1 > 0:
            mom3 = (c1 / c0 - 1.0) * 100.0
            features["mom3_pct"] = round(mom3, 4)
            mom3_n = _clip(mom3 / 4.0)

    atr = atr_pct
    if atr is None or atr <= 0:
        atr = atr_pct_from_bars(hist, int(cfg.get("atr_window") or 14)) if hist else None
    features["atr_pct"] = atr
    gap_atr_n = 0.0
    if atr and atr > 1e-6:
        gap_atr = gap_pct / atr
        features["gap_atr"] = round(gap_atr, 4)
        gap_atr_n = _clip(gap_atr / 1.2)

    score = (
        float(cfg["w_gap"]) * gap_n
        + float(cfg["w_yclose_loc"]) * yclose_loc_n
        + float(cfg["w_mom3"]) * mom3_n
        + float(cfg["w_gap_atr"]) * gap_atr_n
    )
    score = round(_clip(score), 4)
    bits = [f"gap{gap_pct:+.2f}%"]
    if features.get("yclose_loc") is not None:
        bits.append(f"昨位{features['yclose_loc']:.2f}")
    if features.get("mom3_pct") is not None:
        bits.append(f"mom3{features['mom3_pct']:+.2f}%")
    if features.get("gap_atr") is not None:
        bits.append(f"gap/ATR{features['gap_atr']:+.2f}")
    return {
        "score": score,
        "features": features,
        "reason": " · ".join(bits) + f" → score {score:+.2f}",
        "ok": True,
    }


def _auto_direction(
    *,
    bar: dict,
    ref: float,
    cfg: dict,
    cash: float,
    shares: float,
) -> Tuple[str, str]:
    """auto 选向：按跳空强弱返回 (direction, reason_tag)；不明默认正 T。"""
    open_px = float(bar.get("open") or 0)
    gap_ref = float(bar.get("prev_close") or 0)
    if gap_ref <= 0:
        trig_ref = float(ref or 0)
        if trig_ref > 0 and abs(open_px - trig_ref) / max(trig_ref, 1e-9) > 1e-6:
            gap_ref = trig_ref
    if open_px <= 0 or gap_ref <= 0:
        return "long_t", "auto_default"
    strong = float(cfg.get("auto_strong_pct") or 0.5)
    weak = float(cfg.get("auto_weak_pct") or 0.5)
    chg = (open_px / gap_ref - 1.0) * 100.0
    if chg >= strong:
        return "long_t", "auto_gap_up"
    if chg <= -weak and cash > 0 and shares > 0:
        return "reverse_t", "auto_gap_down"
    return "long_t", "auto_default"


def resolve_direction(
    *,
    bar: dict,
    ref: float,
    cfg: dict,
    cash: float,
    shares: float,
    hist_bars: Optional[Sequence[dict]] = None,
    atr_pct: Optional[float] = None,
    scores: Optional[dict] = None,
) -> Dict[str, Any]:
    """统一选向：返回 direction / skip / score / reason。"""
    mode = str(cfg.get("direction") or "auto")
    if mode == "long_t":
        return {
            "direction": "long_t",
            "skip": False,
            "direction_score": None,
            "direction_reason": "强制正T",
        }
    if mode == "reverse_t":
        return {
            "direction": "reverse_t",
            "skip": False,
            "direction_score": None,
            "direction_reason": "强制反T",
        }
    if mode == "dual_y":
        sc = scores if isinstance(scores, dict) else scores_from_item(None)
        return resolve_dual_y_direction(
            scores=sc,
            cfg=cfg,
            cash=float(cash or 0),
            shares=float(shares or 0),
        )
    if mode == "signal":
        scored = score_t0_direction(bar=bar, cfg=cfg, hist_bars=hist_bars, atr_pct=atr_pct)
        if not scored.get("ok"):
            # 缺特征：回退 auto，避免纸面完全不能做
            d, tag = _auto_direction(bar=bar, ref=ref, cfg=cfg, cash=cash, shares=shares)
            return {
                "direction": d,
                "skip": False,
                "direction_score": 0.0,
                "direction_reason": f"{scored.get('reason')}；回退{tag}→{d}",
                "features": scored.get("features"),
            }
        score = float(scored.get("score") or 0)
        enter = float(cfg.get("dir_enter") or 0.35)
        if score >= enter:
            return {
                "direction": "long_t",
                "skip": False,
                "direction_score": score,
                "direction_reason": scored.get("reason"),
                "features": scored.get("features"),
            }
        if score <= -enter:
            if cash > 0 and shares > 0:
                return {
                    "direction": "reverse_t",
                    "skip": False,
                    "direction_score": score,
                    "direction_reason": scored.get("reason"),
                    "features": scored.get("features"),
                }
            return {
                "direction": None,
                "skip": True,
                "direction_score": score,
                "direction_reason": f"{scored.get('reason')}；反T缺现金跳过",
                "features": scored.get("features"),
            }
        return {
            "direction": None,
            "skip": True,
            "direction_score": score,
            "direction_reason": f"{scored.get('reason')}；|score|<{enter} 低置信跳过",
            "features": scored.get("features"),
        }
    # auto
    d, tag = _auto_direction(bar=bar, ref=ref, cfg=cfg, cash=cash, shares=shares)
    return {
        "direction": d,
        "skip": False,
        "direction_score": None,
        "direction_reason": f"{tag}→{d}",
    }


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
) -> Dict[str, Any]:
    """单票单日做 T：仅 5m 第一触达（已删除日线 high/low 代理）。"""
    cfg = load_t0_rules(rules)
    cfg["path_mode"] = "first_touch"
    if not cfg.get("enabled", True):
        return _skip_result(reason="t0 disabled", shares=shares, bar=bar)

    score_snap = scores if isinstance(scores, dict) else None
    if str(cfg.get("direction") or "") == "dual_y" and not scores_have_any(score_snap):
        from core.t0.score_policy import (
            resolve_fuse_intraday,
            resolve_scores_for_code,
            resolve_y_score_source,
            tau_pool_day_score_kwargs,
        )

        as_of = str((bar or {}).get("date") or "")[:10]
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
                **tau_pool_day_score_kwargs(tau_pool_day, stock_code),
            )

    mins = list(minute_bars or [])
    if len(mins) < 2:
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
    )



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
) -> Dict[str, Any]:
    """对纸面持仓逐票跑单日做 T（需传入当日 bar）。

    dry_run=True 时不改 paper，只返回预演结果。
    hist_bars_by_code：各票历史日线（不含当日），供 signal 选向。
    minute_bars_by_code：各票当日分钟线，有则第一触达。
    stance_by_code：code → stance_code 或 {stance_code,...}；配合 coupling.t0_vs_stance。
    scores_by_code：code → dual_y ŷ 快照。
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

    # 预演用副本；确认时写回
    work_holdings = holdings

    for h in work_holdings:
        code = str(h.get("stock_code") or "")
        bar = bars_by_code.get(code)
        if not code or not bar:
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
        for t in day.get("trades") or []:
            row = dict(t)
            row["ts"] = ts
            row["stock_name"] = h.get("stock_name")
            all_trades.append(row)

        apply_t0_trades(
            h,
            day.get("trades") or [],
            as_of=as_of or str(ts)[:10],
            ts=ts,
        )
        from core.paper.tplus1 import sellable_shares as t1_after

        h["t0"] = {
            "enabled": True,
            "sellable_shares": t1_after(h, as_of=as_of or None),
            "day_sold": day.get("sold_qty") or 0,
            "day_bought": (day.get("covered_qty") or 0) + (day.get("bought_qty") or 0),
            "day_pnl": day.get("pnl") or 0,
            "day_exposure_pnl": day.get("exposure_pnl") or 0,
            "direction": day.get("direction_used"),
            "last_date": day.get("date"),
        }
        working_cash += float(day.get("cash_delta") or 0)
        pnl_total += float(day.get("pnl") or 0)
        exposure_total += float(day.get("exposure_pnl") or 0)
        results.append({"stock_code": code, "stock_name": h.get("stock_name"), **day})

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
