"""基本面数据拉取与字段归一（便于 mock）。"""


from typing import Any, Dict, List, Optional

from core.ports.market import query_quote, resolve_market_code
from skills.screen.engine import _normalize_rows, _row_get, _to_float


def _records(df_or_rows: Any) -> List[dict]:
    return _normalize_rows(df_or_rows)


def fetch_cn_spot_row(code: str) -> Optional[dict]:
    """从 A 股现货表中取单行（含动态 PE/PB 等）。优先本地/内存现货，避免每票重拉全表。"""
    from skills.screen.engine import fetch_a_spot, load_disk_spot, spot_row_get

    code = str(code).zfill(6)
    rows = load_disk_spot(max_age_hours=24 * 14) or []
    if not rows:
        try:
            rows = list(fetch_a_spot() or [])
        except Exception:
            logger.exception('unexpected error in fetch_cn_spot_row')
            rows = []
    for row in rows:
        c = str(spot_row_get(row, "code") or "").zfill(6)
        if c == code:
            return row
    return None


def fetch_cn_valuation_latest(code: str) -> Dict[str, Any]:
    """估值最新一条：乐咕优先；缺失时回退东方财富 ``stock_value_em``（含 PE/PB）。"""
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()
    code = str(code).zfill(6)

    fn = getattr(ak, "stock_a_lg_indicator", None) or getattr(ak, "stock_a_indicator_lg", None)
    if fn is not None:
        try:
            df = fn(stock=code)
            rows = _records(df)
            if rows:
                latest = rows[-1]
                for cand in (rows[-1], rows[0]):
                    if _to_float(cand.get("pe_ttm") or cand.get("pe")) is not None:
                        latest = cand
                        break
                pe = _to_float(latest.get("pe"))
                pe_ttm = _to_float(latest.get("pe_ttm"))
                pb = _to_float(latest.get("pb"))
                if pe is not None or pe_ttm is not None or pb is not None:
                    return {
                        "pe": pe,
                        "pe_ttm": pe_ttm,
                        "pb": pb,
                        "ps_ttm": _to_float(latest.get("ps_ttm")),
                        "dv_ttm": _to_float(latest.get("dv_ttm")),
                        "total_mv": _to_float(latest.get("total_mv")),
                        "as_of": str(
                            latest.get("trade_date") or latest.get("日期") or ""
                        ),
                        "source": "akshare_lg_indicator",
                    }
        except Exception:
            logger.exception('unexpected error in fetch_cn_valuation_latest')

    # 乐咕不可用 / 东财全表现货挂掉时：单票估值序列仍常可用
    try:
        df = ak.stock_value_em(symbol=code)
        rows = _records(df)
        if not rows:
            return {}
        latest = rows[-1]
        pe_ttm = _to_float(
            latest.get("PE(TTM)")
            or latest.get("PE_TTM")
            or latest.get("pe_ttm")
        )
        pe_static = _to_float(
            latest.get("PE(静)") or latest.get("PE") or latest.get("pe")
        )
        pb = _to_float(latest.get("市净率") or latest.get("pb"))
        return {
            "pe": pe_ttm if pe_ttm is not None else pe_static,
            "pe_ttm": pe_ttm,
            "pb": pb,
            "ps_ttm": _to_float(latest.get("市销率") or latest.get("ps_ttm")),
            "dv_ttm": None,
            "total_mv": _to_float(latest.get("总市值") or latest.get("total_mv")),
            "as_of": str(latest.get("数据日期") or latest.get("date") or "")[:10],
            "source": "akshare_value_em",
        }
    except Exception:
        logger.exception('unexpected error in fetch_cn_valuation_latest')
        return {}


def _extract_ann_date(row: dict) -> str:
    """从财务行尽量取公告/披露日。"""
    if not isinstance(row, dict):
        return ""
    for key in (
        "公告日期",
        "公告日",
        "披露日期",
        "ann_date",
        "announce_date",
        "pub_date",
        "notice_date",
    ):
        raw = row.get(key)
        if raw is None:
            continue
        if hasattr(raw, "isoformat"):
            s = str(raw.isoformat())[:10]
        else:
            s = str(raw).strip().replace("/", "-")[:10]
        if len(s) == 8 and s.isdigit():
            s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return s[:10]
    return ""


def _pick_financial_row(row: dict) -> Dict[str, Any]:
    """从财务指标表单行抽取归一字段。"""

    def pick(*keys):
        for k in keys:
            if k in row and row[k] not in (None, "", "-", "--"):
                return _to_float(row[k])
        return None

    roe = pick("净资产收益率(%)", "ROE", "roe")
    revenue_growth = pick("主营业务收入增长率(%)", "营业收入同比增长率(%)", "revenue_growth")
    profit_growth = pick("净利润增长率(%)", "归属母公司净利润同比增长率(%)", "profit_growth")
    eps = pick("摊薄每股收益(元)", "每股收益", "eps")
    raw_date = row.get("日期") or row.get("date") or ""
    if hasattr(raw_date, "isoformat"):
        as_of = str(raw_date.isoformat())[:10]
    else:
        as_of = str(raw_date)[:10]

    ann = _extract_ann_date(row)
    out = {
        "roe": roe,
        "revenue_growth": revenue_growth,
        "profit_growth": profit_growth,
        "eps": eps,
        "as_of": as_of,
        "source": "akshare_financial_indicator",
    }
    if ann:
        out["ann_date"] = ann
        out["available_as_of"] = ann
    else:
        out["ann_missing"] = True
    return out


def _parse_pct_or_float(val: Any) -> Optional[float]:
    if val is None or val is False:
        return None
    if isinstance(val, (int, float)):
        return _to_float(val)
    text = str(val).strip().replace(",", "").replace("%", "")
    if text in ("", "-", "--", "False", "None", "nan", "NaN"):
        return None
    return _to_float(text)


def _pick_financial_row_ths(row: dict) -> Dict[str, Any]:
    """同花顺财务摘要行 → 归一字段。"""
    raw_date = row.get("报告期") or row.get("date") or ""
    if hasattr(raw_date, "isoformat"):
        as_of = str(raw_date.isoformat())[:10]
    else:
        as_of = str(raw_date)[:10].replace("/", "-")
    if len(as_of) == 8 and as_of.isdigit():
        as_of = f"{as_of[:4]}-{as_of[4:6]}-{as_of[6:]}"
    ann = _extract_ann_date(row)
    out = {
        "roe": _parse_pct_or_float(row.get("净资产收益率") or row.get("净资产收益率-摊薄")),
        "revenue_growth": _parse_pct_or_float(row.get("营业总收入同比增长率")),
        "profit_growth": _parse_pct_or_float(row.get("净利润同比增长率")),
        "eps": _parse_pct_or_float(row.get("基本每股收益")),
        "as_of": as_of,
        "source": "akshare_financial_abstract_ths",
    }
    if ann:
        out["ann_date"] = ann
        out["available_as_of"] = ann
    else:
        out["ann_missing"] = True
    return out


def _fetch_cn_financial_rows(code: str) -> List[dict]:
    """拉取 A 股财务分析指标全表行（升序，旧→新）。主接口空则回退同花顺摘要。"""
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()
    code = str(code).zfill(6)
    df = ak.stock_financial_analysis_indicator(symbol=code)
    rows = _records(df)
    if not rows:
        try:
            df = ak.stock_financial_analysis_indicator(stock=code)
            rows = _records(df)
        except Exception:
            logger.exception('unexpected error in _fetch_cn_financial_rows')
            rows = []
    if rows:
        for r in rows:
            r["_fin_source"] = "indicator"
        return rows

    # 回退：同花顺财务摘要（多码主接口返回空表时仍可用）
    fn = getattr(ak, "stock_financial_abstract_ths", None)
    if fn is None:
        return []
    try:
        df = fn(symbol=code)
    except Exception:
        logger.exception('unexpected error in _fetch_cn_financial_rows')
        try:
            df = fn(stock=code)
        except Exception:
            logger.exception('unexpected error in _fetch_cn_financial_rows')
            return []
    rows = _records(df)
    for r in rows:
        r["_fin_source"] = "ths_abstract"
    return rows or []


def fetch_cn_financial_latest(code: str) -> Dict[str, Any]:
    """财务分析指标取最近一期。"""
    rows = _fetch_cn_financial_rows(code)
    if not rows:
        return {}
    latest = rows[-1]
    if latest.get("_fin_source") == "ths_abstract":
        return _pick_financial_row_ths(latest)
    return _pick_financial_row(latest)


def fetch_cn_financial_series(
    code: str,
    *,
    max_points: int = 12,
) -> List[Dict[str, Any]]:
    """财务分析指标多期序列（真实报告期），供 PIT history 入库。

    返回按 as_of 升序的点；不含 synthetic_demo。max_points 取最近 N 期。
    """
    rows = _fetch_cn_financial_rows(code)
    if not rows:
        return []
    n = max(1, min(40, int(max_points or 12)))
    selected = rows[-n:] if len(rows) > n else rows
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for row in selected:
        if row.get("_fin_source") == "ths_abstract":
            parsed = _pick_financial_row_ths(row)
        else:
            parsed = _pick_financial_row(row)
        as_of = str(parsed.get("as_of") or "")[:10]
        if not as_of or as_of in seen:
            continue
        if all(parsed.get(k) is None for k in ("roe", "profit_growth", "revenue_growth", "eps")):
            continue
        seen.add(as_of)
        out.append(parsed)
    out.sort(key=lambda p: str(p.get("as_of") or ""))
    return out


def spot_to_metrics(row: dict) -> Dict[str, Any]:
    return {
        "price": _to_float(_row_get(row, "price")),
        "change": _to_float(_row_get(row, "change")),
        "pe": _to_float(_row_get(row, "pe")),
        "pb": _to_float(_row_get(row, "pb")),
        "market_cap": _row_get(row, "market_cap"),
        "volume": _row_get(row, "volume"),
        "source": "akshare_spot_em",
    }

import logging

logger = logging.getLogger(__name__)


def fetch_hk_spot_row(code: str) -> Optional[dict]:
    """港股现货表中取单行（PE/市值等，字段随 akshare 版本变化）。"""
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()

    bare = "".join(ch for ch in str(code) if ch.isdigit())
    targets = {bare, bare.zfill(5), bare.lstrip("0") or bare}

    for fn_name in ("stock_hk_spot_em", "stock_hk_main_board_spot_em", "stock_hk_spot"):
        fn = getattr(ak, fn_name, None)
        if not fn:
            continue
        try:
            df = fn()
        except Exception:
            logger.exception('unexpected error in fetch_hk_spot_row')
            continue
        rows = _records(df)
        for row in rows:
            c = str(
                _row_get(row, "code")
                or row.get("代码")
                or row.get("股票代码")
                or row.get("symbol")
                or ""
            )
            c_digits = "".join(ch for ch in c if ch.isdigit())
            if c_digits in targets or c_digits.zfill(5) in {t.zfill(5) for t in targets}:
                return row
    return None


def hk_spot_to_metrics(row: dict) -> Dict[str, Any]:
    pe = _to_float(
        row.get("市盈率")
        or row.get("市盈率-动态")
        or row.get("pe")
        or _row_get(row, "pe")
    )
    pb = _to_float(row.get("市净率") or row.get("pb") or _row_get(row, "pb"))
    mv = (
        row.get("总市值")
        or row.get("市值")
        or row.get("HK$总市值")
        or _row_get(row, "market_cap")
    )
    return {
        "pe": pe,
        "pb": pb,
        "market_cap": mv,
        "turnover": _to_float(row.get("成交额") or row.get("turnover")),
        "amplitude": _to_float(row.get("振幅") or row.get("amplitude")),
        "source": "akshare_hk_spot",
    }


def build_fundamentals(stock_code: str) -> dict:
    market, code = resolve_market_code(stock_code)
    quote = query_quote(stock_code)
    name = quote.get("stock_name") if quote.get("success") else stock_code
    display_code = quote.get("stock_code") if quote.get("success") else (code or stock_code)

    metrics: Dict[str, Any] = {
        "stock_code": display_code,
        "stock_name": name,
        "market": market,
        "price": quote.get("price") if quote.get("success") else None,
        "change": quote.get("change") if quote.get("success") else None,
    }
    sources = []
    notes = []

    if market == "CN" and code:
        try:
            row = fetch_cn_spot_row(code)
            if row:
                spot = spot_to_metrics(row)
                metrics.update(
                    {
                        "pe": spot.get("pe"),
                        "pb": spot.get("pb"),
                        "market_cap": spot.get("market_cap"),
                    }
                )
                sources.append(spot["source"])
        except Exception as e:
            logger.exception('unexpected error in build_fundamentals')
            notes.append(f"现货估值拉取失败: {e}")

        try:
            val = fetch_cn_valuation_latest(code)
            if val:
                # spot 优先；缺失再用 lg
                for k in ("pe", "pb"):
                    if metrics.get(k) is None and val.get(k) is not None:
                        metrics[k] = val.get(k)
                if metrics.get("pe") is None and val.get("pe_ttm") is not None:
                    metrics["pe"] = val.get("pe_ttm")
                if val.get("pe_ttm") is not None:
                    metrics["pe_ttm"] = val.get("pe_ttm")
                if val.get("dv_ttm") is not None:
                    metrics["dividend_yield"] = val.get("dv_ttm")
                if metrics.get("market_cap") is None and val.get("total_mv") is not None:
                    metrics["market_cap"] = val.get("total_mv")
                if val.get("as_of"):
                    metrics["valuation_as_of"] = val["as_of"]
                sources.append(val["source"])
        except Exception as e:
            logger.exception('unexpected error in build_fundamentals')
            notes.append(f"乐咕估值拉取失败: {e}")

        try:
            fin = fetch_cn_financial_latest(code)
            if fin:
                metrics["roe"] = fin.get("roe")
                metrics["revenue_growth"] = fin.get("revenue_growth")
                metrics["profit_growth"] = fin.get("profit_growth")
                metrics["eps"] = fin.get("eps")
                metrics["financial_as_of"] = fin.get("as_of")
                sources.append(fin["source"])
        except Exception as e:
            logger.exception('unexpected error in build_fundamentals')
            notes.append(f"财务指标拉取失败: {e}")
    elif market == "HK":
        if quote.get("success"):
            metrics["price_raw"] = quote.get("price_raw")
            metrics["change_raw"] = quote.get("change_raw")
            sources.append("tencent_quote")
        try:
            row = fetch_hk_spot_row(code or display_code or stock_code)
            if row:
                hk = hk_spot_to_metrics(row)
                for k in ("pe", "pb", "market_cap", "turnover", "amplitude"):
                    if hk.get(k) is not None:
                        metrics[k] = hk.get(k)
                sources.append(hk["source"])
            else:
                notes.append("港股现货表未匹配到该代码，估值字段可能缺失")
        except Exception as e:
            logger.exception('unexpected error in build_fundamentals')
            notes.append(f"港股现货估值拉取失败: {e}")
        if metrics.get("pe") is None and metrics.get("pb") is None:
            notes.append("港股 ROE/增速等深度财务暂未接入；已尽量提供 PE/市值/行情")
    else:
        notes.append("当前基本面深度数据以 A 股为主；美股等市场字段有限")
        if quote.get("success"):
            metrics["price_raw"] = quote.get("price_raw")
            sources.append("tencent_quote")

    # 简单可解释点评
    highlights = []
    pe = metrics.get("pe") or metrics.get("pe_ttm")
    pb = metrics.get("pb")
    roe = metrics.get("roe")
    if pe is not None:
        if pe <= 0:
            highlights.append("市盈率为负或无效，盈利端需单独核对")
        elif pe < 15:
            highlights.append(f"动态/现货 PE 约 {pe:.2f}，相对偏低区间（需结合行业）")
        elif pe > 40:
            highlights.append(f"PE 约 {pe:.2f}，估值偏高区间（需结合成长）")
        else:
            highlights.append(f"PE 约 {pe:.2f}")
    if pb is not None and pb > 0:
        highlights.append(f"PB 约 {pb:.2f}")
    if roe is not None:
        highlights.append(f"ROE 约 {roe:.2f}%")
    if metrics.get("revenue_growth") is not None:
        highlights.append(f"营收增速约 {metrics['revenue_growth']:.2f}%")
    if metrics.get("profit_growth") is not None:
        highlights.append(f"净利增速约 {metrics['profit_growth']:.2f}%")
    if metrics.get("market_cap") is not None:
        highlights.append(f"市值约 {metrics['market_cap']}")
    if market == "HK" and quote.get("success") and not highlights:
        highlights.append(
            f"现价 {quote.get('price')}，当日 {quote.get('change')}（港股深度财务有限，建议结合 peer/kline）"
        )

    has_core = any(
        metrics.get(k) is not None for k in ("pe", "pb", "roe", "pe_ttm", "price_raw", "price")
    )
    if not has_core:
        return {
            "success": False,
            "stock_code": display_code,
            "stock_name": name,
            "market": market,
            "error": "未能获取有效基本面/行情字段",
            "notes": notes,
        }

    return {
        "success": True,
        "stock_code": display_code,
        "stock_name": name,
        "market": market,
        "coverage": "full" if pe is not None or roe is not None else "partial",
        "metrics": metrics,
        "highlights": highlights,
        "sources": sources,
        "notes": notes,
        "note": "以上为基本面事实摘要，供投顾建议使用；市场有风险，不保证收益，不代客下单。",
    }
