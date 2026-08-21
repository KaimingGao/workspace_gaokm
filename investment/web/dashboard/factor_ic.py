"""仪表盘：因子 IC 时序。"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _factor_label(name: str) -> str:
    try:
        from core.signal.factor_registry import factor_label

        return str(factor_label(name) or name)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_dashboard.py", exc_info=True)
        return str(name)


def _ic_series_from_daily_tail(
    daily_tail: List[dict],
    *,
    lookback: int,
    top_n: int = 5,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """从组截面 IC ``daily_tail`` 组装多因子时序。"""
    import math as _math
    from statistics import mean, stdev

    lookback = max(10, min(int(lookback or 60), 180))
    rows = [r for r in (daily_tail or []) if isinstance(r, dict) and r.get("date")]
    rows = rows[-lookback:]
    by_factor: Dict[str, List[Dict[str, Any]]] = {}
    for day in rows:
        date = str(day.get("date") or "")[:10]
        factors = day.get("factors") or {}
        if not isinstance(factors, dict):
            continue
        for fname, pack in factors.items():
            if not isinstance(pack, dict):
                continue
            ic = pack.get("pearson")
            if ic is None:
                ic = pack.get("spearman")
            try:
                ic_f = float(ic)
            except (TypeError, ValueError):
                continue
            by_factor.setdefault(str(fname), []).append({"time": date, "value": round(ic_f, 4)})

    factor_series: List[Dict[str, Any]] = []
    for factor, points in by_factor.items():
        if len(points) < 3:
            continue
        vals = [p["value"] for p in points]
        ic_mean = mean(vals)
        ic_std = stdev(vals) if len(vals) > 1 else 0.0
        ic_ir = (ic_mean / ic_std) if ic_std > 1e-12 else 0.0
        ic_ir_annual = ic_ir * _math.sqrt(252.0)
        pos_ratio = sum(1 for v in vals if v > 0) / len(vals) * 100
        factor_series.append(
            {
                "factor": factor,
                "label": _factor_label(factor),
                "ic_values": points,
                "ic_mean": round(ic_mean, 4),
                "ic_std": round(ic_std, 4),
                "ic_ir": round(ic_ir, 4),
                "ic_ir_annual": round(ic_ir_annual, 4),
                "positive_ratio": round(pos_ratio, 1),
                "sample_count": len(points),
            }
        )

    factor_series.sort(key=lambda x: abs(float(x.get("ic_ir_annual") or 0)), reverse=True)
    top = factor_series[: max(1, min(int(top_n or 5), 8))]
    summary: Dict[str, Any] = {}
    if top:
        summary = {
            "ic_mean": round(mean(float(f["ic_mean"]) for f in top), 4),
            "ir_mean": round(mean(float(f["ic_ir_annual"]) for f in top), 4),
            "positive_ratio": round(mean(float(f["positive_ratio"]) for f in top), 1),
            "day_count": max(int(f["sample_count"]) for f in top),
            "factor_count": len(top),
        }
    return top, summary


def _ic_series_from_cluster_cache(lookback: int) -> Optional[Dict[str, Any]]:
    """优先用分组缓存里的真实日频截面 IC。"""
    try:
        from core.paths import CLUSTER_REPORT_CACHE_PATH
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_dashboard.py", exc_info=True)
        return None

    if not os.path.isfile(CLUSTER_REPORT_CACHE_PATH):
        return None
    try:
        import json as _json

        with open(CLUSTER_REPORT_CACHE_PATH, encoding="utf-8") as fh:
            cached = _json.load(fh)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_dashboard.py", exc_info=True)
        return None
    if not isinstance(cached, dict):
        return None

    report = cached.get("report") if isinstance(cached.get("report"), dict) else cached
    clusters = report.get("clusters") if isinstance(report, dict) else None
    if not isinstance(clusters, list) or not clusters:
        return None

    preferred = str(report.get("preferred_cluster") or "").strip()
    cands: List[dict] = []
    for c in clusters:
        if not isinstance(c, dict) or c.get("singleton"):
            continue
        panel = c.get("factor_ic_panel") or {}
        if not isinstance(panel, dict):
            continue
        if not (panel.get("ok") or panel.get("success")):
            continue
        if not (panel.get("daily_tail") or []):
            continue
        cands.append(c)
    if not cands:
        return None

    def _rank(c: dict) -> tuple:
        label = str(c.get("label") or "")
        hit = 1 if preferred and (label == preferred or str(c.get("cluster_id")) == preferred) else 0
        return (hit, int(c.get("member_count") or 0))

    best = sorted(cands, key=_rank, reverse=True)[0]
    panel = best.get("factor_ic_panel") or {}
    factors, summary = _ic_series_from_daily_tail(
        list(panel.get("daily_tail") or []),
        lookback=lookback,
        top_n=5,
    )
    if not factors:
        return None
    return {
        "ok": True,
        "source": "cluster_cs_ic",
        "cluster_label": best.get("label"),
        "member_count": best.get("member_count"),
        "horizon_days": panel.get("horizon_days"),
        "factors": factors,
        "summary": summary,
        "note": (
            f"组 {best.get('label') or '—'} · {best.get('member_count') or 0} 只 · "
            f"日频截面 IC（Pearson）近 {lookback} 日 · 展示 |IR| Top {len(factors)}"
        ),
    }

