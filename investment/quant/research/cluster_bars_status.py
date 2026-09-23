"""观察池日线缓存覆盖与仅刷新日线（研究枢纽 UI）。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.numbers import date_key

logger = logging.getLogger(__name__)


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
        logger.debug("catch except Exception: in cluster_bars_status.py", exc_info=True)
        return None


def build_cluster_bars_status(*, watching_limit: int = 200) -> Dict[str, Any]:
    """汇总观察池截断后的日线末 bar 覆盖。"""
    from quant.research.cluster_bars_daily import (
        cluster_bars_session_date,
        needs_force_latest_bars,
        read_force_latest_bars_marker,
    )
    from quant.research.factor_ols_clusters import clamp_watching_limit, merge_cluster_universe

    limit = clamp_watching_limit(watching_limit, 200)
    watchlist: List[Any] = []
    try:
        from core.watching.store import read_watching

        watchlist = list((read_watching() or {}).get("watchlist") or [])
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in cluster_bars_status.py", exc_info=True)
        watchlist = []

    uni = merge_cluster_universe(
        watchlist,
        [],
        watching_limit=limit,
        universe_mode="watching",
    )
    codes = [str(c).strip() for c in (uni.get("codes") or []) if str(c).strip()]
    expected = expected_latest_daily_bar_date()
    session = cluster_bars_session_date()

    at_expected = 0
    stale = 0
    missing = 0
    date_counts: Dict[str, int] = {}
    last_min = ""
    last_max = ""

    for code in codes:
        lb = _last_bar_date_for_code(code)
        if not lb:
            missing += 1
            continue
        date_counts[lb] = date_counts.get(lb, 0) + 1
        if not last_min or lb < last_min:
            last_min = lb
        if not last_max or lb > last_max:
            last_max = lb
        if expected and lb >= expected:
            at_expected += 1
        else:
            stale += 1

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
        "forced_marker": marker if marker else None,
        "refresh_job": _bars_refresh_job_snapshot(),
    }


def _bars_refresh_job_snapshot() -> Optional[Dict[str, Any]]:
    from core.job_progress import cluster_bars_refresh_job
    from quant.research.cluster_refresh_job_snapshot import public_refresh_job_snapshot

    return public_refresh_job_snapshot(
        cluster_bars_refresh_job,
        result_summary_key="bars_refresh",
    )


def refresh_cluster_bars_only(
    *,
    watching_limit: int = 200,
    lookback: int = 80,
    mode: str = "topup",
    progress_cb: Optional[Any] = None,
) -> Dict[str, Any]:
    """仅更新观察池日线，不跑 OLS 分组。

    ``mode=topup``：强制增量对齐最新（日常）；``mode=full``：整窗重拉（仓坏/复权问题兜底）。
    """
    from quant.research.cluster_bars_daily import (
        cluster_bars_session_date,
        mark_force_latest_bars_done,
    )
    from quant.research.cluster_panels import build_cluster_ols_panels
    from quant.research.factor_ols_clusters import clamp_watching_limit, merge_cluster_universe

    limit = clamp_watching_limit(watching_limit, 200)
    lb = max(40, int(lookback or 80))
    mode_s = str(mode or "topup").strip().lower()
    if mode_s not in ("full", "topup"):
        mode_s = "topup"
    do_full = mode_s == "full"
    watchlist: List[Any] = []
    try:
        from core.watching.store import read_watching

        watchlist = list((read_watching() or {}).get("watchlist") or [])
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in cluster_bars_status.py", exc_info=True)
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

    bars_session = cluster_bars_session_date()

    def _on_progress(msg: str, cur: int = 0, tot: int = 0) -> None:
        if not progress_cb:
            return
        try:
            progress_cb(msg, int(cur or 0), int(tot or n_codes or 1))
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in cluster_bars_status.py", exc_info=True)

    built = build_cluster_ols_panels(
        codes,
        lookback=lb,
        pit_fundamentals=False,
        code_roles=dict(uni.get("code_roles") or {}),
        progress_cb=_on_progress,
        refresh_bars=False,
        force_latest_bars=not do_full,
        full_window_bars=do_full,
    )
    bars_refresh = dict(built.get("bars_refresh") or {})
    bars_refresh["manual_refresh"] = True
    bars_refresh["session_date"] = bars_session
    bars_refresh["mode"] = mode_s
    mark_force_latest_bars_done(
        session_date=bars_session,
        remote_count=int(bars_refresh.get("remote_count") or 0),
        total=int(bars_refresh.get("total") or n_codes),
    )
    status = build_cluster_bars_status(watching_limit=limit)
    return {
        "success": True,
        "task": "cluster_bars_refresh",
        "mode": mode_s,
        "watching_limit": limit,
        "universe_count": n_codes,
        "lookback": lb,
        "bars_refresh": bars_refresh,
        "status": status,
    }
