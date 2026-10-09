"""同一天的一批打分，按当天截面重算 ŷ。

单票 ``predict`` 看不到同伴，不能在那里做截面标准化。池子打完后用原始特征再算一次。
旧模型（训练窗 μ/σ）不改已写出的 ŷ。
"""

from __future__ import annotations

import logging
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)


def rescore_fitted_cross_section(items: Sequence[dict]) -> List[dict]:
    """就地刷新截面模型的 ŷ_oo / ŷ_τ / ŷ_co。返回原列表。"""
    rows = [it for it in items if isinstance(it, dict)]
    if len(rows) < 2:
        return list(items)
    _rescore_oo_ridge(rows)
    _rescore_oo_tree(rows)
    _rescore_tau(rows)
    _rescore_co(rows)
    return list(items)


def _z_rows(raws: Sequence[dict], names: Sequence[str]) -> List[dict]:
    from core.research.feature_standardize import cross_section_zscore_dicts

    return cross_section_zscore_dicts(list(raws), [str(n) for n in names])


def _active_names(model: Optional[dict]) -> List[str]:
    if not isinstance(model, dict):
        return []
    names = model.get("feature_names")
    if isinstance(names, list) and names:
        return [str(n) for n in names if n]
    active = model.get("active_features")
    if isinstance(active, list) and active:
        return [str(n) for n in active if n]
    coefs = model.get("coefficients") if isinstance(model.get("coefficients"), dict) else {}
    return [str(k) for k, v in coefs.items() if v is not None]


def _rescore_oo_ridge(items: List[dict]) -> None:
    try:
        from core.research.feature_standardize import is_cross_section_zscore
        from core.signal.return_score import apply_predicted_scores
        from core.signal.return_score_store import load_return_model
    except Exception:  # noqa: BLE001
        logger.debug("oo ridge rescore import failed", exc_info=True)
        return
    model, _meta = load_return_model(prefer_active=True)
    if model is None or not is_cross_section_zscore(model):
        return
    if any(str(it.get("y_oo_source") or "") == "tree" for it in items):
        return
    rescored = apply_predicted_scores(items, model, write_rank_score=False)
    for src, dst in zip(rescored, items):
        for key in ("predicted_score", "predicted_score_oo", "y_oo", "score_formula_terms", "formula_terms"):
            if key in src:
                dst[key] = src[key]


def _rescore_oo_tree(items: List[dict]) -> None:
    raws = []
    slots = []
    for i, it in enumerate(items):
        feats = it.get("_oo_tree_raw")
        if isinstance(feats, dict) and feats:
            raws.append(feats)
            slots.append(i)
    if len(raws) < 2:
        return
    try:
        from core.research.feature_standardize import is_cross_section_zscore
        from core.research.oo_tree import load_oo_tree_model, predict_oo_tree_from_features
        from core.research.return_tree import tree_scores_requested
    except Exception:  # noqa: BLE001
        logger.debug("oo tree rescore import failed", exc_info=True)
        return
    if not tree_scores_requested():
        return
    doc = load_oo_tree_model()
    if not is_cross_section_zscore(doc):
        return
    rm = doc.get("return_model") if isinstance(doc, dict) else {}
    names = _active_names(rm if isinstance(rm, dict) else {})
    if not names:
        names = list(raws[0].keys())
    z_rows = _z_rows(raws, names)
    for slot, z_row, raw in zip(slots, z_rows, raws):
        try:
            yhat = predict_oo_tree_from_features(z_row, model_doc=doc)
        except Exception:  # noqa: BLE001
            logger.debug("oo tree rescore predict failed", exc_info=True)
            continue
        if yhat is None:
            continue
        yv = float(yhat)
        item = items[slot]
        item["y_oo"] = yv
        item["predicted_score"] = yv
        item["predicted_score_oo"] = yv
        item["y_oo_source"] = "tree"
        item["_oo_tree_raw"] = raw


def _rescore_tau(items: List[dict]) -> None:
    raws = []
    slots = []
    for i, it in enumerate(items):
        feats = it.get("features_tau")
        if isinstance(feats, dict) and feats:
            raws.append(dict(feats))
            slots.append(i)
    if len(raws) < 2:
        return
    try:
        from core.research.feature_standardize import is_cross_section_zscore
        from core.research.return_tree import predict_return_head, tree_scores_requested
        from core.research.tc_ridge import load_tau_model, predict_tau_from_features
        from core.research.tc_tree import load_tau_tree_model, predict_tau_tree_from_features
        from core.signal.dual_score.tau import apply_tau_score_fields
    except Exception:  # noqa: BLE001
        logger.debug("tau rescore import failed", exc_info=True)
        return
    ridge_doc = load_tau_model()
    tree_doc = load_tau_tree_model() if tree_scores_requested() else None
    use_tree = tree_doc is not None and is_cross_section_zscore(tree_doc)
    use_ridge = (not use_tree) and is_cross_section_zscore(ridge_doc)
    if not use_tree and not use_ridge:
        return
    src_doc = tree_doc if use_tree else ridge_doc
    rm = src_doc.get("return_model") if isinstance(src_doc, dict) else {}
    names = _active_names(rm if isinstance(rm, dict) else {})
    if not names:
        names = list(raws[0].keys())
    z_rows = _z_rows(raws, names)
    for slot, z_row, raw in zip(slots, z_rows, raws):
        item = items[slot]
        try:
            if use_tree:
                yhat, y_src = predict_return_head(
                    z_row,
                    load_tree=load_tau_tree_model,
                    predict_tree=predict_tau_tree_from_features,
                    predict_ridge=predict_tau_from_features,
                    ridge_model=ridge_doc,
                )
            else:
                yhat = predict_tau_from_features(z_row, model_doc=ridge_doc)
                y_src = "ridge"
        except Exception:  # noqa: BLE001
            logger.debug("tau rescore predict failed", exc_info=True)
            continue
        fuse = str(item.get("dual_score_window") or "") != "eod_next"
        apply_tau_score_fields(
            item,
            rem_yhat=yhat,
            gap_pct=item.get("gap_pct"),
            feats=raw,
            as_of_tau=item.get("as_of_tau"),
            rem_model_doc=ridge_doc,
            fuse_intraday=fuse,
            y_source=y_src or item.get("y_τc_source"),
        )


def _rescore_co(items: List[dict]) -> None:
    raws = []
    slots = []
    for i, it in enumerate(items):
        feats = it.get("features_co") if isinstance(it.get("features_co"), dict) else it.get("features_on")
        if isinstance(feats, dict) and feats:
            raws.append(dict(feats))
            slots.append(i)
    if len(raws) < 2:
        return
    try:
        from core.research.co_ridge import load_co_model, predict_co_from_features
        from core.research.co_tree import load_co_tree_model, predict_co_tree_from_features
        from core.research.feature_standardize import is_cross_section_zscore
        from core.research.return_tree import predict_return_head, tree_scores_requested
        from core.signal.dual_score.co import apply_co_score_fields
    except Exception:  # noqa: BLE001
        logger.debug("co rescore import failed", exc_info=True)
        return
    ridge_doc = load_co_model()
    tree_doc = load_co_tree_model() if tree_scores_requested() else None
    use_tree = tree_doc is not None and is_cross_section_zscore(tree_doc)
    use_ridge = (not use_tree) and is_cross_section_zscore(ridge_doc)
    if not use_tree and not use_ridge:
        return
    src_doc = tree_doc if use_tree else ridge_doc
    rm = src_doc.get("return_model") if isinstance(src_doc, dict) else {}
    names = _active_names(rm if isinstance(rm, dict) else {})
    if not names:
        names = list(raws[0].keys())
    z_rows = _z_rows(raws, names)
    for slot, z_row, raw in zip(slots, z_rows, raws):
        item = items[slot]
        try:
            if use_tree:
                yhat, y_src = predict_return_head(
                    z_row,
                    load_tree=load_co_tree_model,
                    predict_tree=predict_co_tree_from_features,
                    predict_ridge=predict_co_from_features,
                    ridge_model=ridge_doc,
                )
            else:
                yhat = predict_co_from_features(z_row, model_doc=ridge_doc)
                y_src = "ridge"
        except Exception:  # noqa: BLE001
            logger.debug("co rescore predict failed", exc_info=True)
            continue
        apply_co_score_fields(
            item,
            co_yhat=yhat,
            feats=raw,
            co_model_doc=ridge_doc if isinstance(ridge_doc, dict) else None,
            source=str(y_src or ""),
        )
