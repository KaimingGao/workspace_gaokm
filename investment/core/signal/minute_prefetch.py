"""批量预热分钟线缓存（tail_anomaly 覆盖率）。"""


import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def prefetch_minute_bars(
    codes: List[str],
    *,
    period: str = "5",
    max_codes: int = 30,
    fetch_if_missing: bool = True,
    max_age_hours: float = 12.0,
) -> Dict[str, Any]:
    """对 codes 批量检查/拉取分钟线；返回 warmed/skipped 统计。"""
    from core.market import resolve_market_code
    from core.store import load_minute_cache

    warmed = 0
    cached = 0
    failed = 0
    errors: List[str] = []
    uniq = list(dict.fromkeys(str(c).strip() for c in (codes or []) if str(c).strip()))
    uniq = uniq[: max(1, int(max_codes))]

    for code in uniq:
        market, bare = resolve_market_code(code)
        if market != "CN" or not bare:
            continue
        packed = load_minute_cache(
            market,
            bare,
            period=str(period or "5"),
            min_bars=4,
            max_age_hours=max_age_hours,
        )
        if packed:
            cached += 1
            continue
        if not fetch_if_missing:
            failed += 1
            continue
        try:
            from core.ports.market import fetch_minute_bars

            bars, meta = fetch_minute_bars(
                code,
                period=str(period or "5"),
                use_cache=True,
                lookback_days=10,
            )
            if bars:
                warmed += 1
            else:
                failed += 1
                if meta.get("error"):
                    errors.append(f"{code}:{meta.get('error')}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            errors.append(f"{code}:{e}")
            logger.debug("minute prefetch failed for %s", code, exc_info=True)

    return {
        "ok": (cached + warmed) > 0 or not uniq,
        "total": len(uniq),
        "cached": cached,
        "warmed": warmed,
        "failed": failed,
        "errors": errors[:10],
        "period": str(period or "5"),
    }


def maybe_prefetch_for_tail_anomaly(
    codes: List[str],
    *,
    config: Optional[dict] = None,
) -> Optional[Dict[str, Any]]:
    """若 tail_anomaly 权重>0 则批量预热分钟线。"""
    if config is None:
        try:
            from core.signal.config import load_signal_config

            config = load_signal_config()
        except Exception:
            logger.exception('unexpected error in maybe_prefetch_for_tail_anomaly')
            config = {}
    weights = (config or {}).get("weights") or {}
    w_tail = float(weights.get("tail_anomaly") or 0)
    tail_cfg = (config or {}).get("tail_anomaly") or {}
    if w_tail <= 0:
        return None
    if tail_cfg.get("prefetch_on_rank") is False:
        return None
    pm = (config or {}).get("pre_market") or {}
    cap = int(pm.get("minute_warmup_cap") or tail_cfg.get("prefetch_cap") or 30)
    return prefetch_minute_bars(codes, max_codes=cap)
