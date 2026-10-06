"""组合收益归因（研究近似 · R4 Brinson lite）。

对一次组合回测的 legs / trades 做：
- 个股贡献（等权腿收益）
- 行业贡献（sector_map）
- 简化 Brinson：allocation / selection / interaction（相对等权行业基准）
"""


import logging
from collections import defaultdict

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Sequence


def _sector_for(code: str) -> str:
    try:
        from core.portfolio_optimize import _sector_for, load_sector_map

        return _sector_for(code, load_sector_map())
    except Exception:  # noqa: BLE001 — best-effort / 非阻塞分支降级
        logger.debug("exception caught in attribution.py line 22", exc_info=True)
        return "其他"


def _brinson_lite(
    legs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    等权组合相对「行业等权基准」的 Brinson 分解（研究近似）。

    基准：每个出现过的行业权重 1/S；组合：该行业腿数 / 总腿数。
    行业收益：该行业腿均收益；基准行业收益：全市场腿均收益（proxy，无外部行业指数）。
    """
    if not legs:
        return {"ok": False, "reason": "no_legs"}

    by_sec: Dict[str, List[float]] = defaultdict(list)
    all_rets: List[float] = []
    for leg in legs:
        try:
            ret = float(leg.get("return_pct"))
        except (TypeError, ValueError):
            continue
        code = str(leg.get("stock_code") or "").strip()
        sec = str(leg.get("sector") or _sector_for(code))
        by_sec[sec].append(ret)
        all_rets.append(ret)
    if not all_rets or not by_sec:
        return {"ok": False, "reason": "no_returns"}

    n = len(all_rets)
    s_count = len(by_sec)
    bench_w = 1.0 / s_count
    bench_r = sum(all_rets) / n  # 无外部行业指数时用全样本均收益作行业基准 proxy

    allocation = 0.0
    selection = 0.0
    interaction = 0.0
    rows: List[Dict[str, Any]] = []
    for sec, rets in by_sec.items():
        w_p = len(rets) / n
        w_b = bench_w
        r_p = sum(rets) / len(rets)
        r_b = bench_r
        a = (w_p - w_b) * r_b
        s = w_b * (r_p - r_b)
        i = (w_p - w_b) * (r_p - r_b)
        allocation += a
        selection += s
        interaction += i
        rows.append(
            {
                "sector": sec,
                "weight_pct": round(w_p * 100.0, 2),
                "bench_weight_pct": round(w_b * 100.0, 2),
                "avg_return_pct": round(r_p, 3),
                "allocation_pct": round(a, 3),
                "selection_pct": round(s, 3),
                "interaction_pct": round(i, 3),
                "n": len(rets),
            }
        )
    rows.sort(key=lambda r: -abs(float(r.get("selection_pct") or 0)))
    total = allocation + selection + interaction
    return {
        "ok": True,
        "allocation_pct": round(allocation, 3),
        "selection_pct": round(selection, 3),
        "interaction_pct": round(interaction, 3),
        "total_excess_pct": round(total, 3),
        "by_sector": rows[:12],
        "methodology": (
            "Brinson lite：组合行业权重相对等权行业基准；"
            "行业基准收益暂用全样本腿均收益（无外部行业指数）。"
        ),
    }


def attribute_portfolio_trades(
    trades: Sequence[dict],
    *,
    equal_weight_benchmark_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """
    trades 项可含 legs: [{stock_code, return_pct, ...}] 或顶层 stock_code/return_pct。
    """
    stock_sum: Dict[str, float] = defaultdict(float)
    stock_n: Dict[str, int] = defaultdict(int)
    sector_sum: Dict[str, float] = defaultdict(float)
    sector_n: Dict[str, int] = defaultdict(int)
    all_rets: List[float] = []
    flat_legs: List[Dict[str, Any]] = []

    for t in trades or []:
        legs = t.get("legs")
        if isinstance(legs, list) and legs:
            for leg in legs:
                code = str(leg.get("stock_code") or "").strip()
                try:
                    ret = float(leg.get("return_pct"))
                except (TypeError, ValueError):
                    continue
                if not code:
                    continue
                stock_sum[code] += ret
                stock_n[code] += 1
                sec = str(leg.get("sector") or _sector_for(code))
                sector_sum[sec] += ret
                sector_n[sec] += 1
                all_rets.append(ret)
                flat_legs.append(
                    {
                        "stock_code": code,
                        "return_pct": ret,
                        "sector": sec,
                        "score": leg.get("score"),
                    }
                )
        else:
            code = str(t.get("stock_code") or "").strip()
            try:
                ret = float(t.get("return_pct"))
            except (TypeError, ValueError):
                continue
            if not code:
                continue
            stock_sum[code] += ret
            stock_n[code] += 1
            sec = _sector_for(code)
            sector_sum[sec] += ret
            sector_n[sec] += 1
            all_rets.append(ret)
            flat_legs.append({"stock_code": code, "return_pct": ret, "sector": sec})

    by_stock = [
        {
            "stock_code": c,
            "avg_return_pct": round(stock_sum[c] / max(1, stock_n[c]), 3),
            "sum_return_pct": round(stock_sum[c], 3),
            "n": stock_n[c],
            "sector": _sector_for(c),
        }
        for c in sorted(stock_sum.keys(), key=lambda x: -abs(stock_sum[x]))
    ]
    by_sector = [
        {
            "sector": s,
            "avg_return_pct": round(sector_sum[s] / max(1, sector_n[s]), 3),
            "sum_return_pct": round(sector_sum[s], 3),
            "n": sector_n[s],
            "count": sector_n[s],
        }
        for s in sorted(sector_sum.keys(), key=lambda x: -abs(sector_sum[x]))
    ]

    port_avg = round(sum(all_rets) / len(all_rets), 3) if all_rets else None
    bench = equal_weight_benchmark_pct
    if bench is None:
        bench = port_avg
    selection = None
    if port_avg is not None and bench is not None:
        selection = round(port_avg - float(bench), 3)

    brinson = _brinson_lite(flat_legs)

    # 因子代理：按 score 高低半组的均收益差（有 score 时）
    factor_proxy: Dict[str, Any] = {"ok": False}
    scored = [
        (float(l["return_pct"]), float(l["score"]))
        for l in flat_legs
        if l.get("score") is not None
    ]
    try:
        scored = [(r, s) for r, s in scored if r == r and s == s]
    except Exception:  # noqa: BLE001 — best-effort / 非阻塞分支降级
        logger.debug("exception caught in attribution.py line 196", exc_info=True)
        scored = []
    if len(scored) >= 2:
        scored.sort(key=lambda x: x[1])
        mid = max(1, len(scored) // 2)
        low = scored[:mid]
        high = scored[mid:]
        if not high:
            high = scored[-1:]
            low = scored[:-1] or scored[:1]
        low_avg = sum(x[0] for x in low) / len(low)
        high_avg = sum(x[0] for x in high) / len(high)
        factor_proxy = {
            "ok": True,
            "high_score_avg_return_pct": round(high_avg, 3),
            "low_score_avg_return_pct": round(low_avg, 3),
            "score_spread_pct": round(high_avg - low_avg, 3),
            "n": len(scored),
            "note": "高/低分半组均收益差（score 代理，非完整因子暴露）。",
        }

    out: Dict[str, Any] = {
        "ok": True,
        "by_stock": by_stock[:20],
        "by_sector": by_sector,
        "portfolio_avg_leg_pct": port_avg,
        "benchmark_avg_pct": bench,
        "selection_excess_pct": selection,
        "leg_count": len(all_rets),
        "brinson": brinson,
        "factor_proxy": factor_proxy,
        "note": (
            "研究近似归因：个股/行业贡献 + Brinson lite（allocation/selection/interaction）；"
            "无数据列应隐藏。非完整因子暴露 / 非代客下单。"
        ),
        "methodology": (brinson or {}).get("methodology"),
    }
    return out
