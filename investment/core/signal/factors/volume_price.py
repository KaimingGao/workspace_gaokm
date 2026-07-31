"""量价因子（增强版）。

多维度量价分析：
1. 量比（短期/长期成交量比）
2. 价量配合（上涨放量/下跌缩量）
3. OBV趋势（能量潮）
4. 换手率分位
5. 量价背离检测
"""

from __future__ import annotations

from typing import List, Optional

from core.signal.factors.momentum import pct_change


def _avg(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def _calc_obv(bars: List[dict]) -> List[float]:
    """计算OBV（能量潮）。"""
    if len(bars) < 2:
        return [0.0]
    
    obv = [0.0]
    for i in range(1, len(bars)):
        vol = float(bars[i].get("volume", 0))
        if bars[i]["close"] > bars[i-1]["close"]:
            obv.append(obv[-1] + vol)
        elif bars[i]["close"] < bars[i-1]["close"]:
            obv.append(obv[-1] - vol)
        else:
            obv.append(obv[-1])
    return obv


def _calc_volume_percentile(bars: List[dict], window: int = 60) -> Optional[float]:
    """计算当前成交量在历史窗口中的分位。"""
    if len(bars) < 10:
        return None
    
    volumes = [float(b.get("volume", 0)) for b in bars[-window:]]
    volumes = [v for v in volumes if v > 0]
    if len(volumes) < 5:
        return None
    
    current = volumes[-1]
    below_count = sum(1 for v in volumes if v < current)
    return below_count / len(volumes) * 100


def volume_ratio(bars: List[dict], short: int = 3, long: int = 10) -> Optional[float]:
    if len(bars) < 2:
        return None
    if len(bars) < long:
        long = len(bars)
    short = min(short, long)
    recent_vols = [b.get("volume", 0) for b in bars[-short:]]
    base_vols = [b.get("volume", 0) for b in bars[-long:]]
    recent = _avg([v for v in recent_vols if v > 0])
    base = _avg([v for v in base_vols if v > 0])
    if not recent or not base:
        return None
    return recent / base


def score_volume_price(
    bars: List[dict],
    *,
    last_change: Optional[float],
) -> tuple[float, dict]:
    """
    多维度量价评分。
    
    维度：
    1. 量比：短期成交量/长期成交量
    2. 价量配合：上涨放量+，下跌缩量+
    3. OBV趋势：能量潮方向
    4. 成交量分位：当前成交量在历史中的位置
    """
    if not bars or len(bars) < 5:
        return 50.0, {"volume_ratio": None}
    
    # 1. 量比评分
    vol_ratio = volume_ratio(bars)
    vr_score = 50.0
    if vol_ratio is not None:
        if vol_ratio >= 2.5:
            vr_score = 82.0  # 显著放量
        elif vol_ratio >= 1.5:
            vr_score = 68.0  # 温和放量
        elif vol_ratio >= 1.1:
            vr_score = 55.0  # 略微放量
        elif vol_ratio < 0.5:
            vr_score = 30.0  # 显著缩量
        elif vol_ratio < 0.8:
            vr_score = 42.0  # 温和缩量
        else:
            vr_score = 50.0
    
    # 2. 价量配合评分
    vp_score = 50.0
    if last_change is not None and vol_ratio is not None:
        if last_change > 2 and vol_ratio > 1.5:
            # 上涨放量（健康上涨）
            vp_score = 85.0
        elif last_change > 0 and vol_ratio > 1.2:
            # 上涨温和放量
            vp_score = 70.0
        elif last_change > 0 and vol_ratio < 0.8:
            # 上涨缩量（上涨动能不足）
            vp_score = 42.0
        elif last_change < -2 and vol_ratio > 1.5:
            # 下跌放量（恐慌性抛售）
            vp_score = 20.0
        elif last_change < 0 and vol_ratio < 0.7:
            # 下跌缩量（惜售）
            vp_score = 55.0
        elif last_change < 0 and vol_ratio > 1.0:
            # 下跌放量
            vp_score = 35.0
        else:
            vp_score = 50.0
    
    # 3. OBV趋势评分
    obv_score = 50.0
    obv = _calc_obv(bars)
    if len(obv) >= 10:
        obv_short = sum(obv[-3:]) / 3
        obv_long = sum(obv[-10:]) / 10
        if obv_short > obv_long * 1.02:
            obv_score = 65.0  # OBV上升
        elif obv_short < obv_long * 0.98:
            obv_score = 35.0  # OBV下降
        else:
            obv_score = 50.0
    
    # 4. 成交量分位评分
    vol_percentile = _calc_volume_percentile(bars)
    percentile_score = 50.0
    if vol_percentile is not None:
        if vol_percentile > 90:
            percentile_score = 72.0  # 成交量极端放大
        elif vol_percentile > 70:
            percentile_score = 62.0  # 成交量放大
        elif vol_percentile < 10:
            percentile_score = 32.0  # 成交量极端萎缩
        elif vol_percentile < 30:
            percentile_score = 42.0  # 成交量萎缩
        else:
            percentile_score = 50.0
    
    # 5. 量价背离检测
    divergence_score = 50.0
    closes = [float(b["close"]) for b in bars[-10:]]
    vols = [float(b.get("volume", 0)) for b in bars[-10:]]
    if len(closes) >= 5 and last_change is not None:
        # 价格创新低但成交量萎缩 → 底背离
        price_trend = closes[-1] - closes[-5]
        vol_trend = sum(vols[-3:]) / 3 - sum(vols[-5:-2]) / 3
        
        if price_trend < 0 and vol_trend > 0:
            # 价格下跌，成交量放大 → 继续下跌
            divergence_score = 30.0
        elif price_trend > 0 and vol_trend < 0:
            # 价格上涨，成交量萎缩 → 顶背离
            divergence_score = 35.0
        elif price_trend < 0 and vol_trend < 0:
            # 价格下跌缩量 → 下跌动能衰竭
            divergence_score = 62.0
        elif price_trend > 0 and vol_trend > 0:
            # 上涨放量 → 健康
            divergence_score = 68.0
    
    # 综合评分
    total = (
        0.20 * vr_score +
        0.25 * vp_score +
        0.20 * obv_score +
        0.15 * percentile_score +
        0.20 * divergence_score
    )
    
    return round(total, 1), {
        "volume_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
        "volume_percentile": round(vol_percentile, 1) if vol_percentile is not None else None,
        "vp配合分": round(vp_score, 1),
        "obv_trend分": round(obv_score, 1),
    }
