"""K 线形态摘要：基于 OHLCV 的可解释描述（非买卖信号）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _avg(vals: List[float]) -> Optional[float]:
    if not vals:
        return None
    return sum(vals) / len(vals)


def describe_candle(bar: dict, prev: Optional[dict] = None) -> Dict[str, Any]:
    o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
    rng = max(h - l, 1e-9)
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    direction = "阳线" if c > o else ("阴线" if c < o else "十字星")
    body_pct = body / rng
    upper_pct = upper / rng
    lower_pct = lower / rng
    change_pct = ((c / o) - 1.0) * 100.0 if o else 0.0

    tags = [direction]
    if body_pct >= 0.7 and abs(change_pct) >= 3:
        tags.append("大" + ("阳" if c > o else "阴"))
    elif body_pct <= 0.15:
        tags.append("小实体")
    if upper_pct >= 0.35:
        tags.append("长上影")
    if lower_pct >= 0.35:
        tags.append("长下影")

    vol_tag = None
    if prev and prev.get("volume"):
        ratio = bar["volume"] / prev["volume"] if prev["volume"] else None
        if ratio is not None:
            if ratio >= 1.5:
                vol_tag = "放量"
                tags.append("放量")
            elif ratio <= 0.7:
                vol_tag = "缩量"
                tags.append("缩量")

    return {
        "date": bar.get("date"),
        "open": round(o, 4),
        "high": round(h, 4),
        "low": round(l, 4),
        "close": round(c, 4),
        "volume": bar.get("volume"),
        "change_pct": round(change_pct, 2),
        "direction": direction,
        "tags": tags,
        "volume_tag": vol_tag,
        "body_ratio": round(body_pct, 2),
        "upper_shadow_ratio": round(upper_pct, 2),
        "lower_shadow_ratio": round(lower_pct, 2),
    }


def summarize_bars(bars: List[dict]) -> Dict[str, Any]:
    if not bars:
        return {"summary": "无 K 线数据", "latest": None, "trend_note": ""}

    described = []
    for i, bar in enumerate(bars):
        prev = bars[i - 1] if i > 0 else None
        described.append(describe_candle(bar, prev))

    latest = described[-1]
    closes = [b["close"] for b in bars]
    n = min(5, len(closes))
    recent = closes[-n:]
    trend = "震荡"
    if recent[-1] > recent[0] * 1.02:
        trend = "近端偏强（收盘抬升）"
    elif recent[-1] < recent[0] * 0.98:
        trend = "近端偏弱（收盘下移）"

    # 近 5 日累计涨跌
    cum = None
    if len(closes) >= 2:
        base = closes[-(n)]
        if base:
            cum = round((closes[-1] / base - 1.0) * 100.0, 2)

    vols = [b["volume"] for b in bars if b.get("volume")]
    avg_vol = _avg(vols[-6:-1]) if len(vols) >= 2 else None
    latest_vol_ratio = None
    if avg_vol and latest.get("volume"):
        latest_vol_ratio = round(latest["volume"] / avg_vol, 2)

    parts = [
        f"最新一根（{latest.get('date')}）为{latest['direction']}，"
        f"涨跌约 {latest['change_pct']:+.2f}%",
        f"标签：{'/'.join(latest['tags'])}",
        f"近端走势：{trend}",
    ]
    if cum is not None:
        parts.append(f"近{n}日累计约 {cum:+.2f}%")
    if latest_vol_ratio is not None:
        parts.append(f"最新量相对近均量约 {latest_vol_ratio:.2f} 倍")

    return {
        "summary": "；".join(parts) + "。",
        "latest": latest,
        "trend_note": trend,
        "recent_cum_change_pct": cum,
        "latest_volume_vs_avg": latest_vol_ratio,
        "candles": described,
    }
