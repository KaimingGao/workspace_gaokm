#!/usr/bin/env python3
"""跑 R5.6 核心黄金路径（北极星 · PIT · 硬拦）。"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="R5.6 core golden paths")
    parser.add_argument("--json-out", default="", help="可选写出 JSON 报告")
    args = parser.parse_args(argv)

    from evals.core_golden_paths import run_all_core_paths

    out = run_all_core_paths()
    print("=" * 64)
    print(f"Core paths: {out['count']} · ok={out['ok']}")
    for p in out.get("paths") or []:
        mark = "OK" if p.get("ok") else "FAIL"
        print(f"  [{mark}] {p.get('id')}")
    if out.get("failures"):
        print("Failures:")
        for f in out["failures"]:
            print(f"  - {f}")
    if args.json_out:
        path = args.json_out
        if not os.path.isabs(path):
            path = os.path.join(ROOT, path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"Wrote {path}")
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
