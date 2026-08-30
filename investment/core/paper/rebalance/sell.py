"""调仓卖出腿：清仓/缩仓、膨胀减仓、账户风控超额减仓。

由 ``simulate_cross_section_rebalance`` 在预取后调用；原地更新 ``RebalanceState``。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.paper.ledger import ORIGIN_STRATEGY, _now_iso, append_operation_log
from core.paper.costs import annotate_trade, apply_fill_price, calc_trade_fees
from core.paper.rebalance.force_trim import (
    select_force_trim_codes,
    select_force_trim_codes_sellable,
)
from core.paper.rebalance.match import (
    _batch_query_quotes,
    _sell_match_block_reason,
    quote_change_pct,
)
from core.paper.rebalance.reasons import (
    _SOFT_HOLD_ELIGIBLE_REASONS,
    SELL_REASON_BELOW_HOLD,
    SELL_REASON_HARD_REJECT,
    SELL_REASON_MARKET_TRIM,
    SELL_REASON_NOT_IN_TOPK,
    SELL_REASON_SENTIMENT_TRIM,
)
from core.paper.rebalance.state import RebalanceState
from core.ports.market import quote_price as _quote_price
from core.t0.intraday import load_rebalance_t0_sell_blocks

logger = logging.getLogger(__name__)


def run_sell_leg(state: RebalanceState) -> None:
    """执行卖出腿（含风控超额减仓），写回 state。"""
    paper = state.paper
    holdings = list(state.holdings)
    cash = float(state.cash)
    equity_before = state.equity_before
    cost_model = state.cost_model
    fee_params = state.fee_params
    respect_max_positions = state.respect_max_positions
    min_hold_score = state.min_hold_score
    max_positions = state.max_positions
    max_turnover_pct = state.max_turnover_pct
    skip_sentiment_prior = state.skip_sentiment_prior
    skip_market_prior = state.skip_market_prior
    _tracks_cfg = state.tracks_cfg
    indexes = state.indexes
    top_items = indexes.top_items
    top_codes = indexes.top_codes
    item_by_code = indexes.item_by_code
    score_by_code = indexes.score_by_code
    tau_by_code = indexes.tau_by_code
    hard_reject_by_code = indexes.hard_reject_by_code
    prior_by_code = state.prior_by_code
    prior_cfg_live = state.prior_cfg_live
    _quote_cache = state.quote_cache
    _sector_breadth_by_code = state.sector_breadth_by_code
    sell_trades = state.sell_trades
    kept = state.kept
    risk_gate = state.risk_gate
    force_trim_sold = state.force_trim_sold
    force_trim_cut_in_book = state.force_trim_cut_in_book
    force_trim_no_rebuy = state.force_trim_no_rebuy
    sell_match_skips = state.sell_match_skips
    event_prior_soft_holds = state.event_prior_soft_holds
    turnover_capped = state.turnover_capped
    buy_trades = state.buy_trades
    sentiment_restore_trades = state.sentiment_restore_trades
    buys_blocked = state.buys_blocked
    risk_budget_skips = state.risk_budget_skips
    mid_summary = state.mid_summary
    risk_limits = state.risk_limits
    _tau_floor_meta = state.tau_floor_meta
    _tau_floor = state.tau_floor
    sentiment_prior_summary = state.sentiment_prior_summary

    t0_sell_blocks = load_rebalance_t0_sell_blocks()

    def _t0_rebalance_block(code: str) -> Optional[str]:
        return t0_sell_blocks.get(str(code or "").strip())

    def _record_t0_open_leg_skip(code: str, h: dict, *, path: str) -> None:
        reason = _t0_rebalance_block(code)
        if not reason:
            return
        sell_match_skips.append(
            {
                "stock_code": code,
                "stock_name": h.get("stock_name"),
                "reason": reason,
                "score": score_by_code.get(code),
                "path": path,
            }
        )

    for h in holdings:
        code = str(h.get("stock_code") or "")
        shares = float(h.get("shares") or 0)
        cost = float(h.get("cost") or 0)
        if not code or shares <= 0:
            continue

        score = score_by_code.get(code)
        in_top = code in top_codes
        reason = None
        reason_tag = None  # 结构化标识，用于 soft-hold 判断
        src_item = item_by_code.get(code) or {}
        if not respect_max_positions:
            # 分池滞回：双轨 hold —— predicted 用 ŷ_trade；heuristic 用 0–100
            if code in hard_reject_by_code:
                reason = hard_reject_by_code[code]
                reason_tag = SELL_REASON_HARD_REJECT
            else:
                try:
                    from core.signal.rebalance_tracks import hold_decision_for_item

                    should_sell, dec_sc, hold_f, track = hold_decision_for_item(
                        src_item or {"score": score, "predicted_score_blend": score},
                        tracks_cfg=_tracks_cfg,
                        predicted_hold_floor=min_hold_score,
                    )
                    if dec_sc is not None:
                        score = dec_sc
                        score_by_code[code] = dec_sc
                    if should_sell:
                        if track == "heuristic":
                            reason = (
                                f"heuristic 低于卖出门槛 min_hold({hold_f})"
                            )
                        else:
                            reason = (
                                f"ŷ_trade 低于卖出门槛 min_hold({hold_f})"
                            )
                        reason_tag = SELL_REASON_BELOW_HOLD
                except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                    logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
                    if score is not None and score < min_hold_score:
                        reason = f"ŷ_trade 低于卖出门槛 min_hold({min_hold_score})"
                        reason_tag = SELL_REASON_BELOW_HOLD
        else:
            if code in hard_reject_by_code:
                reason = hard_reject_by_code[code]
                reason_tag = SELL_REASON_HARD_REJECT
            elif not in_top:
                reason = "不在横截面 TopK"
                reason_tag = SELL_REASON_NOT_IN_TOPK
            else:
                try:
                    from core.signal.rebalance_tracks import hold_decision_for_item

                    should_sell, dec_sc, hold_f, track = hold_decision_for_item(
                        src_item or {"score": score, "predicted_score_blend": score},
                        tracks_cfg=_tracks_cfg,
                        predicted_hold_floor=min_hold_score,
                    )
                    if dec_sc is not None:
                        score = dec_sc
                    if should_sell:
                        reason = (
                            f"heuristic<{hold_f}"
                            if track == "heuristic"
                            else f"ŷ_trade 低于 min_hold_score({hold_f})"
                        )
                        reason_tag = SELL_REASON_BELOW_HOLD
                except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                    logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
                    if score is not None and score < min_hold_score:
                        reason = f"ŷ_trade 低于 min_hold_score({min_hold_score})"
                        reason_tag = SELL_REASON_BELOW_HOLD

        # 早盘 path_matrix 卖闸（默认开）：pending_exit 本窗 defer；λ 缩卖出比例
        _path_sell_frac = 1.0
        if reason:
            try:
                from core.paper.rebalance.path_matrix import (
                    get_path_matrix_cfg,
                    sell_execution_gate,
                )

                _pm_cfg = get_path_matrix_cfg(paper=paper)
                if _pm_cfg.get("enabled"):
                    _pm_sell = sell_execution_gate(
                        src_item or {"score": score, "y_trade": score},
                        w=1.0,
                        w_star_day=0.0,
                        in_topk=bool(in_top),
                        cfg=_pm_cfg,
                        buy_floor=None,
                        hold_floor=min_hold_score,
                    )
                    if _pm_sell.get("defer") or not _pm_sell.get("allow"):
                        event_prior_soft_holds.append(
                            {
                                "stock_code": code,
                                "path_matrix_defer": True,
                                "path_matrix_action": _pm_sell.get("action"),
                                "reason": _pm_sell.get("reason")
                                or "path_matrix 本窗推迟卖出",
                            }
                        )
                        reason = None
                        reason_tag = None
                        kept.append(h)
                        continue
                    try:
                        _path_sell_frac = max(
                            0.0, min(1.0, float(_pm_sell.get("sell_fraction") or 1.0))
                        )
                    except (TypeError, ValueError):
                        _path_sell_frac = 1.0
            except Exception:  # noqa: BLE001
                logger.debug("path_matrix sell gate failed", exc_info=True)
                _path_sell_frac = 1.0

        # P1：主题开盘缺口 / rem / 舆情看多 · 卖出改为 soft hold（不改 ŷ）
        # 分池滞回卖因「低于*」；横截面另有「不在 TopK」——主题日同样保护，避免踏空
        _soft_hold_eligible = bool(
            reason and reason_tag in _SOFT_HOLD_ELIGIBLE_REASONS
        )
        if _soft_hold_eligible:
            try:
                from core.event_prior import (
                    build_event_prior_from_quote,
                    get_event_prior_cfg,
                    should_soft_hold_for_low_score,
                )
                from core.sentiment_prior import should_soft_hold_from_sentiment
                from core.research.tau_ridge import predict_tau_from_features

                ep_cfg = get_event_prior_cfg()
                rem_yhat = None
                q_ep = _quote_cache.get(code) or {}
                _sector_breadth = _sector_breadth_by_code.get(code)
                if str(ep_cfg.get("mode") or "off") != "off":
                    try:
                        from core.event_prior import gap_pct_from_quote_bars

                        gap_v = gap_pct_from_quote_bars(
                            q_ep if q_ep.get("success") else None
                        )
                        feats = {
                            "gap_pct": gap_v,
                            "open_gap": gap_v,
                            "sector_gap_breadth": _sector_breadth,
                            "theme_day": 1.0
                            if (
                                gap_v is not None
                                and float(gap_v) >= float(ep_cfg.get("gap_trigger_pct") or 2)
                            )
                            else 0.0,
                        }
                        rem_yhat = tau_by_code.get(code)
                        if rem_yhat is None:
                            rem_yhat = predict_tau_from_features(feats)
                    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                        logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
                        rem_yhat = tau_by_code.get(code)
                    ep = build_event_prior_from_quote(
                        q_ep if q_ep.get("success") else None,
                        sector_breadth=_sector_breadth,
                        rem_yhat=rem_yhat,
                        stock_code=code,
                    )
                    sent_soft = should_soft_hold_from_sentiment(
                        prior_by_code.get(code)
                    )
                    y_conflict = False
                    try:
                        from core.signal.y_state import stamp_y_state

                        # 卖出保护：双头分歧时暂缓因低分清仓
                        sig_row = {
                            "predicted_score": None,
                            "predicted_score_eod_rem": None,
                            "predicted_score_tau": rem_yhat,
                            "gap_pct": ep.get("gap_pct") if isinstance(ep, dict) else None,
                            "dual_score_window": "intraday",
                        }
                        # 尽量用持仓/评分上已有字段
                        for src in (h,):
                            if isinstance(src, dict):
                                for k in (
                                    "predicted_score",
                                    "predicted_score_eod",
                                    "predicted_score_eod_rem",
                                    "predicted_score_tau",
                                    "predicted_score_blend",
                                    "score_rem",
                                    "dual_score_head",
                                    "y_check",
                                ):
                                    if src.get(k) is not None:
                                        sig_row[k] = src.get(k)
                        if sig_row.get("y_check") is None:
                            stamp_y_state(sig_row)
                        y_conflict = str(sig_row.get("y_check") or "") == "conflict"
                    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                        logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
                        y_conflict = False
                    if should_soft_hold_for_low_score(ep) or sent_soft or y_conflict:
                        reason = None
                        kept.append(h)
                        event_prior_soft_holds.append(
                            {
                                "stock_code": code,
                                "gap_pct": ep.get("gap_pct") if isinstance(ep, dict) else None,
                                "sector_breadth": _sector_breadth,
                                "rem_yhat": rem_yhat,
                                "sentiment_soft_hold": bool(sent_soft),
                                "y_check_soft_hold": bool(y_conflict),
                                "y_check": "conflict" if y_conflict else None,
                                "warnings": list((ep or {}).get("warnings") or []),
                            }
                        )
                        continue
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
                logger.warning("sell_loop event_prior failed for %s", code, exc_info=True)

        sell_shares = shares
        keep_shares = 0.0
        sell_note = None
        sentiment_prior_trim = False
        market_prior_trim = False
        if reason and _path_sell_frac < 1.0 - 1e-9:
            sell_shares = float(shares) * float(_path_sell_frac)
            keep_shares = float(shares) - float(sell_shares)
            sell_note = f"path_matrix λ卖出{_path_sell_frac:.0%}"

        # 市场级 prior：主动缩仓（不改 ŷ）
        if not reason and isinstance(src_item, dict) and not skip_market_prior:
            try:
                from core.market.prior_policy import apply_market_priors_to_hold

                mp_hold = apply_market_priors_to_hold(src_item, shares=shares)
                if mp_hold.get("trim") and float(mp_hold.get("sell_shares") or 0) > 0:
                    sell_shares = float(mp_hold["sell_shares"])
                    keep_shares = float(mp_hold.get("keep_shares") or 0)
                    sell_note = "市场 prior 缩仓"
                    market_prior_trim = True
                    reason = mp_hold.get("reason") or "market_prior_trim"
                    reason_tag = SELL_REASON_MARKET_TRIM
            except Exception:  # noqa: BLE001
                logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
                logger.warning("sell_loop market_prior hold failed for %s", code, exc_info=True)

        if not reason:
            # 舆情先验：gate + scale_holds → 已持仓缩至 scale_buy_pct（不改 ŷ）
            if str(prior_cfg_live.get("mode") or "off") == "off" or not prior_cfg_live.get(
                "scale_holds"
            ):
                kept.append(h)
                continue
            try:
                from core.sentiment_prior import apply_prior_to_hold

                prior = prior_by_code.get(code)
                if not isinstance(prior, dict):
                    kept.append(h)
                    continue
                hold_apply = apply_prior_to_hold(prior, shares=shares)
                if hold_apply.get("trim") and float(hold_apply.get("sell_shares") or 0) > 0:
                    sell_shares = float(hold_apply["sell_shares"])
                    keep_shares = float(hold_apply.get("keep_shares") or 0)
                    scale_h = hold_apply.get("scale")
                    sell_note = (
                        f"舆情先验缩仓至 {float(scale_h):.0%}"
                        if scale_h is not None
                        else "舆情先验缩仓"
                    )
                    sentiment_prior_trim = True
                    reason = hold_apply.get("reason") or "sentiment_prior_bearish"
                    reason_tag = SELL_REASON_SENTIMENT_TRIM
                else:
                    kept.append(h)
                    continue
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
                logger.warning("sell_loop sentiment_prior failed for %s", code, exc_info=True)
                kept.append(h)
                continue

        quote = _quote_cache.get(code) or {}
        price = _quote_price(quote) if quote.get("success") else None
        if not price or price <= 0:
            kept.append(h)
            continue

        # P3-3：跌停/停牌无法成交 → 跳过卖出，保留持仓
        _sell_block = _sell_match_block_reason(code, quote)
        if _sell_block:
            sell_match_skips.append({
                "stock_code": code,
                "stock_name": h.get("stock_name"),
                "reason": _sell_block,
                "score": score,
                "path": "main",
            })
            kept.append(h)
            continue

        if _t0_rebalance_block(code):
            _record_t0_open_leg_skip(code, h, path="t0_open_leg")
            kept.append(h)
            continue

        from core.paper.tplus1 import TPLUS1_LOCK_REASON, clip_sell_shares, consume_sell_lots

        sell_shares, t1_meta = clip_sell_shares(h, sell_shares)
        if sell_shares <= 1e-9:
            sell_match_skips.append({
                "stock_code": code,
                "stock_name": h.get("stock_name"),
                "reason": t1_meta.get("reason") or TPLUS1_LOCK_REASON,
                "score": score,
                "path": "tplus1",
            })
            kept.append(h)
            continue

        pnl_pct = round((price / cost - 1.0) * 100.0, 2) if cost else None
        fill_px = apply_fill_price(
            "sell", float(price), model=cost_model, params=fee_params
        )
        amount = round(sell_shares * fill_px, 2)
        fee_info = calc_trade_fees(
            "sell", amount, model=cost_model, params=fee_params
        )
        note = (
            sell_note
            if sentiment_prior_trim or market_prior_trim
            else (
                f"分池调仓卖出：{reason}"
                if not respect_max_positions
                else f"横截面调仓卖出：{reason}"
            )
        )
        trade = annotate_trade(
            {
                "ts": _now_iso(),
                "side": "sell",
                "stock_code": code,
                "stock_name": h.get("stock_name") or quote.get("stock_name"),
                "shares": sell_shares,
                "price": round(fill_px, 4),
                "amount": amount,
                "pnl_pct": pnl_pct,
                "score": score,
                "origin": ORIGIN_STRATEGY,
                "note": note,
                "sentiment_prior": bool(sentiment_prior_trim),
                "market_prior": bool(market_prior_trim),
            },
            fee_info,
        )
        paper.setdefault("trades", []).append(trade)
        sell_trades.append(trade)
        cash = round(cash + float(fee_info["net_cash_delta"]), 2)
        consume_sell_lots(h, sell_shares)
        if float(h.get("shares") or 0) > 1e-6:
            kept.append(h)

    paper["holdings"] = kept
    paper["cash"] = round(cash, 2)

    # 分池滞回：持仓膨胀时优先卸中间带，再卸簿内最低分；跌停则换下一可卖票
    if not respect_max_positions and len(kept) > max_positions:
        from core.paper.tplus1 import TPLUS1_LOCK_REASON, consume_sell_lots, is_fully_sellable

        kept_by_code = {str(x.get("stock_code") or ""): x for x in kept}

        def _force_trim_sell_block(code, quote):
            r = _sell_match_block_reason(code, quote)
            if r:
                return r
            t0_r = _t0_rebalance_block(str(code or ""))
            if t0_r:
                return t0_r
            row = kept_by_code.get(str(code or ""))
            if row is not None and not is_fully_sellable(row):
                return TPLUS1_LOCK_REASON
            return None

        trim_count = len(kept) - max_positions
        sellable, trim_blocked = select_force_trim_codes_sellable(
            kept,
            score_by_code=score_by_code,
            top_codes=top_codes,
            trim_count=trim_count,
            quote_cache=_quote_cache,
            sell_block_fn=_force_trim_sell_block,
        )
        for b in trim_blocked:
            sell_match_skips.append(
                {
                    "stock_code": b.get("stock_code"),
                    "stock_name": next(
                        (
                            h.get("stock_name")
                            for h in kept
                            if str(h.get("stock_code") or "") == b.get("stock_code")
                        ),
                        None,
                    ),
                    "reason": b.get("reason"),
                    "score": b.get("score"),
                    "path": "force_trim",
                }
            )
        if len(sellable) < trim_count:
            warns = list(risk_gate.get("warnings") or [])
            msg = (
                f"分池膨胀减仓：需卸 {trim_count} 只，可卖 {len(sellable)} 只"
                f"（{len(trim_blocked)} 只跌停/停牌跳过）"
            )
            if msg not in warns:
                warns.append(msg)
            risk_gate["warnings"] = warns
            risk_gate["force_trim_incomplete"] = True
        force_sell_codes = set(sellable)
        new_kept = []
        force_sell_trades: List[dict] = []
        for h in kept:
            code = str(h.get("stock_code") or "")
            if code in force_sell_codes:
                shares = float(h.get("shares") or 0)
                cost = float(h.get("cost") or 0)
                if shares <= 0:
                    new_kept.append(h)
                    continue
                quote = _quote_cache.get(code) or {}
                price = _quote_price(quote) if quote.get("success") else None
                if not price or price <= 0:
                    new_kept.append(h)
                    continue
                band = "中间带" if code not in top_codes else "簿内"
                if code in top_codes:
                    force_trim_no_rebuy.add(code)
                pnl_pct = round((price / cost - 1.0) * 100.0, 2) if cost else None
                fill_px = apply_fill_price("sell", float(price), model=cost_model, params=fee_params)
                amount = round(shares * fill_px, 2)
                fee_info = calc_trade_fees("sell", amount, model=cost_model, params=fee_params)
                trade = annotate_trade(
                    {
                        "ts": _now_iso(),
                        "side": "sell",
                        "stock_code": code,
                        "stock_name": h.get("stock_name"),
                        "shares": shares,
                        "price": round(fill_px, 4),
                        "amount": amount,
                        "pnl_pct": pnl_pct,
                        "score": score_by_code.get(code),
                        "origin": ORIGIN_STRATEGY,
                        "note": f"分池持仓膨胀强制减仓·{band}（持仓 {len(kept)} > 簿长 {max_positions}）",
                    },
                    fee_info,
                )
                paper.setdefault("trades", []).append(trade)
                sell_trades.append(trade)
                force_sell_trades.append(trade)
                cash = round(cash + float(fee_info["net_cash_delta"]), 2)
                consume_sell_lots(h, shares)
                if float(h.get("shares") or 0) > 1e-6:
                    new_kept.append(h)
            else:
                new_kept.append(h)
        kept = new_kept
        paper["holdings"] = kept
        paper["cash"] = round(cash, 2)
        force_trim_sold = {
            str(t.get("stock_code") or "") for t in force_sell_trades if t.get("stock_code")
        }
        force_trim_cut_in_book = any(c in top_codes for c in force_trim_sold)
        if force_sell_trades:
            if force_trim_no_rebuy:
                warns = list(risk_gate.get("warnings") or [])
                msg = (
                    "膨胀减仓已卸簿内 "
                    + ",".join(sorted(force_trim_no_rebuy))
                    + "，本轮不买回"
                )
                if msg not in warns:
                    warns.append(msg)
                risk_gate["warnings"] = warns
            append_operation_log(
                paper,
                "force_trim",
                detail=f"分池持仓膨胀强制减仓 {len(force_sell_trades)} 只至簿长 {max_positions}",
                meta={
                    "trim_count": len(force_sell_trades),
                    "max_positions": max_positions,
                    "codes": [str(t.get("stock_code")) for t in force_sell_trades],
                    "prefer_mid_band": True,
                    "cut_in_book": force_trim_cut_in_book,
                    "path": "cluster",
                },
            )
            if force_trim_cut_in_book:
                warns = list(risk_gate.get("warnings") or [])
                msg = "膨胀减仓卸了簿内票（中间带不可卖）：本轮不买回，避免空转"
                if msg not in warns:
                    warns.append(msg)
                risk_gate["warnings"] = warns

    buy_trades: List[dict] = []
    sentiment_restore_trades: List[dict] = []
    held_codes = {str(h.get("stock_code")) for h in kept}
    holdings = kept
    buys_blocked = False
    risk_budget_skips: List[dict] = []
    mid_summary: Dict[str, Any] = {}
    risk_limits: Dict[str, Any] = {}
    _tau_floor_meta: Dict[str, Any] = {}
    _tau_floor: Optional[float] = None
    # 膨胀减仓可能已写入 warnings / force_trim_incomplete，账户风控覆盖后并回
    _trim_warns = list(risk_gate.get("warnings") or [])
    _trim_incomplete = bool(risk_gate.get("force_trim_incomplete"))

    # 卖出后、买入前：账户风控；回撤硬拦，单票/行业改走逐笔预算缩量（P1）
    try:
        from core.paper.ledger import mark_to_market
        from core.risk import check_account_risk

        mid_summary = mark_to_market(paper)
        risk_gate = check_account_risk(paper, mid_summary)
        risk_limits = dict(risk_gate.get("limits") or {})
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
        risk_gate = {"ok": True, "blocks": [], "warnings": []}
        risk_limits = {}
    if _trim_warns:
        warns = list(risk_gate.get("warnings") or [])
        for w in _trim_warns:
            if w not in warns:
                warns.append(w)
        risk_gate["warnings"] = warns
    if _trim_incomplete:
        risk_gate["force_trim_incomplete"] = True

    # 风控超额主动减仓：单票/行业超限 → 部分卖出至限额内（不只拦买入）
    risk_excess_trims: List[dict] = []
    if mid_summary and risk_limits:
        _eq = float(mid_summary.get("equity") or 0) or equity_before or 0
        _max_pos_pct = float(risk_limits.get("max_position_pct") or 25.0)
        _max_sec_pct = float(risk_limits.get("max_sector_pct") or 40.0)
        if _eq > 0:
            from core.portfolio_optimize import _sector_for, load_sector_map as _lsm
            _smap = _lsm()
            # 单票超限减仓
            for h in list(kept):
                code = str(h.get("stock_code") or "")
                shares = float(h.get("shares") or 0)
                if shares <= 0:
                    continue
                mv = float(h.get("market_value") or 0)
                if mv <= 0:
                    cost = float(h.get("cost") or 0)
                    mv = cost * shares
                pct = mv / _eq * 100.0 if _eq > 0 else 0
                if pct > _max_pos_pct:
                    # 减仓到限额以下
                    target_mv = _eq * _max_pos_pct / 100.0
                    trim_shares = int((mv - target_mv) / (mv / shares) // 100) * 100
                    if trim_shares >= 100:
                        quote = _quote_cache.get(code) or {}
                        price = _quote_price(quote) if quote.get("success") else None
                        if price and price > 0:
                            # P3-3：跌停/停牌 → 跳过风控减仓
                            _sell_block = _sell_match_block_reason(code, quote)
                            if _sell_block:
                                sell_match_skips.append({
                                    "stock_code": code,
                                    "stock_name": h.get("stock_name"),
                                    "reason": _sell_block,
                                    "score": score_by_code.get(code),
                                    "path": "risk_excess_name",
                                })
                                continue
                            if _t0_rebalance_block(code):
                                _record_t0_open_leg_skip(code, h, path="t0_open_leg")
                                continue
                            from core.paper.tplus1 import (
                                TPLUS1_LOCK_REASON,
                                clip_sell_shares,
                                consume_sell_lots,
                            )

                            trim_shares, t1_meta = clip_sell_shares(h, trim_shares)
                            if trim_shares < 100:
                                if t1_meta.get("reason"):
                                    sell_match_skips.append({
                                        "stock_code": code,
                                        "stock_name": h.get("stock_name"),
                                        "reason": t1_meta.get("reason") or TPLUS1_LOCK_REASON,
                                        "score": score_by_code.get(code),
                                        "path": "tplus1",
                                    })
                                continue
                            fill_px = apply_fill_price("sell", float(price), model=cost_model, params=fee_params)
                            amount = round(trim_shares * fill_px, 2)
                            fee_info = calc_trade_fees("sell", amount, model=cost_model, params=fee_params)
                            trade = annotate_trade(
                                {
                                    "ts": _now_iso(),
                                    "side": "sell",
                                    "stock_code": code,
                                    "stock_name": h.get("stock_name"),
                                    "shares": trim_shares,
                                    "price": round(fill_px, 4),
                                    "amount": amount,
                                    "score": score_by_code.get(code),
                                    "origin": ORIGIN_STRATEGY,
                                    "note": f"风控超额减仓：仓位 {pct:.1f}% > 单票上限 {_max_pos_pct}%",
                                },
                                fee_info,
                            )
                            paper.setdefault("trades", []).append(trade)
                            sell_trades.append(trade)
                            risk_excess_trims.append(trade)
                            cash = round(cash + float(fee_info["net_cash_delta"]), 2)
                            consume_sell_lots(h, trim_shares)
                            after_shares = float(h.get("shares") or 0)
                            if after_shares <= 0:
                                kept.remove(h)
                            else:
                                h["market_value"] = round(after_shares * float(fill_px), 2)

            # 行业超限：行业内按 ŷ 从低到高部分卖出，直至落入限额
            for _round in range(8):
                sec_mv: Dict[str, float] = {}
                sec_holds: Dict[str, List[dict]] = {}
                for h in kept:
                    code = str(h.get("stock_code") or "")
                    sh = float(h.get("shares") or 0)
                    if sh <= 0:
                        continue
                    sec = str(h.get("sector") or _sector_for(code, _smap) or "其他")
                    h["sector"] = sec
                    mv = float(h.get("market_value") or 0)
                    if mv <= 0:
                        mv = float(h.get("cost") or 0) * sh
                    sec_mv[sec] = float(sec_mv.get(sec) or 0.0) + mv
                    sec_holds.setdefault(sec, []).append(h)
                progressed = False
                for sec, total_mv in list(sec_mv.items()):
                    sec_pct = total_mv / _eq * 100.0 if _eq > 0 else 0.0
                    if sec_pct <= _max_sec_pct + 1e-9:
                        continue
                    need_cut = total_mv - (_eq * _max_sec_pct / 100.0)
                    ranked = sorted(
                        sec_holds.get(sec) or [],
                        key=lambda hh: (
                            float(score_by_code[str(hh.get("stock_code") or "")])
                            if score_by_code.get(str(hh.get("stock_code") or "")) is not None
                            else float("-inf")
                        ),
                    )
                    for h in ranked:
                        if need_cut <= 0:
                            break
                        code = str(h.get("stock_code") or "")
                        shares = float(h.get("shares") or 0)
                        mv = float(h.get("market_value") or 0)
                        if mv <= 0:
                            mv = float(h.get("cost") or 0) * shares
                        if shares < 100 or mv <= 0:
                            continue
                        px = mv / shares
                        trim_shares = int(min(need_cut, mv) / px // 100) * 100
                        if trim_shares < 100:
                            continue
                        quote = _quote_cache.get(code) or {}
                        price = _quote_price(quote) if quote.get("success") else None
                        if not price or price <= 0:
                            continue
                        _sell_block = _sell_match_block_reason(code, quote)
                        if _sell_block:
                            sell_match_skips.append({
                                "stock_code": code,
                                "stock_name": h.get("stock_name"),
                                "reason": _sell_block,
                                "score": score_by_code.get(code),
                                "path": "risk_excess_sector",
                            })
                            continue
                        if _t0_rebalance_block(code):
                            _record_t0_open_leg_skip(code, h, path="t0_open_leg")
                            continue
                        fill_px = apply_fill_price(
                            "sell", float(price), model=cost_model, params=fee_params
                        )
                        from core.paper.tplus1 import (
                            TPLUS1_LOCK_REASON,
                            clip_sell_shares,
                            consume_sell_lots,
                        )

                        trim_shares, t1_meta = clip_sell_shares(h, trim_shares)
                        if trim_shares < 100:
                            if t1_meta.get("reason"):
                                sell_match_skips.append({
                                    "stock_code": code,
                                    "stock_name": h.get("stock_name"),
                                    "reason": t1_meta.get("reason") or TPLUS1_LOCK_REASON,
                                    "score": score_by_code.get(code),
                                    "path": "tplus1",
                                })
                            continue
                        amount = round(trim_shares * fill_px, 2)
                        fee_info = calc_trade_fees(
                            "sell", amount, model=cost_model, params=fee_params
                        )
                        trade = annotate_trade(
                            {
                                "ts": _now_iso(),
                                "side": "sell",
                                "stock_code": code,
                                "stock_name": h.get("stock_name"),
                                "shares": trim_shares,
                                "price": round(fill_px, 4),
                                "amount": amount,
                                "score": score_by_code.get(code),
                                "origin": ORIGIN_STRATEGY,
                                "note": (
                                    f"风控超额减仓：行业 {sec} {sec_pct:.1f}% "
                                    f"> 上限 {_max_sec_pct}%"
                                ),
                            },
                            fee_info,
                        )
                        paper.setdefault("trades", []).append(trade)
                        sell_trades.append(trade)
                        risk_excess_trims.append(trade)
                        cash = round(cash + float(fee_info["net_cash_delta"]), 2)
                        before_mv = mv
                        consume_sell_lots(h, trim_shares)
                        after_shares = float(h.get("shares") or 0)
                        h["market_value"] = round(after_shares * float(fill_px), 2)
                        if after_shares <= 0:
                            kept.remove(h)
                            sold_mv = before_mv
                        else:
                            sold_mv = before_mv - float(h.get("market_value") or 0)
                        need_cut -= max(0.0, sold_mv)
                        progressed = True
                if not progressed:
                    break

            paper["holdings"] = kept
            paper["cash"] = round(cash, 2)
            if risk_excess_trims:
                append_operation_log(
                    paper,
                    "risk_excess_trim",
                    detail=f"风控超额主动减仓 {len(risk_excess_trims)} 笔",
                    meta={
                        "trims": [
                            {"code": t.get("stock_code"), "shares": t.get("shares"), "note": t.get("note")}
                            for t in risk_excess_trims
                        ],
                        "path": "cluster" if not respect_max_positions else "cross_section",
                    },
                )

    # 风控减仓后同步 held 视图（避免后续买入腿用过期集合）
    holdings = kept
    held_codes = {str(h.get("stock_code")) for h in kept}
    paper["holdings"] = kept
    paper["cash"] = round(cash, 2)

    state.cash = float(cash)
    state.holdings = list(holdings)
    state.kept = list(kept)
    state.sell_trades = sell_trades
    state.buy_trades = buy_trades
    state.sentiment_restore_trades = sentiment_restore_trades
    state.risk_gate = risk_gate
    state.risk_limits = dict(risk_limits or {})
    state.mid_summary = mid_summary if isinstance(mid_summary, dict) else {}
    state.buys_blocked = bool(buys_blocked)
    state.risk_budget_skips = risk_budget_skips
    state.tau_floor = _tau_floor
    state.tau_floor_meta = _tau_floor_meta if isinstance(_tau_floor_meta, dict) else {}
    state.force_trim_sold = force_trim_sold
    state.force_trim_cut_in_book = bool(force_trim_cut_in_book)
    state.force_trim_no_rebuy = force_trim_no_rebuy
    state.sell_match_skips = sell_match_skips
    state.event_prior_soft_holds = event_prior_soft_holds
    state.turnover_capped = bool(turnover_capped)

