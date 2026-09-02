"""多轮独立做 T：固定时钟用前缀 ŷ + 后半占比选向，确认根收盘开第一腿。

独立：该轮 ŷ / 方向、20% 仓、确认根成交、相对本轮成交价的止损线与第二腿。
共用：第二腿触发%、止损%、延迟/收盘确认、fill、午后追价、dual_y 闸（正/反分侧）。
已下线：第一腿 hunt% 搜索窗触价。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.t0.config import apply_side_exec_params
from core.t0.costs import t0_fees_total, t0_leg_cash_delta
from core.t0.rules import (
    _lot_floor,
    _ref_price,
    _skip_result,
    _t0_qty_lots,
    resolve_direction,
)

logger = logging.getLogger(__name__)

_PENDING_PREFIX = "待固定前缀"


def _slot_prefix_bars(mins: Sequence[dict], prefix_n: int) -> List[dict]:
    """槽位前缀窗：开盘起至本钟决策根（约 09:30～当前时钟）的全部 5m K。"""
    n = int(prefix_n or 0)
    if n <= 0:
        return []
    return [b for b in list(mins[:n]) if isinstance(b, dict)]


# 兼容旧名
_slot_morph_bars = _slot_prefix_bars


def slot_specs(cfg: dict) -> List[dict]:
    raw = (cfg or {}).get("t0_slots")
    if isinstance(raw, list) and raw:
        return [dict(s) for s in raw if isinstance(s, dict)]
    from core.t0.config import normalize_t0_slots

    return normalize_t0_slots(None)


def allocate_slot_slices(
    shares: float,
    sellable: float,
    slots: Sequence[dict],
    lot: int,
) -> List[float]:
    """按 ratio 切分可卖整手，余量逐轮回补（避免 <1 手静默浪费）。"""
    lot_i = max(int(lot or 0), 1)
    cap = float(_lot_floor(min(max(float(sellable), 0.0), float(shares)), lot_i))
    if cap < lot_i or not slots:
        return [0.0] * len(slots)
    ratios = [max(float(s.get("ratio") or 0.15), 0.0) for s in slots]
    total_r = sum(ratios) or 1.0
    slices = [float(_lot_floor(cap * (r / total_r), lot_i)) for r in ratios]
    leftover = int(round(cap - sum(slices)))
    idx = 0
    guard = 0
    while leftover >= lot_i and slices and guard < len(slots) * 64:
        j = idx % len(slices)
        slices[j] += float(lot_i)
        leftover -= lot_i
        idx += 1
        guard += 1
    return slices


def _open_leg1_cash_lock(out: dict) -> float:
    """未平正 T 第一腿占用的现金（反 T 卖开不占买侧现金）。"""
    direction = str(out.get("direction_used") or "")
    bought = int(out.get("bought_qty") or 0)
    sold_back = int(out.get("sold_back_qty") or 0)
    if direction != "buy_then_sell" or bought <= sold_back:
        return 0.0
    for t in out.get("trades") or []:
        if not isinstance(t, dict):
            continue
        if str(t.get("side") or "").endswith("buy"):
            return max(0.0, -float(t0_leg_cash_delta(t)))
    return 0.0


def _try_apply_slot_trades(
    trades: Sequence[dict],
    *,
    cash_now: float,
    shares_now: float,
    sellable_old: float,
) -> Tuple[bool, float, float, float]:
    """按时间序试落账一轮成交；失败则状态不变。"""
    ordered = sorted(
        [t for t in (trades or []) if isinstance(t, dict)],
        key=lambda t: str(t.get("at") or ""),
    )
    cash = float(cash_now)
    shares = float(shares_now)
    sellable = float(sellable_old)
    for trade in ordered:
        side = str(trade.get("side") or "")
        qty = float(trade.get("shares") or 0)
        delta = t0_leg_cash_delta(trade)
        if side.endswith("buy"):
            if cash + delta < -1e-6:
                return False, cash_now, shares_now, sellable_old
            shares += qty
        elif side.endswith("sell"):
            if sellable + 1e-9 < qty:
                return False, cash_now, shares_now, sellable_old
            shares -= qty
            sellable -= qty
        cash += delta
    return True, cash, shares, sellable


def _decision_idx(prefix_bars: int) -> int:
    """prefix=0 → -1（开盘、无确认根）；否则最后一根前缀的下标。"""
    n = int(prefix_bars or 0)
    return -1 if n <= 0 else n - 1



def _confirm_idx(prefix_bars: int) -> int:
    """第一腿确认根下标：有前缀取末根；开盘轮取第 0 根。"""
    di = _decision_idx(prefix_bars)
    return 0 if di < 0 else di


def _score_slot(
    *,
    slot: dict,
    mins: Sequence[dict],
    bar: dict,
    cost: float,
    cfg_day: dict,
    cash: float,
    shares: float,
    hist_bars: Optional[Sequence[dict]],
    atr_pct: Optional[float],
    score_snap: Optional[dict],
    stock_code: str,
    tau_pool_day: Optional[dict],
) -> Tuple[Optional[dict], Optional[dict], Optional[dict]]:
    """返回 (plan, pending, skip)。plan 含 direction / cfg_side / 确认根参数。"""
    from core.t0.minute_path import (
        _day_ohlc_from_minutes,
        _score_y_tau,
        _side_exec_pack,
        prefix_fixed_bar_ratio_entry_ok,
        prefix_range_gate,
        tau_leg1_fill_price_ok,
    )
    from core.t0.score_policy import (
        rescore_scores_at_fixed_prefix,
        resolve_cover_policy,
        resolve_fuse_intraday,
    )

    prefix_n = int(slot.get("prefix_bars") or 0)
    n_bars = len(mins)
    from core.t0.config import T0_LAST_LEG1_PREFIX_BARS, _slot_allows_leg1

    hm_slot = str(slot.get("hm") or "")
    if not _slot_allows_leg1(hm=hm_slot, prefix_bars=prefix_n):
        return (
            None,
            None,
            _skip_result(
                reason=f"已过末轮做T时钟（>{T0_LAST_LEG1_PREFIX_BARS}根/11:30），午后不开第一腿",
                shares=shares,
                bar=bar,
                extra=_slot_meta_extra(slot, prefix_bars=prefix_n),
            ),
        )
    if prefix_n > 0 and n_bars < prefix_n:
        return (
            None,
            _skip_result(
                reason=f"{_PENDING_PREFIX} {n_bars}/{prefix_n}",
                shares=shares,
                bar=bar,
                extra={"pending": True, "t0_slot": slot.get("id"), "prefix_bars": n_bars},
            ),
            None,
        )

    live_snap = dict(score_snap) if isinstance(score_snap, dict) else score_snap
    code = str(stock_code or "").strip()
    if prefix_n >= 2 and code and str(cfg_day.get("direction") or "") == "dual_y":
        try:
            live_snap = rescore_scores_at_fixed_prefix(
                stock_code=code,
                minute_prefix=list(mins[:prefix_n]),
                day_bar=bar if isinstance(bar, dict) else None,
                hist_bars=hist_bars,
                tau_pool_day=tau_pool_day,
                fuse_intraday=resolve_fuse_intraday(cfg_day),
                open_snap=live_snap if isinstance(live_snap, dict) else None,
            )
        except Exception:  # noqa: BLE001
            logger.debug("slot prefix rescore failed", exc_info=True)

    if prefix_n <= 0:
        bar_n = bar
        ref = _ref_price(bar, cost, cfg_day)
        range_pct = 0.0
    else:
        prefix = list(mins[:prefix_n])
        gate = prefix_range_gate(prefix, bar, cost=cost, cfg=cfg_day)
        bar_n = gate.get("bar_day") or _day_ohlc_from_minutes(prefix, bar)
        ref = gate.get("ref")
        range_pct = float(gate.get("range_pct") or 0)
        if ref is None or float(ref) <= 0:
            return (
                None,
                None,
                _skip_result(
                    reason="前缀无有效开盘锚",
                    shares=shares,
                    bar=bar_n,
                    extra=_slot_meta_extra(slot, snap=live_snap, prefix_bars=prefix_n),
                ),
            )

    dir_res = resolve_direction(
        bar=bar_n if isinstance(bar_n, dict) else bar,
        ref=float(ref or 0),
        cfg=cfg_day,
        cash=float(cash or 0),
        shares=shares,
        hist_bars=hist_bars,
        atr_pct=atr_pct,
        scores=live_snap,
    )
    if dir_res.get("skip") or not dir_res.get("direction"):
        return (
            None,
            None,
            _skip_result(
                reason=str(dir_res.get("direction_reason") or "选向跳过"),
                shares=shares,
                bar=bar_n if isinstance(bar_n, dict) else bar,
                extra=_slot_meta_extra(
                    slot,
                    snap=live_snap,
                    dir_res=dir_res,
                    signal_skip=True,
                    direction_reason=dir_res.get("direction_reason"),
                ),
            ),
        )

    direction = str(dir_res["direction"])
    cfg_side = apply_side_exec_params(cfg_day, direction)
    cover_meta = None
    if str(cfg_day.get("direction") or "") == "dual_y":
        cover_meta = resolve_cover_policy(
            scores=live_snap or {},
            direction=direction,
            cfg=cfg_side,
        )
        cfg_side["must_cover_same_day"] = bool(cover_meta.get("must_cover"))

    if prefix_n >= 2:
        # 后半阴阳 / τ入场价：一律用开盘→本钟整段前缀；确认根 close = 第一腿价
        prefix_win = _slot_prefix_bars(mins, prefix_n)
        gate_side = prefix_range_gate(prefix_win, bar, cost=cost, cfg=cfg_side)
        # 振幅下限已下线；range_pct 仅诊断
        if gate_side.get("range_pct") is not None:
            range_pct = float(gate_side.get("range_pct") or range_pct)
        seg_key = (
            "y_prefix_segment_enabled_buy_then_sell"
            if direction == "buy_then_sell"
            else "y_prefix_segment_enabled_sell_then_buy"
        )
        if bool(cfg_side.get(seg_key, True)):
            # 后半占比相对整段前缀（N=本钟 prefix_bars），不是固定 6 根
            seg_cfg = dict(cfg_side)
            seg_cfg["y_path_abandon_bars"] = prefix_n
            seg_cfg["y_path_abandon_bars_buy_then_sell"] = prefix_n
            seg_cfg["y_path_abandon_bars_sell_then_buy"] = prefix_n
            seg = prefix_fixed_bar_ratio_entry_ok(
                prefix_win, direction=direction, cfg=seg_cfg
            )
            if not seg.get("ok"):
                return (
                    None,
                    None,
                    _skip_result(
                        reason=str(seg.get("reason") or "固定前缀未过"),
                        shares=shares,
                        bar=bar_n if isinstance(bar_n, dict) else bar,
                        extra=_slot_meta_extra(
                            slot, direction=direction, snap=live_snap, dir_res=dir_res
                        ),
                    ),
                )
        confirm_bar = prefix_win[-1] if prefix_win else None
        try:
            fill_px = float((confirm_bar or {}).get("close") or 0)
        except (TypeError, ValueError):
            fill_px = 0.0
        tau_px = tau_leg1_fill_price_ok(
            fill_px=fill_px,
            ref=float(ref or 0),
            y_tau=_score_y_tau(live_snap if isinstance(live_snap, dict) else None),
            direction=direction,
            cfg=cfg_side,
        )
        if not bool(tau_px.get("ok")):
            return (
                None,
                None,
                _skip_result(
                    reason=str(tau_px.get("reason") or "τ入场价未过"),
                    shares=shares,
                    bar=bar_n if isinstance(bar_n, dict) else bar,
                    extra=_slot_meta_extra(
                        slot, direction=direction, snap=live_snap, dir_res=dir_res
                    ),
                ),
            )

    cfg_side, fill_mode = _side_exec_pack(cfg_side=cfg_side)
    plan = {
        "direction": direction,
        "cfg_side": cfg_side,
        "fill_mode": fill_mode,
        "ref": float(ref or 0),
        "range_pct": float(range_pct or 0),
        "bar_day": bar_n if isinstance(bar_n, dict) else bar,
        "cover_meta": cover_meta,
        "score_snap": live_snap,
        "dir_res": dir_res,
        "prefix_bars": prefix_n,
    }
    return plan, None, None


def simulate_t0_slot(
    *,
    slot: dict,
    next_slot: Optional[dict],
    minute_bars: Sequence[dict],
    bar: dict,
    shares: float,
    cost: float,
    sellable_shares: Optional[float],
    cfg_day: dict,
    cash: float,
    stock_code: str,
    lot: int,
    cost_model: str,
    cost_params: dict,
    atr_pct: Optional[float],
    hist_bars: Optional[Sequence[dict]],
    score_snap: Optional[dict],
    session_bar: Optional[dict] = None,
    defer_eod: bool = True,
    tau_pool_day: Optional[dict] = None,
) -> Dict[str, Any]:
    """单轮：齐前缀独立 dual_y → 确认根收盘第一腿 → 独立第二腿。"""
    from core.t0.minute_path import (
        _day_ohlc_from_minutes,
        _first_touch_buy_then_sell,
        _first_touch_sell_then_buy,
        _set_forward_trace_on_result,
    )

    mins = list(minute_bars)
    sid = str(slot.get("id") or "")
    _ = next_slot  # 保留签名；第一腿不再用搜索窗
    n_bars = len(mins)
    confirm_idx = _confirm_idx(int(slot.get("prefix_bars") or 0))

    plan, pending, early = _score_slot(
        slot=slot,
        mins=mins,
        bar=bar,
        cost=cost,
        cfg_day=cfg_day,
        cash=cash,
        shares=shares,
        hist_bars=hist_bars,
        atr_pct=atr_pct,
        score_snap=score_snap,
        stock_code=stock_code,
        tau_pool_day=tau_pool_day,
    )
    hm = str(slot.get("hm") or "")

    def _stamp(out: dict) -> dict:
        out["t0_slot"] = sid
        if hm:
            out["t0_slot_hm"] = hm
        # 拟合对齐：该钟因果 ŷ 写入结果（跳过/待定/成交共用）
        return attach_slot_fit_portrait_scores(
            out,
            slot=slot,
            minute_bars=mins,
            bar=bar if isinstance(bar, dict) else None,
            hist_bars=hist_bars,
            open_snap=score_snap if isinstance(score_snap, dict) else None,
            stock_code=stock_code,
            tau_pool_day=tau_pool_day,
            cfg_day=cfg_day,
        )

    if pending is not None:
        pending["pending"] = True
        return _stamp(pending)
    if early is not None:
        return _stamp(early)
    if plan is None:
        return _stamp(
            _skip_result(
                reason="槽位无法选向",
                shares=shares,
                bar=bar,
                extra={"t0_slot": sid},
            )
        )

    # 确认根尚未到达（盘中递进）：等下一根
    if n_bars < confirm_idx + 1:
        out = _skip_result(
            reason=_PENDING_PREFIX,
            shares=shares,
            bar=bar,
            extra={"pending": True, "t0_slot": sid, "direction_used": plan["direction"]},
        )
        out["_t0_score_snap"] = plan.get("score_snap")
        return _stamp(out)

    cfg_slot = dict(plan["cfg_side"])
    ratio = float(slot.get("ratio") or 0.20)
    cfg_slot["t0_ratio"] = ratio
    bar_session = session_bar or _day_ohlc_from_minutes(mins, bar)
    path_kwargs = {
        "session_bars": mins,
        "session_bar": bar_session,
        "defer_eod": bool(defer_eod),
        "leg1_gate_at": (lambda i, ci=confirm_idx: i == ci),
    }
    direction = str(plan["direction"])
    from core.t0.minute_path import _score_y_tau

    slot_y_tau = _score_y_tau(
        plan.get("score_snap") if isinstance(plan.get("score_snap"), dict) else score_snap
    )
    path_kwargs["y_tau"] = slot_y_tau
    if direction == "buy_then_sell":
        out = _first_touch_buy_then_sell(
            minute_bars=mins,
            bar=plan["bar_day"],
            shares=shares,
            cash=float(cash or 0),
            sellable_shares=sellable_shares,
            ref=float(plan["ref"]),
            lot=lot,
            fill_mode=str(plan["fill_mode"]),
            cfg=cfg_slot,
            cost_model=cost_model,
            cost_params=cost_params,
            stock_code=stock_code,
            atr_pct=atr_pct,
            range_pct=float(plan["range_pct"]),
            **path_kwargs,
        )
    else:
        out = _first_touch_sell_then_buy(
            minute_bars=mins,
            bar=plan["bar_day"],
            shares=shares,
            sellable_shares=sellable_shares,
            ref=float(plan["ref"]),
            lot=lot,
            fill_mode=str(plan["fill_mode"]),
            cfg=cfg_slot,
            cost_model=cost_model,
            cost_params=cost_params,
            stock_code=stock_code,
            atr_pct=atr_pct,
            range_pct=float(plan["range_pct"]),
            t0_ratio=ratio,
            cash=float(cash or 0),
            **path_kwargs,
        )

    if not isinstance(out, dict):
        return _stamp(
            _skip_result(reason="槽位路径失败", shares=shares, bar=bar, extra={"t0_slot": sid})
        )

    out["t0_slot"] = sid
    out["t0_slot_hm"] = str(slot.get("hm") or "")
    out["prefix_bars"] = int(plan.get("prefix_bars") or 0)
    out["t0_ratio"] = ratio
    out["direction_used"] = direction
    out["path_mode"] = "first_touch"
    out["range_mode"] = "slot_confirm"
    if isinstance(plan.get("score_snap"), dict):
        out["_t0_score_snap"] = plan["score_snap"]
    dir_res = plan.get("dir_res") or {}
    out["direction_score"] = dir_res.get("direction_score")
    out["direction_reason"] = dir_res.get("direction_reason")
    if isinstance(dir_res.get("features"), dict):
        out["direction_features"] = dir_res.get("features")
    cover_meta = plan.get("cover_meta")
    if cover_meta:
        out["cover_policy"] = cover_meta
        out["must_cover_same_day"] = bool(cover_meta.get("must_cover"))
    _set_forward_trace_on_result(
        out,
        [],
        cfg_day=cfg_day,
        direction=direction,
        dir_res=dir_res,
    )

    for t in out.get("trades") or []:
        if isinstance(t, dict):
            t["t0_slot"] = sid
            t["t0_slot_hm"] = str(slot.get("hm") or "")
    return _stamp(out)


def _rollback_slot_fills(
    *,
    fills: Sequence[Tuple[str, float, float]],
    cash_now: float,
    shares_now: float,
    sellable_old: float,
) -> Tuple[float, float, float]:
    """撤销一整轮已落账成交（T+1 可卖与现金一并还原）。"""
    for side, qty, delta in reversed(list(fills or [])):
        cash_now -= delta
        if str(side).endswith("buy"):
            shares_now -= qty
        else:
            shares_now += qty
            sellable_old += qty
    return cash_now, shares_now, sellable_old


def _slot_session_trace(
    minute_bars: Optional[Sequence[dict]],
    trades: Sequence[dict],
) -> List[dict]:
    """全日 5m 轨迹：按实际买卖腿打标（多轮/混合向也能画 tip K 线）。"""
    from core.t0.minute_path import _forward_trace_row

    rows: List[dict] = []
    for i, m in enumerate(minute_bars or []):
        if not isinstance(m, dict):
            continue
        row = _forward_trace_row(idx=i, m=m, prefix_bars=0, evaluated=True)
        row["buy_fill"] = False
        row["sell_fill"] = False
        rows.append(row)
    for t in trades or []:
        if not isinstance(t, dict):
            continue
        side = str(t.get("side") or "").lower()
        at = str(t.get("at") or "")
        if not at:
            continue
        is_buy = side.endswith("buy")
        is_sell = side.endswith("sell")
        hm = at[11:16] if len(at) >= 16 else ""
        for row in rows:
            dt = str(row.get("datetime") or "")
            tm = str(row.get("time") or "")
            if (at and at == dt) or (hm and hm == tm):
                if is_buy:
                    row["buy_fill"] = True
                if is_sell:
                    row["sell_fill"] = True
                break
    return rows


def _slot_meta_extra(
    slot: dict,
    *,
    direction: Optional[str] = None,
    snap: Any = None,
    dir_res: Optional[dict] = None,
    **more: Any,
) -> dict:
    """跳过/待定轮也带上本轮 ŷ，供跳过饼图与过程列读取。"""
    extra: dict = {"t0_slot": slot.get("id"), "t0_slot_hm": slot.get("hm")}
    extra.update(more)
    if direction:
        extra["direction_used"] = direction
    if isinstance(snap, dict):
        extra["_t0_score_snap"] = snap
    feats = (dir_res or {}).get("features") if isinstance(dir_res, dict) else None
    if isinstance(feats, dict):
        extra["direction_features"] = feats
    return extra


def _promote_causal_portrait_fields(packed: dict) -> dict:
    """因果前缀快照显式写出画像字段，供分槽命中与拟合 by_tau 同口径。"""
    out = dict(packed)
    src = str(out.get("_score_source") or "")
    if src == "prefix_causal" or out.get("y_tau_portrait_oc") is not None or out.get(
        "y_path_portrait"
    ) is not None:
        if out.get("y_tau_portrait_oc") is None:
            for k in ("y_tau_oc", "y_tau", "predicted_score_tau_oc"):
                if out.get(k) is not None:
                    out["y_tau_portrait_oc"] = out.get(k)
                    break
        if out.get("y_path_portrait") is None and out.get("y_path") is not None:
            out["y_path_portrait"] = out.get("y_path")
    return out


def attach_slot_fit_portrait_scores(
    out: dict,
    *,
    slot: dict,
    minute_bars: Sequence[dict],
    bar: Optional[dict],
    hist_bars: Optional[Sequence[dict]] = None,
    open_snap: Optional[dict] = None,
    stock_code: str = "",
    tau_pool_day: Optional[dict] = None,
    cfg_day: Optional[dict] = None,
) -> dict:
    """槽位结果写入该钟因果 ŷ（与拟合 OOS.by_tau 同信息集）。

    不论成交/跳过：只要分钟齐到 ``prefix_bars``，就补 ``y_tau_portrait_oc`` /
    ``y_path_portrait``，供回测画像分槽命中率（全样本预估准确性）。
    """
    row = dict(out or {})
    prefix_n = int(slot.get("prefix_bars") or 0)
    hm = str(slot.get("hm") or row.get("t0_slot_hm") or "")[:5]
    mins = [b for b in (minute_bars or []) if isinstance(b, dict)]
    if prefix_n < 2 or len(mins) < prefix_n or not isinstance(bar, dict):
        return row

    snap = row.get("_t0_score_snap") if isinstance(row.get("_t0_score_snap"), dict) else None
    if snap is None and isinstance(open_snap, dict):
        snap = dict(open_snap)
    if snap is None and isinstance(row.get("scores"), dict):
        snap = dict(row.get("scores") or {})

    try:
        from core.t0.score_policy import (
            attach_portrait_dual_scores,
            pack_day_scores,
            rescore_scores_at_fixed_prefix,
            resolve_fuse_intraday,
            scores_have_any,
        )

        live = dict(snap) if isinstance(snap, dict) else {}
        # 尚无因果源：按本钟前缀重算（跳过/预留不足路径也要对齐拟合钟）
        need_rescore = str(live.get("_score_source") or "") != "prefix_causal"
        code = str(stock_code or row.get("stock_code") or "").strip()
        cfg = cfg_day if isinstance(cfg_day, dict) else {}
        if need_rescore and code:
            try:
                live = rescore_scores_at_fixed_prefix(
                    stock_code=code,
                    minute_prefix=list(mins[:prefix_n]),
                    day_bar=bar,
                    hist_bars=hist_bars,
                    tau_pool_day=tau_pool_day,
                    fuse_intraday=resolve_fuse_intraday(cfg),
                    open_snap=live if scores_have_any(live) else None,
                )
            except Exception:  # noqa: BLE001
                logger.debug("slot fit rescore failed", exc_info=True)

        packed_day = attach_portrait_dual_scores(
            {"scores": dict(row.get("scores") or {})},
            live if isinstance(live, dict) else {},
            minute_bars=mins,
            day_bar=bar,
            hist_bars=hist_bars,
            prefix_bars=prefix_n,
            tau_hm=hm or "10:00",
        )
        sc = (
            dict(packed_day.get("scores") or {})
            if isinstance(packed_day.get("scores"), dict)
            else {}
        )
        for k in ("y_tau_portrait_oc", "y_path_portrait", "portrait_prefix_bars", "portrait_prefix_hm"):
            if packed_day.get(k) is not None:
                sc[k] = packed_day.get(k)
                live[k] = packed_day.get(k)
        if str(live.get("_score_source") or "") != "prefix_causal":
            # 仍标记为该钟前缀画像（拟合对照），即使重算失败只靠 open_snap 预测
            if sc.get("y_tau_portrait_oc") is not None or sc.get("y_path_portrait") is not None:
                live["_score_source"] = live.get("_score_source") or "slot_portrait"
                live["_score_prefix_hm"] = hm
                live["_score_prefix_bars"] = prefix_n
                sc["_score_source"] = live["_score_source"]
                sc["_score_prefix_hm"] = hm
                sc["_score_prefix_bars"] = prefix_n
        sc = _promote_causal_portrait_fields(sc)
        live = _promote_causal_portrait_fields(live)
        pub = pack_day_scores(live) or pack_day_scores(sc)
        if pub:
            pub = _promote_causal_portrait_fields(pub)
            row["scores"] = pub
        elif sc:
            row["scores"] = sc
        if live:
            row["_t0_score_snap"] = live
    except Exception:  # noqa: BLE001
        logger.debug("attach_slot_fit_portrait_scores failed", exc_info=True)
    return row


def _slot_public_scores(row: dict) -> dict:
    """槽位对外 ŷ：供成交明细 / 归因按轮读取，避免被日级最后一轮覆盖。"""
    extra: dict = {}
    existing = row.get("scores") if isinstance(row.get("scores"), dict) else None
    snap = row.get("_t0_score_snap") if isinstance(row.get("_t0_score_snap"), dict) else None
    feats = row.get("direction_features") if isinstance(row.get("direction_features"), dict) else None
    packed = None
    try:
        from core.t0.score_policy import pack_day_scores

        packed = pack_day_scores(snap)
        if packed is None:
            packed = pack_day_scores(feats)
        if packed is None:
            packed = pack_day_scores(existing)
    except Exception:
        packed = None
    if packed:
        extra["scores"] = _promote_causal_portrait_fields(packed)
    elif existing:
        extra["scores"] = _promote_causal_portrait_fields(dict(existing))
    if feats:
        slim = {}
        for k in (
            "y_tau",
            "y_path",
            "y_eod",
            "y_trade",
            "y_on",
            "y_nc",
            "gap_pct",
            "y_tau_oc",
            "y_tau_portrait_oc",
            "y_path_portrait",
        ):
            if feats.get(k) is not None:
                slim[k] = feats[k]
        if slim:
            extra["direction_features"] = slim
    return extra


def _merge_slot_day(
    *,
    slot_outs: Sequence[dict],
    bar: dict,
    shares: float,
    cash: float,
    sellable_shares: Optional[float] = None,
    minute_bars: Optional[Sequence[dict]] = None,
) -> Dict[str, Any]:
    """按时间合并各轮成交；现金/可卖不够则整轮回滚丢弃。卖出只动日初可卖（T+1）。"""
    tagged: List[Tuple[str, dict, str]] = []
    for out in slot_outs:
        sid = str((out or {}).get("t0_slot") or "")
        for t in (out or {}).get("trades") or []:
            if isinstance(t, dict):
                tagged.append((str(t.get("at") or ""), dict(t), sid))
    tagged.sort(key=lambda x: x[0])

    dropped = set()
    cash_now = float(cash or 0)
    shares_now = float(shares)
    sellable_cap = float(
        sellable_shares if sellable_shares is not None else shares
    )
    sellable_old = min(max(sellable_cap, 0.0), float(shares))
    applied: Dict[str, List[Tuple[str, float, float]]] = {}
    for _at, trade, sid in tagged:
        if sid in dropped:
            continue
        side = str(trade.get("side") or "")
        qty = float(trade.get("shares") or 0)
        delta = t0_leg_cash_delta(trade)
        ok = True
        if side.endswith("buy"):
            if cash_now + delta < -1e-6:
                ok = False
        elif sellable_old + 1e-9 < qty:
            ok = False
        if not ok:
            cash_now, shares_now, sellable_old = _rollback_slot_fills(
                fills=applied.get(sid) or [],
                cash_now=cash_now,
                shares_now=shares_now,
                sellable_old=sellable_old,
            )
            dropped.add(sid)
            applied.pop(sid, None)
            continue
        if side.endswith("buy"):
            shares_now += qty
        else:
            shares_now -= qty
            sellable_old -= qty
        cash_now += delta
        applied.setdefault(sid, []).append((side, qty, delta))

    trades: List[dict] = []
    for _at, trade, sid in tagged:
        if sid not in dropped:
            trades.append(trade)

    dirs = []
    slot_rows = []
    exposure_pnl = 0.0
    snap = None
    prefix_bars = None
    dir_score = None
    dir_reason = None
    dir_feats = None
    cover_policy = None
    must_cover = None
    filled_sids = {sid for _at, _t, sid in tagged if sid not in dropped}
    for out in slot_outs:
        row = dict(out)
        sid = str(row.get("t0_slot") or "")
        if sid in dropped:
            row = _skip_result(
                reason="现金或可卖不足，本轮未落账",
                shares=shares,
                bar=bar,
                extra={"t0_slot": sid, "direction_used": row.get("direction_used")},
            )
        kept = sid not in dropped
        if kept:
            exposure_pnl += float(row.get("exposure_pnl") or 0)
            if sid in filled_sids:
                if isinstance(row.get("_t0_score_snap"), dict) and snap is None:
                    snap = row.get("_t0_score_snap")
                if row.get("prefix_bars") is not None and prefix_bars is None:
                    prefix_bars = row.get("prefix_bars")
                if row.get("direction_score") is not None and dir_score is None:
                    dir_score = row.get("direction_score")
                if row.get("direction_reason") and dir_reason is None:
                    dir_reason = row.get("direction_reason")
                if isinstance(row.get("direction_features"), dict) and dir_feats is None:
                    dir_feats = row.get("direction_features")
                if isinstance(row.get("cover_policy"), dict) and cover_policy is None:
                    cover_policy = row.get("cover_policy")
                if row.get("must_cover_same_day") is not None and must_cover is None:
                    must_cover = row.get("must_cover_same_day")
        slot_rec = {
            "id": sid,
            "hm": row.get("t0_slot_hm") or row.get("hm"),
            "skipped": bool(row.get("skipped")),
            "pending": bool(row.get("pending")),
            "reason": row.get("reason"),
            "direction": row.get("direction_used"),
            "trades": len(row.get("trades") or []) if kept else 0,
            "sold_qty": int(row.get("sold_qty") or 0) if kept else 0,
            "covered_qty": int(row.get("covered_qty") or 0) if kept else 0,
            "uncovered_qty": int(row.get("uncovered_qty") or 0) if kept else 0,
            "bought_qty": int(row.get("bought_qty") or 0) if kept else 0,
            "sold_back_qty": int(row.get("sold_back_qty") or 0) if kept else 0,
            "pnl": row.get("pnl") if kept else 0,
            "exposure_pnl": row.get("exposure_pnl") if kept else 0,
            "exit_reason": row.get("exit_reason") if kept else None,
        }
        slot_rec.update(_slot_public_scores(row))
        slot_rows.append(slot_rec)
        if kept and not row.get("skipped") and row.get("direction_used"):
            dirs.append(str(row.get("direction_used")))

    if snap is None:
        for out in slot_outs:
            sid = str((out or {}).get("t0_slot") or "")
            if sid in dropped:
                continue
            if isinstance((out or {}).get("_t0_score_snap"), dict):
                snap = (out or {}).get("_t0_score_snap")

    uniq = list(dict.fromkeys(dirs))
    direction_used = uniq[0] if len(uniq) == 1 else ("mixed" if uniq else None)
    sold_qty = 0
    covered_qty = 0
    uncovered_qty = 0
    bought_qty = 0
    sold_back_qty = 0
    for r in slot_rows:
        d = str(r.get("direction") or "")
        if d == "sell_then_buy":
            sold_qty += int(r.get("sold_qty") or 0)
            covered_qty += int(r.get("covered_qty") or 0)
            uncovered_qty += int(r.get("uncovered_qty") or 0)
        elif d == "buy_then_sell":
            bought_qty += int(r.get("bought_qty") or 0)
            sold_back_qty += int(r.get("sold_back_qty") or 0)
    any_pending = any(bool(o.get("pending")) for o in slot_outs if str(o.get("t0_slot") or "") not in dropped)
    any_fill = bool(trades)
    # 已实现 PnL 按轮加总（未回补轮为 0 + exposure）。禁止对全日 trades 做现金求和：
    # 单腿卖出会把整段成交额记成盈利（卫通/工行那种 +99%）。
    pnl = round(sum(float(r.get("pnl") or 0) for r in slot_rows), 2)
    exposure_pnl = round(exposure_pnl, 2)
    leg1_notional = 0.0
    seen_leg1 = set()
    for _at, trade, sid in tagged:
        if sid in dropped or sid in seen_leg1:
            continue
        seen_leg1.add(sid)
        px = float(trade.get("price") or 0)
        q = float(trade.get("shares") or 0)
        if px > 0 and q > 0:
            leg1_notional += px * q
    net = float(pnl or 0) + float(exposure_pnl or 0)
    day_return_pct = (
        round(net / leg1_notional * 100.0, 4) if leg1_notional > 1e-9 else None
    )
    merged: Dict[str, Any] = {
        "success": True,
        "skipped": not any_fill and not any_pending,
        "reason": None if any_fill else ("多轮均未成交" if not any_pending else _PENDING_PREFIX),
        "pending": bool(any_pending and not any_fill),
        "date": (bar or {}).get("date"),
        "trades": trades,
        "pnl": pnl,
        "fees_total": t0_fees_total(trades),
        "cash_delta": round(sum(t0_leg_cash_delta(t) for t in trades), 2),
        "shares_end": round(shares_now, 4),
        "sold_qty": sold_qty,
        "bought_qty": bought_qty,
        "covered_qty": covered_qty,
        "uncovered_qty": uncovered_qty,
        "sold_back_qty": sold_back_qty,
        "exposure_pnl": exposure_pnl,
        "direction_used": direction_used,
        "path_mode": "first_touch",
        "range_mode": "slot_confirm",
        "t0_slots_enabled": True,
        "t0_slot_results": slot_rows,
        "open": (bar or {}).get("open"),
        "close": (bar or {}).get("close"),
        "day_return_pct": day_return_pct,
        "leg1_notional": round(leg1_notional, 2) if leg1_notional else 0,
    }
    if any_fill:
        merged["forward_trace"] = _slot_session_trace(minute_bars, trades)
    if snap is not None:
        merged["_t0_score_snap"] = snap
    if prefix_bars is not None:
        merged["prefix_bars"] = prefix_bars
    if dir_score is not None:
        merged["direction_score"] = dir_score
    if dir_reason is not None:
        merged["direction_reason"] = dir_reason
    if dir_feats is not None:
        merged["direction_features"] = dir_feats
    if cover_policy is not None:
        merged["cover_policy"] = cover_policy
    if must_cover is not None:
        merged["must_cover_same_day"] = must_cover
    return merged


def simulate_t0_day_slots(
    *,
    bar: dict,
    minute_bars: Sequence[dict],
    shares: float,
    cost: float,
    sellable_shares: Optional[float],
    cfg: dict,
    cash: float,
    stock_code: str,
    lot: int,
    cost_model: str,
    cost_params: dict,
    atr_pct: Optional[float],
    hist_bars: Optional[Sequence[dict]],
    score_snap: Optional[dict],
    session_bar: Optional[dict] = None,
    defer_eod: bool = False,
    tau_pool_day: Optional[dict] = None,
) -> Dict[str, Any]:
    """全日：多轮独立预估 + 确认根第一腿 + 各轮第二腿。"""
    slots = slot_specs(cfg)
    sellable_cap = float(sellable_shares if sellable_shares is not None else shares)
    sellable_cap = min(max(sellable_cap, 0.0), float(shares))
    slices = allocate_slot_slices(shares, sellable_cap, slots, lot)
    outs: List[dict] = []
    cash_now = float(cash or 0)
    shares_now = float(shares)
    sellable_now = float(sellable_cap)
    cash_locked = 0.0
    for i, slot in enumerate(slots):
        slice_qty = min(float(slices[i]), sellable_now)
        if slice_qty < lot:
            skip = _skip_result(
                reason="本轮预留不足 1 手",
                shares=shares,
                bar=bar,
                extra={"t0_slot": slot.get("id"), "t0_slot_hm": slot.get("hm")},
            )
            outs.append(
                attach_slot_fit_portrait_scores(
                    skip,
                    slot=slot,
                    minute_bars=minute_bars,
                    bar=bar if isinstance(bar, dict) else None,
                    hist_bars=hist_bars,
                    open_snap=score_snap if isinstance(score_snap, dict) else None,
                    stock_code=stock_code,
                    tau_pool_day=tau_pool_day,
                    cfg_day=cfg,
                )
            )
            continue
        nxt = slots[i + 1] if i + 1 < len(slots) else None
        avail_cash = max(0.0, cash_now - cash_locked)
        out = simulate_t0_slot(
            slot=slot,
            next_slot=nxt,
            minute_bars=minute_bars,
            bar=bar,
            shares=shares,
            cost=cost,
            sellable_shares=slice_qty,
            cfg_day=cfg,
            cash=avail_cash,
            stock_code=stock_code,
            lot=lot,
            cost_model=cost_model,
            cost_params=cost_params,
            atr_pct=atr_pct,
            hist_bars=hist_bars,
            score_snap=score_snap,
            session_bar=session_bar,
            defer_eod=defer_eod,
            tau_pool_day=tau_pool_day,
        )
        trades = list(out.get("trades") or [])
        if trades:
            ok, nc, ns, nsel = _try_apply_slot_trades(
                trades,
                cash_now=cash_now,
                shares_now=shares_now,
                sellable_old=sellable_now,
            )
            if not ok:
                out = _skip_result(
                    reason="现金或可卖不足，本轮未落账",
                    shares=shares,
                    bar=bar,
                    extra={
                        "t0_slot": slot.get("id"),
                        "t0_slot_hm": slot.get("hm"),
                        "direction_used": out.get("direction_used"),
                    },
                )
                out = attach_slot_fit_portrait_scores(
                    out,
                    slot=slot,
                    minute_bars=minute_bars,
                    bar=bar if isinstance(bar, dict) else None,
                    hist_bars=hist_bars,
                    open_snap=score_snap if isinstance(score_snap, dict) else None,
                    stock_code=stock_code,
                    tau_pool_day=tau_pool_day,
                    cfg_day=cfg,
                )
            else:
                cash_now, shares_now, sellable_now = nc, ns, nsel
                cash_locked += _open_leg1_cash_lock(out)
        else:
            cash_locked += _open_leg1_cash_lock(out)
        outs.append(out)
    return _merge_slot_day(
        slot_outs=outs,
        bar=bar,
        shares=shares,
        cash=cash,
        sellable_shares=sellable_cap,
        minute_bars=minute_bars,
    )


__all__ = [
    "allocate_slot_slices",
    "simulate_t0_day_slots",
    "simulate_t0_slot",
    "slot_specs",
]
