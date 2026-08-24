"""做 T 回测可视化数据聚合（供 Web 图表）。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence

SKIP_CAT_LABELS: Dict[str, str] = {
    "missing_scores": "缺ŷ快照",
    "y_tau_flat": "y_τ横盘",
    "y_trade_weak": "y_trade不足",
    "conflict": "先验冲突",
    "amplitude": "振幅不足",
    "lot_size": "手数不足",
    "path": "路径否决",
    "trigger_miss": "未触达",
    "other": "其它",
}

SKIP_CAT_COLORS: Dict[str, str] = {
    "missing_scores": "#94a3b8",
    "y_tau_flat": "#f59e0b",
    "y_trade_weak": "#fb923c",
    "conflict": "#ef4444",
    "amplitude": "#64748b",
    "lot_size": "#a78bfa",
    "path": "#6366f1",
    "trigger_miss": "#cbd5e1",
    "other": "#d1d5db",
}


def classify_t0_skip_reason(reason: Optional[str]) -> str:
    r = str(reason or "")
    if "缺" in r and ("y_" in r or "快照" in r):
        return "missing_scores"
    if "y_trade" in r:
        return "y_trade_weak"
    if "冲突" in r:
        return "conflict"
    if "|y_τ|" in r or ("y_τ" in r and "横盘" in r):
        return "y_tau_flat"
    if "振幅" in r:
        return "amplitude"
    if "不足1手" in r or ("手" in r and "不足" in r):
        return "lot_size"
    if "路径" in r or "veto" in r.lower():
        return "path"
    if "未触及" in r or "未触" in r:
        return "trigger_miss"
    return "other"


def _f(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return v


def extract_scores(day: dict) -> Dict[str, Optional[float]]:
    feats = day.get("direction_features") if isinstance(day.get("direction_features"), dict) else {}
    raw = day.get("scores") if isinstance(day.get("scores"), dict) else {}
    y_tau = _f(day.get("direction_score"))
    if y_tau is None:
        y_tau = _f(feats.get("y_tau"))
    if y_tau is None:
        y_tau = _f(raw.get("y_tau"))
    return {
        "y_tau": y_tau,
        "y_eod": _f(feats.get("y_eod")) if feats else _f(raw.get("y_eod")),
        "y_trade": _f(feats.get("y_trade")) if feats else _f(raw.get("y_trade")),
        "y_on": _f(feats.get("y_on")) if feats else _f(raw.get("y_on")),
    }


def extract_y_tau(day: dict) -> Optional[float]:
    return extract_scores(day).get("y_tau")


def is_traded_t0_day(day: dict) -> bool:
    if day.get("skipped"):
        return False
    return (
        int(day.get("sold_qty") or 0) > 0
        or int(day.get("bought_qty") or 0) > 0
        or abs(float(day.get("pnl") or 0)) > 1e-9
        or abs(float(day.get("exposure_pnl") or 0)) > 1e-9
    )


def _cover_completed(day: dict) -> Optional[bool]:
    sold = int(day.get("sold_qty") or 0)
    covered = int(day.get("covered_qty") or 0)
    bought = int(day.get("bought_qty") or 0)
    sold_back = int(day.get("sold_back_qty") or 0)
    direction = str(day.get("direction") or day.get("direction_used") or "")
    if direction == "long_t" and sold > 0:
        return covered >= sold
    if direction == "reverse_t" and bought > 0:
        return sold_back >= bought
    if sold > 0 or bought > 0:
        return (sold > 0 and covered >= sold) or (bought > 0 and sold_back >= bought)
    return None


def _y_tau_bucket(val: float) -> str:
    if val <= -0.5:
        return "≤-0.5"
    if val <= -0.25:
        return "-0.5~-0.25"
    if val < -0.05:
        return "-0.25~-0.05"
    if val <= 0.05:
        return "≈0"
    if val < 0.25:
        return "+0.05~0.25"
    if val < 0.5:
        return "+0.25~0.5"
    return "≥+0.5"


def _bucket_order() -> List[str]:
    return [
        "≤-0.5",
        "-0.5~-0.25",
        "-0.25~-0.05",
        "≈0",
        "+0.05~0.25",
        "+0.25~0.5",
        "≥+0.5",
    ]


def _summary_from_counts(
    *,
    traded_n: int,
    skip_n: int,
    signal_skip_n: int,
    cover_n: int,
    score_seen: int,
    score_total: int,
    rules: Optional[dict],
) -> Dict[str, Any]:
    total = traded_n + skip_n
    cfg = rules if isinstance(rules, dict) else {}
    return {
        "total_days": total,
        "trade_count": traded_n,
        "skip_count": skip_n,
        "signal_skip_count": signal_skip_n,
        "participate_rate_pct": round(traded_n / total * 100.0, 2) if total else None,
        "signal_skip_rate_pct": round(signal_skip_n / total * 100.0, 2) if total else None,
        "cover_rate_pct": round(cover_n / traded_n * 100.0, 2) if traded_n else None,
        "score_coverage_pct": round(score_seen / score_total * 100.0, 2) if score_total else None,
        "y_tau_enter": _f(cfg.get("y_tau_enter")) or 0.25,
        "y_trade_floor": _f(cfg.get("y_trade_floor")) or -0.15,
    }


def build_t0_viz_payload(
    days: Sequence[dict],
    *,
    stock_code: str = "",
    stock_name: str = "",
    rules: Optional[dict] = None,
) -> Dict[str, Any]:
    """从单日 walk 全量 days 生成 viz 块。"""
    skip_counts: Dict[str, int] = defaultdict(int)
    daily: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"traded": 0, "signal_skip": 0, "other_skip": 0}
    )
    y_tau_hist: Dict[str, int] = defaultdict(int)
    trade_points: List[Dict[str, Any]] = []
    y_tau_scatter: List[Dict[str, Any]] = []
    direction_split = {"long_t": 0, "reverse_t": 0}
    pnl_by_direction = {"long_t": 0.0, "reverse_t": 0.0}
    traded_n = 0
    skip_n = 0
    signal_skip_n = 0
    cover_n = 0
    score_seen = 0
    score_total = 0
    y_tau_traded: List[float] = []
    y_tau_skipped: List[float] = []

    for d in days or []:
        if not isinstance(d, dict):
            continue
        date = str(d.get("date") or "")[:10]
        if not date:
            continue
        code = str(d.get("stock_code") or stock_code or "")
        name = str(d.get("stock_name") or stock_name or "")
        sc = extract_scores(d)

        if is_traded_t0_day(d):
            traded_n += 1
            daily[date]["traded"] += 1
            pnl = round(float(d.get("pnl") or 0) + float(d.get("exposure_pnl") or 0), 2)
            direction = str(d.get("direction") or d.get("direction_used") or "")
            if direction in direction_split:
                direction_split[direction] += 1
            if direction in pnl_by_direction:
                pnl_by_direction[direction] = round(pnl_by_direction[direction] + pnl, 2)
            covered = _cover_completed(d)
            if covered:
                cover_n += 1
            if sc.get("y_tau") is not None:
                score_seen += 1
                y_tau_traded.append(float(sc["y_tau"]))
            score_total += 1
            cp = d.get("cover_policy") if isinstance(d.get("cover_policy"), dict) else {}
            trade_points.append(
                {
                    "date": date,
                    "pnl": pnl,
                    "direction": direction,
                    "stock_code": code,
                    "stock_name": name,
                    "y_tau": sc.get("y_tau"),
                    "y_eod": sc.get("y_eod"),
                    "y_trade": sc.get("y_trade"),
                    "y_on": sc.get("y_on"),
                    "sold_qty": int(d.get("sold_qty") or 0),
                    "covered_qty": int(d.get("covered_qty") or 0),
                    "bought_qty": int(d.get("bought_qty") or 0),
                    "sold_back_qty": int(d.get("sold_back_qty") or 0),
                    "cover_completed": covered,
                    "must_cover": cp.get("must_cover") if cp else None,
                    "cover_reason": cp.get("reason") if cp else None,
                    "minute_path": bool(d.get("minute_path")),
                    "path_mode": d.get("path_mode"),
                }
            )
            if sc.get("y_tau") is not None:
                y_tau_scatter.append(
                    {
                        "date": date,
                        "y_tau": sc["y_tau"],
                        "outcome": "traded",
                        "direction": direction,
                        "stock_code": code,
                        "stock_name": name,
                    }
                )
            continue

        if not d.get("skipped"):
            continue
        skip_n += 1
        reason = str(d.get("reason") or d.get("direction_reason") or "跳过")
        cat = str(d.get("skip_category") or classify_t0_skip_reason(reason))
        skip_counts[cat] += 1
        if d.get("signal_skip"):
            signal_skip_n += 1
            daily[date]["signal_skip"] += 1
        else:
            daily[date]["other_skip"] += 1
        yt = sc.get("y_tau")
        if yt is not None:
            score_seen += 1
            if d.get("signal_skip"):
                y_tau_skipped.append(float(yt))
                y_tau_hist[_y_tau_bucket(float(yt))] += 1
                y_tau_scatter.append(
                    {
                        "date": date,
                        "y_tau": yt,
                        "outcome": "signal_skip",
                        "direction": None,
                        "stock_code": code,
                        "stock_name": name,
                        "skip_category": cat,
                    }
                )
        if d.get("signal_skip") or cat in {"missing_scores", "y_tau_flat", "conflict", "y_trade_weak"}:
            score_total += 1

    trade_points.sort(key=lambda x: (str(x.get("date") or ""), str(x.get("stock_code") or "")))
    cum = 0.0
    cumulative: List[Dict[str, Any]] = []
    for tp in trade_points:
        cum = round(cum + float(tp.get("pnl") or 0), 2)
        row = dict(tp)
        row["cum_pnl"] = cum
        cumulative.append(row)

    y_tau_scatter.sort(key=lambda x: str(x.get("date") or ""))
    if len(y_tau_scatter) > 120:
        y_tau_scatter = y_tau_scatter[-120:]

    activity = [
        {
            "date": dt,
            "traded": v["traded"],
            "signal_skip": v["signal_skip"],
            "other_skip": v["other_skip"],
            "total": v["traded"] + v["signal_skip"] + v["other_skip"],
        }
        for dt, v in sorted(daily.items())
    ]

    skip_total = max(1, sum(skip_counts.values()))
    skip_categories = [
        {
            "id": cid,
            "label": SKIP_CAT_LABELS.get(cid, cid),
            "count": int(cnt),
            "pct": round(int(cnt) / skip_total * 100.0, 1),
            "color": SKIP_CAT_COLORS.get(cid, "#94a3b8"),
        }
        for cid, cnt in sorted(skip_counts.items(), key=lambda x: (-x[1], x[0]))
    ]

    y_tau_buckets = [
        {"bin": b, "count": int(y_tau_hist[b])}
        for b in _bucket_order()
        if y_tau_hist.get(b)
    ]

    stock_contrib: List[Dict[str, Any]] = []
    if stock_code or traded_n or skip_n:
        pnl_sum = round(sum(float(tp.get("pnl") or 0) for tp in trade_points), 2)
        total = traded_n + skip_n
        stock_contrib.append(
            {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "pnl": pnl_sum,
                "trade_days": traded_n,
                "skip_days": skip_n,
                "long_days": direction_split.get("long_t") or 0,
                "reverse_days": direction_split.get("reverse_t") or 0,
                "participate_rate_pct": round(traded_n / total * 100.0, 2) if total else None,
                "score_coverage_pct": round(score_seen / score_total * 100.0, 2) if score_total else None,
            }
        )

    summary = _summary_from_counts(
        traded_n=traded_n,
        skip_n=skip_n,
        signal_skip_n=signal_skip_n,
        cover_n=cover_n,
        score_seen=score_seen,
        score_total=score_total,
        rules=rules,
    )
    summary["score_seen"] = score_seen
    summary["score_total"] = score_total
    if y_tau_traded:
        summary["avg_y_tau_traded"] = round(sum(y_tau_traded) / len(y_tau_traded), 3)
    if y_tau_skipped:
        summary["avg_y_tau_signal_skip"] = round(sum(y_tau_skipped) / len(y_tau_skipped), 3)

    return {
        "skip_categories": skip_categories,
        "cumulative_pnl": cumulative,
        "daily_activity": activity,
        "y_tau_buckets": y_tau_buckets,
        "y_tau_scatter": y_tau_scatter,
        "stock_contrib": stock_contrib,
        "direction_split": direction_split,
        "pnl_by_direction": {
            k: round(v, 2) for k, v in pnl_by_direction.items()
        },
        "trade_count": traded_n,
        "skip_count": skip_n,
        "summary": summary,
    }


def merge_t0_viz_payloads(
    payloads: Sequence[Optional[dict]],
    *,
    compare: Optional[dict] = None,
    rules: Optional[dict] = None,
) -> Dict[str, Any]:
    """多持仓回测合并各票 viz。"""
    skip_counts: Dict[str, int] = defaultdict(int)
    daily: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"traded": 0, "signal_skip": 0, "other_skip": 0}
    )
    y_tau_hist: Dict[str, int] = defaultdict(int)
    trade_points: List[Dict[str, Any]] = []
    y_tau_scatter: List[Dict[str, Any]] = []
    stock_contrib: List[Dict[str, Any]] = []
    direction_split = {"long_t": 0, "reverse_t": 0}
    pnl_by_direction = {"long_t": 0.0, "reverse_t": 0.0}
    traded_n = 0
    skip_n = 0
    signal_skip_n = 0
    cover_n = 0
    score_seen = 0
    score_total = 0
    y_tau_traded: List[float] = []
    y_tau_skipped: List[float] = []

    for p in payloads or []:
        if not isinstance(p, dict):
            continue
        for sc in p.get("skip_categories") or []:
            if isinstance(sc, dict):
                skip_counts[str(sc.get("id") or "other")] += int(sc.get("count") or 0)
        for row in p.get("daily_activity") or []:
            if not isinstance(row, dict):
                continue
            dt = str(row.get("date") or "")[:10]
            if not dt:
                continue
            daily[dt]["traded"] += int(row.get("traded") or 0)
            daily[dt]["signal_skip"] += int(row.get("signal_skip") or 0)
            daily[dt]["other_skip"] += int(row.get("other_skip") or 0)
        for row in p.get("y_tau_buckets") or []:
            if isinstance(row, dict) and row.get("bin"):
                y_tau_hist[str(row["bin"])] += int(row.get("count") or 0)
        for tp in p.get("cumulative_pnl") or []:
            if isinstance(tp, dict) and tp.get("date"):
                trade_points.append(dict(tp))
                if tp.get("cover_completed"):
                    cover_n += 1
                yt = _f(tp.get("y_tau"))
                if yt is not None:
                    score_seen += 1
                    y_tau_traded.append(yt)
                score_total += 1
        for pt in p.get("y_tau_scatter") or []:
            if isinstance(pt, dict):
                y_tau_scatter.append(dict(pt))
                if pt.get("outcome") == "signal_skip" and _f(pt.get("y_tau")) is not None:
                    y_tau_skipped.append(float(pt["y_tau"]))
        for st in p.get("stock_contrib") or []:
            if isinstance(st, dict):
                stock_contrib.append(dict(st))
        ds = p.get("direction_split") or {}
        direction_split["long_t"] += int(ds.get("long_t") or 0)
        direction_split["reverse_t"] += int(ds.get("reverse_t") or 0)
        pbd = p.get("pnl_by_direction") or {}
        pnl_by_direction["long_t"] += float(pbd.get("long_t") or 0)
        pnl_by_direction["reverse_t"] += float(pbd.get("reverse_t") or 0)
        traded_n += int(p.get("trade_count") or 0)
        skip_n += int(p.get("skip_count") or 0)
        sm = p.get("summary") or {}
        signal_skip_n += int(sm.get("signal_skip_count") or 0)
        score_seen += int(sm.get("score_seen") or 0)
        score_total += int(sm.get("score_total") or 0)

    trade_points.sort(key=lambda x: (str(x.get("date") or ""), str(x.get("stock_code") or "")))
    cum = 0.0
    cumulative: List[Dict[str, Any]] = []
    for tp in trade_points:
        cum = round(cum + float(tp.get("pnl") or 0), 2)
        row = dict(tp)
        row["cum_pnl"] = cum
        cumulative.append(row)

    y_tau_scatter.sort(key=lambda x: str(x.get("date") or ""))
    if len(y_tau_scatter) > 160:
        y_tau_scatter = y_tau_scatter[-160:]

    skip_total = max(1, sum(skip_counts.values()))
    summary = _summary_from_counts(
        traded_n=traded_n,
        skip_n=skip_n,
        signal_skip_n=signal_skip_n,
        cover_n=cover_n,
        score_seen=score_seen,
        score_total=max(score_total, traded_n + signal_skip_n),
        rules=rules,
    )
    if y_tau_traded:
        summary["avg_y_tau_traded"] = round(sum(y_tau_traded) / len(y_tau_traded), 3)
    if y_tau_skipped:
        summary["avg_y_tau_signal_skip"] = round(sum(y_tau_skipped) / len(y_tau_skipped), 3)
    summary["score_seen"] = score_seen
    summary["score_total"] = max(score_total, traded_n + signal_skip_n)

    stock_contrib.sort(key=lambda x: -abs(float(x.get("pnl") or 0)))

    out: Dict[str, Any] = {
        "skip_categories": [
            {
                "id": cid,
                "label": SKIP_CAT_LABELS.get(cid, cid),
                "count": int(cnt),
                "pct": round(int(cnt) / skip_total * 100.0, 1),
                "color": SKIP_CAT_COLORS.get(cid, "#94a3b8"),
            }
            for cid, cnt in sorted(skip_counts.items(), key=lambda x: (-x[1], x[0]))
        ],
        "cumulative_pnl": cumulative,
        "daily_activity": [
            {
                "date": dt,
                "traded": v["traded"],
                "signal_skip": v["signal_skip"],
                "other_skip": v["other_skip"],
                "total": v["traded"] + v["signal_skip"] + v["other_skip"],
            }
            for dt, v in sorted(daily.items())
        ],
        "y_tau_buckets": [
            {"bin": b, "count": int(y_tau_hist[b])}
            for b in _bucket_order()
            if y_tau_hist.get(b)
        ],
        "y_tau_scatter": y_tau_scatter,
        "stock_contrib": stock_contrib,
        "direction_split": direction_split,
        "pnl_by_direction": {k: round(v, 2) for k, v in pnl_by_direction.items()},
        "trade_count": traded_n,
        "skip_count": skip_n,
        "summary": summary,
    }
    if isinstance(compare, dict) and compare:
        out["compare"] = compare
    return out


def attach_compare_to_viz(report: dict) -> None:
    """把回测报告中的对照指标写入 viz.summary / viz.compare。"""
    viz = report.get("viz")
    if not isinstance(viz, dict):
        return
    opt = report.get("optimistic_compare") or {}
    if opt.get("t0_pnl_total") is not None:
        viz["compare"] = {
            "actual_pnl": report.get("t0_pnl_total"),
            "optimistic_pnl": opt.get("t0_pnl_total"),
            "delta_pnl": opt.get("delta_pnl"),
            "delta_ratio_pct": opt.get("delta_pnl_ratio_pct") or report.get("optimistic_delta_ratio_pct"),
        }
    daily = report.get("daily_compare") or {}
    if daily.get("t0_pnl_total") is not None:
        viz["daily_compare"] = {
            "actual_pnl": report.get("t0_pnl_total"),
            "daily_veto_pnl": daily.get("t0_pnl_total"),
            "delta_pnl": daily.get("delta_pnl"),
            "path_mode": daily.get("path_mode"),
        }
    sm = dict(viz.get("summary") or {})
    for k in (
        "participate_rate_pct",
        "cover_rate_pct",
        "t0_pnl_with_exposure",
        "pnl_vs_hold_mv_pct",
        "avg_pnl_per_trade_day",
    ):
        if report.get(k) is not None:
            sm[k] = report.get(k)
    viz["summary"] = sm
