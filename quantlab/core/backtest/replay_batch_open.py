"""09:30 冷启动开盘分。

按回测日期切片 L1，不把整段历史面板留在进程里。截面 z 用整池；
ŷ 与名次只写 ``score_codes``。排序头仍读已有 L2 的 μ/σ，不在这里重拟合。
切片不齐、排序立方读不到、或名次模型在但打不出来时，当日返回 None，调用方走逐日旧路径。
决策头是 ŷ_oo / ŷ_τc / 名次，并写四份悬停组成：ŷ_oo、ŷ_τc 的 Ridge β·z，
以及 oo_rank / τc_rank 的 Ridge β·z 或 LambdaRank pred_contrib。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

_HOLDER: Optional["_OpenSlice"] = None
_MIN_COVER = 0.5


class _RankModel:
    def __init__(
        self,
        model: Dict[str, Any],
        names: List[str],
        booster: Any,
        *,
        broken: bool = False,
    ) -> None:
        self.model = model
        self.names = names
        self.booster = booster
        self.broken = broken


class _OpenSlice:
    def __init__(
        self,
        codes: List[str],
        dates: List[str],
        oo: Dict[str, tuple],
        tc: Dict[str, tuple],
    ) -> None:
        self.codes = codes
        self.dates = list(dates)
        self.oo = oo
        self.tc = tc
        self.oo_model: Any = None
        self.tau_doc: Any = None
        self.oo_rank: Optional[_RankModel] = None
        self.tc_rank: Optional[_RankModel] = None
        self._models_ready = False
        self._notes: set = set()


def clear_open_slice() -> None:
    global _HOLDER
    _HOLDER = None


def prepare_replay_features(
    codes: Sequence[str],
    dates: Sequence[str],
    *,
    clock: str = "09:30",
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> None:
    """09:30 切回测日期的 L1。切不齐就退回整票预热。"""
    from core.research.lake_bridge import FAMILY_OO, FAMILY_TC, family_for_tc
    from core.research.lake_replay import clear_coverage_cache, prefetch_l1_coverage

    clear_coverage_cache()
    clear_open_slice()
    clock_hm = str(clock or "")[:5]
    code_list = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    day_list = _norm_dates(dates)
    if _cancelled(cancel_cb):
        return
    if clock_hm in ("", "09:30"):
        loaded = _load_slice(code_list, day_list, cancel_cb=cancel_cb)
        if _cancelled(cancel_cb):
            clear_open_slice()
            return
        if loaded is not None:
            logger.info(
                "replay open batch slice codes=%s days=%s oo=%s tc=%s",
                len(code_list),
                len(day_list),
                len(loaded.oo),
                len(loaded.tc),
            )
            return
        logger.info("replay open batch slice unavailable; prefetch full panels")
    fams = [FAMILY_OO, FAMILY_TC]
    if clock_hm not in ("", "09:30"):
        fams.append(family_for_tc(tau_hm=clock))
    prefetch_l1_coverage(code_list, families=fams)


def score_open_day_from_slice(
    day: str,
    *,
    date_maps: Optional[Dict[str, Dict[str, dict]]] = None,
    dates: Optional[Sequence[str]] = None,
    day_i: int = 0,
    score_codes: Optional[Sequence[str]] = None,
    tau_pool_day: Optional[Mapping[str, Any]] = None,
) -> Optional[List[dict]]:
    """切片已备好时写当日决策头。返回 None 表示走逐日旧路径。"""
    holder = _HOLDER
    day_s = str(day or "")[:10]
    if holder is None or len(day_s) < 10:
        return None
    if dates is not None and _norm_dates(dates) != holder.dates:
        _note_once(holder, "dates", "replay open batch slice dates differ; legacy score")
        return None
    if date_maps is not None:
        got = {str(c).strip() for c in date_maps if str(c or "").strip()}
        if got != set(holder.codes):
            _note_once(holder, "codes", "replay open batch slice codes differ; legacy score")
            return None
    if day_s not in holder.dates:
        return None
    from core.research.feature_standardize import normalize_score_codes

    allow = normalize_score_codes(score_codes)
    oo_rows = _day_rows(holder.oo, day_s)
    if len(oo_rows) < _min_rows(len(holder.codes)):
        logger.info("replay open batch thin day=%s oo=%s", day_s, len(oo_rows))
        return None
    if not _ensure_models(holder):
        return None
    tc_rows = _day_rows(holder.tc, day_s)
    codes = [c for c in holder.codes if c in oo_rows]
    prev = ""
    if dates and 0 < int(day_i) < len(dates):
        prev = str(dates[int(day_i) - 1])[:10]
    maps = date_maps or {}
    quotes = {code: _quote(code, day_s, prev, maps) for code in codes}
    trigger = _gap_trigger()
    oo_raw = [_stamped(oo_rows.get(c), _stamp_oo) for c in codes]
    tc_raw = [
        _stamped_tau(
            tc_rows.get(code),
            quote=quotes.get(code),
            pool=tau_pool_day,
            code=code,
            day=day_s,
            trigger=trigger,
        )
        for code in codes
    ]
    y_oo, oo_terms = _ridge_pack(
        codes,
        oo_raw,
        holder.oo_model,
        allow,
        impute_all_missing=False,
        getter=_oo_get,
        head="eod",
    )
    y_tc, tc_terms = _ridge_pack(
        codes,
        tc_raw,
        holder.tau_doc,
        allow,
        impute_all_missing=True,
        getter=_tc_get,
        head="tau",
    )
    if y_oo is None or y_tc is None:
        _note_once(holder, "ridge", "replay open batch ridge spec missing; legacy score")
        return None

    oo_status, oo_ranks, _oo_z, _oo_hats = _rank_pass(
        day_s, codes, oo_raw, holder.oo_rank, allow, getter=_oo_get
    )
    tc_status, tc_ranks, tc_z, tc_hats = _rank_pass(
        day_s, codes, tc_raw, holder.tc_rank, allow, getter=_tc_get
    )
    if oo_status == "miss" or tc_status == "miss":
        _note_once(holder, "rank", "replay open batch rank unavailable; legacy score")
        return None

    items: List[dict] = []
    for code in codes:
        items.append(
            _decision_item(
                code,
                day_s,
                quote=quotes.get(code) or {},
                y_oo=y_oo.get(code),
                y_tc=y_tc.get(code),
            )
        )
    _stamp_oo_ranks(items, oo_ranks)
    _stamp_tc_ranks(items, tc_ranks)
    _apply_formula_map(items, oo_terms, ("formula_terms", "score_formula_terms"))
    _apply_formula_map(
        items, tc_terms, ("formula_terms_tau", "score_formula_terms_tau")
    )
    _stamp_oo_rank_formula(items, codes, _oo_z, _oo_hats, holder.oo_rank, oo_ranks)
    _stamp_tc_rank_formula(items, codes, tc_z, tc_hats, holder.tc_rank, tc_ranks)
    return items


def cross_section_ridge(
    rows: Sequence[Mapping[str, Any]],
    names: Sequence[str],
    coefs: Mapping[str, float],
    intercept: float,
    *,
    impute_all_missing: bool = True,
    getter: Optional[Callable[[Mapping[str, Any], str], Any]] = None,
) -> List[Optional[float]]:
    """整批总体 z 后点乘。缺特征按 0（截面均值）计入；``impute_all_missing=False`` 时整行皆缺则 None。"""
    values, _z, _finite = _cross_section_detail(
        rows,
        names,
        coefs,
        intercept,
        impute_all_missing=impute_all_missing,
        getter=getter,
    )
    return values


def _cross_section_detail(
    rows: Sequence[Mapping[str, Any]],
    names: Sequence[str],
    coefs: Mapping[str, float],
    intercept: float,
    *,
    impute_all_missing: bool = True,
    getter: Optional[Callable[[Mapping[str, Any], str], Any]] = None,
) -> tuple:
    """与 ``cross_section_ridge`` 同一套 z。返回 ``(values, z, finite)``。"""
    from core.research.feature_standardize import cross_section_zscore_matrix

    keys = [str(n) for n in names]
    n = len(rows)
    if n == 0:
        return [], None, None
    read = getter or _mapping_get
    mat = np.full((n, len(keys)), np.nan, dtype=np.float64)
    for i, row in enumerate(rows):
        src = row if isinstance(row, Mapping) else {}
        for j, name in enumerate(keys):
            fv = _finite(read(src, name))
            if fv is not None:
                mat[i, j] = fv
    if not keys:
        base = round(float(intercept), 6)
        if impute_all_missing:
            return [base] * n, None, None
        return [None] * n, None, None
    z = cross_section_zscore_matrix(mat, ["d"] * n)
    finite = np.isfinite(z)
    filled = np.where(finite, z, 0.0)
    beta = np.array([float(coefs.get(name) or 0.0) for name in keys], dtype=np.float64)
    total = float(intercept) + filled.dot(beta)
    used = finite.any(axis=1)
    out: List[Optional[float]] = []
    for i, value in enumerate(total):
        if not impute_all_missing and not bool(used[i]):
            out.append(None)
            continue
        out.append(round(float(value), 6))
    return out, z, finite


def _ridge_map(
    codes: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
    model: Any,
    allow: Optional[set],
    *,
    impute_all_missing: bool,
    getter: Callable[[Mapping[str, Any], str], Any],
) -> Optional[Dict[str, float]]:
    scores, _expl = _ridge_pack(
        codes,
        rows,
        model,
        allow,
        impute_all_missing=impute_all_missing,
        getter=getter,
        head="eod",
    )
    return scores


def _ridge_pack(
    codes: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
    model: Any,
    allow: Optional[set],
    *,
    impute_all_missing: bool,
    getter: Callable[[Mapping[str, Any], str], Any],
    head: str,
) -> tuple:
    """``(scores, expl_by_code)``。``scores is None`` 表示 Ridge 规格不可用。"""
    spec = _ridge_spec(model)
    if spec is None:
        return None, {}
    names, coefs, intercept = spec
    values, z, finite = _cross_section_detail(
        rows,
        names,
        coefs,
        intercept,
        impute_all_missing=impute_all_missing,
        getter=getter,
    )
    _refuse_collapsed(values, intercept)
    role = _suite_role(model)
    labels = _term_labels(names, head=head)
    scores: Dict[str, float] = {}
    expls: Dict[str, dict] = {}
    for i, (code, value) in enumerate(zip(codes, values)):
        if value is None:
            continue
        if allow is not None and str(code) not in allow:
            continue
        scores[str(code)] = float(value)
        expl = _ridge_expl_row(
            None if z is None else z[i],
            None if finite is None else finite[i],
            names,
            coefs,
            intercept,
            y_hat=float(value),
            head=head,
            role=role,
            labels=labels,
        )
        if expl is not None:
            expls[str(code)] = _cap_formula(expl, tau=(head == "tau"))
    return scores, expls


def _suite_role(model: Any) -> str:
    raw = ""
    if hasattr(model, "model_role"):
        raw = str(getattr(model, "model_role") or "")
    elif isinstance(model, Mapping):
        inner = model.get("return_model") if isinstance(model.get("return_model"), Mapping) else model
        raw = str(
            (inner or {}).get("model_role")
            or (inner or {}).get("score_model_role")
            or model.get("model_role")
            or ""
        )
    role = raw.strip().lower()
    if role in {"research", "holdout", "oos", "backtest"}:
        return "research"
    if role in {"live", "active", "execution", "exec"}:
        return "live"
    return ""


def _term_labels(names: Sequence[str], *, head: str) -> Dict[str, str]:
    try:
        from core.signal.factors.meta.registry import factor_label
    except Exception:  # noqa: BLE001
        factor_label = lambda k: str(k)  # noqa: E731
    tau_labels: Dict[str, str] = {}
    if head == "tau":
        try:
            from core.research.tc_ridge import _TAU_FEAT_LABELS

            tau_labels = {str(k): str(v) for k, v in (_TAU_FEAT_LABELS or {}).items()}
        except Exception:  # noqa: BLE001
            tau_labels = {}
    out: Dict[str, str] = {}
    for name in names:
        key = str(name)
        out[key] = tau_labels.get(key) or str(factor_label(key) or key)
    return out


def _ridge_expl_row(
    z_row: Any,
    finite_row: Any,
    names: Sequence[str],
    coefs: Mapping[str, float],
    intercept: float,
    *,
    y_hat: float,
    head: str,
    role: str,
    labels: Mapping[str, str],
) -> Optional[dict]:
    """β·z 与打分同一行 z。缺特征贡献为 0，不进表。"""
    terms: List[dict] = []
    for j, name in enumerate(names):
        if finite_row is not None and not bool(finite_row[j]):
            continue
        if z_row is None:
            continue
        try:
            beta = float(coefs.get(str(name)) or 0.0)
            zz = float(z_row[j])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(beta) or not math.isfinite(zz):
            continue
        key = str(name)
        terms.append(
            {
                "key": key,
                "label": labels.get(key) or key,
                "beta": round(beta, 6),
                "z": round(zz, 4),
                "contrib": round(beta * zz, 6),
            }
        )
    if not terms and abs(float(intercept)) < 1e-12:
        return None
    terms.sort(key=lambda t: -abs(float(t.get("contrib") or 0.0)))
    expl: Dict[str, Any] = {
        "intercept": round(float(intercept), 6),
        "terms": terms,
        "total": round(float(y_hat), 6),
        "head": "tau" if head == "tau" else "eod",
    }
    if role:
        expl["score_model_role"] = role
    if head == "tau":
        expl["y_τc"] = round(float(y_hat), 6)
    return expl


def _cap_formula(expl: dict, *, tau: bool) -> dict:
    from core.paper.rebalance.rank_lots import (
        _TAU_TIP_PIN,
        _Y_HEAD_FACTOR_N,
        _slim_tip_formula,
    )

    if tau:
        capped = _slim_tip_formula(expl, limit=_Y_HEAD_FACTOR_N, pin_keys=_TAU_TIP_PIN)
    else:
        capped = _slim_tip_formula(expl, limit=_Y_HEAD_FACTOR_N)
    return capped if isinstance(capped, dict) else expl


def _apply_formula_map(
    items: List[dict],
    expls: Mapping[str, dict],
    keys: Sequence[str],
) -> None:
    if not expls or not keys:
        return
    for it in items:
        if not isinstance(it, dict):
            continue
        expl = expls.get(str(it.get("stock_code") or ""))
        if not isinstance(expl, dict):
            continue
        for key in keys:
            it[key] = expl


def _refuse_collapsed(values: Sequence[Optional[float]], intercept: float) -> None:
    from core.signal.factors.sum_identity import cs_batch_preds_look_collapsed

    if cs_batch_preds_look_collapsed(values, intercept=intercept):
        raise RuntimeError("replay batch ridge collapsed (raw×β)")


def _rank_map(
    day: str,
    codes: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
    rank: Optional[_RankModel],
    allow: Optional[set],
    *,
    getter: Callable[[Mapping[str, Any], str], Any],
) -> Optional[Dict[str, float]]:
    """None：名次打不出来，当日退回旧路径。没有排序模型则空表。"""
    status, scores, _z_rows, _hats = _rank_pass(
        day, codes, rows, rank, allow, getter=getter
    )
    if status == "miss":
        return None
    return scores


def _rank_pass(
    day: str,
    codes: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
    rank: Optional[_RankModel],
    allow: Optional[set],
    *,
    getter: Callable[[Mapping[str, Any], str], Any],
) -> tuple:
    """``(status, scores, z_rows, hats)``。status：``empty`` 无模型 / ``miss`` 退回旧路径 / ``ok``。"""
    if rank is None:
        return "empty", {}, None, None
    from core.research.feature_standardize import is_cross_section_zscore
    from core.research.oo_rank_lambdarank import (
        _predict_oo_rank_rows,
        rank_cross_section_z_dicts,
    )

    if rank.broken or not is_cross_section_zscore(rank.model):
        return "miss", {}, None, None
    z_rows = rank_cross_section_z_dicts(
        rows,
        rank.names,
        [day] * len(rows),
        codes=list(codes),
        clock="open",
    )
    if z_rows is None:
        return "miss", {}, None, None
    if rank.booster is None:
        hats = _predict_oo_rank_rows(rank.model, z_rows, impute_missing=True)
    else:
        hats = _booster_hats(rank.booster, z_rows, rank.names, getter)
    if hats is None:
        return "miss", {}, None, None
    out: Dict[str, float] = {}
    for code, hat in zip(codes, hats):
        if hat is None:
            continue
        if allow is not None and str(code) not in allow:
            continue
        try:
            out[str(code)] = round(float(hat), 6)
        except (TypeError, ValueError):
            continue
    return "ok", out, z_rows, hats


def _stamp_oo_rank_formula(
    items: List[dict],
    codes: Sequence[str],
    z_rows: Optional[Sequence[Mapping[str, Any]]],
    hats: Optional[Sequence[Optional[float]]],
    rank: Optional[_RankModel],
    scores: Mapping[str, float],
) -> None:
    from core.research.oo_rank_lambdarank import _stamp_formula_terms_oo_rank

    _stamp_rank_formula(
        items, codes, z_rows, hats, rank, scores, stamp=_stamp_formula_terms_oo_rank
    )


def _stamp_tc_rank_formula(
    items: List[dict],
    codes: Sequence[str],
    z_rows: Optional[Sequence[Mapping[str, Any]]],
    hats: Optional[Sequence[Optional[float]]],
    rank: Optional[_RankModel],
    scores: Mapping[str, float],
) -> None:
    from core.research.tc_rank import _stamp_formula_terms_tc_rank

    _stamp_rank_formula(
        items, codes, z_rows, hats, rank, scores, stamp=_stamp_formula_terms_tc_rank
    )


def _stamp_rank_formula(
    items: List[dict],
    codes: Sequence[str],
    z_rows: Optional[Sequence[Mapping[str, Any]]],
    hats: Optional[Sequence[Optional[float]]],
    rank: Optional[_RankModel],
    scores: Mapping[str, float],
    *,
    stamp: Callable[..., None],
) -> None:
    """把与名次同一套 z 的因子组成写上票。缺模型或解释失败不改名次。"""
    if rank is None or rank.broken or not scores or not z_rows or hats is None:
        return
    from core.research.oo_rank_lambdarank import (
        _explain_oo_rank_lambdarank_rows,
        _explain_oo_rank_ridge,
        _oo_rank_use_booster,
        _oo_rank_use_ridge,
    )

    by_code = {str(it.get("stock_code") or ""): it for it in items if isinstance(it, dict)}
    model = rank.model
    try:
        if _oo_rank_use_booster(model):
            expls = _explain_oo_rank_lambdarank_rows(
                list(z_rows), model, hats=list(hats)
            )
            for code, expl in zip(codes, expls):
                if str(code) not in scores or not isinstance(expl, dict):
                    continue
                it = by_code.get(str(code))
                if it is not None:
                    stamp(it, expl)
            return
        if not _oo_rank_use_ridge(model):
            return
        for code, feats, hat in zip(codes, z_rows, hats):
            if str(code) not in scores:
                continue
            expl = _explain_oo_rank_ridge(
                feats if isinstance(feats, dict) else {},
                model,
                y_hat=hat,
            )
            it = by_code.get(str(code))
            if it is not None and isinstance(expl, dict):
                stamp(it, expl)
    except Exception:  # noqa: BLE001
        logger.debug("replay batch rank formula terms failed", exc_info=True)


def _booster_hats(
    booster: Any,
    rows: Sequence[Mapping[str, Any]],
    names: Sequence[str],
    getter: Callable[[Mapping[str, Any], str], Any],
) -> Optional[List[Optional[float]]]:
    n = len(rows)
    keys = [str(name) for name in names]
    if n == 0 or not keys:
        return [None] * n
    mat = np.zeros((n, len(keys)), dtype=np.float64)
    for i, row in enumerate(rows):
        src = row if isinstance(row, Mapping) else {}
        for j, name in enumerate(keys):
            fv = _finite(getter(src, name))
            if fv is not None:
                mat[i, j] = fv
    try:
        pred = np.asarray(booster.predict(mat), dtype=np.float64).reshape(-1)
    except Exception:  # noqa: BLE001
        logger.info("replay batch booster predict failed", exc_info=True)
        return None
    if int(pred.shape[0]) != n:
        logger.info("replay batch booster predict width %s != %s", int(pred.shape[0]), n)
        return None
    return [round(float(v), 6) if math.isfinite(float(v)) else None for v in pred]


def _stamp_oo_ranks(items: List[dict], scores: Mapping[str, float]) -> None:
    from core.research.oo_rank_lambdarank import assign_oo_rank_day_ranks

    for it in items:
        code = str(it.get("stock_code") or "")
        if code in scores:
            it["y_oo_rank_score"] = float(scores[code])
    assign_oo_rank_day_ranks(items)


def _stamp_tc_ranks(items: List[dict], scores: Mapping[str, float]) -> None:
    from core.research.tc_rank import _assign_tc_rank_day_ranks

    for it in items:
        code = str(it.get("stock_code") or "")
        if code in scores:
            it["y_tc_rank_score"] = float(scores[code])
    _assign_tc_rank_day_ranks(items)


def _decision_item(
    code: str,
    day: str,
    *,
    quote: Mapping[str, Any],
    y_oo: Optional[float],
    y_tc: Optional[float],
) -> dict:
    item: Dict[str, Any] = {
        "stock_code": code,
        "as_of": day,
        "date": day,
        "trade_date": day,
        "success": True,
        "_score_source": "feature_lake_batch",
        "ret_open_to_tau": 0.0,
        # 与 tip provenance 对齐：回测开盘分 = 日线今开终态
        "pit_phase": "final",
        "open_source": "daily",
        "cube_builder": "lake_open",
    }
    if quote:
        item["quote"] = dict(quote)
        for key in ("price_raw", "price", "open", "open_raw", "prev_close", "change_pct"):
            if quote.get(key) is not None:
                item.setdefault(key, quote.get(key))
    if y_oo is not None:
        item["y_oo"] = y_oo
        item["predicted_score_oo"] = y_oo
        item["predicted_score"] = y_oo
    if y_tc is not None:
        item["y_τc"] = y_tc
        item["predicted_score_τc"] = y_tc
        item["y_τc_source"] = "ridge"
    return item


def _load_slice(
    codes: Sequence[str],
    dates: Sequence[str],
    *,
    cancel_cb: Optional[Callable[[], bool]],
) -> Optional[_OpenSlice]:
    from core.research.feature_lake import enabled, slice_stock_panels
    from core.research.lake_bridge import FAMILY_OO, FAMILY_TC

    if not enabled() or not codes or not dates:
        return None
    packed = []
    for fam in (FAMILY_OO, FAMILY_TC):
        if _cancelled(cancel_cb):
            return None
        part = slice_stock_panels(fam, codes, dates, cancel_cb=cancel_cb)
        if part is None:
            return None
        packed.append(part)
    oo, tc = packed
    if len(oo) < _min_rows(len(codes)):
        return None
    holder = _OpenSlice(list(codes), list(dates), oo, tc)
    global _HOLDER
    _HOLDER = holder
    return holder


def _ensure_models(holder: _OpenSlice) -> bool:
    if holder._models_ready:
        return holder.oo_model is not None
    holder._models_ready = True
    try:
        from core.research.feature_standardize import is_cross_section_zscore
        from core.research.oo_rank_lambdarank import load_oo_rank_model
        from core.research.oo_ridge import load_scoring_return_model
        from core.research.tc_rank import load_tc_rank_model
        from core.research.tc_ridge import load_tau_model

        oo_model, _meta = load_scoring_return_model()
        tau_doc = load_tau_model()
        oo_rank = _load_rank(load_oo_rank_model)
        tc_rank = _load_rank(load_tc_rank_model)
    except Exception as exc:  # noqa: BLE001
        if exc.__class__.__name__ == "MaterializeRequired":
            raise
        logger.info("replay open batch model load failed", exc_info=True)
        return False
    if (
        not is_cross_section_zscore(oo_model)
        or not is_cross_section_zscore(tau_doc)
    ):
        logger.info("replay open batch skip: ridge head is not cross-section z")
        return False
    if (oo_rank is not None and oo_rank.broken) or (tc_rank is not None and tc_rank.broken):
        logger.info("replay open batch rank model unusable; legacy score")
        return False
    holder.oo_model = oo_model
    holder.tau_doc = tau_doc
    holder.oo_rank = oo_rank
    holder.tc_rank = tc_rank
    return True


def _load_rank(loader: Callable[..., Any]) -> Optional[_RankModel]:
    from core.research.holdout import MODEL_ROLE_RESEARCH, current_scoring_model_role
    from core.research.oo_rank_lambdarank import (
        _oo_rank_feature_names,
        _oo_rank_use_booster,
        _resolve_oo_rank_fit,
    )

    prefer_research = current_scoring_model_role() == MODEL_ROLE_RESEARCH
    try:
        doc = loader(prefer_research=prefer_research)
    except TypeError:
        doc = loader()
    model = _resolve_oo_rank_fit(model_doc=doc)
    if not isinstance(model, dict):
        return None
    names = _oo_rank_feature_names(model)
    booster = _decode_booster(model) if _oo_rank_use_booster(model) else None
    if _oo_rank_use_booster(model) and booster is None:
        logger.info("replay open batch rank booster undecoded")
        return _RankModel(model, names, None, broken=True)
    return _RankModel(model, names, booster)


def _decode_booster(model: Mapping[str, Any]) -> Any:
    import base64

    import lightgbm as lgb

    raw = str(model.get("booster_b64") or "")
    if not raw:
        return None
    try:
        text = base64.b64decode(raw.encode("ascii")).decode("utf-8")
        return lgb.Booster(model_str=text)
    except Exception:  # noqa: BLE001
        logger.info("replay batch booster decode failed", exc_info=True)
        return None


def _ridge_spec(model: Any) -> Optional[tuple]:
    from core.research.feature_standardize import is_cross_section_zscore
    from core.signal.factors.sum_identity import filter_predict_active_names, finite_coef

    if model is None or not is_cross_section_zscore(model):
        return None
    if hasattr(model, "coefficients"):
        coefs = model.coefficients or {}
        try:
            intercept = float(model.intercept or 0.0)
        except (TypeError, ValueError):
            intercept = 0.0
        raw_names = list(coefs.keys())
    else:
        rm = model.get("return_model") if isinstance(model, Mapping) else None
        if not isinstance(rm, Mapping):
            rm = model if isinstance(model, Mapping) else {}
        coefs = rm.get("coefficients") if isinstance(rm.get("coefficients"), Mapping) else {}
        try:
            intercept = float(rm.get("intercept") or 0.0)
        except (TypeError, ValueError):
            intercept = 0.0
        listed = rm.get("active_features")
        raw_names = list(listed) if isinstance(listed, list) and listed else list(coefs.keys())
    names = filter_predict_active_names(raw_names, coefs)
    clean: Dict[str, float] = {}
    for name in names:
        beta = finite_coef(coefs.get(name))
        if beta is not None:
            clean[str(name)] = float(beta)
    if not clean:
        return None
    return list(clean.keys()), clean, intercept


def _day_rows(packed: Mapping[str, tuple], day: str) -> Dict[str, Dict[str, float]]:
    from core.research.feature_lake import finite_feature_dict

    out: Dict[str, Dict[str, float]] = {}
    for code, hit in (packed or {}).items():
        if not hit or len(hit) < 2 or not isinstance(hit[1], Mapping):
            continue
        row = hit[1].get(day)
        if row is None:
            continue
        feats = finite_feature_dict(hit[0], row)
        if feats:
            out[str(code)] = feats
    return out


def _stamped(raw: Optional[Mapping[str, Any]], stamp: Callable[[Dict[str, Any]], Any]) -> Dict[str, Any]:
    row = dict(raw or {})
    try:
        stamp(row)
    except Exception:  # noqa: BLE001
        logger.debug("replay batch stamp failed", exc_info=True)
    return row


def _stamped_tau(
    raw: Optional[Mapping[str, Any]],
    *,
    quote: Optional[Mapping[str, Any]] = None,
    pool: Optional[Mapping[str, Any]] = None,
    code: str = "",
    day: str = "",
    trigger: float = 2.0,
) -> Dict[str, Any]:
    from core.signal.minute_tau_feats import stamp_open_clock_minute_z

    row = _stamped(raw, _stamp_tc)
    _overlay_open_tau(row, quote=quote, pool=pool, code=code, day=day, trigger=trigger)
    stamp_open_clock_minute_z(row)
    _stamp_tc(row)
    return row


def _gap_trigger() -> float:
    try:
        from core.market.priors.event import get_event_prior_cfg

        return float((get_event_prior_cfg() or {}).get("gap_trigger_pct") or 2.0)
    except (TypeError, ValueError):
        return 2.0
    except Exception:  # noqa: BLE001
        logger.debug("replay batch gap trigger failed", exc_info=True)
        return 2.0


def _overlay_open_tau(
    row: Dict[str, Any],
    *,
    quote: Optional[Mapping[str, Any]],
    pool: Optional[Mapping[str, Any]],
    code: str,
    day: str,
    trigger: float,
) -> None:
    """09:30 开盘缺口截面。裸名留给后面的 stamp，盖过湖里的同名列。"""
    try:
        from core.research.tau_panel import gap_vs_sector_value
        from core.research.tau_theme import resolve_theme_day
        from core.signal.session_pit import resolve_open_t
        from core.t0.score_policy import tau_pool_day_score_kwargs
    except Exception:  # noqa: BLE001
        logger.debug("replay batch tau overlay import failed", exc_info=True)
        return
    xs = tau_pool_day_score_kwargs(pool if isinstance(pool, Mapping) else {}, code)
    try:
        gap = resolve_open_t(quote if isinstance(quote, dict) else {}, [], trade_day=day).get("gap_pct")
    except Exception:  # noqa: BLE001
        logger.debug("replay batch open gap failed", exc_info=True)
        gap = None
    breadth = xs.get("sector_gap_breadth")
    try:
        theme = resolve_theme_day(
            gap_pct=gap,
            sector_breadth=breadth,
            pool_gaps=xs.get("pool_gaps") if isinstance(xs.get("pool_gaps"), (list, tuple)) else None,
            gap_trigger_pct=float(trigger),
        )
    except Exception:  # noqa: BLE001
        theme = None
    vs = gap_vs_sector_value(gap, xs.get("sector_gap_median"))
    if gap is not None:
        row["gap_pct"] = gap
    if breadth is not None:
        row["sector_gap_breadth"] = breadth
    if theme is not None:
        row["theme_day"] = theme
    if vs is not None:
        row["gap_vs_sector"] = vs


def _stamp_oo(row: Dict[str, Any]) -> None:
    from core.signal.factors.z_oo import stamp_z_oo_aliases

    stamp_z_oo_aliases(row)


def _stamp_tc(row: Dict[str, Any]) -> None:
    from core.signal.factors.z_tc import stamp_z_tc_aliases

    stamp_z_tc_aliases(row)


def _oo_get(row: Mapping[str, Any], name: str) -> Any:
    from core.signal.factors.z_oo import feature_get

    return feature_get(row, name)


def _tc_get(row: Mapping[str, Any], name: str) -> Any:
    from core.signal.factors.z_tc import feature_get

    return feature_get(row, name)


def _quote(
    code: str,
    day: str,
    prev: str,
    date_maps: Mapping[str, Mapping[str, dict]],
) -> dict:
    from core.backtest.replay_scoring import mock_quote_from_bar

    dm = date_maps.get(code) or {}
    today = dm.get(day)
    prev_bar = dm.get(prev) if prev else None
    quote = mock_quote_from_bar(today, prev_bar=prev_bar, px_field="open")
    if quote:
        quote["date"] = day
        quote["trade_date"] = day
    return quote or {}


def _note_once(holder: _OpenSlice, key: str, message: str) -> None:
    if key in holder._notes:
        return
    holder._notes.add(key)
    logger.info(message)


def _norm_dates(dates: Sequence[str]) -> List[str]:
    return [str(d)[:10] for d in (dates or []) if len(str(d or "")[:10]) >= 10]


def _min_rows(n_codes: int) -> int:
    return max(2, int(math.ceil(float(n_codes) * _MIN_COVER)))


def _cancelled(cancel_cb: Optional[Callable[[], bool]]) -> bool:
    if cancel_cb is None:
        return False
    try:
        return bool(cancel_cb())
    except Exception:  # noqa: BLE001
        logger.debug("replay batch cancel_cb failed", exc_info=True)
        return False


def _mapping_get(row: Mapping[str, Any], name: str) -> Any:
    return row.get(name)


def _finite(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out
