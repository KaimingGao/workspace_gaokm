"""北向资金 / 概念资金流 / 轻量宏观日历（盘前 Prior Desk，非 PIT）。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.numbers import to_float as _to_float

logger = logging.getLogger(__name__)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def build_northbound_snapshot() -> Dict[str, Any]:
    """北向净流入：优先当日 summary / 分钟末值，否则标 status。"""
    from core.data.ak_lock import import_akshare

    ak = import_akshare()
    errors: List[str] = []
    net_yi: Optional[float] = None  # 亿元
    as_of = _today()
    status = "empty"
    source = ""
    detail: Dict[str, Any] = {}

    # 1) 分时累计（盘中）
    try:
        fn = getattr(ak, "stock_hsgt_fund_min_em", None)
        if fn:
            df = fn()
            if df is not None and getattr(df, "empty", True) is False:
                col = "北向资金" if "北向资金" in df.columns else None
                if col:
                    sub = df[df[col].notna()]
                    if not sub.empty:
                        last = sub.iloc[-1]
                        net_yi = _to_float(last.get(col))
                        as_of = str(last.get("日期") or as_of)[:10]
                        tm = str(last.get("时间") or "")
                        status = "live" if net_yi not in (None, 0.0) else "open_zero"
                        source = "stock_hsgt_fund_min_em"
                        detail = {
                            "time": tm,
                            "hgt": _to_float(last.get("沪股通")),
                            "sgt": _to_float(last.get("深股通")),
                        }
    except Exception:
        logger.debug("northbound minute failed", exc_info=True)
        errors.append("min:fail")

    # 2) 当日 summary（沪+深北向）
    if net_yi is None or status in ("empty", "open_zero"):
        try:
            fn = getattr(ak, "stock_hsgt_fund_flow_summary_em", None)
            if fn:
                df = fn()
                if df is not None and not getattr(df, "empty", True):
                    north = df[df["资金方向"].astype(str) == "北向"] if "资金方向" in df.columns else df
                    vals = []
                    for _, row in north.iterrows():
                        v = _to_float(row.get("资金净流入"))
                        if v is None:
                            v = _to_float(row.get("成交净买额"))
                        if v is not None:
                            vals.append(v)
                        d = row.get("交易日")
                        if d is not None:
                            as_of = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
                    if vals:
                        net_yi = round(sum(vals), 4)
                        status = "summary" if any(abs(v) > 1e-9 for v in vals) else "open_zero"
                        source = source or "stock_hsgt_fund_flow_summary_em"
                        detail["legs"] = vals
        except Exception:
            logger.debug("northbound summary failed", exc_info=True)
            errors.append("summary:fail")

    if net_yi is None:
        status = "unavailable"
        errors.append("no_value")

    return {
        "success": net_yi is not None,
        "as_of": as_of,
        "net_inflow_yi": net_yi,
        "unit": "亿元",
        "status": status,
        "source": source,
        "detail": detail,
        "errors": errors,
        "note": "北向净流入；开盘前常为 0/空，盘中看分时。非 PIT。",
    }


def build_concept_flow_snapshot(*, top_n: int = 10) -> Dict[str, Any]:
    """概念资金流热力：按净额取头部/尾部。"""
    from core.data.ak_lock import import_akshare

    ak = import_akshare()
    top_n = max(4, min(int(top_n or 10), 20))
    rows: List[Dict[str, Any]] = []
    source = ""
    try:
        fn = getattr(ak, "stock_fund_flow_concept", None)
        if not fn:
            return {"success": False, "as_of": _today(), "rows": [], "errors": ["missing_api"]}
        df = fn()
        source = "stock_fund_flow_concept"
        if df is None or getattr(df, "empty", True):
            return {"success": False, "as_of": _today(), "rows": [], "errors": ["empty"], "source": source}
        name_col = "行业" if "行业" in df.columns else df.columns[1]
        net_col = "净额" if "净额" in df.columns else None
        chg_col = "行业-涨跌幅" if "行业-涨跌幅" in df.columns else None
        if not net_col:
            return {"success": False, "as_of": _today(), "rows": [], "errors": ["no_net"], "source": source}
        ranked = df.sort_values(net_col, ascending=False)
        head = ranked.head(top_n)
        tail = ranked.tail(top_n)
        seen = set()
        for part, side in ((head, "in"), (tail, "out")):
            for _, r in part.iterrows():
                name = str(r.get(name_col) or "").strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                net = _to_float(r.get(net_col))
                chg = _to_float(r.get(chg_col)) if chg_col else None
                rows.append(
                    {
                        "concept": name,
                        "net_yi": round(net, 2) if net is not None else None,
                        "chg_pct": round(chg, 2) if chg is not None else None,
                        "side": side if (net or 0) >= 0 else "out",
                    }
                )
        # 统一按净额降序，便于热力条
        rows.sort(key=lambda x: float(x.get("net_yi") or 0), reverse=True)
    except Exception:
        logger.debug("concept flow failed", exc_info=True)
        return {
            "success": False,
            "as_of": _today(),
            "rows": [],
            "errors": ["fail"],
            "source": source,
        }

    return {
        "success": bool(rows),
        "as_of": _today(),
        "rows": rows[: top_n * 2],
        "source": source,
        "note": "概念净流入（亿元）· 东财即时；非 PIT。",
    }


_CAL_KEY_RE = (
    "非农|利率决议|联邦基金|FOMC|CPI|PCE|GDP|失业率|初请失业|续请失业|"
    "PMI|零售销售|耐用品|成屋销售|新屋销售|密歇根|就业|降息|加息"
)
_CAL_NOISE_RE = "持仓|仓单|库存-每日|iShares|SPDR|COMEX黄金库存|白银持仓|黄金持仓"


def _short_event_label(title: str) -> str:
    t = str(title or "")
    if "非农" in t:
        return "非农"
    if "利率决议" in t or "联邦基金" in t or "FOMC" in t.upper() or "美联储利率" in t:
        return "FOMC"
    if "CPI" in t.upper():
        return "CPI"
    if "PCE" in t.upper():
        return "PCE"
    if "初请" in t:
        return "初请"
    if "续请" in t:
        return "续请"
    if "GDP" in t.upper():
        return "GDP"
    if "PMI" in t.upper():
        return "PMI"
    if "耐用品" in t:
        return "耐用品"
    return (t[:8] + "…") if len(t) > 8 else (t or "事件")


def build_macro_calendar_hint() -> Dict[str, Any]:
    """宏观日历：百度财经经济数据 + 美联储利率决议史 + 非农兜底。"""
    from core.data.ak_lock import import_akshare

    ak = import_akshare()
    events: List[Dict[str, Any]] = []
    errors: List[str] = []
    sources: List[str] = []
    today = _today()

    # 1) 百度经济日历：高重要 + 关键词，去持仓噪音
    try:
        fn = getattr(ak, "news_economic_baidu", None)
        if fn:
            df = fn()
            sources.append("news_economic_baidu")
            if df is not None and not getattr(df, "empty", True):
                import re

                key_re = re.compile(_CAL_KEY_RE)
                noise_re = re.compile(_CAL_NOISE_RE)
                for _, row in df.iterrows():
                    title = str(row.get("事件") or "")
                    if not title or noise_re.search(title):
                        continue
                    try:
                        imp = int(float(row.get("重要性") or 0))
                    except (TypeError, ValueError):
                        imp = 0
                    region = str(row.get("地区") or "")
                    if imp < 2 and not key_re.search(title):
                        continue
                    if not key_re.search(title) and "美国" not in region and "中国" not in region:
                        continue
                    if not key_re.search(title) and imp < 3:
                        continue
                    d = row.get("日期")
                    ds = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
                    if not ds:
                        continue
                    events.append(
                        {
                            "key": _short_event_label(title).lower(),
                            "label": _short_event_label(title),
                            "title": title[:80],
                            "date": ds,
                            "time": str(row.get("时间") or "")[:5],
                            "region": region[:12],
                            "importance": imp,
                            "actual": _to_float(row.get("公布")),
                            "forecast": _to_float(row.get("预期")),
                            "previous": _to_float(row.get("前值")),
                            "kind": "upcoming" if ds >= today else "recent",
                            "source": "baidu",
                        }
                    )
    except Exception:
        logger.debug("baidu economic calendar failed", exc_info=True)
        errors.append("baidu:fail")

    # 2) 美联储利率决议史 → 最近一次有效 FOMC
    try:
        fn = getattr(ak, "macro_bank_usa_interest_rate", None)
        if fn:
            df = fn()
            sources.append("macro_bank_usa_interest_rate")
            if df is not None and not getattr(df, "empty", True):
                for _, row in df.iloc[::-1].iterrows():
                    val = _to_float(row.get("今值"))
                    if val is None:
                        continue
                    d = row.get("日期")
                    ds = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
                    events.append(
                        {
                            "key": "fomc",
                            "label": "FOMC",
                            "title": str(row.get("商品") or "美联储利率决议"),
                            "date": ds,
                            "time": "",
                            "region": "美国",
                            "importance": 3,
                            "actual": val,
                            "forecast": _to_float(row.get("预测值")),
                            "previous": _to_float(row.get("前值")),
                            "kind": "last_release",
                            "source": "fed_rate",
                        }
                    )
                    break
    except Exception:
        logger.debug("fed rate calendar failed", exc_info=True)
        errors.append("fed:fail")

    # 3) 非农兜底（若日历里没有）
    if not any(e.get("label") == "非农" for e in events):
        try:
            fn = getattr(ak, "macro_usa_non_farm", None)
            if fn:
                df = fn()
                sources.append("macro_usa_non_farm")
                if df is not None and not getattr(df, "empty", True):
                    date_col = next(
                        (c for c in ("日期", "date", "时间") if c in df.columns),
                        df.columns[0],
                    )
                    last = df.iloc[-1]
                    d = last.get(date_col)
                    ds = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
                    val = None
                    for c in ("今值", "公布值", "非农就业人数", "值"):
                        if c in df.columns:
                            val = _to_float(last.get(c))
                            break
                    events.append(
                        {
                            "key": "nfp",
                            "label": "非农",
                            "title": "美国非农就业",
                            "date": ds,
                            "time": "",
                            "region": "美国",
                            "importance": 3,
                            "actual": val,
                            "forecast": None,
                            "previous": None,
                            "kind": "last_release",
                            "source": "nfp",
                        }
                    )
        except Exception:
            logger.debug("nonfarm calendar failed", exc_info=True)
            errors.append("nfp:fail")

    # 去重：同日同 label 留重要度更高
    dedup: Dict[str, Dict[str, Any]] = {}
    for e in events:
        k = f"{e.get('date')}|{e.get('label')}"
        prev = dedup.get(k)
        if prev is None or int(e.get("importance") or 0) >= int(prev.get("importance") or 0):
            dedup[k] = e
    events = sorted(dedup.values(), key=lambda x: (str(x.get("date") or ""), str(x.get("time") or "")))

    # 优先：未来日程 → 钉住 FOMC/非农 → 其余近端
    upcoming = [e for e in events if str(e.get("date") or "") >= today]
    recent = [e for e in events if str(e.get("date") or "") < today]
    pinned_labs = ("FOMC", "非农", "CPI")
    pinned = []
    for lab in pinned_labs:
        hit = next((e for e in reversed(events) if e.get("label") == lab), None)
        if hit and hit not in pinned:
            pinned.append(hit)
    rest = [e for e in (upcoming + list(reversed(recent))) if e not in pinned]
    picked = (pinned + rest)[:12]

    return {
        "success": bool(picked),
        "as_of": today,
        "events": picked,
        "errors": errors,
        "sources": sources,
        "note": "经济日历·FOMC/非农/CPI 等；百度财经+美联储利率史。非 PIT。",
    }
