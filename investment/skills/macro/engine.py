"""跨市场宏观数据采集：美股科技 / A50 / 汇率 / 美债。"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.numbers import to_float as _to_float
from skills.common.history import fetch_us_daily_bars, normalize_bars

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT_SEC = float(os.environ.get("MACRO_FETCH_TIMEOUT_SEC", "18"))
_YFINANCE_FIRST = os.environ.get("MACRO_YFINANCE_FIRST", "").strip().lower() in (
    "1",
    "true",
    "yes",
)

# 新浪美股/指数（国内网络通常比 Yahoo / 东财美股 hist 更稳）
_SINA_US_SYMBOL = {
    "QQQ": "qqq",
    "KWEB": "kweb",
    "SOXX": "soxx",
}
_SINA_INDEX_SYMBOL = {
    "费城半导体": ".sox",
    "纳斯达克": ".ndx",
}


def _call_with_timeout(fn, *args, timeout: Optional[float] = None, **kwargs):
    """避免 akshare 卡死拖垮 pre_market_ingest。"""
    lim = float(timeout if timeout is not None else _FETCH_TIMEOUT_SEC)
    if lim <= 0:
        return fn(*args, **kwargs)
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(fn, *args, **kwargs)
        try:
            return fut.result(timeout=lim)
        except FuturesTimeoutError:
            logger.warning("macro fetch timeout after %.0fs: %s", lim, getattr(fn, "__name__", fn))
            return None


# 符号别名 → ingest 键
MACRO_SYMBOLS: Dict[str, Tuple[str, str]] = {
    "sox": ("index", "费城半导体"),
    "ndx": ("index", "纳斯达克"),
    "qqq": ("us_etf", "QQQ"),
    "kweb": ("us_etf", "KWEB"),
    "a50": ("futures", "A50"),
    "cnh": ("fx", "USD/CNH"),
    "us10y": ("bond", "US10Y"),
}


def _pct_change(bars: List[dict], days: int = 1) -> Optional[float]:
    if not bars or len(bars) < days + 1:
        return None
    c0 = _to_float(bars[-(days + 1)].get("close"))
    c1 = _to_float(bars[-1].get("close"))
    if c0 is None or c1 is None or c0 == 0:
        return None
    return round((c1 / c0 - 1.0) * 100.0, 4)


def _fetch_yfinance_bars(symbol: str, *, limit: int = 30) -> Tuple[List[dict], str]:
    """Yahoo Finance 备用（akshare 美股/指数不可用时）。"""

    def _pull() -> Tuple[List[dict], str]:
        sym = str(symbol or "").strip()
        if not sym:
            return [], "yfinance_empty"
        try:
            import yfinance as yf
        except ImportError:
            return [], "yfinance_missing"
        df = yf.Ticker(sym).history(period="6mo", interval="1d", auto_adjust=True)
        if df is None or getattr(df, "empty", True):
            return [], "yfinance_empty"
        rows = []
        for idx, row in df.iterrows():
            d = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
            rows.append(
                {
                    "date": d,
                    "open": row.get("Open"),
                    "high": row.get("High"),
                    "low": row.get("Low"),
                    "close": row.get("Close"),
                    "volume": row.get("Volume"),
                }
            )
        bars = normalize_bars(rows)
        if bars:
            return bars[-limit:], f"yfinance:{sym}"
        return [], "yfinance_empty"

    try:
        got = _call_with_timeout(_pull, timeout=min(_FETCH_TIMEOUT_SEC, 12.0))
        if isinstance(got, tuple) and got[0]:
            return got
    except Exception:
        logger.debug("yfinance fetch failed for %s", symbol, exc_info=True)
    return [], "yfinance_empty"


_YF_INDEX_BY_NAME = {
    "费城半导体": ("^SOX", "SOXX"),
    "纳斯达克": ("^NDX", "QQQ"),
}


def _fetch_us_sina_bars(
    symbol: str,
    *,
    limit: int = 30,
    raw: bool = False,
) -> Tuple[List[dict], str]:
    """新浪美股/指数日线（symbol 如 qqq / kweb / .sox）。"""
    if raw:
        sym = str(symbol or "").strip()
    else:
        sym = _SINA_US_SYMBOL.get(str(symbol or "").strip().upper()) or str(
            symbol or ""
        ).strip().lower()
    if not sym:
        return [], "empty"
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()
    fn = getattr(ak, "index_us_stock_sina", None)
    if not fn:
        return [], "missing_api"
    try:
        df = fn(symbol=sym)
        bars = normalize_bars(
            df.to_dict(orient="records") if hasattr(df, "to_dict") else list(df)
        )
        if bars:
            return bars[-limit:], f"index_us_stock_sina:{sym}"
    except Exception:
        logger.debug("index_us_stock_sina failed for %s", sym, exc_info=True)
    return [], "empty"


def _fetch_sox_macro_bars(*, limit: int = 30) -> Tuple[List[dict], str]:
    """东方财富 SOX 宏观序列（费城半导体备用）。"""
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()
    fn = getattr(ak, "macro_global_sox_index", None)
    if not fn:
        return [], "missing_api"
    try:
        df = fn()
        rows = []
        for row in df.to_dict(orient="records"):
            d = row.get("日期")
            close = _to_float(row.get("最新值"))
            if d is None or close is None:
                continue
            ds = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
            rows.append({"date": ds, "close": close})
        bars = normalize_bars(rows)
        if bars:
            return bars[-limit:], "macro_global_sox_index"
    except Exception:
        logger.debug("macro_global_sox_index failed", exc_info=True)
    return [], "empty"


def _fetch_a50_spot_bars(*, limit: int = 30) -> Tuple[List[dict], str]:
    """A50 主力合约现货（futures_global_hist 不可用时）。"""
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()
    fn = getattr(ak, "futures_global_spot_em", None)
    if not fn:
        return [], "missing_api"
    try:
        df = fn()
        if df is None or getattr(df, "empty", True):
            return [], "empty"
        sub = df[df["名称"].astype(str).str.contains("A50期指", na=False)]
        active = sub[sub["最新价"].notna()].copy()
        if active.empty:
            return [], "empty"
        active["_vol"] = active["成交量"].apply(lambda x: _to_float(x) or 0.0)
        row = active.sort_values("_vol", ascending=False).iloc[0]
        close = _to_float(row.get("最新价"))
        settle = _to_float(row.get("昨结"))
        if close is None:
            return [], "empty"
        today = datetime.now().strftime("%Y-%m-%d")
        rows: List[dict] = []
        if settle is not None and settle > 0:
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            rows = [
                {"date": yesterday, "close": settle},
                {"date": today, "close": close},
            ]
        else:
            rows = [{"date": today, "close": close}]
        bars = normalize_bars(rows)
        code = str(row.get("代码") or "")
        if bars:
            return bars[-limit:], f"futures_global_spot_em:{code}"
    except Exception:
        logger.debug("futures_global_spot_em A50 failed", exc_info=True)
    return [], "empty"


def _fetch_cnh_boc(*, limit: int = 30) -> Tuple[List[dict], Optional[float], Optional[float], str]:
    """央行中间价 USD/CNY（CNH 不可达时的流动性 proxy）。"""
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()
    fn = getattr(ak, "currency_boc_safe", None)
    if not fn:
        return [], None, None, "missing_api"
    try:
        df = fn()
        rows = []
        for row in df.to_dict(orient="records"):
            d = row.get("日期")
            usd = _to_float(row.get("美元"))
            if d is None or usd is None:
                continue
            ds = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
            rows.append({"date": ds, "close": float(usd)})
        bars = normalize_bars(rows)
        if not bars:
            return [], None, None, "empty"
        tail = bars[-limit:]
        level = _to_float(tail[-1].get("close"))
        chg = _pct_change(tail, 1)
        return tail, level, chg, "currency_boc_safe:USD/CNY"
    except Exception:
        logger.debug("currency_boc_safe failed", exc_info=True)
    return [], None, None, "empty"


def _fetch_global_index_akshare(name: str, *, limit: int = 30) -> Tuple[List[dict], str]:
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()
    fn = getattr(ak, "index_global_hist_em", None)
    if not fn:
        return [], "missing_api"
    for sym in (name, name.upper(), name.lower()):
        try:
            df = fn(symbol=sym)
            bars = normalize_bars(
                df.to_dict(orient="records") if hasattr(df, "to_dict") else list(df)
            )
            if bars:
                return bars[-limit:], f"index_global_hist_em:{sym}"
        except Exception:
            continue
    return [], "empty"


def _fetch_global_index(name: str, *, limit: int = 30) -> Tuple[List[dict], str]:
    sina_sym = _SINA_INDEX_SYMBOL.get(name)
    if sina_sym:
        got = _call_with_timeout(
            _fetch_us_sina_bars,
            sina_sym,
            limit=limit,
            raw=True,
            timeout=15,
        )
        if isinstance(got, tuple) and got[0]:
            return got
    if name == "费城半导体":
        got = _call_with_timeout(_fetch_sox_macro_bars, limit=limit, timeout=25)
        if isinstance(got, tuple) and got[0]:
            return got
    skip_ak = os.environ.get("MACRO_SKIP_AKSHARE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not skip_ak:
        got = _call_with_timeout(_fetch_global_index_akshare, name, limit=limit, timeout=10)
        if isinstance(got, tuple) and got[0]:
            return got
        if got is None:
            return [], "timeout"
    if _YFINANCE_FIRST:
        for sym in _YF_INDEX_BY_NAME.get(name, ()):
            bars, src = _fetch_yfinance_bars(sym, limit=limit)
            if bars:
                return bars, src
    elif not skip_ak:
        for sym in _YF_INDEX_BY_NAME.get(name, ()):
            bars, src = _fetch_yfinance_bars(sym, limit=limit)
            if bars:
                return bars, src
    if skip_ak:
        return [], "akshare_skipped"
    return [], "empty"


def _fetch_us_etf(symbol: str, *, limit: int = 30) -> Tuple[List[dict], str]:
    sym = str(symbol or "").strip().upper()

    def _akshare_paths() -> Tuple[List[dict], str]:
        got = _call_with_timeout(_fetch_us_sina_bars, sym, limit=limit, timeout=15)
        if isinstance(got, tuple) and got[0]:
            return got
        got = _call_with_timeout(fetch_us_daily_bars, sym, limit=limit + 5, timeout=12)
        bars = got if isinstance(got, list) else []
        if bars:
            return bars[-limit:], f"stock_us_hist:{sym}"
        return [], "empty"

    if _YFINANCE_FIRST:
        bars, src = _fetch_yfinance_bars(sym, limit=limit)
        if bars:
            return bars, src
        return _akshare_paths()
    bars, src = _akshare_paths()
    if bars:
        return bars, src
    bars, src = _fetch_yfinance_bars(sym, limit=limit)
    if bars:
        return bars, src
    return [], "empty"


def _fetch_a50_futures(*, limit: int = 30) -> Tuple[List[dict], str]:
    got = _call_with_timeout(_fetch_a50_spot_bars, limit=limit, timeout=30)
    if isinstance(got, tuple) and got[0]:
        return got
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()
    for fn_name in ("futures_global_hist_em", "futures_foreign_hist"):
        fn = getattr(ak, fn_name, None)
        if not fn:
            continue
        for sym in ("CN00Y", "SGX_CN", "A50", "富时中国A50", "FTSE China A50"):
            try:
                try:
                    df = fn(symbol=sym)
                except TypeError:
                    df = fn(symbol=sym, start_date=(datetime.now() - timedelta(days=120)).strftime("%Y%m%d"))
                bars = normalize_bars(
                    df.to_dict(orient="records") if hasattr(df, "to_dict") else list(df)
                )
                if bars:
                    return bars[-limit:], f"{fn_name}:{sym}"
            except Exception:
                continue
    return [], "empty"


def _fetch_cnh_fx() -> Tuple[Optional[float], Optional[float], str, List[dict]]:
    """返回 (level, overnight_change_pct, source, bars)。"""
    got = _call_with_timeout(_fetch_cnh_boc, limit=45, timeout=12)
    if isinstance(got, tuple) and len(got) >= 4 and got[1] is not None:
        bars, level, chg, src = got
        return level, chg, src, bars
    if _YFINANCE_FIRST:
        bars, src = _fetch_yfinance_bars("USDCNH=X", limit=10)
        if len(bars) >= 2:
            chg = _pct_change(bars, 1)
            return _to_float(bars[-1].get("close")), chg, src, bars
    bars, src = _fetch_yfinance_bars("USDCNH=X", limit=10)
    if len(bars) >= 2:
        chg = _pct_change(bars, 1)
        return _to_float(bars[-1].get("close")), chg, src, bars
    return None, None, "empty", []


def _fetch_us10y_yfinance() -> Tuple[Optional[float], Optional[float], str]:
    bars, src = _fetch_yfinance_bars("^TNX", limit=10)
    if len(bars) >= 2:
        level = _to_float(bars[-1].get("close"))
        chg = _pct_change(bars, 1)
        if level is not None:
            return float(level), chg, src
    return None, None, "yfinance_empty"


def _fetch_us10y_fred() -> Tuple[Optional[float], Optional[float], str]:
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        return None, None, "fred_no_key"
    try:
        import requests

        url = (
            "https://api.stlouisfed.org/fred/series/observations"
            f"?series_id=DGS10&api_key={key}&file_type=json&sort_order=desc&limit=5"
        )
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        obs = (resp.json() or {}).get("observations") or []
        vals = [_to_float(o.get("value")) for o in obs if _to_float(o.get("value")) is not None]
        if not vals:
            return None, None, "fred_empty"
        level = float(vals[0])
        chg = round(level - float(vals[1]), 4) if len(vals) >= 2 else None
        return level, chg, "fred:DGS10"
    except Exception as e:
        logger.debug("FRED fetch failed: %s", e)
        return None, None, f"fred_error:{e}"


def _fetch_us10y_akshare() -> Tuple[Optional[float], Optional[float], str]:
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()
    fn = getattr(ak, "bond_zh_us_rate", None)
    if not fn:
        return None, None, "missing_api"
    try:
        start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
        df = fn(start_date=start)
        if df is None or getattr(df, "empty", True):
            return None, None, "empty"
        records = df.to_dict(orient="records")
        if not records:
            return None, None, "empty"
        last = records[-1]
        prev = records[-2] if len(records) >= 2 else None
        for key in ("10年", "10年期", "10Y", "ten_year"):
            if key in last:
                level = _to_float(last.get(key))
                prev_v = _to_float(prev.get(key)) if prev else None
                chg = round(level - prev_v, 4) if level is not None and prev_v is not None else None
                if level is not None:
                    return float(level), chg, "bond_zh_us_rate"
        # 列名可能是中文完整字段
        for col in last:
            if "10" in str(col) and "年" in str(col):
                level = _to_float(last.get(col))
                prev_v = _to_float(prev.get(col)) if prev else None
                chg = round(level - prev_v, 4) if level is not None and prev_v is not None else None
                if level is not None:
                    return float(level), chg, "bond_zh_us_rate"
    except Exception:
        logger.debug("bond_zh_us_rate failed", exc_info=True)
    return None, None, "empty"


def _series_payload(
    key: str,
    bars: List[dict],
    *,
    source: str,
    level: Optional[float] = None,
    change_1d_pct: Optional[float] = None,
    history_bars: int = 45,
) -> Dict[str, Any]:
    chg1 = change_1d_pct if change_1d_pct is not None else _pct_change(bars, 1)
    chg5 = _pct_change(bars, min(5, max(len(bars) - 1, 1)))
    close = level
    if close is None and bars:
        close = _to_float(bars[-1].get("close"))
    recent_bars = [
        {"date": str(b.get("date") or "")[:10], "close": _to_float(b.get("close"))}
        for b in (bars or [])[-max(5, int(history_bars)) :]
        if b.get("date") is not None and _to_float(b.get("close")) is not None
    ]
    return {
        "key": key,
        "close": close,
        "change_1d_pct": chg1,
        "change_5d_pct": chg5,
        "bars_count": len(bars or []),
        "source": source,
        "recent_bars": recent_bars,
    }


def build_macro_snapshot(*, lookback: int = 30) -> Dict[str, Any]:
    """拉取跨市场序列并汇总为盘前 macro 快照。"""
    lookback = max(5, min(int(lookback or 30), 120))
    series: Dict[str, Any] = {}
    errors: List[str] = []

    # SOX / NDX via global index; fallback SOXX ETF
    for key, idx_name in (("sox", "费城半导体"), ("ndx", "纳斯达克")):
        got = _call_with_timeout(_fetch_global_index, idx_name, limit=lookback)
        bars, src = got if isinstance(got, tuple) else ([], "timeout")
        if not bars and key == "sox":
            got = _call_with_timeout(_fetch_us_etf, "SOXX", limit=lookback)
            bars, src = got if isinstance(got, tuple) else ([], "timeout")
        if not bars and key == "ndx":
            got = _call_with_timeout(_fetch_us_etf, "QQQ", limit=lookback)
            bars, src = got if isinstance(got, tuple) else ([], "timeout")
        if bars:
            series[key] = _series_payload(key, bars, source=src)
        else:
            errors.append(f"{key}:{src}")

    got = _call_with_timeout(_fetch_us_etf, "QQQ", limit=lookback)
    bars, src = got if isinstance(got, tuple) else ([], "timeout")
    if bars:
        series["qqq"] = _series_payload("qqq", bars, source=src)

    got = _call_with_timeout(_fetch_us_etf, "KWEB", limit=lookback)
    bars, src = got if isinstance(got, tuple) else ([], "timeout")
    if bars:
        series["kweb"] = _series_payload("kweb", bars, source=src)
    else:
        errors.append(f"kweb:{src}")

    got = _call_with_timeout(_fetch_a50_futures, limit=lookback)
    bars, src = got if isinstance(got, tuple) else ([], "timeout")
    if bars:
        series["a50"] = _series_payload("a50", bars, source=src)
    else:
        errors.append(f"a50:{src}")

    cnh_got = _call_with_timeout(_fetch_cnh_fx) or (None, None, "timeout", [])
    cnh_level, cnh_chg, cnh_src, cnh_bars = cnh_got
    if cnh_bars:
        series["cnh"] = _series_payload(
            "cnh",
            cnh_bars[-lookback:],
            source=cnh_src,
            level=cnh_level,
            change_1d_pct=cnh_chg,
        )
    else:
        series["cnh"] = {
            "key": "cnh",
            "close": cnh_level,
            "change_1d_pct": cnh_chg,
            "source": cnh_src,
            "recent_bars": [],
        }
    if cnh_level is None:
        errors.append(f"cnh:{cnh_src}")

    us10y, us10y_chg, us10y_src = None, None, "skipped"
    if _YFINANCE_FIRST:
        us10y, us10y_chg, us10y_src = _fetch_us10y_yfinance()
    if us10y is None:
        us10y, us10y_chg, us10y_src = _call_with_timeout(_fetch_us10y_fred, timeout=8) or (
            None,
            None,
            "fred_no_key",
        )
    if us10y is None and os.environ.get("MACRO_SKIP_AKSHARE", "").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        us10y, us10y_chg, us10y_src = _call_with_timeout(_fetch_us10y_akshare, timeout=8) or (
            None,
            None,
            "timeout",
        )
    if us10y is None and not _YFINANCE_FIRST:
        us10y, us10y_chg, us10y_src = _fetch_us10y_yfinance()
    series["us10y"] = {
        "key": "us10y",
        "close": us10y,
        "change_1d_pct": us10y_chg,
        "source": us10y_src,
    }
    if us10y is None:
        errors.append(f"us10y:{us10y_src}")

    # 聚合海外科技拖累得分（-100 ~ 0，越负越利空 A 股科技）
    tech_rets = [
        series.get("sox", {}).get("change_1d_pct"),
        series.get("ndx", {}).get("change_1d_pct"),
        series.get("qqq", {}).get("change_1d_pct"),
        series.get("kweb", {}).get("change_1d_pct"),
    ]
    tech_vals = [float(x) for x in tech_rets if x is not None]
    overseas_tech_1d = round(sum(tech_vals) / len(tech_vals), 4) if tech_vals else None

    liquidity_stress = 0.0
    cnh_c = series.get("cnh", {}).get("change_1d_pct")
    if cnh_c is not None and float(cnh_c) > 0.15:
        liquidity_stress += 1.0
    if us10y_chg is not None and float(us10y_chg) > 0.05:
        liquidity_stress += 1.0

    a50_1d = series.get("a50", {}).get("change_1d_pct")
    # Lead-lag proxy：海外科技隔夜均涨 × 经验 beta → A 股科技开盘缺口预期
    lead_lag_beta = 0.55
    lead_lag_expected_gap = (
        round(float(overseas_tech_1d) * lead_lag_beta, 4)
        if overseas_tech_1d is not None
        else None
    )

    as_of = datetime.now().strftime("%Y-%m-%d")
    # 延迟导入，避免与 market_context 编排形成热重载半加载 ImportError
    from core.market_context_merge import prune_macro_errors

    return {
        "success": bool(series),
        "as_of": as_of,
        "series": series,
        "overseas_tech_1d_pct": overseas_tech_1d,
        "a50_1d_pct": a50_1d,
        "lead_lag_expected_gap_pct": lead_lag_expected_gap,
        "lead_lag_beta": lead_lag_beta,
        "liquidity_stress_score": liquidity_stress,
        "errors": prune_macro_errors(errors, series),
        "note": "跨市场宏观快照；非 PIT，仅供盘前 prior / regime 叠加。",
    }
