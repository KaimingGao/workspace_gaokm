#!/usr/bin/env python3
"""将 data/store/daily|minute 下 JSON 缓存幂等迁入 bars.db（不删原 JSON）。

用法::

    cd investment
    python3 scripts/migrate_bars_to_sqlite.py --dry-run
    python3 scripts/migrate_bars_to_sqlite.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.store import (  # noqa: E402
    DAILY_SUBDIR,
    MINUTE_SUBDIR,
    assess_quality,
    get_store_dir,
    trim_daily_bars,
    trim_minute_bars,
)
from core.store_bars_sqlite import (  # noqa: E402
    db_path,
    get_conn,
    reset_conn_cache,
    save_daily,
    save_minute,
)


def _iter_daily_json(store_dir: str):
    base = os.path.join(store_dir, DAILY_SUBDIR)
    if not os.path.isdir(base):
        return
    for mkt in sorted(os.listdir(base)):
        mdir = os.path.join(base, mkt)
        if not os.path.isdir(mdir):
            continue
        for name in sorted(os.listdir(mdir)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(mdir, name)
            try:
                with open(path, encoding="utf-8") as f:
                    payload = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                print(f"skip daily {path}: {e}")
                continue
            yield mkt, path, payload


def _iter_minute_json(store_dir: str):
    base = os.path.join(store_dir, MINUTE_SUBDIR)
    if not os.path.isdir(base):
        return
    for per in sorted(os.listdir(base)):
        pdir = os.path.join(base, per)
        if not os.path.isdir(pdir):
            continue
        for mkt in sorted(os.listdir(pdir)):
            mdir = os.path.join(pdir, mkt)
            if not os.path.isdir(mdir):
                continue
            for name in sorted(os.listdir(mdir)):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(mdir, name)
                try:
                    with open(path, encoding="utf-8") as f:
                        payload = json.load(f)
                except (OSError, json.JSONDecodeError) as e:
                    print(f"skip minute {path}: {e}")
                    continue
                yield per, mkt, path, payload


def migrate(store_dir: str, *, dry_run: bool = False) -> int:
    reset_conn_cache()
    daily_n = 0
    minute_n = 0
    bar_rows = 0

    for mkt, path, payload in _iter_daily_json(store_dir) or []:
        code = payload.get("code") or os.path.basename(path)[:-5]
        bars = list(payload.get("bars") or [])
        daily_n += 1
        bar_rows += len(bars)
        if dry_run:
            if daily_n % 50 == 0:
                print(f"dry-run daily … {daily_n}")
            continue
        save_daily(
            mkt,
            code,
            bars,
            data_source=payload.get("data_source") or "migrated_json",
            stock_code=payload.get("stock_code") or code,
            store_dir=store_dir,
            adjust_policy=payload.get("adjust_policy") or "qfq",
            assess_quality=assess_quality,
            trim_daily_bars=trim_daily_bars,
        )
        if daily_n % 50 == 0:
            print(f"migrated daily {daily_n} …")

    for per, mkt, path, payload in _iter_minute_json(store_dir) or []:
        code = payload.get("code") or os.path.basename(path)[:-5]
        bars = list(payload.get("bars") or [])
        minute_n += 1
        if dry_run:
            if minute_n % 50 == 0:
                print(f"dry-run minute … {minute_n}")
            continue
        save_minute(
            mkt,
            code,
            bars,
            period=str(payload.get("period") or per),
            data_source=payload.get("data_source") or "migrated_json",
            stock_code=payload.get("stock_code") or code,
            store_dir=store_dir,
            adjust_policy=payload.get("adjust_policy") or "qfq",
            trim_minute_bars=trim_minute_bars,
        )
        if minute_n % 50 == 0:
            print(f"migrated minute {minute_n} …")

    if not dry_run:
        conn = get_conn(store_dir)
        db_daily = int(conn.execute("SELECT COUNT(*) FROM daily_cache_meta").fetchone()[0])
        db_bars = int(conn.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0])
        print(f"db meta={db_daily} daily_bars={db_bars} path={db_path(store_dir)}")
        print("原 JSON 保留未删；确认稳定后再手工清理 daily/minute 目录。")

    print(
        f"{'dry-run ' if dry_run else ''}daily_files={daily_n} "
        f"minute_files={minute_n} json_bar_rows≈{bar_rows}"
    )
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Migrate bars JSON → SQLite bars.db")
    p.add_argument("--store-dir", default=None, help="默认 INVESTMENT_STORE_DIR / data/store")
    p.add_argument("--dry-run", action="store_true", help="只统计不写入")
    args = p.parse_args(argv)
    store = args.store_dir or get_store_dir()
    print(f"store: {store}")
    return migrate(store, dry_run=bool(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
