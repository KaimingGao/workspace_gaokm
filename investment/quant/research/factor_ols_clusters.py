"""按单票 OLS β 相似度聚类，使同组可共用建模、异组各用各的（研究用，不写盘）。

目的：OLS 表现相似的股票进同一组 → 组内池 OLS + 共用小步权；不同组独立建模。
默认宇宙=观察池。流程：逐票 OLS → β 缩尾+z-score → average·目标 k（≈√N，3～8）
→ 多票组池 OLS → 因子系数(return_model)。异质用探针核对（自动路径不再拆成单票堆）。

``beta_scale``：
- ``feature_zscore``（默认）：列缩尾 + 因子维 z-score
- ``l2``：行 L2 归一只比方向
- ``none``：原始 β（易被极端票主导）
"""

from __future__ import annotations

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


def clamp_n_clusters(value: Any, default: int = 3) -> int:
    """目标组数下限 2；不再硬顶 12（上限由调用方 ``min(k, n)`` 按宇宙规模收束）。"""
    try:
        k = int(value)
    except (TypeError, ValueError):
        return int(default)
    return max(2, k)


def clamp_watching_limit(value: Any, default: int = 8) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return int(default)
    return max(3, min(n, 20))


def merge_cluster_universe(
    watchlist: Sequence[Any],
    holdings: Sequence[Any],
    *,
    watching_limit: int = 8,
    universe_mode: str = "watching",
) -> Dict[str, Any]:
    """构建聚类宇宙。

    默认 ``universe_mode=watching``：**全部**观察池（不截前 N）。
    ``holdings``：仅纸面持仓（旧默认）。
    ``union``：观察池前 N ∪ 全部纸面持仓（``watching_limit`` 仅约束观察侧）。
    """
    limit = clamp_watching_limit(watching_limit, 8)
    mode = str(universe_mode or "watching").strip().lower()
    if mode in ("paper", "holding", "holdings_only"):
        mode = "holdings"
    if mode in ("watch", "watchlist", "watching_only"):
        mode = "watching"

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

    if mode == "watching":
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
            # 全量模式：limit 记实际只数，便于 UI/报告展示
            "watching_limit": len(watching_codes),
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
    _centers_from_labels,
    _feature_union,
    _ols_coef_dict,
    _relabel_non_negative,
    agglomerative_cut_by_tau,
    agglomerative_labels,
    apply_beta_scale_transform,
    auto_cluster_range,
    beta_delta_mismatch,
    cluster_beta_vectors,
    cluster_diameter,
    cluster_within_stats,
    coef_vector_from_report,
    complete_linkage_distance,
    default_max_cluster_size,
    default_n_clusters,
    eject_by_group_beta_delta,
    eject_far_from_group_beta,
    enforce_diameter_cap,
    enforce_min_cluster_size,
    fit_beta_scale_transform,
    kmeans_labels,
    promote_outliers_to_singleton_clusters,
    refine_cluster_labels,
    resolve_beta_scale,
    scale_beta_matrix,
    silhouette_score,
    split_oversized_clusters,
    within_dist_tau,
)


def group_ts_ic_panel(
    xs: Sequence[Dict[str, Any]],
    ys: Sequence[float],
    feature_names: Sequence[str],
) -> Dict[str, Any]:
    """单票组回退：堆叠时序 IC（无日截面序列，ICIR 恒 null）。"""
    from core.signal.factor_corr import pearson_with_reason

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
    out = compute_factor_cross_section_ic(
        bars_map,
        horizon_days=horizon_days,
        min_history=12,
        max_window=30,
        min_names=min_names,
        fundamentals_by_code=fundamentals_by_code,
        pit_fundamentals=bool(pit_fundamentals),
        factor_names=names or None,
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


def compute_factor_ols_cluster_report(
    stock_panels: List[Dict[str, Any]],
    *,
    horizon_days: int = 3,
    ridge_lambda: float = 0.0,
    n_clusters: Optional[int] = None,
    pit_fundamentals: bool = True,
    l2_normalize_betas: Optional[bool] = None,
    beta_scale: str = "feature_zscore",
    cluster_method: str = "hierarchical",
    cluster_linkage: str = "average",
    within_dist_quantile: float = 0.75,
) -> Dict[str, Any]:
    """逐票 OLS → β 聚类 → 组内池 OLS + 小步权。

    默认 / 显式 ``n_clusters`` 均切到目标 k；不严踢异质升单票组（否则手动 k 会炸组数）。
    异质用探针核对。自动 k 另加超大组二分。
    """
    lam = clamp_ridge_lambda(ridge_lambda, 0.0)
    k_req = None if n_clusters is None else clamp_n_clusters(n_clusters, 3)
    horizon_days = max(1, min(int(horizon_days or 3), 10))
    use_pit = bool(pit_fundamentals)
    scale_mode = resolve_beta_scale(beta_scale, l2_normalize_betas=l2_normalize_betas)
    method_s = str(cluster_method or "hierarchical").strip().lower()
    link_s = str(cluster_linkage or "average").strip().lower()
    tau_q = float(within_dist_quantile)

    per_stock: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    panel_by_code: Dict[str, Dict[str, Any]] = {}

    for item in stock_panels or []:
        code = str(item.get("code") or item.get("stock_code") or "").strip()
        bars = item.get("bars") or []
        if not code:
            continue
        if not bars:
            skipped.append({"code": code, "reason": "无日线"})
            continue
        xs, ys = collect_subscore_forward_panel(
            bars,
            horizon_days=horizon_days,
            index_bars=item.get("index_bars"),
            fundamentals=item.get("fundamentals"),
            stock_code=code,
            pit_fundamentals=use_pit,
        )
        if not ys:
            skipped.append({"code": code, "reason": "面板为空"})
            continue
        fit = fit_factor_ols_from_panel(
            xs,
            ys,
            horizon_days=horizon_days,
            fundamentals_used=bool(item.get("fundamentals")) or use_pit,
            pit_fundamentals=use_pit,
            mode="single",
            stock_codes=[code],
            ridge_lambda=lam,
        )
        if not fit.get("success"):
            skipped.append(
                {"code": code, "reason": str(fit.get("error") or "单票 OLS 失败")}
            )
            continue
        fit["stock_code"] = code
        per_stock.append(fit)
        panel_by_code[code] = {
            "code": code,
            "bars": bars,
            "index_bars": item.get("index_bars"),
            "fundamentals": item.get("fundamentals"),
            "xs": xs,
            "ys": ys,
        }

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
        }

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
    raw = _beta_matrix(per_stock, feature_names)
    x, scale_tf = fit_beta_scale_transform(raw, scale_mode)
    clustered = cluster_beta_vectors(
        x,
        method=method_s,
        n_clusters=k_req,
        seed=42,
        cluster_linkage=link_s,
        within_dist_quantile=tau_q,
    )
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
                    max_hetero_factors=3,
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
    eject_by_idx: Dict[int, Dict[str, Any]] = {}
    for e in list(clustered.get("ejects") or []) + pool_ejects:
        if "index" in e:
            eject_by_idx[int(e["index"])] = e
    labels, promoted_idx = promote_outliers_to_singleton_clusters(labels)
    labels = _relabel_non_negative(labels)
    promoted_set = set(int(i) for i in promoted_idx)
    within_stats = cluster_within_stats(x, labels)
    within_by_id = {
        int(s["cluster_id"]): s for s in within_stats if "cluster_id" in s
    }
    k = int(len(set(int(v) for v in labels if int(v) >= 0)))
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
        }

    clusters: List[Dict[str, Any]] = []
    for cid in range(k):
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
            "members": members,
            "member_count": len(members),
            "singleton": len(members) < 2,
            "outlier_singleton": from_outlier and len(members) < 2,
            "eject_reason": eject_meta.get("reason") if from_outlier else None,
            "max_within_dist": wstat.get("max_within_dist"),
            "mean_center_dist": wstat.get("mean_center_dist"),
            "within_dist_cap": tau_used,
        }
        all_xs = []
        all_ys: List[float] = []
        for code in members:
            panel = panel_by_code.get(code) or {}
            all_xs.extend(panel.get("xs") or [])
            all_ys.extend(panel.get("ys") or [])

        if len(members) < 2:
            cluster["factor_ic_panel"] = _cluster_factor_ic_panel(
                members,
                panel_by_code=panel_by_code,
                feature_names=feature_names,
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
            clusters.append(cluster)
            continue

        pooled = fit_factor_ols_from_panel(
            all_xs,
            all_ys,
            horizon_days=horizon_days,
            fundamentals_used=False,
            pit_fundamentals=use_pit,
            mode="watching_pooled",
            stock_codes=members,
            ridge_lambda=lam,
        )
        pooled["mode"] = "cluster_pooled"
        cluster["ols"] = _public_cluster_ols(pooled, mode="cluster_pooled")
        cluster["return_model"] = _return_model_from_ols(cluster["ols"])
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
            for i in member_idx:
                d = float(np.linalg.norm(x[i] - g_scaled))
                dd = beta_delta_mismatch(raw[i], g_raw, active_mask=amask)
                gaps.append(
                    {
                        "code": codes[i],
                        "distance_to_group_beta": round(d, 4),
                        "max_abs_delta": dd["max_abs_delta"],
                        "mean_abs_delta": dd["mean_abs_delta"],
                        "hetero_count": dd["hetero_count"],
                        "within_cap": round(tau_used, 4),
                    }
                )
            cluster["factor_ic_panel"] = _cluster_factor_ic_panel(
                members,
                panel_by_code=panel_by_code,
                feature_names=feature_names,
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
                feature_names=feature_names,
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
                feature_names=feature_names,
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
        "n_clusters_auto": bool(clustered.get("auto_k")),
        "target_k": clustered.get("target_k"),
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
        "beta_scale": scale_mode,
        "l2_normalize_betas": scale_mode == "l2",
        "cluster_balance": _cluster_balance_stats(clusters),
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
                    "（自动）"
                    if clustered.get("auto_k")
                    else ("（手动）" if clustered.get("target_k") is not None else "")
                )
                + f"，共 {k} 组（其中多票组 {n_multi}）；"
            )
            + (f"单票离群组 {n_singleton_out}；" if n_singleton_out else "")
            + (f"组β校验触发 {n_pool_ej}；" if n_pool_ej else "")
            + "多票组池 OLS → 因子系数 return_model（同组同建模）。"
            + (
                " 默认关闭逐日 PIT 财务以加速；仅作分组探针。"
                if not use_pit
                else " 已开 PIT 财务，耗时更长。"
            )
            + " 研究探针，不写 signal_config。"
        ),
    }


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
