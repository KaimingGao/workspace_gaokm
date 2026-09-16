"""ŷ 几何：剩余窗映射、复合收益、τ 头是否 open→close。

从已退役的 nowcast/Kalman 对照层抽出，供融合 / ŷ_τc residual 使用。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def remaining_at_tau(
    yhat: Optional[float],
    realized_pct: Optional[float],
) -> Optional[float]:
    """把「从原点到 T 收」的 ŷ 映成当前时刻的剩余收益。

    (1+ŷ/100)/(1+r/100)−1。无已实现时退回 ŷ（尚未走过该段）。
    """
    if yhat is None:
        return None
    try:
        ye = float(yhat)
    except (TypeError, ValueError):
        return None
    if realized_pct is None:
        return round(ye, 6)
    try:
        r = float(realized_pct)
    except (TypeError, ValueError):
        return round(ye, 6)
    denom = 1.0 + r / 100.0
    if abs(denom) < 1e-12:
        return None
    return round(((1.0 + ye / 100.0) / denom - 1.0) * 100.0, 6)


def remap_variance(p: float, realized_pct: Optional[float]) -> float:
    """几何映剩余窗时，方差按 Jacobian² 缩放：1/(1+r/100)²。"""
    p = _as_var(p, 1.0)
    if realized_pct is None:
        return p
    try:
        r = float(realized_pct)
    except (TypeError, ValueError):
        return p
    scale = 1.0 + r / 100.0
    if abs(scale) < 1e-12:
        return p
    return max(1e-12, p / (scale * scale))


def compound_pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None and b is None:
        return None
    try:
        fa = 0.0 if a is None else float(a)
        fb = 0.0 if b is None else float(b)
    except (TypeError, ValueError):
        return a if b is None else b
    return ((1.0 + fa / 100.0) * (1.0 + fb / 100.0) - 1.0) * 100.0


def _as_var(v: Any, default: float = 1.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        x = float(default)
    return max(1e-6, x)


def _as_opt_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _theme_on(theme_day: Any) -> bool:
    try:
        return float(theme_day or 0.0) >= 0.5
    except (TypeError, ValueError):
        return False


def _clock_is_open(clock: Any) -> bool:
    s = str(clock or "open").strip()
    if "T" in s:
        rest = s.split("T", 1)[1]
        s = rest[:5] if len(rest) >= 5 else s
    low = s.lower()
    if low in ("", "open", "eod"):
        return True
    hm = s[:5]
    return hm in ("09:30", "09:25")


def rem_label_is_open_to_close(rem_model_doc: Optional[dict]) -> bool:
    """τ 头是否估 open→close（含分钟特征但标签仍为 OC 的现网）。

    仅当 ``horizon_mode=tau_to_close`` 或公式显式 price[τ] 时返回 False（旧分钟残差头）。
    """
    doc = rem_model_doc if isinstance(rem_model_doc, dict) else {}
    rm = doc.get("return_model") if isinstance(doc.get("return_model"), dict) else doc
    if not isinstance(rm, dict):
        rm = {}
    mode = str(rm.get("horizon_mode") or doc.get("horizon_mode") or "").strip().lower()
    if mode in ("tau_to_close", "remaining", "tau_rem"):
        return False
    if mode in ("open_to_close", "oc", "open"):
        return True
    ys = rm.get("y_spec") if isinstance(rm.get("y_spec"), dict) else {}
    if not ys:
        ys = doc.get("y_spec") if isinstance(doc.get("y_spec"), dict) else {}
    tau = str((ys or {}).get("tau") or "open").strip().lower()
    formula = str((ys or {}).get("formula") or "").lower()
    if "price_min[" in formula or "/price[" in formula:
        return False
    if "open" in formula:
        return True
    return tau in ("", "open")


def align_rem_yhat_to_clock(
    y_tau: Optional[float],
    *,
    rem_model_doc: Optional[dict] = None,
    ret_open_to_tau: Optional[float] = None,
    clock: Any = "open",
) -> Optional[float]:
    """把 ŷ_τ 映到当前时钟的剩余窗，与 ŷ_oo_rem 对齐。

    OC 头在开盘后：用 open→τ 已实现做几何剩余映射。
    已是 τ→close 的模型：不再映射，避免双重扣减。
    """
    z = _as_opt_float(y_tau)
    if z is None:
        return None
    if _clock_is_open(clock):
        return round(z, 6)
    if not rem_label_is_open_to_close(rem_model_doc):
        return round(z, 6)
    return remaining_at_tau(z, ret_open_to_tau)


def rem_obs_var(
    rem_model_doc: Optional[dict],
    default: float = 1.0,
    *,
    tau: Optional[str] = None,
    theme_day: Any = None,
) -> float:
    """τ 头观测噪声：by_tau → by_theme（PIT 分层）→ 总体 residual_var。"""
    doc = rem_model_doc if isinstance(rem_model_doc, dict) else {}
    oos = doc.get("oos") if isinstance(doc.get("oos"), dict) else {}
    if not oos:
        rm = doc.get("return_model") if isinstance(doc.get("return_model"), dict) else {}
        oos = rm.get("oos") if isinstance(rm.get("oos"), dict) else {}

    def _from_bucket(bucket: Any) -> Optional[float]:
        if not isinstance(bucket, dict) or bucket.get("residual_var") is None:
            return None
        return _as_var(bucket.get("residual_var"), default)

    tau_lab = str(tau or "").strip()[:5] or None
    if tau_lab in ("eod", "open"):
        tau_lab = None
    by_tau = oos.get("by_tau") if isinstance(oos.get("by_tau"), dict) else {}
    if tau_lab:
        got = _from_bucket(by_tau.get(tau_lab))
        if got is not None:
            return got

    by_theme = oos.get("by_theme") if isinstance(oos.get("by_theme"), dict) else {}
    theme_key = "theme" if _theme_on(theme_day) else "normal"
    if by_theme:
        got = _from_bucket(by_theme.get(theme_key))
        if got is not None:
            return got
        got = _from_bucket(by_theme.get("all"))
        if got is not None:
            return got

    if oos.get("residual_var") is not None:
        return _as_var(oos.get("residual_var"), default)
    return _as_var(default, 1.0)


_CLUSTER_VAR_CACHE: Dict[str, Any] = {"mtime": None, "var": None, "ready": False}


def cluster_holdout_residual_var(
    report: Optional[dict] = None,
) -> Optional[float]:
    """分组 holdout RMSE² → ŷ_oo 先验方差。只用已落盘前向指标。"""
    doc = report
    from_disk = not isinstance(doc, dict)
    if from_disk:
        try:
            import json
            import os

            from core.paths import CLUSTER_LAST_REPORT_PATH

            path = CLUSTER_LAST_REPORT_PATH
            if not os.path.isfile(path):
                return None
            mtime = os.path.getmtime(path)
            if (
                _CLUSTER_VAR_CACHE.get("ready")
                and _CLUSTER_VAR_CACHE.get("mtime") == mtime
            ):
                return _CLUSTER_VAR_CACHE.get("var")
            with open(path, encoding="utf-8") as f:
                doc = json.load(f) or {}
            _CLUSTER_VAR_CACHE["mtime"] = mtime
        except Exception:  # noqa: BLE001
            logger.debug("cluster_holdout_residual_var disk read failed", exc_info=True)
            return None
    if not isinstance(doc, dict):
        if from_disk:
            _CLUSTER_VAR_CACHE["var"] = None
            _CLUSTER_VAR_CACHE["ready"] = True
        return None
    rmse = None
    for bag in (
        doc.get("k_selection"),
        doc.get("walk_forward"),
        doc.get("oos"),
        doc,
    ):
        if not isinstance(bag, dict):
            continue
        if bag.get("mean_holdout_rmse") is not None:
            try:
                rmse = float(bag.get("mean_holdout_rmse"))
                break
            except (TypeError, ValueError):
                continue
        if bag.get("rmse") is not None:
            try:
                rmse = float(bag.get("rmse"))
                break
            except (TypeError, ValueError):
                continue
    if rmse is None or rmse <= 0:
        if from_disk:
            _CLUSTER_VAR_CACHE["var"] = None
            _CLUSTER_VAR_CACHE["ready"] = True
        return None
    var = round(float(rmse) * float(rmse), 6)
    var = max(1e-6, var)
    if from_disk:
        _CLUSTER_VAR_CACHE["var"] = var
        _CLUSTER_VAR_CACHE["ready"] = True
    return var


def resolve_oo_prior_var(
    *,
    cfg_var: Any = None,
    prior_var: Any = None,
    cluster_report: Optional[dict] = None,
    prefer_cluster: bool = True,
) -> Tuple[float, str]:
    """ŷ_oo 先验方差：显式 prior → 分组 holdout RMSE² → dual_score.eod_residual_var。"""
    if prior_var is not None:
        return _as_var(prior_var, 1.0), "prior_var"
    if prefer_cluster:
        cv = cluster_holdout_residual_var(cluster_report)
        if cv is not None:
            return float(cv), "cluster_holdout_rmse2"
    if cfg_var is not None:
        return _as_var(cfg_var, 1.0), "eod_residual_var"
    return 1.0, "default"


# 旧名
resolve_eod_prior_var = resolve_oo_prior_var
