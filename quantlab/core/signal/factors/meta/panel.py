"""因子面板：注册表 + 权重 + IC 合并展示（P48）。"""

from typing import Any, Dict, List, Optional

from core.signal.config import load_signal_config
from core.signal.factors.meta.registry import list_factors
from core.signal.factors.meta.taxonomy import attach_taxonomy


def build_factor_panel_rows(
    *,
    config: Optional[dict] = None,
    experiment: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    cfg = config or load_signal_config()
    weights = cfg.get("weights") or {}
    factor_groups = cfg.get("factor_groups") or {}
    ic_map: Dict[str, dict] = {}
    for row in (experiment or {}).get("factors") or []:
        name = row.get("factor")
        if name:
            ic_map[str(name)] = row

    rows: List[Dict[str, Any]] = []
    for fac in list_factors():
        name = fac["name"]
        ic_row = ic_map.get(name) or {}
        weight = float(weights.get(name, 0.0))
        rows.append(
            attach_taxonomy(
                {
                    "factor": name,
                    "label": fac.get("label") or name,
                    "description": fac.get("description") or "",
                    "weight": round(weight, 3),
                    "weight_pct": round(weight * 100.0, 1),
                    "ic": ic_row.get("ic"),
                    "sample_count": ic_row.get("sample_count"),
                    "exclusion_reason": ic_row.get("exclusion_reason"),
                },
                factor_groups=factor_groups,
            )
        )
    return rows


def build_factor_panel(
    *,
    config: Optional[dict] = None,
    experiment: Optional[dict] = None,
    stock_code: Optional[str] = None,
    horizon_days: Optional[int] = None,
    data_source: Optional[str] = None,
) -> Dict[str, Any]:
    rows = build_factor_panel_rows(config=config, experiment=experiment)
    weight_sum = round(sum(r["weight"] for r in rows), 3)
    ic_ready = sum(1 for r in rows if r.get("ic") is not None)
    exclusion_reasons: Dict[str, str] = {}
    for r in rows:
        reason = r.get("exclusion_reason")
        if reason and r.get("ic") is None:
            exclusion_reasons[r["factor"]] = str(reason)
    if experiment and isinstance(experiment.get("exclusion_reasons"), dict):
        for name, reason in experiment["exclusion_reasons"].items():
            if name not in exclusion_reasons and reason:
                exclusion_reasons[str(name)] = str(reason)
    return {
        "success": True,
        "factor_count": len(rows),
        "weight_sum": weight_sum,
        "ic_ready_count": ic_ready,
        "stock_code": stock_code,
        "horizon_days": horizon_days,
        "data_source": data_source,
        "rows": rows,
        "exclusion_reasons": exclusion_reasons,
        "factors": [
            {
                "name": r["factor"],
                "label": r["label"],
                "description": r.get("description") or "",
                "family": r.get("family") or "",
                "family_label": r.get("family_label") or "",
                "family_tip": r.get("family_tip") or "",
                "source": r.get("source") or "",
                "source_label": r.get("source_label") or "",
                "source_note": r.get("source_note") or "",
            }
            for r in rows
        ],
        "note": "面板展示全部注册因子与当前权重；IC 需运行 factor-experiment 后填充。",
    }
