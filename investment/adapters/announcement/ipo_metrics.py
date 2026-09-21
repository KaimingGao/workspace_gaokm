"""IPO 虹吸：概念板块成交额 + 新股市值估算。"""


import logging
import re
from typing import Any, Dict, List, Optional

from core.numbers import to_float as _to_float

logger = logging.getLogger(__name__)

DEFAULT_CONCEPT_FOR_DRAIN = ("机器人", "人形机器人", "人工智能", "半导体")


def _fetch_concept_board_amount(concept: str) -> Optional[float]:
    from adapters.market.ak_lock import import_akshare

    ak = import_akshare()
    fn = getattr(ak, "stock_board_concept_name_em", None)
    if not fn:
        return None
    try:
        df = fn()
        if df is None or getattr(df, "empty", True):
            return None
        for row in df.to_dict(orient="records"):
            name = str(row.get("板块名称") or row.get("name") or "")
            if concept not in name:
                continue
            amt = _to_float(
                row.get("总成交额")
                or row.get("成交额")
                or row.get("amount")
                or row.get("总市值")
            )
            if amt is not None:
                return float(amt)
    except Exception:
        logger.debug("concept board amount failed for %s", concept, exc_info=True)
    return None


def estimate_ipo_market_cap(
    *,
    code: Optional[str] = None,
    name: Optional[str] = None,
    issue_shares: Optional[float] = None,
) -> Optional[float]:
    """估算 IPO 首日市值：优先 quote 总市值，否则 issue_shares × price。"""
    if code:
        try:
            from core.data.facade import get_quote

            q = get_quote(str(code))
            if isinstance(q, dict) and q.get("success"):
                mv = _to_float(q.get("market_cap") or q.get("market_cap_raw"))
                if mv is not None and mv > 0:
                    return float(mv)
                price = _to_float(q.get("price_raw") or q.get("price"))
                shares = issue_shares
                if price is not None and shares is not None and shares > 0:
                    return float(price) * float(shares)
        except Exception:
            logger.debug("ipo quote mv failed for %s", code, exc_info=True)
    return None


def concept_turnover_map(concepts: Optional[List[str]] = None) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for c in concepts or DEFAULT_CONCEPT_FOR_DRAIN:
        amt = _fetch_concept_board_amount(c)
        if amt is not None:
            out[c] = float(amt)
    return out


def enrich_ipo_events(events: List[dict]) -> List[dict]:
    enriched: List[dict] = []
    for ev in events or []:
        row = dict(ev)
        code = str(row.get("code") or row.get("stock_code") or "")
        digits = re.sub(r"\D", "", code)
        if len(digits) >= 6:
            code = digits[-6:]
        shares_raw = row.get("issue_amount") or row.get("发行总数") or row.get("issue_shares")
        shares = _to_float(shares_raw)
        mv = estimate_ipo_market_cap(
            code=code or None,
            name=str(row.get("name") or ""),
            issue_shares=shares,
        )
        row["stock_code"] = code or row.get("stock_code")
        row["market_cap_est"] = mv
        enriched.append(row)
    return enriched


def compute_drain_ratios(
    ipo_events: List[dict],
    concept_amounts: Dict[str, float],
) -> Dict[str, Any]:
    """IPO 市值 / 概念板块成交额。"""
    max_mv = 0.0
    leader = None
    for ev in ipo_events or []:
        mv = _to_float(ev.get("market_cap_est"))
        if mv is not None and mv > max_mv:
            max_mv = float(mv)
            leader = ev

    ratios: Dict[str, float] = {}
    for concept, amt in (concept_amounts or {}).items():
        if amt and amt > 0 and max_mv > 0:
            ratios[concept] = round(max_mv / float(amt), 4)

    best_ratio = max(ratios.values()) if ratios else None
    return {
        "max_ipo_market_cap": max_mv if max_mv > 0 else None,
        "lead_ipo": leader,
        "concept_turnover": concept_amounts,
        "drain_ratios_by_concept": ratios,
        "liquidity_drain_ratio": best_ratio,
        "extreme_ipo_day": bool(max_mv >= 100_000_000_000 or (best_ratio or 0) >= 3.0),
    }
