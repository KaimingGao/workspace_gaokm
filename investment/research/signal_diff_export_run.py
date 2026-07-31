#!/usr/bin/env python3
"""导出 signal_config diff 合并包 CLI（P21.2）。"""

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
    parser = argparse.ArgumentParser(description="导出 signal_config diff 合并包")
    parser.add_argument("--code", default="茅台", help="单票分析默认标的")
    parser.add_argument("--fresh", action="store_true", help="忽略 quant_daily，即时计算")
    parser.add_argument("--output", "-o", default="", help="输出路径，默认 stdout")
    args = parser.parse_args(argv)

    svc = QuantService()
    out = svc.export_config_diff_bundle(code=args.code, use_saved=not args.fresh)
    if not out.get("success"):
        print(out.get("error") or "failed", file=sys.stderr)
        return 1

    content = json.dumps(out, ensure_ascii=False, indent=2)
    if args.output:
        path = args.output
        if not os.path.isabs(path):
            path = os.path.join(ROOT, path)
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print(path)
    else:
        print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
