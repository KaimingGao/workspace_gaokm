#!/usr/bin/env python3
"""组合横截面回测 CLI（P10.2）。"""


import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.backtest.topk_backtest import backtest_topk_equal_weight  # noqa: E402
from core.data_service import bars_and_source as fetch_daily_bars  # noqa: E402
from core.data_service import get_quote  # noqa: E402
from core.watching_store import read_watching  # noqa: E402


def _load_bars_for_codes(codes, lookback: int) -> dict:
    stock_bars = {}
    failures = []
    for raw in codes:
        quote = get_quote(str(raw))
        sym = quote.get("stock_code") if quote.get("success") else str(raw)
        bars, _ = fetch_daily_bars(raw, limit=lookback + 35)
        if not bars and quote.get("success"):
            bars, _ = fetch_daily_bars(sym, limit=lookback + 35)
        if bars:
            stock_bars[sym] = bars
        else:
            failures.append(str(raw))
    return stock_bars, failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="横截面 TopK 组合回测")
    parser.add_argument("--codes", default="", help="逗号分隔；默认 watching watchlist")
    parser.add_argument("--lookback", type=int, default=120)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--horizon", type=int, default=3)
    parser.add_argument("--min-score", type=float, default=55)
    parser.add_argument("--apply-costs", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    codes = []
    if args.codes.strip():
        codes = [c.strip() for c in args.codes.replace("，", ",").split(",") if c.strip()]
    else:
        try:
            uni = read_watching()
            codes = uni.get("watchlist") or []
        except FileNotFoundError:
            print("watching.json 不存在，请 --codes 或先 watching_run --init")
            return 1

    if not codes:
        print("无候选标的")
        return 1

    stock_bars, failures = _load_bars_for_codes(codes, args.lookback)
    if len(stock_bars) < 2:
        print(f"有效日线不足（{len(stock_bars)}），failures={failures}")
        return 1

    result = backtest_topk_equal_weight(
        stock_bars,
        top_k=args.top_k,
        horizon_days=args.horizon,
        min_score=args.min_score,
        apply_costs=bool(args.apply_costs),
    )
    result["loaded_stocks"] = list(stock_bars.keys())
    result["failures"] = failures

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("success") else 1

    if not result.get("success"):
        print(result.get("error") or "failed")
        return 1

    m = result.get("metrics") or {}
    print(
        f"stocks={len(stock_bars)} common_dates={result['params']['common_dates']} "
        f"trades={m.get('trade_count')} total={m.get('total_return_pct')}% "
        f"win={m.get('win_rate_pct')}% sharpe={m.get('sharpe_approx')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
