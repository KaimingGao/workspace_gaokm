"""按单票 OLS β 相似度聚类，使同组可共用建模、异组各用各的（研究用，不写盘）。

目的：OLS 表现相似的股票进同一组 → 组内池 OLS + 共用小步权；不同组独立建模。
默认宇宙=观察池。流程：逐票全样本 OLS（展示）→ **前段窗口 β** 聚类定组
（中心 ≈ n/5，夹 4～10；auto 时邻域 ±1 × 多种分区配方，按组 ŷ 每票时间尾段
有符号 IC↑ / 前段重拟合误差↓ 选优，ΔOOS 辅门禁）
→ 多票组**全样本**池 OLS → 因子系数(return_model)。异质用探针核对。

``beta_scale``：
- ``feature_zscore``（默认）：列缩尾 + 因子维 z-score
- ``l2``：行 L2 归一只比方向
- ``none``：原始 β（易被极端票主导）
"""


import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from quant.research.cluster_weight_display import (
    _draft_weights_from_ols,
    _public_cluster_ols,
    _return_model_from_ols,
    _weight_suggest_public,
)
from quant.research.factor_ols import (
    clamp_ridge_lambda,
    collect_subscore_forward_panel,
    fit_factor_ols_from_panel,
)

logger = logging.getLogger(__name__)


from quant.research.cluster_report_util import (  # A3
    clamp_n_clusters,
    clamp_watching_limit,
    cluster_speed_policy,
)


def panel_ic_factor_names(
    *,
    respect_regime: bool = False,
    index_bars: Optional[List[dict]] = None,
    cluster_feature_names: Optional[Sequence[str]] = None,
) -> List[str]:
    """组 IC 因子清单：全注册（或 regime 对齐），不绑单票 OLS 入模集。

    大宇宙末日财务快照下，估值/质量在单票时序里近似常数 → 被剔出
    ``_feature_union``；若 IC 表沿用该并集，截面 IC 永远不算这些因子。
    """
    from core.research.panel import _resolve_factor_names

    names = list(
        _resolve_factor_names(
            respect_regime=bool(respect_regime),
            index_bars=index_bars,
            config=None,
        )
    )
    seen = set(names)
    for f in cluster_feature_names or []:
        k = str(f).strip()
        if k and k not in seen:
            seen.add(k)
            names.append(k)
    return names


def merge_cluster_universe(
    watchlist: Sequence[Any],
    holdings: Sequence[Any],
    *,
    watching_limit: int = 8,
    universe_mode: str = "watching",
) -> Dict[str, Any]:
    """构建聚类宇宙。

    ``universe_mode=watching``：观察池前 N（``watching_limit``，默认钳制 3–100）。
    ``holdings``：仅纸面持仓。
    ``union``：观察池前 N ∪ 全部纸面持仓。
    ``watching_all``：全部观察池（旧行为，显式开启）。
    """
    limit = clamp_watching_limit(watching_limit, 8)
    mode = str(universe_mode or "watching").strip().lower()
    if mode in ("paper", "holding", "holdings_only"):
        mode = "holdings"
    if mode in ("watch", "watchlist", "watching_only"):
        mode = "watching"
    if mode in ("watching_full", "all_watching", "full"):
        mode = "watching_all"

    holdings_codes: List[str] = []
    seen_h: set = set()
    for h in holdings or []:
        if isinstance(h, dict):
            c = str(h.get("stock_code") or "").strip()
        else:
            c = str(h or "").strip()
        if not c or c in seen_h:
            continue
        seen_h.add(c)
        holdings_codes.append(c)

    # 去重保序
    watch_all: List[str] = []
    seen_w: set = set()
    for c in watchlist or []:
        code = str(c).strip()
        if not code or code in seen_w:
            continue
        seen_w.add(code)
        watch_all.append(code)

    if mode == "watching_all":
        watching_codes = list(watch_all)
        code_roles = {
            c: {
                "from_watching": True,
                "from_holdings": c in seen_h,
                "holdings_added": False,
            }
            for c in watching_codes
        }
        return {
            "codes": list(watching_codes),
            "watching_codes": list(watching_codes),
            "holdings_codes": list(holdings_codes),
            "holdings_added": [],
            "watching_limit": len(watching_codes),
            "universe_count": len(watching_codes),
            "universe_mode": "watching_all",
            "code_roles": code_roles,
        }

    if mode == "watching":
        watching_codes = watch_all[:limit]
        code_roles = {
            c: {
                "from_watching": True,
                "from_holdings": c in seen_h,
                "holdings_added": False,
            }
            for c in watching_codes
        }
        return {
            "codes": list(watching_codes),
            "watching_codes": list(watching_codes),
            "holdings_codes": list(holdings_codes),
            "holdings_added": [],
            "watching_limit": limit,
            "universe_count": len(watching_codes),
            "universe_mode": "watching",
            "code_roles": code_roles,
        }

    watching_codes = watch_all[:limit]

    if mode == "holdings":
        code_roles = {
            c: {
                "from_watching": False,
                "from_holdings": True,
                "holdings_added": True,
            }
            for c in holdings_codes
        }
        return {
            "codes": list(holdings_codes),
            "watching_codes": [],
            "holdings_codes": list(holdings_codes),
            "holdings_added": list(holdings_codes),
            "watching_limit": limit,
            "universe_count": len(holdings_codes),
            "universe_mode": "holdings",
            "code_roles": code_roles,
        }

    # union
    watch_set = set(watching_codes)
    holdings_added = [c for c in holdings_codes if c not in watch_set]
    codes: List[str] = []
    seen: set = set()
    code_roles: Dict[str, Dict[str, bool]] = {}
    for c in watching_codes + holdings_codes:
        if c not in code_roles:
            code_roles[c] = {
                "from_watching": False,
                "from_holdings": False,
                "holdings_added": False,
            }
        if c in watch_set:
            code_roles[c]["from_watching"] = True
        if c in seen_h:
            code_roles[c]["from_holdings"] = True
        if c not in watch_set and c in seen_h:
            code_roles[c]["holdings_added"] = True
        if c in seen:
            continue
        seen.add(c)
        codes.append(c)
    return {
        "codes": codes,
        "watching_codes": watching_codes,
        "holdings_codes": holdings_codes,
        "holdings_added": holdings_added,
        "watching_limit": limit,
        "universe_count": len(codes),
        "universe_mode": "union",
        "code_roles": code_roles,
    }


from quant.research.cluster_partition import (
    OUTLIER_LABEL,
    _beta_matrix,
    _feature_union,
    _ols_coef_dict,
    _relabel_non_negative,
    agglomerative_cut_by_tau,
    agglomerative_labels,
    apply_beta_scale_transform,
    auto_k_candidates,
    beta_delta_mismatch,
    cluster_beta_vectors,
    cluster_diameter,
    cluster_within_stats,
    coef_vector_from_report,
    default_max_cluster_size,
    default_n_clusters,
    eject_by_group_beta_delta,
    eject_far_from_group_beta,
    fit_beta_scale_transform,
    kmeans_labels,
    promote_outliers_to_singleton_clusters,
    refine_cluster_labels,
    resolve_beta_scale,
    scale_beta_matrix,
    within_dist_tau,
)


def group_ts_ic_panel(
    xs: Sequence[Dict[str, Any]],
    ys: Sequence[float],
    feature_names: Sequence[str],
) -> Dict[str, Any]:
    """单票组回退：堆叠时序 IC（无日截面序列，ICIR 恒 null）。"""
    from core.signal.factors.meta.corr import pearson_with_reason

    rows: List[Dict[str, Any]] = []
    exclusion_reasons: Dict[str, str] = {}
    y_list = list(ys or [])
    x_list = list(xs or [])
    n_pair = min(len(x_list), len(y_list))
    for f in feature_names or []:
        name = str(f).strip()
        if not name:
            continue
        fx: List[float] = []
        fy: List[float] = []
        for i in range(n_pair):
            row = x_list[i]
            if not isinstance(row, dict):
                continue
            v = row.get(name)
            if v is None:
                continue
            try:
                fx.append(float(v))
                fy.append(float(y_list[i]))
            except (TypeError, ValueError):
                continue
        ic, reason = pearson_with_reason(fx, fy)
        if reason:
            exclusion_reasons[name] = reason
        rows.append(
            {
                "factor": name,
                "name": name,
                "ic": round(float(ic), 4) if ic is not None else None,
                "icir": None,
                "sample_count": len(fx),
                "exclusion_reason": reason,
            }
        )
    return {
        "success": True,
        "mode": "group_ts_ic",
        "rows": rows,
        "factors": rows,
        "exclusion_reasons": exclusion_reasons,
        "note": "单票组时序 IC 回退（非截面）；ICIR 不适用",
    }


def group_cs_ic_panel(
    stock_bars: Dict[str, List[dict]],
    feature_names: Sequence[str],
    *,
    horizon_days: int = 3,
    pit_fundamentals: bool = True,
    fundamentals_by_code: Optional[Dict[str, dict]] = None,
) -> Dict[str, Any]:
    """组内按日截面 IC → ICIR（宇宙=组员；与研究池 factor_cs_ic 同口径）。"""
    from core.backtest.factor_cs_ic import compute_factor_cross_section_ic

    names = [str(f).strip() for f in (feature_names or []) if str(f).strip()]
    bars_map = {
        str(c): list(b)
        for c, b in (stock_bars or {}).items()
        if str(c).strip() and b
    }
    n_stocks = len(bars_map)
    if n_stocks < 2:
        empty_rows = [
            {
                "factor": name,
                "name": name,
                "ic": None,
                "icir": None,
                "sample_count": 0,
                "exclusion_reason": "sparse",
            }
            for name in names
        ]
        return {
            "success": True,
            "ok": False,
            "mode": "group_cs_ic",
            "rows": empty_rows,
            "factors": empty_rows,
            "exclusion_reasons": {r["factor"]: "sparse" for r in empty_rows},
            "stock_count": n_stocks,
            "note": "组员不足 2，无法做组内截面 IC",
        }

    min_names = 2 if n_stocks == 2 else 3
    index_bars: Optional[List[dict]] = None
    try:
        from core.data.facade import index_bars_and_source
        from core.ports.market import default_benchmark

        bench = str(default_benchmark("CN") or "000300")
        ib, _ = index_bars_and_source(bench, limit=160)
        index_bars = list(ib or []) or None
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in factor_ols_clusters.py", exc_info=True)
        index_bars = None
    out = compute_factor_cross_section_ic(
        bars_map,
        horizon_days=horizon_days,
        min_history=12,
        max_window=30,
        min_names=min_names,
        fundamentals_by_code=fundamentals_by_code,
        pit_fundamentals=bool(pit_fundamentals),
        factor_names=names or None,
        index_bars=index_bars,
        require_all_factors=True,
    )
    if not out.get("success"):
        empty_rows = [
            {
                "factor": name,
                "name": name,
                "ic": None,
                "icir": None,
                "sample_count": 0,
                "exclusion_reason": "other",
            }
            for name in names
        ]
        return {
            "success": True,
            "ok": False,
            "mode": "group_cs_ic",
            "rows": empty_rows,
            "factors": empty_rows,
            "exclusion_reasons": {r["factor"]: "other" for r in empty_rows},
            "stock_count": n_stocks,
            "min_names": min_names,
            "error": out.get("error"),
            "note": f"组内截面 IC 失败：{out.get('error') or 'unknown'}",
        }

    by_fac = {
        str(r.get("factor") or r.get("name") or ""): r
        for r in (out.get("factors") or [])
        if isinstance(r, dict)
    }
    rows: List[Dict[str, Any]] = []
    exclusion_reasons: Dict[str, str] = {}
    for name in names or list(by_fac.keys()):
        src = by_fac.get(name) or {}
        reason = src.get("exclusion_reason")
        if reason:
            exclusion_reasons[name] = str(reason)
        pear = src.get("pearson") if isinstance(src.get("pearson"), dict) else {}
        n_days = src.get("sample_count")
        if n_days is None:
            n_days = pear.get("day_count")
        icir = src.get("icir")
        if icir is None:
            icir = pear.get("icir")
        ic = src.get("ic")
        if ic is None:
            ic = pear.get("ic_mean")
        rows.append(
            {
                "factor": name,
                "name": name,
                "ic": ic,
                "icir": icir,
                "sample_count": int(n_days or 0),
                "exclusion_reason": reason,
                "pearson": src.get("pearson"),
                "spearman": src.get("spearman"),
            }
        )
    return {
        "success": True,
        "ok": bool(out.get("ok")),
        "mode": "group_cs_ic",
        "rows": rows,
        "factors": rows,
        "exclusion_reasons": exclusion_reasons,
        "horizon_days": out.get("horizon_days") or horizon_days,
        "min_names": min_names,
        "stock_count": n_stocks,
        "day_count": out.get("day_count"),
        "pit_fundamentals": bool(pit_fundamentals),
        "score_ic": out.get("score_ic"),
        # 日截面 IC 尾段：组头 IC spark（按日对因子 Pearson 取均）
        "daily_tail": out.get("daily_tail") or [],
        "note": "组内按日截面 IC → ICIR（宇宙=组员；非全市场）",
    }


def _member_bars_and_funds(
    members: Sequence[str],
    panel_by_code: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, List[dict]], Dict[str, dict]]:
    bars_map: Dict[str, List[dict]] = {}
    funds: Dict[str, dict] = {}
    for code in members or []:
        c = str(code).strip()
        panel = panel_by_code.get(c) or {}
        bars = panel.get("bars") or []
        if not bars:
            continue
        bars_map[c] = list(bars)
        fund = panel.get("fundamentals")
        if isinstance(fund, dict) and fund:
            funds[c] = fund
    return bars_map, funds


def _cluster_factor_ic_panel(
    members: Sequence[str],
    *,
    panel_by_code: Dict[str, Dict[str, Any]],
    feature_names: Sequence[str],
    horizon_days: int,
    pit_fundamentals: bool,
    all_xs: Sequence[Dict[str, Any]],
    all_ys: Sequence[float],
) -> Dict[str, Any]:
    """多票组：组内日截面 IC→ICIR；单票组：时序 IC 回退。"""
    if len(members) < 2:
        return group_ts_ic_panel(all_xs, all_ys, feature_names)
    bars_map, funds = _member_bars_and_funds(members, panel_by_code)
    return group_cs_ic_panel(
        bars_map,
        feature_names,
        horizon_days=horizon_days,
        pit_fundamentals=pit_fundamentals,
        fundamentals_by_code=funds or None,
    )



def _stock_train_xy(
    panel: Dict[str, Any],
    *,
    holdout_ratio: float = 0.3,
    cut_date: Optional[str] = None,
    min_train: int = 6,
) -> Tuple[List[Dict[str, Any]], List[float], bool]:
    """单票面板 → 时间前段 (xs, ys)。过短则退回全样本，并标记 used_full。

    有 ``cut_date`` + ``dates`` 时按宇宙日历切；否则按票比例切尾。
    """
    from quant.research.partition_loss import (
        _pair_rows,
        split_chrono_holdout_pairs,
        split_panel_by_cut_date,
    )

    xs = list(panel.get("xs") or [])
    ys = list(panel.get("ys") or [])
    dates = list(panel.get("dates") or [])
    cut = str(cut_date or "").strip()[:10]
    if cut and dates and len(dates) >= min(len(xs), len(ys)):
        train_xs, train_ys, _hx, _hy = split_panel_by_cut_date(
            xs, ys, dates, cut_date=cut
        )
        if len(train_ys) < int(min_train):
            return xs, ys, True
        return train_xs, train_ys, False
    pairs = _pair_rows(xs, ys)
    if len(pairs) < max(6, int(min_train)):
        return xs, ys, True
    train, _hold, _ratio = split_chrono_holdout_pairs(
        pairs, holdout_ratio=holdout_ratio
    )
    if len(train) < int(min_train):
        return xs, ys, True
    return [r for r, _ in train], [y for _, y in train], False


def build_train_window_beta_matrix(
    codes: Sequence[str],
    panel_by_code: Dict[str, Dict[str, Any]],
    feature_names: Sequence[str],
    *,
    horizon_days: int,
    ridge_lambda: float,
    collinearity_policy: str,
    respect_regime: bool,
    y_spec: Dict[str, Any],
    use_pit: bool,
    holdout_ratio: float = 0.3,
    cut_date: Optional[str] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """用每票**时间前段**估单票 OLS β，供聚类（避免全样本定组泄漏）。

    返回 (raw β 矩阵 n×p, meta)。系数维对齐 ``feature_names``（缺省 0）。
    有 ``cut_date`` 时与 holdout / 选 k 组池共用宇宙日历切分。
    """
    reports: List[Dict[str, Any]] = []
    n_full_fallback = 0
    n_fit_fail = 0
    for code in codes:
        panel = panel_by_code.get(str(code)) or {}
        train_xs, train_ys, used_full = _stock_train_xy(
            panel, holdout_ratio=holdout_ratio, cut_date=cut_date
        )
        if len(train_ys) < 6:
            n_fit_fail += 1
            reports.append(
                {
                    "success": False,
                    "stock_code": str(code),
                    "coefficients": {},
                    "active_features": [],
                }
            )
            continue
        fit = fit_factor_ols_from_panel(
            train_xs,
            train_ys,
            horizon_days=horizon_days,
            fundamentals_used=bool(use_pit),
            pit_fundamentals=bool(use_pit),
            mode="single",
            stock_codes=[str(code)],
            ridge_lambda=float(ridge_lambda),
            select_ridge=False,
            collinearity_policy=str(collinearity_policy),
            respect_regime=bool(respect_regime),
            y_spec=dict(y_spec or {}),
        )
        if not fit.get("success") and not used_full:
            # 前段失败再试全样本
            full_xs = list(panel.get("xs") or [])
            full_ys = list(panel.get("ys") or [])
            if len(full_ys) >= 6:
                fit = fit_factor_ols_from_panel(
                    full_xs,
                    full_ys,
                    horizon_days=horizon_days,
                    fundamentals_used=bool(use_pit),
                    pit_fundamentals=bool(use_pit),
                    mode="single",
                    stock_codes=[str(code)],
                    ridge_lambda=float(ridge_lambda),
                    select_ridge=False,
                    collinearity_policy=str(collinearity_policy),
                    respect_regime=bool(respect_regime),
                    y_spec=dict(y_spec or {}),
                )
                if fit.get("success"):
                    used_full = True
        if not fit.get("success"):
            n_fit_fail += 1
            reports.append(
                {
                    "success": False,
                    "stock_code": str(code),
                    "coefficients": {},
                    "active_features": [],
                }
            )
            continue
        if used_full:
            n_full_fallback += 1
        fit["stock_code"] = str(code)
        reports.append(fit)
    raw = _beta_matrix(reports, feature_names)
    meta = {
        "holdout_ratio": float(holdout_ratio),
        "cut_date": str(cut_date or "").strip()[:10] or None,
        "split_mode": (
            "calendar" if str(cut_date or "").strip()[:10] else "per_stock_ratio"
        ),
        "n_codes": int(len(codes)),
        "n_fit_ok": int(sum(1 for r in reports if r.get("success"))),
        "n_fit_fail": int(n_fit_fail),
        "n_full_sample_fallback": int(n_full_fallback),
        "note": "聚类 β 来自每票时间前段 OLS；交付组池 return_model 仍用全样本。",
    }
    return raw, meta


def _partition_method_specs(
    method_s: str, link_s: str
) -> List[Dict[str, Any]]:
    """auto 选区：少量分区配方（层次 complete/average + kmeans），按 holdout loss 择优。"""
    specs: List[Dict[str, Any]] = [
        {
            "kind": "hierarchical_complete",
            "method": "hierarchical",
            "linkage": "complete",
            "seed": 42,
        },
        {
            "kind": "hierarchical_average",
            "method": "hierarchical",
            "linkage": "average",
            "seed": 42,
        },
        {
            "kind": "kmeans_42",
            "method": "kmeans",
            "linkage": link_s or "average",
            "seed": 42,
        },
    ]
    # 调用方显式 method 时，保证对应配方在最前
    prefer = str(method_s or "hierarchical").strip().lower()
    if prefer == "kmeans":
        specs = [
            specs[2],
            specs[0],
            specs[1],
            {
                "kind": "kmeans_7",
                "method": "kmeans",
                "linkage": link_s or "average",
                "seed": 7,
            },
        ]
    return specs


def _score_labels_partition(
    *,
    x: np.ndarray,
    labels: np.ndarray,
    clustered_base: Dict[str, Any],
    codes: Sequence[str],
    panel_by_code: Dict[str, Dict[str, Any]],
    per_stock: Sequence[Dict[str, Any]],
    bars_by_code: Dict[str, List[dict]],
    horizon_days: int,
    lam: float,
    use_pit: bool,
    select_ridge: bool,
    collinearity_policy: str,
    respect_regime: bool,
    y_spec: Dict[str, Any],
    oos_tol_pp: float,
    kind: str,
    holdout_ratio: float = 0.3,
    cut_date: Optional[str] = None,
    progress_cb: Optional[Any] = None,
    run_oos_gate: bool = True,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """labels → slim 组池 + partition_oos 打分；返回 (clustered, score, row_extra)。"""
    from quant.research.cluster_oos import score_cluster_partition_oos

    labels_i = np.asarray(labels, dtype=int)
    labels_i, _ = promote_outliers_to_singleton_clusters(labels_i)
    labels_i = _relabel_non_negative(labels_i)
    clustered_i = dict(clustered_base)
    clustered_i["labels"] = labels_i
    clustered_i["n_clusters"] = int(
        len(set(int(v) for v in labels_i if int(v) >= 0))
    )
    clustered_i["auto_k"] = True
    clustered_i["partition_kind"] = str(kind)
    clusters_i = _slim_pool_clusters_for_oos(
        codes=codes,
        labels=labels_i,
        panel_by_code=panel_by_code,
        per_stock=per_stock,
        horizon_days=horizon_days,
        ridge_lambda=lam,
        use_pit=use_pit,
        select_ridge=select_ridge,
        collinearity_policy=collinearity_policy,
        respect_regime=respect_regime,
        y_spec=y_spec,
        train_window_only=True,
        holdout_ratio=holdout_ratio,
        cut_date=cut_date,
        progress_cb=progress_cb,
    )
    score = score_cluster_partition_oos(
        clusters_i,
        horizon_days=horizon_days,
        oos_tol_pp=oos_tol_pp,
        respect_regime=respect_regime,
        bars_by_code=bars_by_code,
        panel_by_code=panel_by_code,
        silhouette=clustered_i.get("silhouette"),
        ridge_lambda=lam,
        select_ridge=select_ridge,
        collinearity_policy=collinearity_policy,
        y_spec=y_spec,
        use_pit=use_pit,
        holdout_ratio=holdout_ratio,
        cut_date=cut_date,
        progress_cb=progress_cb,
        run_oos_gate=bool(run_oos_gate),
    )
    extra = {
        "partition_kind": str(kind),
        "n_multi_member": sum(1 for c in clusters_i if not c.get("singleton")),
    }
    return clustered_i, score, extra


def _prepare_auto_k_cut_packs(
    *,
    x_primary: np.ndarray,
    codes: Sequence[str],
    panel_by_code: Dict[str, Dict[str, Any]],
    feature_names: Sequence[str],
    scale_mode: str,
    horizon_days: int,
    ridge_lambda: float,
    collinearity_policy: str,
    respect_regime: bool,
    y_spec: Dict[str, Any],
    use_pit: bool,
    holdout_ratio: float,
    cut_date: Optional[str],
    expanding_score: bool,
) -> List[Dict[str, Any]]:
    """主切点 +（可选）更早日历切点的尺度化 β，供 auto-k 多折打分。"""
    packs: List[Dict[str, Any]] = [
        {
            "cut_date": str(cut_date or "").strip()[:10] or None,
            "holdout_ratio": float(holdout_ratio),
            "train_fraction": round(1.0 - float(holdout_ratio), 4),
            "x": x_primary,
            "primary": True,
        }
    ]
    if not expanding_score or not cut_date:
        return packs
    from quant.research.cluster_wf_audit import resolve_expanding_cut_dates

    early = resolve_expanding_cut_dates(
        panel_by_code,
        codes=codes,
        train_fractions=(0.55,),
    )
    primary_cut = str(cut_date).strip()[:10]
    for meta in early:
        early_cut = str(meta.get("cut_date") or "").strip()[:10]
        if not early_cut or early_cut == primary_cut:
            continue
        raw_e, _meta_e = build_train_window_beta_matrix(
            codes,
            panel_by_code,
            feature_names,
            horizon_days=horizon_days,
            ridge_lambda=float(ridge_lambda),
            collinearity_policy=str(collinearity_policy),
            respect_regime=bool(respect_regime),
            y_spec=dict(y_spec or {}),
            use_pit=bool(use_pit),
            holdout_ratio=float(meta.get("holdout_ratio") or 0.45),
            cut_date=early_cut,
        )
        x_e, _tf = fit_beta_scale_transform(raw_e, scale_mode)
        packs.append(
            {
                "cut_date": early_cut,
                "holdout_ratio": float(meta.get("holdout_ratio") or 0.45),
                "train_fraction": float(meta.get("train_fraction") or 0.55),
                "x": x_e,
                "primary": False,
            }
        )
    return packs


def _aggregate_expanding_auto_k_score(
    fold_scores: Sequence[Dict[str, Any]],
    *,
    primary_score: Dict[str, Any],
) -> Dict[str, Any]:
    """多折 score → 均值 loss 写入排序键；交付仍用 primary_score 的辅指标。"""
    losses: List[float] = []
    ics: List[float] = []
    for sc in fold_scores:
        try:
            if sc.get("partition_loss") is not None:
                losses.append(float(sc["partition_loss"]))
        except (TypeError, ValueError):
            pass
        try:
            if sc.get("mean_yhat_ic") is not None:
                ics.append(float(sc["mean_yhat_ic"]))
        except (TypeError, ValueError):
            pass
    mean_loss = (
        float(sum(losses) / len(losses))
        if losses
        else float(primary_score.get("partition_loss") or 1e9)
    )
    mean_ic = (
        round(float(sum(ics) / len(ics)), 4)
        if ics
        else primary_score.get("mean_yhat_ic")
    )
    sil = primary_score.get("silhouette")
    mean_delta = primary_score.get("mean_delta_oos_pp")
    passed = int(primary_score.get("passed") or 0)
    sort_key = (
        -mean_loss,
        passed,
        float(mean_delta) if mean_delta is not None else float("-inf"),
        float(sil) if sil is not None else float("-inf"),
    )
    return {
        "partition_loss": round(mean_loss, 6),
        "partition_loss_primary": primary_score.get("partition_loss"),
        "mean_yhat_ic": mean_ic,
        "mean_yhat_ic_primary": primary_score.get("mean_yhat_ic"),
        "mean_holdout_r2": primary_score.get("mean_holdout_r2"),
        "mean_holdout_rmse": primary_score.get("mean_holdout_rmse"),
        "passed": passed,
        "failed": primary_score.get("failed"),
        "skipped": primary_score.get("skipped"),
        "mean_delta_oos_pp": mean_delta,
        "silhouette": sil,
        "sort_key": sort_key,
        "n_expanding_folds": len(fold_scores),
    }

def _slim_pool_clusters_for_oos(
    *,
    codes: Sequence[str],
    labels: np.ndarray,
    panel_by_code: Dict[str, Dict[str, Any]],
    per_stock: Sequence[Dict[str, Any]],
    horizon_days: int,
    ridge_lambda: float,
    use_pit: bool,
    select_ridge: bool,
    collinearity_policy: str,
    respect_regime: bool,
    y_spec: Dict[str, Any],
    train_window_only: bool = False,
    holdout_ratio: float = 0.3,
    cut_date: Optional[str] = None,
    progress_cb: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """仅为选 k：组池 OLS + return_model，跳过 IC / gaps / 共线诊断。

    ``train_window_only=True``：组池只用每票时间前段（与 holdout 评口径对齐）。
    """
    labs = np.asarray(labels, dtype=int)
    uniq = sorted(set(int(v) for v in labs if int(v) >= 0))
    out: List[Dict[str, Any]] = []
    n_uniq = max(1, len(uniq))
    for gi, cid in enumerate(uniq, start=1):
        if progress_cb:
            try:
                progress_cb(f"估β {gi}/{n_uniq}", gi, n_uniq)
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in factor_ols_clusters.py", exc_info=True)
        members = sorted(
            codes[i] for i, lab in enumerate(labs) if int(lab) == cid
        )
        member_idx = [i for i, lab in enumerate(labs) if int(lab) == cid]
        cluster: Dict[str, Any] = {
            "cluster_id": cid,
            "label": f"G{cid + 1}",
            "members": members,
            "member_count": len(members),
            "singleton": len(members) < 2,
        }
        if len(members) < 2:
            if member_idx:
                one = per_stock[member_idx[0]]
                cluster["ols"] = _public_cluster_ols(one, mode="single")
                cluster["return_model"] = _return_model_from_ols(cluster["ols"])
            else:
                cluster["ols"] = {"success": False, "error": "空组"}
                cluster["return_model"] = None
            out.append(cluster)
            continue
        all_xs: List[Dict[str, Optional[float]]] = []
        all_ys: List[float] = []
        for code in members:
            panel = panel_by_code.get(code) or {}
            if train_window_only:
                tx, ty, _ = _stock_train_xy(
                    panel, holdout_ratio=holdout_ratio, cut_date=cut_date
                )
                all_xs.extend(tx)
                all_ys.extend(ty)
            else:
                all_xs.extend(panel.get("xs") or [])
                all_ys.extend(panel.get("ys") or [])
        pooled = fit_factor_ols_from_panel(
            all_xs,
            all_ys,
            horizon_days=horizon_days,
            fundamentals_used=False,
            pit_fundamentals=use_pit,
            mode="watching_pooled",
            stock_codes=members,
            ridge_lambda=ridge_lambda,
            select_ridge=select_ridge,
            collinearity_policy=collinearity_policy,
            respect_regime=respect_regime,
            y_spec=y_spec,
        )
        pooled["mode"] = "cluster_pooled"
        cluster["ols"] = _public_cluster_ols(pooled, mode="cluster_pooled")
        cluster["return_model"] = _return_model_from_ols(cluster["ols"])
        out.append(cluster)
    return out


def _select_clustered_by_delta_oos(
    x: np.ndarray,
    *,
    codes: Sequence[str],
    panel_by_code: Dict[str, Dict[str, Any]],
    per_stock: Sequence[Dict[str, Any]],
    method_s: str,
    link_s: str,
    tau_q: float,
    horizon_days: int,
    lam: float,
    use_pit: bool,
    select_ridge: bool,
    collinearity_policy: str,
    respect_regime: bool,
    y_spec: Dict[str, Any],
    oos_tol_pp: float = 1.0,
    progress_cb: Optional[Any] = None,
    holdout_ratio: float = 0.3,
    cut_date: Optional[str] = None,
    feature_names: Optional[Sequence[str]] = None,
    scale_mode: str = "feature_zscore",
    expanding_score: bool = True,
    run_oos_gate: bool = True,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """auto-k：邻域 k × 配方；主切点定交付标签，多折均值 loss 主排。"""
    from quant.research.cluster_oos import pick_best_k_selection_row

    n = int(x.shape[0])
    center_k = default_n_clusters(n)
    cand_ks = auto_k_candidates(n)
    method_specs = _partition_method_specs(method_s, link_s)
    bars_by_code: Dict[str, List[dict]] = {
        str(c): list((panel_by_code.get(str(c)) or {}).get("bars") or [])
        for c in codes
    }
    cut_packs = _prepare_auto_k_cut_packs(
        x_primary=x,
        codes=codes,
        panel_by_code=panel_by_code,
        feature_names=list(feature_names or []),
        scale_mode=scale_mode,
        horizon_days=horizon_days,
        ridge_lambda=lam,
        collinearity_policy=collinearity_policy,
        respect_regime=respect_regime,
        y_spec=y_spec,
        use_pit=use_pit,
        holdout_ratio=holdout_ratio,
        cut_date=cut_date,
        expanding_score=bool(expanding_score) and bool(feature_names),
    )
    rows: List[Dict[str, Any]] = []
    by_key: Dict[Tuple[int, str], Dict[str, Any]] = {}

    def _progress(msg: str, cur: int = 0, tot: int = 0) -> None:
        if not progress_cb:
            return
        try:
            progress_cb(msg, cur, tot)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in factor_ols_clusters.py", exc_info=True)
            logger.warning("k 选择进度回调异常", exc_info=True)

    total_steps = max(1, len(cand_ks) * len(method_specs))
    step = 0
    seen_labels: set = set()
    for k_cand in cand_ks:
        for spec in method_specs:
            step += 1
            kind = str(spec["kind"])
            fold_n = max(1, len(cut_packs))
            units = total_steps * 1000

            def _recipe_progress(inner_msg: str, frac: float) -> None:
                f = min(1.0, max(0.0, float(frac)))
                micro = int(round((step - 1 + f) * 1000))
                bits = [
                    f"选区 {step}/{total_steps}",
                    f"k={k_cand}",
                    kind,
                ]
                if fold_n > 1:
                    bits.append(f"{fold_n}折")
                if inner_msg:
                    bits.append(inner_msg)
                _progress(" · ".join(bits), micro, units)

            _recipe_progress("聚类", 0.0)
            fold_scores: List[Dict[str, Any]] = []
            primary_clustered: Optional[Dict[str, Any]] = None
            primary_extra: Optional[Dict[str, Any]] = None
            primary_score: Optional[Dict[str, Any]] = None
            skip_candidate = False
            for pack_i, pack in enumerate(cut_packs):
                fold_base = pack_i / fold_n
                x_fold = pack["x"]
                clustered_raw = cluster_beta_vectors(
                    x_fold,
                    method=str(spec["method"]),
                    n_clusters=int(k_cand),
                    seed=int(spec.get("seed") or 42),
                    cluster_linkage=str(spec.get("linkage") or "average"),
                    within_dist_quantile=tau_q,
                    auto_postprocess=True,
                )
                if pack.get("primary"):
                    lab_tuple = tuple(
                        int(v)
                        for v in np.asarray(
                            clustered_raw["labels"], dtype=int
                        ).tolist()
                    )
                    if lab_tuple in seen_labels:
                        skip_candidate = True
                        break
                    seen_labels.add(lab_tuple)

                def _on_inner(msg: str, cur: int = 0, tot: int = 0) -> None:
                    inner = min(1.0, float(cur or 0) / float(max(1, tot or 1)))
                    if str(msg or "").startswith("估β"):
                        frac = fold_base + (0.08 + 0.42 * inner) / fold_n
                    else:
                        frac = fold_base + (0.5 + 0.5 * inner) / fold_n
                    _recipe_progress(msg, frac)

                clustered_i, score, extra = _score_labels_partition(
                    x=x_fold,
                    labels=np.asarray(clustered_raw["labels"], dtype=int),
                    clustered_base=clustered_raw,
                    codes=codes,
                    panel_by_code=panel_by_code,
                    per_stock=per_stock,
                    bars_by_code=bars_by_code,
                    horizon_days=horizon_days,
                    lam=lam,
                    use_pit=use_pit,
                    select_ridge=select_ridge,
                    collinearity_policy=collinearity_policy,
                    respect_regime=respect_regime,
                    y_spec=y_spec,
                    oos_tol_pp=oos_tol_pp,
                    kind=kind,
                    holdout_ratio=float(pack.get("holdout_ratio") or holdout_ratio),
                    cut_date=pack.get("cut_date"),
                    progress_cb=_on_inner,
                    run_oos_gate=bool(run_oos_gate),
                )
                fold_scores.append(
                    {
                        **score,
                        "cut_date": pack.get("cut_date"),
                        "train_fraction": pack.get("train_fraction"),
                        "primary": bool(pack.get("primary")),
                    }
                )
                if pack.get("primary"):
                    primary_clustered = clustered_i
                    primary_extra = extra
                    primary_score = score
            if skip_candidate or primary_clustered is None or primary_score is None:
                continue
            agg = _aggregate_expanding_auto_k_score(
                fold_scores, primary_score=primary_score
            )
            row = {
                "k": int(k_cand),
                "target_k": int(primary_clustered.get("target_k") or k_cand),
                "n_clusters": int(primary_clustered.get("n_clusters") or 0),
                "n_multi_member": int(
                    (primary_extra or {}).get("n_multi_member") or 0
                ),
                "partition_kind": kind,
                "passed": agg["passed"],
                "failed": agg["failed"],
                "skipped": agg["skipped"],
                "mean_delta_oos_pp": agg["mean_delta_oos_pp"],
                "mean_yhat_ic": agg.get("mean_yhat_ic"),
                "mean_holdout_r2": agg.get("mean_holdout_r2"),
                "mean_holdout_rmse": agg.get("mean_holdout_rmse"),
                "partition_loss": agg.get("partition_loss"),
                "partition_loss_primary": agg.get("partition_loss_primary"),
                "n_expanding_folds": agg.get("n_expanding_folds"),
                "expanding_folds": [
                    {
                        "cut_date": f.get("cut_date"),
                        "train_fraction": f.get("train_fraction"),
                        "primary": f.get("primary"),
                        "partition_loss": f.get("partition_loss"),
                        "mean_yhat_ic": f.get("mean_yhat_ic"),
                    }
                    for f in fold_scores
                ],
                "silhouette": agg["silhouette"],
                "sort_key": agg["sort_key"],
            }
            rows.append(row)
            by_key[(int(k_cand), kind)] = primary_clustered

    if not rows:
        clustered = cluster_beta_vectors(
            x,
            method=method_s,
            n_clusters=center_k,
            seed=42,
            cluster_linkage=link_s,
            within_dist_quantile=tau_q,
            auto_postprocess=True,
        )
        labels = np.asarray(clustered["labels"], dtype=int)
        labels, _ = promote_outliers_to_singleton_clusters(labels)
        clustered = dict(clustered)
        clustered["labels"] = _relabel_non_negative(labels)
        clustered["auto_k"] = True
        return clustered, {
            "mode": "auto_fit_ic",
            "center_k": int(center_k),
            "candidate_ks": list(cand_ks),
            "candidates": [],
            "chosen_k": int(center_k),
            "reason": "无有效候选，回退中心 k",
            "objective": "max_yhat_ic_min_fit_error",
            "expanding_score": bool(len(cut_packs) > 1),
            "run_oos_gate": bool(run_oos_gate),
        }

    best = pick_best_k_selection_row(rows, oos_tol_pp=float(oos_tol_pp)) or rows[0]
    chosen_k = int(best["k"])
    chosen_kind = str(best.get("partition_kind") or "hierarchical_complete")
    parts = []
    if best.get("partition_loss") is not None:
        parts.append(f"loss={best.get('partition_loss')}")
    if best.get("mean_yhat_ic") is not None:
        parts.append(f"ŷIC={best.get('mean_yhat_ic')}")
    if best.get("mean_holdout_r2") is not None:
        parts.append(f"R²={best.get('mean_holdout_r2')}")
    parts.append(f"kind={chosen_kind}")
    parts.append(f"passed={best.get('passed')}")
    if best.get("mean_delta_oos_pp") is not None:
        parts.append(f"mean_ΔOOS={best.get('mean_delta_oos_pp')}pp")
    if int(best.get("n_expanding_folds") or 1) > 1:
        parts.append(f"folds={best.get('n_expanding_folds')}")
    reason = f"邻域{cand_ks}×配方 优选 k={chosen_k}（{' · '.join(parts)}）"
    k_selection = {
        "mode": "auto_fit_ic",
        "center_k": int(center_k),
        "candidate_ks": list(cand_ks),
        "partition_kinds": [s["kind"] for s in method_specs],
        "candidates": rows,
        "chosen_k": chosen_k,
        "chosen_partition_kind": chosen_kind,
        "reason": reason,
        "oos_tol_pp": float(oos_tol_pp),
        "objective": "max_yhat_ic_min_fit_error",
        "expanding_score": bool(len(cut_packs) > 1),
        "n_expanding_folds": int(len(cut_packs)),
        "run_oos_gate": bool(run_oos_gate),
        "note": (
            (
                "主排：多折日历前段 β 重聚类的均值 partition_loss"
                "（尾段 ŷ 有符号 IC↑ / 前段重拟合 R²↑；重拟合失败不计分）；"
                "交付标签取主切点（~70%）；"
                + (
                    "选区跳过组权 OOS 辅门禁（满池加速；定组后仍跑组内 OOS）；"
                    if not run_oos_gate
                    else "辅门禁：有过门 ΔOOS 时淘汰更差负 ΔOOS；"
                )
                + "kmeans 与层次同样劈超大组"
            )
            if len(cut_packs) > 1
            else (
                "主排：partition_loss（前段 β 定组 · 尾段 ŷ 有符号 IC↑ / 前段重拟合 R²↑；"
                "宇宙日历切分优先；重拟合失败不计分）；"
                + (
                    "选区跳过组权 OOS 辅门禁（满池加速；定组后仍跑组内 OOS）；"
                    if not run_oos_gate
                    else "辅门禁：有过门 ΔOOS 时淘汰更差负 ΔOOS；"
                )
                + "kmeans 与层次同样劈超大组；配方含层次/kmeans"
            )
        ),
        "walk_forward": {
            "holdout_ratio": float(holdout_ratio),
            "cut_date": str(cut_date or "").strip()[:10] or None,
            "split_mode": (
                "calendar"
                if str(cut_date or "").strip()[:10]
                else "per_stock_ratio"
            ),
            "expanding_score_cuts": [
                {
                    "cut_date": p.get("cut_date"),
                    "train_fraction": p.get("train_fraction"),
                    "primary": p.get("primary"),
                }
                for p in cut_packs
            ],
        },
    }
    clustered_best = by_key.get((chosen_k, chosen_kind))
    if clustered_best is None:
        for (k, kind), cl in by_key.items():
            if k == chosen_k:
                clustered_best = cl
                break
    if clustered_best is None:
        clustered_best = next(iter(by_key.values()))
    return clustered_best, k_selection



def compute_factor_ols_cluster_report(
    stock_panels: List[Dict[str, Any]],
    *,
    horizon_days: int = 3,
    ridge_lambda: float = 0.0,
    n_clusters: Optional[int] = None,
    pit_fundamentals: bool = True,
    sentiment_pit: bool = False,
    l2_normalize_betas: Optional[bool] = None,
    beta_scale: str = "feature_zscore",
    cluster_method: str = "hierarchical",
    cluster_linkage: str = "average",
    within_dist_quantile: float = 0.75,
    respect_regime: bool = True,
    select_ridge: bool = True,
    collinearity_policy: str = "drop_redundant",
    progress_cb: Optional[Any] = None,
    max_workers: int = 8,
) -> Dict[str, Any]:
    """逐票 OLS → β 聚类 → 组内池 OLS + 小步权。

    默认 / 显式 ``n_clusters`` 均切到目标 k；不严踢异质升单票组（否则手动 k 会炸组数）。
    异质用探针核对。``n_clusters is None`` 时：前段 β 定组，邻域 ±1 × 多种分区配方，
    按组 ŷ 每票时间尾段有符号 IC / 前段重拟合 R²（partition_loss）选优，ΔOOS 辅门禁。
    交付组池仍全样本估 β。手动 k 同样用前段 β 定组。
    FS2：``sentiment_pit`` 注入 as_of alt_sentiment（与 live 闸独立）。
    B5：默认 ``respect_regime=True``；B3：默认选 Ridge λ + 共线 drop_redundant。
    大宇宙（≥40）：末日财务快照进面板（跳过逐日 PIT）+ 默认不选 λ，显著加速。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from core.research.beta_accuracy import build_y_spec

    lam = clamp_ridge_lambda(ridge_lambda, 0.0)
    k_req = None if n_clusters is None else clamp_n_clusters(n_clusters, 3)
    horizon_days = max(1, min(int(horizon_days or 3), 10))
    use_pit = bool(pit_fundamentals)
    use_sent_pit = bool(sentiment_pit)
    scale_mode = resolve_beta_scale(beta_scale, l2_normalize_betas=l2_normalize_betas)
    method_s = str(cluster_method or "hierarchical").strip().lower()
    link_s = str(cluster_linkage or "average").strip().lower()
    tau_q = float(within_dist_quantile)
    y_spec = build_y_spec(horizon_days=horizon_days)
    respect_regime = bool(respect_regime)
    select_ridge = bool(select_ridge)
    collinearity_policy = str(collinearity_policy or "drop_redundant")

    panel_n = sum(
        1
        for item in (stock_panels or [])
        if str(item.get("code") or item.get("stock_code") or "").strip()
        and (item.get("bars") or [])
    )
    speed = cluster_speed_policy(panel_n)
    large_universe = bool(speed["large_universe"])
    daily_pit = bool(use_pit and speed["daily_pit"])
    if large_universe and select_ridge:
        select_ridge = False
    speed_note = speed.get("note") if large_universe else None

    def _progress(msg: str, cur: int = 0, tot: int = 0) -> None:
        if not progress_cb:
            return
        try:
            progress_cb(msg, cur, tot)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in factor_ols_clusters.py", exc_info=True)
            logger.warning("OLS 单票拟合异常", exc_info=True)

    def _fit_one(item: Dict[str, Any]) -> Tuple[str, Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[str]]:
        code = str(item.get("code") or item.get("stock_code") or "").strip()
        bars = item.get("bars") or []
        if not code:
            return "", None, None, None
        if not bars:
            return code, None, None, "无日线"
        xs, ys, dates = collect_subscore_forward_panel(
            bars,
            horizon_days=horizon_days,
            index_bars=item.get("index_bars"),
            fundamentals=item.get("fundamentals"),
            stock_code=code,
            pit_fundamentals=daily_pit,
            sentiment_pit=use_sent_pit,
            respect_regime=respect_regime,
        )
        if not ys:
            return code, None, None, "面板为空"
        fit = fit_factor_ols_from_panel(
            xs,
            ys,
            horizon_days=horizon_days,
            fundamentals_used=bool(item.get("fundamentals")) or use_pit,
            pit_fundamentals=use_pit,
            mode="single",
            stock_codes=[code],
            ridge_lambda=lam,
            select_ridge=False,
            collinearity_policy=collinearity_policy,
            respect_regime=respect_regime,
            y_spec=y_spec,
        )
        if not fit.get("success"):
            return code, None, None, str(fit.get("error") or "单票 OLS 失败")
        fit["stock_code"] = code
        if xs:
            fit["last_sub_scores"] = dict(xs[-1])
        panel_pack = {
            "code": code,
            "bars": bars,
            "index_bars": item.get("index_bars"),
            "fundamentals": item.get("fundamentals"),
            "xs": xs,
            "ys": ys,
            "dates": list(dates or []),
        }
        return code, fit, panel_pack, None

    per_stock: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    panel_by_code: Dict[str, Dict[str, Any]] = {}

    items = [it for it in (stock_panels or []) if isinstance(it, dict)]
    workers = max(1, min(int(max_workers or 8), 12, len(items) or 1))
    _progress(f"拟合 0/{len(items)}", 0, len(items))
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_fit_one, item) for item in items]
        for fut in as_completed(futs):
            code, fit, panel_pack, reason = fut.result()
            done += 1
            if done == len(items) or done % 5 == 0 or done <= 3:
                _progress(f"拟合 {done}/{len(items)}", done, len(items))
            if not code:
                continue
            if reason:
                skipped.append({"code": code, "reason": reason})
                continue
            if fit is None or panel_pack is None:
                skipped.append({"code": code, "reason": "单票 OLS 失败"})
                continue
            per_stock.append(fit)
            panel_by_code[code] = panel_pack

    # 线程完成顺序不稳定；按代码排序保证聚类 / holdout 可复现
    per_stock.sort(key=lambda r: str(r.get("stock_code") or ""))

    if len(per_stock) < 2:
        return {
            "success": False,
            "error": "有效单票 OLS 不足 2 只，无法按 β 聚类",
            "task": "factor_ols_clusters",
            "mode": "ols_beta_clusters",
            "stock_count": len(per_stock),
            "skipped": skipped,
            "horizon_days": horizon_days,
            "ridge_lambda": lam,
            "n_clusters": k_req,
            "speed_note": speed_note,
            "daily_pit": daily_pit,
        }

    _progress(f"聚类 {len(per_stock)} 只…", len(per_stock), len(per_stock))
    feature_names = _feature_union(per_stock)
    if len(feature_names) < 2:
        return {
            "success": False,
            "error": "入模因子过少，无法按 β 向量聚类",
            "task": "factor_ols_clusters",
            "mode": "ols_beta_clusters",
            "stock_count": len(per_stock),
            "skipped": skipped,
            "horizon_days": horizon_days,
            "ridge_lambda": lam,
            "n_clusters": k_req,
        }

    codes = [str(r.get("stock_code")) for r in per_stock]
    index_bars_probe = None
    for c0 in codes:
        ib0 = (panel_by_code.get(c0) or {}).get("index_bars")
        if ib0:
            index_bars_probe = ib0
            break
    ic_feature_names = panel_ic_factor_names(
        respect_regime=respect_regime,
        index_bars=index_bars_probe,
        cluster_feature_names=feature_names,
    )
    # 定组用前段 β（时间因果）；全样本 per_stock 仍供探针 / 展示 / 最终组池
    holdout_ratio = 0.3
    from quant.research.partition_loss import resolve_calendar_cut_date

    cut_date = resolve_calendar_cut_date(
        panel_by_code, holdout_ratio=holdout_ratio, codes=codes
    )
    walk_forward = {
        "holdout_ratio": float(holdout_ratio),
        "cut_date": cut_date,
        "split_mode": "calendar" if cut_date else "per_stock_ratio",
        "note": (
            "宇宙日历切分：决策日 ≤ cut_date 为前段（定组 β / 选 k 组池 / holdout 重拟合）；"
            "之后为尾段评 ŷ。auto-k 另用更早切点均值 loss 排序；另附 expanding 审计。"
            "无足够日期时退回按票比例切。"
            if cut_date
            else "面板缺决策日，退回每票比例切尾；交付组池 return_model 仍用全样本。"
        ),
    }
    raw, cluster_beta_meta = build_train_window_beta_matrix(
        codes,
        panel_by_code,
        feature_names,
        horizon_days=horizon_days,
        ridge_lambda=lam,
        collinearity_policy=collinearity_policy,
        respect_regime=respect_regime,
        y_spec=y_spec,
        use_pit=use_pit,
        holdout_ratio=holdout_ratio,
        cut_date=cut_date,
    )
    x, scale_tf = fit_beta_scale_transform(raw, scale_mode)
    k_selection: Dict[str, Any]
    if k_req is None:
        clustered, k_selection = _select_clustered_by_delta_oos(
            x,
            codes=codes,
            panel_by_code=panel_by_code,
            per_stock=per_stock,
            method_s=method_s,
            link_s=link_s,
            tau_q=tau_q,
            horizon_days=horizon_days,
            lam=lam,
            use_pit=use_pit,
            select_ridge=select_ridge,
            collinearity_policy=collinearity_policy,
            respect_regime=respect_regime,
            y_spec=y_spec,
            progress_cb=progress_cb,
            holdout_ratio=holdout_ratio,
            cut_date=cut_date,
            feature_names=feature_names,
            scale_mode=scale_mode,
            expanding_score=bool(cut_date) and not large_universe,
            run_oos_gate=not large_universe,
        )
        # 选 k 侧的 expanding_score_cuts 并入顶层 walk_forward
        sel_wf = (k_selection or {}).get("walk_forward") or {}
        if sel_wf.get("expanding_score_cuts"):
            walk_forward["expanding_score_cuts"] = sel_wf.get(
                "expanding_score_cuts"
            )
            walk_forward["expanding_score"] = bool(
                k_selection.get("expanding_score")
            )
    else:
        clustered = cluster_beta_vectors(
            x,
            method=method_s,
            n_clusters=k_req,
            seed=42,
            cluster_linkage=link_s,
            within_dist_quantile=tau_q,
        )
        k_selection = {
            "mode": "manual",
            "center_k": None,
            "candidate_ks": [],
            "candidates": [],
            "chosen_k": int(k_req),
            "reason": "n_clusters_explicit",
            "partition_kind": f"{method_s}_{link_s}",
        }
    k_selection = dict(k_selection)
    k_selection["cluster_beta"] = cluster_beta_meta
    k_selection["walk_forward"] = walk_forward
    labels = np.asarray(clustered["labels"], dtype=int)
    tau_used = float(clustered.get("within_dist_cap") or clustered.get("tau") or 1.0)
    pool_ejects: List[Dict[str, Any]] = []
    # 目标 k（自动或手动）都不做组池 Δβ/直径踢出：踢出再升单票组会把 k=2 炸成十几组。
    # 异质看探针 / 组内距离，不靠拆组。
    run_pool_eject = False

    if run_pool_eject:
        for _round in range(3):
            uniq = sorted(c for c in set(int(v) for v in labels) if c >= 0)
            group_raw_map: Dict[int, np.ndarray] = {}
            active_mask_map: Dict[int, np.ndarray] = {}
            group_scaled: Dict[int, np.ndarray] = {}
            for cid in uniq:
                member_idx = [i for i, lab in enumerate(labels) if int(lab) == cid]
                if len(member_idx) < 2:
                    continue
                members = [codes[i] for i in member_idx]
                all_xs: List[Dict[str, Optional[float]]] = []
                all_ys: List[float] = []
                for code in members:
                    panel = panel_by_code.get(code) or {}
                    all_xs.extend(panel.get("xs") or [])
                    all_ys.extend(panel.get("ys") or [])
                pooled = fit_factor_ols_from_panel(
                    all_xs,
                    all_ys,
                    horizon_days=horizon_days,
                    fundamentals_used=False,
                    pit_fundamentals=use_pit,
                    mode="watching_pooled",
                    stock_codes=members,
                    ridge_lambda=lam,
                    select_ridge=select_ridge,
                    collinearity_policy=collinearity_policy,
                    respect_regime=respect_regime,
                    y_spec=y_spec,
                )
                if not pooled.get("success"):
                    continue
                g_raw = coef_vector_from_report(pooled, feature_names)
                group_scaled[cid] = apply_beta_scale_transform(g_raw, scale_tf)
                if len(member_idx) >= 4:
                    group_raw_map[cid] = g_raw
                    active = set(str(f) for f in (pooled.get("active_features") or []))
                    if not active:
                        active = {
                            str(f)
                            for f, v in _ols_coef_dict(pooled).items()
                            if abs(float(v)) > 1e-12
                        }
                    active_mask_map[cid] = np.asarray(
                        [fn in active for fn in feature_names], dtype=bool
                    )
            if not group_scaled and not group_raw_map:
                break
            labels, ejected_delta = (
                eject_by_group_beta_delta(
                    raw,
                    labels,
                    group_raw_by_cluster=group_raw_map,
                    active_mask_by_cluster=active_mask_map,
                    hetero_abs=0.30,
                    max_abs_delta=0.55,
                    hetero_rel=0.40,
                    max_rel_delta=0.75,
                    abs_floor=0.01,
                    max_hetero_factors=3,
                    use_relative=True,
                )
                if group_raw_map
                else (labels, [])
            )
            labels, ejected_l2 = (
                eject_far_from_group_beta(
                    x, labels, group_beta_scaled_by_cluster=group_scaled, tau=tau_used
                )
                if group_scaled
                else (labels, [])
            )
            ejected = ejected_delta + ejected_l2
            if not ejected:
                break
            pool_ejects.extend(ejected)
            refined = refine_cluster_labels(
                x,
                labels,
                min_size=2,
                tau=tau_used,
                strict_diameter=True,
                absorb_far=False,
            )
            labels = np.asarray(refined["labels"], dtype=int)
            pool_ejects.extend(refined.get("ejects") or [])

    # 离群不丢弃：各自升为单票组（自动 k 路径通常无离群）
    _progress("组池 OLS…", 0, 1)
    eject_by_idx: Dict[int, Dict[str, Any]] = {}
    for e in list(clustered.get("ejects") or []) + pool_ejects:
        if "index" in e:
            eject_by_idx[int(e["index"])] = e
    labels, promoted_idx = promote_outliers_to_singleton_clusters(labels)
    labels = _relabel_non_negative(labels)

    # 轻量 holdout 贪心换组（B2：大宇宙用 focused 弱组换票，不再整段跳过）
    greedy_refine: Dict[str, Any]
    n_codes = len(codes)
    if (
        cut_date
        and n_codes >= 4
        and n_codes <= 100
        and len(set(int(v) for v in labels if int(v) >= 0)) >= 2
    ):
        _progress("贪心换组…", 0, 1)
        try:
            from quant.research.cluster_greedy_refine import light_greedy_swap_refine

            greedy_mode = "focused" if (large_universe or n_codes > 32) else "full"
            labels, greedy_refine = light_greedy_swap_refine(
                labels,
                codes,
                panel_by_code,
                holdout_ratio=holdout_ratio,
                cut_date=cut_date,
                horizon_days=horizon_days,
                ridge_lambda=lam,
                use_pit=use_pit,
                select_ridge=select_ridge,
                collinearity_policy=collinearity_policy,
                respect_regime=respect_regime,
                y_spec=y_spec,
                mode=greedy_mode,
                max_rounds=2 if greedy_mode == "full" else 3,
                max_evals=(
                    min(80, max(20, n_codes * 4))
                    if greedy_mode == "full"
                    else min(120, max(30, n_codes * 2))
                ),
            )
            labels = _relabel_non_negative(np.asarray(labels, dtype=int))
        except Exception as exc:
            logger.warning("贪心换组跳过: %s", exc, exc_info=True)
            greedy_refine = {"ok": False, "reason": f"refine_error:{exc}"}
    else:
        greedy_refine = {
            "ok": False,
            "reason": (
                "no_calendar_cut"
                if not cut_date
                else ("n_out_of_range" if n_codes > 100 else "too_few_codes")
            ),
        }
    k_selection["greedy_refine"] = greedy_refine

    # 对齐上一版 live 的 cluster_id / G 标签，减轻重跑后 ŷ 抖动
    label_alignment: Dict[str, Any]
    try:
        from quant.research.cluster_label_align import align_labels_with_active_live

        labels, label_alignment = align_labels_with_active_live(codes, labels)
    except Exception as exc:
        logger.warning("分组标签对齐跳过: %s", exc, exc_info=True)
        label_alignment = {
            "aligned": False,
            "reason": f"align_error:{exc}",
        }

    # 扩展窗多折审计（只读）：大宇宙跳过以控时
    if cut_date and not large_universe and len(codes) >= 4:
        _progress("扩展窗审计…", 0, 1)
        try:
            from quant.research.cluster_wf_audit import expanding_cluster_wf_audit

            kind = (
                k_selection.get("chosen_partition_kind")
                or k_selection.get("partition_kind")
                or f"{method_s}_{link_s}"
            )
            target_k = int(
                k_selection.get("chosen_k")
                or clustered.get("target_k")
                or max(2, len(set(int(v) for v in labels if int(v) >= 0)))
            )
            walk_forward["expanding"] = expanding_cluster_wf_audit(
                codes,
                panel_by_code,
                feature_names,
                n_clusters=target_k,
                partition_kind=str(kind),
                method=method_s,
                linkage=link_s,
                scale_mode=scale_mode,
                tau_q=tau_q,
                horizon_days=horizon_days,
                ridge_lambda=lam,
                collinearity_policy=collinearity_policy,
                respect_regime=respect_regime,
                y_spec=y_spec,
                use_pit=use_pit,
                select_ridge=select_ridge,
                train_fractions=(0.55, 0.7),
                per_stock=per_stock,
            )
        except Exception as exc:
            logger.warning("扩展窗聚类审计跳过: %s", exc, exc_info=True)
            walk_forward["expanding"] = {
                "ok": False,
                "reason": f"audit_error:{exc}",
            }
    else:
        walk_forward["expanding"] = {
            "ok": False,
            "reason": (
                "large_universe_skip"
                if large_universe
                else ("no_calendar_cut" if not cut_date else "too_few_codes")
            ),
        }
    k_selection["walk_forward"] = walk_forward

    promoted_set = set(int(i) for i in promoted_idx)
    within_stats = cluster_within_stats(x, labels)
    within_by_id = {
        int(s["cluster_id"]): s for s in within_stats if "cluster_id" in s
    }
    uniq_cids = sorted(set(int(v) for v in labels if int(v) >= 0))
    k = int(len(uniq_cids))
    beta_outliers: List[Dict[str, Any]] = []  # 兼容字段：升为单票组后不再「未入簇」
    singleton_outlier_groups: List[Dict[str, Any]] = []
    for i in promoted_idx:
        meta = eject_by_idx.get(int(i)) or {}
        singleton_outlier_groups.append(
            {
                "code": codes[int(i)],
                "cluster_label": f"G{int(labels[int(i)]) + 1}",
                "eject_reason": meta.get("reason") or "outlier",
                "distance": meta.get("distance"),
                "threshold": meta.get("threshold"),
            }
        )

    if k < 1:
        return {
            "success": False,
            "error": "无有效分组（拟合失败或样本不足）",
            "task": "factor_ols_clusters",
            "mode": "ols_beta_clusters",
            "stock_count": 0,
            "fitted_count": len(codes),
            "beta_outliers": beta_outliers,
            "skipped": skipped,
            "horizon_days": horizon_days,
            "ridge_lambda": lam,
            "n_clusters": 0,
            "cluster_method": clustered.get("method") or method_s,
            "cluster_linkage": clustered.get("cluster_linkage") or link_s,
            "note": "无法形成分组；检查观察池标的与日线。",
            "within_dist_cap": tau_used,
            "tau_quantile": clustered.get("tau_quantile") or tau_q,
            "label_alignment": label_alignment,
        }

    clusters: List[Dict[str, Any]] = []
    for ord_i, cid in enumerate(uniq_cids):
        members = [codes[i] for i, lab in enumerate(labels) if int(lab) == cid]
        member_idx = [i for i, lab in enumerate(labels) if int(lab) == cid]
        wstat = within_by_id.get(cid) or {}
        from_outlier = bool(member_idx) and all(
            int(i) in promoted_set for i in member_idx
        )
        eject_meta = (
            eject_by_idx.get(int(member_idx[0])) if from_outlier and member_idx else {}
        ) or {}
        cluster: Dict[str, Any] = {
            "cluster_id": cid,
            "label": f"G{cid + 1}",
            # 研究展示用连续序号（1..k）；label/G* 可能因对齐 live 而不连续
            "ordinal": int(ord_i + 1),
            "members": members,
            "member_count": len(members),
            "singleton": len(members) < 2,
            "outlier_singleton": from_outlier and len(members) < 2,
            "eject_reason": eject_meta.get("reason") if from_outlier else None,
            "max_within_dist": wstat.get("max_within_dist"),
            "mean_center_dist": wstat.get("mean_center_dist"),
            "within_dist_cap": tau_used,
        }
        from quant.research.cluster_soft_hetero import build_pooled_rows_with_codes

        all_xs, all_ys, row_codes = build_pooled_rows_with_codes(
            members, panel_by_code
        )
        if len(members) < 2:
            cluster["factor_ic_panel"] = _cluster_factor_ic_panel(
                members,
                panel_by_code=panel_by_code,
                feature_names=ic_feature_names,
                horizon_days=horizon_days,
                pit_fundamentals=use_pit,
                all_xs=all_xs,
                all_ys=all_ys,
            )
            if member_idx:
                one = per_stock[member_idx[0]]
                cluster["ols"] = _public_cluster_ols(one, mode="single")
                cluster["return_model"] = _return_model_from_ols(cluster["ols"])
                draft = _draft_weights_from_ols(
                    one,
                    ic_panel=cluster.get("factor_ic_panel"),
                    return_model=cluster.get("return_model"),
                )
                cluster["weight_suggest"] = _weight_suggest_public(draft)
            else:
                cluster["ols"] = {"success": False, "error": "空组"}
                cluster["return_model"] = None
            cluster["member_beta_gaps"] = []
            cluster["soft_hetero"] = {"enabled": False, "refit": False}
            clusters.append(cluster)
            continue

        # 先等权估组 β，再按单票 Δβ 软降权重拟合（不拆组）
        pooled = fit_factor_ols_from_panel(
            all_xs,
            all_ys,
            horizon_days=horizon_days,
            fundamentals_used=False,
            pit_fundamentals=use_pit,
            mode="watching_pooled",
            stock_codes=members,
            ridge_lambda=lam,
            select_ridge=select_ridge,
            collinearity_policy=collinearity_policy,
            respect_regime=respect_regime,
            y_spec=y_spec,
        )
        soft_hetero: Dict[str, Any] = {
            "enabled": False,
            "refit": False,
            "member_weights": {},
        }
        if pooled.get("success") and len(members) >= 2 and row_codes:
            from quant.research.cluster_soft_hetero import (
                compute_member_soft_weights,
                expand_member_weights_to_rows,
            )

            g_raw0 = coef_vector_from_report(pooled, feature_names)
            active0 = set(str(f) for f in (pooled.get("active_features") or []))
            if not active0:
                active0 = {
                    str(f)
                    for f, v in _ols_coef_dict(pooled).items()
                    if abs(float(v)) > 1e-12
                }
            amask0 = np.asarray(
                [fn in active0 for fn in feature_names], dtype=bool
            )
            member_raw_map = {
                codes[i]: raw[i] for i in member_idx if 0 <= i < len(codes)
            }
            mw = compute_member_soft_weights(
                member_codes=members,
                member_raw=member_raw_map,
                group_raw=g_raw0,
                active_mask=amask0,
            )
            sw = expand_member_weights_to_rows(row_codes, mw)
            # 仅当确有降权时才二遍拟合
            if any(abs(float(w) - 1.0) > 1e-6 for w in sw):
                lam_w = float(
                    pooled.get("ridge_lambda_selected")
                    if pooled.get("ridge_lambda_selected") is not None
                    else (pooled.get("ridge_lambda") or lam)
                )
                pooled_w = fit_factor_ols_from_panel(
                    all_xs,
                    all_ys,
                    horizon_days=horizon_days,
                    fundamentals_used=False,
                    pit_fundamentals=use_pit,
                    mode="watching_pooled",
                    stock_codes=members,
                    ridge_lambda=lam_w,
                    select_ridge=False,
                    collinearity_policy=collinearity_policy,
                    respect_regime=respect_regime,
                    y_spec=y_spec,
                    sample_weights=sw,
                )
                if pooled_w.get("success"):
                    pooled = pooled_w
                    soft_hetero["refit"] = True
            soft_hetero["enabled"] = True
            soft_hetero["member_weights"] = mw
            soft_hetero["mean_weight"] = round(
                float(sum(sw) / max(1, len(sw))), 4
            ) if sw else None
            soft_hetero["min_weight"] = round(float(min(sw)), 4) if sw else None

        pooled["mode"] = "cluster_pooled"
        cluster["ols"] = _public_cluster_ols(pooled, mode="cluster_pooled")
        cluster["return_model"] = _return_model_from_ols(cluster["ols"])
        cluster["soft_hetero"] = soft_hetero
        gaps: List[Dict[str, Any]] = []
        if pooled.get("success"):
            g_raw = coef_vector_from_report(pooled, feature_names)
            g_scaled = apply_beta_scale_transform(g_raw, scale_tf)
            active = set(str(f) for f in (pooled.get("active_features") or []))
            if not active:
                active = {
                    str(f)
                    for f, v in _ols_coef_dict(pooled).items()
                    if abs(float(v)) > 1e-12
                }
            amask = np.asarray(
                [fn in active for fn in feature_names], dtype=bool
            )
            mw_map = soft_hetero.get("member_weights") or {}
            for i in member_idx:
                d = float(np.linalg.norm(x[i] - g_scaled))
                dd = beta_delta_mismatch(raw[i], g_raw, active_mask=amask)
                code_i = codes[i]
                gaps.append(
                    {
                        "code": code_i,
                        "distance_to_group_beta": round(d, 4),
                        "max_abs_delta": dd["max_abs_delta"],
                        "mean_abs_delta": dd["mean_abs_delta"],
                        "hetero_count": dd["hetero_count"],
                        "within_cap": round(tau_used, 4),
                        "soft_weight": (mw_map.get(code_i) or {}).get("weight"),
                    }
                )
            cluster["factor_ic_panel"] = _cluster_factor_ic_panel(
                members,
                panel_by_code=panel_by_code,
                feature_names=ic_feature_names,
                horizon_days=horizon_days,
                pit_fundamentals=use_pit,
                all_xs=all_xs,
                all_ys=all_ys,
            )
            draft = _draft_weights_from_ols(
                pooled,
                ic_panel=cluster.get("factor_ic_panel"),
                return_model=cluster.get("return_model"),
            )
            cluster["weight_suggest"] = _weight_suggest_public(draft)
        else:
            cluster["factor_ic_panel"] = _cluster_factor_ic_panel(
                members,
                panel_by_code=panel_by_code,
                feature_names=ic_feature_names,
                horizon_days=horizon_days,
                pit_fundamentals=use_pit,
                all_xs=all_xs,
                all_ys=all_ys,
            )
            cluster["weight_suggest"] = {
                "success": False,
                "error": pooled.get("error") or "组内池 OLS 失败",
            }
        cluster["member_beta_gaps"] = gaps
        if gaps:
            cluster["max_distance_to_group_beta"] = max(
                float(g["distance_to_group_beta"]) for g in gaps
            )
            cluster["mean_distance_to_group_beta"] = round(
                float(np.mean([g["distance_to_group_beta"] for g in gaps])), 4
            )
            cluster["max_abs_delta_to_group"] = max(
                float(g.get("max_abs_delta") or 0) for g in gaps
            )
            cluster["mean_hetero_count"] = round(
                float(np.mean([g.get("hetero_count") or 0 for g in gaps])), 2
            )
        if "factor_ic_panel" not in cluster:
            cluster["factor_ic_panel"] = _cluster_factor_ic_panel(
                members,
                panel_by_code=panel_by_code,
                feature_names=ic_feature_names,
                horizon_days=horizon_days,
                pit_fundamentals=use_pit,
                all_xs=all_xs,
                all_ys=all_ys,
            )
        clusters.append(cluster)

    for cl in clusters:
        coefs = _ols_coef_dict(cl.get("ols") or {})
        top = sorted(coefs.items(), key=lambda kv: abs(kv[1]), reverse=True)[:5]
        cl["top_betas"] = [
            {"factor": f, "beta": round(b, 4)} for f, b in top if math.isfinite(b)
        ]
        # FS1：组内趋势族共线性（晋升人审提示）
        try:
            from core.signal.factors.meta.collinearity import collinearity_from_panel_rows

            mem_xs: List[Dict[str, Any]] = []
            for code in cl.get("members") or []:
                mem_xs.extend((panel_by_code.get(str(code)) or {}).get("xs") or [])
            cl["trend_collinearity"] = collinearity_from_panel_rows(mem_xs)
        except Exception as exc:
            logger.exception('unexpected error in compute_factor_ols_cluster_report')
            cl["trend_collinearity"] = {"success": False, "error": str(exc)}

    panel_xs: List[Dict[str, Any]] = []
    for p in panel_by_code.values():
        panel_xs.extend(p.get("xs") or [])
    try:
        from core.signal.factors.meta.collinearity import collinearity_from_panel_rows

        trend_collinearity = collinearity_from_panel_rows(panel_xs)
    except Exception as exc:
        logger.exception('unexpected error in compute_factor_ols_cluster_report')
        trend_collinearity = {"success": False, "error": str(exc)}

    alt_sentiment_ic = None
    if use_sent_pit:
        try:
            from core.research.sentiment_ic import summarize_alt_sentiment_ic_pool

            alt_sentiment_ic = summarize_alt_sentiment_ic_pool(
                [
                    {
                        "code": c,
                        "bars": (panel_by_code.get(c) or {}).get("bars"),
                        "index_bars": (panel_by_code.get(c) or {}).get("index_bars"),
                        "fundamentals": (panel_by_code.get(c) or {}).get("fundamentals"),
                    }
                    for c in panel_by_code
                ],
                horizon_days=horizon_days,
                pit_fundamentals=use_pit,
            )
        except Exception as exc:
            logger.exception('unexpected error in compute_factor_ols_cluster_report')
            alt_sentiment_ic = {"success": False, "error": str(exc)}

    in_codes = [codes[i] for i in range(len(codes)) if int(labels[i]) >= 0]
    n_singleton_out = len(singleton_outlier_groups)
    n_multi = sum(1 for c in clusters if not c.get("singleton"))
    n_pool_ej = sum(
        1
        for e in pool_ejects
        if str(e.get("reason") or "").startswith(
            ("far_from_group_beta", "delta_beta_")
        )
    )
    return {
        "success": True,
        "task": "factor_ols_clusters",
        "mode": "ols_beta_clusters",
        "horizon_days": horizon_days,
        "ridge_lambda": lam,
        "n_clusters": k,
        "n_multi_member_clusters": n_multi,
        "n_clusters_requested": k_req,
        "n_clusters_auto": bool(clustered.get("auto_k")) or k_req is None,
        "target_k": clustered.get("target_k"),
        "k_selection": k_selection,
        "walk_forward": walk_forward,
        "greedy_refine": greedy_refine,
        "label_alignment": label_alignment,
        "max_cluster_size": clustered.get("max_cluster_size"),
        "cluster_method": clustered.get("method") or method_s,
        "cluster_linkage": clustered.get("cluster_linkage") or link_s,
        "cut_by_tau": bool(clustered.get("cut_by_tau")),
        "silhouette": clustered.get("silhouette"),
        "cluster_k_candidates": clustered.get("candidates") or [],
        "cluster_merges": clustered.get("merges") or [],
        "cluster_ejects": list(clustered.get("ejects") or []) + pool_ejects,
        "min_cluster_size": clustered.get("min_cluster_size"),
        "within_dist_cap": tau_used,
        "tau": tau_used,
        "tau_quantile": clustered.get("tau_quantile") or tau_q,
        "distance_scale": clustered.get("distance_scale"),
        "feature_names": list(feature_names),
        "ic_feature_names": list(ic_feature_names),
        "stock_codes": in_codes,
        "fitted_codes": codes,
        "fitted_count": len(codes),
        "stock_count": len(in_codes),
        "beta_outliers": beta_outliers,
        "outlier_count": 0,
        "singleton_outlier_groups": singleton_outlier_groups,
        "singleton_outlier_count": n_singleton_out,
        "skipped": skipped,
        "clusters": clusters,
        "pit_fundamentals": use_pit,
        "sentiment_pit": use_sent_pit,
        "y_spec": y_spec,
        "respect_regime": respect_regime,
        "select_ridge": select_ridge,
        "collinearity_policy": collinearity_policy,
        "sample_fingerprint": _aggregate_sample_fingerprint(clusters),
        "daily_pit": daily_pit,
        "speed_note": speed_note,
        "large_universe": large_universe,
        "narrative": "primary=cross_section_pooled_ols; secondary=single_stock_timeseries_probe",
        "trend_collinearity": trend_collinearity,
        "alt_sentiment_ic": alt_sentiment_ic,
        "beta_scale": scale_mode,
        "l2_normalize_betas": scale_mode == "l2",
        "cluster_balance": _cluster_balance_stats(clusters),
        "track": "B0-B5",
        "note": (
            (
                f"按单票 OLS β（{scale_mode}，列缩尾）"
                + (
                    f"目标k={clustered.get('target_k')}·{clustered.get('cluster_linkage') or link_s}"
                    if clustered.get("target_k") is not None
                    else (
                        f"层次聚类({clustered.get('cluster_linkage') or link_s})"
                        if (clustered.get("method") or method_s) == "hierarchical"
                        else "k-means"
                    )
                )
                + (
                    "（自动·拟合+IC选k）"
                    if k_selection.get("mode") == "auto_fit_ic"
                    else (
                        "（自动·ΔOOS选k）"
                        if k_selection.get("mode") == "auto_oos"
                        else (
                            "（自动）"
                            if clustered.get("auto_k")
                            else ("（手动）" if clustered.get("target_k") is not None else "")
                        )
                    )
                )
                + f"，共 {k} 组（其中多票组 {n_multi}）；"
            )
            + (
                f"选k：{k_selection.get('reason')}；"
                if k_selection.get("mode") in ("auto_fit_ic", "auto_oos")
                and k_selection.get("reason")
                else ""
            )
            + (f"单票离群组 {n_singleton_out}；" if n_singleton_out else "")
            + (f"组β校验触发 {n_pool_ej}；" if n_pool_ej else "")
            + (
                f"标签对齐 live（稳定度 {label_alignment.get('stability')}）；"
                if label_alignment.get("aligned")
                else ""
            )
            + (
                "多票组软异质降权池 OLS；"
                if any(
                    (c.get("soft_hetero") or {}).get("refit") for c in clusters
                )
                else ""
            )
            + "多票组池 OLS → 因子系数 return_model（同组同建模）。"
            + (
                " 默认关闭逐日 PIT 财务以加速；仅作分组探针。"
                if not use_pit
                else " 已开 PIT 财务，耗时更长。"
            )
            + (
                f" {speed_note}。"
                if speed_note
                else (
                    " 主路径：截面/组池 OLS（respect_regime）；单票时序为诊断。"
                    if respect_regime
                    else " 研究全因子拟合（未对齐 regime）。"
                )
            )
            + " 研究探针，不写 signal_config。"
        ),
    }


def _aggregate_sample_fingerprint(clusters: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总各组 sample_fingerprint。

    小组成员数 < 3 时，组指纹本身用自适应 min_names，不再把「n_names<3」
    当成硬拦（单票/双票组是聚类设计内结果）。仍汇总 n_obs / 总 names。
    """
    from core.research.beta_accuracy import sample_fingerprint

    obs = 0
    names = 0
    blockers: List[str] = []
    spans: List[str] = []
    for cl in clusters or []:
        rm = (cl or {}).get("return_model") or {}
        ols = (cl or {}).get("ols") or {}
        fp = rm.get("sample_fingerprint") or ols.get("sample_fingerprint") or {}
        if not isinstance(fp, dict):
            continue
        try:
            obs += int(fp.get("n_obs") or 0)
        except (TypeError, ValueError):
            pass
        try:
            names += int(fp.get("n_names") or 0)
        except (TypeError, ValueError):
            pass
        if fp.get("date_span"):
            spans.append(str(fp["date_span"]))
        if fp.get("promote_ok") is False:
            # 仅保留非「小组员数」类拦阻（如 n_obs 不足）
            for b in fp.get("blockers") or []:
                bs = str(b)
                if "n_names=" in bs and "min_names=" in bs:
                    try:
                        # n_names=2 < min_names=3 → 小组，忽略
                        left = bs.split("n_names=")[1]
                        n_part = left.split("<")[0].strip()
                        if int(float(n_part)) < 3:
                            continue
                    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                        logger.debug("catch except Exception: in factor_ols_clusters.py", exc_info=True)
                        logger.warning("聚类后处理异常", exc_info=True)
                blockers.append(bs)
    # 全产物：总映射票数门槛仍用 3（至少覆盖若干票）；obs 用累加
    out = sample_fingerprint(
        n_obs=obs,
        n_names=names,
        date_span=spans[0] if len(spans) == 1 else ("; ".join(spans[:3]) if spans else None),
        min_obs=24,
        min_names=3,
    )
    if blockers:
        out["promote_ok"] = False
        existing = list(out.get("blockers") or [])
        out["blockers"] = existing + [f"组内：{b}" for b in blockers[:6]]
    out["n_clusters_with_fp"] = sum(
        1
        for cl in clusters or []
        if isinstance(((cl or {}).get("return_model") or {}).get("sample_fingerprint"), dict)
        or isinstance(((cl or {}).get("ols") or {}).get("sample_fingerprint"), dict)
    )
    return out


def _cluster_balance_stats(clusters: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    sizes = [int(c.get("member_count") or 0) for c in clusters or []]
    total = sum(sizes) or 1
    max_n = max(sizes) if sizes else 0
    singletons = sum(1 for n in sizes if n <= 1)
    max_share = round(max_n / total, 4)
    return {
        "sizes": sizes,
        "max_share": max_share,
        "singleton_groups": singletons,
        "imbalanced": bool(max_share >= 0.7 or singletons >= 1),
        "hint": (
            "组规模失衡（常见于原始 β 被极端票拉开）；已默认用因子 z-score。"
            if max_share >= 0.7 or singletons >= 1
            else ""
        ),
    }
