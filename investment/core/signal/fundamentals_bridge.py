"""基本面指标桥接：供 value/quality 因子使用（P46 + R1 PIT）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def normalize_fundamentals_metrics(raw: Optional[dict]) -> Optional[Dict[str, Any]]:
    """将 build_fundamentals 或外部 dict 归一化为因子可读字段。"""
    if not raw:
        return None
    src = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else raw
    if not isinstance(src, dict):
        return None

    out: Dict[str, Any] = {}
    for key in (
        "pe",
        "pe_ttm",
        "pb",
        "roe",
        "profit_growth",
        "revenue_growth",
        "market_cap",
        "dividend_yield",
        "eps",
    ):
        val = src.get(key)
        if val is not None:
            out[key] = val
    # 透传报告期，便于落盘 history
    for key in ("as_of", "report_date", "report_period", "end_date", "ann_date"):
        if src.get(key) is not None and key not in out:
            out[key] = src.get(key)
    return out or None


def fetch_score_fundamentals(
    stock_code: str,
    *,
    as_of: Optional[str] = None,
    live_pit: bool = False,
) -> Optional[Dict[str, Any]]:
    """拉取单票基本面摘要（失败返回 None，不阻断 score）。

    - ``as_of`` 非空：财务 PIT 面板
    - ``live_pit=True``：按配置 pit_mode 走 live 同构（X0）
    - 否则：最新快照（兼容旧调用）
    """
    try:
        if live_pit and not as_of:
            from core.signal.live_features import resolve_live_fundamentals

            pack = resolve_live_fundamentals(stock_code)
            return pack.get("metrics")
        if as_of:
            from core.fundamentals_pit import resolve_fundamentals_for_score
            from core.signal.config import load_signal_config

            fund_cfg = load_signal_config().get("fundamentals") or {}
            resolved = resolve_fundamentals_for_score(
                stock_code,
                as_of=as_of,
                fund_cfg=fund_cfg,
                live_fallback=False,
            )
            return resolved.get("metrics")

        from core.data_service import get_fundamentals

        result = get_fundamentals(stock_code)
        if not result.get("success"):
            return None
        return normalize_fundamentals_metrics(result)
    except Exception:
        return None


def fetch_fundamentals_batch(
    codes: List[str],
    *,
    as_of: Optional[str] = None,
    live: bool = True,
) -> Dict[str, Dict[str, Any]]:
    """批量拉取基本面；失败单票跳过，不阻断组合回测。

    ``live=False``：只读本地快照（放宽 TTL），不打远端——日报/大宇宙回测用，
    避免 100 票串行 ``build_fundamentals`` 挂死。
    """
    out: Dict[str, Dict[str, Any]] = {}
    seen = set()
    for raw in codes or []:
        key = str(raw or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        if as_of:
            metrics = fetch_score_fundamentals(key, as_of=as_of)
        elif live:
            metrics = fetch_score_fundamentals(key)
        else:
            try:
                from core.data_service import get_fundamentals

                result = get_fundamentals(
                    key,
                    use_cache=True,
                    cache_max_age_hours=24.0 * 30,
                    live=False,
                )
                metrics = (
                    normalize_fundamentals_metrics(result)
                    if result.get("success")
                    else None
                )
            except Exception:
                metrics = None
        if metrics:
            out[key] = metrics
    return out
