"""QuantService · ② 回溯（研究池 Top-K 回测 / 中性化对照）。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


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
        weight_mode: str = "equal",
        max_position_pct: float = 40.0,
        max_sector_pct: float = 60.0,
        dropout_n: int = 0,
        exclude_st: bool = False,
        min_avg_amount_pctile: Optional[float] = None,
        include_score_ic: bool = True,
        include_quantile: bool = True,
        include_benchmark: bool = True,
        benchmark_code: str = "000300",
        rank_mode: str = "predicted_score",
        min_predicted_score: Optional[float] = None,
        return_model_min_samples: int = 24,
        return_model_ridge_lambda: float = 0.0,
    ) -> Dict[str, Any]:
        from core.backtest.topk_backtest import backtest_topk_equal_weight
        from quant.research.portfolio_data import load_portfolio_stock_bars

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

                result["benchmark"] = build_topk_benchmark_summary(
                    result,
                    stock_bars,
                    lookback=lookback,
                    index_code=benchmark_code or "000300",
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
        except Exception:
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
        except Exception:
            result.setdefault("data_quality", {})

        # R0：落盘回测曲线 + TTM backtest_ready（权威 realization 输入）
        if result.get("success"):
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
            except Exception:
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
        apply_costs: bool = True,
        max_cells: int = 12,
    ) -> Dict[str, Any]:
        """W3.3 · top_k × lookback 网格；跳过 WF / 成本对照以控时。"""
        top_ks = [int(x) for x in (top_k_values or [2, 3, 5]) if 1 <= int(x) <= 10]
        lookbacks = [
            int(x) for x in (lookback_values or [60, 90, 120]) if 40 <= int(x) <= 500
        ]
        if not top_ks:
            top_ks = [3]
        if not lookbacks:
            lookbacks = [120]
        max_n = max(1, min(int(max_cells or 12), 20))
        pairs = [(lb, tk) for lb in lookbacks for tk in top_ks]
        if len(pairs) > max_n:
            pairs = pairs[:max_n]

        cells: List[Dict[str, Any]] = []
        best: Optional[Dict[str, Any]] = None
        try:
            from core.north_star import TTM_EVENT_IDEA, append_ttm_event

            append_ttm_event(
                TTM_EVENT_IDEA,
                ref="param_grid",
                meta={"pairs": len(pairs), "max_cells": max_n},
            )
        except Exception:
            logger.warning("回测导出异常", exc_info=True)
        for lookback, top_k in pairs:
            try:
                raw = self.run_portfolio_backtest(
                    codes=codes,
                    lookback=lookback,
                    top_k=top_k,
                    horizon_days=horizon_days,
                    min_score=min_score,
                    apply_costs=apply_costs,
                    include_cost_compare=False,
                    include_wf_slices=False,
                    fetch_fundamentals=False,
                )
            except Exception as e:
                raw = {"success": False, "error": str(e)}
            m = (raw or {}).get("metrics") or {}
            cell = {
                "lookback": lookback,
                "top_k": top_k,
                "success": bool((raw or {}).get("success")),
                "total_return_pct": m.get("total_return_pct"),
                "max_drawdown_pct": m.get("max_drawdown_pct"),
                "win_rate_pct": m.get("win_rate_pct"),
                "trade_count": m.get("trade_count") or (raw or {}).get("trade_count"),
                "sharpe_approx": m.get("sharpe_approx"),
                "error": None if (raw or {}).get("success") else (raw or {}).get("error"),
            }
            cells.append(cell)
            ret = cell.get("total_return_pct")
            if cell["success"] and ret is not None:
                if best is None or float(ret) > float(best.get("total_return_pct") or -1e18):
                    best = dict(cell)

        # 热力矩阵：行=lookback，列=top_k，值=累计收益
        matrix: List[List[Optional[float]]] = []
        by_pair = {(c["lookback"], c["top_k"]): c for c in cells}
        for lb in lookbacks:
            row: List[Optional[float]] = []
            for tk in top_ks:
                cell = by_pair.get((lb, tk))
                if cell and cell.get("success") and cell.get("total_return_pct") is not None:
                    row.append(float(cell["total_return_pct"]))
                else:
                    row.append(None)
            matrix.append(row)

        return {
            "success": True,
            "axes": {"lookback": lookbacks, "top_k": top_ks},
            "cells": cells,
            "best": best,
            "cell_count": len(cells),
            "trial_count": len(cells),
            "multiple_testing_note": (
                f"共试验 {len(cells)} 格（样本内累计收益选优）；"
                "试验次数越多，最优越易过拟合——须 OOS/纸面复核后再应用。"
            ),
            "heat": {
                "metric": "total_return_pct",
                "rows": lookbacks,
                "cols": top_ks,
                "matrix": matrix,
            },
            "apply_best_gate": {
                "allowed": False,
                "reason": (
                    "网格最优为样本内累计收益；须先用该 lookback/top_k 跑「Top-K 回测」"
                    "且 OOS 未失败后，方可「应用最优」。"
                ),
            },
            "note": (
                "网格跳过 WF 与成本对照；最优按累计收益选取；"
                f"trial_count={len(cells)}；"
                "应用最优受 OOS 闸门约束；已打 TTM idea_opened。"
            ),
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
        weight_mode: str = "equal",
        max_position_pct: float = 40.0,
        max_sector_pct: float = 60.0,
        dropout_n: int = 0,
        exclude_st: bool = False,
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
