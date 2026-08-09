"""回测稳健性摘要（N4）：样本内外切分 + 简单 regime 切片。"""

from __future__ import annotations
from core.numbers import to_float as _f

from typing import Any, Dict, List, Optional, Sequence


def split_oos_summary(
    equity_curve: Sequence[dict],
    *,
    oos_ratio: float = 0.3,
) -> Dict[str, Any]:
    """按权益曲线后段作为 OOS，比较首末净值变化。"""
    curve = list(equity_curve or [])
    n = len(curve)
    if n < 4:
        return {
            "ok": False,
            "failed": True,
            "reason": "equity_curve_too_short",
            "points": n,
        }
    ratio = min(0.5, max(0.15, float(oos_ratio or 0.3)))
    cut = max(1, int(n * (1.0 - ratio)))
    is_part = curve[:cut]
    oos_part = curve[cut:]

    def _ret(part: List[dict]) -> Optional[float]:
        if len(part) < 2:
            return None
        a = _f(part[0].get("equity") or part[0].get("value") or part[0].get("nav"))
        b = _f(part[-1].get("equity") or part[-1].get("value") or part[-1].get("nav"))
        if a is None or b is None or a == 0:
            return None
        return round((b / a - 1.0) * 100.0, 2)

    is_ret = _ret(is_part)
    oos_ret = _ret(oos_part)
    # P1：样本外显著弱于样本内 → 标记失败（报告标红）
    failed = False
    fail_reason = None
    if is_ret is not None and oos_ret is not None:
        gap = oos_ret - is_ret
        if gap <= -10.0:
            failed = True
            fail_reason = f"oos_underperform_gap_{gap:.1f}pp"
        elif is_ret > 0 and oos_ret < 0 and gap <= -5.0:
            failed = True
            fail_reason = "oos_negative_while_is_positive"

    return {
        "ok": True,
        "failed": failed,
        "fail_reason": fail_reason,
        "oos_ratio": ratio,
        "is_points": len(is_part),
        "oos_points": len(oos_part),
        "is_return_pct": is_ret,
        "oos_return_pct": oos_ret,
        "note": "按权益曲线时间切分；非严格 Walk-forward 标签。",
    }


def regime_label_from_closes(closes: List[float]) -> str:
    """与 regime_summary_from_bars 相同规则，供分桶复用。"""
    if len(closes) < 5:
        return "unknown"
    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1]:
            rets.append(closes[i] / closes[i - 1] - 1.0)
    if not rets:
        return "unknown"
    import math

    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / len(rets)
    vol = math.sqrt(var)
    total = closes[-1] / closes[0] - 1.0 if closes[0] else 0.0
    if vol >= 0.025:
        return "high_vol"
    if vol <= 0.01:
        return "low_vol"
    if total >= 0.05:
        return "trend_up"
    if total <= -0.05:
        return "trend_down"
    return "chop"


def regime_summary_from_bars(
    bars: List[dict],
    *,
    window: int = 20,
) -> Dict[str, Any]:
    """用末段波动粗分 regime：high_vol / low_vol / trend_up / trend_down / chop。"""
    rows = list(bars or [])
    if len(rows) < max(5, window):
        return {"ok": False, "reason": "bars_too_short", "bar_count": len(rows)}

    closes: List[float] = []
    for b in rows[-(window + 5) :]:
        try:
            closes.append(float(b.get("close")))
        except (TypeError, ValueError):
            continue
    if len(closes) < 5:
        return {"ok": False, "reason": "no_closes"}

    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1]:
            rets.append(closes[i] / closes[i - 1] - 1.0)
    if not rets:
        return {"ok": False, "reason": "no_returns"}

    import math

    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / len(rets)
    vol = math.sqrt(var)
    total = closes[-1] / closes[0] - 1.0 if closes[0] else 0.0
    regime = regime_label_from_closes(closes)

    return {
        "ok": True,
        "regime": regime,
        "vol": round(vol, 5),
        "window_return_pct": round(total * 100.0, 2),
        "window": len(rets),
        "note": "简单波动/趋势 proxy；供报告切片，非完整状态机。",
    }


def regime_buckets_from_trades(
    trades: Sequence[dict],
    sample_bars: Optional[List[dict]] = None,
    *,
    window: int = 20,
) -> Dict[str, Any]:
    """
    R4.3 · 按信号日把每笔调仓分到 regime 桶，汇总收益/胜率。
    用 sample_bars（通常为池内一只代表指数/个股）在信号日向前窗口打标。
    """
    rows = list(sample_bars or [])
    if len(rows) < max(5, window):
        return {"ok": False, "reason": "bars_too_short", "buckets": []}

    by_date: Dict[str, int] = {}
    closes_all: List[float] = []
    dates: List[str] = []
    for i, b in enumerate(rows):
        d = str(b.get("date") or "")
        try:
            c = float(b.get("close"))
        except (TypeError, ValueError):
            continue
        if not d:
            continue
        by_date[d] = len(closes_all)
        closes_all.append(c)
        dates.append(d)

    buckets: Dict[str, Dict[str, Any]] = {}
    tagged = 0
    for t in trades or []:
        sig = str(t.get("signal_date") or t.get("entry_date") or "")
        if sig not in by_date:
            continue
        idx = by_date[sig]
        start = max(0, idx - window + 1)
        window_closes = closes_all[start : idx + 1]
        label = regime_label_from_closes(window_closes)
        try:
            ret = float(t.get("return_pct"))
        except (TypeError, ValueError):
            continue
        b = buckets.get(label)
        if not b:
            b = {"regime": label, "returns": [], "n": 0, "wins": 0}
            buckets[label] = b
        b["returns"].append(ret)
        b["n"] += 1
        if ret > 0:
            b["wins"] += 1
        tagged += 1

    out_rows: List[Dict[str, Any]] = []
    for label, b in buckets.items():
        rets = b["returns"]
        avg = round(sum(rets) / len(rets), 3) if rets else None
        wr = round(100.0 * b["wins"] / b["n"], 1) if b["n"] else None
        out_rows.append(
            {
                "regime": label,
                "trade_count": b["n"],
                "avg_return_pct": avg,
                "win_rate_pct": wr,
                "total_return_pct": round(sum(rets), 3) if rets else None,
            }
        )
    out_rows.sort(key=lambda r: -int(r.get("trade_count") or 0))
    return {
        "ok": bool(out_rows),
        "tagged_trades": tagged,
        "buckets": out_rows,
        "note": "按信号日+样本日线窗口打标；多 regime 对照，非 HMM。",
    }


def attach_robustness_fields(
    result: Dict[str, Any],
    *,
    cost_model: str = "simple_cn",
    sample_bars: Optional[List[dict]] = None,
    oos_ratio: float = 0.3,
) -> Dict[str, Any]:
    """就地/拷贝增强回测结果：强制成本头 + OOS + regime + regime 分桶。"""
    out = dict(result or {})
    params = dict(out.get("params") or {})
    params["cost_model"] = cost_model or "simple_cn"
    if "apply_costs" not in params and out.get("params"):
        params["apply_costs"] = bool((out.get("params") or {}).get("apply_costs"))
    out["params"] = params
    out["cost_model"] = params["cost_model"]
    out["oos_summary"] = split_oos_summary(out.get("equity_curve") or [], oos_ratio=oos_ratio)
    if sample_bars:
        out["regime_summary"] = regime_summary_from_bars(sample_bars)
        trades = out.get("trades_sample") or out.get("trades") or []
        # prefer full trades if present
        if out.get("trades"):
            trades = out["trades"]
        out["regime_buckets"] = regime_buckets_from_trades(trades, sample_bars)
    else:
        out["regime_summary"] = {"ok": False, "reason": "no_sample_bars"}
        out["regime_buckets"] = {"ok": False, "reason": "no_sample_bars", "buckets": []}
    return out
