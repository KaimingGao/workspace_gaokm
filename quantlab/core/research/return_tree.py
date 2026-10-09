"""调仓 / 做 T 的 ŷ_oo / ŷ_τc / ŷ_co 树头开关。

``ridge``：线性 Ridge（默认）。
``tree``：三头读已落盘树，缺文件回退 Ridge。
买序一律走融合 ranking。ŷ_oo_rank 不进本开关（不改 ranking 分数；可选 oo_rank_max 入场闸）。
``horizon_prob_backend=tree`` 时 ŷ_τc 与 horizon 头同开。
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
    if s in {"tree", "lightgbm", "lgb"}:
        return REBALANCE_SCORE_BACKEND_TREE
    return REBALANCE_SCORE_BACKEND_RIDGE


def current_rebalance_score_backend() -> str:
    return normalize_rebalance_score_backend(_REBALANCE_SCORE_BACKEND.get())


def tree_scores_requested() -> bool:
    """调仓 Tree，或做 T ``horizon_prob_backend=tree``（ŷ_τc 与 horizon 头同开）。"""
    if current_rebalance_score_backend() == REBALANCE_SCORE_BACKEND_TREE:
        return True
    try:
        from core.research.horizon_tree import (
            HORIZON_PROB_BACKEND_TREE,
            current_horizon_prob_backend,
        )

        return current_horizon_prob_backend() == HORIZON_PROB_BACKEND_TREE
    except Exception:  # noqa: BLE001
        logger.debug("horizon tree backend probe failed", exc_info=True)
        return False


def tree_main_heads_usable() -> bool:
    """ŷ_oo / ŷ_τc 落盘树可用（非缺、非 stump）。"""
    st = return_tree_model_state()
    return st.get("oo") == "loaded" and st.get("tc") == "loaded"


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
    if tree_scores_requested():
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


def stamp_tree_formula(
    item: dict,
    features: Optional[Dict[str, Any]],
    model_doc: Optional[Dict[str, Any]],
    *,
    y_hat: Optional[float],
    keys: tuple,
) -> None:
    """用树贡献拆解盖掉 Ridge 组成，tip 合计对齐表列。"""
    from core.research.horizon_tree import tree_tip_formula

    expl = tree_tip_formula(features, model_doc, y_hat=y_hat)
    for k in keys:
        item[k] = expl


def overlay_oo_tree_on_item(
    item: Optional[dict],
    *,
    window: Optional[list] = None,
    quote: Optional[dict] = None,
) -> Optional[dict]:
    """调仓 / 做 T 共用：Tree 时用 ŷ_oo_tree 同面板替换 ŷ_oo 与组成。"""
    if not isinstance(item, dict):
        return item
    if not tree_scores_requested():
        if item.get("y_oo") is not None:
            item.setdefault("y_oo_source", "ridge")
        return item
    from core.research.oo_tree import (
        load_oo_tree_model,
        oo_tree_features_from_window,
        predict_oo_tree_from_features,
    )

    tree_doc = None
    try:
        tree_doc = load_oo_tree_model()
    except Exception:  # noqa: BLE001
        logger.debug("load oo tree for overlay failed", exc_info=True)
    oo_feats = oo_tree_features_from_window(window or [], quote)
    if isinstance(oo_feats, dict) and oo_feats:
        item["_oo_tree_raw"] = dict(oo_feats)
    y_tree = None
    if tree_doc is not None:
        try:
            y_tree = predict_oo_tree_from_features(oo_feats, model_doc=tree_doc)
        except Exception:  # noqa: BLE001
            logger.debug("predict oo tree overlay failed", exc_info=True)
    if y_tree is not None:
        yv = float(y_tree)
        item["y_oo"] = yv
        item["predicted_score"] = yv
        item["predicted_score_oo"] = yv
        item["y_oo_source"] = "tree"
        stamp_tree_formula(
            item,
            oo_feats,
            tree_doc,
            y_hat=yv,
            keys=("score_formula_terms", "formula_terms"),
        )
    elif item.get("y_oo") is not None:
        item["y_oo_source"] = "ridge"
    return item


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
    from core.research.horizon_tree import tree_return_model_collapsed
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
        if not doc:
            out[head] = "missing_fallback_ridge"
            continue
        out[head] = (
            "collapsed_fallback_ridge"
            if tree_return_model_collapsed(doc)
            else "loaded"
        )
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
