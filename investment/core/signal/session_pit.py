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
    rolled = bool(
        final and eod_as_of and quote_day and str(eod_as_of)[:10] == str(quote_day)[:10]
    )
    return eod, {
        "quote_as_of": quote_day,
        "eod_as_of": eod_as_of,
        "asof_final": final,
        "stripped_asof_bar": stripped,
        "rolled_to_next": rolled,
        "dual_score_window": "eod_next" if rolled else "intraday",
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
    win = "intraday"
    q = quote if isinstance(quote, dict) else None
    b = list(bars) if bars is not None else None
    if q is not None or b is not None:
        _eod, pit = prepare_eod_bars(b, q, now=now)
        win = str(pit.get("dual_score_window") or "intraday")
    else:
        n = shanghai_now(now)
        # 无行情上下文：仅在交易日盘中时段标 intraday；
        # 开盘前 / 收盘后 / 周末一律 eod_next（勿把凌晨误判成盘中）。
        t = (n.hour, n.minute)
        in_session = (9, 30) <= t < _SESSION_CLOSE
        trading = True
        try:
            from core.market.calendar import is_trading_day, resolve_session_date

            sess = resolve_session_date(now=n)
            trading = bool(sess and is_trading_day(sess))
        except Exception:  # noqa: BLE001
            logger.debug("trading-day check failed in refresh_dual_score_window", exc_info=True)
            trading = n.weekday() < 5
        if trading and in_session:
            win = "intraday"
        else:
            win = "eod_next"
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
