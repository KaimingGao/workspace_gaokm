"""纸面调仓 / 日循环流水线（从 paper.py 拆出，降低单文件集中度）。"""

from __future__ import annotations

from typing import Any, Dict, List

from core.paper import (
    append_operation_log,
    append_snapshot,
    build_ops_report,
    mark_to_market,
    resolve_cost_model,
    run_signal_scan,
    simulate_buys,
    simulate_sells,
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

    # 根据策略规格合并 paper.rules / 成本默认（Q2）
    strategy_spec = None
    if strategy:
        try:
            from core.strategy import apply_strategy_to_paper

            strategy_spec = apply_strategy_to_paper(paper, strategy)
        except Exception:
            from core.backtest.strategies import get_strategy

            try:
                strategy_spec = get_strategy(strategy)
                paper_rules = paper.get("rules") or {}
                paper["rules"] = {**paper_rules, **(strategy_spec.get("params") or {})}
            except Exception:
                pass

    holdings = paper.get("holdings") or []
    holding_codes = [str(h.get("stock_code")) for h in holdings if h.get("stock_code")]
    # 保存调仓前的名称映射（清仓后仍可用于报告展示）
    name_by_code = {str(h.get("stock_code")): h.get("stock_name") or "" for h in holdings if h.get("stock_code")}
    # 只评分持仓股票
    score_total = max(len(holding_codes), 1)
    phase_extra = 3
    grand = score_total + phase_extra

    def _score_progress(i: int, n: int, msg: str) -> None:
        _p(i, grand, msg)

    _p(0, grand, f"开始扫描持仓评分…（策略: {strategy}）")
    # 扫描持仓股票评分
    pool = run_signal_scan(paper, on_progress=_score_progress, stock_codes=holding_codes)
    new_trades: List[dict] = []
    sell_trades: List[dict] = []

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

    # Q4：调仓前风控（基于当前市值快照）
    risk_gate = None
    try:
        from core.risk import check_account_risk

        pre_summary = mark_to_market(paper)
        risk_gate = check_account_risk(paper, pre_summary)
    except Exception:
        risk_gate = {"ok": True, "blocks": [], "warnings": []}

    buys_blocked = False
    _p(len(holding_codes) + 1, grand, "减仓/卖出规则…")
    sell_trades = simulate_sells(paper, pool)

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
            min_score=float((paper.get("rules") or {}).get("min_score") or 55.0),
        )
    except Exception:
        paper["last_optimize"] = None

    if simulate_buy:
        if risk_gate and not risk_gate.get("ok"):
            _p(len(holding_codes) + 2, grand, "风控拦截加仓")
            new_trades = []
            buys_blocked = True
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
        else:
            _p(len(holding_codes) + 2, grand, "加仓/买入…")
            new_trades = simulate_buys(paper, pool)
    else:
        _p(len(holding_codes) + 2, grand, "跳过加仓")
    _p(len(holding_codes) + 3, grand, "更新净值…")
    summary = mark_to_market(paper)
    append_snapshot(paper, summary)
    _p(grand, grand, "完成")

    buy_amount = round(sum(float(t.get("amount") or 0) for t in new_trades), 2)
    sell_amount = round(sum(float(t.get("amount") or 0) for t in sell_trades), 2)
    cash_after = float((summary or {}).get("cash") or paper.get("cash") or 0)
    cost_model = resolve_cost_model(paper)
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

    dq = None
    codes: list = []
    try:
        from core.data_service import summarize_data_quality

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
    except Exception:
        dq = None

    monitor = None
    try:
        from core.strategy_monitor import assess_strategy_health

        monitor = assess_strategy_health(
            paper,
            summary=summary,
            compute_rolling_ic=True,
            codes=codes or holding_codes,
        )
    except Exception:
        monitor = {"ok": True, "alerts": [], "level": "ok"}

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
    except Exception:
        pass

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
            if "止损" in note:
                decision = "止损卖出"
            elif "超时" in note:
                decision = "超时卖出"
            elif "减仓" in note or "清仓" in note:
                decision = "减仓"
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

        # 生成评分公式
        score_formula = ""
        if signal and signal.get("sub_scores") and signal.get("factor_contrib"):
            from core.signal.config import load_signal_config

            sub_scores = signal.get("sub_scores") or {}
            contrib = signal.get("factor_contrib") or {}
            signal_cfg = load_signal_config()
            weights = signal_cfg.get("weights") or {}
            factor_labels = {
                "momentum": "动量",
                "technical_pattern": "技术形态",
                "volume_price": "量价",
                "ma_slope": "均线斜率",
                "weekly_confirm": "周线确认",
                "relative_strength": "相对强弱",
                "volatility": "波动",
                "reversal": "反转",
                "liquidity": "流动性",
                "value": "估值",
                "quality": "质量",
            }

            terms = []
            total_contrib = 0
            for factor_name in sub_scores:
                weight = weights.get(factor_name)
                if weight is None:
                    continue
                label = factor_labels.get(factor_name, factor_name)
                sub_score = sub_scores[factor_name]
                contribution = contrib.get(factor_name, 0)
                total_contrib += contribution
                terms.append(f"{label}({sub_score:.1f}×{weight:.2f}={contribution:.2f})")

            if terms:
                formula = " + ".join(terms)
                penalty = contrib.get("regime_penalty")
                if penalty:
                    penalty_val = abs(float(penalty))
                    formula += f" - 环境惩罚({penalty_val:.2f})"
                    total_contrib += float(penalty)

                # 因子交互调整
                interaction = contrib.get("interaction_adj")
                if interaction is not None:
                    ival = float(interaction)
                    if ival != 0:
                        op = " + " if ival > 0 else " - "
                        formula += f"{op}交互调整({abs(ival):.2f})"
                        total_contrib += ival

                # 风控惩罚
                risk_pen = contrib.get("risk_penalty")
                if risk_pen is not None:
                    rpval = abs(float(risk_pen))
                    if rpval > 0:
                        formula += f" - 风控惩罚({rpval:.2f})"
                        total_contrib += float(risk_pen)

                # 舆情情绪调整
                sent_adj = contrib.get("sentiment_adj")
                if sent_adj is not None:
                    sval = float(sent_adj)
                    if sval != 0:
                        op = " + " if sval > 0 else " - "
                        formula += f"{op}舆情({abs(sval):.1f})"
                        total_contrib += sval

                formula += f" = {round(total_contrib, 1)}"
                score_formula = formula

        rebalance_report.append({
            "stock_code": code,
            "stock_name": name,
            "score": round(score, 1) if score is not None else None,
            "old_shares": int(old_shares),
            "new_shares": int(new_shares),
            "shares_change": int(shares_change),
            "decision": decision,
            "reason": reason,
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
        })

    # 按评分降序排列
    rebalance_report.sort(key=lambda x: x.get("score") or 0, reverse=True)

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
    except Exception:
        pass
    try:
        from core.data_consistency import audit_code_sources
        from core.paper import holding_codes

        source_audit = audit_code_sources(holding_codes(paper) or [])
    except Exception:
        source_audit = None
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
    )
    paper["last_ops_report"] = ops_report

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
        "last_optimize": paper.get("last_optimize"),
        "target_weights": (paper.get("last_optimize") or {}).get("weights_pct"),
        "health": monitor,
    }
