"""按股票读 L1、按日期块凑齐，再写 DayCube。

一块里每只股票的每个族只打开一次，切出这块日期的行。
股票走完后按天算截面 μ/σ。默认 80 天一块，避免把全部日期的矩阵同时留在内存里。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# 800 只 × 三族 × 约几百列，80 天大约两百多 MB。开盘用这块。
CUBE_FILL_CHUNK_DAYS = 80
# 分钟钟还有窗头列，并且每个钟各读一遍。20 天一块，峰值大约是开盘的四分之一。
CUBE_FILL_MINUTE_CHUNK_DAYS = 20

ProgressCb = Optional[Callable[[int, int, str], None]]
CancelCb = Optional[Callable[[], bool]]


def _cancelled(cb: CancelCb) -> bool:
    if not cb:
        return False
    try:
        return bool(cb())
    except Exception:  # noqa: BLE001
        return False


def _emit(cb: ProgressCb, cur: int, tot: int, msg: str) -> None:
    if not cb:
        return
    try:
        cb(int(cur), max(1, int(tot)), str(msg or ""))
    except Exception:  # noqa: BLE001
        logger.debug("cube batch progress failed", exc_info=True)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def day_chunks(days: Sequence[str], size: int = CUBE_FILL_CHUNK_DAYS) -> List[List[str]]:
    n = max(1, _as_int(size, CUBE_FILL_CHUNK_DAYS) or CUBE_FILL_CHUNK_DAYS)
    out: List[List[str]] = []
    buf: List[str] = []
    for day in days or []:
        text = str(day or "")[:10]
        if len(text) < 10:
            continue
        buf.append(text)
        if len(buf) >= n:
            out.append(buf)
            buf = []
    if buf:
        out.append(buf)
    return out


def _feats_for(
    sliced: Dict[str, tuple],
    code: str,
    day: str,
) -> Optional[Dict[str, float]]:
    try:
        hit = sliced.get(code)
        if not hit or len(hit) < 2:
            return None
        names, by_day = hit[0], hit[1]
        if not isinstance(by_day, dict):
            return None
        row = by_day.get(day)
        if row is None:
            return None
        from core.research.feature_lake import finite_feature_dict

        feats = finite_feature_dict(names, row)
    except Exception:  # noqa: BLE001
        logger.debug("cube batch row failed code=%s day=%s", code, day, exc_info=True)
        return None
    return feats or None


def build_day_cubes_from_lake(
    codes: Sequence[str],
    days: Sequence[str],
    *,
    family_oo: str,
    family_tc: str,
    family_co: str,
    clock: str,
    universe_codes: Sequence[str],
    min_items: int,
    require_tc_day: bool = False,
    require_oo_item: bool = False,
    horizon_ok: Optional[Callable[[Any], bool]] = None,
    on_items: Optional[Callable[[str, List[dict]], None]] = None,
    progress_cb: ProgressCb = None,
    cancel_cb: CancelCb = None,
    chunk_days: int = CUBE_FILL_CHUNK_DAYS,
    progress_total: Optional[int] = None,
    progress_index: Optional[Dict[str, int]] = None,
    label: str = "DayCube",
    swallow_ensure_errors: bool = False,
    replace_existing: bool = False,
    shared_oo: Optional[Dict[str, tuple]] = None,
    shared_co: Optional[Dict[str, tuple]] = None,
) -> Dict[str, int]:
    """为 ``days`` 写立方。返回 ``cubes_built`` / ``days_skipped`` / ``slim_reject``。

    ``require_oo_item``：开盘预热，没有 oo 行的票不进当天矩阵。
    ``require_tc_day``：分钟钟，当天没有任何 tc 行则整日跳过。
    ``shared_oo`` / ``shared_co`` 已由调用方切开时不再读这两族；不传则三族都在这里切。
    ``progress_index`` 是日期到全列表 1-based 位置，进度条按全列表走。
    """
    from core.research.batch_score import ensure_day_cube_for_items
    from core.research.day_cube import forget_dates
    from core.research.feature_lake import slice_stock_panels
    from core.research.lake_replay import assemble_item_from_lake_rows

    code_list: List[str] = []
    seen_codes: set = set()
    for raw in codes or []:
        code = str(raw or "").strip()
        if not code or code in seen_codes:
            continue
        seen_codes.add(code)
        code_list.append(code)
    day_list: List[str] = []
    seen_days: set = set()
    for raw_day in days or []:
        day = str(raw_day or "")[:10]
        if len(day) < 10 or day in seen_days:
            continue
        seen_days.add(day)
        day_list.append(day)
    stats = {"cubes_built": 0, "days_skipped": 0, "slim_reject": 0}
    if len(code_list) < 2 or not day_list:
        return stats

    total = max(1, _as_int(progress_total, len(day_list)) or len(day_list))
    index = progress_index if isinstance(progress_index, dict) else {}
    need = max(2, _as_int(min_items, 2))
    univ = [str(c).strip() for c in (universe_codes or []) if str(c or "").strip()]
    if len(univ) < 2:
        univ = list(code_list)

    def _pos(day: str, fallback: int) -> int:
        try:
            got = index.get(day)
            if got is None:
                return int(fallback)
            return int(got)
        except (TypeError, ValueError):
            return int(fallback)

    for chunk in day_chunks(day_list, chunk_days):
        if _cancelled(cancel_cb):
            break
        anchor = _pos(chunk[0], 1)
        oo_s: Optional[Dict[str, tuple]] = None
        tc_s: Optional[Dict[str, tuple]] = None
        co_s: Optional[Dict[str, tuple]] = None

        def _read_prog(done: int, n: int, fam: str = "") -> None:
            _emit(
                progress_cb,
                anchor,
                total,
                f"{label} {anchor}/{total} · 读 L1 {fam} {done}/{n}",
            )

        try:
            if shared_oo is not None:
                oo_s = shared_oo
            else:
                oo_s = slice_stock_panels(
                    family_oo,
                    code_list,
                    chunk,
                    progress_cb=lambda d, n: _read_prog(d, n, "oo"),
                    cancel_cb=cancel_cb,
                )
            tc_s = (
                slice_stock_panels(
                    family_tc,
                    code_list,
                    chunk,
                    progress_cb=lambda d, n: _read_prog(d, n, "tc"),
                    cancel_cb=cancel_cb,
                )
                if oo_s is not None
                else None
            )
            if shared_co is not None and tc_s is not None:
                co_s = shared_co
            else:
                co_s = (
                    slice_stock_panels(
                        family_co,
                        code_list,
                        chunk,
                        progress_cb=lambda d, n: _read_prog(d, n, "co"),
                        cancel_cb=cancel_cb,
                    )
                    if tc_s is not None
                    else None
                )
        except Exception:  # noqa: BLE001
            logger.warning(
                "cube batch slice failed clock=%s days=%s..%s",
                clock,
                chunk[0],
                chunk[-1],
            )
            logger.debug("cube batch slice failed", exc_info=True)
            stats["days_skipped"] += len(chunk)
            continue
        if oo_s is None or tc_s is None or co_s is None:
            return stats
        try:
            for offset, day in enumerate(chunk):
                if _cancelled(cancel_cb):
                    return stats
                pos = _pos(day, anchor + offset)
                _emit(progress_cb, pos, total, f"{label} {pos}/{total}")
                any_tc = False
                items: List[dict] = []
                for code in code_list:
                    oo = _feats_for(oo_s, code, day)
                    tc = _feats_for(tc_s, code, day)
                    co = _feats_for(co_s, code, day)
                    if tc:
                        any_tc = True
                    if require_oo_item:
                        if not oo:
                            continue
                    elif not oo and not tc:
                        continue
                    try:
                        it = assemble_item_from_lake_rows(
                            code, day, oo=oo, tc=tc, co=co
                        )
                    except Exception:  # noqa: BLE001
                        logger.debug(
                            "cube batch assemble failed code=%s day=%s",
                            code,
                            day,
                            exc_info=True,
                        )
                        continue
                    if it is not None:
                        items.append(it)
                if require_tc_day and not any_tc:
                    stats["days_skipped"] += 1
                    continue
                if len(items) < need:
                    stats["days_skipped"] += 1
                    continue
                try:
                    cube = ensure_day_cube_for_items(
                        items,
                        date=day,
                        clock=clock,
                        persist=True,
                        universe_codes=univ,
                        replace=bool(replace_existing),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("cube batch ensure failed day=%s err=%s", day, exc)
                    logger.debug("cube batch ensure failed day=%s", day, exc_info=True)
                    if swallow_ensure_errors:
                        stats["days_skipped"] += 1
                        continue
                    raise
                if on_items is not None and cube is not None:
                    try:
                        on_items(day, items)
                    except Exception:  # noqa: BLE001
                        logger.debug(
                            "cube batch on_items failed day=%s", day, exc_info=True
                        )
                if cube is None:
                    stats["days_skipped"] += 1
                    continue
                if horizon_ok is not None:
                    try:
                        covered = bool(horizon_ok(cube))
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "cube batch horizon check failed day=%s",
                            day,
                            exc_info=True,
                        )
                        stats["slim_reject"] += 1
                        continue
                    if not covered:
                        stats["slim_reject"] += 1
                        continue
                stats["cubes_built"] += 1
        finally:
            oo_s = None
            tc_s = None
            co_s = None
            forget_dates(chunk)
    return stats
