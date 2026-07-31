#!/usr/bin/env python3
"""Daily preset 离线校验 CLI（P21/P22，供 CI 单独调用）。"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.preset_check import check_daily_presets  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="校验 daily preset 标志位")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    out = check_daily_presets()
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif not out.get("ok"):
        for item in out.get("failures") or []:
            print(item, file=sys.stderr)
    else:
        print(f"OK: {', '.join(out.get('checked') or [])}")
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
