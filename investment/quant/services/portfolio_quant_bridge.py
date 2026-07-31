"""模拟持仓 ↔ 量化联动摘要（只读、不下单）。

持仓来源：paper.json（对照仓 portfolio.json 已下线）。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Set

from core.paths import PAPER_PATH, WATCHING_PATH


def _stance_summary(advice: List[dict]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for row in advice or []:
        label = str(row.get("stance_label") or "—").strip() or "—"
        out[label] = out.get(label, 0) + 1
    return out


def build_portfolio_quant_bridge(
    *,
    paper_path: Optional[str] = None,
    include_stance: bool = False,
) -> Dict[str, Any]:
    """聚合 paper 持仓 / watching / quant 状态，供 Web 与 quant(task=portfolio_bridge)。"""
    paper_p = paper_path or PAPER_PATH

    holdings_block: Dict[str, Any] = {
        "exists": False,
        "path": paper_p,
        "count": 0,
        "codes": [],
        "cash": None,
        "total_equity": None,
        "stance_summary": {},
        "source": "paper",
    }
    codes: List[str] = []
    paper_block: Dict[str, Any] = {
        "initialized": os.path.isfile(paper_p),
        "path": paper_p,
        "position_count": 0,
    }

    if paper_block["initialized"]:
        try:
            from core.paper import load_paper

            paper = load_paper(paper_p)
            holdings = paper.get("holdings") or []
            paper_block["position_count"] = len(holdings)
            holdings_block["exists"] = True
            holdings_block["cash"] = paper.get("cash")
            for h in holdings:
                code = str(h.get("stock_code") or h.get("code") or "").strip()
                if code and code not in codes:
                    codes.append(code)
            holdings_block["codes"] = codes
            holdings_block["count"] = len(codes)
        except Exception as e:
            paper_block["error"] = str(e)
            holdings_block["error"] = str(e)

    if include_stance and holdings_block["exists"] and not holdings_block.get("error"):
        try:
            from skills.position.engine import PositionEngine

            adv = PositionEngine().advise(
                {
                    "paper_path": paper_p,
                    "include_stance": True,
                    "horizon_days": 3,
                }
            )
            if adv.get("success"):
                holdings_block["total_equity"] = adv.get("total_equity")
                holdings_block["stance_summary"] = _stance_summary(adv.get("advice") or [])
        except Exception as e:
            holdings_block["stance_error"] = str(e)

    watchlist: List[str] = []
    watching_exists = os.path.isfile(WATCHING_PATH)
    if watching_exists:
        try:
            from core.watching_store import read_watching

            watchlist = list(read_watching(WATCHING_PATH).get("watchlist") or [])
        except Exception:
            watchlist = []

    watch_set: Set[str] = set(watchlist)
    overlap = [c for c in codes if c in watch_set]
    outside = [c for c in codes if c not in watch_set]

    daily_empty = True
    try:
        from quant.services.quant_service import QuantService

        saved = QuantService().load_last_daily()
        daily_empty = bool(saved.get("empty"))
    except Exception:
        daily_empty = True

    quant_block: Dict[str, Any] = {
        "daily_empty": daily_empty,
        "watching_exists": watching_exists,
        "watching_watchlist_count": len(watchlist),
        "overlap_count": len(overlap),
        "overlap_codes": overlap,
        "outside_watching_codes": outside,
    }

    can_compare = paper_block["initialized"] and not paper_block.get("error")

    notes: List[str] = []
    if not paper_block["initialized"]:
        notes.append("模拟账户未初始化")
    elif not codes:
        notes.append("模拟持仓为空")
    if outside:
        notes.append(f"{len(outside)} 只持仓不在 watching watchlist")
    if daily_empty:
        notes.append("尚无 quant_daily，可先跑 daily preset quant")
    if can_compare:
        notes.append("模拟账户可用")

    return {
        "success": True,
        "task": "portfolio_bridge",
        "readonly": True,
        # 兼容字段名 portfolio = 当前模拟持仓摘要（非对照仓文件）
        "portfolio": holdings_block,
        "paper": paper_block,
        "quant": quant_block,
        "actions": {},
        "note": "；".join(notes) if notes else "模拟持仓与量化状态正常。",
    }
