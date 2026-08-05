"""薄 DataService（N1 / M1）：上层唯一读窗口，委托 core.ports，附带质量与可审计元数据。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.store import assess_quality

# 日线默认复权策略（写入 manifest / 质量摘要；实际源可能降级为 none/cached）
DEFAULT_ADJUST_POLICY = "qfq"

# 基本面 / 资讯快照 TTL（小时）
FUNDAMENTALS_CACHE_HOURS = 24.0
NEWS_CACHE_HOURS = 6.0


def allows_production_score(
    *,
    quality_level: Optional[str] = None,
    fallback: bool = False,
) -> Tuple[bool, str]:
    """P1 清洗门禁：仅 good 且非 fallback 的日线可进生产 score。

    返回 (allowed, reject_reason)。reason 空串表示放行。
    """
    if fallback:
        return False, "data_quality_gate:fallback"
    level = str(quality_level or "empty").strip().lower() or "empty"
    if level == "empty":
        return False, "data_quality_gate:empty"
    if level == "thin":
        return False, "data_quality_gate:thin"
    if level != "good":
        return False, f"data_quality_gate:{level}"
    return True, ""


def infer_adjust(data_source: str, *, policy: str = DEFAULT_ADJUST_POLICY) -> str:
    """由 data_source 推断实际复权标签；policy 为声明的目标策略。"""
    src = str(data_source or "")
    if "raw" in src or src.endswith("_none"):
        return "none"
    if src.startswith("cache:"):
        return "cached"
    if "fallback" in src or src in ("empty", "quote_fallback"):
        return "none"
    return policy or DEFAULT_ADJUST_POLICY


def get_quote(code: str) -> Dict[str, Any]:
    from core.ports.market import query_quote

    raw = str(code or "").strip()
    quote = query_quote(raw)
    return {
        **(quote if isinstance(quote, dict) else {}),
        "stock_code_query": raw,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "data_source": "tencent_quote" if (quote or {}).get("success") else "empty",
        "non_pit": False,
        "note": "现价快照；非历史 PIT 面板。",
    }


def normalize_adjust_policy(adjust: Optional[str] = None) -> str:
    """D2：规范化复权策略 → qfq|raw|hfq。"""
    p = str(adjust or DEFAULT_ADJUST_POLICY or "qfq").strip().lower()
    if p in ("none", "unadjusted", ""):
        return "raw"
    if p in ("qfq", "raw", "hfq"):
        return p
    return "qfq"


def get_bars(
    code: str,
    *,
    limit: int = 120,
    use_cache: bool = True,
    cache_max_age_hours: float = 24.0,
    as_of: Optional[str] = None,
    incremental: bool = True,
    adjust: Optional[str] = None,
    offline_ok: bool = False,
) -> Dict[str, Any]:
    """返回 {bars, data_source, quality, adjust, adjust_policy, fallback, production_ok, pit}。

    as_of：若给日期，只返回 date <= as_of 的 bars（PIT 切条）。
    incremental：允许缓存增量合并（观察池轻本地史）。
    adjust：qfq|raw|hfq（D2）。
    offline_ok：本地有足够 bars 时不打远端（研究分组）。
    """
    from core.data_pit import bars_as_of
    from core.ports.market import fetch_daily_bars

    policy = normalize_adjust_policy(adjust)
    raw = str(code or "").strip()
    result = fetch_daily_bars(
        raw,
        limit=limit,
        use_cache=use_cache,
        cache_max_age_hours=cache_max_age_hours,
        incremental=incremental,
        adjust=policy,
        offline_ok=offline_ok,
    )
    bars: List[dict]
    data_source: str
    if isinstance(result, tuple) and len(result) >= 2:
        bars, data_source = list(result[0] or []), str(result[1] or "")
    else:
        bars, data_source = list(result or []), "unknown"

    pit_meta: Dict[str, Any]
    cutoff = str(as_of or "").strip()
    if cutoff:
        bars = bars_as_of(bars, cutoff, inclusive=True)
        pit_meta = {
            "bars_pit": True,
            "as_of": cutoff,
            "bar_count": len(bars),
            "note": "日线已 as_of 切条；基本面仍可能非 PIT。",
        }
    else:
        pit_meta = {
            "bars_pit": False,
            "as_of": None,
            "note": "未指定 as_of；回测请用 window_as_of。",
        }

    adjust_tag = infer_adjust(data_source, policy=policy)
    if ":raw" in data_source or policy == "raw":
        adjust_tag = "raw"
    elif ":hfq" in data_source or policy == "hfq":
        adjust_tag = "hfq" if "hfq" in data_source or policy == "hfq" else adjust_tag
    quality = assess_quality(bars, data_source=data_source)
    fallback = data_source in ("empty", "quote_fallback") or "fallback" in data_source
    prod_ok, gate_reason = allows_production_score(
        quality_level=(quality or {}).get("level"),
        fallback=fallback,
    )
    date_min = bars[0].get("date") if bars else None
    date_max = bars[-1].get("date") if bars else None
    return {
        "stock_code": raw,
        "bars": bars,
        "data_source": data_source,
        "adjust": adjust_tag,
        "adjust_policy": policy,
        "fallback": fallback,
        "quality": quality,
        "production_ok": prod_ok,
        "gate_reason": gate_reason or None,
        "pit": pit_meta,
        "bar_count": len(bars),
        "date_min": date_min,
        "date_max": date_max,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "non_pit": False,
    }


def bars_and_source(
    code: str,
    *,
    limit: int = 120,
    **kwargs: Any,
) -> Tuple[List[dict], str]:
    """兼容旧 (bars, data_source) 调用形态。"""
    pack = get_bars(code, limit=limit, **kwargs)
    return list(pack.get("bars") or []), str(pack.get("data_source") or "empty")


def get_fundamentals(
    code: str,
    *,
    use_cache: bool = True,
    cache_max_age_hours: float = FUNDAMENTALS_CACHE_HOURS,
    as_of: Optional[str] = None,
    live: bool = True,
    **kwargs: Any,
) -> Dict[str, Any]:
    """基本面：默认快照；传 as_of 时走 history 面板 PIT 选取（R1）。

    ``live=False``：缓存未命中也不打远端（日报批量用）。
    """
    from core.ports.market import build_fundamentals
    from core.store import load_snapshot_cache, save_snapshot_cache

    raw = str(code or "").strip()
    cutoff = str(as_of or "").strip()

    # R1 PIT 路径：不走 TTL 最新快照，按 as_of 选 history
    if cutoff:
        from core.fundamentals_pit import resolve_fundamentals_for_score
        from core.signal.config import load_signal_config

        fund_cfg = (load_signal_config().get("fundamentals") or {})
        resolved = resolve_fundamentals_for_score(
            raw,
            as_of=cutoff,
            fund_cfg=fund_cfg,
            live_fallback=False,
        )
        metrics = resolved.get("metrics")
        return {
            "success": bool(metrics),
            "stock_code": raw,
            "metrics": metrics or {},
            "as_of": resolved.get("as_of"),
            "decision_as_of": cutoff,
            "data_source": "fundamentals_history_pit",
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "cache_hit": True,
            "non_pit": bool(resolved.get("non_pit")),
            "fundamentals_pit": bool(resolved.get("fundamentals_pit") and resolved.get("ok")),
            "pit_meta": resolved.get("pit_meta") or {},
            "mode": resolved.get("mode"),
            "quality": {"level": "pit" if metrics else "empty"},
            "note": resolved.get("note")
            or "财务 as_of 选取；缺失不回退未来快照。",
        }

    cache_hit = False
    payload: Optional[Dict[str, Any]] = None
    if use_cache:
        cached = load_snapshot_cache(
            "fundamentals",
            raw,
            max_age_hours=cache_max_age_hours,
        )
        if cached:
            payload, meta = cached
            cache_hit = True
            return {
                **(payload if isinstance(payload, dict) else {"metrics": payload}),
                "stock_code": raw,
                "data_source": f"cache:{(meta or {}).get('data_source') or 'fundamentals'}",
                "fetched_at": (meta or {}).get("fetched_at"),
                "cache_hit": True,
                "non_pit": True,
                "fundamentals_pit": False,
                "quality": {"level": "snapshot"},
                "note": "基本面快照缓存；非公告日 PIT。传 as_of= 可走 history。",
            }

    if not live:
        return {
            "success": False,
            "stock_code": raw,
            "error": "cache_miss_no_live",
            "data_source": "empty",
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "cache_hit": False,
            "non_pit": True,
            "fundamentals_pit": False,
            "quality": {"level": "empty"},
            "note": "live=False 且无可用快照缓存",
        }

    try:
        payload_live = build_fundamentals(raw, **kwargs)
    except Exception as e:
        return {
            "success": False,
            "stock_code": raw,
            "error": str(e),
            "data_source": "empty",
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "cache_hit": False,
            "non_pit": True,
            "fundamentals_pit": False,
            "quality": {"level": "empty"},
        }

    if not isinstance(payload_live, dict):
        payload_live = {"success": False, "raw": payload_live}
    src = "akshare_fundamentals"
    if payload_live.get("sources"):
        src = ",".join(str(s) for s in (payload_live.get("sources") or [])[:3]) or src
    fetched_at = datetime.now().isoformat(timespec="seconds")
    if use_cache and payload_live.get("success"):
        try:
            save_snapshot_cache(
                "fundamentals",
                raw,
                payload_live,
                data_source=src,
            )
        except OSError:
            pass
    return {
        **payload_live,
        "stock_code": raw,
        "data_source": src,
        "fetched_at": fetched_at,
        "cache_hit": cache_hit,
        "non_pit": True,
        "fundamentals_pit": False,
        "quality": {"level": "snapshot" if payload_live.get("success") else "empty"},
        "note": "基本面为当前快照；非 PIT。传 as_of= 可走 history。",
    }


def get_news(
    code: str,
    *,
    limit: int = 8,
    use_cache: bool = True,
    cache_max_age_hours: float = NEWS_CACHE_HOURS,
    **kwargs: Any,
) -> Dict[str, Any]:
    """资讯标题快照（非 PIT）；带可审计缓存。"""
    from core.ports.market import build_news
    from core.store import load_snapshot_cache, save_snapshot_cache

    raw = str(code or "").strip()
    if use_cache:
        cached = load_snapshot_cache("news", raw, max_age_hours=cache_max_age_hours)
        if cached:
            payload, meta = cached
            return {
                **(payload if isinstance(payload, dict) else {"items": payload}),
                "stock_code": raw,
                "data_source": f"cache:{(meta or {}).get('data_source') or 'news'}",
                "fetched_at": (meta or {}).get("fetched_at"),
                "cache_hit": True,
                "non_pit": True,
                "quality": {"level": "snapshot"},
                "note": "资讯标题缓存；非历史 PIT 面板。",
            }

    try:
        live = build_news(raw, limit=limit, **kwargs)
    except Exception as e:
        return {
            "success": False,
            "stock_code": raw,
            "error": str(e),
            "items": [],
            "data_source": "empty",
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "cache_hit": False,
            "non_pit": True,
            "quality": {"level": "empty"},
        }

    if not isinstance(live, dict):
        live = {"success": False, "items": []}
    src = "akshare_news"
    fetched_at = datetime.now().isoformat(timespec="seconds")
    if use_cache and (live.get("success") or live.get("items")):
        try:
            save_snapshot_cache("news", raw, live, data_source=src)
        except OSError:
            pass
    return {
        **live,
        "stock_code": raw,
        "data_source": src,
        "fetched_at": fetched_at,
        "cache_hit": False,
        "non_pit": True,
        "quality": {"level": "snapshot" if (live.get("items") or live.get("success")) else "empty"},
        "note": "资讯为实时拉取快照；非 PIT。",
    }


def get_spot(*, force: bool = False) -> Dict[str, Any]:
    """A 股现货列表（估值筛选用）。经 ports，不直调 skills。"""
    from core.ports.market import fetch_a_spot

    rows = fetch_a_spot(force=bool(force))
    src = getattr(fetch_a_spot, "last_source", None) or "akshare_spot"
    # 适配器可能是 bound method / lambda；尽量从底层实现读 last_source
    try:
        from core.ports.adapters import get_adapter

        impl = get_adapter("fetch_a_spot")
        if impl is not None:
            src = getattr(impl, "last_source", None) or src
    except Exception:
        pass
    return {
        "ok": True,
        "rows": rows or [],
        "count": len(rows or []),
        "data_source": src,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "non_pit": True,
        "note": "A 股现货快照；供估值列表；非 PIT。",
    }


def summarize_data_quality(
    codes: Optional[List[str]] = None,
    *,
    limit: int = 60,
) -> Dict[str, Any]:
    """批量质量快照，供 Run Manifest / 调度报告。"""
    watch = [str(c).strip() for c in (codes or []) if str(c).strip()]
    items: List[Dict[str, Any]] = []
    levels = {"good": 0, "thin": 0, "empty": 0}
    fallback_n = 0
    for code in watch:
        try:
            pack = get_bars(code, limit=limit)
        except Exception as e:
            items.append(
                {
                    "stock_code": code,
                    "ok": False,
                    "error": str(e),
                    "quality": {"level": "empty"},
                    "fallback": True,
                    "production_ok": False,
                    "gate_reason": "data_quality_gate:empty",
                    "adjust_policy": DEFAULT_ADJUST_POLICY,
                }
            )
            levels["empty"] += 1
            fallback_n += 1
            continue
        q = pack.get("quality") or {}
        level = str(q.get("level") or "empty")
        levels[level] = levels.get(level, 0) + 1
        if pack.get("fallback"):
            fallback_n += 1
        items.append(
            {
                "stock_code": code,
                "ok": True,
                "data_source": pack.get("data_source"),
                "adjust": pack.get("adjust"),
                "adjust_policy": pack.get("adjust_policy") or DEFAULT_ADJUST_POLICY,
                "fallback": bool(pack.get("fallback")),
                "quality": q,
                "production_ok": bool(pack.get("production_ok", True)),
                "gate_reason": pack.get("gate_reason"),
                "date_min": pack.get("date_min"),
                "date_max": pack.get("date_max"),
                "bar_count": pack.get("bar_count"),
            }
        )
    gated = sum(1 for it in items if not it.get("production_ok", True))
    return {
        "ok": True,
        "count": len(items),
        "levels": levels,
        "fallback_count": fallback_n,
        "gated_count": gated,
        "adjust_policy": DEFAULT_ADJUST_POLICY,
        "items": items,
        "note": "N1/M1 DataService 质量摘要；P1 门禁：thin/empty/fallback 不计 production_ok；不代客下单。",
    }
