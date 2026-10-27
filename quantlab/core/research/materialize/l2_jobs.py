"""L2 DayCube 物化：按日 × 宇宙 × clock ensure。"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from core.research.materialize.catalog import normalize_clocks
from core.research.materialize.cover_policy import LAKE_COVER_FRAC

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
    codes: Sequence[str] | None = None,
) -> List[str]:
    """未指定日期时跟本地够格成交日；仓读不到再退交易日日历。"""
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
    from core.research.materialize.coverage import (
        recent_pool_bar_dates,
        recent_trading_days,
    )

    pooled = recent_pool_bar_dates(n, list(codes or []))
    if len(pooled) >= 2:
        return pooled
    return recent_trading_days(n)


def _attach_open_slim(codes: Sequence[str], days: Sequence[str]) -> int:
    """用日线开盘缺口把 slim 并进已有 open 立方。失败只记日志。

    按 ``CUBE_FILL_CHUNK_DAYS`` 分块写盘，避免一次把整窗 DayCube 载入进程。
    """
    try:
        from core.research.day_cube import attach_slim, clear_memory, forget_dates
        from core.research.materialize.cube_batch import (
            CUBE_FILL_CHUNK_DAYS,
            day_chunks,
        )
        from core.t0.score_policy import build_tau_pool_by_date, load_bars_by_code_for_tau_pool
    except Exception:  # noqa: BLE001
        logger.debug("open slim imports failed", exc_info=True)
        return 0
    day_list = [str(d)[:10] for d in (days or []) if len(str(d or "")[:10]) >= 10]
    if not day_list:
        return 0
    limit = max(20, min(1500, len(day_list) + 8))
    try:
        bars = load_bars_by_code_for_tau_pool(codes, limit=limit)
        pool = build_tau_pool_by_date(bars) or {}
    except Exception:  # noqa: BLE001
        logger.debug("open slim tau pool failed", exc_info=True)
        return 0
    n = 0
    try:
        for chunk in day_chunks(day_list, CUBE_FILL_CHUNK_DAYS):
            for day in chunk:
                slim = pool.get(str(day)[:10])
                if not isinstance(slim, dict) or not slim:
                    continue
                try:
                    if attach_slim(str(day)[:10], codes, clock="open", slim=slim):
                        n += 1
                except Exception:  # noqa: BLE001
                    logger.debug("attach open slim failed day=%s", day, exc_info=True)
            forget_dates(chunk)
    finally:
        clear_memory()
        bars = None
        pool = None
    return n


def _clock_reaches_days(row: dict, *, n_days: int, lake_frac: float) -> bool:
    """单钟 hit+built 达到 ``LAKE_COVER_FRAC``（与 L1 τ / 消费同口径）。"""
    from core.research.materialize.cover_policy import cover_reaches

    try:
        hit = int(row.get("cubes_hit") or 0) + int(row.get("cubes_built") or 0)
    except (TypeError, ValueError):
        hit = 0
    return cover_reaches(hit, n_days, frac=lake_frac, minimum=1)


def _mark_clock_cover_short(
    report: Dict[str, Any],
    row: dict,
    clock: str,
    *,
    n_days: int,
    lake_frac: float,
) -> None:
    """写入统一覆盖不足文案（含 need / slim_reject）。"""
    from core.research.materialize.cover_policy import format_cube_cover_error

    row["ok"] = False
    row["error"] = row.get("error") or format_cube_cover_error(
        clock,
        hit=int(row.get("cubes_hit") or 0),
        built=int(row.get("cubes_built") or 0),
        n_days=n_days,
        frac=lake_frac,
        slim_reject=int(row.get("slim_reject") or 0),
        skipped=int(row.get("days_skipped") or 0),
    )
    report["ok"] = False
    if not report.get("error"):
        report["error"] = row["error"]


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

    days = [str(d)[:10] for d in (day_list or []) if len(str(d or "")[:10]) >= 10]
    try:
        warm = warmup_watching_day_cubes(
            code_list,
            days,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
            min_lake_frac=lake_frac,
            prefetch_l1=False,
            seed_pool=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("l2 open warmup failed: %s", exc)
        logger.debug("l2 open warmup failed", exc_info=True)
        report["ok"] = False
        report["by_clock"][clock] = {"ok": False, "error": str(exc)}
        return
    try:
        from core.research.day_cube import clear_memory

        clear_memory()
    except Exception:  # noqa: BLE001
        logger.debug("clear day_cube memory after warmup failed", exc_info=True)
    slim_n = _attach_open_slim(code_list, days)
    row = {
        "ok": True,
        "slim_days": slim_n,
        **dict(warm or {}),
    }
    spill_err = str((warm or {}).get("error") or "").strip()
    if spill_err:
        row["ok"] = False
        row["error"] = spill_err
        report["ok"] = False
        if not report.get("error"):
            report["error"] = spill_err
        report["by_clock"][clock] = row
        return
    if days and not _clock_reaches_days(row, n_days=len(days), lake_frac=lake_frac):
        _mark_clock_cover_short(
            report, row, clock, n_days=len(days), lake_frac=lake_frac
        )
    report["by_clock"][clock] = row


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
    # 近端优先，避免千日回看从历史尾巴开始烘、做 T 近端一直缺立方
    warm_days = list(reversed(list(day_list or [])))
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
            warm_days,
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
    from core.research.materialize.horizon_names import (
        HORIZON_COVER_FRAC,
        minute_horizon_required_names,
        names_cover_horizon,
    )

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
                "l2 minute window %s days %s (lookback %s)",
                len(minute_days),
                report["minute_span"],
                len(day_list),
            )
    # 近端优先：做 T 回测先吃最近交易日，避免千日回看从 2022 慢慢烘到今天
    if minute_days:
        minute_days = list(reversed(minute_days))
    open_steps = 1 if open_clocks else 0
    n_clocks = max(1, len(minute_clocks))
    n_steps = open_steps + n_clocks
    n_days = max(1, len(minute_days))
    fams = {str(clock): family_for_tc(tau_hm=str(clock)) for clock in minute_clocks}
    def _minute_horizon_ok(cube: Any) -> bool:
        # 真 DayCube 必有 names；无该属性的探测桩视为「已存在」。
        # names=[] 的空立方不当覆盖通过（须重建）。
        if cube is None:
            return False
        if not hasattr(cube, "names"):
            return True
        try:
            names = list(getattr(cube, "names") or [])
        except TypeError:
            return False
        if not names:
            return False
        return bool(
            names_cover_horizon(
                cube,
                min_frac=HORIZON_COVER_FRAC,
                need=minute_horizon_required_names(),
            )
        )

    horizon_ok = _minute_horizon_ok
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
        # 与分钟钟同一窗：未指定 dates 时不拿千日日历空跑 open
        open_days = minute_days if clip_minute_store else list(day_list)
        if clip_minute_store and open_days:
            open_days = list(open_days)  # 已是近端优先
        elif open_days:
            open_days = list(reversed(open_days))
        for clock in open_clocks:
            _warmup_open_clock(
                report,
                code_list,
                open_days,
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
                try:
                    covered = bool(horizon_ok(existing))
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
        reused = 0
        for clock in minute_clocks:
            key = str(clock)
            _fail_unread(key)
            row = report["by_clock"].get(key) or {}
            reused += int(row.get("cubes_hit") or 0)
            if report.get("error") == "cancelled":
                continue
            if not _clock_reaches_days(row, n_days=n_days, lake_frac=lake_frac):
                _mark_clock_cover_short(
                    report, row, key, n_days=n_days, lake_frac=lake_frac
                )
                report["by_clock"][key] = row
        report["l2_incremental"] = {
            "pending_days": 0,
            "cubes_reused": reused,
            "note": "全部按钟 Cube 已命中覆盖门槛，未重建（旧盘缺列时需删立方或短窗 replace）",
        }
        if reused > 0:
            logger.info(
                "l2 minute incremental skip: reused=%s clocks=%s days=%s (no rebuild)",
                reused,
                len(minute_clocks),
                n_days,
            )
        return

    # 分钟钟主依赖 tc_HHMM；oo/co 可空（空 dict ≠ None/取消）
    oo_s: Dict[str, tuple] = {}
    co_s: Dict[str, tuple] = {}

    def _read_prog(done: int, n: int, fam: str) -> None:
        _step_emit(open_steps, done, max(1, n), f"L2 读 {fam} {done}/{n}")

    try:
        oo_got = slice_stock_panels(
            FAMILY_OO,
            code_list,
            want,
            progress_cb=lambda done, n: _read_prog(done, n, "oo"),
            cancel_cb=cancel_cb,
        )
        if oo_got is None:
            for clock in minute_clocks:
                key = str(clock)
                if pending.get(key) or short.get(key):
                    _mark_cancelled(key)
            return
        oo_s = oo_got if isinstance(oo_got, dict) else {}
        co_got = slice_stock_panels(
            FAMILY_CO,
            code_list,
            want,
            progress_cb=lambda done, n: _read_prog(done, n, "co"),
            cancel_cb=cancel_cb,
        )
        if co_got is None:
            for clock in minute_clocks:
                key = str(clock)
                if pending.get(key) or short.get(key):
                    _mark_cancelled(key)
            return
        co_s = co_got if isinstance(co_got, dict) else {}
        if not oo_s or not co_s:
            logger.info(
                "minute L2 without oo/co panels (tc-only cubes) days=%s..%s oo=%s co=%s",
                want[0],
                want[-1],
                len(oo_s) if isinstance(oo_s, dict) else 0,
                len(co_s) if isinstance(co_s, dict) else 0,
            )
    except Exception as exc:  # noqa: BLE001
        # oo/co 读失败不取消分钟钟：以空 panel 继续，靠每钟 tc
        logger.warning(
            "l2 shared oo/co slice failed; continuing tc-only days=%s..%s: %s",
            want[0],
            want[-1],
            exc,
        )
        logger.debug("l2 shared slice failed", exc_info=True)
        oo_s = {}
        co_s = {}
    if not isinstance(oo_s, dict):
        oo_s = {}
    if not isinstance(co_s, dict):
        co_s = {}

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
            if report.get("error") != "cancelled" and not _clock_reaches_days(
                row, n_days=n_days, lake_frac=lake_frac
            ):
                _mark_clock_cover_short(
                    report, row, key, n_days=n_days, lake_frac=lake_frac
                )
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
        # 空 dict：仍进 build（require_tc_day 会跳日 / 覆盖闸收口）；仅记日志
        if isinstance(tc_s, dict) and not tc_s:
            logger.warning(
                "l2 tc panel empty clock=%s days=%s; build will skip/cover-fail",
                key,
                len(clock_days),
            )
        elif not isinstance(tc_s, dict):
            logger.warning(
                "l2 tc slice non-dict clock=%s type=%s; continuing",
                key,
                type(tc_s).__name__,
            )

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
                    require_oo_item=False,
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
            # slim_reject / days_skipped 分计，勿把 skipped 并进 slim（诊断会偏）
            row["slim_reject"] = int(row.get("slim_reject") or 0) + int(
                stats.get("slim_reject") or 0
            )
            row["days_skipped"] = int(row.get("days_skipped") or 0) + int(
                stats.get("days_skipped") or 0
            )
            if replace:
                row["days_short_rebuild"] = int(row.get("days_short_rebuild") or 0) + int(
                    stats.get("cubes_built") or 0
                )

        filled = _fill(days_new, replace=False)
        if filled is not None:
            _take(filled, replace=False)
            replaced = _fill(days_short, replace=True)
            if replaced is not None:
                _take(replaced, replace=True)
        if report.get("error") != "cancelled" and not _clock_reaches_days(
            row, n_days=n_days, lake_frac=lake_frac
        ):
            _mark_clock_cover_short(
                report, row, key, n_days=n_days, lake_frac=lake_frac
            )
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
    progress_cb: ProgressCb = None,
    cancel_cb: CancelCb = None,
    min_lake_frac: float = LAKE_COVER_FRAC,
    require_horizon_names: bool = False,
) -> Dict[str, Any]:
    """Ensure DayCube for codes × dates × clocks（复用 warmup 逻辑）。

    ``require_horizon_names``：兼容旧入参；分钟钟按 ``HORIZON_COVER_FRAC`` 验收窗头列。
    开盘钟仍走 warmup。分钟钟整窗 oo、co 各读一次，每个钟的 tc 只读一次。
    未指定 ``dates`` 时，只覆盖本地 5m 里达到截面人数的交易日，不按日线回看窗空跑。
    """
    explicit_dates = any(len(str(d or "")[:10]) >= 10 for d in (dates or []))
    code_list = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    day_list = _resolve_dates(
        dates,
        lookback_days=lookback_days,
        codes=code_list,
    )
    clock_list = normalize_clocks(clocks)
    has_minute = any(
        str(c).strip().lower() not in ("", "open", "09:30", "0930") for c in clock_list
    )
    report: Dict[str, Any] = {
        "ok": True,
        "n_codes": len(code_list),
        "dates": day_list,
        "clocks": clock_list,
        "by_clock": {},
        "require_horizon_names": True if has_minute else bool(require_horizon_names),
        "elapsed_s": 0.0,
    }
    if len(code_list) < 2 or not day_list:
        report["ok"] = False
        report["error"] = "codes < 2 or empty dates"
        return report

    t0 = time.perf_counter()
    from core.research.materialize.progress import emit_scaled

    try:
        lake_frac = float(
            min_lake_frac if min_lake_frac not in (None, "") else LAKE_COVER_FRAC
        )
    except (TypeError, ValueError):
        lake_frac = float(LAKE_COVER_FRAC)
    if not (lake_frac > 0):
        lake_frac = float(LAKE_COVER_FRAC)

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
    from core.research.materialize.cover_policy import finalize_l2_report

    finalize_l2_report(report, clock_list, lake_frac=lake_frac)
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
        try:
            from core.store_bars_sqlite import checkpoint_bars_db

            report["bars_wal"] = checkpoint_bars_db(mode="TRUNCATE")
        except Exception:  # noqa: BLE001
            logger.warning("bars wal checkpoint after l2 failed", exc_info=True)
    return report
