"""观察池 JSON CRUD：读写、校验、名单增删与行情列表。"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.numbers import now_iso_local as _now_iso
from core.paths import WATCHING_EXAMPLE_PATH, WATCHING_PATH
from core.watching.policy import (
    WATCHING_DEFAULT_SIZE,
    WATCHING_MAX_SIZE,
    clamp_watching_max_size,
)
from core.watching.sources import (
    _clean_name,
    _entries_from_source,
    _resolve_entry,
)


def validate_watching(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("watching 须为 JSON 对象")
    sources = data.get("sources")
    if not isinstance(sources, list):
        raise ValueError("sources 须为数组")
    max_size = clamp_watching_max_size(data.get("max_size"), WATCHING_DEFAULT_SIZE)
    watchlist = data.get("watchlist") or []
    if not isinstance(watchlist, list):
        raise ValueError("watchlist 须为数组")
    cleaned_wl = [str(c).strip() for c in watchlist if str(c).strip()]
    cleaned_sources = []
    for i, src in enumerate(sources):
        if not isinstance(src, dict):
            raise ValueError(f"sources[{i}] 须为对象")
        stype = str(src.get("type") or "").strip().lower()
        if stype not in ("static", "screen"):
            raise ValueError(f"sources[{i}] type 须为 static 或 screen")
        cleaned_sources.append(src)
    out: Dict[str, Any] = {
        "version": int(data.get("version") or 1),
        "name": str(data.get("name") or "default"),
        "max_size": max_size,
        "sources": cleaned_sources,
        "watchlist": cleaned_wl,
        "updated_at": data.get("updated_at"),
    }
    origins = data.get("watchlist_origins")
    if isinstance(origins, list) and len(origins) == len(cleaned_wl):
        out["watchlist_origins"] = [str(x).strip() or "筛选/合并" for x in origins]
    names = data.get("watchlist_names")
    if isinstance(names, list) and len(names) == len(cleaned_wl):
        out["watchlist_names"] = [str(x).strip() for x in names]
    added = data.get("watchlist_added_at")
    if isinstance(added, list) and len(added) == len(cleaned_wl):
        out["watchlist_added_at"] = [str(x).strip() for x in added]
    reserve = data.get("watchlist_reserve")
    if isinstance(reserve, list):
        head = set(cleaned_wl)
        out["watchlist_reserve"] = [
            c for c in _clean_code_list(reserve) if c not in head
        ][:WATCHING_MAX_SIZE]
    return out


def read_watching(path: Optional[str] = None) -> Dict[str, Any]:
    p = path or WATCHING_PATH
    if not os.path.isfile(p):
        raise FileNotFoundError(
            f"未找到 watching: {p}。可复制 watching.example.json 为 watching.json"
        )
    with open(p, encoding="utf-8") as f:
        return validate_watching(json.load(f))


_WATCHLIST_SIDE_KEYS = ("watchlist_origins", "watchlist_names", "watchlist_added_at")


def _clean_code_list(raw: Any) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in raw or []:
        code = str(item).strip()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _pad_side_fields(data: Dict[str, Any], old_n: int, added: int) -> None:
    stamp = _now_iso()
    for key in _WATCHLIST_SIDE_KEYS:
        arr = list(data.get(key) or [])
        if len(arr) > old_n:
            arr = arr[:old_n]
        while len(arr) < old_n:
            arr.append("")
        if key == "watchlist_origins":
            pad = "扩容"
        elif key == "watchlist_added_at":
            pad = stamp
        else:
            pad = ""
        arr.extend([pad] * max(0, int(added)))
        data[key] = arr


def _fill_codes_for_expand(data: Dict[str, Any]) -> List[str]:
    """扩容候选：暂存 → example 观察池 → 研究宇宙。"""
    chunks: List[str] = []
    chunks.extend(_clean_code_list(data.get("watchlist_reserve")))
    try:
        if os.path.isfile(WATCHING_EXAMPLE_PATH):
            with open(WATCHING_EXAMPLE_PATH, encoding="utf-8") as f:
                example = json.load(f) or {}
            chunks.extend(_clean_code_list(example.get("watchlist")))
    except Exception:
        logger.debug("expand watchlist: example read failed", exc_info=True)
    try:
        from core.research_universe import load_research_universe

        uni = load_research_universe() or {}
        chunks.extend(_clean_code_list(uni.get("codes")))
    except Exception:
        logger.debug("expand watchlist: research_universe read failed", exc_info=True)
    return _clean_code_list(chunks)


def _resize_watchlist(data: Dict[str, Any], cap: int, *, expand: bool) -> Dict[str, int]:
    """把 watchlist 收到 cap：超出暂存；expand 时从暂存/示例/研究宇宙补齐。"""
    n_cap = clamp_watching_max_size(cap, WATCHING_DEFAULT_SIZE)
    wl = _clean_code_list(data.get("watchlist"))
    reserve = _clean_code_list(data.get("watchlist_reserve"))
    dropped = 0
    added = 0
    if len(wl) > n_cap:
        overflow = wl[n_cap:]
        dropped = len(overflow)
        seen_head = set(wl[:n_cap])
        reserve = overflow + [c for c in reserve if c not in seen_head and c not in overflow]
        reserve = _clean_code_list(reserve)[:WATCHING_MAX_SIZE]
        for key in _WATCHLIST_SIDE_KEYS:
            arr = data.get(key)
            if isinstance(arr, list):
                data[key] = arr[:n_cap]
        wl = wl[:n_cap]
    elif expand and len(wl) < n_cap:
        seen = set(wl)
        need = n_cap - len(wl)
        restored: List[str] = []
        new_reserve: List[str] = []
        for code in reserve:
            if len(restored) >= need:
                if code not in seen:
                    new_reserve.append(code)
                continue
            if code in seen:
                continue
            seen.add(code)
            restored.append(code)
        if len(restored) < need:
            for code in _fill_codes_for_expand(data):
                if len(restored) >= need:
                    break
                if code in seen:
                    continue
                seen.add(code)
                restored.append(code)
        added = len(restored)
        old_n = len(wl)
        wl = wl + restored
        _pad_side_fields(data, old_n, added)
        reserve = _clean_code_list(new_reserve)[:WATCHING_MAX_SIZE]
    data["watchlist"] = wl
    data["watchlist_reserve"] = reserve
    return {"dropped": dropped, "added": added, "count": len(wl)}


def write_watching(data: dict, path: Optional[str] = None) -> str:
    p = path or WATCHING_PATH
    cleaned = validate_watching(data)
    _resize_watchlist(cleaned, int(cleaned["max_size"]), expand=False)
    cleaned["updated_at"] = _now_iso()
    atomic_write_json(p, cleaned)
    return p


def set_watching_max_size(max_size: int, path: Optional[str] = None) -> Dict[str, Any]:
    """改观察池目标只数：缩小截断并暂存，放大从暂存/示例/研究宇宙补齐。"""
    data = read_watching(path)
    before = len(_clean_code_list(data.get("watchlist")))
    cap = clamp_watching_max_size(max_size, WATCHING_DEFAULT_SIZE)
    data["max_size"] = cap
    stats = _resize_watchlist(data, cap, expand=True)
    if stats.get("added"):
        try:
            data["watchlist_names"] = watchlist_names_for(data, allow_live=False)
        except Exception:
            logger.debug("expand watchlist: names failed", exc_info=True)
    write_watching(data, path)
    saved = read_watching(path)
    n = len(_clean_code_list(saved.get("watchlist")))
    return {
        "ok": True,
        "max_size": cap,
        "count": n,
        "dropped": int(stats.get("dropped") or 0),
        "added": int(stats.get("added") or 0),
        "reserve": len(_clean_code_list(saved.get("watchlist_reserve"))),
        "over_cap": False,
        "before": before,
    }


def init_from_example(path: Optional[str] = None) -> str:
    p = path or WATCHING_PATH
    if os.path.isfile(p):
        raise FileExistsError(f"已存在: {p}")
    with open(WATCHING_EXAMPLE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    data["updated_at"] = _now_iso()
    wl = [str(c).strip() for c in (data.get("watchlist") or []) if str(c).strip()]
    if wl and not data.get("watchlist_added_at"):
        data["watchlist_added_at"] = [data["updated_at"]] * len(wl)
    return write_watching(data, p)


def watchlist_added_at_for(data: dict) -> List[str]:
    """与 watchlist 等长的加入时间；缺失则补空串。"""
    wl = [str(c).strip() for c in (data.get("watchlist") or []) if str(c).strip()]
    saved = data.get("watchlist_added_at")
    if isinstance(saved, list) and len(saved) == len(wl):
        return [str(x).strip() for x in saved]
    return [""] * len(wl)


def watchlist_added_map(data: dict) -> Dict[str, str]:
    wl = [str(c).strip() for c in (data.get("watchlist") or []) if str(c).strip()]
    times = watchlist_added_at_for(data)
    return {c: times[i] for i, c in enumerate(wl) if times[i]}


def _align_watch_meta(data: dict) -> None:
    """保证 names/origins/added_at 与 watchlist 等长。"""
    wl = [str(c).strip() for c in (data.get("watchlist") or []) if str(c).strip()]
    origins = list(data.get("watchlist_origins") or [])
    names = list(data.get("watchlist_names") or [])
    added = list(data.get("watchlist_added_at") or [])
    if len(origins) != len(wl):
        origins = ["手动"] * len(wl)
    if len(names) != len(wl):
        names = watchlist_names_for({**data, "watchlist": wl})
    if len(added) != len(wl):
        # 历史名单无加入日：不伪造日期，留给 UI 显示 —
        added = (added + [""] * len(wl))[: len(wl)]
    data["watchlist"] = wl
    data["watchlist_origins"] = origins
    data["watchlist_names"] = names
    data["watchlist_added_at"] = added


def watchlist_origins_for(data: dict) -> List[str]:
    """为 watchlist 每项标注来源（优先用落盘 origins；否则解析 static 名称→代码）。"""
    wl = [str(c).strip() for c in (data.get("watchlist") or []) if str(c).strip()]
    saved = data.get("watchlist_origins")
    if isinstance(saved, list) and len(saved) == len(wl):
        return [str(x).strip() or "筛选/合并" for x in saved]

    code_to_label: Dict[str, str] = {}
    for i, src in enumerate(data.get("sources") or []):
        stype = str(src.get("type") or "").lower()
        label = f"S{i + 1} {stype}"
        if stype != "static":
            continue
        for raw, _hint in _entries_from_source(src):
            raw_s = str(raw).strip()
            if raw_s and raw_s not in code_to_label:
                code_to_label[raw_s] = label
            try:
                resolved, _ = _resolve_entry(raw_s)
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                resolved = ""
            if resolved and resolved not in code_to_label:
                code_to_label[resolved] = label
    return [code_to_label.get(c, "筛选/合并") for c in wl]


def watchlist_names_for(data: dict, *, allow_live: bool = True) -> List[str]:
    """为 watchlist 每项补全股票名（优先落盘；再 static 提示；可选行情解析）。

    ``allow_live=False``：绝不打行情（给 GET /api/watching 热路径用，避免满池空名时
    逐只 ``get_quote`` 把前端拖到「观察名单加载超时」）。
    """
    wl = [str(c).strip() for c in (data.get("watchlist") or []) if str(c).strip()]
    saved = data.get("watchlist_names")
    if isinstance(saved, list) and len(saved) == len(wl):
        names = [_clean_name(x) for x in saved]
        if all(names):
            return names
    else:
        names = [""] * len(wl)

    code_to_name: Dict[str, str] = {}
    for src in data.get("sources") or []:
        if str(src.get("type") or "").lower() != "static":
            continue
        for raw, _hint in _entries_from_source(src):
            raw_s = str(raw).strip()
            if not raw_s or raw_s.isdigit():
                continue
            try:
                if allow_live:
                    code, name = _resolve_entry(raw_s, raw_s)
                else:
                    # 离线：非纯数字文本当显示名；纯代码留给名单空名，由 insights 补
                    code, name = "", ("" if raw_s.isdigit() else _clean_name(raw_s))
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                code, name = "", raw_s
            if code and name:
                code_to_name[code] = name
            code_to_name[raw_s] = name or raw_s

    for i, code in enumerate(wl):
        if names[i]:
            continue
        hint = code_to_name.get(code, "")
        if hint:
            names[i] = hint
            continue
        if not allow_live:
            continue
        try:
            _c, name = _resolve_entry(code, hint)
            if name:
                names[i] = name
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            pass
    return names


def add_watchlist_item(
    query: str,
    *,
    path: Optional[str] = None,
    sync_paper: bool = False,
) -> Dict[str, Any]:
    """按名称/代码解析后手动加入观察名单（不依赖 sources/static）。"""
    text = str(query or "").strip()
    if not text:
        raise ValueError("请输入股票名称或代码")

    code, name = _resolve_entry(text)
    if not code:
        raise ValueError(f"无法识别「{text}」")

    from core.data.facade import get_quote

    quote = get_quote(text)
    if quote.get("success") and quote.get("stock_code"):
        code = str(quote["stock_code"]).strip()
        name = _clean_name(str(quote.get("stock_name") or "")) or name or text
    elif not str(code).isdigit() and len(str(code)) != 6:
        if not quote.get("success"):
            raise ValueError(quote.get("error") or f"无法识别「{text}」")

    data = read_watching(path)
    max_size = clamp_watching_max_size(data.get("max_size"), WATCHING_DEFAULT_SIZE)
    wl = [str(c).strip() for c in (data.get("watchlist") or []) if str(c).strip()]
    origins = list(data.get("watchlist_origins") or [])
    names = list(data.get("watchlist_names") or [])
    if len(origins) != len(wl):
        origins = ["手动"] * len(wl)
    if len(names) != len(wl):
        names = watchlist_names_for({**data, "watchlist": wl})

    if code in wl:
        return {
            "success": True,
            "added": False,
            "already": True,
            "stock_code": code,
            "stock_name": name or code,
            "watchlist": wl,
            "count": len(wl),
            "message": f"「{name or code}」已在观察名单",
        }

    if len(wl) >= max_size:
        raise ValueError(f"观察名单已满（上限 {max_size}）")

    wl.append(code)
    origins.append("手动")
    names.append(name or code)
    added = list(data.get("watchlist_added_at") or [])
    if len(added) != len(wl) - 1:
        added = (added + [""] * (len(wl) - 1))[: len(wl) - 1]
    added.append(_now_iso())

    data["sources"] = []  # 观察名单改为纯手动，不再维护 static/screen
    data["watchlist"] = wl
    data["watchlist_origins"] = origins
    data["watchlist_names"] = names
    data["watchlist_added_at"] = added
    write_watching(data, path)

    out: Dict[str, Any] = {
        "success": True,
        "added": True,
        "already": False,
        "stock_code": code,
        "stock_name": name or code,
        "watchlist": wl,
        "count": len(wl),
        "message": f"已加入观察：{name or code}（{code}）",
    }
    if sync_paper:
        from core.watching.sync_paper import sync_paper_watchlist

        out["paper_sync"] = sync_paper_watchlist()
    return out


def remove_watchlist_item(
    code: str,
    *,
    path: Optional[str] = None,
    sync_paper: bool = False,
) -> Dict[str, Any]:
    """从观察名单移除一只股票（纯手动列表）。"""
    target = str(code or "").strip()
    if not target:
        raise ValueError("请指定要移除的股票代码")

    data = read_watching(path)
    wl = [str(c).strip() for c in (data.get("watchlist") or []) if str(c).strip()]
    origins = list(data.get("watchlist_origins") or [])
    names = list(data.get("watchlist_names") or [])
    if len(origins) != len(wl):
        origins = ["手动"] * len(wl)
    if len(names) != len(wl):
        names = watchlist_names_for({**data, "watchlist": wl})

    idx = next((i for i, c in enumerate(wl) if c == target), None)
    if idx is None:
        resolved, _ = _resolve_entry(target)
        idx = next((i for i, c in enumerate(wl) if c == resolved), None)
        if idx is not None:
            target = wl[idx]

    if idx is None:
        raise ValueError(f"观察名单中没有「{code}」")

    removed_name = names[idx] if idx < len(names) else target
    added = list(data.get("watchlist_added_at") or [])
    if len(added) != len(wl):
        added = (added + [""] * len(wl))[: len(wl)]
    wl.pop(idx)
    if idx < len(origins):
        origins.pop(idx)
    if idx < len(names):
        names.pop(idx)
    if idx < len(added):
        added.pop(idx)

    data["sources"] = []
    data["watchlist"] = wl
    data["watchlist_origins"] = origins
    data["watchlist_names"] = names
    data["watchlist_added_at"] = added
    write_watching(data, path)

    out: Dict[str, Any] = {
        "success": True,
        "removed": True,
        "stock_code": target,
        "stock_name": removed_name,
        "watchlist": wl,
        "count": len(wl),
        "message": f"已移除：{removed_name}（{target}）",
    }
    if sync_paper:
        from core.watching.sync_paper import sync_paper_watchlist

        out["paper_sync"] = sync_paper_watchlist()
    return out


def list_watchlist_quotes(
    *,
    path: Optional[str] = None,
    codes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """对观察名单（或给定 codes）批量拉现价；单票失败不影响其它。"""
    from core.data.facade import batch_get_quotes

    def _blank(code: str, name: str = "", *, error: str = "") -> Dict[str, Any]:
        return {
            "stock_code": code,
            "stock_name": name or code,
            "ok": False,
            "error": error or None,
            "price": None,
            "price_raw": None,
            "change_percent": None,
            "change_amount": None,
            "open": None,
            "high": None,
            "low": None,
            "volume": None,
            "market": None,
        }

    if codes is not None:
        watch = [str(c).strip() for c in codes if str(c).strip()]
    else:
        try:
            data = read_watching(path)
        except FileNotFoundError:
            return {"ok": True, "count": 0, "items": [], "note": "尚未创建观察名单"}
        watch = [str(c).strip() for c in (data.get("watchlist") or []) if str(c).strip()]

    # 腾讯批量接口一次请求；分块避免超长 URL
    chunk_size = 40
    quotes_by: Dict[str, Any] = {}
    for i in range(0, len(watch), chunk_size):
        chunk = watch[i : i + chunk_size]
        try:
            part = batch_get_quotes(chunk) or {}
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            part = {}
        if isinstance(part, dict):
            quotes_by.update(part)

    items: List[Dict[str, Any]] = []
    for code in watch:
        q = quotes_by.get(code)
        if not isinstance(q, dict) or not q.get("success"):
            items.append(
                _blank(
                    code,
                    (q or {}).get("stock_name") or code if isinstance(q, dict) else code,
                    error=(q or {}).get("error") if isinstance(q, dict) else "行情不可用",
                )
            )
            continue
        chg = q.get("change_raw")
        try:
            chg_f = float(chg) if chg is not None else None
        except (TypeError, ValueError):
            chg_f = None
        items.append(
            {
                "stock_code": str(q.get("stock_code") or code),
                "stock_name": q.get("stock_name") or code,
                "ok": True,
                "price": q.get("price"),
                "price_raw": q.get("price_raw"),
                "change_percent": chg_f,
                "change_amount": q.get("change_amount") or None,
                "open": q.get("open") or None,
                "high": q.get("high") or None,
                "low": q.get("low") or None,
                "volume": q.get("volume") or None,
                "market": q.get("market") or None,
            }
        )
    return {"ok": True, "count": len(items), "items": items}
