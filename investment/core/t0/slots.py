"""v6 收盘带宽多轮做 T。

逐根用前缀 ŷ_oc 估 ĉ，收价相对 ĉ 的超额带宽开 leg1（每轮 ratio，累计至 max_pos）；
11:00 后不开 leg1。ŷ_τc 只对照、不参与估 ĉ / 选腿 / 目标价。
共用：第二腿触发%、止损%、延迟/收盘确认、fill、午后追价。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.t0.config import apply_side_exec_params
from core.t0.costs import t0_fees_total, t0_leg_cash_delta
from core.t0.rules import (
    _lot_floor,
    _skip_result,
)

logger = logging.getLogger(__name__)

_PENDING_PREFIX = "多轮待成交"


def _is_scan_tip_key(key: str) -> bool:
    """扫描行只贴该钟因子拆解；不要把日级 ŷ 标量或隔夜项盖进去。"""
    if key in {"as_of_tau", "gap_pct", "features_tau"}:
        return True
    if key.startswith("y_spec"):
        return True
    if key.endswith("_on"):
        return False
    return key.startswith(("formula_terms_", "score_formula_terms_"))


def _attach_scan_row_tip_fields(row: dict, live_snap: Optional[dict]) -> None:
    """扫描行带上该钟前缀因子；否则 tip 会落到日级 scores（预演 09:45 vs 回测 10:40）。"""
    if not isinstance(row, dict) or not isinstance(live_snap, dict):
        return
    try:
        from core.t0.score_policy import tip_fields_from_item

        tips = tip_fields_from_item(live_snap)
    except Exception:  # noqa: BLE001
        logger.debug("scan row tip fields failed", exc_info=True)
        return
    if not isinstance(tips, dict):
        return
    for k, v in tips.items():
        if v is not None and _is_scan_tip_key(str(k)):
            row[k] = v
    if row.get("as_of_tau") is None and row.get("hm"):
        row["as_of_tau"] = row.get("hm")


def _norm_trade_at(raw: Any) -> str:
    """统一成交/分钟时间串，避免 ``YYYY-MM-DD HH:MM`` 与 ``…T…`` 字典序前窥。"""
    s = str(raw or "").strip().replace("T", " ")
    if len(s) >= 19:
        return s[:19]
    if len(s) >= 16 and s[10] == " " and s[13] == ":":
        return s[:16] + ":00"
    return s


def _slot_trade_legs(row: Optional[dict]) -> List[dict]:
    """槽位成交腿：优先 ``trade_legs``；兼容旧 list ``trades``（int 计数忽略）。"""
    if not isinstance(row, dict):
        return []
    legs = row.get("trade_legs")
    if isinstance(legs, list):
        return [t for t in legs if isinstance(t, dict)]
    raw = row.get("trades")
    if isinstance(raw, list):
        return [t for t in raw if isinstance(t, dict)]
    return []


def slot_budget_t0_ratio(
    shares: float,
    sellable_budget: Optional[float],
    fallback_ratio: float,
) -> float:
    """槽位动仓比例：以本轮可卖预算为准（滚仓后预算可大于 slot.ratio×持仓）。"""
    try:
        fb = float(fallback_ratio)
    except (TypeError, ValueError):
        fb = 0.15
    fb = max(0.0, min(fb, 1.0))
    sh = max(float(shares or 0), 0.0)
    if sellable_budget is None or sh <= 0:
        return fb
    try:
        budget = float(sellable_budget)
    except (TypeError, ValueError):
        return fb
    if budget <= 0:
        return 0.0
    return min(1.0, budget / sh)


def _parse_max_rounds(cfg: Optional[dict], n_slots: int) -> int:
    """``t0_slots_max_rounds``：缺省=槽位数；0=不开第一腿。"""
    default = max(int(n_slots or 0), 1)
    raw = (cfg or {}).get("t0_slots_max_rounds")
    if raw is None or raw == "":
        return min(default, 16)
    try:
        return max(0, min(int(raw), 16))
    except (TypeError, ValueError):
        return min(default, 16)


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
        key=lambda t: _norm_trade_at(t.get("at")),
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


def _sid_first_at(tagged: Sequence[Tuple[str, dict, str]], sid: str) -> str:
    """该轮第一笔成交时间（开仓先后）。"""
    key = str(sid or "")
    for at, _t, s in tagged:
        if str(s) == key:
            return str(at or "")
    return ""


def _sid_open_rank(tagged: Sequence[Tuple[str, dict, str]], sid: str) -> Tuple[str, int, str]:
    """开仓序：先墙钟，再 rN / sN 编号。编号越大越晚开。"""
    s = str(sid or "")
    n = 0
    i = len(s) - 1
    while i >= 0 and s[i].isdigit():
        i -= 1
    if i < len(s) - 1:
        try:
            n = int(s[i + 1 :])
        except ValueError:
            n = 0
    return (_sid_first_at(tagged, s), n, s)


def _latest_open_sid(tagged: Sequence[Tuple[str, dict, str]], remaining: set) -> Optional[str]:
    """剩余轮里最晚开的一笔。现金/可卖不够时丢掉后轮，不撤已发生的第一笔。"""
    left = [str(s) for s in remaining if s]
    if not left:
        return None
    return max(left, key=lambda s: _sid_open_rank(tagged, s))


def _settle_tagged_slot_legs(
    tagged: Sequence[Tuple[str, dict, str]],
    *,
    cash: float,
    shares: float,
    sellable_old: float,
) -> Tuple[set, float, float]:
    """按墙钟落账。现金/可卖不够时丢掉**最晚开**的一轮，从日初重放，直到稳定。

    正 T 第二腿卖的是底仓：后轮下午先卖光可卖时，不能把先开的第一轮整笔撤掉。
    """
    dropped: set = set()
    n_unique = len({sid for _at, _t, sid in tagged})
    cash_now = float(cash or 0)
    shares_now = float(shares)
    for _ in range(max(n_unique, 1) + 1):
        cash_now = float(cash or 0)
        shares_now = float(shares)
        sellable_now = float(sellable_old)
        failed = False
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
            elif sellable_now + 1e-9 < qty:
                ok = False
            if not ok:
                failed = True
                break
            if side.endswith("buy"):
                shares_now += qty
            else:
                shares_now -= qty
                sellable_now -= qty
            cash_now += delta
        if not failed:
            return dropped, cash_now, shares_now
        remaining = {str(s) for _a, _t, s in tagged if s not in dropped}
        latest = _latest_open_sid(tagged, remaining)
        if not latest:
            break
        dropped.add(latest)
    return dropped, cash_now, shares_now


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
    ) is not None or out.get("y_hl_portrait") is not None:
        if out.get("y_tau_portrait_oc") is None:
            for k in ("y_tau_oc", "y_tau", "predicted_score_tau_oc"):
                if out.get(k) is not None:
                    out["y_tau_portrait_oc"] = out.get(k)
                    break
        if out.get("y_hl_portrait") is None:
            yp = out.get("y_hl") if out.get("y_hl") is not None else out.get("y_path")
            if yp is None:
                yp = out.get("y_path_portrait")
            if yp is not None:
                out["y_hl_portrait"] = yp
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
    # ≥1 根即可（含 09:35 首根）；09:30 开盘 Z 走 open_snap，不经此前缀重算
    if prefix_n < 1 or len(mins) < prefix_n or not isinstance(bar, dict):
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
        for k in ("y_tau_portrait_oc", "y_hl_portrait", "y_path_portrait", "portrait_prefix_bars", "portrait_prefix_hm"):
            if packed_day.get(k) is not None:
                sc[k] = packed_day.get(k)
                live[k] = packed_day.get(k)
        if str(live.get("_score_source") or "") != "prefix_causal":
            # 仍标记为该钟前缀画像（拟合对照），即使重算失败只靠 open_snap 预测
            if sc.get("y_tau_portrait_oc") is not None or sc.get("y_hl_portrait") is not None or sc.get("y_path_portrait") is not None:
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


def _close_band_public_scores(
    *,
    gate_snap: Optional[dict],
    gate_y_tau: Optional[float],
) -> Optional[dict]:
    """成交明细：y_τ/Ĉ 与该根前缀 gate 同源（开盘 Z + ≤该根分钟因果 ŷ_τ）。"""
    if not isinstance(gate_snap, dict) or not gate_snap:
        return None
    base = dict(gate_snap)
    if gate_y_tau is not None:
        try:
            yt = float(gate_y_tau)
        except (TypeError, ValueError):
            yt = None
        if yt is not None:
            base["y_tau"] = yt
            if base.get("y_tau_oc") is None:
                base["y_tau_oc"] = yt
    base["c_hat_score_source"] = "bar_prefix"
    return base


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
            "y_hl",
            "y_path",
            "y_co",
            "y_hl",
            "predicted_score_hl",
            "predicted_score_complexity",
            "y_complexity_hat",
            "predicted_score_cx",
            "y_cx_hat",
            "predicted_score_tpd",
            "y_tpd_hat",
            "predicted_score_r",
            "y_r_hat",
            "y_r",
            "r_realized",
            "y_r_realized",
            "y_τ30",
            "y_t30",
            "y_t30_hat",
            "predicted_score_t30",
            "y_t30_realized",
            "t30_realized",
            "y_τ60",
            "y_t60",
            "y_t60_hat",
            "predicted_score_t60",
            "y_t60_realized",
            "t60_realized",
            "y_τ90",
            "y_t90",
            "y_t90_hat",
            "predicted_score_t90",
            "y_t90_realized",
            "t90_realized",
            "y_eod",
            "y_trade",
            "y_on",
            "gap_pct",
            "y_tau_oc",
            "y_tau_portrait_oc",
            "y_hl_portrait",
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
    """按时间合并各轮成交；现金/可卖不够则丢掉最晚开的一轮并重放。卖出只动日初可卖（T+1）。"""
    tagged: List[Tuple[str, dict, str]] = []
    for out in slot_outs:
        sid = str((out or {}).get("t0_slot") or "")
        for t in _slot_trade_legs(out):
            tagged.append((_norm_trade_at(t.get("at")), dict(t), sid))
    tagged.sort(key=lambda x: x[0])

    sellable_cap = float(
        sellable_shares if sellable_shares is not None else shares
    )
    sellable_old = min(max(sellable_cap, 0.0), float(shares))
    dropped, cash_now, shares_now = _settle_tagged_slot_legs(
        tagged,
        cash=float(cash or 0),
        shares=float(shares),
        sellable_old=sellable_old,
    )

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
        orig = dict(out)
        sid = str(orig.get("t0_slot") or "")
        row = orig
        if sid in dropped:
            extra = {
                "t0_slot": sid,
                "direction_used": orig.get("direction_used"),
                "t0_slot_hm": orig.get("t0_slot_hm") or orig.get("hm"),
                "hm": orig.get("t0_slot_hm") or orig.get("hm"),
            }
            if orig.get("close_band") is not None:
                extra["close_band"] = orig.get("close_band")
            row = _skip_result(
                reason="现金或可卖不足，本轮未落账",
                shares=shares,
                bar=bar,
                extra=extra,
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
            "t0_slot": sid,
            "t0_slot_hm": row.get("t0_slot_hm") or row.get("hm"),
            "skipped": bool(row.get("skipped")),
            "pending": bool(row.get("pending")),
            "reason": row.get("reason"),
            "direction": row.get("direction_used"),
            "direction_used": row.get("direction_used"),
            "trades": len(row.get("trades") or []) if kept else 0,
            "trade_legs": list(row.get("trades") or []) if kept else [],
            "sold_qty": int(row.get("sold_qty") or 0) if kept else 0,
            "covered_qty": int(row.get("covered_qty") or 0) if kept else 0,
            "uncovered_qty": int(row.get("uncovered_qty") or 0) if kept else 0,
            "bought_qty": int(row.get("bought_qty") or 0) if kept else 0,
            "sold_back_qty": int(row.get("sold_back_qty") or 0) if kept else 0,
            "pnl": row.get("pnl") if kept else 0,
            "exposure_pnl": row.get("exposure_pnl") if kept else 0,
            "exit_reason": row.get("exit_reason") if kept else None,
            "close_band": row.get("close_band"),
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
        "range_mode": "close_band",
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


def _open_close_band_round(
    *,
    round_id: str,
    hm: str,
    bar_index: int,
    direction: str,
    frozen: dict,
    minute_bars: Sequence[dict],
    bar: dict,
    shares: float,
    cost: float,
    sellable_shares: float,
    ratio: float,
    cfg_day: dict,
    cash: float,
    stock_code: str,
    lot: int,
    cost_model: str,
    cost_params: dict,
    atr_pct: Optional[float],
    score_snap: Optional[dict],
    session_bar: Optional[dict],
    defer_eod: bool,
    ref: float,
) -> Dict[str, Any]:
    """破带选向后开一轮：确认根=触发根收盘；leg2 冻结 C_τ。"""
    from core.t0.minute_path import (
        _day_ohlc_from_minutes,
        _first_touch_buy_then_sell,
        _first_touch_sell_then_buy,
        _set_forward_trace_on_result,
        _side_exec_pack,
    )

    cfg_side = apply_side_exec_params(cfg_day, direction)
    cfg_side, fill_mode = _side_exec_pack(cfg_side=cfg_side)
    cfg_side = dict(cfg_side)
    cfg_side["t0_ratio"] = float(ratio)
    mins = list(minute_bars)
    bar_session = session_bar or _day_ohlc_from_minutes(mins, bar)
    gate_idx = int(bar_index)

    def _gate(i: int, ci: int = gate_idx) -> bool:
        return i == ci

    path_kwargs = {
        "session_bars": mins,
        "session_bar": bar_session,
        "defer_eod": bool(defer_eod),
        "leg1_gate_at": _gate,
        "leg2_target_px": float(frozen.get("leg2_target") or 0) or None,
    }
    y_tau = None
    if isinstance(score_snap, dict):
        from core.t0.minute_path import _score_y_tau

        y_tau = _score_y_tau(score_snap)
    path_kwargs["y_tau"] = y_tau

    if direction == "buy_then_sell":
        out = _first_touch_buy_then_sell(
            minute_bars=mins,
            bar=bar,
            shares=shares,
            cash=float(cash or 0),
            sellable_shares=sellable_shares,
            ref=float(ref),
            lot=lot,
            fill_mode=str(fill_mode),
            cfg=cfg_side,
            cost_model=cost_model,
            cost_params=cost_params,
            stock_code=stock_code,
            atr_pct=atr_pct,
            range_pct=0.0,
            **path_kwargs,
        )
    else:
        out = _first_touch_sell_then_buy(
            minute_bars=mins,
            bar=bar,
            shares=shares,
            sellable_shares=sellable_shares,
            ref=float(ref),
            lot=lot,
            fill_mode=str(fill_mode),
            cfg=cfg_side,
            cost_model=cost_model,
            cost_params=cost_params,
            stock_code=stock_code,
            atr_pct=atr_pct,
            range_pct=0.0,
            t0_ratio=float(ratio),
            cash=float(cash or 0),
            **path_kwargs,
        )
    if not isinstance(out, dict):
        return _skip_result(
            reason="收盘带宽路径失败",
            shares=shares,
            bar=bar,
            extra={"t0_slot": round_id, "t0_slot_hm": hm},
        )
    out["t0_slot"] = round_id
    out["t0_slot_hm"] = hm
    out["t0_ratio"] = float(ratio)
    out["direction_used"] = direction
    out["path_mode"] = "first_touch"
    out["range_mode"] = "close_band"
    out["close_band"] = dict(frozen)
    out["direction_reason"] = (
        f"v6收盘带宽：收价破带→{('反T' if direction == 'sell_then_buy' else '正T')}"
        f"·C_τ={frozen.get('close_px')}·δ={frozen.get('delta_px')}"
        f"·leg2={frozen.get('leg2_target')}"
    )
    if isinstance(score_snap, dict):
        out["_t0_score_snap"] = score_snap
        out["scores"] = score_snap
    _set_forward_trace_on_result(
        out,
        [],
        cfg_day=cfg_day,
        direction=direction,
        dir_res={"direction": direction, "direction_reason": out.get("direction_reason")},
    )
    for t in out.get("trades") or []:
        if isinstance(t, dict):
            t["t0_slot"] = round_id
            t["t0_slot_hm"] = hm
    return out


def _collect_close_band_trigger_hms(slot_outs: Sequence[dict]) -> set:
    out: set = set()
    for row in slot_outs or ():
        if not isinstance(row, dict) or row.get("skipped"):
            continue
        hm = str(
            row.get("t0_slot_hm") or (row.get("close_band") or {}).get("hm") or ""
        )[:5]
        if hm:
            out.add(hm)
    return out


def _build_close_band_scan_trace(
    *,
    minute_bars: Sequence[dict],
    bar: Optional[dict],
    daily_bar: Optional[dict],
    cfg: dict,
    score_snap: Optional[dict],
    stock_code: str,
    hist_bars: Optional[Sequence[dict]],
    tau_pool_day: Optional[dict],
    trigger_hms: Optional[set] = None,
) -> List[dict]:
    """11:00 前每根 5m：OLHC + R̂_τ + y_r/y_τ30/y_τ60/y_τ90/y_τ/y_hl/y_cx/y_tpd（debug 展开用）。"""
    from core.t0.close_band import (
        close_band_enter_skip_reason,
        close_band_pick_direction,
        close_band_sign_skip_reason,
        close_band_y_tc_skip_reason,
        close_band_y_t30_skip_reason,
        close_band_y_tw_skip_reason,
        close_band_y_t60_skip_reason,
        close_band_y_t90_skip_reason,
        estimate_close_px,
        y_tc_band_agree,
        hm_allows_leg1,
        map_close_px_to_minute,
        parse_bar_hm,
        resolve_t0_price_space,
    )
    from core.t0.config import T0_LAST_LEG1_HM
    from core.t0.score_policy import rescore_scores_at_fixed_prefix, resolve_fuse_intraday

    mins = [b for b in (minute_bars or []) if isinstance(b, dict)]
    if not mins:
        return []
    day_anchor = daily_bar if isinstance(daily_bar, dict) else bar
    trade_date = str((day_anchor or {}).get("date") or "")[:10]
    if not trade_date:
        for _b in mins:
            d0 = str(_b.get("date") or "")[:10]
            if len(d0) == 10 and d0[4] == "-":
                trade_date = d0
                break
            dt0 = str(_b.get("datetime") or "")
            if len(dt0) >= 10 and dt0[4] == "-":
                trade_date = dt0[:10]
                break
    space = resolve_t0_price_space(
        bar if isinstance(bar, dict) else None,
        mins,
        cfg,
        daily_bar=day_anchor if isinstance(day_anchor, dict) else None,
    )
    open_px_m = space.get("minute_open")
    est_open = space.get("estimate_open") or open_px_m
    est_prev = space.get("estimate_prev")
    scale = float(space.get("scale") or 1.0)
    try:
        delta_pct = float(cfg.get("t0_close_band_delta_pct") or 3.0)
    except (TypeError, ValueError):
        delta_pct = 3.0
    last_hm = str(cfg.get("t0_last_leg1_hm") or T0_LAST_LEG1_HM)
    open_snap = score_snap if isinstance(score_snap, dict) else None
    code = str(stock_code or "").strip()
    triggers = set(trigger_hms or ())
    rows: List[dict] = []

    for idx, mb in enumerate(mins):
        hm = parse_bar_hm(mb)
        if not hm_allows_leg1(hm, last_hm):
            break
        try:
            o = float(mb.get("open") or 0)
            h = float(mb.get("high") or 0)
            l = float(mb.get("low") or 0)
            c = float(mb.get("close") or 0)
        except (TypeError, ValueError):
            o, h, l, c = 0.0, 0.0, 0.0, 0.0
        if c <= 0:
            continue

        live_snap = open_snap
        is_open_hm = str(hm or "")[:5] == "09:30"
        if code and not is_open_hm:
            try:
                live_snap = rescore_scores_at_fixed_prefix(
                    stock_code=code,
                    minute_prefix=list(mins[: idx + 1]),
                    day_bar=bar if isinstance(bar, dict) else None,
                    hist_bars=hist_bars,
                    tau_pool_day=tau_pool_day,
                    fuse_intraday=resolve_fuse_intraday(cfg),
                    open_snap=open_snap,
                )
            except Exception:  # noqa: BLE001
                live_snap = {
                    **(dict(open_snap) if isinstance(open_snap, dict) else {}),
                    "y_hl_status": "minute_data_missing",
                    "_minute_data_missing": True,
                }
        elif code and is_open_hm and len(mins[: idx + 1]) >= 1:
            try:
                scored = rescore_scores_at_fixed_prefix(
                    stock_code=code,
                    minute_prefix=list(mins[: idx + 1]),
                    day_bar=bar if isinstance(bar, dict) else None,
                    hist_bars=hist_bars,
                    tau_pool_day=tau_pool_day,
                    fuse_intraday=resolve_fuse_intraday(cfg),
                    open_snap=open_snap,
                )
                if isinstance(scored, dict) and not scored.get("_minute_data_missing"):
                    live_snap = scored
            except Exception:  # noqa: BLE001
                pass

        snap_for_gate = live_snap if isinstance(live_snap, dict) else open_snap
        gate_snap = dict(snap_for_gate) if isinstance(snap_for_gate, dict) else {}
        # Ĉ 与该根前缀 ŷ_τ 同源；破带 price = 本根 5m 收价 C（非日线收）。
        c_hat_snap = gate_snap

        c_tau = None
        y_tau = None
        y_path = None
        y_complexity = None
        y_tpd = None
        y_r = None
        y_t30 = None
        y_t60 = None
        y_t90 = None
        r_realized = None
        y_t30_realized = None
        y_t60_realized = None
        y_t90_realized = None
        r_pct = None
        upper_pct = None
        lower_pct = None
        r_hat = None
        remaining_oc_v = None
        c_hat_source = None
        y_oc_scan = None
        y_tc_scan = None
        y_tc_ridge = None
        y_tc_source = None
        direction = None
        enter_skip = None
        sign_skip = None
        y_tc_skip = None
        y_t30_skip = None
        y_tw_skip = None
        y_t60_skip = None
        y_t90_skip = None
        y_tc_agree = None
        minute_missing = bool(
            isinstance(gate_snap, dict)
            and (
                gate_snap.get("_minute_data_missing")
                or str(gate_snap.get("y_hl_status") or gate_snap.get("y_path_status") or "").strip()
                == "minute_data_missing"
            )
            and not is_open_hm
        )

        if est_open and float(est_open) > 0:
            price_tau_d = float(c) * float(scale) if c > 0 and scale else None
            est = estimate_close_px(
                c_hat_snap,
                open_px=float(est_open),
                prev_close=est_prev,
                price_tau=price_tau_d,
                cfg=cfg,
            )
            if est.get("ok") and est.get("close_px") is not None:
                close_px_m = map_close_px_to_minute(float(est["close_px"]), scale=scale)
                if close_px_m is not None:
                    direction, band_meta = close_band_pick_direction(
                        c,
                        float(close_px_m),
                        float(delta_pct),
                        c_hat_snap,
                        cfg,
                    )
                    r_hat = est.get("r_hat")
                    c_tau = round(float(close_px_m), 4)
                    r_pct = band_meta.get("r_pct")
                    upper_pct = band_meta.get("upper_pct")
                    lower_pct = band_meta.get("lower_pct")
                    remaining_oc_v = est.get("remaining_oc")
                    c_hat_source = est.get("c_hat_source")
                    y_oc_scan = est.get("y_oc")
                    y_tc_scan = est.get("y_τc")
                    y_tc_ridge = est.get("y_τc_ridge")
                    y_tc_source = est.get("y_τc_source")
                    y_tc_gate = y_tc_ridge if y_tc_ridge is not None else y_tc_scan
                    if direction:
                        y_tc_agree = y_tc_band_agree(direction, y_tc_gate)
                        enter_skip = close_band_enter_skip_reason(
                            gate_snap, cfg, direction=direction, r_pct=r_pct
                        )
                        if not enter_skip:
                            sign_skip = close_band_sign_skip_reason(gate_snap, cfg)
                        if not enter_skip and not sign_skip:
                            y_tc_skip = close_band_y_tc_skip_reason(
                                gate_snap, cfg, direction=direction, y_τc=y_tc_gate
                            )
                            if not y_tc_skip:
                                y_t30_skip = close_band_y_t30_skip_reason(
                                    gate_snap, cfg, direction=direction
                                )
                                if not y_t30_skip:
                                    y_tw_skip = close_band_y_tw_skip_reason(
                                        gate_snap, cfg, direction=direction
                                    )
                                    if not y_tw_skip:
                                        y_t60_skip = close_band_y_t60_skip_reason(
                                            gate_snap, cfg, direction=direction
                                        )
                                        if not y_t60_skip:
                                            y_t90_skip = close_band_y_t90_skip_reason(
                                                gate_snap, cfg, direction=direction
                                            )

        if isinstance(gate_snap, dict):
            from core.t0.score_policy import scores_from_item
            from core.t0.minute_path import _score_y_tau as _yt_gate

            sc = scores_from_item(gate_snap)
            from core.research.path_panel import pick_y_hl

            yp = pick_y_hl(sc, gate_snap)
            if yp is not None:
                y_path = round(float(yp), 4)
            from core.research.cx_panel import pick_y_complexity_hat, pick_y_tpd_hat

            y_hat = pick_y_complexity_hat(sc, gate_snap)
            if y_hat is not None:
                y_complexity = round(float(y_hat), 6)
            y_tpd_hat = pick_y_tpd_hat(sc, gate_snap)
            if y_tpd_hat is not None:
                y_tpd = round(float(y_tpd_hat), 6)
            from core.research.r_ridge import pick_y_r_hat, r_realized_pct

            y_r_hat = pick_y_r_hat(sc, gate_snap)
            if y_r_hat is not None:
                y_r = round(float(y_r_hat), 4)
            from core.research.t30_ridge import pick_y_t30_hat as _pick_t30
            from core.research.t30_ridge import t30_realized_pct
            from core.research.t60_ridge import pick_y_t60_hat as _pick_t60
            from core.research.t60_ridge import t60_realized_pct
            from core.research.t90_ridge import pick_y_t90_hat as _pick_t90
            from core.research.t90_ridge import t90_realized_pct
            from core.research.tau_panel import HORIZON_T60_MIN, HORIZON_T90_MIN, price_at_tau_plus_session

            y_t30_hat = _pick_t30(sc, gate_snap)
            y_t30 = round(float(y_t30_hat), 4) if y_t30_hat is not None else None
            y_t60_hat = _pick_t60(sc, gate_snap)
            y_t60 = round(float(y_t60_hat), 4) if y_t60_hat is not None else None
            y_t90_hat = _pick_t90(sc, gate_snap)
            y_t90 = round(float(y_t90_hat), 4) if y_t90_hat is not None else None
            if trade_date and hm:
                _, px30 = price_at_tau_plus_session(
                    mins, trade_date=trade_date, tau_hm=str(hm)[:5]
                )
                y_t30_realized = t30_realized_pct(c, px30)
                _, px60 = price_at_tau_plus_session(
                    mins,
                    trade_date=trade_date,
                    tau_hm=str(hm)[:5],
                    add_min=HORIZON_T60_MIN,
                )
                y_t60_realized = t60_realized_pct(c, px60)
                _, px90 = price_at_tau_plus_session(
                    mins,
                    trade_date=trade_date,
                    tau_hm=str(hm)[:5],
                    add_min=HORIZON_T90_MIN,
                )
                y_t90_realized = t90_realized_pct(c, px90)
            daily_c = None
            try:
                daily_c = float((day_anchor or {}).get("close") or 0)
            except (TypeError, ValueError):
                daily_c = 0.0
            if daily_c and daily_c > 0:
                r_realized = r_realized_pct(c, daily_c)
            yt_gate = _yt_gate(gate_snap)
            if yt_gate is not None:
                y_tau = round(float(yt_gate), 4)
            elif sc.get("y_tau") is not None:
                y_tau = round(float(sc["y_tau"]), 4)

        if y_tau is not None:
            y_tau = round(float(y_tau), 4)

        row = {
            "hm": hm,
            "idx": idx,
            "o": round(o, 4) if o > 0 else None,
            "l": round(l, 4) if l > 0 else None,
            "h": round(h, 4) if h > 0 else None,
            "c": round(c, 4),
            "c_tau": c_tau,
            "y_tau": y_tau,
            "y_hl": y_path,
            "y_complexity": y_complexity,
            "y_cx": y_complexity,
            "y_tpd": y_tpd,
            "y_r": y_r,
            "predicted_score_r": y_r,
            "y_r_hat": y_r,
            "y_τ30": y_t30,
            "y_t30": y_t30,
            "y_t30_hat": y_t30,
            "predicted_score_t30": y_t30,
            "y_t30_realized": y_t30_realized,
            "t30_realized": y_t30_realized,
            "y_τ60": y_t60,
            "y_t60": y_t60,
            "y_t60_hat": y_t60,
            "predicted_score_t60": y_t60,
            "y_t60_realized": y_t60_realized,
            "t60_realized": y_t60_realized,
            "y_τ90": y_t90,
            "y_t90": y_t90,
            "y_t90_hat": y_t90,
            "predicted_score_t90": y_t90,
            "y_t90_realized": y_t90_realized,
            "t90_realized": y_t90_realized,
            "r_realized": r_realized,
            "y_r_realized": r_realized,
            "r_pct": round(float(r_pct), 4) if r_pct is not None else None,
            "r_hat": round(float(r_hat), 4) if r_hat is not None else None,
            "residual": round(float(r_hat), 4) if r_hat is not None else None,
            "y_oc": (
                round(float(y_oc_scan if y_oc_scan is not None else y_tau), 4)
                if (y_oc_scan is not None or y_tau is not None)
                else None
            ),
            "y_τc": (
                round(float(y_tc_ridge if y_tc_ridge is not None else y_r), 4)
                if (y_tc_ridge is not None or y_r is not None)
                else None
            ),
            "y_τc_ridge": (
                round(float(y_tc_ridge), 4) if y_tc_ridge is not None else None
            ),
            "y_τc_source": y_tc_source,
            "y_tc": (
                round(float(y_tc_ridge if y_tc_ridge is not None else y_r), 4)
                if (y_tc_ridge is not None or y_r is not None)
                else None
            ),
            "remaining_oc": (
                round(float(remaining_oc_v), 4) if remaining_oc_v is not None else None
            ),
            "c_hat_source": c_hat_source,
            "upper_pct": (
                round(float(upper_pct), 4) if upper_pct is not None else None
            ),
            "lower_pct": (
                round(float(lower_pct), 4) if lower_pct is not None else None
            ),
            "pick": direction,
            "enter_skip": enter_skip,
            "sign_skip": sign_skip,
            "y_tc_skip": y_tc_skip,
            "y_t30_skip": y_t30_skip,
            "y_tw_skip": y_tw_skip,
            "y_t60_skip": y_t60_skip,
            "y_t90_skip": y_t90_skip,
            "y_tc_agree": y_tc_agree,
            "minute_missing": minute_missing,
            "leg1": bool(hm and hm[:5] in triggers),
        }
        _attach_scan_row_tip_fields(row, live_snap if isinstance(live_snap, dict) else None)
        rows.append(row)
    return rows


def _attach_close_band_scan(
    day_out: dict,
    *,
    minute_bars: Sequence[dict],
    bar: Optional[dict],
    daily_bar: Optional[dict],
    cfg: dict,
    score_snap: Optional[dict],
    stock_code: str,
    hist_bars: Optional[Sequence[dict]],
    tau_pool_day: Optional[dict],
    slot_outs: Sequence[dict],
) -> dict:
    trace = _build_close_band_scan_trace(
        minute_bars=minute_bars,
        bar=bar,
        daily_bar=daily_bar,
        cfg=cfg,
        score_snap=score_snap,
        stock_code=stock_code,
        hist_bars=hist_bars,
        tau_pool_day=tau_pool_day,
        trigger_hms=_collect_close_band_trigger_hms(slot_outs),
    )
    if trace:
        day_out["close_band_scan"] = trace
    return day_out


def refresh_close_band_scan(
    day: Optional[dict],
    *,
    minute_bars: Sequence[dict],
    bar: Optional[dict],
    daily_bar: Optional[dict] = None,
    cfg: Optional[dict] = None,
    score_snap: Optional[dict] = None,
    stock_code: str = "",
    hist_bars: Optional[Sequence[dict]] = None,
    tau_pool_day: Optional[dict] = None,
) -> dict:
    """已成交票：用当前前缀重扫 11:00 前每根 ŷ，不改已落账腿。"""
    out = dict(day or {})
    cfg_d = cfg if isinstance(cfg, dict) else {}
    slots = [r for r in (out.get("t0_slot_results") or []) if isinstance(r, dict)]
    out = _attach_close_band_scan(
        out,
        minute_bars=minute_bars,
        bar=bar,
        daily_bar=daily_bar if daily_bar is not None else bar,
        cfg=cfg_d,
        score_snap=score_snap,
        stock_code=stock_code,
        hist_bars=hist_bars,
        tau_pool_day=tau_pool_day,
        slot_outs=slots,
    )
    try:
        from core.t0.minute_path import _day_ohlc_from_minutes

        ohlc = _day_ohlc_from_minutes(
            minute_bars, bar if isinstance(bar, dict) else None
        )
        # 开/高/低可用分钟合成；收盘锚与 y_oc 真实值必须用日 K，勿用可见末根。
        for k in ("open", "high", "low"):
            if ohlc.get(k) is not None:
                out[k] = ohlc.get(k)
        min_c = ohlc.get("close")
        ps = dict(out["price_space"]) if isinstance(out.get("price_space"), dict) else {}
        if min_c is not None:
            ps["minute_close"] = min_c
        anchor = daily_bar if isinstance(daily_bar, dict) else bar
        if isinstance(anchor, dict) and anchor.get("close") is not None:
            ps["daily_close"] = anchor.get("close")
            out["close"] = anchor.get("close")
        if ps:
            out["price_space"] = ps
        from core.t0.score_policy import attach_eod_tau_realized

        out = attach_eod_tau_realized(
            out,
            open_px=anchor.get("open") if isinstance(anchor, dict) else None,
            close_px=anchor.get("close") if isinstance(anchor, dict) else None,
            prev_close=anchor.get("prev_close") if isinstance(anchor, dict) else None,
        )
    except Exception:  # noqa: BLE001
        logger.debug("refresh_close_band_scan ohlc failed", exc_info=True)
    return out


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
    daily_bar: Optional[dict] = None,
) -> Dict[str, Any]:
    """v6：逐根 C 相对 C_τ 破带开轮（每轮 ratio，累计至 max_pos）；11:00 后不开 leg1。

    ``daily_bar``：原始日 K（开/收/昨收）；勿传分钟合成 OHLC。缺省用 ``bar``。
    """
    from core.t0.close_band import (
        band_delta_px,
        close_band_enter_skip_reason,
        close_band_pick_direction,
        close_band_sign_skip_reason,
        close_band_y_tc_skip_reason,
        close_band_y_t30_skip_reason,
        close_band_y_tw_skip_reason,
        close_band_y_t60_skip_reason,
        close_band_y_t90_skip_reason,
        day_price_space_payload,
        estimate_close_px,
        freeze_round,
        hm_allows_leg1,
        map_close_components_to_minute,
        map_close_px_to_minute,
        parse_bar_hm,
        resolve_t0_price_space,
    )
    from core.t0.config import T0_LAST_LEG1_HM
    from core.t0.score_policy import rescore_scores_at_fixed_prefix, resolve_fuse_intraday

    mins = [b for b in (minute_bars or []) if isinstance(b, dict)]
    sellable_cap = float(sellable_shares if sellable_shares is not None else shares)
    sellable_cap = min(max(sellable_cap, 0.0), float(shares))
    try:
        round_ratio = float(cfg.get("t0_round_ratio") or 0.4)
    except (TypeError, ValueError):
        round_ratio = 0.4
    round_ratio = max(0.05, min(round_ratio, 1.0))
    try:
        max_pos = float(cfg.get("t0_max_position_pct") or 1.0)
    except (TypeError, ValueError):
        max_pos = 1.0
    max_pos = max(0.05, min(max_pos, 1.0))
    try:
        delta_pct = float(cfg.get("t0_close_band_delta_pct") or 3.0)
    except (TypeError, ValueError):
        delta_pct = 3.0
    max_rounds = _parse_max_rounds(cfg, max(1, int(round(max_pos / round_ratio))))
    last_hm = str(cfg.get("t0_last_leg1_hm") or T0_LAST_LEG1_HM)

    day_anchor = daily_bar if isinstance(daily_bar, dict) else bar
    space = resolve_t0_price_space(
        bar if isinstance(bar, dict) else None,
        mins,
        cfg,
        daily_bar=day_anchor if isinstance(day_anchor, dict) else None,
    )
    open_px_m = space.get("minute_open") or float(cost or 0)
    # 估 C_τ：优先日线开/昨收（与训标签同空间）；破带触价映回分钟
    est_open = space.get("estimate_open") or open_px_m
    est_prev = space.get("estimate_prev")
    scale = float(space.get("scale") or 1.0)
    delta_px = band_delta_px(float(open_px_m or 0), delta_pct)
    ref = float(open_px_m or cost or 0)

    outs: List[dict] = []
    cash_now = float(cash or 0)
    shares_now = float(shares)
    sellable_now = float(sellable_cap)
    used_ratio = 0.0
    cover_committed = 0.0  # 正T已开轮将占用的底仓回补额度
    leg1_rounds = 0
    last_leg1_idx = -1
    last_sign_skip: Optional[str] = None
    last_enter_skip: Optional[str] = None
    last_y_tc_skip: Optional[str] = None
    last_y_t30_skip: Optional[str] = None
    last_y_tw_skip: Optional[str] = None
    last_y_t60_skip: Optional[str] = None
    last_y_t90_skip: Optional[str] = None
    last_tplus1_skip: Optional[str] = None
    open_snap = score_snap if isinstance(score_snap, dict) else None
    code = str(stock_code or "").strip()

    if space.get("skip_reason"):
        ps = day_price_space_payload(
            space,
            bar if isinstance(bar, dict) else None,
            daily_bar=day_anchor if isinstance(day_anchor, dict) else None,
        )
        skipped = _skip_result(
            reason=str(space["skip_reason"]),
            shares=shares,
            bar=bar,
            extra={
                "signal_skip": True,
                "price_space": ps,
            },
        )
        skipped["range_mode"] = "close_band"
        skipped["t0_close_band_delta_pct"] = delta_pct
        skipped["price_space"] = ps
        skipped["price_space_scale"] = ps.get("scale_open")
        skipped["price_space_mode"] = ps.get("estimate_mode")
        return _attach_close_band_scan(
            skipped,
            minute_bars=mins,
            bar=bar,
            daily_bar=day_anchor,
            cfg=cfg,
            score_snap=open_snap,
            stock_code=code,
            hist_bars=hist_bars,
            tau_pool_day=tau_pool_day,
            slot_outs=[],
        )

    if not mins or ref <= 0 or delta_px is None:
        merged_empty = _merge_slot_day(
            slot_outs=[],
            bar=bar,
            shares=shares,
            cash=cash,
            sellable_shares=sellable_cap,
            minute_bars=mins,
        )
        return _attach_close_band_scan(
            merged_empty,
            minute_bars=mins,
            bar=bar,
            daily_bar=day_anchor,
            cfg=cfg,
            score_snap=open_snap,
            stock_code=code,
            hist_bars=hist_bars,
            tau_pool_day=tau_pool_day,
            slot_outs=[],
        )

    for idx, mb in enumerate(mins):
        if leg1_rounds >= max_rounds or used_ratio >= max_pos - 1e-12:
            break
        if idx <= last_leg1_idx:
            continue
        hm = parse_bar_hm(mb)
        if not hm_allows_leg1(hm, last_hm):
            break
        # 第一腿现价 = **本根 5m 收价 C**（mb.close）；勿用日线 bar.close。
        try:
            bar_close = float(mb.get("close") or 0)
        except (TypeError, ValueError):
            bar_close = 0.0
        if bar_close <= 0:
            continue

        live_snap = open_snap
        # 仅 09:30 允许无分钟、用开盘 Z；其后每根须前缀重算，缺小包=数据缺失
        is_open_hm = str(hm or "")[:5] == "09:30"
        if code and not is_open_hm:
            try:
                live_snap = rescore_scores_at_fixed_prefix(
                    stock_code=code,
                    minute_prefix=list(mins[: idx + 1]),
                    day_bar=bar if isinstance(bar, dict) else None,
                    hist_bars=hist_bars,
                    tau_pool_day=tau_pool_day,
                    fuse_intraday=resolve_fuse_intraday(cfg),
                    open_snap=open_snap,
                )
            except Exception:  # noqa: BLE001
                logger.debug("close-band rescore failed", exc_info=True)
                live_snap = {
                    **(dict(open_snap) if isinstance(open_snap, dict) else {}),
                    "y_hl_status": "minute_data_missing",
                    "_minute_data_missing": True,
                    "_score_source": "prefix_minute_missing",
                }
        elif code and is_open_hm and idx >= 0 and len(mins[: idx + 1]) >= 1:
            # 09:30 若已有该根分钟，仍优先因果重算；失败可退回开盘 Z
            try:
                scored = rescore_scores_at_fixed_prefix(
                    stock_code=code,
                    minute_prefix=list(mins[: idx + 1]),
                    day_bar=bar if isinstance(bar, dict) else None,
                    hist_bars=hist_bars,
                    tau_pool_day=tau_pool_day,
                    fuse_intraday=resolve_fuse_intraday(cfg),
                    open_snap=open_snap,
                )
                if isinstance(scored, dict) and not scored.get("_minute_data_missing"):
                    live_snap = scored
            except Exception:  # noqa: BLE001
                logger.debug("close-band open-bar rescore failed", exc_info=True)

        snap_for_gate = live_snap if isinstance(live_snap, dict) else open_snap
        if (
            isinstance(snap_for_gate, dict)
            and (
                snap_for_gate.get("_minute_data_missing")
                or str(snap_for_gate.get("y_hl_status") or snap_for_gate.get("y_path_status") or "").strip()
                == "minute_data_missing"
            )
            and not is_open_hm
        ):
            last_enter_skip = "分钟数据缺失（非 09:30 须有分钟小包）"
            continue

        # 每根前缀重算 ŷ_oc / y_path；C_τ=O×(1+clip(ŷ_oc×scale)/100)；第一腿现价 = 本根 5m 收价 C。
        gate_snap = dict(snap_for_gate) if isinstance(snap_for_gate, dict) else {}
        c_hat_snap = gate_snap
        price_tau_d = float(bar_close) * float(scale) if bar_close > 0 and scale else None
        est = estimate_close_px(
            c_hat_snap,
            open_px=float(est_open),
            prev_close=est_prev,
            price_tau=price_tau_d,
            cfg=cfg,
        )
        if not est.get("ok") or est.get("close_px") is None:
            continue
        close_px_m = map_close_px_to_minute(float(est["close_px"]), scale=scale)
        if close_px_m is None:
            continue
        direction, band_meta = close_band_pick_direction(
            bar_close,
            float(close_px_m),
            float(delta_pct),
            c_hat_snap,
            cfg,
        )
        if not direction:
            continue
        # |y_τ| / |y_path| 入场（path 可关）；ŷ 用该根前缀
        enter_skip = close_band_enter_skip_reason(
            gate_snap, cfg, direction=direction, r_pct=band_meta.get("r_pct")
        )
        if enter_skip:
            last_enter_skip = enter_skip
            continue
        # |y_hl|>y_hl_strong 须与 y_τ 同号（trade/eod 强闸已下线）
        sign_skip = close_band_sign_skip_reason(gate_snap, cfg)
        if sign_skip:
            last_sign_skip = sign_skip
            continue
        y_tc_skip = close_band_y_tc_skip_reason(
            gate_snap,
            cfg,
            direction=direction,
            y_τc=est.get("y_τc_ridge") if est.get("y_τc_ridge") is not None else est.get("y_τc"),
        )
        if y_tc_skip:
            last_y_tc_skip = y_tc_skip
            continue
        y_t30_skip = close_band_y_t30_skip_reason(
            gate_snap,
            cfg,
            direction=direction,
        )
        if y_t30_skip:
            last_y_t30_skip = y_t30_skip
            continue
        y_tw_skip = close_band_y_tw_skip_reason(
            gate_snap,
            cfg,
            direction=direction,
        )
        if y_tw_skip:
            last_y_tw_skip = y_tw_skip
            continue
        y_t60_skip = close_band_y_t60_skip_reason(
            gate_snap,
            cfg,
            direction=direction,
        )
        if y_t60_skip:
            last_y_t60_skip = y_t60_skip
            continue
        y_t90_skip = close_band_y_t90_skip_reason(
            gate_snap,
            cfg,
            direction=direction,
        )
        if y_t90_skip:
            last_y_t90_skip = y_t90_skip
            continue

        remain = max_pos - used_ratio
        ratio = min(round_ratio, remain)
        if ratio < 0.05:
            break
        slice_qty = float(_lot_floor(sellable_cap * ratio, lot))
        # 反T卖开受剩余可卖约束；正T 第二腿仍卖旧仓，开新轮前预扣已承诺回补额度
        slice_qty = min(slice_qty, float(_lot_floor(sellable_now, lot)))
        path_sellable = slice_qty if direction == "sell_then_buy" else sellable_now
        if direction == "buy_then_sell":
            remain_cover = float(
                _lot_floor(max(0.0, float(sellable_cap) - cover_committed), lot)
            )
            if remain_cover < lot:
                from core.t0.minute_path import _tplus1_skip_reason

                if not last_tplus1_skip:
                    last_tplus1_skip = _tplus1_skip_reason(
                        side="buy_then_sell",
                        shares=shares,
                        sellable=sellable_cap,
                        lot=lot,
                    )
                continue
            path_sellable = remain_cover
        if direction == "sell_then_buy" and slice_qty < lot:
            from core.t0.minute_path import _tplus1_skip_reason

            if not last_tplus1_skip:
                last_tplus1_skip = _tplus1_skip_reason(
                    side="sell_then_buy",
                    shares=shares,
                    sellable=sellable_now,
                    lot=lot,
                )
            continue

        frozen = freeze_round(
            direction=direction,
            leg1_px=bar_close,
            close_px=float(close_px_m),
            delta_px=float(close_px_m) * float(delta_pct) / 100.0,
            ratio=ratio,
            bar_index=idx,
            hm=hm,
        )
        sid = f"r{leg1_rounds + 1}"
        # 扫描态现金=已落账（仅含截至本根）；勿再叠 cash_locked，否则未平正T会双重扣减
        avail_cash = max(0.0, cash_now)
        px_m = map_close_components_to_minute(est, scale=scale)
        gate_yt = est.get("y_tau")
        if gate_yt is None and c_hat_snap:
            from core.t0.minute_path import _score_y_tau as _yt_bar

            gate_yt = _yt_bar(c_hat_snap)
        live_yt = None
        if gate_snap:
            from core.t0.minute_path import _score_y_tau as _yt_live

            live_yt = _yt_live(gate_snap)
        score_pub = _close_band_public_scores(
            gate_snap=gate_snap or None,
            gate_y_tau=gate_yt,
        )
        out = _open_close_band_round(
            round_id=sid,
            hm=hm,
            bar_index=idx,
            direction=direction,
            frozen={
                **frozen.to_dict(),
                "c_tau": px_m.get("c_tau"),
                "c_trade": px_m.get("c_trade"),
                "c_nowcast": px_m.get("c_nowcast"),
                "n_sources": est.get("n_sources"),
                "trade_vs": est.get("trade_vs"),
                "nowcast_vs": est.get("nowcast_vs"),
                "close_px_daily": px_m.get("close_px_daily"),
                "c_tau_daily": px_m.get("c_tau_daily"),
                "c_trade_daily": px_m.get("c_trade_daily"),
                "c_nowcast_daily": px_m.get("c_nowcast_daily"),
                "price_space_scale": scale,
                "estimate_mode": space.get("estimate_mode"),
                "band_r_pct": band_meta.get("r_pct"),
                "band_upper_pct": band_meta.get("upper_pct"),
                "band_lower_pct": band_meta.get("lower_pct"),
                "y_oc_target": est.get("y_oc_target"),
                "t0_y_oc_target_scale": est.get("t0_y_oc_target_scale"),
                "t0_y_oc_l": est.get("t0_y_oc_l"),
                "t0_y_oc_u": est.get("t0_y_oc_u"),
                "target_pct": band_meta.get("target_pct"),
                "target_px": band_meta.get("target_px"),
                "r_hat": est.get("r_hat"),
                "residual": est.get("residual"),
                "remaining_oc": est.get("remaining_oc"),
                "c_hat_source": est.get("c_hat_source") or "bar_prefix",
                "y_tau": gate_yt,
                "y_tau_live": live_yt,
                "c_hat_score_source": "bar_prefix",
                "live_score_source": (
                    snap_for_gate.get("_score_source")
                    if isinstance(snap_for_gate, dict)
                    else None
                ),
            },
            minute_bars=mins,
            bar=bar,
            shares=shares_now,
            cost=cost,
            sellable_shares=path_sellable,
            ratio=ratio,
            cfg_day=cfg,
            cash=avail_cash,
            stock_code=stock_code,
            lot=lot,
            cost_model=cost_model,
            cost_params=cost_params,
            atr_pct=atr_pct,
            score_snap=score_pub,
            session_bar=session_bar,
            defer_eod=defer_eod,
            ref=ref,
        )
        trades = list(out.get("trades") or [])
        # 前窥防护：扫描态只落「截至本根」成交（通常仅 leg1）。
        # 午后 leg2/EOD 留在 out，由 _merge_slot_day 按时间序合并；不可提前归还可卖/现金。
        cur_at = _norm_trade_at(mb.get("datetime") or mb.get("date") or "")
        scan_trades = [
            t
            for t in trades
            if isinstance(t, dict) and _norm_trade_at(t.get("at")) <= cur_at
        ]
        if not scan_trades and trades and isinstance(trades[0], dict):
            # 仅当首笔钟点与触发根一致时回退（避免把午后腿误当成 leg1）
            from core.t0.close_band import parse_bar_hm as _parse_hm

            if _parse_hm({"datetime": trades[0].get("at")}) == hm:
                scan_trades = [trades[0]]
        if scan_trades:
            ok, nc, ns, nsel = _try_apply_slot_trades(
                scan_trades,
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
                        "t0_slot": sid,
                        "t0_slot_hm": hm,
                        "direction_used": direction,
                        "close_band": frozen.to_dict(),
                    },
                )
            else:
                cash_now, shares_now, sellable_now = nc, ns, nsel
                used_ratio += ratio
                leg1_rounds += 1
                last_leg1_idx = idx
                if direction == "buy_then_sell":
                    cover_committed += sum(
                        float(t.get("shares") or 0)
                        for t in scan_trades
                        if str(t.get("side") or "").endswith("buy")
                    )
            outs.append(out)
        else:
            # 无截至本根成交：不占轮次（防时间戳错位空占；全日腿也不入合并）
            if trades:
                out = _skip_result(
                    reason="本根无落账成交（时间戳未对齐）",
                    shares=shares,
                    bar=bar,
                    extra={
                        "t0_slot": sid,
                        "t0_slot_hm": hm,
                        "direction_used": direction,
                        "close_band": frozen.to_dict(),
                    },
                )
                outs.append(out)
            elif out.get("skipped"):
                outs.append(out)

    merged = _merge_slot_day(
        slot_outs=outs,
        bar=bar,
        shares=shares,
        cash=cash,
        sellable_shares=sellable_cap,
        minute_bars=mins,
    )
    merged["range_mode"] = "close_band"
    merged["t0_close_band_delta_pct"] = delta_pct
    merged["t0_round_ratio"] = round_ratio
    ps = day_price_space_payload(
        space,
        bar if isinstance(bar, dict) else None,
        daily_bar=day_anchor if isinstance(day_anchor, dict) else None,
    )
    merged["price_space"] = ps
    merged["price_space_scale"] = ps.get("scale_open")
    merged["price_space_mode"] = ps.get("estimate_mode")
    if last_enter_skip:
        merged["close_band_last_enter_skip"] = last_enter_skip
        if merged.get("skipped") and not merged.get("direction_reason"):
            merged["direction_reason"] = last_enter_skip
            merged["reason"] = last_enter_skip
            merged["signal_skip"] = True
    if last_sign_skip:
        merged["close_band_last_sign_skip"] = last_sign_skip
        if merged.get("skipped") and not merged.get("direction_reason"):
            merged["direction_reason"] = last_sign_skip
            merged["reason"] = last_sign_skip
            merged["signal_skip"] = True
    if last_y_tc_skip:
        merged["close_band_last_y_tc_skip"] = last_y_tc_skip
        if merged.get("skipped") and not merged.get("direction_reason"):
            merged["direction_reason"] = last_y_tc_skip
            merged["reason"] = last_y_tc_skip
            merged["signal_skip"] = True
    if last_y_t30_skip:
        merged["close_band_last_y_t30_skip"] = last_y_t30_skip
        if merged.get("skipped") and not merged.get("direction_reason"):
            merged["direction_reason"] = last_y_t30_skip
            merged["reason"] = last_y_t30_skip
            merged["signal_skip"] = True
    if last_y_tw_skip:
        merged["close_band_last_y_tw_skip"] = last_y_tw_skip
        if merged.get("skipped") and not merged.get("direction_reason"):
            merged["direction_reason"] = last_y_tw_skip
            merged["reason"] = last_y_tw_skip
            merged["signal_skip"] = True
    if last_y_t60_skip:
        merged["close_band_last_y_t60_skip"] = last_y_t60_skip
        if merged.get("skipped") and not merged.get("direction_reason"):
            merged["direction_reason"] = last_y_t60_skip
            merged["reason"] = last_y_t60_skip
            merged["signal_skip"] = True
    if last_y_t90_skip:
        merged["close_band_last_y_t90_skip"] = last_y_t90_skip
        if merged.get("skipped") and not merged.get("direction_reason"):
            merged["direction_reason"] = last_y_t90_skip
            merged["reason"] = last_y_t90_skip
            merged["signal_skip"] = True
    if last_tplus1_skip:
        if merged.get("skipped") and not merged.get("direction_reason"):
            merged["direction_reason"] = last_tplus1_skip
            merged["reason"] = last_tplus1_skip
            merged["signal_skip"] = False
    return _attach_close_band_scan(
        merged,
        minute_bars=mins,
        bar=bar,
        daily_bar=day_anchor,
        cfg=cfg,
        score_snap=open_snap,
        stock_code=code,
        hist_bars=hist_bars,
        tau_pool_day=tau_pool_day,
        slot_outs=outs,
    )


__all__ = [
    "simulate_t0_day_slots",
    "refresh_close_band_scan",
    "slot_budget_t0_ratio",
    "_open_close_band_round",
    "_slot_trade_legs",
    "_norm_trade_at",
    "_merge_slot_day",
]
