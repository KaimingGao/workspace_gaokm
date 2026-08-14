"""特异动量因子（V2.1）：个股收益对指数回归残差（市场中性后的动量）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _aligned_returns(
    stock_bars: List[dict],
    index_bars: List[dict],
    window: int = 20,
) -> Tuple[List[float], List[float]]:
    idx_by_date = {}
    for b in index_bars or []:
        d = str(b.get("date") or "")
        if d:
            idx_by_date[d] = b

    pairs: List[Tuple[float, float]] = []
    for i in range(1, len(stock_bars)):
        cur = stock_bars[i]
        prev = stock_bars[i - 1]
        d = str(cur.get("date") or "")
        ib = idx_by_date.get(d)
        if not ib:
            continue
        # 找指数前一日
        # 简化：用同日 close 相对前一根匹配到的 index bar 序列位置
        try:
            sc = float(cur["close"])
            sp = float(prev["close"])
            ic = float(ib["close"])
        except (TypeError, ValueError, KeyError):
            continue
        if sp <= 0 or sc <= 0 or ic <= 0:
            continue
        # 指数前收：向前搜相邻交易日
        prev_idx = None
        for j in range(i - 1, -1, -1):
            pd = str(stock_bars[j].get("date") or "")
            cand = idx_by_date.get(pd)
            if cand is not None:
                prev_idx = cand
                break
        if prev_idx is None:
            continue
        try:
            ip = float(prev_idx["close"])
        except (TypeError, ValueError):
            continue
        if ip <= 0:
            continue
        pairs.append(((sc / sp) - 1.0, (ic / ip) - 1.0))

    if len(pairs) > window:
        pairs = pairs[-window:]
    ys = [p[0] for p in pairs]
    xs = [p[1] for p in pairs]
    return ys, xs


def _ols_residual_mean(ys: List[float], xs: List[float]) -> Optional[float]:
    n = len(ys)
    if n < 5 or len(xs) != n:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    var_x = sum((x - mx) ** 2 for x in xs)
    if var_x < 1e-16:
        # 指数几乎不动：残差≈个股收益
        return my
    beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var_x
    alpha = my - beta * mx
    residuals = [y - (alpha + beta * x) for x, y in zip(xs, ys)]
    # 近端残差均值（特异动量）
    k = min(5, len(residuals))
    return sum(residuals[-k:]) / k


def score_idio_momentum(
    bars: List[dict],
    *,
    index_bars: Optional[List[dict]] = None,
    **_kw,
) -> Tuple[float, Dict[str, Any]]:
    """无指数或样本不足 → 不进 ŷ（omit）。"""
    if not bars or not index_bars:
        return 50.0, {
            "idio_residual": None,
            "ok": False,
            "reason": "no_index",
            "omit_sub_score": True,
        }

    ys, xs = _aligned_returns(bars, index_bars)
    resid = _ols_residual_mean(ys, xs)
    if resid is None:
        return 50.0, {
            "idio_residual": None,
            "ok": False,
            "reason": "thin_sample",
            "sample_count": len(ys),
            "omit_sub_score": True,
        }

    pct = resid * 100.0
    if 0.3 <= pct <= 2.5:
        score = 68.0
    elif 0.0 <= pct < 0.3 or 2.5 < pct <= 5.0:
        score = 58.0
    elif -1.0 <= pct < 0.0:
        score = 48.0
    elif pct > 5.0:
        score = 42.0  # 过热特异涨
    else:
        score = 38.0

    return float(score), {
        "idio_residual": round(resid, 6),
        "idio_residual_pct": round(pct, 3),
        "ok": True,
        "sample_count": len(ys),
        "omit_sub_score": False,
    }
