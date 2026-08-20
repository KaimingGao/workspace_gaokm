"""市场上下文 ingest 合并与新鲜度工具。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Tuple


def _parse_ts(raw: Any) -> Optional[datetime]:
    s = str(raw or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19] if "T" in fmt else s[:10], fmt)
        except ValueError:
            continue
    return None


def snapshot_age_hours(snap: Optional[dict], *, now: Optional[datetime] = None) -> Optional[float]:
    if not isinstance(snap, dict):
        return None
    ts = _parse_ts(snap.get("fetched_at") or snap.get("as_of"))
    if ts is None:
        return None
    ref = now or datetime.now()
    return max(0.0, (ref - ts).total_seconds() / 3600.0)


def is_snapshot_stale(
    snap: Optional[dict],
    *,
    max_age_hours: float = 24.0,
) -> bool:
    age = snapshot_age_hours(snap)
    if age is None:
        return True
    return age > float(max_age_hours)


def merge_series_dict(
    fresh: Optional[dict],
    prior: Optional[dict],
) -> Dict[str, Any]:
    """按 key 合并 macro series：fresh 优先，缺失项保留 prior。"""
    out = dict(prior or {})
    for k, v in dict(fresh or {}).items():
        if isinstance(v, dict) and v.get("close") is not None:
            out[k] = v
        elif k not in out:
            out[k] = v
    return out


def prune_macro_errors(errors: list, series: Optional[dict]) -> list:
    """去掉 series 已有 close 的键对应 error，避免 merge 后 stale empty 误报。"""
    ser = series if isinstance(series, dict) else {}
    kept = []
    for e in errors or []:
        key = str(e or "").split(":", 1)[0].strip()
        slot = ser.get(key) if key else None
        close = (slot or {}).get("close") if isinstance(slot, dict) else None
        if close is not None:
            continue
        kept.append(str(e))
    return sorted(set(kept))[:20]


# 兼容旧私有名（热重载 / 外部引用）
_prune_macro_errors = prune_macro_errors


def merge_macro_snapshots(
    fresh: Dict[str, Any],
    prior: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not isinstance(prior, dict):
        out = dict(fresh or {})
        out["errors"] = prune_macro_errors(out.get("errors"), out.get("series"))
        return out
    merged = dict(prior)
    merged.update({k: v for k, v in fresh.items() if k not in ("series", "errors")})
    merged["series"] = merge_series_dict(
        (fresh or {}).get("series"),
        prior.get("series"),
    )
    errs = list(prior.get("errors") or [])
    errs.extend(list(fresh.get("errors") or []))
    merged["errors"] = prune_macro_errors(errs, merged.get("series"))
    # 重算聚合字段
    tech_rets = []
    for key in ("sox", "ndx", "qqq", "kweb"):
        v = (merged.get("series") or {}).get(key, {}).get("change_1d_pct")
        if v is not None:
            tech_rets.append(float(v))
    merged["overseas_tech_1d_pct"] = (
        round(sum(tech_rets) / len(tech_rets), 4) if tech_rets else prior.get("overseas_tech_1d_pct")
    )
    merged["liquidity_stress_score"] = fresh.get("liquidity_stress_score")
    if merged["liquidity_stress_score"] is None:
        merged["liquidity_stress_score"] = prior.get("liquidity_stress_score")
    merged["merged_from_prior"] = True
    merged["success"] = bool(merged.get("series"))
    return merged


def freshness_report(
    ctx: Dict[str, Any],
    *,
    warn_hours: float = 18.0,
    stale_hours: float = 36.0,
) -> Dict[str, Any]:
    """汇总 macro/sentiment/announcement 新鲜度。"""
    parts: Dict[str, Any] = {}
    any_stale = False
    for key in ("macro", "market_sentiment", "announcement"):
        snap = (ctx or {}).get(key)
        age = snapshot_age_hours(snap if isinstance(snap, dict) else None)
        stale = is_snapshot_stale(snap if isinstance(snap, dict) else None, max_age_hours=stale_hours)
        warn = bool(age is not None and age > warn_hours)
        any_stale = any_stale or stale
        parts[key] = {
            "age_hours": round(age, 2) if age is not None else None,
            "warn": warn,
            "stale": stale,
            "as_of": (snap or {}).get("as_of") if isinstance(snap, dict) else None,
        }
    return {"parts": parts, "any_stale": any_stale, "needs_ingest": any_stale}
