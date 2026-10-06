"""TTM 阶段瓶颈拆解与趋势分析。

在 core/ttm_events.py 基础上做增强（不改动原模块）：
- 阶段瓶颈识别（哪段最慢）
- TTM 趋势（改善 / 恶化）
- 并行假设吞吐
- 目标基线对比
- 一键综合摘要

配对逻辑复用 compute_ttm_metrics 的算法：同 meta.cycle_id 优先，否则回退“下一时间戳”启发式。
仅使用标准库，不引入 numpy。
"""


import logging

logger = logging.getLogger(__name__)
import statistics
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.risk_metrics import _parse_ts
from core.ttm_events import (
    TTM_EVENT_BACKTEST,
    TTM_EVENT_IDEA,
    TTM_EVENT_PAPER,
    load_ttm_events,
)

# 配对记录：(idea_ts, end_ts, duration_h, cycle_id)
_PairRecord = Tuple[datetime, datetime, float, str]


def _cycle(e: dict) -> str:
    """取事件 meta.cycle_id（无则空串）。"""
    meta = e.get("meta") if isinstance(e.get("meta"), dict) else {}
    return str(meta.get("cycle_id") or "").strip()


def _resolve_events(
    events: Optional[Sequence[dict]], path: Optional[str]
) -> List[dict]:
    """events 为 None 时从磁盘加载，否则拷贝。"""
    if events is not None:
        return list(events)
    return load_ttm_events(path=path)


def _pair_records(
    starts: List[Tuple[datetime, dict]],
    ends: List[Tuple[datetime, dict]],
) -> List[_PairRecord]:
    """复用 compute_ttm_metrics 的配对逻辑，返回带时间戳的配对记录。

    - 优先同 cycle_id（end_ts >= start_ts 且未被占用）
    - 无 cycle_id 时回退“下一时间戳”
    """
    used_end_idx: set = set()
    out: List[_PairRecord] = []

    def _hours(a: datetime, b: datetime) -> float:
        return (b - a).total_seconds() / 3600.0

    # 1) 同 cycle_id
    end_by_cycle: Dict[str, List[Tuple[int, datetime, dict]]] = {}
    for i, (te, ee) in enumerate(ends):
        cid = _cycle(ee)
        if cid:
            end_by_cycle.setdefault(cid, []).append((i, te, ee))
    for ts, es in starts:
        cid = _cycle(es)
        if not cid:
            continue
        cands = [
            (i, te)
            for i, te, _ in end_by_cycle.get(cid, [])
            if te >= ts and i not in used_end_idx
        ]
        if not cands:
            continue
        i, te = min(cands, key=lambda x: x[1])
        used_end_idx.add(i)
        out.append((ts, te, _hours(ts, te), cid))
    # 2) 无 cycle：下一时间戳（不复用已占用 end）
    for ts, es in starts:
        if _cycle(es):
            continue
        later = [
            (i, te)
            for i, (te, _) in enumerate(ends)
            if te >= ts and i not in used_end_idx
        ]
        if not later:
            continue
        i, te = min(later, key=lambda x: x[1])
        used_end_idx.add(i)
        out.append((ts, te, _hours(ts, te), ""))
    return out


def _p90(sorted_xs: List[float]) -> Optional[float]:
    """90 分位数：index = ceil(0.9 * len) - 1。"""
    n = len(sorted_xs)
    if n == 0:
        return None
    idx = (9 * n + 9) // 10 - 1  # 等价 ceil(0.9 * n) - 1
    if idx < 0:
        idx = 0
    if idx >= n:
        idx = n - 1
    return round(sorted_xs[idx], 2)


def _stage_stats(durations: List[float]) -> Dict[str, Any]:
    """单阶段统计：中位 / 均值 / P90 / 最小 / 最大 / 样本数。"""
    n = len(durations)
    if n == 0:
        return {
            "median_h": None,
            "mean_h": None,
            "p90_h": None,
            "min_h": None,
            "max_h": None,
            "samples": 0,
        }
    s = sorted(durations)
    return {
        "median_h": round(statistics.median(durations), 2),
        "mean_h": round(statistics.mean(durations), 2),
        "p90_h": _p90(s),
        "min_h": round(s[0], 2),
        "max_h": round(s[-1], 2),
        "samples": n,
    }


def _split_by_event(
    evs: Sequence[dict],
) -> Tuple[
    List[Tuple[datetime, dict]],
    List[Tuple[datetime, dict]],
    List[Tuple[datetime, dict]],
]:
    """按事件类型拆分并过滤无效时间戳。"""
    ideas = [
        (_parse_ts(e.get("ts")), e)
        for e in evs
        if e.get("event") == TTM_EVENT_IDEA
    ]
    bts = [
        (_parse_ts(e.get("ts")), e)
        for e in evs
        if e.get("event") == TTM_EVENT_BACKTEST
    ]
    papers = [
        (_parse_ts(e.get("ts")), e)
        for e in evs
        if e.get("event") == TTM_EVENT_PAPER
    ]
    ideas = [(t, e) for t, e in ideas if t]
    bts = [(t, e) for t, e in bts if t]
    papers = [(t, e) for t, e in papers if t]
    return ideas, bts, papers


def compute_ttm_stage_breakdown(
    events: Optional[Sequence[dict]] = None, *, path: Optional[str] = None
) -> Dict[str, Any]:
    """TTM 阶段瓶颈拆解。

    对 idea→backtest、backtest→paper 两段分别统计中位 / 均值 / P90 / 最小 / 最大；
    瓶颈阶段 = 中位数最大的阶段；瓶颈占比 = 瓶颈中位 / 端到端中位。
    """
    evs = _resolve_events(events, path)
    if not evs:
        return {
            "stages": {
                "idea_to_backtest": _stage_stats([]),
                "backtest_to_paper": _stage_stats([]),
            },
            "bottleneck_stage": None,
            "bottleneck_share": None,
            "end_to_end_median_h": None,
            "sample_pairs": 0,
            "note": "尚无 TTM 事件，无法拆解阶段。",
        }

    ideas, bts, papers = _split_by_event(evs)
    i2b_pairs = _pair_records(ideas, bts)
    b2p_pairs = _pair_records(bts, papers)
    i2p_pairs = _pair_records(ideas, papers)

    i2b_durations = [d for _, _, d, _ in i2b_pairs]
    b2p_durations = [d for _, _, d, _ in b2p_pairs]
    i2p_durations = [d for _, _, d, _ in i2p_pairs]

    stages = {
        "idea_to_backtest": _stage_stats(i2b_durations),
        "backtest_to_paper": _stage_stats(b2p_durations),
    }
    e2e_median = (
        round(statistics.median(i2p_durations), 2) if i2p_durations else None
    )

    med_i2b = stages["idea_to_backtest"]["median_h"]
    med_b2p = stages["backtest_to_paper"]["median_h"]
    if med_i2b is None and med_b2p is None:
        bottleneck_stage: Optional[str] = None
    elif med_b2p is None or (med_i2b is not None and med_i2b >= med_b2p):
        bottleneck_stage = "idea_to_backtest"
    else:
        bottleneck_stage = "backtest_to_paper"

    if bottleneck_stage is not None and e2e_median and e2e_median > 0:
        bn_median = stages[bottleneck_stage]["median_h"] or 0.0
        bottleneck_share: Optional[float] = round(bn_median / e2e_median, 4)
    else:
        bottleneck_share = None

    return {
        "stages": stages,
        "bottleneck_stage": bottleneck_stage,
        "bottleneck_share": bottleneck_share,
        "end_to_end_median_h": e2e_median,
        "sample_pairs": len(i2p_pairs),
        "note": "阶段耗时（小时）；瓶颈=中位数最大阶段，占比=瓶颈中位/端到端中位。",
    }


def ttm_trend(
    events: Optional[Sequence[dict]] = None,
    *,
    window: int = 5,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """TTM 趋势分析（近 window 次配对 vs 更早的 window 次配对）。

    改善率 = (基线中位 - 近期中位) / 基线中位，正值代表改善。
    """
    window = max(1, int(window or 5))
    evs = _resolve_events(events, path)
    if not evs:
        return {
            "recent_median_h": None,
            "baseline_median_h": None,
            "improvement_rate": None,
            "trend": "insufficient",
            "recent_samples": 0,
            "baseline_samples": 0,
            "recent_ttms": [],
            "note": "尚无 TTM 事件，无法分析趋势。",
        }

    ideas, _bts, papers = _split_by_event(evs)
    i2p_pairs = _pair_records(ideas, papers)
    i2p_pairs.sort(key=lambda r: r[0])  # 按 idea 时间排序
    ttms = [r[2] for r in i2p_pairs]

    # 近期 = 最近 window 个；基线 = 近期之前紧邻的最多 window 个
    # 样本不足：连近期 window 个都凑不齐，或基线为空
    if len(ttms) < window:
        recent = ttms
        recent_median = round(statistics.median(recent), 2) if recent else None
        return {
            "recent_median_h": recent_median,
            "baseline_median_h": None,
            "improvement_rate": None,
            "trend": "insufficient",
            "recent_samples": len(recent),
            "baseline_samples": 0,
            "recent_ttms": [round(x, 2) for x in recent],
            "note": "配对样本不足，无法构造近期 window 个。",
        }

    recent = ttms[-window:]
    remaining = ttms[:-window]
    if not remaining:
        recent_median = round(statistics.median(recent), 2)
        return {
            "recent_median_h": recent_median,
            "baseline_median_h": None,
            "improvement_rate": None,
            "trend": "insufficient",
            "recent_samples": len(recent),
            "baseline_samples": 0,
            "recent_ttms": [round(x, 2) for x in recent],
            "note": "仅有近期样本，缺少更早基线用于对比。",
        }

    baseline = remaining[-window:]
    recent_median = round(statistics.median(recent), 2)
    baseline_median = round(statistics.median(baseline), 2)

    if baseline_median in (0, None):
        improvement_rate: Optional[float] = None
        trend = "insufficient"
    else:
        improvement_rate = round((baseline_median - recent_median) / baseline_median, 4)
        if improvement_rate > 0.1:
            trend = "improving"
        elif improvement_rate < -0.1:
            trend = "declining"
        else:
            trend = "stable"

    return {
        "recent_median_h": recent_median,
        "baseline_median_h": baseline_median,
        "improvement_rate": improvement_rate,
        "trend": trend,
        "recent_samples": len(recent),
        "baseline_samples": len(baseline),
        "recent_ttms": [round(x, 2) for x in recent],
        "note": "近期 vs 更早的等量配对；改善率=(基线-近期)/基线，正=改善。",
    }


def identify_bottleneck(
    events: Optional[Sequence[dict]] = None, *, path: Optional[str] = None
) -> Dict[str, Any]:
    """识别 TTM 瓶颈（综合阶段拆解 + 趋势）。"""
    sb = compute_ttm_stage_breakdown(events, path=path)
    tr = ttm_trend(events, path=path)

    bottleneck_stage = sb.get("bottleneck_stage")
    bottleneck_share = sb.get("bottleneck_share")
    trend = tr.get("trend")
    is_worsening = trend == "declining"

    suggestion_map = {
        "idea_to_backtest": "加速因子研究→回测转化：考虑批量回测/参数预设",
        "backtest_to_paper": "加速回测→纸面落地：考虑自动 promote 流程/减少人审环节",
    }
    suggestion = suggestion_map.get(bottleneck_stage or "", "")
    if is_worsening:
        warning = "注意：瓶颈阶段耗时在增加"
        suggestion = (suggestion + "；" + warning) if suggestion else warning
    if not suggestion:
        suggestion = "样本不足，暂无瓶颈建议。"

    return {
        "bottleneck_stage": bottleneck_stage,
        "bottleneck_share": bottleneck_share,
        "trend": trend,
        "is_worsening": is_worsening,
        "actionable_suggestion": suggestion,
        "note": "综合阶段拆解与趋势；is_worsening=趋势 declining。",
    }


def parallel_hypothesis_throughput(
    events: Optional[Sequence[dict]] = None,
    *,
    window_days: int = 30,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """并行假设吞吐（近 window_days 天）。

    - 新假设数 = 窗口内 idea_opened 事件数
    - 完成回测数 = 窗口内 backtest_ready 事件数
    - 周吞吐 = 完成回测数 / window_days * 7
    - 平均并行度 = 窗口内事件时间点采样的活跃假设数均值（累计 idea - 累计 paper，下界 0）
    """
    window_days = max(1, int(window_days or 30))
    evs = _resolve_events(events, path)
    if not evs:
        return {
            "window_days": window_days,
            "new_hypotheses": 0,
            "completed_backtests": 0,
            "weekly_throughput": 0.0,
            "avg_parallel": 0.0,
            "note": "尚无 TTM 事件，无法计算吞吐。",
        }

    all_ts = [_parse_ts(e.get("ts")) for e in evs]
    all_ts = [t for t in all_ts if t]
    reference = max(all_ts) if all_ts else datetime.now()
    window_start = reference - timedelta(days=window_days)

    ideas, bts, papers = _split_by_event(evs)
    ideas_in_window = [(t, e) for t, e in ideas if window_start <= t <= reference]
    bts_in_window = [(t, e) for t, e in bts if window_start <= t <= reference]

    new_hypotheses = len(ideas_in_window)
    completed_backtests = len(bts_in_window)
    weekly_throughput = round(completed_backtests / window_days * 7, 2)

    # 平均并行度：在窗口内事件时间点采样，活跃假设 = 累计 idea - 累计 paper（>=0）
    sample_points = sorted({t for t in all_ts if window_start <= t <= reference})
    active_counts: List[int] = []
    for t in sample_points:
        started = sum(1 for ti, _ in ideas if ti <= t)
        completed = sum(1 for tp, _ in papers if tp <= t)
        active_counts.append(max(0, started - completed))
    avg_parallel = round(statistics.mean(active_counts), 3) if active_counts else 0.0

    return {
        "window_days": window_days,
        "new_hypotheses": new_hypotheses,
        "completed_backtests": completed_backtests,
        "weekly_throughput": weekly_throughput,
        "avg_parallel": avg_parallel,
        "note": "近窗口新假设/完成回测；周吞吐=完成回测/天数*7；平均并行度=事件点采样活跃均值。",
    }


def ttm_vs_target(
    events: Optional[Sequence[dict]] = None,
    *,
    target_hours: float = 8.0,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """TTM 目标基线对比。

    - 达成率 = target / actual（>1 = 优于目标）
    - 超时率 = 超过 target 的配对占比；准时率 = 1 - 超时率
    """
    target_hours = float(target_hours or 0.0)
    evs = _resolve_events(events, path)
    if not evs:
        return {
            "target_hours": target_hours,
            "actual_median_hours": None,
            "achievement_rate": None,
            "on_time_rate": None,
            "overtime_count": 0,
            "total_count": 0,
            "verdict": "insufficient",
            "note": "尚无 TTM 事件，无法对比目标。",
        }

    ideas, _bts, papers = _split_by_event(evs)
    i2p_pairs = _pair_records(ideas, papers)
    ttms = [d for _, _, d, _ in i2p_pairs]

    if not ttms:
        return {
            "target_hours": target_hours,
            "actual_median_hours": None,
            "achievement_rate": None,
            "on_time_rate": None,
            "overtime_count": 0,
            "total_count": 0,
            "verdict": "insufficient",
            "note": "无 idea→paper 配对，无法对比目标。",
        }

    actual_median = round(statistics.median(ttms), 2)
    total = len(ttms)
    overtime_count = sum(1 for x in ttms if x > target_hours)
    on_time_rate = round((total - overtime_count) / total, 4)

    if actual_median > 0:
        achievement_rate: Optional[float] = round(target_hours / actual_median, 4)
    else:
        achievement_rate = None

    if achievement_rate is None:
        verdict = "insufficient"
    elif achievement_rate > 1.0:
        verdict = "on_track"
    elif achievement_rate > 0.7:
        verdict = "behind"
    else:
        verdict = "far_behind"

    return {
        "target_hours": target_hours,
        "actual_median_hours": actual_median,
        "achievement_rate": achievement_rate,
        "on_time_rate": on_time_rate,
        "overtime_count": overtime_count,
        "total_count": total,
        "verdict": verdict,
        "note": "达成率=目标/实际(>1优于目标)；超时=超过目标的配对占比。",
    }


def summarize_ttm_stages(
    events: Optional[Sequence[dict]] = None, *, path: Optional[str] = None
) -> Dict[str, Any]:
    """TTM 综合摘要（一键报告）。"""
    sb = compute_ttm_stage_breakdown(events, path=path)
    tr = ttm_trend(events, path=path)
    bn = identify_bottleneck(events, path=path)
    th = parallel_hypothesis_throughput(events, path=path)
    vt = ttm_vs_target(events, path=path)

    stage = bn.get("bottleneck_stage")
    share = bn.get("bottleneck_share")
    trend = tr.get("trend")
    verdict = vt.get("verdict")
    actual = vt.get("actual_median_hours")
    target = vt.get("target_hours")

    parts: List[str] = []
    if stage:
        share_str = ("占比%.0f%%" % (share * 100)) if share is not None else "占比N/A"
        parts.append("瓶颈:%s(%s)" % (stage, share_str))
    else:
        parts.append("瓶颈:样本不足")
    parts.append("趋势:%s" % (trend or "N/A"))
    if actual is not None and target is not None:
        parts.append(
            "目标:%s(实际%.1fh/目标%.1fh)" % (verdict or "N/A", actual, target)
        )
    else:
        parts.append("目标:%s" % (verdict or "N/A"))
    headline = "；".join(parts)

    return {
        "stage_breakdown": sb,
        "trend": tr,
        "bottleneck": bn,
        "throughput": th,
        "target": vt,
        "headline": headline,
        "note": "TTM 阶段拆解+趋势+瓶颈+吞吐+目标 一键摘要。",
    }


if __name__ == "__main__":
    # 模拟 TTM 事件
    from datetime import datetime, timedelta

    base = datetime(2024, 1, 1, 9, 0, 0)
    events = []
    for i in range(8):
        cid = f"cycle_{i}"
        events.append({"ts": (base + timedelta(hours=i * 72)).isoformat(), "event": "idea_opened", "meta": {"cycle_id": cid}})
        events.append({"ts": (base + timedelta(hours=i * 72 + 5)).isoformat(), "event": "backtest_ready", "meta": {"cycle_id": cid}})
        # 后面几个 cycle 的 backtest→paper 变慢
        b2p_hours = 3 if i < 4 else 10
        events.append({"ts": (base + timedelta(hours=i * 72 + 5 + b2p_hours)).isoformat(), "event": "paper_rule_live", "meta": {"cycle_id": cid}})

    sb = compute_ttm_stage_breakdown(events)
    logger.info("stage_breakdown: bottleneck=%s, share=%.2f" % (sb["bottleneck_stage"], sb["bottleneck_share"]))
    logger.info("  i2b median=%.1fh, b2p median=%.1fh" % (sb["stages"]["idea_to_backtest"]["median_h"], sb["stages"]["backtest_to_paper"]["median_h"]))

    tr = ttm_trend(events, window=4)
    logger.info("trend: recent=%.1fh, baseline=%.1fh, %s" % (tr["recent_median_h"] or 0, tr["baseline_median_h"] or 0, tr["trend"]))

    bn = identify_bottleneck(events)
    logger.info("bottleneck: %s, worsening=%s, suggestion=%s" % (bn["bottleneck_stage"], bn["is_worsening"], bn["actionable_suggestion"][:30]))

    th = parallel_hypothesis_throughput(events, window_days=30)
    logger.info("throughput: weekly=%.1f, parallel=%.1f" % (th["weekly_throughput"], th["avg_parallel"]))

    vt = ttm_vs_target(events, target_hours=8.0)
    logger.info("vs_target: actual=%.1fh, achievement=%.2f, %s" % (vt["actual_median_hours"] or 0, vt["achievement_rate"] or 0, vt["verdict"]))

    summary = summarize_ttm_stages(events)
    logger.info("headline:", summary["headline"])
