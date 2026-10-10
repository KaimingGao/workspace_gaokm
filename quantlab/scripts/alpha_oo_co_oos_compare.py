"""ŷ_co：现网固定吃 alpha_oo 的 OOS 快照（旧 A/B 开关已下线）。

用法（quantlab/ 下）::

    .venv/bin/python scripts/alpha_oo_co_oos_compare.py
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


def main():
    stock_bars = load_stock_bars()
    print(f"loaded {len(stock_bars)} stocks (≥80 bars)")

    t0 = time.time()
    rep = fit_co_ridge_report(stock_bars, holdout_trading_days=20)
    elapsed = round(time.time() - t0, 1)
    oos = rep.get("oos") or {}
    rm = rep.get("return_model") or {}
    active = list(rm.get("active_features") or [])
    from core.signal.factors.alpha_oo import is_alpha_oo_raw_key

    n_aoo = sum(1 for a in active if is_alpha_oo_raw_key(str(a)))
    print(
        f"ok={rep.get('success')} n={rep.get('sample_count')} ic={oos.get('ic')} "
        f"sign={oos.get('sign_hit')} active={len(active)} "
        f"aoo_feats={rep.get('n_alpha_oo_features')} aoo_active={n_aoo} "
        f"({elapsed}s)"
        + (f" err={rep.get('error')}" if not rep.get("success") else "")
    )


if __name__ == "__main__":
    main()
