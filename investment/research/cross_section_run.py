#!/usr/bin/env python3
"""横截面排序 CLI（P9.2）。"""


import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.signal.service import get_research_signal_service  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Investment 横截面排序")
    parser.add_argument("--codes", default="", help="逗号分隔候选（默认读 watching）")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--horizon", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    codes = None
    if args.codes.strip():
        codes = [c.strip() for c in args.codes.replace("，", ",").split(",") if c.strip()]

    # 研究 CLI：允许绕过日线质量门禁；启发式仍标 production_ok=False
    result = get_research_signal_service().rank_cross_section(
        codes,
        horizon_days=args.horizon,
        limit=args.limit,
        min_score=args.min_score,
    ).as_dict()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("success") else 1

    if not result.get("success"):
        print(result.get("error") or "failed")
        return 1

    print(f"ranked {result['ranked_count']} / candidates {result['candidate_count']}")
    for i, row in enumerate(result.get("ranking") or [], 1):
        print(
            f"{i:2d}. {row.get('stock_name')}({row.get('stock_code')}) "
            f"score={row.get('score')} src={row.get('data_source')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
