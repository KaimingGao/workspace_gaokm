"""做 T 选腿：09:30–11:00 每根 5m **纯 ŷ_τw** 定方向与金额。

ŷ_τw = ŷ_τ30/45/60/75/90 相对共用中位点（默认 47%）的符号和。
正T：ŷ_τw≥Y_τw入场；反T：ŷ_τw≤−入场。金额看 |ŷ_τw|（入场/强票数）。
leg2 无 C_τ 目标价（止损 / 锁赢 / 追价 / 收盘强平）；旧 C_τ 破带估已移除。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Sequence

from core.intraday.price_space import (
    DEFAULT_PRICE_SPACE_MAX_DEV_PCT,
    price_space_scale,
)


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


def resolve_t0_price_space(
    bar: Optional[dict],
    minute_bars: Sequence[dict] = (),
    cfg: Optional[dict] = None,
    *,
    daily_bar: Optional[dict] = None,
) -> Dict[str, Any]:
    """做 T 价空间：日/分 open·昨收对齐与闸门。

    - ``minute_open`` / ``minute_prev``：执行锚（分钟首开 / 附带昨收）
    - ``daily_open`` / ``daily_prev``：日线 bar 的开/昨收
    - ``daily_bar``：可选，显式原始日 K（勿传 ``_day_ohlc_from_minutes`` 合成结果）
    - ``scale`` = O_d/O_m；无日线开则 1.0（纯分钟回退）
    - ``skip_reason``：|O_d/O_m−1| 或 |P_d/P_m−1| 超阈（P=昨收）
    - ``estimate_open`` / ``estimate_prev``：有日线则用日线锚，否则分钟回退
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


Y_T30_HIT_EPS = 0.0  # 30m |ŷ| 中位约 0.02%；与旁路闸「有符号即投票」同口径
Y_T45_HIT_EPS = 0.0
Y_T60_HIT_EPS = 0.0
Y_T75_HIT_EPS = 0.0
Y_T90_HIT_EPS = 0.0
DEFAULT_Y_TW_ENTER = 2.0  # 正T：ŷ_τw>=此值；反T：ŷ_τw<=−此值。0=允许 0 票
DEFAULT_Y_TW_STRONG = 3.0  # |ŷ_τw|≥此票用强金额
DEFAULT_Y_TW_WEAK_RATIO = 0.5  # 过入场未过强：比例仓半档
DEFAULT_Y_TW_VOTE_MARGIN = 5.0  # |p_up−mid|≤此百分点不投票
DEFAULT_Y_TW_MIDPOINT = 47.0  # ŷ_τ* 共用中位点（百分点）


def _horizon_vote_margin_pp(cfg: Optional[dict] = None, margin_pp: Any = None) -> Any:
    if margin_pp not in (None, ""):
        return margin_pp
    cfg_d = cfg if isinstance(cfg, dict) else {}
    raw = cfg_d.get("y_tw_vote_margin")
    return raw if raw not in (None, "") else None


def _horizon_vote_midpoint(cfg: Optional[dict] = None, midpoint: Any = None) -> Any:
    if midpoint not in (None, ""):
        return midpoint
    cfg_d = cfg if isinstance(cfg, dict) else {}
    raw = cfg_d.get("y_tw_midpoint")
    return raw if raw not in (None, "") else DEFAULT_Y_TW_MIDPOINT


def y_t30_band_agree(
    direction: Optional[str],
    y_τ30: Optional[float],
    *,
    eps: float = Y_T30_HIT_EPS,
) -> Optional[bool]:
    """选向是否与 ŷ_τ30=p_up 同向。正T 期望 p_up≥0.5；反T 期望 1−p_up≥0.5。"""
    _ = eps
    from core.research.horizon_prob import horizon_band_agree

    return horizon_band_agree(direction, y_τ30, floor=0.5)


def y_t45_band_agree(
    direction: Optional[str],
    y_τ45: Optional[float],
    *,
    eps: float = Y_T45_HIT_EPS,
) -> Optional[bool]:
    """选向是否与 ŷ_τ45=p_up 同向。"""
    _ = eps
    from core.research.horizon_prob import horizon_band_agree

    return horizon_band_agree(direction, y_τ45, floor=0.5)


def y_t60_band_agree(
    direction: Optional[str],
    y_τ60: Optional[float],
    *,
    eps: float = Y_T60_HIT_EPS,
) -> Optional[bool]:
    """选向是否与 ŷ_τ60=p_up 同向。"""
    _ = eps
    from core.research.horizon_prob import horizon_band_agree

    return horizon_band_agree(direction, y_τ60, floor=0.5)


def y_t75_band_agree(
    direction: Optional[str],
    y_τ75: Optional[float],
    *,
    eps: float = Y_T75_HIT_EPS,
) -> Optional[bool]:
    """选向是否与 ŷ_τ75=p_up 同向。"""
    _ = eps
    from core.research.horizon_prob import horizon_band_agree

    return horizon_band_agree(direction, y_τ75, floor=0.5)


def y_t90_band_agree(
    direction: Optional[str],
    y_τ90: Optional[float],
    *,
    eps: float = Y_T90_HIT_EPS,
) -> Optional[bool]:
    """选向是否与 ŷ_τ90=p_up 同向。"""
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
        from core.research.horizon_ridge import pick_y_hat

        y = pick_y_hat("t30", raw)
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
        from core.research.horizon_ridge import pick_y_hat

        y = pick_y_hat("t45", raw)
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
        from core.research.horizon_ridge import pick_y_hat

        y = pick_y_hat("t60", raw)
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
        from core.research.horizon_ridge import pick_y_hat

        y = pick_y_hat("t75", raw)
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
        from core.research.horizon_ridge import pick_y_hat

        y = pick_y_hat("t90", raw)
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
    """ŷ_τw 共用入场幅度（y_tw_enter）。票数门槛。"""
    from core.t0.score_policy import _cfg_float

    if cfg_d.get("y_tw_enter") not in (None, ""):
        return max(0.0, min(float(_cfg_float(cfg_d, "y_tw_enter", DEFAULT_Y_TW_ENTER)), 5.0))
    return DEFAULT_Y_TW_ENTER


def _ytw_enter_for_direction(cfg_d: dict, direction: str) -> float:
    """正/反 T 共用 y_tw_enter。分侧键已下线。"""
    _ = direction
    return _y_tw_enter_floor(cfg_d)


def _lot_amount(raw: Any, default: float = 0.0) -> float:
    try:
        v = float(default if raw in (None, "") else raw)
    except (TypeError, ValueError):
        v = float(default)
    if v != v or v <= 0:
        return 0.0
    return float(v)


def _y_tw_strong_floor(cfg_d: dict) -> float:
    """|ŷ_τw| 强档票数。至少等于入场票。"""
    from core.t0.score_policy import _cfg_float

    enter = _y_tw_enter_floor(cfg_d)
    raw = cfg_d.get("y_tw_strong")
    if raw not in (None, ""):
        try:
            strong = max(0.0, min(float(raw), 5.0))
        except (TypeError, ValueError):
            strong = max(
                0.0,
                min(float(_cfg_float(cfg_d, "y_tw_strong", DEFAULT_Y_TW_STRONG)), 5.0),
            )
        return max(enter, strong)
    return max(enter, DEFAULT_Y_TW_STRONG)


def close_band_y_tw_is_strong(
    y_tw: Optional[float],
    cfg: Optional[dict] = None,
) -> bool:
    if y_tw is None:
        return False
    cfg_d = cfg if isinstance(cfg, dict) else {}
    return abs(float(y_tw)) + 1e-12 >= float(_y_tw_strong_floor(cfg_d))


def close_band_y_tw_round_scale(
    y_tw: Optional[float],
    cfg: Optional[dict] = None,
) -> float:
    if close_band_y_tw_is_strong(y_tw, cfg):
        return 1.0
    return DEFAULT_Y_TW_WEAK_RATIO


def close_band_y_tw_round_shares(
    cfg: Optional[dict],
    *,
    y_tw: Optional[float],
    lot: int = 100,
    price: Optional[float] = None,
) -> Optional[int]:
    """过 ŷ_τw 入场用入场金额/价，过强票用强金额；两边都未配则 None→比例仓。

    只读 ``y_tw_*_amount``。
    """
    from core.paper.sizing import shares_from_amount

    cfg_d = cfg if isinstance(cfg, dict) else {}
    enter = _lot_amount(cfg_d.get("y_tw_enter_amount"), 0.0)
    strong = _lot_amount(cfg_d.get("y_tw_strong_amount"), 0.0)
    if enter <= 0 and strong <= 0:
        return None
    if enter <= 0:
        enter = strong
    if strong <= 0:
        strong = enter
    strong = max(enter, strong)
    amt = float(strong if close_band_y_tw_is_strong(y_tw, cfg_d) else enter)
    return int(shares_from_amount(amt, price, lot))


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


def bar_ytw_pick_direction(
    bar_open: float,
    bar_close: float,
    scores: Optional[dict] = None,
    cfg: Optional[dict] = None,
    bar_low: Optional[float] = None,
    bar_high: Optional[float] = None,
) -> tuple[Optional[str], Dict[str, Any]]:
    """仅 ŷ_τw 门槛选向。

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


def bar_close_band_pick_direction(
    bar_open: float,
    bar_close: float,
    scores: Optional[dict] = None,
    cfg: Optional[dict] = None,
    *,
    open_px: Optional[float] = None,
    price_tau: Optional[float] = None,
    scale: float = 1.0,
    bar_low: Optional[float] = None,
    bar_high: Optional[float] = None,
) -> tuple[Optional[str], Dict[str, Any]]:
    """旗舰选腿：**纯 ŷ_τw**（旧 C_τ 破带估已移除）。

    ``open_px`` / ``price_tau`` / ``scale`` 保留签名兼容，不再用于估价。
    """
    _ = open_px, price_tau, scale
    return bar_ytw_pick_direction(
        bar_open,
        bar_close,
        scores,
        cfg,
        bar_low=bar_low,
        bar_high=bar_high,
    )



@dataclass
class FrozenRound:
    """leg1 成交瞬间冻结的本轮参数。leg2 目标须显式传入（纯 ŷ_τw 默认无）。"""

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
    """冻结本轮。``leg2_target`` 默认 None（无 C_τ 目标价）。"""
    d = str(direction or "").strip().lower()
    c_tau = _f(close_px)
    target = _f(leg2_target)
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
    """做 T 锚点：优先分钟价空间。

    - ``open_px``：分钟**首根** open（缺才回退日线 open）
    - ``session_close``：分钟**末根** close（缺才回退日线 close）
    - ``prev_close``：优先分钟 bar 附带昨收（同复权），再日线

    盘中仅有前缀分钟时，``session_close`` 为当前可见末根，仅供展示。
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
