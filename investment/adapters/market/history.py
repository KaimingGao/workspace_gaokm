"""日线数据获取：A 股 / 港股 / 美股，多接口重试；失败可用 quote 退化。"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Callable, List, Optional, Tuple

from core.data.policy import DAILY_CACHE_HOURS
from core.market import register_symbol_resolver, resolve_market_code
from core.numbers import to_float as _to_float
from core.store import load_daily_cache, merge_save_daily_cache
from adapters.market.quote_api import StockAPI

register_symbol_resolver(StockAPI.resolve_symbol)

import logging

logger = logging.getLogger(__name__)


def normalize_bars(rows: List[dict]) -> List[dict]:
    """统一日线字段为 date/open/high/low/close/volume，有则保留独立 amount。

    volume 只取成交量；成交额写入 amount，不再混进 volume（避免 Amihud/流动性失真）。
    """
    from core.bar_fields import extract_amount_from_row, extract_volume_from_row

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
        vol = extract_volume_from_row(row)
        amt = extract_amount_from_row(row)
        bar = {
            "date": str(date) if date is not None else "",
            "open": _to_float(row.get("open") or row.get("开盘") or row.get("开盘价"))
            or close,
            "high": _to_float(row.get("high") or row.get("最高") or row.get("最高价"))
            or close,
            "low": _to_float(row.get("low") or row.get("最低") or row.get("最低价"))
            or close,
            "close": close,
            "volume": float(vol) if vol is not None else 0.0,
        }
        if amt is not None:
            bar["amount"] = float(amt)
        bars.append(bar)
    bars.sort(key=lambda x: x["date"])
    return bars


def _df_to_bars(df: Any, limit: int) -> List[dict]:
    if df is None or getattr(df, "empty", True):
        return []
    records = df.to_dict(orient="records") if hasattr(df, "to_dict") else list(df)
    bars = normalize_bars(records)
    return bars[-limit:] if limit else bars


def _normalize_ymd(raw: Optional[str]) -> Optional[str]:
    """YYYY-MM-DD / YYYYMMDD → YYYYMMDD；非法则 None。"""
    s = str(raw or "").strip().replace("-", "").replace("/", "").replace(".", "")
    if len(s) >= 8 and s[:8].isdigit():
        return s[:8]
    return None


def _parse_bar_date(raw: Any) -> Optional[datetime]:
    s = _normalize_ymd(str(raw or ""))
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y%m%d")
    except ValueError:
        return None


def _last_bar_dt(bars: List[dict]) -> Optional[datetime]:
    if not bars:
        return None
    return _parse_bar_date((bars[-1] or {}).get("date"))


def _asof_complete_bar_dt(now: Optional[datetime] = None) -> Optional[datetime]:
    """完整日线 as-of（15:05 / 周末回退）；解析失败则 None。"""
    try:
        from core.market.calendar import expected_latest_daily_bar_date

        as_of_s = expected_latest_daily_bar_date(now=now)
    except Exception:
        logger.debug("catch except Exception: in history.py", exc_info=True)
        return None
    return _parse_bar_date(as_of_s)


def _incremental_remote_plan(
    existing: List[dict],
    *,
    limit: int,
    adjust: str = "qfq",
    now: Optional[datetime] = None,
) -> Tuple[Optional[str], int, bool]:
    """增量远端计划：(start_ymd|None=默认整窗, fetch_limit, skip_remote)。

    本地条数够且缺口不大时只从最后交易日（含当日重叠）拉到今天；
    条数不足或缺很久（尤其 qfq）则回退整窗，避免前复权断层。
    末根已到完整 as-of（收盘后交易日 / 周末上一个交易日）则 skip_remote。
    """
    last = _last_bar_dt(existing)
    if last is None:
        return None, max(int(limit or 30), 30), False

    dt = now or datetime.now()
    today = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    as_of = _asof_complete_bar_dt(dt)
    target = as_of or today
    if last.date() >= target.date() or last.date() >= today.date():
        return None, 0, True

    gap_days = max(1, (today.date() - last.date()).days)
    need = max(1, int(limit or 30))
    # 缺口补上后仍凑不够 limit → 需要更早历史，走整窗
    thin = len(existing) < max(5, need - max(gap_days, 3))
    # 长缺口 + 前复权：整窗重拉，避免分红后历史价与本地旧段错位
    from core.data.policy import QFQ_LONG_GAP_DAYS

    long_qfq_gap = (
        str(adjust or "qfq").lower() == "qfq" and gap_days > QFQ_LONG_GAP_DAYS
    )

    if thin or long_qfq_gap:
        fetch_limit = max(need, len(existing) + 5, need + 10)
        return None, fetch_limit, False

    # 缺口窗：重叠最后一日（收盘价/成交量修正）+ 少量日历缓冲
    start_s = last.strftime("%Y%m%d")
    fetch_limit = max(8, min(need, gap_days + 6))
    return start_s, fetch_limit, False


def fetch_a_daily_bars(
    code: str,
    limit: int = 30,
    *,
    adjust: str = "qfq",
    start_date: Optional[str] = None,
) -> List[dict]:
    """
    拉取 A 股日线。code 可为 600519 / sh600519。
    adjust: qfq | raw | hfq（hfq 不可用时回退 qfq）。
    start_date: 可选 YYYYMMDD / YYYY-MM-DD；给定则缩小请求窗口（增量补缺）。
    """
    from core.data.ak_lock import import_akshare

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
    start_s = _normalize_ymd(start_date)
    if not start_s:
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
            logger.warning('W: code: %s label: %s unexpected error in fetch_a_daily_bars', code, label)
            errors.append(f"{label}: {e}")
            continue

    logger.warning('W: code: %s no bars found in fetch_a_daily_bars', code)

    if errors:
        fetch_a_daily_bars.last_errors = errors[-6:]  # type: ignore[attr-defined]
    return []


def fetch_hk_daily_bars(
    code: str,
    limit: int = 30,
    *,
    start_date: Optional[str] = None,
) -> List[dict]:
    """
    拉取港股日线。多符号形态 + 多接口重试。
    start_date：可选，缩小增量窗口。
    注意：单次接口异常应 continue，不要整段中断。
    """
    from core.data.ak_lock import import_akshare

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
    start_s = _normalize_ymd(start_date)
    if not start_s:
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
            logger.exception('unexpected error in fetch_hk_daily_bars')
            errors.append(f"{label}: {e}")
            continue

    if errors:
        # 留给上层 notes；此处仍返回空列表
        fetch_hk_daily_bars.last_errors = errors[-5:]  # type: ignore[attr-defined]
    return []


def fetch_us_daily_bars(
    code: str,
    limit: int = 30,
    *,
    start_date: Optional[str] = None,
) -> List[dict]:
    """拉取美股日线。code 如 AAPL。start_date：可选，缩小增量窗口。"""
    from core.data.ak_lock import import_akshare

    ak = import_akshare()

    symbol = str(code).strip().upper()
    if symbol.startswith("US"):
        symbol = symbol[2:]

    end = datetime.now()
    start_s = _normalize_ymd(start_date)
    if not start_s:
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
            logger.exception('unexpected error in fetch_us_daily_bars')
            continue
    return []


def fetch_daily_bars(
    stock_code: str,
    limit: int = 30,
    *,
    use_cache: bool = True,
    cache_max_age_hours: float = DAILY_CACHE_HOURS,
    incremental: bool = True,
    adjust: str = "qfq",
    offline_ok: bool = False,
    offline_only: bool = False,
) -> Tuple[List[dict], str]:
    """
    按市场拉取日线（带本地缓存 data/store/daily/）。
    返回 (bars, data_source)：
    data_source: cache:akshare_* / akshare_cn_daily / akshare_hk_daily / empty

    incremental=True：本地已有足够历史时，只请求「最后交易日→今天」缺口窗并按 date 合并落盘；
    条数不足或前复权长缺口则回退整窗。
    adjust：qfq|raw|hfq（写入缓存 adjust_policy；与请求不一致则跳过缓存防混用）。
    offline_ok=True：本地有足够 bars 时直接返回（可过期），不打远端——研究分组用。
    offline_only=True：只读缓存（含过期），不够也不打远端；批量回测先扫盘再用进程池补缺。
    cache_max_age_hours<=0：不因「条数够」短路返回；把本地当 existing，配合
    incremental 走缺口拉网（末根已到完整 as-of 则仍可 skip_remote）。用于强制刷新到最新。
    """
    from core.store import (
        code_refresh_lock,
        merge_bars_by_date,
        peek_daily_cache_meta,
    )

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
    # <=0：强制刷新语义（不短路命中缓存）；store 侧 max_age<=0 本就忽略年龄
    force_refresh = float(cache_max_age_hours or 0) <= 0
    existing: List[dict] = []
    cached_src = "cache"
    if use_cache and not disable_cache:
        # D2：缓存 adjust_policy 与请求不一致则跳过，防 qfq/raw 混用
        meta_peek = None
        try:
            meta_peek = peek_daily_cache_meta(market, code)
        except Exception:
            logger.exception('unexpected error in fetch_daily_bars')
            meta_peek = None
        cached_adj = str((meta_peek or {}).get("adjust_policy") or "qfq").lower()
        cache_ok = cached_adj == policy or (
            policy == "qfq" and cached_adj in ("qfq", "cached", "")
        )
        if cache_ok:
            if force_refresh:
                # 强制刷新：直接读盘作 existing，再走增量远端
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
            else:
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

    if offline_only:
        if existing:
            trimmed = existing[-limit:] if limit and len(existing) > limit else existing
            return trimmed, cached_src
        return [], "empty"

    with code_refresh_lock(market, code, kind="daily"):
        # 进入远端前再读一次本地，避免并发穿透重复拉
        if use_cache and not disable_cache and not existing:
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
            if offline_ok and existing and len(existing) >= min_offline:
                trimmed = (
                    existing[-limit:] if limit and len(existing) > limit else existing
                )
                return trimmed, cached_src

        gap_start: Optional[str] = None
        fetch_limit = max(
            int(limit or 30),
            len(existing) + 5 if existing else int(limit or 30),
        )
        if incremental and existing:
            gap_start, fetch_limit, skip_remote = _incremental_remote_plan(
                existing, limit=int(limit or 30), adjust=policy
            )
            if skip_remote:
                trimmed = (
                    existing[-limit:] if limit and len(existing) > limit else existing
                )
                return trimmed, cached_src

        try:
            if market == "CN":
                bars = fetch_a_daily_bars(
                    code, limit=fetch_limit, adjust=policy, start_date=gap_start
                )
                src = f"akshare_cn_daily:{policy}" if bars else "empty"
            elif market == "HK":
                bars = fetch_hk_daily_bars(code, limit=fetch_limit, start_date=gap_start)
                src = "akshare_hk_daily" if bars else "empty"
            elif market == "US":
                bars = fetch_us_daily_bars(code, limit=fetch_limit, start_date=gap_start)
                src = "akshare_us_daily" if bars else "empty"
            else:
                return [], "empty"
        except Exception:
            logger.exception('unexpected error in fetch_daily_bars')
            if existing:
                trimmed = (
                    existing[-limit:] if limit and len(existing) > limit else existing
                )
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
                _, bars = merge_save_daily_cache(
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
    amt = _to_float(
        str(quote.get("amount", "") or quote.get("turnover", "") or "")
        .replace("万", "e4")
        .replace("亿", "e8")
    )
    # 「8363万」类字符串 _to_float 可能失败，忽略量
    out = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "close": price,
        "volume": vol or 0.0,
    }
    if amt is not None and amt > 0:
        out["amount"] = float(amt)
    return out


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
