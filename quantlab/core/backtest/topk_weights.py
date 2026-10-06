"""TopK 回测权重分配（从 topk_backtest 按用例拆出 · A3）。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

WEIGHT_MODES = ("equal", "score_budget", "risk_parity_lite")


def vol_from_window(bars: List[dict], *, window: int = 20) -> Optional[float]:
    closes: List[float] = []
    for b in (bars or [])[-max(window + 2, 5) :]:
        try:
            closes.append(float(b.get("close")))
        except (TypeError, ValueError):
            continue
    if len(closes) < 5:
        return None
    rets = []
    for i in range(1, len(closes)):
        a, b = closes[i - 1], closes[i]
        if a > 0 and b > 0:
            rets.append(b / a - 1.0)
    if len(rets) < 4:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
    return var**0.5


def allocate_topk_weights(
    legs: List[dict],
    *,
    weight_mode: str = "score_budget",
    max_position_pct: float = 25.0,
    max_sector_pct: float = 40.0,
    stock_bars: Optional[Dict[str, List[dict]]] = None,
    renormalize: Optional[bool] = None,
) -> Tuple[Dict[str, float], str]:
    """为已成交腿分配目标权重（%）。失败回退等权。"""
    mode = (weight_mode or "score_budget").strip().lower()
    if mode not in WEIGHT_MODES:
        mode = "equal"
    n = len(legs)
    if n <= 0:
        return {}, mode
    if mode == "equal":
        w = round(100.0 / n, 4)
        return {str(leg["stock_code"]): w for leg in legs}, "equal"

    ranked: List[dict] = []
    for leg in legs:
        code = str(leg.get("stock_code") or "")
        row = {
            "stock_code": code,
            "score": float(leg.get("score") or 50.0),
            "sector": leg.get("sector") or "未知",
        }
        if mode == "risk_parity_lite" and stock_bars:
            vol = vol_from_window(stock_bars.get(code) or [])
            if vol is not None:
                row["vol"] = vol
        ranked.append(row)

    try:
        if mode == "score_budget":
            from core.risk.budget import score_budget_weights

            weights, _, _ = score_budget_weights(
                ranked,
                max_position_pct=max_position_pct,
                max_sector_pct=max_sector_pct,
                max_positions=n,
            )
        else:
            from core.risk.budget import risk_parity_lite_weights

            weights, _, _ = risk_parity_lite_weights(
                ranked,
                max_position_pct=max_position_pct,
                max_sector_pct=max_sector_pct,
                max_positions=n,
            )
    except Exception:  # noqa: BLE001
        logger.warning(
            "weight mode %s compute failed, falling back to equal weight",
            mode,
            exc_info=True,
        )
        weights = {}

    filtered = {
        str(leg["stock_code"]): float(weights.get(str(leg["stock_code"])) or 0.0)
        for leg in legs
    }
    total = sum(filtered.values())
    if total <= 1e-6:
        w = round(100.0 / n, 4)
        return {str(leg["stock_code"]): w for leg in legs}, "equal"
    do_renorm = bool(renormalize) if renormalize is not None else False
    if do_renorm:
        return {
            c: round(100.0 * v / total, 4) for c, v in filtered.items() if v > 0
        }, mode
    return {c: round(v, 4) for c, v in filtered.items() if v > 0.05}, mode
