"""分池持仓膨胀减仓：选码与可卖过滤。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def select_force_trim_codes(
    holdings: List[dict],
    *,
    score_by_code: Dict[str, Any],
    top_codes: set,
    trim_count: int,
) -> List[str]:
    """分池持仓膨胀减仓：优先卸中间带（不在目标簿），再卸簿内最低分。

    避免「簿内低分被砍 → 买入腿立刻买回 → 膨胀消不掉」。
    """
    n = max(0, int(trim_count or 0))
    if n <= 0 or not holdings:
        return []
    mid: List[tuple] = []
    book: List[tuple] = []
    for h in holdings:
        code = str(h.get("stock_code") or "").strip()
        if not code:
            continue
        sc = score_by_code.get(code)
        try:
            key = float(sc) if sc is not None else float("-inf")
        except (TypeError, ValueError):
            key = float("-inf")
        (book if code in top_codes else mid).append((code, key))
    mid.sort(key=lambda x: (x[1], x[0]))
    book.sort(key=lambda x: (x[1], x[0]))
    out: List[str] = []
    seen = set()
    for code, _ in mid + book:
        if code in seen:
            continue
        seen.add(code)
        out.append(code)
        if len(out) >= n:
            break
    return out


def select_force_trim_codes_sellable(
    holdings: List[dict],
    *,
    score_by_code: Dict[str, Any],
    top_codes: set,
    trim_count: int,
    quote_cache: Optional[Dict[str, dict]] = None,
    sell_block_fn=None,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """膨胀减仓：跳过跌停/停牌，继续选下一名可卖票。

    返回 (可卖 codes, 被挡 skips)。
    """
    n = max(0, int(trim_count or 0))
    blocked: List[Dict[str, Any]] = []
    if n <= 0 or not holdings:
        return [], blocked
    ordered = select_force_trim_codes(
        holdings,
        score_by_code=score_by_code,
        top_codes=top_codes,
        trim_count=len(holdings),  # 取全序，再按可卖性截断
    )
    out: List[str] = []
    qcache = quote_cache or {}
    for code in ordered:
        if len(out) >= n:
            break
        quote = qcache.get(code) or {}
        reason = None
        if sell_block_fn is not None:
            try:
                reason = sell_block_fn(code, quote)
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in paper_rebalance_force_trim.py", exc_info=True)
                reason = None
        if reason:
            blocked.append(
                {
                    "stock_code": code,
                    "reason": reason,
                    "score": score_by_code.get(code),
                    "path": "force_trim",
                }
            )
            continue
        out.append(code)
    return out, blocked
