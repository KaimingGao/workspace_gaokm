"""组合回测：中性化 vs 绝对分对照（P52）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional

from core.backtest.topk_backtest import backtest_topk_equal_weight


def _metric_delta(neutral: dict, absolute: dict, key: str) -> Optional[float]:
    n = (neutral or {}).get(key)
    a = (absolute or {}).get(key)
    if n is None or a is None:
        return None
    try:
        return round(float(n) - float(a), 2)
    except (TypeError, ValueError):
        return None


def compare_portfolio_neutralization(
    stock_bars: Dict[str, List[dict]],
    *,
    fundamentals_by_code: Optional[Dict[str, dict]] = None,
    top_k: int = 3,
    horizon_days: int = 3,
    min_score: float = 55.0,
    min_predicted_score: Optional[float] = None,
    min_history: int = 12,
    max_window: int = 30,
    apply_costs: bool = False,
    cost_config: Optional[dict] = None,
    weight_mode: str = "equal",
    max_position_pct: float = 40.0,
    max_sector_pct: float = 60.0,
    dropout_n: int = 0,
) -> Dict[str, Any]:
    """同一 watching 跑中性化 / 绝对分两套组合回测并汇总 delta。"""
    common_kwargs = dict(
        top_k=top_k,
        horizon_days=horizon_days,
        min_score=min_score,
        min_predicted_score=min_predicted_score,
        min_history=min_history,
        max_window=max_window,
        apply_costs=apply_costs,
        cost_config=cost_config,
        fundamentals_by_code=fundamentals_by_code,
        weight_mode=weight_mode,
        max_position_pct=max_position_pct,
        max_sector_pct=max_sector_pct,
        dropout_n=dropout_n,
        # 历史日线无可靠分钟 τ；开闸会把对照臂打成 0 笔
        apply_tau_buy_gate=False,
    )
    neutral = backtest_topk_equal_weight(stock_bars, neutralize=True, **common_kwargs)
    absolute = backtest_topk_equal_weight(stock_bars, neutralize=False, **common_kwargs)

    if not neutral.get("success") and not absolute.get("success"):
        return {
            "success": False,
            "error": neutral.get("error") or absolute.get("error") or "组合回测失败",
            "neutralized": neutral,
            "absolute": absolute,
        }

    nm = neutral.get("metrics") or {}
    am = absolute.get("metrics") or {}
    delta = {
        "total_return_pct": _metric_delta(nm, am, "total_return_pct"),
        "win_rate_pct": _metric_delta(nm, am, "win_rate_pct"),
        "trade_count": _metric_delta(nm, am, "trade_count"),
        "avg_return_pct": _metric_delta(nm, am, "avg_return_pct"),
    }

    winner = "neutralized"
    if delta["total_return_pct"] is not None and delta["total_return_pct"] < 0:
        winner = "absolute"
    elif delta["total_return_pct"] in (None, 0):
        winner = "tie"

    return {
        "success": True,
        "neutralized": neutral,
        "absolute": absolute,
        "delta": delta,
        "winner": winner,
        "fundamentals_count": len(fundamentals_by_code or {}),
        "note": "同一标的与参数下对比调仓日截面中性化；非样本外结论，仅供研究。",
    }


def summarize_portfolio_neutral_compare(
    *,
    codes: Optional[List[str]] = None,
    lookback: Optional[int] = None,
    top_k: Optional[int] = None,
    horizon_days: Optional[int] = None,
    min_score: Optional[float] = None,
    fetch_fundamentals: Optional[bool] = None,
    apply_costs: Optional[bool] = None,
) -> Dict[str, Any]:
    """每日报告用的轻量中性化对照摘要（P54）。"""
    from core.research.portfolio_bars import DAILY_PORTFOLIO_MAX_NAMES
    from core.watching.store import read_watching
    from quant.research.portfolio_data import (
        DAILY_BT_UI_LOOKBACK,
        load_portfolio_stock_bars,
        resolve_daily_topk_backtest_kwargs,
    )

    lb = int(lookback) if lookback is not None else int(DAILY_BT_UI_LOOKBACK)
    candidates = list(codes or [])
    if not candidates:
        try:
            uni = read_watching()
            candidates = uni.get("watchlist") or []
        except FileNotFoundError:
            return {"success": False, "error": "watching.json 不存在且无 codes"}

    if len(candidates) < 2:
        return {"success": False, "error": "候选标的不足"}

    n_all = len(candidates)
    stock_bars, failures, fundamentals_by_code = load_portfolio_stock_bars(
        candidates,
        lookback=lb,
        fetch_fundamentals=fetch_fundamentals,
        offline_ok=True,
        max_names=DAILY_PORTFOLIO_MAX_NAMES,
        fundamentals_live=False,
    )
    if len(stock_bars) < 2:
        return {
            "success": False,
            "error": f"有效日线不足（{len(stock_bars)}）",
            "failures": failures,
        }

    resolved = resolve_daily_topk_backtest_kwargs(
        top_k=top_k,
        horizon_days=horizon_days,
        apply_costs=apply_costs,
    )
    cmp_kwargs: Dict[str, Any] = {
        "fundamentals_by_code": fundamentals_by_code or None,
        "top_k": resolved["top_k"],
        "horizon_days": resolved["horizon_days"],
        "min_predicted_score": resolved["min_predicted_score"],
        "apply_costs": resolved["apply_costs"],
        "weight_mode": resolved["weight_mode"],
        "max_position_pct": resolved["max_position_pct"],
        "max_sector_pct": resolved["max_sector_pct"],
    }
    if min_score is not None:
        cmp_kwargs["min_score"] = float(min_score)

    out = compare_portfolio_neutralization(stock_bars, **cmp_kwargs)
    if not out.get("success"):
        out["failures"] = failures
        return out

    nm = (out.get("neutralized") or {}).get("metrics") or {}
    am = (out.get("absolute") or {}).get("metrics") or {}
    delta = out.get("delta") or {}
    winner = out.get("winner") or "tie"
    n_ret = nm.get("total_return_pct")
    a_ret = am.get("total_return_pct")
    d_ret = delta.get("total_return_pct")
    if winner == "neutralized":
        interp = f"调仓日截面中性化累计收益较未中性化ŷ高 {abs(d_ret or 0)} 个百分点"
    elif winner == "absolute":
        interp = f"未中性化ŷ累计收益较中性化高 {abs(d_ret or 0)} 个百分点"
    else:
        interp = "中性化与未中性化ŷ累计收益接近"

    return {
        "success": True,
        "winner": winner,
        "delta": delta,
        "neutralized_total_return_pct": n_ret,
        "absolute_total_return_pct": a_ret,
        "neutralized_win_rate_pct": nm.get("win_rate_pct"),
        "absolute_win_rate_pct": am.get("win_rate_pct"),
        "fundamentals_count": out.get("fundamentals_count", 0),
        "loaded_stocks": list(stock_bars.keys()),
        "interpretation": interp,
        "failures": failures,
        "note": (
            (
                f"日报轻量对照截断观察池 {n_all}→{DAILY_PORTFOLIO_MAX_NAMES}；"
                if n_all > DAILY_PORTFOLIO_MAX_NAMES
                else ""
            )
            + "快照基本面 + 截面中性化对照；非 point-in-time，仅供研究。"
        ),
    }
