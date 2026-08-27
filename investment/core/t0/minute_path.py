"""做 T：分钟线第一触达路径（已删除日线 high/low 代理）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.t0.costs import (
    append_t0_leg,
    resolve_t0_cost_context,
    t0_fees_total,
    t0_pnl_from_trades,
)
from core.t0.config import load_t0_rules, resolve_min_range_pct, apply_side_exec_params, resolve_path_abandon_bars
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


def _tplus1_skip_reason(*, side: str, shares: float, sellable: float, lot: int) -> str:
    """可卖不足 1 手：主因是 T+1，不是动仓比例。"""
    sh = int(shares)
    sv = int(sellable)
    if side == "reverse_t":
        if sv <= 0:
            return (
                f"反T：可卖旧仓 0 股（持仓 {sh} 全被 T+1 锁定），"
                f"第二腿卖不掉旧仓"
            )
        return (
            f"反T：可卖旧仓仅 {sv} 股 < {lot}（持仓 {sh}），"
            f"第二腿不够 1 手"
        )
    if sv <= 0:
        return f"正T：可卖 0 股（持仓 {sh} 全被 T+1 锁定），无法先卖"
    return f"正T：可卖仅 {sv} 股 < {lot}（持仓 {sh}），不够 1 手"


def _ratio_lot_skip_reason(*, side: str, shares: float, t0_ratio: float, lot: int) -> str:
    """可卖够、但持仓×动仓比例仍不足 1 手（动仓固定 100%）。"""
    sh = int(shares)
    raw = int(float(shares) * float(t0_ratio))
    pct = f"{float(t0_ratio):.0%}"
    tag = "反T" if side == "reverse_t" else "正T"
    return f"{tag}：动仓不足 1 手（持仓 {sh}×{pct}≈{raw} < {lot}）"


def _parse_hm(raw: Any) -> Optional[Tuple[int, int]]:
    s = str(raw or "").strip()
    if not s or s.lower() in {"off", "none", "0", "-"}:
        return None
    # "11:30" / "1130" / "11:30:00"
    parts = s.replace("：", ":").split(":")
    try:
        if len(parts) >= 2:
            return int(parts[0]), int(parts[1])
        if len(s) == 4 and s.isdigit():
            return int(s[:2]), int(s[2:])
    except (TypeError, ValueError):
        return None
    return None


def _ts_hm(ts: Any) -> Optional[Tuple[int, int]]:
    if ts is None:
        return None
    s = str(ts).strip()
    if "T" in s:
        s = s.split("T", 1)[1]
    elif " " in s:
        s = s.split(" ", 1)[1]
    return _parse_hm(s[:8] if len(s) >= 5 else s)


def _hm_reached(ts: Any, stop_hm: Optional[Tuple[int, int]]) -> bool:
    if not stop_hm:
        return False
    cur = _ts_hm(ts)
    if not cur:
        return False
    return cur[0] > stop_hm[0] or (cur[0] == stop_hm[0] and cur[1] >= stop_hm[1])


def _allows_eod_cover(
    minute_bars: Sequence[dict],
    session_bars: Optional[Sequence[dict]],
    *,
    defer_eod: bool,
) -> bool:
    """defer_eod 时仅末根 ≥14:55 才强制回补，避免盘中前缀误当收盘。"""
    sess = session_bars if session_bars is not None else minute_bars
    if len(minute_bars) < len(sess):
        return False
    if not defer_eod:
        return True
    if not minute_bars:
        return False
    last = minute_bars[-1]
    ts = last.get("datetime") or last.get("date")
    return _hm_reached(ts, (14, 55))


def _ts_minutes(ts: Any) -> Optional[int]:
    """当日分钟数（0–1439）；解析失败返回 None。"""
    hm = _ts_hm(ts)
    if not hm:
        return None
    return int(hm[0]) * 60 + int(hm[1])


def _pm_chase_interval_min(cfg: dict) -> int:
    try:
        n = int(cfg.get("t0_pm_chase_interval_min") or 10)
    except (TypeError, ValueError):
        n = 10
    return max(1, min(n, 60))


def _maybe_pm_chase_level(
    *,
    level: float,
    px: float,
    ts: Any,
    pm_hm: Optional[Tuple[int, int]],
    last_chase_min: Optional[int],
    interval_min: int,
) -> Tuple[float, Optional[int], bool]:
    """反T/正T 中点追价：目标 = 旧目标与现价中点；到点起算，之后每 interval_min 再调。

    反T：卖旧仓目标下移；正T：买回目标上移。触不到则走 eod / 放弃回补。

    Returns (new_level, last_chase_min, adjusted_this_bar).
    """
    if not pm_hm or not _hm_reached(ts, pm_hm):
        return level, last_chase_min, False
    if not (level > 0 and px > 0):
        return level, last_chase_min, False
    cur_min = _ts_minutes(ts)
    if cur_min is None:
        return level, last_chase_min, False
    if last_chase_min is not None and (cur_min - last_chase_min) < interval_min:
        return level, last_chase_min, False
    return (level + px) / 2.0, cur_min, True


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


def prefix_range_gate(
    minute_bars: Sequence[dict],
    daily_bar: Optional[dict],
    *,
    cost: float,
    cfg: dict,
) -> Dict[str, Any]:
    """前向前缀振幅门禁：仅用 ``minute_bars`` 前缀合成 high/low（与 Worker 单 tick 同口径）。

    仅判断波动幅度是否达 min_range_pct；第一腿方向是否具备空间见
    ``prefix_directional_amplitude_ok``。

    Returns ok/range_pct/min_range_pct/bar_day/ref/prefix_bars；无效输入时 ok=False。
    """
    min_range = resolve_min_range_pct(cfg)
    prefix_bars = len(minute_bars or [])
    if prefix_bars < 2:
        return {
            "ok": False,
            "range_pct": None,
            "min_range_pct": min_range,
            "bar_day": dict(daily_bar or {}),
            "ref": None,
            "prefix_bars": prefix_bars,
            "range_mode": "forward",
            "reason": "分钟线不足",
        }
    bar_n = _day_ohlc_from_minutes(minute_bars, daily_bar)
    ref = _ref_price(bar_n, cost, cfg)
    hi = float(bar_n.get("high") or 0)
    lo = float(bar_n.get("low") or 0)
    if ref <= 0 or hi <= 0 or lo <= 0:
        return {
            "ok": False,
            "range_pct": None,
            "min_range_pct": min_range,
            "bar_day": bar_n,
            "ref": ref,
            "prefix_bars": prefix_bars,
            "range_mode": "forward",
            "reason": "无效 bar",
        }
    range_pct = (hi - lo) / ref * 100.0
    flat = hi > 0 and abs(hi - lo) / hi < 0.001
    ok = range_pct >= min_range and not flat
    return {
        "ok": ok,
        "range_pct": round(range_pct, 4),
        "min_range_pct": min_range,
        "bar_day": bar_n,
        "ref": ref,
        "prefix_bars": prefix_bars,
        "range_mode": "forward",
        "flat": flat,
    }


def prefix_directional_amplitude_ok(
    minute_bars: Sequence[dict],
    *,
    direction: str,
    ref: float,
    sell_trig: float,
    buy_trig: float,
) -> Dict[str, Any]:
    """方向振幅：前缀是否已具备第一腿方向的触达空间（与总量振幅门禁分离）。

    long_t — 前缀 high 达卖出触发（向上振幅）；reverse_t — 前缀 low 达低吸触发（向下振幅）。
    """
    if ref <= 0 or len(minute_bars or []) < 1:
        return {"ok": False, "reason": "无效 ref 或分钟线"}
    highs = [float(b.get("high") or 0) for b in minute_bars if float(b.get("high") or 0) > 0]
    lows = [float(b.get("low") or 0) for b in minute_bars if float(b.get("low") or 0) > 0]
    if not highs or not lows:
        return {"ok": False, "reason": "无效 OHLC"}
    hi = max(highs)
    lo = min(lows)
    direction = str(direction or "").strip().lower()
    if direction == "long_t":
        sell_level = ref * (1.0 + float(sell_trig) / 100.0)
        up_pct = (hi - ref) / ref * 100.0
        ok = hi >= sell_level
        return {
            "ok": ok,
            "direction": direction,
            "up_pct": round(up_pct, 4),
            "down_pct": None,
            "trigger_level": round(sell_level, 4),
            "reason": (
                f"正T上移振幅 {up_pct:.2f}%≥{float(sell_trig):.2f}%"
                if ok
                else f"正T上移振幅 {up_pct:.2f}%<{float(sell_trig):.2f}%"
            ),
        }
    if direction == "reverse_t":
        buy_level = ref * (1.0 - float(buy_trig) / 100.0)
        down_pct = (ref - lo) / ref * 100.0
        ok = lo <= buy_level
        return {
            "ok": ok,
            "direction": direction,
            "up_pct": None,
            "down_pct": round(down_pct, 4),
            "trigger_level": round(buy_level, 4),
            "reason": (
                f"反T下移振幅 {down_pct:.2f}%≥{float(buy_trig):.2f}%"
                if ok
                else f"反T下移振幅 {down_pct:.2f}%<{float(buy_trig):.2f}%"
            ),
        }
    return {"ok": True, "reason": "未知方向，跳过方向振幅", "direction": direction}


def _prefix_segment_params(cfg: dict, direction: str) -> tuple[bool, float]:
    """段向确认开关与阈值（正T=回落%，反T=反弹%）。"""
    direction = str(direction or "").strip().lower()
    if not bool(cfg.get("y_prefix_segment_enabled", True)):
        return False, 0.0
    side_flag = (
        "y_prefix_segment_enabled_long"
        if direction == "long_t"
        else "y_prefix_segment_enabled_reverse"
    )
    if not bool(cfg.get(side_flag, True)):
        return False, 0.0
    key = (
        "y_prefix_pullback_pct_long"
        if direction == "long_t"
        else "y_prefix_bounce_pct_reverse"
    )
    try:
        pct = float(cfg.get(key) if cfg.get(key) is not None and cfg.get(key) != "" else 0.25)
    except (TypeError, ValueError):
        pct = 0.25
    if pct <= 0:
        return False, 0.0
    return True, max(0.0, min(pct, 2.0))


def prefix_segment_entry_ok(
    minute_bars: Sequence[dict],
    *,
    direction: str,
    ref: float,
    sell_trig: float,
    buy_trig: float,
    cfg: dict,
) -> Dict[str, Any]:
    """第一腿段向确认：正T须下行段（close 自前缀 high 回落）；反T须上升段（close 自前缀 low 弹起）。"""
    direction = str(direction or "").strip().lower()
    dir_amp = prefix_directional_amplitude_ok(
        minute_bars,
        direction=direction,
        ref=ref,
        sell_trig=sell_trig,
        buy_trig=buy_trig,
    )
    seg_on, seg_pct = _prefix_segment_params(cfg, direction)
    base = {
        "direction": direction,
        "segment_enabled": seg_on,
        "segment_pct": round(seg_pct, 4) if seg_on else None,
        "directional_amplitude": dir_amp,
    }
    if not dir_amp.get("ok"):
        return {
            **base,
            "ok": False,
            "reason": str(dir_amp.get("reason") or "方向振幅未达标"),
        }
    if not seg_on:
        return {**base, "ok": True, "reason": "段向确认关", "segment": "off"}

    highs = [float(b.get("high") or 0) for b in minute_bars if float(b.get("high") or 0) > 0]
    lows = [float(b.get("low") or 0) for b in minute_bars if float(b.get("low") or 0) > 0]
    if not highs or not lows:
        return {**base, "ok": False, "reason": "无效 OHLC"}
    hi = max(highs)
    lo = min(lows)
    close = float((minute_bars[-1] or {}).get("close") or 0)
    if close <= 0:
        return {**base, "ok": False, "reason": "无效 close"}

    if direction == "long_t":
        floor_px = hi * (1.0 - seg_pct / 100.0)
        pullback_pct = (hi - close) / hi * 100.0 if hi > 0 else 0.0
        ok = close <= floor_px
        return {
            **base,
            "ok": ok,
            "segment": "pullback",
            "prefix_high": round(hi, 4),
            "prefix_low": round(lo, 4),
            "close": round(close, 4),
            "segment_floor": round(floor_px, 4),
            "segment_delta_pct": round(pullback_pct, 4),
            "reason": (
                f"正T回落确认 {pullback_pct:.2f}%≥{seg_pct:.2f}%"
                if ok
                else f"正T待回落 {pullback_pct:.2f}%<{seg_pct:.2f}%"
            ),
        }
    if direction == "reverse_t":
        ceil_px = lo * (1.0 + seg_pct / 100.0)
        bounce_pct = (close - lo) / lo * 100.0 if lo > 0 else 0.0
        ok = close >= ceil_px
        return {
            **base,
            "ok": ok,
            "segment": "bounce",
            "prefix_high": round(hi, 4),
            "prefix_low": round(lo, 4),
            "close": round(close, 4),
            "segment_ceiling": round(ceil_px, 4),
            "segment_delta_pct": round(bounce_pct, 4),
            "reason": (
                f"反T反弹确认 {bounce_pct:.2f}%≥{seg_pct:.2f}%"
                if ok
                else f"反T待反弹 {bounce_pct:.2f}%<{seg_pct:.2f}%"
            ),
        }
    return {**base, "ok": True, "reason": "未知方向，跳过段向确认", "segment": "skip"}


def _session_close_at(minute_bars: Sequence[dict], bar: Optional[dict] = None) -> str:
    """分钟路径收盘腿时点：末根已到收盘窗则用该 K，否则记 15:00。"""
    if minute_bars:
        last = minute_bars[-1]
        ts = last.get("datetime") or last.get("date")
        if ts and _hm_reached(ts, (14, 55)):
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
    cost_model: str,
    cost_params: dict,
    stock_code: str,
    atr_pct: Optional[float],
    range_pct: float,
    t0_ratio: float,
    session_bars: Optional[Sequence[dict]] = None,
    session_bar: Optional[dict] = None,
    defer_eod: bool = False,
    leg1_gate_at: Optional[Callable[[int], bool]] = None,
) -> Dict[str, Any]:
    close = float(bar.get("close") or 0)
    sess_bars = session_bars if session_bars is not None else minute_bars
    sess_bar = session_bar if session_bar is not None else bar
    sellable = float(sellable_shares if sellable_shares is not None else shares)
    sellable = min(sellable, shares)
    sell_level = ref * (1.0 + sell_trig / 100.0)
    if sellable < lot:
        return _skip_result(
            reason=_tplus1_skip_reason(
                side="long_t", shares=shares, sellable=sellable, lot=lot
            ),
            shares=shares,
            bar=bar,
            extra={
                "direction_used": "long_t",
                "sellable_shares": sellable,
                "t0_ratio": t0_ratio,
                "path_mode": "first_touch",
            },
        )
    qty = _t0_qty_lots(shares, t0_ratio, lot, sellable)
    if qty <= 0:
        return _skip_result(
            reason=_ratio_lot_skip_reason(
                side="long_t", shares=shares, t0_ratio=t0_ratio, lot=lot
            ),
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
    exit_reason = None
    pm_hm = _parse_hm(cfg.get("t0_pm_degrade"))
    pm_chase_iv = _pm_chase_interval_min(cfg)
    chase_buy_level: Optional[float] = None
    last_chase_min: Optional[int] = None
    seg_on, seg_pct = _prefix_segment_params(cfg, "long_t")
    prefix_hi = 0.0

    for idx, mb in enumerate(minute_bars):
        hi = float(mb.get("high") or 0)
        lo = float(mb.get("low") or 0)
        close = float(mb.get("close") or 0)
        ts = mb.get("datetime") or mb.get("date")
        pm_hit = bool(pm_hm and _hm_reached(ts, pm_hm))
        if hi > 0:
            prefix_hi = max(prefix_hi, hi)

        # 午后窗：未开第一腿则不再新开
        if sold_qty <= 0 and qty > 0 and hi >= sell_level and not pm_hit:
            if leg1_gate_at is not None and not leg1_gate_at(idx):
                continue
            if leg1_gate_at is None and seg_on and prefix_hi > 0 and close > 0:
                if close > prefix_hi * (1.0 - seg_pct / 100.0):
                    continue
            fill_sell = _fill_sell(hi, sell_level, fill_mode)
            cash_delta += append_t0_leg(
                trades,
                cost_model=cost_model,
                cost_params=cost_params,
                side="t0_sell",
                stock_code=stock_code,
                shares=qty,
                price=fill_sell,
                trigger=sell_level,
                at=ts,
                leg_kind="trigger",
                note="正T卖出（分钟第一触达）",
            )
            shares_now -= qty
            sold_qty = qty
            sold_price = fill_sell
            touch_sell_at = ts
            continue

        if sold_qty > 0 and covered <= 0:
            base_buy = sold_price * (1.0 - buy_trig / 100.0)
            if chase_buy_level is None:
                chase_buy_level = base_buy
            px = float(mb.get("close") or hi or sold_price)
            chase_buy_level, last_chase_min, _ = _maybe_pm_chase_level(
                level=float(chase_buy_level),
                px=px,
                ts=ts,
                pm_hm=pm_hm,
                last_chase_min=last_chase_min,
                interval_min=pm_chase_iv,
            )
            buy_level = float(chase_buy_level)
            if lo <= buy_level:
                fill_buy = _fill_buy(lo, buy_level, fill_mode)
                cover = sold_qty
                used_chase = last_chase_min is not None
                cash_delta += append_t0_leg(
                    trades,
                    cost_model=cost_model,
                    cost_params=cost_params,
                    side="t0_buy",
                    stock_code=stock_code,
                    shares=cover,
                    price=fill_buy,
                    trigger=buy_level,
                    at=ts,
                    leg_kind="pm_chase" if used_chase else "trigger",
                    note=(
                        f"正T中点追价买回（目标{buy_level:.4f}）"
                        if used_chase
                        else "正T买回（分钟第一触达）"
                    ),
                )
                shares_now += cover
                covered = cover
                touch_cover_at = ts
                exit_reason = "pm_chase" if used_chase else "trigger"
                continue

    at_end = len(minute_bars) >= len(sess_bars)
    if sold_qty > 0 and covered <= 0 and at_end:
        sess_close = float(sess_bar.get("close") or close)
        eod_ok = _allows_eod_cover(minute_bars, sess_bars, defer_eod=defer_eod)
        if cfg.get("must_cover_same_day") and eod_ok:
            cover = sold_qty
            close_at = _session_close_at(sess_bars, sess_bar)
            cash_delta += append_t0_leg(
                trades,
                cost_model=cost_model,
                cost_params=cost_params,
                side="t0_buy",
                stock_code=stock_code,
                shares=cover,
                price=sess_close,
                trigger=sess_close,
                at=close_at,
                leg_kind="eod_cover",
                note="正T强制收盘买回",
            )
            shares_now += cover
            covered = cover
            touch_cover_at = close_at or touch_cover_at
            exit_reason = "eod_cover"
        elif eod_ok or not defer_eod:
            exposure_pnl = round((sold_price - sess_close) * sold_qty, 2)
            exit_reason = "abandon_cover"

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
    if covered > 0:
        pnl = t0_pnl_from_trades(trades)
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
        "fees_total": t0_fees_total(trades),
        "cost_model": cost_model,
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
        "exit_reason": exit_reason,
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
    cost_model: str,
    cost_params: dict,
    stock_code: str,
    atr_pct: Optional[float],
    range_pct: float,
    session_bars: Optional[Sequence[dict]] = None,
    session_bar: Optional[dict] = None,
    defer_eod: bool = False,
    leg1_gate_at: Optional[Callable[[int], bool]] = None,
) -> Dict[str, Any]:
    """反 T 分钟路径：低吸加仓后卖旧底仓（T+1），不卖当日新买股。"""
    close = float(bar.get("close") or 0)
    sess_bars = session_bars if session_bars is not None else minute_bars
    sess_bar = session_bar if session_bar is not None else bar
    t0_ratio = float(cfg["t0_ratio"])
    buy_level = ref * (1.0 - buy_trig / 100.0)
    sellable = float(sellable_shares if sellable_shares is not None else shares)
    sellable = min(sellable, shares)
    if sellable < lot:
        return _skip_result(
            reason=_tplus1_skip_reason(
                side="reverse_t", shares=shares, sellable=sellable, lot=lot
            ),
            shares=shares,
            bar=bar,
            extra={
                "direction_used": "reverse_t",
                "path_mode": "first_touch",
                "sellable_shares": sellable,
            },
        )
    if cash <= 0:
        return _skip_result(
            reason="反T：缺现金（低吸加仓需要预留现金）",
            shares=shares,
            bar=bar,
            extra={"direction_used": "reverse_t", "path_mode": "first_touch"},
        )
    max_shares = _t0_qty_lots(shares, t0_ratio, lot, sellable)
    if max_shares <= 0:
        return _skip_result(
            reason=_ratio_lot_skip_reason(
                side="reverse_t", shares=shares, t0_ratio=t0_ratio, lot=lot
            ),
            shares=shares,
            bar=bar,
            extra={
                "direction_used": "reverse_t",
                "path_mode": "first_touch",
                "sellable_shares": sellable,
                "t0_ratio": t0_ratio,
            },
        )

    afford = _lot_floor(cash / max(buy_level, 1e-6), lot)
    qty = min(max_shares, afford, _lot_floor(sellable, lot))
    if qty <= 0:
        afford_n = int(afford)
        if afford_n < lot:
            reason = (
                f"反T：现金不够 1 手（现金 {cash:.0f} 约可买 {afford_n} 股 < {lot}；"
                f"目标 {int(max_shares)} 股）"
            )
        else:
            reason = "反T：买不起或可卖旧仓不足"
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
                "sellable_shares": sellable,
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
    exit_reason = None
    sell_old_cap = _lot_floor(sellable, lot)
    pm_hm = _parse_hm(cfg.get("t0_pm_degrade"))
    pm_chase_iv = _pm_chase_interval_min(cfg)
    chase_sell_level: Optional[float] = None
    last_chase_min: Optional[int] = None
    seg_on, seg_pct = _prefix_segment_params(cfg, "reverse_t")
    prefix_lo = 0.0

    for idx, mb in enumerate(minute_bars):
        hi = float(mb.get("high") or 0)
        lo = float(mb.get("low") or 0)
        close = float(mb.get("close") or 0)
        ts = mb.get("datetime") or mb.get("date")
        pm_hit = bool(pm_hm and _hm_reached(ts, pm_hm))
        if lo > 0:
            prefix_lo = lo if prefix_lo <= 0 else min(prefix_lo, lo)

        # 中点追价窗：未开第一腿则不再低吸
        if bought_qty <= 0 and lo <= buy_level and not pm_hit:
            if leg1_gate_at is not None and not leg1_gate_at(idx):
                continue
            if leg1_gate_at is None and seg_on and prefix_lo > 0 and close > 0:
                if close < prefix_lo * (1.0 + seg_pct / 100.0):
                    continue
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
            cash_delta += append_t0_leg(
                trades,
                cost_model=cost_model,
                cost_params=cost_params,
                side="t0_buy",
                stock_code=stock_code,
                shares=qty,
                price=fill_buy,
                trigger=buy_level,
                at=ts,
                leg_kind="trigger",
                note="反T低吸加仓（分钟第一触达；新股T+1锁仓）",
            )
            shares_now += qty
            bought_qty = qty
            buy_price = fill_buy
            touch_buy_at = ts
            chase_sell_level = buy_price * (1.0 + sell_trig / 100.0)
            # 同根不明先后：低吸后不在同一根卖旧仓
            continue

        if bought_qty > 0 and sold_back <= 0:
            base_sell = buy_price * (1.0 + sell_trig / 100.0)
            if chase_sell_level is None:
                chase_sell_level = base_sell
            sell_old_qty = min(bought_qty, sell_old_cap)
            px = float(mb.get("close") or lo or buy_price)
            chase_sell_level, last_chase_min, _ = _maybe_pm_chase_level(
                level=float(chase_sell_level),
                px=px,
                ts=ts,
                pm_hm=pm_hm,
                last_chase_min=last_chase_min,
                interval_min=pm_chase_iv,
            )
            sell_level = float(chase_sell_level)
            if sell_old_qty > 0 and hi >= sell_level:
                fill_sell = _fill_sell(hi, sell_level, fill_mode)
                used_chase = last_chase_min is not None
                cash_delta += append_t0_leg(
                    trades,
                    cost_model=cost_model,
                    cost_params=cost_params,
                    side="t0_sell",
                    stock_code=stock_code,
                    shares=sell_old_qty,
                    price=fill_sell,
                    trigger=sell_level,
                    at=ts,
                    leg_kind="pm_chase" if used_chase else "trigger",
                    note=(
                        f"反T中点追价卖旧仓（目标{sell_level:.4f}）"
                        if used_chase
                        else "反T卖旧底仓（分钟第一触达；T+1可卖）"
                    ),
                )
                shares_now -= sell_old_qty
                sold_back = sell_old_qty
                touch_sell_at = ts
                exit_reason = "pm_chase" if used_chase else "trigger"
                continue

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

    at_end = len(minute_bars) >= len(sess_bars)
    if sold_back <= 0 and at_end:
        sell_old_qty = min(bought_qty, sell_old_cap)
        sess_close = float(sess_bar.get("close") or close)
        if cfg.get("must_cover_same_day") and sell_old_qty > 0 and _allows_eod_cover(
            minute_bars, sess_bars, defer_eod=defer_eod
        ):
            fill_sell = sess_close
            close_at = _session_close_at(sess_bars, sess_bar)
            cash_delta += append_t0_leg(
                trades,
                cost_model=cost_model,
                cost_params=cost_params,
                side="t0_sell",
                stock_code=stock_code,
                shares=sell_old_qty,
                price=fill_sell,
                trigger=sess_close,
                at=close_at,
                leg_kind="eod_cover",
                note="强制收盘卖旧底仓（T+1）",
            )
            shares_now -= sell_old_qty
            sold_back = sell_old_qty
            touch_sell_at = close_at or touch_sell_at
            exit_reason = "eod_cover"
        elif not cfg.get("must_cover_same_day"):
            exposure_pnl = round((sess_close - buy_price) * bought_qty, 2)

    if sold_back > 0:
        pnl = t0_pnl_from_trades(trades)
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
        "fees_total": t0_fees_total(trades),
        "cost_model": cost_model,
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
        "exit_reason": exit_reason,
        "note": "分钟第一触达（反T·T+1换仓）",
    }


def _touch_path_complete(out: dict, direction: str) -> bool:
    """第二腿 intraday 完成，或 session 末 eod / 放弃回补敞口已入账。"""
    if direction == "long_t":
        sold = int(out.get("sold_qty") or 0)
        covered = int(out.get("covered_qty") or 0)
        if sold <= 0:
            return False
        if covered >= sold:
            return True
        # 正T放弃买回（减仓落袋）或未回补敞口已标记
        if str(out.get("exit_reason") or "") in ("abandon_cover", "eod_cover"):
            return True
        return abs(float(out.get("exposure_pnl") or 0)) > 1e-9
    if direction == "reverse_t":
        bought = int(out.get("bought_qty") or 0)
        sold_back = int(out.get("sold_back_qty") or 0)
        if bought <= 0:
            return False
        if sold_back >= bought:
            return True
        return abs(float(out.get("exposure_pnl") or 0)) > 1e-9
    return bool(out.get("trades"))


def _side_trigger_pack(
    *,
    cfg_day: dict,
    cfg_side: dict,
    direction: str,
    atr_pct: Optional[float],
    score_snap: Optional[dict],
) -> Tuple[dict, float, float, str, Dict[str, float]]:
    """侧向执行参数 + 缩放后卖/买触发价与 fill_mode。"""
    from core.t0.score_policy import scale_t0_triggers, scores_have_any

    scaled_side = scale_triggers_with_atr(
        cfg_side,
        atr_pct=atr_pct,
    )
    sell_trig = float(scaled_side["sell_trigger_pct"])
    buy_trig = float(scaled_side["buy_trigger_pct"])
    fill_mode = str(cfg_side.get("fill_mode") or "trigger")
    meta: Dict[str, float] = {
        "sell_trigger_pct_base": round(sell_trig, 4),
        "buy_trigger_pct_base": round(buy_trig, 4),
        "trigger_scale": 1.0,
    }
    if str(cfg_day.get("direction") or "") == "dual_y" and scores_have_any(score_snap):
        trig = scale_t0_triggers(sell_trig, buy_trig, score_snap or {}, cfg_side)
        sell_trig = float(trig["sell_trigger_pct"])
        buy_trig = float(trig["buy_trigger_pct"])
        cfg_side = dict(cfg_side)
        cfg_side["sell_trigger_pct"] = sell_trig
        cfg_side["buy_trigger_pct"] = buy_trig
        meta = {
            "sell_trigger_pct_base": float(trig["sell_trigger_pct_base"]),
            "buy_trigger_pct_base": float(trig["buy_trigger_pct_base"]),
            "trigger_scale": float(trig["trigger_scale"]),
        }
    else:
        cfg_side = dict(cfg_side)
        cfg_side["sell_trigger_pct"] = sell_trig
        cfg_side["buy_trigger_pct"] = buy_trig
    return cfg_side, sell_trig, buy_trig, fill_mode, meta


def _minute_bar_time(m: dict) -> str:
    dt = str(m.get("datetime") or "")
    if len(dt) >= 16:
        return dt[11:16]
    return dt


def _forward_trace_row(
    *,
    idx: int,
    m: dict,
    prefix_bars: int,
    evaluated: bool,
    range_ok: Optional[bool] = None,
    range_pct: Optional[float] = None,
    min_range_pct: Optional[float] = None,
    direction: Optional[str] = None,
    side_range_ok: Optional[bool] = None,
    dir_amp_ok: Optional[bool] = None,
    seg_ok: Optional[bool] = None,
    entry_ready: bool = False,
    ref: Optional[float] = None,
    sell_trig: Optional[float] = None,
    buy_trig: Optional[float] = None,
    wait_reason: Optional[str] = None,
) -> Dict[str, Any]:
    sell_level = buy_level = None
    if ref is not None and sell_trig is not None:
        sell_level = round(float(ref) * (1.0 + float(sell_trig) / 100.0), 4)
    if ref is not None and buy_trig is not None:
        buy_level = round(float(ref) * (1.0 - float(buy_trig) / 100.0), 4)
    lo = float(m.get("low") or 0)
    hi = float(m.get("high") or 0)
    touch_leg1 = False
    touch_blocked = False
    if direction == "reverse_t" and buy_level is not None:
        touch_leg1 = lo <= float(buy_level)
    elif direction == "long_t" and sell_level is not None:
        touch_leg1 = hi >= float(sell_level)
    if touch_leg1 and not entry_ready:
        touch_blocked = True
    return {
        "idx": idx,
        "time": _minute_bar_time(m),
        "datetime": str(m.get("datetime") or ""),
        "open": round(float(m.get("open") or 0), 4),
        "high": round(float(hi), 4),
        "low": round(float(lo), 4),
        "close": round(float(m.get("close") or 0), 4),
        "prefix_bars": prefix_bars,
        "evaluated": evaluated,
        "range_ok": range_ok,
        "range_pct": round(float(range_pct), 4) if range_pct is not None else None,
        "min_range_pct": round(float(min_range_pct), 4) if min_range_pct is not None else None,
        "direction": direction,
        "side_range_ok": side_range_ok,
        "dir_amp_ok": dir_amp_ok,
        "seg_ok": seg_ok,
        "entry_ready": bool(entry_ready),
        "ref": round(float(ref), 4) if ref is not None else None,
        "sell_trig": round(float(sell_trig), 4) if sell_trig is not None else None,
        "buy_trig": round(float(buy_trig), 4) if buy_trig is not None else None,
        "sell_level": sell_level,
        "buy_level": buy_level,
        "touch_leg1": touch_leg1,
        "touch_blocked": touch_blocked,
        "pm_hit": False,
        "pm_block": False,
        "wait_reason": wait_reason,
        "leg1_fill": False,
        "leg2_fill": False,
    }


def _annotate_forward_trace(
    trace: Sequence[dict],
    out: Optional[dict],
    *,
    direction: Optional[str],
) -> List[Dict[str, Any]]:
    rows = [dict(r) for r in (trace or [])]
    if not rows or not isinstance(out, dict):
        return rows
    trades = out.get("trades") or []
    leg1_side = "t0_buy" if direction == "reverse_t" else "t0_sell"
    leg2_side = "t0_sell" if direction == "reverse_t" else "t0_buy"
    for t in trades:
        side = str(t.get("side") or "")
        at = str(t.get("at") or "")
        for row in rows:
            if at and at == str(row.get("datetime") or ""):
                if side == leg1_side:
                    row["leg1_fill"] = True
                elif side == leg2_side:
                    row["leg2_fill"] = True
                break
            if at and str(row.get("time") or "") and at[11:16] == str(row.get("time") or ""):
                if side == leg1_side:
                    row["leg1_fill"] = True
                elif side == leg2_side:
                    row["leg2_fill"] = True
                break
    return rows


def _annotate_trace_pm_blocks(
    trace: Sequence[dict],
    *,
    cfg: dict,
) -> List[Dict[str, Any]]:
    """午后禁新开：触价但 pm_hit 时标 pm_block（与执行器 not pm_hit 一致）。"""
    pm_hm = _parse_hm(cfg.get("t0_pm_degrade"))
    rows: List[Dict[str, Any]] = []
    for r in trace or []:
        row = dict(r)
        ts = row.get("datetime")
        pm_hit = bool(pm_hm and _hm_reached(ts, pm_hm))
        row["pm_hit"] = pm_hit
        row["pm_block"] = bool(pm_hit and row.get("touch_leg1"))
        rows.append(row)
    return rows


def _finalize_forward_trace(
    trace: Sequence[dict],
    out: Optional[dict],
    *,
    direction: Optional[str],
    cfg_side: dict,
) -> List[Dict[str, Any]]:
    rows = _annotate_forward_trace(trace, out, direction=direction)
    return _annotate_trace_pm_blocks(rows, cfg=cfg_side)


def _set_forward_trace_on_result(
    result: Dict[str, Any],
    trace: Sequence[dict],
    *,
    cfg_day: dict,
    direction: Optional[str] = None,
    dir_res: Optional[dict] = None,
) -> None:
    """把 forward_trace 收尾（成交腿标注 + 午后禁新开）写到结果 dict。"""
    if not trace:
        return
    dir_used = (
        direction
        or result.get("direction_used")
        or (dir_res or {}).get("direction")
    )
    cfg_side = (
        apply_side_exec_params(cfg_day, dir_used)
        if dir_used in {"long_t", "reverse_t"}
        else cfg_day
    )
    result["forward_trace"] = _finalize_forward_trace(
        trace,
        result,
        direction=dir_used,
        cfg_side=cfg_side,
    )


def _plan_forward_leg1_gates(
    *,
    mins: Sequence[dict],
    bar: dict,
    cost: float,
    cfg_day: dict,
    cfg_pre: dict,
    cash: float,
    shares: float,
    hist_bars: Optional[Sequence[dict]],
    atr_pct: Optional[float],
    score_snap: Optional[dict],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[dict]]:
    """滑动前缀：逐根评估门禁，构建 leg1 仅能在 entry_ready 根成交的 gate 函数。

    Returns (plan, early_exit, dir_res_for_finish).
    plan 含 leg1_gate_at / direction / cfg_side / triggers / ref / range_pct 等。
  early_exit 为 signal_skip / abandon / 全日振幅不足等应直接返回的结果。
    """
    from core.t0.score_policy import resolve_cover_policy

    n_bars = len(mins)
    entry_flags = [False] * n_bars
    trace_rows: List[Dict[str, Any]] = []
    direction_locked: Optional[str] = None
    dir_res_locked: Optional[dict] = None
    plan_tail: Optional[Dict[str, Any]] = None
    last_amp_skip: Optional[Dict[str, Any]] = None
    last_dir_wait: Optional[Dict[str, Any]] = None
    cover_meta: Optional[dict] = None

    for j in range(n_bars):
        m = mins[j]
        prefix = mins[: j + 1]
        if len(prefix) < 2:
            trace_rows.append(
                _forward_trace_row(
                    idx=j,
                    m=m,
                    prefix_bars=len(prefix),
                    evaluated=False,
                    direction=direction_locked,
                )
            )
            continue
        n = len(prefix)
        gate = prefix_range_gate(prefix, bar, cost=cost, cfg=cfg_pre)
        bar_n = gate.get("bar_day") or _day_ohlc_from_minutes(prefix, bar)
        ref = gate.get("ref")
        range_pct = gate.get("range_pct")
        if ref is None or float(ref) <= 0:
            trace_rows.append(
                _forward_trace_row(
                    idx=j,
                    m=m,
                    prefix_bars=n,
                    evaluated=False,
                    direction=direction_locked,
                )
            )
            continue
        if range_pct is None:
            trace_rows.append(
                _forward_trace_row(
                    idx=j,
                    m=m,
                    prefix_bars=n,
                    evaluated=False,
                    direction=direction_locked,
                )
            )
            continue
        if not gate.get("ok"):
            last_amp_skip = _skip_result(
                reason=(
                    f"振幅不足 {float(range_pct):.2f}% < {float(gate['min_range_pct']):.2f}%"
                    if not gate.get("flat")
                    else "一字板/无波动"
                ),
                shares=shares,
                bar=bar_n,
                extra={
                    "range_pct": range_pct,
                    "min_range_pct": gate["min_range_pct"],
                    "path_mode": "first_touch",
                    "range_mode": "forward",
                    "prefix_bars": n,
                },
            )
            trace_rows.append(
                _forward_trace_row(
                    idx=j,
                    m=m,
                    prefix_bars=n,
                    evaluated=True,
                    range_ok=False,
                    range_pct=range_pct,
                    min_range_pct=gate.get("min_range_pct"),
                    direction=direction_locked,
                    ref=float(ref),
                )
            )
            continue

        dir_res: Optional[dict] = None
        if direction_locked is None:
            dir_res = resolve_direction(
                bar=bar_n,
                ref=float(ref),
                cfg=cfg_day,
                cash=float(cash or 0),
                shares=shares,
                hist_bars=hist_bars,
                atr_pct=atr_pct,
                scores=score_snap,
            )
            if dir_res.get("skip") or not dir_res.get("direction"):
                early = _skip_result(
                    reason=str(dir_res.get("direction_reason") or "选向跳过"),
                    shares=shares,
                    bar=bar_n,
                    extra={
                        "direction_used": None,
                        "direction_score": dir_res.get("direction_score"),
                        "direction_reason": dir_res.get("direction_reason"),
                        "direction_features": dir_res.get("features"),
                        "signal_skip": True,
                        "range_pct": round(float(range_pct), 4),
                        "path_mode": "first_touch",
                        "range_mode": "forward",
                        "prefix_bars": n,
                        "forward_trace": trace_rows,
                    },
                )
                return None, early, dir_res

            cand = str(dir_res["direction"])
            cfg_try = apply_side_exec_params(cfg_day, cand)
            gate_try = prefix_range_gate(prefix, bar, cost=cost, cfg=cfg_try)
            if not gate_try.get("ok"):
                trace_rows.append(
                    _forward_trace_row(
                        idx=j,
                        m=m,
                        prefix_bars=n,
                        evaluated=True,
                        range_ok=True,
                        range_pct=range_pct,
                        min_range_pct=gate.get("min_range_pct"),
                        direction=cand,
                        side_range_ok=False,
                        ref=float(ref),
                    )
                )
                continue

            direction_locked = cand
            dir_res_locked = dir_res
            if str(cfg_day.get("direction") or "") == "dual_y":
                cover_meta = resolve_cover_policy(
                    scores=score_snap or {},
                    direction=direction_locked,
                    cfg=cfg_try,
                )
                cfg_try = dict(cfg_try)
                cfg_try["must_cover_same_day"] = bool(cover_meta.get("must_cover"))
        else:
            dir_res = dir_res_locked

        direction = str(direction_locked)
        cfg_side, sell_trig, buy_trig, fill_mode, trigger_meta = _side_trigger_pack(
            cfg_day=cfg_day,
            cfg_side=apply_side_exec_params(cfg_day, direction),
            direction=direction,
            atr_pct=atr_pct,
            score_snap=score_snap,
        )
        if cover_meta is not None:
            cfg_side["must_cover_same_day"] = bool(cover_meta.get("must_cover"))

        gate_side = prefix_range_gate(prefix, bar, cost=cost, cfg=cfg_side)
        if not gate_side.get("ok"):
            range_pct_side = gate_side.get("range_pct")
            last_amp_skip = _skip_result(
                reason=(
                    f"振幅不足 {float(range_pct_side):.2f}% < {float(gate_side['min_range_pct']):.2f}%"
                    if range_pct_side is not None and not gate_side.get("flat")
                    else "一字板/无波动"
                ),
                shares=shares,
                bar=bar_n,
                extra={
                    "range_pct": range_pct_side,
                    "min_range_pct": gate_side["min_range_pct"],
                    "path_mode": "first_touch",
                    "range_mode": "forward",
                    "prefix_bars": n,
                    "direction_used": direction,
                },
            )
            trace_rows.append(
                _forward_trace_row(
                    idx=j,
                    m=m,
                    prefix_bars=n,
                    evaluated=True,
                    range_ok=True,
                    range_pct=range_pct,
                    min_range_pct=gate.get("min_range_pct"),
                    direction=direction,
                    side_range_ok=False,
                    ref=float(ref),
                    sell_trig=sell_trig,
                    buy_trig=buy_trig,
                )
            )
            continue

        range_pct_side = float(gate_side.get("range_pct") or range_pct or 0)
        dir_amp = prefix_directional_amplitude_ok(
            prefix,
            direction=direction,
            ref=float(ref),
            sell_trig=sell_trig,
            buy_trig=buy_trig,
        )
        seg = prefix_segment_entry_ok(
            prefix,
            direction=direction,
            ref=float(ref),
            sell_trig=sell_trig,
            buy_trig=buy_trig,
            cfg=cfg_side,
        )
        entry_ready = bool(dir_amp.get("ok") and seg.get("ok"))
        entry_flags[j] = entry_ready
        wait_reason = None
        if not entry_ready:
            wait_reason = str(
                seg.get("reason") if dir_amp.get("ok") else dir_amp.get("reason") or "方向振幅未达标"
            )

        trace_rows.append(
            _forward_trace_row(
                idx=j,
                m=m,
                prefix_bars=n,
                evaluated=True,
                range_ok=True,
                range_pct=range_pct_side,
                min_range_pct=gate_side.get("min_range_pct"),
                direction=direction,
                side_range_ok=True,
                dir_amp_ok=bool(dir_amp.get("ok")),
                seg_ok=bool(seg.get("ok")),
                entry_ready=entry_ready,
                ref=float(ref),
                sell_trig=sell_trig,
                buy_trig=buy_trig,
                wait_reason=wait_reason,
            )
        )

        plan_tail = {
            "direction": direction,
            "dir_res": dir_res_locked or dir_res,
            "cfg_side": cfg_side,
            "sell_trig": sell_trig,
            "buy_trig": buy_trig,
            "fill_mode": fill_mode,
            "trigger_scale_meta": trigger_meta,
            "ref": float(ref),
            "range_pct": range_pct_side,
            "bar_day": bar_n,
            "cover_meta": cover_meta,
        }

        if not entry_ready:
            last_dir_wait = _skip_result(
                reason=wait_reason or "方向振幅未达标",
                shares=shares,
                bar=bar_n,
                extra={
                    "direction_used": direction,
                    "path_mode": "first_touch",
                    "range_mode": "forward",
                    "prefix_bars": n,
                    "range_pct": range_pct_side,
                    "directional_amplitude": dir_amp,
                    "prefix_segment": seg,
                },
            )
            abandon_on = bool(cfg_day.get("y_path_abandon_enabled", True))
            abandon_bars = resolve_path_abandon_bars(cfg_side, direction)
            if abandon_on and n >= abandon_bars:
                if not dir_amp.get("ok"):
                    if direction == "reverse_t":
                        abandon_reason = f"前缀无低吸空间，放弃反T（{wait_reason}）"
                    elif direction == "long_t":
                        abandon_reason = f"前缀无高抛空间，放弃正T（{wait_reason}）"
                    else:
                        abandon_reason = wait_reason
                elif direction == "reverse_t":
                    abandon_reason = f"前缀无反弹确认，放弃反T（{wait_reason}）"
                elif direction == "long_t":
                    abandon_reason = f"前缀无回落确认，放弃正T（{wait_reason}）"
                else:
                    abandon_reason = wait_reason
                early = _skip_result(
                    reason=abandon_reason,
                    shares=shares,
                    bar=bar_n,
                    extra={
                        "direction_used": direction,
                        "path_mode": "first_touch",
                        "range_mode": "forward",
                        "prefix_bars": n,
                        "range_pct": range_pct_side,
                        "directional_amplitude": dir_amp,
                        "prefix_segment": seg,
                        "path_abandon": True,
                        "forward_trace": trace_rows,
                    },
                )
                return None, early, dir_res_locked or dir_res

    if plan_tail is None:
        if last_amp_skip is not None:
            if isinstance(last_amp_skip, dict):
                last_amp_skip = dict(last_amp_skip)
                last_amp_skip["forward_trace"] = trace_rows
            return None, last_amp_skip, None
        return None, None, None

    def leg1_gate_at(idx: int) -> bool:
        if idx < 0 or idx >= n_bars:
            return False
        return bool(entry_flags[idx])

    plan = dict(plan_tail)
    plan["leg1_gate_at"] = leg1_gate_at
    plan["gate_trace"] = trace_rows
    if not any(entry_flags) and last_dir_wait is not None:
        plan["pending_wait"] = last_dir_wait
    return plan, None, plan_tail.get("dir_res")


def run_forward_first_touch(
    *,
    minute_bars: Sequence[dict],
    bar: dict,
    shares: float,
    cost: float,
    sellable_shares: Optional[float],
    cfg_day: dict,
    cfg_pre: dict,
    cash: float,
    stock_code: str,
    lot: int,
    cost_model: str,
    cost_params: dict,
    atr_pct: Optional[float],
    hist_bars: Optional[Sequence[dict]],
    score_snap: Optional[dict],
    session_bar: Optional[dict] = None,
    base_t0_ratio: Optional[float] = None,
    defer_eod: bool = True,
) -> Tuple[Optional[Dict[str, Any]], Optional[dict]]:
    """前向滑动窗口第一触达：仅在当前根 entry_ready 时允许开第一腿。

    ``defer_eod=True``（Worker 盘中前缀）：末根未到 14:55 不强平。
    回测传 ``False``：当日收盘强制回补，即使分钟缓存在午前截断。
    """
    mins = list(minute_bars)
    bar_session = session_bar or _day_ohlc_from_minutes(mins, bar)
    plan, early, dir_res = _plan_forward_leg1_gates(
        mins=mins,
        bar=bar,
        cost=cost,
        cfg_day=cfg_day,
        cfg_pre=cfg_pre,
        cash=cash,
        shares=shares,
        hist_bars=hist_bars,
        atr_pct=atr_pct,
        score_snap=score_snap,
    )
    if early is not None:
        if isinstance(early, dict):
            extra = early.get("extra") if isinstance(early.get("extra"), dict) else {}
            ft = extra.get("forward_trace") or early.get("forward_trace")
            if ft:
                _set_forward_trace_on_result(early, ft, cfg_day=cfg_day, dir_res=dir_res)
        return early, dir_res
    if plan is None:
        return None, dir_res

    path_kwargs = {
        "session_bars": mins,
        "session_bar": bar_session,
        "defer_eod": bool(defer_eod),
        "leg1_gate_at": plan["leg1_gate_at"],
    }
    direction = str(plan["direction"])
    cfg_side = plan["cfg_side"]
    sell_trig = float(plan["sell_trig"])
    buy_trig = float(plan["buy_trig"])
    fill_mode = str(plan["fill_mode"])
    ref = float(plan["ref"])
    range_pct = float(plan["range_pct"])
    bar_day = plan["bar_day"]
    trigger_scale_meta = plan["trigger_scale_meta"]
    cover_meta = plan.get("cover_meta")
    dir_res = plan.get("dir_res") or dir_res

    if not any(plan["leg1_gate_at"](i) for i in range(len(mins))):
        pending = plan.get("pending_wait")
        if isinstance(pending, dict):
            pending = dict(pending)
            _set_forward_trace_on_result(
                pending,
                plan.get("gate_trace") or [],
                cfg_day=cfg_day,
                direction=direction,
                dir_res=dir_res,
            )
            return pending, dir_res

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
            cfg=cfg_side,
            cost_model=cost_model,
            cost_params=cost_params,
            stock_code=stock_code,
            atr_pct=atr_pct,
            range_pct=range_pct,
            **path_kwargs,
        )
    else:
        ratio = float(base_t0_ratio if base_t0_ratio is not None else cfg_day.get("t0_ratio") or 1.0)
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
            cfg=cfg_side,
            cost_model=cost_model,
            cost_params=cost_params,
            stock_code=stock_code,
            atr_pct=atr_pct,
            range_pct=range_pct,
            t0_ratio=ratio,
            **path_kwargs,
        )

    if isinstance(out, dict):
        out["path_mode"] = "first_touch"
        out["intraday_path"] = "first_touch"
        out["range_mode"] = "forward"
        out["prefix_bars"] = len(mins)
        out["minute_bars"] = len(mins)
        out["direction_score"] = (dir_res or {}).get("direction_score")
        out["direction_reason"] = (dir_res or {}).get("direction_reason")
        out["direction_features"] = (dir_res or {}).get("features")
        if cover_meta:
            out["cover_policy"] = cover_meta
            out["must_cover_same_day"] = bool(cover_meta.get("must_cover"))
        base_ratio = float(base_t0_ratio if base_t0_ratio is not None else cfg_day.get("t0_ratio") or 1.0)
        out["t0_ratio_base"] = round(base_ratio, 4)
        out["t0_ratio"] = round(float(cfg_day.get("t0_ratio") or base_ratio), 4)
        out.update(trigger_scale_meta)
        out["sell_trigger_pct"] = round(sell_trig, 4)
        out["buy_trigger_pct"] = round(buy_trig, 4)
        _set_forward_trace_on_result(
            out,
            plan.get("gate_trace") or [],
            cfg_day=cfg_day,
            direction=direction,
            dir_res=dir_res,
        )
    return out, dir_res


def simulate_t0_day_minute(
    *,
    bar: dict,
    minute_bars: Sequence[dict],
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
    scores: Optional[dict] = None,
    tau_pool_day: Optional[dict] = None,
) -> Dict[str, Any]:
    """单日做 T：选向仍用开盘/隔夜特征；成交路径按分钟第一触达（前向滑动窗口）。

    振幅/段向门禁按 5m 前缀逐根评估；第一腿仅在当前根 entry_ready 时触价成交（不回溯）。
    """
    from core.t0.score_policy import (
        attach_day_scores,
        resolve_scores_for_code,
        resolve_y_score_source,
        resolve_cover_policy,
        scale_t0_triggers,
        scores_have_any,
        tau_pool_day_score_kwargs,
    )

    cfg = load_t0_rules(rules)
    cost_model, cost_params = resolve_t0_cost_context(paper=paper, cost_config=cost_config)
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
                allow_fallback=(resolve_y_score_source(cfg) != "compute"),
                **tau_pool_day_score_kwargs(tau_pool_day, stock_code),
            )

    mins = [dict(m) for m in (minute_bars or [])]
    mins.sort(key=lambda x: str(x.get("datetime") or ""))
    scaled = scale_triggers_with_atr(cfg, atr_pct=atr_pct)
    sell_trig = float(scaled["sell_trigger_pct"])
    buy_trig = float(scaled["buy_trigger_pct"])
    cfg_day = dict(cfg)
    cfg_day["sell_trigger_pct"] = sell_trig
    cfg_day["buy_trigger_pct"] = buy_trig
    cfg_day["path_mode"] = "first_touch"
    trigger_scale_meta: Dict[str, float] = {
        "sell_trigger_pct_base": round(sell_trig, 4),
        "buy_trigger_pct_base": round(buy_trig, 4),
        "trigger_scale": 1.0,
    }
    if str(cfg_day.get("direction") or "") == "dual_y" and scores_have_any(score_snap):
        trig = scale_t0_triggers(sell_trig, buy_trig, score_snap or {}, cfg_day)
        sell_trig = float(trig["sell_trigger_pct"])
        buy_trig = float(trig["buy_trigger_pct"])
        cfg_day["sell_trigger_pct"] = sell_trig
        cfg_day["buy_trigger_pct"] = buy_trig
        trigger_scale_meta = {
            "sell_trigger_pct_base": float(trig["sell_trigger_pct_base"]),
            "buy_trigger_pct_base": float(trig["buy_trigger_pct_base"]),
            "trigger_scale": float(trig["trigger_scale"]),
        }

    # path实对照：用 path 模型训练触发（与 ŷ_path 同口径）；成交仍用上方 execution 触发
    try:
        from core.research.path_ridge import load_path_model, path_label_triggers

        path_sell_trig, path_buy_trig = path_label_triggers(load_path_model())
    except Exception:  # noqa: BLE001
        logger.debug("path_label_triggers fallback", exc_info=True)
        path_sell_trig = float(trigger_scale_meta["sell_trigger_pct_base"])
        path_buy_trig = float(trigger_scale_meta["buy_trigger_pct_base"])

    def _finish(out: Optional[Dict[str, Any]], dir_res: Optional[dict] = None) -> Dict[str, Any]:
        feats = (dir_res or {}).get("features") if isinstance(dir_res, dict) else None
        packed = attach_day_scores(out, score_snap, features=feats)
        try:
            from core.t0.score_policy import attach_eod_tau_realized

            packed = attach_eod_tau_realized(
                packed,
                open_px=bar.get("open") if isinstance(bar, dict) else None,
                close_px=bar.get("close") if isinstance(bar, dict) else None,
                prev_close=bar.get("prev_close") if isinstance(bar, dict) else None,
            )
        except Exception:  # noqa: BLE001
            logger.debug("attach_eod_tau_realized failed", exc_info=True)
        try:
            from core.research.path_panel import attach_path_realized

            ref = None
            if isinstance(bar, dict):
                ref = bar.get("open")
            if ref is None and isinstance(out, dict):
                ref = out.get("open")
            packed = attach_path_realized(
                packed,
                mins,
                ref=float(ref) if ref is not None else None,
                sell_trig_pct=float(path_sell_trig),
                buy_trig_pct=float(path_buy_trig),
            )
            if isinstance(packed, dict):
                packed["path_realized_trig"] = {
                    "path_label_mode": "extreme_order",
                    "note": "对照=极值序 signed (H−L)/ref%；非触价触发",
                }
        except Exception:  # noqa: BLE001
            logger.debug("attach_path_realized failed", exc_info=True)
        return packed


    if len(mins) < 2:
        return _finish(
            _skip_result(
                reason="分钟线不足，无法第一触达",
                shares=shares,
                bar=bar,
                extra={"path_mode": "first_touch"},
            )
        )

    bar_day = _day_ohlc_from_minutes(mins, bar)
    # 分钟缓存若在午前截断，高低用已有 5m；收盘价保留日线，供强制回补
    daily_close = float((bar or {}).get("close") or 0)
    bar_session = dict(bar_day)
    if daily_close > 0:
        bar_session["close"] = daily_close
    lot = int(cfg["lot_size"])
    high = float(bar_day.get("high") or 0)
    low = float(bar_day.get("low") or 0)
    if high <= 0 or low <= 0 or shares <= 0:
        return _error_result("无效 bar 或持仓", shares)

    base_t0_ratio = float(cfg_day.get("t0_ratio") or 1.0)
    # 动仓比例固定为基准；ŷ 信心改缩放卖/买目标价（见 trigger_scale_meta）
    # 定向前总量振幅用两侧振幅下限的较松者，定方向后再用侧向下限复核
    cfg_pre = dict(cfg_day)
    try:
        ml = cfg_day.get("min_range_pct_long")
        mr = cfg_day.get("min_range_pct_reverse")
        if ml is not None and mr is not None:
            cfg_pre["min_range_pct"] = min(float(ml), float(mr))
    except (TypeError, ValueError):
        pass

    out, dir_res = run_forward_first_touch(
        minute_bars=mins,
        bar=bar,
        shares=shares,
        cost=cost,
        sellable_shares=sellable_shares,
        cfg_day=cfg_day,
        cfg_pre=cfg_pre,
        cash=cash,
        stock_code=stock_code,
        lot=lot,
        cost_model=cost_model,
        cost_params=cost_params,
        atr_pct=scaled.get("atr_pct") if scaled.get("atr_pct") is not None else atr_pct,
        hist_bars=hist_bars,
        score_snap=score_snap,
        session_bar=bar_session,
        base_t0_ratio=base_t0_ratio,
        defer_eod=False,
    )
    if out is None:
        return _finish(
            _skip_result(
                reason="分钟线不足，无法第一触达",
                shares=shares,
                bar=bar_day,
                extra={"path_mode": "first_touch", "range_mode": "forward"},
            ),
            dir_res,
        )
    return _finish(out, dir_res)
