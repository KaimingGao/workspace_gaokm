"""做 T 选腿：09:30–11:00 每根 5m 用 y_τc 估 C_τ，破带定方向。

C_τ = price(τ)×(1+clip(y_τc×scale, ±20)/100)。τ=开盘时 price(τ)=open。
硬顶 ±20。upper = C_τ×(1+δ/100)，lower = C_τ×(1−δ/100)。
C>upper → 反T（现价卖，leg2 目标 C_τ）；C<lower → 正T（现价买，leg2 目标 C_τ）。
ŷ_τw = ŷ_τ30/45/60/75/90 相对共用中位点（默认 47%）的符号和，过门槛才开腿。
股数额度看 |ŷ_oc|：过 y_oc入场% 用入场金额/价换算股数，过 y_oc强% 用强金额。
阴阳门槛 / 前序 ŷ_τw 确认 / 收贴端已下线。
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


DEFAULT_Y_OC_TARGET_SCALE = 2.0
Y_OC_TARGET_SCALE_MIN = 0.0
Y_OC_TARGET_SCALE_MAX = 100.0
Y_OC_CLIP_ABS_MAX = 20.0  # 硬顶；表单已下线上下界
DEFAULT_CLOSE_BAND_DELTA_PCT = 0.5
CLOSE_BAND_DELTA_PCT_MAX = 10.0


def resolve_y_oc_target_scale(cfg: Optional[dict] = None) -> float:
    """C_τ 放大倍数。缺键默认 2。"""
    cfg_d = cfg if isinstance(cfg, dict) else {}
    try:
        raw = cfg_d.get("t0_y_oc_target_scale")
        v = float(DEFAULT_Y_OC_TARGET_SCALE if raw is None or raw == "" else raw)
    except (TypeError, ValueError):
        v = float(DEFAULT_Y_OC_TARGET_SCALE)
    if v != v:  # NaN
        v = float(DEFAULT_Y_OC_TARGET_SCALE)
    return max(Y_OC_TARGET_SCALE_MIN, min(Y_OC_TARGET_SCALE_MAX, v))


def resolve_y_oc_target_params(cfg: Optional[dict] = None) -> float:
    """兼容旧名：只返回 scale。上下界已下线。"""
    return resolve_y_oc_target_scale(cfg)


def resolve_close_band_delta_pct(cfg: Optional[dict] = None) -> float:
    """破带带宽 δ%：upper=C_τ×(1+δ/100)。缺键默认 0.5。"""
    cfg_d = cfg if isinstance(cfg, dict) else {}
    try:
        raw = cfg_d.get("t0_close_band_delta_pct")
        v = float(DEFAULT_CLOSE_BAND_DELTA_PCT if raw is None or raw == "" else raw)
    except (TypeError, ValueError):
        v = float(DEFAULT_CLOSE_BAND_DELTA_PCT)
    if v != v:  # NaN
        v = float(DEFAULT_CLOSE_BAND_DELTA_PCT)
    return max(0.0, min(v, CLOSE_BAND_DELTA_PCT_MAX))


def clip_y_oc_target_pct(
    y_oc: float,
    *,
    scale: float = DEFAULT_Y_OC_TARGET_SCALE,
    y_oc_l: Any = None,
    y_oc_u: Any = None,
) -> float:
    """ŷ_oc×scale（百分点）；硬顶 ±20。``y_oc_l`` / ``y_oc_u`` 已忽略。"""
    _ = y_oc_l, y_oc_u
    yt = float(y_oc) * float(scale)
    return max(-Y_OC_CLIP_ABS_MAX, min(Y_OC_CLIP_ABS_MAX, yt))


def target_c_tau_px(
    price: float,
    y_oc: float,
    *,
    scale: float = DEFAULT_Y_OC_TARGET_SCALE,
    y_oc_l: Any = None,
    y_oc_u: Any = None,
) -> Optional[float]:
    """C_τ = price(τ) × (1 + clip(y_τc×scale, ±20)/100)。"""
    _ = y_oc_l, y_oc_u
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if p <= 0:
        return None
    clipped = clip_y_oc_target_pct(y_oc, scale=scale)
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
    """估 C_τ：price(τ)×(1+clip(y_τc×scale, ±20)/100)。缺 price(τ) 时用 open。

    缺 y_τc 时回退 y_oc / y_tau（旧行、开盘时钟同一个数）。
    R̂_τ = Ĉ_τ/price(τ)−1，等于 clip 后的百分点。
    remaining_oc 仍是未 clip 的 remaining(y_oc)，不进破带。
    ``c_oc`` 为未 clip 的 O×(1+y_oc/100)，不进破带。
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
    direct_τc = (
        "y_τc",
        "predicted_score_τc",
        "y_tc",
        "predicted_score_tc",
        "y_to",
        "predicted_score_to",
        "y_pc",
        "predicted_score_pc",
    )
    has_direct_τc = any(raw.get(k) is not None for k in direct_τc)
    # 只有旧 y_r（分钟收盘头）时不拿来估带，回退 y_oc / y_tau。
    y_for_band = (
        y_τc_ridge
        if y_τc_ridge is not None and has_direct_τc
        else y_oc
    )
    y_tau = y_oc
    scale = resolve_y_oc_target_scale(cfg)
    y_oc_target = (
        clip_y_oc_target_pct(y_for_band, scale=scale) if y_for_band is not None else None
    )
    c_oc = _pct_to_px(open_px, y_oc) if y_oc is not None else None
    pt = _f(price_tau)
    if pt is None or pt <= 0:
        feats = raw.get("features_tau") if isinstance(raw.get("features_tau"), dict) else {}
        pt = _f(raw.get("price_tau")) or _f(feats.get("price_tau")) or _f(feats.get("price"))
    anchor = pt if pt is not None and pt > 0 else _f(open_px)
    c_hat = _pct_to_px(anchor, y_oc_target) if y_oc_target is not None and anchor else None
    if c_hat is not None:
        c_hat = round(float(c_hat), 4)
    rot = ret_open_to_tau_pct(open_px, pt)
    if rot is None:
        rot = ret_open_to_tau_of(raw)
    rem = remaining_oc(y_oc, rot, open_px=open_px, price_tau=pt) if y_oc is not None else None
    r_hat = r_hat_from_c_tau_px(c_hat, anchor)
    # 表列 ŷ_τc = Ridge 预估 price→close；R̂_τ = Ĉ_τ/price(τ)−1。
    y_τc = y_τc_ridge
    y_τc_source = Y_TC_SOURCE_RIDGE if y_τc_ridge is not None else None
    c_τc = _pct_to_px(anchor, y_τc) if y_τc is not None and anchor else None
    c_rem = _pct_to_px(pt, rem) if rem is not None and pt is not None and pt > 0 else None
    source = None
    if c_hat is not None:
        source = "y_τc" if y_for_band is y_τc_ridge and y_τc_ridge is not None else "y_oc"
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
    """C_τ = price(τ)×(1+clip(y_τc×scale, ±20)/100)。缺 y_τc 且缺 y_oc → 不可估。

    ``price_tau`` 与 ``open_px`` 同一价空间；缺 price(τ) 时锚在 open。
    扫描传入该根前缀的 y_τc。破带比较的是本根 5m 收价。
    R̂_τ = Ĉ_τ/price(τ)−1。``cfg`` 提供 scale（默认 2）。
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
    scale = resolve_y_oc_target_scale(cfg)
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


Y_TC_BAND_EPS = 0.05  # |ŷ_τc| 低于此视为无投票（与符号命中死区同）
Y_T30_HIT_EPS = 0.0  # 30m |ŷ| 中位约 0.02%，不可套用 τc 的 0.05；与旁路闸「有符号即投票」同口径
Y_T45_HIT_EPS = 0.0
Y_T60_HIT_EPS = 0.0
Y_T75_HIT_EPS = 0.0
Y_T90_HIT_EPS = 0.0
DEFAULT_Y_TW_ENTER = 2.0  # 正T：ŷ_τw>=此值；反T：ŷ_τw<=−此值。0=允许 0 票
DEFAULT_Y_OC_ENTER = 0.5  # |ŷ_oc| 入场百分点；0=不拦
DEFAULT_Y_OC_STRONG = 1.0  # |ŷ_oc|>=此值用强股数，否则入场股数
DEFAULT_Y_OC_WEAK_RATIO = 0.5  # 过入场未过强且未配股数：轮次仓位 × 此比例
DEFAULT_Y_OC_ENTER_SHARES = 2000
DEFAULT_Y_OC_STRONG_SHARES = 4000
DEFAULT_Y_TW_VOTE_MARGIN = 5.0  # |p_up−mid|≤此百分点不投票
DEFAULT_Y_TW_MIDPOINT = 47.0  # ŷ_τ* 共用中位点（百分点）


def _horizon_vote_margin_pp(cfg: Optional[dict] = None, margin_pp: Any = None) -> Any:
    if margin_pp not in (None, ""):
        return margin_pp
    cfg_d = cfg if isinstance(cfg, dict) else {}
    raw = cfg_d.get("y_tw_vote_margin")
    if raw in (None, "") and cfg_d.get("y_τw_vote_margin") not in (None, ""):
        raw = cfg_d.get("y_τw_vote_margin")
    return raw if raw not in (None, "") else None


def _horizon_vote_midpoint(cfg: Optional[dict] = None, midpoint: Any = None) -> Any:
    if midpoint not in (None, ""):
        return midpoint
    cfg_d = cfg if isinstance(cfg, dict) else {}
    raw = cfg_d.get("y_tw_midpoint")
    if raw in (None, "") and cfg_d.get("y_τw_midpoint") not in (None, ""):
        raw = cfg_d.get("y_τw_midpoint")
    return raw if raw not in (None, "") else DEFAULT_Y_TW_MIDPOINT


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


def y_tw_sign(x: Any, *, margin_pp: Any = None, midpoint: Any = None) -> Optional[int]:
    """ŷ_τw 票：sign(p_up−mid)。|p−mid|≤margin_pp（默认 5pp）/ 缺分不投票。mid 默认 47%。"""
    from core.research.horizon_prob import p_up_vote

    return p_up_vote(x, margin_pp=margin_pp, midpoint=midpoint)


def blend_y_tw(
    y_τ30: Any = None,
    y_τ60: Any = None,
    y_τ90: Any = None,
    y_τ45: Any = None,
    y_τ75: Any = None,
    *,
    margin_pp: Any = None,
    midpoint: Any = None,
    cfg: Optional[dict] = None,
) -> Optional[float]:
    """ŷ_τw = f(ŷ_τ30)+…+f(ŷ_τ90)；f=sign(p_up−mid)。

    mid 默认 47%（``y_tw_midpoint``）。|p−mid|≤y_tw_vote_margin / 缺头不计该项。
    有头但全弃权计 **0 票**；无任何头返回 None。
    """
    band = _horizon_vote_margin_pp(cfg, margin_pp)
    mid = _horizon_vote_midpoint(cfg, midpoint)
    heads = (y_τ30, y_τ45, y_τ60, y_τ75, y_τ90)
    vals = [
        s
        for s in (y_tw_sign(h, margin_pp=band, midpoint=mid) for h in heads)
        if s is not None
    ]
    if vals:
        return float(sum(vals))
    if any(_f(h) is not None for h in heads):
        return 0.0
    return None


def _y_tw_enter_floor(cfg_d: dict) -> float:
    """ŷ_τw 共用入场幅度。优先 y_tw_enter。"""
    from core.t0.score_policy import _cfg_float

    for k in ("y_tw_enter", "y_τw_enter"):
        if cfg_d.get(k) not in (None, ""):
            return max(0.0, min(float(_cfg_float(cfg_d, k, DEFAULT_Y_TW_ENTER)), 5.0))
    return DEFAULT_Y_TW_ENTER


def _ytw_enter_for_direction(cfg_d: dict, direction: str) -> float:
    """正/反 T 共用 y_tw_enter。分侧键已下线。"""
    _ = direction
    return _y_tw_enter_floor(cfg_d)


def _y_oc_enter_floor(cfg_d: dict) -> float:
    """ŷ_oc 入场百分点。0=不拦。"""
    from core.t0.score_policy import _cfg_float

    if cfg_d.get("y_oc_enter") not in (None, ""):
        return max(0.0, min(float(_cfg_float(cfg_d, "y_oc_enter", DEFAULT_Y_OC_ENTER)), 20.0))
    return DEFAULT_Y_OC_ENTER


def _y_oc_strong_floor(cfg_d: dict) -> float:
    """ŷ_oc 强档百分点。至少等于入场，避免强档低于入场。"""
    from core.t0.score_policy import _cfg_float

    enter = _y_oc_enter_floor(cfg_d)
    if cfg_d.get("y_oc_strong") not in (None, ""):
        strong = max(0.0, min(float(_cfg_float(cfg_d, "y_oc_strong", DEFAULT_Y_OC_STRONG)), 20.0))
        return max(enter, strong)
    return max(enter, DEFAULT_Y_OC_STRONG)


def close_band_y_oc_is_strong(
    y_oc: Optional[float],
    cfg: Optional[dict] = None,
) -> bool:
    """过 y_oc入场后是否达到 y_oc强（全额轮次）。"""
    if y_oc is None:
        return False
    cfg_d = cfg if isinstance(cfg, dict) else {}
    strong = _y_oc_strong_floor(cfg_d)
    return abs(float(y_oc)) + 1e-12 >= float(strong)


def close_band_y_oc_round_scale(
    y_oc: Optional[float],
    cfg: Optional[dict] = None,
) -> float:
    """轮次仓位乘数：过强=1，过入场未过强=半仓。配了绝对金额时引擎改走 round_shares。"""
    if close_band_y_oc_is_strong(y_oc, cfg):
        return 1.0
    return DEFAULT_Y_OC_WEAK_RATIO


def _yoc_lot_amount(raw: Any, default: float = 0.0) -> float:
    try:
        v = float(default if raw in (None, "") else raw)
    except (TypeError, ValueError):
        v = float(default)
    if v != v or v <= 0:
        return 0.0
    return float(v)


def close_band_y_oc_round_shares(
    cfg: Optional[dict],
    *,
    y_oc: Optional[float],
    lot: int = 100,
    price: Optional[float] = None,
) -> Optional[int]:
    """过 y_oc入场用入场金额/价，过 y_oc强用强金额。不够一手则买一手。两边都未配（≤0）则 None，走比例仓。"""
    from core.paper.sizing import shares_from_amount

    cfg_d = cfg if isinstance(cfg, dict) else {}
    enter = _yoc_lot_amount(cfg_d.get("y_oc_enter_amount"), 0.0)
    strong = _yoc_lot_amount(cfg_d.get("y_oc_strong_amount"), 0.0)
    if enter <= 0 and strong <= 0:
        return None
    if enter <= 0:
        enter = strong
    if strong <= 0:
        strong = enter
    strong = max(enter, strong)
    amt = float(strong if close_band_y_oc_is_strong(y_oc, cfg_d) else enter)
    return int(shares_from_amount(amt, price, lot))


def close_band_y_oc_skip_reason(
    y_oc: Optional[float],
    cfg: Optional[dict] = None,
) -> Optional[str]:
    """选腿入场：|ŷ_oc| 须过 y_oc入场%。0=不拦。缺 ŷ_oc 由估 C_τ 先行跳过。"""
    if y_oc is None:
        return None
    cfg_d = cfg if isinstance(cfg, dict) else {}
    enter = _y_oc_enter_floor(cfg_d)
    if enter <= 1e-12:
        return None
    if abs(float(y_oc)) + 1e-12 < float(enter):
        return f"ŷ_oc={float(y_oc):+.2f} 未过入场（须|ŷ_oc|>={enter:g}）"
    return None


def blend_y_tw_from_scores(
    scores: Optional[dict] = None,
    cfg: Optional[dict] = None,
) -> Optional[float]:
    """从快照抽 ŷ_τ30/45/60/75/90 合成 ŷ_τw。"""
    return blend_y_tw(
        _pick_y_t30_for_band(scores),
        _pick_y_t60_for_band(scores),
        _pick_y_t90_for_band(scores),
        _pick_y_t45_for_band(scores),
        _pick_y_t75_for_band(scores),
        cfg=cfg,
    )


def blend_y_tw_realized(
    y_τ30: Any = None,
    y_τ60: Any = None,
    y_τ90: Any = None,
    y_τ45: Any = None,
    y_τ75: Any = None,
) -> Optional[float]:
    """真实 y_τw：各窗收益符号和。收益=0/缺头不计；全横盘=0；无头=None。"""
    heads = (y_τ30, y_τ45, y_τ60, y_τ75, y_τ90)
    votes: list[int] = []
    any_head = False
    for h in heads:
        v = _f(h)
        if v is None:
            continue
        any_head = True
        if abs(float(v)) <= 1e-12:
            continue
        votes.append(1 if float(v) > 0 else -1)
    if votes:
        return float(sum(votes))
    if any_head:
        return 0.0
    return None




def _fmt_ytw_votes(y: Optional[float]) -> str:
    if y is None:
        return "—"
    yt = int(round(float(y)))
    return f"{yt:+d}" if yt else "0"


def close_band_y_tw_skip_reason(
    scores: Optional[dict],
    cfg: Optional[dict] = None,
    *,
    direction: Optional[str] = None,
    y_tw: Optional[float] = None,
) -> Optional[str]:
    """选腿入场：正T须 ŷ_τw>=正T入场；反T须 ŷ_τw<=−反T入场。

    缺 ŷ_τw（无任何窗头）不开腿。全弃权计 0 票；入场=0 时 0 票可通过。
    """
    cfg_d = cfg if isinstance(cfg, dict) else {}
    d = str(direction or "").strip()
    if d not in ("sell_then_buy", "buy_then_sell"):
        return None
    y = y_tw if y_tw is not None else blend_y_tw_from_scores(scores, cfg_d)
    if y is None:
        return "缺 ŷ_τw，未开腿"
    enter = _ytw_enter_for_direction(cfg_d, d)
    shown = _fmt_ytw_votes(y)
    if d == "buy_then_sell":
        if float(y) + 1e-12 < float(enter):
            return f"ŷ_τw={shown} 未过正T入场（须>={enter:g}）"
        return None
    if float(y) - 1e-12 > -float(enter):
        return f"ŷ_τw={shown} 未过反T入场（须<={-enter:g}）"
    return None


def bar_close_band_pick_direction(
    bar_open: float,
    bar_close: float,
    scores: Optional[dict] = None,
    cfg: Optional[dict] = None,
    *,
    open_px: Optional[float] = None,
    prev_close: Optional[float] = None,
    price_tau: Optional[float] = None,
    scale: float = 1.0,
    bar_low: Optional[float] = None,
    bar_high: Optional[float] = None,
) -> tuple[Optional[str], Dict[str, Any]]:
    """旗舰选腿：y_τc 估 C_τ，破带定方向；ŷ_τw 门槛为辅。

    C>upper → sell_then_buy；C<lower → buy_then_sell。
    缺 y_τc / 未破带 / ŷ_τw 未过门槛 / |y| 未过入场% → None。
    ``open_px`` 在估带空间（有日线则为日线开）。``price_tau`` 与本根收价同空间，
    先乘 ``scale`` 再估，估完的 C_τ 再除 ``scale`` 映回分钟。
    """
    o, c = _f(bar_open), _f(bar_close)
    lo, hi = _f(bar_low), _f(bar_high)
    y = blend_y_tw_from_scores(scores, cfg)
    delta = resolve_close_band_delta_pct(cfg)
    est_open = _f(open_px) or o
    meta: Dict[str, Any] = {
        "y_tw": y,
        "bar_open": o,
        "bar_close": c,
        "bar_low": lo,
        "bar_high": hi,
        "delta_pct": delta,
        "c_tau": None,
        "close_px": None,
        "lower_px": None,
        "upper_px": None,
        "upper_pct": delta,
        "lower_pct": -delta,
        "r_pct": None,
    }
    if c is None or c <= 0:
        meta["skip"] = "缺开收"
        return None, meta
    if est_open is None or est_open <= 0:
        meta["skip"] = "缺开盘，未估 C_τ"
        return None, meta
    try:
        s = float(scale) if scale else 1.0
    except (TypeError, ValueError):
        s = 1.0
    if s <= 0:
        s = 1.0
    pt_raw = _f(price_tau)
    if pt_raw is None:
        pt_raw = c
    pt_est = float(pt_raw) * s if pt_raw is not None and pt_raw > 0 else None
    est = estimate_close_px(
        scores,
        open_px=float(est_open),
        prev_close=prev_close,
        price_tau=pt_est,
        cfg=cfg,
    )
    if not est.get("ok") or not est.get("close_px"):
        meta["skip"] = "缺 y_τc，未估 C_τ"
        return None, meta
    c_tau_d = est.get("close_px")
    c_tau_m = map_close_px_to_minute(c_tau_d, scale=s)
    if c_tau_m is None:
        meta["skip"] = "缺 y_τc，未估 C_τ"
        return None, meta
    direction, band_meta = close_band_pick_direction(
        float(c), float(c_tau_m), delta, scores, cfg
    )
    lo_px, up_px = close_band_edges_px(c_tau_m, delta)
    dpx = band_delta_px(c_tau_m, delta)
    meta.update(band_meta)
    meta["y_tw"] = y
    meta["c_tau"] = c_tau_m
    meta["close_px"] = c_tau_m
    meta["c_tau_daily"] = c_tau_d
    meta["lower_px"] = lo_px
    meta["upper_px"] = up_px
    meta["delta_px"] = dpx
    meta["delta_pct"] = delta
    if est.get("y_oc") is not None:
        meta["y_oc"] = est.get("y_oc")
    if est.get("y_oc_target") is not None:
        meta["y_oc_target"] = est.get("y_oc_target")
    if est.get("r_hat") is not None:
        meta["r_hat"] = est.get("r_hat")
    if est.get("remaining_oc") is not None:
        meta["remaining_oc"] = est.get("remaining_oc")
    if est.get("c_hat_source"):
        meta["c_hat_source"] = est.get("c_hat_source")
    if not direction:
        meta["skip"] = "未破带"
        return None, meta
    ytw_skip = close_band_y_tw_skip_reason(
        scores, cfg, direction=direction, y_tw=y
    )
    if ytw_skip:
        meta["skip"] = ytw_skip
        return None, meta
    yoc_skip = close_band_y_oc_skip_reason(meta.get("y_oc"), cfg)
    if yoc_skip:
        meta["skip"] = yoc_skip
        return None, meta
    return direction, meta


def bar_ytw_pick_direction(
    bar_open: float,
    bar_close: float,
    scores: Optional[dict] = None,
    cfg: Optional[dict] = None,
    bar_low: Optional[float] = None,
    bar_high: Optional[float] = None,
) -> tuple[Optional[str], Dict[str, Any]]:
    """库函数：仅 ŷ_τw 门槛选向（生产走 ``bar_close_band_pick_direction``）。

    ŷ_τw>=正T门槛 → buy_then_sell。
    ŷ_τw<=−反T门槛 → sell_then_buy。
    缺 ŷ_τw 或未过门槛 → None。
    """
    o, c = _f(bar_open), _f(bar_close)
    lo, hi = _f(bar_low), _f(bar_high)
    y = blend_y_tw_from_scores(scores, cfg)
    meta: Dict[str, Any] = {
        "y_tw": y,
        "bar_open": o,
        "bar_close": c,
        "bar_low": lo,
        "bar_high": hi,
        "upper_pct": None,
        "lower_pct": None,
        "r_pct": None,
    }
    if c is None or c <= 0:
        meta["skip"] = "缺开收"
        return None, meta
    skip_pos = close_band_y_tw_skip_reason(
        scores, cfg, direction="buy_then_sell", y_tw=y
    )
    if not skip_pos:
        meta["tentative"] = "buy_then_sell"
        return "buy_then_sell", meta
    skip_neg = close_band_y_tw_skip_reason(
        scores, cfg, direction="sell_then_buy", y_tw=y
    )
    if not skip_neg:
        meta["tentative"] = "sell_then_buy"
        return "sell_then_buy", meta
    if y is None:
        meta["skip"] = "缺 ŷ_τw，未开腿"
    elif float(y) >= 0:
        meta["skip"] = skip_pos
        meta["tentative"] = "buy_then_sell"
    else:
        meta["skip"] = skip_neg
        meta["tentative"] = "sell_then_buy"
    return None, meta


@dataclass
class FrozenRound:
    """leg1 成交瞬间冻结的本轮参数。leg2 目标默认 C_τ。"""

    direction: str
    leg1_px: float
    leg2_target: Optional[float]
    close_px: Optional[float]
    delta_px: Optional[float]
    ratio: float
    bar_index: int
    hm: str = ""
    y_tw: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def freeze_round(
    *,
    direction: str,
    leg1_px: float,
    close_px: Optional[float] = None,
    delta_px: Optional[float] = None,
    ratio: float = 0.4,
    bar_index: int = 0,
    hm: str = "",
    y_tw: Optional[float] = None,
    leg2_target: Optional[float] = None,
) -> FrozenRound:
    """冻结本轮：leg2 目标默认 C_τ（``close_px``）。"""
    d = str(direction or "").strip().lower()
    c_tau = _f(close_px)
    target = _f(leg2_target)
    if target is None:
        target = c_tau
    dpx = _f(delta_px)
    return FrozenRound(
        direction=d,
        leg1_px=float(leg1_px),
        leg2_target=round(float(target), 4) if target is not None else None,
        close_px=round(float(c_tau), 4) if c_tau is not None else None,
        delta_px=round(float(dpx), 4) if dpx is not None else None,
        ratio=float(ratio),
        bar_index=int(bar_index),
        hm=str(hm or ""),
        y_tw=float(y_tw) if _f(y_tw) is not None else None,
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
