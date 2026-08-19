"""Walk-forward 交叉验证：滚动/扩展窗口、IC 衰减、参数稳定性扫描。

本模块面向 ``ReturnScoreModel``（OLS/Ridge 线性回归，把因子 sub_scores 映射为
predicted_score 收益分 ŷ%）的样本外评估，提供：

- ``rolling_window_fit``：滚动窗口 walk-forward
- ``expanding_window_fit``：扩展窗口 walk-forward
- ``ic_decay_curve``：IC 衰减曲线
- ``param_stability_scan``：参数稳定性扫描
- ``spearman_ic`` / ``rank_ic_series``：秩相关 IC
- ``fit_ridge_ols``：OLS/Ridge 拟合辅助
- ``summarize_walk_forward``：结果摘要

仅依赖 numpy + 标准库，不引入 scipy/sklearn；IC 一律用 numpy 实现的 Spearman 秩相关。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
import math
from typing import Any, Dict, Sequence, Tuple

import numpy as np

from core.signal.return_score import ReturnScoreModel


# --------------------------------------------------------------------------- #
# 秩相关 / IC
# --------------------------------------------------------------------------- #
def _rank(a: np.ndarray) -> np.ndarray:
    """计算秩（average rank for ties）。"""
    arr = np.asarray(a, dtype=float)
    sorter = np.argsort(arr, kind="mergesort")
    inv = np.empty(sorter.size, dtype=np.intp)
    inv[sorter] = np.arange(sorter.size)
    arr = arr[sorter]
    obs = np.r_[True, arr[1:] != arr[:-1]]
    dense = obs.cumsum()[inv]
    # average rank
    count = np.r_[np.nonzero(obs)[0], len(obs)]
    return 0.5 * (count[dense] + count[dense - 1] + 1)


def spearman_ic(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman 秩相关系数（numpy 实现，无 scipy 依赖）。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(x) < 3 or len(y) < 3 or len(x) != len(y):
        return 0.0
    rx = _rank(x)
    ry = _rank(y)
    rx = rx - rx.mean()
    ry = ry - ry.mean()
    denom = np.sqrt(np.sum(rx**2) * np.sum(ry**2))
    if denom < 1e-12:
        return 0.0
    return float(np.sum(rx * ry) / denom)


def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    """Pearson 线性相关系数（numpy 实现）。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(x) < 2 or len(y) < 2 or len(x) != len(y):
        return 0.0
    xc = x - x.mean()
    yc = y - y.mean()
    denom = np.sqrt(np.sum(xc**2) * np.sum(yc**2))
    if denom < 1e-12:
        return 0.0
    return float(np.sum(xc * yc) / denom)


def _corr(x: np.ndarray, y: np.ndarray, method: str = "spearman") -> float:
    """按 method 选择相关系数：spearman（默认）或 pearson。"""
    if method == "pearson":
        return _pearson(x, y)
    return spearman_ic(x, y)


def rank_ic_series(
    factor_scores: np.ndarray, forward_returns: np.ndarray
) -> np.ndarray:
    """逐因子计算时间序列 rank IC。

    - ``factor_scores``: (T, K) 因子得分矩阵（也接受 (T,) 单因子）
    - ``forward_returns``: (T,) 前瞻收益
    - 返回 (K,) 数组，第 j 个元素 = ``spearman_ic(factor_scores[:, j], forward_returns)``。
    """
    X = np.asarray(factor_scores, dtype=float)
    y = np.asarray(forward_returns, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)
    if X.ndim != 2 or X.shape[0] != y.shape[0]:
        return np.asarray([], dtype=float)
    k = X.shape[1]
    return np.asarray(
        [spearman_ic(X[:, j], y) for j in range(k)], dtype=float
    )


# --------------------------------------------------------------------------- #
# OLS / Ridge 拟合
# --------------------------------------------------------------------------- #
def _solve_beta(
    design: np.ndarray, y: np.ndarray, ridge_lambda: float = 0.0
) -> np.ndarray:
    """最小二乘求解 design @ beta ≈ y；Ridge 时截距列（第 0 列）不惩罚。

    用 ``np.linalg.lstsq``（SVD）求解，可处理秩亏/共线，避免单窗口被丢弃。
    """
    n, m = design.shape
    if ridge_lambda and ridge_lambda > 0 and m > 1:
        sqrt_l = math.sqrt(float(ridge_lambda))
        extra = np.zeros((m - 1, m), dtype=float)
        for j in range(m - 1):
            extra[j, j + 1] = sqrt_l
        design = np.vstack([design, extra])
        y = np.concatenate([y, np.zeros(m - 1, dtype=float)])
    try:
        beta, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
    except np.linalg.LinAlgError:
        return np.asarray([], dtype=float)
    if beta.size != m or not np.all(np.isfinite(beta)):
        return np.asarray([], dtype=float)
    return beta


def _fit_window(
    X: np.ndarray, y: np.ndarray, ridge_lambda: float = 0.0
) -> Tuple[np.ndarray, float, np.ndarray, np.ndarray, float]:
    """单窗口拟合：对 X 做 z-score 标准化后拟合 OLS/Ridge。

    返回 ``(coefficients, intercept, z_means, z_stds, r_squared)``；
    ``coefficients`` 为标准化系数（因子高 1σ → 收益变多少），与
    ``ReturnScoreModel.coefficients`` 同口径。样本不足返回 ``(np.zeros(k), 0.0, ...)``
    并由调用方判定是否跳过。
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)
    n, k = X.shape
    z_means = X.mean(axis=0)
    z_stds = X.std(axis=0, ddof=0)
    z_stds = np.where(z_stds < 1e-12, 1.0, z_stds)
    Xz = (X - z_means) / z_stds
    design = np.column_stack([np.ones(n), Xz])
    beta = _solve_beta(design, y, ridge_lambda)
    if beta.size == 0:
        return np.zeros(k), 0.0, z_means, z_stds, 0.0
    intercept = float(beta[0])
    coefficients = np.asarray(beta[1:], dtype=float)
    pred = design @ beta
    ss_res = float(np.sum((y - pred) ** 2))
    y_mean = float(y.mean())
    ss_tot = float(np.sum((y - y_mean) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    return coefficients, intercept, z_means, z_stds, float(r2)


def fit_ridge_ols(
    X: np.ndarray, y: np.ndarray, ridge_lambda: float = 0.0
) -> Tuple[np.ndarray, float]:
    """OLS/Ridge 拟合，返回 ``(coefficients, intercept)``。

    - ``X``: (T, K) 已中心化或未中心化
    - ``y``: (T,)
    - 对 X 做 z-score 标准化后拟合，返回标准化系数和截距。
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)
    coefficients, intercept, _, _, _ = _fit_window(X, y, ridge_lambda)
    return coefficients, intercept


def _build_model(
    coefficients: np.ndarray,
    intercept: float,
    z_means: np.ndarray,
    z_stds: np.ndarray,
    factor_names: Sequence[str],
    *,
    ridge_lambda: float = 0.0,
    horizon_days: int = 3,
    sample_count: int = 0,
) -> ReturnScoreModel:
    """由拟合产物构造 ``ReturnScoreModel``，复用其标准化+预测逻辑。"""
    names = list(factor_names)
    return ReturnScoreModel(
        intercept=float(intercept),
        coefficients={names[j]: float(coefficients[j]) for j in range(len(names))},
        z_means={names[j]: float(z_means[j]) for j in range(len(names))},
        z_stds={names[j]: float(z_stds[j]) for j in range(len(names))},
        standardized=True,
        horizon_days=int(horizon_days),
        sample_count=int(sample_count),
        ridge_lambda=float(ridge_lambda),
        solver="ridge" if ridge_lambda > 0 else "qr",
    )


def _predict_matrix(
    model: ReturnScoreModel, X: np.ndarray, factor_names: Sequence[str]
) -> np.ndarray:
    """用 ``ReturnScoreModel.predict`` 逐行预测，返回 (T,) 数组。"""
    X = np.asarray(X, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)
    names = list(factor_names)
    out = np.zeros(X.shape[0], dtype=float)
    for t in range(X.shape[0]):
        subs = {names[j]: float(X[t, j]) for j in range(len(names))}
        pred = model.predict(subs)
        out[t] = float(pred) if pred is not None else 0.0
    return out


def _coef_cv_mean(coef_arrays: np.ndarray) -> float:
    """系数变异系数均值：每个因子 std/|mean| 跨窗口，再对因子求均值（越低越稳）。"""
    if coef_arrays.size == 0 or coef_arrays.ndim != 2 or coef_arrays.shape[0] < 2:
        return float("nan")
    cmean = coef_arrays.mean(axis=0)
    cstd = coef_arrays.std(axis=0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        cv = np.where(np.abs(cmean) > 1e-12, cstd / np.abs(cmean), np.nan)
    valid = cv[np.isfinite(cv)]
    return float(np.mean(valid)) if valid.size else float("nan")


# --------------------------------------------------------------------------- #
# Walk-forward 主流程
# --------------------------------------------------------------------------- #
def _summarize_ic_series(test_ic_series: Sequence[float]) -> Dict[str, float]:
    """由 IC 序列算 mean/std/tstat/ir。"""
    arr = np.asarray(test_ic_series, dtype=float)
    n = arr.size
    if n == 0:
        return {"test_ic_mean": 0.0, "test_ic_std": 0.0, "test_ic_tstat": 0.0, "test_ic_ir": 0.0}
    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if n > 1 else 0.0
    if std > 1e-12:
        tstat = float(mean / (std / math.sqrt(n)))
        ir = float(mean / std)
    else:
        tstat = 0.0
        ir = 0.0
    return {
        "test_ic_mean": mean,
        "test_ic_std": std,
        "test_ic_tstat": tstat,
        "test_ic_ir": ir,
    }


def rolling_window_fit(
    factor_scores: np.ndarray,
    forward_returns: np.ndarray,
    factor_names: Sequence[str],
    *,
    train_size: int = 60,
    test_size: int = 20,
    step: int = 10,
    ridge_lambda: float = 0.0,
    horizon_days: int = 3,
) -> Dict[str, Any]:
    """滚动窗口 walk-forward 拟合（Rolling window walk-forward）。

    - ``factor_scores``: (T, K) 因子得分矩阵，T=时间期数, K=因子数
    - ``forward_returns``: (T,) 前瞻收益率（%）
    - ``factor_names``: (K,) 因子名列表
    - ``train_size`` / ``test_size`` / ``step``：训练窗口长度 / 测试窗口长度 / 滚动步长

    对每个窗口 i（起点 0, step, 2*step, ...）：
    训练集 = ``[i, i+train_size)``，测试集 = ``[i+train_size, i+train_size+test_size)``；
    用 OLS/Ridge 拟合训练集（复用 ``ReturnScoreModel`` 的标准化+预测逻辑），
    在测试集上预测并计算 Spearman IC，记录系数、IC、R²。
    """
    X = np.asarray(factor_scores, dtype=float)
    y = np.asarray(forward_returns, dtype=float)
    names = list(factor_names)
    if X.ndim == 1:
        X = X.reshape(-1, 1)
    T = X.shape[0]
    K = X.shape[1]

    windows: list = []
    test_ic_series: list = []
    coef_arrays: list = []

    i = 0
    while i + train_size + test_size <= T:
        X_tr = X[i : i + train_size]
        y_tr = y[i : i + train_size]
        X_te = X[i + train_size : i + train_size + test_size]
        y_te = y[i + train_size : i + train_size + test_size]

        coefficients, intercept, z_means, z_stds, r2 = _fit_window(
            X_tr, y_tr, ridge_lambda
        )
        if not np.any(coefficients) and intercept == 0.0 and r2 == 0.0:
            # 拟合失败（样本不足/奇异），跳过该窗口
            i += step
            continue

        model = _build_model(
            coefficients,
            intercept,
            z_means,
            z_stds,
            names,
            ridge_lambda=ridge_lambda,
            horizon_days=horizon_days,
            sample_count=train_size,
        )
        train_pred = _predict_matrix(model, X_tr, names)
        test_pred = _predict_matrix(model, X_te, names)
        train_ic = spearman_ic(train_pred, y_tr)
        test_ic = spearman_ic(test_pred, y_te)

        windows.append(
            {
                "start": int(i),
                "end": int(i + train_size + test_size),
                "train_ic": float(train_ic),
                "test_ic": float(test_ic),
                "r_squared": float(r2),
                "coefficients": {names[j]: float(coefficients[j]) for j in range(K)},
                "intercept": float(intercept),
            }
        )
        test_ic_series.append(float(test_ic))
        coef_arrays.append(np.asarray(coefficients, dtype=float))
        i += step

    ic_stats = _summarize_ic_series(test_ic_series)
    coef_stack = (
        np.asarray(coef_arrays, dtype=float)
        if coef_arrays
        else np.asarray([], dtype=float)
    )
    return {
        "windows": windows,
        "test_ic_series": test_ic_series,
        "test_ic_mean": ic_stats["test_ic_mean"],
        "test_ic_std": ic_stats["test_ic_std"],
        "test_ic_tstat": ic_stats["test_ic_tstat"],
        "test_ic_ir": ic_stats["test_ic_ir"],
        "coefficient_stability": _coef_cv_mean(coef_stack),
        "n_windows": len(windows),
        "factor_names": names,
    }


def expanding_window_fit(
    factor_scores: np.ndarray,
    forward_returns: np.ndarray,
    factor_names: Sequence[str],
    *,
    min_train: int = 60,
    test_size: int = 20,
    step: int = 10,
    ridge_lambda: float = 0.0,
    horizon_days: int = 3,
) -> Dict[str, Any]:
    """扩展窗口 walk-forward 拟合（Expanding window walk-forward）。

    训练集从 0 开始不断增长（初始长度 ``min_train``，每轮 +``step``），
    测试集固定 ``test_size`` 长度紧随训练集之后。返回结构同 ``rolling_window_fit``。
    """
    X = np.asarray(factor_scores, dtype=float)
    y = np.asarray(forward_returns, dtype=float)
    names = list(factor_names)
    if X.ndim == 1:
        X = X.reshape(-1, 1)
    T = X.shape[0]
    K = X.shape[1]

    windows: list = []
    test_ic_series: list = []
    coef_arrays: list = []

    train_end = min_train
    while train_end + test_size <= T:
        X_tr = X[:train_end]
        y_tr = y[:train_end]
        X_te = X[train_end : train_end + test_size]
        y_te = y[train_end : train_end + test_size]

        coefficients, intercept, z_means, z_stds, r2 = _fit_window(
            X_tr, y_tr, ridge_lambda
        )
        if not np.any(coefficients) and intercept == 0.0 and r2 == 0.0:
            train_end += step
            continue

        model = _build_model(
            coefficients,
            intercept,
            z_means,
            z_stds,
            names,
            ridge_lambda=ridge_lambda,
            horizon_days=horizon_days,
            sample_count=train_end,
        )
        train_pred = _predict_matrix(model, X_tr, names)
        test_pred = _predict_matrix(model, X_te, names)
        train_ic = spearman_ic(train_pred, y_tr)
        test_ic = spearman_ic(test_pred, y_te)

        windows.append(
            {
                "start": 0,
                "end": int(train_end + test_size),
                "train_ic": float(train_ic),
                "test_ic": float(test_ic),
                "r_squared": float(r2),
                "coefficients": {names[j]: float(coefficients[j]) for j in range(K)},
                "intercept": float(intercept),
            }
        )
        test_ic_series.append(float(test_ic))
        coef_arrays.append(np.asarray(coefficients, dtype=float))
        train_end += step

    ic_stats = _summarize_ic_series(test_ic_series)
    coef_stack = (
        np.asarray(coef_arrays, dtype=float)
        if coef_arrays
        else np.asarray([], dtype=float)
    )
    return {
        "windows": windows,
        "test_ic_series": test_ic_series,
        "test_ic_mean": ic_stats["test_ic_mean"],
        "test_ic_std": ic_stats["test_ic_std"],
        "test_ic_tstat": ic_stats["test_ic_tstat"],
        "test_ic_ir": ic_stats["test_ic_ir"],
        "coefficient_stability": _coef_cv_mean(coef_stack),
        "n_windows": len(windows),
        "factor_names": names,
    }


# --------------------------------------------------------------------------- #
# IC 衰减曲线
# --------------------------------------------------------------------------- #
def ic_decay_curve(
    factor_scores: np.ndarray,
    forward_returns: np.ndarray,
    *,
    max_lag: int = 10,
    method: str = "spearman",
) -> Dict[str, Any]:
    """IC 衰减曲线：因子预测能力随滞后衰减。

    - ``factor_scores``: (T, K) 当期因子得分（也接受 (T,) 单因子）
    - ``forward_returns``: (T,) 前瞻收益
    - ``max_lag``: 最大滞后天数
    - ``method``: ``"spearman"``（默认）或 ``"pearson"``

    对每个 lag = 0, 1, ..., max_lag：计算 ``factor_scores[t-lag]`` 与
    ``forward_returns[t]`` 的 IC（多因子时取各因子 IC 的均值）。
    """
    X = np.asarray(factor_scores, dtype=float)
    y = np.asarray(forward_returns, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)
    T = X.shape[0]
    K = X.shape[1]

    lags = list(range(max_lag + 1))
    ic_values: list = []
    ic_ir: list = []
    for lag in lags:
        if lag >= T:
            ic_values.append(0.0)
            ic_ir.append(0.0)
            continue
        if lag == 0:
            x_seg = X
            y_seg = y
        else:
            x_seg = X[: T - lag]
            y_seg = y[lag:]
        ics = np.asarray(
            [_corr(x_seg[:, j], y_seg, method) for j in range(K)], dtype=float
        )
        ic_values.append(float(np.mean(ics)) if ics.size else 0.0)
        # 各 lag 的 ICIR：多因子时用横截面 mean/std，单因子时无分布置 0
        if ics.size > 1:
            s = float(ics.std(ddof=1))
            ic_ir.append(float(np.mean(ics) / s) if s > 1e-12 else 0.0)
        else:
            ic_ir.append(0.0)

    # 半衰期：IC 衰减到 lag0 一半的期数（线性插值）
    ic0 = ic_values[0] if ic_values else 0.0
    half_life = None
    if ic0 > 1e-12:
        half_target = ic0 / 2.0
        for idx in range(len(ic_values) - 1):
            a, b = ic_values[idx], ic_values[idx + 1]
            if a == b:
                continue
            if (a - half_target) * (b - half_target) <= 0:
                half_life = idx + (half_target - a) / (b - a)
                break
        if half_life is None:
            half_life = float(max_lag)

    return {
        "lags": lags,
        "ic_values": ic_values,
        "ic_ir": ic_ir,
        "half_life": half_life,
        "method": method,
    }


# --------------------------------------------------------------------------- #
# 参数稳定性扫描
# --------------------------------------------------------------------------- #
def param_stability_scan(
    factor_scores: np.ndarray,
    forward_returns: np.ndarray,
    factor_names: Sequence[str],
    *,
    param_name: str = "ridge_lambda",
    param_values: Sequence[float] = None,
    train_size: int = 60,
    test_size: int = 20,
) -> Dict[str, Any]:
    """参数稳定性扫描（扫描 ``ridge_lambda`` 或 ``horizon_days``）。

    对每个参数值跑一次 ``rolling_window_fit``，记录 ``test_ic_mean``；
    ``best_param`` 为 IC 最高的参数值，``stability`` = 最优 IC / 参数范围内 IC 的标准差
    （越高越稳定）。
    """
    if param_name not in ("ridge_lambda", "horizon_days"):
        raise ValueError(
            f"param_name 仅支持 'ridge_lambda' 或 'horizon_days'，收到 {param_name!r}"
        )
    if param_values is None:
        param_values = (
            [1, 3, 5, 10, 20]
            if param_name == "horizon_days"
            else [0.0, 0.01, 0.1, 1.0, 10.0]
        )

    means: list = []
    stds: list = []
    for v in param_values:
        kwargs: Dict[str, Any] = {"train_size": train_size, "test_size": test_size}
        if param_name == "ridge_lambda":
            kwargs["ridge_lambda"] = float(v)
        else:
            kwargs["horizon_days"] = int(v)
        res = rolling_window_fit(
            factor_scores, forward_returns, factor_names, **kwargs
        )
        means.append(float(res["test_ic_mean"]))
        stds.append(float(res["test_ic_std"]))

    means_arr = np.asarray(means, dtype=float)
    best_idx = int(np.argmax(means_arr)) if means_arr.size else 0
    best_param = param_values[best_idx]
    best_ic = float(means_arr[best_idx]) if means_arr.size else 0.0
    std_all = float(means_arr.std(ddof=1)) if means_arr.size > 1 else 0.0
    stability = float(best_ic / std_all) if std_all > 1e-12 else float("inf")

    return {
        "param_name": param_name,
        "param_values": list(param_values),
        "test_ic_means": means,
        "test_ic_stds": stds,
        "best_param": best_param,
        "best_ic": best_ic,
        "stability": stability,
    }


# --------------------------------------------------------------------------- #
# 摘要
# --------------------------------------------------------------------------- #
def summarize_walk_forward(result: Dict[str, Any]) -> Dict[str, Any]:
    """汇总 walk-forward 结果，生成简洁摘要。

    输入 ``rolling_window_fit`` 或 ``expanding_window_fit`` 的返回。
    ``verdict``：``pass``（IC 显著为正，|t|>2）/ ``fail``（IC<=0 或无窗口）/
    ``marginal``（IC 为正但不显著）。
    """
    windows = result.get("windows") or []
    n = len(windows)
    ics = (
        np.asarray([float(w["test_ic"]) for w in windows], dtype=float)
        if windows
        else np.asarray([], dtype=float)
    )

    if n > 0:
        mean = float(ics.mean())
        std = float(ics.std(ddof=1)) if n > 1 else 0.0
        if std > 1e-12:
            tstat = float(mean / (std / math.sqrt(n)))
            ir = float(mean / std)
        else:
            tstat = 0.0
            ir = 0.0
        pos_rate = float(np.mean(ics > 0)) if n > 0 else 0.0
    else:
        mean = 0.0
        std = 0.0
        tstat = 0.0
        ir = 0.0
        pos_rate = 0.0

    significant = bool(abs(tstat) > 2)

    # 系数变异系数均值
    coef_cv_mean = float("nan")
    if windows:
        coef_rows = [
            [float(v) for v in (w.get("coefficients") or {}).values()] for w in windows
        ]
        if coef_rows and len(coef_rows) > 1:
            coef_stack = np.asarray(coef_rows, dtype=float)
            coef_cv_mean = _coef_cv_mean(coef_stack)

    if n == 0 or mean <= 0:
        verdict = "fail"
    elif significant:
        verdict = "pass"
    else:
        verdict = "marginal"

    return {
        "n_windows": n,
        "test_ic_mean": mean,
        "test_ic_ir": ir,
        "test_ic_positive_rate": pos_rate,
        "test_ic_tstat": tstat,
        "significant": significant,
        "coefficient_cv_mean": coef_cv_mean,
        "verdict": verdict,
    }


if __name__ == "__main__":
    rng = np.random.default_rng(42)
    T, K = 200, 4
    factor_scores = rng.standard_normal((T, K))
    true_beta = np.array([0.5, -0.3, 0.1, 0.0])
    forward_returns = factor_scores @ true_beta + rng.standard_normal(T) * 0.5
    names = ["mom", "value", "qual", "size"]
    res = rolling_window_fit(factor_scores, forward_returns, names, train_size=60, test_size=20, step=20)
    print("rolling:", summarize_walk_forward(res))
    res2 = expanding_window_fit(factor_scores, forward_returns, names, min_train=60, test_size=20, step=20)
    print("expanding:", summarize_walk_forward(res2))
    decay = ic_decay_curve(factor_scores[:, 0], forward_returns, max_lag=5)
    print("decay:", decay)
    scan = param_stability_scan(factor_scores, forward_returns, names)
    print("scan:", {k: v for k, v in scan.items() if k != "param_values"})
