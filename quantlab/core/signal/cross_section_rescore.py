"""同一天的一批打分，按当天截面重算 ŷ。

单票 ``predict`` 看不到同伴，不能在那里做截面标准化。池子打完后用原始特征再算一次。
旧模型（训练窗 μ/σ）不改已写出的 ŷ。
"""

from __future__ import annotations

import logging
from typing import Any, Collection, List, Optional, Sequence

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


def _day_cube_usable(day_cube: Any) -> bool:
    """DayCube 宇宙 ≥2 即可对单票做 z（μ/σ 已固化，不要求本批凑同伴）。"""
    if day_cube is None:
        return False
    try:
        codes = getattr(day_cube, "codes", None) or []
        return len(codes) >= 2
    except Exception:  # noqa: BLE001
        return False


def rescore_fitted_cross_section(
    items: Sequence[dict],
    *,
    score_codes: Optional[Collection[str]] = None,
    day_cube: Any = None,
) -> List[dict]:
    """就地刷新截面模型的 ŷ_oo / ŷ_τ / ŷ_co / ŷ_τ*。返回原列表。

    ``score_codes`` 给定时，μ/σ 仍用整批原始特征，只给名单内的代码写 ŷ。
    ``day_cube`` 给定时，τ/co 用 cube 预计算 μ/σ 做 z（单遍，避免重复扫池）；
    此时允许单票 items（μ/σ 只信 cube）。
    """
    rows = [it for it in items if isinstance(it, dict)]
    if not rows:
        return list(items)
    if len(rows) < 2 and not _day_cube_usable(day_cube):
        return list(items)
    _rescore_oo_ridge(rows, score_codes=score_codes, day_cube=day_cube)
    _rescore_tau(rows, score_codes=score_codes, day_cube=day_cube)
    _rescore_co(rows, score_codes=score_codes, day_cube=day_cube)
    _rescore_horizons(rows, score_codes=score_codes, day_cube=day_cube)
    return list(items)


def _z_rows(
    raws: Sequence[dict],
    names: Sequence[str],
    *,
    day_cube: Any = None,
    codes: Optional[Sequence[str]] = None,
) -> List[dict]:
    name_list = [str(n) for n in names]
    if day_cube is not None:
        try:
            from core.research.day_cube import apply_cube_z_to_dicts

            z_all = apply_cube_z_to_dicts(list(raws), list(codes or []), day_cube)
            out = [{k: row[k] for k in name_list if k in row} for row in z_all]
            if out:
                return out
        except Exception:  # noqa: BLE001
            logger.debug("cube z_rows fallback", exc_info=True)
    from core.research.feature_standardize import cross_section_zscore_dicts

    return cross_section_zscore_dicts(list(raws), name_list)


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
    day_cube: Any = None,
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
        items,
        model,
        write_rank_score=False,
        score_codes=score_codes,
        day_cube=day_cube,
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
    day_cube: Any = None,
) -> None:
    raws = []
    slots = []
    codes = []
    for i, it in enumerate(items):
        feats = it.get("features_tau")
        if isinstance(feats, dict) and feats:
            raws.append(dict(feats))
            slots.append(i)
            codes.append(str(it.get("stock_code") or "").strip())
    if not raws:
        return
    if len(raws) < 2 and not _day_cube_usable(day_cube):
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
    try:
        from core.signal.factors.z_tc import stamp_z_tc_aliases

        for r in raws:
            stamp_z_tc_aliases(r)
    except Exception:  # noqa: BLE001
        logger.debug("tau rescore stamp failed", exc_info=True)
    z_rows = _z_rows(raws, names, day_cube=day_cube, codes=codes)
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
            # tip 与 predict 同吃截面 z，避免 raw×β 把 YKLEN 等小尺度列挤出组成表
            explain_feats=z_row,
            as_of_tau=item.get("as_of_tau"),
            rem_model_doc=ridge_doc,
            fuse_intraday=fuse,
            y_source="ridge",
        )


def _rescore_co(
    items: List[dict],
    *,
    score_codes: Optional[Collection[str]] = None,
    day_cube: Any = None,
) -> None:
    raws = []
    slots = []
    codes = []
    for i, it in enumerate(items):
        feats = it.get("features_co") if isinstance(it.get("features_co"), dict) else it.get("features_on")
        if isinstance(feats, dict) and feats:
            raws.append(dict(feats))
            slots.append(i)
            codes.append(str(it.get("stock_code") or "").strip())
    if not raws:
        return
    if len(raws) < 2 and not _day_cube_usable(day_cube):
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
    try:
        from core.signal.factors.z_co import stamp_z_co_aliases

        for r in raws:
            stamp_z_co_aliases(r)
    except Exception:  # noqa: BLE001
        logger.debug("co rescore stamp failed", exc_info=True)
    z_rows = _z_rows(raws, names, day_cube=day_cube, codes=codes)
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
            explain_feats=z_row,
            co_model_doc=ridge_doc if isinstance(ridge_doc, dict) else None,
            source="ridge",
        )


_HORIZON_HEADS = ("t30", "t45", "t60", "t75", "t90")

_HORIZON_HEAD_KEYS = (
    "predicted_score_t30",
    "y_t30_hat",
    "y_t30",
    "y_τ30",
    "y_τ30_source",
    "y_t30_source",
    "formula_terms_t30",
    "score_formula_terms_t30",
    "predicted_score_t45",
    "y_t45_hat",
    "y_t45",
    "y_τ45",
    "y_τ45_source",
    "y_t45_source",
    "formula_terms_t45",
    "score_formula_terms_t45",
    "predicted_score_t60",
    "y_t60_hat",
    "y_t60",
    "y_τ60",
    "y_τ60_source",
    "y_t60_source",
    "formula_terms_t60",
    "score_formula_terms_t60",
    "predicted_score_t75",
    "y_t75_hat",
    "y_t75",
    "y_τ75",
    "y_τ75_source",
    "y_t75_source",
    "formula_terms_t75",
    "score_formula_terms_t75",
    "predicted_score_t90",
    "y_t90_hat",
    "y_t90",
    "y_τ90",
    "y_τ90_source",
    "y_t90_source",
    "formula_terms_t90",
    "score_formula_terms_t90",
)


def _rescore_horizons(
    items: List[dict],
    *,
    score_codes: Optional[Collection[str]] = None,
    day_cube: Any = None,
) -> None:
    """池级刷新 ŷ_τ30…90：截面模型用当日同伴 μ/σ 再预测。"""
    for head in _HORIZON_HEADS:
        _rescore_one_horizon(items, head, score_codes=score_codes, day_cube=day_cube)


def _finite_name_count(rows: Sequence[dict], name: str) -> int:
    import math

    key = str(name or "").strip()
    n = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        v = row.get(key)
        if v is None or v == "":
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fv):
            n += 1
    return n


def _rescore_one_horizon(
    items: List[dict],
    head: str,
    *,
    score_codes: Optional[Collection[str]] = None,
    day_cube: Any = None,
) -> None:
    raws = []
    slots = []
    codes = []
    for i, it in enumerate(items):
        feats = it.get("features_tau")
        if isinstance(feats, dict) and feats:
            raws.append(dict(feats))
            slots.append(i)
            codes.append(str(it.get("stock_code") or "").strip())
    if not raws:
        return
    if len(raws) < 2 and not _day_cube_usable(day_cube):
        return
    try:
        from core.research.feature_standardize import (
            is_cross_section_zscore,
            normalize_score_codes,
        )
        from core.research.horizon_ridge import (
            _doc_backend,
            explain_ridge_prediction,
            load_ridge_model,
            predict_ridge_from_features,
            write_y_hat,
        )
        from core.research.horizon_tree import predict_tree_from_features
        from core.signal.factors.z_th import stamp_z_th_aliases
    except Exception:  # noqa: BLE001
        logger.debug("horizon rescore import failed head=%s", head, exc_info=True)
        return
    try:
        doc = load_ridge_model(head)
    except Exception:  # noqa: BLE001
        doc = None
    if not doc or not is_cross_section_zscore(doc):
        return
    rm = doc.get("return_model") if isinstance(doc, dict) else {}
    names = _active_names(rm if isinstance(rm, dict) else {})
    if not names:
        names = _active_names(doc if isinstance(doc, dict) else {})
    if not names:
        return
    allow = normalize_score_codes(score_codes)
    try:
        for r in raws:
            stamp_z_th_aliases(r)
    except Exception:  # noqa: BLE001
        logger.debug("horizon rescore stamp failed head=%s", head, exc_info=True)
    from core.research.horizon_ridge import clear_y_hat
    from core.t0.score_policy import _cube_covers_horizon_names, _finalize_cs_row

    cube_ok = day_cube is not None and _cube_covers_horizon_names(day_cube, names)
    if not cube_ok and len(raws) < 2:
        for slot in slots:
            clear_y_hat(head, items[slot])
        return
    if cube_ok:
        z_rows = _z_rows(raws, names, day_cube=day_cube, codes=codes)
        covered = list(names)
    else:
        covered = [n for n in names if _finite_name_count(raws, n) >= 2]
        z_rows = _z_rows(raws, covered or list(names), day_cube=None, codes=codes)
    be = _doc_backend(doc)
    for slot, z_row in zip(slots, z_rows):
        item = items[slot]
        code = str(item.get("stock_code") or "").strip()
        if allow is not None and code not in allow:
            continue
        final = _finalize_cs_row(z_row if isinstance(z_row, dict) else {}, names, covered)
        if final is None:
            clear_y_hat(head, item)
            continue
        try:
            if be == "tree":
                yhat = predict_tree_from_features(head, final, model_doc=doc)
            else:
                yhat = predict_ridge_from_features(head, final, model_doc=doc)
        except Exception:  # noqa: BLE001
            logger.debug("horizon rescore predict failed head=%s", head, exc_info=True)
            clear_y_hat(head, item)
            continue
        if yhat is None:
            clear_y_hat(head, item)
            continue
        write_y_hat(head, item, float(yhat))
        item[f"y_τ{head[1:]}_source" if head.startswith("t") else f"y_{head}_source"] = be
        item[f"y_{head}_source"] = be
        try:
            expl = explain_ridge_prediction(head, final, model_doc=doc)
        except Exception:  # noqa: BLE001
            expl = None
        if isinstance(expl, dict):
            item[f"formula_terms_{head}"] = expl
            item[f"score_formula_terms_{head}"] = expl
