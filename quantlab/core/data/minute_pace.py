"""新浪分钟批量开工闸。

增量补齐和强更共用：两次开工至少隔一个间隔，同时在途由调用方的线程池封顶。
空返回、超时、断连之后，本轮后续间隔拉长，不再按原速顶。
"""

from __future__ import annotations

import threading
import time

from core.data.policy import MINUTE_PACE_STRETCH, MINUTE_PACE_STRETCH_MAX_SEC

_STRETCH_MARKS = (
    "timeout",
    "timed out",
    "empty",
    "no_minute",
    "connection",
    "disconnected",
    "reset",
    "aborted",
    "refused",
    "broken pipe",
    "403",
    "429",
    "forbidden",
)


def minute_pace_should_stretch(err: str) -> bool:
    """空包、超时、断连才拉长。业务错误（代码非法等）保持原间隔。"""
    text = str(err or "").lower()
    if not text:
        return False
    return any(mark in text for mark in _STRETCH_MARKS)


class MinuteStartPace:
    """线程安全的开工间隔。``pace_sec<=0`` 时 ``wait`` 立即返回。"""

    def __init__(self, pace_sec: float) -> None:
        self.base = max(0.0, float(pace_sec or 0.0))
        self._pace = self.base
        self._next = 0.0
        self._lock = threading.Lock()
        self.stretched = False

    @property
    def pace_sec(self) -> float:
        with self._lock:
            return self._pace

    def wait(self) -> None:
        with self._lock:
            pace = self._pace
            if pace <= 0:
                return
            now = time.monotonic()
            wait_s = self._next - now
            self._next = max(now, self._next) + pace
        if wait_s > 0:
            time.sleep(wait_s)

    def note_remote_failure(self, err: str) -> None:
        if self.base <= 0 or not minute_pace_should_stretch(err):
            return
        with self._lock:
            nxt = min(
                float(MINUTE_PACE_STRETCH_MAX_SEC),
                max(self._pace, self.base) * float(MINUTE_PACE_STRETCH),
            )
            if nxt > self._pace:
                self._pace = nxt
                self.stretched = True
