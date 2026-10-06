"""观察池日线缓存覆盖与仅刷新日线（研究枢纽 UI）。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.numbers import date_key
from core.watching.store import WATCHING_MAX_SIZE

logger = logging.getLogger(__name__)

# 观察池日 K 写入窗（交易日）。仓上限见 DAILY_BARS_MAX_KEEP（800）。
BARS_DAILY_LOOKBACK = 600


def bars_daily_fetch_limit(lookback: int = BARS_DAILY_LOOKBACK) -> int:
    """拉日 K 的条数：lookback 个交易日，再垫 Alpha158 特征窗。"""
    lb = max(40, int(lookback or BARS_DAILY_LOOKBACK))
    pad = 62
    try:
        from core.signal.factors.alpha158 import ALPHA158_PANEL_WINDOW

        pad = max(35, int(ALPHA158_PANEL_WINDOW))
    except Exception:  # noqa: BLE001
        logger.debug("cluster daily fetch pad fallback", exc_info=True)
    return lb + pad


def expected_latest_daily_bar_date(*, now: Optional[datetime] = None) -> str:
    """研究侧期望的最新完整日线日期（A 股 15:05 前仍用上一交易日）。"""
    from core.market.calendar import expected_latest_daily_bar_date as _as_of

    return _as_of(now=now)


def _last_bar_date_for_code(code: str) -> Optional[str]:
    """只读 meta.date_max，不拉全日线（覆盖状态热路径）。"""
    try:
        from core.ports.market import resolve_market_code
        from core.store import peek_daily_cache_meta

        market, sym = resolve_market_code(code)
        meta = peek_daily_cache_meta(market, sym)
        if not meta:
            return None
        return date_key(meta.get("date_max")) or None
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        return None


def _cached_daily_bar_count(code: str) -> Optional[int]:
    """本地日 K 条数；读不到 meta 时返回 None（不当成短仓）。"""
    try:
        from core.ports.market import resolve_market_code
        from core.store import peek_daily_cache_meta

        market, sym = resolve_market_code(code)
        meta = peek_daily_cache_meta(market, sym)
        if not meta or meta.get("bar_count") is None:
            return None
        return int(meta.get("bar_count") or 0)
    except Exception:  # noqa: BLE001
        return None


def build_bars_status(*, watching_limit: int = WATCHING_MAX_SIZE) -> Dict[str, Any]:
    """汇总观察池截断后的日线末 bar 覆盖。"""
    from core.market.calendar import prev_trading_day
    from quant.research.bars_daily import (
        bars_session_date,
        needs_force_latest_bars,
        read_force_latest_bars_marker,
    )
    from quant.research.watching_universe import clamp_watching_limit, merge_cluster_universe

    limit = clamp_watching_limit(watching_limit, WATCHING_MAX_SIZE)
    watchlist: List[Any] = []
    try:
        from core.watching.store import read_watching

        watchlist = list((read_watching() or {}).get("watchlist") or [])
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        watchlist = []

    uni = merge_cluster_universe(
        watchlist,
        [],
        watching_limit=limit,
        universe_mode="watching",
    )
    codes = [str(c).strip() for c in (uni.get("codes") or []) if str(c).strip()]
    expected = expected_latest_daily_bar_date()
    session = bars_session_date()

    at_expected = 0
    stale = 0
    missing = 0
    date_counts: Dict[str, int] = {}
    lag_counts: Dict[int, int] = {}
    last_min = ""
    last_max = ""

    def _trading_lag(last: str, expect: str) -> int:
        if not last or not expect or last >= expect:
            return 0
        n = 0
        d = expect
        while d and d > last and n < 30:
            d = str(prev_trading_day(d) or "")[:10]
            n += 1
        return n

    for code in codes:
        lb = _last_bar_date_for_code(code)
        if not lb:
            missing += 1
            lag_counts[-1] = lag_counts.get(-1, 0) + 1
            continue
        date_counts[lb] = date_counts.get(lb, 0) + 1
        if not last_min or lb < last_min:
            last_min = lb
        if not last_max or lb > last_max:
            last_max = lb
        if expected and lb >= expected:
            at_expected += 1
            lag_counts[0] = lag_counts.get(0, 0) + 1
        else:
            stale += 1
            lag = _trading_lag(lb, expected) if expected else 1
            lag_counts[lag] = lag_counts.get(lag, 0) + 1

    total = len(codes)
    coverage_ok = bool(total) and stale == 0 and missing == 0
    dist = sorted(date_counts.items(), key=lambda x: (-x[1], x[0]))[:6]
    marker = read_force_latest_bars_marker()

    span_days = 0
    if last_min and last_max:
        try:
            d0 = datetime.strptime(last_min[:10], "%Y-%m-%d")
            d1 = datetime.strptime(last_max[:10], "%Y-%m-%d")
            span_days = max(0, (d1 - d0).days)
        except (ValueError, TypeError):
            span_days = 0

    try:
        from core.store import bars_backend

        backend = bars_backend()
    except Exception:  # noqa: BLE001
        backend = "unknown"

    lag_distribution = [
        {
            "lag": int(k),
            "label": "Missing" if int(k) < 0 else ("齐" if int(k) == 0 else f"缺{int(k)}"),
            "count": int(v),
        }
        for k, v in sorted(lag_counts.items(), key=lambda x: (x[0] < 0, x[0]))
        if v
    ]

    return {
        "success": True,
        "session_date": session,
        "expected_latest_bar": expected or None,
        "watching_limit": limit,
        "universe_count": total,
        "universe_mode": "watching",
        "at_expected": at_expected,
        "stale": stale,
        "missing": missing,
        "coverage_ok": coverage_ok,
        "coverage_pct": round((100 * at_expected) / total, 1) if total else 0.0,
        "needs_force_latest_bars": bool(needs_force_latest_bars(session_date=session)),
        "last_bar_min": last_min or None,
        "last_bar_max": last_max or None,
        "last_bar_span_days": span_days,
        "last_bar_aligned": bool(
            coverage_ok
            and expected
            and last_min
            and last_max
            and last_min == last_max == expected
        ),
        "bars_backend": backend,
        "bar_fields": ["open", "high", "low", "close", "volume"],
        "as_of_rule": "trading_day_before_1505_prev",
        "date_distribution": [{"date": d, "count": c} for d, c in dist],
        "lag_distribution": lag_distribution,
        "forced_marker": marker if marker else None,
        "refresh_job": _bars_refresh_job_snapshot(),
    }


def _bars_refresh_job_snapshot() -> Optional[Dict[str, Any]]:
    from core.job_progress import bars_refresh_job
    from quant.research.cluster_refresh_job_snapshot import public_refresh_job_snapshot

    return public_refresh_job_snapshot(
        bars_refresh_job,
        result_summary_key="bars_refresh",
    )


def refresh_bars_only(
    *,
    watching_limit: int = WATCHING_MAX_SIZE,
    lookback: int = BARS_DAILY_LOOKBACK,
    mode: str = "topup",
    progress_cb: Optional[Any] = None,
) -> Dict[str, Any]:
    """仅更新观察池日线，不跑 OLS 分组。

    ``mode=topup``：末 bar 未齐 as-of 的走缺口合并；本地条数短于拉取窗的整窗重拉。
    ``mode=full``：全池整窗重拉（仓坏/复权问题兜底）。
    走进程池并行拉取，避免主进程 ak_lock 把 4 线程串成单通道后 360s 超时。
    """
    from quant.research.bars_daily import (
        bars_session_date,
        mark_force_latest_bars_done,
    )
    from quant.research.watching_universe import clamp_watching_limit, merge_cluster_universe

    limit = clamp_watching_limit(watching_limit, WATCHING_MAX_SIZE)
    lb = max(40, int(lookback or BARS_DAILY_LOOKBACK))
    fetch_limit = bars_daily_fetch_limit(lb)
    mode_s = str(mode or "topup").strip().lower()
    if mode_s not in ("full", "topup"):
        mode_s = "topup"
    do_full = mode_s == "full"
    watchlist: List[Any] = []
    try:
        from core.watching.store import read_watching

        watchlist = list((read_watching() or {}).get("watchlist") or [])
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        watchlist = []

    uni = merge_cluster_universe(
        watchlist,
        [],
        watching_limit=limit,
        universe_mode="watching",
    )
    codes = [str(c).strip() for c in (uni.get("codes") or []) if str(c).strip()]
    n_codes = len(codes)
    if n_codes < 1:
        return {
            "success": False,
            "error": "观察池为空，无法更新日线",
            "watching_limit": limit,
            "universe_count": 0,
            "mode": mode_s,
        }

    bars_session = bars_session_date()
    expected = expected_latest_daily_bar_date()

    def _on_progress(msg: str, cur: int = 0, tot: int = 0) -> None:
        if not progress_cb:
            return
        try:
            progress_cb(msg, int(cur or 0), int(tot or n_codes or 1))
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            pass

    # full_codes：整窗（无仓、条数短于拉取窗、或 mode=full）。末根已齐时增量会跳过远端，短仓必须整窗。
    # tail_codes：末 bar 落后，但本地条数已够，只补缺口。
    full_codes: List[str] = []
    tail_codes: List[str] = []
    aligned_n = 0
    if do_full:
        full_codes = list(codes)
    else:
        for code in codes:
            lb_date = _last_bar_date_for_code(code)
            stale = (not lb_date) or bool(expected and lb_date < expected)
            n_bars = _cached_daily_bar_count(code)
            thin = n_bars is not None and n_bars < fetch_limit
            if not stale and not thin:
                aligned_n += 1
                continue
            if thin or not lb_date:
                full_codes.append(code)
            else:
                tail_codes.append(code)

    need = full_codes + tail_codes
    remote_n = 0
    failed_n = 0
    chunk = 32
    n_need = len(need)
    pulled = 0

    def _pull(part_codes: List[str], *, incremental: bool) -> None:
        nonlocal remote_n, failed_n, pulled
        if not part_codes or svc is None:
            return
        for i in range(0, len(part_codes), chunk):
            part = part_codes[i : i + chunk]
            try:
                packs = svc.get_bars_batch(
                    part,
                    limit=fetch_limit,
                    cache_max_age_hours=0,
                    incremental=incremental,
                    timeout=60.0,
                )
            except Exception:  # noqa: BLE001
                logger.exception("bars batch refresh failed at offset %s", i)
                packs = []
                failed_n += len(part)
            for code, pack in zip(part, packs or []):
                if not isinstance(pack, dict):
                    failed_n += 1
                    continue
                src = str(pack.get("data_source") or "")
                bars = pack.get("bars") or []
                if bars and (
                    "akshare" in src
                    or "baostock" in src
                    or (src and not src.startswith("cache"))
                ):
                    remote_n += 1
                elif not bars:
                    failed_n += 1
            pulled += len(part)
            _on_progress(
                f"拉日线 {pulled}/{n_need}（远端 {remote_n} · 失败 {failed_n} · 已齐 {aligned_n} · 窗 {lb} 日）",
                aligned_n + pulled,
                n_codes,
            )

    svc = None
    if n_need:
        _on_progress(
            f"拉日线 0/{n_need}（已齐 {aligned_n} · 窗 {lb} 日 · 进程池并行）",
            aligned_n,
            n_codes,
        )
        try:
            from core.data.service import get_research_service

            svc = get_research_service()
        except Exception:  # noqa: BLE001
            logger.exception("research service unavailable for bars refresh")
            svc = None
        if svc is None:
            failed_n = n_need
        else:
            _pull(full_codes, incremental=False)
            _pull(tail_codes, incremental=True)
    else:
        _on_progress(
            f"日线已齐 as-of {expected or '—'}（{aligned_n}/{n_codes} · 窗 {lb} 日）",
            n_codes,
            n_codes,
        )

    bars_refresh = {
        "requested": True,
        "force_latest": not do_full,
        "full_window": do_full,
        "mode": mode_s,
        "remote_count": int(remote_n),
        "total": int(n_codes),
        "cache_count": int(aligned_n),
        "gap_count": int(n_need),
        "full_count": len(full_codes),
        "tail_count": len(tail_codes),
        "fetch_limit": int(fetch_limit),
        "failed_count": int(failed_n),
        "note": (
            f"{'整窗强更' if do_full else '增量补齐'}"
            f"（窗 {lb} 日 · 拉 {fetch_limit} 根 · 整窗 {len(full_codes)} · 缺口 {len(tail_codes)} · 已齐 {aligned_n}）"
        ),
        "manual_refresh": True,
        "session_date": bars_session,
        "pool": "process",
    }
    mark_force_latest_bars_done(
        session_date=bars_session,
        remote_count=int(remote_n),
        total=int(n_codes),
    )
    status = build_bars_status(watching_limit=limit)
    return {
        "success": True,
        "task": "bars_refresh",
        "mode": mode_s,
        "watching_limit": limit,
        "universe_count": n_codes,
        "lookback": lb,
        "bars_refresh": bars_refresh,
        "status": status,
    }
