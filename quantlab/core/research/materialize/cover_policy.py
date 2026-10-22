"""物化写入 / 消费共用覆盖门槛（单一真相）。

改「怎么挂 5m / 怎么估覆盖 / 怎么判齐」时，先改本文件契约，再改调用方与测试。

覆盖契约
========
消费方           L1                              L2 钟                         日期窗
--------------  -------------------------------  ----------------------------  --------------------
做 T 回测        决策钟 tc_HHMM（不硬要 oo）         ≤leg1 的 suggested τ（不含 open） 回看 ∩ 5m 够格日
分钟 τ 物化      tc_HHMM 网格（自 09:35）           **仅** suggested τ（不含 open）  5m 够格日（近端优先）
ŷ_τ* 拟合        —                                同物化 τ 钟                     lookback ∩ 5m 够格日（有多少用多少）
调仓 / 日线物化  oo/tc/co                          open                          本地日线够格日（近端优先；日历只作回退）

门槛
----
- ``LAKE_COVER_FRAC``：L1 票/日、L2 hit+built、消费 assess 同口径（默认 0.5）
- ``HORIZON_COVER_FRAC``：窗头列名覆盖；与湖日同口径。早盘缺 ``raw_ath_*`` 不得按 1.0 全拒
- 探针可外推「有分钟的票数」，**禁止**按池比例放大交易日跨度
- 写入 ``ok``：禁止 hit+built=0 仍成功；失败文案须带 hit/built/slim_reject/need
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set

LAKE_COVER_FRAC = 0.5
HORIZON_COVER_FRAC = LAKE_COVER_FRAC


def cover_need(
    n: int,
    *,
    frac: float = LAKE_COVER_FRAC,
    minimum: int = 1,
) -> int:
    """``ceil(n * frac)``，至少 ``minimum``。"""
    try:
        total = int(n or 0)
    except (TypeError, ValueError):
        total = 0
    try:
        f = float(frac if frac not in (None, "") else LAKE_COVER_FRAC)
    except (TypeError, ValueError):
        f = float(LAKE_COVER_FRAC)
    if not (f > 0):
        f = float(LAKE_COVER_FRAC)
    try:
        floor = int(minimum or 0)
    except (TypeError, ValueError):
        floor = 1
    floor = max(1, floor)
    return max(floor, int(max(total, 0) * f + 0.999))


def cover_reaches(
    have: int,
    universe: int,
    *,
    frac: float = LAKE_COVER_FRAC,
    minimum: int = 1,
) -> bool:
    try:
        got = int(have or 0)
    except (TypeError, ValueError):
        got = 0
    return got >= cover_need(universe, frac=frac, minimum=minimum)


def resolve_fit_minute_covered_dates(
    codes: Sequence[str],
    *,
    period: str = "5",
    lake_frac: float = LAKE_COVER_FRAC,
) -> Optional[List[str]]:
    """宇宙级 5m 够格日（与分钟物化 L2 同门槛）。

    供报告 ``minute_days``/``minute_span`` 对照；ŷ_τ* **拟合不再按此裁单票 5m**
    （单票本地有多少用多少；截面 z 仍须 L2，缺则 ``materialize_required``）。
    查询失败返回 ``None``。
    """
    code_list = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    if len(code_list) < 2:
        return []
    need = cover_need(len(code_list), frac=lake_frac, minimum=2)
    try:
        from core.store import list_minute_covered_dates

        return list_minute_covered_dates(
            code_list, min_codes=need, period=str(period or "5")
        )
    except Exception:  # noqa: BLE001
        return None


def filter_minute_bars_to_covered(
    minute_bars: Sequence[Any],
    covered: Optional[Set[str]],
) -> List[Any]:
    """``covered is None`` 不裁；否则只留够格日上的 5m 根。"""
    rows = list(minute_bars or [])
    if covered is None:
        return rows
    return [
        b
        for b in rows
        if isinstance(b, dict) and str(b.get("date") or "")[:10] in covered
    ]


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return int(default)


def scale_eligible_stocks(
    eligible_probe: int,
    *,
    probe_n: int,
    pool_n: int,
) -> int:
    """有分钟票数可按池/探针比例外推；结果至少为探针观测值。"""
    got = max(0, _as_int(eligible_probe))
    pn = max(0, _as_int(probe_n))
    pool = max(0, _as_int(pool_n))
    if pn < 1 or pool <= pn:
        return got
    return max(got, int(round(got * (pool / float(pn)))))


def clamp_span_days(span_probe: int, max_written_dates: int) -> int:
    """探针日历 union 过长时钳到写出日（禁止按票数放大跨度）。"""
    span = max(0, _as_int(span_probe))
    written = max(0, _as_int(max_written_dates))
    if written > 0 and span > written * 2:
        return written
    return span


def format_cube_cover_error(
    clock: str,
    *,
    hit: int = 0,
    built: int = 0,
    n_days: int,
    frac: float = LAKE_COVER_FRAC,
    slim_reject: int = 0,
    skipped: int = 0,
) -> str:
    """统一 L2 写入失败文案：``立方 09:35 覆盖不足 hit+built=0/50 need≥25 …``。"""
    h = _as_int(hit)
    b = _as_int(built)
    days = _as_int(n_days)
    need = cover_need(days, frac=frac, minimum=1)
    who = str(clock or "").strip() or "?"
    text = f"立方 {who} 覆盖不足 hit+built={h + b}/{days} need≥{need}"
    slim = _as_int(slim_reject)
    if slim:
        text += f" slim_reject={slim}"
    skip = _as_int(skipped)
    if skip:
        text += f" skipped={skip}"
    return text


def format_l1_clock_cover_error(
    clock: str,
    *,
    n_stocks: int = 0,
    n_dates: int = 0,
    n_eligible: int,
    n_days: int,
    frac: float = LAKE_COVER_FRAC,
) -> str:
    """L1 τ 单钟覆盖不足文案。"""
    stocks = _as_int(n_stocks)
    dates = _as_int(n_dates)
    need_s = cover_need(n_eligible, frac=frac, minimum=2)
    need_d = cover_need(n_days, frac=frac, minimum=1)
    who = str(clock or "").strip() or "?"
    return (
        f"{who} 覆盖不足 stocks={stocks}/{need_s} "
        f"dates={dates}/{need_d}"
    )


def finalize_l2_report(
    report: Dict[str, Any],
    clocks: Sequence[str],
    *,
    lake_frac: float = LAKE_COVER_FRAC,
) -> Dict[str, Any]:
    """对账 by_clock：有失败钟或 hit+built 全 0 则 report.ok=False。"""
    if not isinstance(report, dict):
        return {"ok": False, "error": "L2 report invalid"}
    if report.get("error") == "cancelled":
        report["ok"] = False
        return report
    by = report.get("by_clock") if isinstance(report.get("by_clock"), dict) else {}
    clock_list = [str(c).strip() for c in (clocks or []) if str(c or "").strip()]
    if not clock_list:
        clock_list = [str(k) for k in by.keys()]
    ok_n = 0
    total_have = 0
    for clk in clock_list:
        row = by.get(clk) if isinstance(by.get(clk), dict) else None
        if row is None:
            row = {"ok": False, "error": "missing clock", "cubes_hit": 0, "cubes_built": 0}
            by[clk] = row
        have = _as_int(row.get("cubes_hit")) + _as_int(row.get("cubes_built"))
        total_have += have
        days = _as_int(row.get("days") or report.get("minute_days") or 0)
        if days < 1:
            try:
                days = len(report.get("dates") or [])
            except TypeError:
                days = 0
        reaches = cover_reaches(have, days, frac=lake_frac, minimum=1) if days else have > 0
        if row.get("ok") is False or row.get("error"):
            if not reaches and not row.get("error"):
                row["error"] = format_cube_cover_error(
                    clk,
                    hit=_as_int(row.get("cubes_hit")),
                    built=_as_int(row.get("cubes_built")),
                    n_days=days,
                    frac=lake_frac,
                    slim_reject=_as_int(row.get("slim_reject")),
                    skipped=_as_int(row.get("days_skipped")),
                )
            report["ok"] = False
            continue
        if not reaches:
            row["ok"] = False
            row["error"] = format_cube_cover_error(
                clk,
                hit=_as_int(row.get("cubes_hit")),
                built=_as_int(row.get("cubes_built")),
                n_days=days,
                frac=lake_frac,
                slim_reject=_as_int(row.get("slim_reject")),
                skipped=_as_int(row.get("days_skipped")),
            )
            report["ok"] = False
            if not report.get("error"):
                report["error"] = row["error"]
            continue
        row["ok"] = True
        ok_n += 1
    report["by_clock"] = by
    report["clocks_ok"] = ok_n
    if clock_list and ok_n < len(clock_list):
        report["ok"] = False
        if not report.get("error"):
            report["error"] = f"L2 覆盖不足 ok={ok_n}/{len(clock_list)}"
    if clock_list and total_have < 1 and report.get("error") != "cancelled":
        report["ok"] = False
        if not report.get("error"):
            report["error"] = "L2 未写入任何立方（hit+built=0）"
    return report


def first_nested_error(*parts: Optional[Dict[str, Any]]) -> str:
    """从 l1 / l1_tau_grid / l2 报告里取第一条非空 error。"""
    for part in parts:
        if not isinstance(part, dict):
            continue
        err = str(part.get("error") or "").strip()
        if err and err != "cancelled":
            return err
        by = part.get("by_clock")
        if isinstance(by, dict):
            for row in by.values():
                if not isinstance(row, dict):
                    continue
                bit = str(row.get("error") or "").strip()
                if bit:
                    return bit
    return ""


def resolve_tau_l2_clocks(
    clocks: Sequence[str] | None,
    *,
    suggested: Sequence[str] | None = None,
) -> List[str]:
    """纯 τ 物化 L2 时钟：仅分钟钟（suggested τ），**不含 open**。

    日线 open 由日线物化卡单独写，不经此函数。
    """
    from core.research.materialize.catalog import normalize_clocks, suggested_tau_clocks

    clks = normalize_clocks(clocks)
    sug = [
        str(c).strip()
        for c in (suggested if suggested is not None else suggested_tau_clocks())
        if str(c or "").strip()
    ]
    # 去掉 open / 09:30：窗头只吃分钟钟 Cube
    clks = [
        c
        for c in clks
        if str(c).lower() not in ("open", "09:30", "0930")
    ]
    if not clks:
        clks = list(sug)
    for c in sug:
        if c not in clks and str(c).lower() not in ("open", "09:30", "0930"):
            clks.append(c)
    return list(clks)


__all__ = [
    "LAKE_COVER_FRAC",
    "HORIZON_COVER_FRAC",
    "cover_need",
    "cover_reaches",
    "scale_eligible_stocks",
    "clamp_span_days",
    "format_cube_cover_error",
    "format_l1_clock_cover_error",
    "finalize_l2_report",
    "first_nested_error",
    "resolve_tau_l2_clocks",
]
