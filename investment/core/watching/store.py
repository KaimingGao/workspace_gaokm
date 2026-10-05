"""投资宇宙 Watching：观察池定义与刷新（P9.1）。"""

import logging

logger = logging.getLogger(__name__)
import json
import os
from typing import Any, Dict, List, Optional

from core.io_atomic import atomic_write_json
from core.numbers import now_iso_local as _now_iso
from core.paths import WATCHING_EXAMPLE_PATH, WATCHING_PATH

WATCHING_MAX_SIZE = 500
# 模型拟合宇宙上限（可走 research_universe，宽于观察池）；分钟暖仓 / live 仍只读观察池
MODEL_FIT_MAX_SIZE = 1000


def validate_watching(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("watching 须为 JSON 对象")
    sources = data.get("sources")
    if not isinstance(sources, list):
        raise ValueError("sources 须为数组")
    max_size = int(data.get("max_size") or 30)
    max_size = max(5, min(max_size, WATCHING_MAX_SIZE))
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
    return out


def read_watching(path: Optional[str] = None) -> Dict[str, Any]:
    p = path or WATCHING_PATH
    if not os.path.isfile(p):
        raise FileNotFoundError(
            f"未找到 watching: {p}。可复制 watching.example.json 为 watching.json"
        )
    with open(p, encoding="utf-8") as f:
        return validate_watching(json.load(f))


def write_watching(data: dict, path: Optional[str] = None) -> str:
    p = path or WATCHING_PATH
    cleaned = validate_watching(data)
    cleaned["updated_at"] = _now_iso()
    atomic_write_json(p, cleaned)
    return p


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
        logger.debug("catch except Exception: in watching_store.py", exc_info=True)
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
                logger.debug("catch except Exception: in watching_store.py", exc_info=True)
                resolved = ""
            if resolved and resolved not in code_to_label:
                code_to_label[resolved] = label
    return [code_to_label.get(c, "筛选/合并") for c in wl]


def watchlist_names_for(data: dict) -> List[str]:
    """为 watchlist 每项补全股票名（优先落盘；再 static 提示；最后行情解析）。"""
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
                code, name = _resolve_entry(raw_s, raw_s)
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in watching_store.py", exc_info=True)
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
        try:
            _c, name = _resolve_entry(code, hint)
            if name:
                names[i] = name
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in watching_store.py", exc_info=True)
            pass
    return names


def refresh_watchlist(
    watching: Optional[dict] = None,
    *,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """按 sources 刷新 watchlist；sources 为空时为手动模式，仅刷新名称不改名单。"""
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

    max_size = int(data.get("max_size") or 30)
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
            logger.debug("catch except Exception: in watching_store.py", exc_info=True)
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



def _resolve_sync_targets(codes: Optional[List[str]] = None) -> tuple:
    """解析加入纸面的目标代码，返回 (target, missing)。"""
    uni = read_watching()
    uni_wl = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
    if not uni_wl and codes is None:
        refresh = refresh_watchlist(uni)
        uni_wl = [str(c).strip() for c in (refresh.get("watchlist") or []) if str(c).strip()]

    if codes is None:
        return list(uni_wl), []

    wanted = [str(c).strip() for c in codes if str(c).strip()]
    if not wanted:
        raise ValueError("请勾选要加入纸面的股票")
    allowed = set(uni_wl)
    selected = [c for c in wanted if c in allowed]
    missing = [c for c in wanted if c not in allowed]
    if not selected:
        raise ValueError("所选代码均不在观察名单中")
    return selected, missing


def _sync_sizing_kwargs(
    *,
    lot_shares: Optional[int] = None,
    shares_by_code: Optional[Dict[str, Any]] = None,
    amount_per_code: Optional[float] = None,
    amount_by_code: Optional[Dict[str, Any]] = None,
    position_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """建仓定量：默认按金额；显式股数/金额/% 时按传入优先。"""
    from core.paper import DEFAULT_SYNC_AMOUNT, DEFAULT_SYNC_LOT_SHARES

    has_shares = lot_shares is not None or bool(shares_by_code)
    has_amount = (
        amount_per_code is not None
        or bool(amount_by_code)
        or position_pct is not None
    )
    if not has_shares and not has_amount:
        return {
            "lot_shares": 0,
            "shares_by_code": None,
            "amount_per_code": DEFAULT_SYNC_AMOUNT,
            "amount_by_code": None,
            "position_pct": None,
        }
    return {
        "lot_shares": int(lot_shares or DEFAULT_SYNC_LOT_SHARES) if has_shares else 0,
        "shares_by_code": shares_by_code,
        "amount_per_code": amount_per_code,
        "amount_by_code": amount_by_code,
        "position_pct": position_pct,
    }


def plan_sync_to_paper(
    paper_path: Optional[str] = None,
    *,
    codes: Optional[List[str]] = None,
    lot_shares: Optional[int] = None,
    shares_by_code: Optional[Dict[str, Any]] = None,
    amount_per_code: Optional[float] = None,
    amount_by_code: Optional[Dict[str, Any]] = None,
    position_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """加入模拟账户预览：每只买多少、合计花多少、剩余现金，不落盘。"""
    from core.paper import load_paper, plan_buy_codes

    target, missing = _resolve_sync_targets(codes)
    paper = load_paper(paper_path)
    plan = plan_buy_codes(
        paper,
        target,
        **_sync_sizing_kwargs(
            lot_shares=lot_shares,
            shares_by_code=shares_by_code,
            amount_per_code=amount_per_code,
            amount_by_code=amount_by_code,
            position_pct=position_pct,
        ),
    )
    plan["success"] = True
    plan["selected"] = codes is not None
    if missing:
        plan["missing"] = missing
    return plan


def sync_paper_watchlist(
    paper_path: Optional[str] = None,
    *,
    codes: Optional[List[str]] = None,
    buy: bool = False,
    lot_shares: Optional[int] = None,
    shares_by_code: Optional[Dict[str, Any]] = None,
    amount_per_code: Optional[float] = None,
    amount_by_code: Optional[Dict[str, Any]] = None,
    position_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """观察 → 模拟建仓：buy=True 时按现价假买进持仓。

    产品只维护 watching + holdings；不再写入 paper.watchlist。
    codes 为 None：全部观察；否则只处理所选子集（须在观察名单内）。
    buy=False：空操作（日更可保留该步骤；观察与仓位已分离）。
    """
    from core.paper import (
        append_operation_log,
        buy_codes_direct,
        holding_codes,
        load_paper,
        mark_to_market,
        save_paper,
    )

    target, missing = _resolve_sync_targets(codes)
    sizing = _sync_sizing_kwargs(
        lot_shares=lot_shares,
        shares_by_code=shares_by_code,
        amount_per_code=amount_per_code,
        amount_by_code=amount_by_code,
        position_pct=position_pct,
    )

    paper = load_paper(paper_path)
    trades: List[dict] = []
    skipped_buy: List[dict] = []
    if buy:
        result = buy_codes_direct(paper, target, **sizing)
        trades = result.get("trades") or []
        skipped_buy = [
            s
            for s in (result.get("skipped") or [])
            if s.get("reason") not in ("已持仓",)
        ]
        mark_to_market(paper)
        paper["updated_at"] = _now_iso()
        if trades:
            from core.paper.costs import fee_fields_from_trade

            for t in trades:
                fee_meta = fee_fields_from_trade(t)
                append_operation_log(
                    paper, "sync_paper",
                    detail=(
                        f"加入模拟 {t.get('stock_name') or t.get('stock_code')} "
                        f"{t.get('shares')}股 @ {t.get('price')}"
                    ),
                    meta={
                        "stock_code": t.get("stock_code"),
                        "stock_name": t.get("stock_name"),
                        "shares": t.get("shares"),
                        "price": t.get("price"),
                        "amount": t.get("amount") or t.get("actual_cost"),
                        "origin": t.get("origin") or "manual",
                        **fee_meta,
                    },
                )
    # 写入时剔除历史 paper.watchlist；buy=False 时也保存以完成迁移
    save_paper(paper, paper_path)

    held = holding_codes(paper)
    bought_n = len(trades)
    out: Dict[str, Any] = {
        "success": True,
        "holdings_count": len(held),
        "holdings_codes": held,
        "selected": codes is not None,
        "bought_count": bought_n,
        "new_trades": trades,
        "buy_skipped": skipped_buy,
    }
    parts = []
    if buy:
        if bought_n:
            parts.append(f"已买入 {bought_n} 只进持仓")
        parts.append(f"持仓共 {len(held)} 只")
        if skipped_buy:
            reasons = "；".join(
                f"{s.get('stock_code')}:{s.get('reason')}" for s in skipped_buy[:5]
            )
            parts.append(f"未买入 {len(skipped_buy)} 只（{reasons}）")
        if bought_n == 0 and not any(
            str(h.get("stock_code")) in target for h in (paper.get("holdings") or [])
        ):
            parts.insert(0, "未能加入持仓")
    else:
        parts.append(f"无需同步名单 · 持仓 {len(held)} 只（观察请在观察页维护）")
    if missing:
        out["skipped"] = missing
        parts.append(f"跳过 {len(missing)} 只不在观察中")
    out["message"] = " · ".join(parts)
    return out


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
    max_size = int(data.get("max_size") or 30)
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
            logger.debug("catch except Exception: in watching_store.py", exc_info=True)
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

