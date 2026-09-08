"""QuantService · ② 回溯（历史回测默认 rank_lots / paper_replay）。"""


import logging
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


def resolve_replay_candidates(
    codes: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """解析回测候选：显式 codes，否则 validation_universe（watching − exclude）。"""
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
        lookback: int = 30,
        apply_costs: bool = True,
        fetch_fundamentals: Optional[bool] = None,
        exclude_st: bool = True,
        min_avg_amount_pctile: Optional[float] = None,
        include_benchmark: bool = True,
        benchmark_code: str = "000300",
        persist_curve: bool = True,
        y_on_alpha: float = 0.0,
        fusion_w_trade: float = 0.6,
        fusion_w_nowcast: float = 0.4,
        rank_enter: float = 0.012,
        rank_strong: float = 0.012,
        **legacy_kw: Any,
    ) -> Dict[str, Any]:
        from core.strategy import backtest_portfolio_defaults
        from quant.research.portfolio_data import load_portfolio_stock_bars

        engine_legacy = str(legacy_kw.get("engine") or "").strip().lower()
        if engine_legacy and engine_legacy != "paper_replay":
            logger.debug(
                "run_portfolio_backtest ignored legacy engine=%s (always paper_replay)",
                engine_legacy,
            )

        bt_def = backtest_portfolio_defaults()
        max_positions = max(1, int(bt_def.get("max_positions") or 20))
        exclude_st = bool(exclude_st if exclude_st is not None else bt_def["exclude_st"])

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

        stock_bars, failures, fundamentals_by_code = load_portfolio_stock_bars(
            candidates,
            lookback=lookback,
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

        if len(stock_bars) < 2:
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

        from core.backtest.paper_replay import (
            REPLAY_CASH_FLOOR,
            REPLAY_FUSION_W_NOWCAST,
            REPLAY_FUSION_W_TRADE,
            REPLAY_INITIAL_CASH,
            REPLAY_RANK_ENTER,
            REPLAY_RANK_STRONG,
            backtest_paper_replay,
        )
        from core.paper.rebalance.rank_lots import clamp_fusion_weight, coerce_rank_threshold

        universe_n = len(stock_bars)
        try:
            alpha = float(y_on_alpha)
        except (TypeError, ValueError):
            alpha = 0.0
        if alpha != alpha:
            alpha = 0.0
        y_on_alpha = max(0.0, min(1.0, alpha))
        w_trade = clamp_fusion_weight(fusion_w_trade, REPLAY_FUSION_W_TRADE)
        w_nowcast = clamp_fusion_weight(fusion_w_nowcast, REPLAY_FUSION_W_NOWCAST)
        enter = coerce_rank_threshold(rank_enter, REPLAY_RANK_ENTER)
        strong = coerce_rank_threshold(rank_strong, REPLAY_RANK_STRONG)
        enter = max(0.0, min(1.0, float(enter)))
        strong = max(0.0, min(1.0, float(strong)))
        if strong < enter:
            strong = enter
        result = backtest_paper_replay(
            stock_bars,
            top_k=universe_n,
            cost_model="simple_cn" if apply_costs else "zero",
            yhat_horizon_days=1,
            initial_cash=REPLAY_INITIAL_CASH,
            cash_floor=REPLAY_CASH_FLOOR,
            y_on_alpha=y_on_alpha,
            fusion_w_trade=w_trade,
            fusion_w_nowcast=w_nowcast,
            rank_enter=enter,
            rank_strong=strong,
            lookback=int(lookback),
        )
        result["loaded_stocks"] = list(stock_bars.keys())
        result["failures"] = failures
        result["request"] = {
            "engine": "paper_replay",
            "lookback": int(lookback),
            "top_k": int(universe_n),
            "max_positions": int(universe_n),
            "paper_max_positions": int(max_positions),
            "horizon_days": 1,
            "apply_costs": bool(apply_costs),
            "exclude_st": bool(exclude_st),
            "min_avg_amount_pctile": min_avg_amount_pctile,
            "benchmark_code": str(benchmark_code or "000300"),
            "initial_cash": REPLAY_INITIAL_CASH,
            "cash_floor": REPLAY_CASH_FLOOR,
            "y_on_alpha": y_on_alpha,
            "fusion_w_trade": w_trade,
            "fusion_w_nowcast": w_nowcast,
            "rank_enter": enter,
            "rank_strong": strong,
            "score_axis_note": (
                "引擎=paper_replay：每个交易日 09:30 rank_lots"
                "（初始 50 万 · 现金地板 10 万 · y_fuse/y_on · 1000/2000 股；"
                f"w_trade={w_trade:g}；w_nowcast={w_nowcast:g}；"
                f"α={y_on_alpha:g}；入场={enter:g}；强={strong:g}；"
                f"宇宙=观察池 {universe_n} 只，开加不按纸面 max_positions={max_positions} 截断）；"
                "现金地板约束实际成交。"
            ),
        }
        result["universe"] = {
            "source": resolved.get("source"),
            "candidate_count": len(candidates),
            "loaded_count": len(stock_bars),
            "load_failures": failures,
            "filters": filter_meta,
            "filter_dropped": filter_dropped,
            "note": "候选=全部观察池；开加不按纸面持仓上限截断（≠研究 Top-K 独立腿）。",
        }
        if include_benchmark and result.get("success"):
            try:
                from core.backtest.topk_benchmark import build_topk_benchmark_summary
                from core.research.bt_excess_attach import attach_benchmark_excess

                result["benchmark"] = build_topk_benchmark_summary(
                    result,
                    stock_bars,
                    lookback=lookback,
                    index_code=benchmark_code or "000300",
                )
                result = attach_benchmark_excess(
                    result,
                    stock_bars,
                    index_code=benchmark_code or "sh000300",
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
            logger.debug("catch except Exception: in quant_service_replay.py", exc_info=True)
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
            logger.debug("catch except Exception: in quant_service_replay.py", exc_info=True)
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
                        "y_on_alpha": y_on_alpha,
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
                logger.debug("catch except Exception: in quant_service_replay.py", exc_info=True)
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
                logger.debug("catch except Exception: in quant_service_replay.py", exc_info=True)
                logger.warning("上次回测结果落盘失败", exc_info=True)
        return result

    def load_last_portfolio_backtest(self) -> Dict[str, Any]:
        from core.backtest_result_store import load_last_portfolio_backtest as load_snap

        return load_snap()

    def fit_return_score_model(self, **kwargs: Any) -> Dict[str, Any]:
        from core.signal.return_score_store import fit_watching_return_model

        return fit_watching_return_model(
            codes=kwargs.get("codes"),
            lookback=int(kwargs.get("lookback") or 120),
            horizon_days=int(kwargs.get("horizon_days") or 3),
            ridge_lambda=float(kwargs.get("ridge_lambda") or 0.0),
            watching_limit=int(kwargs.get("watching_limit") or 12),
            min_samples=int(kwargs.get("min_samples") or 24),
            save_draft=bool(kwargs.get("save_draft", True)),
        )

    def promote_return_score_model(self, note: str = "") -> Dict[str, Any]:
        from core.signal.return_score_store import promote_return_model_draft

        return promote_return_model_draft(note=note)

    def return_score_model_status(self) -> Dict[str, Any]:
        from core.signal.return_score_store import return_model_status

        return return_model_status()

    def run_portfolio_neutral_compare(self, **kwargs: Any) -> Dict[str, Any]:
        """研究口已下线；HTTP 410。保留方法以免旧调用崩。"""
        _ = kwargs
        return {
            "success": False,
            "deprecated": True,
            "error": "中性化对照研究口已下线（ŷ 路径开关空转）；产品回测请用 /replay",
        }

    def portfolio_daily_summary(self, **kwargs: Any) -> Dict[str, Any]:
        from quant.research.portfolio_data import summarize_portfolio_backtest

        return summarize_portfolio_backtest(**kwargs)

    def portfolio_neutral_compare_summary(self, **kwargs: Any) -> Dict[str, Any]:
        from quant.research.portfolio_neutral_compare import summarize_portfolio_neutral_compare

        return summarize_portfolio_neutral_compare(**kwargs)


# 兼容旧名（P94 文档 / 外部引用）
QuantPortfolioMixin = QuantReplayMixin
