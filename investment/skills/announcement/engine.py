"""交易所公告扫描：停牌核查 / 异常波动 / 监管降温。"""


import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

REGULATORY_PATTERNS = (
    r"停牌核查",
    r"停牌审查",
    r"严重异常波动",
    r"异常波动公告",
    r"股票交易异常",
    r"核查公告",
    r"暂停交易",
    r"风险提示",
)

CONCEPT_HINTS = (
    "机器人",
    "人形机器人",
    "半导体",
    "芯片",
    "人工智能",
    "AI",
    "光通信",
    "算力",
)


def _compile_patterns() -> List[re.Pattern]:
    return [re.compile(p, re.I) for p in REGULATORY_PATTERNS]


def _safe_notice_rows(*, limit: int = 200) -> List[dict]:
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()
    for fn_name in ("stock_notice_report", "stock_individual_notice_report"):
        fn = getattr(ak, fn_name, None)
        if not fn:
            continue
        try:
            try:
                df = fn(symbol="全部", date=(datetime.now() - timedelta(days=3)).strftime("%Y%m%d"))
            except TypeError:
                try:
                    df = fn()
                except TypeError:
                    df = fn(symbol="000001")
            if df is None or getattr(df, "empty", True):
                continue
            rows = df.to_dict(orient="records")
            return list(rows or [])[:limit]
        except Exception:
            logger.debug("%s failed", fn_name, exc_info=True)
            continue
    return []


def _fetch_ipo_calendar() -> List[dict]:
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()
    rows: List[dict] = []
    for fn_name in ("stock_ipo_info", "stock_new_ipo_cninfo", "stock_ipo_summary_cninfo"):
        fn = getattr(ak, fn_name, None)
        if not fn:
            continue
        try:
            df = fn()
            if df is not None and not getattr(df, "empty", True):
                rows.extend(df.to_dict(orient="records"))
                if rows:
                    break
        except Exception:
            logger.debug("%s failed", fn_name, exc_info=True)
    return rows[:50]


def _row_text(row: dict) -> str:
    parts = []
    for k in ("公告标题", "title", "名称", "公告类型", "内容", "摘要"):
        v = row.get(k)
        if v:
            parts.append(str(v))
    return " ".join(parts)


def _row_code(row: dict) -> str:
    for k in ("代码", "股票代码", "code", "symbol"):
        v = row.get(k)
        if v:
            s = str(v).strip()
            digits = re.sub(r"\D", "", s)
            if len(digits) >= 6:
                return digits[-6:]
            return s
    return ""


def scan_regulatory_notices(rows: Optional[List[dict]] = None) -> Dict[str, Any]:
    """扫描公告标题，提取监管降温信号。"""
    patterns = _compile_patterns()
    data = list(rows or _safe_notice_rows())
    hits: List[Dict[str, Any]] = []
    concept_tags: Set[str] = set()
    penalty_codes: Set[str] = set()
    for row in data:
        text = _row_text(row)
        if not text:
            continue
        if not any(p.search(text) for p in patterns):
            continue
        code = _row_code(row)
        hit = {
            "stock_code": code or None,
            "title": text[:200],
            "category": "regulatory_review",
        }
        hits.append(hit)
        if code:
            penalty_codes.add(code[-6:])
        for hint in CONCEPT_HINTS:
            if hint in text:
                concept_tags.add(hint)
    return {
        "count": len(hits),
        "hits": hits[:30],
        "concept_tags": sorted(concept_tags),
        "penalty_codes": sorted(penalty_codes),
        "active": len(hits) > 0,
    }


def build_ipo_drain_metrics(*, sector_daily_amount: Optional[float] = None) -> Dict[str, Any]:
    """超级 IPO 流动性虹吸：概念成交额 + 新股市值估算。"""
    from skills.announcement.ipo_metrics import (
        compute_drain_ratios,
        concept_turnover_map,
        enrich_ipo_events,
    )

    ipo_rows = _fetch_ipo_calendar()
    today = datetime.now().strftime("%Y-%m-%d")
    upcoming: List[Dict[str, Any]] = []
    today_compact = today.replace("-", "")
    for row in ipo_rows:
        text = _row_text(row)
        list_date = str(row.get("上市日期") or row.get("listing_date") or row.get("date") or "")[:10]
        list_compact = list_date.replace("-", "")
        if list_compact and list_compact not in (today_compact, today):
            continue
        name = str(row.get("名称") or row.get("name") or row.get("股票简称") or "")
        code = _row_code(row)
        issue_amt = row.get("发行总数") or row.get("issue_amount") or row.get("募资总额")
        upcoming.append(
            {
                "name": name[:40] if name else None,
                "code": code or None,
                "stock_code": code or None,
                "list_date": list_date or today,
                "issue_amount": issue_amt,
                "title": text[:120],
            }
        )

    upcoming = enrich_ipo_events(upcoming)
    concept_amounts = concept_turnover_map()
    if sector_daily_amount and sector_daily_amount > 0:
        concept_amounts.setdefault("sector_proxy", float(sector_daily_amount))

    drain = compute_drain_ratios(upcoming, concept_amounts)
    ratio = drain.get("liquidity_drain_ratio")
    if ratio is None and sector_daily_amount and drain.get("max_ipo_market_cap"):
        ratio = round(float(drain["max_ipo_market_cap"]) / float(sector_daily_amount), 4)

    return {
        "ipo_today_count": len(upcoming),
        "ipo_events": upcoming[:10],
        "extreme_ipo_day": bool(drain.get("extreme_ipo_day") or len(upcoming) >= 1),
        "liquidity_drain_ratio": ratio,
        "liquidity_drain_ratio_proxy": ratio,
        "max_ipo_market_cap": drain.get("max_ipo_market_cap"),
        "drain_ratios_by_concept": drain.get("drain_ratios_by_concept"),
        "concept_turnover": drain.get("concept_turnover"),
        "note": "IPO 虹吸：概念板块成交额 vs 估算首日市值。",
    }


def build_announcement_snapshot(
    *,
    sector_daily_amount: Optional[float] = None,
    enrich_concept_graph: bool = True,
) -> Dict[str, Any]:
    """公告 + IPO 合并快照。"""
    rows = _safe_notice_rows()
    regulatory = scan_regulatory_notices(rows)
    concept_index: Dict[str, Any] = {}
    if enrich_concept_graph and regulatory.get("active"):
        try:
            from skills.announcement.concept_graph import enrich_regulatory_with_concepts

            regulatory = enrich_regulatory_with_concepts(
                regulatory,
                concept_hints=list(regulatory.get("concept_tags") or []),
            )
            concept_index = dict(regulatory.get("code_concepts") or {})
        except Exception:
            logger.debug("concept graph enrich failed", exc_info=True)
    ipo = build_ipo_drain_metrics(sector_daily_amount=sector_daily_amount)
    as_of = datetime.now().strftime("%Y-%m-%d")
    return {
        "success": True,
        "as_of": as_of,
        "regulatory": regulatory,
        "ipo": ipo,
        "concept_index": concept_index,
        "note": "公告扫描快照；非 PIT，供 prior 层。",
    }
