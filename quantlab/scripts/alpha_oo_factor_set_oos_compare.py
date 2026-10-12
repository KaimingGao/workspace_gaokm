"""ŷ_oo 因子集合 Holdout 对比：z_oo_* vs raw_aoo_* vs 并集 / 精简子集。

配置对齐现网研究套验证：观察池 ≤300、训练窗 160 交易日、Holdout 20。
建面板一次，按列子集分别拟合；**不落盘 / 不 promote**。

用法（在 quantlab/ 下）::

    .venv/bin/python scripts/alpha_oo_factor_set_oos_compare.py \\
        --max-names 300 --train-days 160 --holdout-days 20
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
from core.research.oo_ridge_compact import (
    assemble_oo_panels,
    fit_oo_ridge_matrix,
    predict_oo_matrix,
)
from core.research.panel_matrix import named_columns
from core.research.portfolio_bars import load_portfolio_stock_bars
from core.research_universe import resolve_research_codes
from core.signal.factors.alpha_oo import (
    ALPHA_OO_MIN_BARS,
    ALPHA_OO_RAW_PREFIX,
    alpha_oo_ridge_raw_names,
    apply_aoo_core_ridge_policy,
    is_alpha_oo_raw_key,
)
from core.signal.factors.z_oo import is_z_oo_key
from core.research.oo_ridge import _oo_ic, _oo_sign_hit


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="ŷ_oo factor-set Holdout: z_oo / raw_aoo / union / core"
    )
    p.add_argument("--max-names", type=int, default=300)
    p.add_argument("--train-days", type=int, default=160)
    p.add_argument("--holdout-days", type=int, default=20)
    p.add_argument("--ridge-lambda", type=float, default=1.0)
    p.add_argument("--horizon-days", type=int, default=1)
    p.add_argument(
        "--min-history",
        type=int,
        default=int(ALPHA_OO_MIN_BARS),
    )
    return p.parse_args()


def load_stock_bars(max_names: int, lookback: int) -> List[Tuple[str, list]]:
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
        (c, b)
        for c, b in stock_bars_dict.items()
        if len(b) >= need
    ]


def _filter_last_calendar_days(
    X: np.ndarray,
    y: np.ndarray,
    names: Sequence[str],
    dates: Sequence[str],
    calendar: Sequence[str],
    *,
    n_days: int,
) -> Tuple[np.ndarray, np.ndarray, List[str], List[str], List[str]]:
    cal = [str(d)[:10] for d in calendar if str(d)[:10]]
    if len(cal) > n_days:
        cal = cal[-n_days:]
    keep_set = set(cal)
    idx = [i for i, d in enumerate(dates) if str(d)[:10] in keep_set]
    if not idx:
        return (
            X[0:0],
            y[0:0],
            list(names),
            [],
            list(cal),
        )
    ii = np.asarray(idx, dtype=np.int64)
    return (
        np.ascontiguousarray(X[ii]),
        np.ascontiguousarray(y[ii]),
        list(names),
        [str(dates[i])[:10] for i in idx],
        list(cal),
    )


def _aoo_gap_yk_keys() -> List[str]:
    from core.signal.factors.alpha_oo import _GAP_KEYS, _YK_KEYS

    return [f"{ALPHA_OO_RAW_PREFIX}{k}" for k in (*_GAP_KEYS, *_YK_KEYS)]


def _select_feature_names(
    all_names: Sequence[str],
    *,
    mode: str,
) -> List[str]:
    z = [n for n in all_names if is_z_oo_key(str(n))]
    aoo = [n for n in all_names if is_alpha_oo_raw_key(str(n))]
    have = set(all_names)
    core = [n for n in alpha_oo_ridge_raw_names() if n in have]
    if mode == "z_oo":
        return list(z)
    if mode == "raw_aoo":
        return list(aoo)
    if mode == "union_full":
        return list(z) + [n for n in aoo if n not in set(z)]
    if mode == "aoo_core":
        return list(core)
    if mode == "aoo_gap_yk":
        return [n for n in _aoo_gap_yk_keys() if n in have]
    if mode == "prod":
        # 现网：z_oo ∪ 全量 raw_aoo
        return list(z) + [n for n in aoo if n not in set(z)]
    if mode == "aoo_core_prod":
        # 消融：去重叠 z_oo ∪ aoo_core
        return apply_aoo_core_ridge_policy(list(z) + list(core))
    raise ValueError(f"unknown mode: {mode}")


SCENARIOS: Tuple[Tuple[str, str], ...] = (
    ("z_oo", "仅 z_oo_*（注册日线）"),
    ("raw_aoo", "仅 raw_aoo_*（全量 ~206）"),
    ("union_full", "全量 z_oo ∪ raw_aoo"),
    ("aoo_core", "raw_aoo 精简：GAP+YK+LAG"),
    ("aoo_gap_yk", "raw_aoo 更简：GAP+YK"),
    ("prod", "现网 z_oo∪raw_aoo 全量"),
    ("aoo_core_prod", "消融 z_oo∪aoo_core（去重叠）"),
)


def fit_holdout(
    X: np.ndarray,
    y: np.ndarray,
    col_names: Sequence[str],
    dates: Sequence[str],
    *,
    feat_names: Sequence[str],
    label: str,
    holdout_days: int,
    calendar: Sequence[str],
    ridge_lambda: float,
    horizon_days: int,
) -> Dict[str, Any]:
    train_idx, test_idx, split_meta = resolve_ridge_split(
        dates,
        holdout_trading_days=holdout_days,
        label_horizon_days=int(horizon_days or 1),
        calendar_dates=calendar,
    )
    X_fit, kept = named_columns(X, col_names, feat_names)
    if not kept:
        return {
            "success": False,
            "label": label,
            "error": "no columns",
            "split": split_meta,
        }

    model, report = fit_oo_ridge_matrix(
        X_fit,
        y,
        kept,
        row_idx=train_idx,
        horizon_days=horizon_days,
        ridge_lambda=ridge_lambda,
        min_samples=24,
        row_dates=dates,
        cross_section_zscore=True,
    )
    if model is None or not report.get("success"):
        return {
            "success": False,
            "label": label,
            "error": (report or {}).get("error") or "fit failed",
            "n_features": len(kept),
            "n_train_days": split_meta.get("n_train_days"),
            "n_test_days": split_meta.get("n_test_days"),
            "split": split_meta,
            "report": report,
        }

    ys_te = [float(y[i]) for i in test_idx]
    preds: List[Optional[float]] = []
    if test_idx:
        preds = predict_oo_matrix(
            model,
            X_fit,
            kept,
            test_idx,
            row_dates=dates,
        )
    oos: Dict[str, Any] = {
        "n_train": len(train_idx),
        "n_test": len(test_idx),
        "ic": _oo_ic(preds, ys_te) if ys_te else None,
        "sign_hit": _oo_sign_hit(preds, ys_te) if ys_te else None,
        "n_valid": sum(1 for p in preds if p is not None),
    }
    try:
        from core.research.daily_cs_ic import attach_daily_cs_ic

        if ys_te:
            metas_te = [{"date": str(dates[i])[:10]} for i in test_idx]
            attach_daily_cs_ic(oos, preds, ys_te, metas_te)
    except Exception:  # noqa: BLE001
        pass

    active = list(report.get("active_features") or [])
    n_z = sum(1 for a in active if is_z_oo_key(str(a)))
    n_aoo = sum(1 for a in active if is_alpha_oo_raw_key(str(a)))
    prep = report.get("prep_meta") or {}
    return {
        "success": True,
        "label": label,
        "n_features": len(kept),
        "n_active": len(active),
        "n_z_active": n_z,
        "n_aoo_active": n_aoo,
        "n_train_days": split_meta.get("n_train_days"),
        "n_test_days": split_meta.get("n_test_days"),
        "fit_end": split_meta.get("fit_end"),
        "eval_start": split_meta.get("eval_start"),
        "r_squared": report.get("r_squared"),
        "complete_n": prep.get("complete_sample_count"),
        "dropped_coverage": len(prep.get("dropped_for_coverage") or []),
        "dropped_sparse": len(prep.get("dropped_sparse") or []),
        "oos": oos,
    }


def _fmt(v: Any, nd: int = 4) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if v != v:
            return "—"
        return f"{v:.{nd}f}"
    return str(v)


def _print_report(r: Dict[str, Any]) -> None:
    oos = r.get("oos") or {}
    print(f"\n=== {r.get('label')} ===")
    if not r.get("success"):
        print(f"  FAIL: {r.get('error')}")
        return
    print(
        f"  days train/test={r.get('n_train_days')}/{r.get('n_test_days')}  "
        f"fit_end={r.get('fit_end')}  eval_start={r.get('eval_start')}"
    )
    print(
        f"  feats={r.get('n_features')} active={r.get('n_active')} "
        f"(z={r.get('n_z_active')} aoo={r.get('n_aoo_active')})  "
        f"complete_n={r.get('complete_n')}  "
        f"drop_cov/sparse={r.get('dropped_coverage')}/{r.get('dropped_sparse')}"
    )
    print(
        f"  samples train/test={oos.get('n_train')}/{oos.get('n_test')}  "
        f"valid={oos.get('n_valid')}  R²={_fmt(r.get('r_squared'))}"
    )
    print(
        f"  IC={_fmt(oos.get('ic'))}  hit={_fmt(oos.get('sign_hit'))}  "
        f"cs_ic={_fmt(oos.get('cs_ic'))}  "
        f"cs_rank_ic={_fmt(oos.get('cs_rank_ic'))}  "
        f"cs_rank_icir={_fmt(oos.get('cs_rank_icir'))}"
    )
    print(
        f"  top10%_y={_fmt(oos.get('cs_top_mean_y'))}  "
        f"LS_y={_fmt(oos.get('cs_ls_mean_y'))}  "
        f"top>0日={_fmt(oos.get('cs_top_gt0_ratio'))}"
    )


def _print_summary(rows: Sequence[Dict[str, Any]]) -> None:
    print("\n=== 汇总（Holdout OOS）===")
    hdr = (
        f"{'场景':<28} {'feats':>5} {'act':>4} {'R²':>7} "
        f"{'IC':>8} {'hit':>7} {'cs_ic':>8} {'rk_ic':>8} {'rk_ir':>7} "
        f"{'top10y':>8} {'LS_y':>8}"
    )
    print(hdr)
    for r in rows:
        oos = r.get("oos") or {}
        name = str(r.get("label") or "")[:28]
        if not r.get("success"):
            print(f"{name:<28} FAIL: {r.get('error')}")
            continue
        print(
            f"{name:<28} {r.get('n_features'):>5} {r.get('n_active'):>4} "
            f"{_fmt(r.get('r_squared'), 3):>7} "
            f"{_fmt(oos.get('ic')):>8} {_fmt(oos.get('sign_hit')):>7} "
            f"{_fmt(oos.get('cs_ic')):>8} {_fmt(oos.get('cs_rank_ic')):>8} "
            f"{_fmt(oos.get('cs_rank_icir'), 3):>7} "
            f"{_fmt(oos.get('cs_top_mean_y')):>8} "
            f"{_fmt(oos.get('cs_ls_mean_y')):>8}"
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
        f"window={train_days}+{holdout_days}, λ={args.ridge_lambda}, "
        f"h={args.horizon_days} (no persist)"
    )
    items = load_stock_bars(args.max_names, lookback)
    print(f"loaded {len(items)} stocks ({time.time() - t0:.1f}s)")
    if len(items) < 20:
        print("ERROR: too few stocks with enough bars")
        return 1

    stock_bars = [{"code": c, "bars": b} for c, b in items]
    cal_all = calendar_dates_from_stock_bars(stock_bars)

    t1 = time.time()
    print("\nbuilding OO panel (full raw_aoo for ablation; fit filters per arm) …")
    X, y, names, dates = assemble_oo_panels(
        items,
        horizon_days=int(args.horizon_days),
        ridge_core_only=False,
    )
    n_z = sum(1 for n in names if is_z_oo_key(str(n)))
    n_aoo = sum(1 for n in names if is_alpha_oo_raw_key(str(n)))
    print(
        f"  rows={len(y)} cols={len(names)} "
        f"(z={n_z} aoo={n_aoo}) ({time.time() - t1:.1f}s)"
    )

    X, y, names, dates, cal = _filter_last_calendar_days(
        X, y, names, dates, cal_all, n_days=window_days
    )
    print(
        f"  calendar window: {len(cal)} days "
        f"[{cal[0] if cal else '—'} … {cal[-1] if cal else '—'}]  "
        f"rows={len(y)}"
    )
    if len(cal) < holdout_days + 20:
        print("ERROR: calendar too short for requested split")
        return 1

    results: List[Dict[str, Any]] = []
    for mode, label in SCENARIOS:
        feats = _select_feature_names(names, mode=mode)
        print(f"\nfitting {mode}: {len(feats)} columns …")
        r = fit_holdout(
            X,
            y,
            names,
            dates,
            feat_names=feats,
            label=label,
            holdout_days=holdout_days,
            calendar=cal,
            ridge_lambda=float(args.ridge_lambda),
            horizon_days=int(args.horizon_days),
        )
        results.append(r)
        _print_report(r)

    _print_summary(results)
    print(f"\ndone in {time.time() - t0:.1f}s (no persist)")
    return 0 if any(r.get("success") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
