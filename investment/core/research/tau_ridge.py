"""ŷ_τ Ridge 头：Z 上拟合 open→close；与 ŷ_EOD 正交后（缺口∘ŷ_τ）加权成 ŷ_trade。"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.research.factor_ols_fit import fit_factor_ols_from_panel
from core.research.tau_panel import (
    attach_cross_section_breadth,
    collect_tau_open_panel,
    collect_tau_intraday_panel,
    theme_sample_weights,
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
)
# τ 头只吃开盘新信息，避免与 ŷ_EOD 的 X 双重计权
TAU_Z_FEATURES = (
    "gap_pct",
    "sector_gap_breadth",
    "theme_day",
    "gap_atr",
    "gap_vs_sector",
    "yclose_loc",
    "mom3_pct",
    "ret_open_to_tau",
    "sector_ret_to_tau",
)
# gap/breadth 单位是百分点或 [0,1]，勿用 0–100 分制的 min_std=5 误剔
TAU_MIN_STD_EXEMPT = TAU_FEATURE_EXTRA + ("ret_open_to_tau", "sector_ret_to_tau")
# open_gap ≡ gap_pct，只拟合其一，避免 Ridge 双计
TAU_FIT_DROP_ALIASES = frozenset({"open_gap"})


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


def _time_split_indices(dates: List[str], *, train_frac: float = 0.7) -> tuple:
    """按日期排序后前 train_frac 为训练。"""
    order = sorted(range(len(dates)), key=lambda i: dates[i])
    n = len(order)
    if n < 10:
        return order, []
    cut = max(4, int(n * float(train_frac)))
    cut = min(cut, n - 2)
    return order[:cut], order[cut:]


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


def _sign_hit(preds: List[Optional[float]], ys: List[float]) -> Optional[float]:
    hits = 0
    n = 0
    for p, y in zip(preds, ys):
        if p is None:
            continue
        if abs(float(p)) < 0.05:
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
    """分桶同号率 + 正负召回（展示/ promote 用）。"""
    buckets = {
        "abs_ge_0_4": {"n": 0, "hit": 0},
        "abs_ge_0_6": {"n": 0, "hit": 0},
    }
    pos_hit = pos_n = neg_hit = neg_n = 0
    n_valid = 0
    hits = 0
    for pred, y in zip(preds, ys):
        if pred is None:
            continue
        n_valid += 1
        ok = (float(pred) > 0) == (float(y) > 0)
        if ok:
            hits += 1
        if float(y) > 0:
            pos_n += 1
            if ok:
                pos_hit += 1
        elif float(y) < 0:
            neg_n += 1
            if ok:
                neg_hit += 1
        ap = abs(float(pred))
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


def _z_only_row(row: Optional[dict]) -> Dict[str, Optional[float]]:
    """始终输出完整 TAU_Z_FEATURES 键，避免 theme_day 等被静默丢掉。"""
    src = row or {}
    return {k: src.get(k) for k in TAU_Z_FEATURES}


def _z_only_xs(xs: Sequence[dict]) -> List[dict]:
    return [_z_only_row(r) for r in xs]


def build_tau_panels_from_bars(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    tau_hm: str = "open",
) -> List[Dict[str, Any]]:
    """``stock_bars``: ``[{code, bars, minute_bars?, index_bars?, fundamentals?}, ...]``。

    ``tau_hm=open``：开盘→收盘标签；否则用分钟价训 τ→收盘（无分钟则跳过该日）。
    """
    use_minute = str(tau_hm or "open").strip().lower() not in ("", "open")
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
                min_history=min_history,
                index_bars=item.get("index_bars"),
                fundamentals=item.get("fundamentals"),
                stock_code=code,
            )
        else:
            xs, ys, dates, metas = collect_tau_open_panel(
                bars,
                min_history=min_history,
                index_bars=item.get("index_bars"),
                fundamentals=item.get("fundamentals"),
                stock_code=code,
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
    train_frac: float = 0.7,
    use_theme_weights: bool = True,
    tau_hm: str = "open",
) -> Dict[str, Any]:
    """池化拟合 ŷ_τ 头 Ridge + 时间 OOS。

    默认标签 = y_oc = close[T]/open[T]−1；``tau_hm`` 非 open 时为 close/price[τ]−1。
    特征仅 Z；不读 EOD 模型、不残差化；与 ŷ_EOD 解耦，live 才加权。
    """
    tau_key = str(tau_hm or "open").strip() or "open"
    use_minute = tau_key.lower() not in ("", "open")
    enriched = build_tau_panels_from_bars(
        stock_bars,
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        tau_hm=tau_key,
    )
    xs, ys, dates, metas = _stack_panels(enriched)
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"rem 样本不足 n={len(ys)}（需≥20）",
            "task": "tau_ridge",
            "sample_count": len(ys),
            "stock_count": len(enriched),
            "tau": tau_key,
        }

    xs_use, ys_use, dates_use, metas_use = xs, ys, dates, metas
    target = "tau_to_close_z" if use_minute else "open_to_close_z"

    xs_z = _z_only_xs(xs_use)
    train_idx, test_idx = _time_split_indices(dates_use, train_frac=train_frac)
    xs_tr, ys_tr, metas_tr = _subset(xs_z, ys_use, metas_use, train_idx)
    xs_te, ys_te, metas_te = _subset(xs_z, ys_use, metas_use, test_idx)

    weights = (
        theme_sample_weights(metas_tr, theme_boost=theme_boost)
        if use_theme_weights
        else None
    )

    # 始终纳入开盘 Z 键（含 theme_day）；分钟专用键仅 minute 模式
    drop_opt = set() if use_minute else {"ret_open_to_tau", "sector_ret_to_tau"}
    feat_names = [k for k in TAU_Z_FEATURES if k not in drop_opt and k not in TAU_FIT_DROP_ALIASES]

    # 去训练均值，减轻截距偏置；推理时截距加回
    y_mean = sum(float(y) for y in ys_tr) / max(1, len(ys_tr))
    ys_tr_dm = [float(y) - y_mean for y in ys_tr]

    fit = fit_factor_ols_from_panel(
        xs_tr,
        ys_tr_dm,
        feature_names=feat_names,
        ridge_lambda=ridge_lambda,
        standardize=True,
        sample_weights=weights,
        min_std_exempt=list(TAU_MIN_STD_EXEMPT),
        collinearity_policy="keep_all",
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

    preds_dm = _predict_rows(fit, xs_te) if xs_te else []
    preds_te = [
        (float(p) + y_mean) if p is not None else None for p in (preds_dm or [])
    ]
    by_theme = _oos_by_theme(preds_te, ys_te, metas_te) if ys_te else {}
    bucket_pack = _oos_sign_buckets(preds_te, ys_te) if ys_te else {}
    oos = {
        "n_train": len(ys_tr),
        "n_test": len(ys_te),
        "n_valid": bucket_pack.get("n_valid"),
        "ic": _ic(preds_te, ys_te) if ys_te else None,
        "sign_hit": _sign_hit(preds_te, ys_te) if ys_te else None,
        "residual_var": _residual_var(preds_te, ys_te) if ys_te else None,
        "by_theme": by_theme,
        "theme_counts": {
            "train": _theme_counts(metas_tr),
            "oos": _theme_counts(metas_te),
            "all": _theme_counts(metas_use),
        },
        "theme_trigger_sensitivity": _theme_trigger_sensitivity(metas_use),
        "feature_fill": None,
        "buckets": bucket_pack.get("buckets") or {},
        "pos_recall": bucket_pack.get("pos_recall"),
        "neg_recall": bucket_pack.get("neg_recall"),
        "n_pos": bucket_pack.get("n_pos"),
        "n_neg": bucket_pack.get("n_neg"),
        "y_label_mean": round(y_mean, 6),
        "train_frac": train_frac,
        "theme_boost": theme_boost if use_theme_weights else None,
        "target": target,
        "residualized": False,
        "tau": tau_key,
    }
    try:
        from core.research.path_panel import feature_fill_rates

        oos["feature_fill"] = feature_fill_rates(
            xs_z, ("theme_day", "yclose_loc", "mom3_pct", "gap_atr", "gap_vs_sector")
        )
    except Exception:  # noqa: BLE001
        logger.debug("tau feature_fill failed", exc_info=True)

    w_all = (
        theme_sample_weights(metas_use, theme_boost=theme_boost)
        if use_theme_weights
        else None
    )
    ys_all_dm = [float(y) - y_mean for y in ys_use]
    fit_full = fit_factor_ols_from_panel(
        xs_z,
        ys_all_dm,
        feature_names=feat_names,
        ridge_lambda=ridge_lambda,
        standardize=True,
        sample_weights=w_all,
        min_std_exempt=list(TAU_MIN_STD_EXEMPT),
        collinearity_policy="keep_all",
    )
    model = fit_full if fit_full.get("success") else fit
    model = dict(model)
    try:
        model["intercept"] = round(float(model.get("intercept") or 0.0) + y_mean, 6)
    except (TypeError, ValueError):
        model["intercept"] = round(y_mean, 6)
    model["intercept_demeaned"] = round(float(fit.get("intercept") or 0.0), 6)
    model["y_label_mean"] = round(y_mean, 6)
    model["y_demeaned"] = True
    model["horizon_mode"] = "tau_to_close" if use_minute else "open_to_close"
    model["target"] = target
    model["residualized"] = False
    y_formula = (
        f"close[T]/price[{tau_key}]-1" if use_minute else "close[T]/open[T]-1"
    )
    model["y_spec"] = {
        "formula": y_formula,
        "unit": "pct",
        "tau": tau_key,
        "note": "Z-only τ→close；demean+theme_day+yclose/mom3；live 与 ŷ_EOD 正交加权成 ŷ_trade",
    }
    model["extra_features"] = list(TAU_Z_FEATURES)

    report = {
        "success": True,
        "task": "tau_ridge",
        "stock_count": len(enriched),
        "sample_count": len(ys_use),
        "sample_count_raw": len(ys),
        "oos": oos,
        "return_model": model,
        "tau": tau_key,
        "y_spec": dict(model.get("y_spec") or {}),
        "schema": "tau_ridge_v8",
        "target": target,
        "residualized": False,
        "note": "ŷ_τ(Z) 独立估 τ→close；theme+|gap|；yclose_loc/mom3；与 EOD 解耦",
    }
    report["promote_gate"] = tau_promote_gate(report)
    return report


def tau_model_path() -> str:
    """Live ŷ_τ 模型主路径。"""
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "tau_ridge_model.json")


def tau_model_path_legacy() -> str:
    """旧文件名（rem_ridge_*）；仅读兼容。"""
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "rem_ridge_model.json")


def tau_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "tau_ridge_last_report.json")


def tau_last_report_path_legacy() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "rem_ridge_last_report.json")


def _load_json_model(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001
        logger.debug("load tau json failed: %s", path, exc_info=True)
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("return_model"), dict):
        return None
    if doc.get("success") is False:
        return None
    return doc


def save_tau_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    if not isinstance(report.get("return_model"), dict):
        return
    path = tau_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)
    # 过渡期双写旧路径，避免旧 UI 读 last report 落空
    try:
        atomic_write_json(tau_last_report_path_legacy(), report)
    except Exception:  # noqa: BLE001
        logger.debug("legacy tau last_report write failed", exc_info=True)


def load_tau_last_report() -> Optional[Dict[str, Any]]:
    for path in (tau_last_report_path(), tau_last_report_path_legacy()):
        doc = _load_json_model(path)
        if doc and doc.get("success"):
            return doc
    return None


def persist_tau_model(
    report: Dict[str, Any],
    *,
    note: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    """人审后写入 data/live/tau_ridge_model.json（契约：τ + y_spec）。"""
    if not report.get("success"):
        return {"success": False, "error": report.get("error") or "no report"}
    rm = report.get("return_model")
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
            "formula": "close[T]/open[T]-1",
            "unit": "pct",
            "tau": "open",
            "note": "Z-only open→close",
        }
    tau = str(y_spec.get("tau") or rm.get("horizon_mode") or "open")
    if tau in ("open_to_close", "open→close"):
        tau = "open"
    y_spec.setdefault("tau", tau)
    y_spec.setdefault("unit", "pct")
    rm = dict(rm)
    rm["y_spec"] = y_spec
    rm["horizon_mode"] = rm.get("horizon_mode") or "open_to_close"
    rm["tau"] = tau
    target = str(report.get("target") or rm.get("target") or "open_to_close_z")
    residualized = False
    rm["target"] = target
    rm["residualized"] = residualized

    raw_schema = str(report.get("schema") or "tau_ridge_v8")
    if raw_schema.startswith("rem_ridge"):
        raw_schema = "tau_ridge_v8"
    schema = raw_schema
    doc = {
        "success": True,
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
        "dual_score_head": "predicted_score_tau",
        "contract_note": "ŷ_τ(Z) 估 open→close；live 与 ŷ_EOD 加权融合（缺口∘ŷ_τ）；不覆盖 EOD predicted_score。",
    }
    path = tau_model_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, doc)
    # 过渡期双写旧 rem 文件名，避免未刷新客户端读不到模型
    try:
        atomic_write_json(tau_model_path_legacy(), doc)
    except Exception:  # noqa: BLE001
        logger.debug("legacy tau model write failed", exc_info=True)
    return {
        "success": True,
        "path": path,
        "legacy_path": tau_model_path_legacy(),
        "promoted_at": doc["promoted_at"],
        "tau": tau,
        "schema": doc["schema"],
        "promote_gate": gate,
    }


def load_tau_model() -> Optional[Dict[str, Any]]:
    for path in (tau_model_path(), tau_model_path_legacy()):
        doc = _load_json_model(path)
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
    "ret_open_to_tau": "开盘→τ 收益 %",
    "sector_ret_to_tau": "板块中位开→τ %",
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
    # 旧 τ 头仍含日线 β 时：缺特征的非 Z 行对 tip 无信息，只保留 Z（含缺特征）与有实值的项
    z_keys = set(TAU_Z_FEATURES) | {"open_gap"}
    slim: List[Dict[str, Any]] = []
    for t in terms:
        key = str(t.get("key") or "")
        if t.get("note") and key not in z_keys:
            continue
        slim.append(t)
    if slim:
        terms = slim
    terms.sort(key=lambda t: -abs(float(t.get("contrib") or 0)))
    # tip 默认只展示有贡献或非零 β 的前若干 + 额外 Z；截到 16 行防过长
    return {
        "intercept": round(intercept, 6),
        "terms": terms[:16],
        "total": round(total, 6),
        "head": "tau",
    }

# --- Backward-compatible aliases (deprecated; prefer tau_* names) ---
REM_FEATURE_EXTRA = TAU_FEATURE_EXTRA
REM_Z_FEATURES = TAU_Z_FEATURES
REM_MIN_STD_EXEMPT = TAU_MIN_STD_EXEMPT
REM_FIT_DROP_ALIASES = TAU_FIT_DROP_ALIASES
build_rem_panels_from_bars = build_tau_panels_from_bars
fit_rem_ridge_report = fit_tau_ridge_report
rem_model_path = tau_model_path
rem_last_report_path = tau_last_report_path
save_rem_last_report = save_tau_last_report
load_rem_last_report = load_tau_last_report
persist_rem_model = persist_tau_model
load_rem_model = load_tau_model
predict_rem_from_features = predict_tau_from_features
explain_rem_prediction = explain_tau_prediction

