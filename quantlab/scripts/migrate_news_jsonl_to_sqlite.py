#!/usr/bin/env python3
"""把 data/store/news/history/*.jsonl 与根下 {code}.json 幂等迁入 news.db。

不删原文件。标题去重依赖 UNIQUE(code, title_hash)。

用法::

    cd quantlab
    python3 scripts/migrate_news_jsonl_to_sqlite.py --dry-run
    python3 scripts/migrate_news_jsonl_to_sqlite.py
    python3 scripts/migrate_news_jsonl_to_sqlite.py --limit-codes 10
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.news_archive import (  # noqa: E402
    bare_a_share,
    ingest_title_rows,
    news_db_path,
)
from core.paths import NEWS_HISTORY_DIR, NEWS_STORE_DIR  # noqa: E402


def _items_from_jsonl_row(row: Dict[str, Any]) -> Tuple[List[dict], str]:
    fetched = str(row.get("fetched_at") or row.get("updated_at") or "").strip()
    titles = row.get("titles")
    if isinstance(titles, list) and titles:
        items: List[dict] = []
        for t in titles:
            if not isinstance(t, dict):
                continue
            title = str(t.get("title") or "").strip()
            if not title:
                continue
            items.append(
                {
                    "title": title,
                    "time": str(t.get("time") or t.get("datetime") or "")[:32],
                    "source": str(t.get("source") or "")[:40],
                    "url": str(t.get("url") or ""),
                }
            )
        return items, fetched
    raw_items = row.get("items")
    if isinstance(raw_items, list):
        items = []
        for it in raw_items:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title") or "").strip()
            if not title:
                continue
            items.append(
                {
                    "title": title,
                    "time": str(it.get("time") or it.get("datetime") or "")[:32],
                    "source": str(it.get("source") or "")[:40],
                    "url": str(it.get("url") or ""),
                }
            )
        return items, fetched
    return [], fetched


def _iter_jsonl(history_dir: str) -> Iterable[Tuple[str, List[dict], str]]:
    if not os.path.isdir(history_dir):
        return
    for name in sorted(os.listdir(history_dir)):
        if not name.endswith(".jsonl"):
            continue
        code = bare_a_share(name[:-6])
        path = os.path.join(history_dir, name)
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    c = bare_a_share(row.get("code") or code)
                    items, fetched = _items_from_jsonl_row(row)
                    if c and items:
                        yield c, items, fetched or "1970-01-01T00:00:00"
        except OSError as e:
            print(f"skip jsonl {path}: {e}")


def _iter_root_json(news_dir: str) -> Iterable[Tuple[str, List[dict], str]]:
    if not os.path.isdir(news_dir):
        return
    for name in sorted(os.listdir(news_dir)):
        if not name.endswith(".json"):
            continue
        if name in ("watching.json",):
            continue
        path = os.path.join(news_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"skip json {path}: {e}")
            continue
        if not isinstance(payload, dict):
            continue
        code = bare_a_share(payload.get("stock_code") or name[:-5])
        items, fetched = _items_from_jsonl_row(payload)
        if not fetched:
            fetched = str(payload.get("updated_at") or "")[:19]
        if code and items:
            yield code, items, fetched or "1970-01-01T00:00:00"


def migrate(
    *,
    dry_run: bool = False,
    limit_codes: Optional[int] = None,
    store_dir: Optional[str] = None,
) -> Dict[str, Any]:
    history_dir = NEWS_HISTORY_DIR
    news_dir = NEWS_STORE_DIR
    by_code: Dict[str, List[Tuple[List[dict], str]]] = {}
    for code, items, fetched in list(_iter_jsonl(history_dir) or []):
        by_code.setdefault(code, []).append((items, fetched))
    for code, items, fetched in list(_iter_root_json(news_dir) or []):
        by_code.setdefault(code, []).append((items, fetched))

    codes = sorted(by_code.keys())
    if limit_codes is not None and limit_codes > 0:
        codes = codes[: int(limit_codes)]

    n_batches = 0
    n_inserted = 0
    n_kept = 0
    n_rejected = 0
    for code in codes:
        for items, fetched in by_code[code]:
            n_batches += 1
            if dry_run:
                continue
            out = ingest_title_rows(
                code,
                items,
                fetched_at=fetched,
                store_dir=store_dir,
                ok=True,
                degraded=False,
            )
            n_inserted += int(out.get("n_inserted") or 0)
            n_kept += int(out.get("n_kept") or 0)
            n_rejected += int(out.get("n_rejected") or 0)

    return {
        "ok": True,
        "dry_run": dry_run,
        "db": news_db_path(store_dir),
        "n_codes": len(codes),
        "n_batches": n_batches,
        "n_inserted": n_inserted,
        "n_kept": n_kept,
        "n_rejected": n_rejected,
        "history_dir": history_dir,
        "news_dir": news_dir,
        "note": "一次性迁移；跑完可删 data/store/news/history 与根下 {code}.json（保留 news/llm）",
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Migrate news json/jsonl into news.db")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit-codes", type=int, default=0)
    p.add_argument(
        "--store-dir",
        default="",
        help="news.db 所在目录（默认 QUANTLAB_NEWS_ARCHIVE_DIR / STORE_DIR）",
    )
    args = p.parse_args(argv)
    out = migrate(
        dry_run=bool(args.dry_run),
        limit_codes=int(args.limit_codes) or None,
        store_dir=str(args.store_dir or "").strip() or None,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
