"""观察池横截面 score IC（T5.1）：验证打分区分度，非 Top-K 截断本身。"""


import logging

logger = logging.getLogger(__name__)
import math
from typing import Any, Dict, List, Optional, Tuple


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x < 1e-12 or den_y < 1e-12:
        return None
    return num / (den_x * den_y)


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


def _forward_return_pct(
    date_map: Dict[str, dict],
    dates: List[str],
    idx: int,
    horizon: int,
) -> Optional[float]:
    if idx + horizon >= len(dates):
        return None
    e = date_map.get(dates[idx])
    x = date_map.get(dates[idx + horizon])
    if not e or not x:
        return None
    c0 = float(e.get("close") or 0)
    c1 = float(x.get("close") or 0)
    if c0 <= 0 or c1 <= 0:
        return None
    return (c1 / c0 - 1.0) * 100.0


def compute_pool_cross_section_ic(
    stock_bars: Dict[str, List[dict]],
    *,
    horizon_days: int = 3,
    min_history: int = 12,
    max_window: int = 30,
    min_names: int = 5,
    fundamentals_by_code: Optional[Dict[str, dict]] = None,
    pit_fundamentals: bool = True,
) -> Dict[str, Any]:
    """
    每个决策日：对池内可打分标的算 score，与 horizon 远期收益做截面 Pearson IC。
    返回 ic_mean / ic_std / icir / 日度样本数。

    S0.2：默认按决策日 resolve 财务 PIT；可传 fundamentals_by_code 覆盖（测试/快照）。
    """
    from core.backtest.engine import _mock_quote_from_bars
    from core.signal.cross_section_batch import score_window_as_item

    if not stock_bars or len(stock_bars) < 2:
        return {"ok": False, "reason": "标的不足", "ic_mean": None, "icir": None}

    horizon_days = max(1, min(int(horizon_days or 3), 10))
    min_history = max(5, int(min_history or 12))
    min_names = max(3, int(min_names or 5))

    date_maps = {c: _bars_by_date(b) for c, b in stock_bars.items()}
    dates = _common_dates(stock_bars)
    try:
        from core.market.calendar import filter_trading_dates

        dates = filter_trading_dates(dates)
        calendar_tag = "cn_lite"
    except Exception:  # noqa: BLE001 — best-effort / 非阻塞分支降级
        logger.debug("exception caught in pool_ic.py line 95", exc_info=True)
        calendar_tag = "none"
    n = len(dates)
    need = min_history + horizon_days
    if n < need:
        return {
            "ok": False,
            "reason": f"共同交易日不足（{n}<{need}）",
            "ic_mean": None,
            "icir": None,
            "common_dates": n,
            "calendar": calendar_tag,
        }

    fund_cache: Dict[Tuple[str, str], Optional[dict]] = {}
    pit_hits = 0
    pit_miss = 0

    def _fund_for(code: str, decision_date: str) -> Optional[dict]:
        nonlocal pit_hits, pit_miss
        if fundamentals_by_code is not None and not pit_fundamentals:
            return (fundamentals_by_code or {}).get(code)
        if not pit_fundamentals:
            return (fundamentals_by_code or {}).get(code)
        key = (code, decision_date)
        if key in fund_cache:
            return fund_cache[key]
        try:
            from core.fundamentals_pit import resolve_fundamentals_for_score

            resolved = resolve_fundamentals_for_score(code, as_of=decision_date, live_fallback=False)
            metrics = resolved.get("metrics") if resolved.get("ok") else None
            if metrics:
                pit_hits += 1
            else:
                pit_miss += 1
                if fundamentals_by_code:
                    metrics = fundamentals_by_code.get(code)
            fund_cache[key] = metrics
            return metrics
        except Exception:  # noqa: BLE001 — best-effort / 非阻塞分支降级
            logger.debug("exception caught in pool_ic.py line 135", exc_info=True)
            pit_miss += 1
            metrics = (fundamentals_by_code or {}).get(code)
            fund_cache[key] = metrics
            return metrics

    daily_ics: List[float] = []
    daily_rows: List[dict] = []
    last_i = n - horizon_days - 1
    for i in range(min_history - 1, last_i + 1):
        scores: List[float] = []
        forwards: List[float] = []
        decision_date = dates[i]
        for code, dm in date_maps.items():
            start = max(0, i - max_window + 1)
            window = [dm[d] for d in dates[start : i + 1] if d in dm]
            if len(window) < 2:
                continue
            quote = _mock_quote_from_bars(window, len(window) - 1)
            fund = _fund_for(code, decision_date)
            item = score_window_as_item(
                code,
                window,
                horizon_days=horizon_days,
                quote=quote,
                fundamentals=fund,
            )
            if not item or item.get("hard_reject"):
                continue
            fr = _forward_return_pct(dm, dates, i, horizon_days)
            if fr is None:
                continue
            scores.append(float(item.get("score") or 0))
            forwards.append(fr)
        if len(scores) < min_names:
            continue
        ic = _pearson(scores, forwards)
        if ic is not None:
            daily_ics.append(ic)
            daily_rows.append(
                {
                    "date": dates[i],
                    "ic": round(ic, 4),
                    "n_names": len(scores),
                }
            )

    if len(daily_ics) < 3:
        return {
            "ok": False,
            "reason": f"有效 IC 日不足（{len(daily_ics)}）",
            "ic_mean": None,
            "icir": None,
            "day_count": len(daily_ics),
            "pit_fundamentals": bool(pit_fundamentals),
            "calendar": calendar_tag,
            "note": "截面 IC：score vs 远期收益；样本少时不具统计意义。",
        }

    mean = sum(daily_ics) / len(daily_ics)
    var = sum((x - mean) ** 2 for x in daily_ics) / len(daily_ics)
    std = math.sqrt(var)
    icir = (mean / std) if std > 1e-12 else None
    pos_n = sum(1 for x in daily_ics if x > 0)
    # 滚动 IC 均值（窗=min(20, len//2 或 5)）
    roll_w = max(5, min(20, max(5, len(daily_ics) // 3)))
    rolling: List[dict] = []
    for j in range(len(daily_rows)):
        lo = max(0, j - roll_w + 1)
        chunk = daily_ics[lo : j + 1]
        if len(chunk) < 3:
            continue
        rm = sum(chunk) / len(chunk)
        rolling.append(
            {
                "date": daily_rows[j]["date"],
                "ic": round(rm, 4),
                "window": len(chunk),
            }
        )

    return {
        "ok": True,
        "ic_mean": round(mean, 4),
        "ic_std": round(std, 4),
        "icir": round(icir, 4) if icir is not None else None,
        "day_count": len(daily_ics),
        "positive_ic_days": pos_n,
        "positive_ic_ratio": round(pos_n / len(daily_ics), 4) if daily_ics else None,
        "horizon_days": horizon_days,
        "min_names": min_names,
        "roll_window": roll_w,
        "ic_series_tail": daily_rows[-80:],
        "ic_rolling_tail": rolling[-80:],
        "pit_fundamentals": bool(pit_fundamentals),
        "pit_resolve_hits": pit_hits,
        "pit_resolve_miss": pit_miss,
        "calendar": calendar_tag,
        "note": (
            "池内截面 Pearson IC（score vs 持有期收益）。"
            + ("按决策日财务 PIT。" if pit_fundamentals else "使用传入快照财务。")
            + "IC 均值显著>0 且 ICIR 较高 → 打分有区分度；"
            "这是有效性第一层，不等于 Top-K 截断可实盘。"
            f"正 IC 日 {pos_n}/{len(daily_ics)}；滚动窗 {roll_w}。"
        ),
    }
