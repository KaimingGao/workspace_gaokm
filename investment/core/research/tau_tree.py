"""ŷ_τ_tree：独立浅树头，标签与 ŷ_τ 相同（open→close）。

与 Ridge 同面板、同 Holdout，只写 ``tau_tree_last_report.json``。
不提供 ``predict_tau_from_features`` / persist / 研究套 sidecar，
不进 live 打分与历史回测。优先 XGBoost 原生 train（不依赖 sklearn），否则 numpy 浅树 GBM。
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
from core.research.tau_ridge import (
    MINUTE_TAU_ALL_KEYS,
    TAU_FIT_DROP_ALIASES,
    TAU_MIN_STD_EXEMPT,
    TAU_Z_FEATURES,
    _ic,
    _oos_by_tau,
    _oos_by_theme,
    _oos_sign_buckets,
    _predict_rows,
    _residual_var,
    _sign_hit,
    _stack_panels,
    _subset,
    _theme_counts,
    _z_only_xs,
    build_tau_panels_from_bars,
)
from core.signal.minute_tau_feats import MINUTE_TAU_FEAT_LABELS

TREE_SCHEMA = "tau_tree_shadow_v1"
TREE_HEAD = "y_tau_tree"
DEFAULT_N_ESTIMATORS = 80
DEFAULT_MAX_DEPTH = 3
DEFAULT_LEARNING_RATE = 0.08
DEFAULT_SUBSAMPLE = 0.85


def resolve_tree_backend(preferred: Optional[str] = None) -> str:
    raw = str(preferred or "").strip().lower()
    if raw in {"numpy", "numpy_gbm", "gbm"}:
        return "numpy_gbm"
    if raw in {"xgboost", "xgb", "auto", ""}:
        if raw in {"xgboost", "xgb"}:
            try:
                import xgboost  # noqa: F401
            except ImportError as exc:
                raise ImportError("未安装 xgboost") from exc
            return "xgboost"
        try:
            import xgboost  # noqa: F401

            return "xgboost"
        except ImportError:
            return "numpy_gbm"
    return "numpy_gbm"


def tau_tree_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "tau_tree_last_report.json")


def tau_boost_last_report_path() -> str:
    """旧对照文件名；仅 load 回退。"""
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "tau_boost_last_report.json")


def save_tau_tree_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    path = tau_tree_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)


def load_tau_tree_last_report() -> Optional[Dict[str, Any]]:
    import json

    for path in (tau_tree_last_report_path(), tau_boost_last_report_path()):
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except OSError:
            continue
        except Exception:  # noqa: BLE001
            logger.debug("load tau tree last_report failed", exc_info=True)
            continue
        if isinstance(doc, dict) and doc.get("success"):
            return doc
    return None


def _design_matrix(
    xs: Sequence[dict],
    names: Sequence[str],
    means: Optional[np.ndarray] = None,
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
        col_means = np.nanmean(x, axis=0)
        means_out = np.where(np.isfinite(col_means), col_means, 0.0).astype(np.float64)
    else:
        means_out = np.asarray(means, dtype=np.float64)
        if means_out.shape != (p,):
            means_out = np.zeros(p, dtype=np.float64)
    for j in range(p):
        col = x[:, j]
        miss = ~np.isfinite(col)
        if np.any(miss):
            col = col.copy()
            col[miss] = float(means_out[j])
            x[:, j] = col
    return x, means_out


def _oos_pack(
    preds: Sequence[Optional[float]],
    ys: Sequence[float],
    metas: Sequence[dict],
    *,
    use_minute: bool,
) -> Dict[str, Any]:
    pred_list = list(preds)
    y_list = [float(y) for y in ys]
    meta_list = list(metas)
    buckets = _oos_sign_buckets(pred_list, y_list) if y_list else {}
    out: Dict[str, Any] = {
        "n_valid": buckets.get("n_valid") if isinstance(buckets, dict) else None,
        "ic": _ic(pred_list, y_list) if y_list else None,
        "sign_hit": _sign_hit(pred_list, y_list) if y_list else None,
        "residual_var": _residual_var(pred_list, y_list) if y_list else None,
        "by_theme": _oos_by_theme(pred_list, y_list, meta_list) if y_list else {},
        "by_tau": _oos_by_tau(pred_list, y_list, meta_list) if y_list and use_minute else {},
        "buckets": (buckets.get("buckets") or {}) if isinstance(buckets, dict) else {},
        "pos_recall": buckets.get("pos_recall") if isinstance(buckets, dict) else None,
        "neg_recall": buckets.get("neg_recall") if isinstance(buckets, dict) else None,
        "n_pos": buckets.get("n_pos") if isinstance(buckets, dict) else None,
        "n_neg": buckets.get("n_neg") if isinstance(buckets, dict) else None,
    }
    return out


def _delta_oos(boost: Dict[str, Any], ridge: Dict[str, Any]) -> Dict[str, Any]:
    def _sub(a: Any, b: Any) -> Optional[float]:
        try:
            if a is None or b is None:
                return None
            return round(float(a) - float(b), 4)
        except (TypeError, ValueError):
            return None

    b06_b = ((boost.get("buckets") or {}).get("abs_ge_0_6") or {}).get("sign_hit")
    b06_r = ((ridge.get("buckets") or {}).get("abs_ge_0_6") or {}).get("sign_hit")
    open_b = ((boost.get("by_tau") or {}).get("09:30") or {}).get("sign_hit")
    open_r = ((ridge.get("by_tau") or {}).get("09:30") or {}).get("sign_hit")
    return {
        "ic": _sub(boost.get("ic"), ridge.get("ic")),
        "sign_hit": _sub(boost.get("sign_hit"), ridge.get("sign_hit")),
        "residual_var": _sub(boost.get("residual_var"), ridge.get("residual_var")),
        "strong_sign_hit": _sub(b06_b, b06_r),
        "open_sign_hit": _sub(open_b, open_r),
    }


def _weighted_mean(y: np.ndarray, w: np.ndarray) -> float:
    sw = float(np.sum(w))
    if sw <= 1e-12:
        return float(np.mean(y)) if y.size else 0.0
    return float(np.dot(y, w) / sw)


def _best_split(
    x: np.ndarray,
    y: np.ndarray,
    w: np.ndarray,
    min_leaf: int,
) -> Optional[Tuple[int, float, float]]:
    n, p = x.shape
    if n < max(4, 2 * min_leaf):
        return None
    parent_sw = float(np.sum(w))
    if parent_sw <= 1e-12:
        return None
    parent_sum = float(np.dot(y, w))
    parent_sumsq = float(np.dot(y * y, w))
    parent_sse = parent_sumsq - (parent_sum * parent_sum) / parent_sw
    best: Optional[Tuple[int, float, float]] = None
    min_w = max(1e-9, float(min_leaf))
    for j in range(p):
        order = np.argsort(x[:, j], kind="mergesort")
        xs = x[order, j]
        ys = y[order]
        ws = w[order]
        left_sw = 0.0
        left_sum = 0.0
        left_sumsq = 0.0
        for i in range(n - 1):
            wi = float(ws[i])
            yi = float(ys[i])
            left_sw += wi
            left_sum += wi * yi
            left_sumsq += wi * yi * yi
            if xs[i + 1] <= xs[i] + 1e-15:
                continue
            right_sw = parent_sw - left_sw
            if left_sw < min_w or right_sw < min_w:
                continue
            if (i + 1) < min_leaf or (n - i - 1) < min_leaf:
                continue
            left_sse = left_sumsq - (left_sum * left_sum) / left_sw
            right_sum = parent_sum - left_sum
            right_sumsq = parent_sumsq - left_sumsq
            right_sse = right_sumsq - (right_sum * right_sum) / right_sw
            gain = parent_sse - left_sse - right_sse
            if best is None or gain > best[2]:
                thr = 0.5 * (float(xs[i]) + float(xs[i + 1]))
                best = (j, thr, float(gain))
    if best is None or best[2] <= 1e-15:
        return None
    return best


def _grow_tree(
    x: np.ndarray,
    y: np.ndarray,
    w: np.ndarray,
    *,
    depth: int,
    max_depth: int,
    min_leaf: int,
    gain_acc: np.ndarray,
) -> Dict[str, Any]:
    if depth >= max_depth or x.shape[0] < max(4, 2 * min_leaf):
        return {"v": _weighted_mean(y, w)}
    split = _best_split(x, y, w, min_leaf)
    if split is None:
        return {"v": _weighted_mean(y, w)}
    feat, thr, gain = split
    gain_acc[feat] += max(0.0, gain)
    left_m = x[:, feat] <= thr
    right_m = ~left_m
    if int(np.sum(left_m)) < min_leaf or int(np.sum(right_m)) < min_leaf:
        return {"v": _weighted_mean(y, w)}
    return {
        "f": int(feat),
        "t": float(thr),
        "l": _grow_tree(
            x[left_m],
            y[left_m],
            w[left_m],
            depth=depth + 1,
            max_depth=max_depth,
            min_leaf=min_leaf,
            gain_acc=gain_acc,
        ),
        "r": _grow_tree(
            x[right_m],
            y[right_m],
            w[right_m],
            depth=depth + 1,
            max_depth=max_depth,
            min_leaf=min_leaf,
            gain_acc=gain_acc,
        ),
    }


def _apply_tree(tree: Dict[str, Any], x: np.ndarray) -> np.ndarray:
    out = np.zeros(x.shape[0], dtype=np.float64)
    idx = np.arange(x.shape[0], dtype=np.int64)

    def walk(node: Dict[str, Any], rows: np.ndarray) -> None:
        if not rows.size:
            return
        if "v" in node:
            out[rows] = float(node["v"])
            return
        feat = int(node["f"])
        thr = float(node["t"])
        go_left = x[rows, feat] <= thr
        walk(node["l"], rows[go_left])
        walk(node["r"], rows[~go_left])

    walk(tree, idx)
    return out


def _fit_numpy_gbm(
    x: np.ndarray,
    y: np.ndarray,
    w: np.ndarray,
    *,
    n_estimators: int,
    max_depth: int,
    learning_rate: float,
    subsample: float,
    rng: np.random.Generator,
) -> Tuple[Dict[str, Any], np.ndarray]:
    n = int(x.shape[0])
    p = int(x.shape[1])
    min_leaf = max(8, n // 80)
    base = _weighted_mean(y, w)
    pred = np.full(n, base, dtype=np.float64)
    trees: List[Dict[str, Any]] = []
    gain = np.zeros(p, dtype=np.float64)
    ss = min(1.0, max(0.4, float(subsample)))
    for _ in range(max(1, int(n_estimators))):
        resid = y - pred
        if ss < 0.999 and n >= 20:
            k = max(min_leaf * 2, int(round(n * ss)))
            k = min(n, max(k, 8))
            take = rng.choice(n, size=k, replace=False)
            xt, yt, wt = x[take], resid[take], w[take]
        else:
            xt, yt, wt = x, resid, w
        tree = _grow_tree(
            xt,
            yt,
            wt,
            depth=0,
            max_depth=max(1, int(max_depth)),
            min_leaf=min_leaf,
            gain_acc=gain,
        )
        trees.append(tree)
        pred = pred + float(learning_rate) * _apply_tree(tree, x)
    pack = {
        "backend": "numpy_gbm",
        "base": float(base),
        "learning_rate": float(learning_rate),
        "trees": trees,
    }
    return pack, gain


def _predict_numpy_gbm(pack: Dict[str, Any], x: np.ndarray) -> np.ndarray:
    pred = np.full(x.shape[0], float(pack.get("base") or 0.0), dtype=np.float64)
    lr = float(pack.get("learning_rate") or 0.0)
    for tree in pack.get("trees") or []:
        if isinstance(tree, dict):
            pred = pred + lr * _apply_tree(tree, x)
    return pred


def _fit_xgboost(
    x: np.ndarray,
    y: np.ndarray,
    w: np.ndarray,
    *,
    n_estimators: int,
    max_depth: int,
    learning_rate: float,
    subsample: float,
) -> Tuple[Any, np.ndarray]:
    import xgboost as xgb

    dtrain = xgb.DMatrix(x, label=y, weight=w)
    booster = xgb.train(
        {
            "max_depth": max(1, int(max_depth)),
            "eta": float(learning_rate),
            "subsample": min(1.0, max(0.4, float(subsample))),
            "colsample_bytree": 0.9,
            "lambda": 1.0,
            "min_child_weight": 8,
            "objective": "reg:squarederror",
            "tree_method": "hist",
            "nthread": 1,
            "seed": 42,
            "verbosity": 0,
        },
        dtrain,
        num_boost_round=max(1, int(n_estimators)),
    )
    gain = np.zeros(int(x.shape[1]), dtype=np.float64)
    scores = booster.get_score(importance_type="gain") or {}
    for i in range(gain.size):
        v = scores.get(f"f{i}")
        if v is not None:
            gain[i] = float(v)
    total = float(np.sum(np.clip(gain, 0.0, None)))
    if total > 1e-12:
        gain = gain / total
    return booster, gain


def _predict_xgboost(booster: Any, x: np.ndarray) -> np.ndarray:
    import xgboost as xgb

    return np.asarray(booster.predict(xgb.DMatrix(x)), dtype=np.float64)


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
) -> Tuple[Dict[str, Any], List[Optional[float]]]:
    weights = (
        theme_sample_weights(metas_tr, theme_boost=theme_boost)
        if use_theme_weights
        else None
    )
    y_mean = sum(float(y) for y in ys_tr) / max(1, len(ys_tr))
    ys_tr_dm = [float(y) - y_mean for y in ys_tr]
    fit = fit_factor_ols_from_panel(
        xs_tr,
        ys_tr_dm,
        feature_names=feat_names,
        ridge_lambda=ridge_lambda,
        standardize=True,
        sample_weights=weights,
        min_std_exempt=list(
            min_std_exempt if min_std_exempt is not None else TAU_MIN_STD_EXEMPT
        ),
        collinearity_policy="keep_all",
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
    preds_dm = _predict_rows(fit, xs_te) if xs_te else []
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
    holdout_trading_days: int = 10,
    use_theme_weights: bool = True,
    tau_hm: str = "open",
    tau_grid: Optional[Sequence[str]] = None,
    backend: Optional[str] = None,
    n_estimators: int = DEFAULT_N_ESTIMATORS,
    max_depth: int = DEFAULT_MAX_DEPTH,
    learning_rate: float = DEFAULT_LEARNING_RATE,
    subsample: float = DEFAULT_SUBSAMPLE,
) -> Dict[str, Any]:
    """同面板拟合树 + Ridge OOS 对照。不写 live / 研究套模型。"""
    t0 = time.perf_counter()
    tau_key = str(tau_hm or "open").strip() or "open"
    use_minute = tau_key.lower() not in ("", "open")
    from core.research.tau_panel import normalize_minute_tau_grid

    grid = (
        normalize_minute_tau_grid(tau_hm=tau_key, tau_grid=tau_grid)
        if use_minute
        else None
    )
    t_panel0 = time.perf_counter()
    enriched = build_tau_panels_from_bars(
        stock_bars,
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        tau_hm=tau_key,
        tau_grid=grid,
    )
    xs, ys, dates, metas = _stack_panels(enriched)
    panel_s = round(time.perf_counter() - t_panel0, 2)
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"树样本不足 n={len(ys)}（需≥20）",
            "task": "tau_tree",
            "head": TREE_HEAD,
            "sample_count": len(ys),
            "stock_count": len(enriched),
            "tau": tau_key,
            "schema": TREE_SCHEMA,
            "live_hook": False,
            "backtest_hook": False,
        }

    xs_z = _z_only_xs(xs)
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
    xs_tr, ys_tr, metas_tr = _subset(xs_z, ys, metas, train_idx)
    xs_te, ys_te, metas_te = _subset(xs_z, ys, metas, test_idx)
    drop_opt = set() if use_minute else set(MINUTE_TAU_ALL_KEYS)
    feat_names = [
        k for k in TAU_Z_FEATURES if k not in drop_opt and k not in TAU_FIT_DROP_ALIASES
    ]
    if len(ys_tr) < 16 or len(ys_te) < 8:
        return {
            "success": False,
            "error": f"Holdout 切分后样本不足 训={len(ys_tr)} 测={len(ys_te)}",
            "task": "tau_tree",
            "head": TREE_HEAD,
            "sample_count": len(ys),
            "schema": TREE_SCHEMA,
            "live_hook": False,
            "backtest_hook": False,
        }

    x_tr, means = _design_matrix(xs_tr, feat_names)
    x_te, _ = _design_matrix(xs_te, feat_names, means=means)
    y_tr = np.asarray(ys_tr, dtype=np.float64)
    w_tr = np.asarray(
        theme_sample_weights(metas_tr, theme_boost=theme_boost)
        if use_theme_weights
        else [1.0] * len(ys_tr),
        dtype=np.float64,
    )
    if w_tr.shape != y_tr.shape:
        w_tr = np.ones_like(y_tr)

    engine = resolve_tree_backend(backend)
    hyper = {
        "n_estimators": int(n_estimators),
        "max_depth": int(max_depth),
        "learning_rate": float(learning_rate),
        "subsample": float(subsample),
    }
    gain = np.zeros(len(feat_names), dtype=np.float64)
    boost_preds: List[Optional[float]]
    t_tree0 = time.perf_counter()
    if engine == "xgboost":
        model, gain = _fit_xgboost(x_tr, y_tr, w_tr, **hyper)
        raw = _predict_xgboost(model, x_te)
        boost_preds = [float(v) if math.isfinite(float(v)) else None for v in raw]
    else:
        rng = np.random.default_rng(42)
        pack, gain = _fit_numpy_gbm(x_tr, y_tr, w_tr, rng=rng, **hyper)
        raw = _predict_numpy_gbm(pack, x_te)
        boost_preds = [float(v) if math.isfinite(float(v)) else None for v in raw]
        engine = "numpy_gbm"
    tree_s = round(time.perf_counter() - t_tree0, 2)

    t_ridge0 = time.perf_counter()
    _, ridge_preds = _fit_ridge_oos(
        xs_tr,
        ys_tr,
        xs_te,
        ys_te,
        metas_tr,
        feat_names,
        ridge_lambda=ridge_lambda,
        theme_boost=theme_boost,
        use_theme_weights=use_theme_weights,
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
            "y_label_mean": round(float(np.mean(y_tr)), 6) if y_tr.size else None,
            "tau": tau_key,
        }
    )
    oos_ridge["n_train"] = len(ys_tr)
    oos_ridge["n_test"] = len(ys_te)
    oos_ridge["holdout_trading_days"] = hold_n

    report: Dict[str, Any] = {
        "success": True,
        "task": "tau_tree",
        "head": TREE_HEAD,
        "schema": TREE_SCHEMA,
        "stock_count": len(enriched),
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
        "feature_importance": _importance_rows(feat_names, gain),
        "oos": oos_boost,
        "ridge_oos": oos_ridge,
        "delta_vs_ridge": _delta_oos(oos_boost, oos_ridge),
        "live_hook": False,
        "backtest_hook": False,
        "persisted": {"success": False, "skipped": True, "reason": "shadow_only"},
        "note": (
            "ŷ_τ_tree 影子头：同标签同 Holdout vs Ridge；不写 tau_ridge_model.json，"
            "不进交易执行 / 历史回测"
        ),
    }
    attach_holdout_meta(report, split_meta)
    return report
