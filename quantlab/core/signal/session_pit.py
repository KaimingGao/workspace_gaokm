"""Live 日线 PIT：ŷ_oo 周期 = T 开盘 → T+1 开盘，今收不进 X。

盘中去掉未完成的 T 日 K。收盘后仍停在 T−1：周期内拟合的是 T 日 10:00 信息集，
不把 ŷ_oo 滚到 T+1→T+2。下一交易日 09:25（今开已定）才换 T；09:25 前仍是上一交易日。
回测成交钟仍是 09:30–10:00。盘中 ŷ_trade 可融 τ；时钟 ``eod_next``（15:05 后）给做 T 闸，不改变 ŷ_oo 目标。
"""


import logging

logger = logging.getLogger(__name__)
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

CN_TZ = timezone(timedelta(hours=8))
# 集合竞价 09:25 定今开；连续竞价 09:30 才成交。Live 的 T 从 09:25 起。
LIVE_SESSION_OPEN = (9, 25)
CONTINUOUS_OPEN = (9, 30)
_SESSION_CLOSE = (15, 5)


def shanghai_now(now: Optional[datetime] = None) -> datetime:
    if now is None:
        return datetime.now(CN_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=CN_TZ)
    return now.astimezone(CN_TZ)


def quote_asof(quote: Optional[dict]) -> str:
    if not isinstance(quote, dict):
        return ""
    return str(quote.get("date") or quote.get("trade_date") or "")[:10]


def asof_session_final(asof: str, *, now: Optional[datetime] = None) -> bool:
    """asof 当日日线是否已完成。历史日 / 收盘后 / 周末相对当日 → True。"""
    n = shanghai_now(now)
    today = n.strftime("%Y-%m-%d")
    day = str(asof or "")[:10]
    if not day:
        return (n.hour, n.minute) >= _SESSION_CLOSE or n.weekday() >= 5
    if day < today:
        return True
    if day > today:
        return False
    return (n.hour, n.minute) >= _SESSION_CLOSE


def clock_dual_score_window(*, now: Optional[datetime] = None) -> str:
    """无「今日报价日」时：交易日 09:25–15:05 → ``intraday``，否则 ``eod_next``。

    必须看 **日历今天** 是否开市，不能用 ``resolve_session_date`` 回退到上周五，
    否则周末白天会被误判成盘中。09:25 前今开未定，仍算上一交易日。
    """
    n = shanghai_now(now)
    t = (n.hour, n.minute)
    in_hours = LIVE_SESSION_OPEN <= t < _SESSION_CLOSE
    today = n.strftime("%Y-%m-%d")
    trading_today = n.weekday() < 5
    try:
        from core.market.calendar import is_trading_day

        trading_today = bool(is_trading_day(today))
    except Exception:  # noqa: BLE001
        logger.debug("trading-day check failed in clock_dual_score_window", exc_info=True)
    if trading_today and in_hours:
        return "intraday"
    return "eod_next"


def oo_cycle_date(*, now: Optional[datetime] = None) -> str:
    """当前 ŷ_oo / 调仓的 T 日。

    交易日 09:25 起（含收盘后当晚）T=今日；次日 09:25 前仍是上一交易日。
    周末 / 假日回退到最近已过交易日。回测成交钟不跟这个边界。
    """
    n = shanghai_now(now)
    today = n.strftime("%Y-%m-%d")
    t = (n.hour, n.minute)
    trading_today = n.weekday() < 5
    try:
        from core.market.calendar import is_trading_day

        trading_today = bool(is_trading_day(today))
    except Exception:  # noqa: BLE001
        logger.debug("oo_cycle_date trading-day check failed", exc_info=True)
    if trading_today and t >= LIVE_SESSION_OPEN:
        return today
    try:
        from core.market.calendar import prev_trading_day, resolve_session_date

        if trading_today:
            prev = str(prev_trading_day(today) or "")[:10]
            return prev or today
        return str(resolve_session_date(now=n) or today)[:10]
    except Exception:  # noqa: BLE001
        logger.debug("oo_cycle_date prev session failed", exc_info=True)
        return today


def _is_latest_complete_offline_asof(asof: str, *, now: Optional[datetime] = None) -> bool:
    """报价日是否等于「此刻仓里应有的最新完整日线」（盘中=昨收）。"""
    day = str(asof or "")[:10]
    if not day:
        return False
    try:
        from core.market.calendar import expected_latest_daily_bar_date

        expected = str(expected_latest_daily_bar_date(now=shanghai_now(now)) or "")[:10]
    except Exception:  # noqa: BLE001
        logger.debug("expected latest bar date failed", exc_info=True)
        n = shanghai_now(now)
        expected = (n - timedelta(days=1)).strftime("%Y-%m-%d")
    return bool(expected) and day == expected


def resolve_minute_tau_trade_date(
    quote: Optional[dict] = None,
    bars: Optional[Sequence[dict]] = None,
    *,
    now: Optional[datetime] = None,
) -> str:
    """分钟 τ / ŷ_τc 的 T 日。

    ŷ_τc = close[T]/price(τ)−1。τ=open 时 price(τ)=open，等于 close/open−1。
    T 为 ŷ_oo 周期日（T 开→T+1 开，含收盘后）。
    日线仓在 15:05 前仍停在昨收完整 K；实时行情也常无 ``date``。
    若把 ``bars[-1]`` 当 T，tip 会停在昨天 10:00，并把昨路径配到今开。

    历史 PIT：报价日不是当前周期 T / 最新完整日线时沿用报价日。
    """
    n = shanghai_now(now)
    q_day = quote_asof(quote)
    bar_day = ""
    hist = [b for b in (bars or []) if isinstance(b, dict)]
    if hist:
        last = hist[-1]
        bar_day = str(last.get("date") or last.get("trade_date") or "")[:10]
    asof = q_day or bar_day
    try:
        from core.market.calendar import resolve_session_date

        session = str(resolve_session_date(now=n) or "")[:10]
    except Exception:  # noqa: BLE001
        logger.debug("resolve_session_date failed in minute tau T", exc_info=True)
        session = n.strftime("%Y-%m-%d")
    cycle = oo_cycle_date(now=n)
    win = clock_dual_score_window(now=n)
    if win == "intraday" and (not asof or _is_latest_complete_offline_asof(asof, now=n)):
        return session or asof
    # 收盘后仍在 T 开→T+1 开周期：ŷ_oc 继续用 T 日 10:00，不要退回昨收 K
    if cycle and (not asof or _is_latest_complete_offline_asof(asof, now=n) or asof == cycle):
        return cycle
    try:
        from core.market.calendar import prev_trading_day

        prev = str(prev_trading_day(cycle) or "")[:10] if cycle else ""
    except Exception:  # noqa: BLE001
        logger.debug("minute tau T prev day failed", exc_info=True)
        prev = ""
    if asof and cycle and asof == prev:
        return cycle
    if asof:
        return asof
    return cycle or session


def _last_daily_bar_date(bars: Optional[Sequence[dict]]) -> str:
    hist = [b for b in (bars or []) if isinstance(b, dict)]
    if not hist:
        return ""
    last = hist[-1]
    return str(last.get("date") or last.get("trade_date") or "")[:10]


def _finite_px(*vals: Any) -> Optional[float]:
    for v in vals:
        if v is None or v == "":
            continue
        try:
            n = float(str(v).replace("元", "").replace(",", "").strip())
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n
    return None


def daily_cache_behind_tau_session(
    quote: Optional[dict] = None,
    bars: Optional[Sequence[dict]] = None,
    trade_day: str = "",
) -> bool:
    """日线/合成 quote 停在昨收，而 ŷ_τc 的 T 已是当前会话日。"""
    day = str(trade_day or "")[:10]
    if len(day) < 10:
        return False
    last_d = _last_daily_bar_date(bars)
    q_day = quote_asof(quote)
    return bool((last_d and day > last_d) or (q_day and day > q_day))


OPEN_T_SOURCE_DAILY = "daily_open"
OPEN_T_SOURCE_QUOTE = "quote_open"
OPEN_T_SOURCE_MINUTE = "minute_open"


def _daily_bar_on(bars: Optional[Sequence[dict]], day: str) -> Optional[dict]:
    want = str(day or "")[:10]
    if len(want) < 10:
        return None
    for b in reversed(list(bars or [])):
        if not isinstance(b, dict):
            continue
        d = str(b.get("date") or b.get("trade_date") or "")[:10]
        if d == want:
            return b
    return None


def _quote_open_is_trade_day(quote: Optional[dict], trade_day: str) -> bool:
    """无 date 的实时行情视为今开；带 date 必须等于 T。昨 K 合成 quote 为假。"""
    day = str(trade_day or "")[:10]
    if len(day) < 10:
        return False
    q_day = quote_asof(quote)
    if not q_day:
        return True
    return q_day == day


def _prev_close_for_open_t(
    quote: Optional[dict],
    bars: Optional[Sequence[dict]],
    trade_day: str,
) -> Optional[float]:
    hist = [b for b in (bars or []) if isinstance(b, dict)]
    day = str(trade_day or "")[:10]
    last_d = _last_daily_bar_date(bars)
    behind = daily_cache_behind_tau_session(quote, bars, day)
    q = quote if isinstance(quote, dict) else {}
    prev_c = _finite_px(
        q.get("prev_close"),
        q.get("pre_close"),
        q.get("yesterday_close"),
        q.get("last_close"),
    )
    if behind and hist:
        return _finite_px(hist[-1].get("close")) or prev_c
    if last_d == day and len(hist) >= 2:
        return _finite_px(hist[-2].get("close")) or prev_c
    if hist:
        return _finite_px(hist[-1].get("close")) or prev_c
    return prev_c


def resolve_open_t(
    quote: Optional[dict] = None,
    bars: Optional[Sequence[dict]] = None,
    *,
    trade_day: str = "",
    minute_bars: Optional[Sequence[dict]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """统一 open(T)：日线今开 → 行情今开 → T 日 09:30–09:35 分钟开。

    昨 K 合成 quote.open 不是今开。现价 / change_raw 不当开盘。
    """
    day = str(trade_day or "")[:10]
    if len(day) < 10:
        day = resolve_minute_tau_trade_date(quote, bars, now=now)
    q = quote if isinstance(quote, dict) else {}
    prev_c = _prev_close_for_open_t(q, bars, day)
    open_px: Optional[float] = None
    source: Optional[str] = None

    day_bar = _daily_bar_on(bars, day)
    if day_bar is not None:
        open_px = _finite_px(day_bar.get("open"))
        if open_px is not None:
            source = OPEN_T_SOURCE_DAILY

    if open_px is None and _quote_open_is_trade_day(q, day):
        open_px = _finite_px(q.get("open_raw"), q.get("open"), q.get("open_price"))
        if open_px is not None:
            source = OPEN_T_SOURCE_QUOTE

    if open_px is None and minute_bars:
        try:
            from core.signal.minute_tau_feats import session_first_minute_open

            open_px = session_first_minute_open(minute_bars, day)
        except Exception:  # noqa: BLE001
            logger.debug("session_first_minute_open failed", exc_info=True)
            open_px = None
        if open_px is not None:
            source = OPEN_T_SOURCE_MINUTE

    gap = None
    if open_px is not None and prev_c is not None and float(prev_c) > 0:
        gap = round((float(open_px) / float(prev_c) - 1.0) * 100.0, 4)
    return {
        "trade_day": day,
        "open": open_px,
        "prev_close": prev_c,
        "gap_pct": gap,
        "source": source,
    }


def tau_open_and_prev_close(
    quote: Optional[dict] = None,
    bars: Optional[Sequence[dict]] = None,
    trade_day: str = "",
    *,
    minute_bars: Optional[Sequence[dict]] = None,
    now: Optional[datetime] = None,
) -> Tuple[Optional[float], Optional[float]]:
    """T 开盘与昨收。昨 K 合成 open 丢掉；无 date 实时行情今开保留。"""
    got = resolve_open_t(
        quote,
        bars,
        trade_day=trade_day,
        minute_bars=minute_bars,
        now=now,
    )
    return got.get("open"), got.get("prev_close")


def recover_gap_pct_from_minute_pack(feats: Optional[dict]) -> Optional[float]:
    """由开→τ / 昨收→τ 还原缺口 open/prev−1（分钟首根开 × 昨收）。"""
    if not isinstance(feats, dict):
        return None
    try:
        rot = float(feats.get("ret_open_to_tau"))
        rpt = float(feats.get("ret_prev_to_tau"))
    except (TypeError, ValueError):
        return None
    denom = 1.0 + rot / 100.0
    if abs(denom) < 1e-12:
        return None
    return round(((1.0 + rpt / 100.0) / denom - 1.0) * 100.0, 4)


def prepare_eod_bars(
    bars: Optional[Sequence[dict]],
    quote: Optional[dict] = None,
    *,
    now: Optional[datetime] = None,
) -> Tuple[List[dict], Dict[str, Any]]:
    """ŷ_oo X 去掉当前周期 T 日 K（今收不进 X），直到 T+1 开盘。

    ``dual_score_window`` 仍按沪市时钟（15:05 后 ``eod_next``）。
    ``rolled_to_next`` 只表示 ŷ_oo 目标已换到下一周期；T 收盘不会把它翻成 True。
    """
    hist = [b for b in (bars or []) if isinstance(b, dict)]
    asof = quote_asof(quote)
    if not asof and hist:
        asof = str(hist[-1].get("date") or "")[:10]
    final = asof_session_final(asof, now=now)
    cycle_t = oo_cycle_date(now=now)
    stripped = False
    eod = hist
    if hist:
        last_d = str(hist[-1].get("date") or "")[:10]
        if last_d and last_d == cycle_t and len(hist) >= 2:
            eod = hist[:-1]
            stripped = True
        elif not final and asof and last_d == asof:
            eod = hist[:-1]
            stripped = True
    eod_as_of = str(eod[-1].get("date") or "")[:10] if eod else None
    quote_day = asof or None
    win = clock_dual_score_window(now=now)
    # live 昨收仓 / 当前周期 T：窗口跟时钟。ŷ_oo 目标要到 T+1 09:30 才滚。
    # 更早的历史 asof 仍走「该会话已收盘 → eod_next」（回测/attach）。
    if quote_day and (
        _is_latest_complete_offline_asof(quote_day, now=now) or quote_day == cycle_t
    ):
        rolled = False
    else:
        rolled = bool(
            final
            and eod_as_of
            and quote_day
            and str(eod_as_of)[:10] == str(quote_day)[:10]
        )
        if rolled:
            win = "eod_next"
    return eod, {
        "quote_as_of": quote_day,
        "eod_as_of": eod_as_of,
        "asof_final": final,
        "stripped_asof_bar": stripped,
        "rolled_to_next": rolled,
        "oo_cycle_date": cycle_t,
        "dual_score_window": win,
    }


def refresh_dual_score_window(
    item: Optional[dict],
    *,
    quote: Optional[dict] = None,
    bars: Optional[Sequence[dict]] = None,
    now: Optional[datetime] = None,
) -> str:
    """按当前沪市时钟刷新 ``dual_score_window``（写回 item）。

    簿上昨晚 ``eod_next`` 不得锁死次日盘中：开盘后应回到 ``intraday``，
    否则读路径会一直剥离 τ、表列 TRADE 假「单」。
    有 quote/bars 时走 ``prepare_eod_bars``；否则仅用今日是否已收盘判断。
    """
    q = quote if isinstance(quote, dict) else None
    b = list(bars) if bars is not None else None
    if q is not None or b is not None:
        _eod, pit = prepare_eod_bars(b, q, now=now)
        win = str(pit.get("dual_score_window") or "intraday")
    else:
        win = clock_dual_score_window(now=now)
    if isinstance(item, dict):
        item["dual_score_window"] = win
    return win


def quote_for_eod_score(
    quote: Optional[dict],
    *,
    strip_intraday_change: bool = False,
    gap_pct: Optional[float] = None,
    open_t: Optional[float] = None,
) -> Optional[dict]:
    """ŷ_oo 的 quote：``change_raw`` 只装今开缺口，不装现价涨跌。"""
    if not isinstance(quote, dict):
        return quote
    out = dict(quote)
    if gap_pct is not None:
        try:
            out["change_raw"] = round(float(gap_pct), 4)
        except (TypeError, ValueError):
            out["change_raw"] = None
        out["change"] = None
    elif strip_intraday_change:
        out["change_raw"] = None
        out["change"] = None
    else:
        # 未解析到今开缺口时，丢掉腾讯现价涨跌，避免灌进 last_change
        out["change_raw"] = None
        out["change"] = None
    if open_t is not None:
        try:
            ox = float(open_t)
        except (TypeError, ValueError):
            ox = None
        if ox is not None and ox > 0:
            out["open_raw"] = ox
            out["open"] = ox
    return out
