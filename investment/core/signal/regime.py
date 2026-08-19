"""市场状态门控（P6.5）。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Tuple

from core.signal.factors.momentum import pct_change


def _regime_anchors(
    *,
    weak_threshold: float,
    bear_threshold: float,
    strong_threshold: float,
    bull_threshold: float,
    penalty: float,
) -> Dict[str, Dict[str, Any]]:
    """各离散 regime 的数值锚点（供边界平滑插值）。"""
    return {
        "bear": {
            "score_penalty": penalty * 2,
            "min_score": 75,
            "max_positions": 3,
            "position_pct": 0.10,
            "weight_adjustments": {
                "momentum": 0.8,
                "quality": 1.3,
                "liquidity": 1.3,
            },
        },
        "weak": {
            "score_penalty": penalty,
            "min_score": 70,
            "max_positions": 4,
            "position_pct": 0.12,
            "weight_adjustments": {
                "momentum": 0.9,
                "quality": 1.15,
                "liquidity": 1.15,
            },
        },
        "neutral": {
            "score_penalty": 0.0,
            "min_score": 65,
            "max_positions": 5,
            "position_pct": 0.15,
            "weight_adjustments": {},
        },
        "strong": {
            "score_penalty": 0.0,
            "min_score": 62,
            "max_positions": 5,
            "position_pct": 0.15,
            "weight_adjustments": {
                "momentum": 1.05,
                "volume_price": 1.05,
            },
        },
        "bull": {
            "score_penalty": 0.0,
            "min_score": 60,
            "max_positions": 5,
            "position_pct": 0.15,
            "weight_adjustments": {
                "momentum": 1.1,
                "volume_price": 1.1,
            },
        },
    }


def _classify_regime(
    idx_ret: float,
    *,
    bear_threshold: float,
    weak_threshold: float,
    strong_threshold: float,
    bull_threshold: float,
) -> str:
    if idx_ret <= bear_threshold:
        return "bear"
    if idx_ret <= weak_threshold:
        return "weak"
    if idx_ret >= bull_threshold:
        return "bull"
    if idx_ret >= strong_threshold:
        return "strong"
    return "neutral"


def _neighbor_regimes(
    regime: str,
) -> Tuple[Optional[str], Optional[str]]:
    order = ["bear", "weak", "neutral", "strong", "bull"]
    i = order.index(regime)
    lo = order[i - 1] if i > 0 else None
    hi = order[i + 1] if i < len(order) - 1 else None
    return lo, hi


def _threshold_between(a: str, b: str, thr: Dict[str, float]) -> Optional[float]:
    """相邻 regime 之间的分割阈值。"""
    pair = {a, b}
    if pair == {"bear", "weak"}:
        return thr["bear"]
    if pair == {"weak", "neutral"}:
        return thr["weak"]
    if pair == {"neutral", "strong"}:
        return thr["strong"]
    if pair == {"strong", "bull"}:
        return thr["bull"]
    return None


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _blend_weight_maps(
    wa: Dict[str, float],
    wb: Dict[str, float],
    t: float,
) -> Dict[str, float]:
    keys = set(wa) | set(wb)
    out: Dict[str, float] = {}
    for k in keys:
        va = float(wa.get(k, 1.0))
        vb = float(wb.get(k, 1.0))
        out[k] = round(_lerp(va, vb, t), 4)
    return out


def _soft_blend_adjustments(
    idx_ret: float,
    regime: str,
    anchors: Dict[str, Dict[str, Any]],
    thr: Dict[str, float],
    *,
    blend_width: float,
) -> Tuple[Dict[str, Any], float, Dict[str, Any]]:
    """在阈值附近对数值调整做线性插值，减轻三重跳变。

    返回 (adjustments, score_penalty, blend_meta)。
    """
    base = dict(anchors[regime])
    score_penalty = float(base.pop("score_penalty"))
    w_adj = dict(base.pop("weight_adjustments") or {})
    blend_meta: Dict[str, Any] = {
        "blend_width_pct": blend_width,
        "blended": False,
        "neighbor": None,
        "t": None,
    }
    if blend_width <= 0:
        return (
            {
                **base,
                "weight_adjustments": w_adj,
            },
            score_penalty,
            blend_meta,
        )

    lo, hi = _neighbor_regimes(regime)
    # 找距 idx_ret 最近、且在 blend 带内的相邻边界
    candidates: List[Tuple[float, str, float]] = []  # (dist, neighbor, boundary)
    for nb in (lo, hi):
        if not nb:
            continue
        boundary = _threshold_between(regime, nb, thr)
        if boundary is None:
            continue
        dist = abs(float(idx_ret) - float(boundary))
        if dist <= blend_width:
            candidates.append((dist, nb, float(boundary)))
    if not candidates:
        return (
            {**base, "weight_adjustments": w_adj},
            score_penalty,
            blend_meta,
        )

    dist, neighbor, boundary = min(candidates, key=lambda x: x[0])
    # t=0 在边界靠本 regime 内侧满权重；t→1 靠近邻域
    # 用「越过边界朝邻域走」的比例
    if idx_ret >= boundary:
        # 收益高于边界：向 bull 侧邻域
        toward = hi if hi == neighbor else (lo if lo == neighbor else neighbor)
    else:
        toward = neighbor
    # 距离边界越近 t 越大（0 在带宽外缘，1 在边界）
    t = 1.0 - (dist / blend_width)
    t = max(0.0, min(1.0, t)) * 0.5  # 最多掺一半邻域，避免标签与数值完全脱节

    other = anchors[toward]
    score_penalty = _lerp(score_penalty, float(other["score_penalty"]), t)
    base["min_score"] = round(
        _lerp(float(base["min_score"]), float(other["min_score"]), t), 2
    )
    base["max_positions"] = int(
        round(_lerp(float(base["max_positions"]), float(other["max_positions"]), t))
    )
    base["position_pct"] = round(
        _lerp(float(base["position_pct"]), float(other["position_pct"]), t), 4
    )
    w_adj = _blend_weight_maps(w_adj, dict(other.get("weight_adjustments") or {}), t)
    blend_meta.update(
        {
            "blended": True,
            "neighbor": toward,
            "boundary_pct": boundary,
            "t": round(t, 4),
        }
    )
    return {**base, "weight_adjustments": w_adj}, score_penalty, blend_meta


def assess_regime(
    index_bars: Optional[List[dict]],
    regime_cfg: Optional[dict] = None,
    macro: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    评估市场状态，返回状态信息和参数调整建议。

    状态等级：
    - bear / weak / neutral / strong / bull
    阈值附近对罚分/仓位/权数做软插值（``blend_width_pct``，默认 1.0），
    减轻边界日三重跳变；因子硬开关仅在非混合带启用。
    """
    cfg = regime_cfg or {}
    if not cfg.get("enabled", True):
        return {
            "regime": "neutral",
            "index_return_pct": None,
            "score_penalty": 0.0,
            "reason": None,
            "adjustments": {},
        }

    if not index_bars or len(index_bars) < 2:
        return {
            "regime": "neutral",
            "index_return_pct": None,
            "score_penalty": 0.0,
            "reason": "无基准日线",
            "adjustments": {},
        }

    days = int(cfg.get("weak_trend_days") or 20)
    threshold = float(cfg.get("weak_trend_threshold_pct") or -3.0)
    penalty = float(cfg.get("score_penalty") or 5.0)
    try:
        blend_width = float(cfg.get("blend_width_pct", 1.0))
    except (TypeError, ValueError):
        blend_width = 1.0
    blend_width = max(0.0, min(blend_width, 5.0))

    idx_ret = pct_change(index_bars, min(days, len(index_bars) - 1))
    if idx_ret is None:
        return {
            "regime": "neutral",
            "index_return_pct": None,
            "score_penalty": 0.0,
            "reason": "基准收益不可算",
            "adjustments": {},
        }

    recent_bars = index_bars[-5:] if len(index_bars) >= 5 else index_bars
    volatility = 0.0
    if len(recent_bars) >= 2:
        changes = []
        for i in range(1, len(recent_bars)):
            prev_close = recent_bars[i - 1]["close"]
            curr_close = recent_bars[i]["close"]
            if prev_close > 0:
                changes.append(abs((curr_close - prev_close) / prev_close * 100))
        if changes:
            volatility = sum(changes) / len(changes)

    weak_threshold = threshold
    bear_threshold = float(
        cfg["bear_threshold_pct"]
        if cfg.get("bear_threshold_pct") is not None
        else min(weak_threshold * 2.0, -8.0)
    )
    strong_threshold = float(
        cfg["strong_threshold_pct"]
        if cfg.get("strong_threshold_pct") is not None
        else abs(weak_threshold)
    )
    bull_threshold = float(
        cfg["bull_threshold_pct"]
        if cfg.get("bull_threshold_pct") is not None
        else max(strong_threshold * 2.0, 8.0)
    )
    thr = {
        "bear": bear_threshold,
        "weak": weak_threshold,
        "strong": strong_threshold,
        "bull": bull_threshold,
    }

    regime = _classify_regime(
        idx_ret,
        bear_threshold=bear_threshold,
        weak_threshold=weak_threshold,
        strong_threshold=strong_threshold,
        bull_threshold=bull_threshold,
    )
    anchors = _regime_anchors(
        weak_threshold=weak_threshold,
        bear_threshold=bear_threshold,
        strong_threshold=strong_threshold,
        bull_threshold=bull_threshold,
        penalty=penalty,
    )
    adjustments, score_penalty, blend_meta = _soft_blend_adjustments(
        idx_ret,
        regime,
        anchors,
        thr,
        blend_width=blend_width,
    )

    if regime == "bear":
        reason = f"基准近{days}日下跌({idx_ret:+.2f}%)，熊市"
    elif regime == "weak":
        reason = f"基准近{days}日偏弱({idx_ret:+.2f}%)"
    elif regime == "bull":
        reason = f"基准近{days}日上涨({idx_ret:+.2f}%)，牛市"
    elif regime == "strong":
        reason = f"基准近{days}日偏强({idx_ret:+.2f}%)"
    else:
        reason = None

    if volatility > 5.0:
        adjustments["min_score"] = float(adjustments.get("min_score", 65)) + 3
        adjustments["position_pct"] = min(
            0.15, float(adjustments.get("position_pct", 0.15)) * 0.9
        )

    # 边界混合带：不做硬关因子，只保留权数调整，避免开关跳变
    if blend_meta.get("blended"):
        factor_selection = {
            "enabled": [
                "momentum",
                "volume_price",
                "relative_strength",
                "volatility",
                "reversal",
                "liquidity",
                "value",
                "quality",
            ],
            "disabled": [],
        }
    else:
        factor_selection = _select_factors_by_regime(regime, volatility)
    adjustments["enabled_factors"] = factor_selection["enabled"]
    adjustments["disabled_factors"] = factor_selection["disabled"]

    macro_overlay: Dict[str, Any] = {"applied": False}
    if isinstance(macro, dict) and macro.get("overseas_tech_1d_pct") is not None:
        try:
            tech_1d = macro.get("overseas_tech_1d_pct")
            stress = float(macro.get("liquidity_stress_score") or 0.0)
            macro_cfg = dict((regime_cfg or {}).get("macro_overlay") or {})
            defer_tech = bool(macro_cfg.get("defer_tech_drag_to_cross_market_prior", True))
            cross_gate = False
            if defer_tech:
                try:
                    from core.cross_market_prior import get_cross_market_cfg

                    cm = get_cross_market_cfg()
                    cross_gate = str(cm.get("mode") or "off") == "gate"
                except Exception:
                    logger.debug("cross_market cfg read failed", exc_info=True)
            if macro_cfg.get("enabled", True):
                trigger = float(macro_cfg.get("tech_drag_trigger_pct") or -1.5)
                if (
                    tech_1d is not None
                    and float(tech_1d) <= trigger
                    and not (defer_tech and cross_gate)
                ):
                    score_penalty = float(score_penalty) + float(
                        macro_cfg.get("extra_penalty") or 4.0
                    )
                    w_adj = dict(adjustments.get("weight_adjustments") or {})
                    w_adj["momentum"] = round(float(w_adj.get("momentum", 1.0)) * 0.85, 4)
                    w_adj["relative_strength"] = round(
                        float(w_adj.get("relative_strength", 1.0)) * 0.9, 4
                    )
                    w_adj["quality"] = round(float(w_adj.get("quality", 1.0)) * 1.1, 4)
                    adjustments["weight_adjustments"] = w_adj
                    adjustments["position_pct"] = min(
                        0.15,
                        float(adjustments.get("position_pct", 0.15)) * 0.85,
                    )
                    macro_overlay = {
                        "applied": True,
                        "tech_1d_pct": tech_1d,
                        "liquidity_stress": stress,
                    }
                    if reason:
                        reason = f"{reason}；海外科技拖累({float(tech_1d):+.2f}%)"
                    else:
                        reason = f"海外科技拖累({float(tech_1d):+.2f}%)"
                elif (
                    tech_1d is not None
                    and float(tech_1d) <= trigger
                    and defer_tech
                    and cross_gate
                ):
                    macro_overlay = {
                        "applied": False,
                        "deferred_to_cross_market_prior": True,
                        "tech_1d_pct": tech_1d,
                    }
                if stress >= float(macro_cfg.get("liquidity_stress_min") or 1.0):
                    adjustments["min_score"] = float(adjustments.get("min_score", 65)) + 2
                    macro_overlay["liquidity_stress"] = stress
                    macro_overlay["applied"] = True
        except (TypeError, ValueError):
            logger.debug("macro overlay skipped", exc_info=True)

    return {
        "regime": regime,
        "index_return_pct": round(idx_ret, 2),
        "volatility_pct": round(volatility, 2),
        "score_penalty": round(float(score_penalty), 4),
        "reason": reason,
        "adjustments": adjustments,
        "blend": blend_meta,
        "macro_overlay": macro_overlay,
    }


def _select_factors_by_regime(regime: str, volatility: float) -> Dict[str, List[str]]:
    """
    根据市场环境选择有效因子。

    - 牛市/强势：优先动量、量价、相对强弱，降低估值/质量
    - 熊市/弱势：优先质量、流动性、估值，降低动量
    - 高波动：优先流动性、反转，降低动量
    """
    all_factors = [
        "momentum",
        "volume_price",
        "relative_strength",
        "volatility",
        "reversal",
        "liquidity",
        "value",
        "quality",
    ]

    if regime in ("bull", "strong"):
        enabled = [
            "momentum",
            "volume_price",
            "relative_strength",
            "volatility",
            "reversal",
        ]
        disabled = ["value", "quality"]
    elif regime in ("bear", "weak"):
        enabled = ["quality", "liquidity", "value", "reversal", "volatility"]
        disabled = ["momentum", "volume_price", "relative_strength"]
    elif volatility > 5.0:
        enabled = ["liquidity", "reversal", "volatility", "quality", "value"]
        disabled = ["momentum", "volume_price", "relative_strength"]
    elif volatility < 2.0:
        enabled = [
            "momentum",
            "relative_strength",
            "volume_price",
            "value",
            "quality",
        ]
        disabled = ["volatility", "reversal"]
    else:
        enabled = all_factors
        disabled = []

    return {"enabled": enabled, "disabled": disabled}
