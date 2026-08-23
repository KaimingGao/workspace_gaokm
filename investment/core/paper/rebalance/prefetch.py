"""调仓批量预取：舆情 prior、卖侧行情/广度、买侧行情。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.paper.rebalance.match import _batch_query_quotes

logger = logging.getLogger(__name__)


def prefetch_sentiment_priors(
    *,
    holdings: Sequence[dict],
    top_items: Sequence[dict],
    skip_sentiment_prior: bool,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """卖/买前一次性批量拉取舆情 prior。

    返回 ``(prior_by_code, sentiment_prior_summary, prior_cfg_live)``。
    """
    prior_by_code: Dict[str, Any] = {}
    sentiment_prior_summary: Dict[str, Any] = {"ok": True, "skipped": True}
    prior_cfg_live: Dict[str, Any] = {"mode": "off"}
    try:
        from core.sentiment_prior import (
            check_sentiment_priors_for_codes,
            get_sentiment_prior_cfg,
        )

        prior_cfg_live = get_sentiment_prior_cfg()
        if skip_sentiment_prior:
            sentiment_prior_summary = {
                "ok": True,
                "skipped": True,
                "warnings": [],
                "blocks": [],
                "note": "确认落账复用预演·跳过舆情重拉",
            }
            prior_cfg_live = {**prior_cfg_live, "mode": "off", "scale_holds": False}
        elif str(prior_cfg_live.get("mode") or "off") != "off":
            hold_codes_all = [
                str(h.get("stock_code") or "").strip()
                for h in holdings
                if str(h.get("stock_code") or "").strip()
            ]
            buy_cand_codes = [
                str(x.get("stock_code") or "").strip()
                for x in top_items
                if str(x.get("stock_code") or "").strip() and not x.get("hard_reject")
            ]
            need_prior = list(buy_cand_codes)
            if prior_cfg_live.get("scale_holds") or prior_cfg_live.get("mode") == "gate":
                need_prior = list(dict.fromkeys([*hold_codes_all, *buy_cand_codes]))
            if need_prior:
                sentiment_prior_summary = check_sentiment_priors_for_codes(need_prior)
                prior_by_code = dict(sentiment_prior_summary.get("by_code") or {})
    except Exception as exc:
        logger.warning("batch sentiment prior failed: %s", exc, exc_info=True)
        sentiment_prior_summary = {"ok": True, "error": str(exc), "by_code": {}}
    return prior_by_code, sentiment_prior_summary, prior_cfg_live


def prefetch_sell_quotes_and_breadth(
    holdings: Sequence[dict],
    *,
    batch_query=_batch_query_quotes,
) -> Tuple[List[str], Dict[str, dict], Dict[str, Optional[float]]]:
    """卖侧批量行情 + 板块开盘缺口广度。

    返回 ``(sell_codes, quote_cache, sector_breadth_by_code)``。
    ``batch_query`` 默认本模块；编排侧可传入 facade 绑定以保留 patch 路径。
    """
    sell_codes = [str(h.get("stock_code") or "") for h in holdings if h.get("stock_code")]
    quote_cache: Dict[str, dict] = batch_query(sell_codes)
    sector_breadth_by_code: Dict[str, Optional[float]] = {}
    try:
        from core.event_prior import compute_sector_gap_breadth_live, get_event_prior_cfg

        epcfg = get_event_prior_cfg()
        if str(epcfg.get("mode") or "off") != "off" and sell_codes:
            for code in sell_codes:
                br = compute_sector_gap_breadth_live(
                    sell_codes,
                    gap_trigger_pct=float(epcfg.get("gap_trigger_pct") or 2.0),
                    quotes=quote_cache,
                    focus_code=code,
                    use_sector_peers=True,
                )
                sector_breadth_by_code[code] = br.get("breadth")
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug(
            "catch except Exception: in paper_rebalance_prefetch.py",
            exc_info=True,
        )
        sector_breadth_by_code = {}
    return sell_codes, quote_cache, sector_breadth_by_code


def prefetch_buy_quotes(
    *,
    holdings: Sequence[dict],
    top_items: Sequence[dict],
    batch_query=_batch_query_quotes,
) -> Dict[str, dict]:
    """买侧候选 + 舆情缩仓待补回行情。"""
    restore_codes = [
        str(h.get("stock_code") or "")
        for h in holdings
        if h.get("sentiment_trim_base_shares") is not None
        and str(h.get("stock_code") or "").strip()
    ]
    buy_codes = [
        str(it.get("stock_code") or "")
        for it in top_items
        if it.get("stock_code") and not it.get("hard_reject")
    ]
    return batch_query(list(dict.fromkeys([*restore_codes, *buy_codes])))
