"""Alpha158 引入前后 OOS IC 对比（真实数据）。

30 票观察池样本，185 根日线，对比 LightGBM LambdaRank / Ridge
在基线（无 alpha158）和引入 alpha158 后的 OOS spearman IC。
"""

import sys
sys.path.insert(0, '.')

import time
import numpy as np

from core.research_universe import resolve_research_codes
from core.research.portfolio_bars import load_portfolio_stock_bars
from core.research.oo_rank_pairwise import fit_oo_rank_report
from core.research.oo_rank_panel import build_oo_rank_day_panels, enrich_day_panels_features
from core.signal.factors.meta.registry import _REGISTRY


def load_stock_bars():
    # 日线研究宇宙（可宽于观察池）；空则回退观察池
    codes = resolve_research_codes()["codes"]
    stock_bars_dict, _failures, _sym = load_portfolio_stock_bars(codes, lookback=150, max_names=len(codes))
    return [
        {"code": code, "bars": bars}
        for code, bars in stock_bars_dict.items()
        if len(bars) >= 70
    ]


def run_scenario(stock_bars, label, backend, alpha158_registered):
    """跑一次 fit_oo_rank_report，返回 OOS IC 摘要。"""
    t0 = time.time()
    # 预计算面板（max_window=70 保证 alpha158 有 61 根）
    days = build_oo_rank_day_panels(
        stock_bars,
        horizon_days=1,
        min_history=70,
        min_names=4,
        max_window=70,
        feature_mode="raw",
    )
    days = enrich_day_panels_features(days, feature_mode="raw")
    rep = fit_oo_rank_report(
        stock_bars,
        day_panels=days,
        holdout_trading_days=20,
        backend=backend,
    )
    elapsed = time.time() - t0
    fm = rep.get("feature_meta") or {}
    oos = rep.get("oos") or {}
    rank_ic = (oos.get("oo_rank") or {}).get("spearman")
    ridge_ic = (oos.get("ridge_oo_baseline") or {}).get("spearman")
    rank_n_days = (oos.get("oo_rank") or {}).get("n_days")
    return {
        "label": label,
        "backend": backend,
        "alpha158": "on" if alpha158_registered else "off",
        "success": rep.get("success"),
        "n_features": fm.get("n_features"),
        "n_raw": fm.get("n_raw_features"),
        "n_days": rep.get("n_days"),
        "n_train": rep.get("n_train_days"),
        "n_test": rep.get("n_test_days"),
        "rank_ic": rank_ic,
        "ridge_ic": ridge_ic,
        "elapsed": round(elapsed, 1),
    }


def main():
    stock_bars = load_stock_bars()
    print(f"loaded {len(stock_bars)} stocks")
    if not stock_bars:
        print("ERROR: no stock bars")
        return

    results = []
    backends = ["lambdarank"]

    for backend in backends:
        # 基线：临时移除 alpha158
        entry = _REGISTRY.pop("alpha158", None)
        try:
            r = run_scenario(stock_bars, "基线", backend, alpha158_registered=False)
            results.append(r)
            print(f"[{backend}] 基线  features={r['n_features']:>3}  rank_ic={r['rank_ic']}  ridge_ic={r['ridge_ic']}  success={r['success']}  ({r['elapsed']}s)")
        finally:
            if entry:
                _REGISTRY["alpha158"] = entry

        # 引入后
        r = run_scenario(stock_bars, "引入后", backend, alpha158_registered=True)
        results.append(r)
        print(f"[{backend}] 引入后 features={r['n_features']:>3}  rank_ic={r['rank_ic']}  ridge_ic={r['ridge_ic']}  success={r['success']}  ({r['elapsed']}s)")

    # 汇总
    print("\n=== 汇总 ===")
    print(f"{'backend':<20} {'场景':<8} {'features':>8} {'rank_ic':>10} {'ridge_ic':>10}")
    for r in results:
        print(f"{r['backend']:<20} {r['alpha158']:<8} {r['n_features']:>8} {str(r['rank_ic']):>10} {str(r['ridge_ic']):>10}")


if __name__ == "__main__":
    main()
