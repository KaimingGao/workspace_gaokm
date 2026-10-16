"""Replay fill clocks, amounts, and price-space gates."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.intraday.clocks import (
    LIVE_FILL_CLOCK,
    LIVE_FILL_CLOCKS,
    REPLAY_FILL_CLOCK,
    REPLAY_FILL_CLOCKS,
    clamp_fill_clock as _clamp_fill_clock,
    clamp_live_fill_clock,
    clamp_replay_fill_clock,
)
from core.intraday.price_space import DEFAULT_PRICE_SPACE_MAX_DEV_PCT

logger = logging.getLogger(__name__)

REPLAY_AMOUNT_BASE = 10_000.0
REPLAY_AMOUNT_STRONG = 20_000.0
REPLAY_AMOUNT_MIN = 1_000.0
REPLAY_AMOUNT_MAX = 1_000_000.0
REPLAY_LOT_BASE = REPLAY_AMOUNT_BASE
REPLAY_LOT_STRONG = REPLAY_AMOUNT_STRONG
REPLAY_INITIAL_CASH = 200_000.0
REPLAY_CASH_FLOOR = 0.0
REPLAY_INITIAL_CASH_MIN = 10_000.0
REPLAY_INITIAL_CASH_MAX = 1.0e8
# 历史回测 / 日报 / live rank_lots 缺省均为 0.1%。
REPLAY_FUSE_ENTER = 0.001
REPLAY_FUSE_STRONG = 0.001
REPLAY_FUSION_W_TRADE = 0.6
REPLAY_FUSION_W_NOWCAST = 0.4


def _fpx(v: Any) -> Optional[float]:
    """Positive price parse; shared with ``replay_contrib`` (imported lazily to avoid cycles)."""
    from core.backtest.replay_contrib import _fpx as _impl

    return _impl(v)


def _window_mins(minute_bars: Optional[Sequence[dict]] = None) -> List[dict]:
    from core.backtest.replay_minute import _replay_window_minutes

    return _replay_window_minutes(minute_bars)


def clamp_replay_initial_cash(raw: Any, default: float = REPLAY_INITIAL_CASH) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = float(default)
    if v != v:
        v = float(default)
    return max(float(REPLAY_INITIAL_CASH_MIN), min(float(v), float(REPLAY_INITIAL_CASH_MAX)))


def clamp_replay_amount(raw: Any, default: float = REPLAY_AMOUNT_BASE) -> float:
    """回测金额：整百，1000～100 万。"""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = float(default)
    if v != v:
        v = float(default)
    v = max(float(REPLAY_AMOUNT_MIN), min(float(v), float(REPLAY_AMOUNT_MAX)))
    return float(int(round(v / 100.0)) * 100)


def clamp_replay_amount_pair(
    lot_base_amount: Any = None,
    lot_strong_amount: Any = None,
) -> Tuple[float, float]:
    base = clamp_replay_amount(
        REPLAY_AMOUNT_BASE if lot_base_amount is None else lot_base_amount,
        REPLAY_AMOUNT_BASE,
    )
    strong = clamp_replay_amount(
        REPLAY_AMOUNT_STRONG if lot_strong_amount is None else lot_strong_amount,
        REPLAY_AMOUNT_STRONG,
    )
    if strong < base:
        strong = base
    return base, strong


def _minute_bar_hm(mb: Optional[dict]) -> str:
    if not isinstance(mb, dict):
        return ""
    try:
        from core.t0.close_band import parse_bar_hm

        return str(parse_bar_hm(mb) or "")[:5]
    except Exception:  # noqa: BLE001
        logger.debug("parse replay minute hm failed", exc_info=True)
        return ""


def replay_fill_px(
    *,
    daily_bar: Optional[dict],
    minute_bars: Optional[Sequence[dict]] = None,
    fill_clock: str = REPLAY_FILL_CLOCK,
) -> Optional[float]:
    """买入/精确钟成交价：09:30=有窗内分钟则首根开盘，否则日开盘；其它钟=该档 5m 收盘。"""
    clock = clamp_replay_fill_clock(fill_clock)
    mins = _window_mins(minute_bars)
    if clock == "09:30":
        if mins:
            first = mins[0]
            hm = _minute_bar_hm(first)
            if hm == "09:30":
                return _fpx(first.get("close")) or _fpx(first.get("open"))
            return _fpx(first.get("open")) or _fpx(first.get("close"))
        if isinstance(daily_bar, dict):
            return _fpx(daily_bar.get("open")) or _fpx(daily_bar.get("close"))
        return None
    for b in mins:
        if _minute_bar_hm(b) == clock:
            return _fpx(b.get("close")) or _fpx(b.get("open"))
    return None


def replay_sell_fill_px(
    *,
    daily_bar: Optional[dict],
    minute_bars: Optional[Sequence[dict]] = None,
    fill_clock: str = REPLAY_FILL_CLOCK,
) -> Tuple[Optional[float], str]:
    """清仓价：该钟 → 其后～10:00 下一根 → 09:30 / 日开盘。

    返回 ``(px, source)``：``clock`` 命中该钟；``09:45`` 等为后一档；
    ``09:30`` 为窗内首根；``daily_open`` 为日 K 开盘。
    """
    clock = clamp_replay_fill_clock(fill_clock)
    exact = replay_fill_px(
        daily_bar=daily_bar, minute_bars=minute_bars, fill_clock=clock
    )
    if exact is not None and exact > 0:
        if clock == "09:30" and not _window_mins(minute_bars):
            return exact, "daily_open"
        return exact, "clock"
    if clock == "09:30":
        return None, ""
    try:
        idx = REPLAY_FILL_CLOCKS.index(clock)
    except ValueError:
        idx = 0
    for later in REPLAY_FILL_CLOCKS[idx + 1 :]:
        px = replay_fill_px(
            daily_bar=daily_bar, minute_bars=minute_bars, fill_clock=later
        )
        if px is not None and px > 0:
            return px, later
    px = replay_fill_px(
        daily_bar=daily_bar, minute_bars=minute_bars, fill_clock="09:30"
    )
    if px is not None and px > 0:
        if _window_mins(minute_bars):
            return px, "09:30"
        return px, "daily_open"
    return None, ""


def _sell_fill_fallback_note(clock: str, src: str) -> str:
    if not src or src == "clock":
        return ""
    if src == "daily_open":
        return f"缺{clock}回退日开盘"
    return f"缺{clock}回退{src}"


def replay_price_space_cfg(override: Optional[dict] = None) -> Dict[str, Any]:
    """日分价闸阈值：默认跟做 T 同一套（闸开；开盘差已下线=0；昨收差 5%）。"""
    from core.t0.config import load_t0_rules

    cfg = load_t0_rules(override if isinstance(override, dict) else None)
    try:
        max_dev = float(
            cfg.get("t0_price_space_max_dev_pct", DEFAULT_PRICE_SPACE_MAX_DEV_PCT)
        )
    except (TypeError, ValueError):
        max_dev = float(DEFAULT_PRICE_SPACE_MAX_DEV_PCT)
    try:
        prev_dev = float(
            cfg.get("t0_price_space_prev_dev_pct", max_dev)
        )
    except (TypeError, ValueError):
        prev_dev = float(max_dev)
    return {
        "t0_price_space_gate": bool(cfg.get("t0_price_space_gate", True)),
        "t0_price_space_max_dev_pct": max(0.0, min(max_dev, 5.0)),
        "t0_price_space_prev_dev_pct": max(0.0, min(prev_dev, 5.0)),
    }


def replay_price_space_check(
    daily_bar: Optional[dict],
    minute_bars: Optional[Sequence[dict]] = None,
    cfg: Optional[dict] = None,
) -> Tuple[Optional[str], bool]:
    """返回 ``(skip_reason, mismatch_seen)``。

    有调仓窗分钟才比日开 vs 分钟首开、日昨 vs 分昨；无窗口分钟 → (None, False)。
    ``mismatch_seen`` 不论闸开/关；``skip_reason`` 仅闸开时非空。
    """
    mins = _window_mins(minute_bars)
    if not mins:
        return None, False
    cfg_d = cfg if isinstance(cfg, dict) else replay_price_space_cfg()
    from core.t0.close_band import resolve_t0_price_space

    day = daily_bar if isinstance(daily_bar, dict) else None
    probe = dict(cfg_d)
    probe["t0_price_space_gate"] = True
    space = resolve_t0_price_space(day, mins, probe, daily_bar=day)
    raw = space.get("skip_reason") if isinstance(space, dict) else None
    seen = bool(raw)
    if not seen:
        return None, False
    if cfg_d.get("t0_price_space_gate") is False:
        return None, True
    return str(raw), True


def _stamp_replay_prev_close(
    bar: Optional[dict],
    *,
    date_maps: Dict[str, Dict[str, dict]],
    dates: Sequence[str],
    code: str,
    day_i: int,
) -> Optional[dict]:
    if not isinstance(bar, dict):
        return None
    out = dict(bar)
    if out.get("prev_close") is None and day_i > 0 and day_i <= len(dates):
        prev = (date_maps.get(code) or {}).get(dates[day_i - 1]) or {}
        pc = _fpx((prev or {}).get("close"))
        if pc is None:
            pc = _fpx((prev or {}).get("open"))
        if pc is not None:
            out["prev_close"] = pc
    return out


def _load_replay_price_space_cfg(
    override: Optional[dict] = None,
) -> Dict[str, Any]:
    ov: Dict[str, Any] = {}
    try:
        from core.execution import resolve_effective_execution
        from core.paper.ledger import load_paper

        live = load_paper()
        bundle = resolve_effective_execution(
            paper=live if isinstance(live, dict) else None,
            channel="backtest",
            has_minute=True,
        )
        raw = ((bundle.get("overlays") or {}).get("t0")) or {}
        if isinstance(raw, dict):
            for k in (
                "t0_price_space_gate",
                "t0_price_space_max_dev_pct",
                "t0_price_space_prev_dev_pct",
            ):
                if raw.get(k) is not None:
                    ov[k] = raw[k]
    except Exception:  # noqa: BLE001
        logger.debug("replay price_space cfg from execution failed", exc_info=True)
    if isinstance(override, dict):
        gate = override.get("t0_price_space_gate")
        if gate is None:
            gate = override.get("price_space_gate")
        if gate is not None:
            ov["t0_price_space_gate"] = bool(gate)
        for k in ("t0_price_space_max_dev_pct", "t0_price_space_prev_dev_pct"):
            if override.get(k) is not None:
                ov[k] = override[k]
    return replay_price_space_cfg(ov or None)


def _apply_price_space_plan_skips(
    plan: dict,
    mismatch: Dict[str, str],
) -> None:
    """无分钟价的买卖：把「无有效报价」改成日分价错位；拟卖且无成交价则改 skip。"""
    if not mismatch or not isinstance(plan, dict):
        return
    skips = [sk for sk in (plan.get("skips") or []) if isinstance(sk, dict)]
    for sk in skips:
        c = str(sk.get("stock_code") or "").strip()
        if c in mismatch:
            sk["reason"] = mismatch[c]
    kept_sells: List[dict] = []
    for leg in plan.get("sells") or []:
        if not isinstance(leg, dict):
            continue
        c = str(leg.get("stock_code") or "").strip()
        if c in mismatch:
            row = dict(leg)
            row["side"] = "sell"
            row["action"] = "skip"
            row["matrix_action"] = "skip"
            row["reason"] = mismatch[c]
            skips.append(row)
        else:
            kept_sells.append(leg)
    plan["sells"] = kept_sells
    plan["skips"] = skips

