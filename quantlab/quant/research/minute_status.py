"""观察池 5m 分钟线缓存覆盖与预热（研究枢纽 UI）。"""

from __future__ import annotations

import logging
import time
from concurrent.futures import (
    FIRST_COMPLETED,
    ThreadPoolExecutor,
    wait,
)
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.data.policy import MINUTE_EM_LOOKBACK_DAYS, MINUTE_WARMUP_READY_MIN_SPAN_DAYS
from core.watching.store import WATCHING_MAX_SIZE

logger = logging.getLogger(__name__)

DEFAULT_MINUTE_PERIOD = "5"
DEFAULT_MIN_SPAN_DAYS = MINUTE_WARMUP_READY_MIN_SPAN_DAYS
DEFAULT_LOOKBACK_DAYS = MINUTE_EM_LOOKBACK_DAYS
# 增量补齐：跨度已够的票只拉近几日。与 Ready「最近 N 日无缺」分开，避免无缺窗口变长后整池打东财全历史。
DEFAULT_TOPUP_SPAN_DAYS = 20
DEFAULT_TOPUP_LOOKBACK_DAYS = 5
DEFAULT_TOPUP_WORKERS = 4
DEFAULT_LABEL_MAX_DAYS = 120
# Span mix 累计档：有缓存标的按跨度同时计入更宽档（与 Ready 闸独立）
SPAN_MIX_CUTS = (30, 40, 50)
_LABEL_PORTRAIT_TTL_SEC = 90.0
_label_portrait_cache: Dict[str, Any] = {"key": None, "at": 0.0, "payload": None}


def _span_mix_bucket_keys() -> List[str]:
    """固定 ``<30d`` / ``<40d`` / ``<50d``（严→宽）。"""
    return [f"<{int(c)}d" for c in SPAN_MIX_CUTS if int(c) > 0]

def _resolve_watching_codes(*, watching_limit: int = WATCHING_MAX_SIZE) -> List[str]:
    from quant.research.watching_universe import clamp_watching_limit, merge_cluster_universe

    limit = clamp_watching_limit(watching_limit, WATCHING_MAX_SIZE)
    watchlist: List[Any] = []
    try:
        from core.watching.store import read_watching

        watchlist = list((read_watching() or {}).get("watchlist") or [])
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("read_watching failed in minute_status", exc_info=True)
        watchlist = []

    uni = merge_cluster_universe(
        watchlist,
        [],
        watching_limit=limit,
        universe_mode="watching",
    )
    return [str(c).strip() for c in (uni.get("codes") or []) if str(c).strip()]


def _minute_bar_hm(bar: Any) -> Optional[Tuple[int, int]]:
    if not isinstance(bar, dict):
        return None
    ts = bar.get("datetime") or bar.get("time") or bar.get("date")
    s = str(ts or "").strip()
    if len(s) >= 16 and s[13:15].isdigit():
        try:
            return int(s[11:13]), int(s[14:16])
        except ValueError:
            return None
    if ":" in s:
        parts = s.replace("T", " ").split()[-1].split(":")
        if len(parts) >= 2:
            try:
                return int(parts[0]), int(parts[1])
            except ValueError:
                return None
    return None


def _minute_day_complete(day_bars: Optional[List[dict]]) -> bool:
    """单日 5m 是否齐到收盘窗（末根≥14:55 或根数≥40）。"""
    rows = [b for b in (day_bars or []) if isinstance(b, dict)]
    if len(rows) < 2:
        return False
    if len(rows) >= 40:
        return True
    hm = _minute_bar_hm(rows[-1])
    if not hm:
        return False
    return hm[0] > 14 or (hm[0] == 14 and hm[1] >= 55)


def _minute_snapshot_for_code(code: str, *, period: str = DEFAULT_MINUTE_PERIOD) -> Optional[Dict[str, Any]]:
    try:
        from core.ports.market import resolve_market_code
        from core.store import load_minute_span_snapshot

        market, sym = resolve_market_code(code)
        snap = load_minute_span_snapshot(
            market, sym, str(period or DEFAULT_MINUTE_PERIOD)
        )
        if not snap:
            return None
        day_bars = list(snap.get("date_max_bars") or [])
        last_hm = _minute_bar_hm(day_bars[-1]) if day_bars else None
        fetched_at = str(snap.get("fetched_at") or "")[:19]
        return {
            "span_days": int(snap.get("span_days") or 0),
            "bar_count": int(snap.get("bar_count") or 0),
            "fetched_at": fetched_at or None,
            "date_min": str(snap.get("date_min") or "")[:10] or None,
            "date_max": str(snap.get("date_max") or "")[:10] or None,
            "date_max_bars": len(day_bars),
            "last_bar_hm": f"{last_hm[0]:02d}:{last_hm[1]:02d}" if last_hm else None,
            "session_complete": _minute_day_complete(day_bars) if day_bars else False,
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

    Ready：最近 ``min_span_days`` 个交易日无缺（miss/head/tail/both/gap；盘中当日不算），
    且 ``fetched_at`` 未过期、``date_max`` 够新。
    返回 ``(ready, snapshot, reason)``；reason 为 ``ready`` | ``missing`` | ``gap`` | ``stale`` | ``date_max_old``。
    """
    from quant.research.bars_integrity import minute_windows_clean

    snap = _minute_snapshot_for_code(code, period=period)
    if not snap:
        return False, None, "missing"
    window = max(1, int(min_span_days or DEFAULT_MIN_SPAN_DAYS))
    clean = minute_windows_clean([code], days=window)
    bare = "".join(ch for ch in str(code) if ch.isdigit())[-6:]
    if not clean.get(bare, False):
        return False, snap, "gap"
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


def _minute_day_extremes(minute_bars: Sequence[dict]) -> Tuple[Optional[float], Optional[float]]:
    day_hi: Optional[float] = None
    day_lo: Optional[float] = None
    for bar in minute_bars or []:
        if not isinstance(bar, dict):
            continue
        hi = _f(bar.get("high"))
        lo = _f(bar.get("low"))
        if hi is not None:
            day_hi = hi if day_hi is None else max(day_hi, hi)
        if lo is not None:
            day_lo = lo if day_lo is None else min(day_lo, lo)
    return day_hi, day_lo


def _first_extreme_bar_indices(
    minute_bars: Sequence[dict],
    day_high: float,
    day_low: float,
    *,
    eps: float = 1e-6,
) -> Tuple[Optional[int], Optional[int]]:
    low_idx: Optional[int] = None
    high_idx: Optional[int] = None
    for i, bar in enumerate(minute_bars or []):
        if not isinstance(bar, dict):
            continue
        lo = _f(bar.get("low"))
        hi = _f(bar.get("high"))
        if low_idx is None and lo is not None and lo <= day_low + eps:
            low_idx = i
        if high_idx is None and hi is not None and hi >= day_high - eps:
            high_idx = i
        if low_idx is not None and high_idx is not None:
            break
    return low_idx, high_idx


def _extreme_order_label(minute_bars: Sequence[dict], *, ref: float) -> Tuple[float, str]:
    """先 low→high 则 (H−L)/ref%；先 high→low 则 (L−H)/ref%。"""
    if ref <= 0 or not minute_bars:
        return 0.0, "invalid_ref_or_empty"
    day_hi, day_lo = _minute_day_extremes(minute_bars)
    if day_hi is None or day_lo is None:
        return 0.0, "invalid_ref_or_empty"
    span = float(day_hi) - float(day_lo)
    if span <= 1e-9:
        return 0.0, "flat_range"
    low_idx, high_idx = _first_extreme_bar_indices(minute_bars, day_hi, day_lo)
    if low_idx is None or high_idx is None:
        return 0.0, "invalid_ref_or_empty"
    if low_idx == high_idx:
        return 0.0, "same_bar_extreme"
    span_pct = span / float(ref) * 100.0
    if low_idx < high_idx:
        return round(span_pct, 4), "low_then_high"
    return round(-span_pct, 4), "high_then_low"


def _path_label_from_minute(day_bars: List[dict]) -> Tuple[Optional[float], str]:
    if not day_bars:
        return None, "empty"
    ref = _f(day_bars[0].get("open"))
    if ref is None or ref <= 0:
        return None, "invalid_ref_or_empty"
    label, reason = _extreme_order_label(day_bars, ref=float(ref))
    return float(label), str(reason or "")


def build_minute_label_portrait(
    *,
    watching_limit: int = WATCHING_MAX_SIZE,
    period: str = DEFAULT_MINUTE_PERIOD,
    max_days_per_code: int = DEFAULT_LABEL_MAX_DAYS,
    codes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """观察池 5m 上 τ(OC) / path(极值序) 标签数量画像（含同号/异号）。"""
    from quant.research.watching_universe import clamp_watching_limit
    from core.ports.market import group_minute_bars_by_date, resolve_market_code
    from core.store import load_minute_since, load_minute_span_snapshot

    limit = clamp_watching_limit(watching_limit, WATCHING_MAX_SIZE)
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
            snap = load_minute_span_snapshot(market, sym, period_s)
            if not snap or not snap.get("date_max"):
                continue
            # 只用近 max_days 个日历日窗口，避免全历史进内存
            try:
                d_max = datetime.strptime(str(snap["date_max"])[:10], "%Y-%m-%d")
                d0 = (d_max - timedelta(days=max(max_days * 2, max_days + 40))).strftime(
                    "%Y-%m-%d"
                )
            except (TypeError, ValueError):
                d0 = str(snap.get("date_min") or "")[:10]
            packed = load_minute_since(
                market,
                sym,
                period_s,
                date_min=d0 or str(snap.get("date_min") or "")[:10],
                min_bars=10,
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


def build_minute_status(
    *,
    watching_limit: int = WATCHING_MAX_SIZE,
    period: str = DEFAULT_MINUTE_PERIOD,
    min_span_days: int = DEFAULT_MIN_SPAN_DAYS,
    stale_hours: float = 24.0,
    include_label_portrait: bool = False,
) -> Dict[str, Any]:
    """汇总观察池截断后的 5m 分钟缓存覆盖。

    ``include_label_portrait`` 默认 False：覆盖状态热路径不扫全历史；
    UI 进页后再单独请求画像。
    """
    from quant.research.bars_integrity import minute_windows_clean
    from quant.research.watching_universe import clamp_watching_limit

    limit = clamp_watching_limit(watching_limit, WATCHING_MAX_SIZE)
    period_s = str(period or DEFAULT_MINUTE_PERIOD)
    window = max(1, int(min_span_days or DEFAULT_MIN_SPAN_DAYS))
    codes = _resolve_watching_codes(watching_limit=limit)
    clean_map = minute_windows_clean(codes, days=window)

    cached_ok = 0
    short = 0
    missing = 0
    stale = 0
    spans: List[int] = []
    bucket_keys = _span_mix_bucket_keys()
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
        bare = "".join(ch for ch in str(code) if ch.isdigit())[-6:]
        if clean_map.get(str(code).strip(), False) or clean_map.get(bare, False):
            cached_ok += 1
        else:
            short += 1
        # 累计：span=35 → <40d 与 <50d；与 Ready 是否过闸无关
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

    # 三档均返回（含 0），便于 UI 固定画出 <30d / <40d / <50d
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
        "task": "minute_status",
        "period": period_s,
        "watching_limit": limit,
        "universe_count": total,
        "universe_mode": "watching",
        "min_span_days": window,
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
        "note": "5m 分钟线本地仓；供 ŷ_hl / T0 回测 / tail_anomaly",
        "refresh_job": _minute_refresh_job_snapshot(),
    }


def _minute_refresh_job_snapshot() -> Optional[Dict[str, Any]]:
    from core.job_progress import minute_refresh_job
    from quant.research.cluster_refresh_job_snapshot import public_refresh_job_snapshot

    return public_refresh_job_snapshot(
        minute_refresh_job,
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
    min_span_days: int = DEFAULT_TOPUP_SPAN_DAYS,
    workers: int = DEFAULT_TOPUP_WORKERS,
    progress_cb: Optional[Any] = None,
) -> Dict[str, Any]:
    """观察池 5m 增量补齐：今日已拉且会话日齐窗才跳过；缺尾（如只到 10:00）强制补拉。

    远端走 ``fetch_a_minute_bars_isolated``：单票子进程超时 kill，不占主进程 ``ak_lock``，
    避免增量补齐再卡死在「超时收尾」。
    """
    from adapters.market.minute_history import fetch_a_minute_bars_isolated
    from core.data.policy import minute_em_lookback_days, minute_isolated_timeout_sec

    watch = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if not watch:
        return {"ok": False, "error": "无标的", "kind": "minute_topup", "total": 0}

    period_s = str(period or DEFAULT_MINUTE_PERIOD)
    em_cap = minute_em_lookback_days()
    full_lb = min(max(int(full_lookback_days or DEFAULT_LOOKBACK_DAYS), 5), em_cap)
    top_lb = min(max(int(topup_lookback_days or DEFAULT_TOPUP_LOOKBACK_DAYS), 2), full_lb)
    min_span = max(1, int(min_span_days or DEFAULT_TOPUP_SPAN_DAYS))
    expected = expected_minute_asof()
    now = datetime.now()
    n_workers = max(1, min(int(workers or DEFAULT_TOPUP_WORKERS), 8, len(watch)))
    timeout_em = minute_isolated_timeout_sec(skip_em=False)
    timeout_skip_em = minute_isolated_timeout_sec(skip_em=True)

    skipped_aligned = 0
    skipped_today = 0
    topped = 0
    bootstrapped = 0
    refreshed_truncated = 0
    warmed = 0
    timed_out = 0
    errors: List[str] = []
    total = len(watch)
    done = 0
    batch_timeout = 0.0

    def _plan(code: str) -> Tuple[str, str, int, bool, bool]:
        """返回 (code, action, lookback, skip_em, force)。action: skip|skip_today|topup|full。"""
        snap = _minute_snapshot_for_code(code, period=period_s)
        if not snap:
            return code, "full", full_lb, False, False
        span = int(snap.get("span_days") or 0)
        date_max = str(snap.get("date_max") or "")[:10]
        on_expected = bool(expected and date_max and date_max >= expected)
        # 会话缺尾：新浪近端强刷（有超时）；不打东财/BaoStock，避免增量整批挂死
        if on_expected and not bool(snap.get("session_complete")):
            return code, "topup", top_lb, True, True
        # 同日已拉且会话齐窗：增量不再打远端（含 Short 新浪近端空转）；强更 5m 不受此限
        if _minute_fetched_today(snap.get("fetched_at"), now=now):
            return code, "skip_today", 0, True, False
        if span >= min_span and on_expected:
            return code, "skip", 0, True, False
        if span >= min_span:
            return code, "topup", top_lb, True, False
        return code, "full", full_lb, False, False

    plans = [_plan(c) for c in watch]
    truncated_codes = {c for c, act, _lb, _sem, force in plans if act == "topup" and force}
    to_fetch = [
        (c, act, lb, sem)
        for c, act, lb, sem, _force in plans
        if act not in ("skip", "skip_today")
    ]
    skipped_aligned = sum(1 for _, act, _, _, _ in plans if act == "skip")
    skipped_today = sum(1 for _, act, _, _, _ in plans if act == "skip_today")
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
        # 会话缺尾：跳过读仓强刷；其余增量仍用短 TTL 读短路
        force = code in truncated_codes
        timeout_sec = timeout_skip_em if skip_em else timeout_em
        bars, meta = fetch_a_minute_bars_isolated(
            code,
            timeout_sec=timeout_sec,
            period=period_s,
            use_cache=not force,
            lookback_days=lookback,
            max_age_hours=0.01,
            skip_em=skip_em,
            skip_bs=action == "topup",
        )
        if bars:
            return code, action, True, None
        err = str((meta or {}).get("error") or "empty")
        return code, action, False, err

    if to_fetch:
        # 勿用 ``with ThreadPoolExecutor``：挂死任务 cancel 不掉，exit 会 wait=True 卡死 Job
        # 子进程单票可 kill；批上限按「每票超时 / 并发」估，避免再卡死在 900s 假收尾
        waves = max(1, (len(to_fetch) + n_workers - 1) // n_workers)
        batch_timeout = max(180.0, min(3600.0, timeout_em * float(waves) + 60.0))
        t0 = time.time()
        pool = ThreadPoolExecutor(max_workers=n_workers)
        try:
            futs = {
                pool.submit(_fetch_one, c, act, lb, sem): (c, act)
                for c, act, lb, sem in to_fetch
            }
            pending = set(futs.keys())
            try:
                while pending:
                    finished, pending = wait(
                        pending, timeout=1.5, return_when=FIRST_COMPLETED
                    )
                    if not finished:
                        if time.time() - t0 >= batch_timeout:
                            raise FuturesTimeout()
                        continue
                    for fut in finished:
                        code, action = futs[fut]
                        try:
                            _c, act, ok_fetch, err = fut.result(timeout=0.1)
                        except Exception as exc:  # noqa: BLE001
                            ok_fetch, act, err = False, action, str(exc)[:80]
                        done += 1
                        if ok_fetch:
                            warmed += 1
                            if act == "topup":
                                topped += 1
                                if code in truncated_codes:
                                    refreshed_truncated += 1
                            else:
                                bootstrapped += 1
                        elif err:
                            if "timeout" in err.lower():
                                timed_out += 1
                            errors.append(f"{code}:{err}")
                        if progress_cb:
                            try:
                                progress_cb(done, total, f"{act} {code}")
                            except Exception:  # noqa: BLE001
                                logger.debug("minute topup progress_cb failed", exc_info=True)
            except FuturesTimeout:
                for fut in list(pending):
                    code, _action = futs[fut]
                    fut.cancel()
                    timed_out += 1
                    errors.append(f"{code}:batch_timeout")
                    done += 1
                    if progress_cb:
                        try:
                            progress_cb(done, total, f"timeout {code}")
                        except Exception:  # noqa: BLE001
                            logger.debug("minute topup progress_cb failed", exc_info=True)
                if progress_cb:
                    try:
                        progress_cb(
                            done,
                            total,
                            f"超时收尾 {done}/{total} · 上限 {int(batch_timeout)}s",
                        )
                    except Exception:  # noqa: BLE001
                        logger.debug("minute topup progress_cb failed", exc_info=True)
        finally:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                pool.shutdown(wait=False)

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
        "refreshed_truncated": refreshed_truncated,
        "skipped_ready": skipped_aligned + skipped_today,
        "timed_out": timed_out,
        "isolated": True,
        "workers": n_workers,
        "errors": errors[:10],
        "note": (
            f"5m 增量补齐 · 齐窗才今日/对齐跳过 · 缺尾走新浪近端 · 近 {top_lb} 日 topup · "
            f"缺/短全窗 {full_lb} 日 · 子进程隔离 · {n_workers} 并发"
            + (f" · 批上限 {int(batch_timeout)}s" if to_fetch else "")
        ),
    }


def refresh_minute_only(
    *,
    watching_limit: int = WATCHING_MAX_SIZE,
    period: str = DEFAULT_MINUTE_PERIOD,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    mode: str = "full",
    topup_lookback_days: int = DEFAULT_TOPUP_LOOKBACK_DAYS,
    progress_cb: Optional[Any] = None,
) -> Dict[str, Any]:
    """预热观察池 5m 分钟线（研究枢纽 UI；不占用 schedule slot）。

    ``mode=full``：原强更（Ready 跳过 + 全窗口重拉）。
    ``mode=topup``：增量补齐（已对齐跳过；跨度够只补近几日；供预演调仓日常刷新）。
    ``mode=repair``：东财补缺（只写比本地更齐的交易日，不打新浪）。
    """
    from core.schedule_jobs import _minute_warmup_core
    from core.data.policy import MINUTE_EM_LOOKBACK_MAX_DAYS, minute_em_lookback_days

    limit = max(1, int(watching_limit or WATCHING_MAX_SIZE))
    period_s = str(period or DEFAULT_MINUTE_PERIOD)
    mode_s = str(mode or "full").strip().lower()
    if mode_s not in ("full", "topup", "repair"):
        mode_s = "full"
    cap = int(MINUTE_EM_LOOKBACK_MAX_DAYS) if mode_s == "repair" else minute_em_lookback_days()
    default_lb = cap if mode_s == "repair" else DEFAULT_LOOKBACK_DAYS
    lb = min(max(int(lookback_days or default_lb), 5), cap)
    codes = _resolve_watching_codes(watching_limit=limit)
    n_codes = len(codes)
    if n_codes < 1:
        return {
            "success": False,
            "error": "观察池为空，无法预热分钟线",
            "watching_limit": limit,
            "universe_count": 0,
            "task": "minute_refresh",
            "mode": mode_s,
        }

    def _on_progress(cur: int, total: int, code: str) -> None:
        if not progress_cb:
            return
        label = {"topup": "增量", "repair": "补缺"}.get(mode_s, "预热")
        try:
            progress_cb(f"{label} 5m {code} ({cur}/{total})", int(cur or 0), int(total or n_codes))
        except Exception:  # noqa: BLE001 — best-effort 进度回调
            logger.debug("cluster minute refresh progress_cb failed", exc_info=True)

    if mode_s == "repair":
        from quant.research.minute_em_repair import repair_minute_from_em

        warmup = repair_minute_from_em(
            watching_limit=limit,
            lookback_days=lb,
            period=period_s,
            progress_cb=_on_progress,
        )
    elif mode_s == "topup":
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
    status = build_minute_status(watching_limit=limit, period=period_s)
    ok = bool(warmup.get("ok"))
    return {
        "success": ok,
        "task": "minute_refresh",
        "mode": mode_s,
        "watching_limit": limit,
        "universe_count": n_codes,
        "lookback_days": lb,
        "period": period_s,
        "minute_warmup": warmup,
        "status": status,
        "error": None if ok else str(warmup.get("error") or "分钟预热失败"),
    }
