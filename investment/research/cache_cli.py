#!/usr/bin/env python3
"""查看 / 清理日线本地缓存（JSON 或 SQLite，随 INVESTMENT_BARS_BACKEND）。"""


import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.store import (  # noqa: E402
    bars_backend,
    clear_daily_cache,
    get_store_dir,
    list_cached_symbols,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Investment 日线缓存管理")
    parser.add_argument(
        "--list",
        action="store_true",
        help="列出已缓存标的",
    )
    parser.add_argument(
        "--market",
        help="过滤市场 CN/HK/US",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="删除全部或指定 market 下的日线缓存",
    )
    args = parser.parse_args(argv)

    store = get_store_dir()
    print(f"store: {store}")
    print(f"bars_backend: {bars_backend()}")

    if args.clear:
        removed = clear_daily_cache(market=args.market, store_dir=store)
        print(f"已删除 {removed} 条日线缓存")
        return 0

    rows = list_cached_symbols(market=args.market)
    if not rows:
        print("（空）")
        return 0
    for row in rows:
        q = row.get("quality") or {}
        print(
            f"{row.get('market')}/{row.get('code')} "
            f"bars={q.get('bar_count')} level={q.get('level')} "
            f"src={row.get('data_source')} at={row.get('fetched_at')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
