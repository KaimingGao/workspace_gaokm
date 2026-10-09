"""因子面板 OLS 拟合（domain 层；不依赖 quant）。"""

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.signal.config import load_signal_config


def clamp_ridge_lambda(value: Any, default: float = 0.0) -> float:
    """研究用 Ridge λ：``[0, 100]``；非法则回落 default。"""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(x) or x < 0:
        return float(default)
    return float(min(x, 100.0))


def _mean_impute_feature_keys(
    xs: List[Dict[str, Optional[float]]],
    keys: Sequence[str],
) -> Tuple[List[Dict[str, Optional[float]]], Dict[str, Any]]:
    """指定列缺测按观测均值填；不改动原行对象。无观测的列跳过。

    填完后该列 z-score 为 0，与 live ``_predict_rows(impute_missing=True)`` 一致，
    避免「尚未定义」的键把整行踢出完整子面板。
    """
    want = [str(n) for n in keys if n]
    meta: Dict[str, Any] = {
        "imputed_keys": [],
        "impute_means": {},
        "impute_n_obs": {},
        "impute_n_filled": {},
        "impute_skipped": [],
    }
    if not want or not xs:
        return xs, meta

    means: Dict[str, float] = {}
    n_obs: Dict[str, int] = {}
    for name in want:
        vals: List[float] = []
        for row in xs:
            if not isinstance(row, dict):
                continue
            v = row.get(name)
            if v is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(fv):
                vals.append(fv)
        if not vals:
            meta["impute_skipped"].append(name)
            continue
        means[name] = sum(vals) / float(len(vals))
        n_obs[name] = len(vals)

    if not means:
        return xs, meta

    n_filled: Dict[str, int] = {name: 0 for name in means}
    out: List[Dict[str, Optional[float]]] = []
    for row in xs:
        if not isinstance(row, dict):
            out.append(row)
            continue
        copied: Optional[Dict[str, Optional[float]]] = None
        for name, mu in means.items():
            v = row.get(name)
            missing = v is None
            if not missing:
                try:
                    fv = float(v)
                    if not math.isfinite(fv):
                        missing = True
                except (TypeError, ValueError):
                    missing = True
            if not missing:
                continue
            if copied is None:
                copied = dict(row)
            copied[name] = mu
            n_filled[name] = n_filled.get(name, 0) + 1
        out.append(copied if copied is not None else row)

    meta["imputed_keys"] = list(means.keys())
    meta["impute_means"] = {k: round(float(v), 6) for k, v in means.items()}
    meta["impute_n_obs"] = dict(n_obs)
    meta["impute_n_filled"] = {k: int(n_filled.get(k) or 0) for k in means}
    return out, meta


def _zscore_complete_panel(
    xs: List[Dict[str, float]],
    active: List[str],
    *,
    eps: float = 1e-6,
) -> Tuple[List[Dict[str, float]], Dict[str, float], Dict[str, float]]:
    """对完整子面板做样本内 z-score；方差过小的列 std 置 1（随后会被常数剔除兜底）。"""
    means: Dict[str, float] = {}
    stds: Dict[str, float] = {}
    n = len(xs)
    if n == 0 or not active:
        return xs, means, stds
    for name in active:
        vals = [float(row[name]) for row in xs]
        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / n
        std = math.sqrt(var) if var > eps * eps else 1.0
        means[name] = mean
        stds[name] = std
    xs_z = [
        {name: (float(row[name]) - means[name]) / stds[name] for name in active}
        for row in xs
    ]
    return xs_z, means, stds


def _prepare_complete_panel(
    xs: List[Dict[str, Optional[float]]],
    ys: List[float],
    feature_names: List[str],
    *,
    min_samples_over_p: int = 3,
    eps: float = 1e-6,
    min_std: float = 5.0,
    min_std_exempt: Optional[Sequence[str]] = None,
    impute_keys: Optional[Sequence[str]] = None,
) -> Tuple[
    Optional[List[Dict[str, float]]],
    Optional[List[float]],
    List[str],
    List[str],
    Dict[str, Any],
]:
    """挑选可用因子与完整行：允许多因子缺测，优先保留覆盖好的因子。

    ``min_std``：完整子面板上因子原始分标准差下限（0–100 分制）。
    过低说明准常数（如离散规模档、gap_risk），进 z-score 后假中性/小扰动会被放大；
    默认 5.0，与 live 组模型里 size/gap_risk/amihud 的炸分阈值对齐。
    ``min_std_exempt``：跳过该门槛的列（如 rem 的 gap_pct，单位是百分点而非 0–100 分）。
    真常数列（max−min≤eps）仍会剔除。
    ``impute_keys``：这些列缺测按观测均值填后再挑完整行（标准化后 z=0），
    与 live 缺键填均值对齐；未列出的列仍缺则整行丢掉。
    """
    exempt = {str(n) for n in (min_std_exempt or []) if n}
    n_raw = len(ys)
    meta: Dict[str, Any] = {
        "raw_sample_count": n_raw,
        "dropped_sparse": [],
        "dropped_constant": [],
        "dropped_low_variance": [],
        "dropped_for_coverage": [],
        "min_std": float(min_std),
        "min_std_exempt": sorted(exempt),
        "imputed_keys": [],
    }
    impute_set = {str(n) for n in (impute_keys or []) if n}
    impute_want = [n for n in feature_names if n in impute_set]
    if impute_want:
        xs, impute_meta = _mean_impute_feature_keys(xs, impute_want)
        meta.update(impute_meta)
    if n_raw < 4:
        return None, None, [], feature_names[:], meta

    # 覆盖门槛不宜过严：完整子面板上再验方差
    min_obs = max(8, min(n_raw // 4, 40))
    candidates: List[str] = []
    for name in feature_names:
        vals = [float(row[name]) for row in xs if row.get(name) is not None]
        if len(vals) < min_obs:
            meta["dropped_sparse"].append(name)
            continue
        if max(vals) - min(vals) <= eps:
            meta["dropped_constant"].append(name)
            continue
        candidates.append(name)

    if not candidates:
        return None, None, [], feature_names[:], meta

    active = list(candidates)
    min_std_eff = float(min_std)
    while active:
        rows_idx = [
            i
            for i, row in enumerate(xs)
            if all(row.get(name) is not None for name in active)
        ]
        n = len(rows_idx)
        p = len(active)
        if n < max(4, p + min_samples_over_p):
            miss = sorted(
                (
                    sum(1 for row in xs if row.get(name) is None),
                    name,
                )
                for name in active
            )
            drop = miss[-1][1]
            active.remove(drop)
            meta["dropped_for_coverage"].append(drop)
            continue

        # 完整子面板上再剔常数列 / 低方差列
        still_var: List[str] = []
        low_var_batch: List[Dict[str, Any]] = []
        for name in active:
            vals = [float(xs[i][name]) for i in rows_idx]
            if max(vals) - min(vals) <= eps:
                meta["dropped_constant"].append(name)
                continue
            mean = sum(vals) / len(vals)
            var = sum((v - mean) ** 2 for v in vals) / len(vals)
            std = math.sqrt(var) if var > 0 else 0.0
            if (
                min_std_eff > 0
                and std < min_std_eff
                and name not in exempt
            ):
                low_var_batch.append(
                    {"name": name, "std": round(std, 4), "n": len(vals)}
                )
                continue
            still_var.append(name)
        # 若低方差剔光后可用因子过少，回退：只剔真常数，保留低方差列以免短面板无法拟合
        min_active = 2
        if len(still_var) < min_active and low_var_batch:
            meta["min_std_relaxed"] = True
            meta["min_std_relaxed_from"] = float(min_std)
            meta["dropped_low_variance_skipped"] = list(low_var_batch)
            still_var = still_var + [d["name"] for d in low_var_batch]
            low_var_batch = []
            min_std_eff = 0.0  # 后续轮次不再按低方差剔，避免死循环
        else:
            meta["dropped_low_variance"].extend(low_var_batch)
        if len(still_var) < len(active):
            active = still_var
            if not active:
                break
            continue

        xs_c = [{name: float(xs[i][name]) for name in active} for i in rows_idx]
        ys_c = [float(ys[i]) for i in rows_idx]
        excluded = (
            list(meta["dropped_sparse"])
            + list(meta["dropped_constant"])
            + [
                d["name"] if isinstance(d, dict) else d
                for d in meta["dropped_low_variance"]
            ]
            + list(meta["dropped_for_coverage"])
        )
        meta["complete_row_indices"] = list(rows_idx)
        meta["complete_sample_count"] = len(rows_idx)
        meta["active_feature_count"] = len(active)
        return xs_c, ys_c, active, excluded, meta

    excluded = (
        list(meta["dropped_sparse"])
        + list(meta["dropped_constant"])
        + [
            d["name"] if isinstance(d, dict) else d
            for d in meta["dropped_low_variance"]
        ]
        + list(meta["dropped_for_coverage"])
    )
    return None, None, [], excluded, meta


def _qr_solve_np(x: np.ndarray, y: np.ndarray) -> Optional[np.ndarray]:
    """最小二乘：X = QR（economy），解 R β = Qᵀ y；秩亏返回 None。"""
    if x.ndim != 2 or y.ndim != 1 or x.shape[0] != y.shape[0] or x.shape[1] < 1:
        return None
    n, m = x.shape
    q, r = np.linalg.qr(x, mode="reduced")
    diag = np.abs(np.diag(r))
    if diag.size < m:
        return None
    scale = float(diag.max()) if diag.size else 0.0
    tol = max(n, m) * np.finfo(np.float64).eps * max(scale, 1.0)
    if float(diag.min()) <= tol:
        return None
    try:
        beta = np.linalg.solve(r, q.T @ y)
    except np.linalg.LinAlgError:
        return None
    if not np.all(np.isfinite(beta)):
        return None
    return beta


def _qr_lstsq(design: List[List[float]], ys: List[float]) -> Optional[List[float]]:
    """最小二乘：X = QR（economy），解 R β = Qᵀ y；秩亏返回 None。

    相对正规方程 + 高斯消元，条件数不平方，数值更稳。
    """
    beta = _qr_solve_np(
        np.asarray(design, dtype=np.float64),
        np.asarray(ys, dtype=np.float64),
    )
    if beta is None:
        return None
    return [float(v) for v in beta]


def _ridge_lstsq(
    design: List[List[float]],
    ys: List[float],
    ridge_lambda: float,
) -> Optional[List[float]]:
    """Ridge：min ||y−Xβ||² + λ Σ_{j≥1} β_j²（截距 β₀ 不惩罚）。

    用 Tikhonov 增广矩阵再走 QR，避免显式求 (XᵀX+λI)⁻¹。
    """
    lam = clamp_ridge_lambda(ridge_lambda, 0.0)
    if lam <= 0:
        return _qr_lstsq(design, ys)

    x = np.asarray(design, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    if x.ndim != 2 or y.ndim != 1 or x.shape[0] != y.shape[0] or x.shape[1] < 1:
        return None

    m = int(x.shape[1])
    if m <= 1:
        return _qr_lstsq(design, ys)

    sqrt_l = math.sqrt(lam)
    extra = np.zeros((m - 1, m), dtype=np.float64)
    extra[:, 1:] = np.eye(m - 1, dtype=np.float64) * sqrt_l
    x_aug = np.vstack([x, extra])
    y_aug = np.concatenate([y, np.zeros(m - 1, dtype=np.float64)])
    beta = _qr_solve_np(x_aug, y_aug)
    if beta is None:
        return None
    return [float(v) for v in beta]


def _fit_ols_once(
    xs: List[Dict[str, float]],
    ys: List[float],
    active: List[str],
    *,
    ridge_lambda: float = 0.0,
    sample_weights: Optional[List[float]] = None,
) -> Optional[Dict[str, Any]]:
    n = len(ys)
    p = len(active)
    if n < 4 or n != len(xs) or p < 1 or n < p + 3:
        return None

    design: List[List[float]] = []
    for row in xs:
        design.append([1.0] + [float(row[name]) for name in active])

    lam = clamp_ridge_lambda(ridge_lambda, 0.0)
    y_fit = [float(y) for y in ys]
    design_fit = design
    # 样本权：对 (X,y) 乘 √w 再走 QR / Ridge（等价加权最小二乘）
    if sample_weights is not None and len(sample_weights) == n:
        design_w: List[List[float]] = []
        y_w: List[float] = []
        w_used = 0
        for i in range(n):
            try:
                w = float(sample_weights[i])
            except (TypeError, ValueError):
                w = 1.0
            if not math.isfinite(w) or w <= 0:
                continue
            sw = math.sqrt(w)
            design_w.append([sw * v for v in design[i]])
            y_w.append(sw * float(ys[i]))
            w_used += 1
        if w_used < max(4, p + 3):
            return None
        design_fit, y_fit = design_w, y_w

    beta = (
        _ridge_lstsq(design_fit, y_fit, lam)
        if lam > 0
        else _qr_lstsq(design_fit, y_fit)
    )
    if beta is None:
        return None

    # R²：在原始（未 √w 变换）设计上算，便于与旧口径对照
    y_mean = sum(float(y) for y in ys) / n
    ss_tot = sum((float(y) - y_mean) ** 2 for y in ys)
    ss_res = 0.0
    for i in range(n):
        pred = sum(beta[j] * design[i][j] for j in range(p + 1))
        ss_res += (float(ys[i]) - pred) ** 2
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else None
    return {
        "intercept": round(float(beta[0]), 6),
        "beta": beta,
        "r_squared": round(r2, 4) if r2 is not None else None,
        "sample_count": n,
        "active": list(active),
        "solver": "ridge" if lam > 0 else "qr",
        "ridge_lambda": lam,
    }


def _exclusion_reasons_map(
    prep_meta: Optional[Dict[str, Any]],
    *,
    dropped_collinear: Optional[List[str]] = None,
    excluded: Optional[List[str]] = None,
) -> Dict[str, str]:
    """因子名 → 未入模原因码：sparse|constant|low_variance|coverage|collinear|other。"""
    meta = prep_meta or {}
    reasons: Dict[str, str] = {}
    for name in meta.get("dropped_sparse") or []:
        reasons[str(name)] = "sparse"
    for name in meta.get("dropped_constant") or []:
        reasons.setdefault(str(name), "constant")
    for item in meta.get("dropped_low_variance") or []:
        name = item.get("name") if isinstance(item, dict) else item
        if name:
            reasons.setdefault(str(name), "low_variance")
    for name in meta.get("dropped_for_coverage") or []:
        reasons.setdefault(str(name), "coverage")
    for name in dropped_collinear or []:
        reasons.setdefault(str(name), "collinear")
    for name in excluded or []:
        reasons.setdefault(str(name), "other")
    return reasons


def _ols_with_intercept(
    xs: List[Dict[str, float]],
    ys: List[float],
    active: List[str],
    excluded: List[str],
    *,
    ridge_lambda: float = 0.0,
    sample_weights: Optional[List[float]] = None,
) -> Optional[Dict[str, Any]]:
    """拟合 OLS / Ridge；纯 OLS 奇异时逐个剔除共线因子。Ridge 尽量保留全部入模列。"""
    lam = clamp_ridge_lambda(ridge_lambda, 0.0)
    work = list(active)
    dropped_collinear: List[str] = []
    fit = None
    while work:
        fit = _fit_ols_once(
            xs, ys, work, ridge_lambda=lam, sample_weights=sample_weights
        )
        if fit is not None:
            break
        if lam > 0:
            # Ridge 仍失败极少见；不再剔列以免掩盖数值问题
            break
        # 丢掉最右侧因子（通常较新/覆盖差）；保留尽量多的左侧核心因子
        dropped_collinear.append(work.pop())
    if fit is None:
        return None

    final_active = fit["active"]
    all_excluded = list(excluded) + list(reversed(dropped_collinear))
    coef_map = {
        name: round(float(fit["beta"][i + 1]), 6)
        for i, name in enumerate(final_active)
    }
    for name in all_excluded:
        coef_map[name] = None

    return {
        "intercept": fit["intercept"],
        "coefficients": coef_map,
        "active_features": final_active,
        "excluded_features": all_excluded,
        "r_squared": fit["r_squared"],
        "sample_count": fit["sample_count"],
        "feature_count": len(final_active) + len(all_excluded),
        "active_feature_count": len(final_active),
        "rank": len(final_active) + 1,
        "rank_deficient": bool(dropped_collinear),
        "dropped_collinear": dropped_collinear,
        "solver": fit.get("solver") or ("ridge" if lam > 0 else "qr"),
        "ridge_lambda": lam,
    }


def fit_factor_ols_from_panel(
    xs: List[Dict[str, Optional[float]]],
    ys: List[float],
    *,
    horizon_days: int = 3,
    fundamentals_used: bool = False,
    pit_fundamentals: bool = True,
    mode: str = "single",
    stock_codes: Optional[List[str]] = None,
    feature_zscore: bool = True,
    ridge_lambda: float = 0.0,
    select_ridge: bool = False,
    collinearity_policy: str = "drop_redundant",
    respect_regime: bool = False,
    y_spec: Optional[Dict[str, Any]] = None,
    sample_weights: Optional[List[float]] = None,
    feature_names: Optional[List[str]] = None,
    min_std: float = 5.0,
    min_std_exempt: Optional[Sequence[str]] = None,
    impute_keys: Optional[Sequence[str]] = None,
    row_dates: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """对已对齐的 (sub_scores, forward return) 面板拟合 OLS / Ridge。

    有 ``row_dates`` 时按当天截面 z-score，否则退回训练窗样本内 z-score；
    β 为「因子高 1σ → 前瞻收益变多少百分点」。负号表示偏相关为负，勿直接与 config.weights 比大小。
    ``ridge_lambda>0`` 时收缩斜率系数（截距不惩罚），共线时尽量保留因子。
    B3：``select_ridge=True`` 时网格选 λ；``collinearity_policy`` 控趋势族冗余。
    ``sample_weights``：与 ``xs/ys`` 等长的非负样本权（组内软异质降权）；拟合时 √w 变换。
    ``feature_names``：可选覆盖默认注册因子集（τ 头可并入 gap_pct 等）。
    ``min_std`` / ``min_std_exempt``：透传 ``_prepare_complete_panel``（分制因子 vs 百分点列）。
    ``impute_keys``：透传；缺测按列均值填、不整行丢掉（与 live z=0 对齐）。
    """
    from core.research.beta_accuracy import (
        apply_collinearity_policy,
        build_y_spec,
        sample_fingerprint,
        select_ridge_lambda,
    )

    lam = clamp_ridge_lambda(ridge_lambda, 0.0)
    from core.signal.factors.alpha158 import (
        ALPHA158_FACTOR_KEY,
        expand_ridge_feature_names,
        is_alpha158_raw_key,
        merge_alpha158_min_std_exempt,
    )

    if feature_names is None:
        # 默认：注册因子 + 面板 raw_alpha158_*（去掉常数 alpha158 分）
        factor_names = expand_ridge_feature_names(xs)
    else:
        factor_names = list(feature_names)
        # 已有 raw_alpha158_* 时丢掉常数 sub_score 列，避免假中性 50 进模
        if any(is_alpha158_raw_key(n) for n in factor_names):
            factor_names = [
                n for n in factor_names if str(n) != ALPHA158_FACTOR_KEY
            ]
    exempt_merged = merge_alpha158_min_std_exempt(factor_names, min_std_exempt)
    cfg = load_signal_config()
    current_weights = dict(cfg.get("weights") or {})
    xs_c, ys_c, active, excluded, prep_meta = _prepare_complete_panel(
        xs,
        ys,
        factor_names,
        min_std=float(min_std),
        min_std_exempt=exempt_merged,
        impute_keys=impute_keys,
    )
    collinearity_meta: Dict[str, Any] = {}
    ridge_select_meta: Dict[str, Any] = {}
    z_means: Dict[str, float] = {}
    z_stds: Dict[str, float] = {}
    zscore_scope = ""
    fit = None
    row_weights: Optional[List[float]] = None
    if (
        sample_weights is not None
        and xs_c is not None
        and isinstance(prep_meta.get("complete_row_indices"), list)
    ):
        idxs = prep_meta["complete_row_indices"]
        if len(sample_weights) == len(xs) and len(idxs) == len(xs_c):
            row_weights = []
            for i in idxs:
                try:
                    w = float(sample_weights[int(i)])
                except (TypeError, ValueError, IndexError):
                    w = 1.0
                if not math.isfinite(w) or w <= 0:
                    w = 1e-6
                row_weights.append(float(w))
    if xs_c is not None and ys_c is not None and active:
        kept, dropped_red, collinearity_meta = apply_collinearity_policy(
            xs_c,
            list(active),
            policy=collinearity_policy,
            ys=ys_c,
        )
        if dropped_red:
            excluded = list(excluded) + list(dropped_red)
            active = kept
            prep_meta = dict(prep_meta)
            prep_meta["dropped_collinear_policy"] = list(dropped_red)
        xs_fit = xs_c
        if feature_zscore and active:
            from core.research.feature_standardize import (
                cross_section_zscore_dicts,
                dates_for_complete_rows,
            )

            dates_c = dates_for_complete_rows(
                row_dates, prep_meta.get("complete_row_indices") or []
            )
            if dates_c is not None and len(dates_c) == len(xs_c):
                xs_fit = cross_section_zscore_dicts(xs_c, active, dates_c)
                zscore_scope = "cross_section"
            else:
                xs_fit, z_means, z_stds = _zscore_complete_panel(xs_c, active)
        if select_ridge and active and row_weights is None:
            # 加权路径跳过选 λ（验证切分与权未对齐）；沿用传入 λ
            ridge_select_meta = select_ridge_lambda(xs_fit, ys_c, list(active))
            lam = float(ridge_select_meta.get("ridge_lambda_selected") or 0.0)
        if active:
            fit = _ols_with_intercept(
                xs_fit,
                ys_c,
                active,
                excluded,
                ridge_lambda=lam,
                sample_weights=row_weights,
            )

    y_spec_out = y_spec or build_y_spec(horizon_days=horizon_days)
    n_names = len(stock_codes) if stock_codes is not None else (1 if mode == "single" else 0)
    n_names_i = int(n_names or 1)
    # 单票/双票组是设计内的：不能用「全市场截面 min_names=3」否掉 promote
    # 门槛 = min(3, 实际组员数)，只要求「组员齐」+ 足够 n_obs
    min_names_gate = max(1, min(3, n_names_i))
    fp = sample_fingerprint(
        n_obs=int((fit or {}).get("sample_count") or prep_meta.get("raw_sample_count") or 0),
        n_names=n_names_i,
        dropped={
            "sparse": prep_meta.get("dropped_sparse") or [],
            "constant": prep_meta.get("dropped_constant") or [],
            "low_variance": [
                (d.get("name") if isinstance(d, dict) else d)
                for d in (prep_meta.get("dropped_low_variance") or [])
            ],
            "coverage": prep_meta.get("dropped_for_coverage") or [],
            "collinear_policy": prep_meta.get("dropped_collinear_policy") or [],
        },
        min_names=min_names_gate,
    )

    task = "factor_ols_pool" if mode == "watching_pooled" else "factor_ols"
    if not fit:
        raw_n = prep_meta.get("raw_sample_count", len(ys))
        excl = excluded or prep_meta.get("dropped_sparse") or []
        err = {
            "success": False,
            "error": (
                "样本不足或矩阵奇异，无法拟合 OLS"
                f"（对齐样本 {raw_n}，注册因子 {len(factor_names)}；"
                "缺测因子已尽量剔除仍不够，可加大 lookback / 研究池 / ridge λ）"
            ),
            "sample_count": raw_n,
            "n_obs": raw_n,
            "feature_count": len(factor_names),
            "horizon_days": horizon_days,
            "y_spec": y_spec_out,
            "sample_fingerprint": fp,
            "excluded_features": excl,
            "exclusion_reasons": _exclusion_reasons_map(prep_meta, excluded=excl),
            "prep_meta": prep_meta,
            "collinearity_meta": collinearity_meta,
            "ridge_select": ridge_select_meta,
            "feature_zscore": bool(feature_zscore),
            "ridge_lambda": lam,
            "ridge_lambda_selected": lam,
            "collinearity_policy": collinearity_policy,
            "respect_regime": bool(respect_regime),
            "solver": "ridge" if lam > 0 else "qr",
            "current_weights": {k: round(float(v), 4) for k, v in current_weights.items()},
            "task": task,
            "mode": mode,
        }
        if stock_codes is not None:
            err["stock_codes"] = list(stock_codes)
            err["stock_count"] = len(stock_codes)
        return err

    note_parts = [
        "面板 OLS 仅供研究对比，不自动写 signal_config。",
        (
            "研究路径已 respect_regime（与 live 启用因子对齐）。"
            if respect_regime
            else "研究路径全量注册因子（不经 regime 择时白名单）。"
        ),
        "缺测/常数/覆盖不足因子已自动剔除后再拟合。",
    ]
    if collinearity_meta.get("dropped"):
        note_parts.append(
            f"共线策略 {collinearity_policy}：剔除 {collinearity_meta.get('dropped')}"
        )
    if lam > 0:
        note_parts.append(
            f"Ridge λ={lam:g}：斜率 L2 收缩、截距不惩罚；缓解共线，β 仍非生产权重。"
        )
    if feature_zscore:
        if zscore_scope == "cross_section":
            note_parts.append(
                "系数基于当天截面 z-score：β≈相对当日同伴高 1σ 时前瞻收益变多少百分点；"
                "负号=偏相关为负，勿与 config.weights 同量级对比。"
            )
        else:
            note_parts.append(
                "系数基于样本内 z-score：β≈因子高 1σ 时前瞻收益变多少百分点；"
                "负号=偏相关为负，勿与 config.weights 同量级对比。"
            )
    if mode == "watching_pooled":
        note_parts.append(
            "模式：研究池堆叠时序面板（非逐日截面 Fama–MacBeth）；系数比单票稳，仍非生产权重。"
        )
    else:
        note_parts.append("单票 walk-forward 非截面回归；样本少时系数不稳定。")
    if row_weights is not None:
        note_parts.append("已按 sample_weights 做加权 OLS（√w 变换）。")
    imputed = [str(n) for n in (prep_meta.get("imputed_keys") or []) if n]
    if imputed:
        filled = prep_meta.get("impute_n_filled") or {}
        n_fill = sum(int(filled.get(k) or 0) for k in imputed)
        note_parts.append(
            f"缺测 {imputed} 按列均值填（{n_fill} 格，标准化后 z=0），与 live 预测一致。"
        )
    if fundamentals_used:
        note_parts.append(
            "value/quality 等基本面按决策日 PIT（缺史跳过）；非静默最新快照。"
            if pit_fundamentals
            else "value/quality 若启用 fundamentals 则为快照估值，非 point-in-time。"
        )
    if fit.get("excluded_features"):
        note_parts.append(
            f"未入模 {len(fit['excluded_features'])} 个因子（缺测/常数/覆盖/共线）。"
        )

    excl_reasons = _exclusion_reasons_map(
        prep_meta,
        dropped_collinear=list(fit.get("dropped_collinear") or [])
        + list(collinearity_meta.get("dropped") or []),
        excluded=fit.get("excluded_features") or [],
    )
    out: Dict[str, Any] = {
        "success": True,
        "task": task,
        "mode": mode,
        "horizon_days": horizon_days,
        "y_spec": y_spec_out,
        "sample_count": fit["sample_count"],
        "n_obs": fit["sample_count"],
        "sample_fingerprint": fp,
        "feature_count": fit["feature_count"],
        "r_squared": fit["r_squared"],
        "intercept": fit["intercept"],
        "coefficients": fit["coefficients"],
        "active_features": fit.get("active_features") or [],
        "excluded_features": fit.get("excluded_features") or [],
        "exclusion_reasons": excl_reasons,
        "feature_zscore": bool(feature_zscore),
        "zscore_scope": zscore_scope,
        "zscore_means": {k: round(v, 4) for k, v in z_means.items()},
        "zscore_stds": {k: round(v, 4) for k, v in z_stds.items()},
        "current_weights": {k: round(float(v), 4) for k, v in current_weights.items()},
        "fundamentals_used": fundamentals_used,
        "pit_fundamentals": bool(pit_fundamentals),
        "rank_deficient": fit.get("rank_deficient"),
        "solver": fit.get("solver") or ("ridge" if lam > 0 else "qr"),
        "ridge_lambda": lam,
        "ridge_lambda_selected": lam,
        "collinearity_policy": collinearity_policy,
        "collinearity_meta": collinearity_meta,
        "ridge_select": ridge_select_meta or None,
        "respect_regime": bool(respect_regime),
        "prep_meta": prep_meta,
        "weighted_ols": bool(row_weights is not None),
        "note": " ".join(note_parts),
        "track": "B1-B3",
    }
    if row_weights is not None and row_weights:
        out["mean_sample_weight"] = round(
            float(sum(row_weights) / max(1, len(row_weights))), 4
        )
    if stock_codes is not None:
        out["stock_codes"] = list(stock_codes)
        out["stock_count"] = len(stock_codes)
        out["n_names"] = len(stock_codes)
    return out
