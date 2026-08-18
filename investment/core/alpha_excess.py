"""α / β 分账：相对基准超额（P0）。

绝对收益含市场腿；超额 = 策略收益 − 基准收益。
本模块只算诊断字段，不改 ŷ / 调仓。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return x


def period_returns_from_equity(
    points: Sequence[Tuple[str, float]],
) -> List[Tuple[str, float]]:
    """相邻净值 → 日收益 %；points 为 (date, equity)。"""
    out: List[Tuple[str, float]] = []
    prev: Optional[float] = None
    for d, eq in points:
        e = _f(eq)
        if e is None or e <= 0:
            continue
        if prev is not None and prev > 0:
            out.append((str(d)[:10], (e / prev - 1.0) * 100.0))
        prev = e
    return out


def align_excess_returns(
    strat_rets: Sequence[Tuple[str, float]],
    bench_rets: Sequence[Tuple[str, float]],
) -> List[float]:
    bmap = {d: r for d, r in bench_rets}
    xs: List[float] = []
    for d, r in strat_rets:
        if d not in bmap:
            continue
        xs.append(float(r) - float(bmap[d]))
    return xs


def ir_from_excesses(
    excesses: Sequence[float],
    *,
    ann_factor: float = 252.0,
) -> Dict[str, Any]:
    n = len(excesses)
    if n < 2:
        return {
            "ok": False,
            "period_count": n,
            "excess_mean_pct": None,
            "excess_std_pct": None,
            "ir": None,
            "ann_ir": None,
            "total_excess_approx_pct": None,
        }
    mean = sum(excesses) / n
    var = sum((x - mean) ** 2 for x in excesses) / n
    std = math.sqrt(var)
    ir = (mean / std) if std > 1e-12 else None
    scale = math.sqrt(max(1.0, float(ann_factor)))
    ann_ir = round(ir * scale, 4) if ir is not None else None
    # 复利近似：Π(1+e_i/100)-1
    nav = 1.0
    for e in excesses:
        nav *= 1.0 + float(e) / 100.0
    total = (nav - 1.0) * 100.0
    return {
        "ok": True,
        "period_count": n,
        "excess_mean_pct": round(mean, 4),
        "excess_std_pct": round(std, 4),
        "ir": round(ir, 4) if ir is not None else None,
        "ann_ir": ann_ir,
        "total_excess_approx_pct": round(total, 2),
    }


def fetch_index_equity_curve(
    *,
    index_code: str = "sh000300",
    limit: int = 120,
) -> Dict[str, Any]:
    """拉指数日线并归一到起点 100 的 (date, equity) 列表。"""
    code = str(index_code or "sh000300").strip() or "sh000300"
    bars: List[dict] = []
    try:
        from core.data_service import get_index_bars

        raw = get_index_bars(code, limit=max(40, int(limit)))
        if isinstance(raw, dict):
            bars = list(raw.get("bars") or [])
        elif isinstance(raw, tuple):
            bars = list(raw[0] or [])
        elif isinstance(raw, list):
            bars = list(raw)
    except Exception as exc:
        return {"ok": False, "index_code": code, "reason": str(exc), "points": []}

    pts: List[Tuple[str, float]] = []
    base: Optional[float] = None
    for b in bars:
        d = str(b.get("date") or "")[:10]
        c = _f(b.get("close"))
        if not d or c is None or c <= 0:
            continue
        if base is None:
            base = c
        pts.append((d, 100.0 * c / base))
    if len(pts) < 3:
        return {
            "ok": False,
            "index_code": code,
            "reason": "index_bars_too_short",
            "points": pts,
        }
    return {"ok": True, "index_code": code, "points": pts, "bar_count": len(pts)}


def compute_benchmark_excess_pack(
    strat_equity_points: Sequence[Tuple[str, float]],
    *,
    index_code: str = "sh000300",
    lookback: int = 120,
) -> Dict[str, Any]:
    """策略净值点 vs 指数 → 超额 / IR 包（α 腿诊断）。"""
    out: Dict[str, Any] = {
        "ok": False,
        "role": "benchmark_excess",
        "index_code": str(index_code or "sh000300"),
        "note": (
            "超额 = 策略日收益 − 基准日收益；"
            "多头组合的绝对收益仍含 β，本包只读 α 腿近似。"
        ),
    }
    if len(strat_equity_points or []) < 5:
        out["reason"] = "strat_equity_too_short"
        return out

    idx = fetch_index_equity_curve(index_code=index_code, limit=lookback)
    if not idx.get("ok"):
        out["reason"] = idx.get("reason") or "index_unavailable"
        out["index"] = idx
        return out

    s_rets = period_returns_from_equity(strat_equity_points)
    b_rets = period_returns_from_equity(list(idx.get("points") or []))
    excesses = align_excess_returns(s_rets, b_rets)
    stats = ir_from_excesses(excesses)
    out.update(stats)
    out["ok"] = bool(stats.get("ok"))
    out["index_code"] = idx.get("index_code")
    out["aligned_days"] = len(excesses)
    if not out["ok"]:
        out["reason"] = "insufficient_aligned_excess"
    return out


def legs_summary(
    *,
    total_return_pct: Optional[float],
    excess_pack: Optional[dict],
) -> Dict[str, Any]:
    """把绝对收益拆成可读的 β≈total−excess、α≈excess（示意分账）。"""
    total = _f(total_return_pct)
    ex = None
    if isinstance(excess_pack, dict) and excess_pack.get("ok"):
        ex = _f(excess_pack.get("total_excess_approx_pct"))
    beta_leg = None
    if total is not None and ex is not None:
        beta_leg = round(total - ex, 2)
    return {
        "total_return_pct": round(total, 2) if total is not None else None,
        "alpha_leg_approx_pct": round(ex, 2) if ex is not None else None,
        "beta_leg_approx_pct": beta_leg,
        "ir": (excess_pack or {}).get("ann_ir")
        if isinstance(excess_pack, dict)
        else None,
        "note": "alpha_leg≈累计超额近似；beta_leg≈total−excess（非严格 CAPM β）。",
    }
