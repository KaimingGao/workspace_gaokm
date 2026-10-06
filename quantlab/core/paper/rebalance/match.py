"""调仓行情批量拉取与涨跌停/停牌撮合拦截。"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def quote_change_pct(quote: Optional[dict]) -> Optional[float]:
    """从行情 dict 解析当日涨跌幅（%）；缺则 None。"""
    if not isinstance(quote, dict):
        return None
    for key in ("change_raw", "change_pct", "pct_chg"):
        raw = quote.get(key)
        if raw is None:
            continue
        try:
            return round(float(raw), 2)
        except (TypeError, ValueError):
            continue
    raw = quote.get("change")
    if raw is None:
        return None
    try:
        return round(float(str(raw).replace("%", "").strip()), 2)
    except (TypeError, ValueError):
        return None


def attach_change_pct_to_rebalance_report(
    report: List[dict],
    *,
    summary: Optional[dict] = None,
) -> List[dict]:
    """给调仓报告行补 ``change_pct`` / ``price`` / ``prev_close``（相对昨收）。

    优先用 mark_to_market 持仓行；清仓/未入仓票再批量补行情。
    """
    rows = list(report or [])
    if not rows:
        return rows
    fields_by: Dict[str, dict] = {}

    def _merge(code: str, **kw) -> None:
        if not code:
            return
        cur = fields_by.setdefault(code, {})
        for k, v in kw.items():
            if v is not None and v != "":
                cur[k] = v

    for h in (summary or {}).get("holdings") or []:
        if not isinstance(h, dict):
            continue
        code = str(h.get("stock_code") or "").strip()
        if not code:
            continue
        _merge(
            code,
            change_pct=h.get("change_pct"),
            price=h.get("price"),
            prev_close=h.get("prev_close"),
        )

    missing = [
        str(r.get("stock_code") or "").strip()
        for r in rows
        if str(r.get("stock_code") or "").strip()
        and str(r.get("stock_code") or "").strip() not in fields_by
    ]
    if missing:
        quotes = _batch_query_quotes(missing)
        for code in missing:
            q = quotes.get(code) or {}
            chg = quote_change_pct(q)
            price = q.get("price_raw")
            if price is None:
                price = q.get("price")
            prev = (
                q.get("prev_close")
                or q.get("pre_close")
                or q.get("yesterday_close")
            )
            _merge(
                code,
                change_pct=chg,
                price=price,
                prev_close=prev,
            )

    for r in rows:
        code = str(r.get("stock_code") or "").strip()
        f = fields_by.get(code) or {}
        if f.get("change_pct") is not None:
            try:
                r["change_pct"] = round(float(f["change_pct"]), 2)
            except (TypeError, ValueError):
                pass
        if f.get("price") is not None:
            try:
                r["price"] = round(float(f["price"]), 4)
            except (TypeError, ValueError):
                r["price"] = f.get("price")
        if f.get("prev_close") is not None:
            try:
                r["prev_close"] = round(float(f["prev_close"]), 4)
            except (TypeError, ValueError):
                r["prev_close"] = f.get("prev_close")
    return rows


def _batch_query_quotes(codes: List[str], *, workers: int = 8) -> Dict[str, dict]:
    """批量行情：优先经 DataService；失败再线程池逐票。

    纸面回放经 ``paper_replay_context`` 注入时走 override，不打网。
    """
    if not codes:
        return {}
    uniq = list(dict.fromkeys(c for c in codes if c))
    if not uniq:
        return {}
    try:
        from core.paper.replay_ctx import replay_batch_query

        override = replay_batch_query()
    except Exception:  # noqa: BLE001 — 回放上下文不可用则走 live
        logger.debug("replay_batch_query lookup failed", exc_info=True)
        override = None
    if override is not None:
        try:
            got = dict(override(uniq) or {})
            return {c: (got.get(c) if isinstance(got.get(c), dict) else {}) for c in uniq}
        except Exception:  # noqa: BLE001 — 回放源失败返回空，勿打网
            logger.warning("replay batch_query failed", exc_info=True)
            return {c: {} for c in uniq}
    try:
        from core.data.service import get_default_service

        got = get_default_service().batch_get_quotes(uniq) or {}
        # 统一成 {code: quote}；补全未返回的 key
        out: Dict[str, dict] = {}
        for c in uniq:
            q = got.get(c)
            if isinstance(q, dict) and q.get("success") or isinstance(q, dict):
                out[c] = q
        if len(out) >= max(1, len(uniq) // 2):
            return out
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.warning("batch_query_quotes failed; fallback per-code", exc_info=True)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    from core.data.facade import get_quote

    out = {}
    with ThreadPoolExecutor(max_workers=min(workers, len(uniq))) as pool:
        futures = {pool.submit(get_quote, c): c for c in uniq}
        try:
            for fut in as_completed(futures, timeout=12):
                code = futures[fut]
                try:
                    out[code] = fut.result(timeout=0)
                except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                    out[code] = {}
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            for code, fut in futures.items():
                if code in out:
                    continue
                try:
                    out[code] = fut.result(timeout=0) if fut.done() else {}
                except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                    out[code] = {}
    return out


def _buy_match_block_reason(code: str, quote: Optional[dict]) -> Optional[str]:
    """Y2.2：无价/涨停/停牌关键词 → 跳过买入原因；否则 None。"""
    q = quote or {}
    try:
        from core.backtest.matching import is_limit_up, limit_up_threshold_for_code
        from core.market.calendar import halt_hint

        blob = " ".join(
            str(q.get(k) or "")
            for k in ("status", "trade_status", "stock_name", "name", "note", "message")
        )
        hint = halt_hint(blob)
        if hint.get("possible_halt"):
            return "停牌/不可交易提示，跳过买入"

        change = q.get("change_raw")
        if change is None:
            change = q.get("change_pct")
        if change is None:
            change = q.get("pct_chg")
        prev = q.get("prev_close") or q.get("pre_close") or q.get("yesterday_close")
        price = q.get("price_raw") or q.get("price")
        if prev is not None and price is not None:
            try:
                if is_limit_up(float(prev), float(price), stock_code=code):
                    return (
                        f"疑似涨停（阈值≥{limit_up_threshold_for_code(code)}%），跳过买入"
                    )
            except (TypeError, ValueError):
                pass
        elif change is not None:
            try:
                thr = limit_up_threshold_for_code(code)
                if float(change) >= thr:
                    return f"涨跌幅 {float(change):.2f}%≥涨停阈值 {thr}%，跳过买入"
            except (TypeError, ValueError):
                pass
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        return None
    return None


def _sell_match_block_reason(code: str, quote: Optional[dict]) -> Optional[str]:
    """P3-3：卖出侧涨跌停/停牌检查 — 跌停/停牌无法成交则跳过卖出；否则 None。"""
    q = quote or {}
    try:
        from core.backtest.matching import is_limit_down, limit_down_threshold_for_code
        from core.market.calendar import halt_hint

        blob = " ".join(
            str(q.get(k) or "")
            for k in ("status", "trade_status", "stock_name", "name", "note", "message")
        )
        hint = halt_hint(blob)
        if hint.get("possible_halt"):
            return "停牌/不可交易提示，跳过卖出"

        change = q.get("change_raw")
        if change is None:
            change = q.get("change_pct")
        if change is None:
            change = q.get("pct_chg")
        prev = q.get("prev_close") or q.get("pre_close") or q.get("yesterday_close")
        price = q.get("price_raw") or q.get("price")
        if prev is not None and price is not None:
            try:
                if is_limit_down(float(prev), float(price), stock_code=code):
                    return (
                        f"疑似跌停（阈值≤{limit_down_threshold_for_code(code)}%），跳过卖出"
                    )
            except (TypeError, ValueError):
                pass
        elif change is not None:
            try:
                thr = limit_down_threshold_for_code(code)
                if float(change) <= thr:
                    return f"涨跌幅 {float(change):.2f}%≤跌停阈值 {thr}%，跳过卖出"
            except (TypeError, ValueError):
                pass
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        return None
    return None
