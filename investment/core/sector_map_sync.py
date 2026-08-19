"""sector_map 维护：清洗板别 + 现货行业补全（DS-R2.1 / R2.2）。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
import json
import os
from typing import Any, Dict, List, Optional

from core.data_policy import is_board_label
from core.io_atomic import atomic_write_json
from core.paths import DATA_DIR
from core.portfolio_optimize import _board_for, load_sector_map, sector_map_coverage

SECTOR_MAP_PATH = os.path.join(DATA_DIR, "sector_map.json")


def save_sector_map(mapping: Dict[str, str], *, path: Optional[str] = None) -> str:
    p = path or SECTOR_MAP_PATH
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    ordered = {k: mapping[k] for k in sorted(mapping.keys())}
    atomic_write_json(p, ordered)
    return p


def scrub_board_labels_from_sector_map(
    *,
    write: bool = True,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """删除 sector_map 中的板别伪主题（主板沪/创业板等），保留真实行业。"""
    p = path or SECTOR_MAP_PATH
    current: Dict[str, str] = {}
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                current = {str(k): str(v) for k, v in raw.items() if k and v}
        except (OSError, json.JSONDecodeError):
            current = {}
    else:
        current = load_sector_map() if path is None else {}
    kept: Dict[str, str] = {}
    removed: List[Dict[str, str]] = []
    for code, label in current.items():
        if is_board_label(label):
            removed.append(
                {
                    "stock_code": code,
                    "sector": str(label),
                    "board": _board_for(code),
                }
            )
        else:
            kept[code] = str(label)
    out: Dict[str, Any] = {
        "ok": True,
        "path": p,
        "before": len(current),
        "after": len(kept),
        "removed_count": len(removed),
        "removed": removed[:40],
        "note": "板别标签已从行业 map 剔除；限额只认真实主题。",
    }
    if write and removed:
        out["path"] = save_sector_map(kept, path=path)
        out["written"] = True
    else:
        out["written"] = False
    return out


def sync_sector_map_from_watching(
    *,
    write: bool = True,
    path: Optional[str] = None,
    watching_path: Optional[str] = None,
    scrub_boards: bool = True,
) -> Dict[str, Any]:
    """对齐 watching：默认清洗板别；仅当 watching 提供真主题时补键。"""
    from core.watching_store import read_watching

    current = dict(load_sector_map())
    scrubbed = 0
    if scrub_boards:
        scrub = scrub_board_labels_from_sector_map(write=False, path=path)
        scrubbed = int(scrub.get("removed_count") or 0)
        current = {k: v for k, v in current.items() if not is_board_label(v)}

    try:
        uni = read_watching(watching_path) if watching_path else read_watching()
    except FileNotFoundError:
        return {
            "ok": False,
            "error": "watching.json 不存在",
            "added": [],
            "mapped": len(current),
        }

    codes = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
    names = uni.get("watchlist_names") or []
    themes = uni.get("watchlist_sectors") or uni.get("sectors") or {}
    if not isinstance(themes, dict):
        themes = {}

    added: List[Dict[str, str]] = []
    skipped_board: List[str] = []
    for i, code in enumerate(codes):
        if code in current and not is_board_label(current.get(code)):
            continue
        hint = str(themes.get(code) or "").strip()
        if not hint:
            continue
        if is_board_label(hint):
            skipped_board.append(code)
            continue
        current[code] = hint
        name = names[i] if i < len(names) else ""
        added.append({"stock_code": code, "sector": hint, "stock_name": name})

    cov = sector_map_coverage(codes, sector_map=current)
    out = {
        "ok": True,
        "path": path or SECTOR_MAP_PATH,
        "mapping": current,
        "mapped": cov["mapped"],
        "watching_count": len(codes),
        "coverage": cov["coverage"],
        "added": added,
        "added_count": len(added),
        "scrubbed_board_labels": scrubbed,
        "skipped_board_hints": skipped_board[:20],
        "unmapped_codes": cov["unmapped_codes"][:20],
        "board_preview": {c: _board_for(c) for c in codes[:12]},
        "note": (
            "DS-R2.1：不再把板别写入行业 map；"
            "仅当 watching 提供真实 theme/sector 时补键；"
            "缺主题码进「未分类」。"
        ),
    }
    if write and (added or scrubbed):
        out["path"] = save_sector_map(current, path=path)
        out["written"] = True
    else:
        out["written"] = False
    return out


def normalize_industry_label(raw: str) -> Optional[str]:
    """东财「所属行业」→ 可写入 sector_map 的主题；板别/空返回 None。"""
    s = str(raw or "").strip()
    if not s:
        return None
    for sep in ("--", "\u2014", "\uff0d", "/", "|"):
        if sep in s:
            s = s.split(sep)[0].strip()
    cleaned = s.replace("行业", "").strip()
    if cleaned:
        s = cleaned
    if len(s) > 20:
        s = s[:20].rstrip()
    if is_board_label(s):
        return None
    return s


def industry_map_from_spot(
    codes: Optional[List[str]] = None,
    *,
    force_spot: bool = False,
) -> Dict[str, Any]:
    """从 A 股现货「所属行业」建 code→industry（经 DataService）。"""
    from core.data_service import get_spot
    from core.ports.market import spot_row_get

    want_raw = {str(c).strip() for c in (codes or []) if str(c).strip()}

    rows: List[dict] = []
    src = "empty"
    try:
        if force_spot:
            pack = get_spot(force=True)
            rows = list(pack.get("rows") or [])
            src = str(pack.get("data_source") or "live")
        else:
            pack = get_spot(disk_only=True)
            rows = list(pack.get("rows") or [])
            if rows:
                src = "disk"
            else:
                pack = get_spot(force=False)
                rows = list(pack.get("rows") or [])
                src = str(pack.get("data_source") or "live_or_cache")
    except Exception as e:
        return {"ok": False, "error": str(e), "mapping": {}, "data_source": src}

    by_code: Dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(spot_row_get(row, "code") or "").strip()
        if not code:
            continue
        ind = normalize_industry_label(str(spot_row_get(row, "industry") or ""))
        if not ind:
            continue
        by_code[code] = ind
        if code.isdigit():
            by_code[code.zfill(6)] = ind

    if want_raw:
        mapping: Dict[str, str] = {}
        for c in want_raw:
            label = by_code.get(c)
            if not label and c.isdigit():
                label = by_code.get(c.zfill(6))
            if label:
                mapping[c] = label
    else:
        mapping = dict(by_code)

    return {
        "ok": True,
        "mapping": mapping,
        "count": len(mapping),
        "data_source": src,
        "row_count": len(rows),
        "note": "来自东财现货所属行业；非申万一级标准名，可人手改。",
    }


def enrich_sector_map_from_spot(
    *,
    codes: Optional[List[str]] = None,
    write: bool = True,
    path: Optional[str] = None,
    overwrite: bool = False,
    force_spot: bool = False,
    scrub_boards: bool = True,
) -> Dict[str, Any]:
    """用现货行业补全 sector_map（默认不覆盖已有真主题）。"""
    from core.watching_store import read_watching

    if codes is None:
        try:
            codes = [
                str(c).strip()
                for c in (read_watching().get("watchlist") or [])
                if str(c).strip()
            ]
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in sector_map_sync.py", exc_info=True)
            codes = []

    current: Dict[str, str] = {}
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                current = {str(k): str(v) for k, v in raw.items() if k and v}
        except (OSError, json.JSONDecodeError):
            current = {}
    else:
        current = dict(load_sector_map())
    scrubbed = 0
    if scrub_boards:
        scrub = scrub_board_labels_from_sector_map(write=False, path=path)
        scrubbed = int(scrub.get("removed_count") or 0)
        current = {k: v for k, v in current.items() if not is_board_label(v)}

    pack = industry_map_from_spot(codes, force_spot=force_spot)
    if not pack.get("ok"):
        return {**pack, "added": [], "updated": [], "written": False}

    added: List[Dict[str, str]] = []
    updated: List[Dict[str, str]] = []
    skipped = 0
    for code, industry in (pack.get("mapping") or {}).items():
        prev = current.get(code)
        if prev and not is_board_label(prev) and not overwrite:
            skipped += 1
            continue
        if prev and not is_board_label(prev) and overwrite and prev != industry:
            updated.append({"stock_code": code, "from": prev, "sector": industry})
        elif not prev or is_board_label(prev):
            added.append({"stock_code": code, "sector": industry})
        current[code] = industry

    cov = sector_map_coverage(list(codes or []), sector_map=current)
    out: Dict[str, Any] = {
        "ok": True if cov.get("total") else False,
        "path": path or SECTOR_MAP_PATH,
        "data_source": pack.get("data_source"),
        "spot_hits": pack.get("count"),
        "added": added,
        "added_count": len(added),
        "updated": updated,
        "updated_count": len(updated),
        "skipped_existing": skipped,
        "scrubbed_board_labels": scrubbed,
        "coverage": cov["coverage"],
        "mapped": cov["mapped"],
        "total": cov["total"],
        "board_labeled": cov.get("board_labeled") or 0,
        "empty_universe": bool(cov.get("empty_universe")),
        "unmapped_codes": cov["unmapped_codes"][:20],
        "note": (
            "DS-R2.2：现货所属行业补全；overwrite=false 不改已有真主题。"
            + (" 无候选码，coverage 不报 100%。" if cov.get("empty_universe") else "")
        ),
    }
    if write and (added or updated or scrubbed):
        out["path"] = save_sector_map(current, path=path)
        out["written"] = True
    else:
        out["written"] = False
    return out


def coverage_report(codes: Optional[List[str]] = None) -> Dict[str, Any]:
    smap = load_sector_map()
    if codes is None:
        try:
            from core.watching_store import read_watching

            codes = [str(c).strip() for c in (read_watching().get("watchlist") or [])]
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in sector_map_sync.py", exc_info=True)
            codes = []
    cov = sector_map_coverage(list(codes or []), sector_map=smap)
    return {
        "total": cov["total"],
        "mapped": cov["mapped"],
        "missing": cov["unmapped_codes"],
        "missing_count": cov["unmapped"],
        "board_labeled": cov.get("board_labeled") or 0,
        "board_codes": cov.get("board_codes") or [],
        "coverage": cov["coverage"],
        "map_size": len(smap),
    }
