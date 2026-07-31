"""因子面板 OLS（研究用，不自动写 signal_config）。

- 单票 walk-forward：``compute_factor_ols_report``
- 研究池堆叠时序：``compute_factor_ols_pooled_report``（非逐日截面 Fama–MacBeth）
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from core.signal.config import load_signal_config
from core.signal.factor_registry import registered_factor_names
from core.signal.scorer import score_bars


def _forward_return(bars: List[dict], idx: int, horizon: int) -> Optional[float]:
    if idx + horizon >= len(bars):
        return None
    entry = bars[idx].get("close")
    exit_p = bars[idx + horizon].get("close")
    if not entry:
        return None
    return (exit_p / entry - 1.0) * 100.0


def collect_subscore_forward_panel(
    bars: List[dict],
    *,
    horizon_days: int = 3,
    min_history: int = 12,
    max_window: int = 30,
    index_bars: Optional[List[dict]] = None,
    fundamentals: Optional[dict] = None,
    stock_code: Optional[str] = None,
    pit_fundamentals: bool = True,
) -> Tuple[List[Dict[str, Optional[float]]], List[float]]:
    """对齐子因子与 forward return，供 OLS / 研究面板复用。

    因子可缺测（None）；不再要求当日全部因子齐全，否则加厚后样本会被清空。
    E2：默认按决策日 PIT 解析财务。
    """
    horizon_days = max(1, min(int(horizon_days or 3), 10))
    min_history = max(5, int(min_history or 12))
    factor_names = registered_factor_names()
    fund_cache: Dict[str, Optional[dict]] = {}

    def _fund_for(decision_date: str) -> Optional[dict]:
        if not pit_fundamentals:
            return fundamentals
        if not stock_code:
            return None
        if decision_date in fund_cache:
            return fund_cache[decision_date]
        try:
            from core.fundamentals_pit import resolve_fundamentals_for_score

            resolved = resolve_fundamentals_for_score(
                stock_code, as_of=decision_date, live_fallback=False
            )
            metrics = resolved.get("metrics") if resolved.get("ok") else None
        except Exception:
            metrics = None
        fund_cache[decision_date] = metrics
        return metrics

    xs: List[Dict[str, Optional[float]]] = []
    ys: List[float] = []
    n = len(bars or [])
    for i in range(min_history - 1, n - horizon_days):
        start = max(0, i - max_window + 1)
        window = bars[start : i + 1]
        quote = {"change_raw": 0.0, "price_raw": bars[i]["close"]}
        if i >= 1:
            c0 = bars[i - 1]["close"]
            c1 = bars[i]["close"]
            if c0:
                quote["change_raw"] = round((c1 / c0 - 1.0) * 100.0, 4)

        idx_slice = index_bars[start : i + 1] if index_bars else None
        decision_date = str((bars[i] or {}).get("date") or "")[:10]
        scored = score_bars(
            window,
            horizon_days=horizon_days,
            quote=quote,
            index_bars=idx_slice,
            fundamentals=_fund_for(decision_date),
        )
        if scored.get("hard_reject"):
            continue

        fr = _forward_return(bars, i, horizon_days)
        if fr is None:
            continue

        sub = scored.get("sub_scores") or {}
        row: Dict[str, Optional[float]] = {}
        any_val = False
        for key in factor_names:
            v = sub.get(key)
            if v is None:
                row[key] = None
            else:
                row[key] = float(v)
                any_val = True
        if not any_val:
            continue
        xs.append(row)
        ys.append(fr)

    return xs, ys


def _prepare_complete_panel(
    xs: List[Dict[str, Optional[float]]],
    ys: List[float],
    feature_names: List[str],
    *,
    min_samples_over_p: int = 3,
    eps: float = 1e-6,
) -> Tuple[
    Optional[List[Dict[str, float]]],
    Optional[List[float]],
    List[str],
    List[str],
    Dict[str, Any],
]:
    """挑选可用因子与完整行：允许多因子缺测，优先保留覆盖好的因子。"""
    n_raw = len(ys)
    meta: Dict[str, Any] = {
        "raw_sample_count": n_raw,
        "dropped_sparse": [],
        "dropped_constant": [],
        "dropped_for_coverage": [],
    }
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

        # 完整子面板上再剔常数列（稀疏行上有波动、完整行全相同的常见）
        still_var: List[str] = []
        for name in active:
            vals = [float(xs[i][name]) for i in rows_idx]
            if max(vals) - min(vals) <= eps:
                meta["dropped_constant"].append(name)
            else:
                still_var.append(name)
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
            + list(meta["dropped_for_coverage"])
        )
        leftover = [n for n in feature_names if n not in active and n not in excluded]
        excluded.extend(leftover)
        meta["complete_sample_count"] = n
        meta["active_feature_count"] = p
        return xs_c, ys_c, active, excluded, meta

    return None, None, [], feature_names[:], meta


def _gauss_solve(matrix: List[List[float]], rhs: List[float]) -> Optional[List[float]]:
    n = len(matrix)
    if n == 0 or len(rhs) != n:
        return None
    aug = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]

    rank = 0
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            continue
        rank += 1
        aug[col], aug[pivot] = aug[pivot], aug[col]
        pv = aug[col][col]
        for r in range(col + 1, n):
            factor = aug[r][col] / pv
            if factor == 0:
                continue
            for c in range(col, n + 1):
                aug[r][c] -= factor * aug[col][c]

    if rank < n:
        return None

    out = [0.0] * n
    for i in range(n - 1, -1, -1):
        if abs(aug[i][i]) < 1e-12:
            return None
        out[i] = aug[i][n]
        for j in range(i + 1, n):
            out[i] -= aug[i][j] * out[j]
        out[i] /= aug[i][i]
    return out


def _fit_ols_once(
    xs: List[Dict[str, float]],
    ys: List[float],
    active: List[str],
) -> Optional[Dict[str, Any]]:
    n = len(ys)
    p = len(active)
    if n < 4 or n != len(xs) or p < 1 or n < p + 3:
        return None

    design: List[List[float]] = []
    for row in xs:
        design.append([1.0] + [float(row[name]) for name in active])

    ata = [[0.0] * (p + 1) for _ in range(p + 1)]
    aty = [0.0] * (p + 1)
    for i in range(n):
        yi = float(ys[i])
        for j in range(p + 1):
            aty[j] += design[i][j] * yi
            for k in range(p + 1):
                ata[j][k] += design[i][j] * design[i][k]

    beta = _gauss_solve(ata, aty)
    if beta is None:
        return None

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
    }


def _exclusion_reasons_map(
    prep_meta: Optional[Dict[str, Any]],
    *,
    dropped_collinear: Optional[List[str]] = None,
    excluded: Optional[List[str]] = None,
) -> Dict[str, str]:
    """因子名 → 未入模原因码：sparse|constant|coverage|collinear|other。"""
    meta = prep_meta or {}
    reasons: Dict[str, str] = {}
    for name in meta.get("dropped_sparse") or []:
        reasons[str(name)] = "sparse"
    for name in meta.get("dropped_constant") or []:
        reasons.setdefault(str(name), "constant")
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
) -> Optional[Dict[str, Any]]:
    """拟合 OLS；奇异时逐个剔除共线因子。"""
    work = list(active)
    dropped_collinear: List[str] = []
    fit = None
    while work:
        fit = _fit_ols_once(xs, ys, work)
        if fit is not None:
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
        "rank_deficient": bool(all_excluded),
        "dropped_collinear": dropped_collinear,
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
) -> Dict[str, Any]:
    """对已对齐的 (sub_scores, forward return) 面板拟合 OLS。"""
    factor_names = registered_factor_names()
    cfg = load_signal_config()
    current_weights = dict(cfg.get("weights") or {})
    xs_c, ys_c, active, excluded, prep_meta = _prepare_complete_panel(
        xs, ys, factor_names
    )
    fit = (
        _ols_with_intercept(xs_c, ys_c, active, excluded)
        if xs_c is not None and ys_c is not None
        else None
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
                "缺测因子已尽量剔除仍不够，可加大 lookback / 研究池）"
            ),
            "sample_count": raw_n,
            "feature_count": len(factor_names),
            "horizon_days": horizon_days,
            "excluded_features": excl,
            "exclusion_reasons": _exclusion_reasons_map(prep_meta, excluded=excl),
            "prep_meta": prep_meta,
            "current_weights": {k: round(float(v), 4) for k, v in current_weights.items()},
            "task": task,
            "mode": mode,
        }
        if stock_codes is not None:
            err["stock_codes"] = list(stock_codes)
            err["stock_count"] = len(stock_codes)
        return err

    note_parts = [
        "面板 OLS 仅供研究对比 config.weights，不自动写 signal_config。",
        "缺测因子已自动剔除后再拟合。",
    ]
    if mode == "watching_pooled":
        note_parts.append(
            "模式：研究池堆叠时序面板（非逐日截面 Fama–MacBeth）；系数比单票稳，仍非生产权重。"
        )
    else:
        note_parts.append("单票 walk-forward 非截面回归；样本少时系数不稳定。")
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
        dropped_collinear=fit.get("dropped_collinear") or [],
        excluded=fit.get("excluded_features") or [],
    )
    out: Dict[str, Any] = {
        "success": True,
        "task": task,
        "mode": mode,
        "horizon_days": horizon_days,
        "sample_count": fit["sample_count"],
        "feature_count": fit["feature_count"],
        "r_squared": fit["r_squared"],
        "intercept": fit["intercept"],
        "coefficients": fit["coefficients"],
        "active_features": fit.get("active_features") or [],
        "excluded_features": fit.get("excluded_features") or [],
        "exclusion_reasons": excl_reasons,
        "current_weights": {k: round(float(v), 4) for k, v in current_weights.items()},
        "fundamentals_used": fundamentals_used,
        "pit_fundamentals": bool(pit_fundamentals),
        "rank_deficient": fit.get("rank_deficient"),
        "prep_meta": prep_meta,
        "note": " ".join(note_parts),
    }
    if stock_codes is not None:
        out["stock_codes"] = list(stock_codes)
        out["stock_count"] = len(stock_codes)
    return out


def compute_factor_ols_report(
    bars: List[dict],
    *,
    horizon_days: int = 3,
    min_history: int = 12,
    max_window: int = 30,
    index_bars: Optional[List[dict]] = None,
    fundamentals: Optional[dict] = None,
    stock_code: Optional[str] = None,
    pit_fundamentals: bool = True,
) -> Dict[str, Any]:
    """对 sub_scores 拟合 forward return 的 OLS（研究用，不产出生产权重 patch）。"""
    xs, ys = collect_subscore_forward_panel(
        bars,
        horizon_days=horizon_days,
        min_history=min_history,
        max_window=max_window,
        index_bars=index_bars,
        fundamentals=fundamentals,
        stock_code=stock_code,
        pit_fundamentals=pit_fundamentals,
    )
    out = fit_factor_ols_from_panel(
        xs,
        ys,
        horizon_days=horizon_days,
        fundamentals_used=bool(pit_fundamentals) or fundamentals is not None,
        pit_fundamentals=pit_fundamentals,
        mode="single",
    )
    if stock_code:
        out["stock_code"] = stock_code
    return out


def compute_factor_ols_pooled_report(
    stock_panels: List[Dict[str, Any]],
    *,
    horizon_days: int = 3,
) -> Dict[str, Any]:
    """堆叠多票时序面板后拟合 OLS（研究池探针，非逐日截面回归）。

    每项需含 ``code`` 与 ``bars``；可选 ``index_bars`` / ``fundamentals``。
    """
    all_xs: List[Dict[str, Optional[float]]] = []
    all_ys: List[float] = []
    loaded: List[str] = []
    skipped: List[Dict[str, str]] = []
    any_fundamentals = False

    for item in stock_panels or []:
        code = str(item.get("code") or item.get("stock_code") or "").strip()
        bars = item.get("bars") or []
        if not code or not bars:
            if code:
                skipped.append({"code": code, "reason": "无日线"})
            continue
        xs, ys = collect_subscore_forward_panel(
            bars,
            horizon_days=horizon_days,
            index_bars=item.get("index_bars"),
            fundamentals=item.get("fundamentals"),
            stock_code=code,
            pit_fundamentals=True,
        )
        if not ys:
            skipped.append({"code": code, "reason": "面板为空"})
            continue
        all_xs.extend(xs)
        all_ys.extend(ys)
        loaded.append(code)
        any_fundamentals = True

    if len(loaded) < 2:
        return {
            "success": False,
            "error": "研究池有效样本不足 2 只，无法做池内 OLS",
            "task": "factor_ols_pool",
            "mode": "watching_pooled",
            "stock_codes": loaded,
            "stock_count": len(loaded),
            "skipped": skipped,
            "horizon_days": horizon_days,
        }

    out = fit_factor_ols_from_panel(
        all_xs,
        all_ys,
        horizon_days=horizon_days,
        fundamentals_used=any_fundamentals,
        pit_fundamentals=True,
        mode="watching_pooled",
        stock_codes=loaded,
    )
    out["skipped"] = skipped
    out["raw_row_count"] = len(all_ys)
    return out

