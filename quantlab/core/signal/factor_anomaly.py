"""因子异常闸：数据缺失与时间错位。

缺测（今开 / 缺口 / 因子日空）和时间错位（因子日 ≠ 应停日、τ 日 ≠ T、
last_change 不是今开缺口）不得静默进生产 ŷ。
历史 PIT（报价日不是当前会话）只做内部自洽，不拿墙上日历打。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

from core.signal import session_pit as _session_pit

KIND_MISSING = "missing"
KIND_PIT = "pit"

_TAU_DATE_CLOCKS = ("open", "eod", "intraday", "eod_next")
_GAP_EPS = 0.2  # 百分点；今开缺口 vs last_change


def _day(raw: Any) -> str:
    return str(raw or "")[:10]


def _finite(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return None
    if n != n:  # NaN
        return None
    return n


def is_live_score_context(
    quote: Optional[Mapping[str, Any]] = None,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """无报价日、或报价日=当前会话/应有完整日线 → live。"""
    n = _session_pit.shanghai_now(now)
    q_day = _session_pit.quote_asof(quote)
    if not q_day:
        return True
    if _session_pit._is_latest_complete_offline_asof(q_day, now=n):
        return True
    try:
        from core.market.calendar import resolve_session_date

        session = str(resolve_session_date(now=n) or "")[:10]
    except Exception:  # noqa: BLE001
        logger.debug("resolve_session_date failed in live context", exc_info=True)
        session = n.strftime("%Y-%m-%d")
    return bool(session) and q_day == session


def expected_eod_as_of(
    *,
    trade_day: str = "",
    dual_score_window: str = "",
    live: bool = False,
    now: Optional[datetime] = None,
) -> str:
    """EOD 因子日应停在哪一天。

    live：ŷ_oo 周期 T 的 T−1（今收不进 X，收盘后也不滚）。
    历史：盘中 = T−1，已完成的历史 asof = T。
    """
    n = _session_pit.shanghai_now(now)
    if live:
        try:
            from core.market.calendar import prev_trading_day

            cycle = _session_pit.oo_cycle_date(now=n)
            return str(prev_trading_day(cycle) or "")[:10]
        except Exception:  # noqa: BLE001
            logger.debug("expected_eod_as_of live cycle failed", exc_info=True)
            return ""
    day = _day(trade_day)
    win = str(dual_score_window or "")
    if win == "intraday" and len(day) >= 10:
        try:
            from core.market.calendar import prev_trading_day

            return str(prev_trading_day(day) or "")[:10]
        except Exception:  # noqa: BLE001
            logger.debug("prev_trading_day failed in expected eod", exc_info=True)
            return ""
    return day


def _issue(
    kind: str,
    code: str,
    reason: str,
    *,
    fatal_eod: bool = False,
    fatal_tau: bool = False,
) -> Dict[str, Any]:
    return {
        "kind": kind,
        "code": code,
        "reason": reason,
        "fatal_eod": bool(fatal_eod),
        "fatal_tau": bool(fatal_tau),
    }


def inspect_factor_anomaly(
    *,
    eod_pit: Optional[Mapping[str, Any]] = None,
    open_t_info: Optional[Mapping[str, Any]] = None,
    quote: Optional[Mapping[str, Any]] = None,
    trade_day: str = "",
    now: Optional[datetime] = None,
    as_of_tau: Optional[str] = None,
    last_change: Optional[float] = None,
    gap_pct: Optional[float] = None,
    required_keys: Optional[Sequence[str]] = None,
    sub_scores: Optional[Mapping[str, Any]] = None,
    live: Optional[bool] = None,
) -> Dict[str, Any]:
    """检查缺失与时间错位。不改分数，只出报告。"""
    pit = dict(eod_pit or {})
    ot = dict(open_t_info or {})
    n = _session_pit.shanghai_now(now)
    day = _day(trade_day) or _day(ot.get("trade_day"))
    win = str(pit.get("dual_score_window") or "")
    eod_as_of = _day(pit.get("eod_as_of"))
    open_px = _finite(ot.get("open"))
    prev_c = _finite(ot.get("prev_close"))
    gap = _finite(ot.get("gap_pct"))
    if gap is None:
        gap = _finite(gap_pct)
    live_ctx = bool(is_live_score_context(quote, now=n) if live is None else live)
    issues: List[Dict[str, Any]] = []

    if not eod_as_of:
        issues.append(
            _issue(
                KIND_MISSING,
                "eod_as_of",
                "日线因子日缺失",
                fatal_eod=True,
            )
        )
    else:
        expect = expected_eod_as_of(
            trade_day=day,
            dual_score_window=win,
            live=live_ctx,
            now=n,
        )
        if expect and eod_as_of != expect:
            issues.append(
                _issue(
                    KIND_PIT,
                    "eod_as_of",
                    f"日线因子日错位（as_of={eod_as_of} 应为 {expect}）",
                    fatal_eod=True,
                )
            )
        if win == "intraday" and day and eod_as_of == day:
            issues.append(
                _issue(
                    KIND_PIT,
                    "eod_intraday_leak",
                    f"盘中日线因子漏入 T 日 K（{eod_as_of}）",
                    fatal_eod=True,
                )
            )

    need_open = win == "intraday" or live_ctx or (win == "eod_next" and bool(eod_as_of))
    if need_open and open_px is None:
        issues.append(
            _issue(
                KIND_MISSING,
                "open_t",
                "今开缺失，ŷ 不能用昨收对前天冒充缺口",
                fatal_eod=True,
                fatal_tau=True,
            )
        )
    if need_open and open_px is not None and prev_c is None:
        issues.append(
            _issue(
                KIND_MISSING,
                "prev_close",
                "昨收缺失，今开缺口算不出",
                fatal_eod=True,
                fatal_tau=True,
            )
        )
    if need_open and open_px is not None and prev_c is not None and gap is None:
        issues.append(
            _issue(
                KIND_MISSING,
                "gap",
                "今开缺口缺失",
                fatal_eod=True,
                fatal_tau=True,
            )
        )

    tau_raw = str(as_of_tau or "").strip()
    tau_day = _day(tau_raw)
    if (
        len(tau_day) >= 10
        and tau_raw.lower() not in _TAU_DATE_CLOCKS
        and day
        and tau_day != day
    ):
        issues.append(
            _issue(
                KIND_PIT,
                "as_of_tau",
                f"分钟 τ 日错位（τ={tau_day} T={day}）",
                fatal_tau=True,
            )
        )

    lc = _finite(last_change)
    if lc is not None and gap is not None and abs(lc - gap) > _GAP_EPS:
        issues.append(
            _issue(
                KIND_PIT,
                "last_change_vs_gap",
                f"last_change={lc} 与今开缺口 {gap} 不一致",
                fatal_eod=True,
            )
        )

    req = [str(k).strip() for k in (required_keys or []) if str(k).strip()]
    if req:
        subs = sub_scores if isinstance(sub_scores, Mapping) else {}
        missing = [k for k in req if subs.get(k) is None]
        if missing and len(missing) == len(req):
            issues.append(
                _issue(
                    KIND_MISSING,
                    "sub_scores",
                    f"β 因子全部缺测（{len(missing)}）",
                    fatal_eod=True,
                )
            )
        elif missing and len(missing) * 2 >= len(req):
            issues.append(
                _issue(
                    KIND_MISSING,
                    "sub_scores_sparse",
                    f"β 因子缺测 {len(missing)}/{len(req)}",
                )
            )

    # 去重：同 code 只留一条
    uniq: List[Dict[str, Any]] = []
    seen = set()
    for iss in issues:
        key = (iss.get("kind"), iss.get("code"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(iss)

    fatal_eod = any(bool(i.get("fatal_eod")) for i in uniq)
    fatal_tau = any(bool(i.get("fatal_tau")) for i in uniq)
    first = next((i for i in uniq if i.get("fatal_eod") or i.get("fatal_tau")), None)
    if first is None and uniq:
        first = uniq[0]
    gate_reason = ""
    if first is not None and (fatal_eod or fatal_tau):
        gate_reason = f"factor_anomaly:{first.get('kind')}:{first.get('code')}"
    return {
        "ok": not uniq,
        "fatal_eod": fatal_eod,
        "fatal_tau": fatal_tau,
        "live": live_ctx,
        "issues": uniq,
        "gate_reason": gate_reason,
        "expected_eod_as_of": expected_eod_as_of(
            trade_day=day,
            dual_score_window=win,
            live=live_ctx,
            now=n,
        ),
    }


def merge_anomaly_reports(*reports: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []
    live = False
    expected = ""
    for rep in reports:
        if not isinstance(rep, Mapping):
            continue
        live = live or bool(rep.get("live"))
        expected = str(rep.get("expected_eod_as_of") or expected or "")[:10]
        for iss in rep.get("issues") or []:
            if isinstance(iss, Mapping):
                issues.append(dict(iss))
    uniq: List[Dict[str, Any]] = []
    seen = set()
    for iss in issues:
        key = (iss.get("kind"), iss.get("code"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(iss)
    fatal_eod = any(bool(i.get("fatal_eod")) for i in uniq)
    fatal_tau = any(bool(i.get("fatal_tau")) for i in uniq)
    first = next((i for i in uniq if i.get("fatal_eod") or i.get("fatal_tau")), None)
    gate_reason = ""
    if first is not None and (fatal_eod or fatal_tau):
        gate_reason = f"factor_anomaly:{first.get('kind')}:{first.get('code')}"
    return {
        "ok": not uniq,
        "fatal_eod": fatal_eod,
        "fatal_tau": fatal_tau,
        "live": live,
        "issues": uniq,
        "gate_reason": gate_reason,
        "expected_eod_as_of": expected,
    }


def stamp_factor_anomaly_warnings(item: Optional[dict], report: Optional[Mapping[str, Any]]) -> None:
    if not isinstance(item, dict) or not isinstance(report, Mapping):
        return
    warns = item.get("warnings")
    if not isinstance(warns, list):
        warns = []
        item["warnings"] = warns
    for iss in report.get("issues") or []:
        if not isinstance(iss, Mapping):
            continue
        tag = f"factor_anomaly:{iss.get('kind')}:{iss.get('code')}"
        if tag not in warns:
            warns.append(tag)
    item["factor_anomaly"] = dict(report)


def null_tau_heads(item: Optional[dict]) -> None:
    """τ 日错位：ŷ_τc / ŷ_co / ranking 作废，ŷ_oo 可留。"""
    if not isinstance(item, dict):
        return
    for k in (
        "y_τc",
        "predicted_score_τc",
        "y_τc_ridge",
        "score_rem",
        "predicted_score_rem",
        "predicted_score_tau",
        "y_co",
        "predicted_score_co",
        "predicted_score_on",
        "ranking",
        "predicted_score_blend",
    ):
        item[k] = None


def null_eod_heads(item: Optional[dict]) -> None:
    if not isinstance(item, dict):
        return
    for k in (
        "score",
        "predicted_score",
        "predicted_score_eod",
        "predicted_score_oo",
        "y_oo",
        "score_global",
        "score_cluster",
        "predicted_score_blend",
        "ranking",
    ):
        item[k] = None
    item["hard_reject"] = True
    item["quality_gate"] = True


def apply_factor_anomaly_to_item(
    item: Optional[dict],
    report: Optional[Mapping[str, Any]],
    *,
    bypass: bool = False,
) -> Optional[dict]:
    """把报告写进 item；生产路径缺测/错位则作废对应头。"""
    if not isinstance(item, dict):
        return item
    stamp_factor_anomaly_warnings(item, report)
    if bypass or not isinstance(report, Mapping):
        return item
    if report.get("fatal_tau"):
        null_tau_heads(item)
    if report.get("fatal_eod"):
        null_eod_heads(item)
        item["gate_reason"] = str(report.get("gate_reason") or "factor_anomaly")
        first = next(
            (i for i in (report.get("issues") or []) if i.get("fatal_eod")),
            None,
        )
        if first:
            item["reject_reason"] = str(first.get("reason") or item.get("gate_reason"))
    return item


__all__ = [
    "KIND_MISSING",
    "KIND_PIT",
    "apply_factor_anomaly_to_item",
    "expected_eod_as_of",
    "inspect_factor_anomaly",
    "is_live_score_context",
    "merge_anomaly_reports",
    "null_eod_heads",
    "null_tau_heads",
    "stamp_factor_anomaly_warnings",
]
