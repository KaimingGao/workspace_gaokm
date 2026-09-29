"""Alpha158 引入前后 OOS IC 对比（真实数据，全量观察池）。

优化：build 面板一次（含 alpha158），基线通过删除 raw_alpha158_* 字段构造。
对照臂：线性 RankNet + 同窗 Ridge ŷ_oo；并打印 Ridge active 中 alpha158 列数。
"""

import sys

sys.path.insert(0, ".")

import time

from core.research.factor_ols_fit import fit_factor_ols_from_panel
from core.research.oo_rank_pairwise import fit_oo_rank_report
from core.research.oo_rank_panel import (
    build_oo_rank_day_panels,
    enrich_day_panels_features,
    stack_day_panels,
)
from core.research.portfolio_bars import load_portfolio_stock_bars
from core.research_universe import resolve_research_codes


def load_stock_bars():
    codes = resolve_research_codes()["codes"]
    stock_bars_dict, _f, _s = load_portfolio_stock_bars(
        codes, lookback=150, max_names=len(codes)
    )
    return [
        {"code": c, "bars": b}
        for c, b in stock_bars_dict.items()
        if len(b) >= 70
    ]


def strip_alpha158_fields(days):
    """从面板删除 raw_alpha158_* 和 alpha158 sub_score 字段，构造基线面板。"""
    stripped = []
    for day in days:
        new_day = {
            "date": day["date"],
            "codes": list(day["codes"]),
            "xs": [],
            "ys": list(day["ys"]),
        }
        for row in day["xs"]:
            if not isinstance(row, dict):
                new_day["xs"].append(row)
                continue
            new_row = {
                k: v for k, v in row.items() if "alpha158" not in str(k).lower()
            }
            new_day["xs"].append(new_row)
        stripped.append(new_day)
    return stripped


def ridge_active_summary(days):
    """同窗堆叠 Ridge：确认 raw_alpha158_* 是否进 active。"""
    xs, ys, _dates, _codes = stack_day_panels(days)
    if len(ys) < 40:
        return {"success": False, "error": f"n={len(ys)}"}
    cut = max(20, int(len(ys) * 0.85))
    fit = fit_factor_ols_from_panel(
        xs[:cut],
        ys[:cut],
        ridge_lambda=1.0,
        standardize=True,
        collinearity_policy="drop_redundant",
    )
    active = list(fit.get("active_features") or [])
    n_a158 = sum(1 for a in active if "alpha158" in str(a).lower())
    return {
        "success": bool(fit.get("success")),
        "n_rows": cut,
        "n_active": len(active),
        "n_alpha158_active": n_a158,
        "error": fit.get("error"),
    }


def run(days, backend, epochs):
    t0 = time.time()
    rep = fit_oo_rank_report(
        [],  # stock_bars 不用（day_panels 已预计算）
        day_panels=days,
        holdout_trading_days=20,
        backend=backend,
        epochs=epochs,
    )
    elapsed = time.time() - t0
    fm = rep.get("feature_meta") or {}
    oos = rep.get("oos") or {}
    ridge = oos.get("ridge_oo_baseline") or {}
    rank = oos.get("oo_rank") or {}
    return {
        "success": rep.get("success"),
        "n_features": fm.get("n_features"),
        "n_train": rep.get("n_train_days"),
        "n_test": rep.get("n_test_days"),
        "rank_ic": rank.get("spearman"),
        "rank_topk": rank.get("topk_mean_y_oo"),
        "ridge_ic": ridge.get("spearman"),
        "ridge_topk": ridge.get("topk_mean_y_oo"),
        "ridge_pair": ridge.get("pair_accuracy"),
        "elapsed": round(elapsed, 1),
        "ridge_active": ridge_active_summary(days),
    }


def _fmt(v):
    return "—" if v is None else str(v)


def main():
    stock_bars = load_stock_bars()
    print(f"loaded {len(stock_bars)} stocks")

    t0 = time.time()
    days_full = build_oo_rank_day_panels(
        stock_bars,
        horizon_days=1,
        min_history=70,
        min_names=4,
        max_window=70,
        feature_mode="raw",
    )
    days_full = enrich_day_panels_features(days_full, feature_mode="raw")
    print(f"panel built: {len(days_full)} days, {time.time() - t0:.1f}s")

    days_base = strip_alpha158_fields(days_full)
    print(f"baseline panel: {len(days_base)} days")

    print("\n=== 基线（无 alpha158）===")
    r = run(days_base, "lambdarank", 0)
    ra = r["ridge_active"]
    print(
        f"  feats={r['n_features']} rank_ic={r['rank_ic']} ridge_ic={r['ridge_ic']} "
        f"ridge_topk={r['ridge_topk']} ridge_active={ra.get('n_active')} "
        f"a158_active={ra.get('n_alpha158_active')} ({r['elapsed']}s)"
    )

    print("\n=== 引入 alpha158 后 ===")
    r2 = run(days_full, "lambdarank", 0)
    ra2 = r2["ridge_active"]
    print(
        f"  feats={r2['n_features']} rank_ic={r2['rank_ic']} ridge_ic={r2['ridge_ic']} "
        f"ridge_topk={r2['ridge_topk']} ridge_active={ra2.get('n_active')} "
        f"a158_active={ra2.get('n_alpha158_active')} ({r2['elapsed']}s)"
    )

    print("\n=== 汇总（Ridge 为重点）===")
    hdr = f"{'场景':<10} {'feats':>6} {'ridge_ic':>10} {'ridge_topk':>12} {'active':>7} {'a158':>5}"
    print(hdr)
    print(
        f"{'基线':<10} {_fmt(r['n_features']):>6} {_fmt(r['ridge_ic']):>10} "
        f"{_fmt(r['ridge_topk']):>12} {_fmt(ra.get('n_active')):>7} "
        f"{_fmt(ra.get('n_alpha158_active')):>5}"
    )
    print(
        f"{'引入后':<10} {_fmt(r2['n_features']):>6} {_fmt(r2['ridge_ic']):>10} "
        f"{_fmt(r2['ridge_topk']):>12} {_fmt(ra2.get('n_active')):>7} "
        f"{_fmt(ra2.get('n_alpha158_active')):>5}"
    )


if __name__ == "__main__":
    main()
