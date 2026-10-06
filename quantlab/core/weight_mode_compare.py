"""V3.3 · score_budget vs risk_parity_lite 目标权重对照（同宇宙）。"""

from typing import Any, Dict, List, Optional


def compare_weight_modes(
    candidates: List[dict],
    *,
    max_position_pct: float = 25.0,
    max_sector_pct: float = 40.0,
    max_positions: int = 8,
    min_score: float = 50.0,
    sector_map: Optional[dict] = None,
) -> Dict[str, Any]:
    from core.portfolio_optimize import optimize_weights

    modes = ("score_budget", "risk_parity_lite", "greedy_cap", "qp_lite")
    results: Dict[str, Any] = {}
    for mode in modes:
        out = optimize_weights(
            candidates,
            max_position_pct=max_position_pct,
            max_sector_pct=max_sector_pct,
            max_positions=max_positions,
            min_score=min_score,
            sector_map=sector_map or {},
            weight_mode=mode,
            apply_market_vol=False,
            apply_regime_scale=False,
        )
        results[mode] = {
            "ok": out.get("ok"),
            "solver": out.get("solver") or mode,
            "weights_pct": out.get("weights_pct"),
            "total_pct": out.get("total_pct"),
            "count": out.get("count"),
            "sector_exposure_pct": out.get("sector_exposure_pct"),
            "unavailable": out.get("unavailable") or out.get("ok") is False,
            "note": out.get("note") or out.get("error"),
        }
    return {
        "ok": True,
        "modes": results,
        "note": "同宇宙对照（含 qp_lite，无 cvxpy 时 unavailable）；非完整回测。",
    }
