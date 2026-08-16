"""组合目标权重（N3）：限额内贪心 / 分数风险预算；可选市场波动缩放。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.data_policy import UNMAPPED_SECTOR, is_board_label


def _board_for(code: str) -> str:
    """板别启发式（科创/创业/主板…）；不作行业限额键。"""
    try:
        from core.risk.exposure import board_style_for

        return board_style_for(code)
    except Exception:
        c = str(code or "").strip()
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


def _sector_for(code: str, sector_map: Optional[Dict[str, str]] = None) -> str:
    """行业主题：仅真实主题；板别标签与未映射 → 未分类（DS-R2 / R2.1）。"""
    m = sector_map or {}
    c = str(code or "").strip()
    if c in m and str(m[c]).strip():
        label = str(m[c]).strip()
        if is_board_label(label):
            return UNMAPPED_SECTOR
        return label
    return UNMAPPED_SECTOR


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


def sector_map_coverage(
    codes: Optional[List[str]] = None,
    *,
    sector_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """真实行业映射覆盖率（板别标签不计 mapped）。"""
    smap = sector_map if sector_map is not None else load_sector_map()
    raw = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if not raw:
        return {
            "total": 0,
            "mapped": 0,
            "unmapped": 0,
            "board_labeled": 0,
            "coverage": None,
            "empty_universe": True,
            "unmapped_codes": [],
            "board_codes": [],
        }
    mapped: List[str] = []
    board_codes: List[str] = []
    unmapped: List[str] = []
    for c in raw:
        if c not in smap or not str(smap[c]).strip():
            unmapped.append(c)
            continue
        label = str(smap[c]).strip()
        if is_board_label(label):
            board_codes.append(c)
            unmapped.append(c)
        else:
            mapped.append(c)
    total = len(raw)
    return {
        "total": total,
        "mapped": len(mapped),
        "unmapped": len(unmapped),
        "board_labeled": len(board_codes),
        "coverage": round(len(mapped) / total, 4) if total else None,
        "unmapped_codes": unmapped[:40],
        "board_codes": board_codes[:40],
    }


def optimize_weights(
    candidates: List[dict],
    *,
    max_position_pct: float = 2.0,
    max_sector_pct: float = 5.0,
    max_positions: int = 20,
    min_score: Optional[float] = None,
    sector_map: Optional[Dict[str, str]] = None,
    weight_mode: str = "score_budget",
    vol_scale: Optional[float] = None,
    apply_market_vol: bool = True,
    apply_regime_scale: bool = True,
    exposure: Optional[dict] = None,
    max_style_pct: Optional[float] = 40.0,
) -> Dict[str, Any]:
    """
    目标权重（百分比）。

    - weight_mode=score_budget：按 score 比例分配（风险预算轻量，默认）
    - weight_mode=greedy_cap：按分数降序填满单票/行业上限（旧行为）
    - weight_mode=risk_parity_lite：TopN 内 1/vol 或等权，限额裁剪（无 QP）
    - weight_mode=qp_lite：可选 cvxpy（V3.4）；不可用则回退 score_budget 并标 unavailable
    - apply_market_vol：高波时压低有效上限（取数失败则不缩放）
    - min_score：ŷ% 入选下限；默认 None → ``resolve_buy_floor``；≥10 视为遗留 0–100 并改走 ŷ 门槛。
      入选对比用 ``eod_gate_score_for_item``（ŷ_EOD）；权重分配用 ``decision_score_for_item``（ŷ_trade）。
    """
    smap = sector_map if sector_map is not None else load_sector_map()
    max_pos = max(0.1, float(max_position_pct or 2.0))
    max_sec = max(0.1, float(max_sector_pct or 5.0))
    max_n = max(1, int(max_positions or 20))
    from core.signal.score_display import json_safe_number, resolve_optimize_score_floor

    floor = resolve_optimize_score_floor(min_score)
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

    # RK1 · regime 仓位缩放（与波动缩放相乘）
    regime_meta: Dict[str, Any] = {
        "ok": False,
        "scale": 1.0,
        "message": "未启用 regime 缩放",
    }
    regime_scale = 1.0
    if apply_regime_scale:
        try:
            from core.pro_core import regime_position_scale
            from core.signal.regime import assess_regime

            index_bars = None
            try:
                from core.signal.live_features import fetch_live_index_bars

                pack = fetch_live_index_bars(lookback=40)
                if isinstance(pack, dict):
                    index_bars = pack.get("bars") or pack.get("index_bars")
                elif isinstance(pack, list):
                    index_bars = pack
            except Exception:
                index_bars = None
            regime_meta = regime_position_scale(
                regime=assess_regime(index_bars)
            )
            regime_scale = float(regime_meta.get("scale") or 1.0)
        except Exception as e:
            regime_meta = {
                "ok": False,
                "scale": 1.0,
                "message": f"regime 缩放失败: {e}",
            }
            regime_scale = 1.0

    combined_scale = max(0.2, min(1.0, float(scale) * float(regime_scale)))
    eff_pos = round(max_pos * combined_scale, 4)
    eff_sec = round(max_sec * combined_scale, 4)

    # RK0 · 风格暴露软约束告警
    style_caps = None
    try:
        from core.pro_core import style_soft_caps_from_exposure

        if exposure is not None and max_style_pct is not None:
            style_caps = style_soft_caps_from_exposure(
                exposure, max_style_pct=float(max_style_pct)
            )
            if style_caps and not style_caps.get("ok"):
                # 风格过浓时额外压一档单票上限（软）
                eff_pos = round(eff_pos * 0.9, 4)
                eff_sec = round(eff_sec * 0.9, 4)
    except Exception:
        style_caps = None

    ranked = []
    for it in candidates or []:
        code = str(it.get("stock_code") or "").strip()
        if not code or it.get("hard_reject"):
            continue
        # 入选门槛用 ŷ_EOD（与买入闸一致）；分配权重用 ŷ_trade，避免 blend 被 EOD floor 误杀
        try:
            from core.signal.dual_score import (
                decision_score_for_item,
                eod_gate_score_for_item,
            )

            gate_sc = eod_gate_score_for_item(it)
            alloc_sc = decision_score_for_item(it)
        except Exception:
            gate_sc = None
            alloc_sc = None
        if gate_sc is None:
            try:
                gate_sc = float(it.get("score"))
            except (TypeError, ValueError):
                continue
        if float(gate_sc) < floor:
            continue
        if alloc_sc is None:
            try:
                alloc_sc = float(it.get("score"))
            except (TypeError, ValueError):
                alloc_sc = float(gate_sc)
        score = float(alloc_sc)
        sector = str(it.get("sector") or _sector_for(code, smap))
        board = str(it.get("board") or _board_for(code))
        row = {"stock_code": code, "score": score, "sector": sector, "board": board}
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
                    f"真实行业 map 覆盖 {coverage.get('mapped')}/{coverage.get('total')}"
                    + (
                        f"（板别伪主题 {coverage.get('board_labeled')}）"
                        if coverage.get("board_labeled")
                        else ""
                    )
                    + "，未映射进「未分类」· 行业预算精度偏弱"
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
        "regime_scale": regime_meta,
        "combined_scale": combined_scale,
        "style_caps": style_caps,
        "limits": {
            "max_position_pct": max_pos,
            "max_sector_pct": max_sec,
            "max_positions": max_n,
            "min_score": json_safe_number(floor),
            "effective_max_position_pct": eff_pos,
            "effective_max_sector_pct": eff_sec,
            "vol_scale": scale,
            "regime_scale": regime_scale,
        },
        "note": (
            "N3 目标权重（模拟账户）；"
            f"mode={mode}"
            + (
                f"（请求 {requested_mode} 不可用已回退）"
                if requested_mode == "qp_lite" and mode != "qp_lite"
                else ""
            )
            + f"；scale=vol×regime={combined_scale}"
            + "；不代客下单。"
        ),
    }
