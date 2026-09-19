"""v6 做 T 选腿：每根 5m 用前缀 ŷ_oc 估目标价 C_τ → 收价 C 相对 ±δ 破带定方向。

C_τ = O×(1 + clip(ŷ_oc×scale, y_oc_l, y_oc_u)/100)。
upper = C_τ×(1+δ/100)，lower = C_τ×(1−δ/100)。
C > upper → 反T；C < lower → 正T。leg2 冻结在 C_τ。
ŷ_τc 已下线（ŷ_τ30/60/90 覆盖剩余窗租用）；
``y_t30_strong`` / ``y_t45_strong`` / ``y_t60_strong`` / ``y_t75_strong`` / ``y_t90_strong`` 已下线（``load_t0_rules`` 丢弃；缺键默认关）。只进 ŷ_τw 票。
path 仅入场校验；y_trade / y_eod / y_nowcast 不参与。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Sequence


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:  # NaN
        return None
    return x


def _pct_to_px(anchor: float, pct: float) -> Optional[float]:
    if anchor is None or float(anchor) <= 0:
        return None
    return float(anchor) * (1.0 + float(pct) / 100.0)


def r_hat_from_c_tau_px(c_tau: Any, price_tau: Any) -> Optional[float]:
    """R̂_τ = Ĉ_τ / price(τ) − 1（百分点）。与价带同一目标收。"""
    mid = _f(c_tau)
    px = _f(price_tau)
    if mid is None or px is None or mid <= 0 or px <= 0:
        return None
    return (float(mid) / float(px) - 1.0) * 100.0


DEFAULT_Y_OC_TARGET_SCALE = 10.0
DEFAULT_Y_OC_L = -3.0
DEFAULT_Y_OC_U = 3.0
Y_OC_TARGET_SCALE_MIN = 0.0
Y_OC_TARGET_SCALE_MAX = 100.0
Y_OC_CLIP_ABS_MAX = 20.0


def resolve_y_oc_target_params(cfg: Optional[dict] = None) -> tuple[float, float, float]:
    """C_τ 公式参数：scale、y_oc_l、y_oc_u（百分点）。缺键用默认 10 / −3 / +3。"""
    cfg_d = cfg if isinstance(cfg, dict) else {}

    def _clamp(key: str, default: float, lo: float, hi: float) -> float:
        try:
            raw = cfg_d.get(key)
            v = float(default if raw is None or raw == "" else raw)
        except (TypeError, ValueError):
            v = float(default)
        if v != v:  # NaN
            v = float(default)
        return max(lo, min(hi, v))

    scale = _clamp(
        "t0_y_oc_target_scale",
        DEFAULT_Y_OC_TARGET_SCALE,
        Y_OC_TARGET_SCALE_MIN,
        Y_OC_TARGET_SCALE_MAX,
    )
    lo = _clamp("t0_y_oc_l", DEFAULT_Y_OC_L, -Y_OC_CLIP_ABS_MAX, Y_OC_CLIP_ABS_MAX)
    hi = _clamp("t0_y_oc_u", DEFAULT_Y_OC_U, -Y_OC_CLIP_ABS_MAX, Y_OC_CLIP_ABS_MAX)
    if lo > hi:
        lo, hi = hi, lo
    return scale, lo, hi


def clip_y_oc_target_pct(
    y_oc: float,
    *,
    scale: float = DEFAULT_Y_OC_TARGET_SCALE,
    y_oc_l: float = DEFAULT_Y_OC_L,
    y_oc_u: float = DEFAULT_Y_OC_U,
) -> float:
    """clip(ŷ_oc×scale, y_oc_l, y_oc_u)；输入输出均为百分点。"""
    yt = float(y_oc) * float(scale)
    lo, hi = float(y_oc_l), float(y_oc_u)
    if lo > hi:
        lo, hi = hi, lo
    return max(lo, min(hi, yt))


def target_c_tau_px(
    price: float,
    y_oc: float,
    *,
    scale: float = DEFAULT_Y_OC_TARGET_SCALE,
    y_oc_l: float = DEFAULT_Y_OC_L,
    y_oc_u: float = DEFAULT_Y_OC_U,
) -> Optional[float]:
    """C_τ = price × (1 + clip(ŷ_oc×scale, y_oc_l, y_oc_u)/100)。"""
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if p <= 0:
        return None
    clipped = clip_y_oc_target_pct(y_oc, scale=scale, y_oc_l=y_oc_l, y_oc_u=y_oc_u)
    return p * (1.0 + clipped / 100.0)


def close_band_edges_px(
    c_tau: float,
    delta_pct: float,
) -> tuple[Optional[float], Optional[float]]:
    """upper = C_τ×(1+δ/100)，lower = C_τ×(1−δ/100)。"""
    try:
        mid = float(c_tau)
        d = max(0.0, float(delta_pct))
    except (TypeError, ValueError):
        return None, None
    if mid <= 0:
        return None, None
    return round(mid * (1.0 - d / 100.0), 4), round(mid * (1.0 + d / 100.0), 4)


def scores_close_components(
    scores: Optional[dict],
    *,
    open_px: float,
    prev_close: Optional[float] = None,
    price_tau: Optional[float] = None,
    cfg: Optional[dict] = None,
) -> Dict[str, Optional[float]]:
    """估 C_τ：O×(1+clip(ŷ_oc×scale, y_oc_l, y_oc_u)/100)。ŷ_τc 只对照。

    估用**日线** open；slots 再经 ``map_close_px_to_minute`` 映回分钟触价。
    remaining(clip(ŷ_oc×scale)) 写入 R̂_τ（= Ĉ_τ/price(τ)−1，与价带同目标）；
    remaining_oc 仍为未 clip 的 remaining(ŷ_oc)。ŷ_τc 为 Ridge 预估。均不参与选腿。
    ``c_oc`` 为未 clip 的 O×(1+ŷ_oc/100)，不进破带。
    """
    from core.signal.yhat_windows import (
        Y_TC_SOURCE_REMAINING,
        Y_TC_SOURCE_RIDGE,
        pick_y_oc,
        pick_y_τc,
        remaining_oc,
        ret_open_to_tau_of,
        ret_open_to_tau_pct,
    )
    from core.t0.score_policy import scores_from_item

    _ = prev_close
    raw = scores if isinstance(scores, dict) else {}
    sc = scores_from_item(raw)
    y_oc = pick_y_oc(raw)
    if y_oc is None:
        y_oc = sc.get("y_tau")
    y_τc_ridge = pick_y_τc(raw)
    if str(raw.get("y_τc_source") or "") == Y_TC_SOURCE_REMAINING:
        y_τc_ridge = _f(raw.get("y_τc_ridge"))
        if y_τc_ridge is None:
            y_τc_ridge = _f(raw.get("y_r")) or _f(raw.get("y_r_hat")) or _f(
                raw.get("predicted_score_r")
            )
    y_tau = y_oc
    scale, y_oc_l, y_oc_u = resolve_y_oc_target_params(cfg)
    y_oc_target = (
        clip_y_oc_target_pct(y_oc, scale=scale, y_oc_l=y_oc_l, y_oc_u=y_oc_u)
        if y_oc is not None
        else None
    )
    c_hat = _pct_to_px(open_px, y_oc_target) if y_oc_target is not None else None
    c_oc = _pct_to_px(open_px, y_oc) if y_oc is not None else None
    pt = _f(price_tau)
    if pt is None or pt <= 0:
        feats = raw.get("features_tau") if isinstance(raw.get("features_tau"), dict) else {}
        pt = _f(raw.get("price_tau")) or _f(feats.get("price_tau")) or _f(feats.get("price"))
    rot = ret_open_to_tau_pct(open_px, pt)
    if rot is None:
        rot = ret_open_to_tau_of(raw)
    rem = remaining_oc(y_oc, rot, open_px=open_px, price_tau=pt)
    rem_target = remaining_oc(y_oc_target, rot, open_px=open_px, price_tau=pt)
    if rem_target is None:
        rem_target = r_hat_from_c_tau_px(c_hat, pt)
    r_hat = rem_target if rem_target is not None else rem
    # 表列 ŷ_τc = Ridge 预估 price→close；R̂_τ = Ĉ_τ/price(τ)−1。
    y_τc = y_τc_ridge
    y_τc_source = Y_TC_SOURCE_RIDGE if y_τc_ridge is not None else None
    c_τc = _pct_to_px(pt, y_τc) if y_τc is not None and pt is not None and pt > 0 else None
    c_rem = _pct_to_px(pt, rem) if rem is not None and pt is not None and pt > 0 else None
    source = "y_oc" if c_hat is not None else None
    n_src = 1 if c_hat is not None else 0
    return {
        "c_tau": round(c_hat, 4) if c_hat is not None else None,
        "c_τc": round(c_τc, 4) if c_τc is not None else None,
        "c_oc": round(c_oc, 4) if c_oc is not None else None,
        "c_rem": round(c_rem, 4) if c_rem is not None else None,
        "c_hat_source": source,
        "c_trade": None,
        "c_nowcast": None,
        "y_tau": y_tau,
        "y_oc": y_oc,
        "y_oc_target": round(float(y_oc_target), 6) if y_oc_target is not None else None,
        "t0_y_oc_target_scale": scale,
        "t0_y_oc_l": y_oc_l,
        "t0_y_oc_u": y_oc_u,
        "y_τc": y_τc,
        "y_τc_ridge": y_τc_ridge,
        "y_τc_source": y_τc_source,
        "remaining_oc": round(float(rem), 6) if rem is not None else None,
        "r_hat": round(float(r_hat), 6) if r_hat is not None else None,
        "residual": round(float(r_hat), 6) if r_hat is not None else None,
        "y_trade": sc.get("y_trade"),
        "y_nowcast": sc.get("y_nowcast"),
        "trade_vs": None,
        "nowcast_vs": None,
        "n_heads": n_src,
    }


def estimate_close_px(
    scores: Optional[dict],
    *,
    open_px: float,
    prev_close: Optional[float] = None,
    price_tau: Optional[float] = None,
    cfg: Optional[dict] = None,
) -> Dict[str, Any]:
    """C_τ := O×(1+clip(ŷ_oc×scale, y_oc_l, y_oc_u)/100)。缺 ŷ_oc → 不可估。

    扫描传入**该根前缀**因果 ŷ_oc。破带比较 price = **本根 5m 收价 C**。
    ``price_tau`` 用于 R̂_τ / ŷ_τc 对照字段，不改 C_τ。
    R̂_τ = Ĉ_τ/price(τ)−1（clip 后目标）；remaining_oc 为未 clip 的 remaining(ŷ_oc)。
    ``cfg`` 提供 scale / y_oc_l / y_oc_u（默认 10 / −3 / +3）。
    """
    parts = scores_close_components(
        scores,
        open_px=open_px,
        prev_close=prev_close,
        price_tau=price_tau,
        cfg=cfg,
    )
    c_tau = parts.get("c_tau")
    n_heads = int(parts.get("n_heads") or 0)
    if c_tau is None:
        return {**parts, "close_px": None, "n_sources": 0, "ok": False}
    return {
        **parts,
        "close_px": round(float(c_tau), 4),
        "n_sources": max(1, n_heads),
        "ok": True,
    }


def band_delta_px(open_px: float, delta_pct: float) -> Optional[float]:
    try:
        o = float(open_px)
        d = float(delta_pct)
    except (TypeError, ValueError):
        return None
    if o <= 0 or d < 0:
        return None
    return round(o * (d / 100.0), 4)


def band_decision(
    bar_close: float,
    close_px: float,
    delta_px: float,
) -> Optional[str]:
    """收价破带 → 方向；带内 → None。

    高于 C_τ+δ_px → sell_then_buy（反T）；低于 C_τ−δ_px → buy_then_sell（正T）。
    生产路径用 ``close_band_pick_direction``（相对 C_τ 的 ±δ% 乘法带）。
    """
    try:
        c = float(bar_close)
        mid = float(close_px)
        d = float(delta_px)
    except (TypeError, ValueError):
        return None
    if c <= 0 or mid <= 0 or d < 0:
        return None
    if c > mid + d:
        return "sell_then_buy"
    if c < mid - d:
        return "buy_then_sell"
    return None


def band_decision_by_excess_pct(
    bar_close: float,
    close_px: float,
    upper_pct: float,
    lower_pct: float,
) -> Optional[str]:
    """用 r=(C/C_τ−1)×100 相对 ±δ 选腿；``bar_close``=本根 5m 收价 C。"""
    try:
        p = float(bar_close)
        mid = float(close_px)
        up = float(upper_pct)
        lo = float(lower_pct)
    except (TypeError, ValueError):
        return None
    if p <= 0 or mid <= 0:
        return None
    r = (p / mid - 1.0) * 100.0
    if r > up:
        return "sell_then_buy"
    if r < lo:
        return "buy_then_sell"
    return None


def close_band_pick_direction(
    bar_close: float,
    close_px: float,
    delta_pct: float,
    scores: Optional[dict] = None,
    cfg: Optional[dict] = None,
) -> tuple[Optional[str], Dict[str, Any]]:
    """破带选腿：C 相对 C_τ 的 ±δ（乘法带）。

    ``bar_close``：**当前 5m 根收价 C**（分钟 ``mb.close``），不是日线收盘价。
    ``close_px``：分钟价空间下的 C_τ。
    ŷ_τ30/60/90 旁路在选向之后。

    Returns (direction, meta) where meta 含 r_pct / upper_pct / lower_pct / y_tau。
    """
    from core.t0.score_policy import resolve_direction_y_tau, scores_from_item

    d = max(0.0, float(delta_pct))
    upper, lower = d, -d
    direction = band_decision_by_excess_pct(bar_close, close_px, upper, lower)
    raw = scores if isinstance(scores, dict) else {}
    sc = scores_from_item(raw)
    y_tau = resolve_direction_y_tau(sc)
    scale, y_oc_l, y_oc_u = resolve_y_oc_target_params(cfg)
    r_pct = None
    try:
        p, mid = float(bar_close), float(close_px)
        if p > 0 and mid > 0:
            r_pct = (p / mid - 1.0) * 100.0
    except (TypeError, ValueError):
        r_pct = None
    lo_px, up_px = close_band_edges_px(close_px, d)
    return direction, {
        "y_tau": y_tau,
        "r_pct": r_pct,
        "upper_pct": upper,
        "lower_pct": lower,
        "delta_pct": d,
        "lower_px": lo_px,
        "upper_px": up_px,
        "t0_y_oc_target_scale": scale,
        "t0_y_oc_l": y_oc_l,
        "t0_y_oc_u": y_oc_u,
    }


# |日线开/分钟开 − 1| 超此百分比 → price_space_mismatch（可配）
DEFAULT_PRICE_SPACE_MAX_DEV_PCT = 5.0


def price_space_scale(
    daily_open: Optional[float],
    minute_open: Optional[float],
) -> Optional[float]:
    """S = O_d / O_m；缺一侧或非正 → None。"""
    od, om = _f(daily_open), _f(minute_open)
    if od is None or om is None or od <= 0 or om <= 0:
        return None
    return float(od) / float(om)


def resolve_t0_price_space(
    bar: Optional[dict],
    minute_bars: Sequence[dict] = (),
    cfg: Optional[dict] = None,
    *,
    daily_bar: Optional[dict] = None,
) -> Dict[str, Any]:
    """做 T 价空间：日线训/估 C_τ，分钟价用 S 映回。

    - ``minute_open`` / ``minute_prev``：执行锚（分钟首开 / 附带昨收）
    - ``daily_open`` / ``daily_prev``：日线 bar 的开/昨收（估 C_τ 优先）
    - ``daily_bar``：可选，显式原始日 K（勿传 ``_day_ohlc_from_minutes`` 合成结果）
    - ``scale`` = O_d/O_m；无日线开则 1.0（纯分钟回退）
    - ``skip_reason``：|O_d/O_m−1| 或 |P_d/P_m−1| 超阈（P=昨收）

    估 C_τ 用 ``estimate_open`` / ``estimate_prev``（有日线则日线）；
    破带/触价用分钟：``close_px_m = C_τ_d / scale``。
    """
    cfg_d = cfg if isinstance(cfg, dict) else {}
    # 分钟锚：只用 minute_bars；日线锚：优先显式 daily_bar，避免被分钟合成 OHLC 污染
    day_src = daily_bar if isinstance(daily_bar, dict) else bar
    anchors = day_open_prev_close(
        day_src if isinstance(day_src, dict) else None, minute_bars
    )
    om = anchors.get("open_px")
    pm = anchors.get("prev_close")
    # 分钟首开必须来自分钟序列；day_open_prev_close 在缺分钟时会回退日开，这里纠正
    om_m = None
    for b in minute_bars or ():
        if not isinstance(b, dict):
            continue
        o = _f(b.get("open"))
        if o is not None and o > 0:
            om_m = o
            break
    if om_m is not None:
        om = om_m
    od = _f((day_src or {}).get("open")) if isinstance(day_src, dict) else None
    pd = None
    if isinstance(day_src, dict):
        pd = (
            _f(day_src.get("prev_close"))
            or _f(day_src.get("pre_close"))
            or _f(day_src.get("yc"))
        )

    # 日线 bar 的 open 若实为分钟合成（与 O_m 相同），仍视为可比
    scale = price_space_scale(od, om) if od is not None and om is not None else None
    if scale is None:
        scale = 1.0
        od_eff = om
        pd_eff = pm if pm is not None else pd
        estimate_mode = "minute_only"
    else:
        od_eff = od
        pd_eff = pd if pd is not None and float(pd) > 0 else pm
        estimate_mode = "daily_anchor"

    skip: Optional[str] = None
    gate_on = bool(cfg_d.get("t0_price_space_gate", True))
    try:
        max_dev = float(
            cfg_d.get("t0_price_space_max_dev_pct", DEFAULT_PRICE_SPACE_MAX_DEV_PCT)
        )
    except (TypeError, ValueError):
        max_dev = float(DEFAULT_PRICE_SPACE_MAX_DEV_PCT)
    max_dev = max(0.0, min(max_dev, 5.0))

    if gate_on and estimate_mode == "daily_anchor" and om is not None and od is not None:
        dev_pct = abs(float(scale) - 1.0) * 100.0
        if max_dev > 0 and dev_pct > max_dev:
            skip = (
                f"price_space_mismatch：|O_d/O_m−1|={dev_pct:.3f}% "
                f"> {max_dev:g}%（O_d={od} O_m={om}）"
            )
        elif (
            skip is None
            and pd is not None
            and pm is not None
            and float(pd) > 0
            and float(pm) > 0
        ):
            # 与开盘差对齐：|日昨/分昨−1|
            try:
                prev_tol = float(
                    cfg_d.get("t0_price_space_prev_dev_pct", max_dev)
                )
            except (TypeError, ValueError):
                prev_tol = max_dev
            prev_tol = max(0.0, min(prev_tol, 5.0))
            s_prev = float(pd) / float(pm)
            prev_dev_pct = abs(s_prev - 1.0) * 100.0
            if prev_tol > 0 and prev_dev_pct > prev_tol:
                skip = (
                    f"price_space_mismatch：|P_d/P_m−1|={prev_dev_pct:.3f}% "
                    f"> {prev_tol:g}%（P_d={pd} P_m={pm}）"
                )

    return {
        "minute_open": om,
        "minute_prev": pm,
        "daily_open": od,
        "daily_prev": pd,
        "scale": float(scale) if scale is not None else None,
        "estimate_open": od_eff,
        "estimate_prev": pd_eff,
        "estimate_mode": estimate_mode,
        "session_close": anchors.get("session_close"),
        "skip_reason": skip,
        "ok": skip is None and om is not None and float(om or 0) > 0,
    }


def map_close_px_to_minute(
    close_px_daily: float,
    *,
    scale: float,
) -> Optional[float]:
    """C_τ_d → 分钟价空间：C_τ_m = C_τ_d / S = C_τ_d * O_m/O_d。"""
    try:
        c = float(close_px_daily)
        s = float(scale)
    except (TypeError, ValueError):
        return None
    if c <= 0 or s <= 0:
        return None
    return round(c / s, 4)


def map_close_components_to_minute(
    est: Optional[dict],
    *,
    scale: float,
) -> Dict[str, Any]:
    """把日线空间 c_* / C_τ 映到分钟；保留 *_daily 供对账。"""
    raw = est if isinstance(est, dict) else {}
    out: Dict[str, Any] = {
        "close_px_daily": raw.get("close_px"),
        "c_tau_daily": raw.get("c_tau"),
        "c_trade_daily": raw.get("c_trade"),
        "c_nowcast_daily": raw.get("c_nowcast"),
        "price_space_scale": float(scale) if scale else None,
    }
    for src, dst in (
        ("c_tau", "c_tau"),
        ("c_trade", "c_trade"),
        ("c_nowcast", "c_nowcast"),
        ("close_px", "close_px"),
    ):
        v = raw.get(src)
        if v is None:
            out[dst] = None
        else:
            out[dst] = map_close_px_to_minute(float(v), scale=scale)
    return out


def day_price_space_payload(
    space: Optional[dict],
    bar: Optional[dict] = None,
    *,
    daily_bar: Optional[dict] = None,
) -> Dict[str, Any]:
    """日结果 ``price_space``：日/分 open·close + scale。"""
    sp = space if isinstance(space, dict) else {}
    day_src = daily_bar if isinstance(daily_bar, dict) else bar
    daily_close = _f((day_src or {}).get("close")) if isinstance(day_src, dict) else None
    if daily_close is not None and daily_close <= 0:
        daily_close = None
    minute_close = _f(sp.get("session_close"))
    scale_open = _f(sp.get("scale"))
    if scale_open is None:
        scale_open = 1.0
    scale_close = None
    if daily_close is not None and minute_close is not None and float(minute_close) > 0:
        scale_close = round(float(daily_close) / float(minute_close), 6)
    return {
        "daily_open": sp.get("daily_open"),
        "minute_open": sp.get("minute_open"),
        "scale_open": float(scale_open),
        "daily_close": daily_close,
        "minute_close": minute_close,
        "scale_close": scale_close,
        "estimate_mode": sp.get("estimate_mode"),
    }


def close_band_sign_skip_reason(
    scores: Optional[dict],
    cfg: Optional[dict] = None,
) -> Optional[str]:
    """HL 强同 τ：|y_hl|>y_hl_strong 时须与 y_τ 同号，异号则跳过。

    trade/eod 强闸已下线；仅 HL。无 y_hl 时不拦。
    y_hl_strong≤0：任意非零 |y_hl| 都要求同号。
    旧键 y_path_strong 仅作未走 load_t0_rules 的直传兼容。
    """
    from core.research.path_panel import pick_y_hl
    from core.t0.score_policy import (
        DEFAULT_PATH_STRONG,
        _cfg_float,
        _strong_head_tau_sign_gate,
        resolve_direction_y_tau,
        scores_from_item,
    )

    cfg_d = cfg if isinstance(cfg, dict) else {}
    raw = scores if isinstance(scores, dict) else {}
    sc = scores_from_item(raw)
    y_path = pick_y_hl(sc, raw)
    if y_path is None:
        return None
    y_tau = resolve_direction_y_tau(sc)
    if y_tau is None:
        # 强 HL 需要对照 τ；缺 τ 时交给入场闸（τ enter）处理
        return None
    if cfg_d.get("y_hl_strong") not in (None, ""):
        hl_strong = _cfg_float(cfg_d, "y_hl_strong", DEFAULT_PATH_STRONG)
    else:
        hl_strong = _cfg_float(cfg_d, "y_path_strong", DEFAULT_PATH_STRONG)
    hl_strong = max(0.0, min(float(hl_strong), 5.0))
    ok, reason = _strong_head_tau_sign_gate(
        float(y_path),
        float(y_tau),
        hl_strong,
        "y_hl",
        tau_label="y_τ",
    )
    if ok:
        return None
    if reason and reason.startswith("dual_y："):
        return reason[len("dual_y：") :]
    return reason or "强 y_hl 与 y_τ 异号跳过"


Y_TC_BAND_EPS = 0.05  # |ŷ_τc| 低于此视为无投票（与符号命中死区同）
Y_T30_HIT_EPS = 0.0  # 30m |ŷ| 中位约 0.02%，不可套用 τc 的 0.05；与旁路闸「有符号即投票」同口径
Y_T45_HIT_EPS = 0.0
Y_T60_HIT_EPS = 0.0
Y_T75_HIT_EPS = 0.0
Y_T90_HIT_EPS = 0.0
DEFAULT_Y_T30_STRONG = 1.0  # ŷ_τ30 个股旁路已下线：缺键=关
DEFAULT_Y_T30_ENTER = 0.0  # 个股入场已下线：缺键=关
DEFAULT_Y_T45_STRONG = 1.0  # ŷ_τ45 个股旁路已下线：缺键=关
DEFAULT_Y_T45_ENTER = 0.0  # 个股入场已下线：缺键=关
DEFAULT_Y_T60_STRONG = 1.0
DEFAULT_Y_T60_ENTER = 0.0  # 个股入场已下线：缺键=关
DEFAULT_Y_T75_STRONG = 1.0  # ŷ_τ75 个股旁路已下线：缺键=关
DEFAULT_Y_T75_ENTER = 0.0  # 个股入场已下线：缺键=关
DEFAULT_Y_T90_STRONG = 1.0
DEFAULT_Y_T90_ENTER = 0.0  # 个股入场已下线：缺键=关
DEFAULT_Y_TW_STRONG = 5.0  # ŷ_τw 票数旁路：0=任意有符号须同号；>=5=关
DEFAULT_Y_TW_VOTE_MARGIN = 2.0  # |p_up−0.5|≤此百分点不投票


def _horizon_vote_margin_pp(cfg: Optional[dict] = None, margin_pp: Any = None) -> Any:
    if margin_pp not in (None, ""):
        return margin_pp
    cfg_d = cfg if isinstance(cfg, dict) else {}
    raw = cfg_d.get("y_tw_vote_margin")
    if raw in (None, "") and cfg_d.get("y_τw_vote_margin") not in (None, ""):
        raw = cfg_d.get("y_τw_vote_margin")
    return raw if raw not in (None, "") else None


def y_tc_band_agree(
    direction: Optional[str],
    y_τc: Optional[float],
    *,
    eps: float = Y_TC_BAND_EPS,
) -> Optional[bool]:
    """破带方向是否与 ŷ_τc 向 ĉ 回归同向。

    反T（价在 ĉ 上）期望 remaining<0；正T 期望 remaining>0。
    缺方向 / 缺 ŷ_τc / |ŷ_τc|≤eps → None（不投票）。
    """
    d = str(direction or "").strip()
    y = _f(y_τc)
    if d not in ("sell_then_buy", "buy_then_sell") or y is None:
        return None
    try:
        thr = abs(float(eps))
    except (TypeError, ValueError):
        thr = Y_TC_BAND_EPS
    if abs(float(y)) <= thr + 1e-12:
        return None
    if d == "sell_then_buy":
        return float(y) < 0.0
    return float(y) > 0.0


def y_t30_band_agree(
    direction: Optional[str],
    y_τ30: Optional[float],
    *,
    eps: float = Y_T30_HIT_EPS,
) -> Optional[bool]:
    """破带方向是否与 ŷ_τ30=p_up 同向。正T 期望 p_up≥0.5；反T 期望 1−p_up≥0.5。"""
    _ = eps
    from core.research.horizon_prob import horizon_band_agree

    return horizon_band_agree(direction, y_τ30, floor=0.5)


def y_t45_band_agree(
    direction: Optional[str],
    y_τ45: Optional[float],
    *,
    eps: float = Y_T45_HIT_EPS,
) -> Optional[bool]:
    """破带方向是否与 ŷ_τ45=p_up 同向。"""
    _ = eps
    from core.research.horizon_prob import horizon_band_agree

    return horizon_band_agree(direction, y_τ45, floor=0.5)


def y_t60_band_agree(
    direction: Optional[str],
    y_τ60: Optional[float],
    *,
    eps: float = Y_T60_HIT_EPS,
) -> Optional[bool]:
    """破带方向是否与 ŷ_τ60=p_up 同向。"""
    _ = eps
    from core.research.horizon_prob import horizon_band_agree

    return horizon_band_agree(direction, y_τ60, floor=0.5)


def y_t75_band_agree(
    direction: Optional[str],
    y_τ75: Optional[float],
    *,
    eps: float = Y_T75_HIT_EPS,
) -> Optional[bool]:
    """破带方向是否与 ŷ_τ75=p_up 同向。"""
    _ = eps
    from core.research.horizon_prob import horizon_band_agree

    return horizon_band_agree(direction, y_τ75, floor=0.5)


def y_t90_band_agree(
    direction: Optional[str],
    y_τ90: Optional[float],
    *,
    eps: float = Y_T90_HIT_EPS,
) -> Optional[bool]:
    """破带方向是否与 ŷ_τ90=p_up 同向。"""
    _ = eps
    from core.research.horizon_prob import horizon_band_agree

    return horizon_band_agree(direction, y_τ90, floor=0.5)


def close_band_y_tc_skip_reason(
    scores: Optional[dict],
    cfg: Optional[dict] = None,
    *,
    direction: Optional[str] = None,
    y_τc: Optional[float] = None,
) -> Optional[str]:
    """已下线：ŷ_τc 旁路由 ŷ_τ30/60/90 覆盖，恒不跳过。"""
    _ = (scores, cfg, direction, y_τc)
    return None


def _pick_y_t30_for_band(
    scores: Optional[dict],
    y_τ30: Optional[float] = None,
) -> Optional[float]:
    y = _f(y_τ30)
    if y is not None:
        return y
    raw = scores if isinstance(scores, dict) else {}
    try:
        from core.research.t30_ridge import pick_y_t30_hat

        y = pick_y_t30_hat(raw)
        if y is not None:
            return y
    except Exception:
        pass
    return _f(raw.get("y_τ30")) or _f(raw.get("y_t30")) or _f(raw.get("y_t30_hat"))


def _horizon_p_agree_skip_reason(
    *,
    y: Optional[float],
    direction: str,
    strong: float,
    label: str,
) -> Optional[str]:
    """strong 0=开（p_agree<0.5 跳过），1=关。缺分不拦。"""
    from core.research.horizon_prob import p_agree

    if y is None:
        return None
    strong_n = max(0.0, min(float(strong), 1.0))
    if strong_n >= 1.0 - 1e-12:
        return None
    pa = p_agree(direction, y)
    if pa is None:
        return None
    if float(pa) + 1e-12 >= 0.5:
        return None
    side = "反T" if direction == "sell_then_buy" else "正T"
    return f"{label}={float(y):.3f} p_agree={float(pa):.3f}<0.5 与{side}旁路逆带跳过"


def close_band_y_t30_skip_reason(
    scores: Optional[dict],
    cfg: Optional[dict] = None,
    *,
    direction: Optional[str] = None,
    y_τ30: Optional[float] = None,
) -> Optional[str]:
    """旁路：破带后 ŷ_τ30=p_up 的 p_agree 须 ≥0.5（正T=p_up，反T=1−p_up）。

    不改 C_τ / 选向 / 目标价。生产 ``load_t0_rules`` 丢 ``y_t30_strong``（缺键默认关）。
    个股入场 ``y_t30_enter`` 已下线（``load_t0_rules`` 丢弃；缺键=关）。缺 ŷ_τ30 不拦。
    """
    from core.t0.score_policy import _cfg_float

    cfg_d = cfg if isinstance(cfg, dict) else {}
    d = str(direction or "").strip()
    if d not in ("sell_then_buy", "buy_then_sell"):
        return None
    y = _pick_y_t30_for_band(scores, y_τ30)
    strong = _cfg_float(cfg_d, "y_t30_strong", DEFAULT_Y_T30_STRONG)
    if cfg_d.get("y_τ30_strong") not in (None, "") and cfg_d.get("y_t30_strong") in (
        None,
        "",
    ):
        strong = _cfg_float(cfg_d, "y_τ30_strong", DEFAULT_Y_T30_STRONG)
    return _horizon_p_agree_skip_reason(y=y, direction=d, strong=strong, label="ŷ_τ30")


def _pick_y_t45_for_band(
    scores: Optional[dict],
    y_τ45: Optional[float] = None,
) -> Optional[float]:
    y = _f(y_τ45)
    if y is not None:
        return y
    raw = scores if isinstance(scores, dict) else {}
    try:
        from core.research.t45_ridge import pick_y_t45_hat

        y = pick_y_t45_hat(raw)
        if y is not None:
            return y
    except Exception:
        pass
    return _f(raw.get("y_τ45")) or _f(raw.get("y_t45")) or _f(raw.get("y_t45_hat"))


def close_band_y_t45_skip_reason(
    scores: Optional[dict],
    cfg: Optional[dict] = None,
    *,
    direction: Optional[str] = None,
    y_τ45: Optional[float] = None,
) -> Optional[str]:
    """旁路：破带后 ŷ_τ45=p_up 的 p_agree 须 ≥0.5。``y_t45_strong>=1`` 关闸。缺分不拦。"""
    from core.t0.score_policy import _cfg_float

    cfg_d = cfg if isinstance(cfg, dict) else {}
    d = str(direction or "").strip()
    if d not in ("sell_then_buy", "buy_then_sell"):
        return None
    y = _pick_y_t45_for_band(scores, y_τ45)
    strong = _cfg_float(cfg_d, "y_t45_strong", DEFAULT_Y_T45_STRONG)
    if cfg_d.get("y_τ45_strong") not in (None, "") and cfg_d.get("y_t45_strong") in (
        None,
        "",
    ):
        strong = _cfg_float(cfg_d, "y_τ45_strong", DEFAULT_Y_T45_STRONG)
    return _horizon_p_agree_skip_reason(y=y, direction=d, strong=strong, label="ŷ_τ45")


def _pick_y_t60_for_band(
    scores: Optional[dict],
    y_τ60: Optional[float] = None,
) -> Optional[float]:
    y = _f(y_τ60)
    if y is not None:
        return y
    raw = scores if isinstance(scores, dict) else {}
    try:
        from core.research.t60_ridge import pick_y_t60_hat

        y = pick_y_t60_hat(raw)
        if y is not None:
            return y
    except Exception:
        pass
    return _f(raw.get("y_τ60")) or _f(raw.get("y_t60")) or _f(raw.get("y_t60_hat"))


def close_band_y_t60_skip_reason(
    scores: Optional[dict],
    cfg: Optional[dict] = None,
    *,
    direction: Optional[str] = None,
    y_τ60: Optional[float] = None,
) -> Optional[str]:
    """旁路：破带后 ŷ_τ60=p_up 的 p_agree 须 ≥0.5。``y_t60_strong>=1`` 关闸。缺分不拦。"""
    from core.t0.score_policy import _cfg_float

    cfg_d = cfg if isinstance(cfg, dict) else {}
    d = str(direction or "").strip()
    if d not in ("sell_then_buy", "buy_then_sell"):
        return None
    y = _pick_y_t60_for_band(scores, y_τ60)
    strong = _cfg_float(cfg_d, "y_t60_strong", DEFAULT_Y_T60_STRONG)
    if cfg_d.get("y_τ60_strong") not in (None, "") and cfg_d.get("y_t60_strong") in (
        None,
        "",
    ):
        strong = _cfg_float(cfg_d, "y_τ60_strong", DEFAULT_Y_T60_STRONG)
    return _horizon_p_agree_skip_reason(y=y, direction=d, strong=strong, label="ŷ_τ60")


def _pick_y_t75_for_band(
    scores: Optional[dict],
    y_τ75: Optional[float] = None,
) -> Optional[float]:
    y = _f(y_τ75)
    if y is not None:
        return y
    raw = scores if isinstance(scores, dict) else {}
    try:
        from core.research.t75_ridge import pick_y_t75_hat

        y = pick_y_t75_hat(raw)
        if y is not None:
            return y
    except Exception:
        pass
    return _f(raw.get("y_τ75")) or _f(raw.get("y_t75")) or _f(raw.get("y_t75_hat"))


def close_band_y_t75_skip_reason(
    scores: Optional[dict],
    cfg: Optional[dict] = None,
    *,
    direction: Optional[str] = None,
    y_τ75: Optional[float] = None,
) -> Optional[str]:
    """旁路：破带后 ŷ_τ75=p_up 的 p_agree 须 ≥0.5。``y_t75_strong>=1`` 关闸。缺分不拦。"""
    from core.t0.score_policy import _cfg_float

    cfg_d = cfg if isinstance(cfg, dict) else {}
    d = str(direction or "").strip()
    if d not in ("sell_then_buy", "buy_then_sell"):
        return None
    y = _pick_y_t75_for_band(scores, y_τ75)
    strong = _cfg_float(cfg_d, "y_t75_strong", DEFAULT_Y_T75_STRONG)
    if cfg_d.get("y_τ75_strong") not in (None, "") and cfg_d.get("y_t75_strong") in (
        None,
        "",
    ):
        strong = _cfg_float(cfg_d, "y_τ75_strong", DEFAULT_Y_T75_STRONG)
    return _horizon_p_agree_skip_reason(y=y, direction=d, strong=strong, label="ŷ_τ75")


def _pick_y_t90_for_band(
    scores: Optional[dict],
    y_τ90: Optional[float] = None,
) -> Optional[float]:
    y = _f(y_τ90)
    if y is not None:
        return y
    raw = scores if isinstance(scores, dict) else {}
    try:
        from core.research.t90_ridge import pick_y_t90_hat

        y = pick_y_t90_hat(raw)
        if y is not None:
            return y
    except Exception:
        pass
    return _f(raw.get("y_τ90")) or _f(raw.get("y_t90")) or _f(raw.get("y_t90_hat"))


def close_band_y_t90_skip_reason(
    scores: Optional[dict],
    cfg: Optional[dict] = None,
    *,
    direction: Optional[str] = None,
    y_τ90: Optional[float] = None,
) -> Optional[str]:
    """旁路：破带后 ŷ_τ90=p_up 的 p_agree 须 ≥0.5。``y_t90_strong>=1`` 关闸。缺分不拦。"""
    from core.t0.score_policy import _cfg_float

    cfg_d = cfg if isinstance(cfg, dict) else {}
    d = str(direction or "").strip()
    if d not in ("sell_then_buy", "buy_then_sell"):
        return None
    y = _pick_y_t90_for_band(scores, y_τ90)
    strong = _cfg_float(cfg_d, "y_t90_strong", DEFAULT_Y_T90_STRONG)
    if cfg_d.get("y_τ90_strong") not in (None, "") and cfg_d.get("y_t90_strong") in (
        None,
        "",
    ):
        strong = _cfg_float(cfg_d, "y_τ90_strong", DEFAULT_Y_T90_STRONG)
    return _horizon_p_agree_skip_reason(y=y, direction=d, strong=strong, label="ŷ_τ90")


def y_tw_sign(x: Any, *, margin_pp: Any = None) -> Optional[int]:
    """ŷ_τw 票：sign(p_up−0.5)。|p−0.5|≤margin_pp（默认 2pp）/ 缺分不投票。"""
    from core.research.horizon_prob import p_up_vote

    return p_up_vote(x, margin_pp=margin_pp)


def blend_y_tw(
    y_τ30: Any = None,
    y_τ60: Any = None,
    y_τ90: Any = None,
    y_τ45: Any = None,
    y_τ75: Any = None,
    *,
    margin_pp: Any = None,
    cfg: Optional[dict] = None,
) -> Optional[float]:
    """ŷ_τw = f(ŷ_τ30)+…+f(ŷ_τ90)；f=sign(p_up−0.5)；|p−0.5|≤y_tw_vote_margin / 缺头跳过该项。"""
    band = _horizon_vote_margin_pp(cfg, margin_pp)
    vals = [
        s
        for s in (
            y_tw_sign(y_τ30, margin_pp=band),
            y_tw_sign(y_τ45, margin_pp=band),
            y_tw_sign(y_τ60, margin_pp=band),
            y_tw_sign(y_τ75, margin_pp=band),
            y_tw_sign(y_τ90, margin_pp=band),
        )
        if s is not None
    ]
    if not vals:
        return None
    return float(sum(vals))


def close_band_y_tw_skip_reason(
    scores: Optional[dict],
    cfg: Optional[dict] = None,
    *,
    direction: Optional[str] = None,
) -> Optional[str]:
    """旁路：破带后 ŷ_τw 票数须与方向同号（反T<0，正T>0）。

    不改 C_τ / 选向 / 目标价。``y_tw_strong>=5`` 关闸；默认 5=关。
    |ŷ_τw| **大于** strong 且逆带则跳过（``|ŷ_τw|<=strong`` 不拦）。缺全部分不拦。
    """
    from core.t0.score_policy import _cfg_float

    cfg_d = cfg if isinstance(cfg, dict) else {}
    d = str(direction or "").strip()
    if d not in ("sell_then_buy", "buy_then_sell"):
        return None
    y = blend_y_tw(
        _pick_y_t30_for_band(scores),
        _pick_y_t60_for_band(scores),
        _pick_y_t90_for_band(scores),
        _pick_y_t45_for_band(scores),
        _pick_y_t75_for_band(scores),
        cfg=cfg_d,
    )
    if y is None:
        return None
    strong = _cfg_float(cfg_d, "y_tw_strong", DEFAULT_Y_TW_STRONG)
    if cfg_d.get("y_τw_strong") not in (None, "") and cfg_d.get("y_tw_strong") in (
        None,
        "",
    ):
        strong = _cfg_float(cfg_d, "y_τw_strong", DEFAULT_Y_TW_STRONG)
    strong = max(0.0, min(float(strong), 5.0))
    if strong >= 5.0 - 1e-12:
        return None
    if abs(float(y)) <= float(strong) + 1e-12:
        return None
    agree = y_tc_band_agree(d, y, eps=0.0)
    if agree is not False:
        return None
    side = "反T" if d == "sell_then_buy" else "正T"
    want = "ŷ_τw<0" if d == "sell_then_buy" else "ŷ_τw>0"
    return f"ŷ_τw={int(y):+d} 与{side}旁路逆带跳过（期望{want}）"


def _enter_profile_skip_reason(
    *,
    y_tau: Optional[float],
    y_path: Optional[float],
    y_t30: Optional[float] = None,
    y_t45: Optional[float] = None,
    y_t60: Optional[float] = None,
    y_t75: Optional[float] = None,
    y_t90: Optional[float] = None,
    y_complexity_u: Optional[float],
    y_tpd_u: Optional[float],
    tau_enter: float,
    path_enter: float,
    t30_enter: float = 0.0,
    t45_enter: float = 0.0,
    t60_enter: float = 0.0,
    t75_enter: float = 0.0,
    t90_enter: float = 0.0,
    cx_max: float,
    tpd_max: float,
    use_path: bool,
    direction: Optional[str] = None,
) -> Optional[str]:
    """一组 |y_τ| / |y_hl| 入场。ŷ_τ* 个股入场仅供显式 cfg 单测；ŷ_cx / tpd 已下线。缺方向时窗口入场不拦。"""
    from core.research.horizon_prob import HORIZON_ENTER_MAX, p_agree

    if tau_enter > 0:
        if y_tau is None:
            return "y_τ 缺失，未过入场门槛"
        yt = float(y_tau)
        if abs(yt) < tau_enter - 1e-12:
            return f"|y_τ|={abs(yt):.3f}%<{tau_enter:g}% 未过入场（横盘）"

    def _p_enter(label: str, y: Optional[float], floor: float) -> Optional[str]:
        fl = max(0.0, min(float(floor), float(HORIZON_ENTER_MAX)))
        if fl <= 0 or y is None:
            return None
        pa = p_agree(direction, y)
        if pa is None:
            return None
        if float(pa) < fl - 1e-12:
            return f"p_agree({label})={float(pa):.3f}<{fl:g} 未过入场"
        return None

    for label, y, fl in (
        ("ŷ_τ30", y_t30, t30_enter),
        ("ŷ_τ45", y_t45, t45_enter),
        ("ŷ_τ60", y_t60, t60_enter),
        ("ŷ_τ75", y_t75, t75_enter),
        ("ŷ_τ90", y_t90, t90_enter),
    ):
        msg = _p_enter(label, y, fl)
        if msg:
            return msg
    if use_path and y_path is not None and path_enter > 0:
        yp = float(y_path)
        if abs(yp) < path_enter - 1e-12:
            return f"|y_hl|={abs(yp):.3f}%<{path_enter:g}% 未过入场（横盘）"
    _ = (y_complexity_u, y_tpd_u, cx_max, tpd_max)
    return None


def _cfg_or_follow(
    cfg_d: dict,
    key: str,
    follow: float,
    *,
    lo: float,
    hi: float,
) -> float:
    from core.t0.score_policy import _cfg_float

    if cfg_d.get(key) in (None, ""):
        return max(lo, min(float(follow), hi))
    return max(lo, min(float(_cfg_float(cfg_d, key, follow)), hi))


def close_band_enter_skip_reason(
    scores: Optional[dict],
    cfg: Optional[dict] = None,
    *,
    direction: Optional[str] = None,
    r_pct: Optional[float] = None,
) -> Optional[str]:
    """入场：|y_τ| / |y_hl| 档已下线。生产 ``load_t0_rules`` 丢门槛1/2 键。

    ``r_pct`` 仅兼容旧调用，不再入闸（超额带宽 δ 已选向）。
    显式 cfg 仍可供单测：``y_enter_enabled`` / ``y_enter_alt_enabled`` 关则该档不参与 OR；两档都关则不开腿。
    缺 y_hl 默认不拦；``y_hl_required`` 时缺分跳过（开盘 minute_feats_missing 除外）。
    分钟缺失共用。ŷ_cx / ŷ_tpd 已下线，不入闸。
    """
    _ = r_pct
    from core.t0.score_policy import (
        DEFAULT_PATH_ENTER,
        DEFAULT_TAU_ENTER,
        _cfg_float,
        resolve_direction_y_tau,
        scores_from_item,
        side_hl_enter,
        side_tau_enter,
    )
    from core.t0.config import coerce_cfg_bool

    raw = scores if isinstance(scores, dict) else {}
    sc = scores_from_item(raw)
    cfg_d = cfg if isinstance(cfg, dict) else {}
    d = str(direction or "").strip()
    if d in ("sell_then_buy", "buy_then_sell"):
        for_bts = d == "buy_then_sell"
        tau_enter = float(side_tau_enter(cfg_d, for_buy_then_sell=for_bts))
        path_enter = float(side_hl_enter(cfg_d, for_buy_then_sell=for_bts))
    else:
        tau_enter = _cfg_float(cfg_d, "y_tau_enter", DEFAULT_TAU_ENTER)
        tau_enter = max(0.0, min(float(tau_enter), 100.0))
        if cfg_d.get("y_hl_enter") not in (None, ""):
            path_enter = _cfg_float(cfg_d, "y_hl_enter", DEFAULT_PATH_ENTER)
        else:
            path_enter = _cfg_float(cfg_d, "y_path_enter", DEFAULT_PATH_ENTER)
        path_enter = max(0.0, min(float(path_enter), 100.0))

    y_tau = resolve_direction_y_tau(sc)
    y_t30 = _pick_y_t30_for_band(raw)
    t30_enter = _cfg_float(cfg_d, "y_t30_enter", DEFAULT_Y_T30_ENTER)
    if cfg_d.get("y_τ30_enter") not in (None, "") and cfg_d.get("y_t30_enter") in (
        None,
        "",
    ):
        t30_enter = _cfg_float(cfg_d, "y_τ30_enter", DEFAULT_Y_T30_ENTER)
    t30_enter = max(0.0, min(float(t30_enter), 0.7))
    y_t45 = _pick_y_t45_for_band(raw)
    t45_enter = _cfg_float(cfg_d, "y_t45_enter", DEFAULT_Y_T45_ENTER)
    if cfg_d.get("y_τ45_enter") not in (None, "") and cfg_d.get("y_t45_enter") in (
        None,
        "",
    ):
        t45_enter = _cfg_float(cfg_d, "y_τ45_enter", DEFAULT_Y_T45_ENTER)
    t45_enter = max(0.0, min(float(t45_enter), 0.7))
    y_t60 = _pick_y_t60_for_band(raw)
    t60_enter = _cfg_float(cfg_d, "y_t60_enter", DEFAULT_Y_T60_ENTER)
    if cfg_d.get("y_τ60_enter") not in (None, "") and cfg_d.get("y_t60_enter") in (
        None,
        "",
    ):
        t60_enter = _cfg_float(cfg_d, "y_τ60_enter", DEFAULT_Y_T60_ENTER)
    t60_enter = max(0.0, min(float(t60_enter), 0.7))
    y_t75 = _pick_y_t75_for_band(raw)
    t75_enter = _cfg_float(cfg_d, "y_t75_enter", DEFAULT_Y_T75_ENTER)
    if cfg_d.get("y_τ75_enter") not in (None, "") and cfg_d.get("y_t75_enter") in (
        None,
        "",
    ):
        t75_enter = _cfg_float(cfg_d, "y_τ75_enter", DEFAULT_Y_T75_ENTER)
    t75_enter = max(0.0, min(float(t75_enter), 0.7))
    y_t90 = _pick_y_t90_for_band(raw)
    t90_enter = _cfg_float(cfg_d, "y_t90_enter", DEFAULT_Y_T90_ENTER)
    if cfg_d.get("y_τ90_enter") not in (None, "") and cfg_d.get("y_t90_enter") in (
        None,
        "",
    ):
        t90_enter = _cfg_float(cfg_d, "y_τ90_enter", DEFAULT_Y_T90_ENTER)
    t90_enter = max(0.0, min(float(t90_enter), 0.7))

    from core.research.path_panel import pick_y_hl, pick_y_hl_status

    status = pick_y_hl_status(sc, raw)
    if raw.get("_minute_data_missing") or status == "minute_data_missing":
        return "分钟数据缺失（非 09:30 须有分钟小包）"

    y_path = pick_y_hl(sc, raw)
    hl_required = coerce_cfg_bool(cfg_d.get("y_hl_required"), False)
    if not hl_required and cfg_d.get("y_path_required") not in (None, ""):
        hl_required = coerce_cfg_bool(cfg_d.get("y_path_required"), False)
    if hl_required and y_path is None and status != "minute_feats_missing":
        return "y_hl 缺失，未过入场门槛"

    gate1_on = coerce_cfg_bool(cfg_d.get("y_enter_enabled"), True)
    gate2_on = coerce_cfg_bool(cfg_d.get("y_enter_alt_enabled"), True)

    skip = None
    if gate1_on:
        skip = _enter_profile_skip_reason(
            y_tau=y_tau,
            y_path=y_path,
            y_t30=y_t30,
            y_t45=y_t45,
            y_t60=y_t60,
            y_t75=y_t75,
            y_t90=y_t90,
            y_complexity_u=None,
            y_tpd_u=None,
            tau_enter=tau_enter,
            path_enter=path_enter,
            t30_enter=t30_enter,
            t45_enter=t45_enter,
            t60_enter=t60_enter,
            t75_enter=t75_enter,
            t90_enter=t90_enter,
            cx_max=1.0,
            tpd_max=1.0,
            use_path=True,
            direction=d,
        )
        if skip is None:
            return None

    skip_alt = None
    if gate2_on:
        tau_enter_alt = _cfg_or_follow(
            cfg_d, "y_tau_enter_alt", tau_enter, lo=0.0, hi=100.0
        )
        path_enter_alt = _cfg_or_follow(
            cfg_d, "y_hl_enter_alt", path_enter, lo=0.0, hi=100.0
        )
        if cfg_d.get("y_hl_enter_alt") in (None, "") and cfg_d.get(
            "y_path_enter_alt"
        ) not in (None, ""):
            path_enter_alt = _cfg_or_follow(
                cfg_d, "y_path_enter_alt", path_enter, lo=0.0, hi=100.0
            )
        t30_enter_alt = _cfg_or_follow(
            cfg_d, "y_t30_enter_alt", t30_enter, lo=0.0, hi=100.0
        )
        if cfg_d.get("y_τ30_enter_alt") not in (None, "") and cfg_d.get(
            "y_t30_enter_alt"
        ) in (None, ""):
            t30_enter_alt = _cfg_or_follow(
                cfg_d, "y_τ30_enter_alt", t30_enter, lo=0.0, hi=100.0
            )
        t45_enter_alt = _cfg_or_follow(
            cfg_d, "y_t45_enter_alt", t45_enter, lo=0.0, hi=100.0
        )
        if cfg_d.get("y_τ45_enter_alt") not in (None, "") and cfg_d.get(
            "y_t45_enter_alt"
        ) in (None, ""):
            t45_enter_alt = _cfg_or_follow(
                cfg_d, "y_τ45_enter_alt", t45_enter, lo=0.0, hi=100.0
            )
        t60_enter_alt = _cfg_or_follow(
            cfg_d, "y_t60_enter_alt", t60_enter, lo=0.0, hi=100.0
        )
        if cfg_d.get("y_τ60_enter_alt") not in (None, "") and cfg_d.get(
            "y_t60_enter_alt"
        ) in (None, ""):
            t60_enter_alt = _cfg_or_follow(
                cfg_d, "y_τ60_enter_alt", t60_enter, lo=0.0, hi=100.0
            )
        t75_enter_alt = _cfg_or_follow(
            cfg_d, "y_t75_enter_alt", t75_enter, lo=0.0, hi=100.0
        )
        if cfg_d.get("y_τ75_enter_alt") not in (None, "") and cfg_d.get(
            "y_t75_enter_alt"
        ) in (None, ""):
            t75_enter_alt = _cfg_or_follow(
                cfg_d, "y_τ75_enter_alt", t75_enter, lo=0.0, hi=100.0
            )
        t90_enter_alt = _cfg_or_follow(
            cfg_d, "y_t90_enter_alt", t90_enter, lo=0.0, hi=100.0
        )
        if cfg_d.get("y_τ90_enter_alt") not in (None, "") and cfg_d.get(
            "y_t90_enter_alt"
        ) in (None, ""):
            t90_enter_alt = _cfg_or_follow(
                cfg_d, "y_τ90_enter_alt", t90_enter, lo=0.0, hi=100.0
            )
        skip_alt = _enter_profile_skip_reason(
            y_tau=y_tau,
            y_path=y_path,
            y_t30=y_t30,
            y_t45=y_t45,
            y_t60=y_t60,
            y_t75=y_t75,
            y_t90=y_t90,
            y_complexity_u=None,
            y_tpd_u=None,
            tau_enter=tau_enter_alt,
            path_enter=path_enter_alt,
            t30_enter=t30_enter_alt,
            t45_enter=t45_enter_alt,
            t60_enter=t60_enter_alt,
            t75_enter=t75_enter_alt,
            t90_enter=t90_enter_alt,
            cx_max=1.0,
            tpd_max=1.0,
            use_path=True,
            direction=d,
        )
        if skip_alt is None:
            return None

    if not gate1_on and not gate2_on:
        return "门槛1/2 均未启用"
    if gate1_on and gate2_on:
        if skip_alt == skip:
            return skip
        return f"门槛1 {skip}；门槛2 {skip_alt}"
    if gate1_on:
        return skip
    return skip_alt


@dataclass
class FrozenRound:
    """leg1 成交瞬间冻结的本轮参数。"""

    direction: str
    leg1_px: float
    leg2_target: float
    close_px: float
    delta_px: float
    ratio: float
    bar_index: int
    hm: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def freeze_round(
    *,
    direction: str,
    leg1_px: float,
    close_px: float,
    delta_px: float,
    ratio: float,
    bar_index: int,
    hm: str = "",
) -> FrozenRound:
    """冻结 leg2：目标交易价 = C_τ。"""
    d = str(direction or "").strip().lower()
    mid = float(close_px)
    band = float(delta_px)
    return FrozenRound(
        direction=d,
        leg1_px=float(leg1_px),
        leg2_target=round(mid, 4),
        close_px=round(mid, 4),
        delta_px=round(band, 4),
        ratio=float(ratio),
        bar_index=int(bar_index),
        hm=str(hm or ""),
    )


def parse_bar_hm(mb: dict) -> str:
    dt = str((mb or {}).get("datetime") or (mb or {}).get("date") or "").strip()
    # "YYYY-MM-DD HH:MM[:SS]"
    if len(dt) >= 16 and dt[10] in (" ", "T") and dt[13] == ":":
        return dt[11:16]
    colon = dt.find(":")
    if colon >= 2:
        head = dt[max(0, colon - 2) : colon + 3]
        if len(head) >= 5 and head[2] == ":":
            return head[:5]
    digits = "".join(ch for ch in dt if ch.isdigit())
    if len(digits) >= 12:
        # YYYYMMDDHHMM…
        return f"{digits[8:10]}:{digits[10:12]}"
    if len(digits) >= 4:
        return f"{digits[-4:-2]}:{digits[-2:]}"
    return ""


def hm_allows_leg1(hm: str, last_hm: str = "11:00") -> bool:
    clock = str(hm or "").strip()[:5]
    last = str(last_hm or "11:00").strip()[:5]
    if not clock:
        return True
    if not last:
        return True
    return clock <= last


def day_open_prev_close(bar: Optional[dict], minute_bars: Sequence[dict] = ()) -> Dict[str, Optional[float]]:
    """做 T 锚点：与 band 同源，优先分钟价空间。

    - ``open_px``：分钟**首根** open（缺才回退日线 open）——τ / δ 锚
    - ``session_close``：分钟**末根** close（缺才回退日线 close）——「收」口径；**不**直接当 Ĉ
    - ``prev_close``：优先分钟 bar 附带昨收（同复权），再日线

    盘中仅有前缀分钟时，``session_close`` 为当前可见末根，仅供展示；估 Ĉ 不用它。
    """
    mins = [b for b in minute_bars if isinstance(b, dict)]
    open_px: Optional[float] = None
    session_close: Optional[float] = None
    prev: Optional[float] = None
    for b in mins:
        o = _f(b.get("open"))
        c = _f(b.get("close")) or _f(b.get("price"))
        if open_px is None and o is not None and o > 0:
            open_px = o
        if c is not None and c > 0:
            session_close = c
        if prev is None:
            prev = _f(b.get("prev_close")) or _f(b.get("pre_close")) or _f(b.get("yc"))
    if open_px is None or open_px <= 0:
        open_px = _f((bar or {}).get("open")) if isinstance(bar, dict) else None
    if session_close is None or session_close <= 0:
        session_close = _f((bar or {}).get("close")) if isinstance(bar, dict) else None
    if prev is None and isinstance(bar, dict):
        prev = _f(bar.get("prev_close")) or _f(bar.get("pre_close")) or _f(bar.get("yc"))
    return {"open_px": open_px, "prev_close": prev, "session_close": session_close}
