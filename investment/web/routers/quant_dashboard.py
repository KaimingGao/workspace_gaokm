"""仪表盘 API：全局 KPI · 市场监控 · 净值曲线 · 板块热力 · 信号告警 · 资产配置 · 回撤 · VaR · 因子 IC。

实现按域拆到 ``web.dashboard.*``；本模块保留路由与再导出（单测 ``web.routers.quant_dashboard``）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query

from web import deps  # noqa: F401 — 单测 patch `quant_dashboard.deps`

from web.dashboard.factor_ic import (
    _factor_label,
    _ic_series_from_cluster_cache,
    _ic_series_from_daily_tail,
)
from web.dashboard.kpis import _build_kpis
from web.dashboard.market import (
    _build_market_context_dashboard,
    _build_market_overview,
    _index_quotes_for_overview,  # noqa: F401 — 单测再导出
)
from web.dashboard.nav_curves import (
    _build_dd_series,
    _calc_series_correlation,
    _fetch_benchmark_curve,
    _filter_eq_by_range,
)
from web.dashboard.paper_helpers import (
    _equity_curve_from_paper,
    _equity_curve_with_live,
    _fetch_index_bars_bounded,  # noqa: F401 — 单测再导出
    _holding_market_value,
    _holding_sector,  # noqa: F401 — 单测再导出
    _holdings_from_paper,
    _load_raw_paper,
    _north_star_from_paper,  # noqa: F401 — 单测再导出
    _trades_from_paper,
    _unpack_index_bars,
)
from web.dashboard.portfolio_views import (
    _build_allocation,
    _build_sector_heatmap,
    _build_signals,
)
from web.dashboard.risk_views import (
    _build_factor_exposure,
    _build_risk_metrics,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

# 再导出：tests / 外部 patch 路径
__all__ = [
    "router",
    "_build_kpis",
    "_equity_curve_from_paper",
    "_equity_curve_with_live",
    "_fetch_index_bars_bounded",
    "_holding_market_value",
    "_holding_sector",
    "_holdings_from_paper",
    "_ic_series_from_cluster_cache",
    "_ic_series_from_daily_tail",
    "_index_quotes_for_overview",
    "_load_raw_paper",
    "_north_star_from_paper",
    "_trades_from_paper",
    "_unpack_index_bars",
    "deps",
]

@router.get("/api/dashboard/kpis")
def dashboard_kpis() -> Dict[str, Any]:
    """核心 KPI 卡片数据。"""
    try:
        return _build_kpis()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/dashboard/nav-curve")
def dashboard_nav_curve(range: str = "all", benchmark: str = "hs300") -> Dict[str, Any]:
    """累计净值曲线（归一到 100）。range: 30 | 90 | ytd | all。含基准对比与相关性。"""
    try:
        paper = _load_raw_paper()
        eq_curve = _filter_eq_by_range(_equity_curve_with_live(paper), range)

        raw_points = [
            {
                "time": e.get("time") or e.get("date"),
                "value": e.get("equity"),
                "live": bool(e.get("live")),
            }
            for e in eq_curve
            if e.get("equity") is not None
        ]

        if not raw_points:
            return {"ok": True, "points": [], "benchmark": [], "correlation": None, "count": 0}

        base = raw_points[0]["value"]
        if not base or base <= 0:
            return {"ok": True, "points": [], "benchmark": [], "correlation": None, "count": 0}

        points = [
            {
                "time": p["time"],
                "value": round(p["value"] / base * 100, 2),
                "live": bool(p.get("live")),
            }
            for p in raw_points
        ]

        benchmark_enabled = benchmark != "none"
        bench_points = []
        bench_code = None
        correlation = None
        if benchmark_enabled:
            # 走指数别名表（hs300 / 上证），不要直接传 sh000300 以外的未知码
            bench_code = "hs300" if benchmark in ("hs300", "sh000300", "csi300") else benchmark
            bench_points = _fetch_benchmark_curve(bench_code, eq_curve)
            correlation = _calc_series_correlation(points, bench_points) if bench_points else None

        return {
            "ok": True,
            "points": points,
            "benchmark": bench_points,
            "benchmark_code": bench_code,
            "correlation": correlation,
            "count": len(points),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/dashboard/drawdown")
def dashboard_drawdown(range: str = "all") -> Dict[str, Any]:
    """回撤曲线。range: 30 | 90 | ytd | all。"""
    try:
        paper = _load_raw_paper()
        eq_curve = _filter_eq_by_range(_equity_curve_from_paper(paper), range)

        dd_points = _build_dd_series(eq_curve)

        max_dd = 0.0
        current_dd = 0.0
        if dd_points:
            vals = [abs(p["value"]) for p in dd_points]
            max_dd = round(max(vals), 2) if vals else 0.0
            current_dd = round(abs(dd_points[-1]["value"]), 2)

        return {
            "ok": True,
            "points": dd_points,
            "max_drawdown": max_dd,
            "current_drawdown": current_dd,
            "count": len(dd_points),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/dashboard/var-historical")
def dashboard_var_historical(range_key: str = Query("all", alias="range")) -> Dict[str, Any]:
    """历史模拟 VaR / CVaR 与收益直方图。

    查询参数仍为 ``?range=``；参数名避开内置 ``range``，防止 ``'str' object is not callable``。
    """
    try:
        paper = _load_raw_paper()
        eq_curve = _filter_eq_by_range(_equity_curve_from_paper(paper), range_key)

        equities = [e.get("equity") for e in eq_curve if e.get("equity") is not None]
        if len(equities) < 2:
            return {"ok": False, "message": "净值数据不足（至少2期）"}

        rets: List[float] = []
        for i in range(1, len(equities)):
            e0, e1 = equities[i - 1], equities[i]
            if e0 and e0 > 0 and e1 is not None:
                rets.append(e1 / e0 - 1)

        if len(rets) < 1:
            return {"ok": False, "message": "收益率数据不足"}

        n = len(rets)
        sorted_rets = sorted(rets)

        idx95 = max(0, min(n - 1, int(n * 0.05)))
        idx99 = max(0, min(n - 1, int(n * 0.01)))
        var_95 = -sorted_rets[idx95] * 100
        var_99 = -sorted_rets[idx99] * 100

        var_95_threshold = sorted_rets[idx95]
        var_99_threshold = sorted_rets[idx99]

        cvar_95 = None
        if var_95_threshold is not None:
            tail = [r for r in rets if r <= var_95_threshold]
            cvar_95 = -sum(tail) / len(tail) * 100 if tail else None

        cvar_99 = None
        if var_99_threshold is not None:
            tail = [r for r in rets if r <= var_99_threshold]
            cvar_99 = -sum(tail) / len(tail) * 100 if tail else None

        num_buckets = 20
        min_ret = min(rets)
        max_ret = max(rets)
        bucket_span = max_ret - min_ret if max_ret > min_ret else 0.01
        bucket_size = bucket_span / num_buckets

        histogram: List[Dict[str, Any]] = []
        for b in range(num_buckets):
            lo = min_ret + b * bucket_size
            hi = lo + bucket_size if b < num_buckets - 1 else max_ret
            count = sum(1 for r in rets if lo <= r < hi or (b == num_buckets - 1 and lo <= r <= hi))
            histogram.append({
                "bucket": b,
                "range_low": round(lo * 100, 3),
                "range_high": round(hi * 100, 3),
                "count": count,
                "label": f"{round(lo * 100, 2)}% ~ {round(hi * 100, 2)}%",
            })

        dd_points = _build_dd_series(eq_curve)

        return {
            "ok": True,
            "var_95": round(var_95, 2) if var_95 is not None else None,
            "var_99": round(var_99, 2) if var_99 is not None else None,
            "cvar_95": round(cvar_95, 2) if cvar_95 is not None else None,
            "cvar_99": round(cvar_99, 2) if cvar_99 is not None else None,
            "sample_count": n,
            "mean_return": round(sum(rets) / n * 100, 4),
            "histogram": histogram,
            "drawdown": dd_points,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/dashboard/factor-ic-series")
def dashboard_factor_ic_series(
    lookback: int = 60,
    horizon_days: int = 3,
) -> Dict[str, Any]:
    """时序对比：多因子日频截面 IC（优先分组缓存）。"""
    try:
        lookback = max(10, min(int(lookback or 60), 180))
        from_cache = _ic_series_from_cluster_cache(lookback)
        if from_cache:
            return from_cache

        # 回退：观察池截面因子分（非真 IC，仅占位）
        from core.watching.insights import load_insights_cache

        insights = load_insights_cache()
        if not insights:
            return {
                "ok": True,
                "factors": [],
                "summary": {},
                "note": "无分组 IC 缓存 · 请先运行「跑分组」",
            }

        items = (
            insights
            if isinstance(insights, list)
            else list(insights.values())
            if isinstance(insights, dict)
            else []
        )
        if not items:
            return {"ok": True, "factors": [], "summary": {}, "note": "观察池为空"}

        factor_scores: Dict[str, List[float]] = {}
        for item in items:
            sub_scores = (item or {}).get("sub_scores") or {}
            for factor, score in sub_scores.items():
                try:
                    s = float(score)
                except (TypeError, ValueError):
                    continue
                factor_scores.setdefault(str(factor), []).append(s)

        import math as _math
        from statistics import mean, stdev

        factor_series: List[Dict[str, Any]] = []
        for factor, scores in factor_scores.items():
            if len(scores) < 3:
                continue
            try:
                ic_mean = mean(scores)
                ic_std = stdev(scores) if len(scores) > 1 else 0.0
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in quant_dashboard.py", exc_info=True)
                continue
            ic_ir = (ic_mean / ic_std) if ic_std > 1e-12 else 0.0
            ic_ir_annual = ic_ir * _math.sqrt(252.0 / max(int(horizon_days or 3), 1))
            pos_ratio = sum(1 for s in scores if s > 0) / len(scores) * 100
            points = [
                {"time": i + 1, "value": round(s, 4)} for i, s in enumerate(scores[-lookback:])
            ]
            factor_series.append(
                {
                    "factor": factor,
                    "label": _factor_label(factor),
                    "ic_values": points,
                    "ic_mean": round(ic_mean, 4),
                    "ic_std": round(ic_std, 4),
                    "ic_ir": round(ic_ir, 4),
                    "ic_ir_annual": round(ic_ir_annual, 4),
                    "positive_ratio": round(pos_ratio, 1),
                    "sample_count": len(points),
                }
            )

        factor_series.sort(key=lambda x: abs(float(x.get("ic_ir_annual") or 0)), reverse=True)
        top = factor_series[:5]
        summary = {}
        if top:
            summary = {
                "ic_mean": round(mean(float(f["ic_mean"]) for f in top), 4),
                "ir_mean": round(mean(float(f["ic_ir_annual"]) for f in top), 4),
                "positive_ratio": round(mean(float(f["positive_ratio"]) for f in top), 1),
                "day_count": max(int(f["sample_count"]) for f in top),
                "factor_count": len(top),
            }
        return {
            "ok": True,
            "source": "insights_proxy",
            "factors": top,
            "summary": summary,
            "note": "未命中分组 IC 缓存 · 暂用观察池因子分截面代理（非真日频 IC）",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/dashboard/drawdown-chart")
def dashboard_drawdown_chart(range: str = "all") -> Dict[str, Any]:
    """Dashboard 回撤时序图数据。"""
    try:
        paper = _load_raw_paper()
        eq_curve = _filter_eq_by_range(_equity_curve_from_paper(paper), range)

        dd_points = _build_dd_series(eq_curve)

        max_dd = 0.0
        if dd_points:
            max_dd = round(max(abs(p["value"]) for p in dd_points), 2)

        return {
            "ok": True,
            "points": dd_points,
            "max_drawdown": max_dd,
            "count": len(dd_points),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/dashboard/sector-heatmap")
def dashboard_sector_heatmap() -> Dict[str, Any]:
    try:
        return _build_sector_heatmap()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/dashboard/signals")
def dashboard_signals(limit: int = 20) -> Dict[str, Any]:
    try:
        return _build_signals(limit)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/dashboard/allocation")
def dashboard_allocation() -> Dict[str, Any]:
    try:
        return _build_allocation()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/dashboard/market-overview")
def dashboard_market_overview() -> Dict[str, Any]:
    """市场监控：指数 / 涨跌家数 / 成交额 / 涨停跌停。"""
    try:
        return _build_market_overview()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/dashboard/market-context")
def dashboard_market_context() -> Dict[str, Any]:
    """盘前跨市场 / 情绪 / 公告上下文。"""
    try:
        return _build_market_context_dashboard()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/dashboard/risk-metrics")
def dashboard_risk_metrics() -> Dict[str, Any]:
    """组合风险指标：Sharpe / Sortino / VaR / 波动率。"""
    try:
        return _build_risk_metrics()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/dashboard/factor-exposure")
def dashboard_factor_exposure() -> Dict[str, Any]:
    """因子暴露分析：基于持仓的板块/风格暴露。"""
    try:
        return _build_factor_exposure()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/dashboard/portfolio-health")
def dashboard_portfolio_health() -> Dict[str, Any]:
    """纸面组合健康度：暴露 · 建簿约束跳过 · α 衰减告警。"""
    try:
        from core.risk.portfolio_health import build_portfolio_health
        from core.signal.cluster_live import load_active_cluster_book

        book_doc = load_active_cluster_book() or {}
        meta = book_doc.get("meta") or {}
        paper = _load_raw_paper()
        rolling = None
        try:
            from core.signal.cluster_live_evidence import build_cluster_live_evidence

            ev = build_cluster_live_evidence(light=True) or {}
            rolling = (ev.get("rolling_ic") or ev.get("yhat_ic") or {})
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in quant_dashboard.py", exc_info=True)
            rolling = None
        return build_portfolio_health(
            paper=paper,
            book=list(book_doc.get("book") or []),
            book_constraints=meta.get("book_constraints"),
            book_skips=list(meta.get("book_skips") or []),
            rolling_ic=rolling if isinstance(rolling, dict) else None,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

