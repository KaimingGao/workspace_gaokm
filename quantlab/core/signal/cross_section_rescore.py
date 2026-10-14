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
        from core.research.oo_ridge import load_scoring_return_model
        from core.signal.return_score import apply_predicted_scores
    except Exception:  # noqa: BLE001
        logger.debug("oo ridge rescore import failed", exc_info=True)
        return
    # 须跟打分上下文：研究回测不能写死 prefer_active 盖掉 Holdout β
    model, _meta = load_scoring_return_model()
    if model is None or not is_cross_section_zscore(model):
        return
    rescored = apply_predicted_scores(items, model, write_rank_score=False)
    for src, dst in zip(rescored, items):
        for key in (
            "predicted_score",
            "predicted_score_oo",
            "y_oo",
            "score_formula_terms",
            "formula_terms",
            "score_model_role",
        ):
            if key in src:
                dst[key] = src[key]


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
        from core.research.tc_ridge import load_tau_model, predict_tau_from_features
        from core.signal.dual_score.tau import apply_tau_score_fields
    except Exception:  # noqa: BLE001
        logger.debug("tau rescore import failed", exc_info=True)
        return
    ridge_doc = load_tau_model()
    if not is_cross_section_zscore(ridge_doc):
        return
    rm = ridge_doc.get("return_model") if isinstance(ridge_doc, dict) else {}
    names = _active_names(rm if isinstance(rm, dict) else {})
    if not names:
        names = list(raws[0].keys())
    z_rows = _z_rows(raws, names)
    for slot, z_row, raw in zip(slots, z_rows, raws):
        item = items[slot]
        try:
            yhat = predict_tau_from_features(z_row, model_doc=ridge_doc)
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
            y_source="ridge",
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
        from core.research.feature_standardize import is_cross_section_zscore
        from core.signal.dual_score.co import apply_co_score_fields
    except Exception:  # noqa: BLE001
        logger.debug("co rescore import failed", exc_info=True)
        return
    ridge_doc = load_co_model()
    if not is_cross_section_zscore(ridge_doc):
        return
    rm = ridge_doc.get("return_model") if isinstance(ridge_doc, dict) else {}
    names = _active_names(rm if isinstance(rm, dict) else {})
    if not names:
        names = list(raws[0].keys())
    z_rows = _z_rows(raws, names)
    for slot, z_row, raw in zip(slots, z_rows, raws):
        item = items[slot]
        try:
            yhat = predict_co_from_features(z_row, model_doc=ridge_doc)
        except Exception:  # noqa: BLE001
            logger.debug("co rescore predict failed", exc_info=True)
            continue
        apply_co_score_fields(
            item,
            co_yhat=yhat,
            feats=raw,
            co_model_doc=ridge_doc if isinstance(ridge_doc, dict) else None,
            source="ridge",
        )
