#!/usr/bin/env python3
"""sector_map 对齐 / 清洗 / 现货行业补全（DS-R2.1 / R2.2）。"""


import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="sync/scrub/enrich sector_map")
    parser.add_argument("--dry-run", action="store_true", help="只报告不写盘")
    parser.add_argument(
        "--scrub-only",
        action="store_true",
        help="仅清洗板别伪主题",
    )
    parser.add_argument(
        "--enrich-spot",
        action="store_true",
        help="用东财现货所属行业补全未映射码",
    )
    parser.add_argument(
        "--force-spot",
        action="store_true",
        help="enrich 时强制刷新现货（否则优先磁盘缓存）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="enrich 时覆盖已有真主题",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from core.sector_map_sync import (
        coverage_report,
        enrich_sector_map_from_spot,
        scrub_board_labels_from_sector_map,
        sync_sector_map_from_watching,
    )

    if args.scrub_only:
        sync = scrub_board_labels_from_sector_map(write=not args.dry_run)
    elif args.enrich_spot:
        sync = enrich_sector_map_from_spot(
            write=not args.dry_run,
            force_spot=bool(args.force_spot),
            overwrite=bool(args.overwrite),
        )
    else:
        sync = sync_sector_map_from_watching(write=not args.dry_run, scrub_boards=True)
    cov = coverage_report()
    out = {"sync": sync, "coverage": cov}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(
            f"map_size={cov.get('map_size')} watching={cov.get('total')} "
            f"coverage={cov.get('coverage')} "
            f"board_labeled={cov.get('board_labeled')} "
            f"added={sync.get('added_count', sync.get('removed_count'))} "
            f"written={sync.get('written')}"
        )
        for a in sync.get("added") or []:
            print(f"  + {a.get('stock_code')} → {a.get('sector')} {a.get('stock_name') or ''}")
        for a in (sync.get("removed") or [])[:12]:
            print(f"  - {a.get('stock_code')} 板别:{a.get('sector')}")
        for a in (sync.get("updated") or [])[:8]:
            print(f"  ~ {a.get('stock_code')} {a.get('from')} → {a.get('sector')}")
    return 0 if sync.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
