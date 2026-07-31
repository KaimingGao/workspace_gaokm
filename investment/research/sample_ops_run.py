#!/usr/bin/env python3
"""样本运营 CLI：TTM 闭环种子 · 财务 history · 纸面快照密度 · 覆盖报告。"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="sample ops: TTM / PIT history / snapshots")
    parser.add_argument(
        "command",
        choices=(
            "status",
            "seed-ttm",
            "persist-history",
            "seed-ladder",
            "ingest-history",
            "densify-snapshots",
            "prune-densified",
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--quarters", type=int, default=3)
    parser.add_argument("--days", type=int, default=40, help="densify target snapshot days")
    parser.add_argument(
        "--max-points",
        type=int,
        default=8,
        help="ingest-history: max real report periods per code",
    )
    parser.add_argument(
        "--keep-synthetic",
        action="store_true",
        help="ingest-history: keep synthetic_demo ladder points",
    )
    parser.add_argument("--codes", default="", help="comma-separated stock codes")
    args = parser.parse_args(argv)

    codes = [c.strip() for c in str(args.codes or "").split(",") if c.strip()] or None
    write = not args.dry_run

    if args.command == "status":
        from core.paper import load_paper
        from core.paths import PAPER_PATH
        from core.sample_ops import sample_status

        paper = None
        if os.path.isfile(PAPER_PATH):
            paper = load_paper(PAPER_PATH)
        out = sample_status(paper=paper)
    elif args.command == "seed-ttm":
        from core.sample_ops import seed_ttm_cycles

        out = seed_ttm_cycles(cycles=args.cycles, write=write)
    elif args.command == "persist-history":
        from core.sample_ops import persist_fundamentals_history

        out = persist_fundamentals_history(codes=codes, write=write)
    elif args.command == "seed-ladder":
        from core.sample_ops import seed_fundamentals_history_ladder

        out = seed_fundamentals_history_ladder(
            codes=codes, quarters=args.quarters, write=write
        )
    elif args.command == "ingest-history":
        from core.sample_ops import ingest_real_fundamentals_history

        out = ingest_real_fundamentals_history(
            codes=codes,
            max_points=args.max_points,
            drop_synthetic=not args.keep_synthetic,
            write=write,
        )
    elif args.command == "prune-densified":
        from core.paper import load_paper, save_paper
        from core.paths import PAPER_PATH
        from core.sample_ops import prune_densified_snapshots

        if not os.path.isfile(PAPER_PATH):
            print("paper.json 不存在", file=sys.stderr)
            return 1
        paper = load_paper(PAPER_PATH)
        out = prune_densified_snapshots(paper, write_key=write)
        if write and out.get("changed"):
            save_paper(paper, PAPER_PATH)
    else:
        from core.paper import load_paper, save_paper
        from core.paths import PAPER_PATH
        from core.sample_ops import densify_paper_snapshots

        if not os.path.isfile(PAPER_PATH):
            print("paper.json 不存在", file=sys.stderr)
            return 1
        paper = load_paper(PAPER_PATH)
        out = densify_paper_snapshots(
            paper, target_days=args.days, write_key=write
        )
        if write and out.get("changed"):
            save_paper(paper, PAPER_PATH)

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
