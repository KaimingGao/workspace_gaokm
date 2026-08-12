"""量化报告归档索引（P17.2）。"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from core.paths import QUANT_REPORTS_DIR

_REPORT_RE = re.compile(r"^quant_daily_(\d{8})\.(md|html)$")
_STAMP_RE = re.compile(r"^\d{8}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_stamp(stamp: str) -> Optional[str]:
    try:
        return datetime.strptime(stamp, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _normalize_stamp(value: str) -> Optional[str]:
    """接受 YYYYMMDD 或 YYYY-MM-DD，返回 stamp；非法则 None。"""
    raw = str(value or "").strip()
    if not raw:
        return None
    if _STAMP_RE.match(raw):
        return raw if _parse_stamp(raw) else None
    if _DATE_RE.match(raw):
        try:
            return datetime.strptime(raw, "%Y-%m-%d").strftime("%Y%m%d")
        except ValueError:
            return None
    match = _REPORT_RE.match(raw)
    if match:
        return match.group(1)
    return None


def _group_days(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 stamp 聚合 md/html，供 UI 一日一清。"""
    by_stamp: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for row in rows:
        stamp = str(row.get("stamp") or "")
        if not stamp:
            continue
        if stamp not in by_stamp:
            by_stamp[stamp] = {
                "stamp": stamp,
                "date": row.get("date") or _parse_stamp(stamp),
                "formats": [],
                "filenames": [],
                "size_bytes": 0,
                "modified_at": row.get("modified_at"),
            }
            order.append(stamp)
        day = by_stamp[stamp]
        fmt = row.get("format")
        if fmt and fmt not in day["formats"]:
            day["formats"].append(fmt)
        name = row.get("filename")
        if name and name not in day["filenames"]:
            day["filenames"].append(name)
        day["size_bytes"] = int(day.get("size_bytes") or 0) + int(row.get("size_bytes") or 0)
        mod = row.get("modified_at")
        if mod and (not day.get("modified_at") or str(mod) > str(day["modified_at"])):
            day["modified_at"] = mod
    return [by_stamp[s] for s in order]


def list_quant_reports(
    *,
    reports_dir: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """列出 data/reports/ 下归档的 quant_daily 报告。"""
    root = reports_dir or QUANT_REPORTS_DIR
    if not os.path.isdir(root):
        return {
            "success": True,
            "reports": [],
            "days": [],
            "count": 0,
            "day_count": 0,
            "reports_dir": root,
        }

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
    days_all = _group_days(rows)
    if limit > 0:
        # limit 按「日」截断，再展开对应文件行，避免同一天 md/html 被拆开
        days = days_all[:limit]
        keep = {d["stamp"] for d in days}
        rows = [r for r in rows if r.get("stamp") in keep]
    else:
        days = days_all

    return {
        "success": True,
        "reports_dir": root,
        "count": len(rows),
        "day_count": len(days),
        "reports": rows,
        "days": days,
    }


def delete_quant_reports(
    *,
    stamp: Optional[str] = None,
    date: Optional[str] = None,
    dates: Optional[Sequence[str]] = None,
    stamps: Optional[Sequence[str]] = None,
    reports_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """删除指定日（可批量）的 quant_daily_{stamp}.{md,html} 归档。

    不触碰 score_ledger / quant_daily.json。
    """
    raw_vals: List[str] = []
    for v in (stamps or []):
        if v is not None:
            raw_vals.append(str(v))
    for v in (dates or []):
        if v is not None:
            raw_vals.append(str(v))
    if stamp:
        raw_vals.append(str(stamp))
    if date:
        raw_vals.append(str(date))

    uniq: List[str] = []
    seen = set()
    invalid: List[str] = []
    for raw in raw_vals:
        s = _normalize_stamp(raw)
        if not s:
            if str(raw).strip():
                invalid.append(str(raw).strip())
            continue
        if s in seen:
            continue
        seen.add(s)
        uniq.append(s)

    if not uniq:
        return {
            "success": False,
            "error": "未指定有效 stamp / date",
            "invalid": invalid,
        }

    root = os.path.abspath(reports_dir or QUANT_REPORTS_DIR)
    deleted: List[str] = []
    missing: List[str] = []
    days_out: List[Dict[str, Any]] = []

    for s in uniq:
        day_deleted: List[str] = []
        for ext in ("md", "html"):
            name = f"quant_daily_{s}.{ext}"
            path = os.path.abspath(os.path.join(root, name))
            if not path.startswith(root + os.sep):
                continue
            if not os.path.isfile(path):
                missing.append(name)
                continue
            try:
                os.remove(path)
            except OSError as exc:
                return {
                    "success": False,
                    "error": f"删除失败：{name} · {exc}",
                    "deleted": deleted,
                    "missing": missing,
                }
            deleted.append(name)
            day_deleted.append(name)
        days_out.append(
            {
                "stamp": s,
                "date": _parse_stamp(s),
                "deleted": day_deleted,
                "had_files": bool(day_deleted),
            }
        )

    if not deleted:
        return {
            "success": False,
            "error": "未找到可删的归档文件",
            "deleted": [],
            "missing": missing,
            "days": days_out,
            "invalid": invalid,
        }

    return {
        "success": True,
        "deleted": deleted,
        "deleted_count": len(deleted),
        "missing": missing,
        "days": days_out,
        "invalid": invalid,
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
