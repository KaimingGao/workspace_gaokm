"""特异动量因子（V2.1）：个股收益对指数回归残差（市场中性后的动量）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _aligned_returns(
    stock_bars: List[dict],
    index_bars: List[dict],
    window: int = 20,
) -> Tuple[List[float], List[float]]:
    """按共同交易日对齐日收益；禁止用错位窗口估 β。"""
    stock_by_date = {}
    for b in stock_bars or []:
        d = str((b or {}).get("date") or "").strip()[:10]
        if len(d) >= 10 and (b or {}).get("close") is not None:
            stock_by_date[d] = b
    index_by_date = {}
    for b in index_bars or []:
        d = str((b or {}).get("date") or "").strip()[:10]
        if len(d) >= 10 and (b or {}).get("close") is not None:
            index_by_date[d] = b

    common = sorted(set(stock_by_date) & set(index_by_date))
    # 需要 window 段日收益 → window+1 个收盘点
    need = int(window) + 1
    if len(common) < need:
        if len(common) < 6:
            return [], []
        dates = common
    else:
        dates = common[-need:]

    pairs: List[Tuple[float, float]] = []
    for i in range(1, len(dates)):
        d0, d1 = dates[i - 1], dates[i]
        try:
            sc = float(stock_by_date[d1]["close"])
            sp = float(stock_by_date[d0]["close"])
            ic = float(index_by_date[d1]["close"])
            ip = float(index_by_date[d0]["close"])
        except (TypeError, ValueError, KeyError):
            continue
        if sp <= 0 or sc <= 0 or ip <= 0 or ic <= 0:
            continue
        pairs.append(((sc / sp) - 1.0, (ic / ip) - 1.0))

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
