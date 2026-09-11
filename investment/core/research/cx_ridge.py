"""ŷ_complexity Ridge：与 ŷ_τ 同因子键（开盘 Z + 早盘前缀分钟小包）→ 全日曲折度 1−D/L ∈ [0,1]。

标签非有符号收益，OOS 看 Spearman IC 与中位命中（≈50% 即无信息），不用方向命中。
研究枢纽拟合；做 T 入场：ŷ_complexity > y_complexity_max 则跳过。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.research.cx_panel import (
    CANON_LAG_FEAT_LABELS,
    CX_Z_FEATURES,
    DEFAULT_CX_MINUTE_TAU_HM,
    DEFAULT_CX_TAU_GRID,
    build_cx_panels_from_bars,
    cx_as_unit_01,
    resolve_feat_value,
)
from core.research.factor_ols_fit import fit_factor_ols_from_panel
from core.research.tau_panel import normalize_minute_tau_grid
from core.research.tau_ridge import _predict_rows, _subset
from core.signal.minute_tau_feats import MINUTE_TAU_FEAT_LABELS

CX_MIN_STD_EXEMPT = CX_Z_FEATURES
CX_PROMOTE_MIN_N_TEST = 40
CX_PROMOTE_MIN_IC = 0.0


def _stack_panels(enriched: Sequence[Dict[str, Any]]) -> tuple:
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


def _z_only_xs(xs: List[dict], features: Optional[Sequence[str]] = None) -> List[dict]:
    keys = list(features or CX_Z_FEATURES)
    out: List[dict] = []
    for row in xs:
        if not isinstance(row, dict):
            continue
        out.append({k: row.get(k) for k in keys})
    return out


def _median(vals: Sequence[float]) -> Optional[float]:
    xs = [float(v) for v in vals]
    if not xs:
        return None
    xs.sort()
    n = len(xs)
    if n % 2:
        return xs[n // 2]
    return 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def _pearson(preds: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(preds)
    if n < 8:
        return None
    mx = sum(preds) / n
    my = sum(ys) / n
    num = sum((a - mx) * (b - my) for a, b in zip(preds, ys))
    dx = sum((a - mx) ** 2 for a in preds) ** 0.5
    dy = sum((b - my) ** 2 for b in ys) ** 0.5
    if dx < 1e-12 or dy < 1e-12:
        return None
    return round(num / (dx * dy), 4)


def _spearman(preds: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(preds)
    if n < 8:
        return None

    def ranks(vals: Sequence[float]) -> List[float]:
        order = sorted(range(n), key=lambda i: float(vals[i]))
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and float(vals[order[j + 1]]) == float(vals[order[i]]):
                j += 1
            avg = 0.5 * (i + j) + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(preds), ranks(ys)
    return _pearson(rx, ry)


def _oos_rank_metrics(
    preds: Sequence[Optional[float]],
    ys: Sequence[float],
) -> Dict[str, Any]:
    pairs = [
        (float(p), float(y))
        for p, y in zip(preds, ys)
        if p is not None
    ]
    n_valid = len(pairs)
    if n_valid < 2:
        return {"n_valid": n_valid, "ic": None, "ic_pearson": None, "mae": None, "median_hit": None}
    ps = [a[0] for a in pairs]
    zs = [a[1] for a in pairs]
    mae = sum(abs(p - y) for p, y in pairs) / n_valid
    mp = _median(ps)
    my = _median(zs)
    median_hit = None
    if mp is not None and my is not None:
        hit = sum(1 for p, y in pairs if (p >= mp) == (y >= my))
        median_hit = round(hit / float(n_valid), 4)
    return {
        "n_valid": n_valid,
        "ic": _spearman(ps, zs),
        "ic_pearson": _pearson(ps, zs),
        "mae": round(mae, 4),
        "median_hit": median_hit,
        "y_median": round(my, 4) if my is not None else None,
        "pred_median": round(mp, 4) if mp is not None else None,
    }


def _oos_by_tau(
    preds: Sequence[Optional[float]],
    ys: Sequence[float],
    metas: Sequence[dict],
) -> Dict[str, Any]:
    buckets: Dict[str, Dict[str, List[Any]]] = {}
    for p, y, m in zip(preds, ys, metas):
        tau = str((m or {}).get("tau") or (m or {}).get("minute_tau_hm") or "10:30").strip()
        slot = buckets.setdefault(tau, {"ps": [], "zs": []})
        slot["ps"].append(p)
        slot["zs"].append(float(y))
    out: Dict[str, Any] = {}
    for tau in sorted(buckets.keys()):
        pack = _oos_rank_metrics(buckets[tau]["ps"], buckets[tau]["zs"])
        out[tau] = {
            "n": len(buckets[tau]["zs"]),
            "n_valid": pack.get("n_valid"),
            "ic": pack.get("ic"),
            "median_hit": pack.get("median_hit"),
            "mae": pack.get("mae"),
        }
    return out


def cx_promote_gate(report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """promote 闸：n 与 Spearman IC（y_complexity 无方向命中）。"""
    rep = report if isinstance(report, dict) else {}
    oos = rep.get("oos") if isinstance(rep.get("oos"), dict) else {}
    n = oos.get("n_valid")
    if n is None:
        n = oos.get("n_test")
    try:
        n_i = int(n or 0)
    except (TypeError, ValueError):
        n_i = 0
    ic = oos.get("ic")
    try:
        ic_f = float(ic) if ic is not None else None
    except (TypeError, ValueError):
        ic_f = None
    blockers: List[str] = []
    if n_i < CX_PROMOTE_MIN_N_TEST:
        blockers.append(f"n_test={n_i}<{CX_PROMOTE_MIN_N_TEST}")
    if ic_f is None:
        blockers.append("缺 OOS Spearman IC")
    elif ic_f < CX_PROMOTE_MIN_IC:
        blockers.append(f"ic={ic_f:.3f}<{CX_PROMOTE_MIN_IC}")
    warn: List[str] = []
    mh = oos.get("median_hit")
    try:
        mh_f = float(mh) if mh is not None else None
    except (TypeError, ValueError):
        mh_f = None
    if mh_f is not None and mh_f < 0.55:
        warn.append(f"中位命中={mh_f:.3f}（≈50% 则无排序信息）")
    return {
        "ok": not blockers,
        "blockers": blockers,
        "warnings": warn,
        "min_n_test": CX_PROMOTE_MIN_N_TEST,
        "min_ic": CX_PROMOTE_MIN_IC,
        "n_test": n_i,
        "ic": ic_f,
        "median_hit": mh_f,
    }


def fit_cx_ridge_report(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    minute_by_code_date: Optional[Dict[str, Dict[str, Sequence[dict]]]] = None,
    ridge_lambda: float = 1.0,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    holdout_trading_days: int = 10,
    minute_tau_hm: Optional[str] = None,
    tau_grid: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """池化拟合 ŷ_complexity + 时间 OOS。"""
    live_hm = str(minute_tau_hm or DEFAULT_CX_MINUTE_TAU_HM).strip() or DEFAULT_CX_MINUTE_TAU_HM
    grid = normalize_minute_tau_grid(
        tau_hm=live_hm,
        tau_grid=list(tau_grid) if tau_grid is not None else list(DEFAULT_CX_TAU_GRID),
    )
    enriched = build_cx_panels_from_bars(
        stock_bars,
        minute_by_code_date=minute_by_code_date,
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        minute_tau_hm=live_hm,
        tau_grid=grid,
    )
    xs, ys, dates, metas = _stack_panels(enriched)
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"cx 样本不足 n={len(ys)}（需≥20 且需分钟线）",
            "task": "cx_ridge",
            "sample_count": len(ys),
            "stock_count": len(enriched),
            "tau_grid": list(grid),
            "minute_tau_hm": live_hm,
        }

    xs_z = _z_only_xs(xs)
    from core.research.holdout import (
        DEFAULT_HOLDOUT_TRADING_DAYS,
        attach_holdout_meta,
        calendar_dates_from_stock_bars,
        make_research_model,
        resolve_ridge_split,
    )

    hold_n = int(holdout_trading_days or DEFAULT_HOLDOUT_TRADING_DAYS)
    train_idx, test_idx, split_meta = resolve_ridge_split(
        dates,
        holdout_trading_days=hold_n,
        calendar_dates=calendar_dates_from_stock_bars(stock_bars),
    )
    xs_tr, ys_tr, _metas_tr = _subset(xs_z, ys, metas, train_idx)
    xs_te, ys_te, metas_te = _subset(xs_z, ys, metas, test_idx)

    y_mean = sum(float(y) for y in ys_tr) / max(1, len(ys_tr))
    ys_tr_dm = [float(y) - y_mean for y in ys_tr]

    fit = fit_factor_ols_from_panel(
        xs_tr,
        ys_tr_dm,
        feature_names=list(CX_Z_FEATURES),
        ridge_lambda=ridge_lambda,
        standardize=True,
        min_std_exempt=list(CX_MIN_STD_EXEMPT),
        collinearity_policy="keep_all",
    )
    if not fit.get("success"):
        fit = {
            "success": True,
            "intercept": 0.0,
            "coefficients": {},
            "active_features": [],
            "zscore_means": {},
            "zscore_stds": {},
            "note": "Z 方差不足，ŷ_complexity 用训练均值",
        }

    oos: Dict[str, Any] = {"n_train": len(ys_tr), "n_test": len(test_idx)}
    if xs_te:
        preds_dm = _predict_rows(fit, xs_te)
        preds = [
            (float(p) + y_mean) if p is not None else None for p in (preds_dm or [])
        ]
        oos.update(_oos_rank_metrics(preds, ys_te))
        if grid and len(grid) > 1:
            oos["by_tau"] = _oos_by_tau(preds, ys_te, metas_te)
    oos["y_label_mean"] = round(y_mean, 4)
    oos["tau_grid"] = list(grid)
    oos["minute_tau_hm"] = live_hm
    try:
        from core.research.path_panel import feature_fill_rates

        oos["feature_fill"] = feature_fill_rates(xs_z, CX_Z_FEATURES)
    except Exception:  # noqa: BLE001
        logger.debug("cx feature fill audit failed", exc_info=True)

    oos["label_dist"] = {
        "n_labeled": len(ys),
        "y_mean": round(sum(ys) / float(len(ys)), 4) if ys else None,
        "y_median": _median(ys),
        "n_unique_days": len({str(d)[:10] for d in dates}),
        "rows_per_day": round(len(ys) / max(1, len({str(d)[:10] for d in dates})), 2),
    }

    research_model = make_research_model(fit, y_mean=y_mean)
    ys_all_dm = [float(y) - y_mean for y in ys]
    fit_full = fit_factor_ols_from_panel(
        xs_z,
        ys_all_dm,
        feature_names=list(CX_Z_FEATURES),
        ridge_lambda=ridge_lambda,
        standardize=True,
        min_std_exempt=list(CX_MIN_STD_EXEMPT),
        collinearity_policy="keep_all",
    )
    model = dict(fit_full if fit_full.get("success") else fit)
    try:
        model["intercept"] = round(float(model.get("intercept") or 0.0) + y_mean, 6)
    except (TypeError, ValueError):
        model["intercept"] = round(y_mean, 6)
    model["intercept_demeaned"] = round(float(fit.get("intercept") or 0.0), 6)
    model["y_label_mean"] = round(y_mean, 6)
    model["y_demeaned"] = True
    from core.signal.minute_tau_grid import format_shared_tau_formula

    y_formula = format_shared_tau_formula("1-D/L", grid or [live_hm])
    model["y_spec"] = {
        "formula": y_formula,
        "unit": "complexity_01",
        "tau": live_hm,
        "tau_grid": list(grid),
        "note": (
            "Kaufman 1−ER：D=全日 5m 收价首末位移，L=邻根路径长（午休跳空不计）；"
            "y_complexity∈[0,1]，0≈直线、1=最折。特征=开盘 Z + ≤τ 前缀"
            " + complexity_lag1/ma5 + tpd_lag1/ma5；"
            "标签=全日 1−D/L（不变）。TPD（转折点密度）补 1−D/L 的中间形状盲区。OOS 看 IC / 中位命中，不看方向命中。"
        ),
    }
    model["extra_features"] = list(CX_Z_FEATURES)
    model["minute_tau_hm"] = live_hm
    model["tau_grid"] = list(grid)
    model["feat_labels"] = {
        **dict(MINUTE_TAU_FEAT_LABELS),
        **dict(CANON_LAG_FEAT_LABELS),
    }

    report = {
        "success": True,
        "task": "cx_ridge",
        "stock_count": len(enriched),
        "sample_count": len(ys),
        "oos": oos,
        "return_model": model,
        "return_model_research": research_model,
        "schema": "cx_ridge_v1",
        "target": "path_complexity_5m_er",
        "minute_tau_hm": live_hm,
        "tau_grid": list(grid),
        "dual_score_head": "y_complexity",
        "note": "开盘 Z + 多 τ 前缀 + 历史真实曲折度 → 全日曲折度 [0,1]；ŷ_complexity>y_complexity_max 跳过做 T",
    }
    attach_holdout_meta(report, split_meta)
    report["promote_gate"] = cx_promote_gate(report)
    return report


def cx_model_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "cx_ridge_model.json")


def cx_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "cx_ridge_last_report.json")


_CX_MODEL_CACHE: Optional[Tuple[Tuple[float, float], Optional[Dict[str, Any]]]] = None


def save_cx_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    if not isinstance(report.get("return_model"), dict):
        return
    path = cx_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)
    global _CX_MODEL_CACHE
    _CX_MODEL_CACHE = None


def _mtime_or_missing(path: str) -> float:
    try:
        return os.path.getmtime(path) if os.path.isfile(path) else -1.0
    except OSError:
        return -1.0


def load_cx_last_report() -> Optional[Dict[str, Any]]:
    path = cx_last_report_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001
        logger.debug("load_cx_last_report failed", exc_info=True)
        return None
    if not isinstance(doc, dict) or not doc.get("success"):
        return None
    if not isinstance(doc.get("return_model"), dict):
        return None
    return doc


def load_cx_model(*, role: Optional[str] = None) -> Optional[Dict[str, Any]]:
    from core.research.holdout import (
        MODEL_ROLE_RESEARCH,
        current_scoring_model_role,
        load_research_promoted_json,
    )

    role_n = role if role is not None else current_scoring_model_role()
    if role_n == MODEL_ROLE_RESEARCH:
        return load_research_promoted_json(cx_model_path())
    global _CX_MODEL_CACHE
    model_p = cx_model_path()
    report_p = cx_last_report_path()
    key = (_mtime_or_missing(model_p), _mtime_or_missing(report_p))
    if _CX_MODEL_CACHE is not None and _CX_MODEL_CACHE[0] == key:
        return _CX_MODEL_CACHE[1]
    doc: Optional[Dict[str, Any]] = None
    if os.path.isfile(model_p):
        try:
            with open(model_p, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get("return_model"), dict):
                doc = loaded
        except Exception:  # noqa: BLE001
            logger.debug("load_cx_model failed", exc_info=True)
    if doc is None:
        fallback = load_cx_last_report()
        if fallback:
            out = dict(fallback)
            out["_shadow"] = True
            doc = out
    _CX_MODEL_CACHE = (key, doc)
    return doc


def persist_cx_model(
    report: Dict[str, Any],
    *,
    note: str = "",
    force: bool = False,
    role: str = "live",
) -> Dict[str, Any]:
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
    gate = cx_promote_gate(report)
    if not force and not gate.get("ok"):
        return {
            "success": False,
            "error": "promote 未过闸：" + "；".join(gate.get("blockers") or []),
            "promote_gate": gate,
        }
    from core.numbers import now_iso_utc

    doc = {
        "success": True,
        "promoted_at": now_iso_utc(),
        "note": note or "cx_ridge promote",
        "return_model": rm,
        "oos": report.get("oos"),
        "sample_count": report.get("sample_count"),
        "stock_count": report.get("stock_count"),
        "schema": report.get("schema") or "cx_ridge_v1",
        "minute_tau_hm": report.get("minute_tau_hm") or rm.get("minute_tau_hm"),
        "tau_grid": report.get("tau_grid") or rm.get("tau_grid"),
        "promote_gate": gate,
        "model_role": role_n,
        "fit_end": report.get("fit_end"),
        "eval_start": report.get("eval_start"),
        "holdout_trading_days": report.get("holdout_trading_days"),
        "dual_score_head": "y_complexity",
    }
    path = (
        research_model_path(cx_model_path())
        if role_n == MODEL_ROLE_RESEARCH
        else cx_model_path()
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, doc)
    global _CX_MODEL_CACHE
    _CX_MODEL_CACHE = None
    out = dict(doc)
    out["path"] = path
    return out


def predict_cx_from_features(
    features: Dict[str, Optional[float]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    """开盘 Z + 前缀分钟 + 历史真实曲折度 → ŷ_complexity ∈ [0,1]（旧 ×100 模型自动折算）。"""
    doc = model_doc if model_doc is not None else load_cx_model()
    if not doc:
        return None
    rm = doc.get("return_model") or {}
    names = list(rm.get("extra_features") or CX_Z_FEATURES)
    if not names:
        names = list(CX_Z_FEATURES)
    row: Dict[str, Optional[float]] = {}
    for k in names:
        row[k] = resolve_feat_value(features, k)
    preds = _predict_rows(rm, [row])
    if not preds or preds[0] is None:
        return None
    return cx_as_unit_01(float(preds[0]), model_doc=doc)
