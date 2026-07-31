#!/usr/bin/env python3
"""因子面板 OLS 实验 CLI（P86，研究用，不写 signal_config）。"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.services.quant_service import QuantService  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Investment 因子面板 OLS 实验（研究用）")
    parser.add_argument("--code", default="茅台")
    parser.add_argument("--lookback", type=int, default=120)
    parser.add_argument("--horizon", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = QuantService().run_factor_ols_experiment(
        args.code,
        lookback=args.lookback,
        horizon_days=args.horizon,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("success") else 1

    if not report.get("success"):
        print(report.get("error") or "OLS 失败")
        return 1

    sym = report.get("stock_code")
    print(f"{sym} src={report.get('data_source')} horizon={report.get('horizon_days')}")
    print(f"  n={report.get('sample_count')} R²={report.get('r_squared')}")
    print(f"  intercept={report.get('intercept')}")
    for name, coef in (report.get("coefficients") or {}).items():
        cur = (report.get("current_weights") or {}).get(name)
        print(f"  {name:18s} ols={coef} config_w={cur}")
    print(report.get("note") or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
