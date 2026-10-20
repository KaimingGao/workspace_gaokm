"""L2 DayCube 物化：按日 × 宇宙 × clock ensure。"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence

from core.research.materialize.catalog import normalize_clocks

logger = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[int, int, str], None]]
CancelCb = Optional[Callable[[], bool]]


def _emit(cb: ProgressCb, cur: int, tot: int, msg: str) -> None:
    if not cb:
        return
    try:
        cb(int(cur), max(1, int(tot)), str(msg or ""))
    except Exception:  # noqa: BLE001
        logger.debug("materialize l2 progress_cb failed", exc_info=True)


def _cancelled(cb: CancelCb) -> bool:
    if not cb:
        return False
    try:
        return bool(cb())
    except Exception:  # noqa: BLE001
        return False


def _resolve_dates(
    dates: Sequence[str] | None,
    *,
    lookback_days: int,
    use_trading_days: bool = False,
) -> List[str]:
    explicit = sorted(
        {str(d)[:10] for d in (dates or []) if len(str(d or "")[:10]) >= 10}
    )
    if explicit:
        return explicit
    try:
        n = int(lookback_days or 60)
    except (TypeError, ValueError):
        n = 60
    n = max(2, min(1500, n))
    if use_trading_days:
        from core.research.materialize.coverage import recent_trading_days

        return recent_trading_days(n)
    # 日历日回退：消费方有交易日过滤；周末立方会被跳过
    today = datetime.now().date()
    out: List[str] = []
    for i in range(n):
        out.append((today - timedelta(days=i)).isoformat())
    return sorted(set(out))


def _attach_open_slim(codes: Sequence[str], days: Sequence[str]) -> int:
    """用日线开盘缺口把 slim 并进已有 open 立方。失败只记日志。"""
    try:
        from core.research.day_cube import attach_slim
        from core.t0.score_policy import build_tau_pool_by_date, load_bars_by_code_for_tau_pool
    except Exception:  # noqa: BLE001
        logger.debug("open slim imports failed", exc_info=True)
        return 0
    limit = max(20, min(1500, len(list(days or [])) + 8))
    try:
        bars = load_bars_by_code_for_tau_pool(codes, limit=limit)
        pool = build_tau_pool_by_date(bars) or {}
    except Exception:  # noqa: BLE001
        logger.debug("open slim tau pool failed", exc_info=True)
        return 0
    n = 0
    for day in days or []:
        slim = pool.get(str(day)[:10])
        if not isinstance(slim, dict) or not slim:
            continue
        try:
            if attach_slim(str(day)[:10], codes, clock="open", slim=slim):
                n += 1
        except Exception:  # noqa: BLE001
            logger.debug("attach open slim failed day=%s", day, exc_info=True)
    return n


def _warmup_open_clock(
    report: Dict[str, Any],
    code_list: Sequence[str],
    day_list: Sequence[str],
    clock: str,
    *,
    lake_frac: float,
    progress_cb: ProgressCb,
    cancel_cb: CancelCb,
) -> None:
    """一个开盘钟的 warmup。失败只记这一钟，调用方继续后面的钟。"""
    from core.t0.score_policy import warmup_watching_day_cubes

    try:
        warm = warmup_watching_day_cubes(
            code_list,
            day_list,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
            min_lake_frac=lake_frac,
            prefetch_l1=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("l2 open warmup failed: %s", exc)
        logger.debug("l2 open warmup failed", exc_info=True)
        report["ok"] = False
        report["by_clock"][clock] = {"ok": False, "error": str(exc)}
        return
    slim_n = _attach_open_slim(code_list, day_list)
    report["by_clock"][clock] = {
        "ok": True,
        "slim_days": slim_n,
        **dict(warm or {}),
    }


def _materialize_l2_open_only(
    report: Dict[str, Any],
    code_list: Sequence[str],
    day_list: Sequence[str],
    clock_list: Sequence[str],
    *,
    n_clocks: int,
    lake_frac: float,
    progress_cb: ProgressCb,
    cancel_cb: CancelCb,
    emit_scaled: Callable[..., None],
) -> None:
    """开盘钟：按钟 warmup。没有分钟钟时走这条，进度刻度与原来一致。"""
    for ci, clock in enumerate(clock_list):
        if _cancelled(cancel_cb):
            report["ok"] = False
            report["error"] = "cancelled"
            break
        clock_index = ci

        def _clock_emit(
            done: int,
            total: int,
            message: str,
            index: int = clock_index,
            steps: int = n_clocks,
        ) -> None:
            emit_scaled(progress_cb, index, steps, done, total, message)

        _clock_emit(0, 1, f"L2 {clock}（{clock_index + 1}/{n_clocks}）…")

        def _pcb(done: int, total: int, message: str = "") -> None:
            _clock_emit(done, total, message or "L2 open")

        _warmup_open_clock(
            report,
            code_list,
            day_list,
            str(clock),
            lake_frac=lake_frac,
            progress_cb=_pcb,
            cancel_cb=cancel_cb,
        )


def _minute_store_days(
    codes: Sequence[str],
    day_list: Sequence[str],
    *,
    min_codes: int,
) -> tuple:
    """未指定日期时，只留本地 5m 里达到截面人数、且落在回看窗内的交易日。

    返回 ``(days, note)``。``note`` 非空表示没有可写的日子。
    """
    try:
        from core.store import list_minute_covered_dates

        covered = list_minute_covered_dates(codes, min_codes=min_codes)
    except Exception:  # noqa: BLE001
        logger.warning("l2 minute dates failed", exc_info=True)
        return [], "分钟线日期读取失败"
    if covered is None:
        return [], "分钟线日期读取失败"
    window = {str(d)[:10] for d in day_list if len(str(d or "")[:10]) >= 10}
    days = [str(d)[:10] for d in covered if str(d)[:10] in window]
    if days:
        return days, ""
    if covered:
        return [], "分钟线不在回看窗内"
    return [], "本地 5m 没有够格的交易日"


def _fail_minute_clocks(report: Dict[str, Any], clocks: Sequence[str], note: str) -> None:
    for clock in clocks:
        report["by_clock"][str(clock)] = {
            "ok": False,
            "error": note,
            "cubes_hit": 0,
            "cubes_built": 0,
            "days_skipped": 0,
            "days": 0,
        }
    report["ok"] = False
    report["error"] = note


def _clock_bucket(report: Dict[str, Any], clock: str, fam: str, n_days: int) -> Dict[str, Any]:
    row = report["by_clock"].get(clock)
    if row is None:
        row = {
            "ok": True,
            "cubes_hit": 0,
            "cubes_built": 0,
            "days_skipped": 0,
            "slim_reject": 0,
            "days": n_days,
            "tc_family": fam,
        }
        report["by_clock"][clock] = row
    return row


def _materialize_l2_with_minute(
    report: Dict[str, Any],
    code_list: Sequence[str],
    day_list: Sequence[str],
    clock_list: Sequence[str],
    minute_clocks: Sequence[str],
    *,
    lake_frac: float,
    require_horizon_names: bool,
    clip_minute_store: bool,
    progress_cb: ProgressCb,
    cancel_cb: CancelCb,
    emit_scaled: Callable[..., None],
) -> None:
    """分钟钟：整窗 oo、co 各读一次，每个钟的 tc 只读一次，再按日写立方。

    ``clip_minute_store``：没人指定日期时，只覆盖本地 5m 够格的交易日。
    """
    from core.research.day_cube import load_day_cube
    from core.research.feature_lake import slice_stock_panels
    from core.research.lake_bridge import FAMILY_CO, FAMILY_OO, family_for_tc
    from core.research.materialize.cube_batch import build_day_cubes_from_lake
    from core.research.materialize.horizon_names import names_cover_horizon

    open_clocks = [c for c in clock_list if str(c) == "open"]
    need = max(2, int(len(code_list) * lake_frac + 0.999))
    minute_days = [str(d)[:10] for d in day_list if len(str(d or "")[:10]) >= 10]
    minute_note = ""
    if clip_minute_store:
        minute_days, minute_note = _minute_store_days(
            code_list, day_list, min_codes=need
        )
        report["minute_days"] = len(minute_days)
        report["minute_span"] = (
            f"{minute_days[0]}→{minute_days[-1]}" if minute_days else ""
        )
        if minute_note:
            report["minute_skip"] = minute_note
            logger.info("l2 minute window skipped: %s", minute_note)
        else:
            logger.info(
                "l2 minute window %s days %s (calendar %s)",
                len(minute_days),
                report["minute_span"],
                len(day_list),
            )
    open_steps = 1 if open_clocks else 0
    n_clocks = max(1, len(minute_clocks))
    n_steps = open_steps + n_clocks
    n_days = max(1, len(minute_days))
    fams = {str(clock): family_for_tc(tau_hm=str(clock)) for clock in minute_clocks}
    horizon_ok = names_cover_horizon if require_horizon_names else None
    load_logged: set = set()
    cover_logged: set = set()

    def _step_emit(step: int, done: int, total: int, message: str) -> None:
        emit_scaled(progress_cb, step, n_steps, done, total, message)

    if open_clocks:
        if _cancelled(cancel_cb):
            report["ok"] = False
            report["error"] = "cancelled"
            return

        def _open_pcb(done: int, total: int, message: str = "") -> None:
            _step_emit(0, done, total, message or "L2 open")

        _step_emit(0, 0, 1, "L2 open…")
        for clock in open_clocks:
            _warmup_open_clock(
                report,
                code_list,
                day_list,
                str(clock),
                lake_frac=lake_frac,
                progress_cb=_open_pcb,
                cancel_cb=cancel_cb,
            )
        if _cancelled(cancel_cb):
            report["ok"] = False
            report["error"] = "cancelled"
            return

    if not minute_days:
        note = minute_note or "本地 5m 没有够格的交易日"
        _emit(progress_cb, 1, 1, note)
        _fail_minute_clocks(report, minute_clocks, note)
        return

    def _mark_cancelled(clock: str) -> None:
        row = report["by_clock"].get(clock)
        if row is not None:
            row["ok"] = False
            row["error"] = "cancelled"
        report["ok"] = False
        report["error"] = "cancelled"

    pending: Dict[str, List[str]] = {}
    short: Dict[str, List[str]] = {}
    stopped = False
    for clock_i, clock in enumerate(minute_clocks):
        if _cancelled(cancel_cb):
            report["ok"] = False
            report["error"] = "cancelled"
            stopped = True
            break
        step = open_steps + clock_i
        label = f"L2 {clock}（{clock_i + 1}/{n_clocks}）"
        _step_emit(step, 0, 1, label)
        row = _clock_bucket(report, str(clock), fams[str(clock)], n_days)
        pend: List[str] = []
        sh: List[str] = []
        for day in minute_days:
            try:
                existing = load_day_cube(
                    day,
                    code_list,
                    clock=clock,
                    remember=False,
                    names_only=True,
                )
            except Exception:  # noqa: BLE001
                if clock not in load_logged:
                    load_logged.add(clock)
                    logger.warning(
                        "l2 day_cube load failed clock=%s day=%s",
                        clock,
                        day,
                        exc_info=True,
                    )
                row["load_errors"] = int(row.get("load_errors") or 0) + 1
                row["days_skipped"] = int(row.get("days_skipped") or 0) + 1
                continue
            if existing is not None:
                if require_horizon_names:
                    try:
                        covered = bool(names_cover_horizon(existing))
                    except Exception:  # noqa: BLE001
                        if clock not in cover_logged:
                            cover_logged.add(clock)
                            logger.warning(
                                "l2 horizon check failed clock=%s day=%s",
                                clock,
                                day,
                                exc_info=True,
                            )
                        covered = False
                    if not covered:
                        sh.append(day)
                        if _cancelled(cancel_cb):
                            _mark_cancelled(str(clock))
                            stopped = True
                            break
                        continue
                row["cubes_hit"] = int(row.get("cubes_hit") or 0) + 1
                if _cancelled(cancel_cb):
                    _mark_cancelled(str(clock))
                    stopped = True
                    break
                continue
            pend.append(day)
            if _cancelled(cancel_cb):
                _mark_cancelled(str(clock))
                stopped = True
                break
        if stopped:
            break
        pending[str(clock)] = pend
        short[str(clock)] = sh

    def _fail_unread(key: str) -> None:
        row = report["by_clock"].get(key)
        if row is None:
            return
        if int(row.get("cubes_hit") or 0) or pending.get(key) or short.get(key):
            return
        if not int(row.get("load_errors") or 0):
            return
        row["ok"] = False
        row["error"] = "立方读取失败"
        report["ok"] = False
        if not report.get("error"):
            report["error"] = row["error"]

    def _cancel_unwritten(start: int) -> None:
        for later_i in range(max(0, start), len(minute_clocks)):
            later = str(minute_clocks[later_i])
            if pending.get(later) or short.get(later):
                _mark_cancelled(later)

    if stopped:
        # 探活已结束、立方还没写。这些钟不能留 ok。
        for key, days in pending.items():
            if days or short.get(key):
                _mark_cancelled(key)
        return

    want: List[str] = []
    seen_days: set = set()
    order = {day: i for i, day in enumerate(minute_days)}
    for clock in minute_clocks:
        key = str(clock)
        for day in list(pending.get(key) or []) + list(short.get(key) or []):
            if day in seen_days:
                continue
            seen_days.add(day)
            want.append(day)
    want.sort(key=lambda day: order.get(day, 0))
    if not want:
        for clock in minute_clocks:
            _fail_unread(str(clock))
        return

    oo_s = None
    co_s = None

    def _read_prog(done: int, n: int, fam: str) -> None:
        _step_emit(open_steps, done, max(1, n), f"L2 读 {fam} {done}/{n}")

    try:
        oo_s = slice_stock_panels(
            FAMILY_OO,
            code_list,
            want,
            progress_cb=lambda done, n: _read_prog(done, n, "oo"),
            cancel_cb=cancel_cb,
        )
        if oo_s is not None:
            co_s = slice_stock_panels(
                FAMILY_CO,
                code_list,
                want,
                progress_cb=lambda done, n: _read_prog(done, n, "co"),
                cancel_cb=cancel_cb,
            )
        if oo_s is None or co_s is None:
            for clock in minute_clocks:
                key = str(clock)
                if pending.get(key) or short.get(key):
                    _mark_cancelled(key)
            return
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "l2 shared slice failed days=%s..%s",
            want[0],
            want[-1],
            exc_info=True,
        )
        note = str(exc).strip() or "L1 读取失败"
        report["ok"] = False
        report["error"] = note
        for clock in minute_clocks:
            key = str(clock)
            missed = len(pending.get(key) or []) + len(short.get(key) or [])
            row = report["by_clock"].get(key)
            if row is None or not missed:
                continue
            row["ok"] = False
            row["error"] = note
            row["days_skipped"] = int(row.get("days_skipped") or 0) + missed
        return

    for clock_i, clock in enumerate(minute_clocks):
        if _cancelled(cancel_cb):
            _cancel_unwritten(clock_i)
            break
        key = str(clock)
        row = _clock_bucket(report, key, fams[key], n_days)
        step = open_steps + clock_i
        label = f"L2 {n_days}日 · {clock}（{clock_i + 1}/{n_clocks}）"
        days_new = list(pending.get(key) or [])
        days_short = list(short.get(key) or [])
        if not days_new and not days_short:
            _fail_unread(key)
            _step_emit(step, 1, 1, label)
            continue
        clock_days: List[str] = []
        seen_clock: set = set()
        for day in days_new + days_short:
            if day in seen_clock:
                continue
            seen_clock.add(day)
            clock_days.append(day)
        try:
            tc_s = slice_stock_panels(
                fams[key],
                code_list,
                clock_days,
                progress_cb=lambda done, n, fam=key: _read_prog(done, n, fam),
                cancel_cb=cancel_cb,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("l2 tc slice failed clock=%s", key, exc_info=True)
            note = str(exc).strip() or "tc 读取失败"
            row["ok"] = False
            row["error"] = note
            row["days_skipped"] = int(row.get("days_skipped") or 0) + len(clock_days)
            report["ok"] = False
            if not report.get("error"):
                report["error"] = note
            continue
        if tc_s is None:
            _cancel_unwritten(clock_i)
            break

        def _fill_prog(
            done: int,
            total: int,
            message: str = "",
            step_index: int = step,
            text: str = label,
        ) -> None:
            _step_emit(step_index, done, total, message or text)

        def _fill(days: List[str], *, replace: bool) -> Optional[Dict[str, int]]:
            if not days:
                return {}
            day_pos = {day: i + 1 for i, day in enumerate(days)}
            try:
                filled = build_day_cubes_from_lake(
                    code_list,
                    days,
                    family_oo=FAMILY_OO,
                    family_tc=fams[key],
                    family_co=FAMILY_CO,
                    clock=key,
                    universe_codes=code_list,
                    min_items=need,
                    require_tc_day=True,
                    horizon_ok=horizon_ok,
                    progress_cb=_fill_prog,
                    cancel_cb=cancel_cb,
                    progress_total=max(1, len(days)),
                    progress_index=day_pos,
                    label=label,
                    swallow_ensure_errors=True,
                    replace_existing=replace,
                    shared_oo=oo_s,
                    shared_co=co_s,
                    shared_tc=tc_s,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("l2 clock %s failed: %s", key, exc)
                logger.debug("l2 clock failed", exc_info=True)
                row["ok"] = False
                row["error"] = str(exc)
                report["ok"] = False
                return None
            return filled or {}

        def _take(stats: Dict[str, int], *, replace: bool) -> None:
            row["cubes_built"] = int(row.get("cubes_built") or 0) + int(
                stats.get("cubes_built") or 0
            )
            if replace:
                row["slim_reject"] = int(row.get("slim_reject") or 0) + int(
                    stats.get("slim_reject") or 0
                )
                row["slim_reject"] = int(row.get("slim_reject") or 0) + int(
                    stats.get("days_skipped") or 0
                )
                return
            row["days_skipped"] = int(row.get("days_skipped") or 0) + int(
                stats.get("days_skipped") or 0
            )
            row["slim_reject"] = int(row.get("slim_reject") or 0) + int(
                stats.get("slim_reject") or 0
            )

        filled = _fill(days_new, replace=False)
        if filled is not None:
            _take(filled, replace=False)
            replaced = _fill(days_short, replace=True)
            if replaced is not None:
                _take(replaced, replace=True)
        tc_s = None
        if _cancelled(cancel_cb):
            _cancel_unwritten(clock_i)
            break



def materialize_l2(
    codes: Sequence[str],
    *,
    dates: Sequence[str] | None = None,
    clocks: Sequence[str] | None = None,
    lookback_days: int = 60,
    use_trading_days: bool = False,
    progress_cb: ProgressCb = None,
    cancel_cb: CancelCb = None,
    min_lake_frac: float = 0.5,
    require_horizon_names: bool = False,
) -> Dict[str, Any]:
    """Ensure DayCube for codes × dates × clocks（复用 warmup 逻辑）。

    ``require_horizon_names``：非 open 钟拒绝 slim / 缺窗头列的 Cube（H2）。
    开盘钟仍走 warmup。分钟钟整窗 oo、co 各读一次，每个钟的 tc 只读一次。
    未指定 ``dates`` 时，只覆盖本地 5m 里达到截面人数的交易日，不按日线回看窗空跑。
    """
    explicit_dates = any(len(str(d or "")[:10]) >= 10 for d in (dates or []))
    code_list = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    day_list = _resolve_dates(
        dates,
        lookback_days=lookback_days,
        use_trading_days=use_trading_days,
    )
    clock_list = normalize_clocks(clocks)
    report: Dict[str, Any] = {
        "ok": True,
        "n_codes": len(code_list),
        "dates": day_list,
        "clocks": clock_list,
        "by_clock": {},
        "require_horizon_names": bool(require_horizon_names),
        "elapsed_s": 0.0,
    }
    if len(code_list) < 2 or not day_list:
        report["ok"] = False
        report["error"] = "codes < 2 or empty dates"
        return report

    t0 = time.perf_counter()
    from core.research.materialize.progress import emit_scaled

    try:
        lake_frac = float(min_lake_frac if min_lake_frac not in (None, "") else 0.5)
    except (TypeError, ValueError):
        lake_frac = 0.5
    if not (lake_frac > 0):
        lake_frac = 0.5

    n_clocks = max(1, len(clock_list))
    minute_clocks = [c for c in clock_list if str(c) != "open"]
    try:
        if minute_clocks:
            _materialize_l2_with_minute(
                report,
                code_list,
                day_list,
                clock_list,
                minute_clocks,
                lake_frac=lake_frac,
                require_horizon_names=bool(require_horizon_names),
                clip_minute_store=not explicit_dates,
                progress_cb=progress_cb,
                cancel_cb=cancel_cb,
                emit_scaled=emit_scaled,
            )
        else:
            _materialize_l2_open_only(
                report,
                code_list,
                day_list,
                clock_list,
                n_clocks=n_clocks,
                lake_frac=lake_frac,
                progress_cb=progress_cb,
                cancel_cb=cancel_cb,
                emit_scaled=emit_scaled,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("l2 failed: %s", exc)
        logger.debug("l2 failed", exc_info=True)
        report["ok"] = False
        if not report.get("error"):
            report["error"] = str(exc).strip() or "L2 失败"
    report["elapsed_s"] = round(time.perf_counter() - t0, 2)
    if report.get("error") == "cancelled":
        tail = "L2 已取消"
    elif not report.get("ok"):
        tail = f"L2 未完成 · {report['elapsed_s']}s"
    else:
        tail = f"L2 完成 · {report['elapsed_s']}s"
    _emit(progress_cb, 1, 1, tail)
    if report.get("ok") and report.get("error") != "cancelled":
        try:
            from core.research.day_cube_gc import prune_cold

            report["gc"] = prune_cold(extra_codes=code_list)
        except Exception:  # noqa: BLE001
            logger.warning("day cube prune after l2 failed", exc_info=True)
    return report
