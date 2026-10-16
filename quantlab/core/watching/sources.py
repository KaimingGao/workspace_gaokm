"""观察池 source 拉取与名单刷新。"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from core.watching.policy import (
    WATCHING_DEFAULT_SIZE,
    clamp_watching_max_size,
)


def _clean_name(name: str) -> str:
    text = str(name or "").strip()
    # 部分行情源会在中文名中插入空格
    if text and any("\u4e00" <= ch <= "\u9fff" for ch in text):
        text = text.replace(" ", "").replace("\u3000", "")
    return text


def _resolve_entry(raw: str, hint_name: str = "") -> tuple:
    """解析标的 → (code, name)。"""
    from core.data.facade import get_quote

    text = str(raw or "").strip()
    hint = _clean_name(hint_name)
    if not text:
        return "", hint
    try:
        quote = get_quote(text)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        return text, hint
    if quote.get("success") and quote.get("stock_code"):
        code = str(quote["stock_code"])
        name = _clean_name(str(quote.get("stock_name") or "")) or hint
        if not name and text and not text.isdigit():
            name = _clean_name(text)
        return code, name
    if not hint and text and not text.isdigit():
        return text, _clean_name(text)
    return text, hint


def _resolve_code(raw: str) -> str:
    code, _ = _resolve_entry(raw)
    return code


def _pull_source(source: dict) -> Dict[str, Any]:
    """拉取一条 source → {entries, error, note}。"""
    stype = str(source.get("type") or "").lower()
    if stype == "static":
        codes = source.get("codes") or []
        if isinstance(codes, str):
            codes = [c.strip() for c in codes.replace("，", ",").split(",") if c.strip()]
        entries = [(str(c).strip(), "") for c in codes if str(c).strip()]
        return {"entries": entries, "error": None, "note": None}

    if stype == "screen":
        from core.ports.market import screen_stocks

        params = {k: v for k, v in source.items() if k != "type"}
        try:
            result = screen_stocks(params)
        except Exception as e:
            logger.exception('unexpected error in _pull_source')
            return {"entries": [], "error": f"筛选异常: {e}", "note": None}
        if not result.get("success"):
            return {
                "entries": [],
                "error": str(result.get("error") or "筛选失败"),
                "note": None,
            }
        entries: List[tuple] = []
        for s in result.get("stocks") or []:
            code = str(s.get("stock_code") or "").strip()
            if not code:
                continue
            entries.append((code, str(s.get("stock_name") or "").strip()))
        note = None
        if not entries:
            note = str(result.get("message") or "无命中")
        return {"entries": entries, "error": None, "note": note}

    return {"entries": [], "error": f"未知类型: {stype or '—'}", "note": None}


def _entries_from_source(source: dict) -> List[tuple]:
    """从 source 取出 [(raw_or_code, hint_name), ...]。"""
    return list(_pull_source(source).get("entries") or [])


def _codes_from_source(source: dict) -> List[str]:
    return [raw for raw, _ in _entries_from_source(source)]


def refresh_watchlist(
    watching: Optional[dict] = None,
    *,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """按 sources 刷新 watchlist；sources 为空时为手动模式，仅刷新名称不改名单。"""
    from core.numbers import now_iso_local as _now_iso
    from core.watching.repository import (
        read_watching,
        watchlist_names_for,
        write_watching,
    )

    data = watching if watching is not None else read_watching(path)
    sources = data.get("sources") or []
    if not sources:
        wl = [str(c).strip() for c in (data.get("watchlist") or []) if str(c).strip()]
        names = watchlist_names_for({**data, "watchlist": wl})
        origins = ["手动"] * len(wl)
        data["watchlist"] = wl
        data["watchlist_names"] = names
        data["watchlist_origins"] = origins
        data["sources"] = []
        data["updated_at"] = _now_iso()
        if path or (watching is None):
            write_watching(data, path)
        return {
            "success": True,
            "watchlist": wl,
            "watchlist_origins": origins,
            "watchlist_names": names,
            "count": len(wl),
            "source_stats": [],
            "updated_at": data["updated_at"],
            "manual": True,
        }

    max_size = clamp_watching_max_size(data.get("max_size"), WATCHING_DEFAULT_SIZE)
    merged: List[str] = []
    origins: List[str] = []
    names: List[str] = []
    seen = set()
    source_stats: List[dict] = []

    # 预热现货缓存，避免多条 screen 各自打远端
    if any(str(s.get("type") or "").lower() == "screen" for s in sources):
        try:
            from core.data.facade import get_spot

            get_spot()
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            pass

    for i, src in enumerate(sources):
        stype = str(src.get("type") or "").lower()
        label = f"S{i + 1} {stype}"
        pulled = _pull_source(src)
        entries = list(pulled.get("entries") or [])
        added = 0
        for raw, hint in entries:
            code, name = _resolve_entry(raw, hint)
            if not code or code in seen:
                continue
            seen.add(code)
            merged.append(code)
            origins.append(label)
            names.append(name or hint or "")
            added += 1
            if len(merged) >= max_size:
                break
        stat: Dict[str, Any] = {
            "label": label,
            "type": src.get("type"),
            "requested": len(entries),
            "added": added,
        }
        if pulled.get("error"):
            stat["error"] = pulled["error"]
        if pulled.get("note"):
            stat["note"] = pulled["note"]
        source_stats.append(stat)
        if len(merged) >= max_size:
            break

    data["watchlist"] = merged[:max_size]
    data["watchlist_origins"] = origins[:max_size]
    data["watchlist_names"] = names[:max_size]
    data["updated_at"] = _now_iso()
    if path or (watching is None):
        write_watching(data, path)

    return {
        "success": True,
        "watchlist": data["watchlist"],
        "watchlist_origins": data["watchlist_origins"],
        "watchlist_names": data["watchlist_names"],
        "count": len(data["watchlist"]),
        "source_stats": source_stats,
        "updated_at": data["updated_at"],
    }
