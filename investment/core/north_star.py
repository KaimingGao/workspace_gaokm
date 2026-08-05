"""产品北极星二级指标（R0）：纸面夏普/卡玛 · 回测–纸面拟合 · TTM · 拦截流水。

权威计算在 core；Web / localStorage 只展示，不作为真相源。
缺样本时字段为 None 且 status=unavailable，禁止编造。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.backtest_curve_store import (
    _curve_points,
    _paper_daily_equities,
    load_last_backtest_curve,
    paper_date_span,
    save_last_backtest_curve,
)
from core.paths import NORTH_STAR_LAST_BACKTEST_PATH, TTM_EVENTS_PATH
from core.risk_metrics import (
    _DEFAULT_ANN_FACTOR,
    _MIN_ALIGN,
    _MIN_RETURNS,
    _parse_ts,
    _safe_float,
    annualization_factor_from_timestamps,
    calmar_ratio,
    compute_paper_risk_metrics,
    equities_from_snapshots,
    equities_from_strategy_snapshots,
    max_drawdown_pct,
    pearson,
    period_returns,
    rolling_sharpe,
    strategy_snapshots_as_equity,
    tracking_error_pct,
)
from core.ttm_events import (
    TTM_EVENT_BACKTEST,
    TTM_EVENT_IDEA,
    TTM_EVENT_PAPER,
    append_ttm_event,
    compute_ttm_metrics,
    load_ttm_events,
)

__all__ = [
    "NORTH_STAR_LAST_BACKTEST_PATH",
    "TTM_EVENTS_PATH",
    "TTM_EVENT_BACKTEST",
    "TTM_EVENT_IDEA",
    "TTM_EVENT_PAPER",
    "_DEFAULT_ANN_FACTOR",
    "_MIN_ALIGN",
    "_MIN_RETURNS",
    "_curve_points",
    "_paper_daily_equities",
    "_parse_ts",
    "_safe_float",
    "annualization_factor_from_timestamps",
    "append_ttm_event",
    "build_north_star_report",
    "calmar_ratio",
    "compute_paper_risk_metrics",
    "compute_realization",
    "compute_ttm_metrics",
    "equities_from_snapshots",
    "equities_from_strategy_snapshots",
    "load_last_backtest_curve",
    "load_ttm_events",
    "max_drawdown_pct",
    "merge_north_star_into_metrics",
    "paper_date_span",
    "pearson",
    "period_returns",
    "rolling_sharpe",
    "save_last_backtest_curve",
    "strategy_snapshots_as_equity",
    "summarize_risk_blocks",
    "tracking_error_pct",
]


def _span_diag(
    paper_pts: Sequence[Tuple[str, float]],
    bt_pts: Sequence[Tuple[str, float]],
) -> Dict[str, Any]:
    paper_span = f"{paper_pts[0][0]}→{paper_pts[-1][0]}" if paper_pts else "—"
    bt_span = f"{bt_pts[0][0]}→{bt_pts[-1][0]}" if bt_pts else "—"
    return {
        "paper_span": paper_span,
        "backtest_span": bt_span,
        "paper_first": paper_pts[0][0] if paper_pts else None,
        "paper_last": paper_pts[-1][0] if paper_pts else None,
        "backtest_first": bt_pts[0][0] if bt_pts else None,
        "backtest_last": bt_pts[-1][0] if bt_pts else None,
    }


def compute_realization(
    paper_snapshots: Sequence[dict],
    backtest_curve: Sequence[dict],
    *,
    window: int = 60,
    scope: str = "all",
) -> Dict[str, Any]:
    """R0.2 · 同日对齐后的 PnL 相关与跟踪误差。"""
    scope_key = str(scope or "all").strip().lower()
    if scope_key in ("strategy", "strategy_only"):
        paper_snapshots = strategy_snapshots_as_equity(paper_snapshots)
        scope_key = "strategy"
    else:
        scope_key = "all"

    paper_pts = _paper_daily_equities(paper_snapshots)
    bt_pts = _curve_points(backtest_curve)
    if window > 0:
        if len(paper_pts) > window:
            paper_pts = paper_pts[-window:]
        if len(bt_pts) > window * 2:
            bt_pts = bt_pts[-(window * 2) :]

    diag = _span_diag(paper_pts, bt_pts)

    if scope_key == "strategy" and len(paper_pts) < _MIN_ALIGN + 1:
        return {
            "ok": False,
            "status": "unavailable",
            "reason": "no_strategy_equity",
            "scope": scope_key,
            "corr": None,
            "tracking_error_pct": None,
            "aligned_days": 0,
            "paper_days": len(paper_pts),
            "backtest_days": len(bt_pts),
            "need_days": _MIN_ALIGN + 1,
            "diagnosis": diag,
            "paper_span": diag["paper_span"],
            "backtest_span": diag["backtest_span"],
            "note": "策略 scope 无足够 equity_strategy 日序列。",
        }

    if len(paper_pts) < _MIN_ALIGN + 1 or len(bt_pts) < _MIN_ALIGN + 1:
        return {
            "ok": False,
            "status": "unavailable",
            "reason": "curves_too_short",
            "scope": scope_key,
            "corr": None,
            "tracking_error_pct": None,
            "aligned_days": 0,
            "paper_days": len(paper_pts),
            "backtest_days": len(bt_pts),
            "need_days": _MIN_ALIGN + 1,
            "diagnosis": diag,
            "paper_span": diag["paper_span"],
            "backtest_span": diag["backtest_span"],
            "note": (
                f"纸面按日净值 {len(paper_pts)} 日、回测曲线 {len(bt_pts)} 日；"
                f"至少各需 {_MIN_ALIGN + 1} 日。请每日 paper_daily，并在回溯页跑组合回测落盘曲线。"
            ),
        }

    bt_map = {d: e for d, e in bt_pts}
    common_dates = [d for d, _ in paper_pts if d in bt_map]
    if len(common_dates) < _MIN_ALIGN + 1:
        return {
            "ok": False,
            "status": "unavailable",
            "reason": "no_date_overlap",
            "scope": scope_key,
            "corr": None,
            "tracking_error_pct": None,
            "aligned_days": len(common_dates),
            "paper_days": len(paper_pts),
            "backtest_days": len(bt_pts),
            "need_days": _MIN_ALIGN + 1,
            "diagnosis": diag,
            "paper_span": diag["paper_span"],
            "backtest_span": diag["backtest_span"],
            "note": (
                f"同日交集仅 {len(common_dates)} 日（需≥{_MIN_ALIGN + 1}）。"
                f"纸面 {diag['paper_span']}；回测 {diag['backtest_span']}。"
                "请把组合回测 lookback 拉到覆盖纸面日期，或依赖 save_last_backtest_curve(align_to_paper)。"
            ),
        }

    pmap = {d: e for d, e in paper_pts}
    paper_eq = [pmap[d] for d in common_dates]
    bt_eq = [bt_map[d] for d in common_dates]
    # normalize to start=1
    p0, b0 = paper_eq[0], bt_eq[0]
    paper_n = [e / p0 for e in paper_eq]
    bt_n = [e / b0 for e in bt_eq]
    paper_rets = period_returns(paper_n)
    bt_rets = period_returns(bt_n)
    corr = pearson(paper_rets, bt_rets)
    te = tracking_error_pct(paper_rets, bt_rets)
    return {
        "ok": corr is not None or te is not None,
        "status": "ok" if (corr is not None or te is not None) else "unavailable",
        "reason": None,
        "scope": scope_key,
        "corr": corr,
        "tracking_error_pct": te,
        "aligned_days": len(common_dates),
        "window": window,
        "first_date": common_dates[0],
        "last_date": common_dates[-1],
        "diagnosis": diag,
        "paper_span": diag["paper_span"],
        "backtest_span": diag["backtest_span"],
        "note": "同日归一化权益收益相关 / 年化跟踪误差(%)；非实盘 Realization。",
    }


def summarize_risk_blocks(
    operation_log: Sequence[dict],
    *,
    window: int = 200,
) -> Dict[str, Any]:
    """R3.2 · 拦截流水按原因码 / 日 / 周汇总；有 outcome 标注时算有效率。"""
    from collections import defaultdict

    from core.risk.exposure import classify_block_message

    logs = list(operation_log or [])[-max(1, int(window or 200)) :]
    blocks = [e for e in logs if (e or {}).get("type") == "risk_block"]
    by_reason: Dict[str, int] = {}
    by_day: Dict[str, int] = defaultdict(int)
    by_week: Dict[str, int] = defaultdict(int)
    labeled_tp = 0
    labeled_fp = 0
    labeled = 0

    def _ts_day_week(entry: dict) -> Tuple[str, str]:
        raw = (
            (entry or {}).get("ts")
            or (entry or {}).get("time")
            or ((entry or {}).get("meta") or {}).get("ts")
            or ""
        )
        raw = str(raw).strip()
        day = "unknown"
        week = "unknown"
        if raw:
            try:
                # ISO or date prefix
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00")[:19])
                day = dt.strftime("%Y-%m-%d")
                iso = dt.isocalendar()
                week = f"{iso[0]}-W{iso[1]:02d}"
            except Exception:
                day = raw[:10] if len(raw) >= 10 else raw
                week = day
        return day, week

    for e in blocks:
        meta = (e or {}).get("meta") or {}
        codes: List[str] = []
        if isinstance(meta.get("codes"), list):
            codes = [str(c) for c in meta["codes"] if c]
        elif meta.get("code") or meta.get("reason") or meta.get("block_code"):
            codes = [
                str(meta.get("code") or meta.get("reason") or meta.get("block_code"))
            ]
        elif isinstance(meta.get("block_items"), list):
            for it in meta["block_items"]:
                if isinstance(it, dict) and it.get("code"):
                    codes.append(str(it["code"]))
        if not codes:
            detail = str((e or {}).get("detail") or "")
            # 多条「；」分隔时逐条分类
            parts = [p for p in detail.split("；") if p.strip()] or [detail]
            for p in parts:
                code, _ = classify_block_message(p)
                codes.append(code)
        if not codes:
            codes = ["unspecified"]
        for code in codes:
            by_reason[code] = by_reason.get(code, 0) + 1

        day, week = _ts_day_week(e or {})
        by_day[day] += 1
        by_week[week] += 1

        outcome = str(meta.get("outcome") or meta.get("label") or "").strip().lower()
        if outcome in ("true_positive", "effective", "tp", "true"):
            labeled += 1
            labeled_tp += 1
        elif outcome in ("false_positive", "false_block", "fp", "false"):
            labeled += 1
            labeled_fp += 1

    eff = None
    false_rate = None
    status = "partial"
    note = "有硬拦流水；有效率需 meta.outcome=true_positive|false_positive 标注后计算。"
    if labeled > 0:
        eff = round(labeled_tp / labeled, 4)
        false_rate = round(labeled_fp / labeled, 4)
        status = "ok"
        note = f"已标注 {labeled}/{len(blocks)} 条；有效率=真拦/已标注。"
    elif blocks:
        note = (
            f"拦截 {len(blocks)} 条 · 按码汇总；"
            "写入 risk_block.meta.outcome 后可算有效率/误拦率。"
        )

    # 日/周序列（最近）
    day_series = [
        {"date": d, "count": by_day[d]}
        for d in sorted(by_day.keys(), reverse=True)
        if d != "unknown"
    ][:14]
    week_series = [
        {"week": w, "count": by_week[w]}
        for w in sorted(by_week.keys(), reverse=True)
        if w != "unknown"
    ][:8]

    return {
        "ok": True,
        "status": status,
        "block_count": len(blocks),
        "log_window": len(logs),
        "by_reason": by_reason,
        "by_day": day_series,
        "by_week": week_series,
        "labeled_count": labeled,
        "effectiveness_rate": eff,
        "false_block_rate": false_rate,
        "note": note,
    }


def merge_north_star_into_metrics(
    paper: Optional[dict],
    metrics: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """把北极星数字并入 monitor_metrics，并返回完整 report。"""
    report = build_north_star_report(paper or {})
    base = dict(metrics or {})
    pr = report.get("paper_risk") or {}
    rz = report.get("realization") or {}
    if pr.get("rolling_sharpe") is not None:
        base["rolling_sharpe"] = pr.get("rolling_sharpe")
    if pr.get("calmar") is not None:
        base["calmar"] = pr.get("calmar")
    if rz.get("corr") is not None:
        base["realization_corr"] = rz.get("corr")
    if rz.get("tracking_error_pct") is not None:
        base["tracking_error_pct"] = rz.get("tracking_error_pct")
    return base, report


def build_north_star_report(
    paper: Optional[dict] = None,
    *,
    backtest_curve: Optional[Sequence[dict]] = None,
    window: int = 60,
) -> Dict[str, Any]:
    """聚合北极星包，供 API / 日更 / ops_report。"""
    paper = paper or {}
    snaps = paper.get("snapshots") or []
    paper_risk = compute_paper_risk_metrics(snaps, window=window, scope="all")
    paper_risk_strategy = compute_paper_risk_metrics(
        snaps, window=window, scope="strategy"
    )

    curve = list(backtest_curve) if backtest_curve is not None else None
    bt_pack = None
    if curve is None:
        bt_pack = load_last_backtest_curve()
        curve = bt_pack.get("curve") or []
    realization = compute_realization(snaps, curve or [], window=window, scope="all")
    realization_strategy = compute_realization(
        snaps, curve or [], window=window, scope="strategy"
    )

    ttm = compute_ttm_metrics()
    risk_eff = summarize_risk_blocks(paper.get("operation_log") or [])
    align_meta = ((bt_pack or {}).get("meta") or {}).get("align") if bt_pack else None

    return {
        "ok": True,
        "computed_at": datetime.now().isoformat(timespec="seconds"),
        "paper_risk": paper_risk,
        "paper_risk_strategy": paper_risk_strategy,
        "realization": realization,
        "realization_strategy": realization_strategy,
        "scopes": {
            "all": {"paper_risk": paper_risk, "realization": realization},
            "strategy": {
                "paper_risk": paper_risk_strategy,
                "realization": realization_strategy,
            },
        },
        "ttm": ttm,
        "risk_blocks": risk_eff,
        "backtest_curve_meta": {
            "saved_at": (bt_pack or {}).get("saved_at") if bt_pack else None,
            "point_count": len(curve or []),
            "empty": not bool(curve),
            "align": align_meta,
        },
        "note": (
            "E 轨：全账户 vs 策略 scope 分列；缺样本为 unavailable；"
            "拦截有效率需 outcome 标注。"
        ),
    }
