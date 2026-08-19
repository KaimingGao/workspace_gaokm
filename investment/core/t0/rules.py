"""单日做 T 模拟（日线 OHLC 代理）。"""

from __future__ import annotations
import logging

logger = logging.getLogger(__name__)
from core.numbers import now_iso_local as _now_iso

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from core.backtest.costs import round_trip_cost_pct
from core.t0.config import load_t0_rules, resolve_min_range_pct


def _lot_floor(shares: float, lot: int) -> int:
    if shares <= 0:
        return 0
    return int(shares // lot) * lot


def _ref_price(bar: dict, cost: float, rules: dict) -> float:
    mode = str(rules.get("ref") or "cost")
    if mode == "open":
        return float(bar.get("open") or cost or 0)
    if mode == "prev_close":
        return float(bar.get("prev_close") or bar.get("open") or cost or 0)
    return float(cost or bar.get("open") or 0)


def atr_pct_from_bars(bars: Sequence[dict], window: int = 14) -> Optional[float]:
    """近 window 根的平均振幅占收盘价 %（简化 ATR%）。"""
    if not bars or len(bars) < 2:
        return None
    w = max(2, min(int(window), len(bars)))
    slice_bars = list(bars)[-w:]
    ranges: List[float] = []
    for b in slice_bars:
        high = float(b.get("high") or 0)
        low = float(b.get("low") or 0)
        close = float(b.get("close") or 0)
        if high <= 0 or low <= 0 or close <= 0 or high < low:
            continue
        ranges.append((high - low) / close * 100.0)
    if not ranges:
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


def choose_direction(
    *,
    bar: dict,
    ref: float,
    cfg: dict,
    cash: float,
    shares: float,
    hist_bars: Optional[Sequence[dict]] = None,
    atr_pct: Optional[float] = None,
) -> str:
    """返回 long_t | reverse_t。

    auto：按跳空选向，不明默认正 T。
    signal：请用 resolve_direction（可 skip）；此处缺历史时回退 auto。
    """
    direction = str(cfg.get("direction") or "auto")
    if direction == "long_t":
        return "long_t"
    if direction == "reverse_t":
        return "reverse_t"
    if direction == "signal":
        resolved = resolve_direction(
            bar=bar,
            ref=ref,
            cfg=cfg,
            cash=cash,
            shares=shares,
            hist_bars=hist_bars,
            atr_pct=atr_pct,
        )
        return resolved.get("direction") or "long_t"
    # auto：用昨收衡量开盘强弱；无昨收时回退到触发 ref（仅当 ref≠open 才有意义）
    open_px = float(bar.get("open") or 0)
    gap_ref = float(bar.get("prev_close") or 0)
    if gap_ref <= 0:
        trig_ref = float(ref or 0)
        if trig_ref > 0 and abs(open_px - trig_ref) / max(trig_ref, 1e-9) > 1e-6:
            gap_ref = trig_ref
    if open_px <= 0 or gap_ref <= 0:
        return "long_t"
    strong = float(cfg.get("auto_strong_pct") or 0.5)
    weak = float(cfg.get("auto_weak_pct") or 0.5)
    chg = (open_px / gap_ref - 1.0) * 100.0
    if chg >= strong:
        return "long_t"
    if chg <= -weak and cash > 0 and shares > 0:
        return "reverse_t"
    return "long_t"


def resolve_direction(
    *,
    bar: dict,
    ref: float,
    cfg: dict,
    cash: float,
    shares: float,
    hist_bars: Optional[Sequence[dict]] = None,
    atr_pct: Optional[float] = None,
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
    if mode == "signal":
        scored = score_t0_direction(bar=bar, cfg=cfg, hist_bars=hist_bars, atr_pct=atr_pct)
        if not scored.get("ok"):
            # 缺特征：回退 auto，避免纸面完全不能做
            d = choose_direction(
                bar=bar, ref=ref, cfg={**cfg, "direction": "auto"}, cash=cash, shares=shares
            )
            return {
                "direction": d,
                "skip": False,
                "direction_score": 0.0,
                "direction_reason": f"{scored.get('reason')}；回退auto→{d}",
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
    d = choose_direction(bar=bar, ref=ref, cfg=cfg, cash=cash, shares=shares)
    return {
        "direction": d,
        "skip": False,
        "direction_score": None,
        "direction_reason": f"auto→{d}",
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
    if extra:
        out.update(extra)
    return out


def infer_intraday_path_bias(bar: dict) -> str:
    """用收盘在当日高低区间的位置粗分路径偏向（仅回测过滤，非实盘信号）。

    返回 hl | lh | ambiguous
    - hl：收近低，更像先冲高再回落 → 利好正 T
    - lh：收近高，更像先杀跌再拉升 → 利好反 T
    """
    high = float(bar.get("high") or 0)
    low = float(bar.get("low") or 0)
    close = float(bar.get("close") or 0)
    rng = high - low
    if rng <= 1e-9 or close <= 0:
        return "ambiguous"
    loc = (close - low) / rng
    if loc <= 0.45:
        return "hl"
    if loc >= 0.55:
        return "lh"
    return "ambiguous"


def resolve_path_for_direction(direction: str, path_mode: str, bar: dict) -> Dict[str, Any]:
    """返回 {ok, skip_reason, intraday_path}。intraday_path: any|hl|lh。"""
    mode = str(path_mode or "veto")
    if mode == "dual_touch":
        return {"ok": True, "intraday_path": "any", "path_bias": None}
    if mode == "adverse":
        # 正T不利=先低后高；反T不利=先高后低
        path = "lh" if direction == "long_t" else "hl"
        return {"ok": True, "intraday_path": path, "path_bias": path}
    # veto
    bias = infer_intraday_path_bias(bar)
    if bias == "ambiguous":
        return {
            "ok": False,
            "skip_reason": "路径不明（收盘居中），跳过以免定反",
            "intraday_path": "any",
            "path_bias": bias,
        }
    if direction == "long_t" and bias != "hl":
        return {
            "ok": False,
            "skip_reason": "开盘选正T但收盘偏强（似先低后高），跳过定反",
            "intraday_path": "any",
            "path_bias": bias,
        }
    if direction == "reverse_t" and bias != "lh":
        return {
            "ok": False,
            "skip_reason": "开盘选反T但收盘偏弱（似先高后低），跳过定反",
            "intraday_path": "any",
            "path_bias": bias,
        }
    return {"ok": True, "intraday_path": bias, "path_bias": bias}


def simulate_t0_day(
    *,
    bar: dict,
    shares: float,
    cost: float,
    sellable_shares: Optional[float] = None,
    rules: Optional[dict] = None,
    cost_config: Optional[dict] = None,
    stock_code: str = "",
    cash: float = 0.0,
    atr_pct: Optional[float] = None,
    hist_bars: Optional[Sequence[dict]] = None,
    minute_bars: Optional[Sequence[dict]] = None,
) -> Dict[str, Any]:
    """对单票单日做底仓 T 模拟（正 T / 反 T）。

    T+1：可卖额度默认 = 开盘前持仓（传入 sellable_shares，缺省等于 shares）。
    成交：fill_mode=trigger|mid|optimistic。
    path_mode：veto/adverse/dual_touch/first_touch，见 config。
    hist_bars：不含当日的历史日线，供 direction=signal 打分。
    minute_bars：当日分钟线；有则走第一触达（忽略日线 veto）。
    """
    cfg = load_t0_rules(rules)
    if not cfg.get("enabled", True):
        return _skip_result(reason="t0 disabled", shares=shares, bar=bar)

    if minute_bars and len(list(minute_bars)) >= 2:
        from core.t0.minute_path import simulate_t0_day_minute

        return simulate_t0_day_minute(
            bar=bar,
            minute_bars=minute_bars,
            shares=shares,
            cost=cost,
            sellable_shares=sellable_shares,
            rules=cfg,
            cost_config=cost_config,
            stock_code=stock_code,
            cash=cash,
            atr_pct=atr_pct,
            hist_bars=hist_bars,
        )

    lot = int(cfg["lot_size"])
    high = float(bar.get("high") or 0)
    low = float(bar.get("low") or 0)
    close = float(bar.get("close") or 0)
    open_px = float(bar.get("open") or 0)
    if high <= 0 or low <= 0 or shares <= 0:
        return {
            "success": False,
            "error": "无效 bar 或持仓",
            "trades": [],
            "pnl": 0.0,
            "shares_end": shares,
            "cash_delta": 0.0,
        }

    scaled = scale_triggers_with_atr(cfg, atr_pct=atr_pct)
    sell_trig = float(scaled["sell_trigger_pct"])
    buy_trig = float(scaled["buy_trigger_pct"])
    cfg_day = dict(cfg)
    cfg_day["sell_trigger_pct"] = sell_trig
    cfg_day["buy_trigger_pct"] = buy_trig

    ref = _ref_price(bar, cost, cfg_day)
    if ref <= 0:
        return {
            "success": False,
            "error": "无效参考价",
            "trades": [],
            "pnl": 0.0,
            "shares_end": shares,
            "cash_delta": 0.0,
        }

    range_pct = (high - low) / ref * 100.0
    min_range = resolve_min_range_pct(cfg_day)
    if range_pct < min_range:
        return _skip_result(
            reason=f"振幅不足 {range_pct:.2f}% < {min_range:.2f}%",
            shares=shares,
            bar=bar,
            extra={"range_pct": round(range_pct, 4), "min_range_pct": min_range},
        )
    # 一字板近似
    if high > 0 and abs(high - low) / high < 0.001:
        return _skip_result(reason="一字板/无波动", shares=shares, bar=bar)

    dir_res = resolve_direction(
        bar=bar,
        ref=ref,
        cfg=cfg_day,
        cash=float(cash or 0),
        shares=shares,
        hist_bars=hist_bars,
        atr_pct=scaled.get("atr_pct") if scaled.get("atr_pct") is not None else atr_pct,
    )
    if dir_res.get("skip") or not dir_res.get("direction"):
        return _skip_result(
            reason=str(dir_res.get("direction_reason") or "选向跳过"),
            shares=shares,
            bar=bar,
            extra={
                "direction_used": None,
                "direction_score": dir_res.get("direction_score"),
                "direction_reason": dir_res.get("direction_reason"),
                "direction_features": dir_res.get("features"),
                "signal_skip": True,
                "range_pct": round(range_pct, 4),
            },
        )
    direction = str(dir_res["direction"])
    path_res = resolve_path_for_direction(direction, cfg_day.get("path_mode"), bar)
    if not path_res.get("ok"):
        return _skip_result(
            reason=str(path_res.get("skip_reason") or "路径否决"),
            shares=shares,
            bar=bar,
            extra={
                "direction_used": direction,
                "direction_score": dir_res.get("direction_score"),
                "direction_reason": dir_res.get("direction_reason"),
                "direction_features": dir_res.get("features"),
                "path_mode": cfg_day.get("path_mode"),
                "path_bias": path_res.get("path_bias"),
                "range_pct": round(range_pct, 4),
            },
        )
    fill_mode = str(cfg_day.get("fill_mode") or "trigger")
    intraday_path = str(path_res.get("intraday_path") or "any")

    if direction == "reverse_t":
        out = _simulate_reverse_t(
            bar=bar,
            shares=shares,
            cost=cost,
            cash=float(cash or 0),
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
            intraday_path=intraday_path,
        )
    else:
        out = _simulate_long_t(
            bar=bar,
            shares=shares,
            cost=cost,
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
            intraday_path=intraday_path,
        )
    if isinstance(out, dict):
        out["path_mode"] = cfg_day.get("path_mode")
        out["path_bias"] = path_res.get("path_bias")
        out["intraday_path"] = intraday_path
        out["direction_score"] = dir_res.get("direction_score")
        out["direction_reason"] = dir_res.get("direction_reason")
        out["direction_features"] = dir_res.get("features")
    return out


def _simulate_long_t(
    *,
    bar: dict,
    shares: float,
    cost: float,
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
    intraday_path: str = "any",
) -> Dict[str, Any]:
    high = float(bar["high"])
    low = float(bar["low"])
    close = float(bar.get("close") or 0)

    sellable = float(sellable_shares if sellable_shares is not None else shares)
    sellable = min(sellable, shares)
    sell_level = ref * (1.0 + sell_trig / 100.0)
    max_t0 = _lot_floor(shares * t0_ratio, lot)
    qty = _lot_floor(min(sellable, max_t0), lot)

    trades: List[dict] = []
    cash_delta = 0.0
    shares_now = float(shares)
    pnl = 0.0
    exposure_pnl = 0.0
    sold_qty = 0
    sold_price = 0.0
    # lh=先低后高：正T第一腿仍可在冲高时卖，但低点已过，不能再按低点买回
    allow_trigger_cover = intraday_path != "lh"

    if qty > 0 and high >= sell_level:
        fill_sell = _fill_sell(high, sell_level, fill_mode)
        amount = qty * fill_sell
        trades.append(
            {
                "side": "t0_sell",
                "stock_code": stock_code,
                "shares": qty,
                "price": round(fill_sell, 4),
                "amount": round(amount, 2),
                "trigger": round(sell_level, 4),
                "note": "正T卖出（日线代理）",
            }
        )
        cash_delta += amount
        shares_now -= qty
        sold_qty = qty
        sold_price = fill_sell

    covered = 0
    if sold_qty > 0:
        buy_level = sold_price * (1.0 - buy_trig / 100.0)
        if allow_trigger_cover and low <= buy_level:
            fill_buy = _fill_buy(low, buy_level, fill_mode)
            cover = sold_qty
            amount = cover * fill_buy
            trades.append(
                {
                    "side": "t0_buy",
                    "stock_code": stock_code,
                    "shares": cover,
                    "price": round(fill_buy, 4),
                    "amount": round(amount, 2),
                    "trigger": round(buy_level, 4),
                    "note": "正T买回（日线代理）",
                }
            )
            cash_delta -= amount
            shares_now += cover
            covered = cover
            gross = (sold_price - fill_buy) / sold_price * 100.0
            cost_pct = round_trip_cost_pct(cost_config)
            pnl = round((gross - cost_pct) * (sold_qty * sold_price) / 100.0, 2)
        elif cfg.get("must_cover_same_day"):
            fill_buy = close
            cover = sold_qty
            amount = cover * fill_buy
            trades.append(
                {
                    "side": "t0_buy",
                    "stock_code": stock_code,
                    "shares": cover,
                    "price": round(fill_buy, 4),
                    "amount": round(amount, 2),
                    "trigger": round(close, 4),
                    "note": "强制当日回补（收盘）",
                }
            )
            cash_delta -= amount
            shares_now += cover
            covered = cover
            gross = (sold_price - fill_buy) / sold_price * 100.0
            cost_pct = round_trip_cost_pct(cost_config)
            pnl = round((gross - cost_pct) * (sold_qty * sold_price) / 100.0, 2)
        else:
            # 未回补：按收盘计敞口（卖出价→收盘的浮盈亏，供研究；不改仓）
            exposure_pnl = round((sold_price - close) * sold_qty, 2)

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
        "intraday_path": intraday_path,
        "note": cfg.get("note"),
    }


def _simulate_reverse_t(
    *,
    bar: dict,
    shares: float,
    cost: float,
    cash: float,
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
    intraday_path: str = "any",
) -> Dict[str, Any]:
    """反 T：先低吸加仓，再冲高卖回加的部分。"""
    high = float(bar["high"])
    low = float(bar["low"])
    close = float(bar.get("close") or 0)
    t0_ratio = float(cfg["t0_ratio"])
    # hl=先高后低：低吸可在后半段，但高点已过，不能再按高点卖回
    allow_trigger_sellback = intraday_path != "hl"

    buy_level = ref * (1.0 - buy_trig / 100.0)
    max_shares = _lot_floor(shares * t0_ratio, lot)
    if cash <= 0 or max_shares <= 0:
        return _skip_result(
            reason="反T缺现金或可加仓额度为0",
            shares=shares,
            bar=bar,
            extra={"direction_used": "reverse_t"},
        )

    afford = _lot_floor(cash / max(buy_level, 1e-6), lot)
    qty = min(max_shares, afford)
    if qty <= 0 or low > buy_level:
        return _skip_result(
            reason="反T未触及低吸位或买不起",
            shares=shares,
            bar=bar,
            extra={
                "direction_used": "reverse_t",
                "buy_level": round(buy_level, 4),
                "range_pct": round(range_pct, 4),
            },
        )

    # 反T买：乐观用 low，保守用 buy_level
    if fill_mode == "optimistic":
        fill_buy = low
    elif fill_mode == "mid":
        fill_buy = (low + buy_level) / 2.0
    else:
        fill_buy = buy_level

    trades: List[dict] = []
    cash_delta = 0.0
    shares_now = float(shares)
    buy_amount = qty * fill_buy
    if buy_amount > cash + 1e-6:
        qty = _lot_floor(cash / fill_buy, lot)
        if qty <= 0:
            return _skip_result(reason="反T现金不足", shares=shares, bar=bar)
        buy_amount = qty * fill_buy

    trades.append(
        {
            "side": "t0_buy",
            "stock_code": stock_code,
            "shares": qty,
            "price": round(fill_buy, 4),
            "amount": round(buy_amount, 2),
            "trigger": round(buy_level, 4),
            "note": "反T低吸（日线代理）",
        }
    )
    cash_delta -= buy_amount
    shares_now += qty
    bought_qty = qty

    sell_level = fill_buy * (1.0 + sell_trig / 100.0)
    sold_back = 0
    pnl = 0.0
    exposure_pnl = 0.0

    if allow_trigger_sellback and high >= sell_level:
        if fill_mode == "optimistic":
            fill_sell = high
        elif fill_mode == "mid":
            fill_sell = (high + sell_level) / 2.0
        else:
            fill_sell = sell_level
        sell_amount = qty * fill_sell
        trades.append(
            {
                "side": "t0_sell",
                "stock_code": stock_code,
                "shares": qty,
                "price": round(fill_sell, 4),
                "amount": round(sell_amount, 2),
                "trigger": round(sell_level, 4),
                "note": "反T卖回（日线代理）",
            }
        )
        cash_delta += sell_amount
        shares_now -= qty
        sold_back = qty
        gross = (fill_sell - fill_buy) / fill_buy * 100.0
        cost_pct = round_trip_cost_pct(cost_config)
        pnl = round((gross - cost_pct) * buy_amount / 100.0, 2)
    elif cfg.get("must_cover_same_day"):
        fill_sell = close
        sell_amount = qty * fill_sell
        trades.append(
            {
                "side": "t0_sell",
                "stock_code": stock_code,
                "shares": qty,
                "price": round(fill_sell, 4),
                "amount": round(sell_amount, 2),
                "trigger": round(close, 4),
                "note": "反T强制收盘卖回",
            }
        )
        cash_delta += sell_amount
        shares_now -= qty
        sold_back = qty
        gross = (fill_sell - fill_buy) / fill_buy * 100.0
        cost_pct = round_trip_cost_pct(cost_config)
        pnl = round((gross - cost_pct) * buy_amount / 100.0, 2)
    else:
        # 未卖回：临时加仓敞口按收盘浮动
        exposure_pnl = round((close - fill_buy) * qty, 2)

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
        "intraday_path": intraday_path,
        "note": cfg.get("note"),
    }


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
) -> Dict[str, Any]:
    """对纸面持仓逐票跑单日做 T（需传入当日 bar）。

    dry_run=True 时不改 paper，只返回预演结果。
    hist_bars_by_code：各票历史日线（不含当日），供 signal 选向。
    minute_bars_by_code：各票当日分钟线，有则第一触达。
    stance_by_code：code → stance_code 或 {stance_code,...}；配合 coupling.t0_vs_stance。
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

    holdings = [dict(h) for h in (paper.get("holdings") or [])]
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
        t0_meta = h.get("t0") or {}
        sellable = t0_meta.get("sellable_shares")
        if sellable is None:
            sellable = shares

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

        day = simulate_t0_day(
            bar=bar,
            shares=shares,
            cost=cost,
            sellable_shares=float(sellable),
            rules=cfg,
            stock_code=code,
            cash=working_cash,
            atr_pct=atr,
            hist_bars=hist,
            minute_bars=mins,
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

        h["shares"] = day["shares_end"]
        h["t0"] = {
            "enabled": True,
            "sellable_shares": day["shares_end"],
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
            + (
                "底仓做T；有分钟线走第一触达，否则日线代理；非实盘、不代客下单。"
                if minute_path_count
                else "底仓做T纸面模拟；日线代理；非实盘、不代客下单。"
            )
            + (f" · 耦合跳过 {coupling_skip_count}" if coupling_skip_count else "")
        ),
    }
