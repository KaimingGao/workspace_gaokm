"""Pure risk / return metrics for paper snapshots and curve alignment."""

import math
import statistics
from datetime import datetime
from typing import Any, List, Optional, Sequence, Tuple

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
) -> dict[str, Any]:
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
