"""量化报告归档索引（P17.2）。"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.paths import QUANT_REPORTS_DIR

_REPORT_RE = re.compile(r"^quant_daily_(\d{8})\.(md|html)$")


def _parse_stamp(stamp: str) -> Optional[str]:
    try:
        return datetime.strptime(stamp, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def list_quant_reports(
    *,
    reports_dir: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """列出 data/reports/ 下归档的 quant_daily 报告。"""
    root = reports_dir or QUANT_REPORTS_DIR
    if not os.path.isdir(root):
        return {"success": True, "reports": [], "count": 0, "reports_dir": root}

    rows: List[Dict[str, Any]] = []
    for name in os.listdir(root):
        match = _REPORT_RE.match(name)
        if not match:
            continue
        stamp, ext = match.group(1), match.group(2)
        path = os.path.join(root, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        rows.append(
            {
                "filename": name,
                "stamp": stamp,
                "date": _parse_stamp(stamp),
                "format": "markdown" if ext == "md" else "html",
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "path": path,
                "share_url": f"/api/quant/reports/{name}",
            }
        )

    rows.sort(key=lambda r: (r.get("stamp") or "", r.get("filename") or ""), reverse=True)
    if limit > 0:
        rows = rows[:limit]

    return {
        "success": True,
        "reports_dir": root,
        "count": len(rows),
        "reports": rows,
    }


def read_quant_report_file(
    filename: str,
    *,
    reports_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """读取单个归档报告（仅允许 quant_daily_YYYYMMDD.md/html）。"""
    match = _REPORT_RE.match(str(filename or "").strip())
    if not match:
        return {"success": False, "error": "非法文件名"}

    root = os.path.abspath(reports_dir or QUANT_REPORTS_DIR)
    path = os.path.abspath(os.path.join(root, match.group(0)))
    if not path.startswith(root + os.sep):
        return {"success": False, "error": "非法路径"}
    if not os.path.isfile(path):
        return {"success": False, "error": "文件不存在"}

    ext = match.group(2)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    return {
        "success": True,
        "filename": match.group(0),
        "format": "markdown" if ext == "md" else "html",
        "content": content,
        "path": path,
    }
