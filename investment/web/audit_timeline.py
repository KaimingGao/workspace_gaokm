"""W2.5 · 审计时间线聚合（决策 / 调度 / 告警 / promote）。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
import json
import os
import time
from typing import Any, Dict, List

from core.alert_outbound import ALERTS_LAST_PATH
from core.paths import DATA_DIR, SCHEDULE_LAST_RUN_PATH
from core.strategy import load_promoted


def _ts_key(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, (int, float)):
        try:
            return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(float(raw)))
        except (OSError, ValueError, OverflowError):
            return str(raw)
    return str(raw)


def build_audit_timeline(*, limit: int = 40) -> Dict[str, Any]:
    """只读聚合；不写盘、不代客下单。"""
    lim = max(1, min(int(limit or 40), 100))
    events: List[Dict[str, Any]] = []

    try:
        from services.platform_service import PlatformService

        plat = PlatformService()
        dec = plat.list_decisions(limit=min(lim, 30))
        for d in dec.get("items") or []:
            events.append(
                {
                    "kind": "decision",
                    "ts": d.get("ts") or d.get("created_at") or d.get("id") or "",
                    "title": f"{d.get('stock_name') or d.get('stock_code') or '—'} · {d.get('stance_label') or '决策'}",
                    "detail": f"{d.get('source') or ''} · {d.get('id') or ''}".strip(" ·"),
                    "href": "/platform",
                }
            )
    except Exception as e:
        events.append(
            {
                "kind": "error",
                "ts": "",
                "title": "决策记录读取失败",
                "detail": str(e),
                "href": "/platform",
            }
        )

    try:
        from services.platform_service import PlatformService

        last_wrap = PlatformService().get_schedule_last()
        last = (last_wrap or {}).get("last") or (
            None if (last_wrap or {}).get("empty") else last_wrap
        )
        if last and isinstance(last, dict):
            events.append(
                {
                    "kind": "schedule",
                    "ts": last.get("finished_at") or last.get("ts") or "",
                    "title": f"调度 {last.get('kind') or 'job'}"
                    + (" · 失败" if last.get("ok") is False else " · 完成"),
                    "detail": f"策略 {last.get('strategy_id') or '—'}"
                    + (
                        f" · 告警 {len(last.get('monitor_alerts') or [])}"
                        if last.get("monitor_alerts")
                        else ""
                    ),
                    "href": "/platform",
                }
            )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in audit_timeline.py", exc_info=True)
        try:
            if os.path.isfile(SCHEDULE_LAST_RUN_PATH):
                with open(SCHEDULE_LAST_RUN_PATH, encoding="utf-8") as f:
                    last = json.load(f)
                events.append(
                    {
                        "kind": "schedule",
                        "ts": last.get("finished_at") or last.get("ts") or "",
                        "title": f"调度 {last.get('kind') or 'job'}",
                        "detail": last.get("note") or last.get("error") or "",
                        "href": "/platform",
                    }
                )
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in audit_timeline.py", exc_info=True)
            pass

    try:
        if os.path.isfile(ALERTS_LAST_PATH):
            with open(ALERTS_LAST_PATH, encoding="utf-8") as f:
                alert = json.load(f)
            events.append(
                {
                    "kind": "alert",
                    "ts": _ts_key(alert.get("ts")),
                    "title": f"出站告警 ×{alert.get('alert_count') or len(alert.get('alerts') or [])}",
                    "detail": alert.get("source") or alert.get("path") or "",
                    "href": "/follow",
                }
            )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in audit_timeline.py", exc_info=True)
        pass

    try:
        promo = load_promoted()
        if promo:
            spec = promo.get("spec") or {}
            events.append(
                {
                    "kind": "promote",
                    "ts": promo.get("promoted_at") or "",
                    "title": f"策略晋升 {spec.get('strategy_id') or '—'}@{spec.get('version') or '—'}",
                    "detail": promo.get("note") or "",
                    "href": "/strategy",
                }
            )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in audit_timeline.py", exc_info=True)
        pass

    events.sort(key=lambda ev: str(ev.get("ts") or ""), reverse=True)
    return {
        "success": True,
        "items": events[:lim],
        "count": min(len(events), lim),
        "note": "只读审计聚合 · 不代客下单",
    }


def read_alerts_last() -> Dict[str, Any]:
    if not os.path.isfile(ALERTS_LAST_PATH):
        return {"success": True, "empty": True, "alerts": [], "alert_count": 0}
    try:
        with open(ALERTS_LAST_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {
            "success": True,
            "empty": False,
            "ts": data.get("ts"),
            "source": data.get("source"),
            "alert_count": data.get("alert_count") or len(data.get("alerts") or []),
            "alerts": data.get("alerts") or [],
            "path": data.get("path"),
        }
    except Exception as e:
        return {"success": False, "error": str(e), "alerts": [], "alert_count": 0}
