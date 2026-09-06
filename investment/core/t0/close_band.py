"""v6 收盘带宽选腿：每根 5m 用前缀 ŷ_τ 估 ĉ_τ → 收价 C 破带定方向 → leg1 后冻结 leg2。

path 仅入场校验；y_trade / y_eod / y_nowcast 不参与估 ĉ 与选腿。
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


def scores_close_components(
    scores: Optional[dict],
    *,
    open_px: float,
    prev_close: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    """由 ŷ_τ 推导绝对收盘价 ĉ_τ（该根前缀因果分；标签 open→close）。

    估 ĉ 优先用**日线** open（与训标签同空间）；slots 再经
    ``map_close_px_to_minute``（S=O_d/O_m）映回分钟触价。
    trade / nowcast **不进** ĉ（保留键位为 None，避免旧 UI 误读均价）。
    """
    from core.t0.score_policy import scores_from_item

    _ = prev_close  # 兼容旧调用；τ 只锚 open
    raw = scores if isinstance(scores, dict) else {}
    sc = scores_from_item(raw)
    y_tau = sc.get("y_tau")
    c_tau = _pct_to_px(open_px, y_tau) if y_tau is not None else None

    return {
        "c_tau": round(c_tau, 4) if c_tau is not None else None,
        "c_trade": None,
        "c_nowcast": None,
        "y_tau": y_tau,
        "y_trade": sc.get("y_trade"),
        "y_nowcast": sc.get("y_nowcast"),
        "trade_vs": None,
        "nowcast_vs": None,
    }


def estimate_close_px(
    scores: Optional[dict],
    *,
    open_px: float,
    prev_close: Optional[float] = None,
) -> Dict[str, Any]:
    """ĉ := ĉ_τ = O×(1+y_τ/100)。缺 y_τ → 不可估。

    扫描传入**该根前缀**因果 ŷ_τ（研究枢纽 OC 头；开盘 Z + ≤该根分钟）。
    破带比较 price = **本根 5m 收价 C**（经 S 映分钟空间），非日线收。
    """
    parts = scores_close_components(scores, open_px=open_px, prev_close=prev_close)
    c_tau = parts.get("c_tau")
    if c_tau is None:
        return {**parts, "close_px": None, "n_sources": 0, "ok": False}
    return {
        **parts,
        "close_px": round(float(c_tau), 4),
        "n_sources": 1,
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

    高于 ĉ+δ → sell_then_buy（反T）；低于 ĉ−δ → buy_then_sell（正T）。
    对称门槛；带 y_τ 非对称请用 ``close_band_pick_direction``。
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


SHIFT_SCALE_MIN = 0.1
SHIFT_SCALE_MAX = 1.0


def clamp_y_tau_leg1_prior_shift_scale(
    raw: Any,
    *,
    default: float = 0.1,
) -> float:
    """score 先验 α：默认 0.1，钳制到 [0.1, 1.0]。

    α=1 时同向下沿可贴 0（须 r<0 才正T / r>0 才反T）；对侧带宽变为 2δ。
    """
    try:
        if raw is None or raw == "":
            a = float(default)
        else:
            a = float(raw)
    except (TypeError, ValueError):
        a = float(default)
    if a != a:  # NaN
        return float(default)
    return max(SHIFT_SCALE_MIN, min(SHIFT_SCALE_MAX, float(a)))


def resolve_close_band_thresholds_pct(
    delta_pct: float,
    y_tau: Optional[float] = None,
    *,
    mode: str = "score",
    risk_k: float = 1.0,
    shift_scale: float = 0.9,
    band_floor_frac: float = 0.5,  # 兼容旧参；α<1 时不再需要 ε 地板
) -> tuple[float, float]:
    """局部超额 r=(p/ĉ−1)×100 的上下门槛（百分点）。

    默认：``r > δ → 反T``，``r < −δ → 正T``。
    ``score`` 模式：

    - ``s = clip(k·y_τ, −α·δ, +α·δ)``（α∈[0.1, 1.0]）
    - ``upper = δ+s``，``lower = −δ+s``
      （α<1 ⇒ upper>0>lower；α=1 时同侧可贴 0）
    """
    _ = band_floor_frac
    d = max(0.0, float(delta_pct))
    mode_n = normalize_y_tau_leg1_prior_mode(mode, default="score")
    if mode_n != "score" or y_tau is None:
        return d, -d
    try:
        k = max(0.0, float(risk_k))
        yt = float(y_tau)
        alpha = clamp_y_tau_leg1_prior_shift_scale(shift_scale, default=0.9)
    except (TypeError, ValueError):
        return d, -d
    shift_cap = alpha * d
    shift = k * yt
    if shift_cap > 0:
        shift = max(-shift_cap, min(shift_cap, shift))
    else:
        shift = 0.0
    return d + shift, -d + shift


def band_decision_by_excess_pct(
    bar_close: float,
    close_px: float,
    upper_pct: float,
    lower_pct: float,
) -> Optional[str]:
    """用 r=(p/Ĉ−1)×100 相对非对称门槛选腿；``bar_close``=本根 5m 收价 C。"""
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
    """破带选腿：对称 δ，或 score 模式下按 y_τ 平移门槛。

    ``bar_close``：**当前 5m 根收价 C**（分钟 ``mb.close``），不是日线收盘价。
    ``close_px``：分钟价空间下的 Ĉ。

    Returns (direction, meta) where meta 含 r_pct / upper_pct / lower_pct / y_tau / mode。
    """
    from core.t0.score_policy import (
        _cfg_float,
        resolve_direction_y_tau,
        scores_from_item,
    )

    cfg_d = cfg if isinstance(cfg, dict) else {}
    mode = normalize_y_tau_leg1_prior_mode(
        cfg_d.get("y_tau_leg1_prior_mode", cfg_d.get("y_tau_leg1_prior")),
        default="off",
    )
    k = _cfg_float(cfg_d, "y_tau_leg1_prior_risk", 0.1)
    k = max(0.0, min(float(k), 10.0))
    shift_scale = clamp_y_tau_leg1_prior_shift_scale(
        cfg_d.get("y_tau_leg1_prior_shift_scale"), default=0.1
    )
    raw = scores if isinstance(scores, dict) else {}
    sc = scores_from_item(raw)
    y_tau = resolve_direction_y_tau(sc)
    upper, lower = resolve_close_band_thresholds_pct(
        float(delta_pct),
        float(y_tau) if y_tau is not None else None,
        mode=mode,
        risk_k=k,
        shift_scale=shift_scale,
    )
    direction = band_decision_by_excess_pct(bar_close, close_px, upper, lower)
    r_pct = None
    try:
        p, mid = float(bar_close), float(close_px)
        if p > 0 and mid > 0:
            r_pct = (p / mid - 1.0) * 100.0
    except (TypeError, ValueError):
        r_pct = None
    return direction, {
        "mode": mode,
        "y_tau": y_tau,
        "prior_risk_k": k,
        "shift_scale": shift_scale,
        "r_pct": r_pct,
        "upper_pct": upper,
        "lower_pct": lower,
        "delta_pct": float(delta_pct),
    }


def band_decision_return(
    r_now_pct: float,
    r_hat_pct: float,
    delta_pct: float,
) -> Optional[str]:
    """收益空间破带：r_now = p_m/O_m−1（%），相对 r_hat±δ。

    与绝对价破带在常数缩放假设下等价。
    """
    try:
        r = float(r_now_pct)
        mid = float(r_hat_pct)
        d = float(delta_pct)
    except (TypeError, ValueError):
        return None
    if d < 0:
        return None
    if r > mid + d:
        return "sell_then_buy"
    if r < mid - d:
        return "buy_then_sell"
    return None


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
    """做 T 价空间：日线训/估 ĉ，分钟价用 S 映回（或收益空间等价）。

    - ``minute_open`` / ``minute_prev``：执行锚（分钟首开 / 附带昨收）
    - ``daily_open`` / ``daily_prev``：日线 bar 的开/昨收（估 ĉ 优先）
    - ``daily_bar``：可选，显式原始日 K（勿传 ``_day_ohlc_from_minutes`` 合成结果）
    - ``scale`` = O_d/O_m；无日线开则 1.0（纯分钟回退）
    - ``skip_reason``：|O_d/O_m−1| 或 |P_d/P_m−1| 超阈（P=昨收）

    估 ĉ 用 ``estimate_open`` / ``estimate_prev``（有日线则日线）；
    破带/触价用分钟：``close_px_m = ĉ_d / scale``。
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
    """ĉ_d → 分钟价空间：ĉ_m = ĉ_d / S = ĉ_d * O_m/O_d（收盘目标价）。"""
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
    """把日线空间 c_* / ĉ 映到分钟；保留 *_daily 供对账。"""
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
    """path 强同 τ：|y_path|>y_path_strong 时须与 y_τ 同号，异号则跳过。

    trade/eod 强闸已下线；仅 path。y_use_path 关或无 y_path 时不拦。
    y_path_strong≤0：任意非零 |y_path| 都要求同号。
    """
    from core.t0.config import coerce_cfg_bool
    from core.t0.score_policy import (
        DEFAULT_PATH_STRONG,
        _cfg_float,
        _strong_head_tau_sign_gate,
        resolve_direction_y_tau,
        scores_from_item,
    )

    cfg_d = cfg if isinstance(cfg, dict) else {}
    if not coerce_cfg_bool(cfg_d.get("y_use_path"), True):
        return None
    raw = scores if isinstance(scores, dict) else {}
    sc = scores_from_item(raw)
    y_path = sc.get("y_path")
    if y_path is None:
        return None
    y_tau = resolve_direction_y_tau(sc)
    if y_tau is None:
        # 强 path 需要对照 τ；缺 τ 时交给入场闸（τ enter）处理
        return None
    path_strong = _cfg_float(cfg_d, "y_path_strong", DEFAULT_PATH_STRONG)
    path_strong = max(0.0, min(float(path_strong), 5.0))
    ok, reason = _strong_head_tau_sign_gate(
        float(y_path),
        float(y_tau),
        path_strong,
        "y_path",
        tau_label="y_τ",
    )
    if ok:
        return None
    # 去掉 dual_y 前缀，贴近 close_band 其它 skip 文案
    if reason and reason.startswith("dual_y："):
        return reason[len("dual_y：") :]
    return reason or "强 y_path 与 y_τ 异号跳过"


def normalize_y_tau_leg1_prior_mode(raw: Any, *, default: str = "off") -> str:
    """日线先验模式：off | score（按 y_τ 平移破带门槛）。旧 skip/hard 并入 score。"""
    if raw is False or raw is None:
        if raw is False:
            return "off"
        return str(default or "off")
    if raw is True:
        return "score"
    s = str(raw).strip().lower()
    if s in {"", "none"}:
        return str(default or "off")
    if s in {"0", "off", "false", "no", "disable", "disabled"}:
        return "off"
    if s in {"1", "true", "yes", "on", "score", "soft", "raise", "scale", "skip", "hard", "block"}:
        return "score"
    return str(default or "off")


def tau_prior_adverse_pct(
    y_tau: float,
    direction: str,
    *,
    eps: float = 1e-9,
) -> float:
    """局部↔整体不一致幅度（收益百分点）。

    破带定腿隐含局部 price→ĉ（正T：价在 ĉ 下、局部向上；反T：价在 ĉ 上、局部向下）；
    y_τ 是分槽/日线 open→close 整体方向。二者异号时 adverse=|y_τ|，同向为 0。
    """
    d = str(direction or "").strip()
    yt = float(y_tau)
    e = max(0.0, float(eps))
    if d == "buy_then_sell":
        return max(0.0, -yt) if yt < -e else 0.0
    if d == "sell_then_buy":
        return max(0.0, yt) if yt > e else 0.0
    return 0.0


def close_band_tau_prior_skip_reason(
    scores: Optional[dict],
    cfg: Optional[dict] = None,
    *,
    direction: Optional[str] = None,
    bar_close: Optional[float] = None,
    close_px: Optional[float] = None,
    delta_px: Optional[float] = None,
) -> Optional[str]:
    """τ先验硬跳过已下线；旧 skip 由 normalize 并入 score，此处一律放行。"""
    _ = (scores, cfg, direction, bar_close, close_px, delta_px)
    return None


def _enter_profile_skip_reason(
    *,
    y_tau: Optional[float],
    y_path: Optional[float],
    y_complexity_u: Optional[float],
    y_tpd_u: Optional[float],
    tau_enter: float,
    path_enter: float,
    cx_max: float,
    tpd_max: float,
    use_path: bool,
) -> Optional[str]:
    """一组 |y_τ|/|y_path|/complexity/tpd 入场闸；缺 hat 的 complexity/tpd 不挡。"""
    if tau_enter > 0:
        if y_tau is None:
            return "y_τ 缺失，未过入场门槛"
        yt = float(y_tau)
        if abs(yt) < tau_enter - 1e-12:
            return f"|y_τ|={abs(yt):.3f}%<{tau_enter:g}% 未过入场（横盘）"
    if use_path and y_path is not None and path_enter > 0:
        yp = float(y_path)
        if abs(yp) < path_enter - 1e-12:
            return f"|y_path|={abs(yp):.3f}%<{path_enter:g}% 未过入场（横盘）"
    if y_complexity_u is not None and y_complexity_u > cx_max + 1e-12:
        return f"y_complexity={y_complexity_u:.3f}>{cx_max:g} 太折跳过"
    if y_tpd_u is not None and y_tpd_u > tpd_max + 1e-12:
        return f"y_tpd={y_tpd_u:.3f}>{tpd_max:g} 反转过密跳过"
    return None


_ENTER_ALT_KEYS = (
    "y_tau_enter_alt",
    "y_path_enter_alt",
    "y_complexity_max_alt",
    "y_tpd_max_alt",
)


def _enter_alt_configured(cfg_d: dict) -> bool:
    """有任一门槛2 键则走门槛1/门槛2 OR；缺键保持门槛1-only（直传旧 cfg）。"""
    return any(cfg_d.get(k) not in (None, "") for k in _ENTER_ALT_KEYS)


def close_band_enter_skip_reason(
    scores: Optional[dict],
    cfg: Optional[dict] = None,
    *,
    direction: Optional[str] = None,
    r_pct: Optional[float] = None,
) -> Optional[str]:
    """入场门槛：|y_τ|≥y_tau_enter；y_use_path 时须有 y_path 且 |y_path|≥y_path_enter。
    ŷ_complexity > y_complexity_max（0.00–1.00）则太折跳过；缺 ŷ_complexity 不挡。
    ŷ_tpd > y_tpd_max（0.00–1.00，默认 0.40）则反转过密跳过；缺 ŷ_tpd 不挡；1.00≈关。

    选腿仍由收盘带宽定方向；此处只过滤横盘/弱信号/缺 path / |R̂_τ| 不足 / 太折 / TPD 过密。
    enter≤0 仅关闭对应 |ŷ| 幅度闸；缺 y_path 在 y_use_path 下仍跳过。
    r_tau_enter 经 load 钳在 0–1.0；≤0 关闸（直传旧 cfg 兼容）。

    门槛2：配置了 y_*_alt 时，门槛1 未过仍可走门槛2（默认 |y_τ|/|y_path|≥0.40%，
    complexity/tpd≤1.0≈关）；|R̂_τ| 与缺 path / 分钟缺失仍共用，不绕过。
    load_t0_rules 会写入门槛2 键；直传缺键的旧 cfg 只走门槛1。
    """
    from core.t0.score_policy import (
        DEFAULT_PATH_ENTER,
        DEFAULT_TAU_ENTER,
        _cfg_float,
        resolve_direction_y_tau,
        scores_from_item,
        side_path_enter,
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
        path_enter = float(side_path_enter(cfg_d, for_buy_then_sell=for_bts))
    else:
        tau_enter = _cfg_float(cfg_d, "y_tau_enter", DEFAULT_TAU_ENTER)
        tau_enter = max(0.0, min(float(tau_enter), 100.0))
        path_enter = _cfg_float(cfg_d, "y_path_enter", DEFAULT_PATH_ENTER)
        path_enter = max(0.0, min(float(path_enter), 100.0))

    y_tau = resolve_direction_y_tau(sc)

    r_enter = _cfg_float(cfg_d, "r_tau_enter", 0.0)
    r_enter = max(0.0, min(float(r_enter), 1.0))
    if r_enter > 0:
        if r_pct is None:
            return "R̂_τ 缺失，未过入场门槛"
        try:
            rv = abs(float(r_pct))
        except (TypeError, ValueError):
            return "R̂_τ 缺失，未过入场门槛"
        if rv < r_enter - 1e-12:
            return f"|R̂_τ|={rv:.3f}%<{r_enter:g}% 未过入场（超额不足）"

    status = str(
        sc.get("y_path_status") or raw.get("y_path_status") or ""
    ).strip()
    # 非 09:30：缺分钟小包 = 数据缺失，硬跳过（与 y_use_path 无关）
    if raw.get("_minute_data_missing") or status == "minute_data_missing":
        return "分钟数据缺失（非 09:30 须有分钟小包）"

    use_path = coerce_cfg_bool(cfg_d.get("y_use_path"), True)
    y_path = sc.get("y_path")
    if use_path:
        if y_path is None:
            # 仅 09:30 / 开盘信息集：无分钟且开盘特征不足时暂不挡 path 闸
            if status == "minute_feats_missing":
                return None
            return "y_path 缺失，未过入场门槛"

    from core.research.cx_panel import pick_y_complexity_hat, pick_y_tpd_hat

    if cfg_d.get("y_complexity_max") not in (None, ""):
        cx_max = _cfg_float(cfg_d, "y_complexity_max", 1.0)
    else:
        cx_max = _cfg_float(cfg_d, "y_cx_max", 1.0)
    cx_max = max(0.0, min(float(cx_max), 1.0))
    y_complexity_u = pick_y_complexity_hat(sc, raw)
    tpd_max = _cfg_float(cfg_d, "y_tpd_max", 0.40)
    tpd_max = max(0.0, min(float(tpd_max), 1.0))
    y_tpd_u = pick_y_tpd_hat(sc, raw)

    skip = _enter_profile_skip_reason(
        y_tau=y_tau,
        y_path=y_path,
        y_complexity_u=y_complexity_u,
        y_tpd_u=y_tpd_u,
        tau_enter=tau_enter,
        path_enter=path_enter,
        cx_max=cx_max,
        tpd_max=tpd_max,
        use_path=use_path,
    )
    if skip is None:
        return None
    if not _enter_alt_configured(cfg_d):
        return skip

    tau_enter_alt = _cfg_float(cfg_d, "y_tau_enter_alt", 0.40)
    tau_enter_alt = max(0.0, min(float(tau_enter_alt), 100.0))
    path_enter_alt = _cfg_float(cfg_d, "y_path_enter_alt", 0.40)
    path_enter_alt = max(0.0, min(float(path_enter_alt), 100.0))
    cx_max_alt = _cfg_float(cfg_d, "y_complexity_max_alt", 1.0)
    cx_max_alt = max(0.0, min(float(cx_max_alt), 1.0))
    tpd_max_alt = _cfg_float(cfg_d, "y_tpd_max_alt", 1.0)
    tpd_max_alt = max(0.0, min(float(tpd_max_alt), 1.0))
    skip_alt = _enter_profile_skip_reason(
        y_tau=y_tau,
        y_path=y_path,
        y_complexity_u=y_complexity_u,
        y_tpd_u=y_tpd_u,
        tau_enter=tau_enter_alt,
        path_enter=path_enter_alt,
        cx_max=cx_max_alt,
        tpd_max=tpd_max_alt,
        use_path=use_path,
    )
    if skip_alt is None:
        return None
    return skip


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
    """按方向冻结 leg2：反T→ĉ−δ；正T→ĉ+δ。"""
    d = str(direction or "").strip().lower()
    mid = float(close_px)
    band = float(delta_px)
    if d == "sell_then_buy":
        leg2 = mid - band
    else:
        leg2 = mid + band
    return FrozenRound(
        direction=d,
        leg1_px=float(leg1_px),
        leg2_target=round(float(leg2), 4),
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
