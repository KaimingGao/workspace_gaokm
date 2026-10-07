"""产品北极星二级指标（R0）：纸面夏普/卡玛 · 回测–纸面拟合 · TTM · 拦截流水。

权威计算在 core；Web / localStorage 只展示，不作为真相源。
缺样本时字段为 None 且 status=unavailable，禁止编造。
"""


import logging

logger = logging.getLogger(__name__)
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
    "north_star_cache_usable",
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
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
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
    bex = report.get("benchmark_excess") or {}
    if bex.get("ok"):
        base["benchmark_excess_ir"] = bex.get("ann_ir")
        base["benchmark_excess_pct"] = bex.get("total_excess_approx_pct")
    legs = report.get("alpha_beta_legs") or {}
    if legs.get("alpha_leg_approx_pct") is not None:
        base["alpha_leg_approx_pct"] = legs.get("alpha_leg_approx_pct")
    if legs.get("beta_leg_approx_pct") is not None:
        base["beta_leg_approx_pct"] = legs.get("beta_leg_approx_pct")
    return base, report


def north_star_cache_usable(paper: Optional[dict], cached: Any) -> bool:
    """回零清空曲线后，旧 ``last_north_star`` 不能再当夏普/卡玛。

    无 ``sample_count`` / ``computed_at`` 的旧缓存仍视为可用，避免短样本单测被误判。
    """
    if not isinstance(cached, dict) or cached.get("ok") is False:
        return False
    snaps = []
    for snap in (paper or {}).get("snapshots") or []:
        if not isinstance(snap, dict):
            continue
        eq = _safe_float(snap.get("equity"))
        if eq is not None and eq > 0:
            snaps.append(snap)
    pr = cached.get("paper_risk") if isinstance(cached.get("paper_risk"), dict) else {}
    sample = pr.get("sample_count")
    try:
        sample_n = int(sample) if sample is not None else None
    except (TypeError, ValueError):
        sample_n = None
    if sample_n is not None and sample_n > len(snaps):
        return False
    computed = str(cached.get("computed_at") or "").strip()
    first_ts = ""
    for snap in snaps:
        raw = str(snap.get("ts") or snap.get("date") or "").strip()
        if raw:
            first_ts = raw
            break
    if computed and first_ts and computed < first_ts[:19]:
        return False
    return True


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

    # ===== R1 增强：滚动拟合 / 三项乘积 / TTM 瓶颈 / 拦截审计 / 退化告警 =====
    r1: Dict[str, Any] = {}
    try:
        from core.backtest_curve_store import _curve_points, _paper_daily_equities
        from core.north_star_pro import (
            composite_north_star_score,
            quantify_fit_gap_attribution,
            rolling_realization,
        )
        from core.risk.block_audit import summarize_block_audit
        from core.risk_metrics import period_returns
        from core.ttm_stages import summarize_ttm_stages

        # 拟合度趋势 + 缺口归因
        roll = rolling_realization(snaps, curve or [], window=max(15, window // 4), step=max(5, window // 12))
        gap_attr: Dict[str, Any] = {}
        paper_pts = _paper_daily_equities(snaps)
        bt_pts = _curve_points(list(curve or []))
        pmap = {d: e for d, e in paper_pts}
        bmap = {d: e for d, e in bt_pts}
        common = sorted(set(pmap) & set(bmap))
        if len(common) >= 5:
            pe0, be0 = pmap[common[0]], bmap[common[0]]
            if pe0 and be0 and pe0 > 0 and be0 > 0:
                p_n = [pmap[d] / pe0 for d in common]
                b_n = [bmap[d] / be0 for d in common]
                prs = list(period_returns(p_n))
                brs = list(period_returns(b_n))
                if len(prs) >= 3:
                    gap_attr = quantify_fit_gap_attribution(prs, brs, cost_pct=0.05)
        r1["fit_roll"] = roll
        r1["fit_gap_attribution"] = gap_attr

        # 三项乘积综合分
        sharpe_r0 = (paper_risk or {}).get("rolling_sharpe")
        corr_r0 = (realization or {}).get("corr")
        te_r0 = (realization or {}).get("tracking_error_pct")
        ttm_r0 = (ttm or {}).get("median_idea_to_paper_hours")
        composite = composite_north_star_score(
            sharpe=sharpe_r0, ttm_hours=ttm_r0, corr=corr_r0, te=te_r0,
        )
        r1["composite"] = composite

        # TTM 阶段瓶颈
        try:
            ttm_stage = summarize_ttm_stages()
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            ttm_stage = None
        r1["ttm_stage"] = ttm_stage

        # 拦截复核审计
        op_log = list(paper.get("operation_log") or [])
        try:
            block_audit_summary = summarize_block_audit(op_log)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            block_audit_summary = None
        r1["block_audit"] = block_audit_summary

        # 退化告警（用 fit_roll 的 corr 趋势）
        corr_series = [t.get("corr") for t in roll.get("trend", []) if t.get("corr") is not None]
        # Sharpe 退化近似：用 snapshots 日收益的滚动夏普（粗粒度）
        sharpe_series: list = []
        if len(paper_pts) >= 15:
            from core.risk_metrics import rolling_sharpe as _rs
            eq_series = [e for _, e in paper_pts]
            if len(eq_series) >= 8:
                rs_raw = list(_rs([(e / eq_series[0] - 1) for e in eq_series], window=7))
                sharpe_series = [v for v in rs_raw if v is not None]
        # TTM 系列退化：从 events 构造的瓶颈时间序列（这里取 ttm_stage 的周吞吐近似）
        ttm_series: list = []
        if isinstance(ttm_stage, dict) and isinstance(ttm_stage.get("trend"), dict):
            tr = ttm_stage["trend"]
            if tr.get("recent_median_h") is not None and tr.get("baseline_median_h") is not None:
                ttm_series = [float(tr["baseline_median_h"]), float(tr["recent_median_h"])]
        try:
            from core.north_star_pro import north_star_degradation_report
            r1["degradation"] = north_star_degradation_report(sharpe_series, corr_series, ttm_series, window=3)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            r1["degradation"] = {"alerts": {}, "any_degrading": False, "degrading_dimensions": []}
    except Exception as _exc:
        logger.exception('unexpected error in build_north_star_report')
        r1["error"] = f"R1增强计算异常: {_exc}"

    # P0：纸面净值 vs 指数超额（α 腿诊断，只读）
    bex: Dict[str, Any] = {"ok": False, "reason": "not_computed"}
    legs: Optional[Dict[str, Any]] = None
    try:
        from core.alpha_excess import compute_benchmark_excess_pack, legs_summary
        from core.backtest_curve_store import _paper_daily_equities
        from core.ports.market import default_benchmark

        paper_pts = _paper_daily_equities(snaps)
        idx_code = str(default_benchmark("CN") or "sh000300")
        bex = compute_benchmark_excess_pack(
            paper_pts, index_code=idx_code, lookback=max(80, window + 40)
        )
        total_ret = None
        if len(paper_pts) >= 2:
            try:
                e0 = float(paper_pts[0][1])
                e1 = float(paper_pts[-1][1])
                if e0 > 0:
                    total_ret = (e1 / e0 - 1.0) * 100.0
            except (TypeError, ValueError, IndexError):
                total_ret = None
        legs = legs_summary(total_return_pct=total_ret, excess_pack=bex)
    except Exception as _exc:
        logger.exception('unexpected error in build_north_star_report')
        bex = {"ok": False, "reason": f"compute_failed:{_exc}"}
        legs = None

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
        "r1": r1,
        "benchmark_excess": bex,
        "alpha_beta_legs": legs,
        "note": (
            "E 轨：全账户 vs 策略 scope 分列；缺样本为 unavailable；"
            "拦截有效率需 outcome 标注。R1=拟合趋势/缺口归因/三项乘积/TTM瓶颈/拦截审计/退化告警。"
            "P0：benchmark_excess / alpha_beta_legs 为相对指数超额分账（只读）。"
        ),
    }
