"""产品北极星二级指标（R0）：纸面夏普/卡玛 · 回测–纸面拟合 · TTM · 拦截流水。

权威计算在 core；Web / localStorage 只展示，不作为真相源。
缺样本时字段为 None 且 status=unavailable，禁止编造。
"""

from __future__ import annotations

import json
import math
import os
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.paths import NORTH_STAR_LAST_BACKTEST_PATH, TTM_EVENTS_PATH

TTM_EVENT_IDEA = "idea_opened"
TTM_EVENT_BACKTEST = "backtest_ready"
TTM_EVENT_PAPER = "paper_rule_live"

_MIN_RETURNS = 5
_MIN_ALIGN = 5
_DEFAULT_ANN_FACTOR = 252.0


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_ts(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw))
        except (OSError, OverflowError, ValueError):
            return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            try:
                return datetime.strptime(s[:10], "%Y-%m-%d")
            except ValueError:
                return None
    return None


def equities_from_snapshots(snapshots: Sequence[dict]) -> List[Tuple[Optional[datetime], float]]:
    """[(ts, equity), ...] 按时间升序，跳过无效净值。"""
    rows: List[Tuple[Optional[datetime], float]] = []
    for snap in snapshots or []:
        eq = _safe_float((snap or {}).get("equity"))
        if eq is None or eq <= 0:
            continue
        rows.append((_parse_ts((snap or {}).get("ts") or (snap or {}).get("date")), eq))
    # 保持原序；多数 snapshot 已按时间追加
    return rows


def period_returns(equities: Sequence[float]) -> List[float]:
    out: List[float] = []
    for i in range(1, len(equities)):
        a, b = equities[i - 1], equities[i]
        if a and a > 0:
            out.append((b / a) - 1.0)
    return out


def max_drawdown_pct(equities: Sequence[float]) -> Optional[float]:
    if len(equities) < 2:
        return None
    peak = equities[0]
    max_dd = 0.0
    for e in equities:
        if e > peak:
            peak = e
        if peak > 0:
            dd = (peak - e) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    return round(max_dd, 4)


def annualization_factor_from_timestamps(
    timestamps: Sequence[Optional[datetime]],
    *,
    default: float = _DEFAULT_ANN_FACTOR,
) -> float:
    """由相邻快照中位间隔估计年化因子；失败则默认 252（按日）。"""
    deltas: List[float] = []
    prev: Optional[datetime] = None
    for ts in timestamps:
        if ts is None:
            prev = None
            continue
        if prev is not None:
            sec = (ts - prev).total_seconds()
            if sec > 60:  # 忽略同秒重复
                deltas.append(sec)
        prev = ts
    if len(deltas) < 2:
        return default
    med = statistics.median(deltas)
    if med <= 0:
        return default
    days = med / 86400.0
    if days <= 0:
        return default
    # 每 periods 年化；periods ≈ 365/days 对日历更稳，研究台用 252 交易日习惯
    return max(1.0, min(252.0 / max(days, 1.0 / 252.0), 252.0 * 24))


def rolling_sharpe(
    returns: Sequence[float],
    *,
    ann_factor: float = _DEFAULT_ANN_FACTOR,
) -> Optional[float]:
    if len(returns) < _MIN_RETURNS:
        return None
    try:
        mu = statistics.mean(returns)
        sigma = statistics.stdev(returns)
    except statistics.StatisticsError:
        return None
    if sigma <= 1e-12:
        return None
    return round((mu / sigma) * math.sqrt(ann_factor), 4)


def calmar_ratio(
    equities: Sequence[float],
    *,
    ann_factor: float = _DEFAULT_ANN_FACTOR,
    n_periods: Optional[int] = None,
) -> Optional[float]:
    if len(equities) < 2:
        return None
    dd = max_drawdown_pct(equities)
    if dd is None or dd <= 1e-9:
        return None
    total = equities[-1] / equities[0] - 1.0
    periods = n_periods if n_periods is not None else max(1, len(equities) - 1)
    years = periods / max(ann_factor, 1.0)
    if years <= 1e-9:
        return None
    # CAGR
    try:
        cagr = (1.0 + total) ** (1.0 / years) - 1.0
    except (OverflowError, ValueError):
        return None
    return round((cagr * 100.0) / dd, 4)


def compute_paper_risk_metrics(
    snapshots: Sequence[dict],
    *,
    window: int = 60,
    scope: str = "all",
) -> Dict[str, Any]:
    """R0.1 · 滚动纸面夏普 / 卡玛（基于 snapshots 净值）。

    scope:
      - all: 全账户 equity
      - strategy: 仅 equity_strategy（E0）；缺字段则 unavailable
    """
    scope_key = str(scope or "all").strip().lower()
    if scope_key in ("strategy", "strategy_only"):
        rows = equities_from_strategy_snapshots(snapshots)
        if not rows:
            has_any = bool(snapshots)
            return {
                "ok": False,
                "status": "unavailable",
                "reason": "no_strategy_equity" if has_any else "snapshots_too_short",
                "scope": "strategy",
                "sample_count": 0,
                "window": window,
                "rolling_sharpe": None,
                "calmar": None,
                "max_drawdown_pct": None,
                "note": (
                    "无策略仓净值序列：需持仓 origin=strategy 且日更快照写入 equity_strategy。"
                    if has_any
                    else "纸面 snapshots 为空。"
                ),
            }
        if window > 0 and len(rows) > window:
            rows = rows[-window:]
        equities = [e for _, e in rows]
        stamps = [t for t, _ in rows]
        label_note = "策略仓归因净值（equity_strategy）；与全账户分列。"
    else:
        rows = equities_from_snapshots(snapshots)
        if window > 0 and len(rows) > window:
            rows = rows[-window:]
        equities = [e for _, e in rows]
        stamps = [t for t, _ in rows]
        label_note = "纸面 snapshots 滚动风险调整收益；与回测 sharpe_approx 分列。"
        scope_key = "all"

    if len(equities) < _MIN_RETURNS + 1:
        return {
            "ok": False,
            "status": "unavailable",
            "reason": "snapshots_too_short",
            "scope": scope_key,
            "sample_count": len(equities),
            "window": window,
            "rolling_sharpe": None,
            "calmar": None,
            "max_drawdown_pct": max_drawdown_pct(equities) if equities else None,
        }
    ann = annualization_factor_from_timestamps(stamps)
    rets = period_returns(equities)
    sharpe = rolling_sharpe(rets, ann_factor=ann)
    calmar = calmar_ratio(equities, ann_factor=ann, n_periods=len(rets))
    return {
        "ok": sharpe is not None or calmar is not None,
        "status": "ok" if (sharpe is not None or calmar is not None) else "unavailable",
        "reason": None,
        "scope": scope_key,
        "sample_count": len(equities),
        "return_count": len(rets),
        "window": window,
        "ann_factor": round(ann, 2),
        "rolling_sharpe": sharpe,
        "calmar": calmar,
        "max_drawdown_pct": max_drawdown_pct(equities),
        "note": label_note,
    }


def equities_from_strategy_snapshots(
    snapshots: Sequence[dict],
) -> List[Tuple[Optional[datetime], float]]:
    """仅取带 equity_strategy 的快照。"""
    rows: List[Tuple[Optional[datetime], float]] = []
    for snap in snapshots or []:
        eq = _safe_float((snap or {}).get("equity_strategy"))
        if eq is None or eq <= 0:
            continue
        rows.append((_parse_ts((snap or {}).get("ts") or (snap or {}).get("date")), eq))
    return rows


def strategy_snapshots_as_equity(snapshots: Sequence[dict]) -> List[dict]:
    """把 equity_strategy 映射为标准 equity 字段，供 realization 复用。"""
    out: List[dict] = []
    for snap in snapshots or []:
        eq = _safe_float((snap or {}).get("equity_strategy"))
        if eq is None or eq <= 0:
            continue
        out.append({"ts": (snap or {}).get("ts"), "equity": eq})
    return out


def _curve_points(curve: Sequence[dict]) -> List[Tuple[str, float]]:
    """归一化为 (date_key, equity)。"""
    out: List[Tuple[str, float]] = []
    for pt in curve or []:
        eq = _safe_float((pt or {}).get("equity") or (pt or {}).get("value"))
        if eq is None or eq <= 0:
            continue
        raw = (pt or {}).get("date") or (pt or {}).get("ts") or ""
        ts = _parse_ts(raw)
        key = ts.strftime("%Y-%m-%d") if ts else str(raw)[:10]
        if len(key) < 8:
            continue
        out.append((key, eq))
    return out


def _paper_daily_equities(snapshots: Sequence[dict]) -> List[Tuple[str, float]]:
    """纸面快照按日取末日净值（同日多次盯市取最后一次）。"""
    by_day: Dict[str, float] = {}
    order: List[str] = []
    for snap in snapshots or []:
        eq = _safe_float((snap or {}).get("equity"))
        if eq is None or eq <= 0:
            continue
        ts = _parse_ts((snap or {}).get("ts"))
        if not ts:
            continue
        key = ts.strftime("%Y-%m-%d")
        if key not in by_day:
            order.append(key)
        by_day[key] = eq
    return [(k, by_day[k]) for k in order]


def pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if len(xs) < _MIN_ALIGN or len(xs) != len(ys):
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x <= 1e-12 or den_y <= 1e-12:
        return None
    return round(num / (den_x * den_y), 4)


def tracking_error_pct(
    paper_rets: Sequence[float],
    bt_rets: Sequence[float],
    *,
    ann_factor: float = _DEFAULT_ANN_FACTOR,
) -> Optional[float]:
    if len(paper_rets) < _MIN_ALIGN or len(paper_rets) != len(bt_rets):
        return None
    diffs = [p - b for p, b in zip(paper_rets, bt_rets)]
    try:
        sigma = statistics.stdev(diffs)
    except statistics.StatisticsError:
        return None
    return round(sigma * math.sqrt(ann_factor) * 100.0, 4)


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


def paper_date_span(
    snapshots: Optional[Sequence[dict]] = None,
) -> Optional[Tuple[str, str]]:
    """纸面快照日期跨度 (first, last)；不足则 None。"""
    pts = _paper_daily_equities(snapshots or [])
    if len(pts) < 2:
        return None
    return pts[0][0], pts[-1][0]


def save_last_backtest_curve(
    curve: Sequence[dict],
    *,
    path: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    align_to_paper: bool = True,
    paper_snapshots: Optional[Sequence[dict]] = None,
) -> str:
    """组合回测成功后落盘，供 realization 与 API 读取。

    E1：默认按纸面日期跨度裁剪曲线，降低 no_date_overlap。
    """
    p = path or NORTH_STAR_LAST_BACKTEST_PATH
    pts = _curve_points(curve)
    meta_out: Dict[str, Any] = dict(meta or {})
    align_meta: Dict[str, Any] = {"align_to_paper": bool(align_to_paper)}

    snaps = list(paper_snapshots) if paper_snapshots is not None else None
    if snaps is None and align_to_paper:
        try:
            from core.paper import load_paper
            from core.paths import PAPER_PATH

            snaps = list((load_paper(PAPER_PATH) or {}).get("snapshots") or [])
        except Exception:
            snaps = []

    span = paper_date_span(snaps) if align_to_paper and snaps else None
    if span and pts:
        lo, hi = span
        clipped = [(d, e) for d, e in pts if lo <= d <= hi]
        align_meta["align_from"] = lo
        align_meta["align_to"] = hi
        align_meta["paper_span"] = f"{lo}→{hi}"
        align_meta["curve_before"] = len(pts)
        if len(clipped) >= _MIN_ALIGN + 1:
            pts = clipped
            align_meta["aligned"] = True
            align_meta["curve_after"] = len(pts)
        else:
            # 交集不足：保留原曲线后段，但写诊断，便于 UI / fit_gap
            align_meta["aligned"] = False
            align_meta["curve_after"] = len(clipped)
            align_meta["warn"] = (
                f"纸面 span {lo}→{hi} 与回测交集仅 {len(clipped)} 日；"
                "已保留原曲线，请加长 lookback 覆盖纸面窗口。"
            )
    elif align_to_paper:
        align_meta["aligned"] = False
        align_meta["warn"] = "无纸面日期跨度，未裁剪回测曲线"

    meta_out["align"] = align_meta
    slim = [{"date": d, "equity": e} for d, e in pts[-240:]]
    payload = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "point_count": len(slim),
        "curve": slim,
        "meta": meta_out,
    }
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return p


def load_last_backtest_curve(path: Optional[str] = None) -> Dict[str, Any]:
    p = path or NORTH_STAR_LAST_BACKTEST_PATH
    if not os.path.isfile(p):
        return {"ok": False, "empty": True, "curve": [], "path": p}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {"ok": False, "empty": True, "error": str(e), "curve": [], "path": p}
    return {
        "ok": True,
        "empty": False,
        "path": p,
        "curve": data.get("curve") or [],
        "saved_at": data.get("saved_at"),
        "meta": data.get("meta") or {},
        "point_count": data.get("point_count"),
    }


def append_ttm_event(
    event: str,
    *,
    ref: str = "",
    meta: Optional[Dict[str, Any]] = None,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """R0.3 · 轻量 TTM 事件（JSONL）。"""
    p = path or TTM_EVENTS_PATH
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "event": str(event),
        "ref": ref or "",
        "meta": meta or {},
    }
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def load_ttm_events(*, limit: int = 200, path: Optional[str] = None) -> List[Dict[str, Any]]:
    p = path or TTM_EVENTS_PATH
    if not os.path.isfile(p):
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows[-max(1, int(limit or 200)) :]


def compute_ttm_metrics(
    events: Optional[Sequence[dict]] = None,
    *,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """Idea→回测、回测→纸面规则 的中位耗时（小时）。

    配对优先同 meta.cycle_id；无 cycle 时回退「下一时间戳」启发式。
    """
    evs = list(events) if events is not None else load_ttm_events(path=path)
    if not evs:
        return {
            "ok": False,
            "status": "unavailable",
            "reason": "no_events",
            "median_idea_to_backtest_hours": None,
            "median_backtest_to_paper_hours": None,
            "median_idea_to_paper_hours": None,
            "sample_count": 0,
            "note": "尚无 TTM 打点；回测成功 / promote 后写入。",
        }

    def _hours(a: datetime, b: datetime) -> float:
        return (b - a).total_seconds() / 3600.0

    def _cycle(e: dict) -> str:
        meta = e.get("meta") if isinstance(e.get("meta"), dict) else {}
        return str(meta.get("cycle_id") or "").strip()

    ideas = [(_parse_ts(e.get("ts")), e) for e in evs if e.get("event") == TTM_EVENT_IDEA]
    bts = [(_parse_ts(e.get("ts")), e) for e in evs if e.get("event") == TTM_EVENT_BACKTEST]
    papers = [(_parse_ts(e.get("ts")), e) for e in evs if e.get("event") == TTM_EVENT_PAPER]
    ideas = [(t, e) for t, e in ideas if t]
    bts = [(t, e) for t, e in bts if t]
    papers = [(t, e) for t, e in papers if t]

    def _pair_hours(
        starts: List[tuple],
        ends: List[tuple],
    ) -> List[float]:
        used_end_idx: set = set()
        out: List[float] = []
        # 1) 同 cycle_id
        end_by_cycle: Dict[str, List[tuple]] = {}
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
            out.append(_hours(ts, te))
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
            out.append(_hours(ts, te))
        return out

    i2b = _pair_hours(ideas, bts)
    b2p = _pair_hours(bts, papers)
    i2p = _pair_hours(ideas, papers)

    def _med(xs: List[float]) -> Optional[float]:
        if not xs:
            return None
        return round(statistics.median(xs), 2)

    med_i2b, med_b2p, med_i2p = _med(i2b), _med(b2p), _med(i2p)
    ok = any(v is not None for v in (med_i2b, med_b2p, med_i2p))
    cycle_n = sum(1 for _, e in ideas + bts + papers if _cycle(e))
    return {
        "ok": ok,
        "status": "ok" if ok else "unavailable",
        "reason": None if ok else "insufficient_pairs",
        "median_idea_to_backtest_hours": med_i2b,
        "median_backtest_to_paper_hours": med_b2p,
        "median_idea_to_paper_hours": med_i2p,
        "sample_count": len(evs),
        "pair_counts": {
            "idea_to_backtest": len(i2b),
            "backtest_to_paper": len(b2p),
            "idea_to_paper": len(i2p),
        },
        "cycle_tagged_events": cycle_n,
        "note": "中位小时；优先 meta.cycle_id 配对，否则下一时间戳。",
    }


def summarize_risk_blocks(
    operation_log: Sequence[dict],
    *,
    window: int = 200,
) -> Dict[str, Any]:
    """R3.2 · 拦截流水按原因码 / 日 / 周汇总；有 outcome 标注时算有效率。"""
    from collections import defaultdict
    from datetime import datetime

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
