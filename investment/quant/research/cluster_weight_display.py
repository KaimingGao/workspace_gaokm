"""分组 OLS / return_model → 展示权与公开字段（从 factor_ols_clusters 拆出）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, Optional


def _public_cluster_ols(report: Dict[str, Any], *, mode: str) -> Dict[str, Any]:
    """组 OLS 公开字段：含收益分预测所需 z 统计。"""
    if not isinstance(report, dict) or not report.get("success"):
        return {
            "success": False,
            "mode": mode,
            "error": (report or {}).get("error") if isinstance(report, dict) else "ols_failed",
        }
    return {
        "success": True,
        "mode": mode,
        "coefficients": report.get("coefficients") or {},
        "intercept": report.get("intercept"),
        "r_squared": report.get("r_squared"),
        "sample_count": report.get("sample_count"),
        "active_features": report.get("active_features") or [],
        "excluded_features": report.get("excluded_features") or [],
        "stock_codes": report.get("stock_codes"),
        "standardized": bool(report.get("standardized", True)),
        "zscore_means": report.get("zscore_means") or report.get("z_means") or {},
        "zscore_stds": report.get("zscore_stds") or report.get("z_stds") or {},
        "horizon_days": report.get("horizon_days"),
        "y_spec": report.get("y_spec"),
        "n_obs": report.get("n_obs") or report.get("sample_count"),
        "sample_fingerprint": report.get("sample_fingerprint"),
        "solver": report.get("solver"),
        "ridge_lambda": report.get("ridge_lambda_selected")
        if report.get("ridge_lambda_selected") is not None
        else report.get("ridge_lambda"),
        "collinearity_policy": report.get("collinearity_policy"),
        "respect_regime": report.get("respect_regime"),
        "error": report.get("error"),
    }


def _return_model_from_ols(ols: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """组 OLS → 收益分模型 dict（供分组 predicted_score）。"""
    if not isinstance(ols, dict) or not ols.get("success"):
        return None
    from core.signal.return_score import ReturnScoreModel

    payload = dict(ols)
    if not payload.get("z_means"):
        payload["z_means"] = payload.get("zscore_means") or {}
    if not payload.get("z_stds"):
        payload["z_stds"] = payload.get("zscore_stds") or {}
    model = ReturnScoreModel.from_ols_report(payload)
    if model is None:
        return None
    out = model.to_dict()
    out["note"] = "分组 OLS β → 因子系数（收益分真源）"
    if payload.get("y_spec"):
        out["y_spec"] = payload["y_spec"]
    if payload.get("sample_fingerprint"):
        out["sample_fingerprint"] = payload["sample_fingerprint"]
    if payload.get("collinearity_policy"):
        out["collinearity_policy"] = payload["collinearity_policy"]
    return out


def _display_weight_suggest_from_return_model(
    return_model: Optional[Dict[str, Any]],
    *,
    ols_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """由 |β| 派生展示权；已退役为选股主轴（deprecated_for_scoring）。"""
    from core.signal.config import load_signal_config
    from core.signal.factors.meta.coefs import (
        coefficients_from_return_model,
        display_weights_from_return_model,
    )

    rm = return_model
    if not isinstance(rm, dict) and isinstance(ols_report, dict):
        rm = _return_model_from_ols(ols_report)
    coefs = coefficients_from_return_model(rm if isinstance(rm, dict) else None)
    suggested = display_weights_from_return_model(rm if isinstance(rm, dict) else None)
    if not suggested:
        return {
            "success": False,
            "error": "无因子系数可派生展示权",
            "deprecated_for_scoring": True,
        }
    current = dict((load_signal_config() or {}).get("weights") or {})
    deltas: Dict[str, float] = {}
    keys = set(current) | set(suggested)
    for k in keys:
        try:
            a = float(current.get(k) or 0.0)
            b = float(suggested.get(k) or 0.0)
        except (TypeError, ValueError):
            continue
        d = round(b - a, 6)
        if abs(d) > 1e-9:
            deltas[k] = d
    return {
        "success": True,
        "suggested_weights": suggested,
        "current_weights": {k: float(current.get(k) or 0.0) for k in suggested},
        "deltas": deltas,
        "delta_sources": dict.fromkeys(suggested, "derived_from_beta"),
        "coefficients": coefs,
        "rationale": [
            f"{k} |β|={abs(float(coefs.get(k) or 0)):.4f} → 展示权 {suggested[k]:.4f}"
            for k in sorted(suggested, key=lambda x: -abs(float(coefs.get(x) or 0)))[:12]
        ],
        "constraint_warnings": [],
        "params": {"source": "abs_beta_normalize"},
        "ic_mode": "factor_coefs",
        "deprecated_for_scoring": True,
        "note": (
            "展示权由 |β| 归一化派生，非选股权；"
            "打分真源为 return_model 因子系数 → ŷ。"
        ),
    }


def _draft_weights_from_ols(
    ols_report: Dict[str, Any],
    *,
    ic_panel: Optional[Dict[str, Any]] = None,
    return_model: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """组内展示权：由 return_model |β| 派生（选股已退役小步权建议）。"""
    del ic_panel  # 保留签名兼容调用方
    return _display_weight_suggest_from_return_model(
        return_model, ols_report=ols_report
    )


def _weight_suggest_public(draft: Dict[str, Any]) -> Dict[str, Any]:
    """一组一表所需字段；标记 deprecated_for_scoring。"""
    if not isinstance(draft, dict):
        return {"success": False, "error": "无建议", "deprecated_for_scoring": True}
    if not draft.get("success"):
        return {
            "success": False,
            "error": draft.get("error") or "建议失败",
            "deprecated_for_scoring": True,
        }
    return {
        "success": True,
        "suggested_weights": draft.get("suggested_weights"),
        "current_weights": draft.get("current_weights"),
        "deltas": draft.get("deltas") or {},
        "delta_sources": draft.get("delta_sources") or {},
        "coefficients": draft.get("coefficients") or {},
        "rationale": list(draft.get("rationale") or [])[:24],
        "constraint_warnings": draft.get("constraint_warnings") or [],
        "params": draft.get("params") or {},
        "ic_mode": draft.get("ic_mode"),
        "deprecated_for_scoring": True,
        "note": draft.get("note")
        or "展示权由 |β| 派生；选股用因子系数 return_model。",
    }
