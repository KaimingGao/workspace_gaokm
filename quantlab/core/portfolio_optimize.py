"""组合目标权重（N3）：限额内贪心 / 分数风险预算；可选市场波动缩放。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional

from core.data.policy import UNMAPPED_SECTOR, is_board_label


def _board_for(code: str) -> str:
    """板别启发式（科创/创业/主板…）；不作行业限额键。"""
    try:
        from core.risk.exposure import board_style_for

        return board_style_for(code)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
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
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
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


# ---------------------------------------------------------------------------
# optimize_weights 的私有子步骤（行为保持一致；仅做结构拆分）
# ---------------------------------------------------------------------------


def _resolve_vol_scale(vol_scale, apply_market_vol):
    """解析外部指定 / 市场波动缩放；返回 (vol_meta, scale)。"""
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
            logger.exception('unexpected error in optimize_weights')
            vol_meta = {
                "ok": False,
                "scale": 1.0,
                "high_vol": False,
                "message": f"波动缩放失败: {e}",
            }
            scale = 1.0
    return vol_meta, scale


def _resolve_regime_scale(apply_regime_scale):
    """RK1 · regime 仓位缩放（与波动缩放相乘）；返回 (regime_meta, regime_scale)。"""
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
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                index_bars = None
            regime_meta = regime_position_scale(
                regime=assess_regime(index_bars)
            )
            regime_scale = float(regime_meta.get("scale") or 1.0)
        except Exception as e:
            logger.exception('unexpected error in optimize_weights')
            regime_meta = {
                "ok": False,
                "scale": 1.0,
                "message": f"regime 缩放失败: {e}",
            }
            regime_scale = 1.0
    return regime_meta, regime_scale


def _compute_combined_scale(max_pos, max_sec, scale, regime_scale):
    """合并 vol×regime 缩放并算有效单票/行业上限；返回 (combined_scale, eff_pos, eff_sec)。"""
    combined_scale = max(0.2, min(1.0, float(scale) * float(regime_scale)))
    eff_pos = round(max_pos * combined_scale, 4)
    eff_sec = round(max_sec * combined_scale, 4)
    return combined_scale, eff_pos, eff_sec


def _resolve_style_caps(exposure, max_style_pct, eff_pos, eff_sec):
    """RK0 · 风格暴露软约束告警；返回 (style_caps, eff_pos, eff_sec)。"""
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
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        style_caps = None
    return style_caps, eff_pos, eff_sec


def _build_ranked_row(it, code, score, sector, board, eod_trust, y_check):
    """构造 ranked 列表的单行（含 vol）。"""
    row = {
        "stock_code": code,
        "score": score,
        "sector": sector,
        "board": board,
        "eod_trust": eod_trust,
        "y_check": y_check,
    }
    vol = it.get("vol")
    if vol is None:
        vol = it.get("volatility")
    if vol is not None:
        try:
            row["vol"] = float(vol)
        except (TypeError, ValueError):
            pass
    return row


def _filter_and_score_candidates(candidates, smap, floor):
    """过滤候选、解析双分数、盖戳 Y(τ)、构造 ranked（按 score 降序）。"""
    ranked = []
    for it in candidates or []:
        code = str(it.get("stock_code") or "").strip()
        if not code or it.get("hard_reject"):
            continue
        # 入选门槛用 ŷ_oo（与买入闸一致）；分配权重用 ŷ_trade，避免 blend 被 EOD floor 误杀
        try:
            from core.signal.dual_score import (
                decision_score_for_item,
                eod_gate_score_for_item,
            )

            gate_sc = eod_gate_score_for_item(it)
            alloc_sc = decision_score_for_item(it)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
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
        # Y(τ) 信任：缺字段时惰性盖戳，供仓位缩放
        eod_trust = it.get("eod_trust")
        y_check = it.get("y_check")
        if eod_trust is None or y_check is None:
            try:
                from core.signal.y_state import stamp_y_state

                stamp_y_state(it)
                eod_trust = it.get("eod_trust")
                y_check = it.get("y_check")
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                pass
        ranked.append(
            _build_ranked_row(it, code, score, sector, board, eod_trust, y_check)
        )
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked


def _score_budget_weights(ranked, eff_pos, eff_sec, max_n):
    """score_budget 模式：按 score 比例分配（风险预算轻量，默认）。"""
    from core.risk.budget import score_budget_weights

    weights, sector_sum, skipped = score_budget_weights(
        ranked,
        max_position_pct=eff_pos,
        max_sector_pct=eff_sec,
        max_positions=max_n,
    )
    return weights, sector_sum, skipped


def _risk_parity_lite_weights(ranked, eff_pos, eff_sec, max_n):
    """risk_parity_lite 模式：TopN 内 1/vol 或等权，限额裁剪（无 QP）。"""
    from core.risk.budget import risk_parity_lite_weights

    weights, sector_sum, skipped = risk_parity_lite_weights(
        ranked,
        max_position_pct=eff_pos,
        max_sector_pct=eff_sec,
        max_positions=max_n,
    )
    return weights, sector_sum, skipped


def _qp_lite_weights(ranked, eff_pos, eff_sec, max_n):
    """qp_lite 模式：可选 cvxpy（V3.4）；返回 (weights, sector_sum, skipped, qp_meta)。"""
    from core.risk.budget import qp_lite_weights

    weights, sector_sum, skipped, qp_meta = qp_lite_weights(
        ranked,
        max_position_pct=eff_pos,
        max_sector_pct=eff_sec,
        max_positions=max_n,
    )
    return weights, sector_sum, skipped, qp_meta


def _greedy_cap_weights(ranked, eff_pos, eff_sec, max_n, skipped=None):
    """贪心按分数降序填满单票/行业上限（旧行为）；skipped 可携带上游已跳过项。"""
    weights = {}
    sector_sum = {}
    out_skipped = list(skipped) if skipped else []
    for row in ranked:
        if len(weights) >= max_n:
            out_skipped.append({**row, "reason": "max_positions"})
            continue
        code = row["stock_code"]
        sector = row["sector"]
        room_sec = eff_sec - float(sector_sum.get(sector) or 0.0)
        if room_sec <= 0.05:
            out_skipped.append({**row, "reason": "max_sector_pct"})
            continue
        alloc = min(eff_pos, room_sec)
        if alloc <= 0.05:
            out_skipped.append({**row, "reason": "alloc_too_small"})
            continue
        weights[code] = round(alloc, 4)
        sector_sum[sector] = round(float(sector_sum.get(sector) or 0.0) + alloc, 4)
    return weights, sector_sum, out_skipped[:20]


def _apply_y_trust_scale(weights, ranked, sector_sum):
    """Y(τ) oo_trust → 目标仓位缩放（不归一）；返回 (weights, y_trust_meta, sector_sum)。"""
    y_trust_meta: Dict[str, Any] = {"applied": False}
    try:
        from core.signal.y_state import scale_weights_by_oo_trust

        trust_map = {
            str(r["stock_code"]): r.get("eod_trust")
            for r in ranked
            if r.get("stock_code")
        }
        weights, y_trust_meta = scale_weights_by_oo_trust(weights, trust_map)
        # 缩放后重算行业暴露
        if y_trust_meta.get("applied") and weights:
            sector_sum = {}
            code_sector = {r["stock_code"]: r["sector"] for r in ranked}
            for c, w in weights.items():
                sec = code_sector.get(c) or UNMAPPED_SECTOR
                sector_sum[sec] = round(float(sector_sum.get(sec) or 0.0) + float(w), 4)
    except Exception as e:
        logger.exception('unexpected error in optimize_weights')
        y_trust_meta = {"applied": False, "error": str(e)}
    return weights, y_trust_meta, sector_sum


def _compute_coverage(ranked):
    """行业覆盖率报告（best-effort，取数失败不阻塞主流程）。"""
    codes_for_cov = [r["stock_code"] for r in ranked]
    try:
        from core.strategy_monitor import sector_coverage_report

        coverage = sector_coverage_report(codes_for_cov)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        coverage = {"coverage": None, "mapped": 0, "total": len(codes_for_cov)}
    return coverage


def _build_budget_alerts(vol_meta, coverage, y_trust_meta, sector_sum, eff_sec):
    """汇总预算告警列表（高波 / 行业 map 薄 / Y 信任缩放 / 行业接近上限）。"""
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
    if y_trust_meta.get("applied") and int(y_trust_meta.get("n_scaled") or 0) > 0:
        budget_alerts.append(
            {
                "level": "info",
                "code": "y_oo_trust_scale",
                "message": (
                    f"Y·oo 信任缩放 {y_trust_meta.get('n_scaled')} 只"
                    + (
                        f"· 剔除 {y_trust_meta.get('n_dropped')}"
                        if int(y_trust_meta.get("n_dropped") or 0) > 0
                        else ""
                    )
                    + f"（目标仓 {y_trust_meta.get('total_before')}%→{y_trust_meta.get('total_after')}%）"
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
    return budget_alerts


def _build_optimize_result(
    weights,
    sector_sum,
    total,
    skipped,
    coverage,
    budget_alerts,
    mode,
    requested_mode,
    qp_meta,
    vol_meta,
    regime_meta,
    y_trust_meta,
    combined_scale,
    style_caps,
    max_pos,
    max_sec,
    max_n,
    floor,
    eff_pos,
    eff_sec,
    scale,
    regime_scale,
):
    """组装 optimize_weights 的最终返回字典。"""
    from core.signal.score_display import json_safe_number

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
        "y_trust_scale": y_trust_meta,
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
            + (
                f"；Y信任缩放 n={y_trust_meta.get('n_scaled')}"
                if y_trust_meta.get("applied") and int(y_trust_meta.get("n_scaled") or 0) > 0
                else ""
            )
            + "；不代客下单。"
        ),
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
      入选对比用 ``eod_gate_score_for_item``（ŷ_oo）；权重分配用 ``decision_score_for_item``（ŷ_trade）。
    """
    smap = sector_map if sector_map is not None else load_sector_map()
    max_pos = max(0.1, float(max_position_pct or 2.0))
    max_sec = max(0.1, float(max_sector_pct or 5.0))
    max_n = max(1, int(max_positions or 20))
    from core.signal.score_display import resolve_optimize_score_floor

    floor = resolve_optimize_score_floor(min_score)
    mode = (weight_mode or "score_budget").strip().lower()
    if mode not in ("score_budget", "greedy_cap", "risk_parity_lite", "qp_lite"):
        mode = "score_budget"

    vol_meta, scale = _resolve_vol_scale(vol_scale, apply_market_vol)
    regime_meta, regime_scale = _resolve_regime_scale(apply_regime_scale)
    combined_scale, eff_pos, eff_sec = _compute_combined_scale(
        max_pos, max_sec, scale, regime_scale
    )
    style_caps, eff_pos, eff_sec = _resolve_style_caps(
        exposure, max_style_pct, eff_pos, eff_sec
    )

    ranked = _filter_and_score_candidates(candidates, smap, floor)

    qp_meta: Optional[Dict[str, Any]] = None
    requested_mode = mode
    if mode == "score_budget":
        weights, sector_sum, skipped = _score_budget_weights(ranked, eff_pos, eff_sec, max_n)
    elif mode == "risk_parity_lite":
        weights, sector_sum, skipped = _risk_parity_lite_weights(ranked, eff_pos, eff_sec, max_n)
        if not weights:
            # 失败回退贪心
            mode = "greedy_cap"
            weights, sector_sum, skipped = _greedy_cap_weights(
                ranked, eff_pos, eff_sec, max_n, skipped
            )
    elif mode == "qp_lite":
        weights, sector_sum, skipped, qp_meta = _qp_lite_weights(ranked, eff_pos, eff_sec, max_n)
        if not weights or not (qp_meta or {}).get("available"):
            mode = "score_budget"
            weights, sector_sum, skipped = _score_budget_weights(ranked, eff_pos, eff_sec, max_n)
            if qp_meta is None:
                qp_meta = {}
            qp_meta["fallback"] = "score_budget"
    else:
        weights, sector_sum, skipped = _greedy_cap_weights(ranked, eff_pos, eff_sec, max_n)

    weights, y_trust_meta, sector_sum = _apply_y_trust_scale(weights, ranked, sector_sum)

    total = round(sum(weights.values()), 4)
    coverage = _compute_coverage(ranked)
    budget_alerts = _build_budget_alerts(vol_meta, coverage, y_trust_meta, sector_sum, eff_sec)

    return _build_optimize_result(
        weights, sector_sum, total, skipped, coverage, budget_alerts,
        mode, requested_mode, qp_meta, vol_meta, regime_meta, y_trust_meta,
        combined_scale, style_caps, max_pos, max_sec, max_n, floor,
        eff_pos, eff_sec, scale, regime_scale,
    )
