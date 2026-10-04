"""ŷ_τ*_tree 可预测落盘：LightGBM → p_up / 收益，供做 T / 调仓回测替换 Ridge。

影子报告写 ``*_tree_last_report.json``；可预测包写 ``*_tree_model.json``。
``horizon_prob_backend=tree`` 时 score_policy 读此包；缺模型回退 Ridge。
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.research.horizon_prob import HORIZON_P_CLIP

HORIZON_PROB_BACKEND_RIDGE = "ridge"
HORIZON_PROB_BACKEND_TREE = "tree"
HORIZON_TREE_HEADS = ("t30", "t45", "t60", "t75", "t90")

_HORIZON_PROB_BACKEND: ContextVar[str] = ContextVar(
    "horizon_prob_backend", default=HORIZON_PROB_BACKEND_RIDGE
)
_TREE_MODEL_CACHE: Dict[str, Tuple[Tuple[float, float], Optional[Dict[str, Any]]]] = {}


def normalize_horizon_prob_backend(raw: Any = None) -> str:
    s = str(raw or "").strip().lower()
    if s in {"tree", "lightgbm", "lgb"}:
        return HORIZON_PROB_BACKEND_TREE
    return HORIZON_PROB_BACKEND_RIDGE


def current_horizon_prob_backend() -> str:
    return normalize_horizon_prob_backend(_HORIZON_PROB_BACKEND.get())


@contextmanager
def horizon_prob_backend_context(backend: Any) -> Iterator[None]:
    token = _HORIZON_PROB_BACKEND.set(normalize_horizon_prob_backend(backend))
    try:
        yield
    finally:
        _HORIZON_PROB_BACKEND.reset(token)


def tree_model_path(head: str) -> str:
    from core.paths import LIVE_DIR

    h = str(head or "").strip().lower()
    return os.path.join(LIVE_DIR, f"{h}_tree_model.json")


def _mtime_or_missing(path: str) -> float:
    try:
        return os.path.getmtime(path) if os.path.isfile(path) else -1.0
    except OSError:
        return -1.0


def _means_dict(feature_names: Sequence[str], means: Optional[np.ndarray]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if means is None:
        return out
    arr = np.asarray(means, dtype=np.float64).reshape(-1)
    for i, name in enumerate(feature_names):
        if i >= arr.size:
            break
        v = float(arr[i])
        out[str(name)] = 0.0 if not math.isfinite(v) else v
    return out


def serialize_lightgbm_booster(booster: Any) -> Dict[str, str]:
    # lgb.Booster.save_model 只接受文件路径字符串，用临时文件中转
    import tempfile

    with tempfile.NamedTemporaryFile(mode="r", suffix=".txt", delete=True, encoding="utf-8") as tmp:
        tmp_path = tmp.name
    booster.save_model(tmp_path)
    try:
        with open(tmp_path, encoding="utf-8") as f:
            text = f.read()
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    return {
        "format": "lightgbm_string",
        "payload_b64": base64.b64encode(text.encode("utf-8")).decode("ascii"),
    }


def load_lightgbm_booster(blob: Dict[str, Any]) -> Any:
    import lightgbm as lgb

    fmt = str((blob or {}).get("format") or "")
    if fmt != "lightgbm_string":
        raise ValueError(f"unsupported lightgbm blob format={fmt}")
    b64 = str((blob or {}).get("payload_b64") or "")
    text = base64.b64decode(b64.encode("ascii")).decode("utf-8")
    return lgb.Booster(model_str=text)


def pack_tree_return_model(
    *,
    head: str,
    backend: str,
    feature_names: Sequence[str],
    impute_means: Sequence[float] | np.ndarray,
    model_obj: Any,
    schema: str,
    hyperparams: Optional[dict] = None,
    target: Optional[str] = None,
    kind: str = "prob",
    y_label: Optional[str] = None,
) -> Dict[str, Any]:
    names = [str(n) for n in feature_names]
    eng = str(backend or "").strip().lower()
    kind_s = str(kind or "prob").strip().lower()
    is_return = kind_s in {"return", "reg", "regression", "pct"}
    out: Dict[str, Any] = {
        "success": True,
        "head_kind": "return" if is_return else "prob",
        "head": str(head),
        "backend": eng,
        "feature_names": names,
        "impute_means": _means_dict(names, np.asarray(impute_means, dtype=np.float64)),
        "schema": str(schema),
        "y_demeaned": False,
        "y_spec": (
            {
                "unit": "pct",
                "label": str(y_label or "return"),
                "solver": eng,
            }
            if is_return
            else {
                "unit": "prob",
                "label": "I(window_return>0)",
                "solver": eng,
            }
        ),
    }
    if target:
        out["target"] = str(target)
    if isinstance(hyperparams, dict):
        out["hyperparams"] = dict(hyperparams)
    if eng != "lightgbm":
        raise ValueError(f"unknown tree backend={backend}；仅支持 lightgbm")
    out["booster"] = serialize_lightgbm_booster(model_obj)
    return out


def _stored_feature_z(
    rm: Dict[str, Any],
) -> Tuple[Optional[Dict[str, float]], Optional[Dict[str, float]]]:
    """训练集特征 z-score。旧模型没有这份统计时返回空，预测保持原值。"""
    from core.research.feature_standardize import resolve_feature_zscore

    candidates = [rm]
    inner = rm.get("return_model") if isinstance(rm.get("return_model"), dict) else None
    if inner is not None:
        candidates.append(inner)
    for src in candidates:
        hp = src.get("hyperparams") if isinstance(src.get("hyperparams"), dict) else {}
        if not resolve_feature_zscore(src, hp, default=False):
            continue
        means = hp.get("zscore_means") if isinstance(hp.get("zscore_means"), dict) else None
        stds = hp.get("zscore_stds") if isinstance(hp.get("zscore_stds"), dict) else None
        if means and stds:
            return means, stds
        means = src.get("zscore_means") if isinstance(src.get("zscore_means"), dict) else None
        stds = src.get("zscore_stds") if isinstance(src.get("zscore_stds"), dict) else None
        if means and stds:
            return means, stds
        means = src.get("z_means") if isinstance(src.get("z_means"), dict) else None
        stds = src.get("z_stds") if isinstance(src.get("z_stds"), dict) else None
        if means and stds:
            return means, stds
    return None, None


def _row_matrix(
    features: Dict[str, Any],
    feature_names: Sequence[str],
    impute_means: Dict[str, float],
    *,
    z_means: Optional[Dict[str, float]] = None,
    z_stds: Optional[Dict[str, float]] = None,
) -> np.ndarray:
    from core.research.tc_tree import _apply_feature_z, _design_matrix

    row = {k: (features or {}).get(k) for k in feature_names}
    means = np.asarray(
        [float(impute_means.get(str(n)) or 0.0) for n in feature_names],
        dtype=np.float64,
    )
    x, _ = _design_matrix([row], list(feature_names), means=means)
    if isinstance(z_means, dict) and isinstance(z_stds, dict) and z_stds:
        mu = np.asarray(
            [float(z_means.get(str(n)) or 0.0) for n in feature_names],
            dtype=np.float64,
        )
        sd = np.asarray(
            [float(z_stds.get(str(n)) or 1.0) for n in feature_names],
            dtype=np.float64,
        )
        x = _apply_feature_z(x, mu, sd)
    return x


_BOOSTER_OBJ_CACHE: Dict[Tuple[str, int], Any] = {}


def _cached_booster(kind: str, blob: Dict[str, Any], loader) -> Any:
    key = (str(kind), id(blob))
    hit = _BOOSTER_OBJ_CACHE.get(key)
    if hit is not None:
        return hit
    booster = loader(blob)
    _BOOSTER_OBJ_CACHE[key] = booster
    return booster


def tree_return_model_collapsed(model_doc: Optional[Dict[str, Any]]) -> bool:
    """best_iteration 过小（stump）时 ŷ 是训练集均值，不能当收益预测。"""
    from core.research.tc_tree import lgb_best_iteration_collapsed

    rm = _return_model_doc(model_doc)
    hp = rm.get("hyperparams") if isinstance(rm.get("hyperparams"), dict) else {}
    return lgb_best_iteration_collapsed(hp.get("best_iteration"))


def predict_tree_raw(
    features: Optional[Dict[str, Any]],
    return_model: Optional[Dict[str, Any]],
) -> Optional[float]:
    """树头原始输出。缺模型/缺特征名/非有限值返回 None。不裁剪到概率。"""
    rm = return_model if isinstance(return_model, dict) else {}
    if tree_return_model_collapsed(rm):
        return None
    names = [str(n) for n in (rm.get("feature_names") or []) if n]
    if not names:
        return None
    means = rm.get("impute_means") if isinstance(rm.get("impute_means"), dict) else {}
    z_means, z_stds = _stored_feature_z(rm)
    try:
        x = _row_matrix(features or {}, names, means, z_means=z_means, z_stds=z_stds)
    except Exception:  # noqa: BLE001
        logger.debug("tree design matrix failed", exc_info=True)
        return None
    eng = str(rm.get("backend") or "").strip().lower()
    try:
        if eng != "lightgbm":
            return None
        from core.research.tc_tree import _predict_lightgbm

        blob = rm.get("booster") if isinstance(rm.get("booster"), dict) else {}
        booster = _cached_booster("lgb", blob, load_lightgbm_booster)
        pred = _predict_lightgbm(booster, x)
    except Exception:  # noqa: BLE001
        logger.debug("tree predict failed backend=%s", eng, exc_info=True)
        return None
    if pred is None or len(pred) < 1:
        return None
    p = float(pred[0])
    if not math.isfinite(p):
        return None
    return p


def predict_tree_p_up(
    features: Optional[Dict[str, Any]],
    return_model: Optional[Dict[str, Any]],
) -> Optional[float]:
    """树头 → p_up∈(0,1)。缺模型/缺特征名返回 None。"""
    p = predict_tree_raw(features, return_model)
    if p is None:
        return None
    return max(HORIZON_P_CLIP, min(p, 1.0 - HORIZON_P_CLIP))


def predict_tree_return(
    features: Optional[Dict[str, Any]],
    return_model: Optional[Dict[str, Any]],
) -> Optional[float]:
    """回归树头 → 百分点收益。缺模型返回 None。"""
    y = predict_tree_raw(features, return_model)
    if y is None:
        return None
    return float(y)


def _return_model_doc(return_model: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    rm = return_model if isinstance(return_model, dict) else {}
    inner = rm.get("return_model")
    if isinstance(inner, dict) and inner.get("feature_names"):
        return inner
    return rm


def explain_tree_return(
    features: Optional[Dict[str, Any]],
    return_model: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """树预测的贡献拆解。偏置 + 各因子贡献 = total，与 ``predict_tree_return`` 同口径。

    LightGBM 用 pred_contrib。
    """
    rm = _return_model_doc(return_model)
    names = [str(n) for n in (rm.get("feature_names") or []) if n]
    if not names:
        return None
    means = rm.get("impute_means") if isinstance(rm.get("impute_means"), dict) else {}
    z_means, z_stds = _stored_feature_z(rm)
    try:
        x = _row_matrix(features or {}, names, means, z_means=z_means, z_stds=z_stds)
    except Exception:  # noqa: BLE001
        logger.debug("tree explain matrix failed", exc_info=True)
        return None
    eng = str(rm.get("backend") or "").strip().lower()
    contrib_row = None
    try:
        if eng != "lightgbm":
            return None
        blob = rm.get("booster") if isinstance(rm.get("booster"), dict) else {}
        booster = _cached_booster("lgb", blob, load_lightgbm_booster)
        pred = booster.predict(x, pred_contrib=True)
        contrib_row = np.asarray(pred, dtype=np.float64)[0]
    except Exception:  # noqa: BLE001
        logger.debug("tree explain failed backend=%s", eng, exc_info=True)
        return None
    if contrib_row is None or int(contrib_row.shape[0]) < len(names) + 1:
        return None
    try:
        from core.signal.factors.meta.registry import factor_label
    except Exception:  # noqa: BLE001
        factor_label = lambda k: str(k)  # noqa: E731

    bias = float(contrib_row[len(names)])
    seen = x[0]
    raw = features if isinstance(features, dict) else {}
    terms: List[Dict[str, Any]] = []
    total = bias
    for i, name in enumerate(names):
        c = float(contrib_row[i])
        total += c
        if abs(c) < 1e-8:
            continue
        src = raw.get(name)
        imputed = src is None or src == ""
        term: Dict[str, Any] = {
            "key": name,
            "label": str(factor_label(name) or name),
            "z": round(float(seen[i]), 4),
            "contrib": round(c, 6),
        }
        if imputed:
            term["note"] = "均值填"
        terms.append(term)
    y_hat = predict_tree_return(features, rm)
    if y_hat is not None and abs(float(total) - float(y_hat)) < 1e-3:
        total = float(y_hat)
    terms.sort(key=lambda t: -abs(float(t.get("contrib") or 0.0)))
    terms = terms[:24]
    return {
        "intercept": round(bias, 6),
        "terms": terms,
        "total": round(float(total), 6),
        "model_role": "tree",
    }


def tree_tip_formula(
    features: Optional[Dict[str, Any]],
    model_doc: Optional[Dict[str, Any]],
    *,
    y_hat: Optional[float] = None,
) -> Dict[str, Any]:
    """tip 用的树组成。合计对齐 ``y_hat``（表列分数），缺拆解时仍写出合计。"""
    rm = _return_model_doc(model_doc)
    try:
        expl = explain_tree_return(features, rm)
    except Exception:  # noqa: BLE001
        logger.debug("explain_tree_return failed", exc_info=True)
        expl = None
    if not isinstance(expl, dict):
        y = None if y_hat is None else round(float(y_hat), 6)
        return {
            "intercept": y,
            "terms": [],
            "total": y,
            "model_role": "tree",
        }
    out = dict(expl)
    if y_hat is not None and out.get("total") is not None:
        try:
            if abs(float(out["total"]) - float(y_hat)) < 1e-3:
                out["total"] = round(float(y_hat), 6)
        except (TypeError, ValueError):
            pass
    return out


def persist_tree_model_doc(
    head: str,
    report: Dict[str, Any],
    *,
    note: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    """把报告里的 return_model 写入 ``{head}_tree_model.json``。"""
    from core.numbers import now_iso_utc
    from core.research.horizon_prob import horizon_promote_gate

    if not report.get("success"):
        return {"success": False, "error": report.get("error") or "no report"}
    rm = report.get("tree_return_model")
    if not isinstance(rm, dict):
        rm = report.get("return_model")
    if not isinstance(rm, dict) or not rm.get("feature_names"):
        return {"success": False, "error": "tree_return_model missing"}
    gate = horizon_promote_gate(report)
    if not force and not gate.get("ok"):
        return {
            "success": False,
            "error": "promote 未过闸：" + "；".join(gate.get("blockers") or []),
            "promote_gate": gate,
        }
    path = tree_model_path(head)
    doc = {
        "success": True,
        "promoted_at": now_iso_utc(),
        "note": note or f"{head}_tree promote",
        "return_model": rm,
        "oos": report.get("oos"),
        "ridge_oos": report.get("ridge_oos"),
        "delta_vs_ridge": report.get("delta_vs_ridge"),
        "sample_count": report.get("sample_count"),
        "stock_count": report.get("stock_count"),
        "schema": report.get("schema"),
        "backend": rm.get("backend") or report.get("backend"),
        "head": report.get("head") or head,
        "head_kind": str(rm.get("head_kind") or "prob"),
        "feature_names": list(rm.get("feature_names") or []),
        "tree_shape_features": list(report.get("tree_shape_features") or []),
        "live_hook": False,
        "backtest_hook": True,
        "promote_gate": gate,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, doc)
    _TREE_MODEL_CACHE.pop(str(head).strip().lower(), None)
    out = dict(doc)
    out["path"] = path
    return out


def load_tree_model_doc(head: str) -> Optional[Dict[str, Any]]:
    h = str(head or "").strip().lower()
    path = tree_model_path(h)
    key = (_mtime_or_missing(path),)
    cached = _TREE_MODEL_CACHE.get(h)
    if cached is not None and cached[0] == key:
        return cached[1]
    doc: Optional[Dict[str, Any]] = None
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get("return_model"), dict):
                doc = loaded
        except Exception:  # noqa: BLE001
            logger.debug("load %s tree model failed", h, exc_info=True)
    _TREE_MODEL_CACHE[h] = (key, doc)
    return doc


def clear_tree_model_cache() -> None:
    _TREE_MODEL_CACHE.clear()
    _BOOSTER_OBJ_CACHE.clear()
