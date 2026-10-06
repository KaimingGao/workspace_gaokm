"""回测/OOS 结果附带相对指数超额（P0 深化）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional


def attach_benchmark_excess(
    bt_result: Optional[dict],
    stock_bars: Optional[Dict[str, List[dict]]] = None,
    *,
    index_code: str = "sh000300",
    lookback: int = 120,
) -> Dict[str, Any]:
    """就地/拷贝增强：写入 ``benchmark``（TopK 口径）与 ``alpha_beta_legs``。"""
    out = dict(bt_result or {})
    if not out:
        return {
            "ok": False,
            "reason": "empty_bt",
            "benchmark": None,
            "alpha_beta_legs": None,
        }
    bench = out.get("benchmark")
    if not isinstance(bench, dict) or not bench.get("ok"):
        try:
            from core.backtest.topk_benchmark import build_topk_benchmark_summary

            bench = build_topk_benchmark_summary(
                out,
                stock_bars or {},
                index_code=index_code,
                lookback=lookback,
            )
            out["benchmark"] = bench
        except Exception as exc:
            logger.exception('unexpected error in attach_benchmark_excess')
            out["benchmark"] = {"ok": False, "reason": str(exc)}
            bench = out["benchmark"]

    from core.alpha_excess import legs_summary

    strat = None
    try:
        strat = float((out.get("metrics") or {}).get("total_return_pct"))
    except (TypeError, ValueError):
        strat = None
    excess_pack = {
        "ok": bool(isinstance(bench, dict) and bench.get("ok")),
        "total_excess_approx_pct": (bench or {}).get("excess_pct")
        if isinstance(bench, dict)
        else None,
        "ann_ir": (bench or {}).get("ann_ir") if isinstance(bench, dict) else None,
        "ir": (bench or {}).get("ir") if isinstance(bench, dict) else None,
    }
    out["alpha_beta_legs"] = legs_summary(
        total_return_pct=strat, excess_pack=excess_pack
    )
    out["excess_attached"] = True
    return out


def compare_arms_excess(
    baseline_bt: Optional[dict],
    research_bt: Optional[dict],
) -> Dict[str, Any]:
    """两臂超额差：research − baseline（pp）。"""

    def _ex(bt: Optional[dict]) -> Optional[float]:
        if not isinstance(bt, dict):
            return None
        if bt.get("excess_pct") is not None:
            try:
                return float(bt["excess_pct"])
            except (TypeError, ValueError):
                pass
        b = bt.get("benchmark") or {}
        if isinstance(b, dict) and b.get("excess_pct") is not None:
            try:
                return float(b["excess_pct"])
            except (TypeError, ValueError):
                pass
        legs = bt.get("alpha_beta_legs") or {}
        try:
            return float(legs["alpha_leg_approx_pct"])
        except (TypeError, ValueError, KeyError):
            return None

    be = _ex(baseline_bt)
    re = _ex(research_bt)
    delta = None
    if be is not None and re is not None:
        delta = round(re - be, 2)
    return {
        "baseline_excess_pct": be,
        "research_excess_pct": re,
        "delta_excess_pp": delta,
        "note": "超额差与 ΔOOS(总收益) 分列；正值=研究臂相对基准更赚。",
    }
