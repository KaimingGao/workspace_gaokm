"""W2.5 · 审计时间线聚合（决策 / 调度 / 告警 / promote）。"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List

from core.alert_outbound import ALERTS_LAST_PATH
from core.paths import SCHEDULE_LAST_RUN_PATH
from core.strategy import load_promoted

logger = logging.getLogger(__name__)


def _ts_key(raw: Any) -> str:
    """Unix 秒/毫秒与数字字符串 → 本地 ISO，便于混源排序。"""
    if raw is None or raw == "":
        return ""
    if isinstance(raw, (int, float)):
        n = float(raw)
        try:
            if n > 1e12:
                n = n / 1000.0
            if n > 1e9:
                return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(n))
        except (OSError, ValueError, OverflowError):
            return str(raw)
        return str(raw)
    s = str(raw).strip()
    if not s:
        return ""
    try:
        n = float(s)
        if n > 1e9:
            if n > 1e12:
                n = n / 1000.0
            return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(n))
    except (ValueError, OSError, OverflowError):
        pass
    return s


def _event(
    *,
    kind: str,
    ts: Any,
    title: str,
    detail: str = "",
    href: str = "",
) -> Dict[str, Any]:
    ev: Dict[str, Any] = {
        "kind": kind,
        "ts": _ts_key(ts),
        "title": title,
        "detail": detail or "",
    }
    if href:
        ev["href"] = href
    return ev


def _append_schedule_from_file(events: List[Dict[str, Any]]) -> None:
    """调度服务失败时，降级读落盘 last_run。"""
    if not os.path.isfile(SCHEDULE_LAST_RUN_PATH):
        return
    try:
        with open(SCHEDULE_LAST_RUN_PATH, encoding="utf-8") as f:
            last = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.info("audit timeline: schedule file unreadable: %s", exc)
        return
    if not isinstance(last, dict):
        return
    events.append(
        _event(
            kind="schedule",
            ts=last.get("finished_at") or last.get("ts") or "",
            title=f"调度 {last.get('kind') or 'job'}",
            detail=last.get("note") or last.get("error") or "",
            href="/platform#platform-schedule-section",
        )
    )


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
                _event(
                    kind="decision",
                    ts=d.get("ts") or d.get("created_at") or "",
                    title=(
                        f"{d.get('stock_name') or d.get('stock_code') or '—'} · "
                        f"{d.get('stance_label') or '决策'}"
                    ),
                    detail=f"{d.get('source') or ''} · {d.get('id') or ''}".strip(" ·"),
                )
            )
    except Exception as e:
        logger.exception("audit timeline: decisions unavailable")
        events.append(
            _event(
                kind="error",
                ts="",
                title="决策记录读取失败",
                detail=str(e),
            )
        )

    try:
        from services.platform_service import PlatformService

        last_wrap = PlatformService().get_schedule_last()
        last = (last_wrap or {}).get("last") or (
            None if (last_wrap or {}).get("empty") else last_wrap
        )
        if last and isinstance(last, dict):
            events.append(
                _event(
                    kind="schedule",
                    ts=last.get("finished_at") or last.get("ts") or "",
                    title=f"调度 {last.get('kind') or 'job'}"
                    + (" · 失败" if last.get("ok") is False else " · 完成"),
                    detail=f"策略 {last.get('strategy_id') or '—'}"
                    + (
                        f" · 告警 {len(last.get('monitor_alerts') or [])}"
                        if last.get("monitor_alerts")
                        else ""
                    ),
                    href="/platform#platform-schedule-section",
                )
            )
    except Exception as exc:
        logger.info("audit timeline: schedule via service failed: %s", exc)
        _append_schedule_from_file(events)

    try:
        if os.path.isfile(ALERTS_LAST_PATH):
            with open(ALERTS_LAST_PATH, encoding="utf-8") as f:
                alert = json.load(f)
            events.append(
                _event(
                    kind="alert",
                    ts=alert.get("ts"),
                    title=(
                        f"出站告警 ×"
                        f"{alert.get('alert_count') or len(alert.get('alerts') or [])}"
                    ),
                    detail=alert.get("source") or alert.get("path") or "",
                    href="/follow",
                )
            )
    except (OSError, json.JSONDecodeError, TypeError, AttributeError) as exc:
        logger.info("audit timeline: alerts last skipped: %s", exc)

    try:
        promo = load_promoted()
        if promo:
            spec = promo.get("spec") or {}
            events.append(
                _event(
                    kind="promote",
                    ts=promo.get("promoted_at") or "",
                    title=(
                        f"策略晋升 {spec.get('strategy_id') or '—'}@"
                        f"{spec.get('version') or '—'}"
                    ),
                    detail=promo.get("note") or "",
                    href="/strategy",
                )
            )
    except (OSError, TypeError, AttributeError, ValueError) as exc:
        logger.info("audit timeline: promote skipped: %s", exc)

    events.sort(key=lambda ev: str(ev.get("ts") or ""), reverse=True)
    return {
        "success": True,
        "items": events[:lim],
        "count": min(len(events), lim),
        "note": "只读审计聚合 · 不代客下单",
    }


def clear_audit_timeline_logs() -> Dict[str, Any]:
    """清空时间线日志源：DecisionRecord + 最近告警快照。

    不删 ``strategy_promoted.json``（生产态）与 ``schedule_last_run.json``（调度块在用）。
    历史 ``data/alerts/alert_*.json`` 归档文件保留。
    """
    from core.decision_record import clear_decisions

    dec = clear_decisions()
    alerts_last_cleared = False
    if os.path.isfile(ALERTS_LAST_PATH):
        try:
            os.remove(ALERTS_LAST_PATH)
            alerts_last_cleared = True
        except OSError as exc:
            logger.exception("audit timeline: alerts_last remove failed")
            return {
                "success": False,
                "ok": False,
                "error": str(exc),
                "decisions_cleared": int(dec.get("cleared") or 0),
                "alerts_last_cleared": False,
            }
    return {
        "success": True,
        "ok": True,
        "decisions_cleared": int(dec.get("cleared") or 0),
        "alerts_last_cleared": alerts_last_cleared,
        "kept": ["promote", "schedule_last_run"],
        "note": "已清空 DecisionRecord 与最近出站告警快照；策略晋升与调度 last-run 未改。",
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
    except (OSError, json.JSONDecodeError) as e:
        logger.exception("read_alerts_last failed")
        return {"success": False, "error": str(e), "alerts": [], "alert_count": 0}
