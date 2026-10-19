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
) -> List[str]:
    explicit = sorted(
        {str(d)[:10] for d in (dates or []) if len(str(d or "")[:10]) >= 10}
    )
    if explicit:
        return explicit
    # 无显式日期：用最近 lookback_days 个日历日（消费方有交易日过滤；Cube miss 会跳过）
    n = max(2, min(400, int(lookback_days or 60)))
    today = datetime.now().date()
    out: List[str] = []
    for i in range(n):
        out.append((today - timedelta(days=i)).isoformat())
    return sorted(set(out))


def materialize_l2(
    codes: Sequence[str],
    *,
    dates: Sequence[str] | None = None,
    clocks: Sequence[str] | None = None,
    lookback_days: int = 60,
    progress_cb: ProgressCb = None,
    cancel_cb: CancelCb = None,
    min_lake_frac: float = 0.5,
    require_horizon_names: bool = False,
) -> Dict[str, Any]:
    """Ensure DayCube for codes × dates × clocks（复用 warmup 逻辑）。

    ``require_horizon_names``：非 open 钟拒绝 slim / 缺窗头列的 Cube（H2）。
    """
    from core.t0.score_policy import warmup_watching_day_cubes

    code_list = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    day_list = _resolve_dates(dates, lookback_days=lookback_days)
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
    # open：warmup_watching_day_cubes；其它钟：按日读 tc_HHMM L1 → ensure Cube
    for ci, clock in enumerate(clock_list):
        if _cancelled(cancel_cb):
            report["ok"] = False
            report["error"] = "cancelled"
            break
        _emit(
            progress_cb,
            ci,
            len(clock_list),
            f"L2 {clock}（{ci + 1}/{len(clock_list)}）…",
        )
        if str(clock) == "open":

            def _pcb(done: int, total: int, message: str = "") -> None:
                _emit(
                    progress_cb,
                    done,
                    max(1, total),
                    message or f"L2 open {done}/{total}",
                )

            # 独立物化刚写完/将读 L1：跳过整池 panel prefetch（易卡 current=0 被 Job 回收）
            warm = warmup_watching_day_cubes(
                code_list,
                day_list,
                progress_cb=_pcb,
                cancel_cb=cancel_cb,
                min_lake_frac=float(min_lake_frac),
                prefetch_l1=False,
            )
            report["by_clock"][clock] = {
                "ok": True,
                **dict(warm or {}),
            }
            continue
        # 非 open：按钟读 L1 ``tc_HHMM``（缺则跳过该日），再 ensure Cube
        built = 0
        hit = 0
        skipped = 0
        slim_reject = 0
        try:
            from core.research.batch_score import ensure_day_cube_for_items
            from core.research.day_cube import load_day_cube
            from core.research.feature_lake import load_day_rows
            from core.research.lake_bridge import FAMILY_CO, FAMILY_OO, family_for_tc
            from core.research.lake_replay import assemble_item_from_lake_rows
            from core.research.materialize.horizon_names import names_cover_horizon

            fam_tc = family_for_tc(tau_hm=str(clock))
            need = max(2, int(len(code_list) * float(min_lake_frac) + 0.999))
            for day in day_list:
                if _cancelled(cancel_cb):
                    break
                try:
                    existing = load_day_cube(day, code_list, clock=clock)
                except Exception:  # noqa: BLE001
                    existing = None
                if existing is not None:
                    if require_horizon_names and not names_cover_horizon(existing):
                        slim_reject += 1
                        # 覆盖不足：重建
                    else:
                        hit += 1
                        continue
                oo_m = load_day_rows(FAMILY_OO, code_list, day) or {}
                tc_m = load_day_rows(fam_tc, code_list, day) or {}
                if not tc_m:
                    skipped += 1
                    continue
                co_m = load_day_rows(FAMILY_CO, code_list, day) or {}
                items = []
                for c in code_list:
                    oo = oo_m.get(c)
                    tc = tc_m.get(c)
                    if not oo and not tc:
                        continue
                    it = assemble_item_from_lake_rows(
                        c, day, oo=oo, tc=tc, co=co_m.get(c)
                    )
                    if it is not None:
                        items.append(it)
                if len(items) < need:
                    skipped += 1
                    continue
                cube = ensure_day_cube_for_items(
                    items,
                    date=day,
                    clock=clock,
                    persist=True,
                    universe_codes=code_list,
                )
                if cube is None:
                    skipped += 1
                    continue
                if require_horizon_names and not names_cover_horizon(cube):
                    slim_reject += 1
                    continue
                built += 1
        except Exception as exc:  # noqa: BLE001
            report["by_clock"][clock] = {
                "ok": False,
                "error": str(exc),
                "cubes_hit": hit,
                "cubes_built": built,
                "days_skipped": skipped,
                "slim_reject": slim_reject,
            }
            report["ok"] = False
            continue
        report["by_clock"][clock] = {
            "ok": True,
            "cubes_hit": hit,
            "cubes_built": built,
            "days_skipped": skipped,
            "slim_reject": slim_reject,
            "days": len(day_list),
            "tc_family": fam_tc,
        }
    report["elapsed_s"] = round(time.perf_counter() - t0, 2)
    _emit(progress_cb, 1, 1, f"L2 完成 · {report['elapsed_s']}s")
    return report
