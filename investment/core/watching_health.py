"""Watching 健康检查（P17.1）。"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from core.paths import WATCHING_PATH
from core.watching_store import read_watching, validate_watching


def _parse_updated_at(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00").split("+")[0])
    except ValueError:
        return None


def check_watching_health(
    *,
    path: Optional[str] = None,
    stale_days: int = 7,
    min_watchlist: int = 1,
) -> Dict[str, Any]:
    """检查 watching 是否可用于量化任务。"""
    p = path or WATCHING_PATH
    issues: List[str] = []
    warnings: List[str] = []

    if not os.path.isfile(p):
        return {
            "success": False,
            "exists": False,
            "path": p,
            "issues": ["watching.json 不存在，请先 init"],
            "warnings": warnings,
        }

    try:
        data = read_watching(p)
    except Exception as e:
        return {
            "success": False,
            "exists": True,
            "path": p,
            "issues": [str(e)],
            "warnings": warnings,
        }

    sources = data.get("sources") or []
    watchlist = data.get("watchlist") or []
    # sources 为空 = 手动模式：refresh 只更新名称，不改名单（见 refresh_watchlist）
    if not sources:
        if watchlist:
            warnings.append("手动模式（sources 为空）· 名单由人工维护，刷新仅更新名称")
        else:
            warnings.append("sources 为空且无 watchlist · 请在数据中心手动加票，或配置 sources 后刷新")
    if len(watchlist) < min_watchlist:
        issues.append(f"watchlist 仅 {len(watchlist)} 只，建议至少 {min_watchlist} 只")

    updated = _parse_updated_at(data.get("updated_at"))
    stale = False
    if updated is None:
        warnings.append("缺少 updated_at，建议 refresh 一次")
    else:
        age = datetime.now() - updated.replace(tzinfo=None)
        stale = age > timedelta(days=max(1, stale_days))
        if stale:
            warnings.append(f"watchlist 已 {age.days} 天未刷新")

    return {
        "success": not issues,
        "exists": True,
        "path": p,
        "name": data.get("name"),
        "max_size": data.get("max_size"),
        "sources_count": len(sources),
        "watchlist_count": len(watchlist),
        "updated_at": data.get("updated_at"),
        "stale": stale,
        "issues": issues,
        "warnings": warnings,
    }


def validate_watching_payload(data: Any) -> Dict[str, Any]:
    """API 保存前校验，返回 cleaned 数据或抛 ValueError。"""
    return validate_watching(data)
