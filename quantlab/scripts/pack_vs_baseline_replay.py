"""离线：最优特征包 vs 现网全量 → 调仓 paper_replay 对比（不入库）。

ŷ_oo：aoo_core_prod vs prod（z∪全量 raw_aoo）
ŷ_τc(open)：z_only vs full（z∪全量 raw_atc）
融合默认 w_oo=0.6 / w_τc=0.4；fill_clock=09:30；模型在 train 窗冻结后只评 Holdout 日。

用法（quantlab/）::

    .venv/bin/python scripts/pack_vs_baseline_replay.py \\
        --max-names 300 --lookback 1000 --train-days 940 --holdout-days 60
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

sys.path.insert(0, ".")

from core.backtest.paper_replay import backtest_paper_replay
from core.research.holdout import (
    calendar_dates_from_stock_bars,
    resolve_ridge_split,
)
from core.research.oo_ridge_compact import (
    _oo_stock_job,
    _stack_blocks,
    fit_oo_ridge_matrix,
    predict_oo_matrix,
)
from core.research.panel_matrix import (
    collect_tau_compact,
    fit_keepall_ridge_matrix,
    named_columns,
    predict_ridge_matrix,
)
from core.research.portfolio_bars import load_portfolio_stock_bars
from core.research.tau_panel import theme_sample_weights
from core.research.tc_ridge import TAU_FIT_DROP_ALIASES, TAU_Z_FEATURES
from core.research_universe import resolve_research_codes
from core.signal.factors.alpha_oo import (
    ALPHA_OO_MIN_BARS,
    alpha_oo_ridge_raw_names,
    apply_aoo_core_ridge_policy,
    is_alpha_oo_raw_key,
)
from core.signal.factors.alpha_tc import (
    ALPHA_TC_MIN_BARS,
    is_alpha_tc_raw_key,
)
from core.signal.factors.z_oo import is_z_oo_key
from core.signal.factors.z_tc import Z_TC_MINUTE_KEYS
from core.signal.minute_tau_feats import MINUTE_TAU_ALL_KEYS
from core.signal.yhat_windows import fuse_pct, oc_with_co


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="pack vs baseline rebalance replay (no persist)"
    )
    p.add_argument("--max-names", type=int, default=300)
    p.add_argument("--train-days", type=int, default=940)
    p.add_argument("--holdout-days", type=int, default=60)
    p.add_argument("--lookback", type=int, default=1000)
    p.add_argument("--ridge-lambda", type=float, default=1.0)
    p.add_argument("--fusion-w-oo", type=float, default=0.6)
    p.add_argument("--fusion-w-oc", type=float, default=0.4)
    p.add_argument("--fusion-w-co", type=float, default=1.0)
    p.add_argument("--initial-cash", type=float, default=200_000.0)
    p.add_argument("--theme-boost", type=float, default=1.5)
    return p.parse_args()


def _load_items(max_names: int, lookback: int) -> List[Tuple[str, list]]:
    codes = resolve_research_codes()["codes"]
    stock_bars_dict, _f, _s = load_portfolio_stock_bars(
        codes,
        lookback=int(lookback),
        max_names=int(max_names),
        offline_ok=True,
        fetch_fundamentals=False,
    )
    need = max(int(ALPHA_OO_MIN_BARS), int(ALPHA_TC_MIN_BARS)) + 2
    return [(c, b) for c, b in stock_bars_dict.items() if len(b) >= need]


def _filter_calendar(
    dates: Sequence[str],
    calendar: Sequence[str],
    *,
    n_days: int,
) -> Tuple[List[int], List[str]]:
    cal = [str(d)[:10] for d in calendar if str(d)[:10]]
    if len(cal) > n_days:
        cal = cal[-n_days:]
    keep = set(cal)
    idx = [i for i, d in enumerate(dates) if str(d)[:10] in keep]
    return idx, cal


def _assemble_oo_with_codes(
    items: Sequence[Tuple[str, list]],
) -> Tuple[np.ndarray, np.ndarray, List[str], List[str], List[str]]:
    parts: List[Any] = []
    codes_flat: List[str] = []
    for code, bars in items:
        part = _oo_stock_job((str(code), list(bars or []), 1, False))
        if part is None:
            continue
        x, y, _n, dates = part
        if x is None or len(dates) == 0:
            continue
        parts.append(part)
        codes_flat.extend([str(code)] * len(dates))
    if not parts:
        return (
            np.zeros((0, 0), dtype=np.float64),
            np.zeros((0,), dtype=np.float64),
            [],
            [],
            [],
        )
    x, y, names, dates = _stack_blocks(parts, ridge_core_only=False)
    if len(codes_flat) != len(dates):
        raise RuntimeError(
            f"oo code/date mismatch {len(codes_flat)} vs {len(dates)}"
        )
    return x, y, names, [str(d)[:10] for d in dates], codes_flat


def _oo_feat_names(all_names: Sequence[str], *, mode: str) -> List[str]:
    z = [n for n in all_names if is_z_oo_key(str(n))]
    aoo = [n for n in all_names if is_alpha_oo_raw_key(str(n))]
    have = set(all_names)
    core = [n for n in alpha_oo_ridge_raw_names() if n in have]
    if mode == "prod":
        return list(z) + [n for n in aoo if n not in set(z)]
    if mode == "aoo_core_prod":
        return apply_aoo_core_ridge_policy(list(z) + list(core))
    raise ValueError(mode)


def _tc_feat_names(
    z_base: Sequence[str],
    atc_base: Sequence[str],
    *,
    mode: str,
) -> List[str]:
    z = list(z_base)
    atc = list(atc_base)
    if mode == "full":
        return z + atc
    if mode == "z_only":
        return z
    raise ValueError(mode)


def _fit_predict_oo(
    X: np.ndarray,
    y: np.ndarray,
    names: Sequence[str],
    dates: Sequence[str],
    codes: Sequence[str],
    *,
    feat_names: Sequence[str],
    train_idx: Sequence[int],
    test_idx: Sequence[int],
    ridge_lambda: float,
) -> Dict[Tuple[str, str], float]:
    X_fit, kept = named_columns(X, names, feat_names)
    model, report = fit_oo_ridge_matrix(
        X_fit,
        y,
        kept,
        row_idx=train_idx,
        horizon_days=1,
        ridge_lambda=ridge_lambda,
        min_samples=24,
        row_dates=dates,
        cross_section_zscore=True,
    )
    if model is None or not report.get("success"):
        raise RuntimeError(f"oo fit failed: {(report or {}).get('error')}")
    preds = predict_oo_matrix(
        model, X_fit, kept, test_idx, row_dates=dates
    )
    out: Dict[Tuple[str, str], float] = {}
    for i, p in zip(test_idx, preds):
        if p is None:
            continue
        out[(str(dates[i])[:10], str(codes[i]))] = float(p)
    return out


def _fit_predict_tc(
    X: np.ndarray,
    col_names: Sequence[str],
    ys: Sequence[float],
    dates: Sequence[str],
    metas: Sequence[dict],
    *,
    feat_names: Sequence[str],
    train_idx: Sequence[int],
    test_idx: Sequence[int],
    ridge_lambda: float,
    theme_boost: float,
) -> Dict[Tuple[str, str], float]:
    X_fit, kept = named_columns(X, col_names, list(feat_names))
    dates_tr = [dates[i] for i in train_idx]
    ys_tr = [ys[i] for i in train_idx]
    metas_tr = [metas[i] for i in train_idx]
    theme_w = theme_sample_weights(metas_tr, theme_boost=theme_boost)
    fit = fit_keepall_ridge_matrix(
        X_fit[np.asarray(train_idx, dtype=np.int64)],
        np.asarray(ys_tr, dtype=np.float64),
        kept,
        ridge_lambda=ridge_lambda,
        sample_weights=theme_w,
        row_dates=dates_tr,
        cross_section_zscore=True,
    )
    if not fit.get("success"):
        raise RuntimeError(f"tc fit failed: {fit.get('error')}")
    dates_te = [dates[i] for i in test_idx]
    preds = predict_ridge_matrix(
        fit,
        X_fit[np.asarray(test_idx, dtype=np.int64)],
        kept,
        row_dates=dates_te,
    )
    out: Dict[Tuple[str, str], float] = {}
    for i, p in zip(test_idx, preds):
        if p is None:
            continue
        try:
            pv = float(p)
        except (TypeError, ValueError):
            continue
        if pv != pv:
            continue
        m = metas[i] if i < len(metas) else {}
        code = str((m or {}).get("stock_code") or (m or {}).get("code") or "")
        if not code:
            continue
        out[(str(dates[i])[:10], code)] = pv
    return out


def _scores_by_date(
    oo: Dict[Tuple[str, str], float],
    tc: Dict[Tuple[str, str], float],
    *,
    w_oo: float,
    w_τc: float,
    w_co: float,
) -> Dict[str, List[dict]]:
    keys = set(oo) | set(tc)
    by_day: Dict[str, List[dict]] = defaultdict(list)
    for day, code in sorted(keys):
        y_oo = oo.get((day, code))
        y_τc = tc.get((day, code))
        right = oc_with_co(y_τc, None, float(w_co))
        fused = fuse_pct(y_oo, right, w_left=w_oo, w_right=w_τc)
        row: Dict[str, Any] = {"stock_code": code}
        if y_oo is not None:
            row["y_oo"] = float(y_oo)
            row["predicted_score"] = float(y_oo)
            row["predicted_score_oo"] = float(y_oo)
        if y_τc is not None:
            row["y_τc"] = float(y_τc)
            row["predicted_score_τc"] = float(y_τc)
        if fused is not None:
            row["y_oo_fuse"] = float(fused)
            row["predicted_score"] = float(fused)
        by_day[day].append(row)
    return dict(by_day)


def _metric_snip(out: Dict[str, Any]) -> Dict[str, Any]:
    m = out.get("metrics") or {}
    params = out.get("params") or {}
    return {
        "success": bool(out.get("success")),
        "error": out.get("error"),
        "n_days": params.get("n_days") or m.get("n_days"),
        "total_return_pct": m.get("total_return_pct"),
        "annual_return_pct": m.get("annual_return_pct"),
        "max_drawdown_pct": m.get("max_drawdown_pct"),
        "sharpe": m.get("sharpe"),
        "calmar": m.get("calmar"),
        "n_trades": m.get("n_trades") or len(out.get("trades") or []),
        "win_rate": m.get("win_rate"),
    }


def _print_cmp(baseline: Dict[str, Any], optimal: Dict[str, Any]) -> None:
    keys = [
        "total_return_pct",
        "annual_return_pct",
        "max_drawdown_pct",
        "sharpe",
        "calmar",
        "n_trades",
        "win_rate",
        "n_days",
    ]
    print("\n=== 调仓回测对比（Holdout 日 · 注入 ŷ · 不入库）===")
    print(f"{'metric':<22} {'baseline':>12} {'optimal':>12} {'Δ':>10}")
    for k in keys:
        a, b = baseline.get(k), optimal.get(k)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            d = float(b) - float(a)
            print(f"{k:<22} {float(a):>12.4f} {float(b):>12.4f} {d:>+10.4f}")
        else:
            print(f"{k:<22} {str(a):>12} {str(b):>12}")


def main() -> int:
    args = _parse_args()
    train_days = max(20, int(args.train_days))
    holdout_days = max(5, int(args.holdout_days))
    window_days = train_days + holdout_days
    lookback = max(
        int(args.lookback or 0),
        window_days + 40,
        int(ALPHA_OO_MIN_BARS) + window_days,
    )
    w_oo = float(args.fusion_w_oo)
    w_τc = float(args.fusion_w_oc)
    w_co = float(args.fusion_w_co)

    t0 = time.time()
    print(
        f"load ≤{args.max_names} lookback={lookback} "
        f"window={train_days}+{holdout_days} "
        f"fuse={w_oo}/{w_τc} w_co={w_co} (no persist)"
    )
    items = _load_items(args.max_names, lookback)
    print(f"loaded {len(items)} stocks ({time.time() - t0:.1f}s)")
    if len(items) < 20:
        print("ERROR: too few stocks")
        return 1

    stock_bars = {c: b for c, b in items}
    stock_bars_list = [{"code": c, "bars": b} for c, b in items]
    cal_all = calendar_dates_from_stock_bars(stock_bars_list)

    # --- OO panel ---
    t1 = time.time()
    print("\nbuilding OO panel …")
    Xo, yo, names_o, dates_o, codes_o = _assemble_oo_with_codes(items)
    print(f"  oo rows={len(yo)} cols={len(names_o)} ({time.time() - t1:.1f}s)")
    idx_o, cal = _filter_calendar(dates_o, cal_all, n_days=window_days)
    Xo = np.ascontiguousarray(Xo[np.asarray(idx_o, dtype=np.int64)])
    yo = np.ascontiguousarray(yo[np.asarray(idx_o, dtype=np.int64)])
    dates_o = [dates_o[i] for i in idx_o]
    codes_o = [codes_o[i] for i in idx_o]
    tr_o, te_o, split_o = resolve_ridge_split(
        dates_o, holdout_trading_days=holdout_days, calendar_dates=cal
    )
    print(
        f"  oo split train/test days="
        f"{split_o.get('n_train_days')}/{split_o.get('n_test_days')} "
        f"eval_start={split_o.get('eval_start')}"
    )

    # --- TC panel (open) ---
    t2 = time.time()
    print("\nbuilding TC open panel …")
    z_seed = [
        k
        for k in TAU_Z_FEATURES
        if k not in TAU_FIT_DROP_ALIASES
        and k not in Z_TC_MINUTE_KEYS
        and k not in MINUTE_TAU_ALL_KEYS
    ]
    Xt, names_t, ys_t, dates_t, metas_t, n_st = collect_tau_compact(
        stock_bars_list,
        z_seed,
        min_history=int(ALPHA_TC_MIN_BARS),
        gap_trigger_pct=2.0,
        tau_hm="open",
        include_alpha_tc=True,
        head="y_tc_pack_replay",
    )
    print(
        f"  tc stocks={n_st} rows={len(ys_t)} cols={len(names_t)} "
        f"({time.time() - t2:.1f}s)"
    )
    idx_t, _ = _filter_calendar(dates_t, cal_all, n_days=window_days)
    ii = np.asarray(idx_t, dtype=np.int64)
    Xt = np.ascontiguousarray(Xt[ii])
    ys_t = [ys_t[i] for i in idx_t]
    dates_t = [str(dates_t[i])[:10] for i in idx_t]
    metas_t = [metas_t[i] for i in idx_t]
    tr_t, te_t, split_t = resolve_ridge_split(
        dates_t, holdout_trading_days=holdout_days, calendar_dates=cal
    )
    z_base = [
        k
        for k in TAU_Z_FEATURES
        if k not in TAU_FIT_DROP_ALIASES
        and k not in Z_TC_MINUTE_KEYS
        and k not in MINUTE_TAU_ALL_KEYS
        and k in set(names_t)
    ]
    # finite atc on train
    view = Xt[np.asarray(tr_t, dtype=np.int64)] if tr_t else Xt
    atc_base = [
        n
        for j, n in enumerate(names_t)
        if is_alpha_tc_raw_key(str(n)) and np.isfinite(view[:, j]).any()
    ]
    print(
        f"  tc split train/test days="
        f"{split_t.get('n_train_days')}/{split_t.get('n_test_days')} "
        f"z={len(z_base)} atc={len(atc_base)}"
    )

    print("\nfitting baseline packs …")
    oo_base = _fit_predict_oo(
        Xo,
        yo,
        names_o,
        dates_o,
        codes_o,
        feat_names=_oo_feat_names(names_o, mode="prod"),
        train_idx=tr_o,
        test_idx=te_o,
        ridge_lambda=float(args.ridge_lambda),
    )
    tc_base = _fit_predict_tc(
        Xt,
        names_t,
        ys_t,
        dates_t,
        metas_t,
        feat_names=_tc_feat_names(z_base, atc_base, mode="full"),
        train_idx=tr_t,
        test_idx=te_t,
        ridge_lambda=float(args.ridge_lambda),
        theme_boost=float(args.theme_boost),
    )
    print(f"  baseline preds oo={len(oo_base)} tc={len(tc_base)}")

    print("fitting optimal packs …")
    oo_opt = _fit_predict_oo(
        Xo,
        yo,
        names_o,
        dates_o,
        codes_o,
        feat_names=_oo_feat_names(names_o, mode="aoo_core_prod"),
        train_idx=tr_o,
        test_idx=te_o,
        ridge_lambda=float(args.ridge_lambda),
    )
    tc_opt = _fit_predict_tc(
        Xt,
        names_t,
        ys_t,
        dates_t,
        metas_t,
        feat_names=_tc_feat_names(z_base, atc_base, mode="z_only"),
        train_idx=tr_t,
        test_idx=te_t,
        ridge_lambda=float(args.ridge_lambda),
        theme_boost=float(args.theme_boost),
    )
    print(f"  optimal preds oo={len(oo_opt)} tc={len(tc_opt)}")

    scores_base = _scores_by_date(
        oo_base, tc_base, w_oo=w_oo, w_τc=w_τc, w_co=w_co
    )
    scores_opt = _scores_by_date(
        oo_opt, tc_opt, w_oo=w_oo, w_τc=w_τc, w_co=w_co
    )
    eval_days = sorted(set(scores_base) | set(scores_opt))
    print(f"\nreplay days={len(eval_days)} "
          f"[{eval_days[0] if eval_days else '—'} … "
          f"{eval_days[-1] if eval_days else '—'}]")

    # 回测窗 = Holdout；lookback 对齐评测日数
    replay_lb = max(holdout_days, len(eval_days))
    common_kw = dict(
        lookback=replay_lb,
        initial_cash=float(args.initial_cash),
        cost_model="simple_cn",
        fill_clock="09:30",
        fusion_w_oo=w_oo,
        fusion_w_oc=w_τc,
        fusion_w_co=w_co,
        fuse_enter=0.001,
        fuse_strong=0.001,
        include_session_day=False,
        yhat_horizon_days=1,
        min_history=12,
        max_window=30,
    )

    print("\nrunning baseline replay …")
    out_b = backtest_paper_replay(
        stock_bars, scores_by_date=scores_base, **common_kw
    )
    print("running optimal replay …")
    out_o = backtest_paper_replay(
        stock_bars, scores_by_date=scores_opt, **common_kw
    )

    mb = _metric_snip(out_b)
    mo = _metric_snip(out_o)
    print("\nbaseline:", mb)
    print("optimal: ", mo)
    if mb.get("success") and mo.get("success"):
        _print_cmp(mb, mo)
    else:
        print("ERROR: replay failed", mb.get("error"), mo.get("error"))
        return 1

    print(
        f"\npacks: baseline=oo_prod+τc_full  "
        f"optimal=oo_aoo_core_prod+τc_z_only"
    )
    print(f"done in {time.time() - t0:.1f}s (no persist)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
