"""D3 · A 股交易日历 lite（周末 + 可选节假日表）。

非交易所官方全文；用于研究日对齐。停牌仅提供关键词 hint，不伪造全日停牌库。
"""

import logging

logger = logging.getLogger(__name__)
import json
import os
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from core.numbers import date_key


def _load_holidays(store_dir: Optional[str] = None) -> Set[str]:
    from core.paths import STORE_DIR

    root = store_dir or STORE_DIR
    path = os.path.join(root, "cn_holidays.json")
    if not os.path.isfile(path):
        return set()
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return set()
    days = payload.get("holidays") if isinstance(payload, dict) else payload
    out: Set[str] = set()
    for d in days or []:
        k = date_key(d)
        if k:
            out.add(k)
    return out


def is_weekend(date_str: str) -> bool:
    d = date_key(date_str)
    if not d:
        return False
    try:
        dt = datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        return False
    return dt.weekday() >= 5


def is_trading_day(
    date_str: str,
    *,
    holidays: Optional[Set[str]] = None,
    store_dir: Optional[str] = None,
) -> bool:
    """周末或节假日 → False。"""
    d = date_key(date_str)
    if not d:
        return False
    if is_weekend(d):
        return False
    hol = holidays if holidays is not None else _load_holidays(store_dir)
    return d not in hol


def filter_trading_dates(
    dates: Iterable[str],
    *,
    store_dir: Optional[str] = None,
) -> List[str]:
    hol = _load_holidays(store_dir)
    out: List[str] = []
    for d in dates:
        k = date_key(d)
        if k and is_trading_day(k, holidays=hol):
            out.append(k)
    return out


def next_trading_day(
    date_str: str,
    *,
    n: int = 1,
    store_dir: Optional[str] = None,
    holidays: Optional[Set[str]] = None,
) -> str:
    """从 date_str 起向后找第 n 个交易日（不含当日）。"""
    from datetime import timedelta

    d = date_key(date_str)
    if not d:
        return ""
    try:
        dt = datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        return ""
    hol = holidays if holidays is not None else _load_holidays(store_dir)
    left = max(1, int(n or 1))
    guard = 0
    while left > 0 and guard < 400:
        dt += timedelta(days=1)
        guard += 1
        k = dt.strftime("%Y-%m-%d")
        if is_trading_day(k, holidays=hol):
            left -= 1
            if left == 0:
                return k
    return ""


def prev_trading_day(
    date_str: str,
    *,
    n: int = 1,
    store_dir: Optional[str] = None,
    holidays: Optional[Set[str]] = None,
) -> str:
    """从 date_str 起向前找第 n 个交易日（不含当日）。"""
    from datetime import timedelta

    d = date_key(date_str)
    if not d:
        return ""
    try:
        dt = datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        return ""
    hol = holidays if holidays is not None else _load_holidays(store_dir)
    left = max(1, int(n or 1))
    guard = 0
    while left > 0 and guard < 400:
        dt -= timedelta(days=1)
        guard += 1
        k = dt.strftime("%Y-%m-%d")
        if is_trading_day(k, holidays=hol):
            left -= 1
            if left == 0:
                return k
    return ""


def resolve_session_date(
    *,
    now: Optional[datetime] = None,
    store_dir: Optional[str] = None,
) -> str:
    """当前会话交易日：今日若开市则为今日，否则回退到最近已过交易日。"""
    from datetime import timedelta

    hol = _load_holidays(store_dir)
    dt = now or datetime.now()
    k = dt.strftime("%Y-%m-%d")
    if is_trading_day(k, holidays=hol):
        return k
    guard = 0
    while guard < 20:
        dt -= timedelta(days=1)
        guard += 1
        k = dt.strftime("%Y-%m-%d")
        if is_trading_day(k, holidays=hol):
            return k
    return datetime.now().strftime("%Y-%m-%d")


def expected_latest_daily_bar_date(*, now: Optional[datetime] = None) -> str:
    """最新完整日线 as-of：交易日 15:05 前仍用上一交易日；周末/假日用最近已过交易日。

    增量补齐用这个判断「本地是否已齐」，不要拿日历今天当缺口终点，
    否则周日会把周五已齐的仓全部打成远端。
    """
    dt = now or datetime.now()
    session = str(resolve_session_date(now=dt) or "")[:10]
    if not session:
        return ""
    if not is_trading_day(session):
        return session
    today = dt.strftime("%Y-%m-%d")
    cutoff = dt.replace(hour=15, minute=5, second=0, microsecond=0)
    if today == session and dt < cutoff:
        prev = prev_trading_day(session)
        return prev or session
    return session


def halt_hint(text: str) -> Dict[str, Any]:
    """最小停牌提示：关键词检测，非完整停牌日历。"""
    s = str(text or "")
    hit = any(k in s for k in ("停牌", "暂停上市", "halt", "suspended"))
    return {
        "possible_halt": hit,
        "note": "仅关键词 hint；完整停牌库不在 D 轨范围。",
    }


def filter_halted_bars(
    bars: Iterable[dict],
    *,
    drop_zero_volume: bool = True,
    drop_keyword_halt: bool = True,
) -> Tuple[List[dict], Dict[str, Any]]:
    """DC1 · 过滤零量/关键词停牌 bar；返回 (kept, audit)。"""
    from core.pro_core import filter_halted_bars as _filter

    return _filter(
        bars,
        drop_zero_volume=drop_zero_volume,
        drop_keyword_halt=drop_keyword_halt,
    )


def calendar_status(*, store_dir: Optional[str] = None) -> Dict[str, Any]:
    hol = sorted(_load_holidays(store_dir))
    return {
        "ok": True,
        "market": "CN",
        "holiday_count": len(hol),
        "holidays_tail": hol[-20:],
        "weekend_filtered": True,
        "note": "日历 lite：周末 + data/store/cn_holidays.json；无文件则仅周末。",
    }
