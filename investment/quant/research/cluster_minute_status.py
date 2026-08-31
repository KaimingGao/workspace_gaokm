"""观察池 5m 分钟线缓存覆盖与预热（研究枢纽 UI）。"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.data.policy import MINUTE_EM_LOOKBACK_DAYS, MINUTE_WARMUP_READY_MIN_SPAN_DAYS

logger = logging.getLogger(__name__)

DEFAULT_MINUTE_PERIOD = "5"
DEFAULT_MIN_SPAN_DAYS = MINUTE_WARMUP_READY_MIN_SPAN_DAYS
DEFAULT_LOOKBACK_DAYS = MINUTE_EM_LOOKBACK_DAYS
# 增量补齐：跨度已够的票只拉近几日 merge（对齐日 K「增量」思路）
DEFAULT_TOPUP_LOOKBACK_DAYS = 5
DEFAULT_TOPUP_WORKERS = 4


def _resolve_watching_codes(*, watching_limit: int = 100) -> List[str]:
    from quant.research.factor_ols_clusters import clamp_watching_limit, merge_cluster_universe

    limit = clamp_watching_limit(watching_limit, 100)
    watchlist: List[Any] = []
    try:
        from core.watching.store import read_watching

        watchlist = list((read_watching() or {}).get("watchlist") or [])
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("read_watching failed in cluster_minute_status", exc_info=True)
        watchlist = []

    uni = merge_cluster_universe(
        watchlist,
        [],
        watching_limit=limit,
        universe_mode="watching",
    )
    return [str(c).strip() for c in (uni.get("codes") or []) if str(c).strip()]


def _minute_snapshot_for_code(code: str, *, period: str = DEFAULT_MINUTE_PERIOD) -> Optional[Dict[str, Any]]:
    try:
        from core.ports.market import group_minute_bars_by_date, resolve_market_code
        from core.store import load_minute_cache

        market, sym = resolve_market_code(code)
        packed = load_minute_cache(
            market,
            sym,
            str(period or DEFAULT_MINUTE_PERIOD),
            min_bars=10,
            ignore_age=True,
        )
        if not packed:
            return None
        bars, meta = packed
        by_day = group_minute_bars_by_date(bars or []) or {}
        span_days = len(by_day)
        fetched_at = str((meta or {}).get("fetched_at") or "")[:19]
        date_min = (meta or {}).get("date_min")
        date_max = (meta or {}).get("date_max")
        if not date_min and by_day:
            date_min = min(by_day.keys())
        if not date_max and by_day:
            date_max = max(by_day.keys())
        return {
            "span_days": span_days,
            "bar_count": int((meta or {}).get("bar_count") or len(bars or [])),
            "fetched_at": fetched_at or None,
            "date_min": str(date_min)[:10] if date_min else None,
            "date_max": str(date_max)[:10] if date_max else None,
        }
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("minute snapshot failed for %s", code, exc_info=True)
        return None


def minute_cache_ready(
    code: str,
    *,
    period: str = DEFAULT_MINUTE_PERIOD,
    min_span_days: int = DEFAULT_MIN_SPAN_DAYS,
    stale_hours: float = 24.0,
    max_calendar_gap_days: int = 4,
) -> tuple[bool, Optional[Dict[str, Any]], str]:
    """本地分钟缓存是否已满足预热/强更，可跳过远端拉取。

    与 UI ``Ready ≥ min_span_days`` 对齐，并要求 ``fetched_at`` 未过期、``date_max`` 够新。
    返回 ``(ready, snapshot, reason)``；reason 为 ``ready`` | ``missing`` | ``short`` | ``stale`` | ``date_max_old``。
    """
    snap = _minute_snapshot_for_code(code, period=period)
    if not snap:
        return False, None, "missing"
    span = int(snap.get("span_days") or 0)
    min_span = max(1, int(min_span_days or DEFAULT_MIN_SPAN_DAYS))
    if span < min_span:
        return False, snap, "short"
    fetched_s = snap.get("fetched_at") or ""
    if fetched_s:
        try:
            fetched_at = datetime.fromisoformat(str(fetched_s))
            cutoff = datetime.now() - timedelta(hours=max(1.0, float(stale_hours or 24.0)))
            if fetched_at < cutoff:
                return False, snap, "stale"
        except (ValueError, TypeError):
            pass
    date_max = str(snap.get("date_max") or "")[:10]
    if date_max:
        try:
            d_max = datetime.strptime(date_max, "%Y-%m-%d").date()
            gap = (datetime.now().date() - d_max).days
            if gap > max(1, int(max_calendar_gap_days or 4)):
                return False, snap, "date_max_old"
        except ValueError:
            return False, snap, "date_max_old"
    else:
        return False, snap, "date_max_old"
    return True, snap, "ready"


def build_cluster_minute_status(
    *,
    watching_limit: int = 100,
    period: str = DEFAULT_MINUTE_PERIOD,
    min_span_days: int = DEFAULT_MIN_SPAN_DAYS,
    stale_hours: float = 24.0,
) -> Dict[str, Any]:
    """汇总观察池截断后的 5m 分钟缓存覆盖。"""
    from quant.research.factor_ols_clusters import clamp_watching_limit

    limit = clamp_watching_limit(watching_limit, 100)
    period_s = str(period or DEFAULT_MINUTE_PERIOD)
    min_span = max(1, int(min_span_days or DEFAULT_MIN_SPAN_DAYS))
    codes = _resolve_watching_codes(watching_limit=limit)

    cached_ok = 0
    short = 0
    missing = 0
    stale = 0
    spans: List[int] = []
    short_label = f"<{min_span}d"
    span_buckets: Dict[str, int] = {short_label: 0}

    now = datetime.now()
    stale_cutoff = now - timedelta(hours=max(1.0, float(stale_hours or 24.0)))

    for code in codes:
        snap = _minute_snapshot_for_code(code, period=period_s)
        if not snap:
            missing += 1
            continue
        span = int(snap.get("span_days") or 0)
        spans.append(span)
        fetched_s = snap.get("fetched_at") or ""
        is_stale = False
        if fetched_s:
            try:
                fetched_at = datetime.fromisoformat(str(fetched_s))
                is_stale = fetched_at < stale_cutoff
            except (ValueError, TypeError):
                is_stale = False
        if is_stale:
            stale += 1
        if span >= min_span:
            cached_ok += 1
        else:
            short += 1
            span_buckets[short_label] += 1

    total = len(codes)
    spans_sorted = sorted(spans)
    med_span = spans_sorted[len(spans_sorted) // 2] if spans_sorted else 0
    coverage_ok = bool(total) and missing == 0 and short == 0

    try:
        from core.store import bars_backend

        backend = bars_backend()
    except Exception:  # noqa: BLE001
        backend = "unknown"

    dist = [
        {"bucket": k, "count": v}
        for k, v in span_buckets.items()
        if v > 0
    ]

    return {
        "success": True,
        "task": "cluster_minute_status",
        "period": period_s,
        "watching_limit": limit,
        "universe_count": total,
        "universe_mode": "watching",
        "min_span_days": min_span,
        "lookback_days_default": DEFAULT_LOOKBACK_DAYS,
        "cached_ok": cached_ok,
        "short": short,
        "missing": missing,
        "stale": stale,
        "coverage_ok": coverage_ok,
        "coverage_pct": round((100 * cached_ok) / total, 1) if total else 0.0,
        "minute_span_days_med": med_span,
        "minute_span_days_min": spans_sorted[0] if spans_sorted else 0,
        "minute_span_days_max": spans_sorted[-1] if spans_sorted else 0,
        "span_distribution": dist,
        "bars_backend": backend,
        "note": "5m 分钟线本地仓；供 ŷ_path / T0 回测 / tail_anomaly",
        "refresh_job": _minute_refresh_job_snapshot(),
    }


def _minute_refresh_job_snapshot() -> Optional[Dict[str, Any]]:
    from core.job_progress import cluster_minute_refresh_job
    from quant.research.cluster_refresh_job_snapshot import public_refresh_job_snapshot

    return public_refresh_job_snapshot(
        cluster_minute_refresh_job,
        result_summary_key="minute_warmup",
    )


def expected_minute_asof(*, now: Optional[datetime] = None) -> str:
    """增量补齐期望的末分钟日：开盘后要对齐到当日会话，开盘前用上一交易日。"""
    from core.market.calendar import is_trading_day, prev_trading_day, resolve_session_date

    dt = now or datetime.now()
    session = str(resolve_session_date(now=dt) or "")[:10]
    if not session:
        return ""
    if not is_trading_day(session):
        return session
    today = dt.strftime("%Y-%m-%d")
    if today == session:
        open_cut = dt.replace(hour=9, minute=30, second=0, microsecond=0)
        if dt < open_cut:
            prev = prev_trading_day(session)
            return prev or session
        return session
    return session


def _minute_topup_core(
    *,
    codes: List[str],
    period: str = DEFAULT_MINUTE_PERIOD,
    full_lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    topup_lookback_days: int = DEFAULT_TOPUP_LOOKBACK_DAYS,
    min_span_days: int = DEFAULT_MIN_SPAN_DAYS,
    workers: int = DEFAULT_TOPUP_WORKERS,
    progress_cb: Optional[Any] = None,
) -> Dict[str, Any]:
    """观察池 5m 增量补齐：已对齐跳过；跨度够只拉近几日；缺/短才全窗口。"""
    from core.data.policy import minute_em_lookback_days
    from core.ports.market import fetch_minute_bars

    watch = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if not watch:
        return {"ok": False, "error": "无标的", "kind": "minute_topup", "total": 0}

    period_s = str(period or DEFAULT_MINUTE_PERIOD)
    em_cap = minute_em_lookback_days()
    full_lb = min(max(int(full_lookback_days or DEFAULT_LOOKBACK_DAYS), 5), em_cap)
    top_lb = min(max(int(topup_lookback_days or DEFAULT_TOPUP_LOOKBACK_DAYS), 2), full_lb)
    min_span = max(1, int(min_span_days or DEFAULT_MIN_SPAN_DAYS))
    expected = expected_minute_asof()
    n_workers = max(1, min(int(workers or DEFAULT_TOPUP_WORKERS), 8, len(watch)))

    skipped_aligned = 0
    topped = 0
    bootstrapped = 0
    warmed = 0
    errors: List[str] = []
    total = len(watch)
    done = 0

    def _plan(code: str) -> Tuple[str, str, int, bool]:
        """返回 (code, action, lookback, skip_em)。action: skip|topup|full。"""
        snap = _minute_snapshot_for_code(code, period=period_s)
        if not snap:
            return code, "full", full_lb, False
        span = int(snap.get("span_days") or 0)
        date_max = str(snap.get("date_max") or "")[:10]
        if span >= min_span and expected and date_max and date_max >= expected:
            return code, "skip", 0, True
        if span >= min_span:
            return code, "topup", top_lb, True
        return code, "full", full_lb, False

    plans = [_plan(c) for c in watch]
    to_fetch = [(c, act, lb, sem) for c, act, lb, sem in plans if act != "skip"]
    skipped_aligned = sum(1 for _, act, _, _ in plans if act == "skip")
    warmed += skipped_aligned
    done = skipped_aligned
    if progress_cb and skipped_aligned:
        try:
            progress_cb(done, total, f"skip aligned ×{skipped_aligned}")
        except Exception:  # noqa: BLE001
            logger.debug("minute topup progress_cb failed", exc_info=True)

    def _fetch_one(code: str, action: str, lookback: int, skip_em: bool) -> Tuple[str, str, bool, Optional[str]]:
        bars, meta = fetch_minute_bars(
            code,
            period=period_s,
            use_cache=True,
            lookback_days=lookback,
            max_age_hours=0.01,
            skip_em=skip_em,
        )
        if bars:
            return code, action, True, None
        err = str((meta or {}).get("error") or "empty")
        return code, action, False, err

    if to_fetch:
        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            futs = {
                ex.submit(_fetch_one, c, act, lb, sem): (c, act)
                for c, act, lb, sem in to_fetch
            }
            for fut in as_completed(futs):
                code, action = futs[fut]
                try:
                    _c, act, ok_fetch, err = fut.result()
                except Exception as exc:  # noqa: BLE001
                    ok_fetch, act, err = False, action, str(exc)[:80]
                done += 1
                if ok_fetch:
                    warmed += 1
                    if act == "topup":
                        topped += 1
                    else:
                        bootstrapped += 1
                elif err:
                    errors.append(f"{code}:{err}")
                if progress_cb:
                    try:
                        progress_cb(done, total, f"{act} {code}")
                    except Exception:  # noqa: BLE001
                        logger.debug("minute topup progress_cb failed", exc_info=True)

    return {
        "ok": warmed > 0 or total == 0,
        "kind": "minute_topup",
        "mode": "topup",
        "period": period_s,
        "lookback_days": full_lb,
        "topup_lookback_days": top_lb,
        "expected_asof": expected or None,
        "total": total,
        "warmed": warmed,
        "skipped_aligned": skipped_aligned,
        "topped": topped,
        "bootstrapped": bootstrapped,
        "skipped_ready": skipped_aligned,
        "workers": n_workers,
        "errors": errors[:10],
        "note": (
            f"5m 增量补齐 · 对齐跳过 · 近 {top_lb} 日 topup（优先新浪/腾讯）· "
            f"缺/短全窗 {full_lb} 日 · {n_workers} 并发"
        ),
    }


def refresh_cluster_minute_only(
    *,
    watching_limit: int = 100,
    period: str = DEFAULT_MINUTE_PERIOD,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    mode: str = "full",
    topup_lookback_days: int = DEFAULT_TOPUP_LOOKBACK_DAYS,
    progress_cb: Optional[Any] = None,
) -> Dict[str, Any]:
    """预热观察池 5m 分钟线（研究枢纽 UI；不占用 schedule slot）。

    ``mode=full``：原强更（Ready 跳过 + 全窗口重拉）。
    ``mode=topup``：增量补齐（已对齐跳过；跨度够只补近几日；供预演调仓日常刷新）。
    """
    from core.schedule_jobs import _minute_warmup_core
    from core.data.policy import minute_em_lookback_days

    limit = max(1, int(watching_limit or 100))
    period_s = str(period or DEFAULT_MINUTE_PERIOD)
    cap = minute_em_lookback_days()
    lb = min(max(int(lookback_days or DEFAULT_LOOKBACK_DAYS), 5), cap)
    mode_s = str(mode or "full").strip().lower()
    if mode_s not in ("full", "topup"):
        mode_s = "full"
    codes = _resolve_watching_codes(watching_limit=limit)
    n_codes = len(codes)
    if n_codes < 1:
        return {
            "success": False,
            "error": "观察池为空，无法预热分钟线",
            "watching_limit": limit,
            "universe_count": 0,
            "task": "cluster_minute_refresh",
            "mode": mode_s,
        }

    def _on_progress(cur: int, total: int, code: str) -> None:
        if not progress_cb:
            return
        label = "增量" if mode_s == "topup" else "预热"
        try:
            progress_cb(f"{label} 5m {code} ({cur}/{total})", int(cur or 0), int(total or n_codes))
        except Exception:  # noqa: BLE001 — best-effort 进度回调
            logger.debug("cluster minute refresh progress_cb failed", exc_info=True)

    if mode_s == "topup":
        warmup = _minute_topup_core(
            codes=codes,
            period=period_s,
            full_lookback_days=lb,
            topup_lookback_days=topup_lookback_days,
            progress_cb=_on_progress,
        )
    else:
        warmup = _minute_warmup_core(
            codes=codes,
            period=period_s,
            cap=n_codes,
            lookback_days=lb,
            progress_cb=_on_progress,
        )
        warmup = dict(warmup)
        warmup["mode"] = "full"
    status = build_cluster_minute_status(watching_limit=limit, period=period_s)
    ok = bool(warmup.get("ok"))
    return {
        "success": ok,
        "task": "cluster_minute_refresh",
        "mode": mode_s,
        "watching_limit": limit,
        "universe_count": n_codes,
        "lookback_days": lb,
        "period": period_s,
        "minute_warmup": warmup,
        "status": status,
        "error": None if ok else str(warmup.get("error") or "分钟预热失败"),
    }
