#!/usr/bin/env python3
"""Agent 全量 golden 回归 CLI（P24.3，需 DASHSCOPE_API_KEY，不进 PR CI）。"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evals.run_checklist import main as checklist_main  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Agent golden 回归（mock + with-agent，可选单 case）"
    )
    parser.add_argument("--case", help="只跑指定 golden case id")
    parser.add_argument("--json-out", help="写入完整 JSON 报告")
    parser.add_argument(
        "--skip-presets",
        action="store_true",
        help="跳过 daily preset 校验（默认与 mock checklist 一致含 presets）",
    )
    parser.add_argument(
        "--quant-only",
        action="store_true",
        help="只跑 id 以 quant_ 开头的 golden case",
    )
    args = parser.parse_args(argv)

    if not os.environ.get("DASHSCOPE_API_KEY"):
        print(
            "DASHSCOPE_API_KEY 未设置；Agent 回归需 LLM。可在 .env 或环境中配置。",
            file=sys.stderr,
        )
        return 2

    argv_out = ["--mock", "--with-agent"]
    if args.case:
        argv_out.extend(["--case", args.case])
    if args.quant_only:
        argv_out.append("--quant-only")
    if args.json_out:
        argv_out.extend(["--json-out", args.json_out])
    if not args.skip_presets:
        argv_out.append("--presets")

    return checklist_main(argv_out)


if __name__ == "__main__":
    raise SystemExit(main())
