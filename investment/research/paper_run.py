#!/usr/bin/env python3
"""纸面账户 CLI（薄封装，逻辑在 core.paper）。"""

from __future__ import annotations

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
    run_daily_cycle,
    save_paper,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Investment 纸面账户")
    parser.add_argument("--path", default=DEFAULT_PAPER_PATH, help="paper.json 路径")
    parser.add_argument("--init", action="store_true", help="从 paper.example.json 初始化")
    parser.add_argument("--run", action="store_true", help="跑持仓/观察 signal 并记快照")
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
        result = run_daily_cycle(paper, simulate_buy=args.simulate_buy)
        save_paper(paper, args.path)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"观察池 {result['observation_pool_count']} 只")
            for s in result.get("top_signals") or []:
                print(
                    f"  - {s.get('stock_name')}({s.get('stock_code')}): "
                    f"score={s.get('score')} reject={s.get('hard_reject')}"
                )
            if result.get("new_trades"):
                print(f"纸面买入 {len(result['new_trades'])} 笔")
            sm = result.get("summary") or {}
            print(
                f"净值 equity={sm.get('equity')} "
                f"pnl={sm.get('total_pnl_pct')}% "
                f"positions={sm.get('position_count')}"
            )
        return 0

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
