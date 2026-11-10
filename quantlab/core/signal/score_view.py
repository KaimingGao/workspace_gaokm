"""评分展示 DTO：公式字符串 / 模型快照（供 UI 悬浮注释）。

原 ``services.paper_helpers`` 中逻辑迁此，避免 core→services。
"""

from typing import Any, Dict, Optional


def build_score_formula(score_info: dict) -> str:
    """生成收益分 ŷ 公式字符串（因子系数 β · z）。"""
    if not score_info:
        return ""
    subs = score_info.get("sub_scores") or {}
    model = score_info.get("return_model")
    if model is None and score_info.get("coefficients"):
        model = {
            "intercept": score_info.get("intercept", 0.0),
            "coefficients": score_info.get("coefficients"),
            "z_means": score_info.get("z_means") or {},
            "z_stds": score_info.get("z_stds") or {},
            "feature_zscore": score_info.get("feature_zscore", True),
        }
    try:
        from core.signal.return_score import ReturnScoreModel

        rm = (
            model
            if isinstance(model, ReturnScoreModel)
            else ReturnScoreModel.from_dict(model)
        )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        rm = None
    if rm is None:
        return ""
    try:
        return rm.format_formula(subs) or ""
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        return ""


def active_return_model_payload(
    *,
    group_model: Any = None,
    global_model: Any = None,
    return_model_source: Optional[str] = None,
) -> Dict[str, Any]:
    """给前端悬浮注释用的精简模型快照（含 ŷ_oo LightGBM gain / 列名）。"""
    src = str(return_model_source or "")
    chosen = None
    if src.startswith("cluster") and group_model is not None:
        chosen = group_model
    elif global_model is not None:
        chosen = global_model
    elif group_model is not None:
        chosen = group_model
    if chosen is None:
        return {}
    try:
        d = chosen.to_dict() if hasattr(chosen, "to_dict") else dict(chosen)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        return {}
    if not isinstance(d, dict):
        return {}
    coefs = dict(d.get("coefficients") or {})
    coefs.pop("intercept", None)
    z_means = d.get("z_means") if isinstance(d.get("z_means"), dict) else {}
    z_stds = d.get("z_stds") if isinstance(d.get("z_stds"), dict) else {}
    out: Dict[str, Any] = {
        "intercept": d.get("intercept"),
        "coefficients": coefs,
        "z_means": z_means or {},
        "z_stds": z_stds or {},
        "feature_zscore": d.get("feature_zscore", True),
    }
    # CS-z LGB：训练窗矩在 impute_*，勿塞进 z_means 冒充截面 μ/σ
    if isinstance(d.get("impute_means"), dict) and d["impute_means"]:
        out["impute_means"] = dict(d["impute_means"])
    if isinstance(d.get("impute_stds"), dict) and d["impute_stds"]:
        out["impute_stds"] = dict(d["impute_stds"])
    solver = str(d.get("solver") or getattr(chosen, "solver", "") or "").strip()
    backend = str(d.get("backend") or "").strip()
    if not backend and solver:
        backend = solver
    if solver:
        out["solver"] = solver
    if backend:
        out["backend"] = backend
    schema = d.get("schema")
    if schema:
        out["schema"] = schema
    names = d.get("feature_names")
    if isinstance(names, list) and names:
        out["feature_names"] = list(names)
    fi = d.get("feature_importance")
    if isinstance(fi, list) and fi:
        out["feature_importance"] = list(fi)
    # 勿塞整段 booster_b64；只戳有树
    if d.get("booster_b64") or (
        isinstance(d.get("booster"), dict) and d["booster"].get("payload_b64")
    ):
        out["has_booster"] = True
    role = str(d.get("model_role") or getattr(chosen, "model_role", "") or "").strip()
    if role:
        out["model_role"] = role
    if d.get("zscore_scope"):
        out["zscore_scope"] = d.get("zscore_scope")
    return out
