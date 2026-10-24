"""ŷ_τ* 树头内核：LightGBM → p_up，供主卡双训（``backends.tree``）。

正式路径：``fit_horizon_dual_report`` → 启用套包内 ``backend`` / ``backends``。
遗留 API ``horizon_prob_backend=tree`` 强制读启用套 ``backends.tree``（或包顶 ``backend=tree``）。
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
        if str(hyperparams.get("zscore_scope") or "") == "cross_section":
            out["zscore_scope"] = "cross_section"
            out["feature_zscore"] = True
            out["zscore_means"] = {}
            out["zscore_stds"] = {}
    if eng != "lightgbm":
        raise ValueError(f"unknown tree backend={backend}；仅支持 lightgbm")
    out["booster"] = serialize_lightgbm_booster(model_obj)
    return out


def stamp_tree_fitted_at(report: Dict[str, Any]) -> Dict[str, Any]:
    """成功拟合的树报告打 ``fitted_at``，并写进 ``tree_return_model``。"""
    from core.research.holdout import stamp_fitted_at

    if not isinstance(report, dict) or not report.get("success"):
        return report
    stamp_fitted_at(report)
    ts = report.get("fitted_at")
    if not ts:
        return report
    for key in ("tree_return_model", "return_model"):
        nested = report.get(key)
        if isinstance(nested, dict) and not nested.get("fitted_at"):
            nested["fitted_at"] = ts
    return report


def _stored_feature_z(
    rm: Dict[str, Any],
) -> Tuple[Optional[Dict[str, float]], Optional[Dict[str, float]]]:
    """旧模型冻住的训练窗 μ/σ。截面模型返回空：输入已是当天 z，或由池子 rescore。"""
    from core.research.feature_standardize import (
        is_cross_section_zscore,
        resolve_feature_zscore,
    )

    if is_cross_section_zscore(rm):
        return None, None
    candidates = [rm]
    inner = rm.get("return_model") if isinstance(rm.get("return_model"), dict) else None
    if inner is not None:
        candidates.append(inner)
    for src in candidates:
        if is_cross_section_zscore(src):
            return None, None
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
    from core.research.horizon_lgb import _apply_feature_z, _design_matrix
    from core.signal.factors.z_th import feature_get as z_th_feature_get

    src = features or {}
    row = {k: z_th_feature_get(src, k) for k in feature_names}
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
    from core.research.horizon_lgb import lgb_best_iteration_collapsed

    rm = _return_model_doc(model_doc)
    hp = rm.get("hyperparams") if isinstance(rm.get("hyperparams"), dict) else {}
    return lgb_best_iteration_collapsed(hp.get("best_iteration"))


def resolve_tree_engine(return_model: Optional[Dict[str, Any]]) -> Optional[str]:
    """树引擎名（仅 lightgbm）。

    包级择优 ``backend=tree`` ≠ 引擎名。晋级曾把 ``return_model.backend`` 误写成
    ``tree``，推理若只认 ``lightgbm`` 会整头空分；此处把 tree/lgb/空+booster 归一。
    """
    rm = return_model if isinstance(return_model, dict) else {}
    eng = str(rm.get("backend") or "").strip().lower()
    if eng in ("lightgbm", "lgb"):
        return "lightgbm"
    if eng in ("tree", "") and isinstance(rm.get("booster"), dict):
        return "lightgbm"
    if eng == "tree":
        return "lightgbm"
    return None


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
    eng = resolve_tree_engine(rm)
    try:
        if eng != "lightgbm":
            return None
        from core.research.horizon_lgb import _predict_lightgbm

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


def _horizon_gain_pct_by_key(*docs: Any) -> Dict[str, float]:
    """模型分裂增益份额 → tip 用百分数（与 oo_rank / 枢纽因子表同口径）。

    优先 ``feature_importance[].share``（0–1），否则 ``gain``；≤1.5 视为份额再 ×100。
    """
    out: Dict[str, float] = {}
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        candidates: List[Any] = [doc]
        rm = doc.get("return_model")
        if isinstance(rm, dict):
            candidates.append(rm)
        be = doc.get("backends") if isinstance(doc.get("backends"), dict) else {}
        tree = be.get("tree") if isinstance(be, dict) else None
        if isinstance(tree, dict):
            candidates.append(tree)
            trm = tree.get("return_model")
            if isinstance(trm, dict):
                candidates.append(trm)
        for src in candidates:
            if not isinstance(src, dict):
                continue
            fi = src.get("feature_importance")
            if not isinstance(fi, list):
                continue
            for g in fi:
                if not isinstance(g, dict):
                    continue
                k = str(g.get("key") or g.get("name") or "").strip()
                if not k or k in out:
                    continue
                share = g.get("share")
                if share is None:
                    share = g.get("gain")
                try:
                    fv = float(share)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(fv) or fv <= 0:
                    continue
                out[k] = fv * 100.0 if fv <= 1.5 else fv
    return out


def _stamp_gain_on_terms(
    terms: Optional[List[Dict[str, Any]]],
    *docs: Any,
) -> None:
    if not isinstance(terms, list) or not terms:
        return
    gains = _horizon_gain_pct_by_key(*docs)
    if not gains:
        return
    for t in terms:
        if not isinstance(t, dict):
            continue
        k = str(t.get("key") or "").strip()
        if not k or t.get("gain") is not None:
            continue
        g = gains.get(k)
        if g is not None:
            t["gain"] = round(float(g), 2)


def explain_tree_return(
    features: Optional[Dict[str, Any]],
    return_model: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """树预测的贡献拆解。偏置 + 各因子贡献 = total，与 ``predict_tree_return`` 同口径。

    LightGBM 用 pred_contrib；有 ``feature_importance`` 时挂 gain% 便于 tip 回溯。
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
    eng = resolve_tree_engine(rm)
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
    _stamp_gain_on_terms(terms, rm, return_model)
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
    """tip 用的树组成。合计对齐 ``y_hat``（表列分数），缺拆解时仍写出合计。

    附带 LightGBM gain%（来自启用套 / return_model 的 feature_importance）。
    """
    rm = _return_model_doc(model_doc)
    try:
        expl = explain_tree_return(features, model_doc if isinstance(model_doc, dict) else rm)
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
    terms = out.get("terms")
    if isinstance(terms, list):
        out["terms"] = [dict(t) for t in terms if isinstance(t, dict)]
        _stamp_gain_on_terms(out["terms"], model_doc, rm)
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
    from core.research.holdout import model_fit_id, stamp_fitted_at
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
    stamp_fitted_at(report)
    fitted_at = model_fit_id(report)
    doc = {
        "success": True,
        "fitted_at": fitted_at,
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


# ---------------------------------------------------------------------------
# 泛化：tree last report 路径 / 读写（替代 tXX_tree.py 的 tXX_tree_last_report_path 等）
# ---------------------------------------------------------------------------


def tree_last_report_path(head: str) -> str:
    from core.paths import LIVE_DIR

    h = str(head or "").strip().lower()
    return os.path.join(LIVE_DIR, f"{h}_tree_last_report.json")


def save_tree_last_report(head: str, report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    from core.research.holdout import stamp_fitted_at

    stamp_fitted_at(report)
    path = tree_last_report_path(head)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)


def load_tree_last_report(head: str) -> Optional[Dict[str, Any]]:
    path = tree_last_report_path(head)
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except OSError:
        return None
    except Exception:  # noqa: BLE001
        logger.debug("load %s tree last_report failed", head, exc_info=True)
        return None
    if isinstance(doc, dict) and doc.get("success"):
        return doc
    return None


def predict_tree_from_features(
    head: str,
    features: Dict[str, Optional[float]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    """按 head 加载树模型并输出 p_up∈(0,1)。缺模型返回 None。"""
    from core.signal.factors.z_th import stamp_z_th_aliases

    doc = model_doc if model_doc is not None else load_tree_model_doc(head)
    if not doc:
        return None
    rm = doc.get("return_model") if isinstance(doc.get("return_model"), dict) else doc
    feats = dict(features or {})
    stamp_z_th_aliases(feats)
    return predict_tree_p_up(feats, rm)


# ---------------------------------------------------------------------------
# 泛化：fit_horizon_tree_report（替代 t30/t45/t60/t75/t90_tree.py 的 fit_tXX_tree_report）
# ---------------------------------------------------------------------------


def _horizon_tree_config(head: str) -> Dict[str, Any]:
    """按 head 解析树头训练所需的 horizon 特定配置。"""
    from core.research.horizon_ridge import _horizon_ridge_config

    h = str(head or "").strip().lower()
    if h not in HORIZON_TREE_HEADS:
        raise ValueError(f"unsupported horizon tree head: {head!r}")
    ridge_cfg = _horizon_ridge_config(h)
    num = ridge_cfg["num"]
    z_features = ridge_cfg["z_features"]
    relabel_fn = ridge_cfg["relabel_fn"]
    train_tau_grid = ridge_cfg["train_tau_grid"]
    offsets = ridge_cfg["offsets"]
    return {
        "head": h,
        "num": num,
        "z_features": tuple(z_features),
        "relabel_fn": relabel_fn,
        "train_tau_grid": tuple(train_tau_grid),
        "schema": f"{h}_tree_shadow_v5",
        "head_label": f"y_{h}_tree",
        "target": f"price_tau_plus_{num}",
        "offsets": tuple(offsets),
    }


def fit_horizon_tree_report(
    head: str,
    stock_bars: Sequence[Dict[str, Any]],
    *,
    ridge_lambda: float = 1.0,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    theme_boost: float = 1.5,
    holdout_trading_days: int = 20,
    use_theme_weights: bool = True,
    tau_hm: str = "10:30",
    tau_grid: Optional[Sequence[str]] = None,
    backend: Optional[str] = None,
    n_estimators: int = None,  # type: ignore[assignment]
    max_depth: int = None,  # type: ignore[assignment]
    learning_rate: float = None,  # type: ignore[assignment]
    subsample: float = None,  # type: ignore[assignment]
    enriched_panels: Optional[Sequence[Dict[str, Any]]] = None,
    compact_panel: Optional[Dict[str, Any]] = None,
    calendar_dates: Optional[Sequence[str]] = None,
    skip_ridge_baseline: bool = False,
    num_threads: Optional[int] = None,
) -> Dict[str, Any]:
    """按 head 拟合 horizon 树头 + Ridge OOS 对照。不写 live / 研究套模型。

    ``enriched_panels``：双训预建面板（入模前折 float64）。
    ``compact_panel``：已折好的紧凑矩阵（双训共用，优先）。
    ``skip_ridge_baseline``：双训已训过 Ridge 时跳过树内重复 Ridge OOS。
    ``num_threads``：LightGBM 线程；默认 ``resolve_lgb_num_threads()``。
    """
    import time

    import numpy as np

    from core.research.horizon_compact import compact_subset
    from core.research.horizon_ridge import prepare_horizon_compact
    from core.research.tc_ridge import _theme_counts
    from core.signal.factors.z_th import Z_TH_FIT_DROP_ALIASES
    from core.research.horizon_lgb import (
        DEFAULT_LEARNING_RATE,
        DEFAULT_MAX_DEPTH,
        DEFAULT_N_ESTIMATORS,
        DEFAULT_SUBSAMPLE,
        _delta_oos,
        _fit_lightgbm,
        _fit_ridge_oos,
        _importance_rows,
        _horizon_lgb_hyper,
        _lgb_train_kwargs,
        _oos_pack,
        _predict_lightgbm,
        prepare_lgb_features_matrix,
        resolve_lgb_num_threads,
        resolve_tree_backend,
    )

    if n_estimators is None:
        n_estimators = DEFAULT_N_ESTIMATORS
    if max_depth is None:
        max_depth = DEFAULT_MAX_DEPTH
    if learning_rate is None:
        learning_rate = DEFAULT_LEARNING_RATE
    if subsample is None:
        subsample = DEFAULT_SUBSAMPLE

    cfg = _horizon_tree_config(head)
    h = cfg["head"]
    num = cfg["num"]
    z_features = cfg["z_features"]
    schema = cfg["schema"]
    head_label = cfg["head_label"]
    target = cfg["target"]
    offsets = cfg["offsets"]
    tree_z_features = tuple(z_features)  # 与 Ridge 同 X（alpha_τh）

    from core.research.horizon_prob import binary_labels
    from core.research.tau_panel import theme_sample_weights

    t0 = time.perf_counter()
    t_panel0 = time.perf_counter()
    prepared = prepare_horizon_compact(
        head,
        stock_bars,
        enriched_panels=enriched_panels,
        compact_panel=compact_panel,
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        tau_hm=tau_hm,
        tau_grid=tau_grid,
    )
    compact = prepared["compact"]
    live_hm = str(prepared["live_hm"])
    grid = list(prepared["tau_grid"] or [])

    ys = list(compact.get("ys") or [])
    dates = list(compact.get("dates") or [])
    metas = list(compact.get("metas") or [])
    stock_count = int(compact.get("stock_count") or 0)
    panel_s = round(time.perf_counter() - t_panel0, 2)
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"树样本不足 n={len(ys)}（需≥20 且需 τ⊕{offsets[0]}/{offsets[1]}/{offsets[2]} 三根均价）",
            "task": f"{h}_tree",
            "head": head_label,
            "sample_count": len(ys),
            "stock_count": stock_count,
            "tau": live_hm,
            "tau_grid": list(grid),
            "schema": schema,
            "live_hook": False,
            "backtest_hook": False,
        }

    from core.research.holdout import (
        DEFAULT_HOLDOUT_TRADING_DAYS,
        attach_holdout_meta,
        calendar_dates_from_stock_bars,
        resolve_ridge_split,
    )

    hold_n = int(holdout_trading_days or DEFAULT_HOLDOUT_TRADING_DAYS)
    cal = (
        list(calendar_dates)
        if calendar_dates is not None
        else calendar_dates_from_stock_bars(stock_bars)
    )
    train_idx, test_idx, split_meta = resolve_ridge_split(
        dates,
        holdout_trading_days=hold_n,
        calendar_dates=cal,
    )
    feat_names = [k for k in tree_z_features if k not in Z_TH_FIT_DROP_ALIASES]
    ridge_feat_names = [k for k in z_features if k not in Z_TH_FIT_DROP_ALIASES]
    ys_tr = [ys[i] for i in train_idx]
    ys_te = [ys[i] for i in test_idx]
    metas_tr = [metas[i] for i in train_idx]
    metas_te = [metas[i] for i in test_idx]
    dates_tr = [dates[i] for i in train_idx]
    dates_te = [dates[i] for i in test_idx]
    l2_rows_dropped: Dict[str, Any] = {"train": 0, "test": 0, "miss": []}
    try:
        from core.research.horizon_compact import (
            cs_groups_from_dates_metas,
            summarize_cs_groups,
        )

        groups_tr = cs_groups_from_dates_metas(dates_tr, metas_tr)
        groups_te = cs_groups_from_dates_metas(dates_te, metas_te)
        from core.research.materialize.cube_z import filter_rows_to_day_cube

        n_tr, n_te = len(groups_tr), len(groups_te)
        groups_tr, kept_tr, miss_tr = filter_rows_to_day_cube(
            groups_tr,
            [list(train_idx), ys_tr, metas_tr, dates_tr],
            clock=live_hm,
            require_any=True,
        )
        train_idx, ys_tr, metas_tr, dates_tr = kept_tr
        groups_te, kept_te, miss_te = filter_rows_to_day_cube(
            groups_te,
            [list(test_idx), ys_te, metas_te, dates_te],
            clock=live_hm,
            require_any=False,
        )
        test_idx, ys_te, metas_te, dates_te = kept_te
        miss_keys: List[str] = []
        seen_miss = set()
        for key in list(miss_tr) + list(miss_te):
            if key in seen_miss:
                continue
            seen_miss.add(key)
            miss_keys.append(key)
        l2_rows_dropped = {
            "train": n_tr - len(groups_tr),
            "test": n_te - len(groups_te),
            "miss": miss_keys[:8],
        }
    except Exception as exc:  # noqa: BLE001
        from core.research.materialize.consumer import MaterializeRequired

        if isinstance(exc, MaterializeRequired):
            raise
        # 禁止退回纯日期混钟（与 live PIT 不一致，且会假抬 Holdout）
        logger.exception("%s CS groups failed; refuse day-pooled fallback", h)
        return {
            "success": False,
            "error": f"CS 分组失败（拒绝按日混钟回退）: {exc}",
            "error_code": "cs_group_failed",
            "task": f"{h}_tree",
            "head": head_label,
            "sample_count": len(ys),
            "schema": schema,
            "live_hook": False,
            "backtest_hook": False,
        }
    if len(ys_tr) < 16 or len(ys_te) < 8:
        return {
            "success": False,
            "error": f"Holdout 切分后样本不足 训={len(ys_tr)} 测={len(ys_te)}",
            "task": f"{h}_tree",
            "head": head_label,
            "sample_count": len(ys),
            "schema": schema,
            "live_hook": False,
            "backtest_hook": False,
        }

    x_tr, x_te, means, z_hyper = prepare_lgb_features_matrix(
        compact.get("X"),
        compact.get("names") or feat_names,
        feat_names,
        train_idx,
        test_idx,
        feature_zscore=True,
        train_dates=groups_tr,
        test_dates=groups_te,
    )
    y_tr = np.asarray(binary_labels(ys_tr), dtype=np.float64)
    w_tr = np.asarray(
        theme_sample_weights(metas_tr, theme_boost=theme_boost)
        if use_theme_weights
        else [1.0] * len(ys_tr),
        dtype=np.float64,
    )
    if w_tr.shape != y_tr.shape:
        w_tr = np.ones_like(y_tr)

    engine = resolve_tree_backend(backend)
    lgb_threads = resolve_lgb_num_threads(num_threads)
    hyper = {
        **_horizon_lgb_hyper(),
        "n_estimators": int(n_estimators),
        "max_depth": int(max_depth),
        "learning_rate": float(learning_rate),
        "subsample": float(subsample),
        "objective": "binary",
        "num_threads": int(lgb_threads),
        **z_hyper,
    }
    gain = np.zeros(len(feat_names), dtype=np.float64)
    boost_preds: List[Optional[float]]
    tree_obj: Any = None
    t_tree0 = time.perf_counter()
    model, gain = _fit_lightgbm(
        x_tr,
        y_tr,
        w_tr,
        n_estimators=int(n_estimators),
        max_depth=int(max_depth),
        learning_rate=float(learning_rate),
        subsample=float(subsample),
        objective="binary",
        num_threads=lgb_threads,
        **_lgb_train_kwargs(hyper),
    )
    tree_obj = model
    raw_pred = np.clip(_predict_lightgbm(model, x_te), 1e-6, 1.0 - 1e-6)
    boost_preds = [float(v) if math.isfinite(float(v)) else None for v in raw_pred]
    tree_s = round(time.perf_counter() - t_tree0, 2)
    return_model = pack_tree_return_model(
        head=h,
        backend=engine,
        feature_names=feat_names,
        impute_means=means,
        model_obj=tree_obj,
        schema=schema,
        hyperparams=hyper,
        target=target,
    )

    t_ridge0 = time.perf_counter()
    ridge_preds: List[Optional[float]] = []
    if skip_ridge_baseline:
        # 双训：Ridge 腿已产出 OOS，树内不再 compact_subset + 重训
        ridge_preds = [None] * len(ys_te)
    else:
        xs_tr, _, _, _ = compact_subset(compact, train_idx, names=ridge_feat_names)
        xs_te, _, _, _ = compact_subset(compact, test_idx, names=ridge_feat_names)
        _, ridge_preds = _fit_ridge_oos(
            xs_tr,
            ys_tr,
            xs_te,
            ys_te,
            metas_tr,
            ridge_feat_names,
            ridge_lambda=ridge_lambda,
            theme_boost=theme_boost,
            use_theme_weights=use_theme_weights,
            kind="prob",
            dates_tr=groups_tr,
            dates_te=groups_te,
        )
    ridge_s = round(time.perf_counter() - t_ridge0, 2)
    oos_boost = _oos_pack(boost_preds, ys_te, metas_te, use_minute=True, kind="prob")
    oos_ridge = (
        {}
        if skip_ridge_baseline
        else _oos_pack(ridge_preds, ys_te, metas_te, use_minute=True, kind="prob")
    )
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
            "tau": live_hm,
            "tau_grid": list(grid),
            "target": target,
            "l2_rows_dropped": l2_rows_dropped,
        }
    )
    if oos_ridge:
        oos_ridge["n_train"] = len(ys_tr)
        oos_ridge["n_test"] = len(ys_te)
        oos_ridge["holdout_trading_days"] = hold_n
        oos_ridge["target"] = target

    from core.signal.factors.alpha_th import alpha_th_report_meta

    n_ath = sum(1 for k in feat_names if str(k).startswith("raw_ath_"))
    pack_meta = alpha_th_report_meta(n_alpha_th_features=n_ath)
    if isinstance(return_model, dict):
        return_model = dict(return_model)
        return_model["schema"] = schema
        return_model.update(pack_meta)
        ys_spec = return_model.get("y_spec")
        if isinstance(ys_spec, dict):
            ys_spec = dict(ys_spec)
            ys_spec["feature_pack"] = pack_meta["feature_pack"]
            ys_spec["feature_pack_formula"] = pack_meta["feature_pack_formula"]
            return_model["y_spec"] = ys_spec

    cs_z_audit = {
        "train": summarize_cs_groups(groups_tr),
        "test": summarize_cs_groups(groups_te),
    }
    report: Dict[str, Any] = {
        "success": True,
        "task": f"{h}_tree",
        "head": head_label,
        "schema": schema,
        "head_kind": "prob",
        "stock_count": stock_count,
        "sample_count": len(ys),
        "tau": live_hm,
        "tau_grid": list(grid),
        "target": target,
        "backend": engine,
        "cs_z": cs_z_audit,
        **pack_meta,
        "hyperparams": hyper,
        "timing": {
            "panel_s": panel_s,
            "tree_s": tree_s,
            "ridge_s": ridge_s,
            "fit_s": round(time.perf_counter() - t0, 2),
        },
        "feature_names": list(feat_names),
        "ridge_feature_names": list(ridge_feat_names),
        "feature_importance": _importance_rows(feat_names, gain),
        "tree_return_model": return_model,
        "oos": oos_boost,
        "ridge_oos": oos_ridge or None,
        "delta_vs_ridge": (
            None if skip_ridge_baseline else _delta_oos(oos_boost, oos_ridge)
        ),
        "skip_ridge_baseline": bool(skip_ridge_baseline),
        "live_hook": False,
        "backtest_hook": True,
        "persisted": {"success": False, "skipped": True, "reason": "fit_only"},
        "note": (
            f"ŷ_τ{num}_tree：X 与 Ridge 同包（alpha_τh）；"
            f"正式路径已并入 ŷ_τ{num} 主卡双训（Holdout AUC 择优）；"
            f"遗留 API 可设 horizon_prob_backend=tree 强制读 backends.tree。"
        ),
    }
    attach_holdout_meta(report, split_meta)
    stamp_tree_fitted_at(report)
    return report
