"""观察池 5m 分钟线缓存覆盖与预热（研究枢纽 UI）。"""

from __future__ import annotations

import logging
import time
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
DEFAULT_LABEL_MAX_DAYS = 120
# Span mix 细档（累计）：在 Ready 闸 `<min_span>d` 之外再标 `<20d` / `<10d`
SPAN_MIX_FINE_CUTS = (20, 10)
_LABEL_PORTRAIT_TTL_SEC = 90.0
_label_portrait_cache: Dict[str, Any] = {"key": None, "at": 0.0, "payload": None}


def _span_mix_bucket_keys(min_span: int) -> List[str]:
    """`<min_span>d` 优先，再追加更严的累计阈值（仅 cut < min_span）。"""
    gate = max(1, int(min_span or DEFAULT_MIN_SPAN_DAYS))
    keys = [f"<{gate}d"]
    for cut in SPAN_MIX_FINE_CUTS:
        c = int(cut)
        if 0 < c < gate:
            keys.append(f"<{c}d")
    return keys

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


def _f(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return v


def _sign_bucket(v: Optional[float], *, eps: float = 1e-9) -> str:
    if v is None:
        return "missing"
    if abs(float(v)) <= eps:
        return "zero"
    return "pos" if float(v) > 0 else "neg"


def _tau_oc_from_minute(day_bars: List[dict]) -> Optional[float]:
    """开→收 %：当日分钟首根 open → 末根 close（与 τ 标签同方向口径）。"""
    if not day_bars:
        return None
    o = _f(day_bars[0].get("open"))
    c = _f(day_bars[-1].get("close"))
    if o is None or c is None or o <= 0:
        return None
    return round((float(c) / float(o) - 1.0) * 100.0, 4)


def _path_label_from_minute(day_bars: List[dict]) -> Tuple[Optional[float], str]:
    from core.research.path_panel import extreme_order_path_label

    if not day_bars:
        return None, "empty"
    ref = _f(day_bars[0].get("open"))
    if ref is None or ref <= 0:
        return None, "invalid_ref_or_empty"
    label, reason = extreme_order_path_label(day_bars, ref=float(ref))
    return float(label), str(reason or "")


def build_minute_label_portrait(
    *,
    watching_limit: int = 100,
    period: str = DEFAULT_MINUTE_PERIOD,
    max_days_per_code: int = DEFAULT_LABEL_MAX_DAYS,
    codes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """观察池 5m 上 τ(OC) / path(极值序) 标签数量画像（含同号/异号）。"""
    from quant.research.factor_ols_clusters import clamp_watching_limit
    from core.ports.market import group_minute_bars_by_date, resolve_market_code
    from core.store import load_minute_cache

    limit = clamp_watching_limit(watching_limit, 100)
    period_s = str(period or DEFAULT_MINUTE_PERIOD)
    max_days = max(10, min(int(max_days_per_code or DEFAULT_LABEL_MAX_DAYS), 400))
    code_list = [str(c).strip() for c in (codes or _resolve_watching_codes(watching_limit=limit)) if str(c).strip()]

    tau = {"pos": 0, "neg": 0, "zero": 0, "missing": 0}
    path = {"pos": 0, "neg": 0, "zero": 0, "missing": 0}
    path_reason: Dict[str, int] = {}
    joint = {"same_sign": 0, "opposite_sign": 0, "flat": 0}
    codes_with_cache = 0
    days_scanned = 0

    for code in code_list:
        try:
            market, sym = resolve_market_code(code)
            packed = load_minute_cache(
                market,
                sym,
                period_s,
                min_bars=10,
                ignore_age=True,
            )
        except Exception:  # noqa: BLE001
            logger.debug("label portrait load failed for %s", code, exc_info=True)
            packed = None
        if not packed:
            continue
        bars, _meta = packed
        by_day = group_minute_bars_by_date(bars or []) or {}
        if not by_day:
            continue
        codes_with_cache += 1
        day_keys = sorted(str(k)[:10] for k in by_day.keys() if str(k)[:10])
        if len(day_keys) > max_days:
            day_keys = day_keys[-max_days:]
        for dkey in day_keys:
            raw = by_day.get(dkey) or by_day.get(dkey[:10]) or []
            day_bars = [b for b in raw if isinstance(b, dict)]
            if not day_bars:
                continue
            days_scanned += 1
            tau_v = _tau_oc_from_minute(day_bars)
            path_v, reason = _path_label_from_minute(day_bars)
            tb = _sign_bucket(tau_v)
            pb = _sign_bucket(path_v)
            tau[tb] = int(tau.get(tb) or 0) + 1
            path[pb] = int(path.get(pb) or 0) + 1
            if reason:
                path_reason[reason] = int(path_reason.get(reason) or 0) + 1
            if tb in ("pos", "neg") and pb in ("pos", "neg"):
                if tb == pb:
                    joint["same_sign"] += 1
                else:
                    joint["opposite_sign"] += 1
            else:
                joint["flat"] += 1

    tau_n = int(tau["pos"] + tau["neg"] + tau["zero"] + tau["missing"])
    path_n = int(path["pos"] + path["neg"] + path["zero"] + path["missing"])
    signed_n = int(joint["same_sign"] + joint["opposite_sign"])
    return {
        "success": True,
        "period": period_s,
        "watching_limit": limit,
        "universe_count": len(code_list),
        "codes_with_cache": codes_with_cache,
        "days_scanned": days_scanned,
        "max_days_per_code": max_days,
        "tau": {
            **tau,
            "n": tau_n,
            "pos_share": round(tau["pos"] / float(tau["pos"] + tau["neg"]), 4)
            if (tau["pos"] + tau["neg"])
            else None,
        },
        "path": {
            **path,
            "n": path_n,
            "pos_share": round(path["pos"] / float(path["pos"] + path["neg"]), 4)
            if (path["pos"] + path["neg"])
            else None,
            "reasons": [
                {"reason": k, "count": v}
                for k, v in sorted(path_reason.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
        },
        "joint": {
            **joint,
            "signed_n": signed_n,
            "same_sign_rate": round(joint["same_sign"] / float(signed_n), 4)
            if signed_n
            else None,
        },
        "note": "τ=当日分钟开→收%；path=极值序 signed (H−L)/open%；按票截断最近 max_days",
    }


def _cached_minute_label_portrait(
    *,
    watching_limit: int,
    period: str,
    codes: List[str],
) -> Dict[str, Any]:
    key = f"{watching_limit}|{period}|{len(codes)}|{','.join(codes[:8])}|{codes[-1] if codes else ''}"
    now = time.time()
    if (
        _label_portrait_cache.get("key") == key
        and isinstance(_label_portrait_cache.get("payload"), dict)
        and (now - float(_label_portrait_cache.get("at") or 0.0)) < _LABEL_PORTRAIT_TTL_SEC
    ):
        out = dict(_label_portrait_cache["payload"])
        out["cached"] = True
        return out
    payload = build_minute_label_portrait(
        watching_limit=watching_limit,
        period=period,
        codes=codes,
    )
    _label_portrait_cache["key"] = key
    _label_portrait_cache["at"] = now
    _label_portrait_cache["payload"] = payload
    out = dict(payload)
    out["cached"] = False
    return out


def build_cluster_minute_status(
    *,
    watching_limit: int = 100,
    period: str = DEFAULT_MINUTE_PERIOD,
    min_span_days: int = DEFAULT_MIN_SPAN_DAYS,
    stale_hours: float = 24.0,
    include_label_portrait: bool = True,
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
    bucket_keys = _span_mix_bucket_keys(min_span)
    span_buckets: Dict[str, int] = {k: 0 for k in bucket_keys}

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
            # 累计阈值：span=5 → 同时计入 <30d / <20d / <10d
            for key in bucket_keys:
                try:
                    cut = int(str(key).strip("<>d"))
                except (TypeError, ValueError):
                    continue
                if span < cut:
                    span_buckets[key] += 1

    total = len(codes)
    spans_sorted = sorted(spans)
    med_span = spans_sorted[len(spans_sorted) // 2] if spans_sorted else 0
    coverage_ok = bool(total) and missing == 0 and short == 0

    try:
        from core.store import bars_backend

        backend = bars_backend()
    except Exception:  # noqa: BLE001
        backend = "unknown"

    # 细档也返回 0，便于 UI 固定画出 <20d / <10d
    dist = [{"bucket": k, "count": int(span_buckets.get(k, 0) or 0)} for k in bucket_keys]

    label_portrait: Optional[Dict[str, Any]] = None
    if include_label_portrait:
        try:
            label_portrait = _cached_minute_label_portrait(
                watching_limit=limit,
                period=period_s,
                codes=codes,
            )
        except Exception:  # noqa: BLE001 — best-effort；覆盖状态仍返回
            logger.debug("minute label portrait failed", exc_info=True)
            label_portrait = {"success": False, "error": "label_portrait_failed"}

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
        "label_portrait": label_portrait,
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


def _minute_fetched_today(
    fetched_at: Any, *, now: Optional[datetime] = None
) -> bool:
    """本地仓 ``fetched_at`` 是否落在今天（日历日）；增量补齐同日只拉一次。"""
    s = str(fetched_at or "").strip()
    if not s:
        return False
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return False
    today = (now or datetime.now()).date()
    return dt.date() == today


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
    """观察池 5m 增量补齐：今日已拉过跳过；已对齐跳过；跨度够只拉近几日；缺/短才全窗口。"""
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
    now = datetime.now()
    n_workers = max(1, min(int(workers or DEFAULT_TOPUP_WORKERS), 8, len(watch)))

    skipped_aligned = 0
    skipped_today = 0
    topped = 0
    bootstrapped = 0
    warmed = 0
    errors: List[str] = []
    total = len(watch)
    done = 0

    def _plan(code: str) -> Tuple[str, str, int, bool]:
        """返回 (code, action, lookback, skip_em)。action: skip|skip_today|topup|full。"""
        snap = _minute_snapshot_for_code(code, period=period_s)
        if not snap:
            return code, "full", full_lb, False
        # 同日已拉过：增量不再打远端（含 Short 新浪近端空转）；强更 5m 不受此限
        if _minute_fetched_today(snap.get("fetched_at"), now=now):
            return code, "skip_today", 0, True
        span = int(snap.get("span_days") or 0)
        date_max = str(snap.get("date_max") or "")[:10]
        if span >= min_span and expected and date_max and date_max >= expected:
            return code, "skip", 0, True
        if span >= min_span:
            return code, "topup", top_lb, True
        return code, "full", full_lb, False

    plans = [_plan(c) for c in watch]
    to_fetch = [
        (c, act, lb, sem)
        for c, act, lb, sem in plans
        if act not in ("skip", "skip_today")
    ]
    skipped_aligned = sum(1 for _, act, _, _ in plans if act == "skip")
    skipped_today = sum(1 for _, act, _, _ in plans if act == "skip_today")
    skipped_total = skipped_aligned + skipped_today
    warmed += skipped_total
    done = skipped_total
    if progress_cb and skipped_total:
        try:
            bits = []
            if skipped_today:
                bits.append(f"今日跳过 ×{skipped_today}")
            if skipped_aligned:
                bits.append(f"对齐跳过 ×{skipped_aligned}")
            progress_cb(done, total, " · ".join(bits) or f"skip ×{skipped_total}")
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
        "skipped_today": skipped_today,
        "topped": topped,
        "bootstrapped": bootstrapped,
        "skipped_ready": skipped_aligned + skipped_today,
        "workers": n_workers,
        "errors": errors[:10],
        "note": (
            f"5m 增量补齐 · 今日已拉跳过 · 对齐跳过 · 近 {top_lb} 日 topup（优先新浪/腾讯）· "
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
    # 强更后作废标签画像缓存，强制重扫
    _label_portrait_cache["key"] = None
    _label_portrait_cache["at"] = 0.0
    _label_portrait_cache["payload"] = None
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
