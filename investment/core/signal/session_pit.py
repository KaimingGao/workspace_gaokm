"""Live 日线 PIT：盘中不把未完成的 T 日 K 线喂给 ŷ_EOD。

收盘后 T 日 K 线完整，ŷ_EOD 滚到预测下一期。
盘中 ŷ_trade = w·ŷ_EOD + w·(缺口∘ŷ_τ)；收盘后 ŷ_trade = ŷ_EOD（剥离当日 τ）。
τ 买入闸收盘后不吃当日 ŷ_τ；nowcast 对照列仍吃 ŷ_τ。
"""

from __future__ import annotations

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
