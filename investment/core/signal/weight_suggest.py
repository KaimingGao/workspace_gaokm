"""基于因子 IC 的权重调整建议（P10.3 / V2.1 去冗守卫）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.signal.config import load_signal_config
from core.signal.factor_registry import list_factors

DEFAULT_FACTOR_NAMES = {f["name"] for f in list_factors()}


def suggest_weights_from_ic(
    factor_experiment: Dict[str, Any],
    *,
    current_weights: Optional[Dict[str, float]] = None,
    max_delta: float = 0.03,
    min_ic: float = 0.03,
    min_samples: int = 8,
    corr_report: Optional[Dict[str, Any]] = None,
    corr_threshold: float = 0.7,
) -> Dict[str, Any]:
    """根据因子 IC 符号与幅度给出权重微调建议（不自动写配置）。"""
    cfg = load_signal_config()
    base = dict(current_weights or cfg.get("weights") or {})
    if not base:
        return {"success": False, "error": "无当前权重配置"}

    deltas = {k: 0.0 for k in base}
    rationale: List[str] = []

    for row in factor_experiment.get("factors") or []:
        fac = row.get("factor")
        ic = row.get("ic")
        n = int(row.get("sample_count") or 0)
        if fac == "score" or ic is None or n < min_samples:
            continue
        key = str(fac)
        if key not in base:
            continue
        ic_val = float(ic)
        if ic_val >= min_ic:
            deltas[key] += max_delta
            rationale.append(f"{fac} IC={ic_val:+.4f}（n={n}）→ 建议提高 {key} 权重 +{max_delta}")
        elif ic_val <= -min_ic:
            deltas[key] -= max_delta
            rationale.append(f"{fac} IC={ic_val:+.4f}（n={n}）→ 建议降低 {key} 权重 -{max_delta}")

    suggested: Dict[str, float] = {}
    for k, w in base.items():
        # 允许小权重因子（gap_risk/dividend 等）保持接近 0，避免统一抬到 0.05
        floor = 0.0 if float(w) < 0.05 else 0.02
        suggested[k] = max(floor, min(0.60, float(w) + deltas.get(k, 0.0)))

    total = sum(suggested.values()) or 1.0
    suggested = {k: round(v / total, 3) for k, v in suggested.items()}

    from core.signal.factor_corr import redundancy_warnings_from_corr

    redundancy_warnings = redundancy_warnings_from_corr(
        corr_report or {},
        threshold=corr_threshold,
        factor_groups=cfg.get("factor_groups") or {},
        weights=suggested,
    )

    return {
        "success": True,
        "current_weights": {k: round(float(v), 3) for k, v in base.items()},
        "suggested_weights": suggested,
        "deltas": {k: round(deltas.get(k, 0.0), 3) for k in base},
        "rationale": rationale,
        "redundancy_warnings": redundancy_warnings,
        "factor_groups": cfg.get("factor_groups") or {},
        "params": {
            "max_delta": max_delta,
            "min_ic": min_ic,
            "min_samples": min_samples,
            "corr_threshold": corr_threshold,
        },
        "note": "建议仅供研究；改 signal_config.json 前须做样本外验证，勿直接用于投顾结论。",
    }


def format_weight_config_diff(suggestion: Dict[str, Any]) -> Dict[str, Any]:
    """生成可下载的 signal_config 权重 diff（不自动写盘）。"""
    from datetime import datetime

    if not suggestion.get("success"):
        return {"success": False, "error": suggestion.get("error") or "无效权重建议"}

    current = suggestion.get("current_weights") or {}
    suggested = suggestion.get("suggested_weights") or {}
    changes: Dict[str, dict] = {}
    for key, cur in current.items():
        c = float(cur)
        s = float(suggested.get(key, c))
        delta = round(s - c, 3)
        if abs(delta) >= 0.001:
            changes[key] = {"from": round(c, 3), "to": round(s, 3), "delta": delta}

    return {
        "success": True,
        "target_file": "data/signal_config.json",
        "patch": {"weights": suggested},
        "changes": changes,
        "deltas": suggestion.get("deltas") or {},
        "rationale": suggestion.get("rationale") or [],
        "redundancy_warnings": suggestion.get("redundancy_warnings") or [],
        "params": suggestion.get("params") or {},
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "apply_note": "请手动合并 patch.weights 到 signal_config.json；须先做样本外验证。",
    }
