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
    ``inclusive=False``：端点不计（午后禁新开 ``t0_pm_degrade=14:00`` 时 14:00 根仍可开）。
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


def _session_minutes_complete(minute_bars: Sequence[dict]) -> bool:
    """末根是否已到收盘窗（≥14:55）。"""
    if not minute_bars:
        return False
    last = minute_bars[-1]
    ts = last.get("datetime") or last.get("date")
    return _hm_reached(ts, (14, 55))



def _allows_eod_cover(
    minute_bars: Sequence[dict],
    *,
    defer_eod: bool,
) -> bool:
    """强制回补仅当分钟已到收盘窗，避免午前截断用日线收盘前视。

    ``defer_eod`` 保留给调用方区分 pending vs incomplete_session；
    是否允许 eod_cover 一律看末根 ≥14:55。
    """
    _ = defer_eod
    return _session_minutes_complete(minute_bars)


def _ts_minutes(ts: Any) -> Optional[int]:
    """当日分钟数（0–1439）；解析失败返回 None。"""
    hm = _ts_hm(ts)
    if not hm:
        return None
    return int(hm[0]) * 60 + int(hm[1])


def _pm_chase_interval_min(cfg: dict) -> int:
    try:
        n = int(cfg.get("t0_pm_chase_interval_min") or 5)
    except (TypeError, ValueError):
        n = 5
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


def _account_can_buy(
    cash_now: float,
    *,
    shares: float,
    price: float,
    cost_model: str,
    cost_params: dict,
) -> bool:
    """账户现金是否够买回（含手续费）；可用账户余额，不要求卖出净得自给。"""
    if shares <= 0 or price <= 0:
        return False
    probe = apply_t0_leg_costs(
        {"side": "t0_buy", "shares": shares, "price": price},
        cost_model=cost_model,
        cost_params=cost_params,
    )
    return float(cash_now or 0) + float(probe.get("net_cash_delta") or 0) >= -1e-6


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
    """前向前缀振幅度量：合成 high/low/ref 与 range_pct（供诊断 / vs|ŷ_path|）。

    **振幅下限门禁已下线**（Web 无配置）；有效 bar 即 ``ok=True``，不再因
    ``min_range_pct`` / 一字板否决。
    """
    min_range = 0.0
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
    return {
        "ok": True,
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


def _tau_price_gate_prefix(leg: str) -> str:
    return "y_tau_entry_price" if str(leg or "").strip().lower() == "entry" else "y_tau_exit_price"


def _tau_price_gate_side_keys(*, leg: str, direction: str) -> tuple[str, str, str, str, str]:
    """skip / mult / bias / move_min / move_max 分侧键。"""
    direction = str(direction or "").strip().lower()
    prefix = _tau_price_gate_prefix(leg)
    if direction == "buy_then_sell":
        return (
            f"{prefix}_skip_buy_then_sell",
            f"{prefix}_mult_buy_then_sell",
            f"{prefix}_bias_buy_then_sell",
            f"{prefix}_move_min_buy_then_sell",
            f"{prefix}_move_max_buy_then_sell",
        )
    if direction == "sell_then_buy":
        return (
            f"{prefix}_skip_sell_then_buy",
            f"{prefix}_mult_sell_then_buy",
            f"{prefix}_bias_sell_then_buy",
            f"{prefix}_move_min_sell_then_buy",
            f"{prefix}_move_max_sell_then_buy",
        )
    return (
        f"{prefix}_skip",
        f"{prefix}_mult",
        f"{prefix}_bias",
        f"{prefix}_move_min",
        f"{prefix}_move_max",
    )


def _tau_entry_price_side_keys(direction: str) -> tuple[str, str]:
    skip_key, mult_key, _bias_key, _min_key, _max_key = _tau_price_gate_side_keys(
        leg="entry", direction=direction
    )
    return skip_key, mult_key


def _tau_entry_price_mult(cfg: Optional[dict], direction: str = "") -> float:
    """τ 入场价裕度：bound = open×(1 + ŷ_τ%×mult/100)；默认 1.0；正/反T 分侧。"""
    _skip_key, mult_key = _tau_entry_price_side_keys(direction)
    raw = (cfg or {}).get(mult_key)
    if raw is None or raw == "":
        raw = (cfg or {}).get("y_tau_entry_price_mult")
    try:
        m = float(1.0 if raw is None or raw == "" else raw)
    except (TypeError, ValueError):
        m = 1.0
    return max(0.5, min(m, 5.0))


def _tau_entry_price_skip(cfg: Optional[dict], direction: str = "") -> bool:
    from core.t0.config import coerce_cfg_bool

    skip_key, _mult_key = _tau_entry_price_side_keys(direction)
    raw = (cfg or {}).get(skip_key)
    if raw is None:
        raw = (cfg or {}).get("y_tau_entry_price_skip")
    return coerce_cfg_bool(raw, True)


def tau_leg1_fill_price_ok(
    *,
    fill_px: float,
    ref: float,
    y_tau: Optional[float],
    direction: str,
    cfg: Optional[dict] = None,
    mult: Optional[float] = None,
) -> Dict[str, Any]:
    """确认根第一腿：有符号 ŷ_τ 定相对开盘边界。

    - 正T：买价 < open×(1 + ŷ_τ×裕度)（ŷ_τ 多为正 → 天花板）
    - 反T：卖价 > open×(1 + ŷ_τ×裕度)（ŷ_τ 多为负 → 地板）
    正T ``y_tau_entry_price_mult_buy_then_sell`` / 反T ``…_sell_then_buy`` 默认 1。
    关对应侧 skip 或显式 ``mult≤0`` 时放行。缺 ŷ_τ / 无效价放行。
    """
    direction = str(direction or "").strip().lower()
    enabled = _tau_entry_price_skip(cfg, direction)
    if mult is not None:
        try:
            m = float(mult)
        except (TypeError, ValueError):
            m = 0.0
    else:
        m = _tau_entry_price_mult(cfg, direction) if enabled else 0.0
    base: Dict[str, Any] = {
        "direction": direction,
        "enabled": enabled,
        "mult": round(m, 4),
        "y_tau": None if y_tau is None else round(float(y_tau), 4),
        "ref": round(float(ref), 4) if ref else None,
        "fill_px": round(float(fill_px), 4) if fill_px else None,
    }
    if (not enabled) or m <= 0:
        return {**base, "ok": True, "skipped": True, "reason": "τ入场价闸关"}
    if ref is None or float(ref) <= 0 or fill_px is None or float(fill_px) <= 0:
        return {**base, "ok": True, "skipped": True, "reason": "τ入场价门禁无效价"}
    if y_tau is None:
        from core.t0.config import coerce_cfg_bool

        require = coerce_cfg_bool((cfg or {}).get("y_tau_require_for_leg1"), True)
        if enabled and require:
            return {
                **base,
                "ok": False,
                "skipped": False,
                "reason": "缺ŷ_τ，不开第一腿",
            }
        return {**base, "ok": True, "skipped": True, "reason": "缺ŷ_τ，跳过入场价门禁"}
    bias = _tau_price_gate_bias(cfg, leg="entry", direction=direction)
    move_min, move_max = _tau_price_gate_move_bounds(cfg, leg="entry", direction=direction)
    move_pct = _tau_price_move_pct(
        y_tau=float(y_tau),
        mult=m,
        move_min=move_min,
        move_max=move_max,
        bias=bias,
    )
    base["move_pct"] = round(move_pct, 4)
    base["price_bias"] = round(bias, 4)
    base["move_min"] = round(move_min, 4)
    base["move_max"] = round(move_max, 4)
    px = float(fill_px)
    o = float(ref)
    bound = _tau_price_bound_px(ref=o, move_pct=move_pct)
    base["bound_px"] = round(bound, 4)
    formula = _tau_price_formula_label(
        mult=m,
        y_tau=float(y_tau),
        move_pct=move_pct,
        move_min=move_min,
        move_max=move_max,
        bias=bias,
    )
    if direction == "buy_then_sell":
        base["bound_kind"] = "ceil"
        ok = px < bound + 1e-9
        return {
            **base,
            "ok": ok,
            "skipped": False,
            "reason": (
                f"正T买价<{formula}：{px:.3f}<{bound:.3f}"
                if ok
                else f"正T买价未低于τ带 {px:.3f}≥{bound:.3f}（{formula}）"
            ),
        }
    if direction == "sell_then_buy":
        base["bound_kind"] = "floor"
        ok = px > bound - 1e-9
        return {
            **base,
            "ok": ok,
            "skipped": False,
            "reason": (
                f"反T卖价>{formula}：{px:.3f}>{bound:.3f}"
                if ok
                else f"反T卖价未高于τ带 {px:.3f}≤{bound:.3f}（{formula}）"
            ),
        }
    return {**base, "ok": True, "skipped": True, "reason": "非正/反T，跳过入场价门禁"}


def _tau_exit_price_side_keys(direction: str) -> tuple[str, str]:
    skip_key, mult_key, _bias_key, _min_key, _max_key = _tau_price_gate_side_keys(
        leg="exit", direction=direction
    )
    return skip_key, mult_key


def _tau_price_gate_float(
    cfg: Optional[dict],
    key: str,
    *,
    legacy_key: str,
    default: float,
    lo: float,
    hi: float,
) -> float:
    raw = (cfg or {}).get(key)
    if raw is None or raw == "":
        raw = (cfg or {}).get(legacy_key)
    try:
        val = float(default if raw is None or raw == "" else raw)
    except (TypeError, ValueError):
        val = float(default)
    return max(lo, min(val, hi))


def _tau_price_gate_bias(cfg: Optional[dict], *, leg: str, direction: str) -> float:
    """价偏百分点（代数可正可负，直接加在 clamp 后）。"""
    _skip, _mult, bias_key, _min_key, _max_key = _tau_price_gate_side_keys(
        leg=leg, direction=direction
    )
    prefix = _tau_price_gate_prefix(leg)
    if direction == "sell_then_buy":
        default = -1.0 if leg == "exit" else -0.5
    elif direction == "buy_then_sell":
        default = 1.0 if leg == "exit" else 0.5
    else:
        default = 0.0
    return _tau_price_gate_float(
        cfg,
        bias_key,
        legacy_key=f"{prefix}_bias",
        default=default,
        lo=-50.0,
        hi=50.0,
    )


def _tau_price_gate_move_bounds(
    cfg: Optional[dict], *, leg: str, direction: str
) -> tuple[float, float]:
    _skip, _mult, _bias_key, min_key, max_key = _tau_price_gate_side_keys(
        leg=leg, direction=direction
    )
    prefix = _tau_price_gate_prefix(leg)
    move_min = _tau_price_gate_float(
        cfg,
        min_key,
        legacy_key=f"{prefix}_move_min",
        default=-100.0,
        lo=-100.0,
        hi=100.0,
    )
    move_max = _tau_price_gate_float(
        cfg,
        max_key,
        legacy_key=f"{prefix}_move_max",
        default=100.0,
        lo=-100.0,
        hi=100.0,
    )
    if move_min > move_max:
        move_min, move_max = move_max, move_min
    return move_min, move_max


def _tau_price_move_pct(
    *,
    y_tau: float,
    mult: float,
    move_min: float,
    move_max: float,
    bias: float,
) -> float:
    raw = float(y_tau) * float(mult)
    lo = min(float(move_min), float(move_max))
    hi = max(float(move_min), float(move_max))
    return max(lo, min(raw, hi)) + float(bias)


def _tau_price_bound_px(*, ref: float, move_pct: float) -> float:
    return float(ref) * (1.0 + float(move_pct) / 100.0)


def _tau_price_formula_label(
    *,
    mult: float,
    y_tau: float,
    move_pct: float,
    move_min: float,
    move_max: float,
    bias: float,
) -> str:
    if (
        abs(float(bias)) < 1e-12
        and abs(float(move_min) + 100.0) < 1e-9
        and abs(float(move_max) - 100.0) < 1e-9
        and abs(float(move_pct) - float(y_tau) * float(mult)) < 1e-9
    ):
        return f"开盘×(1+{mult:g}×ŷ_τ={float(y_tau):.3f}%)"
    return (
        f"开盘×(1+(clamp({mult:g}×ŷ_τ,{move_min:g},{move_max:g})"
        f"{f'+{bias:g}' if bias >= 0 else f'{bias:g}'})"
        f"={move_pct:.3f}%)"
    )


def _tau_exit_price_mult(cfg: Optional[dict], direction: str = "") -> float:
    """第二腿 τ 出场裕度：bound = open×(1 + ŷ_τ%×mult/100)；正/反T 分侧。"""
    _skip_key, mult_key = _tau_exit_price_side_keys(direction)
    raw = (cfg or {}).get(mult_key)
    if raw is None or raw == "":
        raw = (cfg or {}).get("y_tau_exit_price_mult")
    try:
        m = float(1.0 if raw is None or raw == "" else raw)
    except (TypeError, ValueError):
        m = 1.0
    return max(0.5, min(m, 5.0))


def _tau_exit_price_skip(cfg: Optional[dict], direction: str = "") -> bool:
    from core.t0.config import coerce_cfg_bool

    skip_key, _mult_key = _tau_exit_price_side_keys(direction)
    raw = (cfg or {}).get(skip_key)
    if raw is None:
        raw = (cfg or {}).get("y_tau_exit_price_skip")
    return coerce_cfg_bool(raw, True)


def tau_exit_bound_px(
    *,
    ref: float,
    y_tau: Optional[float],
    direction: str,
    cfg: Optional[dict] = None,
) -> Optional[float]:
    """第二腿 bound=open×(1+ŷ_τ×裕度)；闸关 / 缺 ŷ_τ / 无效价 → None。"""
    direction = str(direction or "").strip().lower()
    if not _tau_exit_price_skip(cfg, direction):
        return None
    m = _tau_exit_price_mult(cfg, direction)
    if m <= 0 or ref is None or float(ref) <= 0 or y_tau is None:
        return None
    bias = _tau_price_gate_bias(cfg, leg="exit", direction=direction)
    move_min, move_max = _tau_price_gate_move_bounds(cfg, leg="exit", direction=direction)
    move_pct = _tau_price_move_pct(
        y_tau=float(y_tau),
        mult=m,
        move_min=move_min,
        move_max=move_max,
        bias=bias,
    )
    return _tau_price_bound_px(ref=float(ref), move_pct=move_pct)


def tau_leg2_fill_price_ok(
    *,
    fill_px: float,
    ref: float,
    y_tau: Optional[float],
    direction: str,
    cfg: Optional[dict] = None,
    mult: Optional[float] = None,
) -> Dict[str, Any]:
    """第二腿：正T 卖价 > bound；反T 买价 < bound（同 open×(1+ŷ_τ×裕度)）。"""
    direction = str(direction or "").strip().lower()
    enabled = _tau_exit_price_skip(cfg, direction)
    if mult is not None:
        try:
            m = float(mult)
        except (TypeError, ValueError):
            m = 0.0
    else:
        m = _tau_exit_price_mult(cfg, direction) if enabled else 0.0
    base: Dict[str, Any] = {
        "leg": 2,
        "direction": direction,
        "enabled": enabled,
        "mult": round(m, 4),
        "y_tau": None if y_tau is None else round(float(y_tau), 4),
        "ref": round(float(ref), 4) if ref else None,
        "fill_px": round(float(fill_px), 4) if fill_px else None,
    }
    if (not enabled) or m <= 0:
        return {**base, "ok": True, "skipped": True, "reason": "τ出场价闸关"}
    if ref is None or float(ref) <= 0 or fill_px is None or float(fill_px) <= 0:
        return {**base, "ok": True, "skipped": True, "reason": "τ出场价门禁无效价"}
    if y_tau is None:
        return {**base, "ok": True, "skipped": True, "reason": "缺ŷ_τ，跳过出场价门禁"}
    bias = _tau_price_gate_bias(cfg, leg="exit", direction=direction)
    move_min, move_max = _tau_price_gate_move_bounds(cfg, leg="exit", direction=direction)
    move_pct = _tau_price_move_pct(
        y_tau=float(y_tau),
        mult=m,
        move_min=move_min,
        move_max=move_max,
        bias=bias,
    )
    bound = _tau_price_bound_px(ref=float(ref), move_pct=move_pct)
    base["move_pct"] = round(move_pct, 4)
    base["price_bias"] = round(bias, 4)
    base["move_min"] = round(move_min, 4)
    base["move_max"] = round(move_max, 4)
    base["bound_px"] = round(bound, 4)
    formula = _tau_price_formula_label(
        mult=m,
        y_tau=float(y_tau),
        move_pct=move_pct,
        move_min=move_min,
        move_max=move_max,
        bias=bias,
    )
    px = float(fill_px)
    if direction == "buy_then_sell":
        base["bound_kind"] = "floor"
        ok = px > bound - 1e-9
        return {
            **base,
            "ok": ok,
            "skipped": False,
            "reason": (
                f"正T卖价>{formula}：{px:.3f}>{bound:.3f}"
                if ok
                else f"正T卖价未高于τ出场带 {px:.3f}≤{bound:.3f}（{formula}）"
            ),
        }
    if direction == "sell_then_buy":
        base["bound_kind"] = "ceil"
        ok = px < bound + 1e-9
        return {
            **base,
            "ok": ok,
            "skipped": False,
            "reason": (
                f"反T买价<{formula}：{px:.3f}<{bound:.3f}"
                if ok
                else f"反T买价未低于τ出场带 {px:.3f}≥{bound:.3f}（{formula}）"
            ),
        }
    return {**base, "ok": True, "skipped": True, "reason": "非正/反T，跳过出场价门禁"}


def tau_entry_bound_px(
    *,
    ref: float,
    y_tau: Optional[float],
    direction: str,
    cfg: Optional[dict] = None,
) -> Optional[float]:
    """第一腿 bound=open×(1+ŷ_τ×裕度)；闸关 / 缺 ŷ_τ → None。"""
    direction = str(direction or "").strip().lower()
    if not _tau_entry_price_skip(cfg, direction):
        return None
    m = _tau_entry_price_mult(cfg, direction)
    if m <= 0 or ref is None or float(ref) <= 0 or y_tau is None:
        return None
    bias = _tau_price_gate_bias(cfg, leg="entry", direction=direction)
    move_min, move_max = _tau_price_gate_move_bounds(cfg, leg="entry", direction=direction)
    move_pct = _tau_price_move_pct(
        y_tau=float(y_tau),
        mult=m,
        move_min=move_min,
        move_max=move_max,
        bias=bias,
    )
    return _tau_price_bound_px(ref=float(ref), move_pct=move_pct)


def _leg2_tau_target_px(
    *,
    ref: float,
    y_tau: Optional[float],
    direction: str,
    cfg: dict,
) -> Optional[float]:
    """第二腿目标价：仅 τ 出场 bound；闸关/缺 ŷ_τ → None（盘中不触价，等止损/追价/EOD）。"""
    return tau_exit_bound_px(ref=ref, y_tau=y_tau, direction=direction, cfg=cfg)


def prefix_range_vs_path_ok(
    *,
    range_pct: Optional[float],
    y_path: Optional[float],
    cfg: Optional[dict] = None,
) -> Dict[str, Any]:
    """历史兼容：前缀振幅 vs |ŷ_path| 已下线；恒放行（诊断可仍读旧 reason）。"""
    _ = cfg
    return {
        "enabled": False,
        "ok": True,
        "skipped": True,
        "range_pct": None if range_pct is None else round(float(range_pct), 4),
        "y_path": None if y_path is None else round(float(y_path), 4),
        "reason": "前缀vs|ŷ_path|已改τ入场价闸",
    }


def _leg2_breakeven_target_px(*, leg1_px: float, direction: str) -> Optional[float]:
    """缺 ŷ_τ 时第二腿回退目标：以 leg1 成交价为平盘线。"""
    if leg1_px is None or float(leg1_px) <= 0:
        return None
    return float(leg1_px)


def _stb_chase_buy_cap_px(
    *,
    sold_price: float,
    stop_level: Optional[float],
    stop_pct: float,
    cfg: Optional[dict],
) -> Optional[float]:
    """反 T 午后买回追价上限（默认 ≤ leg1 卖价，防结构性亏）。"""
    from core.t0.config import coerce_cfg_bool

    if sold_price <= 0:
        return None
    cap_on = coerce_cfg_bool((cfg or {}).get("t0_pm_chase_cap_leg1_sell_then_buy"), True)
    must = bool((cfg or {}).get("must_cover_same_day"))
    if not cap_on and not must:
        return None
    ceil_px = float(sold_price)
    if stop_level is not None and stop_pct > 0:
        ceil_px = min(ceil_px, float(stop_level))
    return ceil_px


def _bts_chase_sell_floor_px(
    *,
    buy_price: float,
    stop_level: Optional[float],
    stop_pct: float,
    cfg: Optional[dict],
) -> Optional[float]:
    """正 T 午后卖回追价下限（默认 ≥ leg1 买价）。"""
    from core.t0.config import coerce_cfg_bool

    if buy_price <= 0:
        return None
    floor_on = coerce_cfg_bool((cfg or {}).get("t0_pm_chase_cap_leg1_buy_then_sell"), True)
    must = bool((cfg or {}).get("must_cover_same_day"))
    if not floor_on and not must:
        return None
    floor_px = float(buy_price)
    if stop_level is not None and stop_pct > 0:
        floor_px = max(floor_px, float(stop_level))
    return floor_px


def _cfg_float(cfg: Optional[dict], key: str, default: float, *, lo: float, hi: float) -> float:
    try:
        raw = (cfg or {}).get(key)
        val = float(default if raw is None or raw == "" else raw)
    except (TypeError, ValueError):
        val = float(default)
    return max(lo, min(float(val), hi))


def _cfg_int(cfg: Optional[dict], key: str, default: int, *, lo: int, hi: int) -> int:
    try:
        raw = (cfg or {}).get(key)
        val = int(default if raw is None or raw == "" else raw)
    except (TypeError, ValueError):
        val = int(default)
    return max(lo, min(int(val), hi))


def prefix_env_gate_ok(
    *,
    range_pct: Optional[float],
    y_path: Optional[float],
    y_tau: Optional[float],
    cfg: Optional[dict],
) -> Dict[str, Any]:
    """环境闸：小波动 / 弱 path / 单边极端 → 整轮不做（v5 常开；单项阈值 0=关）。"""
    base: Dict[str, Any] = {"segment": "env_gate"}

    min_range = _cfg_float(cfg, "t0_env_min_range_pct", 0.5, lo=0.0, hi=30.0)
    min_path = _cfg_float(cfg, "t0_env_min_path_abs", 0.08, lo=0.0, hi=50.0)
    one_tau = _cfg_float(cfg, "t0_env_one_sided_tau_abs", 2.0, lo=0.0, hi=50.0)
    one_path = _cfg_float(cfg, "t0_env_one_sided_path_abs", 2.0, lo=0.0, hi=50.0)
    base.update(
        {
            "min_range_pct": min_range,
            "min_path_abs": min_path,
            "one_sided_tau_abs": one_tau,
            "one_sided_path_abs": one_path,
            "range_pct": None if range_pct is None else round(float(range_pct), 4),
            "y_path": None if y_path is None else round(float(y_path), 4),
            "y_tau": None if y_tau is None else round(float(y_tau), 4),
        }
    )
    if min_range > 1e-12 and range_pct is not None and float(range_pct) + 1e-12 < min_range:
        return {
            **base,
            "ok": False,
            "reason": f"环境闸：前缀振幅 {float(range_pct):.2f}%<{min_range:.2f}%",
        }
    if (
        min_path > 1e-12
        and y_path is not None
        and abs(float(y_path)) + 1e-12 < min_path
    ):
        return {
            **base,
            "ok": False,
            "reason": f"环境闸：|ŷ_path|={abs(float(y_path)):.3f}<{min_path:.3f}",
        }
    if (
        one_tau > 1e-12
        and one_path > 1e-12
        and y_tau is not None
        and y_path is not None
        and float(y_tau) * float(y_path) > 0
        and abs(float(y_tau)) + 1e-12 >= one_tau
        and abs(float(y_path)) + 1e-12 >= one_path
    ):
        return {
            **base,
            "ok": False,
            "reason": (
                f"环境闸：单边市 |ŷ_τ|={abs(float(y_tau)):.2f}≥{one_tau:.2f} "
                f"且 |ŷ_path|={abs(float(y_path)):.2f}≥{one_path:.2f}"
            ),
        }
    return {**base, "ok": True, "reason": "环境闸通过"}


def prefix_composite_entry_ok(
    minute_bars: Sequence[dict],
    *,
    direction: str,
    cfg: dict,
    ref: Optional[float] = None,
) -> Dict[str, Any]:
    """复合确认：开盘锚偏离 + 短窗动量翻转（可选量能放大）。

    正T：前缀曾下探 ≥dev%，确认根附近动量转多；反T 对称。
    """
    direction = str(direction or "").strip().lower()
    base: Dict[str, Any] = {"direction": direction, "segment": "composite"}
    if direction not in ("buy_then_sell", "sell_then_buy"):
        return {**base, "ok": True, "reason": "非正/反T，跳过复合确认"}

    want_buy = direction == "buy_then_sell"
    side = "正T" if want_buy else "反T"
    bars = [b for b in (minute_bars or []) if isinstance(b, dict)]
    fixed_n, _thr = _prefix_bar_ratio_params(cfg or {}, direction)
    n = min(len(bars), fixed_n) if fixed_n > 0 else len(bars)
    base["fixed_bars"] = fixed_n
    base["prefix_bars"] = n
    mom_n = _cfg_int(cfg, "t0_confirm_mom_bars", 2, lo=1, hi=12)
    min_n = max(2, mom_n)
    if n < min_n:
        return {
            **base,
            "ok": False,
            "reason": f"{side}复合确认：前缀不足 {n}/{min_n}",
        }
    win = bars[:n]
    try:
        open_px = float(ref or 0)
    except (TypeError, ValueError):
        open_px = 0.0
    if open_px <= 0:
        try:
            open_px = float(win[0].get("open") or 0)
        except (TypeError, ValueError):
            open_px = 0.0
    if open_px <= 0:
        return {**base, "ok": False, "reason": f"{side}复合确认：无有效开盘锚"}

    dev_pct = _cfg_float(cfg, "t0_confirm_dev_pct", 0.3, lo=0.0, hi=20.0)
    vol_mult = _cfg_float(cfg, "t0_confirm_vol_mult", 0.0, lo=0.0, hi=20.0)
    base.update({"dev_pct": dev_pct, "mom_bars": mom_n, "vol_mult": vol_mult})

    lows: List[float] = []
    highs: List[float] = []
    for b in win:
        try:
            lo = float(b.get("low") or 0)
            hi = float(b.get("high") or 0)
        except (TypeError, ValueError):
            continue
        if lo > 0:
            lows.append(lo)
        if hi > 0:
            highs.append(hi)
    if want_buy:
        if not lows:
            return {**base, "ok": False, "reason": f"{side}复合确认：无有效 low"}
        extreme = min(lows)
        reached = extreme <= open_px * (1.0 - dev_pct / 100.0)
        extreme_move = (extreme / open_px - 1.0) * 100.0
    else:
        if not highs:
            return {**base, "ok": False, "reason": f"{side}复合确认：无有效 high"}
        extreme = max(highs)
        reached = extreme >= open_px * (1.0 + dev_pct / 100.0)
        extreme_move = (extreme / open_px - 1.0) * 100.0
    base["extreme_move_pct"] = round(extreme_move, 4)
    if not reached:
        return {
            **base,
            "ok": False,
            "reason": (
                f"{side}复合确认：未达开盘锚偏离 "
                f"{extreme_move:.2f}%（需{'≤' if want_buy else '≥'}"
                f"{-dev_pct if want_buy else dev_pct:.2f}%）"
            ),
        }

    tail = win[-mom_n:]
    hit = 0
    valid = 0
    for b in tail:
        try:
            o = float(b.get("open") or 0)
            c = float(b.get("close") or 0)
        except (TypeError, ValueError):
            continue
        if o <= 0 or c <= 0:
            continue
        valid += 1
        if (c > o) if want_buy else (c < o):
            hit += 1
    base["mom_hits"] = hit
    base["mom_valid"] = valid
    if valid <= 0 or hit < max(1, (valid + 1) // 2):
        return {
            **base,
            "ok": False,
            "reason": f"{side}复合确认：动量未翻转 {hit}/{valid}",
        }

    if vol_mult > 1e-12:
        vols: List[float] = []
        for b in win:
            try:
                v = float(b.get("volume") or b.get("vol") or 0)
            except (TypeError, ValueError):
                v = 0.0
            vols.append(max(0.0, v))
        last_v = vols[-1] if vols else 0.0
        prior = [v for v in vols[:-1] if v > 0]
        if prior:
            avg = sum(prior) / float(len(prior))
            base["vol_last"] = round(last_v, 4)
            base["vol_avg_prior"] = round(avg, 4)
            if avg > 0 and last_v + 1e-12 < avg * vol_mult:
                return {
                    **base,
                    "ok": False,
                    "reason": (
                        f"{side}复合确认：量能不足 "
                        f"{last_v:.0f}<{avg:.0f}×{vol_mult:.2f}"
                    ),
                }

    return {
        **base,
        "ok": True,
        "reason": f"{side}复合确认通过（偏离{extreme_move:.2f}% · 动量{hit}/{valid}）",
    }


def prefix_leg1_confirm_ok(
    minute_bars: Sequence[dict],
    *,
    direction: str,
    cfg: dict,
    ref: Optional[float] = None,
) -> Dict[str, Any]:
    """第一腿确认（v5）：阴阳占比 ∩ 复合确认（开盘锚偏离+动量）。"""
    parts: List[Dict[str, Any]] = [
        prefix_fixed_bar_ratio_entry_ok(
            minute_bars, direction=direction, cfg=cfg
        ),
        prefix_composite_entry_ok(
            minute_bars, direction=direction, cfg=cfg, ref=ref
        ),
    ]
    ok = all(bool(p.get("ok")) for p in parts)
    reasons = [str(p.get("reason") or "") for p in parts if p.get("reason")]
    return {
        "ok": ok,
        "mode": "both",
        "segment": "leg1_confirm",
        "parts": parts,
        "reason": " · ".join(reasons) if reasons else ("确认通过" if ok else "确认未过"),
    }


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
    import math

    # 仅按占比阈值：ceil(thr × 后半有效根)；不再叠 y_prefix_min_half_hits 硬下限
    required_hits = int(math.ceil(thr * valid_n - 1e-12))
    required_hits = max(0, min(required_hits, valid_n))
    ok = hit_n >= required_hits
    return {
        **base,
        "ok": ok,
        "segment": seg_name,
        "prefix_bars": fixed_n,
        "half_bars": half,
        count_key: hit_n,
        "half_valid": valid_n,
        "required_hits": required_hits,
        ratio_key: round(ratio, 4),
        "reason": (
            f"{side}固定前缀后半{move_label} {hit_n}/{valid_n}={ratio:.0%}≥{required_hits}根"
            if ok
            else f"{side}固定前缀后半{move_label}不足 {hit_n}/{valid_n}<{required_hits}根"
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
    lot: int,
    fill_mode: str,
    cfg: dict,
    cost_model: str,
    cost_params: dict,
    stock_code: str,
    atr_pct: Optional[float],
    range_pct: float,
    t0_ratio: float,
    cash: float = 0.0,
    session_bars: Optional[Sequence[dict]] = None,
    session_bar: Optional[dict] = None,
    defer_eod: bool = False,
    leg1_gate_at: Optional[Callable[[int], bool]] = None,
    y_tau: Optional[float] = None,
) -> Dict[str, Any]:
    """反 T 分钟路径：先卖后买回。"""
    close = float(bar.get("close") or 0)
    sess_bars = session_bars if session_bars is not None else minute_bars
    sess_bar = session_bar if session_bar is not None else bar
    cash0 = float(cash or 0)
    sellable = float(sellable_shares if sellable_shares is not None else shares)
    sellable = min(sellable, shares)
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
    sell_level = float(ref or 0)

    for idx, mb in enumerate(minute_bars):
        hi = float(mb.get("high") or 0)
        lo = float(mb.get("low") or 0)
        close = float(mb.get("close") or 0)
        ts = mb.get("datetime") or mb.get("date")
        pm_hit = bool(pm_hm and _hm_reached(ts, pm_hm, inclusive=False))

        # 第一腿：确认根收盘（leg1_gate_at）；无闸时 τ 入场闸
        if sold_qty <= 0 and qty > 0:
            if pm_hit:
                continue
            gated = leg1_gate_at is not None
            if gated:
                if not leg1_gate_at(idx):
                    continue
                if close <= 0:
                    continue
                fill_sell = float(close)
            else:
                if close <= 0:
                    continue
                fill_sell = float(close)
                gate = tau_leg1_fill_price_ok(
                    fill_px=fill_sell,
                    ref=ref,
                    y_tau=y_tau,
                    direction="sell_then_buy",
                    cfg=cfg,
                )
                if not gate.get("ok"):
                    continue
            cash_delta += append_t0_leg(
                trades,
                cost_model=cost_model,
                cost_params=cost_params,
                side="t0_sell",
                stock_code=stock_code,
                shares=qty,
                price=fill_sell,
                trigger=fill_sell,
                at=ts,
                leg_kind="trigger",
                note="反T卖出（ŷ+前缀后半下跌确认）",
            )
            shares_now -= qty
            sold_qty = qty
            sold_price = fill_sell
            sell_level = float(sold_price)
            touch_sell_at = ts
            leg1_idx = idx
            chase_buy_level = _leg2_tau_target_px(
                ref=ref,
                y_tau=y_tau,
                direction="sell_then_buy",
                cfg=cfg,
            )
            if chase_buy_level is None and sold_price > 0:
                chase_buy_level = _leg2_breakeven_target_px(
                    leg1_px=sold_price, direction="sell_then_buy"
                )
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
                    # 止损买回：账户余额够才成交；不够则本根继续尝试追价/触发（勿 continue 跳过）
                    if _account_can_buy(
                        cash0 + cash_delta,
                        shares=cover,
                        price=fill_stop,
                        cost_model=cost_model,
                        cost_params=cost_params,
                    ):
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

            # 第二腿：τ 出场价闸（买回须低于 open×(1+ŷ_τ×买价裕度)）
            tau_buy_target = chase_buy_level
            if tau_buy_target is None:
                tau_buy_target = _leg2_tau_target_px(
                    ref=ref,
                    y_tau=y_tau,
                    direction="sell_then_buy",
                    cfg=cfg,
                )
            if tau_buy_target is None:
                tau_buy_target = _leg2_breakeven_target_px(
                    leg1_px=sold_price, direction="sell_then_buy"
                )
            if tau_buy_target is None:
                px = float(mb.get("close") or hi or sold_price)
                chase_buy_level, last_chase_min, adjusted = _maybe_pm_chase_level(
                    level=float(sold_price),
                    px=px,
                    ts=ts,
                    pm_hm=pm_hm,
                    last_chase_min=last_chase_min,
                    interval_min=pm_chase_iv,
                )
                if not adjusted:
                    continue
                buy_level = float(chase_buy_level)
                used_chase = last_chase_min is not None
                ceil_px = _stb_chase_buy_cap_px(
                    sold_price=sold_price,
                    stop_level=stop_level,
                    stop_pct=stop_pct,
                    cfg=cfg,
                )
                if ceil_px is not None:
                    buy_level = min(float(buy_level), float(ceil_px))
                if lo > buy_level:
                    continue
            else:
                if chase_buy_level is None:
                    chase_buy_level = float(tau_buy_target)
                buy_level = float(chase_buy_level)
                used_chase = last_chase_min is not None
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
                    ceil_px = _stb_chase_buy_cap_px(
                        sold_price=sold_price,
                        stop_level=stop_level,
                        stop_pct=stop_pct,
                        cfg=cfg,
                    )
                    if ceil_px is not None:
                        chase_buy_level = min(float(chase_buy_level), float(ceil_px))
                    # 追价可抬高目标（勿再 min 回 τ 闸，否则中点追价被钉死）
                    buy_level = float(chase_buy_level)
                    used_chase = last_chase_min is not None
                    if not adjusted or lo > buy_level:
                        continue
                else:
                    buy_level = min(float(buy_level), float(tau_buy_target))
                ceil_px = _stb_chase_buy_cap_px(
                    sold_price=sold_price,
                    stop_level=stop_level,
                    stop_pct=stop_pct,
                    cfg=cfg,
                )
                if ceil_px is not None:
                    buy_level = min(float(buy_level), float(ceil_px))
            if lo > buy_level:
                continue
            fill_buy = _fill_buy(lo, buy_level, fill_mode)
            # 中点追价已改目标：不再用原始 τ 出场闸否决（止损同样不受闸）
            if not used_chase and not tau_leg2_fill_price_ok(
                fill_px=fill_buy,
                ref=ref,
                y_tau=y_tau,
                direction="sell_then_buy",
                cfg=cfg,
            ).get("ok"):
                continue
            cover = sold_qty
            # 账户余额（开盘现金 + 当日累计）够则买回；不要求卖出净得自给
            if not _account_can_buy(
                cash0 + cash_delta,
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
        # 敞口估价：完整日可用 session close；未齐窗只用末根 close（禁日线收盘）
        last_px = float(close) if close > 0 else float(sess_bar.get("close") or 0)
        sess_close = (
            float(sess_bar.get("close") or last_px)
            if _session_minutes_complete(minute_bars)
            else last_px
        )
        eod_ok = _allows_eod_cover(minute_bars, defer_eod=defer_eod)
        if cfg.get("must_cover_same_day") and eod_ok:
            cover = sold_qty
            # 账户余额够则强制买回；不够才 abandon_cover_cash
            if not _account_can_buy(
                cash0 + cash_delta,
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
        elif eod_ok:
            exposure_pnl = round((sold_price - sess_close) * sold_qty, 2)
            exit_reason = "abandon_cover"
        elif defer_eod:
            # 盘中前缀：已卖未买回，不记敞口估值（等后续 K / 收盘窗）
            exit_reason = T0_PENDING_EXIT
        else:
            # 回测全日模式但分钟未齐：记 incomplete，敞口用末根（非日线收盘）
            exposure_pnl = round((sold_price - sess_close) * sold_qty, 2)
            exit_reason = "incomplete_session"

    if sold_qty <= 0:
        return _skip_result(
            reason="反T确认根未开第一腿",
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
    y_tau: Optional[float] = None,
) -> Dict[str, Any]:
    """正 T 分钟路径：确认根加仓后卖旧底仓（T+1），不卖当日新买股。"""
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
            reason="正T：缺现金（确认根加仓需要预留现金）",
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

        # 第一腿：确认根收盘（leg1_gate_at）；无闸时保留相对开盘触价兜底
        if bought_qty <= 0:
            if pm_hit:
                continue
            gated = leg1_gate_at is not None
            if gated:
                if not leg1_gate_at(idx):
                    continue
                if close <= 0:
                    continue
                fill_buy = float(close)
            else:
                if close <= 0:
                    continue
                fill_buy = float(close)
                gate = tau_leg1_fill_price_ok(
                    fill_px=fill_buy,
                    ref=ref,
                    y_tau=y_tau,
                    direction="buy_then_sell",
                    cfg=cfg,
                )
                if not gate.get("ok"):
                    continue
                buy_level = float(gate.get("bound_px") or fill_buy)
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
                note="正T加仓（ŷ+前缀后半上涨确认；新股T+1锁仓）",
            )
            shares_now += qty
            bought_qty = qty
            buy_price = fill_buy
            touch_buy_at = ts
            sell_old_qty = min(bought_qty, sell_old_cap)
            chase_sell_level = _leg2_tau_target_px(
                ref=ref,
                y_tau=y_tau,
                direction="buy_then_sell",
                cfg=cfg,
            )
            if chase_sell_level is None and buy_price > 0:
                chase_sell_level = _leg2_breakeven_target_px(
                    leg1_px=buy_price, direction="buy_then_sell"
                )
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

            tau_sell_target = chase_sell_level
            if tau_sell_target is None:
                tau_sell_target = _leg2_tau_target_px(
                    ref=ref,
                    y_tau=y_tau,
                    direction="buy_then_sell",
                    cfg=cfg,
                )
            if tau_sell_target is None:
                tau_sell_target = _leg2_breakeven_target_px(
                    leg1_px=buy_price, direction="buy_then_sell"
                )
            if tau_sell_target is None:
                px = float(mb.get("close") or lo or buy_price)
                chase_sell_level, last_chase_min, adjusted = _maybe_pm_chase_level(
                    level=float(buy_price),
                    px=px,
                    ts=ts,
                    pm_hm=pm_hm,
                    last_chase_min=last_chase_min,
                    interval_min=pm_chase_iv,
                )
                if not adjusted:
                    continue
                floor_px = _bts_chase_sell_floor_px(
                    buy_price=buy_price,
                    stop_level=stop_level,
                    stop_pct=stop_pct,
                    cfg=cfg,
                )
                if floor_px is not None:
                    chase_sell_level = max(float(chase_sell_level), float(floor_px))
                sell_level = float(chase_sell_level)
                used_chase = last_chase_min is not None
                if sell_old_qty <= 0 or hi < sell_level:
                    continue
            else:
                if chase_sell_level is None:
                    chase_sell_level = float(tau_sell_target)
                sell_level = float(chase_sell_level)
                used_chase = last_chase_min is not None
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
                    floor_px = _bts_chase_sell_floor_px(
                        buy_price=buy_price,
                        stop_level=stop_level,
                        stop_pct=stop_pct,
                        cfg=cfg,
                    )
                    if floor_px is not None:
                        chase_sell_level = max(float(chase_sell_level), float(floor_px))
                    # 追价可压低目标（勿再 max 回 τ 闸）
                    sell_level = float(chase_sell_level)
                    used_chase = last_chase_min is not None
                    if not adjusted or sell_old_qty <= 0 or hi < sell_level:
                        continue
                else:
                    sell_level = max(float(sell_level), float(tau_sell_target))
                floor_px = _bts_chase_sell_floor_px(
                    buy_price=buy_price,
                    stop_level=stop_level,
                    stop_pct=stop_pct,
                    cfg=cfg,
                )
                if floor_px is not None:
                    sell_level = max(float(sell_level), float(floor_px))
            if sell_old_qty <= 0 or hi < sell_level:
                continue
            fill_sell = _fill_sell(hi, sell_level, fill_mode)
            if not used_chase and not tau_leg2_fill_price_ok(
                fill_px=fill_sell,
                ref=ref,
                y_tau=y_tau,
                direction="buy_then_sell",
                cfg=cfg,
            ).get("ok"):
                continue
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
            reason="正T确认根未开第一腿",
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
        last_px = float(close) if close > 0 else float(sess_bar.get("close") or 0)
        sess_close = (
            float(sess_bar.get("close") or last_px)
            if _session_minutes_complete(minute_bars)
            else last_px
        )
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
        elif eod_ok and not cfg.get("must_cover_same_day"):
            exposure_pnl = round((sess_close - buy_price) * bought_qty, 2)
            exit_reason = exit_reason or "abandon_cover"
        elif eod_ok and cfg.get("must_cover_same_day") and bought_qty > 0 and sell_old_qty <= 0:
            # 防御：must_cover 但可卖旧仓为 0，无法卖回 → 记多头敞口
            exposure_pnl = round((sess_close - buy_price) * bought_qty, 2)
            exit_reason = "abandon_cover_cap"
        elif defer_eod and not eod_ok and bought_qty > 0:
            # 盘中前缀：已买未卖旧，不记敞口估值（等后续 K / 收盘窗）
            exit_reason = T0_PENDING_EXIT
        elif not eod_ok and bought_qty > 0:
            # 回测全日模式但分钟未齐：incomplete，敞口用末根（非日线收盘）
            if sell_old_qty > 0:
                exposure_pnl = round((sess_close - buy_price) * float(sell_old_qty), 2)
            else:
                exposure_pnl = round((sess_close - buy_price) * bought_qty, 2)
            exit_reason = "incomplete_session"

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


def _side_exec_pack(
    *,
    cfg_side: dict,
) -> Tuple[dict, str]:
    """侧向执行参数 + fill_mode（第二腿仅 τ 出场价闸）。"""
    fill_mode = str(cfg_side.get("fill_mode") or "trigger")
    return dict(cfg_side), fill_mode


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
    y_tau: Optional[float] = None,
    cfg_side: Optional[dict] = None,
    wait_reason: Optional[str] = None,
) -> Dict[str, Any]:
    sell_level = buy_level = tau_entry_bound = tau_exit_bound = None
    direction_s = str(direction or "").strip().lower()
    if ref is not None and y_tau is not None and direction_s in ("buy_then_sell", "sell_then_buy"):
        tau_entry_bound = tau_entry_bound_px(
            ref=float(ref),
            y_tau=y_tau,
            direction=direction_s,
            cfg=cfg_side,
        )
        tau_exit_bound = tau_exit_bound_px(
            ref=float(ref),
            y_tau=y_tau,
            direction=direction_s,
            cfg=cfg_side,
        )
        if direction_s == "buy_then_sell":
            buy_level = (
                round(float(tau_entry_bound), 4) if tau_entry_bound is not None else None
            )
            sell_level = (
                round(float(tau_exit_bound), 4) if tau_exit_bound is not None else None
            )
        else:
            sell_level = (
                round(float(tau_entry_bound), 4) if tau_entry_bound is not None else None
            )
            buy_level = (
                round(float(tau_exit_bound), 4) if tau_exit_bound is not None else None
            )
    lo = float(m.get("low") or 0)
    hi = float(m.get("high") or 0)
    touch_leg1 = False
    touch_blocked = False
    if direction_s == "buy_then_sell" and buy_level is not None:
        touch_leg1 = lo <= float(buy_level)
    elif direction_s == "sell_then_buy" and sell_level is not None:
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
        "y_tau": round(float(y_tau), 4) if y_tau is not None else None,
        "tau_entry_bound": (
            round(float(tau_entry_bound), 4) if tau_entry_bound is not None else None
        ),
        "tau_exit_bound": (
            round(float(tau_exit_bound), 4) if tau_exit_bound is not None else None
        ),
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
    stock_code: str = "",
    tau_pool_day: Optional[dict] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[dict]]:
    """前缀第一腿闸：齐 N 根 → 前 N 根因果重算 ŷ → 选向 → 确认根开第一腿。

    契约：分钟输入仅前 N 根；选向不在开盘锁死；第一腿在确认根收盘。
    Returns (plan, early_exit, dir_res_for_finish).
    """
    from core.t0.config import resolve_path_abandon_bars
    from core.t0.score_policy import (
        rescore_scores_at_fixed_prefix,
        resolve_cover_policy,
        resolve_fuse_intraday,
        scores_have_any,
    )

    n_bars = len(mins)
    entry_flags = [False] * n_bars
    trace_rows: List[Dict[str, Any]] = []
    direction_locked: Optional[str] = None
    dir_res_locked: Optional[dict] = None
    plan_tail: Optional[Dict[str, Any]] = None
    last_amp_skip: Optional[Dict[str, Any]] = None
    last_dir_wait: Optional[Dict[str, Any]] = None
    cover_meta: Optional[dict] = None
    live_snap: Optional[dict] = dict(score_snap) if isinstance(score_snap, dict) else score_snap

    # 选向信息集根数：强制方向用侧向 N；dual_y 先取两侧较小者（定方向后再齐侧向窗）
    mode_dir = str(cfg_day.get("direction") or "").strip().lower()
    n_buy = int(resolve_path_abandon_bars(cfg_day, "buy_then_sell"))
    n_sell = int(resolve_path_abandon_bars(cfg_day, "sell_then_buy"))
    if mode_dir == "buy_then_sell":
        score_n = n_buy
    elif mode_dir == "sell_then_buy":
        score_n = n_sell
    elif mode_dir == "dual_y":
        score_n = min(n_buy, n_sell)
    else:
        score_n = int(resolve_path_abandon_bars(cfg_day, None))
    scored_at_prefix = False

    for j in range(n_bars):
        m = mins[j]
        prefix = mins[: j + 1]
        n = len(prefix)
        if n < 2:
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

        # 未齐选向窗：只等待（不锁方向、不偷用未完成前缀）
        if n < score_n:
            trace_rows.append(
                _forward_trace_row(
                    idx=j,
                    m=m,
                    prefix_bars=n,
                    evaluated=False,
                    direction=direction_locked,
                    wait_reason=f"待固定前缀 {n}/{score_n}",
                )
            )
            last_dir_wait = _skip_result(
                reason=f"待固定前缀 {n}/{score_n}",
                shares=shares,
                bar=_day_ohlc_from_minutes(prefix, bar),
                extra={
                    "path_mode": "first_touch",
                    "range_mode": "fixed_prefix",
                    "prefix_bars": n,
                },
            )
            continue

        # 齐 score_n：用前 N 根因果重算 dual_y 并选向（只做一次）
        if direction_locked is None and not scored_at_prefix:
            scored_at_prefix = True
            fixed_for_score = mins[:score_n]
            code = str(stock_code or "").strip()
            if code and str(cfg_day.get("direction") or "") == "dual_y":
                try:
                    live_snap = rescore_scores_at_fixed_prefix(
                        stock_code=code,
                        minute_prefix=fixed_for_score,
                        day_bar=bar if isinstance(bar, dict) else None,
                        hist_bars=hist_bars,
                        tau_pool_day=tau_pool_day,
                        fuse_intraday=resolve_fuse_intraday(cfg_day),
                        open_snap=live_snap if isinstance(live_snap, dict) else None,
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("prefix causal rescore failed", exc_info=True)

            gate_pre = prefix_range_gate(fixed_for_score, bar, cost=cost, cfg=cfg_pre)
            bar_n = gate_pre.get("bar_day") or _day_ohlc_from_minutes(fixed_for_score, bar)
            ref_pre = gate_pre.get("ref")
            if ref_pre is None or float(ref_pre) <= 0:
                early = _skip_result(
                    reason="前缀无有效开盘锚",
                    shares=shares,
                    bar=bar_n,
                    extra={
                        "path_mode": "first_touch",
                        "range_mode": "fixed_prefix",
                        "prefix_bars": score_n,
                        "forward_trace": trace_rows,
                    },
                )
                return None, early, dir_res_locked

            dir_res = resolve_direction(
                bar=bar_n,
                ref=float(ref_pre),
                cfg=cfg_day,
                cash=float(cash or 0),
                shares=shares,
                hist_bars=hist_bars,
                atr_pct=atr_pct,
                scores=live_snap,
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
                        "path_mode": "first_touch",
                        "range_mode": "fixed_prefix",
                        "prefix_bars": score_n,
                        "forward_trace": trace_rows,
                        "scores": live_snap if isinstance(live_snap, dict) else None,
                    },
                )
                return None, early, dir_res

            cand = str(dir_res["direction"])
            cfg_try = apply_side_exec_params(cfg_day, cand)
            direction_locked = cand
            dir_res_locked = dir_res
            if str(cfg_day.get("direction") or "") == "dual_y":
                cover_meta = resolve_cover_policy(
                    scores=live_snap or {},
                    direction=direction_locked,
                    cfg=cfg_try,
                )

        direction = str(direction_locked)
        cfg_side, fill_mode = _side_exec_pack(
            cfg_side=apply_side_exec_params(cfg_day, direction),
        )
        path_y_tau_trace = _score_y_tau(live_snap)
        if cover_meta is not None:
            cfg_side["must_cover_same_day"] = bool(cover_meta.get("must_cover"))

        fixed_n, _ratio_thr = _prefix_bar_ratio_params(cfg_side, direction)
        side_label = "正T" if direction == "buy_then_sell" else "反T"

        # 侧向 N 可能大于共用 score_n：继续等到齐窗
        if n < fixed_n:
            seg = {
                "ok": False,
                "segment": "fixed_prefix_wait",
                "fixed_bars": fixed_n,
                "prefix_bars": n,
                "reason": f"{side_label}待固定前缀 {n}/{fixed_n}",
            }
            last_dir_wait = _skip_result(
                reason=str(seg["reason"]),
                shares=shares,
                bar=_day_ohlc_from_minutes(prefix, bar),
                extra={
                    "direction_used": direction,
                    "path_mode": "first_touch",
                    "range_mode": "fixed_prefix",
                    "prefix_bars": n,
                    "prefix_segment": seg,
                },
            )
            trace_rows.append(
                _forward_trace_row(
                    idx=j,
                    m=m,
                    prefix_bars=n,
                    evaluated=True,
                    direction=direction,
                    wait_reason=str(seg["reason"]),
                )
            )
            continue

        if j != fixed_n - 1:
            # 决策窗已过：不再滚动重评
            if j > fixed_n - 1:
                trace_rows.append(
                    _forward_trace_row(
                        idx=j,
                        m=m,
                        prefix_bars=n,
                        evaluated=False,
                        direction=direction,
                        wait_reason=f"{side_label}固定前缀决策已过",
                    )
                )
            continue

        fixed_prefix = mins[:fixed_n]
        gate_fixed = prefix_range_gate(fixed_prefix, bar, cost=cost, cfg=cfg_side)
        bar_n = gate_fixed.get("bar_day") or _day_ohlc_from_minutes(fixed_prefix, bar)
        ref = gate_fixed.get("ref")
        range_pct_side = gate_fixed.get("range_pct")
        if ref is None or float(ref) <= 0:
            early = _skip_result(
                reason="确认根无有效开盘锚",
                shares=shares,
                bar=bar_n,
                extra={
                    "direction_used": direction,
                    "path_mode": "first_touch",
                    "range_mode": "fixed_prefix",
                    "prefix_bars": fixed_n,
                    "forward_trace": trace_rows,
                },
            )
            return None, early, dir_res_locked

        seg = prefix_leg1_confirm_ok(
            fixed_prefix,
            direction=direction,
            cfg=cfg_side,
            ref=float(ref) if ref is not None else None,
        )
        env = prefix_env_gate_ok(
            range_pct=range_pct_side,
            y_path=_score_y_path(live_snap),
            y_tau=_score_y_tau(live_snap),
            cfg=cfg_side,
        )
        if not bool(env.get("ok")):
            seg = {**seg, "ok": False, "env_gate": env, "reason": env.get("reason")}

        entry_ready = bool(seg.get("ok"))
        dir_amp = {
            "ok": True,
            "reason": f"{side_label}固定前缀：跳过探极值振幅",
            "direction": direction,
        }
        wait_reason = None

        if entry_ready:
            fill_px = float(m.get("close") or 0)
            tau_px = tau_leg1_fill_price_ok(
                fill_px=fill_px,
                ref=float(ref),
                y_tau=_score_y_tau(live_snap),
                direction=direction,
                cfg=cfg_side,
            )
            seg = {**seg, "tau_entry_price": tau_px}
            if not bool(tau_px.get("ok")):
                entry_ready = False
                wait_reason = str(tau_px.get("reason") or f"{side_label}τ入场价未过")
                seg = {**seg, "ok": False, "reason": wait_reason}

        if entry_ready:
            y_path_gate = _score_y_path(live_snap)
            if y_path_gate is None and isinstance(live_snap, dict):
                try:
                    from core.t0.score_policy import predict_path_from_prefix_minutes

                    y_path_gate = predict_path_from_prefix_minutes(
                        live_snap,
                        fixed_prefix,
                        day_bar=bar,
                        hist_bars=hist_bars,
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("causal prefix y_path failed", exc_info=True)
                    y_path_gate = None
            if y_path_gate is not None:
                seg["y_path_prefix"] = round(float(y_path_gate), 4)
                if isinstance(live_snap, dict) and _score_y_path(live_snap) is None:
                    live_snap = dict(live_snap)
                    live_snap["y_path"] = seg["y_path_prefix"]
                    live_snap["predicted_score_path"] = seg["y_path_prefix"]

        entry_flags[j] = entry_ready
        wait_reason = (
            None if entry_ready else str(seg.get("reason") or wait_reason or f"{side_label}固定前缀未过")
        )

        if not entry_ready:
            abandon_on = bool(cfg_day.get("y_path_abandon_enabled", True))
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
                    "scores": live_snap if isinstance(live_snap, dict) else None,
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
                        "scores": live_snap if isinstance(live_snap, dict) else None,
                        "forward_trace": trace_rows
                        + [
                            _forward_trace_row(
                                idx=j,
                                m=m,
                                prefix_bars=n,
                                evaluated=True,
                                range_ok=bool(gate_fixed.get("ok")),
                                range_pct=range_pct_side,
                                min_range_pct=gate_fixed.get("min_range_pct"),
                                direction=direction,
                                side_range_ok=bool(gate_fixed.get("ok")),
                                dir_amp_ok=True,
                                seg_ok=False,
                                entry_ready=False,
                                gate_open=False,
                                ref=float(ref),
                                y_tau=path_y_tau_trace,
                                cfg_side=cfg_side,
                                wait_reason=wait_reason,
                            )
                        ],
                    },
                )
                return None, early, dir_res_locked

        trace_rows.append(
            _forward_trace_row(
                idx=j,
                m=m,
                prefix_bars=n,
                evaluated=True,
                range_ok=bool(gate_fixed.get("ok")),
                range_pct=range_pct_side,
                min_range_pct=gate_fixed.get("min_range_pct"),
                direction=direction,
                side_range_ok=bool(gate_fixed.get("ok")),
                dir_amp_ok=bool(dir_amp.get("ok")),
                seg_ok=bool(seg.get("ok")),
                entry_ready=entry_ready,
                gate_open=entry_ready,
                ref=float(ref),
                y_tau=path_y_tau_trace,
                cfg_side=cfg_side,
                wait_reason=wait_reason,
            )
        )

        plan_tail = {
            "direction": direction,
            "dir_res": dir_res_locked,
            "cfg_side": cfg_side,
            "fill_mode": fill_mode,
            "ref": float(ref),
            "range_pct": range_pct_side,
            "bar_day": bar_n,
            "cover_meta": cover_meta,
            "score_snap": live_snap,
        }

    if plan_tail is None:
        if last_amp_skip is not None:
            if isinstance(last_amp_skip, dict):
                last_amp_skip = dict(last_amp_skip)
                last_amp_skip["forward_trace"] = trace_rows
                if isinstance(live_snap, dict):
                    last_amp_skip["scores"] = live_snap
            return None, last_amp_skip, dir_res_locked
        if last_dir_wait is not None:
            if isinstance(last_dir_wait, dict) and isinstance(live_snap, dict):
                last_dir_wait = dict(last_dir_wait)
                last_dir_wait["scores"] = live_snap
                last_dir_wait["forward_trace"] = trace_rows
            return None, last_dir_wait, dir_res_locked
        return None, None, dir_res_locked

    def leg1_gate_at(idx: int) -> bool:
        """仅确认根可开第一腿。"""
        if idx < 0 or idx >= n_bars:
            return False
        return bool(entry_flags[idx])

    plan = dict(plan_tail)
    plan["leg1_gate_at"] = leg1_gate_at
    plan["gate_trace"] = trace_rows
    plan["score_snap"] = live_snap
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
    tau_pool_day: Optional[dict] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[dict]]:
    """前向固定前缀第一触达：齐 N 根因果重算 ŷ → 选向 → 确认根第一腿。

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
        stock_code=str(stock_code or ""),
        tau_pool_day=tau_pool_day if isinstance(tau_pool_day, dict) else None,
    )
    if early is not None:
        if isinstance(early, dict):
            extra = early.get("extra") if isinstance(early.get("extra"), dict) else {}
            ft = extra.get("forward_trace") or early.get("forward_trace")
            if ft:
                _set_forward_trace_on_result(early, ft, cfg_day=cfg_day, dir_res=dir_res)
            snap_e = early.get("scores")
            if isinstance(snap_e, dict):
                early["_t0_score_snap"] = snap_e
        return early, dir_res
    if plan is None:
        return None, dir_res

    if isinstance(plan.get("score_snap"), dict):
        score_snap = plan["score_snap"]

    from core.t0.rules import _ensure_fixed_direction_path_y_tau

    score_snap = _ensure_fixed_direction_path_y_tau(
        score_snap if isinstance(score_snap, dict) else None,
        plan.get("direction"),
    )

    path_kwargs = {
        "session_bars": mins,
        "session_bar": bar_session,
        "defer_eod": bool(defer_eod),
        "leg1_gate_at": plan["leg1_gate_at"],
    }
    direction = str(plan["direction"])
    cfg_side = plan["cfg_side"]
    fill_mode = str(plan["fill_mode"])
    ref = float(plan["ref"])
    range_pct = float(plan["range_pct"])
    bar_day = plan["bar_day"]
    cover_meta = plan.get("cover_meta")
    dir_res = plan.get("dir_res") or dir_res
    path_y_tau = _score_y_tau(
        score_snap if isinstance(score_snap, dict) else plan.get("score_snap")
    )
    path_kwargs["y_tau"] = path_y_tau

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
            if isinstance(score_snap, dict):
                pending["_t0_score_snap"] = score_snap
            return pending, dir_res

    if direction == "buy_then_sell":
        out = _first_touch_buy_then_sell(
            minute_bars=mins,
            bar=bar_day,
            shares=shares,
            cash=float(cash or 0),
            sellable_shares=sellable_shares,
            ref=ref,
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
            lot=lot,
            fill_mode=fill_mode,
            cfg=cfg_side,
            cost_model=cost_model,
            cost_params=cost_params,
            stock_code=stock_code,
            atr_pct=atr_pct,
            range_pct=range_pct,
            t0_ratio=ratio,
            cash=float(cash or 0),
            **path_kwargs,
        )

    if isinstance(out, dict):
        out["path_mode"] = "first_touch"
        out["intraday_path"] = "first_touch"
        out["range_mode"] = "fixed_prefix"
        try:
            from core.t0.config import resolve_path_abandon_bars

            out["prefix_bars"] = int(resolve_path_abandon_bars(cfg_side, direction))
        except Exception:  # noqa: BLE001
            out["prefix_bars"] = len(mins)
        out["minute_bars"] = len(mins)
        out["direction_score"] = (dir_res or {}).get("direction_score")
        out["direction_reason"] = (dir_res or {}).get("direction_reason")
        out["direction_features"] = (dir_res or {}).get("features")
        if isinstance(score_snap, dict):
            out["_t0_score_snap"] = score_snap
        if cover_meta:
            out["cover_policy"] = cover_meta
            out["must_cover_same_day"] = bool(cover_meta.get("must_cover"))
        base_ratio = float(base_t0_ratio if base_t0_ratio is not None else cfg_day.get("t0_ratio") or 1.0)
        out["t0_ratio_base"] = round(base_ratio, 4)
        out["t0_ratio"] = round(float(cfg_day.get("t0_ratio") or base_ratio), 4)
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
    """单日做 T：齐固定前缀 N 根后用前 N 根因果重算 ŷ 再选向；确认根开第一腿。

    开盘可预计算开盘-only 快照；确认窗用 ``rescore_scores_at_fixed_prefix`` 覆盖。
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
                # 选向仅开盘/隔夜信息集；分钟只用于成交路径（禁 ≤τ 前缀前瞻）
                use_minute_tau=False,
                **tau_pool_day_score_kwargs(tau_pool_day, stock_code),
            )

    mins = [dict(m) for m in (minute_bars or [])]
    mins.sort(key=lambda x: str(x.get("datetime") or ""))
    cfg_day = dict(cfg)
    cfg_day["path_mode"] = "first_touch"

    # path实对照：用 path 模型训练标签阈值（研究对照，非成交触发）
    try:
        from core.research.path_ridge import load_path_model, path_label_triggers

        path_sell_trig, path_buy_trig = path_label_triggers(load_path_model())
    except Exception:  # noqa: BLE001
        logger.debug("path_label_triggers fallback", exc_info=True)
        path_sell_trig = 2.0
        path_buy_trig = 1.5

    def _finish(out: Optional[Dict[str, Any]], dir_res: Optional[dict] = None) -> Dict[str, Any]:
        nonlocal score_snap
        feats = (dir_res or {}).get("features") if isinstance(dir_res, dict) else None
        if isinstance(out, dict) and isinstance(out.get("_t0_score_snap"), dict):
            score_snap = out.pop("_t0_score_snap")
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
        # 画像：前 N 根因果 ŷ（与确认根选向同信息集）
        try:
            from core.t0.config import resolve_path_abandon_bars
            from core.t0.score_policy import attach_portrait_dual_scores

            dir_used = None
            if isinstance(packed, dict):
                dir_used = packed.get("direction_used") or packed.get("direction")
            n_pref = resolve_path_abandon_bars(cfg_day, dir_used)
            # 优先确认根实际重算用的 N（双侧不一致时与决策同信息集）
            if isinstance(score_snap, dict) and score_snap.get("_score_prefix_bars") is not None:
                try:
                    n_pref = int(score_snap.get("_score_prefix_bars"))
                except (TypeError, ValueError):
                    pass
            elif isinstance(packed, dict) and packed.get("prefix_bars") is not None:
                try:
                    n_pref = int(packed.get("prefix_bars"))
                except (TypeError, ValueError):
                    pass
            packed = attach_portrait_dual_scores(
                packed,
                score_snap,
                minute_bars=mins,
                day_bar=bar if isinstance(bar, dict) else None,
                hist_bars=hist_bars,
                prefix_bars=n_pref,
            )
        except Exception:  # noqa: BLE001
            logger.debug("attach_portrait_dual_scores failed", exc_info=True)
        return packed


    from core.t0.config import t0_slots_enabled
    from core.t0.slots import simulate_t0_day_slots

    if t0_slots_enabled(cfg_day) and mins:
        lot = int(cfg["lot_size"])
        bar_day = _day_ohlc_from_minutes(mins, bar)
        bar_session = dict(bar_day)
        if _session_minutes_complete(mins):
            daily_close = float((bar or {}).get("close") or 0)
            if daily_close > 0:
                bar_session["close"] = daily_close
        slot_out = simulate_t0_day_slots(
            bar=bar,
            minute_bars=mins,
            shares=shares,
            cost=cost,
            sellable_shares=sellable_shares,
            cfg=cfg_day,
            cash=cash,
            stock_code=stock_code,
            lot=lot,
            cost_model=cost_model,
            cost_params=cost_params,
            atr_pct=atr_pct,
            hist_bars=hist_bars,
            score_snap=score_snap,
            session_bar=bar_session,
            defer_eod=bool(defer_eod),
            tau_pool_day=tau_pool_day if isinstance(tau_pool_day, dict) else None,
        )
        return _finish(slot_out, None)

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
    # 仅完整日（末根≥14:55）才用日线收盘作强平价；午前截断禁用日线收盘前视
    bar_session = dict(bar_day)
    if _session_minutes_complete(mins):
        daily_close = float((bar or {}).get("close") or 0)
        if daily_close > 0:
            bar_session["close"] = daily_close
    lot = int(cfg["lot_size"])
    high = float(bar_day.get("high") or 0)
    low = float(bar_day.get("low") or 0)
    if high <= 0 or low <= 0 or shares <= 0:
        return _error_result("无效 bar 或持仓", shares)

    base_t0_ratio = float(cfg_day.get("t0_ratio") or 1.0)
    # 动仓比例固定为基准
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
        atr_pct=atr_pct,
        hist_bars=hist_bars,
        score_snap=score_snap,
        session_bar=bar_session,
        base_t0_ratio=base_t0_ratio,
        defer_eod=bool(defer_eod),
        tau_pool_day=tau_pool_day if isinstance(tau_pool_day, dict) else None,
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
