"""启发式 0–100 vs raw+分档 特征编码影子对照。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


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
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx < 1e-12 or dy < 1e-12:
        return None
    return round(num / (dx * dy), 4)


def _sign_hit(preds: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = 0
    hit = 0
    for p, y in zip(preds, ys):
        if abs(float(p)) < 0.05:
            continue
        n += 1
        if (float(p) > 0 and float(y) > 0) or (float(p) < 0 and float(y) < 0):
            hit += 1
    if n < 8:
        return None
    return round(hit / float(n), 4)


def _time_split(dates: List[str], train_frac: float = 0.7):
    order = sorted(range(len(dates)), key=lambda i: dates[i])
    n = len(order)
    if n < 20:
        return order, []
    cut = max(8, int(n * float(train_frac)))
    cut = min(cut, n - 5)
    return order[:cut], order[cut:]


def _fit_encoding_arm(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    encoding: str,
    horizon_days: int,
    ridge_lambda: float,
    train_frac: float,
) -> Dict[str, Any]:
    from core.research.factor_ols_fit import fit_factor_ols_from_panel
    from core.research.panel import collect_subscore_forward_panel
    from core.signal.factors.raw_basis import RAW_BASIS_MIN_STD_EXEMPT
    from quant.research.rem_ridge import _predict_rows

    xs_all: List[dict] = []
    ys_all: List[float] = []
    dates_all: List[str] = []
    for item in stock_bars:
        code = str(item.get("code") or item.get("stock_code") or "").strip()
        bars = list(item.get("bars") or [])
        if len(bars) < 20:
            continue
        xs, ys, dates = collect_subscore_forward_panel(
            bars,
            horizon_days=horizon_days,
            index_bars=item.get("index_bars"),
            fundamentals=item.get("fundamentals"),
            stock_code=code,
            feature_encoding=encoding,
        )
        xs_all.extend(xs)
        ys_all.extend(ys)
        dates_all.extend(dates)

    if len(ys_all) < 40:
        return {
            "success": False,
            "encoding": encoding,
            "error": f"样本不足 n={len(ys_all)}",
            "n": len(ys_all),
        }

    tr_idx, te_idx = _time_split(dates_all, train_frac=train_frac)
    xs_tr = [xs_all[i] for i in tr_idx]
    ys_tr = [ys_all[i] for i in tr_idx]
    xs_te = [xs_all[i] for i in te_idx]
    ys_te = [ys_all[i] for i in te_idx]

    feat_names: List[str] = []
    seen = set()
    for row in xs_tr:
        for k, v in (row or {}).items():
            if v is None or k in seen:
                continue
            seen.add(k)
            feat_names.append(k)

    fit = fit_factor_ols_from_panel(
        xs_tr,
        ys_tr,
        feature_names=feat_names,
        ridge_lambda=ridge_lambda,
        standardize=True,
        min_std_exempt=list(RAW_BASIS_MIN_STD_EXEMPT),
        collinearity_policy="drop_redundant",
    )
    if not fit.get("success"):
        return {
            "success": False,
            "encoding": encoding,
            "error": fit.get("error") or "fit failed",
            "n": len(ys_all),
        }

    preds = _predict_rows(fit, xs_te) if xs_te else []
    preds_f = [float(p) for p in preds if p is not None]
    ys_f = [float(y) for p, y in zip(preds, ys_te) if p is not None]
    return {
        "success": True,
        "encoding": encoding,
        "n": len(ys_all),
        "n_train": len(ys_tr),
        "n_test": len(ys_te),
        "n_features": len(fit.get("active_features") or []),
        "active_features": list(fit.get("active_features") or []),
        "oos": {
            "ic": _spearman(preds_f, ys_f) if ys_f else None,
            "sign_hit": _sign_hit(preds_f, ys_f) if ys_f else None,
            "n": len(ys_f),
        },
        "replaced": (
            ["momentum", "volatility", "value"]
            if encoding == "raw_basis"
            else []
        ),
    }


def compare_feature_encoding_shadow(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    horizon_days: int = 1,
    ridge_lambda: float = 1.0,
    train_frac: float = 0.7,
) -> Dict[str, Any]:
    """同一池、同一时间切分：heuristic vs raw_basis OOS 对照。"""
    h = max(1, min(int(horizon_days or 1), 10))
    arms = {}
    for enc in ("heuristic", "raw_basis"):
        arms[enc] = _fit_encoding_arm(
            stock_bars,
            encoding=enc,
            horizon_days=h,
            ridge_lambda=float(ridge_lambda),
            train_frac=float(train_frac),
        )

    heur = arms.get("heuristic") or {}
    raw = arms.get("raw_basis") or {}
    winner = None
    note = "影子对照；不写 live / 不改 signal_config"
    if heur.get("success") and raw.get("success"):
        ic_h = (heur.get("oos") or {}).get("ic")
        ic_r = (raw.get("oos") or {}).get("ic")
        if ic_h is not None and ic_r is not None:
            if float(ic_r) > float(ic_h) + 0.01:
                winner = "raw_basis"
                note = "raw_basis holdout IC 更优；可人审切 scoring.feature_encoding 后重跑分组"
            elif float(ic_h) > float(ic_r) + 0.01:
                winner = "heuristic"
                note = "heuristic 仍优；保持默认编码"
            else:
                winner = "tie"
                note = "两者接近；优先可解释/稳定，不必急切"

    return {
        "success": bool(heur.get("success") or raw.get("success")),
        "task": "feature_encoding_shadow",
        "horizon_days": h,
        "ridge_lambda": float(ridge_lambda),
        "arms": arms,
        "winner": winner,
        "note": note,
    }
