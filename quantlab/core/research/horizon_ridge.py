"""ŷ_τ{t30..t90} Ridge 泛化模块：开盘 Z + 开→τ 收益/截面 + 序列特征 → P(窗收益>0)。

替代 ``core/research/t30_ridge.py … t90_ridge.py``，所有 horizon 共享一套实现，
通过 ``head`` 参数（"t30"/"t45"/"t60"/"t75"/"t90"）区分。

盘中写 y_τ{num}=p_up。进 ŷ_τw 投票；个股闸与入场已下线。不进 fuse。
训练决策钟 09:35…11:00（仅分钟钟；与 ŷ_τc 的 09:30 开盘点分离）；窗外不训、不预。
Ridge / LightGBM 同用 alpha_τh = z_τh_* ∪ raw_ath_*（不含 OC 路径形状）。
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
    select_horizon_backend,
    stamp_horizon_explain,
    train_eval_horizon_prob,
)
from core.research.tc_ridge import (
    _theme_counts,
    _theme_trigger_sensitivity,
    build_tau_panels_from_bars,
)
from core.signal.factors.z_th import (
    Z_TH_FIT_DROP_ALIASES,
    Z_TH_FIT_FEATURES,
    stamp_z_th_rows,
    z_th_feat_labels,
)
from core.signal.minute_tau_feats import MINUTE_TAU_FEAT_LABELS
from core.signal.minute_tau_grid import (
    DEFAULT_T0_TRAIN_TAU_GRID_5M,
    HORIZON_TAU_START_HM,
    format_shared_tau_formula,
    is_openish_clock,
    normalize_horizon_tau_end_hm,
    sanitize_horizon_tau_grid,
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
    lag_feat_labels = getattr(tau_mod, f"{h.upper()}_LAG_FEAT_LABELS")
    relabel_fn = getattr(tau_mod, f"relabel_tau_panels_as_{h}")
    y_pct_fn = getattr(tau_mod, f"y_{h}_pct")
    train_tau_grid = getattr(grid_mod, f"DEFAULT_{h.upper()}_TRAIN_TAU_GRID", DEFAULT_T0_TRAIN_TAU_GRID_5M)
    offsets = getattr(grid_mod, f"{h.upper()}_LABEL_OFFSETS")
    from core.signal.factors.alpha_th import alpha_th_fit_features_for_head

    ath_features = tuple(alpha_th_fit_features_for_head(h))
    z_features = tuple(Z_TH_FIT_FEATURES) + ath_features
    return {
        "head": h,
        "num": num,
        "z_features": z_features,
        "ath_features": ath_features,
        "lag_feat_labels": dict(lag_feat_labels),
        "relabel_fn": relabel_fn,
        "y_pct_fn": y_pct_fn,
        "train_tau_grid": tuple(train_tau_grid),
        "offsets": tuple(offsets),
        "schema": f"{h}_ridge_v4",
        "target": f"price_tau_plus_{num}",
        "dual_score_head": f"y_{h}",
        "formula": f"mean(price[τ+{offsets[0]}m],price[τ+{offsets[1]}m],price[τ+{offsets[2]}m])/price[τ]-1",
        "feature_pack": "alpha_τh",
        "feature_pack_formula": "z_τh_* ∪ raw_ath_*",
    }


# ---------------------------------------------------------------------------
# 字段读写：pick_y_hat / pick_y_label / write_y_hat
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


def clear_y_hat(head: str, dest: Dict[str, Any]) -> None:
    """截面 z 不完整时丢掉该头，避免沿用上一次写上的 p_up。"""
    if not isinstance(dest, dict):
        return
    cfg = _horizon_ridge_config(head)
    h = cfg["head"]
    num = cfg["num"]
    for key in (
        f"predicted_score_{h}",
        f"y_{h}_hat",
        f"y_{h}",
        f"y_\u03c4{num}",
        f"y_spec_\u03c4{num}",
        f"y_spec_{h}",
        f"y_{h}_source",
        f"y_\u03c4{num}_source",
    ):
        dest.pop(key, None)


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


def horizon_realized_pct(head: str, price_tau: Any, price_tau_n: Any) -> Optional[float]:
    cfg = _horizon_ridge_config(head)
    v = cfg["y_pct_fn"](price_tau, price_tau_n)
    return round(float(v), 4) if v is not None else None


# ---------------------------------------------------------------------------
# 面板：enrich → float64 紧凑矩阵；双训 LightGBM + Ridge 共用一份
# ---------------------------------------------------------------------------


def _resolve_horizon_clocks(
    head: str,
    *,
    tau_hm: str = "10:30",
    tau_grid: Optional[Sequence[str]] = None,
) -> Tuple[str, List[str], Any]:
    cfg = _horizon_ridge_config(head)
    raw_live = str(tau_hm or "").strip() or HORIZON_TAU_START_HM
    live_hm = (
        normalize_horizon_tau_end_hm(raw_live, default=HORIZON_TAU_START_HM)
        or HORIZON_TAU_START_HM
    )
    if is_openish_clock(raw_live):
        live_hm = HORIZON_TAU_START_HM
    grid = sanitize_horizon_tau_grid(
        tau_grid if tau_grid is not None else cfg.get("train_tau_grid"),
        fallback=list(cfg.get("train_tau_grid") or ()) or None,
    )
    if live_hm not in grid and grid:
        # 终点钟须落在网格内；否则抬到网格末档
        live_hm = str(grid[-1])
    return live_hm, grid, cfg


def _is_materialize_tree_fail(tree_rep: Any) -> bool:
    """仅认明确的物化契约；避免普通 tree 报错误伤整单。"""
    if not isinstance(tree_rep, dict) or tree_rep.get("success"):
        return False
    if tree_rep.get("error_code") == "materialize_required":
        return True
    err = str(tree_rep.get("error") or "")
    # 正式文案：请先物化 L2：…；裸 "cube miss"  alone 不抬成整单失败
    return "请先物化" in err or "materialize_required" in err


def _dual_materialize_fail_report(
    ridge_rep: Dict[str, Any],
    *,
    error: str,
    hint: str = "分钟线物化",
    layer: str = "l2",
    clocks: Any = None,
    error_code: str = "materialize_required",
) -> Dict[str, Any]:
    """窗头缺分钟 L2：整单失败（禁止 Ridge 假成功 + LightGBM 空）。"""
    oos_ridge = ridge_rep.get("oos") if isinstance(ridge_rep.get("oos"), dict) else {}
    ridge_rm = ridge_rep.get("return_model")
    out = dict(ridge_rep)
    out.update(
        {
            "success": False,
            "ok": False,
            "error_code": str(error_code or "materialize_required"),
            "error": error,
            "hint": hint or "分钟线物化",
            "layer": layer or "l2",
            "clocks": clocks,
            "tree_success": False,
            "tree_error": error,
            "ridge_oos": oos_ridge,
            "recommended_backend": "ridge",
            "backend": "ridge",
            "backends": (
                {
                    "ridge": {
                        "return_model": ridge_rm,
                        "return_model_research": ridge_rep.get("return_model_research")
                        or ridge_rm,
                    }
                }
                if isinstance(ridge_rm, dict)
                else {}
            ),
        }
    )
    return out


def prepare_horizon_compact(
    head: str,
    stock_bars: Sequence[Dict[str, Any]] = (),
    *,
    enriched_panels: Optional[Sequence[Dict[str, Any]]] = None,
    compact_panel: Optional[Dict[str, Any]] = None,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    tau_hm: str = "10:30",
    tau_grid: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """解析/建面板并折成 ``HorizonCompact``。

    优先 ``compact_panel``，其次 ``enriched_panels``（折完清空），否则从
    ``stock_bars`` 建 enrich 再折。返回 ``{compact, live_hm, tau_grid, head}``。
    """
    from core.research.horizon_compact import fold_horizon_enriched

    live_hm, grid, cfg = _resolve_horizon_clocks(
        head, tau_hm=tau_hm, tau_grid=tau_grid
    )
    z_features = cfg["z_features"]
    if compact_panel is not None:
        compact = compact_panel
    elif enriched_panels is not None:
        compact = fold_horizon_enriched(
            list(enriched_panels), z_features, clear_panels=True
        )
    else:
        bundle = build_horizon_enriched_panels(
            head,
            stock_bars,
            min_history=min_history,
            gap_trigger_pct=gap_trigger_pct,
            tau_hm=tau_hm,
            tau_grid=tau_grid,
        )
        live_hm = str(bundle.get("live_hm") or live_hm)
        grid = list(bundle.get("tau_grid") or grid)
        compact = fold_horizon_enriched(
            list(bundle.get("enriched") or []),
            z_features,
            clear_panels=True,
        )
    return {
        "compact": compact,
        "live_hm": live_hm,
        "tau_grid": list(grid),
        "head": cfg["head"],
    }


def enrich_horizon_raw_panels(
    head: str,
    raw_panels: Sequence[Dict[str, Any]],
    *,
    gap_trigger_pct: float = 2.0,
    already_breadth: bool = False,
) -> List[Dict[str, Any]]:
    """对 raw 面板做截面广度（可选）+ relabel + stamp ``raw_ath_*``。"""
    from core.research.tau_panel import attach_cross_section_breadth

    cfg = _horizon_ridge_config(head)
    h = cfg["head"]
    relabel_fn = cfg["relabel_fn"]
    raw_list = list(raw_panels or [])
    if not already_breadth:
        raw_list = attach_cross_section_breadth(
            raw_list, gap_trigger_pct=gap_trigger_pct
        )
    enriched = relabel_fn(raw_list)
    # L1 stitch 为补标签暂留的分钟线，relabel 后即可丢
    for p in enriched:
        if isinstance(p, dict):
            p.pop("minute_bars", None)
    try:
        from core.signal.factors.alpha_th import stamp_ath_on_panels

        stamp_ath_on_panels(enriched, h)
    except Exception:  # noqa: BLE001
        logger.debug("%s stamp raw_ath_ lags failed", h, exc_info=True)
    try:
        for p in enriched:
            if isinstance(p, dict):
                stamp_z_th_rows(p.get("xs") or [])
    except Exception:  # noqa: BLE001
        logger.debug("%s stamp z_τh_* failed", h, exc_info=True)
    return enriched


def build_horizon_enriched_panels(
    head: str,
    stock_bars: Sequence[Dict[str, Any]],
    *,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    tau_hm: str = "10:30",
    tau_grid: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """建 ŷ_τ* enrich 面板（relabel + stamp raw_ath_*），并释放分钟线。

    返回 ``{enriched, live_hm, tau_grid, head}``。正式双训请走
    ``prepare_horizon_compact``（折 float64 后共用）；本函数仅供建 raw/enrich。
    """
    live_hm, grid, cfg = _resolve_horizon_clocks(
        head, tau_hm=tau_hm, tau_grid=tau_grid
    )
    h = cfg["head"]
    raw = build_tau_panels_from_bars(
        stock_bars,
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        tau_hm=live_hm,
        tau_grid=grid,
        include_alpha_th=True,
        attach_breadth=True,
        label_heads=(h,),
    )
    enriched = enrich_horizon_raw_panels(
        head, raw, gap_trigger_pct=gap_trigger_pct, already_breadth=True
    )
    for item in stock_bars or []:
        if isinstance(item, dict):
            item.pop("minute_bars", None)
    return {
        "enriched": enriched,
        "live_hm": live_hm,
        "tau_grid": list(grid),
        "head": h,
    }


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
    enriched_panels: Optional[Sequence[Dict[str, Any]]] = None,
    compact_panel: Optional[Dict[str, Any]] = None,
    calendar_dates: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """池化拟合 ŷ_τ{num}=p_up + 时间 OOS。标签 = I(mean(price(τ⊕off1/off2/off3))/price(τ)−1 > 0)。

    ``cross_section_zscore``：True=日截面 z；False=训练样本内全局 μ/σ。
    ``enriched_panels``：双训预建面板（跳过再灌分钟线；入模前折 float64 并清空）。
    ``compact_panel``：已折好的 ``HorizonCompact``（双训共用，优先于 enriched）。
    ``calendar_dates``：Holdout 共用日历；缺省从 ``stock_bars`` 抽（抽完不改 bars）。
    """
    from core.research.horizon_compact import (
        compact_subset,
        cs_groups_from_dates_metas,
        summarize_cs_groups,
        feature_fill_rates_matrix,
    )
    from core.research.tau_panel import theme_sample_weights

    cfg = _horizon_ridge_config(head)
    h = cfg["head"]
    num = cfg["num"]
    z_features = cfg["z_features"]
    offsets = cfg["offsets"]
    schema = cfg["schema"]
    target = cfg["target"]
    dual_score_head = cfg["dual_score_head"]
    formula = cfg["formula"]
    lag_feat_labels = cfg["lag_feat_labels"]

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
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"{h} 样本不足 n={len(ys)}（需≥20 且需 τ⊕{offsets[0]}/{offsets[1]}/{offsets[2]} 三根均价）",
            "task": f"{h}_ridge",
            "sample_count": len(ys),
            "stock_count": stock_count,
            "tau_grid": list(grid),
            "minute_tau_hm": live_hm,
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
    feat_names = [k for k in z_features if k not in Z_TH_FIT_DROP_ALIASES]
    xs_tr, ys_tr, metas_tr, dates_tr = compact_subset(
        compact, train_idx, names=feat_names
    )
    xs_te, ys_te, metas_te, dates_te = compact_subset(
        compact, test_idx, names=feat_names
    )

    use_cs = bool(cross_section_zscore)
    # PIT：CS 组 = date|τ（同日多钟不混组）；Holdout 仍按交易日切折
    # Live 亦须按钟；按日混钟会抬 Holdout，但不可上线（lookahead）。
    groups_tr = (
        cs_groups_from_dates_metas(dates_tr, metas_tr) if use_cs else None
    )
    groups_te = (
        cs_groups_from_dates_metas(dates_te, metas_te) if use_cs else None
    )
    l2_rows_dropped = {"train": 0, "test": 0, "miss": []}
    if use_cs and groups_tr is not None and groups_te is not None:
        from core.research.materialize.cube_z import filter_rows_to_day_cube

        n_tr, n_te = len(groups_tr), len(groups_te)
        groups_tr, kept_tr, miss_tr = filter_rows_to_day_cube(
            groups_tr,
            [xs_tr, ys_tr, metas_tr, dates_tr],
            clock=live_hm,
            require_any=True,
        )
        xs_tr, ys_tr, metas_tr, dates_tr = kept_tr
        groups_te, kept_te, miss_te = filter_rows_to_day_cube(
            groups_te,
            [xs_te, ys_te, metas_te, dates_te],
            clock=live_hm,
            require_any=False,
        )
        xs_te, ys_te, metas_te, dates_te = kept_te
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
    weights = (
        theme_sample_weights(metas_tr, theme_boost=theme_boost)
        if use_theme_weights
        else None
    )
    cs_z_audit = {
        "train": summarize_cs_groups(groups_tr),
        "test": summarize_cs_groups(groups_te),
    }
    if use_cs and (cs_z_audit["train"].get("mode") or "") in ("day", "mixed"):
        logger.warning(
            "%s CS groups not pure PIT train=%s test=%s "
            "(day-pooled z is not live-safe)",
            h,
            cs_z_audit["train"],
            cs_z_audit["test"],
        )
    packed = train_eval_horizon_prob(
        xs_tr,
        ys_tr,
        xs_te,
        ys_te,
        metas_te,
        feat_names=feat_names,
        ridge_lambda=ridge_lambda,
        weights=weights,
        dates_tr=groups_tr,
        dates_te=groups_te,
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
            "l2_rows_dropped": l2_rows_dropped,
        }
    )
    try:
        oos["feature_fill"] = feature_fill_rates_matrix(
            compact.get("X"), compact.get("names") or z_features
        )
    except Exception:  # noqa: BLE001
        logger.debug("%s feature_fill failed", h, exc_info=True)

    oos["label_dist"] = {
        "n_labeled": len(ys),
        "y_mean": round(sum(ys) / float(len(ys)), 4) if ys else None,
        "n_unique_days": len({str(d)[:10] for d in dates}),
        "rows_per_day": round(len(ys) / max(1, len({str(d)[:10] for d in dates})), 2),
    }

    model = dict(packed.get("model") or {})
    model["horizon_mode"] = target
    model["target"] = target
    y_formula = format_shared_tau_formula(formula, grid or [live_hm])
    from core.signal.factors.alpha_th import ATH_FEAT_LABELS, alpha_th_report_meta

    ath_features = tuple(cfg.get("ath_features") or ())
    n_ath = sum(1 for k in z_features if str(k).startswith("raw_ath_"))
    pack_meta = alpha_th_report_meta(n_alpha_th_features=n_ath or len(ath_features))

    model["y_spec"] = {
        "formula": y_formula,
        "unit": "prob",
        "label": f"I(mean(price(τ⊕{offsets[0]}/{offsets[1]}/{offsets[2]}))/price(τ)−1 > 0)",
        "tau": live_hm,
        "tau_grid": list(grid),
        "feature_pack": pack_meta["feature_pack"],
        "feature_pack_formula": pack_meta["feature_pack_formula"],
        "note": (
            f"X = alpha_τh（z_τh_* ∪ raw_ath_*）；短窗/会话/本头 H_* 在 raw_ath_*；"
            f"LightGBM/Ridge 同 X。"
            f"ŷ_τ{num}=P(窗收益>0)；做 T 用 p_agree（正T=p_up，反T=1−p_up）；不进 fuse。"
        ),
    }
    model["extra_features"] = list(z_features)
    from core.research.tau_panel import TAU_LAG_FEAT_LABELS

    model["feat_labels"] = z_th_feat_labels(
        {
            **dict(MINUTE_TAU_FEAT_LABELS),
            **dict(TAU_LAG_FEAT_LABELS),
            **lag_feat_labels,
            **dict(ATH_FEAT_LABELS),
        }
    )
    model["minute_tau_hm"] = live_hm
    model["tau_grid"] = list(grid)
    model["model_role"] = "research"
    model["head_kind"] = "prob"
    model["schema"] = schema
    model.update(pack_meta)

    report = {
        "success": True,
        "task": f"{h}_ridge",
        "stock_count": stock_count,
        "sample_count": len(ys),
        "oos": oos,
        "return_model": model,
        "return_model_research": model,
        "schema": schema,
        "target": target,
        "minute_tau_hm": live_hm,
        "tau_grid": list(grid),
        "dual_score_head": dual_score_head,
        "head_kind": "prob",
        "cross_section_zscore": use_cs,
        "cs_z": cs_z_audit,
        "note": (
            f"ŷ_τ{num}=P(mean(price(τ⊕{offsets[0]}/{offsets[1]}/{offsets[2]}))/price(τ)−1>0)。"
            "特征包 alpha_τh=z_τh_*∪raw_ath_*。"
            "CS-z=date|τ PIT（与 live 同口径；按日混钟不可上线）。"
            "进 ŷ_τw 投票；个股旁路闸已下线。不进 fuse。"
        ),
        **pack_meta,
    }
    oos["cs_z"] = cs_z_audit
    attach_holdout_meta(report, split_meta)
    report["backend"] = "ridge"
    report["recommended_backend"] = "ridge"
    report["backends"] = {
        "ridge": {
            "return_model": model,
            "return_model_research": model,
        }
    }
    report["promote_gate"] = horizon_promote_gate(report)
    return report


def _backend_pack_from_report(
    report: Dict[str, Any], backend: str
) -> Optional[Dict[str, Any]]:
    backends = report.get("backends") if isinstance(report.get("backends"), dict) else {}
    pack = backends.get(backend) if isinstance(backends, dict) else None
    if isinstance(pack, dict) and isinstance(pack.get("return_model"), dict):
        return pack
    if backend == "ridge" and isinstance(report.get("return_model"), dict):
        return {
            "return_model": report.get("return_model"),
            "return_model_research": report.get("return_model_research")
            or report.get("return_model"),
        }
    if backend == "tree":
        tree_rm = report.get("tree_return_model")
        if isinstance(tree_rm, dict):
            return {"return_model": tree_rm, "return_model_research": tree_rm}
    return None


def fit_horizon_dual_report(
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
    n_estimators: Optional[int] = None,
    max_depth: Optional[int] = None,
    learning_rate: Optional[float] = None,
    subsample: Optional[float] = None,
    enriched_panels: Optional[Sequence[Dict[str, Any]]] = None,
    compact_panel: Optional[Dict[str, Any]] = None,
    calendar_dates: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """同窗双训 LightGBM + Ridge（**串行**：先 Ridge 再 LightGBM）；Holdout AUC 择优（打平→ridge）。

    产出 ``backends`` / ``recommended_backend``；顶层 ``return_model`` 为胜出侧。
    LightGBM / Ridge 同 X（alpha_τh）。面板只建一次（或传入 ``enriched_panels`` /
    ``compact_panel``），折 float64 后共用；分钟线与日线 ``bars`` 在抽日历后释放。
    """
    from core.research.horizon_compact import (
        release_stock_bar_payloads,
        take_calendar_and_release,
    )
    from core.research.horizon_lgb import _delta_oos
    from core.research.horizon_tree import fit_horizon_tree_report

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

    cal = (
        list(calendar_dates)
        if calendar_dates is not None
        else take_calendar_and_release(stock_bars)
    )
    if calendar_dates is not None:
        release_stock_bar_payloads(stock_bars)

    ridge_kw: Dict[str, Any] = dict(
        ridge_lambda=ridge_lambda,
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        theme_boost=theme_boost,
        holdout_trading_days=holdout_trading_days,
        use_theme_weights=use_theme_weights,
        tau_hm=live_hm,
        tau_grid=grid,
        cross_section_zscore=cross_section_zscore,
        compact_panel=compact,
        calendar_dates=cal,
    )
    ridge_rep = fit_horizon_ridge_report(head, stock_bars, **ridge_kw)
    if not ridge_rep.get("success"):
        return ridge_rep

    tree_kw: Dict[str, Any] = dict(
        ridge_lambda=ridge_lambda,
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        theme_boost=theme_boost,
        holdout_trading_days=holdout_trading_days,
        use_theme_weights=use_theme_weights,
        tau_hm=live_hm,
        tau_grid=grid,
        backend="lightgbm",
        compact_panel=compact,
        calendar_dates=cal,
    )
    if n_estimators is not None:
        tree_kw["n_estimators"] = n_estimators
    if max_depth is not None:
        tree_kw["max_depth"] = max_depth
    if learning_rate is not None:
        tree_kw["learning_rate"] = learning_rate
    if subsample is not None:
        tree_kw["subsample"] = subsample

    from core.research.materialize.consumer import MaterializeRequired

    tree_rep: Dict[str, Any]
    try:
        tree_rep = fit_horizon_tree_report(head, stock_bars, **tree_kw)
    except MaterializeRequired as exc:
        payload = exc.to_dict()
        return _dual_materialize_fail_report(
            ridge_rep,
            error=str(payload.get("error") or exc),
            hint=str(payload.get("hint") or "分钟线物化"),
            layer=str(payload.get("layer") or "l2"),
            clocks=payload.get("clocks"),
            error_code=str(payload.get("error_code") or "materialize_required"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s dual tree fit failed: %s", head, exc, exc_info=True)
        tree_rep = {"success": False, "error": str(exc)}

    cfg = _horizon_ridge_config(head)
    h = cfg["head"]
    num = cfg["num"]
    oos_ridge = ridge_rep.get("oos") if isinstance(ridge_rep.get("oos"), dict) else {}
    oos_tree = (
        tree_rep.get("oos")
        if isinstance(tree_rep, dict) and isinstance(tree_rep.get("oos"), dict)
        else {}
    )
    # tree 失败时 ridge_oos 可能仍在
    if not oos_tree and isinstance(tree_rep, dict):
        oos_tree = {}

    if _is_materialize_tree_fail(tree_rep):
        tree_err = str(tree_rep.get("error") or "请先物化 L2")
        tree_hint = (
            str(tree_rep.get("hint") or "").strip()
            if isinstance(tree_rep, dict)
            else ""
        )
        return _dual_materialize_fail_report(
            ridge_rep,
            error=tree_err,
            hint=tree_hint or "分钟线物化",
            layer=str((tree_rep or {}).get("layer") or "l2"),
            clocks=tree_rep.get("clocks") if isinstance(tree_rep, dict) else None,
            error_code=str(
                (tree_rep or {}).get("error_code") or "materialize_required"
            ),
        )

    recommended = select_horizon_backend(
        oos_tree if tree_rep.get("success") else None,
        oos_ridge,
    )
    if recommended == "tree" and not tree_rep.get("success"):
        recommended = "ridge"

    ridge_rm = ridge_rep.get("return_model")
    tree_rm = None
    if isinstance(tree_rep, dict):
        tree_rm = tree_rep.get("tree_return_model") or tree_rep.get("return_model")
    backends: Dict[str, Any] = {}
    if isinstance(ridge_rm, dict):
        backends["ridge"] = {
            "return_model": ridge_rm,
            "return_model_research": ridge_rep.get("return_model_research") or ridge_rm,
        }
    if isinstance(tree_rm, dict):
        backends["tree"] = {
            "return_model": tree_rm,
            "return_model_research": tree_rm,
        }

    winner_oos = oos_tree if recommended == "tree" else oos_ridge
    merged_oos: Dict[str, Any] = dict(winner_oos) if isinstance(winner_oos, dict) else {}
    if isinstance(oos_ridge, dict):
        merged_oos["ridge"] = oos_ridge
    if isinstance(oos_tree, dict) and oos_tree:
        merged_oos["tree"] = oos_tree
    merged_oos["recommended_backend"] = recommended
    # 顶层训/测行数：胜出侧缺时从另一侧补（同窗双训样本相同）
    for side in (oos_ridge, oos_tree):
        if not isinstance(side, dict):
            continue
        if merged_oos.get("n_train") is None and side.get("n_train") is not None:
            merged_oos["n_train"] = side.get("n_train")
        if merged_oos.get("n_test") is None and side.get("n_test") is not None:
            merged_oos["n_test"] = side.get("n_test")
        if merged_oos.get("holdout_trading_days") is None and side.get(
            "holdout_trading_days"
        ) is not None:
            merged_oos["holdout_trading_days"] = side.get("holdout_trading_days")


    pack = backends.get(recommended) if recommended in backends else backends.get("ridge")
    winner_rm = None
    if isinstance(pack, dict):
        winner_rm = pack.get("return_model")
    if not isinstance(winner_rm, dict):
        winner_rm = ridge_rm if isinstance(ridge_rm, dict) else None
        recommended = "ridge"

    report: Dict[str, Any] = dict(ridge_rep)
    report.update(
        {
            "task": f"{h}_ridge",
            "schema": ridge_rep.get("schema") or cfg["schema"],
            "backend": recommended,
            "recommended_backend": recommended,
            "backends": backends,
            "oos": merged_oos,
            "return_model": winner_rm,
            "return_model_research": winner_rm,
            "ridge_oos": oos_ridge,
            "tree_oos": oos_tree if oos_tree else None,
            "delta_vs_ridge": (
                _delta_oos(oos_tree, oos_ridge)
                if oos_tree and oos_ridge
                else None
            ),
            "tree_success": bool(tree_rep.get("success")),
            "tree_error": None
            if tree_rep.get("success")
            else (tree_rep.get("error") if isinstance(tree_rep, dict) else "tree fit failed"),
            # 始终保留 Tree gain（Ridge 胜出时因子表仍画 gain% 旁列）
            "feature_importance": (
                tree_rep.get("feature_importance")
                if isinstance(tree_rep, dict)
                and isinstance(tree_rep.get("feature_importance"), list)
                else ridge_rep.get("feature_importance")
            ),
            "note": (
                f"ŷ_τ{num} 同窗双训 LightGBM + Ridge；Holdout AUC 择优"
                f"（打平→ridge）→ recommended={recommended}。"
                "特征包 alpha_τh=z_τh_*∪raw_ath_*；LightGBM/Ridge 同 X。"
                "进 ŷ_τw 投票；不进 fuse。"
            ),
        }
    )
    from core.signal.factors.alpha_th import alpha_th_report_meta

    if not report.get("feature_pack"):
        n_ath = int(ridge_rep.get("n_alpha_th_features") or 0)
        if not n_ath:
            n_ath = len(tuple(cfg.get("ath_features") or ()))
        report.update(alpha_th_report_meta(n_alpha_th_features=n_ath))
    if isinstance(tree_rep, dict) and isinstance(tree_rep.get("timing"), dict):
        rt = report.get("timing") if isinstance(report.get("timing"), dict) else {}
        report["timing"] = {**rt, "tree": tree_rep.get("timing")}
    if isinstance(tree_rm, dict):
        report["tree_return_model"] = tree_rm
        # 便于 get_model / 因子表从 backends.tree 取 gain
        tree_pack = backends.get("tree")
        if isinstance(tree_pack, dict) and isinstance(
            tree_rep.get("feature_importance"), list
        ):
            tree_pack = dict(tree_pack)
            rm_t = dict(tree_pack.get("return_model") or tree_rm)
            rm_t["feature_importance"] = tree_rep.get("feature_importance")
            tree_pack["return_model"] = rm_t
            tree_pack["return_model_research"] = rm_t
            backends["tree"] = tree_pack
            report["backends"] = backends
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
    """启用研究=报告→研究槽（AUC 择优）；启用执行=研究槽→执行槽（晋级）。"""
    from core.research.holdout import (
        MODEL_ROLE_LIVE,
        MODEL_ROLE_RESEARCH,
        model_fit_id,
        normalize_model_role,
        require_research_suite,
        research_model_path,
        select_fit_return_model,
        stamp_fitted_at,
        stamp_live_suite_doc,
    )

    cfg = _horizon_ridge_config(head)
    h = cfg["head"]
    schema = cfg["schema"]
    dual_score_head = cfg["dual_score_head"]
    live_path = ridge_model_path(h)
    role_n = normalize_model_role(role)

    if role_n == MODEL_ROLE_LIVE:
        research_doc, err = require_research_suite(live_path)
        if err:
            return err
        doc = stamp_live_suite_doc(
            research_doc, note=note or f"{h}_ridge research → live promote"
        )
        os.makedirs(os.path.dirname(live_path), exist_ok=True)
        atomic_write_json(live_path, doc)
        _RIDGE_MODEL_CACHE.pop(h, None)
        return {
            "success": True,
            "path": live_path,
            "promoted_at": doc.get("promoted_at"),
            "fitted_at": doc.get("fitted_at"),
            "model_role": MODEL_ROLE_LIVE,
            "backend": doc.get("backend"),
            "recommended_backend": doc.get("recommended_backend") or doc.get("backend"),
            "promoted_from": "research",
        }

    if not report.get("success"):
        return {"success": False, "error": report.get("error") or "no report"}

    oos = report.get("oos") if isinstance(report.get("oos"), dict) else {}
    oos_tree = oos.get("tree") if isinstance(oos.get("tree"), dict) else None
    oos_ridge = oos.get("ridge") if isinstance(oos.get("ridge"), dict) else None
    if oos_ridge is None and oos.get("auc") is not None:
        # 旧扁平报告：整包视为 ridge
        oos_ridge = oos
    backend = select_horizon_backend(oos_tree, oos_ridge)
    if backend == "tree" and not _backend_pack_from_report(report, "tree"):
        backend = "ridge"

    pack = _backend_pack_from_report(report, backend)
    view = dict(report)
    if isinstance(pack, dict):
        view["return_model"] = pack.get("return_model")
        if isinstance(pack.get("return_model_research"), dict):
            view["return_model_research"] = pack.get("return_model_research")
    rm = select_fit_return_model(view)
    if not isinstance(rm, dict):
        return {"success": False, "error": "return_model missing"}

    # 闸看胜出侧 OOS
    gate_view = dict(report)
    gate_view["recommended_backend"] = backend
    gate_view["backend"] = backend
    gate = horizon_promote_gate(gate_view)
    if not force and not gate.get("ok"):
        return {
            "success": False,
            "error": "promote 未过闸：" + "；".join(gate.get("blockers") or []),
            "promote_gate": gate,
            "backend": backend,
            "recommended_backend": backend,
        }
    from core.numbers import now_iso_utc

    stamp_fitted_at(report)
    fitted_at = model_fit_id(report)
    rm = dict(rm)
    rm["model_role"] = MODEL_ROLE_RESEARCH
    # 包级 backend=ridge|tree（择优）；return_model.backend 是引擎名。
    # 勿把 tree 写入引擎字段，否则 predict_tree_raw 只认 lightgbm 会整头空分。
    if backend == "tree":
        rm["backend"] = "lightgbm"
    else:
        rm["backend"] = "ridge"
    doc = {
        "success": True,
        "fitted_at": fitted_at,
        "promoted_at": now_iso_utc(),
        "note": note or f"{h} promote research backend={backend}",
        "return_model": rm,
        "return_model_research": dict(rm),
        "oos": report.get("oos"),
        "sample_count": report.get("sample_count"),
        "stock_count": report.get("stock_count"),
        "schema": report.get("schema") or schema,
        "minute_tau_hm": report.get("minute_tau_hm") or rm.get("minute_tau_hm"),
        "tau_grid": report.get("tau_grid") or rm.get("tau_grid"),
        "promote_gate": gate,
        "model_role": MODEL_ROLE_RESEARCH,
        "fit_end": report.get("fit_end"),
        "eval_start": report.get("eval_start"),
        "holdout_trading_days": report.get("holdout_trading_days"),
        "dual_score_head": dual_score_head,
        "backend": backend,
        "recommended_backend": backend,
    }
    backends = report.get("backends")
    if isinstance(backends, dict) and backends:
        doc["backends"] = backends
    for k in (
        "delta_vs_ridge",
        "ridge_oos",
        "tree_oos",
        "feature_importance",
        "cross_section_zscore",
    ):
        if k in report and report.get(k) is not None:
            doc[k] = report.get(k)
    path = research_model_path(live_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, doc)
    _RIDGE_MODEL_CACHE.pop(h, None)
    # 不再镜像 *_tree_model.json；tree 走启用包 backends.tree

    out = dict(doc)
    out["path"] = path
    return out


def _doc_backend(doc: Optional[Dict[str, Any]]) -> str:
    if not isinstance(doc, dict):
        return "ridge"
    be = str(doc.get("backend") or doc.get("recommended_backend") or "").strip().lower()
    if be in ("tree", "ridge"):
        return be
    rm = doc.get("return_model") if isinstance(doc.get("return_model"), dict) else {}
    rm_be = str(rm.get("backend") or "").strip().lower()
    if rm_be in ("tree", "lightgbm") or (
        isinstance(rm.get("booster"), dict) and not isinstance(rm.get("coefficients"), dict)
    ):
        return "tree"
    return "ridge"


def predict_ridge_from_features(
    head: str,
    features: Dict[str, Optional[float]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    """开盘 Z + 前缀分钟 → ŷ_τ{num} = P(窗收益>0)。

    启用包 ``backend=tree`` 时走树预测。
    """
    doc = model_doc if model_doc is not None else load_ridge_model(head)
    if not doc:
        return None
    from core.signal.factors.z_th import stamp_z_th_aliases

    feats = dict(features or {})
    stamp_z_th_aliases(feats)
    if _doc_backend(doc) == "tree":
        from core.research.horizon_tree import predict_tree_from_features

        return predict_tree_from_features(head, feats, model_doc=doc)
    rm = doc.get("return_model") or {}
    preds = predict_p_up_rows(rm, [feats])
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

    from core.signal.factors.z_th import stamp_z_th_aliases

    h = _horizon_ridge_config(head)["head"]
    doc = model_doc if model_doc is not None else load_ridge_model(h)
    feats = dict(features or {}) if isinstance(features, dict) else {}
    stamp_z_th_aliases(feats)
    if _doc_backend(doc) == "tree":
        from core.research.horizon_tree import explain_tree_return

        expl = explain_tree_return(feats, (doc or {}).get("return_model"))
        return stamp_horizon_explain(expl, head=h, model_doc=doc)
    expl = explain_tau_prediction(feats, model_doc=doc)
    return stamp_horizon_explain(expl, head=h, model_doc=doc)
