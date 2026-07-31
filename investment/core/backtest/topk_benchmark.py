"""TopK 回测相对基准（T6/T9）：指数优先，失败则池等权买持；净值曲线 + 年化超额/IR。"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple


def _period_return_from_bars(bars: List[dict]) -> Optional[float]:
    if not bars or len(bars) < 2:
        return None
    try:
        c0 = float(bars[0].get("close") or 0)
        c1 = float(bars[-1].get("close") or 0)
    except (TypeError, ValueError):
        return None
    if c0 <= 0 or c1 <= 0:
        return None
    return round((c1 / c0 - 1.0) * 100.0, 2)


def _close_by_date(bars: List[dict]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for b in bars or []:
        d = str(b.get("date") or "").strip()
        if not d:
            continue
        try:
            c = float(b.get("close") or 0)
        except (TypeError, ValueError):
            continue
        if c > 0:
            out[d] = c
    return out


def _pool_equal_hold_return(stock_bars: Dict[str, List[dict]]) -> Optional[float]:
    rets = []
    for bars in (stock_bars or {}).values():
        r = _period_return_from_bars(bars)
        if r is not None:
            rets.append(r)
    if not rets:
        return None
    return round(sum(rets) / len(rets), 2)


def _normalize_equity_curve(
    date_closes: Dict[str, float],
    dates: List[str],
) -> List[dict]:
    """将价格序列对齐到 dates，起点 100。"""
    pts: List[dict] = []
    base: Optional[float] = None
    for d in dates:
        c = date_closes.get(d)
        if c is None or c <= 0:
            continue
        if base is None:
            base = c
        pts.append({"date": d, "equity": round(100.0 * c / base, 2)})
    return pts


def _pool_ew_equity_curve(
    stock_bars: Dict[str, List[dict]],
    dates: List[str],
) -> List[dict]:
    maps = {code: _close_by_date(bars) for code, bars in (stock_bars or {}).items()}
    if not maps or not dates:
        return []
    start_date = None
    for d in dates:
        xs = [m[d] for m in maps.values() if d in m]
        if len(xs) >= 2:
            start_date = d
            break
    if not start_date:
        return []
    start_closes = {c: m[start_date] for c, m in maps.items() if start_date in m}
    pts = [{"date": start_date, "equity": 100.0}]
    for d in dates:
        if d == start_date:
            continue
        rets = []
        for code, c0 in start_closes.items():
            c1 = maps[code].get(d)
            if c1 is None or c0 <= 0:
                continue
            rets.append(c1 / c0)
        if len(rets) < 2:
            continue
        eq = 100.0 * (sum(rets) / len(rets))
        pts.append({"date": d, "equity": round(eq, 2)})
    return pts


def _align_period_excess(
    strat_curve: List[dict],
    bench_curve: List[dict],
) -> Tuple[List[float], List[dict]]:
    """在共同日期上算相邻点超额收益（百分点）。"""
    bmap = {
        str(p.get("date")): float(p.get("equity"))
        for p in (bench_curve or [])
        if p.get("date") is not None and p.get("equity") is not None
    }
    aligned_bench: List[dict] = []
    excesses: List[float] = []
    prev_s: Optional[float] = None
    prev_b: Optional[float] = None
    for p in strat_curve or []:
        d = str(p.get("date") or "")
        if d not in bmap:
            continue
        try:
            s = float(p.get("equity"))
            b = float(bmap[d])
        except (TypeError, ValueError):
            continue
        if s <= 0 or b <= 0:
            continue
        aligned_bench.append({"date": d, "equity": round(b, 2)})
        if prev_s is not None and prev_b is not None and prev_s > 0 and prev_b > 0:
            s_ret = (s / prev_s - 1.0) * 100.0
            b_ret = (b / prev_b - 1.0) * 100.0
            excesses.append(s_ret - b_ret)
        prev_s, prev_b = s, b
    return excesses, aligned_bench


def _ann_excess_pct(total_excess_pct: float, start_date: str, end_date: str) -> Optional[float]:
    try:
        from datetime import date

        d0 = date.fromisoformat(str(start_date)[:10])
        d1 = date.fromisoformat(str(end_date)[:10])
        days = max((d1 - d0).days, 1)
    except Exception:
        return None
    years = days / 365.25
    if years < 1e-6:
        return None
    try:
        factor = (1.0 + float(total_excess_pct) / 100.0) ** (1.0 / years) - 1.0
    except (OverflowError, ValueError):
        return None
    return round(factor * 100.0, 2)


def _ir_stats(excesses: List[float], *, horizon_days: int = 3) -> Dict[str, Any]:
    if len(excesses) < 2:
        return {"ir": None, "ann_ir": None, "period_count": len(excesses)}
    mean = sum(excesses) / len(excesses)
    var = sum((x - mean) ** 2 for x in excesses) / len(excesses)
    std = math.sqrt(var)
    ir = (mean / std) if std > 1e-12 else None
    h = max(1, int(horizon_days or 3))
    scale = math.sqrt(252.0 / h)
    ann_ir = round(ir * scale, 4) if ir is not None else None
    return {
        "ir": round(ir, 4) if ir is not None else None,
        "ann_ir": ann_ir,
        "period_excess_mean_pct": round(mean, 4),
        "period_excess_std_pct": round(std, 4),
        "period_count": len(excesses),
    }


def build_topk_benchmark_summary(
    result: Dict[str, Any],
    stock_bars: Dict[str, List[dict]],
    *,
    index_code: str = "000300",
    lookback: int = 120,
    force_pool: bool = False,
) -> Dict[str, Any]:
    """相对基准：优先指定指数；force_pool 或指数失败则池等权买持。附净值曲线与年化超额/IR。"""
    strat = ((result.get("metrics") or {}).get("total_return_pct"))
    params = result.get("params") or {}
    req = result.get("request") or {}
    horizon_days = int(params.get("horizon_days") or req.get("horizon_days") or 3)
    strat_curve = list(result.get("equity_curve") or [])
    strat_dates = [str(p.get("date")) for p in strat_curve if p.get("date")]

    code = str(index_code or "000300").strip()
    if code.lower() in ("pool", "pool_ew", "ew", "universe"):
        force_pool = True
        code = "000300"

    out: Dict[str, Any] = {
        "ok": False,
        "index_code": None if force_pool else code,
        "benchmark_label": None,
        "benchmark_return_pct": None,
        "strategy_return_pct": strat,
        "excess_pct": None,
        "ann_excess_pct": None,
        "ir": None,
        "ann_ir": None,
        "equity_curve": [],
        "force_pool": bool(force_pool),
        "note": "",
    }
    if strat is None:
        out["reason"] = "无策略累计收益"
        return out

    index_bars: List[dict] = []
    if not force_pool:
        try:
            from core.ports.market import fetch_index_bars

            raw = fetch_index_bars(code, limit=max(40, int(lookback) + 20))
            if isinstance(raw, tuple):
                index_bars = list(raw[0] or [])
            elif isinstance(raw, list):
                index_bars = raw
            elif isinstance(raw, dict) and raw.get("success"):
                index_bars = list(raw.get("bars") or raw.get("data") or [])
        except Exception as e:
            out["index_error"] = str(e)

    bench_ret: Optional[float] = None
    bench_curve: List[dict] = []
    label: Optional[str] = None
    note = ""

    if index_bars and not force_pool:
        bench_ret = _period_return_from_bars(index_bars)
        if strat_dates:
            bench_curve = _normalize_equity_curve(_close_by_date(index_bars), strat_dates)
        label = f"指数{code}"
        note = "超额 = 策略累计 − 指数同期买持；曲线按策略调仓日对齐指数收盘。"

    if bench_ret is None or (strat_dates and len(bench_curve) < 2) or force_pool:
        pool_bh = _pool_equal_hold_return(stock_bars)
        pool_curve = _pool_ew_equity_curve(stock_bars, strat_dates) if strat_dates else []
        if pool_bh is not None:
            bench_ret = pool_bh
            bench_curve = pool_curve
            label = "池等权买持"
            if force_pool:
                note = "强制池等权买持；超额 = 策略 − 池买持。"
            else:
                note = (
                    f"指数 {code} 不可用或无法对齐，回退为验证池等权买持；"
                    "超额 = 策略 − 池买持（非指数）。"
                )

    if bench_ret is None:
        out["reason"] = "无可用基准"
        return out

    excess = round(float(strat) - float(bench_ret), 2)
    excesses, aligned = _align_period_excess(strat_curve, bench_curve)
    ir_pack = _ir_stats(excesses, horizon_days=horizon_days)
    ann_ex = None
    if strat_dates and len(strat_dates) >= 2:
        ann_ex = _ann_excess_pct(excess, strat_dates[0], strat_dates[-1])

    warn_abs_pos_excess_neg = bool(
        float(strat) > 0 and excess is not None and float(excess) < 0
    )

    out.update(
        {
            "ok": True,
            "benchmark_label": label,
            "benchmark_return_pct": bench_ret,
            "excess_pct": excess,
            "ann_excess_pct": ann_ex,
            "ir": ir_pack.get("ir"),
            "ann_ir": ir_pack.get("ann_ir"),
            "period_excess_mean_pct": ir_pack.get("period_excess_mean_pct"),
            "period_excess_std_pct": ir_pack.get("period_excess_std_pct"),
            "period_count": ir_pack.get("period_count"),
            "equity_curve": aligned if aligned else bench_curve,
            "warn_abs_pos_excess_neg": warn_abs_pos_excess_neg,
            "note": note
            + (
                " ⚠ 绝对收益为正但超额为负：赚的是 beta/池涨，非相对 alpha。"
                if warn_abs_pos_excess_neg
                else ""
            ),
        }
    )
    return out
