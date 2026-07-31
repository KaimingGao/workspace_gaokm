"""sector_map 维护：与观察池对齐、启发式补全（R5 后运营收尾）。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from core.paths import DATA_DIR
from core.portfolio_optimize import _sector_for, load_sector_map

SECTOR_MAP_PATH = os.path.join(DATA_DIR, "sector_map.json")


def save_sector_map(mapping: Dict[str, str], *, path: Optional[str] = None) -> str:
    p = path or SECTOR_MAP_PATH
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    ordered = {k: mapping[k] for k in sorted(mapping.keys())}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return p


def sync_sector_map_from_watching(
    *,
    write: bool = True,
    path: Optional[str] = None,
    watching_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    把 watching 池中未映射代码用板块启发式写入 sector_map（不覆盖已有显式主题）。
    """
    from core.watching_store import read_watching

    current = load_sector_map()
    try:
        uni = read_watching(watching_path) if watching_path else read_watching()
    except FileNotFoundError:
        return {"ok": False, "error": "watching.json 不存在", "added": [], "mapped": len(current)}

    codes = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
    names = uni.get("watchlist_names") or []
    added: List[Dict[str, str]] = []
    for i, code in enumerate(codes):
        if code in current:
            continue
        sector = _sector_for(code, {})
        # 启发式板块桶升为「主题」标签（主板沪等）；可人工改 sector_map
        current[code] = sector
        name = names[i] if i < len(names) else ""
        added.append({"stock_code": code, "sector": sector, "stock_name": name})

    out = {
        "ok": True,
        "path": path or SECTOR_MAP_PATH,
        "mapped": len(current),
        "watching_count": len(codes),
        "added": added,
        "added_count": len(added),
        "note": "仅补未映射代码；已有主题键不覆盖。",
    }
    if write and added:
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
        except Exception:
            codes = []
    mapped = [c for c in codes if c in smap]
    missing = [c for c in codes if c not in smap]
    return {
        "ok": True,
        "total": len(codes),
        "mapped": len(mapped),
        "missing": missing,
        "coverage": round(len(mapped) / len(codes), 4) if codes else None,
        "map_size": len(smap),
    }
