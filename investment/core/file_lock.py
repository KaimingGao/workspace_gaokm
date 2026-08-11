"""进程内 RLock + 文件锁，串行化对本机 JSON 账本的 RMW。

用法::

    with path_lock("/path/to/paper.json"):
        data = load(...)
        ...
        save(...)

macOS/Linux 用 fcntl.flock；无 fcntl 时退化为仅线程锁（仍防同进程竞态）。
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Dict, Iterator, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover — Windows
    fcntl = None  # type: ignore

_LOCKS: Dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _norm(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path or "."))


def _thread_lock(path: str) -> threading.RLock:
    key = _norm(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


@contextmanager
def path_lock(path: str, *, timeout_sec: Optional[float] = 60.0) -> Iterator[None]:
    """对 ``path`` 取排他锁（线程 + ``path.lock`` 文件）。

    ``timeout_sec`` 仅用于等待进程内 RLock；文件锁在持有线程锁后阻塞获取。
    """
    key = _norm(path)
    tlock = _thread_lock(key)
    acquired = tlock.acquire(timeout=None if timeout_sec is None else float(timeout_sec))
    if not acquired:
        raise TimeoutError(f"等待文件锁超时: {key}")

    lock_path = key + ".lock"
    fh = None
    try:
        os.makedirs(os.path.dirname(key) or ".", exist_ok=True)
        fh = open(lock_path, "a+", encoding="utf-8")
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if fh is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                fh.close()
            except OSError:
                pass
        tlock.release()
