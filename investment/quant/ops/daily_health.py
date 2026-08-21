"""Daily / quant 运维健康聚合（P17.3）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, Optional

from core.watching_health import check_watching_health
from quant.services.quant_report_index import list_quant_reports
from services.daily_service import DailyRunService


def build_daily_health(
    *,
    daily: Optional[DailyRunService] = None,
    report_limit: int = 5,
) -> Dict[str, Any]:
    svc = daily or DailyRunService()
    last = svc.load_last_run()
    watching = check_watching_health()
    reports = list_quant_reports(limit=report_limit)

    daily_ok = True if last.get("empty") else bool(last.get("ok"))
    issues = list(watching.get("issues") or [])
    if not last.get("empty") and not last.get("ok"):
        issues.append("最近一次 daily 任务存在失败步骤")

    warnings = list(watching.get("warnings") or [])
    latest_reports = reports.get("reports") or []

    return {
        "success": True,
        "ok": daily_ok and watching.get("success") and not issues,
        "daily_last": last,
        "watching": watching,
        "reports": {
            "count": reports.get("count"),
            "latest": latest_reports[:3],
            "reports_dir": reports.get("reports_dir"),
        },
        "issues": issues,
        "warnings": warnings,
    }
