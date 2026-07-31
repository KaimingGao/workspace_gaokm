#!/usr/bin/env python3
"""stance 阈值 OOS 校准建议 CLI（P13.3）。"""

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
    parser = argparse.ArgumentParser(description="stance 阈值 OOS 校准建议")
    parser.add_argument("--code", default="茅台")
    parser.add_argument("--lookback", type=int, default=120)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = QuantService().suggest_thresholds(args.code, lookback=args.lookback)
    if args.json:
        slim = dict(result)
        slim.pop("oos_scan", None)
        print(json.dumps(slim, ensure_ascii=False, indent=2))
        return 0 if result.get("success") else 1

    if not result.get("success"):
        print(result.get("error") or "failed")
        return 1

    cur = result.get("current_thresholds") or {}
    sug = result.get("suggested_thresholds") or {}
    print(f"wait {cur.get('wait')} → {sug.get('wait')}  probe {cur.get('probe')} → {sug.get('probe')}")
    for line in result.get("rationale") or []:
        print(f"  · {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
