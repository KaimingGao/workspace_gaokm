"""Web 内自动做 T 后台 worker。

开关持久化到 data/t0_auto_worker.json；启动/停止时同步 paper.rules.t0_auto.enabled。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

from core.t0.config import T0_INTRADAY_TICK_SEC

TICK_INTERVAL_SEC = float(T0_INTRADAY_TICK_SEC)


def _now_ts() -> float:
    return time.time()


def _worker_config_path() -> str:
    from core.paths import T0_AUTO_WORKER_PATH

    return T0_AUTO_WORKER_PATH


def _load_enabled_flag() -> bool:
    path = _worker_config_path()
    if not os.path.isfile(path):
        return False
    try:
        import json

        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return bool((raw or {}).get("enabled"))
    except Exception:  # noqa: BLE001
        logger.debug("load t0 worker config failed", exc_info=True)
        return False


def _save_enabled_flag(enabled: bool) -> None:
    from core.io_atomic import atomic_write_json

    atomic_write_json(
        _worker_config_path(),
        {"enabled": bool(enabled), "updated_at": _now_ts()},
    )


def _session_closed(now: Any = None) -> tuple[bool, Optional[str]]:
    try:
        from core.market.calendar import resolve_session_date
        from core.signal.session_pit import asof_session_final, shanghai_now

        sess = resolve_session_date()
        closed = bool(asof_session_final(sess, now=shanghai_now(now)))
        return closed, sess
    except Exception:  # noqa: BLE001
        logger.debug("t0 worker session check failed", exc_info=True)
        return False, None


class T0AutoWorker:
    """本 Web 进程内的后台 worker；开关持久化到 data/t0_auto_worker.json。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._enabled = False
        self._runtime: Dict[str, Any] = {
            "started_at": None,
            "last_tick_ts": None,
            "last_tick_message": "",
            "last_error": None,
            "last_run_session": None,
        }

    def _thread_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> Dict[str, Any]:
        with self._lock:
            rt = dict(self._runtime)
            alive = self._thread_alive()
            enabled = bool(self._enabled)
            out: Dict[str, Any] = {
                "enabled": enabled,
                "running": alive,
                "thread_alive": alive,
                "started_at": rt.get("started_at"),
                "last_tick_ts": rt.get("last_tick_ts"),
                "last_tick_message": rt.get("last_tick_message") or "",
                "last_error": rt.get("last_error"),
                "last_run_session": rt.get("last_run_session"),
                "tick_interval_sec": TICK_INTERVAL_SEC,
            }
            if alive and rt.get("last_tick_ts"):
                elapsed = _now_ts() - float(rt["last_tick_ts"])
                out["next_tick_in_sec"] = max(0, int(TICK_INTERVAL_SEC - elapsed))
            else:
                out["next_tick_in_sec"] = None
            if enabled and not alive:
                out["state"] = "starting"
            elif alive:
                out["state"] = "running"
            else:
                out["state"] = "stopped"
            return out

    def is_running(self) -> bool:
        return self._thread_alive()

    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._enabled = True
                _save_enabled_flag(True)
                return {
                    "ok": True,
                    "worker": self.status(),
                    "message": "后台进程已在运行",
                }
            self._stop.clear()
            self._enabled = True
            self._runtime["started_at"] = _now_ts()
            self._runtime["last_error"] = None
            self._thread = threading.Thread(
                target=self._loop,
                name="t0-auto-worker",
                daemon=True,
            )
            self._thread.start()
        _save_enabled_flag(True)
        return {"ok": True, "worker": self.status(), "message": "已启动后台进程"}

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            self._enabled = False
            self._stop.set()
            th = self._thread
        if th and th.is_alive():
            th.join(timeout=TICK_INTERVAL_SEC + 5)
        with self._lock:
            self._thread = None
        _save_enabled_flag(False)
        return {"ok": True, "worker": self.status(), "message": "已停止后台进程"}

    def set_enabled(self, enabled: bool) -> Dict[str, Any]:
        if enabled:
            return self.start()
        return self.stop()

    def restore(self) -> None:
        if _load_enabled_flag():
            self.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:  # noqa: BLE001
                logger.exception("t0 auto worker tick failed")
                with self._lock:
                    self._runtime["last_error"] = str(e)
            self._stop.wait(TICK_INTERVAL_SEC)

    def _tick(self) -> None:
        from core.paths import PAPER_PATH
        from core.t0.intraday import session_in_market
        from services.paper_service import PaperService

        with self._lock:
            if not self._enabled:
                self._runtime["last_tick_message"] = "后台进程已关闭"
                self._runtime["last_tick_ts"] = _now_ts()
                return

        in_market, sess = session_in_market()
        closed, sess2 = _session_closed()
        sess = sess or sess2

        if not in_market and not closed:
            with self._lock:
                self._runtime["last_tick_ts"] = _now_ts()
                self._runtime["last_tick_message"] = "盘外待机"
                self._runtime["last_run_session"] = sess
            return

        svc = PaperService(PAPER_PATH)
        try:
            svc.t0_auto_status()
        except FileNotFoundError:
            with self._lock:
                self._runtime["last_tick_ts"] = _now_ts()
                self._runtime["last_tick_message"] = "纸面账户未初始化"
            return

        with self._lock:
            self._runtime["last_tick_ts"] = _now_ts()
            self._runtime["last_run_session"] = sess
            self._runtime["last_tick_message"] = (
                f"5m 盯盘 · {sess or '—'}" if in_market else f"收盘收尾 · {sess or '—'}"
            )

        out = svc.run_t0_intraday_tick(
            skip_open_fill_gate=True,
            force_session_close=bool(closed and not in_market),
        )
        with self._lock:
            if out.get("error") or out.get("ok") is False:
                self._runtime["last_error"] = str(out.get("error") or out.get("note") or "失败")
                self._runtime["last_tick_message"] = f"盯盘失败：{self._runtime['last_error']}"
            elif out.get("skipped") and out.get("reason"):
                self._runtime["last_error"] = None
                self._runtime["last_tick_message"] = str(out.get("reason"))
            elif int(out.get("trade_count") or 0) > 0:
                self._runtime["last_error"] = None
                trades = int(out.get("trade_count") or 0)
                pnl = out.get("pnl_total")
                self._runtime["last_tick_message"] = (
                    f"落账 · 新成交 {trades} · PnL {pnl if pnl is not None else '—'}"
                )
            else:
                self._runtime["last_error"] = None
                skip = out.get("skip_count")
                self._runtime["last_tick_message"] = (
                    f"5m 盯盘 · 无新成交" + (f" · 跳过 {skip}" if skip is not None else "")
                )


t0_auto_worker = T0AutoWorker()
