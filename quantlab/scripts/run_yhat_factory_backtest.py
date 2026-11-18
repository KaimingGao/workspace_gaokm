"""模型中心离线回测进程。只读工厂权重，不写研究套 / 执行套 / live。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="工厂四头离线回测")
    parser.add_argument("--factory", required=True, choices=("L", "T", "R"))
    parser.add_argument("--versions", required=True, help="四头版本号 JSON")
    parser.add_argument("--rules", default="", help="本页 rank_lots JSON；缺省读工厂目录")
    args = parser.parse_args(argv)
    from core.research.yhat_factory_backtest import finish_factory_backtest, run_factory_backtest

    try:
        versions = json.loads(args.versions)
        if not isinstance(versions, dict):
            raise ValueError("versions 须为对象")
        rules = json.loads(args.rules) if args.rules else None
        if rules is not None and not isinstance(rules, dict):
            raise ValueError("rules 须为对象")
        report = run_factory_backtest(args.factory, versions, rules=rules)
        print(
            f"离线回测完成 窗口 {report.get('window_start')}→{report.get('window_end')} "
            f"收益 {float(report.get('cumulative_return') or 0):.4%}",
            flush=True,
        )
        finish_factory_backtest(args.factory, report=report)
        return 0
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        try:
            finish_factory_backtest(args.factory, error=str(exc))
        except Exception:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
