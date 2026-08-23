"""打分账本：决策日 as_of / 因子截止日推断。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

from collections import Counter

from core.numbers import date_key


def default_as_of() -> str:
    """默认识别为「上一交易日」（相对当前会话日）。"""
    from core.market.calendar import prev_trading_day, resolve_session_date

    sess = resolve_session_date()
    prev = prev_trading_day(sess, n=1)
    return prev or sess


def _codes_from_book_doc(book_doc: Optional[dict]) -> List[str]:
    if not isinstance(book_doc, dict):
        return []
    codes: List[str] = []
    seen = set()
    for key in ("book", "scored_all"):
        for item in book_doc.get(key) or []:
            if not isinstance(item, dict):
                continue
            c = str(item.get("stock_code") or item.get("code") or "").strip()
            if not c or c in seen:
                continue
            seen.add(c)
            codes.append(c)
    return codes


def _last_bar_date_for_code(code: str) -> Optional[str]:
    """本地日线末根日期（只读缓存，不拉网；经 DataService）。"""
    raw = str(code or "").strip()
    if not raw:
        return None
    try:
        from core.data.facade import bars_and_source

        bars, _src = bars_and_source(
            raw,
            limit=5,
            offline_only=True,
            reject_quote_fallback=True,
        )
        if not bars:
            return None
        return date_key(bars[-1].get("date") or bars[-1].get("time"))
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
        return None


def infer_feature_as_of(
    *,
    book_doc: Optional[dict] = None,
    codes: Optional[Sequence[str]] = None,
    sample: int = 16,
) -> Optional[str]:
    """从分池簿标的本地日线推断因子截止日（多数末根日期）。"""

    pool = list(codes or []) or _codes_from_book_doc(book_doc)
    if not pool:
        return None
    take = max(1, min(int(sample or 16), 40, len(pool)))
    lasts: List[str] = []
    for code in pool[:take]:
        d = _last_bar_date_for_code(code)
        if d:
            lasts.append(d)
    if not lasts:
        return None
    mode, n = Counter(lasts).most_common(1)[0]
    # 至少一半样本同意；否则取最早末根（偏保守，避免超前标决策日）
    if n * 2 >= len(lasts):
        return mode
    return min(lasts)


def resolve_freeze_as_of(
    as_of: Optional[str] = None,
    *,
    book_doc: Optional[dict] = None,
    codes: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """冻结决策日：对齐因子截止，禁止「会话日标签 + 昨收因子」。

    规则：
    - 无显式 as_of → 优先本地日线推断的 feature_as_of，否则上一交易日
    - 显式 as_of 若晚于 feature_as_of → 下调到 feature_as_of
    - 无日线证据时，禁止把 as_of 标成「今天会话日」（除非已是上一交易日）
    """
    from core.market.calendar import resolve_session_date

    sess = resolve_session_date()
    prev = default_as_of()
    feature = infer_feature_as_of(book_doc=book_doc, codes=codes)
    requested = date_key(as_of) if as_of else None
    notes: List[str] = []

    if requested:
        target = requested
    elif feature:
        target = feature
        notes.append(f"按因子截止日 {feature} 冻结")
    else:
        target = prev
        notes.append(f"无日线证据，默认上一交易日 {prev}")

    remapped = False
    if feature and target and target > feature:
        notes.append(f"请求 {target} 晚于因子截止 {feature}，已下调")
        target = feature
        remapped = True
    if not feature and target == sess and sess != prev:
        notes.append(f"无今日日线证据，会话日 {sess} 下调为 {prev}")
        target = prev
        remapped = True

    return {
        "as_of": target,
        "session_date": sess,
        "prev_trading_day": prev,
        "feature_as_of": feature,
        "requested_as_of": requested,
        "remapped": remapped,
        "note": "；".join(notes) if notes else None,
    }

