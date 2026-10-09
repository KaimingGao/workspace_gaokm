"""ŷ_τ{t30..t90} Ridge 泛化模块：开盘 Z + 开→τ 收益/截面 + 序列特征 → P(窗收益>0)。

替代 ``core/research/t30_ridge.py … t90_ridge.py``，所有 horizon 共享一套实现，
通过 ``head`` 参数（"t30"/"t45"/"t60"/"t75"/"t90"）区分。

盘中写 y_τ{num}=p_up。进 ŷ_τw 投票；个股闸与入场已下线。不进 C_τ / ranking。
决策钟只覆盖 09:30…11:00（与 ŷ_τc 对齐）；窗外不训、不预。
不含 ŷ_τ 的 OC 路径形状（HL/回撤/振幅）。
序列键只进本头，不进 TAU_Z_FEATURES。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.research.horizon_prob import (
    horizon_promote_gate,
    predict_p_up_rows,
    stamp_horizon_explain,
    train_eval_horizon_prob,
)
from core.research.tc_ridge import (
    TAU_FIT_DROP_ALIASES,
    TAU_HORIZON_DROP_OC_SHAPE,
    TAU_MIN_STD_EXEMPT,
    TAU_Z_FEATURES,
    _stack_panels,
    _subset,
    _theme_counts,
    _theme_trigger_sensitivity,
    build_tau_panels_from_bars,
)
from core.signal.minute_tau_feats import MINUTE_TAU_FEAT_LABELS
from core.signal.minute_tau_grid import (
    DEFAULT_T0_TRAIN_TAU_GRID_5M,
    format_shared_tau_formula,
)

HORIZON_RIDGE_HEADS = ("t30", "t45", "t60", "t75", "t90")

_RIDGE_MODEL_CACHE: Dict[str, Tuple[Tuple[float, float], Optional[Dict[str, Any]]]] = {}


def _horizon_ridge_config(head: str) -> Dict[str, Any]:
    """按 head 解析 horizon ridge 训练所需的特定配置。"""
    import importlib

    h = str(head or "").strip().lower()
    if h not in HORIZON_RIDGE_HEADS:
        raise ValueError(f"unsupported horizon ridge head: {head!r}")
    num = int(h[1:])
    tau_mod = importlib.import_module("core.research.tau_panel")
    grid_mod = importlib.import_module("core.signal.minute_tau_grid")
    seq_features = tuple(getattr(tau_mod, f"{h.upper()}_SEQ_FEATURES"))
    lag_feat_labels = getattr(tau_mod, f"{h.upper()}_LAG_FEAT_LABELS")
    relabel_fn = getattr(tau_mod, f"relabel_tau_panels_as_{h}")
    y_pct_fn = getattr(tau_mod, f"y_{h}_pct")
    train_tau_grid = getattr(grid_mod, f"DEFAULT_{h.upper()}_TRAIN_TAU_GRID", DEFAULT_T0_TRAIN_TAU_GRID_5M)
    offsets = getattr(grid_mod, f"{h.upper()}_LABEL_OFFSETS")
    z_features = tuple(
        k for k in (TAU_Z_FEATURES + seq_features) if k not in TAU_HORIZON_DROP_OC_SHAPE
    )
    min_std_exempt = tuple(TAU_MIN_STD_EXEMPT) + seq_features
    return {
        "head": h,
        "num": num,
        "z_features": z_features,
        "min_std_exempt": min_std_exempt,
        "seq_features": seq_features,
        "lag_feat_labels": dict(lag_feat_labels),
        "relabel_fn": relabel_fn,
        "y_pct_fn": y_pct_fn,
        "train_tau_grid": tuple(train_tau_grid),
        "offsets": tuple(offsets),
        "schema": f"{h}_ridge_v2",
        "target": f"price_tau_plus_{num}",
        "dual_score_head": f"y_{h}",
        "formula": f"mean(price[τ+{offsets[0]}m],price[τ+{offsets[1]}m],price[τ+{offsets[2]}m])/price[τ]-1",
    }


# ---------------------------------------------------------------------------
# 字段读写：pick_y_hat / pick_y_label / write_y_hat / write_y_label / pack_y_fields
# ---------------------------------------------------------------------------


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return x


def _hat_keys(head: str) -> Tuple[str, ...]:
    cfg = _horizon_ridge_config(head)
    h = cfg["head"]
    num = cfg["num"]
    return (f"predicted_score_{h}", f"y_{h}_hat", f"y_\u03c4{num}", f"y_{h}")


def _label_keys(head: str) -> Tuple[str, ...]:
    h = _horizon_ridge_config(head)["head"]
    return (f"{h}_realized", f"y_{h}_realized")


def pick_y_hat(head: str, *objs: Any) -> Optional[float]:
    """盘中 ŷ_τ{num}（p_up∈(0,1)）。"""
    keys = _hat_keys(head)
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        for k in keys:
            v = _f(obj.get(k))
            if v is not None:
                return v
    return None


def pick_y_label(head: str, *objs: Any) -> Optional[float]:
    keys = _label_keys(head)
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        for k in keys:
            v = _f(obj.get(k))
            if v is not None:
                return v
    return None


def write_y_hat(head: str, dest: Dict[str, Any], val: float, *, formula: Any = None) -> None:
    cfg = _horizon_ridge_config(head)
    h = cfg["head"]
    num = cfg["num"]
    x = float(val)
    dest[f"predicted_score_{h}"] = x
    dest[f"y_{h}_hat"] = x
    dest[f"y_{h}"] = x
    dest[f"y_\u03c4{num}"] = x
    spec = str(formula or cfg["formula"])
    dest[f"y_spec_\u03c4{num}"] = {"formula": spec, "unit": "prob"}
    dest[f"y_spec_{h}"] = {"formula": spec, "unit": "prob"}


def write_y_label(head: str, dest: Dict[str, Any], val: float) -> None:
    h = _horizon_ridge_config(head)["head"]
    x = float(val)
    dest[f"{h}_realized"] = x
    dest[f"y_{h}_realized"] = x


def pack_y_fields(head: str, day: Optional[dict]) -> Dict[str, Any]:
    cfg = _horizon_ridge_config(head)
    h = cfg["head"]
    num = cfg["num"]
    val = pick_y_label(head, day) if isinstance(day, dict) else None
    hat = pick_y_hat(head, day) if isinstance(day, dict) else None
    return {
        f"{h}_realized": val,
        f"y_{h}_realized": val,
        f"y_\u03c4{num}": hat,
        f"y_{h}": hat,
    }


def horizon_realized_pct(head: str, price_tau: Any, price_tau_n: Any) -> Optional[float]:
    cfg = _horizon_ridge_config(head)
    v = cfg["y_pct_fn"](price_tau, price_tau_n)
    return round(float(v), 4) if v is not None else None


# ---------------------------------------------------------------------------
# 训练：fit_horizon_ridge_report
# ---------------------------------------------------------------------------


def fit_horizon_ridge_report(
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
    cross_section_zscore: bool = True,
) -> Dict[str, Any]:
    """池化拟合 ŷ_τ{num}=p_up + 时间 OOS。标签 = I(mean(price(τ⊕off1/off2/off3))/price(τ)−1 > 0)。

    ``cross_section_zscore``：True=日截面 z；False=训练样本内全局 μ/σ。
    """
    from core.research.tau_panel import theme_sample_weights

    cfg = _horizon_ridge_config(head)
    h = cfg["head"]
    num = cfg["num"]
    z_features = cfg["z_features"]
    min_std_exempt = cfg["min_std_exempt"]
    relabel_fn = cfg["relabel_fn"]
    train_tau_grid = cfg["train_tau_grid"]
    offsets = cfg["offsets"]
    schema = cfg["schema"]
    target = cfg["target"]
    dual_score_head = cfg["dual_score_head"]
    formula = cfg["formula"]
    seq_features = cfg["seq_features"]
    lag_feat_labels = cfg["lag_feat_labels"]

    live_hm = str(tau_hm or "10:30").strip() or "10:30"
    if live_hm.lower() in ("", "open"):
        live_hm = "10:30"
    grid = list(tau_grid) if tau_grid is not None else list(train_tau_grid)
    raw = build_tau_panels_from_bars(
        stock_bars,
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        tau_hm=live_hm,
        tau_grid=grid,
    )
    enriched = relabel_fn(raw)
    xs, ys, dates, metas = _stack_panels(enriched)
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"{h} 样本不足 n={len(ys)}（需≥20 且需 τ⊕{offsets[0]}/{offsets[1]}/{offsets[2]} 三根均价）",
            "task": f"{h}_ridge",
            "sample_count": len(ys),
            "stock_count": len(enriched),
            "tau_grid": list(grid),
            "minute_tau_hm": live_hm,
        }

    xs_z = [{k: (row or {}).get(k) for k in z_features} for row in xs]
    from core.research.holdout import (
        DEFAULT_HOLDOUT_TRADING_DAYS,
        attach_holdout_meta,
        calendar_dates_from_stock_bars,
        resolve_ridge_split,
    )

    hold_n = int(holdout_trading_days or DEFAULT_HOLDOUT_TRADING_DAYS)
    train_idx, test_idx, split_meta = resolve_ridge_split(
        dates,
        holdout_trading_days=hold_n,
        calendar_dates=calendar_dates_from_stock_bars(stock_bars),
    )
    xs_tr, ys_tr, metas_tr = _subset(xs_z, ys, metas, train_idx)
    xs_te, ys_te, metas_te = _subset(xs_z, ys, metas, test_idx)

    weights = (
        theme_sample_weights(metas_tr, theme_boost=theme_boost)
        if use_theme_weights
        else None
    )
    feat_names = [k for k in z_features if k not in TAU_FIT_DROP_ALIASES]
    w_all = (
        theme_sample_weights(metas, theme_boost=theme_boost)
        if use_theme_weights
        else None
    )
    use_cs = bool(cross_section_zscore)
    packed = train_eval_horizon_prob(
        xs_tr,
        ys_tr,
        xs_te,
        ys_te,
        metas_te,
        xs_z,
        ys,
        feat_names=feat_names,
        ridge_lambda=ridge_lambda,
        weights=weights,
        weights_all=w_all,
        min_std_exempt=min_std_exempt,
        dates_tr=[dates[i] for i in train_idx] if use_cs else None,
        dates_te=[dates[i] for i in test_idx] if use_cs else None,
        dates_all=dates if use_cs else None,
        cross_section_zscore=use_cs,
    )
    oos: Dict[str, Any] = dict(packed.get("oos_core") or {})
    oos.update(
        {
            "n_train": len(ys_tr),
            "n_test": len(ys_te),
            "theme_counts": {
                "train": _theme_counts(metas_tr),
                "oos": _theme_counts(metas_te),
                "all": _theme_counts(metas),
            },
            "theme_trigger_sensitivity": _theme_trigger_sensitivity(metas),
            "feature_fill": None,
            "holdout_trading_days": hold_n,
            "theme_boost": theme_boost if use_theme_weights else None,
            "target": target,
            "tau": live_hm,
            "tau_grid": list(grid),
            "minute_tau_hm": live_hm,
        }
    )
    try:
        from core.research.panel import feature_fill_rates

        oos["feature_fill"] = feature_fill_rates(xs_z, z_features)
    except Exception:  # noqa: BLE001
        logger.debug("%s feature_fill failed", h, exc_info=True)

    oos["label_dist"] = {
        "n_labeled": len(ys),
        "y_mean": round(sum(ys) / float(len(ys)), 4) if ys else None,
        "n_unique_days": len({str(d)[:10] for d in dates}),
        "rows_per_day": round(len(ys) / max(1, len({str(d)[:10] for d in dates})), 2),
    }

    research_model = dict(packed.get("research_model") or {})
    model = dict(packed.get("model") or {})
    model["horizon_mode"] = target
    model["target"] = target
    y_formula = format_shared_tau_formula(formula, grid or [live_hm])
    seq_str = " / ".join(seq_features)
    model["y_spec"] = {
        "formula": y_formula,
        "unit": "prob",
        "label": f"I(mean(price(τ⊕{offsets[0]}/{offsets[1]}/{offsets[2]}))/price(τ)−1 > 0)",
        "tau": live_hm,
        "tau_grid": list(grid),
        "note": (
            f"X = 开盘 Z + 开→τ 收益/截面 + ŷ_τ{num} 序列特征"
            f"（{seq_str}）；"
            "不含 ŷ_τ 的 OC 路径形状。"
            f"ŷ_τ{num}=P(窗收益>0)；做 T 用 p_agree（正T=p_up，反T=1−p_up）；不进 C_τ / ranking。"
        ),
    }
    model["extra_features"] = list(z_features)
    from core.research.tau_panel import TAU_LAG_FEAT_LABELS

    model["feat_labels"] = {
        **dict(MINUTE_TAU_FEAT_LABELS),
        **dict(TAU_LAG_FEAT_LABELS),
        **lag_feat_labels,
    }
    model["minute_tau_hm"] = live_hm
    model["tau_grid"] = list(grid)
    model["model_role"] = "live"
    model["head_kind"] = "prob"
    for k in (
        "y_spec",
        "extra_features",
        "feat_labels",
        "horizon_mode",
        "target",
        "minute_tau_hm",
        "tau_grid",
        "head_kind",
    ):
        research_model[k] = model.get(k)

    report = {
        "success": True,
        "task": f"{h}_ridge",
        "stock_count": len(enriched),
        "sample_count": len(ys),
        "oos": oos,
        "return_model": model,
        "return_model_research": research_model,
        "schema": schema,
        "target": target,
        "minute_tau_hm": live_hm,
        "tau_grid": list(grid),
        "dual_score_head": dual_score_head,
        "head_kind": "prob",
        "cross_section_zscore": use_cs,
        "note": (
            f"ŷ_τ{num}=P(mean(price(τ⊕{offsets[0]}/{offsets[1]}/{offsets[2]}))/price(τ)−1>0)。"
            "进 ŷ_τw 投票；个股旁路闸已下线。不进 C_τ / ranking。"
        ),
    }
    attach_holdout_meta(report, split_meta)
    report["promote_gate"] = horizon_promote_gate(report)
    return report


# ---------------------------------------------------------------------------
# 模型路径 / 持久化 / 加载 / 预测 / 解释
# ---------------------------------------------------------------------------


def ridge_model_path(head: str) -> str:
    from core.paths import LIVE_DIR

    h = _horizon_ridge_config(head)["head"]
    return os.path.join(LIVE_DIR, f"{h}_ridge_model.json")


def ridge_last_report_path(head: str) -> str:
    from core.paths import LIVE_DIR

    h = _horizon_ridge_config(head)["head"]
    return os.path.join(LIVE_DIR, f"{h}_ridge_last_report.json")


def _mtime_or_missing(path: str) -> float:
    try:
        return os.path.getmtime(path) if os.path.isfile(path) else -1.0
    except OSError:
        return -1.0


def save_ridge_last_report(head: str, report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    if not isinstance(report.get("return_model"), dict):
        return
    from core.research.holdout import stamp_fitted_at

    stamp_fitted_at(report)
    path = ridge_last_report_path(head)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)
    _RIDGE_MODEL_CACHE.pop(_horizon_ridge_config(head)["head"], None)


def load_ridge_last_report(head: str) -> Optional[Dict[str, Any]]:
    path = ridge_last_report_path(head)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001
        logger.debug("load %s last_report failed", head, exc_info=True)
        return None
    if not isinstance(doc, dict) or not doc.get("success"):
        return None
    if not isinstance(doc.get("return_model"), dict):
        return None
    return doc


def load_ridge_model(head: str, *, role: Optional[str] = None) -> Optional[Dict[str, Any]]:
    from core.research.holdout import (
        MODEL_ROLE_RESEARCH,
        current_scoring_model_role,
        load_research_promoted_json,
    )

    h = _horizon_ridge_config(head)["head"]
    role_n = role if role is not None else current_scoring_model_role()
    if role_n == MODEL_ROLE_RESEARCH:
        return load_research_promoted_json(ridge_model_path(h))
    model_p = ridge_model_path(h)
    report_p = ridge_last_report_path(h)
    key = (_mtime_or_missing(model_p), _mtime_or_missing(report_p))
    cached = _RIDGE_MODEL_CACHE.get(h)
    if cached is not None and cached[0] == key:
        return cached[1]
    doc: Optional[Dict[str, Any]] = None
    if os.path.isfile(model_p):
        try:
            with open(model_p, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get("return_model"), dict):
                doc = loaded
        except Exception:  # noqa: BLE001
            logger.debug("load %s model failed", h, exc_info=True)
    if doc is None:
        fallback = load_ridge_last_report(h)
        if fallback:
            out = dict(fallback)
            out["_shadow"] = True
            doc = out
    _RIDGE_MODEL_CACHE[h] = (key, doc)
    return doc


def persist_ridge_model(
    head: str,
    report: Dict[str, Any],
    *,
    note: str = "",
    force: bool = False,
    role: str = "live",
) -> Dict[str, Any]:
    from core.research.holdout import (
        MODEL_ROLE_RESEARCH,
        model_fit_id,
        research_model_path,
        select_persist_return_model,
        stamp_fitted_at,
    )

    cfg = _horizon_ridge_config(head)
    h = cfg["head"]
    schema = cfg["schema"]
    dual_score_head = cfg["dual_score_head"]

    if not report.get("success"):
        return {"success": False, "error": report.get("error") or "no report"}
    role_n, rm = select_persist_return_model(report, role=role)
    if not isinstance(rm, dict):
        return {"success": False, "error": "return_model missing"}
    gate = horizon_promote_gate(report)
    if not force and not gate.get("ok"):
        return {
            "success": False,
            "error": "promote 未过闸：" + "；".join(gate.get("blockers") or []),
            "promote_gate": gate,
        }
    from core.numbers import now_iso_utc

    stamp_fitted_at(report)
    fitted_at = model_fit_id(report)
    doc = {
        "success": True,
        "fitted_at": fitted_at,
        "promoted_at": now_iso_utc(),
        "note": note or f"{h}_ridge promote",
        "return_model": rm,
        "oos": report.get("oos"),
        "sample_count": report.get("sample_count"),
        "stock_count": report.get("stock_count"),
        "schema": report.get("schema") or schema,
        "minute_tau_hm": report.get("minute_tau_hm") or rm.get("minute_tau_hm"),
        "tau_grid": report.get("tau_grid") or rm.get("tau_grid"),
        "promote_gate": gate,
        "model_role": role_n,
        "fit_end": report.get("fit_end"),
        "eval_start": report.get("eval_start"),
        "holdout_trading_days": report.get("holdout_trading_days"),
        "dual_score_head": dual_score_head,
    }
    path = (
        research_model_path(ridge_model_path(h))
        if role_n == MODEL_ROLE_RESEARCH
        else ridge_model_path(h)
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, doc)
    _RIDGE_MODEL_CACHE.pop(h, None)
    out = dict(doc)
    out["path"] = path
    return out


def predict_ridge_from_features(
    head: str,
    features: Dict[str, Optional[float]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    """开盘 Z + 前缀分钟 → ŷ_τ{num} = P(窗收益>0)。"""
    doc = model_doc if model_doc is not None else load_ridge_model(head)
    if not doc:
        return None
    rm = doc.get("return_model") or {}
    preds = predict_p_up_rows(rm, [features or {}])
    if not preds or preds[0] is None:
        return None
    return float(preds[0])


def explain_ridge_prediction(
    head: str,
    features: Optional[Dict[str, Any]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    from core.research.tc_ridge import explain_tau_prediction

    h = _horizon_ridge_config(head)["head"]
    doc = model_doc if model_doc is not None else load_ridge_model(h)
    expl = explain_tau_prediction(features, model_doc=doc)
    return stamp_horizon_explain(expl, head=h, model_doc=doc)


def clear_ridge_model_cache() -> None:
    _RIDGE_MODEL_CACHE.clear()
