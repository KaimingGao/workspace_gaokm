"""简单内存任务进度（Web 轮询用）+ 多槽 JobRegistry；paper 槽可落盘抗 reload。"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional


class JobProgress:
    def __init__(
        self,
        *,
        name: str = "default",
        persist_path: Optional[str] = None,
    ) -> None:
        self.name = name
        self._persist_path = persist_path
        self._lock = threading.Lock()
        self._job: Dict[str, Any] = self._idle()
        if persist_path:
            self._load()

    @staticmethod
    def _idle() -> Dict[str, Any]:
        return {
            "id": None,
            "name": None,
            "kind": None,
            "status": "idle",
            "current": 0,
            "total": 0,
            "pct": 0,
            "message": "",
            "error": None,
            "result": None,
            "updated_at": None,
        }

    def _load(self) -> None:
        path = self._persist_path
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            job = self._idle()
            for k in job:
                if k in data:
                    job[k] = data[k]
            # 热重载后进程已死：running 视为中断，避免前端空等
            if job.get("status") == "running":
                job["status"] = "failed"
                job["error"] = job.get("error") or "任务因服务重启中断，请重试"
                job["message"] = "已中断"
            self._job = job
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

    def _save_unlocked(self) -> None:
        path = self._persist_path
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            payload = dict(self._job)
            # 结果可能很大；落盘保留摘要字段，完整 result 仍在内存供同进程轮询
            result = payload.get("result")
            # paper 等可截断；研究分组报告须完整（FH2）
            if (
                self.name != "quant-ols-clusters"
                and isinstance(result, dict)
                and len(json.dumps(result, default=str)) > 80_000
            ):
                payload["result"] = {
                    "ok": result.get("ok", True),
                    "persisted_truncated": True,
                    "observation_pool_count": result.get("observation_pool_count"),
                    "new_trades": result.get("new_trades") or result.get("buy_trades"),
                    "sell_trades": result.get("sell_trades"),
                    "cash_impact": result.get("cash_impact"),
                    "risk_gate": result.get("risk_gate"),
                    "ops_report": result.get("ops_report"),
                    "rebalance_report": (result.get("rebalance_report") or [])[:30],
                }
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
            os.replace(tmp, path)
        except OSError:
            pass

    def get(self) -> Dict[str, Any]:
        with self._lock:
            out = dict(self._job)
            out["slot"] = self.name
            return out

    def start(self, *, kind: str, total: int = 0, message: str = "") -> str:
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._job = {
                "id": job_id,
                "name": self.name,
                "kind": kind,
                "status": "running",
                "current": 0,
                "total": max(0, int(total)),
                "pct": 0,
                "message": message or "启动中…",
                "error": None,
                "result": None,
                "cancel_requested": False,
                "updated_at": time.time(),
            }
            self._save_unlocked()
        return job_id

    def update(
        self,
        *,
        current: Optional[int] = None,
        total: Optional[int] = None,
        message: Optional[str] = None,
        pct: Optional[float] = None,
    ) -> None:
        with self._lock:
            if self._job.get("status") != "running":
                return
            if current is not None:
                self._job["current"] = int(current)
            if total is not None:
                self._job["total"] = max(0, int(total))
            if message is not None:
                self._job["message"] = message
            tot = int(self._job.get("total") or 0)
            cur = int(self._job.get("current") or 0)
            if pct is not None:
                self._job["pct"] = max(0.0, min(100.0, float(pct)))
            elif tot > 0:
                self._job["pct"] = round(100.0 * cur / tot, 1)
            self._job["updated_at"] = time.time()
            self._save_unlocked()

    def finish(self, *, result: Optional[dict] = None, error: Optional[str] = None) -> None:
        with self._lock:
            self._job["status"] = "failed" if error else "done"
            self._job["error"] = error
            self._job["result"] = result
            if not error:
                self._job["pct"] = 100.0
                if self._job.get("total"):
                    self._job["current"] = self._job["total"]
                self._job["message"] = self._job.get("message") or "完成"
            self._job["updated_at"] = time.time()
            self._save_unlocked()

    def is_running(self) -> bool:
        with self._lock:
            return self._job.get("status") == "running"

    def request_cancel(self) -> bool:
        """协作取消：标记 cancel_requested；worker 需轮询 ``is_cancel_requested``。"""
        with self._lock:
            if self._job.get("status") != "running":
                return False
            self._job["cancel_requested"] = True
            self._job["message"] = self._job.get("message") or "取消中…"
            self._job["updated_at"] = time.time()
            self._save_unlocked()
            return True

    def is_cancel_requested(self) -> bool:
        with self._lock:
            return bool(self._job.get("cancel_requested"))


class JobRegistry:
    """命名任务槽：paper / evals / schedule / generic。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._slots: Dict[str, JobProgress] = {}

    def slot(self, name: str, *, persist_path: Optional[str] = None) -> JobProgress:
        key = (name or "default").strip() or "default"
        with self._lock:
            if key not in self._slots:
                self._slots[key] = JobProgress(name=key, persist_path=persist_path)
            return self._slots[key]

    def list_jobs(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [s.get() for s in self._slots.values()]

    def get(self, name: str) -> Dict[str, Any]:
        return self.slot(name).get()


# 全局注册表；paper_job 保持兼容别名（落盘抗 uvicorn reload）
job_registry = JobRegistry()
try:
    from core.paths import PAPER_JOB_PATH, QUANT_OLS_CLUSTERS_JOB_PATH

    paper_job = job_registry.slot("paper", persist_path=PAPER_JOB_PATH)
    quant_ols_clusters_job = job_registry.slot(
        "quant-ols-clusters", persist_path=QUANT_OLS_CLUSTERS_JOB_PATH
    )
except Exception:
    paper_job = job_registry.slot("paper")
    quant_ols_clusters_job = job_registry.slot("quant-ols-clusters")
