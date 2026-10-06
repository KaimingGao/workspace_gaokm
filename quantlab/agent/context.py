"""Agent 会话上下文构建：把纸面持仓、观察池、近期决策、盘前简报注入 system prompt。

让 Agent 从「你问我答」变成「了解你当前状态的副驾驶」。
所有外部依赖（paper / watching / decisions）均延迟导入，避免启动期循环依赖。
"""

import logging

logger = logging.getLogger(__name__)
import os
import time
from typing import Any, Dict, List, Optional

from core.paths import SCHEDULE_LAST_RUN_PATH


def _fmt_pct(v: Any) -> str:
    try:
        f = float(v)
        return f"{f:+.2f}%"
    except (TypeError, ValueError):
        return "—"


def _load_paper_summary() -> Optional[str]:
    """纸面账户摘要：权益、总盈亏、持仓数、Top 持仓。"""
    try:
        from core.paper import load_paper, mark_to_market
        from core.paths import PAPER_PATH

        if not os.path.isfile(PAPER_PATH):
            return None
        paper = load_paper(PAPER_PATH)
        mtm = mark_to_market(paper)
        equity = mtm.get("total_equity") or mtm.get("equity")
        pnl = mtm.get("total_pnl_pct")
        holdings = mtm.get("holdings") or paper.get("holdings") or []
        lines = [
            f"纸面账户：权益 {equity or '—'}，总盈亏 {_fmt_pct(pnl)}，持仓 {len(holdings)} 只"
        ]
        top = []
        for h in holdings[:5]:
            name = h.get("stock_name") or h.get("stock_code")
            mv = h.get("market_value") or h.get("mv")
            pnl_pct = h.get("pnl_pct")
            top.append(f"{name}(市值{mv}, 浮盈{_fmt_pct(pnl_pct)})")
        if top:
            lines.append("主要持仓：" + "；".join(top))
        return "\n".join(lines)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("load_paper_summary failed", exc_info=True)
        return None


def _load_watchlist_summary() -> Optional[str]:
    """观察池摘要：数量 + 名称列表。"""
    try:
        from core.watching.store import read_watching

        w = read_watching()
        codes = w.get("watchlist") or []
        names = w.get("watchlist_names") or []
        if not codes:
            return None
        display = []
        for i, code in enumerate(codes[:12]):
            nm = names[i] if i < len(names) and names[i] else code
            display.append(str(nm))
        more = "" if len(codes) <= 12 else f" 等{len(codes)}只"
        return f"观察池（{len(codes)}只）：{'、'.join(display)}{more}"
    except Exception:  # noqa: BLE001
        logger.debug("load_watchlist_summary failed", exc_info=True)
        return None


def _decision_outcome(record: Dict[str, Any]) -> str:
    """轻量决策结果：若 facts 含价格且能取到现价，计算涨跌幅；否则标「待验证」。"""
    facts = record.get("facts") or {}
    price_at = None
    if isinstance(facts, dict):
        price_at = facts.get("price") or (facts.get("quote") or {}).get("price")
    horizon = record.get("horizon_days") or 3
    ts = record.get("ts") or 0
    age_days = (time.time() - ts) / 86400 if ts else 0
    if price_at is None or age_days < 1:
        return "待验证"
    try:
        from core.data.facade import get_quote

        code = record.get("stock_code")
        if not code:
            return "待验证"
        q = get_quote(str(code))
        if not isinstance(q, dict) or not q.get("success"):
            return "待验证"
        cur = q.get("price") or q.get("price_raw")
        if cur is None:
            return "待验证"
        pct = (float(cur) / float(price_at) - 1) * 100
        tag = "✓" if (pct > 0 and "买入" in str(record.get("stance_label") or "")) else ""
        tag = tag or ("✗" if (pct < 0 and "买入" in str(record.get("stance_label") or "")) else "")
        return f"{tag}{pct:+.1f}%（{age_days:.0f}天）"
    except Exception:  # noqa: BLE001
        return "待验证"


def _load_recent_decisions(limit: int = 6) -> Optional[str]:
    """近期决策记录 + 结果。"""
    try:
        from core.decision_record import list_decisions

        data = list_decisions(limit=limit)
        items = data.get("items") or []
        if not items:
            return None
        lines = [f"近期决策（最近{len(items)}条）："]
        for r in items:
            name = r.get("stock_name") or r.get("stock_code") or "?"
            stance = r.get("stance_label") or "?"
            outcome = _decision_outcome(r)
            ts = r.get("ts") or 0
            day = time.strftime("%m-%d", time.localtime(ts)) if ts else "?"
            lines.append(f"  {day} {name}：{stance} → {outcome}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        logger.debug("load_recent_decisions failed", exc_info=True)
        return None


def _load_briefing() -> Optional[str]:
    """最近一次调度简报（异动扫描 / 盘后复盘）。"""
    try:
        if not os.path.isfile(SCHEDULE_LAST_RUN_PATH):
            return None
        import json

        with open(SCHEDULE_LAST_RUN_PATH, encoding="utf-8") as f:
            data = json.load(f)
        kind = data.get("kind") or ""
        ts = data.get("ts") or 0
        day = time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "?"
        if kind == "watch_alert":
            alerts = data.get("alerts") or []
            if not alerts:
                return f"盘前简报（{day}）：扫描{data.get('scanned', 0)}只，无 ≥2% 异动"
            names = [f"{a.get('stock_name') or a.get('stock_code')}({_fmt_pct(a.get('change_percent'))})" for a in alerts[:6]]
            return f"盘前简报（{day}）：异动 {len(alerts)} 只 — {'、'.join(names)}"
        if kind == "daily_review":
            decs = data.get("decisions") or []
            return f"盘后复盘（{day}）：含 {len(decs)} 条决策"
        return None
    except Exception:  # noqa: BLE001
        logger.debug("load_briefing failed", exc_info=True)
        return None


def build_session_context() -> str:
    """构建注入 system prompt 的会话上下文块（无数据时返回空串）。"""
    parts: List[str] = []
    for loader in (_load_briefing, _load_paper_summary, _load_watchlist_summary, _load_recent_decisions):
        text = loader()
        if text:
            parts.append(text)
    if not parts:
        return ""
    return "## 当前会话上下文（自动加载，仅供你理解用户状态，不要逐字复述）\n" + "\n".join(parts)
