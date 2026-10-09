"""ŷ_τc_tree：独立树头，标签与 ŷ_τc 相同（τ→close，close[T]/price[τ]−1）。

与 Ridge 同面板、同 Holdout。卡头可调日截面 Z / 滑窗增量（``init_model``）。
拟合写入 ``tc_tree_model.json``，供调仓回测「ŷ头=Tree」替换 ŷ_τc。不进交易执行。
引擎仅 LightGBM。
"""

from __future__ import annotations

import logging
import math
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.research.factor_ols_fit import fit_factor_ols_from_panel
from core.research.tau_panel import (
    TAU_LAG_FEAT_LABELS,
    T30_LAG_FEAT_LABELS,
    T60_LAG_FEAT_LABELS,
    theme_sample_weights,
)
from core.research.tc_ridge import (
    MINUTE_TAU_ALL_KEYS,
    TAU_FIT_DROP_ALIASES,
    TAU_HORIZON_TREE_SHAPE_FEATURES,
    TAU_MIN_STD_EXEMPT,
    TAU_Z_FEATURES,
    _ic,
    _oos_by_tau,
    _oos_by_theme,
    _oos_sign_buckets,
    _predict_rows,
    _residual_var,
    _sign_hit,
    _theme_counts,
    with_horizon_tree_shape,
)
from core.signal.minute_tau_feats import MINUTE_TAU_FEAT_LABELS

TREE_SCHEMA = "tau_tree_shadow_v2"
TREE_HEAD = "y_tau_tree"
TAU_TREE_Z_FEATURES = with_horizon_tree_shape(TAU_Z_FEATURES)

# LightGBM：深度 6、叶子 64、300 轮、lr=0.2、λ₁/λ₂=10/20；标签是百分点
PANEL_LGB = {
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
DEFAULT_N_ESTIMATORS = int(PANEL_LGB["n_estimators"])
DEFAULT_MAX_DEPTH = int(PANEL_LGB["max_depth"])
DEFAULT_LEARNING_RATE = float(PANEL_LGB["learning_rate"])
DEFAULT_SUBSAMPLE = float(PANEL_LGB["subsample"])
# stump / 旧早停产物：best_iteration 过小则推理跳过
MIN_USABLE_LGB_TREES = 8
# 滑窗增量：窗内行数下限；默认滑窗/步长（交易日）
MIN_SLIDING_WINDOW_ROWS = 16
DEFAULT_TREE_WINDOW_DAYS = 60
DEFAULT_TREE_STEP_DAYS = 20


def lgb_best_iteration_collapsed(best_iter: Any) -> bool:
    if best_iter is None:
        return False
    try:
        return int(best_iter) < MIN_USABLE_LGB_TREES
    except (TypeError, ValueError):
        return False


def _panel_lgb_hyper() -> Dict[str, Any]:
    q = dict(PANEL_LGB)
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


def clamp_tree_window_days(v: Any, default: int = DEFAULT_TREE_WINDOW_DAYS) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return int(default)
    if n <= 0:
        return 0
    return max(1, min(500, n))


def clamp_tree_step_days(v: Any, default: int = DEFAULT_TREE_STEP_DAYS) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return int(default)
    return max(1, min(200, n))


def iter_sliding_day_windows(
    train_dates: Sequence[str],
    window_days: int,
    step_days: int,
) -> List[np.ndarray]:
    """按排序唯一交易日滑窗，返回每窗在 ``train_dates`` 中的行下标。

    ``window_days≤0`` 或训练日数不超过窗宽 → 单窗（全训练日）。
    """
    n = len(train_dates)
    if n == 0:
        return []
    days = sorted({str(d)[:10] for d in train_dates if d})
    win = int(window_days or 0)
    step = max(1, int(step_days or 1))
    if win <= 0 or len(days) <= win:
        return [np.arange(n, dtype=np.int64)]

    day_to_rows: Dict[str, List[int]] = {}
    for i, d in enumerate(train_dates):
        key = str(d)[:10]
        day_to_rows.setdefault(key, []).append(i)

    windows: List[np.ndarray] = []
    start = 0
    while start < len(days):
        end = min(len(days), start + win)
        day_slice = days[start:end]
        rows: List[int] = []
        for d in day_slice:
            rows.extend(day_to_rows.get(d) or [])
        if rows:
            windows.append(np.asarray(sorted(rows), dtype=np.int64))
        if end >= len(days):
            break
        start += step
    return windows or [np.arange(n, dtype=np.int64)]


def _split_boost_rounds(n_estimators: int, n_windows: int) -> List[int]:
    """把总轮数均分到各窗，余数给末窗。"""
    n_est = max(1, int(n_estimators))
    n_w = max(1, int(n_windows))
    base = n_est // n_w
    rem = n_est % n_w
    out = [max(1, base) for _ in range(n_w)]
    if base == 0:
        # 窗数 > 总轮数：前 n_est 窗各 1 轮，其余跳过由调用方过滤
        return [1] * n_est + [0] * (n_w - n_est)
    if rem:
        out[-1] = out[-1] + rem
    return out


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


def fit_lgb_on_matrices(
    x_tr: np.ndarray,
    ys_tr: Sequence[float],
    x_te: np.ndarray,
    feat_names: Sequence[str],
    *,
    n_estimators: int,
    max_depth: int,
    learning_rate: float,
    subsample: float,
    sample_weights: Optional[Sequence[float]] = None,
    feature_zscore: bool = False,
    cross_section_zscore: bool = True,
    train_dates: Optional[Sequence[str]] = None,
    test_dates: Optional[Sequence[str]] = None,
    window_days: int = 0,
    step_days: int = DEFAULT_TREE_STEP_DAYS,
) -> Tuple[Any, np.ndarray, Dict[str, Any], np.ndarray, List[Optional[float]], float]:
    """在已折好的 float64 面板上拟合 Holdout 树。缺测为 NaN。

    ``feature_zscore`` + ``cross_section_zscore``：日截面 z（需 ``train_dates``）
    或训练窗全局 μ/σ。标签单位是百分点。

    ``window_days>0`` 且训练日足够：按日滑窗，每窗 ``lgb.train(init_model=…)``
    增量加树；总轮数均分，约等于 ``n_estimators``（滑窗仍用 ``train_dates``）。
    """
    from core.research.feature_standardize import (
        annotate_feature_zscore,
        cross_section_zscore_matrix,
        resolve_feature_zscore,
        stamp_cross_section_zscore,
    )

    use_z = resolve_feature_zscore(feature_zscore=feature_zscore, default=False)
    use_cs = (
        use_z
        and bool(cross_section_zscore)
        and train_dates is not None
        and len(train_dates) == int(np.asarray(x_tr).shape[0])
    )
    n_y = len(ys_tr)
    if sample_weights is not None and len(sample_weights) == n_y:
        w_tr = [float(x) for x in sample_weights]
    else:
        w_tr = [1.0] * n_y
    hyper = _panel_lgb_hyper()
    hyper["n_estimators"] = int(n_estimators)
    hyper["max_depth"] = int(max_depth)
    hyper["learning_rate"] = float(learning_rate)
    hyper["subsample"] = float(subsample)
    lgb_kwargs = _lgb_train_kwargs(hyper)
    win_d = clamp_tree_window_days(window_days, 0)
    step_d = clamp_tree_step_days(step_days, DEFAULT_TREE_STEP_DAYS)

    x_raw = np.asarray(x_tr, dtype=np.float64)
    x_te_raw = np.asarray(x_te, dtype=np.float64)
    if use_cs:
        x_fit = cross_section_zscore_matrix(x_raw, train_dates)
        x_fit = np.where(np.isfinite(x_fit), x_fit, 0.0)
        if test_dates is not None and len(test_dates) == int(x_te_raw.shape[0]):
            x_out = cross_section_zscore_matrix(x_te_raw, test_dates)
            x_out = np.where(np.isfinite(x_out), x_out, 0.0)
        else:
            x_out = np.where(np.isfinite(x_te_raw), x_te_raw, 0.0)
        means = np.zeros(int(x_fit.shape[1]), dtype=np.float64)
        stamp_cross_section_zscore(hyper)
        hyper["cross_section_zscore"] = True
    elif use_z:
        z_means, z_stds = _population_z_stats(x_raw)
        x_fit = _apply_feature_z(_impute_matrix(x_raw, z_means), z_means, z_stds)
        x_out = _apply_feature_z(_impute_matrix(x_te_raw, z_means), z_means, z_stds)
        means = z_means
        names = [str(n) for n in feat_names]
        annotate_feature_zscore(hyper, True)
        hyper["cross_section_zscore"] = False
        hyper.pop("zscore_scope", None)
        hyper["zscore_means"] = {n: float(z_means[i]) for i, n in enumerate(names)}
        hyper["zscore_stds"] = {n: float(z_stds[i]) for i, n in enumerate(names)}
    else:
        means = _column_means(x_raw)
        x_fit = _impute_matrix(x_raw, means)
        x_out = _impute_matrix(x_te_raw, means)
        annotate_feature_zscore(hyper, False)
        hyper["cross_section_zscore"] = False
        hyper.pop("zscore_scope", None)
    y_fit = np.asarray(ys_tr, dtype=np.float64)
    w_fit = np.asarray(w_tr, dtype=np.float64)
    if w_fit.shape != y_fit.shape:
        w_fit = np.ones_like(y_fit)

    date_list = [str(d)[:10] for d in (train_dates or [])]
    windows = (
        iter_sliding_day_windows(date_list, win_d, step_d)
        if date_list and len(date_list) == int(y_fit.shape[0])
        else [np.arange(int(y_fit.shape[0]), dtype=np.int64)]
    )
    # 过滤过小窗；若全被滤掉则退回全样本单窗
    kept: List[np.ndarray] = []
    skipped = 0
    for idx in windows:
        if int(idx.size) >= MIN_SLIDING_WINDOW_ROWS:
            kept.append(idx)
        else:
            skipped += 1
    if not kept:
        kept = [np.arange(int(y_fit.shape[0]), dtype=np.int64)]
        skipped = 0
    # 仅一窗或未开滑窗 → 单次全样本
    use_slide = win_d > 0 and len(kept) > 1
    if not use_slide:
        kept = [np.arange(int(y_fit.shape[0]), dtype=np.int64)]

    rounds = _split_boost_rounds(int(hyper["n_estimators"]), len(kept))
    # 丢掉 0 轮窗（窗数 > 总轮数时）
    pairs = [(idx, r) for idx, r in zip(kept, rounds) if int(r) > 0]
    if not pairs:
        pairs = [(np.arange(int(y_fit.shape[0]), dtype=np.int64), max(1, int(hyper["n_estimators"])))]

    t0 = time.perf_counter()
    model = None
    gain = np.zeros(int(x_fit.shape[1]), dtype=np.float64)
    rounds_done: List[int] = []
    for idx, n_round in pairs:
        model, gain = _fit_lightgbm(
            x_fit[idx],
            y_fit[idx],
            w_fit[idx],
            n_estimators=int(n_round),
            max_depth=int(hyper["max_depth"]),
            learning_rate=float(hyper["learning_rate"]),
            subsample=float(hyper["subsample"]),
            init_model=model,
            **lgb_kwargs,
        )
        rounds_done.append(int(n_round))
    if model is None:
        raise RuntimeError("LightGBM 滑窗拟合未产生模型")
    best_iter = getattr(model, "best_iteration", None)
    if best_iter is not None and int(best_iter) > 0:
        hyper["best_iteration"] = int(best_iter)
    try:
        total_trees = int(model.num_trees())
    except Exception:  # noqa: BLE001
        total_trees = int(sum(rounds_done))
    hyper["window_days"] = int(win_d) if use_slide else 0
    hyper["step_days"] = int(step_d) if use_slide else 0
    hyper["n_windows"] = int(len(rounds_done))
    hyper["rounds_per_window"] = list(rounds_done)
    hyper["total_trees"] = total_trees
    hyper["skipped_small_windows"] = int(skipped) if use_slide else 0
    raw = _predict_lightgbm(model, x_out)
    preds = [float(v) if math.isfinite(float(v)) else None for v in raw]
    tree_s = round(time.perf_counter() - t0, 2)
    return model, gain, hyper, means, preds, tree_s


def fit_lgb_holdout(
    xs_tr: Sequence[dict],
    ys_tr: Sequence[float],
    xs_te: Sequence[dict],
    feat_names: Sequence[str],
    *,
    n_estimators: int,
    max_depth: int,
    learning_rate: float,
    subsample: float,
    sample_weights: Optional[Sequence[float]] = None,
    feature_zscore: bool = False,
    cross_section_zscore: bool = True,
    train_dates: Optional[Sequence[str]] = None,
    test_dates: Optional[Sequence[str]] = None,
    window_days: int = 0,
    step_days: int = DEFAULT_TREE_STEP_DAYS,
) -> Tuple[Any, np.ndarray, Dict[str, Any], np.ndarray, List[Optional[float]], float]:
    """Holdout 树拟合。LightGBM（百分点标签）。

    ``cross_section_zscore``：日截面 z vs 训练窗全局 μ/σ（需 ``feature_zscore``）。
    ``window_days`` 透传滑窗增量（默认 0=全样本一次训）。
    """
    x_tr, _ = _design_matrix(xs_tr, feat_names, impute=False)
    x_te, _ = _design_matrix(xs_te, feat_names, impute=False)
    return fit_lgb_on_matrices(
        x_tr,
        ys_tr,
        x_te,
        feat_names,
        n_estimators=n_estimators,
        max_depth=max_depth,
        learning_rate=learning_rate,
        subsample=subsample,
        sample_weights=sample_weights,
        feature_zscore=feature_zscore,
        cross_section_zscore=cross_section_zscore,
        train_dates=train_dates,
        test_dates=test_dates,
        window_days=window_days,
        step_days=step_days,
    )


def resolve_tree_backend(preferred: Optional[str] = None) -> str:
    """ŷ_τc_tree / ŷ_τ*_tree 仅 LightGBM；``None`` / ``lgb`` 均解析为 lightgbm。"""
    raw = str(preferred or "").strip().lower()
    if raw in {"", "lightgbm", "lgb"}:
        try:
            import lightgbm  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "ŷ_τc_tree / ŷ_τ*_tree 仅支持 LightGBM，请安装 lightgbm"
            ) from exc
        return "lightgbm"
    raise ValueError(f"unsupported tree backend={raw!r}；仅支持 lightgbm")


def tau_tree_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "tau_tree_last_report.json")


def save_tau_tree_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    from core.research.holdout import stamp_fitted_at

    stamp_fitted_at(report)
    path = tau_tree_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)


def load_tau_tree_last_report() -> Optional[Dict[str, Any]]:
    import json

    path = tau_tree_last_report_path()
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except OSError:
        return None
    except Exception:  # noqa: BLE001
        logger.debug("load tau tree last_report failed", exc_info=True)
        return None
    if isinstance(doc, dict) and doc.get("success"):
        return doc
    return None


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
    init_model: Any = None,
) -> Tuple[Any, np.ndarray]:
    import lightgbm as lgb

    raw_obj = str(objective or "regression").strip().lower() or "regression"
    is_binary = raw_obj == "binary"
    obj = "binary" if is_binary else "regression"
    dtrain = lgb.Dataset(x, label=y, weight=w)
    leaves = int(PANEL_LGB["num_leaves"] if num_leaves is None else num_leaves)
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
                    PANEL_LGB["colsample_bytree"]
                    if colsample_bytree is None
                    else colsample_bytree
                ),
            ),
        ),
        "lambda_l1": max(
            0.0,
            float(PANEL_LGB["lambda_l1"] if lambda_l1 is None else lambda_l1),
        ),
        "lambda_l2": max(
            0.0,
            float(PANEL_LGB["lambda_l2"] if lambda_l2 is None else lambda_l2),
        ),
        "min_data_in_leaf": max(
            1,
            int(
                PANEL_LGB["min_data_in_leaf"]
                if min_data_in_leaf is None
                else min_data_in_leaf
            ),
        ),
        "verbose": -1,
        "seed": 42,
        "num_threads": max(1, int(num_threads)),
    }
    train_kw: Dict[str, Any] = {
        "params": params,
        "train_set": dtrain,
        "num_boost_round": max(1, int(n_estimators)),
    }
    if init_model is not None:
        train_kw["init_model"] = init_model
    booster = lgb.train(**train_kw)
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
    min_std_exempt: Optional[Sequence[str]] = None,
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
            min_std_exempt=list(
                min_std_exempt if min_std_exempt is not None else TAU_MIN_STD_EXEMPT
            ),
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
        min_std_exempt=list(
            min_std_exempt if min_std_exempt is not None else TAU_MIN_STD_EXEMPT
        ),
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


def fit_tau_tree_report(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    ridge_lambda: float = 1.0,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    theme_boost: float = 1.5,
    holdout_trading_days: int = 20,
    use_theme_weights: bool = True,
    tau_hm: str = "open",
    tau_grid: Optional[Sequence[str]] = None,
    backend: Optional[str] = None,
    n_estimators: int = DEFAULT_N_ESTIMATORS,
    max_depth: int = DEFAULT_MAX_DEPTH,
    learning_rate: float = DEFAULT_LEARNING_RATE,
    subsample: float = DEFAULT_SUBSAMPLE,
    include_alpha158: bool = True,
    window_days: int = DEFAULT_TREE_WINDOW_DAYS,
    step_days: int = DEFAULT_TREE_STEP_DAYS,
    cross_section_zscore: bool = True,
) -> Dict[str, Any]:
    """同面板拟合树 + Ridge OOS 对照。不写 live / 研究套模型。

    ``include_alpha158=True``（默认）：树吃 ``raw_alpha158_*``。LightGBM
    （深度 6、300 轮、叶子 64、λ₁=10、λ₂=20、学习率 0.2、百分点标签）。
    ``cross_section_zscore``：日截面 z（默认）或训练窗全局 μ/σ；标签仍是百分点。
    ``window_days>0``：训练日滑窗，每窗 ``init_model`` 增量加树（总轮数均分）。
    Ridge 对照仍只用 ``TAU_Z_FEATURES``（避免与 ŷ_oo 双重计权）。
    """
    t0 = time.perf_counter()
    tau_key = str(tau_hm or "open").strip() or "open"
    use_minute = tau_key.lower() not in ("", "open")
    from core.research.tau_panel import normalize_minute_tau_grid

    grid = (
        normalize_minute_tau_grid(tau_hm=tau_key, tau_grid=tau_grid)
        if use_minute
        else None
    )
    from core.research.panel_matrix import (
        collect_tau_compact,
        keepall_ridge_oos,
        finite_name_set,
        named_columns,
        raw_alpha158_finite,
    )

    t_panel0 = time.perf_counter()
    X, col_names, ys, dates, metas, n_stocks = collect_tau_compact(
        stock_bars,
        list(TAU_TREE_Z_FEATURES),
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        tau_hm=tau_key,
        tau_grid=grid,
        include_alpha158=include_alpha158,
        head="y_tc_tree",
    )
    panel_s = round(time.perf_counter() - t_panel0, 2)
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"树样本不足 n={len(ys)}（需≥20）",
            "task": "tc_tree",
            "head": TREE_HEAD,
            "sample_count": len(ys),
            "stock_count": n_stocks,
            "tau": tau_key,
            "schema": TREE_SCHEMA,
            "live_hook": False,
            "backtest_hook": False,
        }

    present = finite_name_set(X, col_names)
    a158_keys = (
        [k for k in raw_alpha158_finite(X, col_names) if k in present]
        if include_alpha158
        else []
    )
    from core.research.holdout import (
        DEFAULT_HOLDOUT_TRADING_DAYS,
        attach_holdout_meta,
        calendar_dates_from_stock_bars,
        resolve_ridge_split,
    )

    hold_n = int(holdout_trading_days or DEFAULT_HOLDOUT_TRADING_DAYS)
    train_idx, test_idx, split_meta = resolve_ridge_split(
        dates,
        holdout_trading_days=hold_n,
        calendar_dates=calendar_dates_from_stock_bars(stock_bars),
    )
    ys_tr = [ys[i] for i in train_idx]
    ys_te = [ys[i] for i in test_idx]
    metas_tr = [metas[i] for i in train_idx]
    metas_te = [metas[i] for i in test_idx]
    drop_opt = set() if use_minute else (
        set(MINUTE_TAU_ALL_KEYS) | set(TAU_HORIZON_TREE_SHAPE_FEATURES)
    )
    feat_names = [
        k
        for k in TAU_TREE_Z_FEATURES
        if k not in drop_opt and k not in TAU_FIT_DROP_ALIASES
    ]
    for k in a158_keys:
        if k not in feat_names:
            feat_names.append(k)
    ridge_feat_names = [
        k
        for k in TAU_Z_FEATURES
        if k not in drop_opt and k not in TAU_FIT_DROP_ALIASES
    ]
    if len(ys_tr) < 16 or len(ys_te) < 8:
        return {
            "success": False,
            "error": f"Holdout 切分后样本不足 训={len(ys_tr)} 测={len(ys_te)}",
            "task": "tc_tree",
            "head": TREE_HEAD,
            "sample_count": len(ys),
            "schema": TREE_SCHEMA,
            "live_hook": False,
            "backtest_hook": False,
        }

    y_tr = np.asarray(ys_tr, dtype=np.float64)
    w_tr = np.asarray(
        theme_sample_weights(metas_tr, theme_boost=theme_boost)
        if use_theme_weights
        else [1.0] * len(ys_tr),
        dtype=np.float64,
    )
    if w_tr.shape != y_tr.shape:
        w_tr = np.ones_like(y_tr)

    X_tree, tree_names = named_columns(X, col_names, feat_names)
    X_ridge, ridge_names = named_columns(X, col_names, ridge_feat_names)
    del X
    row_tr = np.asarray(train_idx, dtype=np.int64)
    row_te = np.asarray(test_idx, dtype=np.int64)
    feat_names = tree_names
    ridge_feat_names = ridge_names

    engine = resolve_tree_backend(backend)
    use_cs = bool(cross_section_zscore)
    model, gain, hyper, means, boost_preds, tree_s = fit_lgb_on_matrices(
        X_tree[row_tr],
        ys_tr,
        X_tree[row_te],
        feat_names,
        n_estimators=n_estimators,
        max_depth=max_depth,
        learning_rate=learning_rate,
        subsample=subsample,
        sample_weights=w_tr,
        feature_zscore=True,
        cross_section_zscore=use_cs,
        train_dates=[dates[i] for i in train_idx],
        test_dates=[dates[i] for i in test_idx],
        window_days=window_days,
        step_days=step_days,
    )
    from core.research.horizon_tree import pack_tree_return_model

    return_model = pack_tree_return_model(
        head="tc",
        backend=engine,
        feature_names=feat_names,
        impute_means=means,
        model_obj=model,
        schema=TREE_SCHEMA,
        hyperparams=hyper,
        kind="return",
        y_label="close[T]/price[τ]-1",
    )

    t_ridge0 = time.perf_counter()
    ridge_w = (
        theme_sample_weights(metas_tr, theme_boost=theme_boost)
        if use_theme_weights
        else None
    )
    _, ridge_preds = keepall_ridge_oos(
        X_ridge[row_tr],
        ys_tr,
        X_ridge[row_te],
        ridge_feat_names,
        ridge_lambda=ridge_lambda,
        sample_weights=ridge_w,
        min_std_exempt=TAU_MIN_STD_EXEMPT,
        row_dates_tr=[dates[i] for i in train_idx],
        row_dates_te=[dates[i] for i in test_idx],
        cross_section_zscore=use_cs,
    )
    ridge_s = round(time.perf_counter() - t_ridge0, 2)
    oos_boost = _oos_pack(boost_preds, ys_te, metas_te, use_minute=use_minute)
    oos_ridge = _oos_pack(ridge_preds, ys_te, metas_te, use_minute=use_minute)
    oos_boost.update(
        {
            "n_train": len(ys_tr),
            "n_test": len(ys_te),
            "holdout_trading_days": hold_n,
            "theme_counts": {
                "train": _theme_counts(metas_tr),
                "oos": _theme_counts(metas_te),
                "all": _theme_counts(metas),
            },
            "tau": tau_key,
        }
    )
    oos_ridge["n_train"] = len(ys_tr)
    oos_ridge["n_test"] = len(ys_te)
    oos_ridge["holdout_trading_days"] = hold_n

    report: Dict[str, Any] = {
        "success": True,
        "task": "tc_tree",
        "head": TREE_HEAD,
        "schema": TREE_SCHEMA,
        "horizon_mode": "tau_to_close",
        "y_spec": {
            "formula": "close[T]/price[τ]-1",
            "unit": "pct",
            "tau": tau_key,
            "note": "ŷ_τc_tree 影子头：标签 τ→close（close[T]/price[τ]−1）；open 时钟 price[τ]=open",
        },
        "stock_count": n_stocks,
        "sample_count": len(ys),
        "tau": tau_key,
        "tau_grid": list(grid) if grid else None,
        "backend": engine,
        "hyperparams": hyper,
        "timing": {
            "panel_s": panel_s,
            "tree_s": tree_s,
            "ridge_s": ridge_s,
            "fit_s": round(time.perf_counter() - t0, 2),
        },
        "feature_names": list(feat_names),
        "ridge_feature_names": list(ridge_feat_names),
        "tree_shape_features": list(TAU_HORIZON_TREE_SHAPE_FEATURES),
        "include_alpha158": bool(include_alpha158),
        "n_alpha158_features": len(a158_keys),
        "feature_importance": _importance_rows(feat_names, gain),
        "tree_return_model": return_model,
        "oos": oos_boost,
        "ridge_oos": oos_ridge,
        "delta_vs_ridge": _delta_oos(oos_boost, oos_ridge),
        "live_hook": False,
        "backtest_hook": True,
        "persisted": {"success": False, "skipped": True, "reason": "fit_only"},
        "window_days": int(hyper.get("window_days") or 0),
        "step_days": int(hyper.get("step_days") or 0),
        "n_windows": int(hyper.get("n_windows") or 0),
        "cross_section_zscore": use_cs,
        "note": (
            "ŷ_τc_tree："
            + ("特征日截面 z-score · " if use_cs else "特征训练窗全局 μ/σ · ")
            + "同 Holdout vs Ridge；路径/量价 shape 仅 Tree；"
            + (
                "树侧含 raw_alpha158_*（Ridge Z 不含，防与 ŷ_oo 双重计权）；"
                if include_alpha158
                else ""
            )
            + (
                f"训练日滑窗 {hyper.get('window_days')}／步 {hyper.get('step_days')}"
                f"（{hyper.get('n_windows')} 窗 · init_model 增量 · 共 {hyper.get('total_trees')} 树）；"
                if int(hyper.get("window_days") or 0) > 0
                else "全样本一次训；"
            )
            + "写入 tc_tree_model.json 后，调仓回测选 Tree 替换 ŷ_τc；不进交易执行"
        ),
    }
    attach_holdout_meta(report, split_meta)
    from core.research.horizon_tree import stamp_tree_fitted_at

    stamp_tree_fitted_at(report)
    return report


def tau_tree_model_path() -> str:
    from core.research.horizon_tree import tree_model_path

    return tree_model_path("tc")


def load_tau_tree_model() -> Optional[Dict[str, Any]]:
    from core.research.horizon_tree import load_tree_model_doc

    return load_tree_model_doc("tc")


def persist_tau_tree_model(
    report: Dict[str, Any],
    *,
    note: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    from core.research.horizon_tree import persist_tree_model_doc

    return persist_tree_model_doc(
        "tc", report, note=note or "tc_tree promote", force=force
    )


def predict_tau_tree_from_features(
    features: Dict[str, Optional[float]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    from core.research.horizon_tree import predict_tree_return

    doc = model_doc if model_doc is not None else load_tau_tree_model()
    if not doc:
        return None
    rm = doc.get("return_model") if isinstance(doc.get("return_model"), dict) else doc
    return predict_tree_return(features, rm)
