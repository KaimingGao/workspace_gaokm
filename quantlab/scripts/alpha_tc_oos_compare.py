"""离线 A/B：现网 ŷ_τc（Z∪raw_aoo_*）vs Z∪raw_atc_*。

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
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

sys.path.insert(0, ".")

from core.research.holdout import (
    calendar_dates_from_stock_bars,
    resolve_ridge_split,
)
from core.research.panel_matrix import (
    PanelMatrix,
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
from core.research.tau_panel import (
    attach_cross_section_breadth,
    collect_tau_open_panel,
    theme_sample_weights,
)
from core.research_universe import resolve_research_codes
from core.signal.factors.alpha_oo import (
    is_alpha_oo_raw_key,
    merge_alpha_oo_min_std_exempt,
)
from core.signal.factors.alpha_tc import (
    ALPHA_TC_MIN_BARS,
    inject_raw_atc,
    is_alpha_tc_raw_key,
)
from core.signal.minute_tau_feats import MINUTE_TAU_ALL_KEYS


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ŷ_τc offline A/B: Z∪aoo vs Z∪atc")
    p.add_argument("--max-names", type=int, default=300)
    p.add_argument("--train-days", type=int, default=160)
    p.add_argument("--holdout-days", type=int, default=40)
    p.add_argument("--ridge-lambda", type=float, default=1.0)
    p.add_argument("--gap-trigger", type=float, default=2.0)
    p.add_argument("--theme-boost", type=float, default=1.5)
    p.add_argument("--min-history", type=int, default=ALPHA_TC_MIN_BARS)
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
    need = int(ALPHA_TC_MIN_BARS) + 2
    return [
        {"code": c, "bars": b}
        for c, b in stock_bars_dict.items()
        if len(b) >= need
    ]


def _inject_atc_on_open_rows(
    xs: List[dict],
    dates: Sequence[str],
    bars: Sequence[dict],
    *,
    min_history: int,
) -> None:
    """就地给开盘面板行补 raw_atc_*（price_τ=open_t）。"""
    by_date = {
        str(b.get("date") or b.get("trade_date") or "")[:10]: i
        for i, b in enumerate(bars)
        if isinstance(b, dict)
    }
    for row, day in zip(xs, dates):
        if not isinstance(row, dict):
            continue
        i = by_date.get(str(day)[:10])
        if i is None or i < 1:
            continue
        window = list(bars[max(0, i - max(min_history + 1, 62)) : i])
        if len(window) < ALPHA_TC_MIN_BARS - 1:
            continue
        try:
            o = float(bars[i].get("open") or 0)
            pc = float(bars[i - 1].get("close") or 0)
        except (TypeError, ValueError):
            continue
        if o <= 0 or pc <= 0:
            continue
        inject_raw_atc(
            row,
            window,
            open_t=o,
            prev_close=pc,
            price_tau=o,
            tau_hm="open",
            quote={"date": str(day)[:10], "open": o, "prev_close": pc},
        )


def build_dual_tau_matrix(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    min_history: int,
    gap_trigger_pct: float,
) -> Tuple[np.ndarray, List[str], List[float], List[str], List[dict], int]:
    """开盘 ŷ_τc 面板：Z + raw_aoo_* + raw_atc_*（同窗一次构建）。"""
    z_names = [
        k
        for k in TAU_Z_FEATURES
        if k not in TAU_FIT_DROP_ALIASES and k not in MINUTE_TAU_ALL_KEYS
    ]
    raw_panels: List[Dict[str, Any]] = []
    for item in stock_bars:
        code = str(item.get("code") or "").strip()
        bars = list(item.get("bars") or [])
        if len(bars) < min_history + 2:
            continue
        xs, ys, dates, metas = collect_tau_open_panel(
            bars,
            min_history=min_history,
            stock_code=code,
            include_alpha_oo=True,
        )
        if len(ys) < 4:
            continue
        _inject_atc_on_open_rows(xs, dates, bars, min_history=min_history)
        raw_panels.append(
            {
                "code": code,
                "xs": xs,
                "ys": ys,
                "dates": dates,
                "metas": metas,
            }
        )
    enriched = attach_cross_section_breadth(
        raw_panels, gap_trigger_pct=gap_trigger_pct
    )
    # 前缀 raw_a 同时 widen aoo / atc
    block = PanelMatrix(z_names, widen_prefix="raw_a")
    ys_out: List[float] = []
    dates_out: List[str] = []
    metas_out: List[dict] = []
    for p in enriched:
        xs = list(p.get("xs") or [])
        ys = list(p.get("ys") or [])
        dates = list(p.get("dates") or [])
        metas = list(p.get("metas") or [])
        if not ys:
            continue
        block.add_rows(xs)
        ys_out.extend(float(y) for y in ys)
        dates_out.extend(str(d)[:10] for d in dates)
        metas_out.extend(metas if metas else [{} for _ in ys])
    X = block.finalize()
    return X, list(block.names), ys_out, dates_out, metas_out, len(enriched)


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


def _finite_raw(
    X: np.ndarray,
    names: Sequence[str],
    row_idx: Sequence[int],
    *,
    kind: str,
) -> List[str]:
    if X.size == 0 or not row_idx:
        return []
    view = X[np.asarray(list(row_idx), dtype=np.int64)]
    pred = is_alpha_oo_raw_key if kind == "aoo" else is_alpha_tc_raw_key
    out: List[str] = []
    for j, name in enumerate(names):
        if not pred(str(name)):
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
    z_base = [
        k
        for k in TAU_Z_FEATURES
        if k not in TAU_FIT_DROP_ALIASES
        and k not in MINUTE_TAU_ALL_KEYS
        and k in set(col_names)
    ]
    feat_names = list(z_base)
    seen = set(feat_names)
    kind = "aoo" if arm == "prod" else "atc"
    for k in _finite_raw(X, col_names, train_idx, kind=kind):
        if k not in seen:
            seen.add(k)
            feat_names.append(k)

    if arm == "prod":
        min_std_exempt = merge_alpha_oo_min_std_exempt(
            feat_names, list(TAU_MIN_STD_EXEMPT)
        )
    else:
        min_std_exempt = list(TAU_MIN_STD_EXEMPT)
        for n in feat_names:
            if is_alpha_tc_raw_key(str(n)) and str(n) not in min_std_exempt:
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
    # named_columns 后行序未变；用相对索引
    # 上面 X_fit 与 X 同行，train_idx 仍是全局行号
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
    n_atc = sum(1 for a in active if is_alpha_tc_raw_key(str(a)))
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
        k for k in feat_names if is_alpha_oo_raw_key(k) or is_alpha_tc_raw_key(k)
    ]
    return {
        "success": True,
        "arm": arm,
        "label": "Z∪raw_aoo_*" if arm == "prod" else "Z∪raw_atc_*",
        "n_features": len(feat_names),
        "n_active": len(active),
        "n_aoo_active": n_aoo,
        "n_atc_active": n_atc,
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
        f"  feats={r.get('n_features')} active={r.get('n_active')} "
        f"aoo_active={r.get('n_aoo_active')} atc_active={r.get('n_atc_active')}"
    )
    print(
        f"  rows train/test={oos.get('n_train')}/{oos.get('n_test')}  "
        f"IC={_fmt(oos.get('ic'))}  sign_hit={_fmt(oos.get('sign_hit'))}  "
        f"resid_var={_fmt(oos.get('residual_var'))}"
    )
    print(
        f"  pos_recall={_fmt(oos.get('pos_recall'))}  "
        f"neg_recall={_fmt(oos.get('neg_recall'))}  "
        f"n_pos/n_neg={oos.get('n_pos')}/{oos.get('n_neg')}"
    )
    cs_p = oos.get("cs_pearson") or {}
    cs_s = oos.get("cs_spearman") or {}
    if isinstance(cs_p, dict) and cs_p.get("mean") is not None:
        print(
            f"  cs_pearson mean={_fmt(cs_p.get('mean'))}  "
            f"cs_spearman mean={_fmt((cs_s or {}).get('mean'))}"
        )
    by_th = oos.get("by_theme") or {}
    if by_th:
        for k in ("theme", "non_theme", "all"):
            b = by_th.get(k) or by_th.get(str(k))
            if isinstance(b, dict):
                print(
                    f"  theme[{k}] ic={_fmt(b.get('ic'))} n={b.get('n')}"
                )


def main() -> int:
    args = _parse_args()
    train_days = int(args.train_days)
    holdout_days = int(args.holdout_days)
    window_days = train_days + holdout_days
    lookback = window_days + 10  # pad 由 load_portfolio 再加 alpha 窗

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
    X, names, ys, dates, metas, n_stocks = build_dual_tau_matrix(
        stock_bars,
        min_history=int(args.min_history),
        gap_trigger_pct=float(args.gap_trigger),
    )
    print(
        f"dual panel: stocks={n_stocks} rows={len(ys)} cols={len(names)} "
        f"({time.time() - t1:.1f}s)"
    )
    n_aoo = sum(1 for n in names if is_alpha_oo_raw_key(str(n)))
    n_atc = sum(1 for n in names if is_alpha_tc_raw_key(str(n)))
    print(f"  columns raw_aoo_={n_aoo} raw_atc_={n_atc}")

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

    r_prod = fit_arm(
        X,
        names,
        ys,
        dates,
        metas,
        arm="prod",
        holdout_days=holdout_days,
        calendar=cal,
        ridge_lambda=float(args.ridge_lambda),
        theme_boost=float(args.theme_boost),
    )
    r_atc = fit_arm(
        X,
        names,
        ys,
        dates,
        metas,
        arm="atc",
        holdout_days=holdout_days,
        calendar=cal,
        ridge_lambda=float(args.ridge_lambda),
        theme_boost=float(args.theme_boost),
    )

    _print_arm(r_prod)
    _print_arm(r_atc)

    if r_prod.get("success") and r_atc.get("success"):
        ic0 = (r_prod.get("oos") or {}).get("ic")
        ic1 = (r_atc.get("oos") or {}).get("ic")
        sh0 = (r_prod.get("oos") or {}).get("sign_hit")
        sh1 = (r_atc.get("oos") or {}).get("sign_hit")
        print("\n=== Δ (atc − prod) ===")
        if ic0 is not None and ic1 is not None:
            print(f"  ΔIC = {_fmt(float(ic1) - float(ic0))}")
        if sh0 is not None and sh1 is not None:
            print(f"  Δsign_hit = {_fmt(float(sh1) - float(sh0))}")
        print(
            f"  Δactive = {int(r_atc.get('n_active') or 0) - int(r_prod.get('n_active') or 0)}"
        )

    print(f"\ndone in {time.time() - t0:.1f}s (offline, not saved)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
