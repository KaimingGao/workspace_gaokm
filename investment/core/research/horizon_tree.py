"""ŷ_τ*_tree 可预测落盘：XGBoost / numpy GBM → p_up，供做 T 回测替换 Ridge。

影子报告仍写 ``*_tree_last_report.json``；可预测包写 ``*_tree_model.json``。
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
    if s in {"tree", "gbm", "xgboost", "boost", "shadow_tree"}:
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


def serialize_xgboost_booster(booster: Any) -> Dict[str, str]:
    raw = booster.save_raw("json")
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    if isinstance(raw, bytearray):
        raw = bytes(raw)
    if isinstance(raw, bytes):
        text = raw.decode("utf-8")
    else:
        text = str(raw)
    return {
        "format": "xgboost_json",
        "payload_b64": base64.b64encode(text.encode("utf-8")).decode("ascii"),
    }


def load_xgboost_booster(blob: Dict[str, Any]) -> Any:
    import xgboost as xgb

    fmt = str((blob or {}).get("format") or "")
    if fmt != "xgboost_json":
        raise ValueError(f"unsupported xgboost blob format={fmt}")
    b64 = str((blob or {}).get("payload_b64") or "")
    text = base64.b64decode(b64.encode("ascii")).decode("utf-8")
    booster = xgb.Booster()
    booster.load_model(bytearray(text.encode("utf-8")))
    return booster


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
    if eng == "xgboost":
        out["booster"] = serialize_xgboost_booster(model_obj)
    elif eng == "lightgbm":
        out["booster"] = serialize_lightgbm_booster(model_obj)
    elif eng in {"numpy_gbm", "numpy", "gbm"}:
        out["backend"] = "numpy_gbm"
        out["gbm_pack"] = dict(model_obj or {})
    else:
        raise ValueError(f"unknown tree backend={backend}")
    return out


def _row_matrix(
    features: Dict[str, Any],
    feature_names: Sequence[str],
    impute_means: Dict[str, float],
) -> np.ndarray:
    from core.research.tc_tree import _design_matrix

    row = {k: (features or {}).get(k) for k in feature_names}
    means = np.asarray(
        [float(impute_means.get(str(n)) or 0.0) for n in feature_names],
        dtype=np.float64,
    )
    x, _ = _design_matrix([row], list(feature_names), means=means)
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


def predict_tree_raw(
    features: Optional[Dict[str, Any]],
    return_model: Optional[Dict[str, Any]],
) -> Optional[float]:
    """树头原始输出。缺模型/缺特征名/非有限值返回 None。不裁剪到概率。"""
    rm = return_model if isinstance(return_model, dict) else {}
    names = [str(n) for n in (rm.get("feature_names") or []) if n]
    if not names:
        return None
    means = rm.get("impute_means") if isinstance(rm.get("impute_means"), dict) else {}
    try:
        x = _row_matrix(features or {}, names, means)
    except Exception:  # noqa: BLE001
        logger.debug("tree design matrix failed", exc_info=True)
        return None
    eng = str(rm.get("backend") or "").strip().lower()
    try:
        if eng == "xgboost":
            from core.research.tc_tree import _predict_xgboost

            blob = rm.get("booster") if isinstance(rm.get("booster"), dict) else {}
            booster = _cached_booster("xgb", blob, load_xgboost_booster)
            pred = _predict_xgboost(booster, x)
        elif eng == "lightgbm":
            from core.research.tc_tree import _predict_lightgbm

            blob = rm.get("booster") if isinstance(rm.get("booster"), dict) else {}
            booster = _cached_booster("lgb", blob, load_lightgbm_booster)
            pred = _predict_lightgbm(booster, x)
        elif eng in {"numpy_gbm", "numpy", "gbm"}:
            from core.research.tc_tree import _predict_numpy_gbm

            pred = _predict_numpy_gbm(rm.get("gbm_pack") or {}, x)
        else:
            return None
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
    return predict_tree_raw(features, return_model)


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
