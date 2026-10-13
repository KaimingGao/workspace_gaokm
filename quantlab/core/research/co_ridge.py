"""隔夜缺口 Ridge：报告包装 / Holdout OOS / 落盘启用；风控旁路 ŷ_co。

面板拼装与矩阵拟合见 ``co_ridge_compact``（对齐 oo/tc）。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.research.co_panel import CO_LAG_FEAT_LABELS
from core.research.co_ridge_compact import fit_co_ridge_matrices
from core.research.tc_ridge import (
    _ic,
    _oos_by_theme,
    _predict_rows,
    _residual_var,
    _sign_hit,
)
from core.signal.factors.z_co import (
    feature_get,
    stamp_z_co_aliases,
    z_co_feat_labels,
)


def fit_co_ridge_report(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    ridge_lambda: float = 1.0,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    theme_boost: float = 1.5,
    holdout_trading_days: int = 20,
    use_theme_weights: bool = True,
    cross_section_zscore: bool = True,
) -> Dict[str, Any]:
    """池化拟合 ŷ_co(Z) + 时间 OOS。标签 = open[T+1]/close[T]-1（决策日 T 开盘）。

    ``cross_section_zscore``：True=日截面 z；False=训练样本内全局 μ/σ。
    """
    from core.research.holdout import attach_holdout_meta, make_research_model
    from core.signal.factors.alpha_oo import is_alpha_oo_raw_key

    packed = fit_co_ridge_matrices(
        stock_bars,
        ridge_lambda=ridge_lambda,
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        theme_boost=theme_boost,
        holdout_trading_days=holdout_trading_days,
        use_theme_weights=use_theme_weights,
        cross_section_zscore=cross_section_zscore,
    )
    if not packed.get("success"):
        return {
            "success": False,
            "error": packed.get("error") or "拟合失败",
            "task": "co_ridge",
            "sample_count": packed.get("sample_count"),
            "stock_count": packed.get("stock_count"),
        }

    n_stocks = packed["n_stocks"]
    ys = packed["ys"]
    ys_tr = packed["ys_tr"]
    ys_te = packed["ys_te"]
    metas_te = packed["metas_te"]
    preds_te = packed["preds_te"]
    hold_n = packed["hold_n"]
    fill_keys = packed["fill_keys"]
    feature_fill = packed["feature_fill"]
    use_cs = packed["use_cs"]
    theme_boost_v = packed["theme_boost"]
    use_theme_weights = packed["use_theme_weights"]
    fit = packed["model"]
    split_meta = packed["split_meta"]

    by_theme = _oos_by_theme(preds_te, ys_te, metas_te) if ys_te else {}
    oos = {
        "n_train": len(ys_tr),
        "n_test": len(ys_te),
        "ic": _ic(preds_te, ys_te) if ys_te else None,
        "sign_hit": _sign_hit(preds_te, ys_te) if ys_te else None,
        "residual_var": _residual_var(preds_te, ys_te) if ys_te else None,
        "by_theme": by_theme,
        "holdout_trading_days": hold_n,
        "theme_boost": theme_boost_v if use_theme_weights else None,
        "target": "overnight_gap",
        "feature_fill": feature_fill,
        "include_alpha_oo": True,
    }
    if ys_te:
        try:
            from core.research.daily_cs_ic import attach_daily_cs_ic

            attach_daily_cs_ic(oos, preds_te, ys_te, metas_te)
        except Exception:  # noqa: BLE001
            logger.debug("co attach_daily_cs_ic failed", exc_info=True)

    model = make_research_model(fit)
    model["horizon_mode"] = "overnight_gap"
    model["target"] = "overnight_gap"
    y_formula = "open[T+1]/close[T]-1"
    model["y_spec"] = {
        "formula": y_formula,
        "unit": "pct",
        "anchor": "close[T]",
        "note": "真实隔夜缺口（T 收盘→T+1 开盘）；风控旁路，不进主排序",
    }
    model["extra_features"] = list(fill_keys)
    model["include_alpha_oo"] = True

    n_aoo = sum(
        1 for k in (model.get("extra_features") or []) if is_alpha_oo_raw_key(str(k))
    )
    report = {
        "success": True,
        "task": "co_ridge",
        "stock_count": n_stocks,
        "sample_count": len(ys),
        "oos": oos,
        "return_model": model,
        "return_model_research": model,
        "y_spec": dict(model.get("y_spec") or {}),
        "schema": "co_ridge_v2",
        "target": "overnight_gap",
        "include_alpha_oo": True,
        "n_alpha_oo_features": n_aoo,
        "feature_pack_formula": "z_co_* ∪ raw_aoo_*",
        "note": (
            "ŷ_co(z_co_*∪raw_aoo_*) 估 open[T+1]/close[T]-1；"
            "T-1 路径 + 开盘 Z + 昨 K 微观 + 隔夜滞后 + raw_aoo_*；与 EOD/τ 解耦"
        ),
    }
    attach_holdout_meta(report, split_meta)
    report["cross_section_zscore"] = use_cs
    return report


def co_model_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "co_ridge_model.json")


def co_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "co_ridge_last_report.json")


def _load_model_file(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001
        logger.debug("load co json failed: %s", path, exc_info=True)
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("return_model"), dict):
        return None
    if doc.get("success") is False:
        return None
    return doc


def save_co_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    if not isinstance(report.get("return_model"), dict):
        return
    from core.research.holdout import stamp_fitted_at

    stamp_fitted_at(report)
    path = co_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)


def load_co_last_report() -> Optional[Dict[str, Any]]:
    doc = _load_model_file(co_last_report_path())
    if doc and doc.get("success"):
        return doc
    return None


def persist_co_model(
    report: Dict[str, Any], *, note: str = "", role: str = "live"
) -> Dict[str, Any]:
    from core.research.holdout import (
        MODEL_ROLE_LIVE,
        MODEL_ROLE_RESEARCH,
        normalize_model_role,
        require_research_suite,
        research_model_path,
        select_fit_return_model,
        stamp_live_suite_doc,
    )

    role_n = normalize_model_role(role)
    live_path = co_model_path()
    if role_n == MODEL_ROLE_LIVE:
        research_doc, err = require_research_suite(live_path)
        if err:
            return err
        doc = stamp_live_suite_doc(
            research_doc, note=note or "co_ridge research → live promote"
        )
        os.makedirs(os.path.dirname(live_path), exist_ok=True)
        atomic_write_json(live_path, doc)
        return {
            "success": True,
            "path": live_path,
            "promoted_at": doc.get("promoted_at"),
            "schema": doc.get("schema"),
            "model_role": MODEL_ROLE_LIVE,
            "promoted_from": "research",
        }

    if not report.get("success"):
        return {"success": False, "error": report.get("error") or "no report"}
    rm = select_fit_return_model(report)
    if not isinstance(rm, dict):
        return {"success": False, "error": "return_model missing"}
    if not rm.get("coefficients") and rm.get("intercept") is None:
        return {"success": False, "error": "return_model missing"}
    from core.numbers import now_iso_utc

    y_spec = dict(rm.get("y_spec") or {})
    if not y_spec:
        y_spec = {
            "formula": "open[T+1]/close[T]-1",
            "unit": "pct",
            "anchor": "close[T]",
            "note": "真实隔夜缺口",
        }
    y_spec.setdefault("unit", "pct")
    y_spec.setdefault("anchor", "close[T]")
    rm = dict(rm)
    rm["y_spec"] = y_spec
    rm["horizon_mode"] = rm.get("horizon_mode") or "overnight_gap"
    rm["target"] = str(report.get("target") or rm.get("target") or "overnight_gap")
    rm["model_role"] = MODEL_ROLE_RESEARCH

    schema = str(report.get("schema") or "co_ridge_v2")
    from core.research.holdout import model_fit_id

    fitted_at = model_fit_id(report)
    doc = {
        "success": True,
        "fitted_at": fitted_at,
        "promoted_at": now_iso_utc(),
        "note": note or "co_ridge promote research",
        "return_model": rm,
        "oos": report.get("oos"),
        "sample_count": report.get("sample_count"),
        "stock_count": report.get("stock_count"),
        "schema": schema,
        "target": rm["target"],
        "y_spec": y_spec,
        "y_spec_co": y_spec,
        "y_spec_on": y_spec,
        "dual_score_head": "predicted_score_on",
        "model_role": MODEL_ROLE_RESEARCH,
        "fit_end": report.get("fit_end"),
        "eval_start": report.get("eval_start"),
        "contract_note": "ŷ_co 估 open[T+1]/close[T]-1 隔夜缺口；风控旁路，不覆盖 EOD/τ 字段。",
    }
    if isinstance(report, dict) and "cross_section_zscore" in report:
        doc["cross_section_zscore"] = bool(report.get("cross_section_zscore"))
    path = research_model_path(live_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, doc)
    out = {
        "success": True,
        "path": path,
        "promoted_at": doc["promoted_at"],
        "schema": doc["schema"],
    }
    return out


def load_co_model(*, role: Optional[str] = None) -> Optional[Dict[str, Any]]:
    from core.research.holdout import (
        MODEL_ROLE_RESEARCH,
        current_scoring_model_role,
        research_model_path,
    )

    role_n = role if role is not None else current_scoring_model_role()
    if role_n == MODEL_ROLE_RESEARCH:
        doc = _load_model_file(research_model_path(co_model_path()))
        if doc:
            return doc
        return None
    doc = _load_model_file(co_model_path())
    if doc:
        return doc
    # 已跑 co Ridge 但未 promote 时，用 last report 影子推理（显式 promote 写 co_ridge_model.json）
    fallback = load_co_last_report()
    if fallback:
        out = dict(fallback)
        out["_shadow"] = True
        return out
    return None


# 分钟前缀不进 ŷ_co。旧模型若仍带系数，打分时丢掉该列（缺特征 → z=0），避免开盘→τ 挪动隔夜预估。
_CO_EXCLUDED_FEATURES = ("ret_open_to_tau",)


def _co_score_row(features: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    row = dict(features or {})
    for key in _CO_EXCLUDED_FEATURES:
        row.pop(key, None)
    return stamp_z_co_aliases(row)


def predict_co_from_features(
    features: Dict[str, Optional[float]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    doc = model_doc if model_doc is not None else load_co_model()
    if not doc:
        return None
    rm = doc.get("return_model") or {}
    # 双写裸名↔z_co_* 后，旧 v1 β 与新 v2 canon 都能打分
    preds = _predict_rows(rm, [_co_score_row(features)])
    return preds[0] if preds else None


_CO_FEAT_LABELS = z_co_feat_labels(
    {
        "ret_oc": "昨开→昨收 %",
        "gap_pct": "跳空 %",
        "ret_cc": "昨收→前收 %",
        "y_on_today": "今开/昨开 %",
        "sector_gap_breadth": "同业缺口广度",
        "theme_day": "主题日",
        "gap_atr": "缺口 / ATR",
        "gap_vs_sector": "行业相对缺口",
        "yclose_loc": "今开相对昨高低",
        "yest_close_loc": "昨收位置",
        "yest_vol_ratio": "昨量/均量",
        "dist_to_up_limit": "距涨停 %",
        **CO_LAG_FEAT_LABELS,
    }
)


def explain_co_prediction(
    features: Optional[Dict[str, Any]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    doc = model_doc if model_doc is not None else load_co_model()
    if not doc:
        return None
    fit = doc.get("return_model") or {}
    coefs = fit.get("coefficients") or {}
    intercept = float(fit.get("intercept") or 0.0)
    if not coefs and abs(intercept) < 1e-12:
        return None
    means = fit.get("zscore_means") or fit.get("z_means") or {}
    stds = fit.get("zscore_stds") or fit.get("z_stds") or {}
    active = [
        n
        for n in (fit.get("active_features") or coefs.keys())
        if coefs.get(n) is not None
    ]
    row = _co_score_row(features)
    try:
        from core.signal.factors.meta.registry import factor_label
    except Exception:  # noqa: BLE001
        factor_label = lambda k: str(k)  # noqa: E731

    terms: List[Dict[str, Any]] = []
    total = intercept
    for name in active:
        if name in _CO_EXCLUDED_FEATURES:
            continue
        beta = float(coefs.get(name) or 0.0)
        v = feature_get(row, name)
        imputed = False
        if v is None:
            z = 0.0
            imputed = True
        else:
            try:
                fv = float(v)
            except (TypeError, ValueError):
                z = 0.0
                imputed = True
            else:
                mu = float(means.get(name, 0.0)) if means else 0.0
                sd = float(stds.get(name, 1.0)) if stds else 1.0
                if sd < 1e-9:
                    sd = 1.0
                z = (fv - mu) / sd if means else fv
        contrib = beta * z
        total += contrib
        label = _CO_FEAT_LABELS.get(name) or factor_label(name) or name
        term: Dict[str, Any] = {
            "key": str(name),
            "label": str(label),
            "beta": round(beta, 6),
            "z": round(float(z), 4),
            "contrib": round(float(contrib), 6),
        }
        if imputed:
            term["note"] = "缺特征·z≈0"
        terms.append(term)
    if not terms and abs(intercept) < 1e-12:
        return None
    terms.sort(key=lambda t: -abs(float(t.get("contrib") or 0)))
    return {
        "intercept": round(intercept, 6),
        "terms": terms[:24],
        "total": round(total, 6),
        "head": "co",
    }
