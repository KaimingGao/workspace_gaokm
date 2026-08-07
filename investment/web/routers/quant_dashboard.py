"""仪表盘 API：全局 KPI · 市场监控 · 净值曲线 · 板块热力 · 信号告警 · 资产配置 · 回撤 · VaR · 因子 IC。

GET /api/dashboard/kpis           — 核心指标（Sharpe / 回撤 / 胜率 …）
GET /api/dashboard/market-overview — 市场监控（指数 / 涨跌家数 / 成交额 / 涨停跌停）
GET /api/dashboard/nav-curve      — 累计净值曲线（含基准对比与相关性）
GET /api/dashboard/sector-heatmap — 板块/行业涨跌热力（含成交量/市值/个股数）
GET /api/dashboard/signals        — 最新信号 / 告警
GET /api/dashboard/allocation     — 资产配置（按板块汇总市值）
GET /api/dashboard/risk-metrics   — 组合风险指标（Sharpe / Sortino / VaR / 波动率）
GET /api/dashboard/factor-exposure — 因子暴露分析
GET /api/dashboard/drawdown       — 回撤曲线（含最大回撤与当前回撤）
GET /api/dashboard/var-historical  — 历史模拟 VaR / CVaR / 收益直方图
GET /api/dashboard/factor-ic-series — 因子 IC 时序（研究台）
GET /api/dashboard/drawdown-chart — Dashboard 回撤时序图数据
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from web import deps

router = APIRouter(tags=["dashboard"])


def _build_kpis() -> Dict[str, Any]:
    """聚合核心 KPI：从 north_star 报告 + paper 状态提取。"""
    paper = deps.paper.status(lite=True)
    ns_report = None
    try:
        from core.north_star import build_north_star_report
        ns_report = build_north_star_report(paper)
    except Exception:
        pass

    pr = (ns_report or {}).get("paper_risk") or {}
    risk_strategy = (ns_report or {}).get("paper_risk_strategy") or {}

    today_ret = None
    total_ret = None
    sharpe = None
    max_dd = None
    win_rate = None
    trade_count = 0

    # Today / Total return from equity
    eq_curve = paper.get("equity_curve") or []
    if eq_curve and len(eq_curve) >= 2:
        today_ret = eq_curve[-1].get("return_pct")
        if today_ret is None and eq_curve[-1].get("equity") and eq_curve[-2].get("equity"):
            e0 = eq_curve[-2]["equity"]
            e1 = eq_curve[-1]["equity"]
            if e0 and e0 > 0:
                today_ret = (e1 / e0 - 1) * 100
    if eq_curve and len(eq_curve) >= 1:
        first_eq = eq_curve[0].get("equity")
        last_eq = eq_curve[-1].get("equity")
        if first_eq and first_eq > 0 and last_eq:
            total_ret = (last_eq / first_eq - 1) * 100

    # Sharpe / MaxDD from north_star
    sharpe = pr.get("rolling_sharpe") or pr.get("sharpe") or risk_strategy.get("rolling_sharpe")
    max_dd = pr.get("max_drawdown_pct") or pr.get("max_drawdown") or risk_strategy.get("max_drawdown_pct")

    # Win rate from trades
    trades = paper.get("trades") or []
    closed = [t for t in trades if t.get("side") == "sell" and t.get("pnl") is not None]
    trade_count = len(closed)
    if closed:
        wins = sum(1 for t in closed if (t.get("pnl") or 0) > 0)
        win_rate = (wins / len(closed)) * 100

    # Sparkline data (simple: last 30 days of equity-derived values)
    spark_today: List[float] = []
    spark_total: List[float] = []
    spark_sharpe: List[float] = []
    spark_dd: List[float] = []
    spark_wr: List[float] = []

    if eq_curve:
        rets = []
        for i in range(1, len(eq_curve)):
            e0 = eq_curve[i - 1].get("equity")
            e1 = eq_curve[i].get("equity")
            if e0 and e0 > 0 and e1:
                rets.append((e1 / e0 - 1) * 100)
        # Last 30 daily returns
        spark_today = rets[-30:] if rets else []
        # Cumulative equity normalized
        if eq_curve and eq_curve[0].get("equity"):
            base = eq_curve[0]["equity"]
            spark_total = [((e.get("equity", base) / base) - 1) * 100 for e in eq_curve[-30:]]
        # Rolling Sharpe (simplified)
        if len(rets) >= 7:
            for i in range(6, len(rets)):
                window = rets[max(0, i - 6): i + 1]
                if window:
                    mean = sum(window) / len(window)
                    std = (sum((x - mean) ** 2 for x in window) / len(window)) ** 0.5
                    if std > 0:
                        spark_sharpe.append(mean / std * (252 ** 0.5))
        # Drawdown series
        if eq_curve:
            peak = eq_curve[0].get("equity", 1.0)
            for e in eq_curve[-30:]:
                v = e.get("equity", peak)
                if v > peak:
                    peak = v
                if peak > 0:
                    spark_dd.append((v / peak - 1) * 100)
        # Win rate rolling
        if len(closed) >= 5:
            window = closed[-20:]
            wins = sum(1 for t in window if (t.get("pnl") or 0) > 0)
            spark_wr = [wins / len(window) * 100] * 5 if window else []

    # Strategy leaderboard
    strategies: List[Dict[str, Any]] = []
    try:
        strat_list = deps.quant.list_strategies()
        if isinstance(strat_list, dict):
            strat_list = strat_list.get("strategies") or strat_list.get("list") or []
        if isinstance(strat_list, list):
            for s in strat_list[:10]:
                if isinstance(s, dict):
                    label = s.get("label") or s.get("name") or s.get("strategy_id") or "未知策略"
                    desc = s.get("description") or ""
                    strategies.append({
                        "name": f"{label}" + (f" · {desc[:24]}" if desc else ""),
                        "return_pct": s.get("total_return_pct") or s.get("return_pct") or 0,
                        "sharpe": s.get("sharpe") or s.get("rolling_sharpe") or 0,
                    })
    except Exception:
        pass

    return {
        "ok": True,
        "today_return": round(today_ret, 2) if today_ret is not None else None,
        "total_return": round(total_ret, 2) if total_ret is not None else None,
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "max_drawdown": round(max_dd, 2) if max_dd is not None else None,
        "win_rate": round(win_rate, 2) if win_rate is not None else None,
        "trade_count": trade_count,
        "period": "历史累计",
        "sparkline_today": spark_today,
        "sparkline_total": spark_total,
        "sparkline_sharpe": spark_sharpe[-30:],
        "sparkline_dd": spark_dd[-30:],
        "sparkline_winrate": spark_wr,
        "strategies": strategies,
        "computed_at": datetime.now().isoformat(timespec="seconds"),
    }


@router.get("/api/dashboard/kpis")
def dashboard_kpis():
    """核心 KPI 卡片数据。"""
    try:
        return _build_kpis()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ——— Benchmark Helpers ———


def _fetch_benchmark_curve(
    benchmark_code: str,
    eq_curve: list,
) -> list:
    """抓取基准指数日线，归一到 100，返回 [{time, value}]。"""
    try:
        from core.ports.market import fetch_index_bars
    except Exception:
        return []

    if not eq_curve:
        return []

    dates = [e.get("date") for e in eq_curve if e.get("date")]
    if not dates:
        return []

    start_date = min(dates)
    end_date = max(dates)

    try:
        bars = fetch_index_bars(benchmark_code, limit=800)
        if not bars:
            return []

        filtered = []
        for b in bars:
            d = b.get("date", "")
            if d and start_date <= d <= end_date:
                filtered.append(b)

        if not filtered:
            return []

        first_close = float(filtered[0].get("close", 0))
        if first_close <= 0:
            return []

        return [
            {
                "time": b.get("date"),
                "value": round(float(b.get("close", 0)) / first_close * 100, 2),
            }
            for b in filtered
        ]
    except Exception:
        return []


def _calc_series_correlation(nav_points: list, bench_points: list) -> Optional[float]:
    """计算 NAV 与 benchmark 日收益率的 Pearson 相关系数。"""
    if len(nav_points) < 5 or len(bench_points) < 5:
        return None

    nav_by_date = {p["time"]: p["value"] for p in nav_points}
    bench_by_date = {p["time"]: p["value"] for p in bench_points}

    common_dates = sorted(set(nav_by_date.keys()) & set(bench_by_date.keys()))
    if len(common_dates) < 5:
        return None

    nav_rets: List[float] = []
    bench_rets: List[float] = []
    for i in range(1, len(common_dates)):
        d0, d1 = common_dates[i - 1], common_dates[i]
        nv0, nv1 = nav_by_date[d0], nav_by_date[d1]
        bv0, bv1 = bench_by_date[d0], bench_by_date[d1]
        if nv0 and nv0 > 0 and bv0 and bv0 > 0:
            nav_rets.append(nv1 / nv0 - 1)
            bench_rets.append(bv1 / bv0 - 1)

    if len(nav_rets) < 5:
        return None

    n = len(nav_rets)
    mean_nav = sum(nav_rets) / n
    mean_bench = sum(bench_rets) / n

    cov = sum((nav_rets[i] - mean_nav) * (bench_rets[i] - mean_bench) for i in range(n)) / n
    std_nav = (sum((r - mean_nav) ** 2 for r in nav_rets) / n) ** 0.5
    std_bench = (sum((r - mean_bench) ** 2 for r in bench_rets) / n) ** 0.5

    if std_nav > 1e-12 and std_bench > 1e-12:
        return round(cov / (std_nav * std_bench), 4)
    return None


def _build_dd_series(eq_curve: list) -> list:
    """从权益曲线计算回撤序列。返回 [{time, value}]，value 为百分比。"""
    if not eq_curve:
        return []
    points: List[Dict[str, Any]] = []
    peak = None
    for e in eq_curve:
        eq = e.get("equity")
        if eq is None:
            continue
        if peak is None or eq > peak:
            peak = eq
        if peak and peak > 0:
            dd = (eq / peak - 1) * 100
        else:
            dd = 0.0
        points.append({
            "time": e.get("date"),
            "value": round(dd, 2),
        })
    return points


@router.get("/api/dashboard/nav-curve")
def dashboard_nav_curve(range: str = "all", benchmark: str = "hs300"):
    """累计净值曲线（归一到 100）。range: 30 | 90 | ytd | all。含基准对比与相关性。"""
    try:
        paper = deps.paper.status(lite=True)
        eq_curve = paper.get("equity_curve") or []
        if range != "all":
            now = datetime.now()
            if range == "30":
                cutoff = now - timedelta(days=30)
            elif range == "90":
                cutoff = now - timedelta(days=90)
            elif range == "ytd":
                cutoff = datetime(now.year, 1, 1)
            else:
                cutoff = None
            if cutoff:
                eq_curve = [
                    e for e in eq_curve
                    if e.get("date") and e["date"] >= cutoff.strftime("%Y-%m-%d")
                ]

        raw_points = [
            {"time": e.get("date"), "value": e.get("equity")}
            for e in eq_curve
            if e.get("equity") is not None
        ]

        if not raw_points:
            return {"ok": True, "points": [], "benchmark": [], "correlation": None, "count": 0}

        base = raw_points[0]["value"]
        if not base or base <= 0:
            return {"ok": True, "points": [], "benchmark": [], "correlation": None, "count": 0}

        points = [
            {"time": p["time"], "value": round(p["value"] / base * 100, 2)}
            for p in raw_points
        ]

        benchmark_enabled = benchmark != "none"
        bench_points = []
        bench_code = None
        correlation = None
        if benchmark_enabled:
            bench_code = "sh000300" if benchmark == "hs300" else benchmark
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
def dashboard_drawdown(range: str = "all"):
    """回撤曲线。range: 30 | 90 | ytd | all。"""
    try:
        paper = deps.paper.status(lite=True)
        eq_curve = paper.get("equity_curve") or []
        if range != "all":
            now = datetime.now()
            if range == "30":
                cutoff = now - timedelta(days=30)
            elif range == "90":
                cutoff = now - timedelta(days=90)
            elif range == "ytd":
                cutoff = datetime(now.year, 1, 1)
            else:
                cutoff = None
            if cutoff:
                eq_curve = [
                    e for e in eq_curve
                    if e.get("date") and e["date"] >= cutoff.strftime("%Y-%m-%d")
                ]

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
def dashboard_var_historical(range: str = "all"):
    """历史模拟 VaR / CVaR 与收益直方图。"""
    try:
        paper = deps.paper.status(lite=True)
        eq_curve = paper.get("equity_curve") or []
        if range != "all":
            now = datetime.now()
            if range == "30":
                cutoff = now - timedelta(days=30)
            elif range == "90":
                cutoff = now - timedelta(days=90)
            elif range == "ytd":
                cutoff = datetime(now.year, 1, 1)
            else:
                cutoff = None
            if cutoff:
                eq_curve = [
                    e for e in eq_curve
                    if e.get("date") and e["date"] >= cutoff.strftime("%Y-%m-%d")
                ]

        equities = [e.get("equity") for e in eq_curve if e.get("equity") is not None]
        if len(equities) < 5:
            return {"ok": False, "message": "净值数据不足（至少5期）"}

        rets: List[float] = []
        for i in range(1, len(equities)):
            e0, e1 = equities[i - 1], equities[i]
            if e0 and e0 > 0 and e1 is not None:
                rets.append(e1 / e0 - 1)

        if len(rets) < 5:
            return {"ok": False, "message": "收益率数据不足"}

        import math as _math

        n = len(rets)
        sorted_rets = sorted(rets)

        var_95 = -sorted_rets[int(n * 0.05)] * 100 if n > 10 else None
        var_99 = -sorted_rets[int(n * 0.01)] * 100 if n > 10 else None

        var_95_threshold = sorted_rets[int(n * 0.05)] if n > 10 else None
        var_99_threshold = sorted_rets[int(n * 0.01)] if n > 10 else None

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
        bucket_range = max_ret - min_ret if max_ret > min_ret else 0.01
        bucket_size = bucket_range / num_buckets

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
):
    """因子 IC 时序（研究台）。基于观察池截面因子分与前瞻收益。"""
    try:
        from core.backtest.factor_cs_ic import compute_factor_cross_section_ic
        from core.watching_insights import load_insights_cache

        insights = load_insights_cache()
        if not insights:
            return {"ok": True, "factors": [], "note": "无观察池数据，请先运行「跑分组」生成因子分数"}

        items = insights if isinstance(insights, list) else list(insights.values()) if isinstance(insights, dict) else []

        if not items:
            return {"ok": True, "factors": [], "note": "观察池为空"}

        factor_scores: Dict[str, List[float]] = {}
        for item in items:
            sub_scores = item.get("sub_scores") or {}
            for factor, score in sub_scores.items():
                try:
                    s = float(score)
                except (TypeError, ValueError):
                    continue
                if factor not in factor_scores:
                    factor_scores[factor] = []
                factor_scores[factor].append(s)

        if not factor_scores:
            return {"ok": True, "factors": [], "note": "无因子分数数据"}

        import math as _math
        from statistics import mean, stdev

        factor_series: List[Dict[str, Any]] = []
        for factor, scores in factor_scores.items():
            if len(scores) < 3:
                continue
            try:
                ic_mean = mean(scores)
                ic_std = stdev(scores) if len(scores) > 1 else 0.0
            except Exception:
                continue

            ic_ir = (ic_mean / ic_std) if ic_std > 1e-12 else 0.0
            ann_factor = (_math.sqrt(252.0 / max(horizon_days, 1))) if horizon_days > 0 else 1.0
            ic_ir_annual = ic_ir * ann_factor

            pos_count = sum(1 for s in scores if s > 0)
            pos_ratio = pos_count / len(scores) * 100

            cumulative = []
            running = 0.0
            for s in scores:
                running += s
                cumulative.append(round(running, 4))

            factor_series.append({
                "factor": factor,
                "ic_values": [round(s, 4) for s in scores],
                "cumulative_ic": cumulative,
                "ic_mean": round(ic_mean, 4),
                "ic_std": round(ic_std, 4),
                "ic_ir": round(ic_ir, 4),
                "ic_ir_annual": round(ic_ir_annual, 4),
                "positive_ratio": round(pos_ratio, 1),
                "sample_count": len(scores),
            })

        factor_series.sort(key=lambda x: abs(x["ic_ir_annual"]), reverse=True)

        return {
            "ok": True,
            "factors": factor_series,
            "total_items": len(items),
            "note": f"基于 {len(items)} 只观察池票的截面因子分数时序",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/dashboard/drawdown-chart")
def dashboard_drawdown_chart(range: str = "all"):
    """Dashboard 回撤时序图数据。"""
    try:
        paper = deps.paper.status(lite=True)
        eq_curve = paper.get("equity_curve") or []
        if range != "all":
            now = datetime.now()
            if range == "30":
                cutoff = now - timedelta(days=30)
            elif range == "90":
                cutoff = now - timedelta(days=90)
            elif range == "ytd":
                cutoff = datetime(now.year, 1, 1)
            else:
                cutoff = None
            if cutoff:
                eq_curve = [
                    e for e in eq_curve
                    if e.get("date") and e["date"] >= cutoff.strftime("%Y-%m-%d")
                ]

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
def dashboard_sector_heatmap():
    """板块热力图。优先用持仓聚合，退化到行业默认列表。含市值/涨跌/个股数。"""
    try:
        paper = deps.paper.status(lite=True)
        holdings = paper.get("holdings") or []
        sectors_map: Dict[str, Dict[str, Any]] = {}

        for h in holdings:
            sector = h.get("sector") or h.get("industry") or "其他"
            value = h.get("market_value") or h.get("equity") or 0
            cost = h.get("cost_value") or value
            change_pct = ((value / cost - 1) * 100) if cost and cost > 0 else 0
            volume = h.get("volume") or h.get("turnover") or 0
            if sector not in sectors_map:
                sectors_map[sector] = {"name": sector, "value": 0, "cost": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0}
            sectors_map[sector]["value"] += value
            sectors_map[sector]["cost"] += cost
            sectors_map[sector]["count"] += 1
            sectors_map[sector]["volume"] += volume
            if change_pct > 0:
                sectors_map[sector]["up_count"] += 1
            elif change_pct < 0:
                sectors_map[sector]["down_count"] += 1

        sectors = []
        for name, data in sectors_map.items():
            change = ((data["value"] / data["cost"] - 1) * 100) if data["cost"] > 0 else 0
            total_count = data["count"]
            up_ratio = (data["up_count"] / total_count * 100) if total_count > 0 else 0
            sectors.append({
                "name": name,
                "change_pct": round(change, 2),
                "value": round(data["value"], 2),
                "count": data["count"],
                "volume": round(data["volume"], 2),
                "up_count": data["up_count"],
                "down_count": data["down_count"],
                "up_ratio": round(up_ratio, 1),
            })

        if not sectors:
            sectors = [
                {"name": "电子", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "电力设备", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "医疗生物", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "计算机", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "通信", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "金融", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "消费", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "化工", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "机械", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "新能源", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "军工", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "其他", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
            ]

        sectors.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
        return {"ok": True, "sectors": sectors}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/dashboard/signals")
def dashboard_signals(limit: int = 20):
    """最新信号 / 告警。"""
    try:
        paper = deps.paper.status(lite=True)
        signals: List[Dict[str, Any]] = []

        # From operation_log (risk blocks + signals)
        op_log = paper.get("operation_log") or []
        for entry in reversed(op_log[-limit * 2:]):
            if entry.get("type") in ("risk_block", "signal", "alert"):
                signals.append({
                    "code": entry.get("code") or entry.get("stock_code"),
                    "direction": entry.get("direction") or entry.get("side") or "—",
                    "score": entry.get("score") or entry.get("predicted_score"),
                    "time": entry.get("ts") or entry.get("time"),
                    "type": entry.get("type"),
                })

        # Fallback: generate from holdings sentiment
        if not signals:
            holdings = paper.get("holdings") or []
            for h in holdings[:limit]:
                sentiment = h.get("sentiment") or {}
                label = sentiment.get("label") or ""
                score = h.get("predicted_score") or h.get("score")
                if label or score:
                    direction = "bearish" if label == "bearish" else "bullish" if label == "bullish" else "—"
                    signals.append({
                        "code": h.get("code") or h.get("stock_code"),
                        "direction": direction,
                        "score": score,
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "type": "sentiment",
                    })

        signals = signals[:limit]
        return {"ok": True, "signals": signals}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/dashboard/allocation")
def dashboard_allocation():
    """资产配置（按板块汇总市值）。"""
    try:
        paper = deps.paper.status(lite=True)
        holdings = paper.get("holdings") or []
        sectors_map: Dict[str, float] = {}
        total_value = 0.0

        for h in holdings:
            sector = h.get("sector") or h.get("industry") or "其他"
            value = h.get("market_value") or h.get("equity") or 0
            sectors_map[sector] = sectors_map.get(sector, 0) + value
            total_value += value

        sectors = [
            {"name": name, "value": round(val, 2)}
            for name, val in sorted(sectors_map.items(), key=lambda x: -x[1])
        ]

        # Add cash as a "sector" if available
        cash = paper.get("cash") or paper.get("available_cash") or 0
        if cash > 0:
            sectors.append({"name": "现金", "value": round(cash, 2)})
            total_value += cash

        if not sectors:
            sectors = [{"name": "暂无持仓", "value": 0}]

        return {
            "ok": True,
            "sectors": sectors,
            "total_value": round(total_value, 2),
            "holdings_count": len(holdings),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ——— Market Overview ———


def _build_market_overview() -> Dict[str, Any]:
    """市场监控：指数 / 涨跌家数 / 成交额 / 涨停跌停。"""
    try:
        from core.ports.market import fetch_index_bars
    except Exception:
        fetch_index_bars = None

    indices: List[Dict[str, Any]] = []
    index_codes = [
        ("sh000001", "上证指数"),
        ("sz399001", "深证成指"),
        ("sz399006", "创业板指"),
    ]

    for code, name in index_codes:
        try:
            bars = fetch_index_bars(code, limit=2) if fetch_index_bars else None
            if bars and len(bars) >= 2:
                cur = bars[-1]
                prev = bars[-2]
                close = float(cur.get("close", 0))
                prev_close = float(prev.get("close", 0))
                change_pct = ((close / prev_close - 1) * 100) if prev_close > 0 else 0
                indices.append({
                    "name": name,
                    "code": code,
                    "close": round(close, 2),
                    "change_pct": round(change_pct, 2),
                    "volume": float(cur.get("volume", 0)),
                })
            else:
                indices.append({
                    "name": name,
                    "code": code,
                    "close": None,
                    "change_pct": None,
                    "volume": 0,
                })
        except Exception:
            indices.append({
                "name": name,
                "code": code,
                "close": None,
                "change_pct": None,
                "volume": 0,
            })

    # Market breadth from watchlist holdings
    paper = deps.paper.status(lite=True)
    holdings = paper.get("holdings") or []
    up_count = 0
    down_count = 0
    flat_count = 0
    total_turnover = 0.0

    for h in holdings:
        change = h.get("change_pct") or 0
        if change > 0:
            up_count += 1
        elif change < 0:
            down_count += 1
        else:
            flat_count += 1
        mv = h.get("market_value") or 0
        total_turnover += mv

    # Limit up/down from sentiment extremes
    limit_up = sum(1 for h in holdings if (h.get("change_pct") or 0) >= 9.5)
    limit_down = sum(1 for h in holdings if (h.get("change_pct") or 0) <= -9.5)

    return {
        "ok": True,
        "indices": indices,
        "breadth": {
            "up_count": up_count,
            "down_count": down_count,
            "flat_count": flat_count,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "total_holdings": len(holdings),
        },
        "turnover": {
            "total_value": round(total_turnover, 2),
            "holding_count": len(holdings),
        },
        "computed_at": datetime.now().isoformat(timespec="seconds"),
    }


@router.get("/api/dashboard/market-overview")
def dashboard_market_overview():
    """市场监控：指数 / 涨跌家数 / 成交额 / 涨停跌停。"""
    try:
        return _build_market_overview()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ——— Risk Metrics ———


def _build_risk_metrics() -> Dict[str, Any]:
    """组合风险指标：Sharpe / Sortino / VaR / 波动率 / 最大回撤。"""
    paper = deps.paper.status(lite=True)
    eq_curve = paper.get("equity_curve") or []

    if not eq_curve or len(eq_curve) < 5:
        return {
            "ok": False,
            "status": "insufficient_data",
            "message": "净值曲线数据不足（至少5期）",
        }

    equities = [e.get("equity", 0) for e in eq_curve if e.get("equity")]
    if len(equities) < 5:
        return {
            "ok": False,
            "status": "insufficient_data",
            "message": "有效净值数据不足",
        }

    # Daily returns
    rets: List[float] = []
    for i in range(1, len(equities)):
        e0, e1 = equities[i - 1], equities[i]
        if e0 and e0 > 0:
            rets.append((e1 / e0 - 1))

    if len(rets) < 5:
        return {"ok": False, "status": "insufficient_data", "message": "收益率数据不足"}

    import math
    import statistics

    n = len(rets)
    mean_ret = statistics.mean(rets)
    std_ret = statistics.stdev(rets) if n > 1 else 0.0
    ann_factor = 252.0

    # Sharpe
    sharpe = (mean_ret / std_ret * math.sqrt(ann_factor)) if std_ret > 1e-12 else None

    # Sortino (downside deviation)
    downside_rets = [r for r in rets if r < 0]
    downside_std = statistics.stdev(downside_rets) if len(downside_rets) > 1 else 0.0
    sortino = (mean_ret / downside_std * math.sqrt(ann_factor)) if downside_std > 1e-12 else None

    # Annualized volatility
    vol = std_ret * math.sqrt(ann_factor) * 100

    # Annualized return
    total_ret = equities[-1] / equities[0] - 1
    years = n / ann_factor
    ann_ret = ((1 + total_ret) ** (1 / years) - 1) * 100 if years > 0 else None

    # Max drawdown
    peak = equities[0]
    max_dd = 0.0
    max_dd_end = peak
    for e in equities:
        if e > peak:
            peak = e
        dd = (peak - e) / peak
        if dd > max_dd:
            max_dd = dd
            max_dd_end = e
    max_dd_pct = max_dd * 100

    # VaR (95%, historical)
    var_95 = -sorted(rets)[int(n * 0.05)] * 100 if n > 10 else None

    # CVaR (95%)
    if n > 10:
        var_threshold = sorted(rets)[int(n * 0.05)]
        tail_rets = [r for r in rets if r <= var_threshold]
        cvar_95 = -statistics.mean(tail_rets) * 100 if tail_rets else None
    else:
        cvar_95 = None

    # Calmar ratio
    calmar = (ann_ret / max_dd_pct) if max_dd_pct > 0 and ann_ret is not None else None

    # Win rate
    win_days = sum(1 for r in rets if r > 0)
    win_rate = win_days / n * 100

    return {
        "ok": True,
        "sample_count": n,
        "ann_return": round(ann_ret, 2) if ann_ret is not None else None,
        "ann_volatility": round(vol, 2),
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "sortino": round(sortino, 2) if sortino is not None else None,
        "max_drawdown": round(max_dd_pct, 2),
        "var_95": round(var_95, 2) if var_95 is not None else None,
        "cvar_95": round(cvar_95, 2) if cvar_95 is not None else None,
        "calmar": round(calmar, 2) if calmar is not None else None,
        "win_rate": round(win_rate, 2),
        "trading_days": n,
    }


@router.get("/api/dashboard/risk-metrics")
def dashboard_risk_metrics():
    """组合风险指标：Sharpe / Sortino / VaR / 波动率。"""
    try:
        return _build_risk_metrics()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ——— Factor Exposure ———


def _build_factor_exposure() -> Dict[str, Any]:
    """因子暴露分析：基于持仓的板块/风格暴露。"""
    paper = deps.paper.status(lite=True)
    holdings = paper.get("holdings") or []

    if not holdings:
        return {"ok": False, "message": "暂无持仓数据"}

    # Sector exposure
    sector_values: Dict[str, float] = {}
    style_values: Dict[str, float] = {}
    total_value = 0.0

    for h in holdings:
        mv = h.get("market_value") or 0
        total_value += mv

        sector = h.get("sector") or h.get("industry") or "其他"
        sector_values[sector] = sector_values.get(sector, 0) + mv

        # Style classification based on simple heuristics
        code = h.get("code") or h.get("stock_code") or ""
        mv_val = mv
        if mv_val > 0:
            ratio = mv_val / max(total_value, 1)
            if ratio > 0.3:
                style_key = "集中度"
            elif ratio > 0.15:
                style_key = "中盘"
            else:
                style_key = "分散"
            style_values[style_key] = style_values.get(style_key, 0) + mv

    # Sector concentration (Herfindahl index)
    hhi = sum((v / max(total_value, 1)) ** 2 for v in sector_values.values())

    sectors = [
        {"name": name, "value": round(val, 2), "pct": round(val / max(total_value, 1) * 100, 2)}
        for name, val in sorted(sector_values.items(), key=lambda x: -x[1])
    ]

    return {
        "ok": True,
        "sectors": sectors,
        "total_value": round(total_value, 2),
        "sector_count": len(sectors),
        "hhi": round(hhi, 4),
        "concentration": "high" if hhi > 0.25 else "medium" if hhi > 0.15 else "low",
        "holdings_count": len(holdings),
    }


@router.get("/api/dashboard/factor-exposure")
def dashboard_factor_exposure():
    """因子暴露分析：基于持仓的板块/风格暴露。"""
    try:
        return _build_factor_exposure()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
