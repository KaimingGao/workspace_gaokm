"""Live JobProgress 槽隔离：单测勿污染 ``data/jobs/*.json``。"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator


@contextmanager
def isolated_live_slot(slot: Any) -> Iterator[Any]:
    """临时关闭 persist，结束后还原内存快照（并写回原盘路径）。"""
    with slot._lock:
        prev_job = dict(slot._job)
    prev_path = slot._persist_path
    slot._persist_path = None
    try:
        yield slot
    finally:
        with slot._lock:
            slot._job = prev_job
            slot._persist_path = prev_path
            if prev_path:
                slot._save_unlocked()
