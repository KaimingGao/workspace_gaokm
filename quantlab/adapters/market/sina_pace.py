"""新浪日线开工闸。

进程池里多只股票会同时进 ``fetch_a_daily_bars``。闸门只包住真正打新浪的那段：
同时最多 ``SINA_DAILY_MAX_INFLIGHT`` 只，两次开工至少隔 ``SINA_DAILY_PACE_SEC``。
缓存命中不会进这里。

状态写在本机文件上，spawn 出来的子进程共用同一把闸。进程退出后名额靠
pid 探测和超时收回。
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from core.data.policy import (
    SINA_DAILY_SLOT_STALE_SEC,
    sina_daily_max_inflight,
    sina_daily_pace_sec,
)
from core.file_lock import path_lock
from core.paths import DATA_DIR

logger = logging.getLogger(__name__)


def sina_daily_gate_path() -> str:
    raw = os.environ.get("QUANTLAB_SINA_DAILY_GATE_PATH", "").strip()
    if raw:
        return raw
    return os.path.join(DATA_DIR, "run", "sina_daily_pace.json")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _load_state(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    holders = data.get("holders")
    if not isinstance(holders, list):
        holders = []
    try:
        last_start = float(data.get("last_start") or 0.0)
    except (TypeError, ValueError):
        last_start = 0.0
    return {"last_start": last_start, "holders": holders}


def _save_state(path: str, state: Dict[str, Any]) -> None:
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)


def _live_holders(holders: List[Any], now: float) -> List[dict]:
    live: List[dict] = []
    for item in holders:
        if not isinstance(item, dict):
            continue
        token = str(item.get("id") or "")
        if not token:
            continue
        try:
            pid = int(item.get("pid") or 0)
            ts = float(item.get("ts") or 0.0)
        except (TypeError, ValueError):
            continue
        if not _pid_alive(pid):
            continue
        if ts <= 0 or (now - ts) > float(SINA_DAILY_SLOT_STALE_SEC):
            continue
        live.append({"id": token, "pid": pid, "ts": ts})
    return live


def _try_grant(
    state: Dict[str, Any],
    *,
    cap: int,
    pace: float,
    now: float,
    token: str,
    pid: int,
) -> bool:
    holders = _live_holders(list(state.get("holders") or []), now)
    state["holders"] = holders
    last = float(state.get("last_start") or 0.0)
    if len(holders) >= cap:
        return False
    if pace > 0 and last > 0 and (now - last) < pace:
        return False
    holders.append({"id": token, "pid": pid, "ts": now})
    state["last_start"] = now
    return True


def _wait_hint(state: Dict[str, Any], *, pace: float, now: float) -> float:
    last = float(state.get("last_start") or 0.0)
    if pace > 0 and last > 0:
        remain = pace - (now - last)
        if remain > 0:
            return min(0.25, max(0.05, remain))
    return 0.2


def _release(path: str, token: str) -> None:
    try:
        with path_lock(path, timeout_sec=30.0):
            state = _load_state(path)
            state["holders"] = [
                h
                for h in _live_holders(list(state.get("holders") or []), time.time())
                if str(h.get("id") or "") != token
            ]
            _save_state(path, state)
    except Exception:
        logger.warning("sina daily slot release failed", exc_info=True)


@contextmanager
def sina_daily_slot(*, gate_path: Optional[str] = None) -> Iterator[None]:
    """占用一个新浪日线名额，离开时归还。拿不到名额就睡，不空转。"""
    path = gate_path or sina_daily_gate_path()
    cap = sina_daily_max_inflight()
    pace = sina_daily_pace_sec()
    token = uuid.uuid4().hex
    pid = os.getpid()
    granted = False
    while not granted:
        with path_lock(path, timeout_sec=30.0):
            now = time.time()
            state = _load_state(path)
            if _try_grant(state, cap=cap, pace=pace, now=now, token=token, pid=pid):
                _save_state(path, state)
                granted = True
            else:
                delay = _wait_hint(state, pace=pace, now=now)
        if not granted:
            time.sleep(delay)
    try:
        yield
    finally:
        _release(path, token)
