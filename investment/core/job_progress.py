"""简单内存任务进度（Web 轮询用）+ 多槽 JobRegistry；paper 槽可落盘抗 reload。"""


import logging

logger = logging.getLogger(__name__)
import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from core.io_atomic import atomic_write_json

_SLOT_STALE_POLICY: Dict[str, Dict[str, Any]] = {
    # 对话：多轮 tool + 联网 LLM 常 >5min；轮询走 /api/jobs/chat 也会 reclaim
    "chat": {
        "stale_sec": 1200.0,
        "stuck_start_sec": 600.0,
        "label": "对话任务",
    },
    "quant-ols-clusters": {
        "stale_sec": 600.0,
        "stuck_start_sec": 360.0,
        "label": "分组任务",
    },
    "quant-param-grid": {
        "stale_sec": 600.0,
        "stuck_start_sec": 360.0,
        "label": "参数网格任务",
    },
    "paper": {
        "stale_sec": 600.0,
        "stuck_start_sec": 300.0,
        "label": "纸面任务",
    },
}
_DEFAULT_STALE_POLICY = {
    "stale_sec": 300.0,
    "stuck_start_sec": 240.0,
    "label": "任务",
}


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
            "cancel_requested": False,
            "updated_at": None,
        }

    def _load(self) -> None:
        path = self._persist_path
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
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
            if isinstance(result, dict):
                # 研究分组报告：有 clusters 即视为大包，勿对整包 json.dumps 估大小（持锁会卡轮询）
                if self.name == "quant-ols-clusters" and (
                    result.get("clusters") is not None
                    or result.get("pool_artifact") is not None
                    or result.get("persisted_truncated")
                ):
                    payload["result"] = {
                        "success": result.get("success"),
                        "ok": result.get("ok", result.get("success")),
                        "persisted_truncated": True,
                        "task": result.get("task"),
                        "n_clusters": result.get("n_clusters"),
                        "fitted_count": result.get("fitted_count"),
                        "stock_count": result.get("stock_count"),
                        "universe_count": result.get("universe_count"),
                        "cache_hit": result.get("cache_hit"),
                        "oos_summary": result.get("oos_summary"),
                        "error": result.get("error"),
                    }
                elif self.name == "quant-param-grid":
                    # 网格结果本身很小（无净值曲线）；热重载后仍要能画出热力
                    pass
                elif self.name != "quant-ols-clusters":
                    try:
                        result_bytes = len(json.dumps(result, default=str))
                    except (TypeError, ValueError):
                        result_bytes = 0
                    if result_bytes > 80_000:
                        payload["result"] = {
                            "ok": result.get("ok", True),
                            "persisted_truncated": True,
                            "observation_pool_count": result.get(
                                "observation_pool_count"
                            ),
                            "new_trades": result.get("new_trades")
                            or result.get("buy_trades"),
                            "sell_trades": result.get("sell_trades"),
                            "cash_impact": result.get("cash_impact"),
                            "risk_gate": result.get("risk_gate"),
                            "ops_report": result.get("ops_report"),
                            "rebalance_report": (
                                result.get("rebalance_report") or []
                            )[:30],
                        }
            atomic_write_json(path, payload)
        except OSError:
            pass

    def get(self) -> Dict[str, Any]:
        with self._lock:
            out = dict(self._job)
            out["slot"] = self.name
            policy = _SLOT_STALE_POLICY.get(self.name, _DEFAULT_STALE_POLICY)
            out["stale_policy"] = {
                "stale_sec": float(policy["stale_sec"]),
                "stuck_start_sec": float(policy["stuck_start_sec"]),
                "label": str(policy.get("label") or "任务"),
            }
            out["persisted"] = bool(self._persist_path)
            # 分组 Job：落盘摘要缺 clusters 时从报告缓存水合（热重载后仍可渲染）
            if self.name == "quant-ols-clusters":
                result = out.get("result")
                if isinstance(result, dict) and not (
                    isinstance(result.get("clusters"), list)
                    and len(result.get("clusters") or []) > 0
                ):
                    try:
                        from core.signal.cluster.job_hydrate import (
                            hydrate_ols_clusters_job_result,
                        )

                        hydrated = hydrate_ols_clusters_job_result(result)
                        if (
                            isinstance(hydrated, dict)
                            and isinstance(hydrated.get("clusters"), list)
                            and hydrated.get("clusters")
                        ):
                            out["result"] = hydrated
                            # 回填内存，避免每次 get 都读盘
                            self._job["result"] = hydrated
                    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                        logger.debug("catch except Exception: in job_progress.py", exc_info=True)
                        pass
            return out

    def policy(self) -> Dict[str, Any]:
        p = _SLOT_STALE_POLICY.get(self.name, _DEFAULT_STALE_POLICY)
        return {
            "slot": self.name,
            "stale_sec": float(p["stale_sec"]),
            "stuck_start_sec": float(p["stuck_start_sec"]),
            "label": str(p.get("label") or "任务"),
            "persisted": bool(self._persist_path),
        }

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
        job_id: Optional[str] = None,
    ) -> None:
        with self._lock:
            if job_id is not None and self._job.get("id") != job_id:
                return
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

    def touch(self, *, job_id: Optional[str] = None) -> bool:
        """仅刷新 updated_at（心跳），不改 message/进度。"""
        with self._lock:
            if job_id is not None and self._job.get("id") != job_id:
                return False
            if self._job.get("status") != "running":
                return False
            self._job["updated_at"] = time.time()
            self._save_unlocked()
            return True

    def finish(
        self,
        *,
        result: Optional[dict] = None,
        error: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> bool:
        """结束任务。``job_id`` 不匹配时忽略（防孤儿 worker 覆盖新任务）。"""
        with self._lock:
            if job_id is not None and self._job.get("id") != job_id:
                return False
            self._job["status"] = "failed" if error else "done"
            self._job["error"] = error
            # Starlette JSONResponse allow_nan=False；OLS 指标常含 ±inf/NaN
            if isinstance(result, dict):
                try:
                    from core.signal.score_display import json_safe

                    result = json_safe(result)
                except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                    logger.debug("catch except Exception: in job_progress.py", exc_info=True)
                    pass
            self._job["result"] = result
            if not error:
                self._job["pct"] = 100.0
                if self._job.get("total"):
                    self._job["current"] = self._job["total"]
                self._job["message"] = self._job.get("message") or "完成"
            self._job["updated_at"] = time.time()
            self._save_unlocked()
            return True

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

    def force_fail(self, error: str = "已强制结束") -> bool:
        """立即标失败（卡住的 worker 仍可能在跑，但槽位可重新开跑）。"""
        with self._lock:
            if self._job.get("status") != "running":
                return False
            self._job["cancel_requested"] = True
            self._job["status"] = "failed"
            self._job["error"] = error or "已强制结束"
            self._job["message"] = "已中断"
            self._job["updated_at"] = time.time()
            self._save_unlocked()
            return True

    def reclaim_if_stale(
        self,
        *,
        stale_sec: Optional[float] = None,
        stuck_start_sec: Optional[float] = None,
    ) -> bool:
        """轮询路径：无进展过久则 force_fail，释放槽位。

        Limit=100 时起点阶段（合并宇宙/拉日线 0）可能数十秒无 done 递增，
        但 touch/心跳会刷新 updated_at；仅当 updated_at 本身过旧才回收。
        """
        policy = _SLOT_STALE_POLICY.get(self.name, _DEFAULT_STALE_POLICY)
        stale_sec = float(
            policy["stale_sec"] if stale_sec is None else stale_sec
        )
        stuck_start_sec = float(
            policy["stuck_start_sec"]
            if stuck_start_sec is None
            else stuck_start_sec
        )
        label = str(policy.get("label") or "任务")
        with self._lock:
            if self._job.get("status") != "running":
                return False
            ts = self._job.get("updated_at")
            try:
                stale = max(0.0, time.time() - float(ts))
            except (TypeError, ValueError):
                stale = stale_sec
            msg = str(self._job.get("message") or "")
            # 有计时/远端/心跳字样 → 长阈值；纯起点文案 → 稍短但仍 ≥4min（满池）
            heartbeating = (
                "远端" in msg
                or "拉指数" in msg
                or "心跳" in msg
                or "思考" in msg
                or "·" in msg
                or "s）" in msg
                or "s)" in msg
            )
            stuck_at_start = (not heartbeating) and (
                "拉日线 0/" in msg
                or msg.startswith("排队")
                or msg.startswith("启动")
                or msg.startswith("合并宇宙")
            )
            threshold = stuck_start_sec if stuck_at_start else stale_sec
            if stale < threshold:
                return False
            self._job["cancel_requested"] = True
            self._job["status"] = "failed"
            self._job["error"] = (
                f"{label}无进展已 {int(stale)}s，已自动释放（{msg or 'running'}）"
            )
            self._job["message"] = "已中断"
            self._job["updated_at"] = time.time()
            self._save_unlocked()
            return True

    def stale_seconds(self) -> Optional[float]:
        with self._lock:
            if self._job.get("status") != "running":
                return None
            ts = self._job.get("updated_at")
            try:
                return max(0.0, time.time() - float(ts))
            except (TypeError, ValueError):
                return None

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

    def reclaim_all_stale(self) -> List[str]:
        """对已注册槽统一跑 reclaim；返回被回收的槽名。"""
        reclaimed: List[str] = []
        with self._lock:
            names = list(self._slots.keys())
        for name in names:
            slot = self.slot(name)
            if slot.reclaim_if_stale():
                reclaimed.append(name)
        return reclaimed

    def list_policies(self) -> List[Dict[str, Any]]:
        with self._lock:
            slots = list(self._slots.values())
        # 保证长任务槽出现在策略表（即便尚未 start）
        for name in (
            "paper",
            "chat",
            "quant-ols-clusters",
            "quant-param-grid",
        ):
            self.slot(name)
        with self._lock:
            slots = list(self._slots.values())
        return [s.policy() for s in slots]


# 全局注册表；paper / ols / param-grid / chat 落盘抗 uvicorn reload
job_registry = JobRegistry()
try:
    from core.paths import (
        CHAT_JOB_PATH,
        CLUSTER_BARS_REFRESH_JOB_PATH,
        PAPER_JOB_PATH,
        QUANT_OLS_CLUSTERS_JOB_PATH,
        QUANT_PARAM_GRID_JOB_PATH,
    )

    paper_job = job_registry.slot("paper", persist_path=PAPER_JOB_PATH)
    quant_ols_clusters_job = job_registry.slot(
        "quant-ols-clusters", persist_path=QUANT_OLS_CLUSTERS_JOB_PATH
    )
    cluster_bars_refresh_job = job_registry.slot(
        "cluster-bars-refresh", persist_path=CLUSTER_BARS_REFRESH_JOB_PATH
    )
    quant_param_grid_job = job_registry.slot(
        "quant-param-grid", persist_path=QUANT_PARAM_GRID_JOB_PATH
    )
    chat_job = job_registry.slot("chat", persist_path=CHAT_JOB_PATH)
except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
    logger.debug("catch except Exception: in job_progress.py", exc_info=True)
    paper_job = job_registry.slot("paper")
    quant_ols_clusters_job = job_registry.slot("quant-ols-clusters")
    cluster_bars_refresh_job = job_registry.slot("cluster-bars-refresh")
    quant_param_grid_job = job_registry.slot("quant-param-grid")
    chat_job = job_registry.slot("chat")
