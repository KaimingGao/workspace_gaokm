"""离线 A/B：ŷ_τc 的 Z-only vs alpha_τc（Z ∪ raw_aoo_*）。

现网已回退 Z∪alpha_oo；本脚本对照「只吃 Z」与「完整包」。
``raw_atc_*`` 不进现网，见 ``alpha_tc.py`` 模块注释。

观察池最多 300 股；近 ``train_days+holdout_days`` 个交易日；
Holdout 末段只测；**不落盘 / 不 promote**。

用法（在 quantlab/ 下）::

    .venv/bin/python scripts/alpha_tc_oos_compare.py
    .venv/bin/python scripts/alpha_tc_oos_compare.py --max-names 300 --train-days 160 --holdout-days 40
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

sys.path.insert(0, ".")

from core.research.holdout import (
    calendar_dates_from_stock_bars,
    resolve_ridge_split,
)
from core.research.panel_matrix import (
    collect_tau_compact,
    fill_rates_from_matrix,
    fit_keepall_ridge_matrix,
    named_columns,
    predict_ridge_matrix,
)
from core.research.portfolio_bars import load_portfolio_stock_bars
from core.research.tc_ridge import (
    TAU_FIT_DROP_ALIASES,
    TAU_MIN_STD_EXEMPT,
    TAU_Z_FEATURES,
    _ic,
    _oos_by_theme,
    _oos_sign_buckets,
    _residual_var,
    _sign_hit,
)
from core.research.tau_panel import theme_sample_weights
from core.research_universe import resolve_research_codes
from core.signal.factors.alpha_oo import (
    ALPHA_OO_MIN_BARS,
    is_alpha_oo_raw_key,
)
from core.signal.minute_tau_feats import MINUTE_TAU_ALL_KEYS


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="ŷ_τc offline A/B: Z-only vs Z∪raw_aoo_*"
    )
    p.add_argument("--max-names", type=int, default=300)
    p.add_argument("--train-days", type=int, default=160)
    p.add_argument("--holdout-days", type=int, default=40)
    p.add_argument("--ridge-lambda", type=float, default=1.0)
    p.add_argument("--gap-trigger", type=float, default=2.0)
    p.add_argument("--theme-boost", type=float, default=1.5)
    p.add_argument("--min-history", type=int, default=ALPHA_OO_MIN_BARS)
    return p.parse_args()


def load_stock_bars(max_names: int, lookback: int) -> List[Dict[str, Any]]:
    codes = resolve_research_codes()["codes"]
    stock_bars_dict, _f, _s = load_portfolio_stock_bars(
        codes,
        lookback=lookback,
        max_names=int(max_names),
        offline_ok=True,
        fetch_fundamentals=False,
    )
    need = int(ALPHA_OO_MIN_BARS) + 2
    return [
        {"code": c, "bars": b}
        for c, b in stock_bars_dict.items()
        if len(b) >= need
    ]


def _filter_last_calendar_days(
    X: np.ndarray,
    col_names: Sequence[str],
    ys: Sequence[float],
    dates: Sequence[str],
    metas: Sequence[dict],
    calendar: Sequence[str],
    *,
    n_days: int,
) -> Tuple[np.ndarray, List[str], List[float], List[str], List[dict], List[str]]:
    cal = [str(d)[:10] for d in calendar if str(d)[:10]]
    if len(cal) > n_days:
        cal = cal[-n_days:]
    keep_set = set(cal)
    idx = [i for i, d in enumerate(dates) if str(d)[:10] in keep_set]
    if not idx:
        return (
            X[0:0],
            list(col_names),
            [],
            [],
            [],
            list(cal),
        )
    ii = np.asarray(idx, dtype=np.int64)
    return (
        np.ascontiguousarray(X[ii]),
        list(col_names),
        [ys[i] for i in idx],
        [str(dates[i])[:10] for i in idx],
        [metas[i] for i in idx],
        list(cal),
    )


def _finite_aoo(
    X: np.ndarray,
    names: Sequence[str],
    row_idx: Sequence[int],
) -> List[str]:
    if X.size == 0 or not row_idx:
        return []
    view = X[np.asarray(list(row_idx), dtype=np.int64)]
    out: List[str] = []
    for j, name in enumerate(names):
        if not is_alpha_oo_raw_key(str(name)):
            continue
        if np.isfinite(view[:, j]).any():
            out.append(str(name))
    return out


def fit_arm(
    X: np.ndarray,
    col_names: Sequence[str],
    ys: Sequence[float],
    dates: Sequence[str],
    metas: Sequence[dict],
    *,
    arm: str,
    holdout_days: int,
    calendar: Sequence[str],
    ridge_lambda: float,
    theme_boost: float,
) -> Dict[str, Any]:
    train_idx, test_idx, split_meta = resolve_ridge_split(
        dates,
        holdout_trading_days=holdout_days,
        calendar_dates=calendar,
    )
    from core.signal.factors.z_tc import Z_TC_MINUTE_KEYS

    z_base = [
        k
        for k in TAU_Z_FEATURES
        if k not in TAU_FIT_DROP_ALIASES
        and k not in Z_TC_MINUTE_KEYS
        and k not in MINUTE_TAU_ALL_KEYS
        and k in set(col_names)
    ]
    feat_names = list(z_base)
    seen = set(feat_names)
    if arm == "aoo":
        for k in _finite_aoo(X, col_names, train_idx):
            if k not in seen:
                seen.add(k)
                feat_names.append(k)

    min_std_exempt = list(TAU_MIN_STD_EXEMPT)
    for n in feat_names:
        if is_alpha_oo_raw_key(str(n)) and str(n) not in min_std_exempt:
            min_std_exempt.append(str(n))

    X_fit, feat_names = named_columns(X, col_names, feat_names)
    dates_tr = [dates[i] for i in train_idx]
    dates_te = [dates[i] for i in test_idx]
    ys_tr = [ys[i] for i in train_idx]
    ys_te = [ys[i] for i in test_idx]
    metas_tr = [metas[i] for i in train_idx]
    metas_te = [metas[i] for i in test_idx]
    theme_w = theme_sample_weights(metas_tr, theme_boost=theme_boost)

    row_tr = np.asarray(train_idx, dtype=np.int64)
    fit = fit_keepall_ridge_matrix(
        X_fit[row_tr],
        np.asarray(ys_tr, dtype=np.float64),
        feat_names,
        ridge_lambda=ridge_lambda,
        sample_weights=theme_w,
        min_std_exempt=min_std_exempt,
        row_dates=dates_tr,
        cross_section_zscore=True,
    )
    if not fit.get("success"):
        return {
            "success": False,
            "arm": arm,
            "error": fit.get("error") or "fit failed",
            "split": split_meta,
        }

    row_te = np.asarray(test_idx, dtype=np.int64)
    preds = (
        predict_ridge_matrix(
            fit,
            X_fit[row_te],
            feat_names,
            row_dates=dates_te,
        )
        if len(test_idx)
        else []
    )
    buckets = _oos_sign_buckets(preds, ys_te) if ys_te else {}
    active = list(fit.get("active_features") or [])
    n_aoo = sum(1 for a in active if is_alpha_oo_raw_key(str(a)))
    oos: Dict[str, Any] = {
        "n_train": len(ys_tr),
        "n_test": len(ys_te),
        "ic": _ic(preds, ys_te) if ys_te else None,
        "sign_hit": _sign_hit(preds, ys_te) if ys_te else None,
        "residual_var": _residual_var(preds, ys_te) if ys_te else None,
        "by_theme": _oos_by_theme(preds, ys_te, metas_te) if ys_te else {},
        "pos_recall": buckets.get("pos_recall"),
        "neg_recall": buckets.get("neg_recall"),
        "n_pos": buckets.get("n_pos"),
        "n_neg": buckets.get("n_neg"),
    }
    try:
        from core.research.daily_cs_ic import attach_daily_cs_ic

        if ys_te:
            attach_daily_cs_ic(oos, preds, ys_te, metas_te)
    except Exception:  # noqa: BLE001
        pass

    fill_keys = list(z_base) + [
        k for k in feat_names if is_alpha_oo_raw_key(k)
    ]
    return {
        "success": True,
        "arm": arm,
        "label": "Z-only" if arm == "z" else "Z∪raw_aoo_*",
        "n_features": len(feat_names),
        "n_active": len(active),
        "n_aoo_active": n_aoo,
        "n_train_days": split_meta.get("n_train_days"),
        "n_test_days": split_meta.get("n_test_days"),
        "fit_end": split_meta.get("fit_end"),
        "eval_start": split_meta.get("eval_start"),
        "oos": oos,
        "feature_fill_n": fill_rates_from_matrix(X_fit, feat_names, fill_keys[:8]).get(
            "n"
        ),
    }


def _fmt(v: Any, nd: int = 4) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if v != v:
            return "—"
        return f"{v:.{nd}f}"
    return str(v)


def _print_arm(r: Dict[str, Any]) -> None:
    oos = r.get("oos") or {}
    print(f"\n=== {r.get('label')} ({r.get('arm')}) ===")
    if not r.get("success"):
        print(f"  FAIL: {r.get('error')}")
        return
    print(
        f"  days train/test={r.get('n_train_days')}/{r.get('n_test_days')}  "
        f"fit_end={r.get('fit_end')}  eval_start={r.get('eval_start')}"
    )
    print(
        f"  features={r.get('n_features')} active={r.get('n_active')} "
        f"aoo_active={r.get('n_aoo_active')}"
    )
    print(
        f"  IC={_fmt(oos.get('ic'))}  hit={_fmt(oos.get('sign_hit'))}  "
        f"cs_ic={_fmt(oos.get('cs_ic'))}  cs_rank_icir={_fmt(oos.get('cs_rank_icir'))}"
    )


def main() -> int:
    args = _parse_args()
    train_days = max(20, int(args.train_days))
    holdout_days = max(5, int(args.holdout_days))
    window_days = train_days + holdout_days
    lookback = max(window_days + 40, int(args.min_history) + window_days)

    t0 = time.time()
    print(
        f"load watching ≤{args.max_names}, lookback={lookback}, "
        f"window={train_days}+{holdout_days} (no persist)"
    )
    stock_bars = load_stock_bars(args.max_names, lookback)
    print(f"loaded {len(stock_bars)} stocks ({time.time() - t0:.1f}s)")
    if len(stock_bars) < 20:
        print("ERROR: too few stocks with enough bars")
        return 1

    t1 = time.time()
    from core.signal.factors.z_tc import Z_TC_MINUTE_KEYS

    z_names = [
        k
        for k in TAU_Z_FEATURES
        if k not in TAU_FIT_DROP_ALIASES
        and k not in Z_TC_MINUTE_KEYS
        and k not in MINUTE_TAU_ALL_KEYS
    ]
    X, names, ys, dates, metas, n_stocks = collect_tau_compact(
        stock_bars,
        z_names,
        min_history=int(args.min_history),
        gap_trigger_pct=float(args.gap_trigger),
        include_alpha_oo=True,
        head="y_tc_ab",
    )
    print(
        f"panel: stocks={n_stocks} rows={len(ys)} cols={len(names)} "
        f"({time.time() - t1:.1f}s)"
    )
    n_aoo = sum(1 for n in names if is_alpha_oo_raw_key(str(n)))
    print(f"  columns raw_aoo_={n_aoo}")

    cal_all = calendar_dates_from_stock_bars(stock_bars)
    X, names, ys, dates, metas, cal = _filter_last_calendar_days(
        X, names, ys, dates, metas, cal_all, n_days=window_days
    )
    print(
        f"calendar window: {len(cal)} days "
        f"[{cal[0] if cal else '—'} … {cal[-1] if cal else '—'}]  rows={len(ys)}"
    )
    if len(cal) < holdout_days + 20:
        print("ERROR: calendar too short for requested split")
        return 1

    r_z = fit_arm(
        X,
        names,
        ys,
        dates,
        metas,
        arm="z",
        holdout_days=holdout_days,
        calendar=cal,
        ridge_lambda=float(args.ridge_lambda),
        theme_boost=float(args.theme_boost),
    )
    r_aoo = fit_arm(
        X,
        names,
        ys,
        dates,
        metas,
        arm="aoo",
        holdout_days=holdout_days,
        calendar=cal,
        ridge_lambda=float(args.ridge_lambda),
        theme_boost=float(args.theme_boost),
    )
    _print_arm(r_z)
    _print_arm(r_aoo)
    print(f"\ndone in {time.time() - t0:.1f}s")
    return 0 if r_z.get("success") and r_aoo.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
