"""消费前检查 L1 日期与 L2 立方是否盖住请求窗。

门槛与时钟契约见 ``cover_policy``（写入 / 消费共用）。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from core.research.materialize import consumer as _mat_consumer
from core.research.materialize.consumer import MaterializeRequired, hint_for_family
from core.research.materialize.cover_policy import LAKE_COVER_FRAC, cover_need

_OPEN_FAMILIES = ("oo", "tc", "co", "oo_rank", "tc_rank")


def recent_trading_days(n: int, *, end: str = "") -> List[str]:
    """从最近完整日线日往前数 ``n`` 个交易日（含当天）。"""
    from core.market.calendar import expected_latest_daily_bar_date, is_trading_day

    need = max(1, min(1500, int(n or 1)))
    end_s = str(end or expected_latest_daily_bar_date() or "")[:10]
    try:
        dt = datetime.strptime(end_s, "%Y-%m-%d")
    except ValueError:
        dt = datetime.now()
    out: List[str] = []
    guard = 0
    while len(out) < need and guard < need * 3 + 40:
        key = dt.strftime("%Y-%m-%d")
        if is_trading_day(key):
            out.append(key)
        dt -= timedelta(days=1)
        guard += 1
    return sorted(out)


def l1_invalidate_decision(stored_bars_fp: Optional[str], current_bars_fp: str) -> bool:
    """无湖不删；有湖但没有价量指纹，或指纹变了，才删 L1。"""
    if stored_bars_fp is None:
        return False
    if not str(stored_bars_fp):
        return True
    return str(stored_bars_fp) != str(current_bars_fp or "")


def _codes_dates(items: Sequence[Any]) -> Tuple[List[str], List[str]]:
    codes: List[str] = []
    seen = set()
    days: Set[str] = set()
    for raw in items or []:
        if isinstance(raw, (tuple, list)) and raw:
            code = str(raw[0] or "").strip()
            bars = raw[1] if len(raw) > 1 else []
        elif isinstance(raw, dict):
            code = str(raw.get("stock_code") or raw.get("code") or "").strip()
            bars = raw.get("bars") or []
        else:
            continue
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
        for bar in bars or []:
            if not isinstance(bar, dict):
                continue
            day = str(bar.get("date") or bar.get("trade_date") or "")[:10]
            if len(day) >= 10:
                days.add(day)
    return codes, sorted(days)


def _other_hash_days(clock: str, universe_hash: str) -> Set[str]:
    from core.research.day_cube import _root, _safe_clock
    from core.research.feature_registry import FEATURE_SCHEMA

    root = os.path.join(_root(), f"v{FEATURE_SCHEMA}")
    clk = _safe_clock(clock)
    found: Set[str] = set()
    if not os.path.isdir(root):
        return found
    try:
        names = os.listdir(root)
    except OSError:
        return found
    for name in names:
        if name == universe_hash or name.startswith("."):
            continue
        folder = os.path.join(root, name, clk)
        if not os.path.isdir(folder):
            continue
        try:
            files = os.listdir(folder)
        except OSError:
            continue
        for fn in files:
            if fn.endswith(".meta.json") and len(fn) >= 10:
                found.add(fn[:10])
    return found


def _problem_span(problem: Dict[str, Any]) -> str:
    """缺段的日期。同一天不写成「某日 … 某日」。"""
    start = str(problem.get("date_from") or "")[:10]
    end = str(problem.get("date_to") or "")[:10]
    if len(start) >= 10 and len(end) >= 10 and start != end:
        return f"（{start} … {end}）"
    if len(start) >= 10:
        return f"（{start}）"
    return ""


def _coverage_error_bits(problems: Sequence[Dict[str, Any]]) -> str:
    bits: List[str] = []
    for problem in list(problems or [])[:4]:
        span = _problem_span(problem)
        n_days = int(problem.get("n_days") or 0)
        if problem.get("family"):
            bits.append(f"{problem['family']} 缺 {n_days} 日{span}")
        else:
            bits.append(f"立方 {problem.get('clock')} 缺 {n_days} 日{span}")
    return "，".join(bits)


def _unique_codes(codes: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in codes or []:
        c = str(raw or "").strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def read_cube_codes() -> List[str]:
    """L2 目录键：物化与拟合用的完整观察池。"""
    try:
        from core.research.materialize.cube_z import universe_codes

        return _unique_codes(universe_codes())
    except Exception:  # noqa: BLE001
        return []


def assess_coverage(
    codes: Sequence[str],
    dates: Sequence[str],
    *,
    families: Sequence[str],
    clocks: Sequence[str],
    min_frac: float = LAKE_COVER_FRAC,
    cube_codes: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """整日覆盖低于 ``min_frac``，或立方缺失 / 宇宙不符，则 ``ok`` 为假。

    ``cube_codes`` 是 L2 目录键。成交名单（剔 ST、分档）可以比观察池短，
    立方仍按物化时的完整观察池查找。
    """
    from core.research.day_cube import day_cube_status
    from core.research.feature_lake import load_stock_dates
    from core.research.feature_registry import universe_hash

    code_list = _unique_codes(codes)
    cube_list = _unique_codes(cube_codes) if cube_codes is not None else list(code_list)
    if len(cube_list) < 2:
        cube_list = list(code_list)
    day_list = sorted({str(d)[:10] for d in (dates or []) if len(str(d or "")[:10]) >= 10})
    fams = [str(f) for f in (families or []) if str(f)]
    clks = [str(c) for c in (clocks or []) if str(c)]
    if len(code_list) < 2 or not day_list or (not fams and not clks):
        return {"ok": True, "success": True, "skipped": True}

    need = cover_need(len(code_list), frac=min_frac, minimum=2)
    problems: List[Dict[str, Any]] = []
    uh = universe_hash(cube_list)

    for fam in fams:
        have = []
        for code in code_list:
            try:
                ds = load_stock_dates(fam, code)
            except Exception:  # noqa: BLE001
                ds = None
            if ds:
                have.append(ds)
        missing = [day for day in day_list if sum(1 for ds in have if day in ds) < need]
        if missing:
            problems.append(
                {
                    "layer": "l1",
                    "family": fam,
                    "n_days": len(missing),
                    "date_from": missing[0],
                    "date_to": missing[-1],
                    "sample": missing[:8],
                }
            )

    for clk in clks:
        try:
            foreign = _other_hash_days(clk, uh)
        except Exception:  # noqa: BLE001
            foreign = set()
        missing_days: List[str] = []
        universe = False
        thin = False
        for day in day_list:
            try:
                status = day_cube_status(day, cube_list, clock=clk)
            except Exception:  # noqa: BLE001
                status = "miss"
            if status == "ok":
                continue
            if status == "universe" or day in foreign:
                universe = True
            if status == "thin":
                thin = True
            missing_days.append(day)
        if missing_days:
            problems.append(
                {
                    "layer": "l2",
                    "clock": clk,
                    "n_days": len(missing_days),
                    "date_from": missing_days[0],
                    "date_to": missing_days[-1],
                    "universe_changed": universe,
                    "thin": thin,
                    "sample": missing_days[:8],
                }
            )

    if not problems:
        return {
            "ok": True,
            "success": True,
            "n_codes": len(code_list),
            "n_dates": len(day_list),
            "universe_hash": uh,
        }

    def _is_open_clock(raw: Any) -> bool:
        return str(raw or "").strip().lower() in ("open", "09:30", "0930")

    def _problem_is_minute(problem: Dict[str, Any]) -> bool:
        fam = problem.get("family")
        if fam and hint_for_family(str(fam)) == "分钟线物化":
            return True
        clk = problem.get("clock")
        if clk and not _is_open_clock(clk):
            return True
        return False

    def _problem_is_open(problem: Dict[str, Any]) -> bool:
        fam = problem.get("family")
        if fam and hint_for_family(str(fam)) == "日线物化":
            return True
        clk = problem.get("clock")
        if clk and _is_open_clock(clk):
            return True
        return False

    has_minute = any(_problem_is_minute(p) for p in problems)
    has_open = any(_problem_is_open(p) for p in problems)
    # 无法归类的缺口当混合处理，避免 request 空参
    if problems and not has_minute and not has_open:
        has_open = True
        has_minute = True
    universe_changed = any(bool(p.get("universe_changed")) for p in problems)

    def _dedupe_clocks(raw: Any) -> List[str]:
        out: List[str] = []
        seen: Set[str] = set()
        for item in raw or []:
            key = str(item or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(key)
        return out

    if universe_changed:
        hint = "日线物化"
        lead = "观察池已变，请按当前池重跑日线物化"
        do_l1, do_tau = True, False
        req_fams = [f for f in fams if f in _OPEN_FAMILIES] or list(_OPEN_FAMILIES[:3])
        req_clks = ["open"]
    elif has_minute and not has_open:
        hint = "分钟线物化"
        lead = "请先分钟线物化 L1/L2"
        do_l1, do_tau = False, True
        req_fams = []
        req_clks = _dedupe_clocks(
            [c for c in clks if c and not _is_open_clock(c)]
            or [
                p.get("clock")
                for p in problems
                if p.get("clock") and not _is_open_clock(p.get("clock"))
            ]
        )
    elif has_open and not has_minute:
        hint = "日线物化"
        lead = "请先日线物化 L1/L2"
        do_l1, do_tau = True, False
        req_fams = [f for f in fams if f in _OPEN_FAMILIES] or list(_OPEN_FAMILIES[:3])
        req_clks = ["open"]
    else:
        # 混缺：一键参数两阶段都开
        hint = "分钟线物化"
        lead = "请先物化 L1/L2（日线 open + 分钟 τ）"
        do_l1, do_tau = True, True
        req_fams = [f for f in fams if f in _OPEN_FAMILIES] or list(_OPEN_FAMILIES[:3])
        req_clks = _dedupe_clocks(clks) or ["open"]
    error = lead
    detail = _coverage_error_bits(problems)
    if detail:
        error = f"{lead}：{detail}"
    if do_l1 and not do_tau:
        req_clks = ["open"]
    elif do_tau and not do_l1 and not req_clks:
        req_clks = _dedupe_clocks(
            p.get("clock")
            for p in problems
            if p.get("clock") and not _is_open_clock(p.get("clock"))
        )
    return {
        "ok": False,
        "success": False,
        "error_code": "materialize_required",
        "error": error,
        "hint": hint,
        "layer": problems[0].get("layer"),
        "families": fams,
        "clocks": clks,
        "problems": problems,
        "universe_hash": uh,
        "universe_changed": universe_changed,
        "n_codes": len(code_list),
        "n_dates": len(day_list),
        "request": {
            "do_l1": bool(do_l1),
            "do_l2": True,
            "do_l1_tau_grid": bool(do_tau),
            "families": list(req_fams),
            "clocks": list(req_clks),
        },
    }


def enforce_consumer_coverage(
    items: Sequence[Any],
    *,
    families: Sequence[str],
    clocks: Sequence[str],
    min_frac: float = LAKE_COVER_FRAC,
) -> None:
    """消费方在 collect 之前调用。未开只读则直接返回。"""
    # 经模块属性取开关：避免单测 patch consumer 时本模块绑死 MagicMock。
    if not _mat_consumer.consumer_read_only():
        return
    codes, dates = _codes_dates(items)
    got = read_cube_codes()
    report = assess_coverage(
        codes,
        dates,
        families=families,
        clocks=clocks,
        min_frac=min_frac,
        cube_codes=got if len(got) >= 2 else None,
    )
    if not report.get("ok"):
        raise MaterializeRequired(report)


def replay_coverage_gap(
    stock_bars: Dict[str, Any],
    dates: Sequence[str],
    *,
    score_from: int,
    fill_clock: str,
    min_frac: float = LAKE_COVER_FRAC,
) -> Optional[Dict[str, Any]]:
    """调仓回测开跑前的覆盖结果。只读未开或已齐时返回 None。"""
    if not _mat_consumer.consumer_read_only():
        return None
    from core.research.lake_bridge import FAMILY_CO, FAMILY_OO, FAMILY_TC, family_for_tc

    codes = [str(c).strip() for c in (stock_bars or {}) if str(c).strip()]
    day_list = [str(d)[:10] for d in (dates or [])]
    start = max(0, int(score_from or 0))
    scored = [d for d in day_list[start:] if len(d) >= 10]
    fams = [FAMILY_OO, FAMILY_TC, FAMILY_CO]
    clks = ["open"]
    clock = str(fill_clock or "09:30")[:5]
    if clock not in ("", "09:30"):
        fams.append(family_for_tc(tau_hm=clock))
        clks.append(clock)
    got = read_cube_codes()
    report = assess_coverage(
        codes,
        scored,
        families=fams,
        clocks=clks,
        min_frac=min_frac,
        cube_codes=got if len(got) >= 2 else None,
    )
    if report.get("ok"):
        return None
    report["task"] = "paper_replay"
    return report
