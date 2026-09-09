"""Web 内自动调仓后台 worker。

每个交易日仅 09:30–10:00 现价成交一次；过点不补跑、不挂开盘单。
开关与 last_run_session 持久化到 data/rebalance_auto_worker.json。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

WINDOW_AFTER = (9, 30)
WINDOW_UNTIL = (10, 0)
TICK_INTERVAL_SEC = 30.0
IDLE_TICK_INTERVAL_SEC = 60.0


def _now_ts() -> float:
    return time.time()


def _worker_config_path() -> str:
    from core.paths import REBALANCE_AUTO_WORKER_PATH

    return REBALANCE_AUTO_WORKER_PATH


def _load_state() -> Dict[str, Any]:
    path = _worker_config_path()
    if not os.path.isfile(path):
        return {}
    try:
        import json

        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return dict(raw or {}) if isinstance(raw, dict) else {}
    except Exception:  # noqa: BLE001
        logger.debug("load rebalance worker config failed", exc_info=True)
        return {}


def _save_state(patch: Dict[str, Any]) -> Dict[str, Any]:
    from core.io_atomic import atomic_write_json

    cur = _load_state()
    cur.update(patch)
    cur["updated_at"] = _now_ts()
    atomic_write_json(_worker_config_path(), cur)
    return cur


def load_enabled_flag() -> bool:
    return bool(_load_state().get("enabled"))


def last_run_session() -> str:
    return str(_load_state().get("last_run_session") or "").strip()[:10]


def mark_run_session(
    session: str,
    *,
    fill_action: str = "",
    buy_count: int = 0,
    sell_count: int = 0,
    note: str = "",
    source: str = "auto",
) -> None:
    sess = str(session or "").strip()[:10]
    if not sess:
        return
    _save_state(
        {
            "enabled": load_enabled_flag(),
            "last_run_session": sess,
            "last_run_ts": _now_ts(),
            "last_fill_action": str(fill_action or ""),
            "last_buy_count": int(buy_count),
            "last_sell_count": int(sell_count),
            "last_note": str(note or ""),
            "last_source": str(source or "auto"),
        }
    )


def _shanghai_hm(now: Any = None) -> Tuple[int, int]:
    from core.signal.session_pit import shanghai_now

    n = shanghai_now(now)
    return int(n.hour), int(n.minute)


def resolve_session(now: Any = None) -> str:
    from core.market.calendar import resolve_session_date
    from core.signal.session_pit import shanghai_now

    return str(resolve_session_date(now=shanghai_now(now)) or "").strip()[:10]


def is_trading_session(now: Any = None) -> bool:
    from core.market.calendar import is_trading_day
    from core.signal.session_pit import shanghai_now

    n = shanghai_now(now)
    day = n.strftime("%Y-%m-%d")
    try:
        return bool(is_trading_day(day))
    except Exception:  # noqa: BLE001
        logger.debug("is_trading_day failed", exc_info=True)
        return n.weekday() < 5


def in_auto_rebalance_window(now: Any = None) -> bool:
    if not is_trading_session(now):
        return False
    hm = _shanghai_hm(now)
    return WINDOW_AFTER <= hm < WINDOW_UNTIL


def after_auto_rebalance_window(now: Any = None) -> bool:
    if not is_trading_session(now):
        return False
    return _shanghai_hm(now) >= WINDOW_UNTIL


def already_ran_today(now: Any = None) -> bool:
    sess = resolve_session(now)
    return bool(sess) and last_run_session() == sess


def t0_wait_for_rebalance(now: Any = None) -> Tuple[bool, str]:
    """自动调仓开启且今日尚未在窗口内跑完时，做 T 不新开腿。"""
    if not load_enabled_flag():
        return False, ""
    if already_ran_today(now):
        return False, ""
    if after_auto_rebalance_window(now):
        return False, ""
    return True, "等待调仓"


def should_record_follow_run(fill_action: str, now: Any = None) -> bool:
    """手动确认仅在开盘窗内现价成交时占今日自动调仓名额。"""
    action = str(fill_action or "").strip()
    if action not in ("immediate", ""):
        return False
    return in_auto_rebalance_window(now)


class RebalanceAutoWorker:
    """本 Web 进程内的后台 worker。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._enabled = False
        self._inflight = False
        self._runtime: Dict[str, Any] = {
            "started_at": None,
            "last_tick_ts": None,
            "last_tick_message": "",
            "last_error": None,
            "last_run_session": last_run_session() or None,
        }

    def _thread_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def is_running(self) -> bool:
        return self._thread_alive()

    def status(self) -> Dict[str, Any]:
        persisted = _load_state()
        with self._lock:
            rt = dict(self._runtime)
            alive = self._thread_alive()
            enabled = bool(self._enabled)
            last_sess = str(
                rt.get("last_run_session") or persisted.get("last_run_session") or ""
            ).strip()[:10]
            interval = TICK_INTERVAL_SEC if in_auto_rebalance_window() else IDLE_TICK_INTERVAL_SEC
            out: Dict[str, Any] = {
                "enabled": enabled,
                "running": alive,
                "thread_alive": alive,
                "started_at": rt.get("started_at"),
                "last_tick_ts": rt.get("last_tick_ts"),
                "last_tick_message": rt.get("last_tick_message") or "",
                "last_error": rt.get("last_error"),
                "last_run_session": last_sess or None,
                "last_run_ts": persisted.get("last_run_ts"),
                "last_buy_count": persisted.get("last_buy_count"),
                "last_sell_count": persisted.get("last_sell_count"),
                "last_note": persisted.get("last_note") or "",
                "last_source": persisted.get("last_source") or "",
                "in_window": in_auto_rebalance_window(),
                "tick_interval_sec": interval,
            }
            if alive and rt.get("last_tick_ts"):
                elapsed = _now_ts() - float(rt["last_tick_ts"])
                out["next_tick_in_sec"] = max(0, int(interval - elapsed))
            else:
                out["next_tick_in_sec"] = None
            out["inflight"] = bool(self._inflight)
            if self._inflight:
                out["state"] = "running"
            elif enabled and not alive:
                out["state"] = "starting"
            elif alive:
                out["state"] = "running"
            else:
                out["state"] = "stopped"
            return out

    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._enabled = True
                _save_state({"enabled": True})
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
                name="rebalance-auto-worker",
                daemon=True,
            )
            self._thread.start()
        _save_state({"enabled": True})
        return {"ok": True, "worker": self.status(), "message": "已启动后台进程"}

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            self._enabled = False
            self._stop.set()
            th = self._thread
        if th and th.is_alive():
            th.join(timeout=IDLE_TICK_INTERVAL_SEC + 5)
        with self._lock:
            self._thread = None
        _save_state({"enabled": False})
        return {"ok": True, "worker": self.status(), "message": "已停止后台进程"}

    def set_enabled(self, enabled: bool) -> Dict[str, Any]:
        if enabled:
            return self.start()
        return self.stop()

    def restore(self) -> None:
        if load_enabled_flag():
            self.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:  # noqa: BLE001
                logger.exception("rebalance auto worker tick failed")
                with self._lock:
                    self._runtime["last_error"] = str(e)
            wait = (
                TICK_INTERVAL_SEC
                if in_auto_rebalance_window()
                else IDLE_TICK_INTERVAL_SEC
            )
            self._stop.wait(wait)

    def _set_tick(self, message: str, *, error: Optional[str] = None, session: str = "") -> None:
        with self._lock:
            self._runtime["last_tick_ts"] = _now_ts()
            self._runtime["last_tick_message"] = message
            self._runtime["last_error"] = error
            if session:
                self._runtime["last_run_session"] = session

    def _tick(self) -> None:
        from core.paths import PAPER_PATH
        from services.paper_service import PaperService

        with self._lock:
            if not self._enabled:
                self._set_tick("后台进程已关闭")
                return
            if self._inflight:
                self._set_tick("执行中…")
                return

        sess = resolve_session()
        if already_ran_today():
            self._set_tick("今日已调仓", session=sess)
            return
        if not in_auto_rebalance_window():
            if after_auto_rebalance_window():
                self._set_tick("错过开盘窗", session=sess)
            else:
                self._set_tick("等待下一开盘", session=sess)
            return

        svc = PaperService(PAPER_PATH)
        try:
            svc.t0_auto_status()
        except FileNotFoundError:
            self._set_tick("纸面账户未初始化", session=sess)
            return

        pending_msg = ""
        leftover = False
        try:
            pending_fill = svc.fill_pending()
            if pending_fill.get("filled"):
                n = len(pending_fill.get("trades") or [])
                mode = str(pending_fill.get("mode") or "chase")
                pending_msg = f"挂单{mode}成交 {n}"
            paper = None
            try:
                from core.paper import load_paper

                paper = load_paper(PAPER_PATH)
            except Exception:  # noqa: BLE001
                logger.debug("load paper after fill_pending failed", exc_info=True)
            po = (paper or {}).get("pending_orders") if isinstance(paper, dict) else None
            leftover = bool(isinstance(po, dict) and (po.get("legs") or []))
        except Exception:  # noqa: BLE001
            logger.debug("rebalance worker fill_pending skipped", exc_info=True)

        if leftover:
            self._set_tick(
                (pending_msg + " · " if pending_msg else "") + "挂单未完成，本次未调仓",
                session=sess,
            )
            return

        with self._lock:
            self._inflight = True
        self._set_tick(
            (pending_msg + " · " if pending_msg else "") + "执行中…",
            session=sess,
        )
        try:
            out = svc.rebalance(dry_run=False, offline_only=False)
        except Exception as e:  # noqa: BLE001
            logger.exception("rebalance auto worker commit failed")
            self._set_tick(f"失败：{e}", error=str(e), session=sess)
            return
        finally:
            with self._lock:
                self._inflight = False

        if not (out.get("success") or out.get("ok")):
            err = str(out.get("error") or out.get("note") or "调仓失败")
            self._set_tick(f"失败：{err}", error=err, session=sess)
            return

        fill_action = str(out.get("fill_action") or "immediate")
        if fill_action in ("staged", "kept_pending", "open_fill_pending", "session_chase_pending"):
            note = str(out.get("note") or fill_action)
            self._set_tick(note, session=sess)
            return

        buys = len(out.get("buy_trades") or [])
        sells = len(out.get("sell_trades") or [])
        note = str(out.get("note") or f"落账 · 卖 {sells} · 买 {buys}")
        mark_run_session(
            sess,
            fill_action=fill_action or "immediate",
            buy_count=buys,
            sell_count=sells,
            note=note,
            source="auto",
        )
        try:
            from core.paper.rebalance.desk import persist_desk_from_result

            persist_desk_from_result(out, sess, source="auto", filled=True)
        except Exception:  # noqa: BLE001
            logger.debug("persist rebalance desk after auto fill failed", exc_info=True)
        self._set_tick(note, session=sess)


rebalance_auto_worker = RebalanceAutoWorker()
