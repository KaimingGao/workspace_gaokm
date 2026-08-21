"""同行对比：预设同业组 + quote/估值摘要。"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from core.ports.market import query_quote, resolve_market_code, resolve_symbol

logger = logging.getLogger(__name__)

PEER_GROUPS: Dict[str, List[str]] = {
    "白酒": ["贵州茅台", "五粮液", "泸州老窖", "山西汾酒"],
    "银行": ["招商银行", "工商银行", "建设银行", "平安银行"],
    "新能源": ["宁德时代", "比亚迪", "隆基绿能"],
    "互联网港股": ["腾讯", "美团", "快手", "小米"],
}


def detect_group(stock_code: str, sector: Optional[str] = None) -> Tuple[Optional[str], List[str]]:
    if sector and sector in PEER_GROUPS:
        return sector, list(PEER_GROUPS[sector])

    symbol = resolve_symbol(stock_code) or ""
    raw = (stock_code or "").strip()
    for name, members in PEER_GROUPS.items():
        for m in members:
            if raw == m or raw in m or m in raw:
                return name, list(members)
            ms = resolve_symbol(m)
            if ms and symbol and ms == symbol:
                return name, list(members)
    return None, []


def _quote_row(code: str) -> dict:
    q = query_quote(code)
    if not q.get("success"):
        return {"stock_code": code, "success": False, "error": q.get("error")}
    return {
        "success": True,
        "stock_code": q.get("stock_code"),
        "stock_name": q.get("stock_name"),
        "price": q.get("price"),
        "price_raw": q.get("price_raw"),
        "change": q.get("change"),
        "change_raw": q.get("change_raw"),
        "market": q.get("market"),
    }


def build_peer_compare(stock_code: str, sector: Optional[str] = None) -> dict:
    group, members = detect_group(stock_code, sector)
    if not members:
        return {
            "success": False,
            "error": "未能匹配同行组，请指定 sector=白酒/银行/新能源/互联网港股",
            "stock_code": stock_code,
        }

    # 确保目标在名单中
    target_symbol = resolve_symbol(stock_code)
    if stock_code not in members and not any(
        resolve_symbol(m) == target_symbol for m in members
    ):
        members = [stock_code] + members

    rows = []
    for m in members[:8]:
        rows.append(_quote_row(m))

    ok = [r for r in rows if r.get("success")]
    if not ok:
        return {"success": False, "error": "同行行情全部失败", "group": group}

    ranked = sorted(ok, key=lambda x: x.get("change_raw") or 0, reverse=True)

    # A 股尝试补 PE（可选，失败忽略）
    pe_notes = []
    try:
        from skills.fundamentals.engine import fetch_cn_spot_row, spot_to_metrics

        for r in ranked:
            market, code = resolve_market_code(str(r.get("stock_code")))
            if market == "CN" and code:
                row = fetch_cn_spot_row(code)
                if row:
                    m = spot_to_metrics(row)
                    r["pe"] = m.get("pe")
                    r["pb"] = m.get("pb")
    except Exception as e:
        logger.exception('unexpected error in build_peer_compare')
        pe_notes.append(f"估值补充失败: {e}")

    target_row = None
    for r in ranked:
        if resolve_symbol(str(r.get("stock_name") or "")) == target_symbol:
            target_row = r
            break
        if resolve_symbol(str(r.get("stock_code") or "")) == target_symbol:
            target_row = r
            break
    if target_row is None and ranked:
        # 用输入再查一次对齐
        tq = _quote_row(stock_code)
        if tq.get("success"):
            target_row = tq

    summary_lines = [f"同行组「{group}」共对比 {len(ranked)} 只（按当日涨跌排序）："]
    for r in ranked:
        extra = ""
        if r.get("pe") is not None:
            extra += f" / PE {r['pe']}"
        if r.get("pb") is not None:
            extra += f" / PB {r['pb']}"
        summary_lines.append(
            f"- {r.get('stock_name')}({r.get('stock_code')}): {r.get('price')} / {r.get('change')}{extra}"
        )
    if target_row:
        summary_lines.append(
            f"目标 {target_row.get('stock_name')} 当日涨跌 {target_row.get('change')}，"
            f"在组内排名约第 {ranked.index(target_row)+1 if target_row in ranked else '-'}。"
        )

    return {
        "success": True,
        "group": group,
        "target": target_row,
        "peers": ranked,
        "summary": "\n".join(summary_lines),
        "notes": pe_notes,
        "note": "以上为同行对比数据，供投顾建议使用；市场有风险，不保证收益。",
    }
