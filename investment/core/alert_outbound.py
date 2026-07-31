"""监控告警出站（本地文件 + 可选 Webhook）。不代客下单、不自动改权。"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from core.paths import DATA_DIR

ALERTS_DIR = os.path.join(DATA_DIR, "alerts")
ALERTS_LAST_PATH = os.path.join(DATA_DIR, "alerts_last.json")


def _ensure_dir() -> None:
    os.makedirs(ALERTS_DIR, exist_ok=True)


def write_alert_file(payload: Dict[str, Any]) -> str:
    _ensure_dir()
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(ALERTS_DIR, f"alert_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    try:
        with open(ALERTS_LAST_PATH, "w", encoding="utf-8") as f:
            json.dump({**payload, "path": path}, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return path


def post_webhook(url: str, payload: Dict[str, Any], *, timeout: float = 8.0) -> Dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"ok": True, "status": getattr(resp, "status", 200)}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def dispatch_monitor_alerts(
    alerts: Optional[List[Any]] = None,
    *,
    source: str = "strategy_monitor",
    extra: Optional[Dict[str, Any]] = None,
    webhook_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    写出告警文件；若 INVESTMENT_ALERT_WEBHOOK 或 webhook_url 有值则 POST。
    """
    items = list(alerts or [])
    if not items:
        return {"ok": True, "skipped": True, "reason": "no_alerts"}

    payload: Dict[str, Any] = {
        "ts": time.time(),
        "source": source,
        "alert_count": len(items),
        "alerts": items,
        "note": "出站告警；不自动改权、不代客下单。",
    }
    if extra:
        payload["extra"] = extra

    path = write_alert_file(payload)
    url = (webhook_url or os.environ.get("INVESTMENT_ALERT_WEBHOOK") or "").strip()
    hook: Dict[str, Any] = {"ok": False, "skipped": True}
    if url:
        hook = post_webhook(url, payload)
        hook["skipped"] = False

    return {
        "ok": True,
        "path": path,
        "alert_count": len(items),
        "webhook": hook,
    }
