"""同一天的一批打分，按当天截面重算 ŷ。

单票 ``predict`` 看不到同伴，不能在那里做截面标准化。池子打完后用原始特征再算一次。
旧模型（训练窗 μ/σ）不改已写出的 ŷ。
"""

from __future__ import annotations

import logging
from typing import Collection, List, Optional, Sequence

logger = logging.getLogger(__name__)

UNSCORED_HEAD_KEYS = (
    "decision_score",
    "dual_score_fusion",
    "predicted_score",
    "predicted_score_blend",
    "predicted_score_blend_tau_cc",
    "predicted_score_blend_vs",
    "predicted_score_co",
    "predicted_score_eod",
    "predicted_score_eod_rem",
    "predicted_score_on",
    "predicted_score_oo",
    "predicted_score_oo_rank",
    "predicted_score_rem",
    "predicted_score_tau_cascade",
    "predicted_score_tau_delta",
    "predicted_score_τc",
    "predicted_score_tc_rank",
    "score",
    "score_model_role",
    "score_rem",
    "y_co",
    "y_co_source",
    "y_oo",
    "y_oo_fuse",
    "y_oo_rank",
    "y_oo_rank_hat",
    "y_oo_rank_n",
    "y_oo_rank_score",
    "y_tau_raw",
    "y_τc",
    "y_τc_ridge",
    "y_τc_source",
    "y_tc_rank",
    "y_tc_rank_hat",
    "y_tc_rank_n",
    "y_tc_rank_score",
)


def drop_unscored_heads(
    items: Sequence[dict],
    score_codes: Optional[Collection[str]],
) -> None:
    """分档外只留特征。ŷ、融合分和名次去掉，原始特征仍在，供当天截面 z 使用。"""
    from core.research.feature_standardize import normalize_score_codes

    allow = normalize_score_codes(score_codes)
    if allow is None:
        return
    for it in items or []:
        if not isinstance(it, dict):
            continue
        code = str(it.get("stock_code") or "").strip()
        if not code or code in allow:
            continue
        for key in list(it.keys()):
            if (
                key in UNSCORED_HEAD_KEYS
                or "formula" in key
                or key.startswith("factor_coefficients")
            ):
                it.pop(key, None)


def rescore_fitted_cross_section(
    items: Sequence[dict],
    *,
    score_codes: Optional[Collection[str]] = None,
) -> List[dict]:
    """就地刷新截面模型的 ŷ_oo / ŷ_τ / ŷ_co。返回原列表。

    ``score_codes`` 给定时，μ/σ 仍用整批原始特征，只给名单内的代码写 ŷ。
    """
    rows = [it for it in items if isinstance(it, dict)]
    if len(rows) < 2:
        return list(items)
    _rescore_oo_ridge(rows, score_codes=score_codes)
    _rescore_tau(rows, score_codes=score_codes)
    _rescore_co(rows, score_codes=score_codes)
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


def _rescore_oo_ridge(
    items: List[dict],
    *,
    score_codes: Optional[Collection[str]] = None,
) -> None:
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
    rescored = apply_predicted_scores(
        items, model, write_rank_score=False, score_codes=score_codes
    )
    oo_keys = (
        "predicted_score",
        "predicted_score_oo",
        "y_oo",
        "score_formula_terms",
        "formula_terms",
        "score_model_role",
    )
    # apply_predicted_scores 对档外已从副本清掉头字段；写回时 dst 也要清掉旧值。
    for src, dst in zip(rescored, items):
        for key in oo_keys:
            if key in src:
                dst[key] = src[key]
            else:
                dst.pop(key, None)


_TAU_HEAD_KEYS = (
    "y_τc",
    "predicted_score_τc",
    "y_τc_ridge",
    "y_τc_source",
    "formula_terms_tau",
    "score_formula_terms_tau",
    "factor_coefficients_tau",
    "y_tau_raw",
    "predicted_score_tau_cascade",
    "predicted_score_tau_delta",
    "predicted_score_rem",
    "score_rem",
)

_CO_HEAD_KEYS = (
    "y_co",
    "predicted_score_co",
    "y_co_source",
    "formula_terms_co",
    "score_formula_terms_co",
    "factor_coefficients_co",
    "predicted_score_on",
    "formula_terms_on",
    "score_formula_terms_on",
)


def _rescore_tau(
    items: List[dict],
    *,
    score_codes: Optional[Collection[str]] = None,
) -> None:
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
        from core.research.feature_standardize import (
            is_cross_section_zscore,
            normalize_score_codes,
        )
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
    allow = normalize_score_codes(score_codes)
    z_rows = _z_rows(raws, names)
    for slot, z_row, raw in zip(slots, z_rows, raws):
        item = items[slot]
        code = str(item.get("stock_code") or "").strip()
        if allow is not None and code not in allow:
            for key in _TAU_HEAD_KEYS:
                item.pop(key, None)
            continue
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


def _rescore_co(
    items: List[dict],
    *,
    score_codes: Optional[Collection[str]] = None,
) -> None:
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
        from core.research.feature_standardize import (
            is_cross_section_zscore,
            normalize_score_codes,
        )
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
    allow = normalize_score_codes(score_codes)
    z_rows = _z_rows(raws, names)
    for slot, z_row, raw in zip(slots, z_rows, raws):
        item = items[slot]
        code = str(item.get("stock_code") or "").strip()
        if allow is not None and code not in allow:
            for key in _CO_HEAD_KEYS:
                item.pop(key, None)
            continue
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
