"""进程内 RLock + 文件锁，串行化对本机 JSON 账本的 RMW。

用法::

    with path_lock("/path/to/paper.json"):
        data = load(...)
        ...
        save(...)

macOS/Linux 用 fcntl.flock；无 fcntl 时退化为仅线程锁（仍防同进程竞态）。

同线程可重入：``with path_lock: ... save_paper()``（内部再次 path_lock）不会自锁。
"""

import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover — Windows
    fcntl = None  # type: ignore

_LOCKS: Dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
# 同线程重入：只在最外层持有 flock fd
_FLOCK_STATE: Dict[str, Dict[str, Any]] = {}


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


def _acquire_flock(fh, *, timeout_sec: Optional[float], key: str) -> None:
    """非阻塞轮询获取文件锁；超时抛 TimeoutError（避免跨进程无限卡住）。"""
    if fcntl is None:
        return
    deadline = None if timeout_sec is None else (time.monotonic() + float(timeout_sec))
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"等待文件锁超时: {key}")
            time.sleep(0.05)


def _release_flock_state(key: str) -> None:
    state = _FLOCK_STATE.pop(key, None)
    if not state:
        return
    fh = state.get("fh")
    if fh is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        fh.close()
    except OSError:
        pass

@contextmanager

def path_lock(path: str, *, timeout_sec: Optional[float] = 60.0) -> Iterator[None]:
    """对 ``path`` 取排他锁（线程 + ``path.lock`` 文件）。

    ``timeout_sec`` 同时约束进程内 RLock 与跨进程 flock；``None`` 表示一直等。
    同线程重入只增加深度，不再二次 flock（避免 macOS 自锁超时）。
    """
    key = _norm(path)
    tlock = _thread_lock(key)
    if timeout_sec is None:
        tlock.acquire()
    else:
        acquired = tlock.acquire(timeout=float(timeout_sec))
        if not acquired:
            raise TimeoutError(f"等待文件锁超时: {key}")

    tid = threading.get_ident()
    state = _FLOCK_STATE.get(key)
    if state and state.get("thread") == tid and int(state.get("depth") or 0) > 0:
        state["depth"] = int(state["depth"]) + 1
        try:
            yield
        finally:
            state["depth"] = int(state["depth"]) - 1
            if int(state["depth"]) <= 0:
                _release_flock_state(key)
            tlock.release()
        return

    lock_path = key + ".lock"
    fh = None
    try:
        os.makedirs(os.path.dirname(key) or ".", exist_ok=True)
        fh = open(lock_path, "a+", encoding="utf-8")
        _acquire_flock(fh, timeout_sec=timeout_sec, key=key)
        _FLOCK_STATE[key] = {"depth": 1, "fh": fh, "thread": tid}
        fh = None  # 所有权交给 _FLOCK_STATE
        yield
    finally:
        state = _FLOCK_STATE.get(key)
        if state and state.get("thread") == tid:
            state["depth"] = int(state.get("depth") or 1) - 1
            if int(state["depth"]) <= 0:
                _release_flock_state(key)
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass
        tlock.release()
