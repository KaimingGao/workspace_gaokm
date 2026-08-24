"""做 T：分钟线第一触达路径（已删除日线 high/low 代理）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Sequence

from core.backtest.costs import round_trip_cost_pct
from core.t0.config import load_t0_rules, resolve_min_range_pct
from core.t0.rules import (
    _error_result,
    _fill_buy,
    _fill_sell,
    _lot_floor,
    _ref_price,
    _skip_result,
    _t0_qty_lots,
    resolve_direction,
    scale_triggers_with_atr,
)


def _day_ohlc_from_minutes(minute_bars: Sequence[dict], daily_bar: Optional[dict] = None) -> dict:
    """用分钟线合成当日 OHLC；缺省回退日线 bar。"""
    base = dict(daily_bar or {})
    if not minute_bars:
        return base
    opens = [float(b.get("open") or 0) for b in minute_bars if float(b.get("open") or 0) > 0]
    highs = [float(b.get("high") or 0) for b in minute_bars if float(b.get("high") or 0) > 0]
    lows = [float(b.get("low") or 0) for b in minute_bars if float(b.get("low") or 0) > 0]
    closes = [float(b.get("close") or 0) for b in minute_bars if float(b.get("close") or 0) > 0]
    if opens:
        base["open"] = opens[0]
    if highs:
        base["high"] = max(highs)
    if lows:
        base["low"] = min(lows)
    if closes:
        base["close"] = closes[-1]
    if not base.get("date"):
        base["date"] = minute_bars[0].get("date") or str(minute_bars[0].get("datetime") or "")[:10]
    return base


def _session_close_at(minute_bars: Sequence[dict], bar: Optional[dict] = None) -> str:
    """分钟路径收盘腿时点：末根 K 线时间，否则当日 15:00。"""
    if minute_bars:
        last = minute_bars[-1]
        ts = last.get("datetime") or last.get("date")
        if ts:
            return str(ts)
    dkey = str((bar or {}).get("date") or "")[:10]
    if dkey:
        return f"{dkey} 15:00:00"
    return ""


def _first_touch_long(
    *,
    minute_bars: Sequence[dict],
    bar: dict,
    shares: float,
    sellable_shares: Optional[float],
    ref: float,
    sell_trig: float,
    buy_trig: float,
    lot: int,
    fill_mode: str,
    cfg: dict,
    cost_config: Optional[dict],
    stock_code: str,
    atr_pct: Optional[float],
    range_pct: float,
    t0_ratio: float,
) -> Dict[str, Any]:
    close = float(bar.get("close") or 0)
    sellable = float(sellable_shares if sellable_shares is not None else shares)
    sellable = min(sellable, shares)
    sell_level = ref * (1.0 + sell_trig / 100.0)
    qty = _t0_qty_lots(shares, t0_ratio, lot, sellable)
    if qty <= 0:
        return _skip_result(
            reason=f"正T动仓不足1手（持仓{int(shares)}×{t0_ratio:.0%} 可卖{int(sellable)}<{lot}股）",
            shares=shares,
            bar=bar,
            extra={
                "direction_used": "long_t",
                "sellable_shares": sellable,
                "t0_ratio": t0_ratio,
                "path_mode": "first_touch",
            },
        )

    trades: List[dict] = []
    cash_delta = 0.0
    shares_now = float(shares)
    pnl = 0.0
    exposure_pnl = 0.0
    sold_qty = 0
    sold_price = 0.0
    covered = 0
    touch_sell_at = None
    touch_cover_at = None

    for mb in minute_bars:
        hi = float(mb.get("high") or 0)
        lo = float(mb.get("low") or 0)
        ts = mb.get("datetime") or mb.get("date")
        if sold_qty <= 0 and qty > 0 and hi >= sell_level:
            fill_sell = _fill_sell(hi, sell_level, fill_mode)
            amount = qty * fill_sell
            trades.append(
                {
                    "side": "t0_sell",
                    "stock_code": stock_code,
                    "shares": qty,
                    "price": round(fill_sell, 4),
                    "amount": round(amount, 2),
                    "trigger": round(sell_level, 4),
                    "at": ts,
                    "leg_kind": "trigger",
                    "note": "正T卖出（分钟第一触达）",
                }
            )
            cash_delta += amount
            shares_now -= qty
            sold_qty = qty
            sold_price = fill_sell
            touch_sell_at = ts
            # 同根 5m OHLC 不知先后：卖出后不在同一根回补，等后续分钟
            continue

        if sold_qty > 0 and covered <= 0:
            buy_level = sold_price * (1.0 - buy_trig / 100.0)
            if lo <= buy_level:
                fill_buy = _fill_buy(lo, buy_level, fill_mode)
                cover = sold_qty
                amount_b = cover * fill_buy
                trades.append(
                    {
                        "side": "t0_buy",
                        "stock_code": stock_code,
                        "shares": cover,
                        "price": round(fill_buy, 4),
                        "amount": round(amount_b, 2),
                        "trigger": round(buy_level, 4),
                        "at": ts,
                        "leg_kind": "trigger",
                        "note": "正T买回（分钟第一触达）",
                    }
                )
                cash_delta -= amount_b
                shares_now += cover
                covered = cover
                touch_cover_at = ts
                gross = (sold_price - fill_buy) / sold_price * 100.0
                cost_pct = round_trip_cost_pct(cost_config)
                pnl = round((gross - cost_pct) * (sold_qty * sold_price) / 100.0, 2)

    if sold_qty > 0 and covered <= 0:
        if cfg.get("must_cover_same_day"):
            fill_buy = close
            cover = sold_qty
            amount_b = cover * fill_buy
            close_at = _session_close_at(minute_bars, bar)
            trades.append(
                {
                    "side": "t0_buy",
                    "stock_code": stock_code,
                    "shares": cover,
                    "price": round(fill_buy, 4),
                    "amount": round(amount_b, 2),
                    "trigger": round(close, 4),
                    "at": close_at,
                    "leg_kind": "eod_cover",
                    "note": "强制当日回补（收盘）",
                }
            )
            cash_delta -= amount_b
            shares_now += cover
            covered = cover
            touch_cover_at = close_at or touch_cover_at
            gross = (sold_price - fill_buy) / sold_price * 100.0
            cost_pct = round_trip_cost_pct(cost_config)
            pnl = round((gross - cost_pct) * (sold_qty * sold_price) / 100.0, 2)
        else:
            exposure_pnl = round((sold_price - close) * sold_qty, 2)

    if sold_qty <= 0:
        return _skip_result(
            reason="正T分钟路径未触及卖出价",
            shares=shares,
            bar=bar,
            extra={
                "direction_used": "long_t",
                "path_mode": "first_touch",
                "sell_level": round(sell_level, 4),
                "range_pct": round(range_pct, 4),
            },
        )

    uncovered = sold_qty - covered
    return {
        "success": True,
        "skipped": False,
        "date": bar.get("date"),
        "ref": round(ref, 4),
        "sell_level": round(sell_level, 4),
        "sold_qty": sold_qty,
        "covered_qty": covered,
        "uncovered_qty": uncovered,
        "bought_qty": 0,
        "sold_back_qty": 0,
        "trades": trades,
        "pnl": pnl,
        "exposure_pnl": exposure_pnl,
        "shares_end": shares_now,
        "cash_delta": round(cash_delta, 2),
        "direction_used": "long_t",
        "fill_mode": fill_mode,
        "sell_trigger_pct": sell_trig,
        "buy_trigger_pct": buy_trig,
        "atr_pct": atr_pct,
        "range_pct": round(range_pct, 4),
        "path_mode": "first_touch",
        "intraday_path": "first_touch",
        "touch_sell_at": touch_sell_at,
        "touch_cover_at": touch_cover_at,
        "note": "分钟第一触达（正T）",
    }


def _first_touch_reverse(
    *,
    minute_bars: Sequence[dict],
    bar: dict,
    shares: float,
    cash: float,
    sellable_shares: Optional[float],
    ref: float,
    sell_trig: float,
    buy_trig: float,
    lot: int,
    fill_mode: str,
    cfg: dict,
    cost_config: Optional[dict],
    stock_code: str,
    atr_pct: Optional[float],
    range_pct: float,
) -> Dict[str, Any]:
    """反 T 分钟路径：低吸加仓后卖旧底仓（T+1），不卖当日新买股。"""
    close = float(bar.get("close") or 0)
    t0_ratio = float(cfg["t0_ratio"])
    buy_level = ref * (1.0 - buy_trig / 100.0)
    sellable = float(sellable_shares if sellable_shares is not None else shares)
    sellable = min(sellable, shares)
    max_shares = _t0_qty_lots(shares, t0_ratio, lot, sellable)
    if max_shares <= 0:
        return _skip_result(
            reason=f"反T动仓不足1手（持仓{int(shares)}×{t0_ratio:.0%} 可卖{int(sellable)}<{lot}股）",
            shares=shares,
            bar=bar,
            extra={"direction_used": "reverse_t", "path_mode": "first_touch"},
        )
    if cash <= 0:
        return _skip_result(
            reason="反T缺现金（低吸需预留现金）",
            shares=shares,
            bar=bar,
            extra={"direction_used": "reverse_t", "path_mode": "first_touch"},
        )
    if sellable < lot:
        return _skip_result(
            reason="反T无可卖旧仓（T+1）",
            shares=shares,
            bar=bar,
            extra={
                "direction_used": "reverse_t",
                "path_mode": "first_touch",
                "sellable_shares": sellable,
            },
        )

    afford = _lot_floor(cash / max(buy_level, 1e-6), lot)
    qty = min(max_shares, afford, _lot_floor(sellable, lot))
    if qty <= 0:
        afford_n = int(afford)
        sell_n = int(_lot_floor(sellable, lot))
        if afford_n < lot:
            reason = (
                f"反T现金不够1手（现金{cash:.0f}可买{afford_n}股<"
                f"{lot}股；目标{int(max_shares)}股）"
            )
        elif sell_n < lot:
            reason = f"反T无可卖旧仓（可卖{int(sellable)}<{lot}股）"
        else:
            reason = "反T买不起或无可卖旧仓"
        return _skip_result(
            reason=reason,
            shares=shares,
            bar=bar,
            extra={
                "direction_used": "reverse_t",
                "path_mode": "first_touch",
                "cash": round(cash, 2),
                "afford_shares": afford_n,
                "target_shares": int(max_shares),
            },
        )

    trades: List[dict] = []
    cash_delta = 0.0
    shares_now = float(shares)
    bought_qty = 0
    buy_price = 0.0
    sold_back = 0
    pnl = 0.0
    exposure_pnl = 0.0
    touch_buy_at = None
    touch_sell_at = None
    sell_old_cap = _lot_floor(sellable, lot)

    for mb in minute_bars:
        hi = float(mb.get("high") or 0)
        lo = float(mb.get("low") or 0)
        ts = mb.get("datetime") or mb.get("date")
        if bought_qty <= 0 and lo <= buy_level:
            if fill_mode == "optimistic":
                fill_buy = lo
            elif fill_mode == "mid":
                fill_buy = (lo + buy_level) / 2.0
            else:
                fill_buy = buy_level
            buy_amount = qty * fill_buy
            if buy_amount > cash + 1e-6:
                qty = _lot_floor(cash / max(fill_buy, 1e-6), lot)
                qty = min(qty, sell_old_cap)
                if qty <= 0:
                    continue
                buy_amount = qty * fill_buy
            trades.append(
                {
                    "side": "t0_buy",
                    "stock_code": stock_code,
                    "shares": qty,
                    "price": round(fill_buy, 4),
                    "amount": round(buy_amount, 2),
                    "trigger": round(buy_level, 4),
                    "at": ts,
                    "leg_kind": "trigger",
                    "note": "反T低吸加仓（分钟第一触达；新股T+1锁仓）",
                }
            )
            cash_delta -= buy_amount
            shares_now += qty
            bought_qty = qty
            buy_price = fill_buy
            touch_buy_at = ts
            # 同根不明先后：低吸后不在同一根卖旧仓
            continue

        if bought_qty > 0 and sold_back <= 0:
            sell_level = buy_price * (1.0 + sell_trig / 100.0)
            sell_old_qty = min(bought_qty, sell_old_cap)
            if sell_old_qty > 0 and hi >= sell_level:
                fill_sell = _fill_sell(hi, sell_level, fill_mode)
                amount_s = sell_old_qty * fill_sell
                trades.append(
                    {
                        "side": "t0_sell",
                        "stock_code": stock_code,
                        "shares": sell_old_qty,
                        "price": round(fill_sell, 4),
                        "amount": round(amount_s, 2),
                        "trigger": round(sell_level, 4),
                        "at": ts,
                        "leg_kind": "trigger",
                        "note": "反T卖旧底仓（分钟第一触达；T+1可卖）",
                    }
                )
                cash_delta += amount_s
                shares_now -= sell_old_qty
                sold_back = sell_old_qty
                touch_sell_at = ts
                gross = (fill_sell - buy_price) / buy_price * 100.0
                cost_pct = round_trip_cost_pct(cost_config)
                pnl = round((gross - cost_pct) * (sell_old_qty * buy_price) / 100.0, 2)

    if bought_qty <= 0:
        return _skip_result(
            reason="反T分钟路径未触及低吸位",
            shares=shares,
            bar=bar,
            extra={
                "direction_used": "reverse_t",
                "path_mode": "first_touch",
                "buy_level": round(buy_level, 4),
                "range_pct": round(range_pct, 4),
            },
        )

    if sold_back <= 0:
        sell_old_qty = min(bought_qty, sell_old_cap)
        if cfg.get("must_cover_same_day") and sell_old_qty > 0:
            fill_sell = close
            amount_s = sell_old_qty * fill_sell
            close_at = _session_close_at(minute_bars, bar)
            trades.append(
                {
                    "side": "t0_sell",
                    "stock_code": stock_code,
                    "shares": sell_old_qty,
                    "price": round(fill_sell, 4),
                    "amount": round(amount_s, 2),
                    "trigger": round(close, 4),
                    "at": close_at,
                    "leg_kind": "eod_cover",
                    "note": "强制收盘卖旧底仓（T+1）",
                }
            )
            cash_delta += amount_s
            shares_now -= sell_old_qty
            sold_back = sell_old_qty
            touch_sell_at = close_at or touch_sell_at
            gross = (fill_sell - buy_price) / buy_price * 100.0
            cost_pct = round_trip_cost_pct(cost_config)
            pnl = round((gross - cost_pct) * (sell_old_qty * buy_price) / 100.0, 2)
        else:
            exposure_pnl = round((close - buy_price) * bought_qty, 2)

    return {
        "success": True,
        "skipped": False,
        "date": bar.get("date"),
        "ref": round(ref, 4),
        "buy_level": round(buy_level, 4),
        "sold_qty": 0,
        "covered_qty": 0,
        "uncovered_qty": 0,
        "bought_qty": bought_qty,
        "sold_back_qty": sold_back,
        "trades": trades,
        "pnl": pnl,
        "exposure_pnl": exposure_pnl,
        "shares_end": shares_now,
        "cash_delta": round(cash_delta, 2),
        "direction_used": "reverse_t",
        "fill_mode": fill_mode,
        "sell_trigger_pct": sell_trig,
        "buy_trigger_pct": buy_trig,
        "atr_pct": atr_pct,
        "range_pct": round(range_pct, 4),
        "path_mode": "first_touch",
        "intraday_path": "first_touch",
        "touch_buy_at": touch_buy_at,
        "touch_sell_at": touch_sell_at,
        "note": "分钟第一触达（反T·T+1换仓）",
    }


def simulate_t0_day_minute(
    *,
    bar: dict,
    minute_bars: Sequence[dict],
    shares: float,
    cost: float,
    sellable_shares: Optional[float] = None,
    rules: Optional[dict] = None,
    cost_config: Optional[dict] = None,
    stock_code: str = "",
    cash: float = 0.0,
    atr_pct: Optional[float] = None,
    hist_bars: Optional[Sequence[dict]] = None,
    scores: Optional[dict] = None,
) -> Dict[str, Any]:
    """单日做 T：选向仍用开盘/隔夜特征；成交路径按分钟第一触达。"""
    from core.t0.score_policy import (
        resolve_scores_for_code,
        resolve_y_score_source,
        resolve_cover_policy,
        scale_t0_ratio,
        scores_have_any,
    )

    cfg = load_t0_rules(rules)
    if not cfg.get("enabled", True):
        return _skip_result(reason="t0 disabled", shares=shares, bar=bar)

    score_snap = scores if isinstance(scores, dict) else None
    if str(cfg.get("direction") or "") == "dual_y" and not scores_have_any(score_snap):
        as_of = str((bar or {}).get("date") or "")[:10]
        if stock_code:
            score_snap = resolve_scores_for_code(
                stock_code,
                hist_bars=hist_bars,
                day_bar=bar if isinstance(bar, dict) else None,
                as_of=as_of or None,
                source=resolve_y_score_source(cfg),
                fuse_intraday=True,
                allow_fallback=True,
            )

    mins = [dict(m) for m in (minute_bars or [])]
    mins.sort(key=lambda x: str(x.get("datetime") or ""))
    if len(mins) < 2:
        return _skip_result(
            reason="分钟线不足，无法第一触达",
            shares=shares,
            bar=bar,
            extra={"path_mode": "first_touch"},
        )

    bar_day = _day_ohlc_from_minutes(mins, bar)
    lot = int(cfg["lot_size"])
    high = float(bar_day.get("high") or 0)
    low = float(bar_day.get("low") or 0)
    if high <= 0 or low <= 0 or shares <= 0:
        return _error_result("无效 bar 或持仓", shares)

    scaled = scale_triggers_with_atr(cfg, atr_pct=atr_pct)
    sell_trig = float(scaled["sell_trigger_pct"])
    buy_trig = float(scaled["buy_trigger_pct"])
    cfg_day = dict(cfg)
    cfg_day["sell_trigger_pct"] = sell_trig
    cfg_day["buy_trigger_pct"] = buy_trig
    # 分钟路径不再用日线 veto/adverse
    cfg_day["path_mode"] = "first_touch"

    if str(cfg_day.get("direction") or "") == "dual_y" and scores_have_any(score_snap):
        cfg_day["t0_ratio"] = scale_t0_ratio(
            float(cfg_day.get("t0_ratio") or 0.4),
            score_snap or {},
            cfg_day,
        )

    ref = _ref_price(bar_day, cost, cfg_day)
    if ref <= 0:
        return _error_result("无效参考价", shares)

    range_pct = (high - low) / ref * 100.0
    min_range = resolve_min_range_pct(cfg)
    if range_pct < min_range:
        return _skip_result(
            reason=f"振幅不足 {range_pct:.2f}% < {min_range:.2f}%",
            shares=shares,
            bar=bar_day,
            extra={"range_pct": round(range_pct, 4), "min_range_pct": min_range, "path_mode": "first_touch"},
        )
    if high > 0 and abs(high - low) / high < 0.001:
        return _skip_result(
            reason="一字板/无波动",
            shares=shares,
            bar=bar_day,
            extra={"path_mode": "first_touch"},
        )

    dir_res = resolve_direction(
        bar=bar_day,
        ref=ref,
        cfg=cfg_day,
        cash=float(cash or 0),
        shares=shares,
        hist_bars=hist_bars,
        atr_pct=scaled.get("atr_pct") if scaled.get("atr_pct") is not None else atr_pct,
        scores=score_snap,
    )
    if dir_res.get("skip") or not dir_res.get("direction"):
        return _skip_result(
            reason=str(dir_res.get("direction_reason") or "选向跳过"),
            shares=shares,
            bar=bar_day,
            extra={
                "direction_used": None,
                "direction_score": dir_res.get("direction_score"),
                "direction_reason": dir_res.get("direction_reason"),
                "direction_features": dir_res.get("features"),
                "signal_skip": True,
                "range_pct": round(range_pct, 4),
                "path_mode": "first_touch",
            },
        )

    direction = str(dir_res["direction"])
    cover_meta = None
    if str(cfg.get("direction") or "") == "dual_y":
        cover_meta = resolve_cover_policy(
            scores=score_snap or {},
            direction=direction,
            cfg=cfg_day,
        )
        cfg_day["must_cover_same_day"] = bool(cover_meta.get("must_cover"))
    fill_mode = str(cfg_day.get("fill_mode") or "trigger")
    if direction == "reverse_t":
        out = _first_touch_reverse(
            minute_bars=mins,
            bar=bar_day,
            shares=shares,
            cash=float(cash or 0),
            sellable_shares=sellable_shares,
            ref=ref,
            sell_trig=sell_trig,
            buy_trig=buy_trig,
            lot=lot,
            fill_mode=fill_mode,
            cfg=cfg_day,
            cost_config=cost_config,
            stock_code=stock_code,
            atr_pct=scaled.get("atr_pct"),
            range_pct=range_pct,
        )
    else:
        out = _first_touch_long(
            minute_bars=mins,
            bar=bar_day,
            shares=shares,
            sellable_shares=sellable_shares,
            ref=ref,
            sell_trig=sell_trig,
            buy_trig=buy_trig,
            lot=lot,
            fill_mode=fill_mode,
            cfg=cfg_day,
            cost_config=cost_config,
            stock_code=stock_code,
            atr_pct=scaled.get("atr_pct"),
            range_pct=range_pct,
            t0_ratio=float(cfg_day["t0_ratio"]),
        )
    if isinstance(out, dict):
        out["path_mode"] = "first_touch"
        out["intraday_path"] = "first_touch"
        out["direction_score"] = dir_res.get("direction_score")
        out["direction_reason"] = dir_res.get("direction_reason")
        out["direction_features"] = dir_res.get("features")
        out["minute_bars"] = len(mins)
        if cover_meta:
            out["cover_policy"] = cover_meta
            out["must_cover_same_day"] = bool(cover_meta.get("must_cover"))
        if score_snap and scores_have_any(score_snap):
            out["scores"] = {
                k: score_snap.get(k)
                for k in ("y_eod", "y_tau", "y_trade", "y_on", "y_nowcast", "y_check")
            }
    return out
