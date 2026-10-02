"""调仓回测 ŷ_oo / ŷ_τc / ŷ_co 的树头开关。

与做 T 的 ``horizon_prob_backend`` 分开：这里只换融合三头的回归树。
默认 ridge。选 tree 时读 ``oo_tree_model.json`` / ``tc_tree_model.json`` /
``co_tree_model.json``；缺文件的头回退 Ridge。不进交易执行。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

logger = logging.getLogger(__name__)

REBALANCE_SCORE_BACKEND_RIDGE = "ridge"
REBALANCE_SCORE_BACKEND_TREE = "tree"

_REBALANCE_SCORE_BACKEND: ContextVar[str] = ContextVar(
    "rebalance_score_backend", default=REBALANCE_SCORE_BACKEND_RIDGE
)


def normalize_rebalance_score_backend(raw: Any = None) -> str:
    s = str(raw or "").strip().lower()
    if s in {"tree", "gbm", "lightgbm", "lgb", "boost", "shadow_tree"}:
        return REBALANCE_SCORE_BACKEND_TREE
    return REBALANCE_SCORE_BACKEND_RIDGE


def current_rebalance_score_backend() -> str:
    return normalize_rebalance_score_backend(_REBALANCE_SCORE_BACKEND.get())


@contextmanager
def rebalance_score_backend_context(backend: Any) -> Iterator[None]:
    token = _REBALANCE_SCORE_BACKEND.set(normalize_rebalance_score_backend(backend))
    try:
        yield
    finally:
        _REBALANCE_SCORE_BACKEND.reset(token)


def predict_return_head(
    features: Optional[Dict[str, Any]],
    *,
    load_tree: Callable[[], Optional[Dict[str, Any]]],
    predict_tree: Callable[..., Optional[float]],
    predict_ridge: Callable[..., Optional[float]],
    ridge_model: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[float], str]:
    """返回 (ŷ, source)。tree 且有模型时优先；否则 Ridge。"""
    feats = features if isinstance(features, dict) else {}
    if current_rebalance_score_backend() == REBALANCE_SCORE_BACKEND_TREE:
        try:
            tree_doc = load_tree()
        except Exception:  # noqa: BLE001
            logger.debug("load return tree model failed", exc_info=True)
            tree_doc = None
        if tree_doc is not None:
            try:
                y_hat = predict_tree(feats, model_doc=tree_doc)
            except Exception:  # noqa: BLE001
                logger.debug("predict return tree failed", exc_info=True)
                y_hat = None
            if y_hat is not None:
                return float(y_hat), REBALANCE_SCORE_BACKEND_TREE
    try:
        y_ridge = predict_ridge(feats, model_doc=ridge_model)
    except Exception:  # noqa: BLE001
        logger.debug("predict return ridge failed", exc_info=True)
        y_ridge = None
    if y_ridge is None:
        return None, ""
    return float(y_ridge), REBALANCE_SCORE_BACKEND_RIDGE


def finish_return_tree_persist(report: Dict[str, Any], saved: Dict[str, Any]) -> Dict[str, Any]:
    """拟合落盘后剥掉 booster，避免实验记录和「上次」接口带上整棵树。"""
    ok = bool((saved or {}).get("success"))
    report["persisted"] = {
        "success": ok,
        "path": (saved or {}).get("path"),
        "error": (saved or {}).get("error"),
        "skipped": not ok,
    }
    if (saved or {}).get("path"):
        report["model_path"] = saved["path"]
    report.pop("tree_return_model", None)
    report["backtest_hook"] = ok
    report["live_hook"] = False
    return report


def return_tree_model_state() -> Dict[str, str]:
    """三头可预测包是否在盘。缺的头回测时回退 Ridge。"""
    from core.research.co_tree import load_co_tree_model
    from core.research.oo_tree import load_oo_tree_model
    from core.research.tc_tree import load_tau_tree_model

    out: Dict[str, str] = {}
    for head, loader in (
        ("oo", load_oo_tree_model),
        ("tc", load_tau_tree_model),
        ("co", load_co_tree_model),
    ):
        try:
            doc = loader()
        except Exception:  # noqa: BLE001
            logger.debug("return tree state load failed head=%s", head, exc_info=True)
            doc = None
        out[head] = "loaded" if doc else "missing_fallback_ridge"
    return out


def loaded_return_tree_feature_names() -> list:
    """已落盘树头的特征名并集，供回测抬 Alpha158 窗。"""
    from core.research.co_tree import load_co_tree_model
    from core.research.oo_tree import load_oo_tree_model
    from core.research.tc_tree import load_tau_tree_model

    names: list = []
    seen = set()
    for loader in (load_oo_tree_model, load_tau_tree_model, load_co_tree_model):
        try:
            doc = loader() or {}
        except Exception:  # noqa: BLE001
            logger.debug("return tree feature names failed", exc_info=True)
            continue
        rm = doc.get("return_model") if isinstance(doc.get("return_model"), dict) else {}
        raw = rm.get("feature_names") or doc.get("feature_names") or []
        for n in raw:
            k = str(n or "").strip()
            if k and k not in seen:
                seen.add(k)
                names.append(k)
    return names
