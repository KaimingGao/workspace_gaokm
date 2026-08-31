"""做 T：分钟线第一触达路径（已删除日线 high/low 代理）。

方向：sell_then_buy=反T（先卖后买），buy_then_sell=正T（先买后卖）。
"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.t0.costs import (
    apply_t0_leg_costs,
    append_t0_leg,
    resolve_t0_cost_context,
    t0_fees_total,
    t0_pnl_from_trades,
)
from core.t0.config import (
    load_t0_rules,
    resolve_min_range_pct,
    apply_side_exec_params,
    resolve_path_abandon_bars,
    t0_dir_label,
)
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

# 主动放弃回补（已入账 → 计完成）；盘中前缀未完成为 defer_eod_pending（不计完成）
T0_INTENTIONAL_ABANDON_EXITS = frozenset(
    {"abandon_cover", "abandon_cover_cash", "abandon_cover_cap"}
)
T0_PENDING_EXIT = "defer_eod_pending"


def _t0_stop_params(cfg: dict, direction: str) -> Tuple[float, int, bool]:
    """正/反T止损：(pct, arm_bars, on_close)。pct≤0 表示关。

    正T：跌破买价×(1−pct%)；反T：涨破卖价×(1+pct%)。
    延迟根 / 收盘确认共用。
    """
    key = (
        "t0_stop_pct_buy_then_sell"
        if direction == "buy_then_sell"
        else "t0_stop_pct_sell_then_buy"
    )
    try:
        raw = (cfg or {}).get(key)
        pct = float(0.0 if raw is None or raw == "" else raw)
    except (TypeError, ValueError):
        pct = 0.0
    pct = max(0.0, min(pct, 20.0))
    try:
        raw_arm = (cfg or {}).get("t0_stop_arm_bars")
        arm = int(2 if raw_arm is None or raw_arm == "" else raw_arm)
    except (TypeError, ValueError):
        arm = 2
    arm = max(0, min(arm, 48))
    from core.t0.config import coerce_cfg_bool

    on_close = coerce_cfg_bool((cfg or {}).get("t0_stop_on_close"), True)
    return pct, arm, on_close


def _bts_stop_params(cfg: dict) -> Tuple[float, int, bool]:
    """兼容旧调用：正T止损参数。"""
    return _t0_stop_params(cfg, "buy_then_sell")


def _tplus1_skip_reason(*, side: str, shares: float, sellable: float, lot: int) -> str:
    """可卖不足 1 手：主因是 T+1，不是动仓比例。"""
    sh = int(shares)
    sv = int(sellable)
    if side == "buy_then_sell":
        if sv <= 0:
            return (
                f"正T：可卖旧仓 0 股（持仓 {sh} 全被 T+1 锁定），"
                f"第二腿卖不掉旧仓"
            )
        return (
            f"正T：可卖旧仓仅 {sv} 股 < {lot}（持仓 {sh}），"
            f"第二腿不够 1 手"
        )
    if sv <= 0:
        return f"反T：可卖 0 股（持仓 {sh} 全被 T+1 锁定），无法先卖"
    return f"反T：可卖仅 {sv} 股 < {lot}（持仓 {sh}），不够 1 手"


def _ratio_lot_skip_reason(*, side: str, shares: float, t0_ratio: float, lot: int) -> str:
    """可卖够、但持仓×动仓比例仍不足 1 手（动仓固定 100%）。"""
    sh = int(shares)
    raw = int(float(shares) * float(t0_ratio))
    pct = f"{float(t0_ratio):.0%}"
    tag = t0_dir_label(side)
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


def _hm_reached(
    ts: Any,
    stop_hm: Optional[Tuple[int, int]],
    *,
    inclusive: bool = True,
) -> bool:
    """是否已到/过 ``stop_hm``。

    ``inclusive=True``（默认）：端点计入（收盘窗 14:55 整根可用）。
    ``inclusive=False``：端点不计（午后禁新开 ``t0_pm_degrade=13:00`` 时 13:00 根仍可开）。
    """
    if not stop_hm:
        return False
    cur = _ts_hm(ts)
    if not cur:
        return False
    if cur[0] > stop_hm[0]:
        return True
    if cur[0] < stop_hm[0]:
        return False
    if inclusive:
        return cur[1] >= stop_hm[1]
    return cur[1] > stop_hm[1]


def _allows_eod_cover(
    minute_bars: Sequence[dict],
    *,
    defer_eod: bool,
) -> bool:
    """defer_eod 时仅末根 ≥14:55 才强制回补，避免盘中前缀误当收盘。"""
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
    """正T/反T 中点追价：目标 = 旧目标与现价中点；到点起算，之后每 interval_min 再调。

    正T：卖旧仓目标下移（第二腿卖）；反T：买回目标上移（第二腿买）。触不到则走 eod / 放弃回补。

    Returns (new_level, last_chase_min, adjusted_this_bar).
    """
    if not pm_hm or not _hm_reached(ts, pm_hm, inclusive=True):
        return level, last_chase_min, False
    if not (level > 0 and px > 0):
        return level, last_chase_min, False
    cur_min = _ts_minutes(ts)
    if cur_min is None:
        return level, last_chase_min, False
    if last_chase_min is not None and (cur_min - last_chase_min) < interval_min:
        return level, last_chase_min, False
    return (level + px) / 2.0, cur_min, True


def _max_affordable_buy_lots(
    cash: float,
    price: float,
    lot: int,
    *,
    cost_model: str,
    cost_params: dict,
    cap: Optional[int] = None,
) -> int:
    """按含费净现金可买手数（不透支）；``cap`` 为股数上限（已手数对齐亦可）。"""
    if cash <= 0 or price <= 0:
        return 0
    lot_i = max(int(lot or 100), 1)
    qty = _lot_floor(cash / price, lot_i)
    if cap is not None:
        qty = min(qty, _lot_floor(float(cap), lot_i))
    while qty >= lot_i:
        probe = apply_t0_leg_costs(
            {"side": "t0_buy", "shares": qty, "price": price},
            cost_model=cost_model,
            cost_params=cost_params,
        )
        if cash + float(probe.get("net_cash_delta") or 0) >= -1e-6:
            return int(qty)
        qty -= lot_i
    return 0


def _buy_self_funded(
    cash_delta: float,
    *,
    shares: float,
    price: float,
    cost_model: str,
    cost_params: dict,
) -> bool:
    """卖出净得是否够覆盖一笔买回（含手续费）。"""
    if shares <= 0 or price <= 0:
        return False
    probe = apply_t0_leg_costs(
        {"side": "t0_buy", "shares": shares, "price": price},
        cost_model=cost_model,
        cost_params=cost_params,
    )
    return cash_delta + float(probe.get("net_cash_delta") or 0) >= -1e-6


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

    判断波动幅度是否达 min_range_pct（一字板视为不足）。

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


def _prefix_bar_ratio_params(cfg: dict, direction: str) -> tuple[int, float]:
    """固定前缀：根数 + 后半段阳/阴K占比阈值（正T上涨 / 反T下跌）。"""
    direction = str(direction or "").strip().lower()
    bars = resolve_path_abandon_bars(cfg or {}, direction)
    if direction == "sell_then_buy":
        key, default = "y_prefix_downbar_ratio_sell_then_buy", 0.2
    else:
        key, default = "y_prefix_upbar_ratio_buy_then_sell", 0.2
    try:
        raw = (cfg or {}).get(key)
        ratio = float(default if raw is None or raw == "" else raw)
    except (TypeError, ValueError):
        ratio = float(default)
    return int(bars), max(0.0, min(float(ratio), 1.0))


def _prefix_upbar_ratio_params(cfg: dict) -> tuple[int, float]:
    """兼容别名：正T固定前缀参数。"""
    return _prefix_bar_ratio_params(cfg or {}, "buy_then_sell")


def _score_y_tau(score_snap: Optional[dict]) -> Optional[float]:
    """从分数快照取 ŷ_τ（百分点）= OC 开→收；与定向同口径。"""
    if not isinstance(score_snap, dict):
        return None
    for key in ("y_tau_oc", "predicted_score_tau_oc", "y_tau", "predicted_score_tau", "score_rem", "yhat_tau"):
        raw = score_snap.get(key)
        if raw is None or raw == "":
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _score_y_path(score_snap: Optional[dict]) -> Optional[float]:
    """从分数快照取 ŷ_path（极值序 signed range%）。"""
    if not isinstance(score_snap, dict):
        return None
    for key in ("y_path", "predicted_score_path"):
        raw = score_snap.get(key)
        if raw is None or raw == "":
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def prefix_range_vs_path_ok(
    *,
    range_pct: Optional[float],
    y_path: Optional[float],
    cfg: Optional[dict] = None,
) -> Dict[str, Any]:
    """前缀窗振幅 (H−L)/ref% 已超过 |ŷ_path| → 跳过（空间用尽 / 与 path 幅度矛盾）。

    缺 ŷ_path、无效振幅、或 ``y_prefix_vs_path_skip=False`` 时放行。
    """
    from core.t0.config import coerce_cfg_bool

    enabled = coerce_cfg_bool((cfg or {}).get("y_prefix_vs_path_skip"), True)
    base: Dict[str, Any] = {
        "enabled": enabled,
        "range_pct": None if range_pct is None else round(float(range_pct), 4),
        "y_path": None if y_path is None else round(float(y_path), 4),
        "abs_y_path": None,
    }
    if not enabled:
        return {**base, "ok": True, "skipped": True, "reason": "前缀vs|ŷ_path|闸关"}
    if range_pct is None or y_path is None:
        return {**base, "ok": True, "skipped": True, "reason": "缺前缀振幅或ŷ_path，跳过闸"}
    try:
        rp = float(range_pct)
        yp = abs(float(y_path))
    except (TypeError, ValueError):
        return {**base, "ok": True, "skipped": True, "reason": "振幅/ŷ_path无效，跳过闸"}
    base["abs_y_path"] = round(yp, 4)
    ok = rp <= yp + 1e-9
    return {
        **base,
        "ok": ok,
        "skipped": False,
        "reason": (
            f"前缀振幅{rp:.3f}%≤|ŷ_path|{yp:.3f}%"
            if ok
            else f"前缀振幅{rp:.3f}%>|ŷ_path|{yp:.3f}%：空间用尽跳过"
        ),
    }


def _tau_entry_price_mult(cfg: Optional[dict]) -> float:
    """第一腿相对开盘允许带宽：|ŷ_τ|% × 倍数（越大越宽/越松）；≤0 关闭门禁。"""
    try:
        raw = (cfg or {}).get("y_tau_entry_price_mult")
        mult = float(0.0 if raw is None or raw == "" else raw)
    except (TypeError, ValueError):
        mult = 0.0
    return max(0.0, min(mult, 50.0))


def tau_leg1_fill_price_ok(
    *,
    fill_px: float,
    ref: float,
    y_tau: Optional[float],
    direction: str,
    cfg: Optional[dict] = None,
    mult: Optional[float] = None,
) -> Dict[str, Any]:
    """固定前缀确认根：第一腿成交价相对开盘允许带宽 = |ŷ_τ|% × mult。

    mult 是**带宽乘数**（越大越宽松）；默认已下线（0=关）。正T：买价 ≤ open×(1+band)；
    反T：卖价 ≥ open×(1−band)。缺 ŷ_τ 或倍数≤0 时跳过（ok=True）。
    """
    direction = str(direction or "").strip().lower()
    m = float(mult) if mult is not None else _tau_entry_price_mult(cfg)
    base: Dict[str, Any] = {
        "direction": direction,
        "mult": round(m, 4),
        "y_tau": None if y_tau is None else round(float(y_tau), 4),
        "ref": round(float(ref), 4) if ref else None,
        "fill_px": round(float(fill_px), 4) if fill_px else None,
    }
    if m <= 0 or ref is None or float(ref) <= 0 or fill_px is None or float(fill_px) <= 0:
        return {**base, "ok": True, "skipped": True, "reason": "τ入场价门禁关/无效价"}
    if y_tau is None:
        return {**base, "ok": True, "skipped": True, "reason": "缺ŷ_τ，跳过入场价门禁"}
    band_pct = abs(float(y_tau)) * m
    base["band_pct"] = round(band_pct, 4)
    px = float(fill_px)
    o = float(ref)
    if direction == "buy_then_sell":
        ceil = o * (1.0 + band_pct / 100.0)
        base["bound_px"] = round(ceil, 4)
        base["bound_kind"] = "ceil"
        ok = px <= ceil + 1e-9
        return {
            **base,
            "ok": ok,
            "skipped": False,
            "reason": (
                f"正T买价≤开盘×(1+{m:g}×|ŷ_τ|)：{px:.3f}≤{ceil:.3f}"
                if ok
                else (
                    f"正T买价超τ带 {px:.3f}>{ceil:.3f}"
                    f"（开盘×(1+{m:g}×|{float(y_tau):.3f}%|））"
                ),
            ),
        }
    if direction == "sell_then_buy":
        floor = o * (1.0 - band_pct / 100.0)
        base["bound_px"] = round(floor, 4)
        base["bound_kind"] = "floor"
        ok = px >= floor - 1e-9
        return {
            **base,
            "ok": ok,
            "skipped": False,
            "reason": (
                f"反T卖价≥开盘×(1−{m:g}×|ŷ_τ|)：{px:.3f}≥{floor:.3f}"
                if ok
                else (
                    f"反T卖价破τ带 {px:.3f}<{floor:.3f}"
                    f"（开盘×(1−{m:g}×|{float(y_tau):.3f}%|））"
                ),
            ),
        }
    return {**base, "ok": True, "skipped": True, "reason": "非正/反T，跳过入场价门禁"}


def prefix_fixed_bar_ratio_entry_ok(
    minute_bars: Sequence[dict],
    *,
    direction: str,
    cfg: dict,
) -> Dict[str, Any]:
    """固定前缀门禁：前 N 根齐后看后半段阴阳占比。

    正T：close>open 占比≥阈值；反T：close<open 占比≥阈值。
    取消贪心滚动探极值 / 回落·反弹。
    """
    direction = str(direction or "").strip().lower()
    want_up = direction == "buy_then_sell"
    if direction not in ("buy_then_sell", "sell_then_buy"):
        return {
            "ok": True,
            "direction": direction,
            "segment": "skip",
            "reason": "非正/反T，跳过固定前缀占比",
        }
    side = "正T" if want_up else "反T"
    seg_name = "fixed_upbar" if want_up else "fixed_downbar"
    ratio_key = "upbar_ratio" if want_up else "downbar_ratio"
    thr_key = "upbar_ratio_thr" if want_up else "downbar_ratio_thr"
    count_key = "up_bars" if want_up else "down_bars"
    move_label = "上涨" if want_up else "下跌"

    fixed_n, thr = _prefix_bar_ratio_params(cfg or {}, direction)
    bars = [b for b in (minute_bars or []) if isinstance(b, dict)]
    base: Dict[str, Any] = {
        "direction": direction,
        "segment": seg_name,
        "fixed_bars": fixed_n,
        thr_key: round(thr, 4),
    }
    if len(bars) < fixed_n:
        return {
            **base,
            "ok": False,
            "segment": "fixed_prefix_wait",
            "prefix_bars": len(bars),
            "reason": f"{side}待固定前缀 {len(bars)}/{fixed_n}",
        }

    win = bars[:fixed_n]
    half = max(1, fixed_n // 2)
    tail = win[-half:]
    hit_n = 0
    valid_n = 0
    for b in tail:
        o = float(b.get("open") or 0)
        c = float(b.get("close") or 0)
        if o <= 0 or c <= 0:
            continue
        valid_n += 1
        if (c > o) if want_up else (c < o):
            hit_n += 1
    if valid_n <= 0:
        return {
            **base,
            "ok": False,
            "segment": seg_name,
            "prefix_bars": fixed_n,
            "half_bars": half,
            "reason": f"{side}固定前缀后半段无有效 OHLC",
        }
    ratio = hit_n / float(valid_n)
    ok = ratio + 1e-12 >= thr
    return {
        **base,
        "ok": ok,
        "segment": seg_name,
        "prefix_bars": fixed_n,
        "half_bars": half,
        count_key: hit_n,
        "half_valid": valid_n,
        ratio_key: round(ratio, 4),
        "reason": (
            f"{side}固定前缀后半{move_label} {hit_n}/{valid_n}={ratio:.0%}≥{thr:.0%}"
            if ok
            else f"{side}固定前缀后半{move_label} {hit_n}/{valid_n}={ratio:.0%}<{thr:.0%}"
        ),
    }


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


def _first_touch_sell_then_buy(
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
    """反 T 分钟路径：先卖后买回。"""
    close = float(bar.get("close") or 0)
    sess_bars = session_bars if session_bars is not None else minute_bars
    sess_bar = session_bar if session_bar is not None else bar
    sellable = float(sellable_shares if sellable_shares is not None else shares)
    sellable = min(sellable, shares)
    sell_level = ref * (1.0 + sell_trig / 100.0)
    if sellable < lot:
        return _skip_result(
            reason=_tplus1_skip_reason(
                side="sell_then_buy", shares=shares, sellable=sellable, lot=lot
            ),
            shares=shares,
            bar=bar,
            extra={
                "direction_used": "sell_then_buy",
                "sellable_shares": sellable,
                "t0_ratio": t0_ratio,
                "path_mode": "first_touch",
            },
        )
    qty = _t0_qty_lots(shares, t0_ratio, lot, sellable)
    if qty <= 0:
        return _skip_result(
            reason=_ratio_lot_skip_reason(
                side="sell_then_buy", shares=shares, t0_ratio=t0_ratio, lot=lot
            ),
            shares=shares,
            bar=bar,
            extra={
                "direction_used": "sell_then_buy",
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
    stop_pct, stop_arm_bars, stop_on_close = _t0_stop_params(cfg, "sell_then_buy")
    leg1_idx: Optional[int] = None
    stop_level: Optional[float] = None

    for idx, mb in enumerate(minute_bars):
        hi = float(mb.get("high") or 0)
        lo = float(mb.get("low") or 0)
        close = float(mb.get("close") or 0)
        ts = mb.get("datetime") or mb.get("date")
        pm_hit = bool(pm_hm and _hm_reached(ts, pm_hm, inclusive=False))

        # 开盘→固定前缀确认根卖出第一腿（成交价用 close）
        if sold_qty <= 0 and qty > 0 and not pm_hit:
            gated = leg1_gate_at is not None
            if gated:
                if not leg1_gate_at(idx):
                    continue
                if close <= 0:
                    continue
                fill_sell = float(close)
            else:
                if hi < sell_level:
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
                note="反T卖出（固定前缀后半下跌确认）",
            )
            shares_now -= qty
            sold_qty = qty
            sold_price = fill_sell
            touch_sell_at = ts
            leg1_idx = idx
            if stop_pct > 0 and sold_price > 0:
                stop_level = sold_price * (1.0 + stop_pct / 100.0)
            # 同根不明先后：确认卖后不在同一根买回
            continue

        if sold_qty > 0 and covered <= 0:
            # 涨破止损（先于买回触发/追价）：延迟 arm_bars 根；默认收盘确认
            if (
                stop_pct > 0
                and stop_level is not None
                and leg1_idx is not None
                and (idx - leg1_idx) > stop_arm_bars
            ):
                hit_stop = (
                    close > 0 and close >= float(stop_level) - 1e-12
                    if stop_on_close
                    else hi >= float(stop_level) - 1e-12
                )
                if hit_stop:
                    fill_stop = (
                        float(close) if stop_on_close else max(float(hi), float(stop_level))
                    )
                    if fill_stop <= 0:
                        fill_stop = float(stop_level)
                    cover = sold_qty
                    # 止损强制买回（可动用账户现金；不要求卖出净得自给）
                    cash_delta += append_t0_leg(
                        trades,
                        cost_model=cost_model,
                        cost_params=cost_params,
                        side="t0_buy",
                        stock_code=stock_code,
                        shares=cover,
                        price=fill_stop,
                        trigger=float(stop_level),
                        at=ts,
                        leg_kind="stop",
                        note=(
                            f"反T涨破止损买回（{stop_pct:.2f}%·"
                            f"{'收盘确认' if stop_on_close else '触价'}·"
                            f"延迟{stop_arm_bars}根）"
                        ),
                    )
                    shares_now += cover
                    covered = cover
                    touch_cover_at = ts
                    exit_reason = "stop_loss"
                    continue

            # 第二腿：相对 leg1 成交价的回撤幅度（与配置 buy_trigger 语义一致）
            base_buy = sold_price * (1.0 - buy_trig / 100.0)
            if chase_buy_level is None:
                chase_buy_level = base_buy
            buy_level = float(chase_buy_level)
            used_chase = last_chase_min is not None
            # 先按当前目标触价；不可成再追价，同根用新目标再试（避免未触就抬价）
            if lo > buy_level:
                px = float(mb.get("close") or hi or sold_price)
                chase_buy_level, last_chase_min, adjusted = _maybe_pm_chase_level(
                    level=float(chase_buy_level),
                    px=px,
                    ts=ts,
                    pm_hm=pm_hm,
                    last_chase_min=last_chase_min,
                    interval_min=pm_chase_iv,
                )
                # must_cover：追价不得高于卖出价（盘中主动追高应交止损/eod）
                if cfg.get("must_cover_same_day") and sold_price > 0:
                    ceil_px = float(sold_price)
                    if stop_level is not None and stop_pct > 0:
                        ceil_px = min(ceil_px, float(stop_level))
                    chase_buy_level = min(float(chase_buy_level), ceil_px)
                buy_level = float(chase_buy_level)
                used_chase = last_chase_min is not None
                if not adjusted or lo > buy_level:
                    continue
            fill_buy = _fill_buy(lo, buy_level, fill_mode)
            cover = sold_qty
            # 与 EOD 一致：卖出净得须覆盖买回净现金；追价抬高后不够则本根不成交
            if not _buy_self_funded(
                cash_delta,
                shares=cover,
                price=fill_buy,
                cost_model=cost_model,
                cost_params=cost_params,
            ):
                continue
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
                    f"反T中点追价买回（目标{buy_level:.4f}）"
                    if used_chase
                    else "反T买回（分钟第一触达）"
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
        eod_ok = _allows_eod_cover(minute_bars, defer_eod=defer_eod)
        if cfg.get("must_cover_same_day") and eod_ok:
            cover = sold_qty
            # 卖出净得须覆盖买回净现金（含佣金/滑点）；不足则改记敞口
            if not _buy_self_funded(
                cash_delta,
                shares=cover,
                price=sess_close,
                cost_model=cost_model,
                cost_params=cost_params,
            ):
                exposure_pnl = round((sold_price - sess_close) * sold_qty, 2)
                exit_reason = "abandon_cover_cash"
            else:
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
                    note="反T强制收盘买回",
                )
                shares_now += cover
                covered = cover
                touch_cover_at = close_at or touch_cover_at
                exit_reason = "eod_cover"
        elif eod_ok or not defer_eod:
            exposure_pnl = round((sold_price - sess_close) * sold_qty, 2)
            exit_reason = "abandon_cover"
        elif defer_eod and not eod_ok:
            # 盘中前缀：已卖未买回，不记敞口估值（等后续 K / 收盘窗）
            exit_reason = T0_PENDING_EXIT

    if sold_qty <= 0:
        return _skip_result(
            reason="反T分钟路径未触及卖出价",
            shares=shares,
            bar=bar,
            extra={
                "direction_used": "sell_then_buy",
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
        "direction_used": "sell_then_buy",
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
        "note": "分钟第一触达（反T）",
    }


def _first_touch_buy_then_sell(
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
    """正 T 分钟路径：低吸加仓后卖旧底仓（T+1），不卖当日新买股。"""
    close = float(bar.get("close") or 0)
    sess_bars = session_bars if session_bars is not None else minute_bars
    sess_bar = session_bar if session_bar is not None else bar
    t0_ratio = float(cfg["t0_ratio"])
    # 固定前缀：第一腿按确认根 close；预估可买手数用开盘价（不再用相对开盘买触发）
    afford_px = float(ref) if float(ref) > 0 else 0.0
    buy_level = afford_px  # 返回字段占位；gated 成交价用确认根 close
    sellable = float(sellable_shares if sellable_shares is not None else shares)
    sellable = min(sellable, shares)
    if sellable < lot:
        return _skip_result(
            reason=_tplus1_skip_reason(
                side="buy_then_sell", shares=shares, sellable=sellable, lot=lot
            ),
            shares=shares,
            bar=bar,
            extra={
                "direction_used": "buy_then_sell",
                "path_mode": "first_touch",
                "sellable_shares": sellable,
            },
        )
    if cash <= 0:
        return _skip_result(
            reason="正T：缺现金（低吸加仓需要预留现金）",
            shares=shares,
            bar=bar,
            extra={"direction_used": "buy_then_sell", "path_mode": "first_touch"},
        )
    max_shares = _t0_qty_lots(shares, t0_ratio, lot, sellable)
    if max_shares <= 0:
        return _skip_result(
            reason=_ratio_lot_skip_reason(
                side="buy_then_sell", shares=shares, t0_ratio=t0_ratio, lot=lot
            ),
            shares=shares,
            bar=bar,
            extra={
                "direction_used": "buy_then_sell",
                "path_mode": "first_touch",
                "sellable_shares": sellable,
                "t0_ratio": t0_ratio,
            },
        )

    afford = _max_affordable_buy_lots(
        cash,
        afford_px if afford_px > 0 else 1.0,
        lot,
        cost_model=cost_model,
        cost_params=cost_params,
        cap=min(max_shares, _lot_floor(sellable, lot)),
    )
    qty = int(afford)
    if qty <= 0:
        gross_afford = _lot_floor(cash / max(afford_px, 1e-6), lot)
        afford_n = int(gross_afford)
        if afford_n < lot:
            reason = (
                f"正T：现金不够 1 手（现金 {cash:.0f} 约可买 {afford_n} 股 < {lot}；"
                f"目标 {int(max_shares)} 股）"
            )
        else:
            reason = "正T：买不起或可卖旧仓不足（含手续费）"
        return _skip_result(
            reason=reason,
            shares=shares,
            bar=bar,
            extra={
                "direction_used": "buy_then_sell",
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
    sell_old_qty = 0
    pm_hm = _parse_hm(cfg.get("t0_pm_degrade"))
    pm_chase_iv = _pm_chase_interval_min(cfg)
    chase_sell_level: Optional[float] = None
    last_chase_min: Optional[int] = None
    stop_pct, stop_arm_bars, stop_on_close = _t0_stop_params(cfg, "buy_then_sell")
    leg1_idx: Optional[int] = None
    stop_level: Optional[float] = None

    for idx, mb in enumerate(minute_bars):
        hi = float(mb.get("high") or 0)
        lo = float(mb.get("low") or 0)
        close = float(mb.get("close") or 0)
        ts = mb.get("datetime") or mb.get("date")
        pm_hit = bool(pm_hm and _hm_reached(ts, pm_hm, inclusive=False))

        # 开盘→固定前缀确认根买入第一腿（成交价用 close）
        if bought_qty <= 0 and not pm_hit:
            gated = leg1_gate_at is not None
            if gated:
                if not leg1_gate_at(idx):
                    continue
                if close <= 0:
                    continue
                fill_buy = float(close)
            else:
                # 无固定前缀门禁：相对开盘触价（buy_trigger_pct）；正常默认走确认根
                leg1_buy_level = ref * (1.0 - buy_trig / 100.0)
                if lo > leg1_buy_level:
                    continue
                if fill_mode == "optimistic":
                    fill_buy = lo
                elif fill_mode == "mid":
                    fill_buy = (lo + leg1_buy_level) / 2.0
                else:
                    fill_buy = leg1_buy_level
                buy_level = leg1_buy_level
            # 含费再缩量，避免 append 后现金转负
            qty = _max_affordable_buy_lots(
                cash,
                fill_buy,
                lot,
                cost_model=cost_model,
                cost_params=cost_params,
                cap=min(qty, sell_old_cap),
            )
            if qty <= 0:
                continue
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
                note="正T加仓（固定前缀后半上涨占比确认；新股T+1锁仓）",
            )
            shares_now += qty
            bought_qty = qty
            buy_price = fill_buy
            touch_buy_at = ts
            sell_old_qty = min(bought_qty, sell_old_cap)
            # 第二腿：相对第一腿成交价上浮 sell_trigger%（非 ref）
            chase_sell_level = buy_price * (1.0 + sell_trig / 100.0)
            leg1_idx = idx
            if stop_pct > 0 and buy_price > 0:
                stop_level = buy_price * (1.0 - stop_pct / 100.0)
            # 同根不明先后：确认买后不在同一根卖旧仓
            continue

        if bought_qty > 0 and sold_back <= 0:
            # 跌破止损（先于卖触发/追价）：延迟 arm_bars 根；默认收盘确认
            if (
                stop_pct > 0
                and stop_level is not None
                and sell_old_qty > 0
                and leg1_idx is not None
                and (idx - leg1_idx) > stop_arm_bars
            ):
                hit_stop = (
                    close > 0 and close <= float(stop_level) + 1e-12
                    if stop_on_close
                    else lo <= float(stop_level) + 1e-12
                )
                if hit_stop:
                    fill_stop = float(close) if stop_on_close else min(float(lo), float(stop_level))
                    if fill_stop <= 0:
                        fill_stop = float(stop_level)
                    cash_delta += append_t0_leg(
                        trades,
                        cost_model=cost_model,
                        cost_params=cost_params,
                        side="t0_sell",
                        stock_code=stock_code,
                        shares=sell_old_qty,
                        price=fill_stop,
                        trigger=float(stop_level),
                        at=ts,
                        leg_kind="stop",
                        note=(
                            f"正T跌破止损（{stop_pct:.2f}%·"
                            f"{'收盘确认' if stop_on_close else '触价'}·"
                            f"延迟{stop_arm_bars}根）"
                        ),
                    )
                    shares_now -= sell_old_qty
                    sold_back = sell_old_qty
                    touch_sell_at = ts
                    exit_reason = "stop_loss"
                    continue

            base_sell = buy_price * (1.0 + sell_trig / 100.0)
            if chase_sell_level is None:
                chase_sell_level = base_sell
            sell_level = float(chase_sell_level)
            used_chase = last_chase_min is not None
            # 先按当前目标触价；不可成再追价，同根用新目标再试（避免未触就压价）
            if sell_old_qty <= 0 or hi < sell_level:
                px = float(mb.get("close") or lo or buy_price)
                chase_sell_level, last_chase_min, adjusted = _maybe_pm_chase_level(
                    level=float(chase_sell_level),
                    px=px,
                    ts=ts,
                    pm_hm=pm_hm,
                    last_chase_min=last_chase_min,
                    interval_min=pm_chase_iv,
                )
                # must_cover：追价不得低于成本（盘中主动割肉应交止损/eod）
                if cfg.get("must_cover_same_day") and buy_price > 0:
                    floor_px = float(buy_price)
                    if stop_level is not None and stop_pct > 0:
                        floor_px = max(floor_px, float(stop_level))
                    chase_sell_level = max(float(chase_sell_level), floor_px)
                sell_level = float(chase_sell_level)
                used_chase = last_chase_min is not None
                if not adjusted or sell_old_qty <= 0 or hi < sell_level:
                    continue
            fill_sell = _fill_sell(hi, sell_level, fill_mode)
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
                    f"正T中点追价卖旧仓（目标{sell_level:.4f}）"
                    if used_chase
                    else "正T卖旧底仓（分钟第一触达；T+1可卖）"
                ),
            )
            shares_now -= sell_old_qty
            sold_back = sell_old_qty
            touch_sell_at = ts
            exit_reason = "pm_chase" if used_chase else "trigger"
            continue

    if bought_qty <= 0:
        return _skip_result(
            reason="正T分钟路径未开成第一腿",
            shares=shares,
            bar=bar,
            extra={
                "direction_used": "buy_then_sell",
                "path_mode": "first_touch",
                "buy_level": round(buy_level, 4),
                "range_pct": round(range_pct, 4),
            },
        )

    at_end = len(minute_bars) >= len(sess_bars)
    if sold_back <= 0 and at_end:
        # sell_old_qty 已在 leg1 按入口 sell_old_cap 锁定；勿再读外部可卖
        sess_close = float(sess_bar.get("close") or close)
        eod_ok = _allows_eod_cover(minute_bars, defer_eod=defer_eod)
        if cfg.get("must_cover_same_day") and sell_old_qty > 0 and eod_ok:
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
        elif (eod_ok or not defer_eod) and not cfg.get("must_cover_same_day"):
            # 与反T对称：仅在收盘窗就绪（或非 defer）时记敞口；盘中前缀继续等
            exposure_pnl = round((sess_close - buy_price) * bought_qty, 2)
            exit_reason = exit_reason or "abandon_cover"
        elif eod_ok and cfg.get("must_cover_same_day") and bought_qty > 0 and sell_old_qty <= 0:
            # 防御：must_cover 但可卖旧仓为 0，无法卖回 → 记多头敞口
            exposure_pnl = round((sess_close - buy_price) * bought_qty, 2)
            exit_reason = "abandon_cover_cap"
        elif defer_eod and not eod_ok and bought_qty > 0:
            # 盘中前缀：已买未卖旧，不记敞口估值（等后续 K / 收盘窗）
            exit_reason = T0_PENDING_EXIT

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
        "uncovered_qty": max(0, int(bought_qty) - int(sold_back)),
        "bought_qty": bought_qty,
        "sold_back_qty": sold_back,
        "trades": trades,
        "pnl": pnl,
        "exposure_pnl": exposure_pnl,
        "fees_total": t0_fees_total(trades),
        "cost_model": cost_model,
        "shares_end": shares_now,
        "cash_delta": round(cash_delta, 2),
        "direction_used": "buy_then_sell",
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
        "note": "分钟第一触达（正T·T+1换仓）",
    }


def _touch_path_complete(out: dict, direction: str) -> bool:
    """第二腿 intraday 完成，或 session 末 eod / 放弃回补敞口已入账。"""
    exit_reason = str(out.get("exit_reason") or "")
    # 盘中前缀未到收盘窗：显式未完成（且不得靠 exposure 误判完成）
    if exit_reason == T0_PENDING_EXIT:
        return False
    if direction == "sell_then_buy":
        sold = int(out.get("sold_qty") or 0)
        covered = int(out.get("covered_qty") or 0)
        if sold <= 0:
            return False
        if covered >= sold:
            return True
        # 反T放弃买回（减仓落袋）或未回补敞口已标记
        if exit_reason in T0_INTENTIONAL_ABANDON_EXITS or exit_reason == "eod_cover":
            return True
        return abs(float(out.get("exposure_pnl") or 0)) > 1e-9
    if direction == "buy_then_sell":
        bought = int(out.get("bought_qty") or 0)
        sold_back = int(out.get("sold_back_qty") or 0)
        if bought <= 0:
            return False
        if sold_back >= bought:
            return True
        if exit_reason in T0_INTENTIONAL_ABANDON_EXITS or exit_reason == "eod_cover":
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
    gate_open: bool = False,
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
    if direction == "buy_then_sell" and buy_level is not None:
        touch_leg1 = lo <= float(buy_level)
    elif direction == "sell_then_buy" and sell_level is not None:
        touch_leg1 = hi >= float(sell_level)
    # 粘滞开闸已废弃：仅固定前缀确认根可成交；触价但未确认时标 blocked（诊断用）
    if touch_leg1 and not (entry_ready or gate_open):
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
    leg1_side = "t0_buy" if direction == "buy_then_sell" else "t0_sell"
    leg2_side = "t0_sell" if direction == "buy_then_sell" else "t0_buy"
    for t in trades:
        side = str(t.get("side") or "")
        at = str(t.get("at") or "")
        for row in rows:
            if at and at == str(row.get("datetime") or ""):
                if side == leg1_side:
                    row["leg1_fill"] = True
                    row["touch_blocked"] = False
                elif side == leg2_side:
                    row["leg2_fill"] = True
                break
            if at and str(row.get("time") or "") and at[11:16] == str(row.get("time") or ""):
                if side == leg1_side:
                    row["leg1_fill"] = True
                    row["touch_blocked"] = False
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
        pm_hit = bool(pm_hm and _hm_reached(ts, pm_hm, inclusive=False))
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
        if dir_used in {"sell_then_buy", "buy_then_sell"}
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
    """前缀第一腿闸：正/反T 均固定前缀 + 后半阳/阴占比（一次判定，不滚动）。

    Returns (plan, early_exit, dir_res_for_finish).
    plan 含 leg1_gate_at（仅 entry_ready 当根为 True）/ direction / cfg_side / triggers / ref 等。
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

        # 正/反T：固定前缀 N 根（默认=abandon_bars）+ 后半段阳/阴占比；不贪心探极值回落/反弹
        fixed_n, _ratio_thr = _prefix_bar_ratio_params(cfg_side, direction)
        side_label = "正T" if direction == "buy_then_sell" else "反T"
        seg_enabled_key = (
            "y_prefix_segment_enabled_buy_then_sell"
            if direction == "buy_then_sell"
            else "y_prefix_segment_enabled_sell_then_buy"
        )
        dir_amp = {
            "ok": True,
            "reason": f"{side_label}固定前缀：跳过探极值振幅",
            "direction": direction,
        }
        if n < fixed_n:
            seg = {
                "ok": False,
                "segment": "fixed_prefix_wait",
                "fixed_bars": fixed_n,
                "prefix_bars": n,
                "reason": f"{side_label}待固定前缀 {n}/{fixed_n}",
            }
            entry_ready = False
            wait_reason = str(seg["reason"])
            # 未齐窗不烧 abandon；齐窗当根再判定
        elif j == fixed_n - 1:
            fixed_prefix = mins[:fixed_n]
            gate_fixed = prefix_range_gate(fixed_prefix, bar, cost=cost, cfg=cfg_side)
            seg = prefix_fixed_bar_ratio_entry_ok(
                fixed_prefix, direction=direction, cfg=cfg_side
            )
            if not gate_fixed.get("ok"):
                seg = {
                    **seg,
                    "ok": False,
                    "range_gate": gate_fixed,
                    "reason": (
                        f"振幅不足 {float(gate_fixed.get('range_pct') or 0):.2f}% "
                        f"< {float(gate_fixed.get('min_range_pct') or 0):.2f}%"
                        if not gate_fixed.get("flat")
                        else "一字板/无波动"
                    ),
                }
            elif not bool(cfg_side.get(seg_enabled_key, True)):
                seg = {
                    "ok": True,
                    "segment": "fixed_prefix_off",
                    "fixed_bars": fixed_n,
                    "reason": f"{side_label}固定前缀确认关",
                }
            entry_ready = bool(seg.get("ok"))
            # 确认根成交价：相对开盘偏离不得超过 |ŷ_τ|×倍数（默认已关 mult=0）
            if entry_ready:
                fill_px = float(m.get("close") or 0)
                tau_px = tau_leg1_fill_price_ok(
                    fill_px=fill_px,
                    ref=float(ref),
                    y_tau=_score_y_tau(score_snap),
                    direction=direction,
                    cfg=cfg_side,
                )
                seg = {**seg, "tau_entry_price": tau_px}
                if not bool(tau_px.get("ok")):
                    entry_ready = False
                    wait_reason = str(tau_px.get("reason") or f"{side_label}τ入场价未过")
                    seg = {
                        **seg,
                        "ok": False,
                        "reason": wait_reason,
                    }
            # 前缀 (H−L)/ref% 已超过 |ŷ_path| → 当日不做
            if entry_ready:
                path_rng = prefix_range_vs_path_ok(
                    range_pct=gate_fixed.get("range_pct")
                    if isinstance(gate_fixed, dict)
                    else range_pct_side,
                    y_path=_score_y_path(score_snap),
                    cfg=cfg_side,
                )
                seg = {**seg, "prefix_vs_path": path_rng}
                if not bool(path_rng.get("ok")):
                    entry_ready = False
                    wait_reason = str(path_rng.get("reason") or f"{side_label}前缀振幅>|ŷ_path|")
                    seg = {
                        **seg,
                        "ok": False,
                        "reason": wait_reason,
                    }
            entry_flags[j] = entry_ready
            wait_reason = (
                None if entry_ready else str(seg.get("reason") or f"{side_label}固定前缀未过")
            )
        else:
            # 决策窗已过：不再滚动重评
            seg = {
                "ok": False,
                "segment": "fixed_prefix_passed",
                "fixed_bars": fixed_n,
                "reason": f"{side_label}固定前缀决策已过",
            }
            entry_ready = False
            wait_reason = None

        if j == fixed_n - 1 and not entry_ready:
            # 齐窗未过 → 立即放弃（不再滚动）
            abandon_on = bool(cfg_day.get("y_path_abandon_enabled", True))
            wait_reason = wait_reason or str(seg.get("reason") or f"{side_label}固定前缀未过")
            last_dir_wait = _skip_result(
                reason=wait_reason,
                shares=shares,
                bar=bar_n,
                extra={
                    "direction_used": direction,
                    "path_mode": "first_touch",
                    "range_mode": "fixed_prefix",
                    "prefix_bars": n,
                    "range_pct": range_pct_side,
                    "directional_amplitude": dir_amp,
                    "prefix_segment": seg,
                },
            )
            if abandon_on:
                early = _skip_result(
                    reason=f"固定前缀未确认，放弃{side_label}（{wait_reason}）",
                    shares=shares,
                    bar=bar_n,
                    extra={
                        "direction_used": direction,
                        "path_mode": "first_touch",
                        "range_mode": "fixed_prefix",
                        "prefix_bars": n,
                        "range_pct": range_pct_side,
                        "directional_amplitude": dir_amp,
                        "prefix_segment": seg,
                        "path_abandon": True,
                        "forward_trace": trace_rows
                        + [
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
                                dir_amp_ok=True,
                                seg_ok=False,
                                entry_ready=False,
                                gate_open=False,
                                ref=float(ref),
                                sell_trig=sell_trig,
                                buy_trig=buy_trig,
                                wait_reason=wait_reason,
                            )
                        ],
                    },
                )
                return None, early, dir_res_locked or dir_res

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
                gate_open=entry_ready,
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

        if n < fixed_n:
            last_dir_wait = _skip_result(
                reason=wait_reason or f"{side_label}待固定前缀 {n}/{fixed_n}",
                shares=shares,
                bar=bar_n,
                extra={
                    "direction_used": direction,
                    "path_mode": "first_touch",
                    "range_mode": "fixed_prefix",
                    "prefix_bars": n,
                    "range_pct": range_pct_side,
                    "prefix_segment": seg,
                },
            )
            continue

    if plan_tail is None:
        if last_amp_skip is not None:
            if isinstance(last_amp_skip, dict):
                last_amp_skip = dict(last_amp_skip)
                last_amp_skip["forward_trace"] = trace_rows
            return None, last_amp_skip, None
        return None, None, None

    def leg1_gate_at(idx: int) -> bool:
        """仅确认根可开第一腿。

        正T：固定前缀齐且后半上涨占比达标当根买；反T：固定前缀齐且后半下跌占比达标当根卖。
        """
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
    """前向固定前缀第一触达：开盘→振幅→齐窗阴阳占比（+τ入场价）确认根成交第一腿。

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

    if direction == "buy_then_sell":
        out = _first_touch_buy_then_sell(
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
        out = _first_touch_sell_then_buy(
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
        out["range_mode"] = "fixed_prefix"
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
    defer_eod: bool = False,
) -> Dict[str, Any]:
    """单日做 T：选向仍用开盘/隔夜特征；成交路径按分钟第一触达（固定前缀）。

    振幅门禁 + 固定前缀阴阳占比（+τ入场价）齐窗确认根成交第一腿；第二腿相对第一腿价。
    ``defer_eod=True`` 时盘中前缀不强平（纸面整单/Worker）。
    """
    from core.t0.score_policy import (
        attach_day_scores,
        resolve_cover_policy,
        resolve_fuse_intraday,
        resolve_score_as_of,
        resolve_scores_for_code,
        resolve_y_score_source,
        scores_have_any,
        tau_pool_day_score_kwargs,
    )

    cfg = load_t0_rules(rules)
    cost_model, cost_params = resolve_t0_cost_context(paper=paper, cost_config=cost_config)
    if not cfg.get("enabled", True):
        return _skip_result(reason="t0 disabled", shares=shares, bar=bar)

    score_snap = scores if isinstance(scores, dict) else None
    if str(cfg.get("direction") or "") == "dual_y" and not scores_have_any(score_snap):
        as_of = resolve_score_as_of(
            hist_bars=hist_bars,
            day_bar=bar if isinstance(bar, dict) else None,
        )
        if stock_code:
            score_snap = resolve_scores_for_code(
                stock_code,
                hist_bars=hist_bars,
                day_bar=bar if isinstance(bar, dict) else None,
                as_of=as_of or None,
                source=resolve_y_score_source(cfg),
                fuse_intraday=resolve_fuse_intraday(cfg),
                allow_fallback=(resolve_y_score_source(cfg) != "compute"),
                minute_bars=minute_bars,
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
    # 信心缩放只在 _side_trigger_pack 做一次（避免与侧向键叠加双重缩放）
    trigger_scale_meta: Dict[str, float] = {
        "sell_trigger_pct_base": round(sell_trig, 4),
        "buy_trigger_pct_base": round(buy_trig, 4),
        "trigger_scale": 1.0,
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
        ml = cfg_day.get("min_range_pct_sell_then_buy")
        mr = cfg_day.get("min_range_pct_buy_then_sell")
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
        defer_eod=bool(defer_eod),
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
