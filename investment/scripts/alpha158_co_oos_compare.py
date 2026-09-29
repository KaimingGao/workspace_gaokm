"""ŷ_co：引入 Alpha158 前后 OOS IC 对比。

用法（investment/ 下）::

    .venv/bin/python scripts/alpha158_co_oos_compare.py
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, ".")

from core.research.co_ridge import fit_co_ridge_report
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
        if len(b) >= 80
    ]


def _fmt(v):
    return "—" if v is None else str(v)


def run(stock_bars, *, include_alpha158: bool):
    t0 = time.time()
    rep = fit_co_ridge_report(
        stock_bars,
        holdout_trading_days=20,
        include_alpha158=include_alpha158,
    )
    elapsed = round(time.time() - t0, 1)
    oos = rep.get("oos") or {}
    rm = rep.get("return_model") or {}
    active = list(rm.get("active_features") or [])
    n_a158 = sum(1 for a in active if "alpha158" in str(a).lower())
    return {
        "success": rep.get("success"),
        "error": rep.get("error"),
        "n_samples": rep.get("sample_count"),
        "n_stocks": rep.get("stock_count"),
        "n_alpha158_features": rep.get("n_alpha158_features"),
        "n_active": len(active),
        "n_alpha158_active": n_a158,
        "ic": oos.get("ic"),
        "sign_hit": oos.get("sign_hit"),
        "n_train": oos.get("n_train"),
        "n_test": oos.get("n_test"),
        "elapsed": elapsed,
    }


def main():
    stock_bars = load_stock_bars()
    print(f"loaded {len(stock_bars)} stocks (≥80 bars)")

    print("\n=== 基线（无 alpha158）===")
    base = run(stock_bars, include_alpha158=False)
    print(
        f"  ok={base['success']} n={base['n_samples']} ic={base['ic']} "
        f"sign={base['sign_hit']} active={base['n_active']} "
        f"a158_active={base['n_alpha158_active']} ({base['elapsed']}s)"
        + (f" err={base['error']}" if not base["success"] else "")
    )

    print("\n=== 引入 alpha158 后 ====")
    full = run(stock_bars, include_alpha158=True)
    print(
        f"  ok={full['success']} n={full['n_samples']} ic={full['ic']} "
        f"sign={full['sign_hit']} active={full['n_active']} "
        f"a158_feats={full['n_alpha158_features']} "
        f"a158_active={full['n_alpha158_active']} ({full['elapsed']}s)"
        + (f" err={full['error']}" if not full["success"] else "")
    )

    print("\n=== 汇总 ===")
    hdr = f"{'场景':<12} {'n':>6} {'ic':>10} {'sign':>8} {'active':>7} {'a158':>5}"
    print(hdr)
    print(
        f"{'基线':<12} {_fmt(base['n_samples']):>6} {_fmt(base['ic']):>10} "
        f"{_fmt(base['sign_hit']):>8} {_fmt(base['n_active']):>7} "
        f"{_fmt(base['n_alpha158_active']):>5}"
    )
    print(
        f"{'引入 alpha158':<12} {_fmt(full['n_samples']):>6} {_fmt(full['ic']):>10} "
        f"{_fmt(full['sign_hit']):>8} {_fmt(full['n_active']):>7} "
        f"{_fmt(full['n_alpha158_active']):>5}"
    )


if __name__ == "__main__":
    main()
