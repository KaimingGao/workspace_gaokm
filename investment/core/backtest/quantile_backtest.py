"""分层（分位数）回测（T5.2）：检验 score 全池单调性，相对 Top-K 截断更细。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _bars_by_date(bars: List[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for b in bars or []:
        d = str(b.get("date") or "").strip()
        if d:
            out[d] = b
    return out


def _common_dates(stock_bars: Dict[str, List[dict]]) -> List[str]:
    common: Optional[set] = None
    for bars in stock_bars.values():
        keys = set(_bars_by_date(bars).keys())
        common = keys if common is None else common & keys
    return sorted(common or [])


def _hold_return(
    date_map: Dict[str, dict],
    dates: List[str],
    entry_i: int,
    horizon: int,
) -> Optional[float]:
    if entry_i + horizon >= len(dates):
        return None
    e = date_map.get(dates[entry_i])
    x = date_map.get(dates[entry_i + horizon])
    if not e or not x:
        return None
    c0 = float(e.get("close") or 0)
    c1 = float(x.get("close") or 0)
    if c0 <= 0 or c1 <= 0:
        return None
    return (c1 / c0 - 1.0) * 100.0


def _split_quantiles(
    scored: List[tuple],
    n_quantiles: int,
) -> List[List[tuple]]:
    """scored: (code, score) 已按 score 升序；Q1=最低，Qn=最高。"""
    n = len(scored)
    if n < n_quantiles:
        return [[] for _ in range(n_quantiles)]
    buckets: List[List[tuple]] = [[] for _ in range(n_quantiles)]
    for i, row in enumerate(scored):
        # 等频：i in [0,n) → bucket
        q = min(n_quantiles - 1, int(i * n_quantiles / n))
        buckets[q].append(row)
    return buckets


def backtest_score_quantiles(
    stock_bars: Dict[str, List[dict]],
    *,
    n_quantiles: int = 5,
    horizon_days: int = 3,
    min_history: int = 12,
    max_window: int = 30,
    min_names: int = 5,
    fundamentals_by_code: Optional[Dict[str, dict]] = None,
) -> Dict[str, Any]:
    """
    非重叠：每 horizon 日按 score 分 Q1..Qn，等权持有至期末，复利各层净值。
    """
    from core.backtest.engine import _mock_quote_from_bars, _trade_metrics
    from core.signal.cross_section_batch import score_window_as_item

    n_quantiles = max(2, min(int(n_quantiles or 5), 10))
    horizon_days = max(1, min(int(horizon_days or 3), 10))
    min_history = max(5, int(min_history or 12))
    min_names = max(n_quantiles, int(min_names or 5))

    if not stock_bars or len(stock_bars) < min_names:
        return {"ok": False, "reason": "标的不足", "quantiles": []}

    date_maps = {c: _bars_by_date(b) for c, b in stock_bars.items()}
    dates = _common_dates(stock_bars)
    n = len(dates)
    need = min_history + horizon_days
    if n < need:
        return {
            "ok": False,
            "reason": f"共同交易日不足（{n}<{need}）",
            "quantiles": [],
            "common_dates": n,
        }

    rets: List[List[float]] = [[] for _ in range(n_quantiles)]
    equity = [100.0] * n_quantiles
    equity_curves: List[List[dict]] = [[{"date": dates[min_history - 1], "equity": 100.0}] for _ in range(n_quantiles)]
    fold_count = 0

    i = min_history - 1
    last_i = n - horizon_days - 1
    while i <= last_i:
        scored: List[tuple] = []
        for code, dm in date_maps.items():
            start = max(0, i - max_window + 1)
            window = [dm[d] for d in dates[start : i + 1] if d in dm]
            if len(window) < 2:
                continue
            quote = _mock_quote_from_bars(window, len(window) - 1)
            fund = (fundamentals_by_code or {}).get(code)
            item = score_window_as_item(
                code,
                window,
                horizon_days=horizon_days,
                quote=quote,
                fundamentals=fund,
            )
            if not item or item.get("hard_reject"):
                continue
            scored.append((code, float(item.get("score") or 0)))
        if len(scored) < min_names:
            i += horizon_days
            continue

        scored.sort(key=lambda x: x[1])  # 升序：低分在前 → Q1
        buckets = _split_quantiles(scored, n_quantiles)
        exit_date = dates[i + horizon_days]
        ok_fold = False
        for qi, bucket in enumerate(buckets):
            if not bucket:
                continue
            leg_rets = []
            for code, _sc in bucket:
                r = _hold_return(date_maps[code], dates, i, horizon_days)
                if r is not None:
                    leg_rets.append(r)
            if not leg_rets:
                continue
            port = sum(leg_rets) / len(leg_rets)
            rets[qi].append(port)
            equity[qi] *= 1.0 + port / 100.0
            equity_curves[qi].append(
                {"date": exit_date, "equity": round(equity[qi], 2), "return_pct": round(port, 2)}
            )
            ok_fold = True
        if ok_fold:
            fold_count += 1
        i += horizon_days

    rows = []
    for qi in range(n_quantiles):
        m = _trade_metrics(rets[qi]) if rets[qi] else {}
        label = f"Q{qi + 1}"
        if qi == 0:
            label += "（低分）"
        elif qi == n_quantiles - 1:
            label += "（高分）"
        rows.append(
            {
                "quantile": qi + 1,
                "label": label,
                "total_return_pct": m.get("total_return_pct"),
                "win_rate_pct": m.get("win_rate_pct"),
                "trade_count": m.get("trade_count") or len(rets[qi]),
                "final_equity": round(equity[qi], 2),
                "equity_curve_tail": equity_curves[qi][-80:],
            }
        )

    # 单调：高分端累计收益应 ≥ 低分端（允许相等）
    highs = [r.get("total_return_pct") for r in rows if r.get("total_return_pct") is not None]
    monotonic = False
    long_short = None
    if len(highs) == n_quantiles:
        monotonic = all(highs[j] <= highs[j + 1] + 1e-9 for j in range(n_quantiles - 1))
        long_short = round(float(highs[-1]) - float(highs[0]), 2)

    ls_curve = _build_long_short_equity_curve(
        equity_curves[-1] if equity_curves else [],
        equity_curves[0] if equity_curves else [],
    )

    return {
        "ok": fold_count > 0,
        "reason": None if fold_count > 0 else "无有效分层期",
        "n_quantiles": n_quantiles,
        "fold_count": fold_count,
        "horizon_days": horizon_days,
        "quantiles": rows,
        "monotonic_increasing": monotonic,
        "q_high_minus_q_low_pct": long_short,
        "long_short_equity_curve": ls_curve[-80:] if ls_curve else [],
        "note": (
            "Q1=低分 … Qn=高分；期望收益大致单调上升。"
            "非单调说明打分只在极端头部有效或样本噪声大。"
            "分层检验区分度；Top-K 另验截断+成本。"
            "Q高−Q低曲线：每期多空价差复利（起点100）。"
        ),
    }


def _build_long_short_equity_curve(
    high_curve: List[dict],
    low_curve: List[dict],
) -> List[dict]:
    """Q高 − Q低 期收益复利净值（起点 100）。"""
    lmap = {
        str(p.get("date")): float(p.get("equity"))
        for p in (low_curve or [])
        if p.get("date") is not None and p.get("equity") is not None
    }
    pts: List[dict] = []
    prev_h: Optional[float] = None
    prev_l: Optional[float] = None
    eq = 100.0
    for p in high_curve or []:
        d = str(p.get("date") or "")
        if d not in lmap:
            continue
        try:
            h = float(p.get("equity"))
            lo = float(lmap[d])
        except (TypeError, ValueError):
            continue
        if h <= 0 or lo <= 0:
            continue
        if prev_h is not None and prev_l is not None and prev_h > 0 and prev_l > 0:
            rh = h / prev_h - 1.0
            rl = lo / prev_l - 1.0
            eq *= 1.0 + (rh - rl)
            pts.append(
                {
                    "date": d,
                    "equity": round(eq, 2),
                    "return_pct": round((rh - rl) * 100.0, 2),
                }
            )
        else:
            pts.append({"date": d, "equity": 100.0, "return_pct": 0.0})
        prev_h, prev_l = h, lo
    return pts
