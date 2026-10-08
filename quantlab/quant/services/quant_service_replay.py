"""QuantService · ② 回溯（历史回测默认 rank_lots / paper_replay）。"""


import logging
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


def resolve_replay_candidates(
    codes: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """解析回测候选：显式 codes，否则 validation_universe（观察池，或 include_only）。"""
    from core.validation_universe import resolve_validation_codes

    if codes:
        cleaned = [str(c).strip() for c in codes if str(c).strip()]
        return {
            "ok": True,
            "codes": cleaned,
            "count": len(cleaned),
            "source": "explicit",
            "excluded": [],
            "watching_count": None,
        }

    watching: List[str] = []
    try:
        from core.watching.store import read_watching

        watching = [
            str(c).strip()
            for c in (read_watching().get("watchlist") or [])
            if str(c).strip()
        ]
    except FileNotFoundError:
        return {
            "ok": False,
            "error": "watching.json 不存在且无 codes",
            "codes": [],
            "count": 0,
            "source": "missing_watching",
            "excluded": [],
            "watching_count": 0,
        }

    resolved = resolve_validation_codes(watching_codes=watching)
    return {
        **resolved,
        "watching_count": len(watching),
    }


class QuantReplayMixin:
    """② 回溯：历史 rank_lots / paper_replay；watching CRUD 见 QuantOpsMixin。"""

    def run_portfolio_backtest(
        self,
        *,
        codes: Optional[List[str]] = None,
        lookback: int = 10,
        apply_costs: bool = True,
        fetch_fundamentals: Optional[bool] = None,
        exclude_st: bool = True,
        min_avg_amount_pctile: Optional[float] = None,
        include_benchmark: bool = True,
        benchmark_code: str = "pool",
        persist_curve: bool = True,
        fusion_w_co: float = 1.0,
        fusion_w_oo: Optional[float] = None,
        fusion_w_oc: Optional[float] = None,
        rank_enter: float = 0.001,
        rank_strong: float = 0.001,
        rank_enter_alt: Optional[float] = None,
        y_enter_enabled: bool = True,
        y_enter_alt_enabled: bool = True,
        y_oo_gt0: Optional[bool] = None,
        y_τc_gt0: Optional[bool] = None,
        oo_rank_max: Optional[int] = None,
        initial_cash: Optional[float] = None,
        fill_clock: str = "09:30",
        lot_base: Optional[float] = None,
        lot_strong: Optional[float] = None,
        lot_base_amount: Optional[float] = None,
        lot_strong_amount: Optional[float] = None,
        use_predictability_tiers: bool = False,
        predictability_tiers: Optional[Sequence[str]] = None,
        holdout_trading_days: Optional[int] = None,
        predictability_head: str = "oo",
        price_space_gate: Optional[bool] = None,
        score_model_role: Optional[str] = None,
        score_backend: Optional[str] = None,
        progress_cb: Optional[Any] = None,
        cancel_cb: Optional[Any] = None,
    ) -> Dict[str, Any]:
        from core.strategy import backtest_portfolio_defaults
        from quant.research.portfolio_data import load_portfolio_stock_bars

        bt_def = backtest_portfolio_defaults()
        max_positions = max(1, int(bt_def.get("max_positions") or 20))
        exclude_st = bool(exclude_st if exclude_st is not None else bt_def["exclude_st"])

        def _emit(msg: str, cur: int = 0, tot: int = 1) -> None:
            if progress_cb is None:
                return
            try:
                progress_cb(str(msg or "回测中…"), int(cur or 0), max(1, int(tot or 1)))
            except Exception:  # noqa: BLE001
                logger.debug("portfolio progress_cb failed", exc_info=True)

        def _cancelled() -> bool:
            if cancel_cb is None:
                return False
            try:
                return bool(cancel_cb())
            except Exception:  # noqa: BLE001
                logger.debug("portfolio cancel_cb failed", exc_info=True)
                return False

        if _cancelled():
            return {"success": False, "error": "已取消", "cancelled": True}

        _emit("解析观察池…")
        resolved = resolve_replay_candidates(codes)
        if not resolved.get("ok", True) and resolved.get("error"):
            return {"success": False, "error": resolved["error"], "universe": resolved}
        candidates = list(resolved.get("codes") or [])
        if not candidates:
            return {
                "success": False,
                "error": "验证宇宙为空（检查 watching / validation_universe）",
                "universe": resolved,
            }

        fit_meta: Dict[str, Any] = {
            "unrestricted": True,
            "n_in": len(candidates),
            "n_out": len(candidates),
        }

        from core.research.return_tree import normalize_rebalance_score_backend

        backend = normalize_rebalance_score_backend(score_backend)

        pred_meta: Dict[str, Any] = {"enabled": False}
        effective_lookback = int(lookback)
        if use_predictability_tiers:
            from core.research.predictability_tiers import (
                load_predictability_tiers_last,
                tier_code_set,
            )

            tier_rep = load_predictability_tiers_last()
            if not isinstance(tier_rep, dict) or not tier_rep.get("success"):
                return {
                    "success": False,
                    "error": "无枢纽分档报告；请先在研究枢纽跑「观察池分档」（Holdout 前半）",
                    "universe": resolved,
                }
            hold_n_req = (
                int(holdout_trading_days)
                if holdout_trading_days is not None
                else None
            )
            last_hold = tier_rep.get("holdout_n")
            if hold_n_req is not None and last_hold is not None and int(last_hold) != int(hold_n_req):
                return {
                    "success": False,
                    "error": (
                        f"页顶 Holdout={hold_n_req} 与枢纽分档 Holdout={last_hold} 不一致；"
                        "请先在研究枢纽重跑「观察池分档」再回测。"
                    ),
                    "universe": resolved,
                }
            allowed = [
                str(t).strip().upper()
                for t in (predictability_tiers or ["A"])
                if str(t or "").strip()
            ] or ["A"]
            _emit(f"复用枢纽分档 · 回测 lookback={int(lookback)}日…")
            keep = tier_code_set(tier_rep, allowed)
            n_before = len(candidates)
            filtered = [c for c in candidates if c in keep]
            hold_n = (
                int(last_hold)
                if last_hold is not None
                else (int(hold_n_req) if hold_n_req is not None else 20)
            )
            tier_dates = [
                str(d).strip()[:10]
                for d in (tier_rep.get("tier_dates") or [])
                if str(d or "").strip()
            ]
            if len(filtered) < 1:
                return {
                    "success": False,
                    "error": (
                        f"枢纽分档过滤后无标的（允许 {''.join(allowed)}；"
                        f"A{(tier_rep.get('counts') or {}).get('A', 0)}"
                        f"/B{(tier_rep.get('counts') or {}).get('B', 0)}"
                        f"/C{(tier_rep.get('counts') or {}).get('C', 0)}）"
                    ),
                    "universe": resolved,
                    "predictability_tiers": {
                        "enabled": True,
                        "protocol": "hub_tier_filter",
                        "reused_hub": True,
                        "holdout_n": hold_n,
                        "tier_n": tier_rep.get("tier_n") or len(tier_dates),
                        "tier_dates": tier_dates,
                        "as_of_tier_last": tier_rep.get("as_of_tier_last"),
                        "lookback": int(lookback),
                        "allowed_tiers": allowed,
                        "counts": (tier_rep.get("counts") if isinstance(tier_rep, dict) else None),
                        "n_in": n_before,
                        "n_out": 0,
                    },
                }
            candidates = filtered
            # 回测天数用请求 lookback（与 Holdout 独立）；分档只过滤宇宙
            effective_lookback = max(1, int(lookback))
            pred_meta = {
                "enabled": True,
                "protocol": "hub_tier_filter",
                "reused_hub": True,
                "holdout_n": hold_n,
                "tier_n": tier_rep.get("tier_n") or len(tier_dates),
                "lookback": effective_lookback,
                "tier_dates": tier_dates,
                "as_of_tier_last": tier_rep.get("as_of_tier_last"),
                "allowed_tiers": allowed,
                "head": tier_rep.get("head") or str(predictability_head or "oo"),
                "counts": tier_rep.get("counts") if isinstance(tier_rep, dict) else None,
                "n_in": n_before,
                "n_out": len(candidates),
                "note": (
                    "复用研究枢纽 Holdout 前半分档过滤宇宙；"
                    "回测天数=请求 lookback（与 Holdout 独立）。"
                ),
            }
            fit_meta = {
                "unrestricted": False,
                "predictability_tiers": allowed,
                "n_in": n_before,
                "n_out": len(candidates),
                "note": "复用枢纽可预测性档过滤（非 cluster fit_tier）",
            }

        if _cancelled():
            return {"success": False, "error": "已取消", "cancelled": True}
        _emit(f"加载日线 {len(candidates)} 只…")
        stock_bars, failures, fundamentals_by_code = load_portfolio_stock_bars(
            candidates,
            lookback=effective_lookback,
            fetch_fundamentals=fetch_fundamentals,
        )

        filter_meta: Dict[str, Any] = {}
        filter_dropped: List[Dict[str, Any]] = []
        if exclude_st or min_avg_amount_pctile is not None:
            from core.backtest.universe_filters import filter_universe_bars

            stock_bars, filter_dropped, filter_meta = filter_universe_bars(
                stock_bars,
                exclude_st=exclude_st,
                min_avg_amount_pctile=min_avg_amount_pctile,
            )

        if len(stock_bars) < 1:
            return {
                "success": False,
                "error": f"有效日线标的不足（{len(stock_bars)}）",
                "failures": failures,
                "universe": {
                    **resolved,
                    "load_failures": failures,
                    "loaded_count": len(stock_bars),
                    "filters": filter_meta,
                    "filter_dropped": filter_dropped,
                },
            }

        if _cancelled():
            return {"success": False, "error": "已取消", "cancelled": True}

        from core.backtest.paper_replay import (
            REPLAY_CASH_FLOOR,
            REPLAY_FILL_CLOCK,
            REPLAY_FUSION_W_NOWCAST,
            REPLAY_FUSION_W_TRADE,
            REPLAY_INITIAL_CASH,
            REPLAY_RANK_ENTER,
            REPLAY_RANK_STRONG,
            backtest_paper_replay,
            clamp_replay_fill_clock,
            clamp_replay_initial_cash,
            clamp_replay_lot_pair,
            load_replay_minute_bars,
        )
        from core.paper.rebalance.rank_lots import clamp_fusion_weight, coerce_rank_threshold
        from core.signal.yhat_windows import FORMULA_RANKING

        universe_n = len(stock_bars)
        try:
            alpha = float(fusion_w_co)
        except (TypeError, ValueError):
            alpha = 0.0
        if alpha != alpha:
            alpha = 0.0
        fusion_w_co = max(0.0, min(10.0, alpha))
        raw_oo = fusion_w_oo
        raw_oc = fusion_w_oc
        w_oo = clamp_fusion_weight(raw_oo, REPLAY_FUSION_W_TRADE)
        w_τc = clamp_fusion_weight(raw_oc, REPLAY_FUSION_W_NOWCAST)
        enter = coerce_rank_threshold(rank_enter, REPLAY_RANK_ENTER)
        strong = coerce_rank_threshold(rank_strong, REPLAY_RANK_STRONG)
        enter = max(0.0, min(1.0, float(enter)))
        strong = max(0.0, min(1.0, float(strong)))
        if strong < enter:
            strong = enter
        cash = clamp_replay_initial_cash(
            REPLAY_INITIAL_CASH if initial_cash is None else initial_cash
        )
        lot_base_n, lot_strong_n = clamp_replay_lot_pair(
            lot_base_amount if lot_base_amount is not None else lot_base,
            lot_strong_amount if lot_strong_amount is not None else lot_strong,
        )
        clock = clamp_replay_fill_clock(fill_clock, REPLAY_FILL_CLOCK)
        gate_on = True if price_space_gate is None else bool(price_space_gate)
        minute_span = min(max(int(effective_lookback or 30) + 20, 15), 120)
        if _cancelled():
            return {"success": False, "error": "已取消", "cancelled": True}
        _emit(f"加载分钟线 {clock} · {universe_n} 只…")
        minute_bars, minute_meta = load_replay_minute_bars(
            list(stock_bars.keys()),
            lookback_days=minute_span,
        )
        if clock != "09:30" and not minute_bars:
            return {
                "success": False,
                "error": (
                    f"回测时间 {clock} 需要 5 分钟 K，观察池无可用分钟缓存。"
                    "请先在研究页预热分钟线后再跑。"
                ),
                "universe": {
                    **resolved,
                    "load_failures": failures,
                    "loaded_count": len(stock_bars),
                    "filters": filter_meta,
                    "filter_dropped": filter_dropped,
                },
                "minute_meta": minute_meta,
            }
        if _cancelled():
            return {"success": False, "error": "已取消", "cancelled": True}
        from core.research.holdout import normalize_backtest_model_role

        role = normalize_backtest_model_role(score_model_role)
        _emit(f"逐日调仓 {clock} · {int(effective_lookback)} 日…")
        result = backtest_paper_replay(
            stock_bars,
            top_k=universe_n,
            cost_model="simple_cn" if apply_costs else "zero",
            yhat_horizon_days=1,
            lookback=int(effective_lookback),
            initial_cash=cash,
            cash_floor=REPLAY_CASH_FLOOR,
            fusion_w_co=fusion_w_co,
            fusion_w_oo=w_oo,
            fusion_w_oc=w_τc,
            rank_enter=enter,
            rank_strong=strong,
            rank_enter_alt=rank_enter_alt,
            y_enter_enabled=y_enter_enabled,
            y_enter_alt_enabled=y_enter_alt_enabled,
            y_oo_gt0=bool(y_oo_gt0),
            y_τc_gt0=bool(y_τc_gt0),
            oo_rank_max=oo_rank_max,
            fill_clock=clock,
            minute_bars_by_code=minute_bars,
            lot_base_amount=lot_base_n,
            lot_strong_amount=lot_strong_n,
            price_space_cfg=(
                None if price_space_gate is None else {"price_space_gate": gate_on}
            ),
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
            score_model_role=role,
            score_backend=backend,
        )
        result["loaded_stocks"] = list(stock_bars.keys())
        result["failures"] = failures
        result["minute_meta"] = minute_meta
        result["request"] = {
            "engine": "paper_replay",
            "lookback": int(effective_lookback),
            "lookback_requested": int(lookback),
            "use_predictability_tiers": bool(use_predictability_tiers),
            "top_k": int(universe_n),
            "max_positions": int(universe_n),
            "paper_max_positions": int(max_positions),
            "horizon_days": 1,
            "apply_costs": bool(apply_costs),
            "exclude_st": bool(exclude_st),
            "min_avg_amount_pctile": min_avg_amount_pctile,
            "benchmark_code": str(benchmark_code or "pool"),
            "initial_cash": cash,
            "cash_floor": REPLAY_CASH_FLOOR,
            "fusion_w_co": fusion_w_co,
            "fusion_w_oo": w_oo,
            "fusion_w_oc": w_τc,
            "rank_enter": enter,
            "rank_strong": strong,
            "rank_enter_alt": rank_enter_alt,
            "y_enter_enabled": bool(y_enter_enabled),
            "y_enter_alt_enabled": bool(y_enter_alt_enabled),
            "y_oo_gt0": bool(y_oo_gt0),
            "y_τc_gt0": bool(y_τc_gt0),
            "oo_rank_max": oo_rank_max,
            "fill_clock": clock,
            "lot_base_amount": lot_base_n,
            "lot_strong_amount": lot_strong_n,
            "price_space_gate": gate_on,
            "score_model_role": role,
            "score_backend": backend,
            "score_axis_note": (
                "引擎=paper_replay：每个交易日 09:30 rank_lots"
                f"（成交 {clock} 5m · 初始 {cash / 10000:g} 万 · ranking={FORMULA_RANKING} · "
                f"{lot_base_n:g}/{lot_strong_n:g} 元；"
                f"w_oo={w_oo:g}；w_τc={w_τc:g}；"
                f"w_co={fusion_w_co:g}；门槛1/2 入场；"
                f"宇宙=观察池 {universe_n} 只"
                + ("；日分价闸开" if gate_on else "；日分价闸关")
                + (
                    f"；oo_rank<{int(oo_rank_max)}"
                    if oo_rank_max is not None and int(oo_rank_max) > 0
                    else ""
                )
                + ("；ŷ头=Tree" if backend == "tree" else "；ŷ头=Ridge")
                + f"，开加不按纸面 max_positions={max_positions} 截断）；"
                "现金用完即止。"
            ),
        }
        result["universe"] = {
            "source": resolved.get("source"),
            "candidate_count": len(candidates),
            "loaded_count": len(stock_bars),
            "load_failures": failures,
            "filters": filter_meta,
            "filter_dropped": filter_dropped,
            "fit_tiers": fit_meta,
            "predictability_tiers": pred_meta if pred_meta.get("enabled") else None,
            "note": (
                "Holdout 前半分档过滤宇宙；回测天数=请求 lookback（与 Holdout 独立）。"
                if pred_meta.get("enabled")
                else "候选=全部观察池；开加不按纸面持仓上限截断（≠研究 Top-K 独立腿）。"
            ),
        }
        if result.get("cancelled"):
            return result
        if include_benchmark and result.get("success"):
            _emit("挂基准…")
            try:
                from core.backtest.topk_benchmark import (
                    POOL_BENCH_ALIASES,
                    build_topk_benchmark_summary,
                    resolve_tier_a_benchmark_bars,
                )
                from core.research.bt_excess_attach import attach_benchmark_excess

                bench_code = str(benchmark_code or "pool").strip() or "pool"
                bench_bars = stock_bars
                pool_label = None
                if bench_code.lower() in POOL_BENCH_ALIASES:
                    _emit("挂基准（观察池A档等权）…")
                    bench_bars, bench_meta = resolve_tier_a_benchmark_bars(
                        stock_bars,
                        lookback=int(effective_lookback or lookback),
                        load_missing=True,
                    )
                    pool_label = str(
                        (bench_meta or {}).get("label") or "观察池A档等权"
                    )
                    result["benchmark_universe"] = {
                        "source": (bench_meta or {}).get("source"),
                        "n_a": (bench_meta or {}).get("n_a"),
                        "n_used": (bench_meta or {}).get("n_used"),
                        "loaded_extra": (bench_meta or {}).get("loaded_extra"),
                        "label": pool_label,
                        "reason": (bench_meta or {}).get("reason"),
                    }
                result["benchmark"] = build_topk_benchmark_summary(
                    result,
                    bench_bars,
                    lookback=lookback,
                    index_code=bench_code,
                    pool_label=pool_label,
                )
                result = attach_benchmark_excess(
                    result,
                    bench_bars,
                    index_code=bench_code,
                    lookback=lookback,
                )
            except Exception as e:
                logger.exception(
                    "unexpected error attaching paper_replay benchmark"
                )
                result["benchmark"] = {"ok": False, "reason": str(e)}
        if isinstance(result, dict):
            # 账本已在 trades / sim_trades；去掉嵌套 paper 减小下发体积
            result.pop("paper", None)

        try:
            from core.backtest.costs import get_cost_breakdown, load_cost_config

            sample_bars = None
            for bars in stock_bars.values():
                if bars:
                    sample_bars = bars[-40:]
                    break
            sample = get_cost_breakdown(100.0, 100, bars=sample_bars, config=None)
            cfg = load_cost_config(None)
            result["cost_assumptions"] = {
                "ok": True,
                "model": "simple_cn" if apply_costs else "zero",
                "cost_mode": "turnover" if apply_costs else "zero",
                "commission_bps": sample.get("commission_bps"),
                "stamp_duty_bps_sell": cfg.get("stamp_duty_bps_sell"),
                "base_slippage_bps": cfg.get("base_slippage_bps"),
                "max_slippage_bps": cfg.get("max_slippage_bps"),
                "impact_coefficient": cfg.get("impact_coefficient"),
                "impact_cost_bps": sample.get("impact_cost_bps"),
                "avg_impact_bps_run": (result.get("params") or {}).get("avg_impact_bps"),
                "turnover_cost_sum_pct": (result.get("params") or {}).get(
                    "turnover_cost_sum_pct"
                ),
                "round_trip_pct_on_100x100": sample.get("round_trip_pct"),
                "note": (
                    "组合按换手计费（续持不扣往返）；"
                    "示意单票往返供参考；冲击按平方根模型，无量则为 0"
                ),
            }
        except Exception as e:
            logger.exception('unexpected error in run_portfolio_backtest')
            result["cost_assumptions"] = {"ok": False, "reason": str(e)}

        try:
            from core.data.consistency import attach_source_audit

            result = attach_source_audit(result, codes=list(stock_bars.keys()))
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.warning("回测后处理异常", exc_info=True)

        try:
            from core.data.facade import summarize_data_quality

            codes_ok = list(stock_bars.keys())[:12]
            if codes_ok:
                raw_dq = summarize_data_quality(codes_ok, limit=40)
                result["data_quality"] = {
                    "levels": raw_dq.get("levels"),
                    "fallback_count": raw_dq.get("fallback_count"),
                    "gated_count": raw_dq.get("gated_count"),
                    "count": raw_dq.get("count"),
                    "adjust_policy": raw_dq.get("adjust_policy"),
                }
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            result.setdefault("data_quality", {})

        # R0：落盘回测曲线 + TTM backtest_ready（权威 realization 输入）
        # persist_curve=False 时不写北极星（日报摘要 / 研究对照）
        if persist_curve and result.get("success"):
            try:
                from core.north_star import (
                    TTM_EVENT_BACKTEST,
                    append_ttm_event,
                    save_last_backtest_curve,
                )

                curve = result.get("equity_curve") or []
                save_last_backtest_curve(
                    curve,
                    meta={
                        "lookback": lookback,
                        "rank_enter": enter,
                        "fusion_w_co": fusion_w_co,
                        "apply_costs": apply_costs,
                        "metrics": result.get("metrics") or {},
                    },
                    align_to_paper=True,
                )
                append_ttm_event(
                    TTM_EVENT_BACKTEST,
                    ref="portfolio_backtest",
                    meta={"trade_count": (result.get("metrics") or {}).get("trade_count")},
                )
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.warning("回测结果序列化异常", exc_info=True)
        # 线上下发：去掉嵌套 period trades（体积大）；保留腿级 sim_trades 供成交账
        if isinstance(result, dict):
            result.pop("trades", None)
            # trades_sample 仍作调仓期摘要；sim_trades 为全量腿级模拟账
        if persist_curve and isinstance(result, dict) and result.get("success"):
            try:
                from core.backtest_result_store import save_last_portfolio_backtest

                save_last_portfolio_backtest(result)
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.warning("上次回测结果落盘失败", exc_info=True)
        if isinstance(result, dict):
            try:
                from core.backtest_result_store import slim_portfolio_backtest_result

                result = slim_portfolio_backtest_result(result)
            except Exception:  # noqa: BLE001
                logger.debug("slim portfolio backtest result failed", exc_info=True)
        return result

    def load_last_portfolio_backtest(self) -> Dict[str, Any]:
        from core.backtest_result_store import load_last_portfolio_backtest as load_snap

        return load_snap()

    def start_portfolio_backtest_job(
        self,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """后台调仓回测；轮询 ``GET /api/jobs/portfolio-backtest``。"""
        import threading

        from core.job_progress import portfolio_backtest_job

        lookback = int(kwargs.get("lookback") or 10)
        portfolio_backtest_job.reclaim_if_stale()
        if portfolio_backtest_job.is_running():
            return {
                "ok": True,
                "success": True,
                "background": True,
                "reused": True,
                "job": portfolio_backtest_job.get(),
            }

        job_id = portfolio_backtest_job.start(
            kind="portfolio_backtest",
            total=max(1, lookback),
            message="回测入队…",
        )

        def _progress(msg: str = "", cur: int = 0, tot: int = 0) -> None:
            portfolio_backtest_job.update(
                current=max(0, int(cur or 0)),
                total=max(1, int(tot or lookback)),
                message=str(msg or "回测中…"),
                job_id=job_id,
            )

        def _worker() -> None:
            stop_hb = threading.Event()

            def _heartbeat() -> None:
                while not stop_hb.wait(8.0):
                    if not portfolio_backtest_job.touch(job_id=job_id):
                        return

            hb = threading.Thread(
                target=_heartbeat, name=f"portfolio-backtest-hb-{job_id}", daemon=True
            )
            hb.start()
            try:
                if portfolio_backtest_job.is_cancel_requested():
                    portfolio_backtest_job.finish(error="已取消", job_id=job_id)
                    return
                result = self.run_portfolio_backtest(
                    progress_cb=_progress,
                    cancel_cb=portfolio_backtest_job.is_cancel_requested,
                    **kwargs,
                )
                if portfolio_backtest_job.is_cancel_requested() or (
                    isinstance(result, dict) and result.get("cancelled")
                ):
                    portfolio_backtest_job.finish(
                        error="已取消",
                        result=result if isinstance(result, dict) else None,
                        job_id=job_id,
                    )
                    return
                if not isinstance(result, dict):
                    portfolio_backtest_job.finish(error="回测无返回", job_id=job_id)
                    return
                if not result.get("success"):
                    portfolio_backtest_job.finish(
                        error=str(result.get("error") or "回测失败"),
                        result=result,
                        job_id=job_id,
                    )
                    return
                portfolio_backtest_job.update(
                    current=max(1, int((result.get("params") or {}).get("n_days") or lookback)),
                    total=max(1, int((result.get("params") or {}).get("n_days") or lookback)),
                    message="完成",
                    job_id=job_id,
                )
                portfolio_backtest_job.finish(result=result, job_id=job_id)
            except Exception as e:
                logger.exception("unexpected error in portfolio backtest worker")
                portfolio_backtest_job.finish(error=str(e), job_id=job_id)
            finally:
                stop_hb.set()

        threading.Thread(
            target=_worker, name=f"portfolio-backtest-{job_id}", daemon=True
        ).start()
        return {
            "ok": True,
            "success": True,
            "background": True,
            "job": portfolio_backtest_job.get(),
        }

    def fit_return_score_model(self, **kwargs: Any) -> Dict[str, Any]:
        from core.signal.return_score_store import fit_watching_return_model

        return fit_watching_return_model(
            codes=kwargs.get("codes"),
            lookback=int(kwargs.get("lookback") or 600),
            horizon_days=int(kwargs.get("horizon_days") or 3),
            ridge_lambda=float(kwargs.get("ridge_lambda") or 0.0),
            watching_limit=int(kwargs.get("watching_limit") or 12),
            min_samples=int(kwargs.get("min_samples") or 24),
            save_draft=bool(kwargs.get("save_draft", True)),
            holdout_trading_days=int(kwargs.get("holdout_trading_days") or 20),
        )

    def promote_return_score_model(
        self, note: str = "", persist_role: str = "live"
    ) -> Dict[str, Any]:
        from core.signal.return_score_store import promote_return_model_draft

        return promote_return_model_draft(note=note, role=persist_role)

    def return_score_model_status(self) -> Dict[str, Any]:
        from core.signal.return_score_store import return_model_status

        return return_model_status()

    def portfolio_daily_summary(self, **kwargs: Any) -> Dict[str, Any]:
        from quant.research.portfolio_data import summarize_portfolio_backtest

        return summarize_portfolio_backtest(**kwargs)


# 兼容旧名（P94 文档 / 外部引用）
QuantPortfolioMixin = QuantReplayMixin
