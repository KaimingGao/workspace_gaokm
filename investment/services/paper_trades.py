"""PaperService · 买卖 / 图表 / T0。"""

from __future__ import annotations

import copy
import os
from typing import Any, Dict, List, Optional

from core.paper import (
    load_paper,
    mark_to_market,
    save_paper,
    append_snapshot,
    append_operation_log,
    append_trade_legs_to_operation_log,
    capture_mark_snapshot,
    paper_write_lock,
)


def _rebalance_report_from_legs(
    *,
    ranking: List[dict],
    sell_trades: List[dict],
    buy_trades: List[dict],
    holdings_before: List[dict],
    holdings_after: List[dict],
    risk_budget_skips: Optional[List[dict]] = None,
    score_rows: Optional[List[dict]] = None,
) -> List[dict]:
    """把分池/横截面腿转成交易执行页调仓报告行。

    ``ranking`` = 目标簿（选股结果）；``score_rows`` = 展示用全量打分
    （含低于 min_score 被踢出簿的票）。省略时回退 ranking。
    """
    sell_by = {str(t.get("stock_code")): t for t in sell_trades or []}
    buy_by = {str(t.get("stock_code")): t for t in buy_trades or []}
    skip_by = {
        str(s.get("stock_code")): s
        for s in (risk_budget_skips or [])
        if s.get("stock_code")
    }
    # 展示分：优先全量 scored；目标簿仅用于「是否在簿」判断
    display_rows = list(score_rows or []) or list(ranking or [])
    score_by = {
        str(r.get("stock_code")): r.get("score")
        for r in display_rows
        if r.get("stock_code")
    }
    # 卖出腿上若已带分，补进 lookup（持仓不在映射/簿时）
    for t in sell_trades or []:
        code = str(t.get("stock_code") or "")
        if code and code not in score_by and t.get("score") is not None:
            score_by[code] = t.get("score")
    hard_by = {
        str(r.get("stock_code")): str(r.get("reject_reason") or "硬拒绝")
        for r in display_rows
        if r.get("stock_code") and r.get("hard_reject")
    }
    for t in sell_trades or []:
        code = str(t.get("stock_code") or "")
        note = str(t.get("note") or "")
        if code and code not in hard_by and (
            t.get("hard_reject") or "追高" in note or "硬拒绝" in note
        ):
            hard_by[code] = note or "硬拒绝"
    book_codes = {
        str(r.get("stock_code"))
        for r in ranking or []
        if r.get("stock_code")
    }
    row_by = {
        str(r.get("stock_code")): r
        for r in display_rows
        if r.get("stock_code")
    }
    name_by: Dict[str, str] = {}
    for src in (holdings_before or []) + display_rows + (sell_trades or []) + (
        buy_trades or []
    ) + list(skip_by.values()):
        code = str(src.get("stock_code") or "")
        if code and src.get("stock_name"):
            name_by[code] = str(src.get("stock_name"))
    old_shares = {
        str(h.get("stock_code")): float(h.get("shares") or 0)
        for h in holdings_before or []
        if h.get("stock_code")
    }
    new_shares = {
        str(h.get("stock_code")): float(h.get("shares") or 0)
        for h in holdings_after or []
        if h.get("stock_code")
    }
    codes = sorted(
        set(old_shares) | set(new_shares) | set(sell_by) | set(buy_by) | set(skip_by)
    )
    rows: List[dict] = []
    for code in codes:
        o = old_shares.get(code, 0.0)
        n = new_shares.get(code, 0.0)
        decision = "持有"
        reason = ""
        if code in sell_by:
            st = sell_by[code]
            note = str(st.get("note") or "")
            prior_trim = bool(st.get("sentiment_prior")) or ("舆情先验" in note)
            if prior_trim and n > 1e-9:
                decision = "减仓"
            else:
                decision = "卖出"
            reason = note or "分池调仓卖出"
        elif code in buy_by:
            decision = "买入"
            reason = buy_by[code].get("note") or "分池调仓买入"
        elif code in skip_by and abs(n - o) < 1e-9:
            decision = "跳过"
            reason = skip_by[code].get("reason") or "风险预算跳过"
        elif abs(n - o) < 1e-9:
            decision = "持有"
            if code in book_codes:
                reason = "仍在目标簿内"
            elif code in hard_by:
                reason = hard_by[code]
            elif score_by.get(code) is not None:
                reason = "未进目标簿 · 滞回持有"
            else:
                reason = "未纳入本轮打分"
        rank_row = row_by.get(code) or {}
        label = rank_row.get("cluster_label")
        # 字段名与 paper_cycle / 交易执行页 renderRebalanceReport 对齐
        delta = n - o
        if not reason:
            if abs(delta) < 1e-9:
                if code in book_codes:
                    reason = "仍在目标簿内"
                elif score_by.get(code) is not None:
                    reason = "未进目标簿 · 滞回持有"
                else:
                    reason = "未纳入本轮打分"
            elif delta > 0:
                reason = "分池调仓买入"
            else:
                reason = "分池调仓卖出"
        below = bool(rank_row.get("below_min_score"))
        if below and decision == "卖出" and "min_score" not in str(reason):
            reason = (reason or "分池调仓卖出") + " · 低于 min_score"
        st_row = sell_by.get(code) or {}
        sk_row = skip_by.get(code) or {}
        prior_flag = bool(st_row.get("sentiment_prior") or sk_row.get("sentiment_prior")) or (
            "舆情先验" in str(reason or "")
            or "sentiment_prior" in str(reason or "")
        )
        hard_flag = bool(rank_row.get("hard_reject") or code in hard_by)
        oos_failed = bool(rank_row.get("oos_failed"))
        if not oos_failed:
            rms = str(rank_row.get("return_model_source") or "")
            oos_failed = rms.startswith("oos_failed") or str(
                rank_row.get("score_scale") or ""
            ) == "heuristic_0_100"
        row_out = {
            "stock_code": code,
            "stock_name": name_by.get(code) or code,
            "score": score_by.get(code),
            "decision": decision,
            "reason": reason,
            "old_shares": int(o),
            "new_shares": int(n),
            "shares_change": int(delta),
            "shares_before": o,
            "shares_after": n,
            "cluster_label": label,
            "in_book": code in book_codes,
            "oos_failed": oos_failed,
            "weight_source": rank_row.get("weight_source")
            or (
                f"cluster:{label}"
                if label and decision != "卖出"
                else None
            ),
            "score_global": rank_row.get("score_global"),
            "score_cluster": rank_row.get("score_cluster")
            if rank_row.get("score_cluster") is not None
            else rank_row.get("score"),
            "below_min_score": below,
            "hard_reject": hard_flag,
            "reject_reason": rank_row.get("reject_reason") or hard_by.get(code),
            "sentiment_prior": bool(prior_flag)
            if (code in sell_by or code in skip_by)
            else False,
        }
        # tip / 校准列：从打分行透传（book 已含 *_cal；缺则现场补 g）
        try:
            from core.signal.service import get_default_signal_service

            src = dict(rank_row) if isinstance(rank_row, dict) else {}
            if src.get("predicted_score") is None and src.get("predicted_score_blend") is None:
                sc = score_by.get(code)
                if sc is not None:
                    src.setdefault("score", sc)
                    src.setdefault("predicted_score", sc)
            row_out.update(get_default_signal_service().book_fields(src))
            for k in (
                "predicted_score",
                "score_formula",
                "score_formula_terms",
                "reasons",
                "factor_coefficients",
                "return_model_source",
                "cluster_mode",
                "cluster_version",
            ):
                if src.get(k) is not None and row_out.get(k) is None:
                    row_out[k] = src.get(k)
            # book_fields 可能带回 return_model_source / score_scale；再对齐 oos 旗标
            if not row_out.get("oos_failed"):
                rms = str(row_out.get("return_model_source") or src.get("return_model_source") or "")
                row_out["oos_failed"] = rms.startswith("oos_failed") or str(
                    row_out.get("score_scale") or src.get("score_scale") or ""
                ) == "heuristic_0_100"
        except Exception:
            # book_fields 整段失败时仍尽量补校准对照列
            try:
                from core.signal.score_calibration import (
                    attach_calibrated_scores,
                    load_calibration_model,
                )

                src = dict(rank_row) if isinstance(rank_row, dict) else {}
                sc = score_by.get(code)
                if sc is not None:
                    src.setdefault("score", sc)
                    src.setdefault("predicted_score", sc)
                attach_calibrated_scores(
                    src, model_doc=load_calibration_model(), force=True
                )
                for k in (
                    "predicted_score_cal",
                    "predicted_score_eod_rem_cal",
                    "predicted_score_tau_cal",
                    "predicted_score_blend_cal",
                    "score_calibration_applied",
                    "score_calibration_enabled",
                    "score_calibration_eod_oor",
                    "score_calibration_eod_rem_oor",
                    "score_calibration_tau_oor",
                    "score_calibration_note",
                    "score_calibration_partial",
                    "predicted_score_eod",
                    "predicted_score_eod_rem",
                    "predicted_score_tau",
                    "predicted_score_blend",
                ):
                    if k in src:
                        row_out[k] = src.get(k)
            except Exception:
                pass
        rows.append(row_out)
    def _sort_key(row: dict) -> tuple:
        # 预演调仓：按分数降序；同分时买卖优先于持有
        sc_raw = row.get("score")
        try:
            sc = float(sc_raw) if sc_raw is not None and sc_raw != "" else None
        except (TypeError, ValueError):
            sc = None
        # None 排最后
        sc_rank = -(sc if sc is not None else -1.0)
        missing = 0 if sc is not None else 1
        dec = str(row.get("decision") or "")
        if "卖" in dec or "买" in dec or "减" in dec or "加" in dec:
            action = 0
        elif "跳过" in dec:
            action = 1
        else:
            action = 2
        return (missing, sc_rank, action)

    rows.sort(key=_sort_key)
    return rows


def _paper_mutation_token(paper: dict) -> tuple:
    """轻量指纹：确认落账前检测账本是否被并发改写。"""
    holdings = paper.get("holdings") or []
    return (
        round(float(paper.get("cash") or 0), 4),
        tuple(
            sorted(
                (
                    str(h.get("stock_code") or ""),
                    round(float(h.get("shares") or 0), 4),
                )
                for h in holdings
                if isinstance(h, dict)
            )
        ),
        len(paper.get("trades") or []),
        len(paper.get("operation_log") or []),
    )


class PaperTradesMixin:
    def rebalance(
        self,
        *,
        top_k: Optional[int] = None,
        limit: Optional[int] = None,
        cluster_mode: bool = False,
        dry_run: bool = False,
        strategy: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")
        from core.paper_rebalance_orchestrator import (
            prepare_cluster_book_rank,
            resolve_rebalance_mode,
            run_paper_rebalance,
        )

        # 打分 / 行情 / 模拟全部在写锁外；锁内只做短写，避免「分池落账」长时间占锁。
        paper_ro = load_paper(self.path)
        token0 = _paper_mutation_token(paper_ro)
        mode = resolve_rebalance_mode(paper_ro, cluster_mode=cluster_mode)
        ranked_pre = None
        if mode == "cluster_book":
            ranked_pre = prepare_cluster_book_rank(paper_ro, dry_run=dry_run)
            if not (ranked_pre.get("success") or ranked_pre.get("ok")):
                return {
                    **ranked_pre,
                    "mode": mode,
                    "cluster_mode": True,
                    "dry_run": dry_run,
                    "success": False,
                    "ok": False,
                }

        work = copy.deepcopy(paper_ro)
        holdings_before = copy.deepcopy(work.get("holdings") or [])
        if not dry_run:
            capture_mark_snapshot(work)
        result = run_paper_rebalance(
            work,
            mode=mode,
            dry_run=dry_run,
            top_k=top_k,
            limit=limit,
            cluster_mode=cluster_mode,
            strategy=str(strategy or work.get("strategy_id") or "short_conservative"),
            ranked=ranked_pre if mode == "cluster_book" else None,
        )
        if not (result.get("success") or result.get("ok")):
            return {**result, "mode": mode}

        ranking = list(result.get("ranking") or [])
        score_rows = list(result.get("score_rows") or [])
        k = int(result.get("top_k") or top_k or 0)
        ranked = result.get("cluster_pools") or result.get("cross_section")
        health = result.get("health")
        use_cluster = mode == "cluster_book"
        # 兜底：simulate 合并后偶发缺 ranking/score_rows 时从 cluster_pools 取
        if isinstance(ranked, dict):
            if not ranking:
                ranking = list(ranked.get("book") or ranked.get("ranking") or [])
            if not score_rows:
                score_rows = list(ranked.get("scored_all") or [])
                if not score_rows:
                    for g in ranked.get("groups") or []:
                        score_rows.extend(list(g.get("ranking") or []))

        summary = mark_to_market(work)
        sell_trades = list(result.get("sell_trades") or [])
        buy_trades = list(result.get("buy_trades") or [])
        report = _rebalance_report_from_legs(
            ranking=ranking,
            sell_trades=sell_trades,
            buy_trades=buy_trades,
            holdings_before=holdings_before,
            holdings_after=work.get("holdings") or [],
            risk_budget_skips=result.get("risk_budget_skips"),
            score_rows=score_rows or None,
        )
        try:
            from core.paper_rebalance import attach_change_pct_to_rebalance_report

            attach_change_pct_to_rebalance_report(report, summary=summary)
        except Exception:
            pass
        if use_cluster:
            try:
                from core.signal.score_display import annotate_score_gate

                for row in report:
                    gate = annotate_score_gate(
                        row.get("score"), paper=work, item=row
                    )
                    row["min_score"] = gate["min_score"]
                    if row.get("below_min_score") is None:
                        row["below_min_score"] = gate["below_min_score"]
                    elif gate["below_min_score"]:
                        row["below_min_score"] = True
                    if gate.get("gate_score") is not None:
                        row["eod_gate_score"] = gate.get("gate_score")
            except Exception:
                pass

        base_out = {
            "success": True,
            "ok": True,
            "mode": mode,
            "dry_run": dry_run,
            "top_k": k,
            "cluster_mode": use_cluster,
            "sell_trades": sell_trades,
            "buy_trades": buy_trades,
            "rebalance_report": report,
            "summary": summary,
            "cash_impact": result.get("cash_impact"),
            "turnover": result.get("turnover"),
            "turnover_capped": result.get("turnover_capped"),
            "risk_budget_skips": result.get("risk_budget_skips"),
            "attribution": result.get("attribution"),
            "risk_gate": result.get("risk_gate"),
            "ops_report": result.get("ops_report"),
            "dual_score": result.get("dual_score"),
            "empty_reason": result.get("empty_reason"),
            "min_score": result.get("min_score"),
            "min_hold_score": result.get("min_hold_score"),
            "observation_pool_count": len(ranking),
        }
        if use_cluster:
            base_out["cluster_pools"] = ranked
            base_out["health"] = health
            # 建簿阶段空簿原因（与调仓 empty_reason 分列）
            if isinstance(ranked, dict) and ranked.get("empty_reason"):
                base_out["book_empty_reason"] = ranked.get("empty_reason")
                base_out["below_min_score_count"] = ranked.get(
                    "below_min_score_count"
                )
                if not base_out.get("empty_reason") and not ranking:
                    base_out["empty_reason"] = ranked.get("empty_reason")
        else:
            base_out["cross_section"] = ranked

        if dry_run:
            if use_cluster:
                base_out["note"] = (
                    "分池预演 · 复用目标簿 · 未写 paper.json"
                    if result.get("book_reused")
                    else "分池预演 · 未写 paper.json"
                )
                if result.get("book_reused"):
                    base_out["book_reused"] = True
            return base_out

        if use_cluster and result.get("book_reused"):
            base_out["book_reused"] = True
            base_out["note"] = "分池落账 · 复用预演目标簿"
        append_snapshot(work, summary)
        if use_cluster:
            append_trade_legs_to_operation_log(
                work,
                sell_trades,
                buy_trades,
                origin="cluster",
                source="follow",
            )
            append_operation_log(
                work,
                "cluster_pool_rebalance",
                detail=(
                    f"分池 live 调仓 Top{k} · v{(ranked or {}).get('cluster_version')} · "
                    f"卖 {len(sell_trades)} · 买 {len(buy_trades)}"
                ),
                meta={
                    "mode": mode,
                    "cluster_mode": True,
                    "cluster_version": (ranked or {}).get("cluster_version"),
                    "book_codes": [r.get("stock_code") for r in ranking],
                    "buy_count": len(buy_trades),
                    "sell_count": len(sell_trades),
                    "health_alerts": ((health or {}).get("alerts") or [])[:5],
                    "signal_config_touched": False,
                    "origin": "cluster",
                    "source": "follow",
                },
            )
            work["last_cluster_pool"] = {
                "applied_at": summary.get("as_of") if isinstance(summary, dict) else None,
                "cluster_version": (ranked or {}).get("cluster_version"),
                "book_codes": [r.get("stock_code") for r in ranking],
                "buy_count": len(buy_trades),
                "sell_count": len(sell_trades),
                "signal_config_touched": False,
            }

        # 锁内短写：直接 atomic_write，避免 save_paper 再套一层 path_lock（曾导致 macOS 自锁 503）
        from core.io_atomic import atomic_write_json
        from core.paper import trim_paper_lists

        with paper_write_lock(self.path):
            current = load_paper(self.path)
            if _paper_mutation_token(current) != token0:
                return {
                    "success": False,
                    "ok": False,
                    "mode": mode,
                    "dry_run": False,
                    "cluster_mode": use_cluster,
                    "error": "账本已变更，请重新预演后再确认调仓",
                }
            work.pop("watchlist", None)
            trim_paper_lists(work)
            atomic_write_json(self.path, work)
        return base_out

    def buy(self, *, stock_code: str, amount: Optional[float] = None, shares: Optional[float] = None) -> Dict[str, Any]:
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")
        from core.paper import manual_buy

        with paper_write_lock(self.path):
            paper = load_paper(self.path)
            capture_mark_snapshot(paper)
            trade = manual_buy(paper, stock_code, amount=amount, shares=shares)
            summary = mark_to_market(paper)
            append_snapshot(paper, summary)
            from core.paper_costs import fee_fields_from_trade

            fee_meta = fee_fields_from_trade(trade)
            append_operation_log(
                paper, "buy",
                detail=f"买入 {trade.get('stock_name') or trade.get('stock_code')} {trade.get('shares')}股 @ {trade.get('price')}",
                meta={
                    "stock_code": trade.get("stock_code"),
                    "stock_name": trade.get("stock_name"),
                    "shares": trade.get("shares"),
                    "price": trade.get("price"),
                    "amount": trade.get("amount") or trade.get("actual_cost"),
                    "origin": trade.get("origin") or "manual",
                    **fee_meta,
                },
            )
            save_paper(paper, self.path)
        out = self.status()
        out["trade"] = trade
        out["message"] = (
            f"已加仓 {trade.get('stock_name') or trade.get('stock_code')} "
            f"{trade.get('shares')}股 · {trade.get('amount')}元"
        )
        return out

    def sell(
        self,
        *,
        codes: Optional[list] = None,
        stock_code: Optional[str] = None,
        shares: Optional[float] = None,
    ) -> Dict[str, Any]:
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")
        from core.paper import manual_sell

        with paper_write_lock(self.path):
            paper = load_paper(self.path)
            capture_mark_snapshot(paper)
            trades = manual_sell(paper, codes=codes, stock_code=stock_code, shares=shares)
            summary = mark_to_market(paper)
            append_snapshot(paper, summary)
            from core.paper_costs import fee_fields_from_trade, pnl_fields_from_trade

            for t in trades:
                fee_meta = fee_fields_from_trade(t)
                pnl_meta = pnl_fields_from_trade(t)
                append_operation_log(
                    paper, "sell",
                    detail=f"卖出 {t.get('stock_name') or t.get('stock_code')} {t.get('shares')}股 @ {t.get('price')}",
                    meta={
                        "stock_code": t.get("stock_code"),
                        "stock_name": t.get("stock_name"),
                        "shares": t.get("shares"),
                        "price": t.get("price"),
                        "amount": t.get("amount") or t.get("actual_cost"),
                        "origin": t.get("origin") or "manual",
                        **fee_meta,
                        **pnl_meta,
                    },
                )
            save_paper(paper, self.path)
        out = self.status()
        out["trades"] = trades
        out["message"] = f"已卖出 {len(trades)} 笔"
        return out

    def holding_chart(self, stock_code: str, *, lookback: int = 60) -> Dict[str, Any]:
        """单只持仓：近 lookback 日收盘价 + 相对成本浮盈%（非整账净值）。"""
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")
        code = str(stock_code or "").strip()
        if not code:
            raise ValueError("请指定股票代码")

        from core.data_service import bars_and_source

        paper = load_paper(self.path)
        holding = next(
            (
                h
                for h in (paper.get("holdings") or [])
                if str(h.get("stock_code") or "").strip() == code
            ),
            None,
        )
        if not holding:
            raise ValueError(f"持仓中没有 {code}")

        cost = float(holding.get("cost") or 0)
        shares = float(holding.get("shares") or 0)
        name = holding.get("stock_name") or code
        bars, src = bars_and_source(code, limit=max(10, min(int(lookback), 120)))
        points = []
        for b in bars or []:
            close = b.get("close")
            try:
                px = float(close)
            except (TypeError, ValueError):
                continue
            if px <= 0:
                continue
            pnl_pct = round((px / cost - 1.0) * 100.0, 2) if cost > 0 else None
            points.append(
                {
                    "date": str(b.get("date") or ""),
                    "close": round(px, 4),
                    "pnl_pct": pnl_pct,
                    "market_value": round(px * shares, 2) if shares else None,
                }
            )
        return {
            "ok": True,
            "mode": "stock",
            "stock_code": code,
            "stock_name": name,
            "cost": cost,
            "shares": shares,
            "data_source": src,
            "points": points,
            "point_count": len(points),
        }

    def simulate_t0(
        self,
        *,
        rules: Optional[dict] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """纸面底仓做 T（日线 high/low 代理，非实盘）。

        dry_run=True：只预演，不写账本。
        """
        if not os.path.isfile(self.path):
            raise FileNotFoundError("请先初始化纸面账户")
        from core.t0.rules import atr_pct_from_bars, simulate_t0_on_holdings
        from core.data_service import bars_and_source

        paper = load_paper(self.path)
        holdings = paper.get("holdings") or []
        if not holdings:
            return {
                "ok": True,
                "success": True,
                "dry_run": dry_run,
                "trades": [],
                "pnl_total": 0.0,
                "results": [],
                "note": "无持仓，跳过做T",
            }

        run_rules = dict(rules or {})
        from core.execution import (
            resolve_effective_execution,
            execution_public_view,
            strip_execution_meta,
        )

        # 预判是否会用分钟（与下方拉取一致）；无持仓分钟时仍先按 paper channel 解析
        bundle = resolve_effective_execution(
            strategy=paper.get("strategy_id"),
            paper=paper,
            request_override=run_rules if run_rules else None,
            channel="paper",
            has_minute=None,
        )
        eff_t0 = strip_execution_meta(bundle["t0"])
        period = str(eff_t0.get("minute_period") or "5")

        bars_by_code: Dict[str, Any] = {}
        atr_by_code: Dict[str, float] = {}
        hist_bars_by_code: Dict[str, Any] = {}
        minute_bars_by_code: Dict[str, Any] = {}
        for h in holdings:
            code = str(h.get("stock_code") or "")
            if not code:
                continue
            bars, _src = bars_and_source(code, limit=40)
            if bars:
                bar = dict(bars[-1])
                if len(bars) >= 2 and not bar.get("prev_close"):
                    prev_c = float(bars[-2].get("close") or 0)
                    if prev_c > 0:
                        bar["prev_close"] = prev_c
                bars_by_code[code] = bar
                hist_bars_by_code[code] = bars[:-1]
                atr = atr_pct_from_bars(bars[:-1] or bars, 14)
                if atr is not None:
                    atr_by_code[code] = atr
                # 纸面预演：尽量用当日 5m 第一触达
                try:
                    from core.ports.market import (
                        fetch_minute_bars,
                        group_minute_bars_by_date,
                    )

                    mbars, _mmeta = fetch_minute_bars(
                        code, period=period, lookback_days=10, use_cache=True
                    )
                    by_day = group_minute_bars_by_date(mbars) if mbars else {}
                    day_key = str(bar.get("date") or "")
                    if day_key and by_day.get(day_key):
                        minute_bars_by_code[code] = by_day[day_key]
                    elif by_day:
                        # 取最近有分钟的一天（盘中可能日线已更新、分钟仍是昨日）
                        last_d = sorted(by_day.keys())[-1]
                        minute_bars_by_code[code] = by_day[last_d]
                except Exception:
                    pass

        stance_by_code: Dict[str, Any] = {}
        coup_mode = str((bundle.get("coupling") or {}).get("t0_vs_stance") or "independent")
        if coup_mode != "independent" and holdings:
            try:
                from core.paper import run_signal_scan
                from core.stance import compute_buy_stance

                codes = [str(h.get("stock_code")) for h in holdings if h.get("stock_code")]
                pool = run_signal_scan(paper, stock_codes=codes)
                for item in pool or []:
                    c = str(item.get("stock_code") or "")
                    if not c:
                        continue
                    st = compute_buy_stance(
                        quote={"success": True},
                        signal_item=item,
                    )
                    stance_by_code[c] = st.get("stance_code")
            except Exception:
                stance_by_code = {}

        if not dry_run:
            capture_mark_snapshot(paper)
        result = simulate_t0_on_holdings(
            paper,
            bars_by_code=bars_by_code,
            rules=eff_t0,
            dry_run=dry_run,
            atr_by_code=atr_by_code,
            hist_bars_by_code=hist_bars_by_code,
            minute_bars_by_code=minute_bars_by_code or None,
            stance_by_code=stance_by_code or None,
            coupling=bundle.get("coupling"),
        )
        result["execution"] = execution_public_view(bundle)
        if dry_run:
            return {"ok": True, **result}

        summary = mark_to_market(paper)
        append_snapshot(paper, summary)
        save_paper(paper, self.path)
        return {"ok": True, **result, "summary": summary}

