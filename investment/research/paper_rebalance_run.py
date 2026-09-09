#!/usr/bin/env python3
"""观察池 rank_lots 纸面调仓 CLI。"""


import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.paper_service import PaperService  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="观察池 rank_lots 纸面调仓（非实盘）")
    parser.add_argument("--top-k", type=int, default=None, help="省略则用观察池容量")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = PaperService().rebalance(top_k=args.top_k)
    except FileNotFoundError as e:
        print(str(e))
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("success") else 1

    if not result.get("success"):
        print(result.get("error") or "failed")
        return 1

    print(
        f"sell={len(result.get('sell_trades') or [])} "
        f"buy={len(result.get('buy_trades') or [])} "
        f"equity={(result.get('summary') or {}).get('equity')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
