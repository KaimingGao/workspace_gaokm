#!/usr/bin/env python3
"""因子实验 CLI（P9.4）。"""


import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.data.facade import bars_and_source as fetch_daily_bars  # noqa: E402
from core.data.facade import get_quote, index_bars_and_source  # noqa: E402
from core.ports.market import (  # noqa: E402
    default_benchmark,
    resolve_market_code,
)
from core.signal.factor_registry import list_factors, run_factor_experiment  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Investment 因子 IC 实验")
    parser.add_argument("--code", default="茅台")
    parser.add_argument("--lookback", type=int, default=120)
    parser.add_argument("--horizon", type=int, default=3)
    parser.add_argument("--list-factors", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.list_factors:
        rows = list_factors()
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for r in rows:
                print(f"- {r['name']}: {r['label']}")
        return 0

    quote = get_quote(args.code)
    sym = quote.get("stock_code") if quote.get("success") else args.code
    bars, src = fetch_daily_bars(args.code, limit=args.lookback + 35)
    if not bars and quote.get("success"):
        bars, src = fetch_daily_bars(sym, limit=args.lookback + 35)
    if not bars:
        err = {"success": False, "error": f"无法获取 {args.code} 日线"}
        print(json.dumps(err, ensure_ascii=False) if args.json else err["error"])
        return 1

    market, _ = resolve_market_code(args.code)
    index_bars, _ = index_bars_and_source(default_benchmark(market), limit=args.lookback + 35)
    report = run_factor_experiment(
        bars,
        horizon_days=args.horizon,
        index_bars=index_bars or None,
    )
    report["stock_code"] = sym
    report["data_source"] = src

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"{sym} src={src} horizon={args.horizon}")
    for row in report.get("factors") or []:
        print(f"  {row['factor']:18s} IC={row.get('ic')} n={row.get('sample_count')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
