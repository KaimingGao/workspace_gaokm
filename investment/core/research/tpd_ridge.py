"""ŷ_tpd Ridge：开盘 Z + 前缀分钟 + tpd_lag + complexity_lag → 全日转折点密度 TPD ∈ [0,1]。

标签非有符号收益，OOS 看 Spearman IC 与中位命中（≈50% 即无信息），不用方向命中。
研究枢纽拟合；盘中写 ŷ_tpd，ŷ_tpd > y_tpd_max 则跳过做 T。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.research.cx_panel import (
    CANON_LAG_FEAT_LABELS,
    TPD_Z_FEATURES,
    DEFAULT_CX_MINUTE_TAU_HM,
    DEFAULT_CX_TAU_GRID,
    build_cx_panels_from_bars,
    resolve_feat_value,
    tpd_as_unit_01,
)
from core.research.cx_ridge import (
    CX_MIN_STD_EXEMPT,
    CX_PROMOTE_MIN_IC,
    CX_PROMOTE_MIN_N_TEST,
    _oos_by_tau,
    _oos_rank_metrics,
    _spearman,
    _stack_panels,
    _z_only_xs,
    _median,
    cx_promote_gate,
)
from core.research.factor_ols_fit import fit_factor_ols_from_panel
from core.research.tau_panel import normalize_minute_tau_grid
from core.research.tau_ridge import _predict_rows, _subset, _time_split_indices
from core.signal.minute_tau_feats import MINUTE_TAU_FEAT_LABELS, MINUTE_TAU_TPD_SHAPE_KEYS

TPD_MIN_STD_EXEMPT = TPD_Z_FEATURES
TPD_PROMOTE_MIN_N_TEST = CX_PROMOTE_MIN_N_TEST
TPD_PROMOTE_MIN_IC = CX_PROMOTE_MIN_IC


def tpd_promote_gate(report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """promote 闸：n 与 Spearman IC（y_tpd 无方向命中）。"""
    return cx_promote_gate(report)


def fit_tpd_ridge_report(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    minute_by_code_date: Optional[Dict[str, Dict[str, Sequence[dict]]]] = None,
    ridge_lambda: float = 1.0,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    train_frac: float = 0.9,
    minute_tau_hm: Optional[str] = None,
    tau_grid: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """池化拟合 ŷ_tpd + 时间 OOS。"""
    live_hm = str(minute_tau_hm or DEFAULT_CX_MINUTE_TAU_HM).strip() or DEFAULT_CX_MINUTE_TAU_HM
    grid = normalize_minute_tau_grid(
        tau_hm=live_hm,
        tau_grid=list(tau_grid) if tau_grid is not None else list(DEFAULT_CX_TAU_GRID),
    )
    enriched = build_cx_panels_from_bars(
        stock_bars,
        minute_by_code_date=minute_by_code_date,
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        minute_tau_hm=live_hm,
        tau_grid=grid,
        label="tpd",
    )
    xs, ys, dates, metas = _stack_panels(enriched)
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"tpd 样本不足 n={len(ys)}（需≥20 且需分钟线）",
            "task": "tpd_ridge",
            "sample_count": len(ys),
            "stock_count": len(enriched),
            "tau_grid": list(grid),
            "minute_tau_hm": live_hm,
        }

    xs_z = _z_only_xs(xs, TPD_Z_FEATURES)
    train_idx, test_idx = _time_split_indices(dates, train_frac=train_frac)
    xs_tr, ys_tr, _metas_tr = _subset(xs_z, ys, metas, train_idx)
    xs_te, ys_te, metas_te = _subset(xs_z, ys, metas, test_idx)

    y_mean = sum(float(y) for y in ys_tr) / max(1, len(ys_tr))
    ys_tr_dm = [float(y) - y_mean for y in ys_tr]

    fit = fit_factor_ols_from_panel(
        xs_tr,
        ys_tr_dm,
        feature_names=list(TPD_Z_FEATURES),
        ridge_lambda=ridge_lambda,
        standardize=True,
        min_std_exempt=list(TPD_MIN_STD_EXEMPT),
        collinearity_policy="keep_all",
        impute_keys=list(MINUTE_TAU_TPD_SHAPE_KEYS),
    )
    if not fit.get("success"):
        fit = {
            "success": True,
            "intercept": 0.0,
            "coefficients": {},
            "active_features": [],
            "zscore_means": {},
            "zscore_stds": {},
            "note": "Z 方差不足，ŷ_tpd 用训练均值",
        }

    oos: Dict[str, Any] = {"n_train": len(ys_tr), "n_test": len(test_idx)}
    if xs_te:
        preds_dm = _predict_rows(fit, xs_te)
        preds = [
            (float(p) + y_mean) if p is not None else None for p in (preds_dm or [])
        ]
        oos.update(_oos_rank_metrics(preds, ys_te))
        if grid and len(grid) > 1:
            oos["by_tau"] = _oos_by_tau(preds, ys_te, metas_te)
        cx_vals: List[float] = []
        tpd_vals: List[float] = []
        for y, m in zip(ys_te, metas_te):
            if not isinstance(m, dict):
                continue
            cx = m.get("y_complexity")
            if cx is None:
                continue
            try:
                cx_vals.append(float(cx))
                tpd_vals.append(float(y))
            except (TypeError, ValueError):
                continue
        if len(cx_vals) >= 8:
            oos["spearman_y_complexity_y_tpd"] = _spearman(cx_vals, tpd_vals)
    oos["y_label_mean"] = round(y_mean, 4)
    oos["train_frac"] = float(train_frac)
    oos["tau_grid"] = list(grid)
    oos["minute_tau_hm"] = live_hm
    try:
        from core.research.path_panel import feature_fill_rates

        oos["feature_fill"] = feature_fill_rates(xs_z, TPD_Z_FEATURES)
    except Exception:  # noqa: BLE001
        logger.debug("tpd feature fill audit failed", exc_info=True)

    oos["label_dist"] = {
        "n_labeled": len(ys),
        "y_mean": round(sum(ys) / float(len(ys)), 4) if ys else None,
        "y_median": _median(ys),
        "n_unique_days": len({str(d)[:10] for d in dates}),
        "rows_per_day": round(len(ys) / max(1, len({str(d)[:10] for d in dates})), 2),
    }

    ys_all_dm = [float(y) - y_mean for y in ys]
    fit_full = fit_factor_ols_from_panel(
        xs_z,
        ys_all_dm,
        feature_names=list(TPD_Z_FEATURES),
        ridge_lambda=ridge_lambda,
        standardize=True,
        min_std_exempt=list(TPD_MIN_STD_EXEMPT),
        collinearity_policy="keep_all",
        impute_keys=list(MINUTE_TAU_TPD_SHAPE_KEYS),
    )
    model = dict(fit_full if fit_full.get("success") else fit)
    try:
        model["intercept"] = round(float(model.get("intercept") or 0.0) + y_mean, 6)
    except (TypeError, ValueError):
        model["intercept"] = round(y_mean, 6)
    model["intercept_demeaned"] = round(float(fit.get("intercept") or 0.0), 6)
    model["y_label_mean"] = round(y_mean, 6)
    model["y_demeaned"] = True
    from core.signal.minute_tau_grid import format_shared_tau_formula

    y_formula = format_shared_tau_formula("TPD", grid or [live_hm])
    model["y_spec"] = {
        "formula": y_formula,
        "unit": "tpd_01",
        "tau": live_hm,
        "tau_grid": list(grid),
        "note": (
            "TPD=连续 5m 段内方向反转次数/有效内点（午休跳空不计）；"
            "y_tpd∈[0,1]，0=无反转、1=每根都反转。特征=开盘 Z + ≤τ 前缀 + prefix_tpd + tpd_lag1/ma5 + complexity_lag1/ma5。"
            " 与 ŷ_complexity 共享开盘 Z 与路径小包，额外吃前缀 TPD。OOS 看 IC / 中位命中，不看方向命中。"
        ),
    }
    model["extra_features"] = list(TPD_Z_FEATURES)
    model["minute_tau_hm"] = live_hm
    model["tau_grid"] = list(grid)
    model["feat_labels"] = {
        **dict(MINUTE_TAU_FEAT_LABELS),
        **dict(CANON_LAG_FEAT_LABELS),
    }

    report = {
        "success": True,
        "task": "tpd_ridge",
        "stock_count": len(enriched),
        "sample_count": len(ys),
        "oos": oos,
        "return_model": model,
        "schema": "tpd_ridge_v1",
        "target": "path_tpd_5m",
        "minute_tau_hm": live_hm,
        "tau_grid": list(grid),
        "dual_score_head": "y_tpd",
        "note": "开盘 Z + 多 τ 前缀 + prefix_tpd + tpd_lag + complexity_lag → 全日转折点密度 [0,1]；ŷ_tpd>y_tpd_max 跳过做 T",
    }
    report["promote_gate"] = tpd_promote_gate(report)
    return report


def tpd_model_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "tpd_ridge_model.json")


def tpd_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "tpd_ridge_last_report.json")


_TPD_MODEL_CACHE: Optional[Tuple[Tuple[float, float], Optional[Dict[str, Any]]]] = None


def save_tpd_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    if not isinstance(report.get("return_model"), dict):
        return
    path = tpd_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)
    global _TPD_MODEL_CACHE
    _TPD_MODEL_CACHE = None


def _mtime_or_missing(path: str) -> float:
    try:
        return os.path.getmtime(path) if os.path.isfile(path) else -1.0
    except OSError:
        return -1.0


def load_tpd_last_report() -> Optional[Dict[str, Any]]:
    path = tpd_last_report_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001
        logger.debug("load_tpd_last_report failed", exc_info=True)
        return None
    if not isinstance(doc, dict) or not doc.get("success"):
        return None
    if not isinstance(doc.get("return_model"), dict):
        return None
    return doc


def load_tpd_model() -> Optional[Dict[str, Any]]:
    global _TPD_MODEL_CACHE
    model_p = tpd_model_path()
    report_p = tpd_last_report_path()
    key = (_mtime_or_missing(model_p), _mtime_or_missing(report_p))
    if _TPD_MODEL_CACHE is not None and _TPD_MODEL_CACHE[0] == key:
        return _TPD_MODEL_CACHE[1]
    doc: Optional[Dict[str, Any]] = None
    if os.path.isfile(model_p):
        try:
            with open(model_p, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get("return_model"), dict):
                doc = loaded
        except Exception:  # noqa: BLE001
            logger.debug("load_tpd_model failed", exc_info=True)
    if doc is None:
        fallback = load_tpd_last_report()
        if fallback:
            out = dict(fallback)
            out["_shadow"] = True
            doc = out
    _TPD_MODEL_CACHE = (key, doc)
    return doc


def persist_tpd_model(
    report: Dict[str, Any],
    *,
    note: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    if not report.get("success"):
        return {"success": False, "error": report.get("error") or "no report"}
    rm = report.get("return_model")
    if not isinstance(rm, dict):
        return {"success": False, "error": "return_model missing"}
    gate = tpd_promote_gate(report)
    if not force and not gate.get("ok"):
        return {
            "success": False,
            "error": "promote 未过闸：" + "；".join(gate.get("blockers") or []),
            "promote_gate": gate,
        }
    from core.numbers import now_iso_utc

    doc = {
        "success": True,
        "promoted_at": now_iso_utc(),
        "note": note or "tpd_ridge promote",
        "return_model": rm,
        "oos": report.get("oos"),
        "sample_count": report.get("sample_count"),
        "stock_count": report.get("stock_count"),
        "schema": report.get("schema") or "tpd_ridge_v1",
        "minute_tau_hm": report.get("minute_tau_hm") or rm.get("minute_tau_hm"),
        "tau_grid": report.get("tau_grid") or rm.get("tau_grid"),
        "promote_gate": gate,
        "dual_score_head": "y_tpd",
    }
    path = tpd_model_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, doc)
    global _TPD_MODEL_CACHE
    _TPD_MODEL_CACHE = None
    out = dict(doc)
    out["path"] = path
    return out


def predict_tpd_from_features(
    features: Dict[str, Optional[float]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    """开盘 Z + 前缀分钟 + tpd_lag → ŷ_tpd ∈ [0,1]。"""
    doc = model_doc if model_doc is not None else load_tpd_model()
    if not doc:
        return None
    rm = doc.get("return_model") or {}
    names = list(rm.get("extra_features") or TPD_Z_FEATURES)
    if not names:
        names = list(TPD_Z_FEATURES)
    row: Dict[str, Optional[float]] = {}
    for k in names:
        row[k] = resolve_feat_value(features, k)
    preds = _predict_rows(rm, [row])
    if not preds or preds[0] is None:
        return None
    return tpd_as_unit_01(float(preds[0]))
