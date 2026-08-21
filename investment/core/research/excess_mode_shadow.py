"""绝对 y vs 指数超额 y（excess_mode）影子对照。

同池、同一时间切分：``none`` vs ``index`` 标签下 Ridge OOS IC。
不写 config；优则人审改面板/分组 ``excess_mode`` 后重跑。
"""


import logging

logger = logging.getLogger(__name__)
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


def _fit_excess_arm(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    excess_mode: str,
    horizon_days: int,
    ridge_lambda: float,
    train_frac: float,
    index_bars: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    from core.research.factor_ols_fit import fit_factor_ols_from_panel
    from core.research.panel import collect_subscore_forward_panel
    from core.research.rem_ridge import _predict_rows

    em = str(excess_mode or "none").strip().lower()
    if em in ("index", "excess", "vs_index", "benchmark"):
        em = "index"
    else:
        em = "none"

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
            index_bars=index_bars or item.get("index_bars"),
            fundamentals=item.get("fundamentals"),
            stock_code=code or None,
            pit_fundamentals=True,
            excess_mode=em,
        )
        if not xs:
            continue
        xs_all.extend(xs)
        ys_all.extend(ys)
        dates_all.extend(dates)

    n = len(ys_all)
    if n < 40:
        return {
            "success": False,
            "excess_mode": em,
            "error": f"样本不足 n={n}",
            "n": n,
        }

    tr_idx, te_idx = _time_split(dates_all, train_frac=train_frac)
    if not te_idx:
        return {
            "success": False,
            "excess_mode": em,
            "error": "holdout_empty",
            "n": n,
        }

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
        ridge_lambda=float(ridge_lambda),
        standardize=True,
        collinearity_policy="drop_redundant",
    )
    if not fit.get("success"):
        return {
            "success": False,
            "excess_mode": em,
            "error": fit.get("error") or "fit_failed",
            "n": n,
        }

    preds = _predict_rows(fit, xs_te) if xs_te else []
    preds_f = [float(p) for p in preds if p is not None]
    ys_f = [float(y) for p, y in zip(preds, ys_te) if p is not None]
    return {
        "success": True,
        "excess_mode": em,
        "n": n,
        "n_train": len(ys_tr),
        "n_hold": len(ys_f),
        "oos": {
            "ic": _spearman(preds_f, ys_f) if ys_f else None,
            "sign_hit": _sign_hit(preds_f, ys_f) if ys_f else None,
            "n": len(ys_f),
        },
        "ic_kind": "chrono_spearman",
        "note": "holdout 拼样本 Spearman；辅指标，≠ 截面主 IC",
    }


def compare_excess_mode_shadow(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    horizon_days: int = 1,
    ridge_lambda: float = 1.0,
    train_frac: float = 0.7,
    index_bars: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    """绝对收益标签 vs 指数超额标签 OOS 对照。"""
    h = max(1, min(int(horizon_days or 1), 10))
    idx = list(index_bars or [])
    if not idx:
        try:
            from core.data_service import get_index_bars
            from core.ports.market import default_benchmark

            code = str(default_benchmark("CN") or "sh000300")
            raw = get_index_bars(code, limit=220)
            if isinstance(raw, dict):
                idx = list(raw.get("bars") or [])
            elif isinstance(raw, tuple):
                idx = list(raw[0] or [])
            elif isinstance(raw, list):
                idx = list(raw)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in excess_mode_shadow.py", exc_info=True)
            idx = []

    arms: Dict[str, Any] = {}
    for em in ("none", "index"):
        arms[em] = _fit_excess_arm(
            stock_bars,
            excess_mode=em,
            horizon_days=h,
            ridge_lambda=float(ridge_lambda),
            train_frac=float(train_frac),
            index_bars=idx,
        )

    abs_arm = arms.get("none") or {}
    ex_arm = arms.get("index") or {}
    winner = None
    note = "影子对照；不写 live / 不改 y_spec"
    if abs_arm.get("success") and ex_arm.get("success"):
        ic_a = (abs_arm.get("oos") or {}).get("ic")
        ic_e = (ex_arm.get("oos") or {}).get("ic")
        if ic_a is not None and ic_e is not None:
            if float(ic_e) > float(ic_a) + 0.01:
                winner = "index"
                note = (
                    "超额标签 holdout IC 更优；可人审用 excess_mode=index 重跑分组 "
                    "（预测相对指数，不是绝对涨跌）。"
                )
            elif float(ic_a) > float(ic_e) + 0.01:
                winner = "none"
                note = "绝对收益标签仍优；保持默认 y_spec"
            else:
                winner = "tie"
                note = "两者接近；要抓 α 可偏 index，要绝对收益保持 none"

    return {
        "success": bool(abs_arm.get("success") or ex_arm.get("success")),
        "ok": bool(abs_arm.get("success") or ex_arm.get("success")),
        "task": "excess_mode_shadow",
        "horizon_days": h,
        "ridge_lambda": float(ridge_lambda),
        "index_bars": len(idx),
        "arms": arms,
        "winner": winner,
        "note": note,
        "promote_hint": "优则人审设研究臂 excess_mode=index 并重跑；不自动写盘。",
    }
