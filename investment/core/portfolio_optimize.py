"""组合目标权重（N3）：限额内贪心 / 分数风险预算；可选市场波动缩放。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _sector_for(code: str, sector_map: Optional[Dict[str, str]] = None) -> str:
    m = sector_map or {}
    c = str(code or "").strip()
    if c in m:
        return str(m[c])
    if c.isdigit() and len(c) == 6:
        if c.startswith(("688", "689")):
            return "科创"
        if c.startswith("300"):
            return "创业板"
        if c.startswith(("60", "90")):
            return "主板沪"
        if c.startswith(("00", "001", "002", "003")):
            return "主板深"
    if len(c) <= 5 and c.isdigit():
        return "港股"
    return "其他"


def load_sector_map() -> Dict[str, str]:
    import json
    import os

    from core.paths import DATA_DIR

    path = os.path.join(DATA_DIR, "sector_map.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            return {str(k): str(v) for k, v in raw.items() if k and v}
    except Exception:
        return {}
    return {}


def optimize_weights(
    candidates: List[dict],
    *,
    max_position_pct: float = 2.0,
    max_sector_pct: float = 5.0,
    max_positions: int = 20,
    min_score: float = 55.0,
    sector_map: Optional[Dict[str, str]] = None,
    weight_mode: str = "score_budget",
    vol_scale: Optional[float] = None,
    apply_market_vol: bool = True,
) -> Dict[str, Any]:
    """
    目标权重（百分比）。

    - weight_mode=score_budget：按 score 比例分配（风险预算轻量，默认）
    - weight_mode=greedy_cap：按分数降序填满单票/行业上限（旧行为）
    - weight_mode=risk_parity_lite：TopN 内 1/vol 或等权，限额裁剪（无 QP）
    - weight_mode=qp_lite：可选 cvxpy（V3.4）；不可用则回退 score_budget 并标 unavailable
    - apply_market_vol：高波时压低有效上限（取数失败则不缩放）
    """
    smap = sector_map if sector_map is not None else load_sector_map()
    max_pos = max(0.1, float(max_position_pct or 2.0))
    max_sec = max(0.1, float(max_sector_pct or 5.0))
    max_n = max(1, int(max_positions or 20))
    floor = float(min_score or 0.0)
    mode = (weight_mode or "score_budget").strip().lower()
    if mode not in ("score_budget", "greedy_cap", "risk_parity_lite", "qp_lite"):
        mode = "score_budget"

    vol_meta: Dict[str, Any] = {
        "ok": False,
        "scale": 1.0,
        "high_vol": False,
        "message": "未启用波动缩放",
    }
    scale = 1.0
    if vol_scale is not None:
        try:
            scale = max(0.2, min(1.0, float(vol_scale)))
        except (TypeError, ValueError):
            scale = 1.0
        vol_meta = {
            "ok": True,
            "scale": scale,
            "high_vol": scale < 0.999,
            "message": f"外部指定 vol_scale={scale}",
        }
    elif apply_market_vol:
        try:
            from core.risk.budget import market_vol_scale

            vol_meta = market_vol_scale()
            scale = float(vol_meta.get("scale") or 1.0)
        except Exception as e:
            vol_meta = {
                "ok": False,
                "scale": 1.0,
                "high_vol": False,
                "message": f"波动缩放失败: {e}",
            }
            scale = 1.0

    eff_pos = round(max_pos * scale, 4)
    eff_sec = round(max_sec * scale, 4)

    ranked = []
    for it in candidates or []:
        code = str(it.get("stock_code") or "").strip()
        if not code or it.get("hard_reject"):
            continue
        try:
            score = float(it.get("score"))
        except (TypeError, ValueError):
            continue
        if score < floor:
            continue
        sector = str(it.get("sector") or _sector_for(code, smap))
        row = {"stock_code": code, "score": score, "sector": sector}
        vol = it.get("vol")
        if vol is None:
            vol = it.get("volatility")
        if vol is not None:
            try:
                row["vol"] = float(vol)
            except (TypeError, ValueError):
                pass
        ranked.append(row)
    ranked.sort(key=lambda x: x["score"], reverse=True)

    qp_meta: Optional[Dict[str, Any]] = None
    requested_mode = mode

    if mode == "score_budget":
        from core.risk.budget import score_budget_weights

        weights, sector_sum, skipped = score_budget_weights(
            ranked,
            max_position_pct=eff_pos,
            max_sector_pct=eff_sec,
            max_positions=max_n,
        )
    elif mode == "risk_parity_lite":
        from core.risk.budget import risk_parity_lite_weights

        weights, sector_sum, skipped = risk_parity_lite_weights(
            ranked,
            max_position_pct=eff_pos,
            max_sector_pct=eff_sec,
            max_positions=max_n,
        )
        if not weights:
            # 失败回退贪心
            mode = "greedy_cap"
            weights = {}
            sector_sum = {}
            skipped = list(skipped)
            for row in ranked:
                if len(weights) >= max_n:
                    skipped.append({**row, "reason": "max_positions"})
                    continue
                code = row["stock_code"]
                sector = row["sector"]
                room_sec = eff_sec - float(sector_sum.get(sector) or 0.0)
                if room_sec <= 0.05:
                    skipped.append({**row, "reason": "max_sector_pct"})
                    continue
                alloc = min(eff_pos, room_sec)
                if alloc <= 0.05:
                    skipped.append({**row, "reason": "alloc_too_small"})
                    continue
                weights[code] = round(alloc, 4)
                sector_sum[sector] = round(float(sector_sum.get(sector) or 0.0) + alloc, 4)
            skipped = skipped[:20]
    elif mode == "qp_lite":
        from core.risk.budget import qp_lite_weights, score_budget_weights

        weights, sector_sum, skipped, qp_meta = qp_lite_weights(
            ranked,
            max_position_pct=eff_pos,
            max_sector_pct=eff_sec,
            max_positions=max_n,
        )
        if not weights or not (qp_meta or {}).get("available"):
            mode = "score_budget"
            weights, sector_sum, skipped = score_budget_weights(
                ranked,
                max_position_pct=eff_pos,
                max_sector_pct=eff_sec,
                max_positions=max_n,
            )
            if qp_meta is None:
                qp_meta = {}
            qp_meta["fallback"] = "score_budget"
    else:
        weights = {}
        sector_sum = {}
        skipped = []
        for row in ranked:
            if len(weights) >= max_n:
                skipped.append({**row, "reason": "max_positions"})
                continue
            code = row["stock_code"]
            sector = row["sector"]
            room_sec = eff_sec - float(sector_sum.get(sector) or 0.0)
            if room_sec <= 0.05:
                skipped.append({**row, "reason": "max_sector_pct"})
                continue
            alloc = min(eff_pos, room_sec)
            if alloc <= 0.05:
                skipped.append({**row, "reason": "alloc_too_small"})
                continue
            weights[code] = round(alloc, 4)
            sector_sum[sector] = round(float(sector_sum.get(sector) or 0.0) + alloc, 4)
        skipped = skipped[:20]

    total = round(sum(weights.values()), 4)
    codes_for_cov = [r["stock_code"] for r in ranked]
    try:
        from core.strategy_monitor import sector_coverage_report

        coverage = sector_coverage_report(codes_for_cov)
    except Exception:
        coverage = {"coverage": None, "mapped": 0, "total": len(codes_for_cov)}

    budget_alerts: List[Dict[str, Any]] = []
    if vol_meta.get("high_vol"):
        budget_alerts.append(
            {
                "level": "warn",
                "code": "market_vol_dampen",
                "message": vol_meta.get("message") or "高波压缩目标仓",
            }
        )
    if coverage.get("total") and float(coverage.get("coverage") or 0) < 0.5:
        budget_alerts.append(
            {
                "level": "info",
                "code": "sector_map_thin",
                "message": (
                    f"行业 map 显式覆盖 {coverage.get('mapped')}/{coverage.get('total')}，"
                    "行业预算精度偏弱"
                ),
            }
        )
    for sec, pct in sector_sum.items():
        if pct >= eff_sec * 0.95:
            budget_alerts.append(
                {
                    "level": "warn",
                    "code": "sector_budget_near_cap",
                    "message": f"行业 {sec} 目标仓位 {pct}% 接近上限 {eff_sec}%",
                }
            )

    return {
        "ok": True,
        "weights_pct": weights,
        "sector_exposure_pct": sector_sum,
        "total_pct": total,
        "count": len(weights),
        "skipped": skipped[:20],
        "sector_coverage": coverage,
        "budget_alerts": budget_alerts,
        "weight_mode": mode,
        "requested_weight_mode": requested_mode,
        "solver": mode,
        "qp": qp_meta,
        "vol_scale": vol_meta,
        "limits": {
            "max_position_pct": max_pos,
            "max_sector_pct": max_sec,
            "max_positions": max_n,
            "min_score": floor,
            "effective_max_position_pct": eff_pos,
            "effective_max_sector_pct": eff_sec,
        },
        "note": (
            "N3 目标权重（模拟账户）；"
            f"mode={mode}"
            + (
                f"（请求 {requested_mode} 不可用已回退）"
                if requested_mode == "qp_lite" and mode != "qp_lite"
                else ""
            )
            + "；不代客下单。"
        ),
    }
