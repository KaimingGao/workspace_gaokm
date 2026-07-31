#!/usr/bin/env python3
"""Watching 管理 CLI（P9.1）。"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.watching_store import (  # noqa: E402
    init_from_example,
    read_watching,
    refresh_watchlist,
    sync_paper_watchlist,
    write_watching,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Investment Watching 管理")
    parser.add_argument("--init", action="store_true", help="从 watching.example.json 初始化")
    parser.add_argument("--refresh", action="store_true", help="按 sources 刷新 watchlist")
    parser.add_argument("--sync-paper", action="store_true", help="兼容空步骤（观察/仓位已分离）")
    parser.add_argument("--show", action="store_true", help="打印当前 watching")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    if not any([args.init, args.refresh, args.sync_paper, args.show]):
        parser.error("请指定 --init / --refresh / --sync-paper / --show")

    out = {}
    try:
        if args.init:
            path = init_from_example()
            out = {"success": True, "action": "init", "path": path}
        if args.refresh:
            result = refresh_watchlist()
            out = {"success": True, "action": "refresh", **result}
        if args.sync_paper:
            result = sync_paper_watchlist()
            out = {"success": True, "action": "sync_paper", **result}
        if args.show:
            out = {"success": True, "watching": read_watching()}
    except Exception as e:
        out = {"success": False, "error": str(e)}

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        if out.get("success"):
            print(f"OK: {out.get('action', 'show')}")
            if out.get("watchlist"):
                print("watchlist:", ", ".join(out["watchlist"][:10]))
            elif out.get("watching"):
                wl = out["watching"].get("watchlist") or []
                print(f"watchlist ({len(wl)}):", ", ".join(wl[:10]))
        else:
            print(out.get("error") or "failed")
    return 0 if out.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
