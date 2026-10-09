"""纸面可实现回放：每个交易日按调仓钟走 rank_lots（ranking=w_oo·((ŷ_oo+1)/(1+rot)−1)+w_τc·((1+ŷ_τc)(1+w_co·ŷ_co)−1) · 按金额换算股数）。

ŷ_oo 始终是 09:30 开盘信息集。ŷ_τc：09:30 成交钟用开盘 Z（``use_minute_tau=False``）；
09:35–10:00 用截至该钟的 5m 前缀重算（与做 T ``rescore_scores_at_fixed_prefix`` 同路径）。
对照列 ŷ_hl 走开盘 Z 挂上；成交钟>09:30 随前缀重算。不挂 ŷ_τw / ŷ_τ30/60/90（调仓回测不用）。
成交价：09:30 取首根开盘（≈集合竞价/开盘价），其后用该档 5 分钟 K 收盘。
无分钟时 09:30 回退日 K 开盘。其它钟：买入缺该根则跳过；清仓按该钟之后～10:00
下一根，再回退 09:30 / 日开盘（避免缺上午 K 把底仓拿到收盘）。
日分价闸只用 09:30–10:00 窗口内的分钟（尾盘残缺仓不当开盘锚）。
有窗口分钟时复用做 T ``resolve_t0_price_space``：|日开/分开−1| 或 |日昨/分昨−1|
超阈则该票当日 skip（``price_space_mismatch``）；比的是开/昨收锚，不是成交价。
live Follow 不走此闸。

与 ``topk_research``（独立腿聚合）并列。本金默认 20 万（表单可改）、不留现金地板（买到现金不够为止）；T+1 仍生效。
历史回测金额默认 1 万 / 2 万（live 读已保存 lot_base_amount/lot_strong_amount，缺省 1 万/2 万）。
"""

from __future__ import annotations

import copy
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

ENGINE_ID = "paper_replay"
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
REPLAY_RANK_ENTER = 0.001
REPLAY_RANK_STRONG = 0.001
REPLAY_FUSION_W_TRADE = 0.6
REPLAY_FUSION_W_NOWCAST = 0.4
# 调仓成交钟：与 A 股 5 分钟 K 对齐（首根通常标 09:35 = 09:30–09:35）。
REPLAY_FILL_CLOCKS = (
    "09:30",
    "09:35",
    "09:40",
    "09:45",
    "09:50",
    "09:55",
    "10:00",
)
REPLAY_FILL_CLOCK = "09:30"
# Live「启动时间」：可含集合竞价定开 09:25；回测钟仍从 09:30 起。
LIVE_FILL_CLOCKS = ("09:25",) + REPLAY_FILL_CLOCKS
LIVE_FILL_CLOCK = "09:30"


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


def clamp_replay_lot(raw: Any, default: float = REPLAY_AMOUNT_BASE) -> float:
    """兼容旧名：现为金额。"""
    return clamp_replay_amount(raw, default)


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


def clamp_replay_lot_pair(
    lot_base: Any = None,
    lot_strong: Any = None,
) -> Tuple[float, float]:
    return clamp_replay_amount_pair(lot_base, lot_strong)


def _clamp_fill_clock(
    raw: Any,
    *,
    allowed: Tuple[str, ...],
    default: str,
) -> str:
    fallback = str(default or "").strip()[:5] or (allowed[0] if allowed else "09:30")
    if fallback not in allowed:
        fallback = allowed[0] if allowed else "09:30"
    s = str(raw or "").strip().replace("：", ":")
    if not s:
        return fallback
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 4:
        hh, mm = digits[:2], digits[2:4]
        cand = f"{hh}:{mm}"
        if cand in allowed:
            return cand
    head = s[:5]
    if len(head) >= 4 and head[1] == ":":
        head = f"0{head}"
    head = head[:5]
    if head in allowed:
        return head
    return fallback


def clamp_replay_fill_clock(raw: Any, default: str = REPLAY_FILL_CLOCK) -> str:
    """历史回测成交钟：09:30–10:00 每 5 分钟一档；无法解析则回默认。"""
    return _clamp_fill_clock(raw, allowed=REPLAY_FILL_CLOCKS, default=default)


def clamp_live_fill_clock(raw: Any, default: str = LIVE_FILL_CLOCK) -> str:
    """Live 启动时间：09:25–10:00（含集合竞价定开）；无法解析则回默认。"""
    return _clamp_fill_clock(raw, allowed=LIVE_FILL_CLOCKS, default=default)


def _minute_bar_hm(mb: Optional[dict]) -> str:
    if not isinstance(mb, dict):
        return ""
    try:
        from core.t0.close_band import parse_bar_hm

        return str(parse_bar_hm(mb) or "")[:5]
    except Exception:  # noqa: BLE001
        logger.debug("parse replay minute hm failed", exc_info=True)
        return ""


def _replay_window_minutes(
    minute_bars: Optional[Sequence[dict]] = None,
) -> List[dict]:
    """只留调仓窗 09:30–10:00 的 5m；尾盘残缺仓不当开盘锚。"""
    out: List[dict] = []
    for b in minute_bars or []:
        if not isinstance(b, dict):
            continue
        if _minute_bar_hm(b) in REPLAY_FILL_CLOCKS:
            out.append(b)
    out.sort(key=lambda b: str(b.get("datetime") or b.get("date") or ""))
    return out


def replay_fill_px(
    *,
    daily_bar: Optional[dict],
    minute_bars: Optional[Sequence[dict]] = None,
    fill_clock: str = REPLAY_FILL_CLOCK,
) -> Optional[float]:
    """买入/精确钟成交价：09:30=窗内首根开盘（无则日开盘）；其它钟=该档 5m 收盘。"""
    clock = clamp_replay_fill_clock(fill_clock)
    mins = _replay_window_minutes(minute_bars)
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
        if clock == "09:30" and not _replay_window_minutes(minute_bars):
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
        if _replay_window_minutes(minute_bars):
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
    """日分价闸阈值：默认跟做 T 同一套（闸开、开盘差 5%、昨收差 5%）。"""
    from core.t0.close_band import DEFAULT_PRICE_SPACE_MAX_DEV_PCT
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
    mins = _replay_window_minutes(minute_bars)
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


def _local_minute_by_date(
    code: str, period: str = "5"
) -> Tuple[Dict[str, List[dict]], Dict[str, Any]]:
    """优先读本地 5m 缓存（忽略 TTL），与做 T 回测同口径。"""
    try:
        from adapters.market.history import resolve_market_code
        from adapters.market.minute_history import _load_stale_minute, group_minute_bars_by_date

        market, bare = resolve_market_code(code)
        if market != "CN" or not bare:
            raw = str(code or "").strip()
            if raw.isdigit() and len(raw) == 6:
                market, bare = "CN", raw
            else:
                return {}, {"ok": False, "error": f"仅支持 A 股分钟线: {code}"}
        packed = _load_stale_minute(market, bare, str(period or "5"))
        if not packed or not packed[0]:
            return {}, {"ok": False, "from_cache": False, "period": period}
        bars, meta = packed
        meta = dict(meta or {})
        meta["period"] = period
        return group_minute_bars_by_date(bars), meta
    except Exception as e:  # noqa: BLE001
        logger.debug("local minute cache failed for replay %s", code, exc_info=True)
        return {}, {"ok": False, "error": str(e), "period": period}


def load_replay_minute_bars(
    codes: Sequence[str],
    *,
    lookback_days: int = 50,
    period: str = "5",
    max_workers: int = 8,
) -> Tuple[Dict[str, Dict[str, List[dict]]], Dict[str, Any]]:
    """观察池 5m：先本地仓，缺则短超时补拉（跳过东财，避免拖死整次回测）。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    cleaned = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    out: Dict[str, Dict[str, List[dict]]] = {}
    missing: List[Dict[str, Any]] = []
    if not cleaned:
        return {}, {"ok": True, "period": period, "covered": 0, "missing": []}

    lb = max(10, min(int(lookback_days or 50), 120))
    workers = max(1, min(int(max_workers or 8), 16, len(cleaned)))

    def _one(code: str) -> Tuple[str, Dict[str, List[dict]], Dict[str, Any]]:
        by_date, meta = _local_minute_by_date(code, period)
        if by_date:
            return code, by_date, meta
        try:
            from quant.research.t0_backtest import _fetch_minute_by_date

            by_date, meta = _fetch_minute_by_date(
                code, period=period, lookback_days=lb
            )
            return code, by_date or {}, dict(meta or {})
        except Exception as e:  # noqa: BLE001
            logger.debug("replay minute fetch failed %s", code, exc_info=True)
            return code, {}, {"ok": False, "error": str(e), "period": period}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_one, c): c for c in cleaned}
        for fut in as_completed(futs):
            code = futs[fut]
            try:
                _c, by_date, meta = fut.result()
            except Exception as e:  # noqa: BLE001
                logger.debug("replay minute worker failed %s", code, exc_info=True)
                missing.append({"stock_code": code, "error": str(e)})
                continue
            if by_date:
                out[code] = by_date
            else:
                missing.append(
                    {
                        "stock_code": code,
                        "error": (meta or {}).get("error") or "无分钟 K",
                    }
                )
    from core.research.bar_identity import drop_minute_maps_mismatch

    out, mismatched = drop_minute_maps_mismatch(out)
    for code in mismatched:
        missing.append({"stock_code": code, "error": "分钟线与代码不符"})
    return out, {
        "ok": True,
        "period": period,
        "lookback_days": lb,
        "covered": len(out),
        "missing": missing,
        "universe": len(cleaned),
    }


def _bars_by_date(bars: List[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for b in bars or []:
        d = str(b.get("date") or "").strip()
        if d:
            out[d] = b
    return out


def _common_dates(stock_bars: Dict[str, List[dict]]) -> List[str]:
    """全交集日历。回测主路径用 ``_coverage_dates``；此处留给对照测试。"""
    common: Optional[set] = None
    for bars in stock_bars.values():
        keys = set(_bars_by_date(bars).keys())
        common = keys if common is None else common & keys
    return sorted(common or [])


def _coverage_dates(
    stock_bars: Dict[str, List[dict]],
    *,
    min_coverage: float = 0.8,
) -> List[str]:
    """多数票有 K 的日期。全交集会被停牌/次新一张票卡死，lookback 拉长也不生效。"""
    n = max(1, len(stock_bars or {}))
    counts: Dict[str, int] = {}
    for bars in (stock_bars or {}).values():
        seen = set()
        for b in bars or []:
            if not isinstance(b, dict):
                continue
            d = str(b.get("date") or "").strip()[:10]
            if d and d not in seen:
                seen.add(d)
                counts[d] = counts.get(d, 0) + 1
    if n <= 3:
        thresh = n
    else:
        cov = min(1.0, max(0.5, float(min_coverage)))
        thresh = max(2, int(n * cov + 0.999999))
    dates = sorted(d for d, c in counts.items() if c >= thresh)
    return dates or sorted(counts)


def _replay_calendar(
    stock_bars: Dict[str, List[dict]],
    *,
    lookback: Optional[int] = None,
    min_history: int = 12,
    max_window: int = 30,
    min_coverage: float = 0.8,
) -> Tuple[List[str], int]:
    """回测交易日：覆盖率日历的最后 lookback 根；前面垫特征窗，不占样本长度。"""
    cal = _coverage_dates(stock_bars, min_coverage=min_coverage)
    if not cal:
        return [], 0
    try:
        lb = int(lookback) if lookback is not None else 0
    except (TypeError, ValueError):
        lb = 0
    core = cal[-lb:] if lb > 0 else cal
    pad_n = max(int(min_history or 1), int(max_window or 1), 1)
    pre = [d for d in cal if d < core[0]]
    dates = pre[-pad_n:] + list(core)
    start_i = dates.index(core[0]) if core[0] in dates else 0
    if start_i < 1 and len(dates) > 1:
        start_i = 1
    return dates, start_i


def _fpx(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        n = float(v)
        return n if n > 0 and n == n else None
    s = str(v).replace("元", "").replace(",", "").strip()
    if not s:
        return None
    try:
        n = float(s)
    except (TypeError, ValueError):
        return None
    return n if n > 0 and n == n else None


DAY_LEG_TOP = 8


def _watching_name_map() -> Dict[str, str]:
    try:
        from core.watching.store import read_watching, watchlist_names_for

        uni = read_watching()
        wl = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
        names = watchlist_names_for(uni)
        out: Dict[str, str] = {}
        for code, name in zip(wl, names or []):
            nm = str(name or "").strip()
            if nm and nm != code:
                out[code] = nm
        return out
    except Exception:  # noqa: BLE001
        logger.debug("watching name map failed in paper_replay", exc_info=True)
        return {}


def _usable_stock_name(code: str, *cands: Any) -> str:
    c = str(code or "").strip()
    for raw in cands:
        nm = str(raw or "").strip()
        if nm and nm != c and not nm.isdigit():
            return nm
    return ""


def _stamp_stock_names(
    rows: Sequence[dict],
    name_by_code: Optional[Dict[str, str]] = None,
) -> None:
    """把观察池中文名写到打分/成交行；缺名时不要用代码冒充。"""
    names = name_by_code or {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stock_code") or "").strip()
        if not code:
            continue
        nm = _usable_stock_name(code, names.get(code), row.get("stock_name"))
        if not nm:
            try:
                from core.t0.intraday import resolve_stock_name

                nm = resolve_stock_name(
                    code,
                    fallback=str(row.get("stock_name") or ""),
                    name_by_code=names,
                )
            except Exception:  # noqa: BLE001
                logger.debug("resolve replay stock name failed %s", code, exc_info=True)
                nm = ""
        if nm and nm != code:
            row["stock_name"] = nm


def _leg_stock_name(
    code: str,
    start: dict,
    end: dict,
    name_by_code: Optional[Dict[str, str]] = None,
) -> str:
    mapped = str((name_by_code or {}).get(code) or "").strip()
    raw = str(end.get("stock_name") or start.get("stock_name") or "").strip()
    if mapped and mapped != code:
        return mapped
    if raw and raw != code:
        return raw
    return mapped or raw or code


def _holding_snap(holdings: Sequence[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for h in holdings or []:
        if not isinstance(h, dict):
            continue
        code = str(h.get("stock_code") or "").strip()
        sh = _fpx(h.get("shares"))
        if not code or sh is None:
            continue
        out[code] = {
            "stock_code": code,
            "stock_name": str(h.get("stock_name") or code),
            "shares": float(sh),
        }
    return out


def _bar_px(
    date_maps: Dict[str, Dict[str, dict]],
    code: str,
    day: str,
    *fields: str,
) -> Optional[float]:
    bar = (date_maps.get(code) or {}).get(day) or {}
    for f in fields:
        v = _fpx(bar.get(f))
        if v is not None:
            return float(v)
    return None


def day_stock_legs(
    *,
    start: Dict[str, dict],
    end: Dict[str, dict],
    date_maps: Dict[str, Dict[str, dict]],
    prev_day: str,
    day: str,
    open_px: Dict[str, float],
    prev_equity: float,
    name_by_code: Optional[Dict[str, str]] = None,
    top: Optional[int] = DAY_LEG_TOP,
) -> Tuple[List[dict], int]:
    """当日个股盈亏：隔夜段 open−昨收 + 当日段 close−open，贡献=盈亏/昨净值。

    ``ret_pct`` 是持有段，不是股票全日收盘涨跌：清仓只计隔夜，新开只计开→收。
    ``top<=0`` 返回全日全部票（分票贡献汇总用）；净值悬停仍截 ``DAY_LEG_TOP``。
    """
    codes = sorted(set(start) | set(end))
    rows: List[dict] = []
    pe = float(prev_equity or 0.0)
    for code in codes:
        st = start.get(code) or {}
        en = end.get(code) or {}
        prev_sh = float(st.get("shares") or 0.0)
        now_sh = float(en.get("shares") or 0.0)
        prev_c = _bar_px(date_maps, code, prev_day, "close", "open")
        opn = _fpx((open_px or {}).get(code)) or _bar_px(
            date_maps, code, day, "open", "close"
        )
        close = _bar_px(date_maps, code, day, "close", "open")
        if close is None and opn is None:
            continue
        if opn is None:
            opn = close
        if prev_c is None:
            prev_c = opn
        if close is None:
            close = opn
        if opn is None or prev_c is None or close is None:
            continue
        opn_f = float(opn)
        prev_c_f = float(prev_c)
        close_f = float(close)
        overnight_pnl = prev_sh * (opn_f - prev_c_f)
        intraday_pnl = now_sh * (close_f - opn_f)
        pnl = overnight_pnl + intraday_pnl
        contrib = (pnl / pe * 100.0) if pe > 0 else 0.0
        capital = 0.0
        stock_ret = None
        if prev_sh > 0 and prev_c_f > 0:
            capital = prev_sh * prev_c_f
            added = now_sh - prev_sh
            if added > 0 and opn_f > 0:
                capital += added * opn_f
            if capital > 0:
                stock_ret = pnl / capital * 100.0
        elif now_sh > 0 and opn_f > 0:
            capital = now_sh * opn_f
            stock_ret = (close_f / opn_f - 1.0) * 100.0
        name = _leg_stock_name(code, st, en, name_by_code)
        rows.append(
            {
                "stock_code": code,
                "stock_name": name,
                "shares": round(now_sh, 0),
                "shares_prev": round(prev_sh, 0),
                "pnl": round(pnl, 2),
                "overnight_pnl": round(overnight_pnl, 2),
                "intraday_pnl": round(intraday_pnl, 2),
                "capital": round(capital, 2) if capital > 0 else 0.0,
                "ret_pct": None if stock_ret is None else round(stock_ret, 3),
                "contrib_pct": round(contrib, 4),
                "prev_close": round(prev_c_f, 4),
                "open": round(opn_f, 4),
                "close": round(close_f, 4),
            }
        )
    rows.sort(key=lambda r: abs(float(r.get("contrib_pct") or 0.0)), reverse=True)
    try:
        limit = DAY_LEG_TOP if top is None else int(top)
    except (TypeError, ValueError):
        limit = DAY_LEG_TOP
    if limit <= 0:
        return rows, 0
    n_more = max(0, len(rows) - limit)
    return rows[:limit], n_more


def _stock_contrib_acc(code: str, name: str) -> Dict[str, Any]:
    return {
        "stock_code": code,
        "stock_name": name,
        "pnl": 0.0,
        "overnight_pnl": 0.0,
        "intraday_pnl": 0.0,
        "contrib_sum_pct": 0.0,
        "hold_days": 0,
        "shares_end": 0.0,
        "shares_max": 0.0,
        "capital_sum": 0.0,
        "first_date": None,
        "last_date": None,
        "start_price": None,
        "end_price": None,
        "buy_count": 0,
        "sell_count": 0,
        "buy_shares": 0.0,
        "sell_shares": 0.0,
    }


def accumulate_day_contrib(
    acc: Dict[str, dict],
    rows: Sequence[dict],
    *,
    day: str,
) -> None:
    """把当日全日个股腿累进分票贡献槽（调用方须传未截断 rows）。"""
    dt = str(day or "")[:10]
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        code = str(r.get("stock_code") or "").strip()
        if not code:
            continue
        slot = acc.get(code)
        if slot is None:
            slot = _stock_contrib_acc(code, str(r.get("stock_name") or code))
            acc[code] = slot
        else:
            nm = str(r.get("stock_name") or "").strip()
            if nm and nm != code:
                slot["stock_name"] = nm
        slot["pnl"] += float(r.get("pnl") or 0.0)
        slot["overnight_pnl"] += float(r.get("overnight_pnl") or 0.0)
        slot["intraday_pnl"] += float(r.get("intraday_pnl") or 0.0)
        slot["contrib_sum_pct"] += float(r.get("contrib_pct") or 0.0)
        prev_sh = float(r.get("shares_prev") or 0.0)
        now_sh = float(r.get("shares") or 0.0)
        if prev_sh > 0 or now_sh > 0:
            slot["hold_days"] += 1
            if not slot["first_date"]:
                slot["first_date"] = dt
                if prev_sh > 0:
                    slot["start_price"] = r.get("prev_close") or r.get("open")
                else:
                    slot["start_price"] = r.get("open")
            slot["last_date"] = dt
            if now_sh > 0:
                slot["end_price"] = r.get("close")
            else:
                slot["end_price"] = r.get("open")
        slot["shares_end"] = now_sh
        slot["shares_max"] = max(float(slot["shares_max"] or 0), prev_sh, now_sh)
        cap = float(r.get("capital") or 0.0)
        if cap > 0:
            slot["capital_sum"] += cap


def finalize_stock_contrib(
    acc: Dict[str, dict],
    *,
    initial_cash: float,
    trades: Sequence[dict] = (),
    name_by_code: Optional[Dict[str, str]] = None,
) -> List[dict]:
    """窗口级分票贡献：盈亏=持仓盯市合计；贡献%=盈亏/回测本金。"""
    cash = float(initial_cash or 0.0)
    for t in trades or []:
        if not isinstance(t, dict):
            continue
        code = str(t.get("stock_code") or "").strip()
        if not code or code not in acc:
            continue
        side = str(t.get("side") or "").strip().lower()
        try:
            sh = float(t.get("shares") or 0.0)
        except (TypeError, ValueError):
            sh = 0.0
        if side == "buy":
            acc[code]["buy_count"] += 1
            acc[code]["buy_shares"] += sh
        elif side == "sell":
            acc[code]["sell_count"] += 1
            acc[code]["sell_shares"] += sh
    out: List[dict] = []
    for slot in acc.values():
        pnl = round(float(slot["pnl"]), 2)
        hold_days = int(slot["hold_days"] or 0)
        avg_cap = (
            float(slot["capital_sum"]) / hold_days
            if hold_days and float(slot["capital_sum"] or 0) > 0
            else 0.0
        )
        ret = round(pnl / avg_cap * 100.0, 4) if avg_cap > 0 else None
        contrib = round(pnl / cash * 100.0, 4) if cash > 0 else 0.0
        row: Dict[str, Any] = {
            "stock_code": slot["stock_code"],
            "stock_name": slot["stock_name"],
            "pnl": pnl,
            "overnight_pnl": round(float(slot["overnight_pnl"]), 2),
            "intraday_pnl": round(float(slot["intraday_pnl"]), 2),
            "contrib_pct": contrib,
            "contrib_sum_pct": round(float(slot["contrib_sum_pct"]), 4),
            "return_pct": ret,
            "hold_days": hold_days,
            "buy_count": int(slot["buy_count"] or 0),
            "sell_count": int(slot["sell_count"] or 0),
            "shares_end": int(round(float(slot["shares_end"] or 0))),
            "shares_max": int(round(float(slot["shares_max"] or 0))),
            "first_date": slot["first_date"],
            "last_date": slot["last_date"],
        }
        try:
            sp = float(slot["start_price"]) if slot.get("start_price") is not None else None
        except (TypeError, ValueError):
            sp = None
        try:
            ep = float(slot["end_price"]) if slot.get("end_price") is not None else None
        except (TypeError, ValueError):
            ep = None
        if sp is not None and sp > 0:
            row["start_price"] = round(sp, 4)
        if ep is not None and ep > 0:
            row["end_price"] = round(ep, 4)
        out.append(row)
    _stamp_stock_names(out, name_by_code)
    out.sort(key=lambda r: -abs(float(r.get("pnl") or 0)))
    return out


def replay_session_day(
    *,
    now: Optional[datetime] = None,
    fill_clock: str = REPLAY_FILL_CLOCK,
) -> Optional[str]:
    """交易日且已过调仓钟才覆盖当天；钟前 / 非交易日返回 None。"""
    from core.market.calendar import is_trading_day, resolve_session_date

    dt = now or datetime.now()
    session = str(resolve_session_date(now=dt) or "")[:10]
    if not session or not is_trading_day(session):
        return None
    if dt.strftime("%Y-%m-%d") == session:
        clock = clamp_replay_fill_clock(fill_clock)
        hh, mm = int(clock[:2]), int(clock[3:5])
        open_at = dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if dt < open_at:
            return None
    return session


def _lookup_quote(quotes: Dict[str, Any], code: str) -> dict:
    if not quotes:
        return {}
    if code in quotes and isinstance(quotes.get(code), dict):
        return quotes[code]
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    for k, v in quotes.items():
        if not isinstance(v, dict):
            continue
        kd = "".join(ch for ch in str(k) if ch.isdigit())
        if digits and kd == digits:
            return v
    return {}


def _quote_open_px(q: Any) -> Optional[float]:
    """只要今开。现价/昨收不能冒充开盘，否则盘中回测会把 last 喂进 09:30 ŷ。"""
    if not isinstance(q, dict):
        return None
    return _fpx(q.get("open")) or _fpx(q.get("open_raw"))


def overlay_session_day_bars(
    stock_bars: Dict[str, List[dict]],
    *,
    now: Optional[datetime] = None,
    quotes_by_code: Optional[Dict[str, dict]] = None,
    fetch_quotes: bool = True,
    fill_clock: str = REPLAY_FILL_CLOCK,
) -> Tuple[Dict[str, List[dict]], Optional[str], int]:
    """仓里还没有当日日 K 时，用今开补一根，让调仓钟能跑到当天。

    只要报价 open / open_raw（可带「元」）；现价 last 不能冒充开盘。
    特征仍只用昨收。成交优先 5m；无分钟时 09:30 用今开。真实收盘 / 隔夜 label 不写。
    仅当覆盖率日历末日恰好是上一交易日才补，避免测试夹具或过期仓跳到今天。
    """
    dates = _coverage_dates(stock_bars)
    session = replay_session_day(now=now, fill_clock=fill_clock)
    if not session or not dates:
        return stock_bars, None, 0
    last = dates[-1]
    if last >= session:
        return stock_bars, None, 0
    from core.market.calendar import prev_trading_day

    if last != prev_trading_day(session):
        return stock_bars, None, 0

    missing = [
        str(code)
        for code, bars in stock_bars.items()
        if session not in _bars_by_date(bars)
    ]
    if not missing:
        return stock_bars, session, 0

    quotes = dict(quotes_by_code or {})
    if fetch_quotes:
        still = [c for c in missing if not _quote_open_px(_lookup_quote(quotes, c))]
        if still:
            try:
                from core.data.facade import batch_get_quotes

                got = batch_get_quotes(still) or {}
                quotes.update(got)
            except Exception:  # noqa: BLE001
                logger.debug("session overlay quotes failed", exc_info=True)

    n = 0
    out = {str(c): list(bars or []) for c, bars in stock_bars.items()}
    for code in missing:
        open_px = _quote_open_px(_lookup_quote(quotes, code))
        if open_px is None:
            continue
        out[code].append(
            {
                "date": session,
                "open": open_px,
                "high": open_px,
                "low": open_px,
                "close": open_px,
                "volume": 0,
                "session_overlay": True,
            }
        )
        n += 1
    if n < 1:
        return stock_bars, None, 0
    return out, session, n


def _window_for_code(
    code: str,
    dates: List[str],
    date_maps: Dict[str, Dict[str, dict]],
    end_idx: int,
    max_window: int,
) -> List[dict]:
    start = max(0, end_idx - max_window + 1)
    dm = date_maps.get(code) or {}
    return [dm[d] for d in dates[start : end_idx + 1] if d in dm]


def mock_quote_from_bar(
    bar: Optional[dict],
    *,
    prev_bar: Optional[dict] = None,
    px_field: str = "open",
) -> dict:
    """日线 → 调仓/盯市用行情 dict（含 prev_close / change_raw，供涨跌停）。"""
    if not isinstance(bar, dict):
        return {}
    try:
        px = float(bar.get(px_field) or bar.get("close") or 0)
    except (TypeError, ValueError):
        px = 0.0
    if px <= 0:
        try:
            px = float(bar.get("close") or 0)
        except (TypeError, ValueError):
            px = 0.0
    prev = None
    if isinstance(prev_bar, dict):
        try:
            prev = float(prev_bar.get("close") or 0) or None
        except (TypeError, ValueError):
            prev = None
    if prev is None:
        try:
            prev = float(bar.get("pre_close") or bar.get("prev_close") or 0) or None
        except (TypeError, ValueError):
            prev = None
    change = 0.0
    if prev and prev > 0 and px > 0:
        change = (px / prev - 1.0) * 100.0
    open_px = None
    try:
        open_px = float(bar.get("open") or 0) or None
    except (TypeError, ValueError):
        open_px = None
    return {
        "success": True,
        "price_raw": px,
        "price": px,
        "open_raw": open_px if open_px is not None else px,
        "open": open_px if open_px is not None else px,
        "prev_close": prev,
        "pre_close": prev,
        "yesterday_close": prev,
        "change_raw": round(change, 4),
        "change_pct": round(change, 4),
    }


def _make_batch_query(
    date_maps: Dict[str, Dict[str, dict]],
    dates: Sequence[str],
    day: str,
    *,
    px_field: str,
) -> Callable[[List[str]], Dict[str, dict]]:
    date_i = {d: i for i, d in enumerate(dates)}
    i = date_i.get(str(day))

    def _batch(codes: List[str]) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for code in codes or []:
            c = str(code or "").strip()
            if not c:
                continue
            dm = date_maps.get(c) or {}
            bar = dm.get(str(day))
            prev_bar = None
            if i is not None and i > 0:
                prev_bar = dm.get(dates[i - 1])
            q = mock_quote_from_bar(bar, prev_bar=prev_bar, px_field=px_field)
            if q:
                q["stock_code"] = c
            out[c] = q
        return out

    return _batch


def _make_fill_batch_query(
    prices: Dict[str, float],
    date_maps: Dict[str, Dict[str, dict]],
    dates: Sequence[str],
    day: str,
) -> Callable[[List[str]], Dict[str, dict]]:
    """盯市/涨跌停用调仓钟成交价，昨收仍取日 K。"""
    base = _make_batch_query(date_maps, dates, day, px_field="open")

    def _batch(codes: List[str]) -> Dict[str, dict]:
        out = base(codes)
        for c, q in list(out.items()):
            px = _fpx((prices or {}).get(c))
            if px is None or not q:
                continue
            q["price_raw"] = px
            q["price"] = px
        return out

    return _batch


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
        "y_fuse": sk.get("ranking") if sk.get("ranking") is not None else sk.get("y_fuse"),
        "ranking": sk.get("ranking") if sk.get("ranking") is not None else sk.get("y_fuse"),
        "y_oo": sk.get("y_oo"),
        "y_co": _sim_y_co(sk),
        "y_τc": _sim_y_τc(sk),
        "residual": sk.get("residual"),
        "r_hat": sk.get("r_hat"),
        "remaining_oc": sk.get("remaining_oc"),
        "ranking_score": sk.get("ranking_score"),
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
    )
    try:
        from core.paper.rebalance.watching_matrix import _open_date_of

        open_date = _open_date_of(holding, day) or ""
    except Exception:  # noqa: BLE001
        logger.debug("hold open_date failed %s", code, exc_info=True)
        open_date = str(
            (holding or {}).get("bought_date") or (holding or {}).get("bought_at") or ""
        )[:10]
    yf = (plan_h or {}).get("ranking")
    if yf is None:
        yf = (plan_h or {}).get("y_fuse")
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
        "y_fuse": yf,
        "ranking": (plan_h or {}).get("ranking") if (plan_h or {}).get("ranking") is not None else yf,
        "y_oo": (plan_h or {}).get("y_oo"),
        "y_co": _sim_y_co(plan_h),
        "y_τc": _sim_y_τc(plan_h),
        "residual": (plan_h or {}).get("residual"),
        "r_hat": (plan_h or {}).get("r_hat"),
        "remaining_oc": (plan_h or {}).get("remaining_oc"),
        "ranking_score": (plan_h or {}).get("ranking_score"),
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
            from core.paper.rebalance.rank_lots import ranking_pct_of
            sc = scores_from_rebalance_item(it)
            rp = ranking_pct_of(
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
    """账本买卖腿 → 前端成交表行（保留 side / 股数 / y_fuse，不是研究独立腿）。"""
    out: List[dict] = []
    for t in trades or []:
        if not isinstance(t, dict):
            continue
        side = str(t.get("side") or "").strip().lower()
        if side not in ("buy", "sell"):
            continue
        day = str(t.get("as_of") or t.get("ts") or "")[:10]
        yf = t.get("ranking")
        if yf is None:
            yf = t.get("y_fuse")
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
            "y_fuse": yf,
            "ranking": t.get("ranking") if t.get("ranking") is not None else yf,
            "y_oo": t.get("y_oo"),
            "y_co": _sim_y_co(t),
            "y_τc": _sim_y_τc(t),
            "residual": t.get("residual"),
            "r_hat": t.get("r_hat"),
            "remaining_oc": t.get("remaining_oc"),
            "ranking_score": t.get("ranking_score"),
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


# |y_fuse| < 0.05% → 无方向，与 score_ledger 同口径。
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
    """成交腿 sign(ranking)=sign(realized_ranking)。

    真实 ranking = (open[T+1]−price(τ))/open[T]，最后一个交易日无次日开、不进分母。
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
        yf = _fnum(r.get("ranking"))
        if yf is None:
            yf = _fnum(r.get("y_fuse"))
        if yf is None:
            rs = _fnum(r.get("ranking_score"))
            if rs is not None:
                yf = float(rs) * 100.0
        real = _fnum(r.get("realized_ranking"))
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


def _load_replay_global_model() -> Any:
    try:
        from core.signal.return_score_store import load_return_model

        # 历史回测优先研究套，与 τ / co 一致
        model, meta = load_return_model(prefer_active=True, prefer_research=True)
        if meta.get("ok") and model is not None:
            return model
    except Exception:  # noqa: BLE001
        logger.debug("load replay global return model failed", exc_info=True)
    return None


def _required_factor_keys_from_return_models(
    global_model: Any = None,
    *,
    cluster_models: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """与 score_stock / T0 同源：ŷ β 键并集，供 FS0 补算（含 raw_alpha158_*）。

    cluster_models 已废弃，忽略（历史回测只用全局模型）。
    """
    del cluster_models
    keys: List[str] = []
    seen = set()
    models: List[Any] = []
    if global_model is not None:
        models.append(global_model)
    for m in models:
        coefs = getattr(m, "coefficients", None)
        if not isinstance(coefs, dict):
            continue
        for k in coefs.keys():
            kk = str(k).strip()
            if kk and kk not in seen:
                seen.add(kk)
                keys.append(kk)
    return keys


def _stamp_tree_formula(
    item: dict,
    features: Optional[dict],
    model_doc: Optional[dict],
    *,
    y_hat: Optional[float],
    keys: tuple,
) -> None:
    from core.research.return_tree import stamp_tree_formula

    stamp_tree_formula(item, features, model_doc, y_hat=y_hat, keys=keys)


def _attach_open_yhat_heads(
    entries: List[dict],
    *,
    quotes: Dict[str, dict],
    windows: Dict[str, List[dict]],
    cluster_models: Optional[Dict[str, Any]] = None,
    global_model: Any = None,
    co_model_doc: Any = None,
    tau_model_doc: Any = None,
    cfg: Optional[dict] = None,
    tau_pool_day: Optional[dict] = None,
) -> List[dict]:
    """heuristic 窗口分 → 全局 ŷ_oo；09:30 PIT 挂 ŷ_τc；开盘特征打 ŷ_co。

    对照头 ŷ_hl 走开盘 Z（与 live ``score_stock`` 同路径），不进 ranking。
    调仓回测不挂 ŷ_τ30/60/90 / ŷ_τw。nowcast 已退役，不再写 y_nowcast。
    cluster_models 已废弃，忽略。
    """
    del cluster_models
    from core.signal.return_score import apply_predicted_scores

    items = list(entries)
    if global_model is not None:
        items = apply_predicted_scores(items, global_model, write_rank_score=False)

    rem_doc = tau_model_doc
    if rem_doc is None:
        try:
            from core.research.tc_ridge import load_tau_model

            rem_doc = load_tau_model()
        except Exception:  # noqa: BLE001
            rem_doc = None
            logger.debug("load_tau_model failed in replay yhat heads", exc_info=True)

    try:
        from core.signal.dual_score import (
            align_trade_score_fields,
            attach_dual_score_pit,
        )
    except Exception:  # noqa: BLE001
        align_trade_score_fields = None  # type: ignore[assignment]
        attach_dual_score_pit = None  # type: ignore[assignment]
        logger.debug("dual_score import failed", exc_info=True)

    try:
        from core.research.co_panel import build_co_features_from_quote_bars
        from core.research.co_ridge import predict_co_from_features
        from core.signal.dual_score.co import apply_co_score_fields
    except Exception:  # noqa: BLE001
        build_co_features_from_quote_bars = None  # type: ignore[assignment]
        predict_co_from_features = None  # type: ignore[assignment]
        apply_co_score_fields = None  # type: ignore[assignment]
        logger.debug("on-head imports failed", exc_info=True)

    try:
        from core.t0.score_policy import _attach_y_path_to_item
    except Exception:  # noqa: BLE001
        _attach_y_path_to_item = None  # type: ignore[assignment]
        logger.debug("path-head import failed", exc_info=True)

    oo_rank_doc = None
    apply_oo_rank_scores = None
    try:
        from core.research.holdout import current_scoring_model_role
        from core.research.oo_rank_lambdarank import (
            apply_oo_rank_scores as _apply_oo_rank,
            load_oo_rank_model,
        )

        apply_oo_rank_scores = _apply_oo_rank
        prefer_research = current_scoring_model_role() != "live"
        oo_rank_doc = load_oo_rank_model(prefer_research=prefer_research)
        if oo_rank_doc is None:
            oo_rank_doc = load_oo_rank_model(prefer_research=not prefer_research)
    except Exception:  # noqa: BLE001
        logger.debug("oo_rank import failed", exc_info=True)

    # ŷ_oo_rank：整日截面批打分后再编 1..n 名次；不改 ranking / 买序
    if apply_oo_rank_scores is not None and oo_rank_doc is not None:
        try:
            apply_oo_rank_scores(
                [it for it in items if isinstance(it, dict)],
                model_doc=oo_rank_doc,
            )
        except Exception:  # noqa: BLE001
            logger.debug("attach y_oo_rank day batch failed", exc_info=True)

    for it in items:
        if not isinstance(it, dict):
            continue
        code = str(it.get("stock_code") or "").strip()
        pred = it.get("predicted_score")
        if pred is not None:
            it.setdefault("predicted_score_oo", pred)
            it.setdefault("y_oo", pred)
        try:
            from core.research.return_tree import overlay_oo_tree_on_item

            overlay_oo_tree_on_item(
                it,
                window=windows.get(code) or [],
                quote=quotes.get(code) if code else None,
            )
        except Exception:  # noqa: BLE001
            logger.debug("overlay y_oo tree failed for %s", code, exc_info=True)
        if attach_dual_score_pit is not None and code:
            try:
                xs: Dict[str, Any] = {}
                try:
                    from core.t0.score_policy import tau_pool_day_score_kwargs

                    xs = tau_pool_day_score_kwargs(tau_pool_day, code) or {}
                except Exception:  # noqa: BLE001
                    logger.debug("replay open tau pool kwargs failed", exc_info=True)
                    xs = {}
                if xs.get("pool_gaps"):
                    it["_pool_gaps"] = xs.get("pool_gaps")
                q = quotes.get(code) or {}
                trade_day = str(
                    q.get("date") or q.get("trade_date") or it.get("date") or ""
                )[:10]
                attach_dual_score_pit(
                    it,
                    quote=q,
                    bars=windows.get(code) or [],
                    config=cfg,
                    rem_model_doc=rem_doc,
                    use_minute_tau=False,
                    fuse_intraday=True,
                    sector_gap_breadth=xs.get("sector_gap_breadth"),
                    sector_gap_median=xs.get("sector_gap_median"),
                    trade_day=trade_day if len(trade_day) >= 10 else None,
                )
            except Exception:  # noqa: BLE001
                logger.debug("attach open yhat heads failed for %s", code, exc_info=True)
        if align_trade_score_fields is not None:
            try:
                align_trade_score_fields(
                    it, write_score=False, refresh_window=False, config=cfg
                )
            except Exception:  # noqa: BLE001
                logger.debug("align_trade_score_fields failed", exc_info=True)
        it.pop("predicted_score_nowcast", None)
        it.pop("y_nowcast", None)
        it.pop("y_nc", None)
        if code:
            q = quotes.get(code) or {}
            d = str(it.get("date") or it.get("as_of") or it.get("trade_date") or "")[:10]
            if len(d) < 10:
                d = str(q.get("date") or q.get("trade_date") or "")[:10]
            if len(d) >= 10:
                it.setdefault("date", d)
                it.setdefault("as_of", d)
                it.setdefault("trade_date", d)
        if _attach_y_path_to_item is not None and code:
            try:
                _attach_y_path_to_item(
                    it,
                    hist_bars=windows.get(code) or [],
                    allow_open_z=True,
                    include_tau_horizons=False,
                )
            except Exception:  # noqa: BLE001
                logger.debug("attach open y_hl failed for %s", code, exc_info=True)
        if (
            apply_co_score_fields is None
            or build_co_features_from_quote_bars is None
            or predict_co_from_features is None
            or not code
        ):
            continue
        quote = quotes.get(code) or {}
        window = windows.get(code) or []
        try:
            gap = None
            if quote:
                try:
                    gap = float(quote.get("change_raw"))
                except (TypeError, ValueError):
                    gap = None
            co_feats = build_co_features_from_quote_bars(
                quote,
                window,
                gap_pct=gap,
                open_t=quote.get("open_raw") or quote.get("open"),
                prev_close=quote.get("prev_close") or quote.get("pre_close"),
                trade_date=quote.get("date") or quote.get("trade_date"),
            )
            if not co_feats:
                continue
            # 拟合面板经截面广度写入这三列；与当日 ŷ_τc 用同一池，避免缺测被当成训练均值。
            from core.signal.dual_score.co import overlay_co_cross_section

            tau_feats = (
                it.get("features_tau")
                if isinstance(it.get("features_tau"), dict)
                else None
            )
            co_feats = overlay_co_cross_section(co_feats, tau_feats)
            from core.research.co_tree import (
                load_co_tree_model,
                predict_co_tree_from_features,
            )
            from core.research.return_tree import predict_return_head

            co_yhat, co_src = predict_return_head(
                co_feats,
                load_tree=load_co_tree_model,
                predict_tree=predict_co_tree_from_features,
                predict_ridge=predict_co_from_features,
                ridge_model=co_model_doc,
            )
            if co_src:
                it["y_co_source"] = co_src
            apply_co_score_fields(
                it,
                co_yhat=co_yhat,
                feats=co_feats,
                co_model_doc=co_model_doc,
                source=co_src,
            )
            if co_src == "tree" and co_yhat is not None:
                from core.research.return_tree import stamp_tree_formula

                stamp_tree_formula(
                    it,
                    co_feats,
                    load_co_tree_model(),
                    y_hat=float(co_yhat),
                    keys=(
                        "formula_terms_co",
                        "score_formula_terms_co",
                        "formula_terms_on",
                        "score_formula_terms_on",
                    ),
                )
        except Exception:  # noqa: BLE001
            logger.debug("attach y_co failed for %s", code, exc_info=True)
    try:
        from core.signal.cross_section_rescore import rescore_fitted_cross_section

        rescore_fitted_cross_section(items)
    except Exception:  # noqa: BLE001
        logger.debug("replay cross-section rescore failed", exc_info=True)
    return items


def _score_open_day(
    *,
    stock_bars: Dict[str, List[dict]],
    dates: List[str],
    date_maps: Dict[str, Dict[str, dict]],
    day_i: int,
    max_window: int,
    horizon_days: int,
    cfg: Optional[dict],
    cluster_models: Optional[Dict[str, Any]] = None,
    global_model: Any = None,
    co_model_doc: Any = None,
    tau_model_doc: Any = None,
    tau_pool_day: Optional[dict] = None,
    required_factor_keys: Optional[List[str]] = None,
) -> List[dict]:
    """9:30 信息集：窗口截至昨收，报价用今开（不把今日收盘喂进特征）。

    cluster_models 已废弃，忽略。
    """
    del cluster_models
    from core.signal.cross_section_batch import score_window_as_item

    if day_i <= 0:
        return []
    entries: List[dict] = []
    quotes: Dict[str, dict] = {}
    windows: Dict[str, List[dict]] = {}
    prev_i = day_i - 1
    today = dates[day_i]
    req_keys = list(required_factor_keys or []) or None
    for code in stock_bars:
        window = _window_for_code(code, dates, date_maps, prev_i, max_window)
        if len(window) < 2:
            continue
        dm = date_maps.get(code) or {}
        today_bar = dm.get(today)
        prev_bar = dm.get(dates[prev_i])
        quote = mock_quote_from_bar(today_bar, prev_bar=prev_bar, px_field="open")
        if quote:
            quote["date"] = today
            quote["trade_date"] = today
        item = score_window_as_item(
            code,
            window,
            horizon_days=horizon_days,
            quote=quote,
            config=cfg,
            required_factor_keys=req_keys,
        )
        if item:
            entries.append(item)
            quotes[str(code)] = quote or {}
            windows[str(code)] = window
    if not entries:
        return []
    return _attach_open_yhat_heads(
        entries,
        quotes=quotes,
        windows=windows,
        global_model=global_model,
        co_model_doc=co_model_doc,
        tau_model_doc=tau_model_doc,
        cfg=cfg,
        tau_pool_day=tau_pool_day,
    )


# 前缀写入 ŷ_τc 与 τ 特征；不覆盖 ŷ_oo。
_OC_PREFIX_KEYS = (
    "features_tau",
    "features_tau_fill",
    "formula_terms_tau",
    "score_formula_terms_tau",
    "factor_coefficients_tau",
    "as_of_tau",
    "_score_source",
    "_score_prefix_hm",
    "_score_prefix_bars",
    "remaining_oc",
    "r_hat",
    "residual",
    "y_τc",
    "predicted_score_τc",
    "y_τc_ridge",
    "y_τc_source",
)


def _replay_hist_and_day_bar(
    code: str,
    *,
    dates: Sequence[str],
    date_maps: Dict[str, Dict[str, dict]],
    day_i: int,
    max_window: int,
) -> Tuple[List[dict], Optional[dict]]:
    """rescore 用：hist=昨收窗；day_bar=今日 K（补 prev_close）。"""
    if day_i <= 0:
        return [], None
    hist = _window_for_code(code, dates, date_maps, day_i - 1, max_window)
    bar = (date_maps.get(code) or {}).get(dates[day_i])
    if not isinstance(bar, dict):
        return hist, None
    day_bar = dict(bar)
    if not day_bar.get("date"):
        day_bar["date"] = dates[day_i]
    if day_bar.get("prev_close") is None and hist:
        prev_c = (hist[-1] or {}).get("close")
        if prev_c is not None:
            day_bar["prev_close"] = prev_c
    return hist, day_bar


def _apply_prefix_oc(item: dict, live: dict, cfg: Optional[dict]) -> bool:
    """把前缀因果 ŷ_τc 写进开盘行，重算 ranking；ŷ_oo 保持开盘头。

    对照头 y_hl 随前缀结果覆盖（缺分则保留开盘 Z）。调仓回测不挂 ŷ_τw / ŷ_τ30/60/90。
    接纳 ``prefix_causal``，以及已注入前缀小包并重拆 ŷ_τc 的 ``prefix_open_fallback``
    （否则 fallback 上算好的盘中 ŷ_τc 会被丢掉，09:50 仍用开盘 Z）。
    """
    from core.signal.yhat_windows import pick_y_oo, stamp_window_scores

    src = str(live.get("_score_source") or "")
    feats = live.get("features_tau") if isinstance(live.get("features_tau"), dict) else {}
    if src == "prefix_causal":
        pass
    elif (
        src == "prefix_open_fallback"
        and isinstance(feats, dict)
        and feats.get("ret_open_to_tau") is not None
        and (live.get("y_τc") is not None or live.get("predicted_score_τc") is not None)
    ):
        pass
    else:
        return False
    y_oo = pick_y_oo(item)
    y_oo_field = item.get("predicted_score_eod")
    if y_oo_field is None:
        y_oo_field = item.get("predicted_score")
    for k in _OC_PREFIX_KEYS:
        if k in live:
            item[k] = live.get(k)
    _merge_aux_yhat(item, live)
    if y_oo is not None:
        item["y_oo"] = y_oo
        ps = y_oo_field if y_oo_field is not None else y_oo
        item["predicted_score_oo"] = ps
        item["predicted_score"] = ps
    if item.get("y_τc") is None and item.get("predicted_score_τc") is not None:
        from core.signal.yhat_windows import write_y_τc

        write_y_τc(item, item.get("predicted_score_τc"))
    win = stamp_window_scores(item, cfg)
    for k, v in win.items():
        if v is not None:
            item[k] = v
    if item.get("ranking") is not None:
        item["y_fuse"] = item["ranking"]
    if str(item.get("y_τc_source") or "") == "tree" and "y_τc_ridge" not in live:
        item.pop("y_τc_ridge", None)
    return item.get("y_τc") is not None


def rescore_replay_y_τc_at_clock(
    scored: Sequence[dict],
    *,
    clock: str,
    dates: Sequence[str],
    date_maps: Dict[str, Dict[str, dict]],
    minute_maps: Dict[str, Dict[str, List[dict]]],
    max_window: int,
    day_i: int,
    cfg: Optional[dict] = None,
    tau_pool_day: Optional[dict] = None,
) -> Tuple[List[dict], int]:
    """成交钟>09:30：用截至该钟的 5m 前缀重算 ŷ_τc（与做 T rescore 同路径）。

    09:30 或无前缀则保持开盘 Z。返回 (行, 成功重算数)。
    """
    items = [dict(it) if isinstance(it, dict) else it for it in (scored or [])]
    head = clamp_replay_fill_clock(clock)
    if head == "09:30" or day_i <= 0 or day_i >= len(dates):
        return items, 0
    from core.t0.score_policy import _minute_bars_until_hm, rescore_scores_at_fixed_prefix

    day = dates[day_i]
    n_ok = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        code = str(it.get("stock_code") or "").strip()
        if not code:
            continue
        mins = (minute_maps.get(code) or {}).get(day) or []
        prefix = _minute_bars_until_hm(mins, tau_hm=head)
        if len(prefix) < 1:
            continue
        hist, day_bar = _replay_hist_and_day_bar(
            code,
            dates=dates,
            date_maps=date_maps,
            day_i=day_i,
            max_window=max_window,
        )
        if not isinstance(day_bar, dict) or len(hist) < 2:
            continue
        try:
            live = rescore_scores_at_fixed_prefix(
                stock_code=code,
                minute_prefix=prefix,
                day_bar=day_bar,
                hist_bars=hist,
                tau_pool_day=tau_pool_day,
                fuse_intraday=True,
                open_snap=dict(it),
                include_tau_horizons=False,
            )
        except Exception:  # noqa: BLE001
            logger.debug("replay prefix y_τc rescore failed %s %s", code, day, exc_info=True)
            continue
        if _apply_prefix_oc(it, live if isinstance(live, dict) else {}, cfg):
            n_ok += 1
    return items, n_ok


def _items_from_injected_ranking(rows: Sequence[dict]) -> List[dict]:
    """测试/日报注入行：ŷ 当作 ranking。"""
    out: List[dict] = []
    for it in rows or []:
        if not isinstance(it, dict) or not it.get("stock_code"):
            continue
        row = dict(it)
        yf = row.get("y_fuse")
        if yf is None:
            yf = row.get("y_fusion")
        if yf is None:
            yf = row.get("predicted_score")
        if yf is None:
            yf = row.get("predicted_score_eod")
        if yf is None:
            yf = row.get("score")
        if yf is not None:
            row["y_fuse"] = yf
            row.setdefault("ranking", yf)
        out.append(row)
    return out


def backtest_paper_replay(
    stock_bars: Dict[str, List[dict]],
    *,
    top_k: Optional[int] = None,
    min_history: int = 12,
    max_window: int = 30,
    lookback: Optional[int] = None,
    initial_cash: float = REPLAY_INITIAL_CASH,
    cost_model: str = "simple_cn",
    rankings_by_date: Optional[Dict[str, List[dict]]] = None,
    yhat_horizon_days: int = 1,
    progress_cb: Optional[Callable[..., Any]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
    cash_floor: Optional[float] = None,
    rank_enter: Optional[float] = None,
    rank_strong: Optional[float] = None,
    fusion_w_co: Optional[float] = None,
    fusion_w_oo: Optional[float] = None,
    fusion_w_oc: Optional[float] = None,
    include_session_day: bool = True,
    session_quotes: Optional[Dict[str, dict]] = None,
    session_now: Optional[datetime] = None,
    fill_clock: str = REPLAY_FILL_CLOCK,
    minute_bars_by_code: Optional[Dict[str, Dict[str, List[dict]]]] = None,
    lot_base: Optional[float] = None,
    lot_strong: Optional[float] = None,
    lot_base_amount: Optional[float] = None,
    lot_strong_amount: Optional[float] = None,
    rank_enter_alt: Optional[float] = None,
    y_enter_enabled: Optional[bool] = None,
    y_enter_alt_enabled: Optional[bool] = None,
    y_oo_gt0: Optional[bool] = None,
    y_τc_gt0: Optional[bool] = None,
    oo_rank_max: Optional[int] = None,
    price_space_cfg: Optional[dict] = None,
    score_model_role: Optional[str] = None,
    score_backend: Optional[str] = None,
) -> Dict[str, Any]:
    """策略调仓历史回测：ŷ_oo 09:30 开盘；ŷ_τc 随成交钟前缀重算（09:30=开盘 Z），按调仓钟 5m 价成交。

    ``score_backend=tree`` 时 ŷ_oo / ŷ_τc / ŷ_co 改读已落盘树；缺模型的头回退 Ridge。
    ŷ_oo_rank 有模型时按当日截面编成 1..n 名次透传到成交行（不进买序）。
    ``oo_rank_max`` 开则入场另须 y_oo_rank < 上限（缺分不拦）。
    """
    from core.research.holdout import (
        current_scoring_model_role,
        normalize_backtest_model_role,
        scoring_model_role_context,
    )
    from core.research.return_tree import (
        current_rebalance_score_backend,
        normalize_rebalance_score_backend,
        rebalance_score_backend_context,
        return_tree_model_state,
    )

    requested = normalize_backtest_model_role(score_model_role)
    requested_backend = normalize_rebalance_score_backend(score_backend)
    if (
        current_scoring_model_role() != requested
        or current_rebalance_score_backend() != requested_backend
    ):
        import inspect

        allowed = inspect.signature(backtest_paper_replay).parameters
        params = {
            k: v for k, v in locals().items() if k in allowed and k != "stock_bars"
        }
        params["score_model_role"] = requested
        params["score_backend"] = requested_backend
        with scoring_model_role_context(requested):
            with rebalance_score_backend_context(requested_backend):
                return backtest_paper_replay(stock_bars, **params)
    from core.backtest.engine import _trade_metrics
    from core.paper.rebalance.rank_lots import (
        DEFAULT_FUSION_W_CO,
        clamp_fusion_weight,
        clamp_fusion_w_co,
        coerce_rank_threshold,
        get_rank_lot_cfg,
        oo_rank_max_from_cfg,
        plan_rank_lot_day,
    )
    from core.paper.rebalance.watching_matrix import _apply_matrix_trades
    from core.paper.replay_ctx import paper_replay_context

    if not stock_bars or len(stock_bars) < 1:
        return {"success": False, "error": "stock_bars 为空", "params": {"engine": ENGINE_ID}}

    clock = clamp_replay_fill_clock(fill_clock)
    minute_maps = minute_bars_by_code if isinstance(minute_bars_by_code, dict) else {}
    ps_cfg = _load_replay_price_space_cfg(price_space_cfg)

    session_day = None
    n_overlay = 0
    if include_session_day:
        stock_bars, session_day, n_overlay = overlay_session_day_bars(
            stock_bars,
            now=session_now,
            quotes_by_code=session_quotes,
            fetch_quotes=session_quotes is None,
            fill_clock=clock,
        )

    date_maps = {str(c): _bars_by_date(bars) for c, bars in stock_bars.items()}

    # 先解析 ŷ 模型 → required_factor_keys / Alpha158 抬窗，再铺日历垫特征窗
    global_model: Any = None
    co_model_doc = None
    tau_model_doc = None
    required_factor_keys: List[str] = []
    if rankings_by_date is None:
        global_model = _load_replay_global_model()
        required_factor_keys = _required_factor_keys_from_return_models(global_model)
        from core.research.return_tree import (
            loaded_return_tree_feature_names,
            tree_scores_requested,
        )

        if tree_scores_requested():
            try:
                seen_keys = set(required_factor_keys)
                for key in loaded_return_tree_feature_names():
                    if key not in seen_keys:
                        seen_keys.add(key)
                        required_factor_keys.append(key)
            except Exception:  # noqa: BLE001
                logger.debug("replay tree feature keys failed", exc_info=True)
        try:
            from core.signal.factors.alpha158 import bump_window_for_alpha158

            min_history, max_window = bump_window_for_alpha158(
                required_factor_keys,
                min_history=int(min_history),
                max_window=int(max_window),
            )
        except Exception:  # noqa: BLE001
            logger.debug("replay alpha158 window bump failed", exc_info=True)
        try:
            from core.research.co_ridge import load_co_model

            co_model_doc = load_co_model()
        except Exception:  # noqa: BLE001
            logger.debug("load_co_model failed in paper_replay", exc_info=True)
        try:
            from core.research.tc_ridge import load_tau_model

            tau_model_doc = load_tau_model()
        except Exception:  # noqa: BLE001
            logger.debug("load_tau_model failed in paper_replay", exc_info=True)

    dates, trade_start = _replay_calendar(
        stock_bars,
        lookback=lookback,
        min_history=min_history,
        max_window=max_window,
    )
    if session_day and session_day not in dates:
        dates = sorted(dates + [session_day])
    n = len(dates)
    need = max(2, int(min_history) + 1)
    if n < need:
        return {
            "success": False,
            "error": f"可交易日不足 {n}<{need}",
            "params": {"engine": ENGINE_ID, "common_dates": n, "lookback": lookback},
        }

    from core.watching.store import WATCHING_MAX_SIZE

    universe_n = max(1, len(stock_bars))
    cap = max(universe_n, int(WATCHING_MAX_SIZE))
    if top_k is None:
        top_k = universe_n
    else:
        top_k = max(1, min(int(top_k or 1), cap))
    floor = float(REPLAY_CASH_FLOOR if cash_floor is None else cash_floor)
    initial_cash = clamp_replay_initial_cash(initial_cash)
    alpha = clamp_fusion_w_co(
        DEFAULT_FUSION_W_CO if fusion_w_co is None else fusion_w_co
    )
    raw_oo = fusion_w_oo
    raw_oc = fusion_w_oc
    w_oo = clamp_fusion_weight(
        REPLAY_FUSION_W_TRADE if raw_oo is None else raw_oo,
        REPLAY_FUSION_W_TRADE,
    )
    w_τc = clamp_fusion_weight(
        REPLAY_FUSION_W_NOWCAST if raw_oc is None else raw_oc,
        REPLAY_FUSION_W_NOWCAST,
    )
    enter = coerce_rank_threshold(
        REPLAY_RANK_ENTER if rank_enter is None else rank_enter,
        REPLAY_RANK_ENTER,
    )
    strong = coerce_rank_threshold(
        REPLAY_RANK_STRONG if rank_strong is None else rank_strong,
        REPLAY_RANK_STRONG,
    )
    enter = max(0.0, min(1.0, float(enter)))
    strong = max(0.0, min(1.0, float(strong)))
    if strong < enter:
        strong = enter
    lot_base_n, lot_strong_n = clamp_replay_amount_pair(
        lot_base_amount if lot_base_amount is not None else lot_base,
        lot_strong_amount if lot_strong_amount is not None else lot_strong,
    )
    gt0_τc = y_τc_gt0
    paper = _default_paper(
        initial_cash=initial_cash,
        top_k=top_k,
        cost_model=cost_model,
    )
    # 必须写 rank_lots：get_path_matrix_cfg 优先该键。只写旧键 path_matrix 时，
    # 其它 live 缺省（如持仓市值帽）会漏进合成 paper。
    replay_lots = {
        "enabled": True,
        "mode": "rank_lots",
        "rank_enter": enter,
        "rank_strong": strong,
        "rank_enter_alt": coerce_rank_threshold(
            enter if rank_enter_alt is None else rank_enter_alt, enter
        ),
        "cash_floor": floor,
        "holdings_mv_cap": 0.0,
        "fusion_w_co": alpha,
        "fusion_w_oo": w_oo,
        "fusion_w_oc": w_τc,
        "y_enter_enabled": True if y_enter_enabled is None else bool(y_enter_enabled),
        "y_enter_alt_enabled": True if y_enter_alt_enabled is None else bool(y_enter_alt_enabled),
        "y_oo_gt0": False if y_oo_gt0 is None else bool(y_oo_gt0),
        "y_τc_gt0": False if gt0_τc is None else bool(gt0_τc),
        "oo_rank_max": oo_rank_max_from_cfg(
            {"oo_rank_max": oo_rank_max} if oo_rank_max is not None else {}
        ),
        "lot_base_amount": lot_base_n,
        "lot_strong_amount": lot_strong_n,
    }
    paper.setdefault("rules", {})["execution"] = {
        "rebalance_timing": {
            "rank_lots": dict(replay_lots),
            "path_matrix": dict(replay_lots),
        }
    }
    rl_cfg = get_rank_lot_cfg(paper, top_k=top_k)
    rl_cfg["cash_floor"] = floor
    rl_cfg["lot_base_amount"] = lot_base_n
    rl_cfg["lot_strong_amount"] = lot_strong_n
    rl_cfg["fusion_w_co"] = alpha
    rl_cfg["fusion_w_oo"] = w_oo
    rl_cfg["fusion_w_oc"] = w_τc
    rl_cfg["rank_enter"] = enter
    rl_cfg["rank_strong"] = strong
    rl_cfg["rank_enter_alt"] = replay_lots["rank_enter_alt"]
    rl_cfg["y_enter_enabled"] = replay_lots["y_enter_enabled"]
    rl_cfg["y_enter_alt_enabled"] = replay_lots["y_enter_alt_enabled"]
    rl_cfg["y_oo_gt0"] = replay_lots["y_oo_gt0"]
    rl_cfg["y_τc_gt0"] = replay_lots["y_τc_gt0"]
    rl_cfg["oo_rank_max"] = replay_lots.get("oo_rank_max")
    rl_cfg["score_backend"] = current_rebalance_score_backend()
    rl_cfg["holdings_mv_cap"] = 0.0
    rl_cfg["t0_sell_blocks"] = {}

    cfg = None
    try:
        from core.signal.config import load_signal_config

        cfg = load_signal_config()
        cfg = dict(cfg)
        scoring = dict(cfg.get("scoring") or {})
        scoring["skip_minute_io"] = True
        cfg["scoring"] = scoring
    except Exception:  # noqa: BLE001
        logger.debug("load_signal_config failed in paper_replay", exc_info=True)

    equity_curve: List[dict] = []
    day_returns: List[float] = []
    rebalance_logs: List[dict] = []
    debug_skips: List[dict] = []
    debug_holds: List[dict] = []
    constraints: Dict[str, int] = {
        "t1_blocks": 0,
        "cash_floor_skips": 0,
        "rebalance_days": 0,
        "minute_skips": 0,
        "minute_fills": 0,
        "tau_prefix_rescored": 0,
        "price_space_skips": 0,
        "price_space_mismatch_seen": 0,
        "sell_px_fallback": 0,
    }
    tau_pool_by_date: Dict[str, Dict[str, Any]] = {}
    if rankings_by_date is None:
        try:
            from core.t0.score_policy import build_tau_pool_by_date, seed_tau_cross_section_pool

            tau_pool_by_date = build_tau_pool_by_date(stock_bars) or {}
            seed_tau_cross_section_pool(tau_pool_by_date)
        except Exception:  # noqa: BLE001
            logger.debug("replay tau pool build failed", exc_info=True)
            tau_pool_by_date = {}
    name_by_code = _watching_name_map()
    stock_acc: Dict[str, dict] = {}
    prev_equity = float(initial_cash)
    if lookback is not None:
        first_i = max(1, int(trade_start))
    else:
        first_i = max(1, int(min_history))
    if first_i >= n:
        first_i = max(1, n - 1)
    if dates:
        equity_curve.append(
            {
                "date": dates[first_i - 1],
                "equity": prev_equity,
                "equity_nav": 100.0,
                "return_pct": 0.0,
            }
        )

    for i in range(first_i, n):
        day = dates[i]
        if cancel_cb is not None:
            try:
                if cancel_cb():
                    return {
                        "success": False,
                        "error": "已取消",
                        "cancelled": True,
                    }
            except Exception:  # noqa: BLE001
                logger.debug("paper_replay cancel_cb failed", exc_info=True)
        if progress_cb is not None:
            try:
                progress_cb(f"{day} {clock}", i - first_i + 1, max(1, n - first_i))
            except Exception:  # noqa: BLE001
                logger.debug("paper_replay progress_cb failed", exc_info=True)

        if rankings_by_date is not None:
            scored = _items_from_injected_ranking(rankings_by_date.get(day) or [])
            held_codes = [
                str(h.get("stock_code") or "")
                for h in (paper.get("holdings") or [])
            ]
            have = {str(it.get("stock_code") or "") for it in scored}
            for code in held_codes:
                if code and code not in have:
                    # 当日名单没有该票：不沿用昨日 ŷ（与真实打分路径一致）。
                    scored.append({"stock_code": code})
        else:
            scored = _score_open_day(
                stock_bars=stock_bars,
                dates=dates,
                date_maps=date_maps,
                day_i=i,
                max_window=max_window,
                horizon_days=max(1, int(yhat_horizon_days or 1)),
                cfg=cfg,
                global_model=global_model,
                co_model_doc=co_model_doc,
                tau_model_doc=tau_model_doc,
                tau_pool_day=tau_pool_by_date.get(day) or {},
                required_factor_keys=required_factor_keys or None,
            )
            have = {str(it.get("stock_code") or "") for it in scored}
            for h in paper.get("holdings") or []:
                if not isinstance(h, dict):
                    continue
                code = str(h.get("stock_code") or "").strip()
                if not code or code in have:
                    continue
                # 当日未打上分不要沿用昨日 ŷ，否则明细预估值会冻住。
                scored.append({"stock_code": code})
                have.add(code)
            if clock != "09:30":
                scored, n_pref = rescore_replay_y_τc_at_clock(
                    scored,
                    clock=clock,
                    dates=dates,
                    date_maps=date_maps,
                    minute_maps=minute_maps,
                    max_window=max_window,
                    day_i=i,
                    cfg=rl_cfg,
                    tau_pool_day=tau_pool_by_date.get(day) or {},
                )
                constraints["tau_prefix_rescored"] = int(
                    constraints.get("tau_prefix_rescored") or 0
                ) + int(n_pref)
        _stamp_stock_names(scored, name_by_code)
        _stamp_stock_names(paper.get("holdings") or [], name_by_code)

        prices: Dict[str, float] = {}
        sell_prices: Dict[str, float] = {}
        sell_fallback_src: Dict[str, str] = {}
        opens: Dict[str, float] = {}
        seen_px: set = set()
        mismatch_reason: Dict[str, str] = {}
        for code in list(stock_bars.keys()) + [
            str(h.get("stock_code") or "") for h in (paper.get("holdings") or [])
        ]:
            c = str(code or "").strip()
            if not c or c in seen_px:
                continue
            seen_px.add(c)
            bar = (date_maps.get(c) or {}).get(day)
            if not bar:
                continue
            open_px = _px(bar, "open")
            if open_px:
                opens[c] = open_px
            mins = (minute_maps.get(c) or {}).get(day) or []
            day_bar = _stamp_replay_prev_close(
                bar,
                date_maps=date_maps,
                dates=dates,
                code=c,
                day_i=i,
            )
            window_mins = _replay_window_minutes(mins)
            skip_ps, seen_ps = replay_price_space_check(day_bar, window_mins, ps_cfg)
            if seen_ps:
                constraints["price_space_mismatch_seen"] += 1
            if skip_ps:
                mismatch_reason[c] = skip_ps
                constraints["price_space_skips"] += 1
                continue
            px = replay_fill_px(
                daily_bar=bar, minute_bars=window_mins, fill_clock=clock
            )
            if px is not None and px > 0:
                prices[c] = px
                if window_mins:
                    constraints["minute_fills"] += 1
            elif clock != "09:30":
                constraints["minute_skips"] += 1
            sell_px, sell_src = replay_sell_fill_px(
                daily_bar=bar, minute_bars=window_mins, fill_clock=clock
            )
            if sell_px is not None and sell_px > 0:
                sell_prices[c] = sell_px
                if sell_src and sell_src != "clock":
                    sell_fallback_src[c] = sell_src

        cash = float(paper.get("cash") or 0)
        start_snap = _holding_snap(paper.get("holdings") or [])
        fill_px_map = {**sell_prices, **prices}
        with paper_replay_context(
            as_of=day,
            batch_query=_make_fill_batch_query(fill_px_map, date_maps, dates, day),
        ):
            plan = plan_rank_lot_day(
                scored=scored,
                holdings=list(paper.get("holdings") or []),
                cash=cash,
                prices=prices,
                sell_prices=sell_prices,
                opens=opens,
                cfg=rl_cfg,
                as_of=day,
            )
            _apply_price_space_plan_skips(plan, mismatch_reason)
            for leg in plan.get("sells") or []:
                if not isinstance(leg, dict):
                    continue
                c = str(leg.get("stock_code") or "").strip()
                src = sell_fallback_src.get(c) or ""
                note = _sell_fill_fallback_note(clock, src)
                if note:
                    reason = str(leg.get("reason") or "").strip()
                    if note not in reason:
                        leg["reason"] = f"{reason} · {note}" if reason else note
                    leg["fill_fallback"] = src
                    constraints["sell_px_fallback"] += 1
            sell_trades = []
            buy_trades = []
            for leg in plan.get("sells") or []:
                px = sell_prices.get(str(leg.get("stock_code") or ""))
                if not px:
                    px = prices.get(str(leg.get("stock_code") or ""))
                if not px:
                    continue
                sell_trades.append(
                    {**leg, "price": px, "amount": round(float(leg["shares"]) * px, 2)}
                )
            for leg in plan.get("buys") or []:
                px = prices.get(str(leg.get("stock_code") or ""))
                if not px:
                    continue
                buy_trades.append(
                    {**leg, "price": px, "amount": round(float(leg["shares"]) * px, 2)}
                )
            for sk in plan.get("skips") or []:
                reason = str(sk.get("reason") or "")
                if "地板" in reason or "现金不足" in reason:
                    constraints["cash_floor_skips"] += 1
                if "T+1" in str(sk.get("reason") or ""):
                    constraints["t1_blocks"] += 1

            applied = {"sell_trades": [], "buy_trades": [], "apply_skips": []}
            if sell_trades or buy_trades:
                applied = _apply_matrix_trades(paper, sell_trades, buy_trades, as_of=day)
                constraints["rebalance_days"] += 1
            day_skip_sell: set = set()
            for sk in list(plan.get("skips") or []) + list(applied.get("apply_skips") or []):
                if not isinstance(sk, dict):
                    continue
                debug_skips.append(_skip_to_sim(sk, day))
                if _infer_skip_side(sk) == "sell":
                    c = str(sk.get("stock_code") or "").strip()
                    if c:
                        day_skip_sell.add(c)
            traded_codes = {
                str(t.get("stock_code") or "").strip()
                for t in list(applied.get("sell_trades") or [])
                + list(applied.get("buy_trades") or [])
                if str(t.get("stock_code") or "").strip()
            }
            for h in paper.get("holdings") or []:
                if not isinstance(h, dict):
                    continue
                code = str(h.get("stock_code") or "").strip()
                if not code or code in traded_codes or code in day_skip_sell:
                    continue
                debug_holds.append(
                    _hold_to_sim(
                        _hold_src_for_code(code, plan=plan, scored=scored),
                        day,
                        h,
                        date_maps=date_maps,
                        prices=prices,
                    )
                )

        equity = float(paper.get("cash") or 0)
        for h in paper.get("holdings") or []:
            if not isinstance(h, dict):
                continue
            code = str(h.get("stock_code") or "")
            bar = (date_maps.get(code) or {}).get(day) or {}
            try:
                close_px = float(bar.get("close") or bar.get("open") or 0)
            except (TypeError, ValueError):
                close_px = 0.0
            sh = float(h.get("shares") or 0)
            if close_px > 0 and sh > 0:
                h["last_price"] = close_px
                equity += sh * close_px
        ret = (equity / prev_equity - 1.0) * 100.0 if prev_equity > 0 else 0.0
        day_returns.append(ret)
        cash_now = round(float(paper.get("cash") or 0), 2)
        nav = (
            round(100.0 * equity / float(initial_cash), 4)
            if initial_cash
            else None
        )
        n_holdings = len(paper.get("holdings") or [])
        end_snap = _holding_snap(paper.get("holdings") or [])
        all_legs, _ = day_stock_legs(
            start=start_snap,
            end=end_snap,
            date_maps=date_maps,
            prev_day=dates[i - 1],
            day=day,
            open_px=prices,
            prev_equity=prev_equity,
            name_by_code=name_by_code,
            top=0,
        )
        accumulate_day_contrib(stock_acc, all_legs, day=day)
        legs = all_legs[:DAY_LEG_TOP]
        legs_more = max(0, len(all_legs) - DAY_LEG_TOP)
        equity_curve.append(
            {
                "date": day,
                "equity": round(equity, 2),
                "equity_nav": nav,
                "return_pct": round(ret, 4),
                "cash": cash_now,
                "n_holdings": n_holdings,
                "legs": legs,
                "legs_more": legs_more,
            }
        )
        rebalance_logs.append(
            {
                "signal_date": day,
                "exec_date": day,
                "ok": True,
                "n_buy": len(applied.get("buy_trades") or []),
                "n_sell": len(applied.get("sell_trades") or []),
                "n_hold": len(plan.get("holds") or []),
                "n_skip": len(plan.get("skips") or []),
                "n_apply_skip": len(applied.get("apply_skips") or []),
                "cash": cash_now,
                "equity": round(equity, 2),
                "equity_nav": nav,
                "n_holdings": n_holdings,
                "return_pct": round(ret, 4),
            }
        )
        prev_equity = equity

    metrics = _trade_metrics(day_returns, holding_days=1)
    metrics = dict(metrics)
    ledger_trades = list(paper.get("trades") or [])
    cash_by_day = {
        str(p.get("date") or "")[:10]: p.get("cash")
        for p in equity_curve
        if isinstance(p, dict)
    }
    hold_by_day = {
        str(p.get("date") or "")[:10]: p.get("n_holdings")
        for p in equity_curve
        if isinstance(p, dict)
    }
    equity_by_day = {
        str(p.get("date") or "")[:10]: p.get("equity")
        for p in equity_curve
        if isinstance(p, dict)
    }
    date_index = {d: i for i, d in enumerate(dates)}

    def _stamp_day_context(row: dict) -> dict:
        day = str(row.get("as_of") or "")[:10]
        row["cash_after"] = cash_by_day.get(day)
        row["n_holdings"] = hold_by_day.get(day)
        row["equity_after"] = equity_by_day.get(day)
        code = str(row.get("stock_code") or "")
        r_cc, r_on, r_tau_open = realized_yhat_windows(
            code,
            day,
            dates=dates,
            date_maps=date_maps,
            date_index=date_index,
        )
        row["realized_cc"] = r_cc
        row["realized_on"] = r_on
        # ŷ_oo 真实 = 次日开/今日开。用 τ=open 的 close/open 与隔夜复合，勿用盘中 price(τ)。
        if r_tau_open is not None and r_on is not None:
            row["realized_oo"] = round(
                ((1.0 + float(r_tau_open) / 100.0) * (1.0 + float(r_on) / 100.0) - 1.0)
                * 100.0,
                4,
            )
        else:
            row["realized_oo"] = None
        stamp_r_tau_on_row(
            row,
            daily_bar=(date_maps.get(code) or {}).get(day),
            minute_bars=(minute_maps.get(code) or {}).get(day) or [],
            fill_clock=clock,
        )
        # ŷ_τc 真实 = close[T]/price(τ)−1（随 fill_clock）；09:30 时 price(τ)=open。
        r_tau_clock = _fnum(row.get("r_realized"))
        row["realized_tau"] = (
            r_tau_clock if r_tau_clock is not None else r_tau_open
        )
        try:
            from core.signal.yhat_windows import realized_ranking_pct

            row["realized_ranking"] = realized_ranking_pct(
                row.get("realized_oo"),
                open_px=row.get("day_open"),
                price_tau=row.get("rebalance_px") or row.get("price_tau"),
            )
        except Exception:  # noqa: BLE001
            logger.debug("stamp realized_ranking failed", exc_info=True)
            row["realized_ranking"] = None
        row["fusion_w_oo"] = w_oo
        row["fusion_w_oc"] = w_τc
        row["fusion_w_co"] = alpha
        row["fill_clock"] = clock
        # tip：open ≡ 09:30；缺钟或仍写 open 时显式落 fill_clock（如 09:30 / 09:50）。
        asof = str(row.get("as_of_tau") or row.get("rem_tau") or "").strip()
        if not asof or asof.lower() == "open":
            row["as_of_tau"] = clock
            row["rem_tau"] = clock
        return row

    sim_trades = [_stamp_day_context(r) for r in _ledger_trades_to_sim(ledger_trades)]
    sim_trades.extend(_stamp_day_context(dict(s)) for s in debug_skips)
    sim_trades.extend(_stamp_day_context(dict(s)) for s in debug_holds)
    _stamp_stock_names(sim_trades, name_by_code)
    _stamp_stock_names(ledger_trades, name_by_code)
    _stamp_stock_names(paper.get("holdings") or [], name_by_code)
    stock_contrib = finalize_stock_contrib(
        stock_acc,
        initial_cash=initial_cash,
        trades=ledger_trades,
        name_by_code=name_by_code,
    )
    hit_pct, hit_n, hit_hits = fuse_hit_metrics(
        sim_trades,
        last_day=dates[-1] if dates else None,
    )
    metrics["trade_count"] = len(ledger_trades)
    metrics["sim_trade_count"] = len(ledger_trades)
    metrics["skip_count"] = len(debug_skips)
    metrics["hold_count"] = len(debug_holds)
    metrics["rebalance_days"] = constraints["rebalance_days"]
    metrics["day_count"] = len(day_returns)
    metrics["hit_rate_pct"] = hit_pct
    metrics["hit_n"] = hit_n
    metrics["hit_hits"] = hit_hits
    final_eq = float(equity_curve[-1]["equity"]) if equity_curve else float(initial_cash)
    total_ret = (final_eq / float(initial_cash) - 1.0) * 100.0 if initial_cash else 0.0
    metrics["total_return_pct"] = round(total_ret, 2)

    used_minutes = bool(minute_maps) and int(constraints.get("minute_fills") or 0) > 0
    exec_mode = "minute_5m" if used_minutes or clock != "09:30" else "open_930"
    fill_note = (
        f"成交={clock} 5m（09:30=首根开盘，其后该档收盘"
        + (
            "；缺分钟买入跳过、清仓回退其后～10:00 再回退 09:30/日开盘"
            if clock != "09:30"
            else "；缺分钟回退日开盘"
        )
        + (
            "；日分价错位跳过"
            if ps_cfg.get("t0_price_space_gate") is not False
            else "；日分价闸关"
        )
        + "）"
    )
    oc_note = (
        "ŷ_τc=开盘 Z"
        if clock == "09:30"
        else f"ŷ_τc=截至 {clock} 的 5m 前缀（与做 T rescore 同路径；ŷ_oo 仍开盘）"
    )
    from core.signal.yhat_windows import FORMULA_RANKING

    note = (
        f"引擎={ENGINE_ID}：每个交易日 ranking={FORMULA_RANKING}，{fill_note}；{oc_note}；"
        f"ranking 权 w_oo={w_oo:g} w_τc={w_τc:g} w_co={alpha:g}；未过入场则已持仓清仓；"
        f"门槛1∪门槛2 过入场（rank入场={rl_cfg.get('rank_enter')}）按分数买（开加上限=观察池 {top_k} 只，现金不够则停），"
        f"每笔 {int(rl_cfg.get('lot_base_amount') or REPLAY_AMOUNT_BASE)} 元；"
        f"不留现金地板；T+1；成本={cost_model}；≠ topk_research。"
        + (
            " ŷ头=Tree（缺模型的头回退 Ridge）。"
            if current_rebalance_score_backend() == "tree"
            else ""
        )
    )
    if session_day:
        note += (
            f" 含当日 {session_day} {clock}（现价开盘 ×{n_overlay}；"
            "真实收盘/隔夜 label 待日K入库）。"
        )
    return {
        "success": True,
        "strategy": ENGINE_ID,
        "params": {
            "engine": ENGINE_ID,
            "top_k": top_k,
            "min_history": min_history,
            "max_window": max_window,
            "required_factor_keys": list(required_factor_keys),
            "initial_cash": initial_cash,
            "cash_floor": floor,
            "rank_enter": rl_cfg.get("rank_enter"),
            "rank_strong": rl_cfg.get("rank_strong"),
            "rank_enter_alt": rl_cfg.get("rank_enter_alt"),
            "y_enter_enabled": rl_cfg.get("y_enter_enabled"),
            "y_enter_alt_enabled": rl_cfg.get("y_enter_alt_enabled"),
            "y_oo_gt0": rl_cfg.get("y_oo_gt0"),
            "y_τc_gt0": rl_cfg.get("y_τc_gt0"),
            "oo_rank_max": rl_cfg.get("oo_rank_max"),
            "fusion_w_co": alpha,
            "fusion_w_oo": w_oo,
            "fusion_w_oc": w_τc,
            "lot_base_amount": float(rl_cfg.get("lot_base_amount") or REPLAY_AMOUNT_BASE),
            "lot_strong_amount": float(rl_cfg.get("lot_strong_amount") or REPLAY_AMOUNT_STRONG),
            "cost_model": cost_model,
            "execution_mode": exec_mode,
            "fill_clock": clock,
            "price_space_gate": bool(ps_cfg.get("t0_price_space_gate", True)),
            "price_space_max_dev_pct": ps_cfg.get("t0_price_space_max_dev_pct"),
            "price_space_prev_dev_pct": ps_cfg.get("t0_price_space_prev_dev_pct"),
            "minute_codes": len(minute_maps),
            "horizon_days": 1,
            "stock_count": len(stock_bars),
            "lookback": int(lookback) if lookback is not None else None,
            "score_model_role": current_scoring_model_role(),
            "score_backend": current_rebalance_score_backend(),
            "tree_models": (
                return_tree_model_state()
                if current_rebalance_score_backend() == "tree"
                else None
            ),
            "common_dates": max(0, n - first_i),
            "trade_days": max(0, n - first_i),
            "end_date": dates[-1] if dates else None,
            "session_day": session_day,
            "session_overlay_n": n_overlay,
            "yhat_horizon_days": yhat_horizon_days,
            "rankings_injected": rankings_by_date is not None,
            "yhat_source": "research",
            "model_role": "research",
        },
        "metrics": metrics,
        "equity_curve": equity_curve,
        "constraints_hit": constraints,
        "rebalance_logs": rebalance_logs,
        "trades": ledger_trades,
        "sim_trades": sim_trades,
        "sim_trade_count": len(ledger_trades),
        "stock_contrib": stock_contrib,
        "holdings_end": copy.deepcopy(paper.get("holdings") or []),
        "cash_end": round(float(paper.get("cash") or 0), 2),
        "paper": paper,
        "note": note,
    }


def rankings_from_topk_precomputed(
    precomputed_ranks: Dict[str, Any],
    *,
    top_k: int,
) -> Dict[str, List[dict]]:
    """把 Top-K ``precomputed_ranks`` 转成纸面调仓 ranking 行。"""
    top_k = max(1, int(top_k or 1))
    out: Dict[str, List[dict]] = {}
    for day, pack in (precomputed_ranks or {}).items():
        if not isinstance(pack, dict):
            continue
        picks = list(pack.get("picks") or [])[:top_k]
        items_by = pack.get("items_by_code") or {}
        rows: List[dict] = []
        for code, score in picks:
            c = str(code or "").strip()
            if not c:
                continue
            base = dict(items_by.get(c) or {})
            base["stock_code"] = c
            try:
                sc = float(score)
            except (TypeError, ValueError):
                sc = None
            if sc is not None:
                base.setdefault("predicted_score", sc)
                base.setdefault("predicted_score_blend", sc)
                base.setdefault("score", sc)
            base.setdefault("score_scale", "predicted_yhat")
            base.setdefault("dual_score_window", "eod_next")
            base["rank_source"] = "topk_precomputed"
            rows.append(base)
        out[str(day)] = rows
    return out


def build_momentum_rankings(
    stock_bars: Dict[str, List[dict]],
    *,
    top_k: int,
) -> Dict[str, List[dict]]:
    """用昨收到今收涨跌幅做轻量目标簿（日报离线摘要用；≠生产 ŷ）。"""
    date_maps = {str(c): _bars_by_date(bars) for c, bars in (stock_bars or {}).items()}
    dates = _coverage_dates(stock_bars)
    top_k = max(1, int(top_k or 1))
    out: Dict[str, List[dict]] = {}
    for i, day in enumerate(dates):
        if i < 1:
            continue
        rows: List[dict] = []
        prev_d = dates[i - 1]
        for code, dm in date_maps.items():
            bar = dm.get(day)
            prev = dm.get(prev_d)
            if not bar or not prev:
                continue
            try:
                c0 = float(prev.get("close") or 0)
                c1 = float(bar.get("close") or 0)
            except (TypeError, ValueError):
                continue
            if c0 <= 0 or c1 <= 0:
                continue
            ret = (c1 / c0 - 1.0) * 100.0
            rows.append(
                {
                    "stock_code": code,
                    "stock_name": code,
                    "score": ret,
                    "predicted_score": ret,
                    "predicted_score_eod": ret,
                    "score_scale": "predicted_yhat",
                    "dual_score_window": "eod_next",
                    "rank_source": "momentum_close",
                }
            )
        rows.sort(key=lambda r: float(r.get("predicted_score") or 0), reverse=True)
        out[day] = rows[:top_k]
    return out


def summarize_paper_replay_for_daily(
    stock_bars: Dict[str, List[dict]],
    *,
    top_k: int,
    lookback: Optional[int] = None,
    rankings_by_date: Optional[Dict[str, List[dict]]] = None,
    rank_source: Optional[str] = None,
) -> Dict[str, Any]:
    """日报用轻量摘要（与 ``summarize_portfolio_backtest`` 字段对齐子集）。

    优先用注入的 Top-K ŷ ranking；否则昨收→今收动量近似。
    """
    src = rank_source
    if rankings_by_date is None:
        rankings = build_momentum_rankings(stock_bars, top_k=top_k)
        src = src or "momentum_close"
    else:
        rankings = rankings_by_date
        src = src or "topk_precomputed"

    bt = backtest_paper_replay(
        stock_bars,
        top_k=top_k,
        rankings_by_date=rankings,
        cost_model="simple_cn",
        initial_cash=REPLAY_INITIAL_CASH,
        cash_floor=REPLAY_CASH_FLOOR,
        lookback=lookback,
    )
    if not bt.get("success"):
        return bt
    metrics = bt.get("metrics") or {}
    curve = bt.get("equity_curve") or []
    params = dict(bt.get("params") or {})
    params["rank_source"] = src
    if lookback is not None:
        params["lookback"] = int(lookback)
    note = str(bt.get("note") or "")
    if src == "topk_precomputed":
        note += " · 日报目标簿=与 Top-K 同序列 ŷ（precomputed_ranks）"
    else:
        note += " · 日报目标簿=昨收涨跌幅动量近似（≠ ŷ_oo）"
    return {
        "success": True,
        "engine": ENGINE_ID,
        "loaded_stocks": list(stock_bars.keys()),
        "trade_count": metrics.get("trade_count"),
        "total_return_pct": metrics.get("total_return_pct"),
        "win_rate_pct": metrics.get("win_rate_pct"),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "params": params,
        "equity_curve_tail": curve[-12:],
        "constraints_hit": bt.get("constraints_hit"),
        "metrics": metrics,
        "note": note,
    }
