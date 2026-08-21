#!/usr/bin/env python3
"""量化日报 Markdown 导出 CLI（P14.2）。"""


import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant.services.quant_service import QuantService  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="导出 quant_daily Markdown")
    parser.add_argument("--format", choices=["markdown", "html"], default="markdown")
    parser.add_argument("--output", "-o", default="", help="输出路径，默认 stdout")
    parser.add_argument("--fresh", action="store_true", help="重新 build_daily_report 而非读缓存")
    args = parser.parse_args(argv)

    svc = QuantService()
    report = None
    if not args.fresh:
        saved = svc.load_last_daily()
        if not saved.get("empty"):
            report = saved
    if report is None:
        report = svc.build_daily_report(include_portfolio_backtest=True)

    out = svc.export_report(report, fmt=args.format, use_saved=False)
    if not out.get("success"):
        print(out.get("error") or "failed", file=sys.stderr)
        return 1

    content = out.get("content") or ""
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(content)
        print(args.output)
    else:
        print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
