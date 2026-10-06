#!/usr/bin/env python3
"""信号/回测可复现性回归（fixture 双跑，不依赖 LLM 与外网）。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.repro import run_repro_case  # noqa: E402

FIXTURES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repro_fixtures.json")


def load_fixtures() -> List[dict]:
    with open(FIXTURES_PATH, "r", encoding="utf-8") as f:
        return list(json.load(f).get("cases") or [])


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="QuantLab repro evals")
    parser.add_argument("--case", help="只跑指定 case id")
    parser.add_argument("--json-out", help="写入 JSON 报告")
    args = parser.parse_args(argv)

    cases = load_fixtures()
    if args.case:
        cases = [c for c in cases if c.get("id") == args.case]
        if not cases:
            print(f"未找到 case: {args.case}", file=sys.stderr)
            return 2

    failures = []
    report = {"cases": [], "failures": []}
    print(f"Loaded {len(cases)} repro case(s)")

    for case in cases:
        cid = case.get("id")
        result = run_repro_case(case)
        ok = bool(result.get("ok"))
        flag = "OK" if ok else "FAIL"
        fp = result.get("fingerprint", "-")
        extra = ""
        if result.get("trade_count") is not None:
            extra = f" trades={result.get('trade_count')}"
        elif result.get("success") is not None:
            extra = f" success={result.get('success')}"
        print(f"[{cid}] {flag}  fp={fp}{extra}")
        if not ok:
            err = result.get("error") or result.get("fingerprint")
            failures.append(f"{cid}: {err}")
        report["cases"].append(result)

    report["failures"] = failures
    print()
    if failures:
        print(f"DONE with {len(failures)} failure(s)")
        code = 1
    else:
        print("DONE: 全部可复现")
        code = 0

    if args.json_out:
        out = args.json_out
        if not os.path.isabs(out):
            out = os.path.join(ROOT, out)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"Wrote {out}")

    return code


if __name__ == "__main__":
    raise SystemExit(main())
