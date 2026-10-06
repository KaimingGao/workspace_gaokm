#!/usr/bin/env python3
"""仅跑 README 覆盖校验（P44）。"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.readme_check import check_readme_coverage  # noqa: E402


def main() -> int:
    out = check_readme_coverage()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if out.get("ok"):
        print(f"OK: README {out['present_count']}/{out['total_dirs']}")
        return 0
    print(f"FAIL: {len(out.get('failures') or [])} issue(s)")
    for row in out.get("failures") or []:
        print(f"  - {row}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
