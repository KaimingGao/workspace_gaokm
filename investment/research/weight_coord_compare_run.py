#!/usr/bin/env python3
"""权重坐标搜索对照 CLI（研究用：global / OLS·IC / 坐标网格；不写 signal_config）。"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.services.quant_service import QuantService  # noqa: E402


def _fmt_arm(arm: dict) -> str:
    label = arm.get("label") or "?"
    if not arm.get("success"):
        err = arm.get("error") or ("skipped" if arm.get("skipped") else "fail")
        return f"  {label:20s}  —  {err}"
    return (
        f"  {label:20s}  total={arm.get('total_return_pct')}%  "
        f"IS={arm.get('is_return_pct')}%  OOS={arm.get('oos_return_pct')}%  "
        f"dd={arm.get('max_drawdown_pct')}%"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="权重三臂对照：全局 / OLS·IC 建议 / 坐标网格（研究只读）"
    )
    parser.add_argument("--codes", default="", help="逗号分隔；默认 watching")
    parser.add_argument("--lookback", type=int, default=90)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--horizon", type=int, default=3)
    parser.add_argument("--min-score", type=float, default=55.0)
    parser.add_argument("--watching-limit", type=int, default=10)
    parser.add_argument("--no-ols", action="store_true", help="跳过 OLS·IC 建议臂")
    parser.add_argument("--suggest-code", default="茅台")
    parser.add_argument("--n-sweeps", type=int, default=2)
    parser.add_argument("--max-evals", type=int, default=48)
    parser.add_argument(
        "--start-from",
        default="current",
        choices=("current", "equal", "suggested"),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    codes = None
    if args.codes.strip():
        codes = [
            c.strip()
            for c in args.codes.replace("，", ",").split(",")
            if c.strip()
        ]

    report = QuantService().compare_weight_coordinate_search(
        codes=codes,
        lookback=args.lookback,
        top_k=args.top_k,
        horizon_days=args.horizon,
        min_score=args.min_score,
        watching_limit=args.watching_limit,
        include_ols_arm=not args.no_ols,
        suggest_code=args.suggest_code,
        n_sweeps=args.n_sweeps,
        max_evals=args.max_evals,
        start_from=args.start_from,
    )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("success") else 1

    if not report.get("success"):
        print(report.get("error") or "对照失败")
        return 1

    print(
        f"stocks={report.get('stock_count')} lookback={report.get('lookback')} "
        f"top_k={report.get('top_k')} start_from={report.get('start_from')}"
    )
    for arm in report.get("arms") or []:
        print(_fmt_arm(arm))
    print(f"best_by_oos={report.get('best_by_oos_label')}  promote_ready=false")
    search = report.get("search") or {}
    if search.get("evals") is not None:
        print(
            f"coord_evals={search.get('evals')}/{search.get('max_evals')} "
            f"objective={search.get('search_objective')}"
        )
    print(report.get("note") or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
