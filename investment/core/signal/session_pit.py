"""Live 日线 PIT：盘中不把未完成的 T 日 K 线喂给 ŷ_EOD。

收盘后 T 日 K 线完整，ŷ_EOD 滚到预测下一期。
盘中 ŷ_trade = w·ŷ_EOD + w·(缺口∘ŷ_τ)；收盘后 ŷ_trade = ŷ_EOD（剥离当日 τ）。
τ 买入闸收盘后不吃当日 ŷ_τ；nowcast 对照列仍吃 ŷ_τ。
"""


import logging

logger = logging.getLogger(__name__)
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

CN_TZ = timezone(timedelta(hours=8))
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
    """无「今日报价日」时：交易日 09:30–15:05 → ``intraday``，否则 ``eod_next``。

    必须看 **日历今天** 是否开市，不能用 ``resolve_session_date`` 回退到上周五，
    否则周末白天会被误判成盘中。
    """
    n = shanghai_now(now)
    t = (n.hour, n.minute)
    in_hours = (9, 30) <= t < _SESSION_CLOSE
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


def prepare_eod_bars(
    bars: Optional[Sequence[dict]],
    quote: Optional[dict] = None,
    *,
    now: Optional[datetime] = None,
) -> Tuple[List[dict], Dict[str, Any]]:
    """盘中去掉与报价同日的未完成 K；收盘后保留。"""
    hist = [b for b in (bars or []) if isinstance(b, dict)]
    asof = quote_asof(quote)
    if not asof and hist:
        asof = str(hist[-1].get("date") or "")[:10]
    final = asof_session_final(asof, now=now)
    stripped = False
    eod = hist
    if hist and not final:
        last_d = str(hist[-1].get("date") or "")[:10]
        if asof and last_d == asof:
            eod = hist[:-1]
            stripped = True
    eod_as_of = str(eod[-1].get("date") or "")[:10] if eod else None
    quote_day = asof or None
    # 昨收仓：末根已完成 ≠ 今日已收盘。若按 asof_final 直接 rolled，
    # 次日盘中会一直 eod_next，数据中心 TRADE 假「单」。
    # 更早的历史 asof 仍走「该会话已收盘 → eod_next」（回测/attach）。
    if quote_day and _is_latest_complete_offline_asof(quote_day, now=now):
        win = clock_dual_score_window(now=now)
        rolled = win == "eod_next"
    else:
        rolled = bool(
            final
            and eod_as_of
            and quote_day
            and str(eod_as_of)[:10] == str(quote_day)[:10]
        )
        win = "eod_next" if rolled else "intraday"
    return eod, {
        "quote_as_of": quote_day,
        "eod_as_of": eod_as_of,
        "asof_final": final,
        "stripped_asof_bar": stripped,
        "rolled_to_next": rolled,
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
    strip_intraday_change: bool,
) -> Optional[dict]:
    """盘中剥掉 T 日 K 线后，禁止把行情涨跌幅漏进 ŷ_EOD 因子。"""
    if not isinstance(quote, dict) or not strip_intraday_change:
        return quote
    out = dict(quote)
    out["change_raw"] = None
    out["change"] = None
    return out
