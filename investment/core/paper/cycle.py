"""纸面调仓 / 日循环流水线（从 paper.py 拆出，降低单文件集中度）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List

from core.paper.costs import resolve_cost_model
from core.paper.exec import (
    mark_to_market,
    simulate_buys,
    simulate_sells,
)
from core.paper.ledger import (
    append_operation_log,
    append_snapshot,
    build_ops_report,
    run_signal_scan,
)


def run_daily_cycle(
    paper: dict,
    *,
    simulate_buy: bool = False,
    strategy: str = "short",
    on_progress=None,
) -> Dict[str, Any]:
    def _p(cur: int, tot: int, msg: str) -> None:
        if on_progress:
            on_progress(cur, tot, msg)

    _resolve_strategy_spec(paper, strategy)

    holdings, holding_codes, name_by_code, grand = _get_holdings_info(paper)

    def _score_progress(i: int, n: int, msg: str) -> None:
        _p(i, grand, msg)

    _p(0, grand, f"开始扫描持仓评分…（策略: {strategy}）")
    # 扫描持仓股票评分
    pool = run_signal_scan(paper, on_progress=_score_progress, stock_codes=holding_codes)
    new_trades: List[dict] = []
    sell_trades: List[dict] = []

    old_shares_by_code, cash_before, position_count_before = _snapshot_pre_rebalance_state(
        holdings, paper
    )

    risk_gate, pre_summary = _check_pre_rebalance_risk(paper)

    buys_blocked = False
    _p(len(holding_codes) + 1, grand, "减仓/卖出规则")
    sell_trades = simulate_sells(paper, pool)

    _optimize_target_weights(paper, pool, strategy)

    if simulate_buy:
        if risk_gate and not risk_gate.get("ok"):
            _p(len(holding_codes) + 2, grand, "风控拦截加仓")
            new_trades = []
            buys_blocked = True
            _log_risk_block(paper, risk_gate)
        else:
            _p(len(holding_codes) + 2, grand, "加仓/买入…")
            new_trades = simulate_buys(paper, pool)
    else:
        _p(len(holding_codes) + 2, grand, "跳过加仓")
    _p(len(holding_codes) + 3, grand, "更新净值…")
    summary = mark_to_market(paper)
    append_snapshot(paper, summary)
    _p(grand, grand, "完成")

    cost_model, cash_impact = _build_cash_impact(
        paper, pre_summary, cash_before, position_count_before, sell_trades, new_trades, summary
    )

    dq, codes = _summarize_data_quality(pool, holding_codes)

    monitor = _assess_strategy_health(paper, summary, codes, holding_codes)

    manifest = _build_and_write_manifest(
        paper, strategy, cost_model, dq, simulate_buy,
        new_trades, sell_trades, risk_gate, buys_blocked, monitor,
    )

    rebalance_report = _build_rebalance_report(
        paper, pool, new_trades, sell_trades,
        old_shares_by_code, name_by_code, summary, simulate_buy,
    )

    sid, sver, risk_blocks, monitor_alerts, ops_report, attribution = _assemble_extras_and_ops_report(
        paper, strategy, risk_gate, monitor, dq, cost_model, buys_blocked, summary
    )

    return {
        "success": True,
        "observation_pool_count": len(pool),
        "new_trades": new_trades,
        "sell_trades": sell_trades,
        "summary": summary,
        "top_signals": pool[:5],
        "rebalance_report": rebalance_report,
        "cash_impact": cash_impact,
        "risk_gate": risk_gate,
        "manifest": manifest,
        "strategy_id": sid,
        "strategy_version": sver,
        "cost_model": cost_model,
        "data_quality": dq or {},
        "risk_blocks": risk_blocks,
        "monitor_alerts": monitor_alerts,
        "buys_blocked": buys_blocked,
        "ops_report": ops_report,
        "attribution": attribution,
        "last_optimize": paper.get("last_optimize"),
        "target_weights": (paper.get("last_optimize") or {}).get("weights_pct"),
        "health": monitor,
    }


def _resolve_strategy_spec(paper: dict, strategy: str) -> None:
    # 根据策略规格合并 paper.rules / 成本默认（Q2）
    strategy_spec = None
    if strategy:
        try:
            from core.strategy import apply_strategy_to_paper

            strategy_spec = apply_strategy_to_paper(paper, strategy)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in paper_cycle.py", exc_info=True)
            from core.backtest.strategies import get_strategy

            try:
                strategy_spec = get_strategy(strategy)
                paper_rules = paper.get("rules") or {}
                paper["rules"] = {**paper_rules, **(strategy_spec.get("params") or {})}
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in paper_cycle.py", exc_info=True)
                pass


def _get_holdings_info(paper: dict):
    holdings = paper.get("holdings") or []
    holding_codes = [str(h.get("stock_code")) for h in holdings if h.get("stock_code")]
    # 保存调仓前的名称映射（清仓后仍可用于报告展示）
    name_by_code = {str(h.get("stock_code")): h.get("stock_name") or "" for h in holdings if h.get("stock_code")}
    # 只评分持仓股票
    score_total = max(len(holding_codes), 1)
    phase_extra = 3
    grand = score_total + phase_extra
    return holdings, holding_codes, name_by_code, grand


def _snapshot_pre_rebalance_state(holdings: list, paper: dict):
    # 保存调仓前的持仓快照（股数）
    old_shares_by_code: Dict[str, float] = {}
    for h in holdings:
        code = str(h.get("stock_code") or "")
        if code:
            old_shares_by_code[code] = float(h.get("shares") or 0)

    # 保存调仓前现金与持仓数，供预演资金影响摘要
    cash_before = float(paper.get("cash") or 0)
    position_count_before = len(
        [h for h in holdings if float(h.get("shares") or 0) > 0]
    )
    return old_shares_by_code, cash_before, position_count_before


def _check_pre_rebalance_risk(paper: dict):
    # Q4：调仓前风控（基于当前市值快照）
    risk_gate = None
    pre_summary = None
    try:
        from core.risk import check_account_risk

        pre_summary = mark_to_market(paper)
        risk_gate = check_account_risk(paper, pre_summary)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_cycle.py", exc_info=True)
        risk_gate = {"ok": True, "blocks": [], "warnings": []}
    return risk_gate, pre_summary


def _optimize_target_weights(paper: dict, pool: list, strategy: str) -> None:
    # P1：目标权重建议（风控拦截时仍可见）
    try:
        from core.portfolio_optimize import optimize_weights
        from core.strategy import get_strategy_spec

        sid_opt = paper.get("strategy_id") or strategy
        rr = (get_strategy_spec(str(sid_opt)).get("risk") or {})
        paper["last_optimize"] = optimize_weights(
            pool,
            max_position_pct=float(rr.get("max_position_pct") or 25.0),
            max_sector_pct=float(rr.get("max_sector_pct") or 40.0),
            max_positions=int(rr.get("max_positions") or 5),
            min_score=None,  # ŷ 滞回：resolve_buy_floor，勿读 paper.rules.min_score
            weight_mode=str(
                (paper.get("rules") or {}).get("weight_mode") or "score_budget"
            ),
        )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_cycle.py", exc_info=True)
        paper["last_optimize"] = None


def _log_risk_block(paper: dict, risk_gate) -> None:
    blocks = (risk_gate or {}).get("blocks") or []
    block_items = (risk_gate or {}).get("block_items") or []
    append_operation_log(
        paper,
        "risk_block",
        detail="风控拦截加仓：" + ("；".join(str(b) for b in blocks) or "超限"),
        meta={
            "codes": (risk_gate or {}).get("block_codes")
            or [i.get("code") for i in block_items if isinstance(i, dict)],
            "block_items": block_items,
            "blocks": blocks,
            "warnings": (risk_gate or {}).get("warnings") or [],
            "limits": (risk_gate or {}).get("limits"),
            "simulate_buy": True,
        },
    )


def _build_cash_impact(
    paper: dict,
    pre_summary,
    cash_before: float,
    position_count_before: int,
    sell_trades: list,
    new_trades: list,
    summary,
):
    cost_model = resolve_cost_model(paper)
    equity_before = None
    try:
        equity_before = float((pre_summary or {}).get("equity") or 0) or None
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_cycle.py", exc_info=True)
        equity_before = None
    max_turnover_pct = None
    raw_mto = (paper.get("rules") or {}).get(
        "max_turnover_pct", (paper.get("rules") or {}).get("max_turnover")
    )
    if raw_mto is not None and raw_mto != "":
        try:
            max_turnover_pct = float(raw_mto)
        except (TypeError, ValueError):
            max_turnover_pct = None
    try:
        from core.paper.rebalance import build_rebalance_cash_impact
        from core.paper.rebalance.cash_reserve import resolve_min_cash_pct

        cash_impact = build_rebalance_cash_impact(
            cash_before=cash_before,
            position_count_before=position_count_before,
            sell_trades=sell_trades,
            buy_trades=new_trades,
            summary=summary,
            equity_before=equity_before,
            cost_model=cost_model,
            max_turnover_pct=max_turnover_pct,
            min_cash_pct=resolve_min_cash_pct(paper.get("rules") or {}),
        )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_cycle.py", exc_info=True)
        buy_amount = round(sum(float(t.get("amount") or 0) for t in new_trades), 2)
        sell_amount = round(sum(float(t.get("amount") or 0) for t in sell_trades), 2)
        cash_after = float((summary or {}).get("cash") or paper.get("cash") or 0)
        cash_impact = {
            "cash_before": round(cash_before, 2),
            "buy_amount": buy_amount,
            "sell_amount": sell_amount,
            "net_cash_flow": round(sell_amount - buy_amount, 2),
            "cash_after": round(cash_after, 2),
            "position_count_before": position_count_before,
            "position_count_after": int((summary or {}).get("position_count") or 0),
            "equity_after": (summary or {}).get("equity"),
            "cost_model": cost_model,
        }
    return cost_model, cash_impact


def _summarize_data_quality(pool: list, holding_codes: list):
    dq = None
    codes: list = []
    try:
        from core.data.facade import summarize_data_quality

        codes = [
            str(s.get("stock_code") or "").strip()
            for s in (pool or [])
            if str(s.get("stock_code") or "").strip()
        ][:12]
        if not codes:
            codes = [c for c in holding_codes if c][:12]
        if codes:
            raw_dq = summarize_data_quality(codes, limit=40)
            dq = {
                "levels": raw_dq.get("levels"),
                "fallback_count": raw_dq.get("fallback_count"),
                "gated_count": raw_dq.get("gated_count"),
                "count": raw_dq.get("count"),
                "adjust_policy": raw_dq.get("adjust_policy"),
            }
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_cycle.py", exc_info=True)
        dq = None
    return dq, codes


def _assess_strategy_health(paper: dict, summary, codes: list, holding_codes: list):
    monitor = None
    try:
        from core.strategy_monitor import assess_strategy_health

        monitor = assess_strategy_health(
            paper,
            summary=summary,
            compute_rolling_ic=True,
            codes=codes or holding_codes,
        )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_cycle.py", exc_info=True)
        monitor = {"ok": True, "alerts": [], "level": "ok"}
    return monitor


def _build_and_write_manifest(
    paper: dict,
    strategy: str,
    cost_model,
    dq,
    simulate_buy: bool,
    new_trades: list,
    sell_trades: list,
    risk_gate,
    buys_blocked: bool,
    monitor,
):
    # Q3：运行清单
    manifest = None
    try:
        from core.run_manifest import build_run_manifest, write_run_manifest

        manifest = build_run_manifest(
            kind="paper_rebalance",
            strategy_id=paper.get("strategy_id") or strategy,
            strategy_version=paper.get("strategy_version"),
            cost_model=cost_model,
            rules=paper.get("rules") or {},
            data_quality=dq,
            extra={
                "simulate_buy": simulate_buy,
                "buy_count": len(new_trades),
                "sell_count": len(sell_trades),
                "risk_ok": (risk_gate or {}).get("ok"),
                "risk_blocks": (risk_gate or {}).get("blocks") or [],
                "buys_blocked": buys_blocked,
                "target_weights": (paper.get("last_optimize") or {}).get("weights_pct"),
                "monitor_level": (monitor or {}).get("level"),
            },
        )
        manifest["path"] = write_run_manifest(manifest)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_cycle.py", exc_info=True)
        pass
    return manifest


def _build_rebalance_report(
    paper: dict,
    pool: list,
    new_trades: list,
    sell_trades: list,
    old_shares_by_code: Dict[str, float],
    name_by_code: dict,
    summary,
    simulate_buy: bool,
) -> List[dict]:
    # 构建调仓报告：每只股票的评分和决策
    rules = paper.get("rules") or {}
    add_score_threshold = float(rules.get("add_score") or 60.0)
    reduce_score_threshold = float(rules.get("reduce_score") or 50.0)
    min_hold_score = float(rules.get("min_hold_score") or 45.0)

    # 交易索引：code -> trade
    buy_by_code = {str(t.get("stock_code")): t for t in new_trades}
    sell_by_code = {str(t.get("stock_code")): t for t in sell_trades}
    # 评分索引：code -> signal_item
    score_by_code = {str(s.get("stock_code")): s for s in pool}

    # 当前 holdings（调仓后）
    current_holdings = paper.get("holdings") or []
    current_shares_by_code = {str(h.get("stock_code")): float(h.get("shares") or 0) for h in current_holdings}

    rebalance_report: List[dict] = []
    book_codes: set = set()
    try:
        from core.signal.cluster.live import load_active_cluster_book

        book_doc = load_active_cluster_book() or {}
        book_codes = {
            str(r.get("stock_code") or r.get("code") or "").strip()
            for r in (book_doc.get("book") or [])
            if isinstance(r, dict)
            and str(r.get("stock_code") or r.get("code") or "").strip()
        }
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_cycle.py", exc_info=True)
        book_codes = set()
    # 遍历调仓前的所有持仓
    for code, old_shares in old_shares_by_code.items():
        name = name_by_code.get(code) or code
        signal = score_by_code.get(code)
        score = signal.get("score") if signal else None
        new_shares = current_shares_by_code.get(code, 0)

        decision = "持有"
        shares_change = new_shares - old_shares
        reason = ""

        if code in sell_by_code:
            t = sell_by_code[code]
            note = t.get("note") or ""
            prior_trim = bool(t.get("sentiment_prior")) or ("舆情先验" in str(note))
            market_prior_trim = bool(t.get("market_prior")) or ("market_prior" in str(note))
            if "止损" in note:
                decision = "止损卖出"
            elif "超时" in note:
                decision = "超时卖出"
            elif (prior_trim or market_prior_trim) and new_shares > 1e-9:
                decision = "减仓"
            elif "减仓" in note or "清仓" in note or (
                ("舆情先验" in str(note) or "market_prior" in str(note)) and "缩仓" in str(note)
            ):
                decision = "减仓" if new_shares > 1e-9 else "卖出"
            else:
                decision = "卖出"
            reason = note
        elif code in buy_by_code:
            t = buy_by_code[code]
            note = t.get("note") or ""
            if "加仓" in note:
                decision = "加仓"
            else:
                decision = "买入"
            reason = note
        elif score is not None:
            s = float(score)
            if simulate_buy and s >= add_score_threshold:
                decision = "持有（评分达标但未加仓）"
                skip = (signal or {}).get("skip_reason")
                reason = skip or f"评分{s:.1f}≥{add_score_threshold}，未加仓"
            elif s < min_hold_score:
                decision = "持有（评分过低）"
                reason = f"评分{s:.1f}<{min_hold_score}"
            elif s < reduce_score_threshold:
                decision = "持有（评分偏低）"
                reason = f"评分{s:.1f}<{reduce_score_threshold}"
            else:
                decision = "持有"
                reason = f"评分{s:.1f}，处于中性区间"
        else:
            reason = "无评分数据"

        # 生成收益分公式（与持仓/观察同源：因子系数 β · z）
        score_formula = ""
        if signal:
            score_formula = str(signal.get("score_formula") or "")
            if not score_formula and signal.get("sub_scores"):
                from core.signal.score_view import build_score_formula

                score_formula = build_score_formula(
                    {
                        "sub_scores": signal.get("sub_scores"),
                        "return_model": signal.get("return_model"),
                    }
                )

        rebalance_report.append({
            "stock_code": code,
            "stock_name": name,
            "score": round(score, 3) if score is not None else None,
            "predicted_score": (
                signal.get("predicted_score") if signal else None
            ),
            "old_shares": int(old_shares),
            "new_shares": int(new_shares),
            "shares_change": int(shares_change),
            "decision": decision,
            "reason": reason,
            "sentiment_prior": bool(
                (sell_by_code.get(code) or {}).get("sentiment_prior")
            )
            or ("舆情先验" in str(reason or "")),
            "market_prior": bool(
                (sell_by_code.get(code) or {}).get("market_prior")
            )
            or ("market_prior" in str(reason or "")),
            "target_weight_pct": (
                (paper.get("last_optimize") or {}).get("weights_pct") or {}
            ).get(code),
            "factors": signal.get("factors") if signal else None,
            "sub_scores": signal.get("sub_scores") if signal else None,
            "factor_contrib": signal.get("factor_contrib") if signal else None,
            "reasons": signal.get("reasons") if signal else None,
            "hard_reject": signal.get("hard_reject") if signal else None,
            "reject_reason": signal.get("reject_reason") if signal else None,
            "score_formula": score_formula,
            "weight_source": signal.get("weight_source") if signal else None,
            "cluster_label": signal.get("cluster_label") if signal else None,
            "cluster_mode": signal.get("cluster_mode") if signal else None,
            "cluster_version": signal.get("cluster_version") if signal else None,
            "score_global": signal.get("score_global") if signal else None,
            "score_cluster": signal.get("score_cluster") if signal else None,
            "return_model_source": signal.get("return_model_source") if signal else None,
            "factor_coefficients": signal.get("factor_coefficients") if signal else None,
            "score_formula_terms": signal.get("score_formula_terms") if signal else None,
            "predicted_score_tau": (
                signal.get("predicted_score_tau")
                if signal and signal.get("predicted_score_tau") is not None
                else (signal.get("score_rem") if signal else None)
            ),
            "score_rem": signal.get("score_rem") if signal else None,
            "gap_pct": signal.get("gap_pct") if signal else None,
            "event_prior": signal.get("event_prior") if signal else None,
            "as_of_tau": signal.get("as_of_tau") if signal else None,
            "dual_score_fusion": signal.get("dual_score_fusion") if signal else None,
            "y_spec_tau": signal.get("y_spec_tau") if signal else None,
            "in_book": code in book_codes,
            "oos_failed": bool(
                (signal or {}).get("oos_failed")
                or str((signal or {}).get("return_model_source") or "").startswith(
                    "oos_failed"
                )
                or str((signal or {}).get("score_scale") or "") == "heuristic_0_100"
            ),
        })
        if signal:
            try:
                from core.signal.service import get_default_signal_service

                rebalance_report[-1].update(
                    get_default_signal_service().book_fields(signal)
                )
                row = rebalance_report[-1]
                if not row.get("oos_failed"):
                    rms = str(row.get("return_model_source") or "")
                    row["oos_failed"] = rms.startswith("oos_failed") or str(
                        row.get("score_scale") or ""
                    ) == "heuristic_0_100"
                row["in_book"] = code in book_codes
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in paper_cycle.py", exc_info=True)
                pass

    # 按 ŷ_trade 降序（缺则 ŷ_τ / EOD）
    try:
        from core.signal.dual_score import rank_key_for_item

        rebalance_report.sort(
            key=lambda x: rank_key_for_item(x) or 0, reverse=True
        )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_cycle.py", exc_info=True)
        rebalance_report.sort(key=lambda x: x.get("score") or 0, reverse=True)

    try:
        from core.paper.rebalance import attach_change_pct_to_rebalance_report

        attach_change_pct_to_rebalance_report(rebalance_report, summary=summary)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_cycle.py", exc_info=True)
        pass

    return rebalance_report


def _assemble_extras_and_ops_report(
    paper: dict,
    strategy: str,
    risk_gate,
    monitor,
    dq,
    cost_model,
    buys_blocked: bool,
    summary,
):
    sid = paper.get("strategy_id") or strategy
    sver = paper.get("strategy_version")
    risk_blocks = (risk_gate or {}).get("blocks") or []
    monitor_alerts = (monitor or {}).get("alerts") or []
    metrics = (monitor or {}).get("metrics")
    north_star = None
    source_audit = None
    try:
        from core.north_star import merge_north_star_into_metrics

        metrics, north_star = merge_north_star_into_metrics(paper, metrics)
        paper["last_north_star"] = north_star
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_cycle.py", exc_info=True)
        pass
    try:
        from core.data.consistency import audit_code_sources
        from core.paper.ledger import holding_codes

        source_audit = audit_code_sources(holding_codes(paper) or [])
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_cycle.py", exc_info=True)
        source_audit = None
    attribution = {}
    try:
        from core.paper.attribution import build_paper_attribution_lite

        attribution = build_paper_attribution_lite(paper, summary) or {}
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in paper_cycle.py", exc_info=True)
        attribution = {"ok": False, "reason": "attribution_error"}

    ops_report = build_ops_report(
        strategy_id=sid,
        strategy_version=sver,
        cost_model=cost_model,
        data_quality=dq,
        risk_blocks=risk_blocks,
        monitor_alerts=monitor_alerts,
        buys_blocked=buys_blocked,
        optimize=paper.get("last_optimize"),
        risk_limits=(risk_gate or {}).get("limits"),
        monitor_metrics=metrics,
        north_star=north_star,
        source_audit=source_audit,
        exposure=(risk_gate or {}).get("exposure"),
        risk_block_items=(risk_gate or {}).get("block_items"),
        attribution=attribution,
    )
    paper["last_ops_report"] = ops_report
    return sid, sver, risk_blocks, monitor_alerts, ops_report, attribution
