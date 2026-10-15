"""TopK 研究探针：组合收益 / 暴露 / 止损影子（从 topk_backtest 拆出 · P3）。"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from core.backtest.engine import _trade_metrics


def _weighted_port_return(legs: List[dict], weights: Dict[str, float]) -> float:
    """组合期收益：权重为净值百分比，未分配部分视为现金（收益 0）。

    不再按已选腿归一。等权（权重和=100）与旧口径一致；score_budget
    单票/行业帽留下的现金会压低收益与回撤。
    """
    if not legs:
        return 0.0
    allocated = sum(
        float(weights.get(str(leg["stock_code"])) or 0.0) for leg in legs
    )
    if allocated <= 1e-9:
        return sum(float(leg.get("return_pct") or 0.0) for leg in legs) / len(legs)
    acc = 0.0
    for leg in legs:
        code = str(leg["stock_code"])
        w = float(weights.get(code) or 0.0)
        acc += (w / 100.0) * float(leg.get("return_pct") or 0.0)
    return acc


def _universe_ew_closes(
    date_maps: Dict[str, Dict[str, dict]],
    dates: Sequence[str],
    upto_i: int,
) -> List[float]:
    """截至信号日的池内等权收盘（PIT；供 regime / 高波缩仓）。"""
    closes: List[float] = []
    end = min(int(upto_i) + 1, len(dates))
    for j in range(end):
        d = dates[j]
        xs: List[float] = []
        for dm in date_maps.values():
            bar = dm.get(d) if isinstance(dm, dict) else None
            if not bar:
                continue
            try:
                px = float(bar.get("close") or 0)
            except (TypeError, ValueError):
                continue
            if px > 0:
                xs.append(px)
        if xs:
            closes.append(sum(xs) / len(xs))
    return closes


def _exposure_scale_at_signal(
    date_maps: Dict[str, Dict[str, dict]],
    dates: Sequence[str],
    signal_i: int,
) -> Dict[str, Any]:
    """PIT 高波/弱势 → 仓位上限缩放（不打远端指数）。"""
    from core.backtest.oos_report import regime_label_from_closes
    from core.pro_core import regime_position_scale

    closes = _universe_ew_closes(date_maps, dates, signal_i)
    window = closes[-24:] if len(closes) >= 5 else closes
    label = regime_label_from_closes(window) if len(window) >= 5 else "unknown"
    meta = regime_position_scale(regime={"label": label})
    scale = 1.0
    try:
        scale = max(0.2, min(1.0, float(meta.get("scale") or 1.0)))
    except (TypeError, ValueError):
        scale = 1.0
    return {
        "label": label,
        "scale": scale,
        "source": meta.get("source"),
        "n_closes": len(window),
    }


def _clip_leg_at_close_stop(
    *,
    code: str,
    entry_px: float,
    entry_date: str,
    exit_date: str,
    exit_px: float,
    date_maps: Dict[str, Dict[str, dict]],
    dates: Sequence[str],
    date_i: Dict[str, int],
    stop_pct: float,
) -> Tuple[str, float, float, bool]:
    """持有期收盘触及 −stop 则提前平。返回 (exit_date, exit_px, ret_pct, clipped)。"""
    try:
        px0 = float(entry_px or 0)
        px1 = float(exit_px or 0)
        sp = float(stop_pct or 0)
    except (TypeError, ValueError):
        px0, px1, sp = 0.0, 0.0, 0.0
    raw_ret = (px1 / px0 - 1.0) * 100.0 if px0 > 0 else 0.0
    if sp <= 0 or sp >= 1 or px0 <= 0 or not code:
        return str(exit_date), px1, raw_ret, False
    i0 = date_i.get(str(entry_date))
    i1 = date_i.get(str(exit_date))
    dm = date_maps.get(str(code)) or {}
    if i0 is None or i1 is None or i1 <= i0:
        return str(exit_date), px1, raw_ret, False
    floor_px = px0 * (1.0 - sp)
    for k in range(i0 + 1, i1 + 1):
        bar = dm.get(dates[k])
        if not bar:
            continue
        try:
            px = float(bar.get("close") or 0)
        except (TypeError, ValueError):
            continue
        if px > 0 and px <= floor_px:
            used = (px / px0 - 1.0) * 100.0
            return str(dates[k]), px, used, True
    return str(exit_date), px1, raw_ret, False


def _stop_shadow_from_trades(
    trades: Sequence[dict],
    date_maps: Dict[str, Dict[str, dict]],
    dates: Sequence[str],
    *,
    stop_pct: float,
    holding_days: int,
    applied_to_main: bool = False,
) -> Dict[str, Any]:
    """持有期内触及止损则提前平仓的影子净值。"""
    try:
        sp = float(stop_pct)
    except (TypeError, ValueError):
        sp = 0.0
    if sp <= 0 or sp >= 1:
        return {"ok": False, "reason": "stop_pct_off"}
    date_i = {d: i for i, d in enumerate(dates)}
    shadow_rets: List[float] = []
    clipped = 0
    for t in trades or []:
        legs = list(t.get("legs") or [])
        if not legs:
            continue
        weights = {
            str(leg.get("stock_code") or ""): float(leg.get("weight_pct") or 0.0)
            for leg in legs
        }
        shadow_legs: List[dict] = []
        for leg in legs:
            code = str(leg.get("stock_code") or "")
            try:
                entry_px = float(leg.get("fill_price") or leg.get("entry_price") or 0)
            except (TypeError, ValueError):
                entry_px = 0.0
            try:
                exit_px = float(leg.get("exit_price") or 0)
            except (TypeError, ValueError):
                exit_px = 0.0
            raw_ret = float(leg.get("return_pct") or 0.0)
            if not code or entry_px <= 0:
                shadow_legs.append({"stock_code": code, "return_pct": raw_ret})
                continue
            entry_d = str(leg.get("entry_date") or t.get("entry_date") or "")
            exit_d = str(leg.get("exit_date") or t.get("exit_date") or "")
            if exit_px <= 0 and entry_px > 0:
                exit_px = entry_px * (1.0 + raw_ret / 100.0)
            _ed, _ep, used, hit = _clip_leg_at_close_stop(
                code=code,
                entry_px=entry_px,
                entry_date=entry_d,
                exit_date=exit_d,
                exit_px=exit_px,
                date_maps=date_maps,
                dates=dates,
                date_i=date_i,
                stop_pct=sp,
            )
            if hit:
                clipped += 1
            shadow_legs.append({"stock_code": code, "return_pct": used})
        shadow_rets.append(_weighted_port_return(shadow_legs, weights))
    m = _trade_metrics(shadow_rets, holding_days=holding_days) if shadow_rets else {}
    note = (
        "持有期收盘触及 −stop 则该腿提前平；已并入主回测路径。"
        if applied_to_main
        else "持有期收盘触及 −stop 则该腿提前平；未进主回测路径。"
    )
    return {
        "ok": bool(shadow_rets),
        "stop_pct": sp,
        "legs_clipped": clipped,
        "applied_to_main": bool(applied_to_main),
        "trade_count": m.get("trade_count"),
        "total_return_pct": m.get("total_return_pct"),
        "max_drawdown_pct": m.get("max_drawdown_pct"),
        "win_rate_pct": m.get("win_rate_pct"),
        "note": note,
    }
