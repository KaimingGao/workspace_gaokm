"""简单内存任务进度（Web 轮询用）+ 多槽 JobRegistry；paper 槽可落盘抗 reload。"""

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
    "bars-refresh": {
        "stale_sec": 480.0,
        "stuck_start_sec": 420.0,
        "label": "日线更新",
    },
    "minute-refresh": {
        "stale_sec": 900.0,
        "stuck_start_sec": 600.0,
        "label": "分钟线预热",
    },
    "quant-ols-clusters": {
        "stale_sec": 600.0,
        "stuck_start_sec": 360.0,
        "label": "分组任务",
    },
    # 满池 800 只建面板 + 双训常 >15min；stuck_start 过短会误杀。
    # stale_sec：心跳停了才按 updated_at 回收；须 ≥ stuck_start。
    "t30-ridge": {
        "stale_sec": 5400.0,
        "stuck_start_sec": 5400.0,
        "label": "ŷ_τ30 拟合",
    },
    "t45-ridge": {
        "stale_sec": 5400.0,
        "stuck_start_sec": 5400.0,
        "label": "ŷ_τ45 拟合",
    },
    "t60-ridge": {
        "stale_sec": 5400.0,
        "stuck_start_sec": 5400.0,
        "label": "ŷ_τ60 拟合",
    },
    "t75-ridge": {
        "stale_sec": 5400.0,
        "stuck_start_sec": 5400.0,
        "label": "ŷ_τ75 拟合",
    },
    "t90-ridge": {
        "stale_sec": 5400.0,
        "stuck_start_sec": 5400.0,
        "label": "ŷ_τ90 拟合",
    },
    "tau-ridge": {
        "stale_sec": 5400.0,
        "stuck_start_sec": 5400.0,
        # 拉日线阶段 current 常为 0；进度靠 message/心跳，勿按零进展误杀
        "allow_zero_progress": True,
        "label": "ŷ_τc 拟合",
    },
    "co-ridge": {
        "stale_sec": 5400.0,
        "stuck_start_sec": 5400.0,
        "label": "ŷ_co 拟合",
    },
    "t0-backtest": {
        "stale_sec": 5400.0,
        "stuck_start_sec": 5400.0,
        "label": "做 T 回测",
    },
    "portfolio-backtest": {
        "stale_sec": 5400.0,
        "stuck_start_sec": 5400.0,
        "label": "调仓回测",
    },
    "paper": {
        "stale_sec": 600.0,
        "stuck_start_sec": 300.0,
        "label": "纸面任务",
    },
    # 满池 L1 write + L2 DayCube：常 >10min；缺策略时走默认 300s，
    # 且 warmup 曾报 current=0 → zero_progress_stuck 误杀（「预热 Feature Lake」）。
    "materialize-l1l2": {
        "stale_sec": 5400.0,
        "stuck_start_sec": 5400.0,
        # L1 宏步 current 常钉在 0（子进度只改 message）；勿按零进展误杀
        "allow_zero_progress": True,
        "label": "物化",
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
            "started_at": None,
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
                try:
                    result_bytes = len(json.dumps(result, default=str))
                except (TypeError, ValueError):
                    result_bytes = 0
                if result_bytes > 80_000:
                    slot_name = str(self.name or "")
                    if slot_name == "t0-backtest":
                        payload["result"] = {
                            "success": result.get("success"),
                            "ok": result.get("success"),
                            "persisted_truncated": True,
                            "task": result.get("task") or "t0_backtest",
                            "ok_count": result.get("ok_count"),
                            "t0_pnl_total": result.get("t0_pnl_total"),
                        }
                    elif slot_name == "portfolio-backtest":
                        metrics = (
                            result.get("metrics")
                            if isinstance(result.get("metrics"), dict)
                            else {}
                        )
                        payload["result"] = {
                            "success": result.get("success"),
                            "ok": result.get("success"),
                            "persisted_truncated": True,
                            "task": result.get("task") or "portfolio_backtest",
                            "trade_count": metrics.get("trade_count")
                            or result.get("trade_count"),
                        }
                    elif slot_name.endswith("-ridge") or slot_name in (
                        "tau-ridge",
                        "co-ridge",
                    ):
                        # ŷ_* Ridge/Tree 大报告：勿裁成调仓 stub
                        oos = (
                            result.get("oos")
                            if isinstance(result.get("oos"), dict)
                            else {}
                        )
                        oos_slim = {
                            k: oos[k]
                            for k in (
                                "auc",
                                "brier",
                                "acc_at_50",
                                "n_test",
                                "holdout_trading_days",
                            )
                            if k in oos
                        }
                        payload["result"] = {
                            "success": result.get("success"),
                            "ok": bool(result.get("success")),
                            "persisted_truncated": True,
                            "task": result.get("task") or slot_name.replace("-", "_"),
                            "schema": result.get("schema"),
                            "recommended_backend": result.get("recommended_backend")
                            or result.get("backend"),
                            "oos": oos_slim or None,
                            "error": result.get("error"),
                            "watching_limit": result.get("watching_limit"),
                            "minute_codes_hit": result.get("minute_codes_hit"),
                        }
                    elif slot_name == "minute-refresh":
                        # 丢弃庞大 status；保留 minute_warmup 摘要供 UI 完成态
                        mw = (
                            result.get("minute_warmup")
                            if isinstance(result.get("minute_warmup"), dict)
                            else {}
                        )
                        mw_slim = {
                            k: mw[k]
                            for k in (
                                "ok",
                                "mode",
                                "kind",
                                "warmed",
                                "total",
                                "skipped_today",
                                "skipped_aligned",
                                "skipped_ready",
                                "topped",
                                "bootstrapped",
                                "timed_out",
                                "error",
                            )
                            if k in mw
                        }
                        payload["result"] = {
                            "success": result.get("success"),
                            "ok": bool(result.get("success")),
                            "persisted_truncated": True,
                            "task": result.get("task") or "minute_refresh",
                            "mode": result.get("mode"),
                            "watching_limit": result.get("watching_limit"),
                            "universe_count": result.get("universe_count"),
                            "period": result.get("period"),
                            "minute_warmup": mw_slim or None,
                            "error": result.get("error"),
                        }
                    elif slot_name == "bars-refresh":
                        br = (
                            result.get("bars_refresh")
                            if isinstance(result.get("bars_refresh"), dict)
                            else {}
                        )
                        payload["result"] = {
                            "success": result.get("success"),
                            "ok": bool(result.get("success")),
                            "persisted_truncated": True,
                            "task": result.get("task") or "bars_refresh",
                            "mode": result.get("mode"),
                            "watching_limit": result.get("watching_limit"),
                            "universe_count": result.get("universe_count"),
                            "lookback": result.get("lookback"),
                            "bars_refresh": br or None,
                            "error": result.get("error"),
                        }
                    elif slot_name == "chat":
                        reply = str(result.get("reply") or "")
                        if len(reply) > 2000:
                            reply = reply[:2000] + "…"
                        arts = result.get("artifacts")
                        payload["result"] = {
                            "ok": result.get("ok", True),
                            "persisted_truncated": True,
                            "reply": reply,
                            "session_id": result.get("session_id"),
                            "primary_tab": result.get("primary_tab"),
                            "artifacts_count": len(arts)
                            if isinstance(arts, list)
                            else 0,
                        }
                    elif slot_name == "quant-ols-clusters":
                        payload["result"] = {
                            "success": result.get("success"),
                            "ok": result.get("ok", result.get("success")),
                            "persisted_truncated": True,
                            "task": result.get("task") or "quant_ols_clusters",
                            "n_clusters": result.get("n_clusters"),
                            "fitted_count": result.get("fitted_count"),
                            "stock_count": result.get("stock_count"),
                            "universe_count": result.get("universe_count"),
                            "cache_hit": result.get("cache_hit"),
                            "oos_summary": result.get("oos_summary"),
                            "error": result.get("error"),
                        }
                    elif slot_name == "paper":
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
                    else:
                        payload["result"] = {
                            "success": result.get("success"),
                            "ok": result.get("ok", result.get("success")),
                            "persisted_truncated": True,
                            "task": result.get("task")
                            or slot_name.replace("-", "_"),
                            "error": result.get("error"),
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
                "allow_zero_progress": bool(policy.get("allow_zero_progress")),
                "label": str(policy.get("label") or "任务"),
            }
            out["persisted"] = bool(self._persist_path)
            # 分组 Job hydrate 已退役（cluster_retired）
            return out

    def policy(self) -> Dict[str, Any]:
        p = _SLOT_STALE_POLICY.get(self.name, _DEFAULT_STALE_POLICY)
        return {
            "slot": self.name,
            "stale_sec": float(p["stale_sec"]),
            "stuck_start_sec": float(p["stuck_start_sec"]),
            "allow_zero_progress": bool(p.get("allow_zero_progress")),
            "label": str(p.get("label") or "任务"),
            "persisted": bool(self._persist_path),
        }

    def start(self, *, kind: str, total: int = 0, message: str = "") -> str:
        job_id = uuid.uuid4().hex[:12]
        now = time.time()
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
                "started_at": now,
                "updated_at": now,
            }
            self._save_unlocked()
        return job_id

    def try_start(
        self, *, kind: str, total: int = 0, message: str = ""
    ) -> tuple[Optional[str], Dict[str, Any]]:
        """原子：仅 idle/非 running 时开槽。成功 ``(job_id, snap)``，冲突 ``(None, running_snap)``。"""
        now = time.time()
        with self._lock:
            if self._job.get("status") == "running":
                return None, dict(self._job)
            job_id = uuid.uuid4().hex[:12]
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
                "started_at": now,
                "updated_at": now,
            }
            self._save_unlocked()
            return job_id, dict(self._job)

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
                or msg.startswith("回测入队")
            )
            threshold = stuck_start_sec if stuck_at_start else stale_sec
            # 心跳会刷 updated_at；进度长期 0 时改看 started_at，避免假活锁槽
            # allow_zero_progress：宏步钉 current（如物化 L1）时只按心跳 stale 回收
            cur = int(self._job.get("current") or 0)
            try:
                started_age = max(
                    0.0, time.time() - float(self._job.get("started_at") or 0.0)
                )
            except (TypeError, ValueError):
                started_age = stale
            allow_zp = bool(policy.get("allow_zero_progress"))
            zero_progress_stuck = (
                (not allow_zp) and cur <= 0 and started_age >= stuck_start_sec
            )
            if stale < threshold and not zero_progress_stuck:
                return False
            age_show = int(started_age if zero_progress_stuck else stale)
            self._job["cancel_requested"] = True
            self._job["status"] = "failed"
            self._job["error"] = (
                f"{label}无进展已 {age_show}s，已自动释放（{msg or 'running'}）"
            )
            self._job["message"] = "已中断"
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
            "t30-ridge",
            "t45-ridge",
            "t60-ridge",
            "t75-ridge",
            "t90-ridge",
            "tau-ridge",
            "co-ridge",
            "t0-backtest",
            "portfolio-backtest",
            "materialize-l1l2",
        ):
            self.slot(name)
        with self._lock:
            slots = list(self._slots.values())
        return [s.policy() for s in slots]

# 全局注册表；paper / ols / chat 落盘抗 uvicorn reload
job_registry = JobRegistry()
try:
    from core.paths import (
        CHAT_JOB_PATH,
        BARS_REFRESH_JOB_PATH,
        MATERIALIZE_JOB_PATH,
        MINUTE_REFRESH_JOB_PATH,
        PAPER_JOB_PATH,
        QUANT_OLS_CLUSTERS_JOB_PATH,
        T30_RIDGE_JOB_PATH,
        T45_RIDGE_JOB_PATH,
        T60_RIDGE_JOB_PATH,
        T75_RIDGE_JOB_PATH,
        T90_RIDGE_JOB_PATH,
        TAU_RIDGE_JOB_PATH,
        CO_RIDGE_JOB_PATH,
        T0_BACKTEST_JOB_PATH,
        PORTFOLIO_BACKTEST_JOB_PATH,
    )

    paper_job = job_registry.slot("paper", persist_path=PAPER_JOB_PATH)
    quant_ols_clusters_job = job_registry.slot(
        "quant-ols-clusters", persist_path=QUANT_OLS_CLUSTERS_JOB_PATH
    )
    bars_refresh_job = job_registry.slot(
        "bars-refresh", persist_path=BARS_REFRESH_JOB_PATH
    )
    materialize_job = job_registry.slot(
        "materialize-l1l2", persist_path=MATERIALIZE_JOB_PATH
    )
    minute_refresh_job = job_registry.slot(
        "minute-refresh", persist_path=MINUTE_REFRESH_JOB_PATH
    )
    chat_job = job_registry.slot("chat", persist_path=CHAT_JOB_PATH)
    t30_ridge_job = job_registry.slot("t30-ridge", persist_path=T30_RIDGE_JOB_PATH)
    t45_ridge_job = job_registry.slot("t45-ridge", persist_path=T45_RIDGE_JOB_PATH)
    t60_ridge_job = job_registry.slot("t60-ridge", persist_path=T60_RIDGE_JOB_PATH)
    t75_ridge_job = job_registry.slot("t75-ridge", persist_path=T75_RIDGE_JOB_PATH)
    t90_ridge_job = job_registry.slot("t90-ridge", persist_path=T90_RIDGE_JOB_PATH)
    tau_ridge_job = job_registry.slot("tau-ridge", persist_path=TAU_RIDGE_JOB_PATH)
    co_ridge_job = job_registry.slot("co-ridge", persist_path=CO_RIDGE_JOB_PATH)
    t0_backtest_job = job_registry.slot("t0-backtest", persist_path=T0_BACKTEST_JOB_PATH)
    portfolio_backtest_job = job_registry.slot(
        "portfolio-backtest", persist_path=PORTFOLIO_BACKTEST_JOB_PATH
    )
except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
    paper_job = job_registry.slot("paper")
    quant_ols_clusters_job = job_registry.slot("quant-ols-clusters")
    bars_refresh_job = job_registry.slot("bars-refresh")
    materialize_job = job_registry.slot("materialize-l1l2")
    minute_refresh_job = job_registry.slot("minute-refresh")
    chat_job = job_registry.slot("chat")
    t30_ridge_job = job_registry.slot("t30-ridge")
    t45_ridge_job = job_registry.slot("t45-ridge")
    t60_ridge_job = job_registry.slot("t60-ridge")
    t75_ridge_job = job_registry.slot("t75-ridge")
    t90_ridge_job = job_registry.slot("t90-ridge")
    tau_ridge_job = job_registry.slot("tau-ridge")
    co_ridge_job = job_registry.slot("co-ridge")
    t0_backtest_job = job_registry.slot("t0-backtest")
    portfolio_backtest_job = job_registry.slot("portfolio-backtest")
