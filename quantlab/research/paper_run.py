#!/usr/bin/env python3
"""纸面账户 CLI（薄封装，逻辑在 core.paper）。"""


import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.paper import (  # noqa: E402
    DEFAULT_PAPER_PATH,
    init_from_example,
    load_paper,
    mark_to_market,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="QuantLab 纸面账户")
    parser.add_argument("--path", default=DEFAULT_PAPER_PATH, help="paper.json 路径")
    parser.add_argument("--init", action="store_true", help="从 paper.example.json 初始化")
    parser.add_argument("--run", action="store_true", help="已下线：持仓规则日循环")
    parser.add_argument(
        "--simulate-buy",
        action="store_true",
        help="对观察池高分标的纸面模拟买入",
    )
    parser.add_argument("--status", action="store_true", help="仅查看净值与持仓")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    if args.init:
        try:
            path = init_from_example(args.path)
            print(f"已创建 {path}")
            return 0
        except FileExistsError as e:
            print(e)
            return 1

    try:
        paper = load_paper(args.path)
    except FileNotFoundError as e:
        print(e)
        return 1

    if args.run:
        print("持仓规则日循环已下线。开盘调仓在交易执行。")
        return 2

    if args.status or not any([args.run, args.init]):
        summary = mark_to_market(paper)
        if args.json:
            out = {"paper": paper.get("name"), "summary": summary}
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(f"账户: {paper.get('name')}  持仓={len(paper.get('holdings') or [])}")
            print(
                f"现金 {summary.get('cash')}  市值 {summary.get('stock_value')}  "
                f"净值 {summary.get('equity')}  盈亏 {summary.get('total_pnl_pct')}%"
            )
            for h in summary.get("holdings") or []:
                print(
                    f"  - {h.get('stock_name')}({h.get('stock_code')}): "
                    f"{h.get('shares')}股 @ {h.get('cost')} → {h.get('price')} "
                    f"({h.get('pnl_pct')}%)"
                )
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
