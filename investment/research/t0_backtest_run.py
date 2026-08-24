#!/usr/bin/env python3
"""底仓做 T 回测 CLI（仅 5m 第一触达；非实盘）。"""


import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.research.t0_backtest import run_t0_backtest_for_code  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Investment 底仓做T回测（5m 第一触达，非实盘）")
    parser.add_argument("--code", default="茅台")
    parser.add_argument("--lookback", type=int, default=10)
    parser.add_argument("--shares", type=float, default=1000)
    parser.add_argument("--t0-ratio", type=float, default=0.4)
    parser.add_argument("--sell-pct", type=float, default=2.0)
    parser.add_argument("--buy-pct", type=float, default=1.5)
    parser.add_argument("--must-cover", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    rules = {
        "t0_ratio": args.t0_ratio,
        "sell_trigger_pct": args.sell_pct,
        "buy_trigger_pct": args.buy_pct,
        "must_cover_same_day": bool(args.must_cover),
    }
    report = run_t0_backtest_for_code(
        args.code,
        lookback=args.lookback,
        initial_shares=args.shares,
        rules=rules,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("success") else 1

    if not report.get("success"):
        print(report.get("error") or "失败")
        return 1

    print(
        f"{report.get('stock_code')} src={report.get('data_source')} "
        f"days={report.get('bar_count')} t0_days={report.get('t0_trade_days')}"
    )
    print(
        f"  pnl={report.get('t0_pnl_total')} with_exp={report.get('t0_pnl_with_exposure')} "
        f"cover={report.get('t0_cover_days')} uncover={report.get('uncover_days')}"
    )
    print(report.get("note") or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
