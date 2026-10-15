"""Horizon 树用的 LightGBM 训推工具（超参、design matrix、fit/predict、OOS pack）。

供 ``horizon_tree``（t30…t90）使用；不组样本面板（面板在 ``tau_panel``）。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

from core.research.factor_ols_fit import fit_factor_ols_from_panel
from core.research.tau_panel import (
    TAU_LAG_FEAT_LABELS,
    T30_LAG_FEAT_LABELS,
    T60_LAG_FEAT_LABELS,
    theme_sample_weights,
)
from core.research.tc_ridge import (
    _ic,
    _oos_by_tau,
    _oos_by_theme,
    _oos_sign_buckets,
    _predict_rows,
    _residual_var,
    _sign_hit,
)
from core.signal.minute_tau_feats import MINUTE_TAU_FEAT_LABELS

HORIZON_LGB = {
    "n_estimators": 300,
    "max_depth": 6,
    "num_leaves": 64,
    "learning_rate": 0.2,
    "subsample": 0.85,
    "colsample_bytree": 0.8,
    "lambda_l1": 10.0,
    "lambda_l2": 20.0,
    "min_data_in_leaf": 20,
}
DEFAULT_N_ESTIMATORS = int(HORIZON_LGB["n_estimators"])
DEFAULT_MAX_DEPTH = int(HORIZON_LGB["max_depth"])
DEFAULT_LEARNING_RATE = float(HORIZON_LGB["learning_rate"])
DEFAULT_SUBSAMPLE = float(HORIZON_LGB["subsample"])
# stump / 旧早停产物：best_iteration 过小则推理跳过
MIN_USABLE_LGB_TREES = 8


def lgb_best_iteration_collapsed(best_iter: Any) -> bool:
    if best_iter is None:
        return False
    try:
        return int(best_iter) < MIN_USABLE_LGB_TREES
    except (TypeError, ValueError):
        return False


def _horizon_lgb_hyper() -> Dict[str, Any]:
    q = dict(HORIZON_LGB)
    return {
        "n_estimators": int(q["n_estimators"]),
        "max_depth": int(q["max_depth"]),
        "num_leaves": int(q["num_leaves"]),
        "learning_rate": float(q["learning_rate"]),
        "subsample": float(q["subsample"]),
        "colsample_bytree": float(q["colsample_bytree"]),
        "lambda_l1": float(q["lambda_l1"]),
        "lambda_l2": float(q["lambda_l2"]),
        "min_data_in_leaf": int(q["min_data_in_leaf"]),
    }


def _lgb_train_kwargs(hyper: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "num_leaves": hyper["num_leaves"],
        "colsample_bytree": hyper["colsample_bytree"],
        "lambda_l1": hyper["lambda_l1"],
        "lambda_l2": hyper["lambda_l2"],
        "min_data_in_leaf": hyper["min_data_in_leaf"],
    }


def _gain_from_booster(booster: Any, n_features: int) -> np.ndarray:
    p = int(n_features)
    gain = np.zeros(p, dtype=np.float64)
    try:
        imp = booster.feature_importance(importance_type="gain")
        for i in range(min(p, int(imp.size))):
            gain[i] = float(imp[i])
    except Exception:  # noqa: BLE001
        pass
    total = float(np.sum(np.clip(gain, 0.0, None)))
    if total > 1e-12:
        gain = gain / total
    return gain


def resolve_tree_backend(preferred: Optional[str] = None) -> str:
    """Horizon 树头仅 LightGBM；``None`` / ``lgb`` 均解析为 lightgbm。"""
    raw = str(preferred or "").strip().lower()
    if raw in {"", "lightgbm", "lgb"}:
        try:
            import lightgbm  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "horizon tree 仅支持 LightGBM，请安装 lightgbm"
            ) from exc
        return "lightgbm"
    raise ValueError(f"unsupported tree backend={raw!r}；仅支持 lightgbm")


def _population_z_stats(x: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """列均值与总体标准差。缺测不进统计；无波动或全缺时 σ=1。

    与 ``factor_ols_fit._zscore_complete_panel`` 同一口径：var = Σ(x−μ)² / n。
    """
    n = np.sum(np.isfinite(x), axis=0).astype(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        means = np.nanmean(x, axis=0)
        centered = x - means
        var = np.nanmean(centered * centered, axis=0)
    means = np.where(np.isfinite(means), means, 0.0)
    var = np.where(np.isfinite(var), var, 0.0)
    stds = np.sqrt(var)
    stds = np.where((n > 0) & (var > 1e-12), stds, 1.0)
    return means.astype(np.float64), stds.astype(np.float64)


def _apply_feature_z(
    x: np.ndarray, means: np.ndarray, stds: np.ndarray
) -> np.ndarray:
    sd = np.where(np.abs(stds) < 1e-12, 1.0, stds)
    return (x - means) / sd


def _column_means(x: np.ndarray) -> np.ndarray:
    """与 ``_design_matrix`` 的列均值相同：无观测则为 0。"""
    n_valid = np.sum(np.isfinite(x), axis=0)
    with np.errstate(invalid="ignore"):
        col_sum = np.nansum(x, axis=0)
    col_means = np.divide(col_sum, np.maximum(n_valid, 1.0))
    col_means = np.where(n_valid > 0, col_means, np.nan)
    return np.where(np.isfinite(col_means), col_means, 0.0).astype(np.float64)


def _impute_matrix(x: np.ndarray, means: np.ndarray) -> np.ndarray:
    out = np.array(x, dtype=np.float64, copy=True)
    if out.ndim != 2:
        return out
    p = int(out.shape[1])
    mu = np.asarray(means, dtype=np.float64)
    if mu.shape != (p,):
        mu = np.zeros(p, dtype=np.float64)
    for j in range(p):
        miss = ~np.isfinite(out[:, j])
        if np.any(miss):
            out[miss, j] = float(mu[j])
    return out


def _design_matrix(
    xs: Sequence[dict],
    names: Sequence[str],
    means: Optional[np.ndarray] = None,
    *,
    impute: bool = True,
) -> Tuple[np.ndarray, np.ndarray]:
    n = len(xs)
    p = len(names)
    x = np.full((n, p), np.nan, dtype=np.float64)
    for i, row in enumerate(xs):
        src = row if isinstance(row, dict) else {}
        for j, name in enumerate(names):
            v = src.get(name)
            if v is None or v == "":
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(fv):
                x[i, j] = fv
    if means is None:
        n_valid = np.sum(np.isfinite(x), axis=0)
        with np.errstate(invalid="ignore"):
            col_sum = np.nansum(x, axis=0)
        col_means = np.divide(col_sum, np.maximum(n_valid, 1.0))
        col_means = np.where(n_valid > 0, col_means, np.nan)
        means_out = np.where(np.isfinite(col_means), col_means, 0.0).astype(np.float64)
    else:
        means_out = np.asarray(means, dtype=np.float64)
        if means_out.shape != (p,):
            means_out = np.zeros(p, dtype=np.float64)
    if impute:
        for j in range(p):
            col = x[:, j]
            miss = ~np.isfinite(col)
            if np.any(miss):
                col = col.copy()
                col[miss] = float(means_out[j])
                x[:, j] = col
    return x, means_out


def prepare_lgb_features(
    xs_tr: Sequence[dict],
    xs_te: Sequence[dict],
    feat_names: Sequence[str],
    *,
    feature_zscore: bool = True,
    train_dates: Optional[Sequence[str]] = None,
    test_dates: Optional[Sequence[str]] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, Dict[str, Any]]:
    """折 panel → 训/测矩阵。有日期时按当天截面 z-score，否则用训练集总体 μ/σ。

    返回 ``(x_tr, x_te, impute_means, hyper_patch)``；``hyper_patch`` 含
    ``feature_zscore`` / ``zscore_means`` / ``zscore_stds``，并入树 hyperparams。
    """
    from core.research.feature_standardize import annotate_feature_zscore

    x_raw_tr, _ = _design_matrix(xs_tr, feat_names, impute=False)
    x_raw_te, _ = _design_matrix(xs_te, feat_names, impute=False)
    patch: Dict[str, Any] = {}
    names = [str(n) for n in feat_names]
    if (
        feature_zscore
        and train_dates is not None
        and len(train_dates) == int(x_raw_tr.shape[0])
    ):
        from core.research.feature_standardize import (
            cross_section_zscore_matrix,
            stamp_cross_section_zscore,
        )

        x_tr = cross_section_zscore_matrix(x_raw_tr, train_dates)
        x_tr = np.where(np.isfinite(x_tr), x_tr, 0.0)
        if test_dates is not None and len(test_dates) == int(x_raw_te.shape[0]):
            x_te = cross_section_zscore_matrix(x_raw_te, test_dates)
            x_te = np.where(np.isfinite(x_te), x_te, 0.0)
        else:
            x_te = np.where(np.isfinite(x_raw_te), x_raw_te, 0.0)
        means = np.zeros(int(x_tr.shape[1]), dtype=np.float64)
        stamp_cross_section_zscore(patch)
        return x_tr, x_te, means, patch
    if feature_zscore:
        z_means, z_stds = _population_z_stats(x_raw_tr)
        x_tr = _apply_feature_z(_impute_matrix(x_raw_tr, z_means), z_means, z_stds)
        x_te = _apply_feature_z(_impute_matrix(x_raw_te, z_means), z_means, z_stds)
        annotate_feature_zscore(patch, True)
        patch["zscore_means"] = {n: float(z_means[i]) for i, n in enumerate(names)}
        patch["zscore_stds"] = {n: float(z_stds[i]) for i, n in enumerate(names)}
        return x_tr, x_te, z_means, patch
    means = _column_means(x_raw_tr)
    x_tr = _impute_matrix(x_raw_tr, means)
    x_te = _impute_matrix(x_raw_te, means)
    annotate_feature_zscore(patch, False)
    return x_tr, x_te, means, patch


def _oos_pack(
    preds: Sequence[Optional[float]],
    ys: Sequence[float],
    metas: Sequence[dict],
    *,
    use_minute: bool,
    kind: str = "pct",
) -> Dict[str, Any]:
    if str(kind or "pct") == "prob":
        from core.research.horizon_prob import oos_prob_pack

        return oos_prob_pack(preds, ys, metas, use_minute=use_minute)
    pred_list = list(preds)
    y_list = [float(y) for y in ys]
    meta_list = list(metas)
    buckets = _oos_sign_buckets(pred_list, y_list) if y_list else {}
    # 默认 |ŷ|≥0.05；幅度过小时回退全样本同号率
    sign_hit = _sign_hit(pred_list, y_list) if y_list else None
    if sign_hit is None and y_list:
        sign_hit = _sign_hit(pred_list, y_list, min_abs=0.0)
    out: Dict[str, Any] = {
        "n_valid": buckets.get("n_valid") if isinstance(buckets, dict) else None,
        "ic": _ic(pred_list, y_list) if y_list else None,
        "sign_hit": sign_hit,
        "residual_var": _residual_var(pred_list, y_list) if y_list else None,
        "by_theme": _oos_by_theme(pred_list, y_list, meta_list) if y_list else {},
        "by_tau": _oos_by_tau(pred_list, y_list, meta_list) if y_list and use_minute else {},
        "buckets": (buckets.get("buckets") or {}) if isinstance(buckets, dict) else {},
        "pos_recall": buckets.get("pos_recall") if isinstance(buckets, dict) else None,
        "neg_recall": buckets.get("neg_recall") if isinstance(buckets, dict) else None,
        "n_pos": buckets.get("n_pos") if isinstance(buckets, dict) else None,
        "n_neg": buckets.get("n_neg") if isinstance(buckets, dict) else None,
    }
    if y_list:
        try:
            from core.research.daily_cs_ic import attach_daily_cs_ic

            attach_daily_cs_ic(out, pred_list, y_list, meta_list)
        except Exception:  # noqa: BLE001
            logger.debug("attach_daily_cs_ic failed", exc_info=True)
    return out


def _delta_oos(boost: Dict[str, Any], ridge: Dict[str, Any]) -> Dict[str, Any]:
    def _sub(a: Any, b: Any) -> Optional[float]:
        try:
            if a is None or b is None:
                return None
            return round(float(a) - float(b), 4)
        except (TypeError, ValueError):
            return None

    def _bucket_hit(pack: Dict[str, Any], key: str) -> Optional[float]:
        b = ((pack.get("buckets") or {}).get(key) or {}).get("sign_hit")
        try:
            return float(b) if b is not None else None
        except (TypeError, ValueError):
            return None

    def _bucket_n(pack: Dict[str, Any], key: str) -> int:
        try:
            return int(((pack.get("buckets") or {}).get(key) or {}).get("n") or 0)
        except (TypeError, ValueError):
            return 0

    # 固定 0.6 桶两侧都空时，用 |ŷ| top30% 分位桶（z 标签树常见）
    strong_key = "abs_ge_0_6"
    if _bucket_n(boost, "abs_ge_0_6") < 5 and _bucket_n(ridge, "abs_ge_0_6") < 5:
        if _bucket_n(boost, "abs_top_30") >= 5 or _bucket_n(ridge, "abs_top_30") >= 5:
            strong_key = "abs_top_30"
    b06_b = _bucket_hit(boost, strong_key)
    b06_r = _bucket_hit(ridge, strong_key)
    open_b = ((boost.get("by_tau") or {}).get("09:30") or {}).get("sign_hit")
    open_r = ((ridge.get("by_tau") or {}).get("09:30") or {}).get("sign_hit")
    return {
        "ic": _sub(boost.get("ic"), ridge.get("ic")),
        "sign_hit": _sub(boost.get("sign_hit"), ridge.get("sign_hit")),
        "residual_var": _sub(boost.get("residual_var"), ridge.get("residual_var")),
        "auc": _sub(boost.get("auc"), ridge.get("auc")),
        "brier": _sub(boost.get("brier"), ridge.get("brier")),
        "acc_at_50": _sub(boost.get("acc_at_50"), ridge.get("acc_at_50")),
        "strong_sign_hit": _sub(b06_b, b06_r),
        "strong_bucket": strong_key,
        "open_sign_hit": _sub(open_b, open_r),
    }


def _fit_lightgbm(
    x: np.ndarray,
    y: np.ndarray,
    w: np.ndarray,
    *,
    n_estimators: int,
    max_depth: int,
    learning_rate: float,
    subsample: float,
    objective: str = "regression",
    num_leaves: Optional[int] = None,
    colsample_bytree: Optional[float] = None,
    lambda_l1: Optional[float] = None,
    lambda_l2: Optional[float] = None,
    min_data_in_leaf: Optional[int] = None,
    num_threads: int = 1,
) -> Tuple[Any, np.ndarray]:
    import lightgbm as lgb

    raw_obj = str(objective or "regression").strip().lower() or "regression"
    is_binary = raw_obj == "binary"
    obj = "binary" if is_binary else "regression"
    # free_raw_data=True：Dataset 建完后释放传入缓冲，降低峰值内存
    dtrain = lgb.Dataset(x, label=y, weight=w, free_raw_data=True)
    leaves = int(HORIZON_LGB["num_leaves"] if num_leaves is None else num_leaves)
    params: Dict[str, Any] = {
        "objective": obj,
        "metric": ["binary_logloss" if is_binary else "l2"],
        "max_depth": max(1, int(max_depth)),
        "num_leaves": max(2, leaves),
        "learning_rate": float(learning_rate),
        "subsample": min(1.0, max(0.4, float(subsample))),
        "colsample_bytree": min(
            1.0,
            max(
                0.2,
                float(
                    HORIZON_LGB["colsample_bytree"]
                    if colsample_bytree is None
                    else colsample_bytree
                ),
            ),
        ),
        "lambda_l1": max(
            0.0,
            float(HORIZON_LGB["lambda_l1"] if lambda_l1 is None else lambda_l1),
        ),
        "lambda_l2": max(
            0.0,
            float(HORIZON_LGB["lambda_l2"] if lambda_l2 is None else lambda_l2),
        ),
        "min_data_in_leaf": max(
            1,
            int(
                HORIZON_LGB["min_data_in_leaf"]
                if min_data_in_leaf is None
                else min_data_in_leaf
            ),
        ),
        "verbose": -1,
        "seed": 42,
        "num_threads": max(1, int(num_threads)),
    }
    booster = lgb.train(
        params=params,
        train_set=dtrain,
        num_boost_round=max(1, int(n_estimators)),
    )
    return booster, _gain_from_booster(booster, int(x.shape[1]))


def _predict_lightgbm(booster: Any, x: np.ndarray) -> np.ndarray:
    return np.asarray(booster.predict(x), dtype=np.float64)


def _importance_rows(
    names: Sequence[str],
    gain: np.ndarray,
) -> List[Dict[str, Any]]:
    labels = {
        **dict(MINUTE_TAU_FEAT_LABELS),
        **dict(TAU_LAG_FEAT_LABELS),
        **dict(T30_LAG_FEAT_LABELS),
        **dict(T60_LAG_FEAT_LABELS),
    }
    total = float(np.sum(np.clip(gain, 0.0, None)))
    rows: List[Dict[str, Any]] = []
    for i, name in enumerate(names):
        g = float(gain[i]) if i < len(gain) else 0.0
        rows.append(
            {
                "key": str(name),
                "label": str(labels.get(name) or name),
                "gain": round(g, 6),
                "share": round(g / total, 4) if total > 1e-12 else 0.0,
            }
        )
    rows.sort(key=lambda r: -abs(float(r.get("gain") or 0.0)))
    return rows[:16]


def _fit_ridge_oos(
    xs_tr: List[dict],
    ys_tr: List[float],
    xs_te: List[dict],
    ys_te: List[float],
    metas_tr: List[dict],
    feat_names: List[str],
    *,
    ridge_lambda: float,
    theme_boost: float,
    use_theme_weights: bool,
    kind: str = "pct",
    dates_tr: Optional[Sequence[str]] = None,
    dates_te: Optional[Sequence[str]] = None,
) -> Tuple[Dict[str, Any], List[Optional[float]]]:
    weights = (
        theme_sample_weights(metas_tr, theme_boost=theme_boost)
        if use_theme_weights
        else None
    )
    if str(kind or "pct") == "prob":
        from core.research.horizon_prob import (
            binary_labels,
            fit_logistic_ridge_from_panel,
            predict_p_up_rows,
        )

        fit = fit_logistic_ridge_from_panel(
            xs_tr,
            binary_labels(ys_tr),
            feature_names=feat_names,
            ridge_lambda=ridge_lambda,
            sample_weights=weights,
            collinearity_policy="keep_all",
            row_dates=dates_tr,
        )
        if not fit.get("success"):
            fit = {
                "success": True,
                "intercept": 0.0,
                "coefficients": {},
                "active_features": [],
                "zscore_means": {},
                "zscore_stds": {},
                "head_kind": "prob",
            }
        preds = (
            predict_p_up_rows(fit, xs_te, row_dates=dates_te) if xs_te else []
        )
        return fit, preds
    y_mean = sum(float(y) for y in ys_tr) / max(1, len(ys_tr))
    ys_tr_dm = [float(y) - y_mean for y in ys_tr]
    fit = fit_factor_ols_from_panel(
        xs_tr,
        ys_tr_dm,
        feature_names=feat_names,
        ridge_lambda=ridge_lambda,
        feature_zscore=True,
        sample_weights=weights,
        collinearity_policy="keep_all",
        row_dates=dates_tr,
    )
    if not fit.get("success"):
        fit = {
            "success": True,
            "intercept": 0.0,
            "coefficients": {},
            "active_features": [],
            "zscore_means": {},
            "zscore_stds": {},
        }
    rows_te = xs_te
    if xs_te and dates_te is not None and len(dates_te) == len(xs_te):
        from core.research.feature_standardize import (
            cross_section_zscore_dicts,
            is_cross_section_zscore,
        )

        if is_cross_section_zscore(fit):
            names = [str(n) for n in (fit.get("active_features") or [])]
            if names:
                rows_te = cross_section_zscore_dicts(xs_te, names, dates_te)
    preds_dm = _predict_rows(fit, rows_te) if xs_te else []
    preds = [
        (float(p) + y_mean) if p is not None else None for p in (preds_dm or [])
    ]
    return fit, preds


