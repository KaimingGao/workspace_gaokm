"""剩余收益头 Ridge：Z 上拟合 open→close；与 ŷ_EOD_rem 正交后加权成 ŷ_trade。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence

from core.io_atomic import atomic_write_json
from core.research.factor_ols_fit import fit_factor_ols_from_panel
from core.research.rem_panel import (
    attach_cross_section_breadth,
    collect_rem_open_panel,
    theme_sample_weights,
)


REM_FEATURE_EXTRA = ("gap_pct", "open_gap", "sector_gap_breadth", "theme_day")
# τ 头只吃开盘新信息，避免与 ŷ_EOD 的 X 双重计权
REM_Z_FEATURES = (
    "gap_pct",
    "sector_gap_breadth",
    "theme_day",
    "ret_open_to_tau",
)
# gap/breadth 单位是百分点或 [0,1]，勿用 0–100 分制的 min_std=5 误剔
REM_MIN_STD_EXEMPT = REM_FEATURE_EXTRA + ("ret_open_to_tau",)
# open_gap ≡ gap_pct，只拟合其一，避免 Ridge 双计
REM_FIT_DROP_ALIASES = frozenset({"open_gap"})


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


def _z_only_row(row: Optional[dict]) -> Dict[str, Optional[float]]:
    src = row or {}
    out: Dict[str, Optional[float]] = {}
    for k in REM_Z_FEATURES:
        if k in src:
            out[k] = src.get(k)
    return out


def _z_only_xs(xs: Sequence[dict]) -> List[dict]:
    return [_z_only_row(r) for r in xs]


def build_rem_panels_from_bars(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
) -> List[Dict[str, Any]]:
    """``stock_bars``: ``[{code, bars, index_bars?, fundamentals?}, ...]``。"""
    raw: List[Dict[str, Any]] = []
    for item in stock_bars:
        code = str(item.get("code") or item.get("stock_code") or "").strip()
        bars = list(item.get("bars") or [])
        if len(bars) < min_history + 2:
            continue
        xs, ys, dates, metas = collect_rem_open_panel(
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


def fit_rem_ridge_report(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    ridge_lambda: float = 1.0,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    theme_boost: float = 1.5,
    train_frac: float = 0.7,
    use_theme_weights: bool = True,
) -> Dict[str, Any]:
    """池化拟合 rem 头 ŷ_τ(Z) + 时间 OOS。

    标签 = y_oc = close[T]/open[T]−1；特征仅 Z（缺口/广度/主题）。
    不读 EOD 模型、不残差化；与 ŷ_EOD 解耦，live 才加权。
    """
    enriched = build_rem_panels_from_bars(
        stock_bars,
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
    )
    xs, ys, dates, metas = _stack_panels(enriched)
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"rem 样本不足 n={len(ys)}（需≥20）",
            "task": "rem_ridge",
            "sample_count": len(ys),
            "stock_count": len(enriched),
        }

    xs_use, ys_use, dates_use, metas_use = xs, ys, dates, metas
    target = "open_to_close_z"

    xs_z = _z_only_xs(xs_use)
    train_idx, test_idx = _time_split_indices(dates_use, train_frac=train_frac)
    xs_tr, ys_tr, metas_tr = _subset(xs_z, ys_use, metas_use, train_idx)
    xs_te, ys_te, _metas_te = _subset(xs_z, ys_use, metas_use, test_idx)

    weights = (
        theme_sample_weights(metas_tr, theme_boost=theme_boost)
        if use_theme_weights
        else None
    )

    feat_names: List[str] = []
    seen = set()
    for row in xs_tr:
        for k in row.keys():
            if k in REM_FIT_DROP_ALIASES:
                continue
            if k not in seen:
                seen.add(k)
                feat_names.append(k)
    if not feat_names:
        feat_names = [k for k in REM_Z_FEATURES if k != "ret_open_to_tau"]

    fit = fit_factor_ols_from_panel(
        xs_tr,
        ys_tr,
        feature_names=feat_names,
        ridge_lambda=ridge_lambda,
        standardize=True,
        sample_weights=weights,
        min_std_exempt=list(REM_MIN_STD_EXEMPT),
        collinearity_policy="keep_all",
    )
    if not fit.get("success"):
        # Z 截面过弱（合成/窄池）时退回截距头
        mu = sum(float(y) for y in ys_tr) / max(1, len(ys_tr))
        fit = {
            "success": True,
            "intercept": round(mu, 6),
            "coefficients": {},
            "active_features": [],
            "zscore_means": {},
            "zscore_stds": {},
            "note": "Z 方差不足，ŷ_τ 用训练均值",
        }

    preds_te = _predict_rows(fit, xs_te) if xs_te else []
    oos = {
        "n_train": len(ys_tr),
        "n_test": len(ys_te),
        "ic": _ic(preds_te, ys_te) if ys_te else None,
        "sign_hit": _sign_hit(preds_te, ys_te) if ys_te else None,
        "train_frac": train_frac,
        "theme_boost": theme_boost if use_theme_weights else None,
        "target": target,
        "residualized": False,
    }

    w_all = (
        theme_sample_weights(metas_use, theme_boost=theme_boost)
        if use_theme_weights
        else None
    )
    fit_full = fit_factor_ols_from_panel(
        xs_z,
        ys_use,
        feature_names=feat_names,
        ridge_lambda=ridge_lambda,
        standardize=True,
        sample_weights=w_all,
        min_std_exempt=list(REM_MIN_STD_EXEMPT),
        collinearity_policy="keep_all",
    )
    model = fit_full if fit_full.get("success") else fit
    model = dict(model)
    model["horizon_mode"] = "open_to_close"
    model["target"] = target
    model["residualized"] = False
    model["y_spec"] = {
        "formula": "close[T]/open[T]-1",
        "unit": "pct",
        "tau": "open",
        "note": "Z-only open→close；与 ŷ_EOD_rem 正交加权成 ŷ_trade",
    }
    model["extra_features"] = list(REM_Z_FEATURES)

    return {
        "success": True,
        "task": "rem_ridge",
        "stock_count": len(enriched),
        "sample_count": len(ys_use),
        "sample_count_raw": len(ys),
        "oos": oos,
        "return_model": model,
        "tau": "open",
        "y_spec": dict(model.get("y_spec") or {}),
        "schema": "rem_ridge_v4",
        "target": target,
        "residualized": False,
        "note": "ŷ_τ(Z) 独立估 open→close；与 EOD 解耦，live 才加权",
    }


def rem_model_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "rem_ridge_model.json")


def persist_rem_model(report: Dict[str, Any], *, note: str = "") -> Dict[str, Any]:
    """人审后写入 data/live/rem_ridge_model.json（契约：τ + y_spec）。"""
    if not report.get("success"):
        return {"success": False, "error": report.get("error") or "no report"}
    rm = report.get("return_model")
    if not isinstance(rm, dict):
        return {"success": False, "error": "return_model missing"}
    if not rm.get("coefficients") and rm.get("intercept") is None:
        return {"success": False, "error": "return_model missing"}
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

    schema = str(report.get("schema") or "rem_ridge_v4")
    doc = {
        "success": True,
        "promoted_at": now_iso_utc(),
        "note": note or "rem_ridge promote",
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
        "dual_score_head": "predicted_score_tau",
        "contract_note": "ŷ_τ(Z) 估 open→close；live 与 ŷ_EOD_rem 加权融合；不覆盖 EOD predicted_score。",
    }
    path = rem_model_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, doc)
    return {
        "success": True,
        "path": path,
        "promoted_at": doc["promoted_at"],
        "tau": tau,
        "schema": doc["schema"],
    }


def load_rem_model() -> Optional[Dict[str, Any]]:
    path = rem_model_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        if isinstance(doc, dict) and isinstance(doc.get("return_model"), dict):
            return doc
    except Exception:
        return None
    return None


def predict_rem_from_features(
    features: Dict[str, Optional[float]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    doc = model_doc if model_doc is not None else load_rem_model()
    if not doc:
        return None
    rm = doc.get("return_model") or {}
    preds = _predict_rows(rm, [features])
    return preds[0] if preds else None


_REM_FEAT_LABELS = {
    "gap_pct": "跳空 %",
    "open_gap": "开盘缺口",
    "sector_gap_breadth": "同业缺口广度",
    "theme_day": "主题日",
    "ret_open_to_tau": "开盘→τ 收益 %",
}


def explain_rem_prediction(
    features: Optional[Dict[str, Any]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """结构化拆解 ŷ_τ（与 ``predict_rem_from_features`` 同口径），供 tip 表格。

    缺特征按训练集均值填（z=0），与 live 预测一致。
    """
    doc = model_doc if model_doc is not None else load_rem_model()
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
        from core.signal.factor_registry import factor_label
    except Exception:
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
        label = _REM_FEAT_LABELS.get(name) or factor_label(name) or name
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
    # 旧 rem 仍含日线 β 时：缺特征的非 Z 行对 tip 无信息，只保留 Z（含缺特征）与有实值的项
    z_keys = set(REM_Z_FEATURES) | {"open_gap"}
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
