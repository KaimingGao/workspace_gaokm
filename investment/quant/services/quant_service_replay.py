"""QuantService · ② 回溯（研究池 Top-K 回测 / 中性化对照）。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# 网格「过门」：OOS 累计 ≥0 且全样本回撤 ≤15%；与日报/主回测过门口径对齐。
PARAM_GRID_MIN_OOS_PCT = 0.0
PARAM_GRID_MAX_DD_PCT = 15.0


def _safe_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def param_grid_cell_eligible(
    cell: Dict[str, Any],
    *,
    min_oos_pct: float = PARAM_GRID_MIN_OOS_PCT,
    max_dd_pct: float = PARAM_GRID_MAX_DD_PCT,
) -> Tuple[bool, Optional[str]]:
    """格是否过门。返回 (过门, 未过原因码)。"""
    if not cell.get("success"):
        return False, "bt_fail"
    if not cell.get("oos_ok"):
        return False, "oos_short"
    oos = _safe_float(cell.get("oos_return_pct"))
    dd = _safe_float(cell.get("max_drawdown_pct"))
    if oos is None:
        return False, "oos_missing"
    if dd is None:
        return False, "dd_missing"
    if oos < float(min_oos_pct):
        return False, "oos_negative"
    if dd > float(max_dd_pct):
        return False, "dd_over"
    return True, None


def select_param_grid_best(
    cells: Sequence[Dict[str, Any]],
    *,
    canonical_lookback: Optional[int] = None,
    min_oos_pct: float = PARAM_GRID_MIN_OOS_PCT,
    max_dd_pct: float = PARAM_GRID_MAX_DD_PCT,
) -> Optional[Dict[str, Any]]:
    """过门格中选优：固定最长 lookback，再按 OOS 高 → 回撤低 → K 小。

    不同 lookback 不是同一段历史，禁止用短窗样本内收益冒充最优。
    """
    pool: List[Dict[str, Any]] = []
    for c in cells or []:
        ok, _reason = param_grid_cell_eligible(
            c, min_oos_pct=min_oos_pct, max_dd_pct=max_dd_pct
        )
        if ok:
            pool.append(c)
    if canonical_lookback is not None:
        same_lb = [
            c
            for c in pool
            if int(c.get("lookback") or 0) == int(canonical_lookback)
        ]
        if same_lb:
            pool = same_lb
        else:
            return None
    if not pool:
        return None

    def _key(c: Dict[str, Any]) -> Tuple[int, float, float, int]:
        failed = 1 if c.get("oos_failed") else 0
        oos = _safe_float(c.get("oos_return_pct"))
        dd = _safe_float(c.get("max_drawdown_pct"))
        k = int(c.get("top_k") or 0)
        return (
            failed,
            -(oos if oos is not None else -1e18),
            dd if dd is not None else 1e18,
            k,
        )

    return dict(min(pool, key=_key))


def mark_equivalent_param_grid_cells(cells: List[Dict[str, Any]]) -> None:
    """同一 lookback 下收益/OOS/回撤/笔数相同的不同 K，多半是门槛填不满。"""
    groups: Dict[Tuple[Any, ...], List[int]] = {}
    for i, c in enumerate(cells or []):
        if not c.get("success"):
            continue
        key = (
            int(c.get("lookback") or 0),
            round(_safe_float(c.get("oos_return_pct")) or 0.0, 4),
            round(_safe_float(c.get("total_return_pct")) or 0.0, 4),
            round(_safe_float(c.get("max_drawdown_pct")) or 0.0, 4),
            int(c.get("trade_count") or 0),
        )
        groups.setdefault(key, []).append(i)
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        ks = sorted(int(cells[i].get("top_k") or 0) for i in idxs)
        note = f"K={','.join(str(k) for k in ks)} 指标相同（门槛可能填不满更大 K）"
        for i in idxs:
            cells[i]["equivalent_ks"] = ks
            cells[i]["equivalent_note"] = note


def _metrics_slice(result: Dict[str, Any]) -> Dict[str, Any]:
    m = (result or {}).get("metrics") or {}
    return {
        "total_return_pct": m.get("total_return_pct"),
        "max_drawdown_pct": m.get("max_drawdown_pct"),
        "win_rate_pct": m.get("win_rate_pct"),
        "trade_count": m.get("trade_count") or (result or {}).get("trade_count"),
        "avg_return_pct": m.get("avg_return_pct"),
        "sharpe_approx": m.get("sharpe_approx"),
    }


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
        from core.watching_store import read_watching

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
    """② 回溯：历史 TopK 推演；watching CRUD 见 QuantOpsMixin，横截面见 QuantFactorMixin。"""

    def run_portfolio_backtest(
        self,
        *,
        codes: Optional[List[str]] = None,
        lookback: int = 120,
        top_k: int = 3,
        horizon_days: int = 3,
        min_score: float = 55.0,
        apply_costs: bool = True,
        fetch_fundamentals: Optional[bool] = None,
        include_cost_compare: bool = False,
        include_wf_slices: bool = False,
        wf_n_splits: int = 3,
        weight_mode: str = "score_budget",
        max_position_pct: float = 25.0,
        max_sector_pct: float = 40.0,
        dropout_n: int = 0,
        exclude_st: bool = True,
        min_avg_amount_pctile: Optional[float] = None,
        include_score_ic: bool = False,
        include_quantile: bool = False,
        include_benchmark: bool = True,
        benchmark_code: str = "000300",
        rank_mode: str = "predicted_score",
        min_predicted_score: Optional[float] = None,
        return_model_min_samples: int = 24,
        return_model_ridge_lambda: float = 0.0,
        persist_curve: bool = True,
    ) -> Dict[str, Any]:
        from core.backtest.topk_backtest import backtest_topk_equal_weight
        from core.strategy import backtest_portfolio_defaults
        from quant.research.portfolio_data import load_portfolio_stock_bars

        bt_def = backtest_portfolio_defaults()
        top_k = int(top_k if top_k is not None else bt_def["top_k"])
        weight_mode = str(weight_mode or bt_def["weight_mode"])
        max_position_pct = float(
            max_position_pct
            if max_position_pct is not None
            else bt_def["max_position_pct"]
        )
        max_sector_pct = float(
            max_sector_pct if max_sector_pct is not None else bt_def["max_sector_pct"]
        )
        if min_predicted_score is None:
            min_predicted_score = bt_def.get("min_predicted_score")
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

        common_kw = dict(
            top_k=top_k,
            horizon_days=horizon_days,
            min_score=min_score,
            fundamentals_by_code=fundamentals_by_code or None,
            weight_mode=weight_mode,
            max_position_pct=max_position_pct,
            max_sector_pct=max_sector_pct,
            dropout_n=dropout_n,
            rank_mode=rank_mode,
            min_predicted_score=min_predicted_score,
            return_model_min_samples=return_model_min_samples,
            return_model_ridge_lambda=return_model_ridge_lambda,
            # 历史日线回测无可靠分钟 τ；默认 ŷ_τ 闸会把几乎所有调仓日挡成 0 笔
            apply_tau_buy_gate=False,
        )
        result = backtest_topk_equal_weight(
            stock_bars,
            apply_costs=apply_costs,
            **common_kw,
        )
        result["loaded_stocks"] = list(stock_bars.keys())
        result["failures"] = failures
        result["request"] = {
            "lookback": int(lookback),
            "top_k": int(top_k),
            "horizon_days": int(horizon_days),
            "min_score": float(min_score),
            "apply_costs": bool(apply_costs),
            "weight_mode": str(weight_mode or "equal"),
            "max_position_pct": float(max_position_pct),
            "max_sector_pct": float(max_sector_pct),
            "dropout_n": int(dropout_n or 0),
            "exclude_st": bool(exclude_st),
            "min_avg_amount_pctile": min_avg_amount_pctile,
            "benchmark_code": str(benchmark_code or "000300"),
            "rank_mode": str(rank_mode or "predicted_score"),
            "min_predicted_score": min_predicted_score,
            "apply_tau_buy_gate": False,
            "rank_key": "predicted_score_eod",
            "score_axis_note": "选股键=ŷ_EOD · 关 τ 闸（日线无可靠分钟 τ；≠ live ŷ_trade）",
            "return_model_min_samples": int(return_model_min_samples or 24),
            "return_model_ridge_lambda": float(return_model_ridge_lambda or 0.0),
        }
        dropped = list(result.get("dropped_stocks") or [])
        result["universe"] = {
            "source": resolved.get("source"),
            "candidate_count": len(candidates),
            "watching_count": resolved.get("watching_count"),
            "excluded": list(resolved.get("excluded") or []),
            "loaded_count": len(stock_bars),
            "load_failures": failures,
            "dropped_thin": dropped,
            "dropped_thin_count": len(dropped),
            "filters": filter_meta,
            "filter_dropped": filter_dropped,
            "note": (
                "候选来自 validation_universe（watching−exclude 或 include_only）；"
                "日线过短者进 dropped_thin；可选 ST/成交额过滤。"
            ),
        }
        if not result.get("success"):
            return result

        # T5.1 截面 IC
        if include_score_ic:
            try:
                from core.backtest.pool_ic import compute_pool_cross_section_ic

                result["score_ic"] = compute_pool_cross_section_ic(
                    stock_bars,
                    horizon_days=horizon_days,
                    fundamentals_by_code=fundamentals_by_code or None,
                )
            except Exception as e:
                result["score_ic"] = {"ok": False, "reason": str(e)}

        # T5.2 分层
        if include_quantile:
            try:
                from core.backtest.quantile_backtest import backtest_score_quantiles

                result["quantile_backtest"] = backtest_score_quantiles(
                    stock_bars,
                    horizon_days=horizon_days,
                    fundamentals_by_code=fundamentals_by_code or None,
                )
            except Exception as e:
                result["quantile_backtest"] = {"ok": False, "reason": str(e)}

        # T6 基准
        if include_benchmark:
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
                result["benchmark"] = {"ok": False, "reason": str(e)}

        # T13：IC ↔ Top-K 期收益对齐
        if include_score_ic and (result.get("score_ic") or {}).get("ok"):
            try:
                from core.backtest.ic_equity_align import align_ic_to_equity_periods

                result["ic_equity_align"] = align_ic_to_equity_periods(
                    result.get("score_ic") or {},
                    result.get("equity_curve") or [],
                )
            except Exception as e:
                result["ic_equity_align"] = {"ok": False, "reason": str(e)}

        # T14：promote 软提示（不硬拦）
        hints: List[Dict[str, Any]] = []
        align = result.get("ic_equity_align") or {}
        if align.get("ok") and align.get("aligned_favor_pos_ic") is False:
            hints.append(
                {
                    "level": "warn",
                    "code": "ic_align_mismatch",
                    "text": (
                        "正IC窗均收益未高于非正IC窗："
                        f"差 {align.get('avg_return_spread_pp')}pp。"
                        "打分区分度与 Top-K 时段可能不同向；应用网格最优前请确认。"
                    ),
                }
            )
        qb = result.get("quantile_backtest") or {}
        if qb.get("ok") and qb.get("monotonic_increasing") is False:
            hints.append(
                {
                    "level": "warn",
                    "code": "quantile_non_monotonic",
                    "text": "分层收益非单调：打分全池区分度弱或噪声大。",
                }
            )
        bench = result.get("benchmark") or {}
        if bench.get("ok") and bench.get("warn_abs_pos_excess_neg"):
            hints.append(
                {
                    "level": "warn",
                    "code": "abs_pos_excess_neg",
                    "text": "绝对收益为正但相对基准超额为负（可能只是 beta/池涨）。",
                }
            )
        result["promote_hints"] = hints

        # P1 / R1.3：成本敏感对照（zero vs simple_cn；附冲击均值）
        if include_cost_compare:
            try:
                zero_res = backtest_topk_equal_weight(
                    stock_bars,
                    apply_costs=False,
                    **common_kw,
                )
                cn_res = (
                    result
                    if apply_costs
                    else backtest_topk_equal_weight(
                        stock_bars,
                        apply_costs=True,
                        **common_kw,
                    )
                )
                z_m = _metrics_slice(zero_res)
                c_m = _metrics_slice(cn_res)
                z_ret = z_m.get("total_return_pct")
                c_ret = c_m.get("total_return_pct")
                gap = None
                if z_ret is not None and c_ret is not None:
                    try:
                        gap = round(float(c_ret) - float(z_ret), 2)
                    except (TypeError, ValueError):
                        gap = None
                cn_params = (cn_res.get("params") or {}) if isinstance(cn_res, dict) else {}
                result["cost_compare"] = {
                    "ok": True,
                    "zero": z_m,
                    "simple_cn": c_m,
                    "return_gap_pp": gap,
                    "avg_impact_bps": cn_params.get("avg_impact_bps"),
                    "impact_legs": cn_params.get("impact_legs"),
                    "note": (
                        "同日线窗口；gap = simple_cn − zero（百分点）；"
                        "avg_impact_bps 为有量样本上平方根冲击均值"
                    ),
                }
            except Exception as e:
                result["cost_compare"] = {"ok": False, "reason": str(e)}

        # P1：Walk-forward 最小切片
        if include_wf_slices:
            try:
                from quant.research.wf_slices import run_portfolio_wf_slices

                result["wf_slices"] = run_portfolio_wf_slices(
                    stock_bars,
                    n_splits=max(1, min(int(wf_n_splits or 3), 6)),
                    apply_costs=apply_costs,
                    **common_kw,
                )
            except Exception as e:
                result["wf_slices"] = {"ok": False, "reason": str(e), "folds": []}

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
            result["cost_assumptions"] = {"ok": False, "reason": str(e)}

        try:
            from core.data_consistency import attach_source_audit

            result = attach_source_audit(result, codes=list(stock_bars.keys()))
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in quant_service_replay.py", exc_info=True)
            logger.warning("回测后处理异常", exc_info=True)

        try:
            from core.data_service import summarize_data_quality

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
        # 参数网格逐格调用时必须 persist_curve=False，避免冲掉主回测北极星曲线
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
                        "top_k": top_k,
                        "horizon_days": horizon_days,
                        "min_score": min_score,
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
        return result

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

    def run_param_grid(
        self,
        *,
        codes: Optional[List[str]] = None,
        top_k_values: Optional[List[int]] = None,
        lookback_values: Optional[List[int]] = None,
        horizon_days: int = 3,
        min_score: float = 55.0,
        min_predicted_score: Optional[float] = None,
        apply_costs: bool = True,
        max_cells: int = 12,
        weight_mode: str = "score_budget",
        dropout_n: int = 0,
        exclude_st: bool = True,
        min_avg_amount_pctile: Optional[float] = None,
        rank_mode: str = "predicted_score",
        progress_cb: Optional[Any] = None,
        cancel_check: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """top_k × lookback 网格：不落盘北极星；跳过 IC/分层/基准/WF；按 OOS 过门选优。"""
        top_ks = [int(x) for x in (top_k_values or [10, 15, 20]) if 1 <= int(x) <= 40]
        lookbacks = [
            int(x) for x in (lookback_values or [120]) if 40 <= int(x) <= 500
        ]
        if not top_ks:
            top_ks = [20]
        if not lookbacks:
            lookbacks = [120]
        max_n = max(1, min(int(max_cells or 12), 20))
        pairs = [(lb, tk) for lb in lookbacks for tk in top_ks]
        if len(pairs) > max_n:
            pairs = pairs[:max_n]
        canonical_lookback = max(lb for lb, _tk in pairs) if pairs else max(lookbacks)
        n_pairs = len(pairs)
        load_units = 10
        cell_units = 100
        job_tot = load_units + max(1, n_pairs) * cell_units

        def _emit(msg: str, cur: int) -> None:
            if not progress_cb:
                return
            try:
                progress_cb(msg, int(cur), job_tot)
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in quant_service_replay.py", exc_info=True)
                pass

        def _cancelled() -> bool:
            if not cancel_check:
                return False
            try:
                return bool(cancel_check())
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in quant_service_replay.py", exc_info=True)
                return False

        cells: List[Dict[str, Any]] = []
        try:
            from core.north_star import TTM_EVENT_IDEA, append_ttm_event

            append_ttm_event(
                TTM_EVENT_IDEA,
                ref="param_grid",
                meta={"pairs": len(pairs), "max_cells": max_n},
            )
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in quant_service_replay.py", exc_info=True)
            logger.warning("回测导出异常", exc_info=True)

        from core.backtest.topk_backtest import backtest_topk_equal_weight
        from core.strategy import backtest_portfolio_defaults
        from quant.research.portfolio_data import load_portfolio_stock_bars

        bt_def = backtest_portfolio_defaults()
        # 网格只扫 K：持有期 / ŷ 门槛对齐纸面，不吃页面 research 默认
        # （replay 未水合时前端曾发 h=1、ŷ=1.0，三格同质且与日报不可比）
        paper_h = int(bt_def.get("horizon_days") or 3)
        paper_ymin = bt_def.get("min_predicted_score")
        horizon_days = paper_h
        min_predicted_score = paper_ymin
        weight_mode = str(weight_mode or bt_def.get("weight_mode") or "score_budget")
        max_position_pct = float(bt_def.get("max_position_pct") or 25.0)
        max_sector_pct = float(bt_def.get("max_sector_pct") or 40.0)

        resolved = resolve_replay_candidates(codes)
        if not resolved.get("ok", True) and resolved.get("error"):
            return {
                "success": False,
                "error": resolved["error"],
                "cells": [],
                "best": None,
                "cell_count": 0,
                "eligible_count": 0,
                "apply_best_gate": {"allowed": False, "reason": resolved["error"]},
            }
        candidates = list(resolved.get("codes") or [])
        if not candidates:
            return {
                "success": False,
                "error": "验证宇宙为空（检查 watching / validation_universe）",
                "cells": [],
                "best": None,
                "cell_count": 0,
                "eligible_count": 0,
                "apply_best_gate": {"allowed": False, "reason": "宇宙为空"},
            }
        if _cancelled():
            return {
                "success": False,
                "error": "已取消",
                "cells": [],
                "best": None,
                "cell_count": 0,
                "eligible_count": 0,
                "apply_best_gate": {"allowed": False, "reason": "已取消"},
            }

        load_lb = int(canonical_lookback)
        logger.info(
            "param_grid load bars n=%s lookback=%s cells=%s",
            len(candidates),
            load_lb,
            len(pairs),
        )
        _emit(f"载入日线 {len(candidates)} 只 · lookback={load_lb}", 2)
        stock_bars, failures, _fund = load_portfolio_stock_bars(
            candidates,
            lookback=load_lb,
            fetch_fundamentals=False,
            offline_ok=True,
        )
        filter_meta: Dict[str, Any] = {}
        if exclude_st or min_avg_amount_pctile is not None:
            from core.backtest.universe_filters import filter_universe_bars

            stock_bars, _dropped, filter_meta = filter_universe_bars(
                stock_bars,
                exclude_st=exclude_st,
                min_avg_amount_pctile=min_avg_amount_pctile,
            )
        if len(stock_bars) < 2:
            err = f"有效日线标的不足（{len(stock_bars)}）"
            return {
                "success": False,
                "error": err,
                "failures": failures,
                "cells": [],
                "best": None,
                "cell_count": 0,
                "eligible_count": 0,
                "apply_best_gate": {"allowed": False, "reason": err},
            }
        _emit(f"日线已载入 {len(stock_bars)} 只 · 开始扫格", load_units)

        bars_by_lb: Dict[int, Dict[str, List[dict]]] = {}
        for lb in {int(x[0]) for x in pairs}:
            if lb >= load_lb:
                bars_by_lb[lb] = stock_bars
            else:
                bars_by_lb[lb] = {
                    code: list(bars)[-lb:] for code, bars in stock_bars.items()
                }
        ranks_by_lb: Dict[int, Dict[str, Any]] = {}
        common_kw = dict(
            horizon_days=horizon_days,
            min_score=min_score,
            apply_costs=apply_costs,
            weight_mode=weight_mode,
            max_position_pct=max_position_pct,
            max_sector_pct=max_sector_pct,
            dropout_n=dropout_n,
            rank_mode=rank_mode,
            min_predicted_score=min_predicted_score,
            apply_tau_buy_gate=False,
        )
        for idx, (lookback, top_k) in enumerate(pairs, start=1):
            if _cancelled():
                return {
                    "success": False,
                    "error": "已取消",
                    "cells": cells,
                    "best": None,
                    "cell_count": len(cells),
                    "eligible_count": 0,
                    "apply_best_gate": {"allowed": False, "reason": "已取消"},
                }
            logger.info(
                "param_grid cell %s/%s lookback=%s top_k=%s loaded=%s",
                idx,
                len(pairs),
                lookback,
                top_k,
                len(stock_bars),
            )
            cell_base = load_units + (idx - 1) * cell_units
            h_note = (
                f"h={horizon_days}"
                + (" 逐日重评分，首格较久" if int(horizon_days) <= 1 else "")
            )
            _emit(
                f"格 {idx}/{n_pairs} · lookback={lookback} K={top_k} · {h_note}",
                cell_base + 1,
            )
            cache = ranks_by_lb.setdefault(int(lookback), {})

            def _bt_progress(
                msg: str,
                cur: int = 0,
                tot: int = 0,
                _base=cell_base,
                _idx=idx,
                _tk=top_k,
            ) -> None:
                t = max(1, int(tot or 1))
                c = max(0, int(cur or 0))
                within = min(1.0, c / t)
                _emit(
                    f"格 {_idx}/{n_pairs} K={_tk} · {msg}",
                    _base + int(cell_units * 0.9 * within),
                )

            try:
                raw = backtest_topk_equal_weight(
                    bars_by_lb[int(lookback)],
                    top_k=top_k,
                    precomputed_ranks=cache,
                    progress_cb=_bt_progress if progress_cb else None,
                    cancel_check=cancel_check,
                    **common_kw,
                )
            except Exception as e:
                raw = {"success": False, "error": str(e)}
            m = (raw or {}).get("metrics") or {}
            oos = (raw or {}).get("oos_summary") or {}
            cell: Dict[str, Any] = {
                "lookback": lookback,
                "top_k": top_k,
                "success": bool((raw or {}).get("success")),
                "total_return_pct": m.get("total_return_pct"),
                "max_drawdown_pct": m.get("max_drawdown_pct"),
                "win_rate_pct": m.get("win_rate_pct"),
                "trade_count": m.get("trade_count") or (raw or {}).get("trade_count"),
                "sharpe_approx": m.get("sharpe_approx"),
                "oos_ok": bool(oos.get("ok")),
                "oos_failed": bool(oos.get("failed")),
                "oos_return_pct": oos.get("oos_return_pct"),
                "is_return_pct": oos.get("is_return_pct"),
                "oos_fail_reason": oos.get("fail_reason"),
                "oos_max_drawdown_pct": oos.get("oos_max_drawdown_pct"),
                "is_max_drawdown_pct": oos.get("is_max_drawdown_pct"),
                "error": None if (raw or {}).get("success") else (raw or {}).get("error"),
            }
            eligible, gate_fail = param_grid_cell_eligible(cell)
            cell["eligible"] = eligible
            cell["gate_fail"] = gate_fail
            cells.append(cell)

        mark_equivalent_param_grid_cells(cells)
        best = select_param_grid_best(cells, canonical_lookback=canonical_lookback)
        eligible_count = sum(1 for c in cells if c.get("eligible"))

        matrix: List[List[Optional[float]]] = []
        by_pair = {(c["lookback"], c["top_k"]): c for c in cells}
        for lb in lookbacks:
            row: List[Optional[float]] = []
            for tk in top_ks:
                cell = by_pair.get((lb, tk))
                oos_v = _safe_float((cell or {}).get("oos_return_pct"))
                if cell and cell.get("success") and oos_v is not None:
                    row.append(oos_v)
                else:
                    row.append(None)
            matrix.append(row)

        if best:
            gap_fail = bool(best.get("oos_failed"))
            if gap_fail:
                apply_allowed = False
                gate_reason = (
                    "过门格 OOS≥0 且回撤合格，但内外缺口旗标失败；"
                    "可写入表单，须确认后再跑 Top-K。仍勿静默 promote。"
                )
            else:
                apply_allowed = True
                gate_reason = (
                    "可将过门最优写入回测表单（非 promote，不落盘北极星）。"
                    "请再跑 Top-K 看 IC/分层/曲线。仍勿静默 promote。"
                )
        else:
            apply_allowed = False
            gate_reason = (
                "无格满足 OOS≥0 且回撤≤15%；网格仅为探路，禁止应用。"
            )

        lookback_note = ""
        if len(lookbacks) > 1:
            lookback_note = (
                f"不同 lookback 不是同一段历史，勿横比累计；最优只在 lookback={canonical_lookback} 过门格中选。"
            )

        return {
            "success": True,
            "axes": {"lookback": lookbacks, "top_k": top_ks},
            "cells": cells,
            "best": best,
            "eligible_count": eligible_count,
            "canonical_lookback": canonical_lookback,
            "gate": {
                "min_oos_pct": PARAM_GRID_MIN_OOS_PCT,
                "max_dd_pct": PARAM_GRID_MAX_DD_PCT,
            },
            "cell_count": len(cells),
            "trial_count": len(cells),
            "multiple_testing_note": (
                f"共试验 {len(cells)} 格，过门 {eligible_count} 格"
                f"（OOS≥{PARAM_GRID_MIN_OOS_PCT:g}% 且回撤≤{PARAM_GRID_MAX_DD_PCT:g}%）；"
                "试验次数越多越易过拟合——须纸面复核后再应用。"
            ),
            "heat": {
                "metric": "oos_return_pct",
                "rows": lookbacks,
                "cols": top_ks,
                "matrix": matrix,
            },
            "apply_best_gate": {
                "allowed": apply_allowed,
                "reason": gate_reason,
            },
            "horizon_days": int(horizon_days),
            "min_predicted_score": min_predicted_score,
            "align_paper": True,
            "weight_mode": str(weight_mode or "score_budget"),
            "dropout_n": int(dropout_n or 0),
            "exclude_st": bool(exclude_st),
            "rank_mode": str(rank_mode or "predicted_score"),
            "persist_curve": False,
            "note": (
                "网格跳过 IC/分层/基准/WF，且不覆盖 north_star 最近回测；"
                f"口径对齐纸面 h={horizon_days} ŷ≥{min_predicted_score}；"
                f"trial_count={len(cells)}；过门 {eligible_count} 格；"
                + (lookback_note or "固定页面 lookback，只扫 K。")
            ),
        }

    def start_param_grid_job(self, **kwargs: Any) -> Dict[str, Any]:
        """后台跑参数网格；轮询 ``GET /api/jobs/quant-param-grid``。"""
        import threading

        from core.job_progress import quant_param_grid_job

        quant_param_grid_job.reclaim_if_stale()
        if quant_param_grid_job.is_running():
            return {
                "ok": True,
                "success": True,
                "background": True,
                "reused": True,
                "job": quant_param_grid_job.get(),
            }

        top_ks = [int(x) for x in (kwargs.get("top_k_values") or [10, 15, 20]) if 1 <= int(x) <= 40]
        lookbacks = [
            int(x) for x in (kwargs.get("lookback_values") or [120]) if 40 <= int(x) <= 500
        ]
        if not top_ks:
            top_ks = [20]
        if not lookbacks:
            lookbacks = [120]
        max_n = max(1, min(int(kwargs.get("max_cells") or 12), 20))
        n_pairs = min(max_n, max(1, len(top_ks) * len(lookbacks)))
        job_total = 10 + n_pairs * 100
        job_id = quant_param_grid_job.start(
            kind="param_grid",
            total=job_total,
            message=f"排队中… {n_pairs} 格 · 对齐纸面持有期与 ŷ 门槛",
        )

        def _progress(msg: str, cur: int = 0, tot: int = 0) -> None:
            t = max(1, int(tot or job_total))
            c = max(0, int(cur or 0))
            mapped = max(1, min(job_total - 1, int(round(job_total * c / t))))
            quant_param_grid_job.update(
                current=mapped,
                total=job_total,
                message=str(msg or "运行中…"),
                job_id=job_id,
            )

        def _worker() -> None:
            stop_hb = threading.Event()

            def _heartbeat() -> None:
                while not stop_hb.wait(8.0):
                    if not quant_param_grid_job.touch(job_id=job_id):
                        return

            hb = threading.Thread(
                target=_heartbeat, name=f"param-grid-hb-{job_id}", daemon=True
            )
            hb.start()
            try:
                if quant_param_grid_job.is_cancel_requested():
                    quant_param_grid_job.finish(error="已取消", job_id=job_id)
                    return
                kw = {
                    k: v
                    for k, v in kwargs.items()
                    if k not in ("progress_cb", "cancel_check")
                }
                result = self.run_param_grid(
                    progress_cb=_progress,
                    cancel_check=quant_param_grid_job.is_cancel_requested,
                    **kw,
                )
                if quant_param_grid_job.is_cancel_requested():
                    quant_param_grid_job.finish(
                        error="已取消",
                        result=result if isinstance(result, dict) else None,
                        job_id=job_id,
                    )
                    return
                if not result.get("success"):
                    quant_param_grid_job.finish(
                        error=str(result.get("error") or "网格失败"),
                        result=result,
                        job_id=job_id,
                    )
                    return
                quant_param_grid_job.finish(result=result, job_id=job_id)
            except Exception as e:
                quant_param_grid_job.finish(error=str(e), job_id=job_id)
            finally:
                stop_hb.set()

        threading.Thread(
            target=_worker, name=f"param-grid-{job_id}", daemon=True
        ).start()
        return {
            "ok": True,
            "success": True,
            "background": True,
            "job": quant_param_grid_job.get(),
        }

    def run_portfolio_neutral_compare(
        self,
        *,
        codes: Optional[List[str]] = None,
        lookback: int = 120,
        top_k: int = 3,
        horizon_days: int = 3,
        min_score: float = 55.0,
        min_predicted_score: Optional[float] = None,
        apply_costs: bool = True,
        fetch_fundamentals: Optional[bool] = None,
        weight_mode: str = "score_budget",
        max_position_pct: float = 25.0,
        max_sector_pct: float = 40.0,
        dropout_n: int = 0,
        exclude_st: bool = True,
        min_avg_amount_pctile: Optional[float] = None,
        benchmark_code: str = "000300",
    ) -> Dict[str, Any]:
        from quant.research.portfolio_data import load_portfolio_stock_bars
        from quant.research.portfolio_neutral_compare import compare_portfolio_neutralization

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
                    "loaded_count": len(stock_bars),
                    "load_failures": failures,
                    "filters": filter_meta,
                    "filter_dropped": filter_dropped,
                },
            }

        out = compare_portfolio_neutralization(
            stock_bars,
            fundamentals_by_code=fundamentals_by_code or None,
            top_k=top_k,
            horizon_days=horizon_days,
            min_score=min_score,
            min_predicted_score=min_predicted_score,
            apply_costs=apply_costs,
            weight_mode=weight_mode,
            max_position_pct=max_position_pct,
            max_sector_pct=max_sector_pct,
            dropout_n=dropout_n,
        )
        out["loaded_stocks"] = list(stock_bars.keys())
        out["failures"] = failures
        out["request"] = {
            "lookback": int(lookback),
            "top_k": int(top_k),
            "horizon_days": int(horizon_days),
            "min_score": float(min_score),
            "min_predicted_score": min_predicted_score,
            "apply_costs": bool(apply_costs),
            "weight_mode": str(weight_mode or "equal"),
            "max_position_pct": float(max_position_pct),
            "max_sector_pct": float(max_sector_pct),
            "dropout_n": int(dropout_n or 0),
            "exclude_st": bool(exclude_st),
            "min_avg_amount_pctile": min_avg_amount_pctile,
            "benchmark_code": str(benchmark_code or "000300"),
        }
        out["universe"] = {
            "source": resolved.get("source"),
            "candidate_count": len(candidates),
            "watching_count": resolved.get("watching_count"),
            "excluded": list(resolved.get("excluded") or []),
            "loaded_count": len(stock_bars),
            "load_failures": failures,
            "filters": filter_meta,
            "filter_dropped": filter_dropped,
        }

        # T11：同一基准挂到中性化 / 绝对分两侧
        if out.get("success"):
            try:
                from core.backtest.topk_benchmark import build_topk_benchmark_summary
                from core.research.bt_excess_attach import attach_benchmark_excess

                code = benchmark_code or "000300"
                for key in ("neutralized", "absolute"):
                    arm = out.get(key) or {}
                    if arm.get("success"):
                        arm["benchmark"] = build_topk_benchmark_summary(
                            arm,
                            stock_bars,
                            lookback=lookback,
                            index_code=code,
                        )
                        enriched = attach_benchmark_excess(
                            arm, stock_bars, index_code=code, lookback=lookback
                        )
                        out[key] = enriched
                n_ex = ((out.get("neutralized") or {}).get("benchmark") or {}).get("excess_pct")
                a_ex = ((out.get("absolute") or {}).get("benchmark") or {}).get("excess_pct")
                d_ex = None
                if n_ex is not None and a_ex is not None:
                    try:
                        d_ex = round(float(n_ex) - float(a_ex), 2)
                    except (TypeError, ValueError):
                        d_ex = None
                label = (
                    ((out.get("neutralized") or {}).get("benchmark") or {}).get("benchmark_label")
                    or ((out.get("absolute") or {}).get("benchmark") or {}).get("benchmark_label")
                )
                out["benchmark_compare"] = {
                    "ok": n_ex is not None or a_ex is not None,
                    "benchmark_label": label,
                    "benchmark_code": code,
                    "neutralized_excess_pct": n_ex,
                    "absolute_excess_pct": a_ex,
                    "delta_excess_pct": d_ex,
                    "note": "两侧相对同一基准的超额；Δ超额 = 中性化超额 − 绝对分超额。",
                }
            except Exception as e:
                out["benchmark_compare"] = {"ok": False, "reason": str(e)}

        return out

    def portfolio_daily_summary(self, **kwargs: Any) -> Dict[str, Any]:
        from quant.research.portfolio_data import summarize_portfolio_backtest

        return summarize_portfolio_backtest(**kwargs)

    def portfolio_neutral_compare_summary(self, **kwargs: Any) -> Dict[str, Any]:
        from quant.research.portfolio_neutral_compare import summarize_portfolio_neutral_compare

        return summarize_portfolio_neutral_compare(**kwargs)


# 兼容旧名（P94 文档 / 外部引用）
QuantPortfolioMixin = QuantReplayMixin
