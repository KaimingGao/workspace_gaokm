#!/usr/bin/env python3
"""排序键对照 CLI（已退役；仅返回提示，不写 signal_config）。"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.services.quant_service import QuantService  # noqa: E402


def _fmt(arm: dict) -> str:
    label = arm.get("label") or "?"
    if not arm.get("success"):
        return f"  {label:22s}  —  {arm.get('error') or 'fail'}"
    return (
        f"  {label:22s}  total={arm.get('total_return_pct')}%  "
        f"IS={arm.get('is_return_pct')}%  OOS={arm.get('oos_return_pct')}%  "
        f"trades={arm.get('trade_count')}"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="排序键对照（已退役；系统仅认 predicted_score）")
    parser.add_argument("--codes", default="")
    parser.add_argument("--lookback", type=int, default=120)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--horizon", type=int, default=3)
    parser.add_argument("--min-score", type=float, default=55.0)
    parser.add_argument("--watching-limit", type=int, default=12)
    parser.add_argument("--min-samples", type=int, default=24)
    parser.add_argument("--ridge-lambda", type=float, default=0.0)
    parser.add_argument("--no-costs", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    codes = None
    if args.codes.strip():
        codes = [
            c.strip()
            for c in args.codes.replace("，", ",").split(",")
            if c.strip()
        ]

    report = QuantService().compare_rank_modes(
        codes=codes,
        lookback=args.lookback,
        top_k=args.top_k,
        horizon_days=args.horizon,
        min_score=args.min_score,
        watching_limit=args.watching_limit,
        return_model_min_samples=args.min_samples,
        return_model_ridge_lambda=args.ridge_lambda,
        apply_costs=not args.no_costs,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("success") else 1

    if not report.get("success"):
        print(report.get("error") or "对照失败")
        return 1

    print(f"stocks={len(report.get('codes') or [])} lookback={report.get('lookback')}")
    for arm in report.get("arms") or []:
        print(_fmt(arm))
    print(f"best_by_oos={report.get('best_by_oos_label')}  promote_ready=false")
    print(report.get("note") or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
