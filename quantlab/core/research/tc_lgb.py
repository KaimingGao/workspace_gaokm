"""ŷ_τc 回归 LightGBM（正式唯一后端；对齐 ``oo_lgb``）。

超参沿用 ``HORIZON_LGB``；标签仍是 τ→close 百分点收益。
推理包 schema=``tau_lgb_v1``；经 ``predict_tree_return`` / tip ``explain_tree_return``。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

SCHEMA_TC_LGB = "tau_lgb_v1"


def _raw_moments(x_raw: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """入模 raw 的列 μ/σ（CS-z 前）。勿写入 zscore_*，避免污染截面推理。"""
    impute = np.nanmean(x_raw, axis=0)
    impute = np.where(np.isfinite(impute), impute, 0.0)
    impute_std = np.nanstd(x_raw, axis=0)
    impute_std = np.where(np.isfinite(impute_std), impute_std, 0.0)
    return impute, impute_std


def is_tc_lightgbm_blob(blob: Any) -> bool:
    """与 ``is_oo_lightgbm_blob`` 同口径：有 feature_names + booster 即认 LGB。

    勿因遗留 ``schema`` 含 ridge 先拒；带 booster 的包一律按 LightGBM。
    """
    if not isinstance(blob, dict):
        return False
    names = blob.get("feature_names")
    if not isinstance(names, list) or not names:
        return False
    if isinstance(blob.get("booster"), dict) and blob["booster"].get("payload_b64"):
        return True
    if str(blob.get("booster_b64") or "").strip():
        return True
    return False


def tc_model_feature_names(model: Any) -> List[str]:
    """入模列名：LightGBM 走 ``feature_names``；旧 Ridge 走 ``coefficients``。"""
    if isinstance(model, dict):
        names = model.get("feature_names")
        if isinstance(names, (list, tuple)) and names:
            return [str(k) for k in names if str(k).strip()]
        coefs = model.get("coefficients") or {}
        if isinstance(coefs, dict):
            return [str(k) for k in coefs.keys() if str(k).strip()]
        return []
    feat_names = getattr(model, "feature_names", None)
    if isinstance(feat_names, (list, tuple)) and feat_names:
        return [str(k) for k in feat_names if str(k).strip()]
    coefs = getattr(model, "coefficients", None) or {}
    if isinstance(coefs, dict):
        return [str(k) for k in coefs.keys() if str(k).strip()]
    return []


def _importance_rows_tc(names: Sequence[str], gain: np.ndarray) -> List[Dict[str, Any]]:
    try:
        from core.signal.factors.meta.registry import factor_label
    except Exception:  # noqa: BLE001
        factor_label = lambda k: str(k)  # noqa: E731

    total = float(np.sum(np.clip(gain, 0.0, None)))
    rows: List[Dict[str, Any]] = []
    for i, name in enumerate(names):
        g = float(gain[i]) if i < len(gain) else 0.0
        key = str(name)
        rows.append(
            {
                "key": key,
                "label": str(factor_label(key) or key),
                "gain": round(g, 6),
                "share": round(g / total, 4) if total > 1e-12 else 0.0,
            }
        )
    rows.sort(key=lambda r: -abs(float(r.get("gain") or 0.0)))
    return rows


def fit_tc_lgb_matrix(
    x: np.ndarray,
    y: np.ndarray,
    names: Sequence[str],
    *,
    row_idx: Optional[Sequence[int]] = None,
    min_samples: int = 24,
    fitted_as_of: Optional[str] = None,
    row_dates: Optional[Sequence[str]] = None,
    sample_weights: Optional[Sequence[float]] = None,
    cross_section_zscore: bool = True,
    clock: str = "open",
    callbacks: Optional[Sequence[Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """在 τc 面板完整行上拟合回归 LightGBM。返回 ``(model_blob|None, report)``。"""
    from core.research.feature_standardize import (
        dates_for_complete_rows,
        stamp_cross_section_zscore,
    )
    from core.research.horizon_lgb import (
        HORIZON_LGB,
        _fit_lightgbm,
        _gain_from_booster,
    )
    from core.research.horizon_tree import _means_dict, pack_tree_return_model
    from core.research.oo_model_compact import _align_row_weights, _prepare_matrix

    y = np.asarray(y, dtype=np.float64)
    n_full = int(y.shape[0])
    row_pos = None if row_idx is None else np.asarray(row_idx, dtype=np.int64)
    n_raw = int(n_full if row_pos is None else row_pos.size)
    need = max(8, int(min_samples or 24))
    if n_raw < need:
        return None, {
            "success": False,
            "error": f"训练样本不足（{n_raw} < {min_samples}）",
            "sample_count": n_raw,
        }

    feature_names = [str(n) for n in names]
    x_c, y_c, active, excluded, prep_meta = _prepare_matrix(
        x,
        y,
        feature_names,
        row_pos=row_pos,
    )
    if x_c is None or y_c is None or not active:
        return None, {
            "success": False,
            "error": "样本不足或矩阵无法对齐，无法拟合 LightGBM",
            "sample_count": prep_meta.get("raw_sample_count", n_raw),
            "excluded_features": excluded,
        }

    # 与 Ridge 同：先对齐到 complete 行，再按 L2 keep_mask 切片（theme_boost 才生效）
    row_w = _align_row_weights(
        sample_weights,
        n_full=n_full,
        row_pos=row_pos,
        complete_idx=prep_meta.get("complete_row_indices"),
    )
    use_cs = bool(cross_section_zscore)
    date_src = row_dates if use_cs else None
    if date_src is not None and row_pos is not None:
        date_src = [str(row_dates[int(i)])[:10] for i in row_pos.tolist()]
    dates_c = dates_for_complete_rows(
        date_src, prep_meta.get("complete_row_indices") or []
    )
    clk = str(clock or "open").strip() or "open"
    if use_cs and dates_c is not None and len(dates_c) == int(x_c.shape[0]):
        from core.research.materialize.cube_z import (
            align_fit_rows_to_l2,
            cs_z_matrix_prefer_cube,
        )

        x_c, y_c, dates_c, keep_mask = align_fit_rows_to_l2(
            x_c, y_c, dates_c, clock=clk
        )
        if (
            row_w is not None
            and keep_mask is not None
            and not bool(np.asarray(keep_mask).all())
        ):
            row_w = np.asarray(row_w, dtype=np.float64)[keep_mask]
        # μ/σ 取 z 之前的 raw。inplace 把 z 写回同一块，避免再留一份 float64。
        impute, impute_std = _raw_moments(x_c)
        x_z = cs_z_matrix_prefer_cube(
            x_c, active, dates_c, clock=clk, inplace=True
        )
    else:
        from core.research.oo_model_compact import _zscore_matrix

        impute, impute_std = _raw_moments(x_c)
        x_z, _zm, _zs = _zscore_matrix(x_c, active)

    if x_z is None or int(np.asarray(x_z).shape[0]) < need:
        return None, {
            "success": False,
            "error": "CS-z 后样本不足，无法拟合 LightGBM",
            "sample_count": int(np.asarray(x_z).shape[0]) if x_z is not None else 0,
        }

    n_z = int(x_z.shape[0])
    n_fit = n_z
    if row_w is not None and int(np.asarray(row_w).shape[0]) == n_z:
        w = np.asarray(row_w, dtype=np.float64)
        w = np.where(np.isfinite(w) & (w > 0), w, 1.0)
    else:
        w = np.ones(n_z, dtype=np.float64)

    hyper = dict(HORIZON_LGB)
    stamp_cross_section_zscore(hyper)
    y_fit = np.asarray(y_c, dtype=np.float64)
    hold = [x_z]
    x_z = None
    x_c = None
    booster, gain = _fit_lightgbm(
        hold[0],
        y_fit,
        w,
        n_estimators=int(hyper["n_estimators"]),
        max_depth=int(hyper["max_depth"]),
        learning_rate=float(hyper["learning_rate"]),
        subsample=float(hyper["subsample"]),
        objective="regression",
        num_leaves=int(hyper["num_leaves"]),
        colsample_bytree=float(hyper["colsample_bytree"]),
        lambda_l1=float(hyper["lambda_l1"]),
        lambda_l2=float(hyper["lambda_l2"]),
        min_data_in_leaf=int(hyper["min_data_in_leaf"]),
        callbacks=callbacks,
        release_hold=hold,
    )
    blob = pack_tree_return_model(
        head="tc",
        backend="lightgbm",
        feature_names=active,
        impute_means=impute,
        model_obj=booster,
        schema=SCHEMA_TC_LGB,
        hyperparams=hyper,
        kind="return",
        y_label="close[T]/price[τ]-1",
    )
    blob["impute_stds"] = _means_dict(active, impute_std)
    fi = _importance_rows_tc(
        active, gain if gain is not None else _gain_from_booster(booster, len(active))
    )
    blob["feature_importance"] = fi
    blob["sample_count"] = int(n_fit)
    blob["excluded_features"] = list(excluded or [])
    blob["solver"] = "lightgbm"
    blob["coefficients"] = {}
    blob["intercept"] = 0.0
    blob["horizon_mode"] = "tau_to_close"
    if fitted_as_of:
        blob["fitted_as_of"] = str(fitted_as_of)
    report: Dict[str, Any] = {
        "success": True,
        "backend": "lightgbm",
        "sample_count": int(n_fit),
        "n_features": len(active),
        "excluded_features": list(excluded or []),
        "hyperparams": hyper,
        "feature_importance": fi,
    }
    return blob, report


def predict_tc_lgb_matrix(
    model_blob: Dict[str, Any],
    x: np.ndarray,
    names: Sequence[str],
    row_idx: Sequence[int],
    row_dates: Optional[Sequence[str]] = None,
    *,
    clock: str = "open",
) -> List[Optional[float]]:
    """对指定行做回归树推理；截面模型按打分行当天 CS-z。"""
    from core.research.feature_standardize import is_cross_section_zscore
    from core.research.horizon_lgb import _predict_lightgbm
    from core.research.horizon_tree import load_lightgbm_booster

    idx = np.asarray(list(row_idx), dtype=np.int64)
    n = int(idx.size)
    if n == 0 or not is_tc_lightgbm_blob(model_blob):
        return [None] * n
    feat_names = [str(n) for n in (model_blob.get("feature_names") or [])]
    if not feat_names:
        return [None] * n
    name_j = {str(name): i for i, name in enumerate(names)}
    cols = []
    for fn in feat_names:
        j = name_j.get(fn)
        if j is None:
            return [None] * n
        cols.append(j)
    # 先切行再升 float64。先升全表会在 τc 宽因子上再占一整块。
    x_sub = np.asarray(x[np.ix_(idx, cols)], dtype=np.float64)
    clk = str(clock or "open").strip() or "open"
    if is_cross_section_zscore(model_blob):
        if row_dates is None or len(row_dates) != int(np.asarray(x).shape[0]):
            logger.debug(
                "tc lgb CS-z predict refused: row_dates missing or misaligned"
            )
            return [None] * n
        dates = [str(row_dates[int(i)])[:10] for i in idx.tolist()]
        from core.research.materialize.consumer import MaterializeRequired
        from core.research.materialize.cube_z import cs_z_matrix_prefer_cube

        try:
            x_sub = cs_z_matrix_prefer_cube(x_sub, feat_names, dates, clock=clk)
        except MaterializeRequired:
            # OOS/Holdout 缺单日 Cube：软弃权，勿整次拟合硬失败
            logger.debug(
                "tc lgb CS-z predict MaterializeRequired; soft-miss n=%s", n
            )
            return [None] * n
    else:
        means = model_blob.get("impute_means") or {}
        for j, fn in enumerate(feat_names):
            col = x_sub[:, j]
            bad = ~np.isfinite(col)
            if bad.any():
                fill = float(means.get(fn) or 0.0) if isinstance(means, dict) else 0.0
                col = col.copy()
                col[bad] = fill
                x_sub[:, j] = col
    try:
        booster = load_lightgbm_booster(model_blob.get("booster") or {})
    except Exception:  # noqa: BLE001
        logger.debug("tc lgb load booster failed", exc_info=True)
        return [None] * n
    raw = _predict_lightgbm(booster, np.asarray(x_sub, dtype=np.float64))
    out: List[Optional[float]] = []
    for v in raw:
        try:
            fv = float(v)
        except (TypeError, ValueError):
            out.append(None)
            continue
        out.append(fv if math.isfinite(fv) else None)
    return out


def predict_tc_lgb_on_cs_z(
    model_blob: Dict[str, Any],
    Z: np.ndarray,
    col_names: Sequence[str],
) -> Optional[np.ndarray]:
    """对已截面 z 的矩阵做批量推理。"""
    from core.research.horizon_lgb import _predict_lightgbm
    from core.research.horizon_tree import load_lightgbm_booster

    blob = (
        model_blob.to_dict()
        if hasattr(model_blob, "to_dict")
        else model_blob
    )
    if isinstance(blob, dict) and "return_model" in blob and not is_tc_lightgbm_blob(blob):
        inner = blob.get("return_model")
        if isinstance(inner, dict):
            blob = inner
    if not is_tc_lightgbm_blob(blob):
        return None
    feat_names = [str(n) for n in (blob.get("feature_names") or [])]
    if not feat_names:
        return None
    name_j = {str(n): i for i, n in enumerate(col_names)}
    cols: List[int] = []
    for fn in feat_names:
        j = name_j.get(fn)
        if j is None:
            return None
        cols.append(int(j))
    x = np.asarray(Z, dtype=np.float64)[:, cols]
    x = np.where(np.isfinite(x), x, 0.0)
    try:
        booster = load_lightgbm_booster(blob.get("booster") or {})
    except Exception:  # noqa: BLE001
        logger.debug("tc lgb cs-z booster load failed", exc_info=True)
        return None
    raw = _predict_lightgbm(booster, x)
    out = np.asarray(raw, dtype=np.float64)
    return np.where(np.isfinite(out), out, np.nan)


class TcLightgbmScorer:
    """把 LightGBM ŷ_τc 包适配成 tip / 立方拆解调用面（对齐 OoLightgbmScorer）。"""

    def __init__(self, blob: Dict[str, Any], *, model_role: str = "") -> None:
        self._blob = dict(blob)
        names = [str(n) for n in (self._blob.get("feature_names") or [])]
        self.coefficients: Dict[str, float] = {}
        self.feature_names = names
        self.intercept = 0.0
        self.z_means: Dict[str, float] = {}
        self.z_stds: Dict[str, float] = {}
        self.feature_zscore = True
        self.zscore_scope = str(
            self._blob.get("zscore_scope")
            or ((self._blob.get("hyperparams") or {}).get("zscore_scope"))
            or "cross_section"
        )
        self.sample_count = int(self._blob.get("sample_count") or 0)
        self.ridge_lambda = 0.0
        self.solver = "lightgbm"
        self.fitted_as_of = self._blob.get("fitted_as_of")
        self.note = str(self._blob.get("note") or "ŷ_τc LightGBM 回归")
        self.y_spec = (
            self._blob.get("y_spec")
            if isinstance(self._blob.get("y_spec"), dict)
            else None
        )
        self.model_role = str(model_role or self._blob.get("model_role") or "")
        self.feature_importance = self._blob.get("feature_importance")

    def predict(self, sub_scores: Optional[Dict[str, float]]) -> Optional[float]:
        from core.research.horizon_tree import predict_tree_return

        return predict_tree_return(sub_scores or {}, self._blob)

    def explain_prediction(
        self, sub_scores: Optional[Dict[str, float]]
    ) -> Optional[Dict[str, Any]]:
        from core.research.horizon_tree import explain_tree_return

        expl = explain_tree_return(sub_scores or {}, self._blob)
        if not isinstance(expl, dict):
            return expl
        out = dict(expl)
        out["backend"] = "lightgbm"
        out["head_kind"] = "return"
        if self.model_role:
            out["score_model_role"] = self.model_role
        return out

    def to_dict(self) -> Dict[str, Any]:
        out = dict(self._blob)
        if self.model_role:
            out["model_role"] = self.model_role
        return out

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["TcLightgbmScorer"]:
        if not isinstance(data, dict):
            return None
        blob = data
        if "return_model" in data and not is_tc_lightgbm_blob(data):
            inner = data.get("return_model")
            if isinstance(inner, dict):
                blob = inner
        if not is_tc_lightgbm_blob(blob):
            return None
        role = str(data.get("model_role") or blob.get("model_role") or "")
        return cls(dict(blob), model_role=role)


def is_tc_lightgbm_scorer(model: Any) -> bool:
    """推理对象 / 套件 JSON / 裸包是否为 ŷ_τc LightGBM。

    ``load_tau_model`` 返回外层 ``{return_model: blob}``，须解包后再认。
    """
    if isinstance(model, TcLightgbmScorer):
        return True
    if hasattr(model, "to_dict") and callable(model.to_dict):
        try:
            if is_tc_lightgbm_blob(model.to_dict()):
                return True
        except Exception:  # noqa: BLE001
            pass
    if is_tc_lightgbm_blob(model):
        return True
    if isinstance(model, dict):
        for key in ("return_model", "model"):
            inner = model.get(key)
            if isinstance(inner, dict) and is_tc_lightgbm_blob(inner):
                return True
    return False
