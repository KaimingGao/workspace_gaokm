#!/usr/bin/env python3
"""将 watching 池未映射代码补入 sector_map（不覆盖已有主题）。"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="sync sector_map from watching")
    parser.add_argument("--dry-run", action="store_true", help="只报告不写盘")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from core.sector_map_sync import coverage_report, sync_sector_map_from_watching

    sync = sync_sector_map_from_watching(write=not args.dry_run)
    cov = coverage_report()
    out = {"sync": sync, "coverage": cov}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(
            f"map_size={cov.get('map_size')} watching={cov.get('total')} "
            f"coverage={cov.get('coverage')} added={sync.get('added_count')} "
            f"written={sync.get('written')}"
        )
        for a in sync.get("added") or []:
            print(f"  + {a.get('stock_code')} → {a.get('sector')} {a.get('stock_name') or ''}")
    return 0 if sync.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
