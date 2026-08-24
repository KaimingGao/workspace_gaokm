"""当日首次分组：强制把日线增量更新到最新。

按交易会话日（``resolve_session_date``）落盘标记；同日后续分组可走 36h 过期刷新 / 纯缓存。
"""


import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def cluster_bars_session_date() -> str:
    """分组日线强制刷新所用的会话日（YYYY-MM-DD）。"""
    try:
        from core.market.calendar import resolve_session_date

        return str(resolve_session_date() or "")[:10]
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in cluster_bars_daily.py", exc_info=True)
        return datetime.now().strftime("%Y-%m-%d")


def read_force_latest_bars_marker() -> Dict[str, Any]:
    """当日强制日线标记（无文件则 {}）。"""
    return _read_forced_marker()


def _read_forced_marker() -> Dict[str, Any]:
    from core.paths import CLUSTER_BARS_FORCED_SESSION_PATH

    path = CLUSTER_BARS_FORCED_SESSION_PATH
    try:
        import json
        import os

        if not os.path.isfile(path):
            return {}
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in cluster_bars_daily.py", exc_info=True)
        return {}


def needs_force_latest_bars(
    *,
    session_date: Optional[str] = None,
) -> bool:
    """True = 本会话日尚未做过「分组强制日线更新」。"""
    sess = str(session_date or cluster_bars_session_date() or "")[:10]
    if not sess:
        return True
    marked = str((_read_forced_marker() or {}).get("session_date") or "")[:10]
    return marked != sess


def mark_force_latest_bars_done(
    *,
    session_date: Optional[str] = None,
    remote_count: int = 0,
    total: int = 0,
) -> None:
    """标记本会话日已完成强制日线更新（成功落盘后调用）。"""
    from core.io_atomic import atomic_write_json
    from core.paths import CLUSTER_BARS_FORCED_SESSION_PATH

    sess = str(session_date or cluster_bars_session_date() or "")[:10]
    if not sess:
        return
    doc = {
        "session_date": sess,
        "saved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "remote_count": int(remote_count or 0),
        "total": int(total or 0),
    }
    try:
        atomic_write_json(CLUSTER_BARS_FORCED_SESSION_PATH, doc)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in cluster_bars_daily.py", exc_info=True)
        logger.warning("写入当日强制日线标记失败", exc_info=True)
