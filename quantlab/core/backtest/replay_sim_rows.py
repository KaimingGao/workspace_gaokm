"""Replay sim-trade rows, hit metrics, and default paper seed."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.backtest.replay_contrib import _fpx
from core.backtest.replay_fill import (
    REPLAY_FILL_CLOCK,
    clamp_replay_fill_clock,
    replay_fill_px,
)
from core.backtest.replay_minute import _replay_window_minutes

logger = logging.getLogger(__name__)

_DEBUG_SKIP_KEYS = (
    "T+1",
    "地板",
    "现金不足",
    "不可卖",
    "无有效报价",
    "无分钟",
    "不足一手",
    "cash_floor",
    "锁定",
    "price_space_mismatch",
    "价空间",
    "日分价",
)


def _is_debug_skip(sk: dict) -> bool:
    r = str((sk or {}).get("reason") or "")
    return any(k in r for k in _DEBUG_SKIP_KEYS)


def _infer_skip_side(sk: dict) -> str:
    s = str((sk or {}).get("side") or "").strip().lower()
    if s in ("buy", "sell"):
        return s
    r = str((sk or {}).get("reason") or "")
    if any(k in r for k in ("T+1", "不可卖", "不足一手", "锁定", "no_position")):
        return "sell"
    return "buy"


_REPLAY_TAU_HORIZON_KEYS = (
    "y_τw",
    "y_tw",
    "y_τ30",
    "y_t30",
    "predicted_score_t30",
    "y_t30_hat",
    "y_t30_realized",
    "t30_realized",
    "y_τ60",
    "y_t60",
    "predicted_score_t60",
    "y_t60_hat",
    "y_t60_realized",
    "t60_realized",
    "y_τ90",
    "y_t90",
    "predicted_score_t90",
    "y_t90_hat",
    "y_t90_realized",
    "t90_realized",
)


def _strip_replay_tau_horizons(row: dict) -> dict:
    """调仓回测明细不挂 ŷ_τw / ŷ_τ30/60/90。"""
    for k in _REPLAY_TAU_HORIZON_KEYS:
        row.pop(k, None)
    return row


def _merge_aux_yhat(row: dict, src: Optional[dict]) -> dict:
    try:
        from core.paper.rebalance.rank_lots import aux_yhat_fields, tip_explain_fields
    except Exception:  # noqa: BLE001
        logger.debug("aux_yhat_fields import failed", exc_info=True)
        return _strip_replay_tau_horizons(row)
    extra = aux_yhat_fields(src, include_tau_horizons=False)
    if extra:
        row.update(extra)
    tips = tip_explain_fields(src)
    if tips:
        row.update(tips)
    return _strip_replay_tau_horizons(row)


def _sim_y_co(src: Optional[dict]) -> Any:
    """ŷ_co 主字段；旧账本隔夜分在 y_on，只投影到 y_co。"""
    from core.signal.yhat_windows import pick_y_co

    src = src if isinstance(src, dict) else {}
    y_co = pick_y_co(src)
    if y_co is None:
        y_co = src.get("y_on")
    return y_co


def _sim_y_τc(src: Optional[dict]) -> Any:
    from core.signal.yhat_windows import pick_y_τc

    return pick_y_τc(src if isinstance(src, dict) else None)


def _skip_to_sim(sk: dict, day: str) -> dict:
    side = _infer_skip_side(sk)
    row = {
        "stock_code": sk.get("stock_code"),
        "stock_name": sk.get("stock_name"),
        "side": side,
        "shares": sk.get("shares"),
        "price": sk.get("price"),
        "amount": sk.get("amount"),
        "as_of": day,
        "signal_date": day,
        "status": "skipped",
        "action": "skip",
        "matrix_action": "skip",
        "y_oo_fuse": sk.get("y_oo_fuse"),
        "y_oo": sk.get("y_oo"),
        "y_co": _sim_y_co(sk),
        "y_τc": _sim_y_τc(sk),
        "residual": sk.get("residual"),
        "r_hat": sk.get("r_hat"),
        "remaining_oc": sk.get("remaining_oc"),
        "y_oo_fuse_score": sk.get("y_oo_fuse_score"),
        "rank_i": sk.get("rank_i"),
        "rank_n": sk.get("rank_n"),
        "lot_kind": sk.get("lot_kind"),
        "reason": sk.get("reason") or "跳过",
    }
    _copy_r_tau_pred(row, sk)
    return _merge_aux_yhat(row, sk)


def _hold_to_sim(
    plan_h: dict,
    day: str,
    holding: dict,
    *,
    date_maps: Dict[str, Dict[str, dict]],
    prices: Optional[Dict[str, float]] = None,
) -> dict:
    """续持腿：无成交；price=收盘，缺 day_close 时给收益率盯市。"""
    code = str(
        (holding or {}).get("stock_code") or (plan_h or {}).get("stock_code") or ""
    ).strip()
    sh = _fpx((holding or {}).get("shares"))
    if sh is None:
        sh = _fpx((plan_h or {}).get("shares"))
    bar = (date_maps.get(code) or {}).get(day) or {}
    close_px = (
        _fpx(bar.get("close"))
        or _fpx(bar.get("open"))
        or _fpx((prices or {}).get(code))
        or _fpx((holding or {}).get("last_price"))
        or _fpx((holding or {}).get("mark_price"))
        or _fpx((holding or {}).get("price"))
    )
    try:
        from core.paper.rebalance.watching_matrix import _open_date_of

        open_date = _open_date_of(holding, day) or ""
    except Exception:  # noqa: BLE001
        logger.debug("hold open_date failed %s", code, exc_info=True)
        open_date = str(
            (holding or {}).get("bought_date") or (holding or {}).get("bought_at") or ""
        )[:10]
    yf = (plan_h or {}).get("y_oo_fuse")
    name = (holding or {}).get("stock_name") or (plan_h or {}).get("stock_name")
    row = {
        "stock_code": code,
        "stock_name": name,
        "side": "hold",
        "shares": sh,
        "price": close_px,
        "as_of": day,
        "signal_date": day,
        "status": "held",
        "action": "hold",
        "matrix_action": "hold",
        "y_oo_fuse": yf,
        "y_oo": (plan_h or {}).get("y_oo"),
        "y_co": _sim_y_co(plan_h),
        "y_τc": _sim_y_τc(plan_h),
        "residual": (plan_h or {}).get("residual"),
        "r_hat": (plan_h or {}).get("r_hat"),
        "remaining_oc": (plan_h or {}).get("remaining_oc"),
        "y_oo_fuse_score": (plan_h or {}).get("y_oo_fuse_score"),
        "predicted_score": yf,
        "score": yf,
        "reason": (plan_h or {}).get("reason") or "续持",
        "open_date": open_date,
    }
    _copy_r_tau_pred(row, plan_h)
    return _merge_aux_yhat(row, plan_h)


def _hold_src_for_code(
    code: str,
    *,
    plan: Optional[dict],
    scored: Optional[Sequence[dict]] = None,
) -> dict:
    """续持盯市：拟卖/跳过未成交时仍带当日 ŷ，避免空行。"""
    c = str(code or "").strip()
    if not c:
        return {}
    plan_d = plan if isinstance(plan, dict) else {}
    for bucket in ("sells", "skips"):
        for row in plan_d.get(bucket) or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("stock_code") or "").strip() == c:
                return row
    for it in scored or []:
        if not isinstance(it, dict):
            continue
        if str(it.get("stock_code") or "").strip() != c:
            continue
        try:
            from core.paper.rebalance.path_matrix import scores_from_rebalance_item
            from core.paper.rebalance.rank_lots import y_oo_fuse_pct_of
            sc = scores_from_rebalance_item(it)
            rp = y_oo_fuse_pct_of(
                it,
                open_px=it.get("day_open"),
                price_tau=it.get("rebalance_px") or it.get("price_tau"),
            )
            rs = None if rp is None else float(rp) / 100.0
            from core.paper.rebalance.rank_lots import _debug_scores

            out = dict(it)
            out.update(_debug_scores(it, rp, rs))
            for k, v in sc.items():
                if v is not None:
                    out.setdefault(k, v)
            return out
        except Exception:  # noqa: BLE001
            logger.debug("hold src scores failed %s", c, exc_info=True)
            return dict(it)
    return {}


def _ledger_trades_to_sim(trades: Sequence[dict]) -> List[dict]:
    """账本买卖腿 → 前端成交表行（保留 side / 股数 / y_oo_fuse，不是研究独立腿）。"""
    out: List[dict] = []
    for t in trades or []:
        if not isinstance(t, dict):
            continue
        side = str(t.get("side") or "").strip().lower()
        if side not in ("buy", "sell"):
            continue
        day = str(t.get("as_of") or t.get("ts") or "")[:10]
        yf = t.get("y_oo_fuse")
        if yf is None:
            yf = t.get("score")
        row: Dict[str, Any] = {
            "stock_code": t.get("stock_code"),
            "stock_name": t.get("stock_name"),
            "side": side,
            "shares": t.get("shares"),
            "price": t.get("price"),
            "amount": t.get("amount"),
            "fees": t.get("fees"),
            "as_of": day,
            "signal_date": day,
            "status": "filled",
            "action": t.get("action") or t.get("matrix_action"),
            "y_oo_fuse": yf,
            "y_oo": t.get("y_oo"),
            "y_co": _sim_y_co(t),
            "y_τc": _sim_y_τc(t),
            "residual": t.get("residual"),
            "r_hat": t.get("r_hat"),
            "remaining_oc": t.get("remaining_oc"),
            "y_oo_fuse_score": t.get("y_oo_fuse_score"),
            "rank_i": t.get("rank_i"),
            "rank_n": t.get("rank_n"),
            "lot_kind": t.get("lot_kind"),
            "predicted_score": yf,
            "score": yf,
            "matrix_action": t.get("matrix_action") or t.get("action"),
            "note": t.get("note"),
            "reason": t.get("reason") or t.get("note"),
            "pnl_pct": t.get("pnl_pct"),
            "origin": t.get("origin"),
            "open_date": t.get("open_date"),
            "rebalance_px": t.get("rebalance_px"),
            "day_open": t.get("day_open"),
        }
        _copy_r_tau_pred(row, t)
        _merge_aux_yhat(row, t)
        if side == "buy":
            row["entry_date"] = day
            row["entry_price"] = t.get("price")
            if not row.get("open_date"):
                row["open_date"] = day
        else:
            row["exit_date"] = day
            row["exit_price"] = t.get("price")
            row["return_pct"] = t.get("pnl_pct")
        out.append(row)
    return out


def _px(bar: Optional[dict], key: str) -> Optional[float]:
    if not isinstance(bar, dict):
        return None
    try:
        v = float(bar.get(key) or 0)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _pct_ret(start: Optional[float], end: Optional[float]) -> Optional[float]:
    if start is None or end is None or start <= 0 or end <= 0:
        return None
    return round((float(end) / float(start) - 1.0) * 100.0, 4)


def _copy_r_tau_pred(row: dict, src: Optional[dict]) -> dict:
    """透传 R̂_τ（remaining / r_hat）；缺则留给 stamp_r_tau_on_row 用成交价回填。"""
    if not isinstance(src, dict):
        return row
    r_hat = src.get("r_hat")
    if r_hat is None:
        r_hat = src.get("remaining_oc")
    if r_hat is not None:
        row["r_hat"] = r_hat
        rem = src.get("remaining_oc")
        row["remaining_oc"] = rem if rem is not None else r_hat
    return row


def stamp_r_tau_on_row(
    row: dict,
    *,
    daily_bar: Optional[dict] = None,
    minute_bars: Optional[Sequence[dict]] = None,
    fill_clock: str = REPLAY_FILL_CLOCK,
) -> dict:
    """成交明细 R_τ：R̂_τ 用行上已有的 remaining；真实=close[T]/price(τ)−1。

    买卖成交用账上成交价当 price(τ)；持/跳过用调仓钟 5m（与净值「持有」新开段同锚）。
    """
    if not isinstance(row, dict):
        return row
    bar = daily_bar if isinstance(daily_bar, dict) else {}
    if bar.get("session_overlay"):
        return row
    clock = clamp_replay_fill_clock(fill_clock)
    mins = _replay_window_minutes(minute_bars)
    px_tau = replay_fill_px(daily_bar=bar, minute_bars=mins, fill_clock=clock)
    status = str(row.get("status") or "").strip().lower()
    side = str(row.get("side") or "").strip().lower()
    row_px = _fpx(row.get("price"))
    if status not in ("skipped", "held") and side in ("buy", "sell") and row_px:
        px_tau = row_px
    close_px = _px(bar, "close")
    open_px = _px(bar, "open")
    if open_px is not None:
        row["day_open"] = open_px
    if close_px is not None:
        row["day_close"] = close_px
    if _fpx(row.get("rebalance_px")) is None and px_tau is not None:
        row["rebalance_px"] = px_tau
    real = _pct_ret(px_tau, close_px)
    if real is not None:
        row["r_realized"] = real
        row["y_r_realized"] = real
    r_hat = _fnum(row.get("r_hat"))
    if r_hat is None:
        r_hat = _fnum(row.get("remaining_oc"))
    if r_hat is not None:
        row["r_hat"] = round(float(r_hat), 4)
        if row.get("remaining_oc") is None:
            row["remaining_oc"] = row["r_hat"]
    return row


# |y_oo_fuse| < 0.05% → 无方向，与 score_ledger 同口径。
HIT_YHAT_EPS = 0.05


def _fnum(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n != n:
        return None
    return n


def _sign_hit_yf(yhat: Optional[float], realized: Optional[float]) -> Optional[bool]:
    if yhat is None or realized is None:
        return None
    if abs(float(yhat)) < HIT_YHAT_EPS:
        return None
    if abs(float(realized)) < 1e-12:
        return None
    return (float(yhat) > 0) == (float(realized) > 0)


def _row_as_of_day(row: dict) -> str:
    return str(row.get("as_of") or row.get("date") or row.get("signal_date") or "")[:10]


def fuse_hit_metrics(
    rows: Sequence[dict],
    *,
    last_day: Optional[str] = None,
) -> Tuple[Optional[float], int, int]:
    """成交腿 sign(y_oo_fuse)=sign(y_oo_fuse_realized)。

    真实 y_oo_fuse = (open[T+1]−price(τ))/open[T]，最后一个交易日无次日开、不进分母。
    跳过/持仓腿不计。返回 (命中率%, 有方向笔数, 命中笔数)。
    """
    last = str(last_day or "")[:10]
    hits = 0
    n = 0
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        if str(r.get("status") or "") in ("skipped", "held"):
            continue
        day = _row_as_of_day(r)
        if last and day == last:
            continue
        yf = _fnum(r.get("y_oo_fuse"))
        if yf is None:
            rs = _fnum(r.get("y_oo_fuse_score"))
            if rs is not None:
                yf = float(rs) * 100.0
        real = _fnum(r.get("y_oo_fuse_realized"))
        hit = _sign_hit_yf(yf, real)
        if hit is None:
            continue
        n += 1
        if hit:
            hits += 1
    if n <= 0:
        return None, 0, 0
    return round(hits / n * 100.0, 1), n, hits


def _bar_date_key(bar_or_date: Any) -> str:
    if isinstance(bar_or_date, dict):
        return str(bar_or_date.get("date") or "")[:10]
    return str(bar_or_date or "")[:10]


def _next_bar_after(
    dm: Dict[str, dict],
    day: str,
    *,
    dates: Sequence[str],
    index: Optional[int] = None,
) -> Optional[dict]:
    """T+1 日 K：先覆盖率日历下一根，缺则用该票自己更晚的一根。

    回测窗末日若仓里还有下一根（停牌后复牌、lookback 截在覆盖率日历前），
    仍应对账 ŷ_oo / ŷ_co；不能只看 dates[i+1]。
    """
    d = str(day or "")[:10]
    if not d or not isinstance(dm, dict):
        return None
    if index is not None and 0 <= int(index) + 1 < len(dates):
        nxt = dm.get(dates[int(index) + 1])
        if isinstance(nxt, dict) and not nxt.get("session_overlay"):
            return nxt
    later: Dict[str, dict] = {}
    for key, bar in dm.items():
        if not isinstance(bar, dict) or bar.get("session_overlay"):
            continue
        kd = _bar_date_key(key) or _bar_date_key(bar)
        if kd > d:
            later[kd] = bar
    if not later:
        return None
    return later[min(later)]


def realized_yhat_windows(
    code: str,
    day: str,
    *,
    dates: Sequence[str],
    date_maps: Dict[str, Dict[str, dict]],
    date_index: Optional[Dict[str, int]] = None,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """事后对照窗：返回 (r_cc, r_on, r_tau_open)。

    r_tau_open=close/open−1（仅复合 ŷ_oo 真值）；成交明细 ŷ_τc 真值改用
    ``stamp_r_tau_on_row`` 的 close/price(τ)−1（随 fill_clock）。
    r_on=open[T+1]/close[T]−1；r_cc 仅诊断。不进决策。
    """
    d = str(day or "")[:10]
    c = str(code or "").strip()
    if not d or not c:
        return None, None, None
    if date_index is not None:
        i = date_index.get(d)
    else:
        try:
            i = list(dates).index(d)
        except ValueError:
            i = None
    if i is None or i < 0:
        return None, None, None
    dm = date_maps.get(c) or {}
    bar = dm.get(d)
    if isinstance(bar, dict) and bar.get("session_overlay"):
        return None, None, None
    prev = dm.get(dates[i - 1]) if i > 0 else None
    nxt = _next_bar_after(dm, d, dates=dates, index=i)
    r_cc = _pct_ret(_px(prev, "close"), _px(bar, "close"))
    r_on = _pct_ret(_px(bar, "close"), _px(nxt, "open"))
    r_tau = _pct_ret(_px(bar, "open"), _px(bar, "close"))
    return r_cc, r_on, r_tau


def _default_paper(
    *,
    initial_cash: float,
    top_k: int,
    cost_model: str,
) -> dict:
    rules: Dict[str, Any] = {
        "max_positions": int(top_k),
        # 单票上限不随观察池放大而变小（否则 1/N 会把 100 股手数挡掉）
        "position_pct": 0.25,
        "min_cash_pct": 0.0,
        "horizon_days": 1,
        "execution_mode": "next_open",
    }
    # 回放关闭做 T，避免卖腿读盘中分钟线打网
    rules["t0"] = {"enabled": False}
    cash = float(initial_cash)
    return {
        "cash": cash,
        "initial_cash": cash,
        "holdings": [],
        "trades": [],
        "snapshots": [],
        "operation_log": [],
        "strategy_id": "short_conservative",
        "cost_model": cost_model,
        "rules": rules,
    }

