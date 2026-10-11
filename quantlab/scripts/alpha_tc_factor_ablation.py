"""ŷ_τc 因子集消融：同面板 / 同 Holdout，对比 z_τc_* 与 raw_atc_* 子集。

默认：观察池 300、训练 160 日、Holdout 20 日；τ=open；不落盘。

用法（在 quantlab/ 下）::

    .venv/bin/python scripts/alpha_tc_factor_ablation.py \\
        --max-names 300 --train-days 160 --holdout-days 20
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

sys.path.insert(0, ".")

from core.research.holdout import (
    calendar_dates_from_stock_bars,
    resolve_ridge_split,
)
from core.research.panel_matrix import (
    collect_tau_compact,
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
from core.signal.factors.alpha_tc import (
    ALPHA_TC_MIN_BARS,
    ALPHA_TC_RAW_PREFIX,
    is_alpha_tc_raw_key,
)
from core.signal.minute_tau_feats import MINUTE_TAU_ALL_KEYS

# raw_atc 与 z_τc 语义重叠（开盘 τ）——用于 dedup 臂
_ATC_Z_OVERLAP = frozenset(
    {
        f"{ALPHA_TC_RAW_PREFIX}GAP",
        f"{ALPHA_TC_RAW_PREFIX}GAP_ABS",
        f"{ALPHA_TC_RAW_PREFIX}GAP_ATR",
        f"{ALPHA_TC_RAW_PREFIX}YCLOSE_LOC",
        f"{ALPHA_TC_RAW_PREFIX}TC_LAG1",
        f"{ALPHA_TC_RAW_PREFIX}TC_MA5",
        f"{ALPHA_TC_RAW_PREFIX}OPEN0",  # 开盘恒 0
        f"{ALPHA_TC_RAW_PREFIX}RET_OT",  # τ=open 恒 0
        f"{ALPHA_TC_RAW_PREFIX}RET_PT",  # ≈ gap
    }
)

_ROLL_VOL_RE = re.compile(
    rf"^{re.escape(ALPHA_TC_RAW_PREFIX)}P("
    r"ROC|MA|STD|BETA|RSQR|RESI|MAX|LOW|QTLU|QTLD|RANK|RSV|"
    r"IMAX|IMIN|IMXD|CORR|CORD|CNTP|CNTN|CNTD|SUMP|SUMN|SUMD|"
    r"VMA|VSTD|WVMA|VSUMP|VSUMN|VSUMD|VROC|VRAMA"
    r")(\d+)$"
)

_LONG_WIN = frozenset({"20", "30", "60"})


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="ŷ_τc factor ablation: z_τc_* vs raw_atc_* subsets"
    )
    p.add_argument("--max-names", type=int, default=300)
    p.add_argument("--train-days", type=int, default=160)
    p.add_argument("--holdout-days", type=int, default=20)
    p.add_argument("--ridge-lambda", type=float, default=1.0)
    p.add_argument("--gap-trigger", type=float, default=2.0)
    p.add_argument("--theme-boost", type=float, default=1.5)
    p.add_argument("--min-history", type=int, default=int(ALPHA_TC_MIN_BARS))
    p.add_argument(
        "--arms",
        type=str,
        default="",
        help="comma arms; default=all",
    )
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
        return X[0:0], list(col_names), [], [], [], list(cal)
    ii = np.asarray(idx, dtype=np.int64)
    return (
        np.ascontiguousarray(X[ii]),
        list(col_names),
        [ys[i] for i in idx],
        [str(dates[i])[:10] for i in idx],
        [metas[i] for i in idx],
        list(cal),
    )


def _z_open_names(col_names: Sequence[str]) -> List[str]:
    from core.signal.factors.z_tc import Z_TC_MINUTE_KEYS

    have = set(col_names)
    return [
        k
        for k in TAU_Z_FEATURES
        if k not in TAU_FIT_DROP_ALIASES
        and k not in Z_TC_MINUTE_KEYS
        and k not in MINUTE_TAU_ALL_KEYS
        and k in have
    ]


def _finite_names(
    X: np.ndarray,
    names: Sequence[str],
    row_idx: Sequence[int],
    *,
    pred: Callable[[str], bool],
) -> List[str]:
    if X.size == 0 or not row_idx:
        return []
    view = X[np.asarray(list(row_idx), dtype=np.int64)]
    out: List[str] = []
    for j, name in enumerate(names):
        k = str(name)
        if not pred(k):
            continue
        if np.isfinite(view[:, j]).any():
            out.append(k)
    return out


def _is_roll_vol(name: str) -> bool:
    return bool(_ROLL_VOL_RE.match(str(name)))


def _is_long_window(name: str) -> bool:
    m = _ROLL_VOL_RE.match(str(name))
    return bool(m and m.group(2) in _LONG_WIN)


def _is_vol_op(name: str) -> bool:
    m = _ROLL_VOL_RE.match(str(name))
    if not m:
        return False
    return m.group(1).startswith("V") or m.group(1) in {
        "WVMA",
        "VSUMP",
        "VSUMN",
        "VSUMD",
        "VROC",
        "VRAMA",
        "VMA",
        "VSTD",
    }


def _is_gap_yk_lag(name: str) -> bool:
    """非滚动窗：gap / 昨 K / lag（含 MOM/REV/SHOCK）；不含 PREFIX。"""
    k = str(name)
    if not is_alpha_tc_raw_key(k):
        return False
    if _is_roll_vol(k):
        return False
    stem = k[len(ALPHA_TC_RAW_PREFIX) :]
    if stem.startswith(
        (
            "RANGE_",
            "LOC_",
            "UP_",
            "DOWN_",
            "PATH_",
            "PULL",
            "BOUNCE",
            "RET_LAST",
            "REALIZED",
            "VOL_LAST",
            "TAU_ELAPSED",
            "PREFIX_",
            "VP_",
        )
    ):
        return False
    return True


ArmSpec = Tuple[str, str, Callable[[List[str], List[str]], List[str]]]


def _arms() -> List[ArmSpec]:
    def full(z: List[str], atc: List[str]) -> List[str]:
        return list(z) + list(atc)

    def z_only(z: List[str], atc: List[str]) -> List[str]:
        return list(z)

    def atc_only(z: List[str], atc: List[str]) -> List[str]:
        return list(atc)

    def z_atc_core(z: List[str], atc: List[str]) -> List[str]:
        return list(z) + [k for k in atc if _is_gap_yk_lag(k)]

    def z_atc_no_roll(z: List[str], atc: List[str]) -> List[str]:
        return list(z) + [k for k in atc if not _is_roll_vol(k)]

    def z_atc_short_win(z: List[str], atc: List[str]) -> List[str]:
        return list(z) + [k for k in atc if not _is_long_window(k)]

    def z_atc_no_vol(z: List[str], atc: List[str]) -> List[str]:
        return list(z) + [k for k in atc if not _is_vol_op(k)]

    def z_atc_dedup(z: List[str], atc: List[str]) -> List[str]:
        return list(z) + [k for k in atc if k not in _ATC_Z_OVERLAP]

    return [
        ("full", "现网 z∪raw_atc 全量", full),
        ("z_only", "仅 z_τc_*", z_only),
        ("atc_only", "仅 raw_atc_*", atc_only),
        ("z+core", "z + gap/yk/lag（无滚动窗）", z_atc_core),
        ("z+no_roll", "z + atc 去全部 P* 滚动窗", z_atc_no_roll),
        ("z+short", "z + atc 去掉 20/30/60 窗", z_atc_short_win),
        ("z+no_vol", "z + atc 去掉量能滚动", z_atc_no_vol),
        ("z+dedup", "z + atc 去掉与 Z 重叠列", z_atc_dedup),
    ]


def fit_arm(
    X: np.ndarray,
    col_names: Sequence[str],
    ys: Sequence[float],
    dates: Sequence[str],
    metas: Sequence[dict],
    *,
    arm: str,
    label: str,
    feat_names: Sequence[str],
    train_idx: Sequence[int],
    test_idx: Sequence[int],
    split_meta: Dict[str, Any],
    ridge_lambda: float,
    theme_boost: float,
) -> Dict[str, Any]:
    min_std_exempt = list(TAU_MIN_STD_EXEMPT)
    for n in feat_names:
        if is_alpha_tc_raw_key(str(n)) and str(n) not in min_std_exempt:
            min_std_exempt.append(str(n))

    X_fit, feat_names_u = named_columns(X, col_names, list(feat_names))
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
        feat_names_u,
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
            "label": label,
            "error": fit.get("error") or "fit failed",
        }

    row_te = np.asarray(test_idx, dtype=np.int64)
    preds = (
        predict_ridge_matrix(
            fit,
            X_fit[row_te],
            feat_names_u,
            row_dates=dates_te,
        )
        if len(test_idx)
        else []
    )
    buckets = _oos_sign_buckets(preds, ys_te) if ys_te else {}
    active = list(fit.get("active_features") or [])
    coefs = fit.get("coefficients") or {}
    n_z = sum(1 for a in active if str(a).startswith("z_τc_"))
    n_raw = sum(1 for a in active if is_alpha_tc_raw_key(str(a)))
    top = sorted(
        (
            (str(k), float(v))
            for k, v in coefs.items()
            if str(k) in active and np.isfinite(float(v))
        ),
        key=lambda kv: abs(kv[1]),
        reverse=True,
    )[:12]
    oos: Dict[str, Any] = {
        "n_train": len(ys_tr),
        "n_test": len(ys_te),
        "ic": _ic(preds, ys_te) if ys_te else None,
        "sign_hit": _sign_hit(preds, ys_te) if ys_te else None,
        "residual_var": _residual_var(preds, ys_te) if ys_te else None,
        "by_theme": _oos_by_theme(preds, ys_te, metas_te) if ys_te else {},
        "pos_recall": buckets.get("pos_recall"),
        "neg_recall": buckets.get("neg_recall"),
    }
    try:
        from core.research.daily_cs_ic import attach_daily_cs_ic

        if ys_te:
            attach_daily_cs_ic(oos, preds, ys_te, metas_te)
    except Exception:  # noqa: BLE001
        pass

    return {
        "success": True,
        "arm": arm,
        "label": label,
        "n_features": len(feat_names_u),
        "n_active": len(active),
        "n_z_active": n_z,
        "n_raw_active": n_raw,
        "n_train_days": split_meta.get("n_train_days"),
        "n_test_days": split_meta.get("n_test_days"),
        "fit_end": split_meta.get("fit_end"),
        "eval_start": split_meta.get("eval_start"),
        "oos": oos,
        "top_abs_beta": top,
    }


def _fmt(v: Any, nd: int = 4) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if v != v:
            return "—"
        return f"{v:.{nd}f}"
    return str(v)


def _print_table(rows: List[Dict[str, Any]]) -> None:
    hdr = (
        f"{'arm':<12} {'feat':>5} {'act':>4} {'z':>3} {'atc':>4} "
        f"{'IC':>8} {'hit':>7} {'cs_ic':>8} {'cs_ric':>8} {'cs_ir':>8} "
        f"{'resid':>8}"
    )
    print("\n" + hdr)
    print("-" * len(hdr))
    for r in rows:
        oos = r.get("oos") or {}
        if not r.get("success"):
            print(f"{r.get('arm'):<12} FAIL {r.get('error')}")
            continue
        print(
            f"{str(r.get('arm')):<12} "
            f"{int(r.get('n_features') or 0):>5} "
            f"{int(r.get('n_active') or 0):>4} "
            f"{int(r.get('n_z_active') or 0):>3} "
            f"{int(r.get('n_raw_active') or 0):>4} "
            f"{_fmt(oos.get('ic')):>8} "
            f"{_fmt(oos.get('sign_hit')):>7} "
            f"{_fmt(oos.get('cs_ic')):>8} "
            f"{_fmt(oos.get('cs_rank_ic')):>8} "
            f"{_fmt(oos.get('cs_rank_icir')):>8} "
            f"{_fmt(oos.get('residual_var')):>8}"
        )


def main() -> int:
    args = _parse_args()
    train_days = max(20, int(args.train_days))
    holdout_days = max(5, int(args.holdout_days))
    window_days = train_days + holdout_days
    lookback = max(window_days + 40, int(args.min_history) + window_days)

    want: Optional[set] = None
    if str(args.arms or "").strip():
        want = {a.strip() for a in str(args.arms).split(",") if a.strip()}

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

    from core.signal.factors.z_tc import Z_TC_MINUTE_KEYS

    z_names = [
        k
        for k in TAU_Z_FEATURES
        if k not in TAU_FIT_DROP_ALIASES
        and k not in Z_TC_MINUTE_KEYS
        and k not in MINUTE_TAU_ALL_KEYS
    ]
    cal_all = calendar_dates_from_stock_bars(stock_bars)

    t1 = time.time()
    print("\nbuilding panel once (include raw_atc_*) …")
    X, names, ys, dates, metas, n_stocks = collect_tau_compact(
        stock_bars,
        z_names,
        min_history=int(args.min_history),
        gap_trigger_pct=float(args.gap_trigger),
        include_alpha_tc=True,
        head="y_tc_ablate",
    )
    n_raw = sum(1 for n in names if is_alpha_tc_raw_key(str(n)))
    print(
        f"  stocks={n_stocks} rows={len(ys)} cols={len(names)} "
        f"raw_cols={n_raw} ({time.time() - t1:.1f}s)"
    )
    X, names, ys, dates, metas, cal = _filter_last_calendar_days(
        X, names, ys, dates, metas, cal_all, n_days=window_days
    )
    print(
        f"  calendar window: {len(cal)} days "
        f"[{cal[0] if cal else '—'} … {cal[-1] if cal else '—'}]  "
        f"rows={len(ys)}"
    )
    if len(cal) < holdout_days + 20:
        print("ERROR: calendar too short for requested split")
        return 1

    train_idx, test_idx, split_meta = resolve_ridge_split(
        dates,
        holdout_trading_days=holdout_days,
        calendar_dates=cal,
    )
    z_base = _z_open_names(names)
    atc_base = _finite_names(
        X, names, train_idx, pred=is_alpha_tc_raw_key
    )
    print(
        f"  split train/test days="
        f"{split_meta.get('n_train_days')}/{split_meta.get('n_test_days')}  "
        f"fit_end={split_meta.get('fit_end')}  "
        f"eval_start={split_meta.get('eval_start')}"
    )
    print(f"  finite train cols: z={len(z_base)} atc={len(atc_base)}")

    results: List[Dict[str, Any]] = []
    for arm, label, picker in _arms():
        if want is not None and arm not in want:
            continue
        feats = picker(z_base, atc_base)
        print(f"\nfitting arm={arm} n_feat={len(feats)} …")
        r = fit_arm(
            X,
            names,
            ys,
            dates,
            metas,
            arm=arm,
            label=label,
            feat_names=feats,
            train_idx=train_idx,
            test_idx=test_idx,
            split_meta=split_meta,
            ridge_lambda=float(args.ridge_lambda),
            theme_boost=float(args.theme_boost),
        )
        results.append(r)
        if r.get("success") and arm == "full":
            print("  top |β| (full):")
            for k, v in r.get("top_abs_beta") or []:
                print(f"    {k:>28}  {v:+.5f}")

    _print_table(results)
    print("\narm labels:")
    for r in results:
        print(f"  {r.get('arm')}: {r.get('label')}")
    print(f"\ndone in {time.time() - t0:.1f}s (no persist)")
    return 0 if all(r.get("success") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
