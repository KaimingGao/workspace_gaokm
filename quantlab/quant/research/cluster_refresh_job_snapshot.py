"""研究枢纽日线/分钟强更 Job 的公开快照（status API + Web 恢复进度）。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.job_progress import JobProgress


def public_refresh_job_snapshot(
    slot: JobProgress,
    *,
    result_summary_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """返回非 idle 任务的轻量快照；done 时可带 ``result_summary``。"""
    job = slot.get()
    if not isinstance(job, dict):
        return None
    status = str(job.get("status") or "idle")
    if status == "idle":
        return None
    snap: Dict[str, Any] = {
        "id": job.get("id"),
        "name": job.get("name"),
        "kind": job.get("kind"),
        "status": status,
        "current": job.get("current"),
        "total": job.get("total"),
        "pct": job.get("pct"),
        "message": job.get("message"),
        "error": job.get("error"),
        "started_at": job.get("started_at"),
        "updated_at": job.get("updated_at"),
    }
    if status == "done" and result_summary_key:
        res = job.get("result")
        if isinstance(res, dict) and isinstance(res.get(result_summary_key), dict):
            snap["result_summary"] = res[result_summary_key]
    return snap
