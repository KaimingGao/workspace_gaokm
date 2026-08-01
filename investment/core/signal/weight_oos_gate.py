"""权重建议 OOS 门禁：当前权 vs 建议权的研究池 Top-K 对照（只读）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _metrics_from_backtest(bt: Dict[str, Any]) -> Dict[str, Any]:
    from core.backtest.oos_report import split_oos_summary

    m = bt.get("metrics") or {}
    curve = bt.get("equity_curve") or []
    oos = split_oos_summary(curve, oos_ratio=0.3)
    return {
        "success": bool(bt.get("success")),
        "error": bt.get("error"),
        "total_return_pct": m.get("total_return_pct"),
        "max_drawdown_pct": m.get("max_drawdown_pct"),
        "win_rate_pct": m.get("win_rate_pct"),
        "trade_count": m.get("trade_count") or len(bt.get("trades") or []),
        "oos": oos,
    }


def _run_topk_with_weights(
    stock_bars: Dict[str, List[dict]],
    weights: Dict[str, float],
    *,
    top_k: int,
    horizon_days: int,
    min_score: float,
    fundamentals_by_code: Optional[Dict[str, dict]] = None,
) -> Dict[str, Any]:
    from core.backtest.topk_backtest import backtest_topk_equal_weight
    from core.signal.config import signal_config_overlay

    with signal_config_overlay({"weights": weights}):
        return backtest_topk_equal_weight(
            stock_bars,
            top_k=top_k,
            horizon_days=horizon_days,
            min_score=min_score,
            apply_costs=True,
            fundamentals_by_code=fundamentals_by_code,
            weight_mode="equal",
        )


def evaluate_weight_suggestion_oos(
    current_weights: Dict[str, float],
    suggested_weights: Dict[str, float],
    *,
    lookback: int = 90,
    top_k: int = 3,
    horizon_days: int = 3,
    min_score: float = 55.0,
    watching_limit: int = 10,
    oos_tol_pp: float = 1.0,
    codes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """对照当前/建议权重跑同一宇宙 Top-K；OOS 段收益不劣于当前（容差）则过门。"""
    from quant.research.portfolio_data import load_portfolio_stock_bars
    from core.watching_store import read_watching

    if codes:
        use_codes = [str(c).strip() for c in codes if str(c).strip()]
    else:
        uni = read_watching()
        use_codes = list(uni.get("watchlist") or [])
    limit = max(3, min(int(watching_limit or 10), 20))
    use_codes = use_codes[:limit]
    if len(use_codes) < 3:
        return {
            "ok": False,
            "passed": False,
            "skipped": True,
            "reason": "watching_too_small",
            "note": "研究池不足 3 只，跳过 OOS 门禁（不阻断导出，但 promote_ready=false）。",
            "stock_count": len(use_codes),
        }

    stock_bars, failures, fund_map = load_portfolio_stock_bars(
        use_codes,
        lookback=lookback,
        fetch_fundamentals=False,
    )
    if len(stock_bars) < 3:
        return {
            "ok": False,
            "passed": False,
            "skipped": True,
            "reason": "bars_too_few",
            "note": f"有效日线不足 3 只（失败 {len(failures)}）",
            "failures": failures[:8],
            "stock_count": len(stock_bars),
        }

    cur_bt = _run_topk_with_weights(
        stock_bars,
        current_weights,
        top_k=top_k,
        horizon_days=horizon_days,
        min_score=min_score,
        fundamentals_by_code=fund_map or None,
    )
    sug_bt = _run_topk_with_weights(
        stock_bars,
        suggested_weights,
        top_k=top_k,
        horizon_days=horizon_days,
        min_score=min_score,
        fundamentals_by_code=fund_map or None,
    )
    cur_m = _metrics_from_backtest(cur_bt)
    sug_m = _metrics_from_backtest(sug_bt)

    if not cur_m["success"] or not sug_m["success"]:
        return {
            "ok": False,
            "passed": False,
            "skipped": True,
            "reason": "backtest_failed",
            "current": cur_m,
            "suggested": sug_m,
            "stock_count": len(stock_bars),
            "codes": list(stock_bars.keys()),
            "note": "Top-K 回测失败，跳过门禁判定。",
        }

    cur_oos = (cur_m.get("oos") or {}).get("oos_return_pct")
    sug_oos = (sug_m.get("oos") or {}).get("oos_return_pct")
    tol = float(oos_tol_pp)
    passed = False
    reason = "insufficient_oos"
    delta_oos = None
    if cur_oos is not None and sug_oos is not None:
        delta_oos = round(float(sug_oos) - float(cur_oos), 2)
        # 建议 OOS 不低于当前 − 容差；且建议自身 OOS 切分未标红失败（或当前也失败）
        sug_failed = bool((sug_m.get("oos") or {}).get("failed"))
        cur_failed = bool((cur_m.get("oos") or {}).get("failed"))
        if delta_oos >= -tol and not (sug_failed and not cur_failed):
            passed = True
            reason = "oos_not_worse"
        elif delta_oos < -tol:
            reason = f"oos_worse_{delta_oos}pp"
        else:
            reason = (sug_m.get("oos") or {}).get("fail_reason") or "suggested_oos_failed"

    return {
        "ok": True,
        "passed": passed,
        "skipped": False,
        "reason": reason,
        "delta_oos_pp": delta_oos,
        "oos_tol_pp": tol,
        "lookback": lookback,
        "top_k": top_k,
        "horizon_days": horizon_days,
        "stock_count": len(stock_bars),
        "codes": list(stock_bars.keys()),
        "current": cur_m,
        "suggested": sug_m,
        "note": (
            "同一研究池 Top-K：权益曲线后 30% 为 OOS。"
            f"过门条件：建议 OOS 收益 ≥ 当前 OOS − {tol}pp，且不新增 OOS 失败旗标。"
        ),
    }
