"""调仓买入腿：舆情恢复补仓、EOD/τ 闸、换手预算与背包再分配。

由 ``simulate_cross_section_rebalance`` 在卖腿与账户门禁后调用；原地更新 ``RebalanceState``。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.paper.ledger import ORIGIN_STRATEGY, _now_iso, append_operation_log
from core.paper.costs import annotate_trade, apply_fill_price, calc_trade_fees
from core.paper.rebalance.match import _batch_query_quotes, _buy_match_block_reason
from core.paper.rebalance.state import RebalanceState
from core.paper.rebalance.cash_reserve import resolve_min_cash_pct, spendable_cash
from core.paper.rebalance.turnover import (
    clip_shares_to_turnover_budget,
    resolve_buy_turnover_budget,
)
from core.ports.market import quote_price as _quote_price

logger = logging.getLogger(__name__)


def run_buy_leg(state: RebalanceState, *, target_w: Optional[Dict[str, Any]] = None) -> None:
    """执行买入腿（含 restore / τ 闸 / 换手再分配），写回 state。"""
    target_w = dict(target_w or {})
    paper = state.paper
    ranking = state.ranking
    holdings = list(state.holdings)
    cash = float(state.cash)
    equity_before = state.equity_before
    cost_model = state.cost_model
    fee_params = state.fee_params
    rules = state.rules
    respect_max_positions = state.respect_max_positions
    min_score = state.min_score
    max_positions = state.max_positions
    position_pct = state.position_pct
    max_turnover_pct = state.max_turnover_pct
    skip_sentiment_prior = state.skip_sentiment_prior
    skip_market_prior = state.skip_market_prior
    _tracks_cfg = state.tracks_cfg
    indexes = state.indexes
    top_items = indexes.top_items
    top_codes = indexes.top_codes
    item_by_code = indexes.item_by_code
    eod_score_by_code = indexes.eod_score_by_code
    tau_by_code = indexes.tau_by_code
    score_by_code = indexes.score_by_code
    prior_by_code = state.prior_by_code
    prior_cfg_live = state.prior_cfg_live
    sentiment_prior_summary = state.sentiment_prior_summary
    buy_trades = state.buy_trades
    sell_trades = state.sell_trades
    sentiment_restore_trades = state.sentiment_restore_trades
    risk_gate = state.risk_gate
    risk_limits = state.risk_limits
    mid_summary = state.mid_summary
    buys_blocked = state.buys_blocked
    risk_budget_skips = state.risk_budget_skips
    _tau_floor = state.tau_floor
    _tau_floor_meta = state.tau_floor_meta
    force_trim_no_rebuy = state.force_trim_no_rebuy
    turnover_capped = state.turnover_capped
    turnover_skipped = state.turnover_skipped
    _turnover_retry_pool = state.turnover_retry_pool
    held_codes = {str(h.get("stock_code")) for h in holdings}
    # 桥接段写回 risk_gate 后，买腿仍用 drawdown_blocks 控制 τ/开仓
    drawdown_blocks = [
        i
        for i in list((risk_gate or {}).get("block_items") or [])
        if isinstance(i, dict) and i.get("code") == "drawdown_limit"
    ]
    if buys_blocked and not drawdown_blocks:
        # 与桥接语义对齐：已封锁加仓时不再走开仓主循环
        drawdown_blocks = [{"code": "drawdown_limit", "message": "buys_blocked"}]

    from core.portfolio_optimize import _sector_for, load_sector_map
    from core.risk.budget import (
        build_running_exposure_mv,
        clip_buy_to_risk_budget,
    )

    smap = load_sector_map()
    mid_holdings = list((mid_summary or {}).get("holdings") or holdings)
    name_mv, sector_mv = build_running_exposure_mv(mid_holdings, sector_map=smap)
    budget_equity = float(
        (mid_summary or {}).get("equity") or equity_before or 0
    ) or float(equity_before or 0)
    max_pos_pct = float(risk_limits.get("max_position_pct") or 25.0)
    max_sec_pct = float(risk_limits.get("max_sector_pct") or 40.0)
    # 策略调仓保留净值比例现金，供反 T；买腿只花 spendable
    min_cash_pct = resolve_min_cash_pct(rules)

    def _spendable() -> float:
        return spendable_cash(cash, budget_equity, min_cash_pct)

    # P0 · 批量预取买入候选行情（含舆情缩仓待补回）
    _restore_codes = [
        str(h.get("stock_code") or "")
        for h in holdings
        if h.get("sentiment_trim_base_shares") is not None
        and str(h.get("stock_code") or "").strip()
    ]
    _buy_codes = [
        str(it.get("stock_code") or "")
        for it in top_items
        if it.get("stock_code") and not it.get("hard_reject")
    ]
    _buy_quote_cache: Dict[str, dict] = _batch_query_quotes(
        list(dict.fromkeys([*_restore_codes, *_buy_codes]))
    )

    # 舆情缩仓 restore：先验不再要求 scale_hold 时，补回至 sentiment_trim_base_shares
    if (
        not skip_sentiment_prior
        and str(prior_cfg_live.get("mode") or "off") != "off"
    ):
        try:
            from core.sentiment_prior import apply_prior_restore_hold

            for hi, h in enumerate(list(holdings)):
                code = str(h.get("stock_code") or "").strip()
                if not code:
                    continue
                base_sh = h.get("sentiment_trim_base_shares")
                if base_sh is None:
                    continue
                cur_sh = float(h.get("shares") or 0)
                restore = apply_prior_restore_hold(
                    prior_by_code.get(code),
                    current_shares=cur_sh,
                    base_shares=base_sh,
                )
                if restore.get("clear_base") and not restore.get("restore"):
                    h2 = {**h}
                    h2.pop("sentiment_trim_base_shares", None)
                    holdings[hi] = h2
                    continue
                if not restore.get("restore"):
                    continue
                buy_sh = float(restore.get("buy_shares") or 0)
                if buy_sh < 100:
                    continue
                quote = _buy_quote_cache.get(code) or {}
                price = _quote_price(quote)
                if not price or price <= 0:
                    continue
                skip_match = _buy_match_block_reason(code, quote)
                if skip_match:
                    risk_budget_skips.append(
                        {
                            "stock_code": code,
                            "stock_name": h.get("stock_name"),
                            "reason": f"舆情补回跳过：{skip_match}",
                            "sentiment_restore": True,
                        }
                    )
                    continue
                sector = str(
                    h.get("sector") or _sector_for(code, smap) or "其他"
                )
                clip = clip_buy_to_risk_budget(
                    code=code,
                    sector=sector,
                    price=float(price),
                    shares=int(buy_sh),
                    equity=budget_equity,
                    name_mv=name_mv,
                    sector_mv=sector_mv,
                    max_position_pct=max_pos_pct,
                    max_sector_pct=max_sec_pct,
                )
                if clip.get("skipped"):
                    risk_budget_skips.append(
                        {
                            "stock_code": code,
                            "stock_name": h.get("stock_name"),
                            "reason": clip.get("reason") or "舆情补回·风险预算跳过",
                            "sentiment_restore": True,
                        }
                    )
                    continue
                shares = int(clip.get("shares") or buy_sh)
                if shares < 100:
                    continue
                fill_px = apply_fill_price(
                    "buy", float(price), model=cost_model, params=fee_params
                )
                amount = round(shares * fill_px, 2)
                if (
                    max_turnover_pct is not None
                    and equity_before
                    and equity_before > 0
                ):
                    sell_amt, buy_amt_so_far, buy_budget_amt = (
                        resolve_buy_turnover_budget(
                            sell_trades=sell_trades,
                            buy_trades=buy_trades,
                            equity_before=equity_before,
                            max_turnover_pct=float(max_turnover_pct),
                        )
                    )
                    clipped_sh = clip_shares_to_turnover_budget(
                        shares=shares,
                        fill_px=fill_px,
                        buy_amt_so_far=buy_amt_so_far,
                        buy_budget_amt=buy_budget_amt,
                        sell_amt=sell_amt,
                        equity_before=equity_before,
                        max_turnover_pct=float(max_turnover_pct),
                    )
                    if clipped_sh < 100:
                        risk_budget_skips.append(
                            {
                                "stock_code": code,
                                "stock_name": h.get("stock_name"),
                                "reason": "舆情补回跳过：换手预算不足",
                                "sentiment_restore": True,
                            }
                        )
                        turnover_capped = True
                        continue
                    if clipped_sh < shares:
                        shares = clipped_sh
                        amount = round(shares * fill_px, 2)
                        turnover_capped = True
                fee_info = calc_trade_fees(
                    "buy", amount, model=cost_model, params=fee_params
                )
                need = amount + float(fee_info.get("fees") or 0)
                if need > _spendable() + 1e-6:
                    continue
                trade = annotate_trade(
                    {
                        "ts": _now_iso(),
                        "side": "buy",
                        "stock_code": code,
                        "stock_name": h.get("stock_name")
                        or quote.get("stock_name"),
                        "shares": shares,
                        "price": round(fill_px, 4),
                        "amount": amount,
                        "score": score_by_code.get(code),
                        "origin": ORIGIN_STRATEGY,
                        "note": "舆情先验恢复补仓",
                        "sentiment_restore": True,
                        "sector": sector,
                    },
                    fee_info,
                )
                paper.setdefault("trades", []).append(trade)
                buy_trades.append(trade)
                sentiment_restore_trades.append(trade)
                from core.paper.tplus1 import add_buy_lot

                old_cost = float(h.get("cost") or 0)
                new_shares = cur_sh + shares
                if new_shares > 0:
                    h["cost"] = round(
                        (old_cost * cur_sh + fill_px * shares) / new_shares, 4
                    )
                add_buy_lot(h, shares, ts=trade["ts"])
                h["market_value"] = round(float(h.get("shares") or 0) * fill_px, 2)
                if restore.get("clear_base") or float(h.get("shares") or 0) + 1e-9 >= float(
                    base_sh
                ):
                    h.pop("sentiment_trim_base_shares", None)
                cash = round(cash + float(fee_info["net_cash_delta"]), 2)
                name_mv[code] = float(name_mv.get(code) or 0.0) + amount
                sector_mv[sector] = float(sector_mv.get(sector) or 0.0) + amount
            paper["holdings"] = holdings
            paper["cash"] = round(cash, 2)
            if sentiment_restore_trades:
                append_operation_log(
                    paper,
                    "sentiment_restore",
                    detail=f"舆情缩仓恢复补仓 {len(sentiment_restore_trades)} 笔",
                    meta={
                        "codes": [
                            str(t.get("stock_code"))
                            for t in sentiment_restore_trades
                        ],
                        "path": "cluster"
                        if not respect_max_positions
                        else "cross_section",
                    },
                )
        except Exception as exc:
            logger.warning("sentiment restore failed: %s", exc, exc_info=True)

    if not drawdown_blocks:
        # τ 买入门槛：候选池无人过基线时冻结降级（可回滚）
        try:
            from core.signal.dual_score import resolve_tau_buy_floor_for_pool
            from core.signal.rebalance_tracks import should_apply_tau_gate

            _tau_pool = [
                it
                for it in top_items
                if isinstance(it, dict)
                and str(it.get("stock_code") or "") not in held_codes
                and not it.get("hard_reject")
                and should_apply_tau_gate(it, tracks_cfg=_tracks_cfg)
            ]
            _tau_floor, _tau_floor_meta = resolve_tau_buy_floor_for_pool(_tau_pool)
            if str(_tau_floor_meta.get("mode") or "") == "freeze_breakglass":
                note = str(_tau_floor_meta.get("note") or "τ 试验档：买入闸临时放宽")
                warns = list(risk_gate.get("warnings") or [])
                if note not in warns:
                    warns.append(note)
                    risk_gate["warnings"] = warns
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
            logger.warning("resolve_tau_buy_floor_for_pool failed", exc_info=True)
            _tau_floor = None
            _tau_floor_meta = {}

        for item in top_items:
            # 分池滞回：账户可暂时多于簿长（中间带未清仓）；买入上限只看「已持目标簿只数」
            if not respect_max_positions:
                book_held = sum(
                    1
                    for h in holdings
                    if str(h.get("stock_code") or "") in top_codes
                )
                if book_held >= max_positions:
                    break
            elif len(holdings) >= max_positions:
                break
            code = str(item.get("stock_code") or "")
            if not code or code in held_codes:
                continue
            if code in force_trim_no_rebuy:
                risk_budget_skips.append(
                    {
                        "stock_code": code,
                        "stock_name": item.get("stock_name"),
                        "reason": "force_trim_no_rebuy",
                        "score": item.get("score"),
                    }
                )
                continue
            if item.get("hard_reject"):
                continue
            # 过热纸面闸：mom5/当日大涨等（生产 ŷ 不硬拒入簿，买入侧单独拦）
            try:
                from core.signal.overheat_gate import paper_overheat_block

                oh_block, oh_reason = paper_overheat_block(item)
                if oh_block:
                    risk_budget_skips.append(
                        {
                            "stock_code": code,
                            "stock_name": item.get("stock_name"),
                            "reason": oh_reason or "overheat_paper_gate",
                            "score": item.get("score"),
                            "overheat": item.get("overheat"),
                        }
                    )
                    continue
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("paper overheat gate skipped", exc_info=True)
            # SS-E2：predicted 轨须 production ŷ；heuristic 轨仍走 buy_gate_for_item
            try:
                from core.signal.gate import allows_production_yhat
                from core.signal.rebalance_tracks import (
                    TRACK_HEURISTIC,
                    resolve_score_track,
                )

                if resolve_score_track(item) != TRACK_HEURISTIC:
                    poke, poke_reason = allows_production_yhat(item)
                    if not poke:
                        risk_budget_skips.append(
                            {
                                "stock_code": code,
                                "stock_name": item.get("stock_name"),
                                "reason": poke_reason or "production_yhat_gate",
                                "score": item.get("score"),
                                "production_ok": False,
                                "score_track": item.get("score_track"),
                            }
                        )
                        continue
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
                pass
            score = item.get("score")
            # 买入门槛：双轨（ŷ_EOD% 或 heuristic 0–100）
            try:
                from core.signal.rebalance_tracks import (
                    buy_gate_for_item,
                    should_apply_tau_gate,
                )

                ok_buy, gate_sc, _track, skip_r = buy_gate_for_item(
                    item,
                    tracks_cfg=_tracks_cfg,
                    predicted_buy_floor=min_score,
                )
                if not ok_buy:
                    risk_budget_skips.append(
                        {
                            "stock_code": code,
                            "stock_name": item.get("stock_name"),
                            "reason": skip_r or "below_eod_floor",
                            "score": score,
                            "eod_gate_score": gate_sc,
                            "min_score": min_score,
                            "score_track": item.get("score_track"),
                        }
                    )
                    continue
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
                try:
                    from core.signal.dual_score import eod_gate_score_for_item

                    gate_sc = eod_gate_score_for_item(item)
                    if gate_sc is None and code in eod_score_by_code:
                        gate_sc = eod_score_by_code.get(code)
                except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                    logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
                    gate_sc = eod_score_by_code.get(code)
                if gate_sc is None or float(gate_sc) < min_score:
                    risk_budget_skips.append(
                        {
                            "stock_code": code,
                            "stock_name": item.get("stock_name"),
                            "reason": "below_eod_floor",
                            "score": score,
                            "eod_gate_score": gate_sc,
                            "min_score": min_score,
                        }
                    )
                    continue

            # F1：ŷ_τ 买入闸（heuristic 袖仓默认跳过）
            try:
                from core.signal.rebalance_tracks import should_apply_tau_gate
                from core.signal.dual_score import buy_passes_tau_gate

                if should_apply_tau_gate(item, tracks_cfg=_tracks_cfg):
                    tau_ok, tau_reason = buy_passes_tau_gate(
                        item, floor=_tau_floor
                    )
                    if not tau_ok:
                        risk_budget_skips.append(
                            {
                                "stock_code": code,
                                "stock_name": item.get("stock_name"),
                                "reason": tau_reason or "ŷ_τ 买入闸",
                                "score": score,
                                "eod_gate_score": gate_sc,
                                "predicted_score_tau": item.get(
                                    "predicted_score_tau", item.get("score_rem")
                                ),
                                "dual_score_tau_gate": True,
                                "tau_floor_effective": _tau_floor,
                                "tau_gate_mode": _tau_floor_meta.get("mode"),
                                "score_track": item.get("score_track"),
                            }
                        )
                        continue
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
                logger.warning("buy_loop tau_gate failed for %s", code, exc_info=True)

            # Y(τ) 校验：双头分歧 / 缺 τ → 不按今日 EOD 新开
            try:
                from core.signal.y_state import buy_passes_y_check, stamp_y_state

                if item.get("y_check") is None:
                    stamp_y_state(item)
                y_ok, y_reason = buy_passes_y_check(item)
                if not y_ok:
                    risk_budget_skips.append(
                        {
                            "stock_code": code,
                            "stock_name": item.get("stock_name"),
                            "reason": y_reason or "Y 校验未过",
                            "score": score,
                            "eod_gate_score": gate_sc,
                            "y_check": item.get("y_check"),
                            "y_disagree": item.get("y_disagree"),
                            "eod_trust": item.get("eod_trust"),
                            "y_state_gate": True,
                        }
                    )
                    continue
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
                logger.warning("buy_loop y_check failed for %s", code, exc_info=True)

            # 舆情先验（ŷ 外）：不改 score；gate 时 skip / 缩 ratio（用开环批量结果）
            prior_apply = None
            try:
                from core.sentiment_prior import apply_prior_to_buy

                if str(prior_cfg_live.get("mode") or "off") == "off":
                    prior_pack = None
                elif isinstance(item.get("sentiment_prior"), dict) and item.get(
                    "sentiment_prior"
                ).get("success"):
                    prior_pack = item["sentiment_prior"]
                elif code in prior_by_code:
                    prior_pack = prior_by_code[code]
                else:
                    prior_pack = None
                if prior_pack is not None:
                    prior_apply = apply_prior_to_buy(
                        prior_pack, position_ratio=position_pct
                    )
                    for w in prior_apply.get("warnings") or []:
                        warns = list(risk_gate.get("warnings") or [])
                        if w and w not in warns:
                            warns.append(w)
                            risk_gate["warnings"] = warns
                    if prior_apply.get("skip"):
                        risk_budget_skips.append(
                            {
                                "stock_code": code,
                                "stock_name": item.get("stock_name"),
                                "reason": prior_apply.get("reason")
                                or "sentiment_prior_bearish",
                                "score": score,
                                "sentiment_prior": True,
                            }
                        )
                        continue
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
                logger.warning("buy_loop sentiment_prior failed for %s", code, exc_info=True)
                prior_apply = None

            # 市场级 prior（跨市场/情绪周期/监管/IPO）
            market_prior_apply = None
            if not skip_market_prior:
                try:
                    from core.market.prior_policy import apply_market_priors_to_buy

                    market_prior_apply = apply_market_priors_to_buy(
                        item, position_ratio=position_pct
                    )
                    for w in market_prior_apply.get("warnings") or []:
                        warns = list(risk_gate.get("warnings") or [])
                        if w and w not in warns:
                            warns.append(w)
                            risk_gate["warnings"] = warns
                    if market_prior_apply.get("skip"):
                        risk_budget_skips.append(
                            {
                                "stock_code": code,
                                "stock_name": item.get("stock_name"),
                                "reason": market_prior_apply.get("reason")
                                or "market_prior",
                                "score": score,
                                "market_prior": True,
                            }
                        )
                        continue
                except Exception:  # noqa: BLE001
                    logger.debug("catch except Exception: in paper_rebalance.py", exc_info=True)
                    logger.warning("buy_loop market_prior failed for %s", code, exc_info=True)
                    market_prior_apply = None

            # 横截面：optimize 未分配则跳过；分池：合并簿即目标集，不因 optimize 漏配而整票跳过
            if (
                respect_max_positions
                and target_w
                and code not in target_w
            ):
                risk_budget_skips.append(
                    {
                        "stock_code": code,
                        "stock_name": item.get("stock_name"),
                        "reason": "不在风险预算目标仓（optimize 未分配权重）",
                        "score": score,
                    }
                )
                continue

            quote = _buy_quote_cache.get(code) or {}
            price = _quote_price(quote)
            if not price or price <= 0:
                risk_budget_skips.append(
                    {
                        "stock_code": code,
                        "stock_name": item.get("stock_name"),
                        "reason": "无有效报价，跳过买入",
                        "score": score,
                    }
                )
                continue

            # Y2.2：涨跌停 / 停牌提示 — 最小纪律
            skip_match = _buy_match_block_reason(code, quote)
            if skip_match:
                risk_budget_skips.append(
                    {
                        "stock_code": code,
                        "stock_name": item.get("stock_name"),
                        "reason": skip_match,
                        "score": score,
                    }
                )
                continue

            ratio = position_pct
            if prior_apply and prior_apply.get("ratio") is not None:
                try:
                    ratio = min(ratio, float(prior_apply["ratio"]))
                except (TypeError, ValueError):
                    pass
            if market_prior_apply and market_prior_apply.get("ratio") is not None:
                try:
                    ratio = min(ratio, float(market_prior_apply["ratio"]))
                except (TypeError, ValueError):
                    pass
            # 目标仓：横截面硬约束缩量；分池同样吃 optimize（等权回退 1/簿长）
            if code in target_w:
                try:
                    ratio = min(ratio, float(target_w[code]) / 100.0)
                except (TypeError, ValueError):
                    pass
            elif not respect_max_positions and max_positions > 0:
                ratio = min(ratio, 1.0 / float(max_positions))
            # sizing：净值×权重，但不得超过可花现金（总现金 − 正T低吸底仓）
            # 横截面过去用 cash*ratio 导致严重欠仓（满仓时单笔只有分池的 1/7），改为与分池一致
            avail = _spendable()
            if budget_equity > 0:
                budget = min(avail, budget_equity * ratio)
            else:
                budget = avail * ratio
            if budget < price * 100:
                continue
            shares = int(budget // price // 100) * 100
            if shares <= 0:
                continue

            sector = str(
                item.get("sector") or _sector_for(code, smap) or "其他"
            )
            clip = clip_buy_to_risk_budget(
                code=code,
                sector=sector,
                price=float(price),
                shares=shares,
                equity=budget_equity,
                name_mv=name_mv,
                sector_mv=sector_mv,
                max_position_pct=max_pos_pct,
                max_sector_pct=max_sec_pct,
            )
            if clip.get("skipped"):
                risk_budget_skips.append(
                    {
                        "stock_code": code,
                        "stock_name": item.get("stock_name"),
                        "reason": clip.get("reason") or "风险预算跳过",
                        "score": score,
                        "sector": sector,
                    }
                )
                continue
            shares = int(clip.get("shares") or shares)

            fill_px = apply_fill_price(
                "buy", float(price), model=cost_model, params=fee_params
            )
            amount = round(shares * fill_px, 2)
            fee_info = calc_trade_fees(
                "buy", amount, model=cost_model, params=fee_params
            )
            need = amount + float(fee_info.get("fees") or 0)
            if need > avail + 1e-6:
                continue

            # P0 + R5：双边换手预算拆分。50% 给 sell，50% 给 buy；sell 侧未满可溢出给 buy
            # 超限时先按剩余预算裁剪手数；仍不足 1 手再进半仓重试池
            _to_clipped = False
            if max_turnover_pct is not None and equity_before and equity_before > 0:
                _max_to = float(max_turnover_pct)
                sell_amt, buy_amt_so_far, buy_budget_amt = resolve_buy_turnover_budget(
                    sell_trades=sell_trades,
                    buy_trades=buy_trades,
                    equity_before=equity_before,
                    max_turnover_pct=_max_to,
                )
                buy_amt_after = buy_amt_so_far + amount
                proj_to = (sell_amt + buy_amt_after) / 2.0 / equity_before * 100.0
                buy_over = buy_amt_after > buy_budget_amt + 1e-9
                total_over = proj_to > _max_to + 1e-9
                if buy_over or total_over:
                    clipped_sh = clip_shares_to_turnover_budget(
                        shares=shares,
                        fill_px=fill_px,
                        buy_amt_so_far=buy_amt_so_far,
                        buy_budget_amt=buy_budget_amt,
                        sell_amt=sell_amt,
                        equity_before=equity_before,
                        max_turnover_pct=_max_to,
                    )
                    if clipped_sh >= 100:
                        shares = clipped_sh
                        amount = round(shares * fill_px, 2)
                        fee_info = calc_trade_fees(
                            "buy", amount, model=cost_model, params=fee_params
                        )
                        need = amount + float(fee_info.get("fees") or 0)
                        if need > _spendable() + 1e-6:
                            continue
                        _to_clipped = True
                        turnover_capped = True
                    else:
                        turnover_capped = True
                        turnover_skipped.append(code)
                        _turnover_retry_pool.append({
                            "item": item,
                            "code": code,
                            "score": score,
                            "price": price,
                            "quote": quote,
                            "sector": sector,
                            "base_ratio": ratio,
                            "prior_apply_ratio": (prior_apply or {}).get("ratio"),
                            "target_w_pct": target_w.get(code),
                        })
                        continue

            tw_note = ""
            if code in target_w:
                tw_note = f" · 目标仓{float(target_w[code]):.1f}%"
            clip_note = f" · {clip['reason']}" if clip.get("clipped") else ""
            to_note = " · 换手预算裁剪" if _to_clipped else ""
            buy_label = (
                "分池目标簿买入"
                if not respect_max_positions
                else "横截面调仓买入（TopK）"
            )
            trade = annotate_trade(
                {
                    "ts": _now_iso(),
                    "side": "buy",
                    "stock_code": code,
                    "stock_name": item.get("stock_name") or quote.get("stock_name"),
                    "shares": shares,
                    "price": round(fill_px, 4),
                    "amount": amount,
                    "score": score,
                    "origin": ORIGIN_STRATEGY,
                    "note": f"{buy_label}{tw_note}{clip_note}{to_note}",
                    "target_weight_pct": target_w.get(code),
                    "sector": sector,
                    "risk_budget_clipped": bool(clip.get("clipped")),
                    "turnover_budget_clipped": bool(_to_clipped),
                },
                fee_info,
            )
            paper.setdefault("trades", []).append(trade)
            buy_trades.append(trade)
            from core.paper.tplus1 import stamp_new_holding

            row = {
                "stock_code": code,
                "stock_name": trade["stock_name"],
                "shares": shares,
                "cost": round(fill_px, 4),
                "bought_at": trade["ts"],
                "origin": ORIGIN_STRATEGY,
                "sector": sector,
                "market_value": amount,
            }
            stamp_new_holding(row, ts=trade["ts"])
            holdings.append(row)
            held_codes.add(code)
            cash = round(cash + float(fee_info["net_cash_delta"]), 2)
            name_mv[code] = float(name_mv.get(code) or 0.0) + amount
            sector_mv[sector] = float(sector_mv.get(sector) or 0.0) + amount

    # P3-1：换手预算背包再分配 — 首轮被软上限跳过的候选，用半仓在剩余换手预算内重试
    # top_items 已按 score 降序，retry_pool 继承该序；高分离票优先榨干剩余预算
    _retry_fitted = 0
    if (
        _turnover_retry_pool
        and max_turnover_pct is not None
        and equity_before
        and equity_before > 0
    ):
        for _rc in _turnover_retry_pool:
            # 分池：目标簿已持只数达上限则停；横截面：总持仓达上限则停
            if not respect_max_positions:
                _book_held = sum(
                    1 for _h in holdings
                    if str(_h.get("stock_code") or "") in top_codes
                )
                if _book_held >= max_positions:
                    break
            elif len(holdings) >= max_positions:
                break

            _code = _rc["code"]
            if _code in held_codes or _code in force_trim_no_rebuy:
                continue
            _item = _rc["item"]
            _price = _rc["price"]
            _quote = _rc["quote"]
            _sector = _rc["sector"]
            _score = _rc["score"]
            if not _price or _price <= 0:
                continue

            # 半仓：base_ratio 折半，再叠 sentiment / target_w 收紧
            _ratio = max(0.0, float(_rc.get("base_ratio") or 0.0) * 0.5)
            _pa_ratio = _rc.get("prior_apply_ratio")
            if _pa_ratio is not None:
                try:
                    _ratio = min(_ratio, float(_pa_ratio))
                except (TypeError, ValueError):
                    pass
            if _rc.get("target_w_pct") is not None:
                try:
                    _ratio = min(_ratio, float(_rc["target_w_pct"]) / 100.0)
                except (TypeError, ValueError):
                    pass
            if _ratio <= 0:
                continue
            _avail = _spendable()
            if not respect_max_positions and budget_equity > 0:
                _budget = min(_avail, budget_equity * _ratio)
            else:
                _budget = _avail * _ratio
            if _budget < _price * 100:
                continue
            _shares = int(_budget // _price // 100) * 100
            if _shares <= 0:
                continue

            _clip = clip_buy_to_risk_budget(
                code=_code,
                sector=_sector,
                price=float(_price),
                shares=_shares,
                equity=budget_equity,
                name_mv=name_mv,
                sector_mv=sector_mv,
                max_position_pct=max_pos_pct,
                max_sector_pct=max_sec_pct,
            )
            if _clip.get("skipped"):
                continue
            _shares = int(_clip.get("shares") or _shares)

            _fill_px = apply_fill_price(
                "buy", float(_price), model=cost_model, params=fee_params
            )
            _amount = round(_shares * _fill_px, 2)
            _fee_info = calc_trade_fees(
                "buy", _amount, model=cost_model, params=fee_params
            )
            _need = _amount + float(_fee_info.get("fees") or 0)
            if _need > _avail + 1e-6:
                continue

            # 换手软上限复检 + R5 双边拆分；超限则裁剪到剩余预算
            _max_to = float(max_turnover_pct) if max_turnover_pct is not None else None
            _to_retry_note = "换手背包半仓补入"
            if _max_to is not None and equity_before and equity_before > 0:
                _sell_amt = sum(float(t.get("amount") or 0) for t in sell_trades)
                _ss_pct = _max_to / 2.0
                _ss_amt = _ss_pct / 100.0 * equity_before
                _s_excess = max(0.0, _ss_amt - _sell_amt)
                _buy_sofar = sum(float(t.get("amount") or 0) for t in buy_trades)
                _buy_budget = _ss_amt + min(_s_excess, _ss_amt)
                _buy_after = _buy_sofar + _amount
                _proj_to = (_sell_amt + _buy_after) / 2.0 / equity_before * 100.0
                _b_over = _buy_after > _buy_budget + 1e-9
                _t_over = _proj_to > _max_to + 1e-9
                if _b_over or _t_over:
                    _clipped = clip_shares_to_turnover_budget(
                        shares=_shares,
                        fill_px=_fill_px,
                        buy_amt_so_far=_buy_sofar,
                        buy_budget_amt=_buy_budget,
                        sell_amt=_sell_amt,
                        equity_before=equity_before,
                        max_turnover_pct=_max_to,
                    )
                    if _clipped < 100:
                        continue
                    _shares = _clipped
                    _amount = round(_shares * _fill_px, 2)
                    _fee_info = calc_trade_fees(
                        "buy", _amount, model=cost_model, params=fee_params
                    )
                    _need = _amount + float(_fee_info.get("fees") or 0)
                    if _need > _spendable() + 1e-6:
                        continue
                    _to_retry_note = "换手背包半仓补入 · 预算裁剪"
            else:
                _sell_amt = sum(float(t.get("amount") or 0) for t in sell_trades)
                _buy_amt = sum(float(t.get("amount") or 0) for t in buy_trades) + _amount
                _proj_to = (_sell_amt + _buy_amt) / 2.0 / equity_before * 100.0
                if max_turnover_pct is not None and _proj_to > float(max_turnover_pct) + 1e-9:
                    continue

            _tw_note = ""
            if _code in target_w:
                _tw_note = f" \u00b7 目标仓{float(target_w[_code]):.1f}%"
            _clip_note = f" \u00b7 {_clip['reason']}" if _clip.get("clipped") else ""
            _trade = annotate_trade(
                {
                    "ts": _now_iso(),
                    "side": "buy",
                    "stock_code": _code,
                    "stock_name": _item.get("stock_name") or _quote.get("stock_name"),
                    "shares": _shares,
                    "price": round(_fill_px, 4),
                    "amount": _amount,
                    "score": _score,
                    "origin": ORIGIN_STRATEGY,
                    "note": f"{_to_retry_note}{_tw_note}{_clip_note}",
                    "target_weight_pct": target_w.get(_code),
                    "sector": _sector,
                    "risk_budget_clipped": bool(_clip.get("clipped")),
                    "turnover_reallocate": True,
                },
                _fee_info,
            )
            paper.setdefault("trades", []).append(_trade)
            buy_trades.append(_trade)
            from core.paper.tplus1 import stamp_new_holding

            _row = {
                "stock_code": _code,
                "stock_name": _trade["stock_name"],
                "shares": _shares,
                "cost": round(_fill_px, 4),
                "bought_at": _trade["ts"],
                "origin": ORIGIN_STRATEGY,
                "sector": _sector,
                "market_value": _amount,
            }
            stamp_new_holding(_row, ts=_trade["ts"])
            holdings.append(_row)
            held_codes.add(_code)
            cash = round(cash + float(_fee_info["net_cash_delta"]), 2)
            name_mv[_code] = float(name_mv.get(_code) or 0.0) + _amount
            sector_mv[_sector] = float(sector_mv.get(_sector) or 0.0) + _amount
            _retry_fitted += 1

    if _retry_fitted > 0:
        append_operation_log(
            paper,
            "turnover_reallocate",
            detail=f"换手背包再分配：半仓补入 {_retry_fitted} 只",
            meta={"fitted_count": _retry_fitted},
        )

    state.cash = float(cash)
    state.holdings = list(holdings)
    state.buy_trades = buy_trades
    state.sell_trades = sell_trades
    state.sentiment_restore_trades = sentiment_restore_trades
    state.risk_gate = risk_gate
    state.risk_limits = risk_limits
    state.mid_summary = mid_summary
    state.buys_blocked = bool(buys_blocked)
    state.risk_budget_skips = risk_budget_skips
    state.tau_floor = _tau_floor
    state.tau_floor_meta = _tau_floor_meta if isinstance(_tau_floor_meta, dict) else {}
    state.turnover_capped = bool(turnover_capped)
    state.turnover_skipped = list(turnover_skipped or [])
    state.turnover_retry_pool = list(_turnover_retry_pool or [])
    if "_buy_quote_cache" in locals() and isinstance(_buy_quote_cache, dict):
        state.buy_quote_cache = _buy_quote_cache
    state.sentiment_prior_summary = sentiment_prior_summary

