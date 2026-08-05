"""日线数据获取：A 股 / 港股 / 美股，多接口重试；失败可用 quote 退化。"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Callable, List, Optional, Tuple

from core.store import load_daily_cache, save_daily_cache
from skills.common.quote_api import StockAPI


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def normalize_bars(rows: List[dict]) -> List[dict]:
    """统一日线字段为 date/open/high/low/close/volume。"""
    bars = []
    for row in rows or []:
        date = row.get("date") or row.get("日期") or row.get("time") or row.get("时间")
        close = _to_float(
            row.get("close")
            or row.get("收盘")
            or row.get("收盘价")
            or row.get("最新价")
        )
        if close is None:
            continue
        bars.append(
            {
                "date": str(date) if date is not None else "",
                "open": _to_float(row.get("open") or row.get("开盘") or row.get("开盘价"))
                or close,
                "high": _to_float(row.get("high") or row.get("最高") or row.get("最高价"))
                or close,
                "low": _to_float(row.get("low") or row.get("最低") or row.get("最低价"))
                or close,
                "close": close,
                "volume": _to_float(
                    row.get("volume") or row.get("成交量") or row.get("成交额")
                )
                or 0.0,
            }
        )
    bars.sort(key=lambda x: x["date"])
    return bars


def resolve_market_code(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """
    返回 (market, code)：
    - market: CN / HK / US / None
    - code: 拉取日线用的裸代码（A 股 6 位，港股 5 位等）
    """
    symbol = StockAPI.resolve_symbol(raw)
    if not symbol:
        text = (raw or "").strip()
        if text.isdigit() and len(text) == 6:
            return "CN", text
        if text.isdigit() and 4 <= len(text) <= 5:
            return "HK", text.zfill(5)
        return None, None

    s = symbol.lower()
    if s.startswith(("sh", "sz")):
        return "CN", s[2:]
    if s.startswith("hk"):
        return "HK", s[2:].zfill(5)
    if s.startswith("us"):
        return "US", s[2:].upper()
    return None, None


def _df_to_bars(df: Any, limit: int) -> List[dict]:
    if df is None or getattr(df, "empty", True):
        return []
    records = df.to_dict(orient="records") if hasattr(df, "to_dict") else list(df)
    bars = normalize_bars(records)
    return bars[-limit:] if limit else bars


def fetch_a_daily_bars(code: str, limit: int = 30, *, adjust: str = "qfq") -> List[dict]:
    """
    拉取 A 股日线。code 可为 600519 / sh600519。
    adjust: qfq | raw | hfq（hfq 不可用时回退 qfq）。
    """
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()

    raw = str(code).strip().lower()
    prefix = ""
    if raw.startswith(("sh", "sz")):
        prefix = raw[:2]
        raw = raw[2:]
    if not (raw.isdigit() and len(raw) == 6):
        raise ValueError(f"仅支持 6 位 A 股代码拉取日线: {code}")

    if not prefix:
        prefix = "sh" if raw.startswith(("5", "6", "9")) else "sz"

    end = datetime.now()
    start = end - timedelta(days=max(90, limit * 4))
    start_s = start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")

    policy = str(adjust or "qfq").strip().lower()
    if policy in ("none", "unadjusted", ""):
        policy = "raw"
    if policy == "hfq":
        adjust_order = ("hfq", "qfq", "")
    elif policy == "raw":
        adjust_order = ("",)
    else:
        adjust_order = ("qfq", "")

    attempts: List[Tuple[str, Callable[[], Any]]] = []

    for adj in adjust_order:
        def _hist(a=adj):
            return ak.stock_zh_a_hist(
                symbol=raw,
                period="daily",
                start_date=start_s,
                end_date=end_s,
                adjust=a,
            )

        attempts.append((f"stock_zh_a_hist:{adj or 'none'}", _hist))

    # 新浪：需要 sh/sz 前缀
    daily_fn = getattr(ak, "stock_zh_a_daily", None)
    if daily_fn:
        for sym in (f"{prefix}{raw}", raw):
            def _daily(s=sym):
                try:
                    return daily_fn(symbol=s)
                except TypeError:
                    return daily_fn(s)

            attempts.append((f"stock_zh_a_daily:{sym}", _daily))

    # 腾讯等备用（若本机 akshare 版本提供）
    for fn_name in ("stock_zh_a_hist_tx", "stock_zh_a_hist_em"):
        fn = getattr(ak, fn_name, None)
        if not fn:
            continue

        def _alt(f=fn, name=fn_name):
            try:
                return f(symbol=raw, start_date=start_s, end_date=end_s)
            except TypeError:
                try:
                    return f(symbol=raw)
                except TypeError:
                    return f(raw)

        attempts.append((fn_name, _alt))

    errors: List[str] = []
    for label, call in attempts:
        try:
            df = call()
            bars = _df_to_bars(df, limit)
            if bars:
                return bars
        except Exception as e:
            errors.append(f"{label}: {e}")
            continue

    if errors:
        fetch_a_daily_bars.last_errors = errors[-6:]  # type: ignore[attr-defined]
    return []


def fetch_hk_daily_bars(code: str, limit: int = 30) -> List[dict]:
    """
    拉取港股日线。多符号形态 + 多接口重试。
    注意：单次接口异常应 continue，不要整段中断。
    """
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()

    raw = str(code).strip().lower()
    if raw.startswith("hk"):
        raw = raw[2:]
    raw = "".join(ch for ch in raw if ch.isdigit())
    if not raw:
        return []

    candidates = []
    for form in (raw.zfill(5), raw.lstrip("0") or raw, raw):
        if form and form not in candidates:
            candidates.append(form)

    end = datetime.now()
    start = end - timedelta(days=max(120, limit * 4))
    start_s = start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")

    attempts: List[Tuple[str, Callable[[], Any]]] = []

    def _add_hist(symbol: str, adjust: str) -> None:
        def _call(sym=symbol, adj=adjust):
            return ak.stock_hk_hist(
                symbol=sym,
                period="daily",
                start_date=start_s,
                end_date=end_s,
                adjust=adj,
            )

        attempts.append((f"stock_hk_hist:{symbol}:{adjust or 'none'}", _call))

    for sym in candidates:
        _add_hist(sym, "")
        _add_hist(sym, "qfq")

    # 东方财富港股日线（若存在）
    for fn_name in ("stock_hk_hist_em", "stock_hk_daily"):
        fn = getattr(ak, fn_name, None)
        if not fn:
            continue
        for sym in candidates:
            def _call(f=fn, s=sym, name=fn_name):
                try:
                    return f(symbol=s)
                except TypeError:
                    return f(s)

            attempts.append((f"{fn_name}:{sym}", _call))

    errors: List[str] = []
    for label, call in attempts:
        try:
            df = call()
            bars = _df_to_bars(df, limit)
            if bars:
                return bars
        except Exception as e:
            errors.append(f"{label}: {e}")
            continue

    if errors:
        # 留给上层 notes；此处仍返回空列表
        fetch_hk_daily_bars.last_errors = errors[-5:]  # type: ignore[attr-defined]
    return []


def fetch_us_daily_bars(code: str, limit: int = 30) -> List[dict]:
    """拉取美股日线。code 如 AAPL。"""
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()

    symbol = str(code).strip().upper()
    if symbol.startswith("US"):
        symbol = symbol[2:]

    end = datetime.now()
    start = end - timedelta(days=max(90, limit * 3))
    start_s = start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")

    fn = getattr(ak, "stock_us_hist", None)
    if not fn:
        return []

    for kwargs in (
        {
            "symbol": symbol,
            "period": "daily",
            "start_date": start_s,
            "end_date": end_s,
            "adjust": "",
        },
        {"symbol": symbol, "start_date": start_s, "end_date": end_s},
        {"symbol": symbol},
    ):
        try:
            df = fn(**kwargs)
            bars = _df_to_bars(df, limit)
            if bars:
                return bars
        except TypeError:
            continue
        except Exception:
            continue
    return []


def fetch_daily_bars(
    stock_code: str,
    limit: int = 30,
    *,
    use_cache: bool = True,
    cache_max_age_hours: float = 24.0,
    incremental: bool = True,
    adjust: str = "qfq",
    offline_ok: bool = False,
) -> Tuple[List[dict], str]:
    """
    按市场拉取日线（带本地缓存 data/store/daily/）。
    返回 (bars, data_source)：
    data_source: cache:akshare_* / akshare_cn_daily / akshare_hk_daily / empty

    incremental=True：若本地已有 bars，则拉更大窗口后按 date 合并再落盘（观察池轻本地史）。
    adjust：qfq|raw|hfq（写入缓存 adjust_policy；与请求不一致则跳过缓存防混用）。
    offline_ok=True：本地有足够 bars 时直接返回（可过期），不打远端——研究分组用。
    """
    from core.store import merge_bars_by_date, peek_daily_cache_meta

    market, code = resolve_market_code(stock_code)
    policy = str(adjust or "qfq").strip().lower()
    if policy in ("none", "unadjusted", ""):
        policy = "raw"
    if policy not in ("qfq", "raw", "hfq"):
        policy = "qfq"

    if not market or not code:
        return [], "empty"

    disable_cache = os.environ.get("INVESTMENT_DISABLE_CACHE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    existing: List[dict] = []
    cached_src = "cache"
    if use_cache and not disable_cache:
        # D2：缓存 adjust_policy 与请求不一致则跳过，防 qfq/raw 混用
        meta_peek = None
        try:
            meta_peek = peek_daily_cache_meta(market, code)
        except Exception:
            meta_peek = None
        cached_adj = str((meta_peek or {}).get("adjust_policy") or "qfq").lower()
        cache_ok = cached_adj == policy or (
            policy == "qfq" and cached_adj in ("qfq", "cached", "")
        )
        if cache_ok:
            cached = load_daily_cache(
                market,
                code,
                min_bars=min(5, limit),
                max_age_hours=cache_max_age_hours,
            )
            if cached:
                bars, meta = cached
                src = meta.get("data_source") or "cache"
                if src and not str(src).startswith("cache:"):
                    src = f"cache:{src}"
                cached_src = str(src)
                if len(bars) >= limit:
                    trimmed = bars[-limit:] if limit and len(bars) > limit else bars
                    return trimmed, str(src)
                existing = list(bars)

            if (incremental or offline_ok) and not existing:
                stale = load_daily_cache(
                    market,
                    code,
                    min_bars=1,
                    max_age_hours=0,
                    ignore_age=True,
                )
                if stale:
                    existing = list(stale[0] or [])
                    cached_src = "cache:stale"

    # 研究路径：本地够用就不打 AkShare（避免 100 票并行挂死）
    min_offline = max(20, min(int(limit or 30), 40))
    if offline_ok and existing and len(existing) >= min_offline:
        trimmed = existing[-limit:] if limit and len(existing) > limit else existing
        return trimmed, cached_src

    fetch_limit = max(int(limit or 30), len(existing) + 5 if existing else int(limit or 30))
    if incremental and existing:
        fetch_limit = max(fetch_limit, int(limit or 30) + 10)

    try:
        if market == "CN":
            bars = fetch_a_daily_bars(code, limit=fetch_limit, adjust=policy)
            src = f"akshare_cn_daily:{policy}" if bars else "empty"
        elif market == "HK":
            bars = fetch_hk_daily_bars(code, limit=fetch_limit)
            src = "akshare_hk_daily" if bars else "empty"
        elif market == "US":
            bars = fetch_us_daily_bars(code, limit=fetch_limit)
            src = "akshare_us_daily" if bars else "empty"
        else:
            return [], "empty"
    except Exception:
        if existing:
            trimmed = existing[-limit:] if limit and len(existing) > limit else existing
            return trimmed, "cache:stale"
        return [], "empty"

    if incremental and existing:
        bars = merge_bars_by_date(existing, bars or [])
        if bars and src == "empty":
            src = "cache:merged"
    if not bars and existing:
        bars = existing
        src = "cache:stale"

    if bars and use_cache and not disable_cache:
        try:
            raw_src = src
            if str(raw_src).startswith("cache:"):
                raw_src = str(raw_src).split(":", 1)[-1] or "cache"
            save_daily_cache(
                market,
                code,
                bars,
                data_source=raw_src,
                stock_code=code,
                adjust_policy=policy,
            )
        except OSError:
            pass

    trimmed = bars[-limit:] if limit and bars and len(bars) > limit else (bars or [])
    return trimmed, src



def enrich_quote_bar(quote: dict) -> Optional[dict]:
    """把腾讯行情压成单日 bar，供 K 线/短线降级。"""
    price = quote.get("price_raw")
    if price is None:
        return None
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None

    def _px(key: str) -> Optional[float]:
        return _to_float(
            str(quote.get(key, ""))
            .replace("元", "")
            .replace("HK$", "")
            .replace("$", "")
            .replace(",", "")
        )

    open_p = _px("open") or price
    high_p = _px("high") or max(open_p, price)
    low_p = _px("low") or min(open_p, price)
    vol = _to_float(
        str(quote.get("volume", "")).replace("万", "e4").replace("亿", "e8")
    )
    # 「8363万」类字符串 _to_float 可能失败，忽略量
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "close": price,
        "volume": vol or 0.0,
    }


def bars_from_quote_fallback(quote: dict) -> List[dict]:
    """
    无日线时的退化：用现价与涨跌幅构造伪 2 日序列，仅支撑极简评分/当日 K。
    """
    price = quote.get("price_raw")
    change = quote.get("change_raw")
    if price is None:
        return []
    try:
        price = float(price)
        change = float(change or 0)
    except (TypeError, ValueError):
        return []
    if price <= 0:
        return []
    prev = price / (1 + change / 100.0) if change != -100 else price
    today = enrich_quote_bar(quote)
    if not today:
        return []
    return [
        {
            "date": "d-1",
            "open": prev,
            "high": prev,
            "low": prev,
            "close": prev,
            "volume": 1.0,
        },
        {
            **today,
            "date": today.get("date") or "d0",
            "volume": today.get("volume") or (1.2 if change > 0 else 0.8),
        },
    ]
