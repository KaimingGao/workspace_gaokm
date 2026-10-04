"""ŷ_τc Ridge：开盘 Z[+Alpha158]+分钟路径上拟合 τ→close（close[T]/price[τ]−1）。

这就是调仓 ranking 里的 ŷ_τc。打分写入 ``y_τc``（含时钟对齐）。
供买入闸、ranking 的 τc 项、做 T 估 C_τ。与 ŷ_oo 独立，不改写 ``predicted_score``。
默认吃 ``raw_alpha158_*``（≤T−1）；与 ŷ_oo 日线 X 可能重叠，融合权重慎设。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.research.tau_panel import (
    TAU_LAG_FEAT_LABELS,
    T30_LAG_FEAT_LABELS,
    TAU_LAG_FEATURES,
    attach_cross_section_breadth,
    collect_tau_intraday_panel,
    collect_tau_open_panel,
    normalize_minute_tau_grid,
    theme_sample_weights,
)
from core.signal.minute_tau_feats import (
    MINUTE_TAU_ALL_KEYS,
    MINUTE_TAU_FEAT_LABELS,
)

TAU_FEATURE_EXTRA = (
    "gap_pct",
    "open_gap",
    "sector_gap_breadth",
    "theme_day",
    "gap_atr",
    "gap_vs_sector",
    "yclose_loc",
    "mom3_pct",
) + TAU_LAG_FEATURES
# τ 头主吃开盘 Z；可选 raw_alpha158_*（与 ŷ_oo 日线 X 可能重叠）
TAU_Z_FEATURES = (
    "gap_pct",
    "sector_gap_breadth",
    "theme_day",
    "gap_atr",
    "gap_vs_sector",
    "yclose_loc",
    "mom3_pct",
) + MINUTE_TAU_ALL_KEYS + TAU_LAG_FEATURES
# gap/breadth 单位是百分点或 [0,1]，勿用 0–100 分制的 min_std=5 误剔
TAU_MIN_STD_EXEMPT = TAU_FEATURE_EXTRA + MINUTE_TAU_ALL_KEYS
# open_gap ≡ gap_pct，只拟合其一，避免 Ridge 双计
TAU_FIT_DROP_ALIASES = frozenset({"open_gap"})
# ŷ_τ30/60/90 Ridge 不要吃 ŷ_τ 的 OC 路径形状。HL/回撤/振幅在 30–90m 前瞻上共线对冲，
# ŷ 被压到训练均值（做 T 回测里多数 |ŷ_τ30|/|ŷ_τ60|<0.1%）。
# 树头加回这些键 + t_hi/t_lo / 动量加速度 / 量价（分段交互，不走线性对冲）。
TAU_HORIZON_TREE_SHAPE_FEATURES = (
    "range_pct",
    "loc_hl",
    "up_extent",
    "down_extent",
    "path_sign",
    "pullback_from_high",
    "bounce_from_low",
    "realized_vol",
    "vol_last3_vs_avg",
    "tau_elapsed_min",
    "t_hi_frac",
    "t_lo_frac",
    "t_hi_minus_lo",
    "room_to_high",
    "room_to_low",
    "mom_accel_5_15",
    "mom_accel_5_30",
    "vol_down_up",
    "range_efficiency",
    "vp_confirm",
    "vol_up_share",
    "pullback_x_vol",
)
TAU_HORIZON_DROP_OC_SHAPE = frozenset(TAU_HORIZON_TREE_SHAPE_FEATURES)


def with_horizon_tree_shape(ridge_z_features: Sequence[str]) -> Tuple[str, ...]:
    """Ridge Z + OC 路径形状。仅 ŷ_τ*_tree；Ridge 对照仍用原 Z。"""
    have = set(ridge_z_features)
    extra = tuple(k for k in TAU_HORIZON_TREE_SHAPE_FEATURES if k not in have)
    return tuple(ridge_z_features) + extra


def _stack_panels(
    enriched: Sequence[Dict[str, Any]],
) -> tuple:
    xs_all: List[dict] = []
    ys_all: List[float] = []
    dates_all: List[str] = []
    metas_all: List[dict] = []
    for p in enriched:
        xs_all.extend(p.get("xs") or [])
        ys_all.extend(p.get("ys") or [])
        dates_all.extend(p.get("dates") or [])
        metas_all.extend(p.get("metas") or [])
    return xs_all, ys_all, dates_all, metas_all


def _subset(xs, ys, metas, idxs):
    return (
        [xs[i] for i in idxs],
        [ys[i] for i in idxs],
        [metas[i] for i in idxs],
    )


def _predict_rows(
    fit: Dict[str, Any],
    xs: List[dict],
    *,
    impute_missing: bool = True,
) -> List[Optional[float]]:
    """用 return_model 对行打分。

    ``impute_missing=True``（默认）：缺特征按训练集均值填（标准化后 z=0），
    避免 live 仅有 gap/部分 sub_scores 时整段返回 None。
    """
    coefs = fit.get("coefficients") or {}
    intercept = float(fit.get("intercept") or 0.0)
    means = fit.get("zscore_means") or fit.get("z_means") or {}
    stds = fit.get("zscore_stds") or fit.get("z_stds") or {}
    active = [
        n
        for n in (fit.get("active_features") or coefs.keys())
        if coefs.get(n) is not None
    ]
    out: List[Optional[float]] = []
    for row in xs:
        pred = intercept
        ok = True
        for name in active:
            v = row.get(name)
            if v is None:
                if not impute_missing or not means:
                    ok = False
                    break
                # 缺省 → 训练集均值 → z=0
                z = 0.0
            else:
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    if not impute_missing or not means:
                        ok = False
                        break
                    z = 0.0
                else:
                    mu = float(means.get(name, 0.0)) if means else 0.0
                    sd = float(stds.get(name, 1.0)) if stds else 1.0
                    if sd < 1e-9:
                        sd = 1.0
                    z = (fv - mu) / sd if means else fv
            pred += float(coefs.get(name) or 0.0) * z
        out.append(round(pred, 6) if ok else None)
    return out


def _ic(preds: List[Optional[float]], ys: List[float]) -> Optional[float]:
    pairs = [(float(p), float(y)) for p, y in zip(preds, ys) if p is not None]
    if len(pairs) < 5:
        return None
    import math

    n = len(pairs)
    mx = sum(p for p, _ in pairs) / n
    my = sum(y for _, y in pairs) / n
    num = sum((p - mx) * (y - my) for p, y in pairs)
    dx = math.sqrt(sum((p - mx) ** 2 for p, _ in pairs))
    dy = math.sqrt(sum((y - my) ** 2 for _, y in pairs))
    if dx < 1e-12 or dy < 1e-12:
        return None
    return round(num / (dx * dy), 4)


def _sign_hit(
    preds: List[Optional[float]],
    ys: List[float],
    *,
    min_abs: float = 0.05,
) -> Optional[float]:
    """方向命中率。默认跳过 |ŷ|<min_abs（百分收益弱信号）。

    强正则下 ŷ 常落在 0.05 以内；此时调用方应 ``min_abs=0`` 回退。
    """
    hits = 0
    n = 0
    thr = max(0.0, float(min_abs))
    for p, y in zip(preds, ys):
        if p is None:
            continue
        if thr > 0.0 and abs(float(p)) < thr:
            continue
        n += 1
        if (float(p) > 0 and float(y) > 0) or (float(p) < 0 and float(y) < 0):
            hits += 1
    if n < 5:
        return None
    return round(hits / n, 4)


def _oos_sign_buckets(
    preds: Sequence[Optional[float]],
    ys: Sequence[float],
) -> Dict[str, Any]:
    """分桶同号率 + 正负召回（展示/ promote 用）。

    固定阈 ``|ŷ|≥0.4/0.6`` 适合百分收益尺度；ŷ 幅度较小时
    另附 ``abs_top_30`` / ``abs_top_15``（按 |ŷ| 分位）以免强信号桶空缺。
    """
    buckets = {
        "abs_ge_0_4": {"n": 0, "hit": 0},
        "abs_ge_0_6": {"n": 0, "hit": 0},
    }
    pos_hit = pos_n = neg_hit = neg_n = 0
    n_valid = 0
    abs_ok: List[Tuple[float, bool]] = []
    for pred, y in zip(preds, ys):
        if pred is None:
            continue
        n_valid += 1
        ok = (float(pred) > 0) == (float(y) > 0)
        if float(y) > 0:
            pos_n += 1
            if ok:
                pos_hit += 1
        elif float(y) < 0:
            neg_n += 1
            if ok:
                neg_hit += 1
        ap = abs(float(pred))
        abs_ok.append((ap, ok))
        for key, thr in (("abs_ge_0_4", 0.4), ("abs_ge_0_6", 0.6)):
            if ap >= thr:
                buckets[key]["n"] += 1
                if ok:
                    buckets[key]["hit"] += 1
    out: Dict[str, Any] = {
        "n_valid": n_valid,
        "pos_recall": round(pos_hit / pos_n, 4) if pos_n else None,
        "neg_recall": round(neg_hit / neg_n, 4) if neg_n else None,
        "n_pos": pos_n,
        "n_neg": neg_n,
        "buckets": {},
    }
    for key, pack in buckets.items():
        n = int(pack["n"])
        out["buckets"][key] = {
            "n": n,
            "sign_hit": round(pack["hit"] / n, 4) if n else None,
        }
    # 分位强信号：top30% / top15%（阈=|ŷ| 的 70/85 分位）
    if len(abs_ok) >= 10:
        import numpy as np

        abs_arr = np.asarray([a for a, _ in abs_ok], dtype=np.float64)
        for key, q in (("abs_top_30", 70.0), ("abs_top_15", 85.0)):
            thr = float(np.percentile(abs_arr, q))
            n = hit = 0
            for ap, ok in abs_ok:
                if ap >= thr and (thr > 1e-15 or ap > 1e-15):
                    n += 1
                    if ok:
                        hit += 1
            out["buckets"][key] = {
                "n": n,
                "sign_hit": round(hit / n, 4) if n >= 5 else None,
                "thr": round(thr, 6),
                "percentile": q,
            }
    else:
        for key in ("abs_top_30", "abs_top_15"):
            out["buckets"][key] = {"n": 0, "sign_hit": None, "thr": None}
    return out


TAU_PROMOTE_MIN_SIGN_HIT = 0.55
TAU_PROMOTE_MIN_N_TEST = 80
TAU_PROMOTE_MIN_STRONG_HIT = 0.58  # |ŷ|≥0.6 桶


def tau_promote_gate(report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """promote 闸：OOS sign_hit / n_test / 强信号桶。"""
    rep = report if isinstance(report, dict) else {}
    oos = rep.get("oos") if isinstance(rep.get("oos"), dict) else {}
    n = oos.get("n_valid")
    if n is None:
        n = oos.get("n_test")
    try:
        n_i = int(n or 0)
    except (TypeError, ValueError):
        n_i = 0
    hit = oos.get("sign_hit")
    try:
        hit_f = float(hit) if hit is not None else None
    except (TypeError, ValueError):
        hit_f = None
    blockers: List[str] = []
    if n_i < TAU_PROMOTE_MIN_N_TEST:
        blockers.append(f"n_test={n_i}<{TAU_PROMOTE_MIN_N_TEST}")
    if hit_f is None:
        blockers.append("缺 OOS sign_hit")
    elif hit_f < TAU_PROMOTE_MIN_SIGN_HIT:
        blockers.append(f"sign_hit={hit_f:.3f}<{TAU_PROMOTE_MIN_SIGN_HIT}")
    buckets = oos.get("buckets") if isinstance(oos.get("buckets"), dict) else {}
    strong = buckets.get("abs_ge_0_6") if isinstance(buckets.get("abs_ge_0_6"), dict) else {}
    s_hit = strong.get("sign_hit")
    s_n = int(strong.get("n") or 0)
    try:
        s_hit_f = float(s_hit) if s_hit is not None else None
    except (TypeError, ValueError):
        s_hit_f = None
    if s_n >= 30 and s_hit_f is not None and s_hit_f < TAU_PROMOTE_MIN_STRONG_HIT:
        blockers.append(
            f"|ŷ|≥0.6 sign_hit={s_hit_f:.3f}<{TAU_PROMOTE_MIN_STRONG_HIT}"
        )
    return {
        "ok": not blockers,
        "blockers": blockers,
        "min_sign_hit": TAU_PROMOTE_MIN_SIGN_HIT,
        "min_n_test": TAU_PROMOTE_MIN_N_TEST,
        "min_strong_hit": TAU_PROMOTE_MIN_STRONG_HIT,
        "n_test": n_i,
        "sign_hit": hit_f,
        "strong_sign_hit": s_hit_f,
        "strong_n": s_n,
    }


def _residual_var(
    preds: List[Optional[float]], ys: List[float]
) -> Optional[float]:
    errs: List[float] = []
    for p, y in zip(preds, ys):
        if p is None:
            continue
        try:
            errs.append((float(y) - float(p)) ** 2)
        except (TypeError, ValueError):
            continue
    if len(errs) < 5:
        return None
    return round(sum(errs) / float(len(errs)), 6)


def _oos_by_theme(
    preds: List[Optional[float]],
    ys: List[float],
    metas: List[dict],
) -> Dict[str, Any]:
    """主题日 vs 普通日分层 OOS（同一切分测试集）。"""

    def _slice(theme_flag: Optional[int]) -> Dict[str, Any]:
        ps: List[Optional[float]] = []
        zs: List[float] = []
        for p, y, m in zip(preds, ys, metas):
            th = int((m or {}).get("theme_day") or 0)
            if theme_flag is not None and th != int(theme_flag):
                continue
            ps.append(p)
            zs.append(float(y))
        return {
            "n": len(zs),
            "ic": _ic(ps, zs) if zs else None,
            "sign_hit": _sign_hit(ps, zs) if zs else None,
            "residual_var": _residual_var(ps, zs) if zs else None,
        }

    return {
        "theme": _slice(1),
        "normal": _slice(0),
        "all": _slice(None),
    }


def _oos_by_tau(
    preds: List[Optional[float]],
    ys: List[float],
    metas: List[dict],
) -> Dict[str, Any]:
    """按决策钟 τ 分层 OOS（τ→close 标签下，τ 越晚 hit 通常越高——已实现开→τ 垫高）。"""
    buckets: Dict[str, Dict[str, List[Any]]] = {}
    for p, y, m in zip(preds, ys, metas):
        tau = str((m or {}).get("tau") or "open").strip() or "open"
        slot = buckets.setdefault(tau, {"ps": [], "zs": []})
        slot["ps"].append(p)
        slot["zs"].append(float(y))
    out: Dict[str, Any] = {}
    for tau in sorted(buckets.keys()):
        ps = buckets[tau]["ps"]
        zs = buckets[tau]["zs"]
        out[tau] = {
            "n": len(zs),
            "ic": _ic(ps, zs) if zs else None,
            "sign_hit": _sign_hit(ps, zs) if zs else None,
            "residual_var": _residual_var(ps, zs) if zs else None,
        }
    return out


def _theme_counts(metas: Sequence[dict]) -> Dict[str, Any]:
    n = len(metas or [])
    n_th = sum(1 for m in (metas or []) if int((m or {}).get("theme_day") or 0) == 1)
    return {
        "n": n,
        "n_theme": n_th,
        "theme_rate": round(n_th / float(n), 4) if n else None,
    }


def _theme_trigger_sensitivity(
    metas: Sequence[dict],
    *,
    triggers: Sequence[float] = (1.5, 2.0, 2.5),
) -> Dict[str, Any]:
    """报告用：不同 gap_trigger 下本票 |gap| 主题正例率（不重跑截面）。"""
    from core.research.tau_theme import resolve_theme_day

    out: Dict[str, Any] = {}
    rows = list(metas or [])
    n = len(rows)
    for thr in triggers:
        n_th = 0
        for m in rows:
            gap = (m or {}).get("gap_pct")
            b = (m or {}).get("sector_gap_breadth")
            if (
                resolve_theme_day(
                    gap_pct=gap,
                    sector_breadth=b,
                    gap_trigger_pct=float(thr),
                )
                >= 1.0
            ):
                n_th += 1
        key = str(thr).replace(".", "_")
        out[key] = {
            "trigger": float(thr),
            "n_theme": n_th,
            "theme_rate": round(n_th / float(n), 4) if n else None,
        }
    return out


def build_tau_panels_from_bars(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    tau_hm: str = "open",
    tau_grid: Optional[Sequence[str]] = None,
    include_alpha158: bool = False,
) -> List[Dict[str, Any]]:
    """``stock_bars``: ``[{code, bars, minute_bars?, index_bars?, fundamentals?}, ...]``。

    ``tau_hm=open``：开盘→收盘标签；否则用分钟价训 τ→收盘（无分钟则跳过该日）。
    ``tau_grid``：变长前缀少数时钟（共享 β）。
    ``include_alpha158``：面板附加 ``raw_alpha158_*``（≤T−1；Ridge / 树共用）。
"""
    use_minute = str(tau_hm or "open").strip().lower() not in ("", "open")
    grid = (
        normalize_minute_tau_grid(tau_hm=tau_hm, tau_grid=tau_grid)
        if use_minute
        else None
    )
    raw: List[Dict[str, Any]] = []
    for item in stock_bars:
        code = str(item.get("code") or item.get("stock_code") or "").strip()
        bars = list(item.get("bars") or [])
        if len(bars) < min_history + 2:
            continue
        if use_minute:
            xs, ys, dates, metas = collect_tau_intraday_panel(
                bars,
                item.get("minute_bars"),
                tau_hm=str(tau_hm),
                tau_grid=grid,
                min_history=min_history,
                index_bars=item.get("index_bars"),
                fundamentals=item.get("fundamentals"),
                stock_code=code,
                include_alpha158=include_alpha158,
            )
        else:
            xs, ys, dates, metas = collect_tau_open_panel(
                bars,
                min_history=min_history,
                index_bars=item.get("index_bars"),
                fundamentals=item.get("fundamentals"),
                stock_code=code,
                include_alpha158=include_alpha158,
            )
        if len(ys) < 4:
            continue
        raw.append(
            {
                "code": code,
                "xs": xs,
                "ys": ys,
                "dates": dates,
                "metas": metas,
            }
        )
    return attach_cross_section_breadth(raw, gap_trigger_pct=gap_trigger_pct)


def fit_tau_ridge_report(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    ridge_lambda: float = 1.0,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    theme_boost: float = 1.5,
    holdout_trading_days: int = 20,
    use_theme_weights: bool = True,
    tau_hm: str = "open",
    tau_grid: Optional[Sequence[str]] = None,
    include_alpha158: bool = True,
    label_demean: bool = True,
) -> Dict[str, Any]:
    """池化拟合 ŷ_τ 头 Ridge + Holdout OOS。

    标签为 close[T]/price[τ]−1（τ→close）；分钟仅作 ≤τ 特征。开盘时 price[τ]=open。
    ``tau_hm`` 非 open 时换信息集（前缀分钟路径）；``tau_grid`` 变长前缀共享 β。
    默认近 ``holdout_trading_days`` 个交易日只测；训练段 β 为研究模型，
    全样本重估为执行模型。
    ``include_alpha158``：默认 True，面板附加 ``raw_alpha158_*``（≤T−1）。
    ``label_demean``：默认 True（历史口径）；训练标签减全局均值，截距加回。
    """
    tau_key = str(tau_hm or "open").strip() or "open"
    use_minute = tau_key.lower() not in ("", "open")
    grid = (
        normalize_minute_tau_grid(tau_hm=tau_key, tau_grid=tau_grid)
        if use_minute
        else None
    )
    from core.research.panel_matrix import (
        collect_tau_compact,
        fill_rates_from_matrix,
        fit_keepall_ridge_matrix,
        named_columns,
        predict_ridge_matrix,
        raw_alpha158_finite,
    )

    X, col_names, ys, dates, metas, n_stocks = collect_tau_compact(
        stock_bars,
        list(TAU_Z_FEATURES),
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        tau_hm=tau_key,
        tau_grid=grid,
        include_alpha158=include_alpha158,
        head="y_tc",
    )
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"rem 样本不足 n={len(ys)}（需≥20）",
            "task": "tc_ridge",
            "sample_count": len(ys),
            "stock_count": n_stocks,
            "tau": tau_key,
            "tau_grid": grid,
        }

    ys_use, dates_use, metas_use = ys, dates, metas
    target = "tau_to_close_z"

    from core.research.holdout import (
        DEFAULT_HOLDOUT_TRADING_DAYS,
        attach_holdout_meta,
        calendar_dates_from_stock_bars,
        resolve_ridge_split,
    )

    hold_n = int(holdout_trading_days or DEFAULT_HOLDOUT_TRADING_DAYS)
    train_idx, test_idx, split_meta = resolve_ridge_split(
        dates_use,
        holdout_trading_days=hold_n,
        calendar_dates=calendar_dates_from_stock_bars(stock_bars),
    )
    ys_tr = [ys_use[i] for i in train_idx]
    ys_te = [ys_use[i] for i in test_idx]
    metas_tr = [metas_use[i] for i in train_idx]
    metas_te = [metas_use[i] for i in test_idx]

    weights = (
        theme_sample_weights(metas_tr, theme_boost=theme_boost)
        if use_theme_weights
        else None
    )

    # 始终纳入开盘 Z 键（含 theme_day）；分钟专用键仅 minute 模式；可选 Alpha158
    drop_opt = set() if use_minute else set(MINUTE_TAU_ALL_KEYS)
    feat_names = [
        k for k in TAU_Z_FEATURES if k not in drop_opt and k not in TAU_FIT_DROP_ALIASES
    ]
    if include_alpha158:
        seen = set(feat_names)
        for k in raw_alpha158_finite(X, col_names, train_idx):
            if k not in seen:
                seen.add(k)
                feat_names.append(k)

    fill_keys = list(TAU_Z_FEATURES)
    seen_fill = set(fill_keys)
    for name in col_names:
        if str(name).startswith("raw_alpha158_") and name not in seen_fill:
            seen_fill.add(name)
            fill_keys.append(name)
    feature_fill = fill_rates_from_matrix(X, col_names, fill_keys)
    X_fit, feat_names = named_columns(X, col_names, feat_names)
    del X

    from core.signal.factors.alpha158 import merge_alpha158_min_std_exempt

    min_std_exempt = merge_alpha158_min_std_exempt(
        feat_names, list(TAU_MIN_STD_EXEMPT)
    )

    from core.research.label_demean import annotate_label_demean, demean_labels

    use_dm = bool(label_demean)
    row_tr = np.asarray(train_idx, dtype=np.int64)
    row_te = np.asarray(test_idx, dtype=np.int64)
    y_tr = np.asarray(ys_tr, dtype=np.float64)
    if use_dm:
        y_fit, y_mean = demean_labels(y_tr)
    else:
        y_fit, y_mean = y_tr, 0.0
    fit = fit_keepall_ridge_matrix(
        X_fit[row_tr],
        y_fit,
        feat_names,
        ridge_lambda=ridge_lambda,
        sample_weights=weights,
        min_std_exempt=min_std_exempt,
    )
    if not fit.get("success"):
        # Z 截面过弱（合成/窄池）时退回截距头
        fit = {
            "success": True,
            "intercept": 0.0,
            "coefficients": {},
            "active_features": [],
            "zscore_means": {},
            "zscore_stds": {},
            "note": "Z 方差不足，ŷ_τ 用训练均值",
        }

    preds_dm = (
        predict_ridge_matrix(fit, X_fit[row_te], feat_names) if len(test_idx) else []
    )
    preds_te = [
        (float(p) + y_mean) if (use_dm and p is not None) else p
        for p in (preds_dm or [])
    ]
    by_theme = _oos_by_theme(preds_te, ys_te, metas_te) if ys_te else {}
    by_tau = _oos_by_tau(preds_te, ys_te, metas_te) if ys_te and use_minute else {}
    bucket_pack = _oos_sign_buckets(preds_te, ys_te) if ys_te else {}
    oos = {
        "n_train": len(ys_tr),
        "n_test": len(ys_te),
        "n_valid": bucket_pack.get("n_valid"),
        "ic": _ic(preds_te, ys_te) if ys_te else None,
        "sign_hit": _sign_hit(preds_te, ys_te) if ys_te else None,
        "residual_var": _residual_var(preds_te, ys_te) if ys_te else None,
        "by_theme": by_theme,
        "by_tau": by_tau,
        "theme_counts": {
            "train": _theme_counts(metas_tr),
            "oos": _theme_counts(metas_te),
            "all": _theme_counts(metas_use),
        },
        "theme_trigger_sensitivity": _theme_trigger_sensitivity(metas_use),
        "feature_fill": feature_fill,
        "buckets": bucket_pack.get("buckets") or {},
        "pos_recall": bucket_pack.get("pos_recall"),
        "neg_recall": bucket_pack.get("neg_recall"),
        "n_pos": bucket_pack.get("n_pos"),
        "n_neg": bucket_pack.get("n_neg"),
        "y_label_mean": round(y_mean, 6),
        "holdout_trading_days": hold_n,
        "theme_boost": theme_boost if use_theme_weights else None,
        "target": target,
        "residualized": False,
        "tau": tau_key,
        "include_alpha158": bool(include_alpha158),
    }
    if ys_te:
        try:
            from core.research.daily_cs_ic import attach_daily_cs_ic

            attach_daily_cs_ic(oos, preds_te, ys_te, metas_te)
        except Exception:  # noqa: BLE001
            logger.debug("tau attach_daily_cs_ic failed", exc_info=True)
    research_model = dict(fit)
    if use_dm:
        try:
            research_model["intercept"] = round(
                float(fit.get("intercept") or 0.0) + y_mean, 6
            )
        except (TypeError, ValueError):
            research_model["intercept"] = round(y_mean, 6)
        research_model["intercept_demeaned"] = round(
            float(fit.get("intercept") or 0.0), 6
        )
        annotate_label_demean(research_model, enabled=True, y_mean=y_mean)
    else:
        annotate_label_demean(research_model, enabled=False, y_mean=0.0)
    research_model["model_role"] = "research"

    w_all = (
        theme_sample_weights(metas_use, theme_boost=theme_boost)
        if use_theme_weights
        else None
    )
    y_all = np.asarray(ys_use, dtype=np.float64)
    if use_dm:
        y_all_fit, y_mean_all = demean_labels(y_all)
    else:
        y_all_fit, y_mean_all = y_all, 0.0
    fit_full = fit_keepall_ridge_matrix(
        X_fit,
        y_all_fit,
        feat_names,
        ridge_lambda=ridge_lambda,
        sample_weights=w_all,
        min_std_exempt=min_std_exempt,
    )
    model = fit_full if fit_full.get("success") else fit
    model = dict(model)
    y_mean = y_mean_all
    if use_dm:
        try:
            model["intercept"] = round(float(model.get("intercept") or 0.0) + y_mean, 6)
        except (TypeError, ValueError):
            model["intercept"] = round(y_mean, 6)
        model["intercept_demeaned"] = round(float(fit.get("intercept") or 0.0), 6)
        annotate_label_demean(model, enabled=True, y_mean=y_mean)
    else:
        annotate_label_demean(model, enabled=False, y_mean=0.0)
    model["horizon_mode"] = "tau_to_close"
    model["target"] = target
    model["residualized"] = False
    if use_minute:
        from core.signal.minute_tau_grid import format_shared_tau_formula

        y_formula = format_shared_tau_formula("close[T]/price[τ]-1", grid or [tau_key])
    else:
        y_formula = "close[T]/price[τ]-1"
    model["y_spec"] = {
        "formula": y_formula,
        "unit": "pct",
        "tau": tau_key,
        "tau_grid": list(grid) if grid else None,
        "note": (
            "变长前缀 5m 槽共享 β；标签=τ→close（close[T]/price[τ]−1）；"
            "open 时钟 price[τ]=open；"
            "live 调仓前缀=因果末根（≤10:00）"
            + ("；Z+raw_alpha158_*" if include_alpha158 else "；Z-only")
            if use_minute
            else (
                "Z[+Alpha158] τ→close（开盘 price[τ]=open）；demean+theme_day+yclose/mom3+tau_lag1/ma5"
                "+raw_alpha158_*；live 写 y_τc"
                if include_alpha158
                else "Z-only τ→close（开盘 price[τ]=open）；demean+theme_day+yclose/mom3+tau_lag1/ma5；live 写 y_τc"
            )
        ),
    }
    model["extra_features"] = list(fill_keys)
    model["feat_labels"] = {**dict(MINUTE_TAU_FEAT_LABELS), **dict(TAU_LAG_FEAT_LABELS)}
    model["include_alpha158"] = bool(include_alpha158)
    if use_minute:
        from core.signal.minute_tau_grid import LIVE_PREFIX_CAUSAL_REBALANCE

        model["live_prefix"] = LIVE_PREFIX_CAUSAL_REBALANCE
    model["model_role"] = "live"
    for k in (
        "y_spec",
        "extra_features",
        "feat_labels",
        "horizon_mode",
        "target",
        "residualized",
        "live_prefix",
        "include_alpha158",
    ):
        if model.get(k) is not None:
            research_model[k] = model.get(k)

    n_a158 = sum(
        1 for k in (model.get("extra_features") or []) if "alpha158" in str(k).lower()
    )
    day_keys = set()
    for m, d in zip(metas_use, dates_use):
        code = ""
        if isinstance(m, dict):
            code = str(m.get("stock_code") or m.get("code") or "").strip()
        day = str(d or "")[:10]
        if day:
            day_keys.add((code, day))
    report = {
        "success": True,
        "task": "tc_ridge",
        "stock_count": n_stocks,
        "sample_count": len(ys_use),
        "sample_count_raw": len(ys),
        "sample_count_day": len(day_keys),
        "oos": oos,
        "return_model": model,
        "return_model_research": research_model,
        "tau": tau_key,
        "tau_grid": list(grid) if grid else None,
        "live_prefix": model.get("live_prefix"),
        "y_spec": dict(model.get("y_spec") or {}),
        "schema": "tau_ridge_v12",
        "target": target,
        "residualized": False,
        "include_alpha158": bool(include_alpha158),
        "n_alpha158_features": n_a158,
        "note": (
            (
                "ŷ_τ(Z[+Alpha158]) 变长前缀 5m 槽共享 β；τ→close 标签；OOS.by_tau；按日 OOS；live 调仓=因果末根"
                if include_alpha158
                else "ŷ_τ(Z) 变长前缀 5m 槽共享 β；τ→close 标签；OOS.by_tau；按日 OOS；live 调仓=因果末根"
            )
            if use_minute
            else (
                "ŷ_τ(Z[+Alpha158]) 独立估 τ→close（开盘 price[τ]=open）；theme+|gap|；yclose_loc/mom3；PIT tau_lag1/ma5；raw_alpha158_*；与 EOD 解耦"
                if include_alpha158
                else "ŷ_τ(Z) 独立估 τ→close（开盘 price[τ]=open）；theme+|gap|；yclose_loc/mom3；PIT tau_lag1/ma5；与 EOD 解耦"
            )
        ),
    }
    attach_holdout_meta(report, split_meta)
    report["promote_gate"] = tau_promote_gate(report)
    return report


def tau_model_path() -> str:
    """Live ŷ_τ 模型主路径。"""
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "tau_ridge_model.json")


def tau_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "tau_ridge_last_report.json")


_JSON_MODEL_CACHE: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}


def _load_json_model(path: str) -> Optional[Dict[str, Any]]:
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _JSON_MODEL_CACHE.get(path)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001
        logger.debug("load tau json failed: %s", path, exc_info=True)
        _JSON_MODEL_CACHE[path] = (mtime, None)
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("return_model"), dict):
        _JSON_MODEL_CACHE[path] = (mtime, None)
        return None
    if doc.get("success") is False:
        _JSON_MODEL_CACHE[path] = (mtime, None)
        return None
    _JSON_MODEL_CACHE[path] = (mtime, doc)
    if len(_JSON_MODEL_CACHE) > 16:
        # 保当前这条，丢掉最旧的若干（模型文件就 2–4 个）
        extra = [k for k in _JSON_MODEL_CACHE if k != path]
        for k in extra[: max(0, len(_JSON_MODEL_CACHE) - 12)]:
            _JSON_MODEL_CACHE.pop(k, None)
    return doc


def save_tau_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    if not isinstance(report.get("return_model"), dict):
        return
    from core.research.holdout import stamp_fitted_at

    stamp_fitted_at(report)
    path = tau_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)
    _JSON_MODEL_CACHE.pop(path, None)


def load_tau_last_report() -> Optional[Dict[str, Any]]:
    doc = _load_json_model(tau_last_report_path())
    if doc and doc.get("success"):
        return doc
    return None


def persist_tau_model(
    report: Dict[str, Any],
    *,
    note: str = "",
    force: bool = False,
    role: str = "live",
) -> Dict[str, Any]:
    """人审后写入执行或研究模型。live → tau_ridge_model.json；research → *_research.json。"""
    from core.research.holdout import (
        MODEL_ROLE_RESEARCH,
        research_model_path,
        select_persist_return_model,
    )

    if not report.get("success"):
        return {"success": False, "error": report.get("error") or "no report"}
    role_n, rm = select_persist_return_model(report, role=role)
    if not isinstance(rm, dict):
        return {"success": False, "error": "return_model missing"}
    if not rm.get("coefficients") and rm.get("intercept") is None:
        return {"success": False, "error": "return_model missing"}
    gate = tau_promote_gate(report)
    if not force and not gate.get("ok"):
        return {
            "success": False,
            "error": "promote 未过闸：" + "；".join(gate.get("blockers") or []),
            "promote_gate": gate,
        }
    from core.numbers import now_iso_utc

    y_spec = dict(rm.get("y_spec") or {})
    if not y_spec:
        y_spec = {
            "formula": "close[T]/price[τ]-1",
            "unit": "pct",
            "tau": "open",
            "note": "Z[+Alpha158] τ→close" if rm.get("include_alpha158") else "Z-only τ→close",
        }
    tau = str(y_spec.get("tau") or rm.get("horizon_mode") or "open")
    if tau in ("open_to_close", "open→close", "tau_to_close", "τ→close"):
        tau = "open"
    y_spec.setdefault("tau", tau)
    y_spec.setdefault("unit", "pct")
    rm = dict(rm)
    rm["y_spec"] = y_spec
    rm["horizon_mode"] = rm.get("horizon_mode") or "tau_to_close"
    rm["tau"] = tau
    target = str(report.get("target") or rm.get("target") or "tau_to_close_z")
    residualized = False
    rm["target"] = target
    rm["residualized"] = residualized
    if "include_alpha158" in report:
        rm["include_alpha158"] = bool(report.get("include_alpha158"))
    elif "include_alpha158" not in rm:
        rm["include_alpha158"] = False

    raw_schema = str(report.get("schema") or "tau_ridge_v12")
    if raw_schema.startswith("rem_ridge"):
        raw_schema = "tau_ridge_v12"
    schema = raw_schema
    from core.research.holdout import model_fit_id

    fitted_at = model_fit_id(report)
    doc = {
        "success": True,
        "fitted_at": fitted_at,
        "promoted_at": now_iso_utc(),
        "note": note or "tau_ridge promote",
        "return_model": rm,
        "oos": report.get("oos"),
        "sample_count": report.get("sample_count"),
        "stock_count": report.get("stock_count"),
        "schema": schema,
        "target": target,
        "residualized": residualized,
        "tau": tau,
        "as_of_tau": tau,
        "y_spec": y_spec,
        "y_spec_tau": y_spec,
        "promote_gate": gate,
        "model_role": role_n,
        "fit_end": report.get("fit_end"),
        "eval_start": report.get("eval_start"),
        "holdout_trading_days": report.get("holdout_trading_days"),
        "include_alpha158": bool(rm.get("include_alpha158")),
        "n_alpha158_features": report.get("n_alpha158_features"),
        "dual_score_head": "predicted_score_tau",
        "contract_note": (
            "ŷ_τc(Z[+Alpha158]) 估 τ→close → y_τc；ranking 用这一列；与 ŷ_oo 独立；不覆盖 predicted_score。"
            if rm.get("include_alpha158")
            else "ŷ_τc(Z) 估 τ→close → y_τc；ranking 用这一列；与 ŷ_oo 独立；不覆盖 predicted_score。"
        ),
    }
    path = (
        research_model_path(tau_model_path())
        if role_n == MODEL_ROLE_RESEARCH
        else tau_model_path()
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, doc)
    _JSON_MODEL_CACHE.pop(path, None)
    out = {
        "success": True,
        "path": path,
        "promoted_at": doc["promoted_at"],
        "tau": tau,
        "schema": doc["schema"],
        "promote_gate": gate,
        "model_role": role_n,
    }
    return out


def load_tau_model(*, role: Optional[str] = None) -> Optional[Dict[str, Any]]:
    from core.research.holdout import (
        MODEL_ROLE_RESEARCH,
        current_scoring_model_role,
        research_model_path,
    )

    role_n = role if role is not None else current_scoring_model_role()
    if role_n == MODEL_ROLE_RESEARCH:
        return _load_json_model(research_model_path(tau_model_path()))
    doc = _load_json_model(tau_model_path())
    if doc:
        return doc
    return None


def predict_tau_from_features(
    features: Dict[str, Optional[float]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    doc = model_doc if model_doc is not None else load_tau_model()
    if not doc:
        return None
    rm = doc.get("return_model") or {}
    preds = _predict_rows(rm, [features])
    return preds[0] if preds else None


_TAU_FEAT_LABELS = {
    "gap_pct": "跳空 %",
    "open_gap": "开盘缺口",
    "sector_gap_breadth": "同业缺口广度",
    "theme_day": "主题日",
    "gap_atr": "缺口 / ATR",
    "gap_vs_sector": "行业相对缺口",
    "yclose_loc": "昨收位置",
    "mom3_pct": "近3日动量 %",
    **TAU_LAG_FEAT_LABELS,
    **T30_LAG_FEAT_LABELS,
    **MINUTE_TAU_FEAT_LABELS,
}


def explain_tau_prediction(
    features: Optional[Dict[str, Any]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """结构化拆解 ŷ_τ（与 ``predict_tau_from_features`` 同口径），供 tip 表格。

    缺特征按训练集均值填（z=0），与 live 预测一致。
    """
    doc = model_doc if model_doc is not None else load_tau_model()
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
    row = features or {}
    try:
        from core.signal.factors.meta.registry import factor_label
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in tau_ridge.py", exc_info=True)
        factor_label = lambda k: str(k)  # noqa: E731

    terms: List[Dict[str, Any]] = []
    total = intercept
    for name in active:
        beta = float(coefs.get(name) or 0.0)
        v = row.get(name)
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
        label = _TAU_FEAT_LABELS.get(name) or factor_label(name) or name
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
    # 旧 τ 头仍含日线 β 时：缺特征的非 Z/a158 行对 tip 无信息，只保留 Z（含缺特征）与有实值的项
    from core.signal.factors.alpha158 import is_alpha158_raw_key

    z_keys = set(TAU_Z_FEATURES) | {"open_gap"}
    slim: List[Dict[str, Any]] = []
    for t in terms:
        key = str(t.get("key") or "")
        if t.get("note") and key not in z_keys and not is_alpha158_raw_key(key):
            continue
        slim.append(t)
    if slim:
        terms = slim
    # 展示只列有实值的项。缺特征已按均值填进合计（contrib=0），不占表。
    missing_terms = [t for t in terms if t.get("note")]
    n_missing = len(missing_terms)
    shown = [t for t in terms if not t.get("note")]
    if shown:
        terms = shown
    terms.sort(key=lambda t: -abs(float(t.get("contrib") or 0)))
    out = {
        "intercept": round(intercept, 6),
        "terms": terms[:32],
        "total": round(total, 6),
        "head": "tau",
    }
    role = str((doc or {}).get("model_role") or "").strip()
    if role:
        out["model_role"] = role
    if n_missing:
        out["missing_n"] = int(n_missing)
        out["missing_keys"] = [
            str(t.get("label") or t.get("key") or "")
            for t in missing_terms[:8]
            if str(t.get("label") or t.get("key") or "")
        ]
    return out

