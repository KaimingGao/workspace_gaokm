#!/usr/bin/env python3
"""查看 / 清理日线本地缓存。"""


import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.store import get_store_dir, list_cached_symbols  # noqa: E402


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
        help="删除全部或指定 market 下的缓存 json",
    )
    args = parser.parse_args(argv)

    store = get_store_dir()
    print(f"store: {store}")

    if args.clear:
        base = os.path.join(store, "daily")
        if not os.path.isdir(base):
            print("无缓存")
            return 0
        removed = 0
        markets = [args.market.upper()] if args.market else os.listdir(base)
        for mkt in markets:
            mdir = os.path.join(base, mkt)
            if not os.path.isdir(mdir):
                continue
            for name in os.listdir(mdir):
                if name.endswith(".json"):
                    os.remove(os.path.join(mdir, name))
                    removed += 1
        print(f"已删除 {removed} 个缓存文件")
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
