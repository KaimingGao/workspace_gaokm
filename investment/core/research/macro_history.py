"""由 macro 快照 recent_bars 构建日度历史索引（研究轨）。"""


import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.io_atomic import atomic_write_json
from core.paths import STORE_DIR
from core.research.macro_asof import macro_view_asof

logger = logging.getLogger(__name__)

_HISTORY_REL = os.path.join("macro", "history_index.json")


def _history_path() -> str:
    return os.path.join(STORE_DIR, _HISTORY_REL)


def build_macro_history_rows(macro: Optional[dict]) -> List[Dict[str, Any]]:
    """从 macro.series.recent_bars 展开为日度 overseas_tech / A50 视图。"""
    if not isinstance(macro, dict):
        return []
    series = macro.get("series") or {}
    dates: set = set()
    for key in ("sox", "ndx", "qqq", "kweb", "a50", "cnh"):
        raw = series.get(key)
        if not isinstance(raw, dict):
            continue
        for b in raw.get("recent_bars") or []:
            d = str(b.get("date") or "")[:10]
            if d:
                dates.add(d)
    rows: List[Dict[str, Any]] = []
    for d in sorted(dates):
        view = macro_view_asof(macro, d)
        if not view:
            continue
        rows.append(
            {
                "date": d,
                "overseas_tech_1d_pct": view.get("overseas_tech_1d_pct"),
                "a50_1d_pct": view.get("a50_1d_pct"),
                "liquidity_stress_score": view.get("liquidity_stress_score"),
            }
        )
    return rows


def save_macro_history_index(
    macro: Optional[dict],
    *,
    source_as_of: Optional[str] = None,
) -> str:
    rows = build_macro_history_rows(macro)
    path = _history_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = {
        "as_of": source_as_of or (macro or {}).get("as_of"),
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "row_count": len(rows),
        "rows": rows,
        "note": "由 macro 快照 recent_bars 合成；非 PIT。",
    }
    atomic_write_json(path, body)
    return path


def load_macro_history_index() -> Optional[Dict[str, Any]]:
    path = _history_path()
    if not os.path.isfile(path):
        return None
    try:
        import json

        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, TypeError):
        logger.debug("macro history index read failed", exc_info=True)
        return None
