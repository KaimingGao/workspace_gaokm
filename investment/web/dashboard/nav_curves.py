"""仪表盘：净值曲线 / 回撤 / VaR。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from web.dashboard.paper_helpers import (
    _fetch_index_bars_bounded,
)


def _fetch_benchmark_curve(
    benchmark_code: str,
    eq_curve: list,
) -> list:
    """抓取基准指数日线，归一到 100，返回 [{time, value}]。"""
    if not eq_curve:
        return []

    days = [str(e.get("date") or "")[:10] for e in eq_curve if e.get("date")]
    days = [d for d in days if len(d) == 10]
    if not days:
        return []

    start_date = min(days)
    end_date = max(days)
    # 同日多次快照时日历跨度为空，放宽到近 30 个交易日以便对照线可见
    if start_date == end_date:
        try:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            start_date = (end_dt - timedelta(days=45)).strftime("%Y-%m-%d")
        except ValueError:
            pass

    try:
        # 仪表盘路径必须有超时；远端/AkShare 卡住时仍返回组合净值
        bars = _fetch_index_bars_bounded(benchmark_code, limit=800, timeout_sec=5.0)
        if not bars:
            return []

        filtered = []
        for b in bars:
            if not isinstance(b, dict):
                continue
            d = str(b.get("date") or "")[:10]
            if d and start_date <= d <= end_date:
                filtered.append(b)

        if not filtered:
            return []

        first_close = float(filtered[0].get("close", 0))
        if first_close <= 0:
            return []

        return [
            {
                "time": str(b.get("date") or "")[:10],
                "value": round(float(b.get("close", 0)) / first_close * 100, 2),
            }
            for b in filtered
            if b.get("close") is not None
        ]
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in quant_dashboard.py", exc_info=True)
        return []


def _filter_eq_by_range(eq_curve: list, range: str) -> list:
    if range == "all" or not eq_curve:
        return list(eq_curve or [])
    now = datetime.now()
    if range == "30":
        cutoff = now - timedelta(days=30)
    elif range == "90":
        cutoff = now - timedelta(days=90)
    elif range == "ytd":
        cutoff = datetime(now.year, 1, 1)
    else:
        return list(eq_curve)
    cut = cutoff.strftime("%Y-%m-%d")
    return [e for e in eq_curve if e.get("date") and e["date"] >= cut]


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
            "time": e.get("time") or e.get("date"),
            "value": round(dd, 2),
        })
    return points

