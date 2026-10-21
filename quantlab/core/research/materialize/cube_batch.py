"""按股票读 L1、按日期块凑齐，再写 DayCube。

开盘一块里每个族只打开一次，再按天算截面 μ/σ。
默认 80 天一块，避免把全部日期的矩阵同时留在内存里。
分钟钟的 L1 在块外按族读一次；写完一块就丢掉进程内立方。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# 800 只 × 三族 × 约几百列，80 天大约两百多 MB。
CUBE_FILL_CHUNK_DAYS = 80

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


def _maybe_enrich_day_cs(
    kept: Sequence[str],
    names: Sequence[str],
    matrix: Any,
    *,
    day: str,
    clock: str,
) -> tuple:
    """组 Cube 前补截面列；失败或形变不合法时退回原矩阵。"""
    import numpy as np

    name_list = list(names)
    try:
        from core.research.tau_panel import enrich_day_matrix_cross_section

        new_names, new_mat, _added = enrich_day_matrix_cross_section(
            kept, name_list, matrix, clock=clock
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "cube CS enrich failed day=%s clock=%s; using aligned matrix",
            day,
            clock,
            exc_info=True,
        )
        return name_list, matrix
    try:
        arr = np.asarray(new_mat, dtype=np.float64)
    except (TypeError, ValueError):
        return name_list, matrix
    if (
        not isinstance(new_names, (list, tuple))
        or arr.ndim != 2
        or arr.shape[0] != len(kept)
        or arr.shape[1] != len(new_names)
    ):
        logger.warning(
            "cube CS enrich shape reject day=%s clock=%s kept=%s names=%s shape=%s",
            day,
            clock,
            len(kept),
            len(new_names) if isinstance(new_names, (list, tuple)) else "?",
            getattr(arr, "shape", None),
        )
        return name_list, matrix
    return list(new_names), arr


def _keep_cube(
    cube: Any,
    *,
    day: str,
    universe: Sequence[str],
    clock: str,
    horizon_ok: Optional[Callable[[Any], bool]],
) -> bool:
    """窗头通过才落盘。失败时删掉磁盘和内存里的真立方。"""
    from core.research.day_cube import DayCube, discard_day_cube, save_day_cube

    if cube is None:
        return False
    if horizon_ok is not None:
        try:
            covered = bool(horizon_ok(cube))
        except Exception:  # noqa: BLE001
            logger.warning("cube batch horizon check failed day=%s", day, exc_info=True)
            covered = False
        if not covered:
            if isinstance(cube, DayCube):
                discard_day_cube(day, universe, clock=clock)
            return False
    if not isinstance(cube, DayCube):
        return True
    try:
        return bool(save_day_cube(cube))
    except Exception:  # noqa: BLE001
        logger.warning("cube batch save failed day=%s", day, exc_info=True)
        return False


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


_ALIAS_PLAN: Optional[tuple] = None


def _alias_plan() -> tuple:
    """别名规则与 ``features_by_code_from_items`` 的盖章顺序一致，只算一次。"""
    global _ALIAS_PLAN
    if _ALIAS_PLAN is not None:
        return _ALIAS_PLAN
    from core.signal.factors.z_co import Z_CO_LEGACY_KEYS, Z_CO_PREFIX
    from core.signal.factors.z_oo import Z_OO_PREFIX, z_oo_legacy_keys
    from core.signal.factors.z_tc import Z_TC_LEGACY_KEYS, Z_TC_PREFIX
    from core.signal.factors.z_th import Z_TH_LEGACY_KEYS, Z_TH_PREFIX

    oo = tuple((leg, f"{Z_OO_PREFIX}{leg}") for leg in z_oo_legacy_keys())
    tc = tuple((leg, f"{Z_TC_PREFIX}{leg}") for leg in Z_TC_LEGACY_KEYS)
    th = tuple(
        (leg, f"{Z_TC_PREFIX}{leg}", f"{Z_TH_PREFIX}{leg}") for leg in Z_TH_LEGACY_KEYS
    )
    co = tuple((leg, f"{Z_CO_PREFIX}{leg}") for leg in Z_CO_LEGACY_KEYS)
    _ALIAS_PLAN = (oo, tc, th, co)
    return _ALIAS_PLAN


def _stack_days(
    sliced: Optional[Dict[str, tuple]],
    codes: Sequence[str],
    days: Sequence[str],
):
    """把已切开的行叠成 ``(n_codes, n_days, n_features)``。缺行是 NaN。"""
    import numpy as np

    n_codes = len(codes)
    n_days = len(days)
    names: List[str] = []
    seen = set()
    for code in codes:
        hit = (sliced or {}).get(code)
        if not hit or len(hit) < 2 or not isinstance(hit[1], dict):
            continue
        try:
            raw_names = list(hit[0] or [])
        except TypeError:
            continue
        for raw in raw_names:
            name = str(raw or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)
    width = len(names)
    out = np.full((n_codes, n_days, width), np.nan, dtype=np.float64)
    if width == 0 or not sliced:
        return names, out
    pos = {name: j for j, name in enumerate(names)}
    code_pos = {code: i for i, code in enumerate(codes)}
    for code, hit in sliced.items():
        i = code_pos.get(code)
        if i is None or not hit or len(hit) < 2 or not isinstance(hit[1], dict):
            continue
        try:
            src_names = [str(raw or "") for raw in (hit[0] or [])]
        except TypeError:
            continue
        idx = [pos.get(name, -1) for name in src_names]
        aligned = src_names == names
        by_day = hit[1]
        for k, day in enumerate(days):
            row = by_day.get(day)
            if row is None:
                continue
            try:
                vec = np.asarray(row, dtype=np.float64).reshape(-1)
            except (TypeError, ValueError):
                continue
            if aligned:
                n = min(width, int(vec.shape[0]))
                if n:
                    out[i, k, :n] = vec[:n]
                continue
            n = min(len(idx), int(vec.shape[0]))
            for j in range(n):
                dest = idx[j]
                if dest >= 0:
                    out[i, k, dest] = vec[j]
    return names, out


def _overlay(cols: Dict[str, Any], n: int, names: Sequence[str], block: Any) -> None:
    import numpy as np

    if block is None or not names:
        return
    arr = np.asarray(block, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[0] != n:
        return
    width = min(len(names), int(arr.shape[1]))
    for j in range(width):
        name = str(names[j] or "").strip()
        if not name:
            continue
        vals = arr[:, j]
        finite = np.isfinite(vals)
        if not bool(finite.any()):
            continue
        col = cols.get(name)
        if col is None:
            col = np.full(n, np.nan, dtype=np.float64)
            cols[name] = col
        col[finite] = vals[finite]


def _row_finite(block: Any, n: int):
    import numpy as np

    if block is None:
        return np.zeros(n, dtype=bool)
    arr = np.asarray(block, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[0] != n or arr.shape[1] == 0:
        return np.zeros(n, dtype=bool)
    return np.isfinite(arr).any(axis=1)


def _pick_source(cols: Dict[str, Any], n: int, names: Sequence[str]):
    import numpy as np

    src = None
    for name in names:
        col = cols.get(name)
        if col is None:
            continue
        if src is None:
            src = np.array(col, dtype=np.float64, copy=True)
            continue
        need = ~np.isfinite(src) & np.isfinite(col)
        src[need] = col[need]
    return src


def _write_alias(cols: Dict[str, Any], n: int, src: Any, dest: str) -> None:
    import numpy as np

    if src is None:
        return
    mask = np.isfinite(src)
    if not bool(mask.any()):
        return
    col = cols.get(dest)
    if col is None:
        col = np.full(n, np.nan, dtype=np.float64)
        cols[dest] = col
    col[mask] = src[mask]


def _apply_pairs(cols: Dict[str, Any], n: int, pairs: Sequence[tuple], *, kind: str) -> None:
    for item in pairs:
        if kind == "th":
            leg, tc_name, th_name = item
            src = _pick_source(cols, n, (leg, tc_name, th_name))
            _write_alias(cols, n, src, th_name)
            _write_alias(cols, n, src, leg)
            continue
        leg, canon = item
        src = _pick_source(cols, n, (leg, canon))
        _write_alias(cols, n, src, canon)
        _write_alias(cols, n, src, leg)


def align_day_matrix(
    codes: Sequence[str],
    *,
    oo_names: Sequence[str],
    oo_block: Any,
    tc_names: Sequence[str],
    tc_block: Any,
    co_names: Sequence[str],
    co_block: Any,
    require_oo_item: bool = False,
    require_tc_day: bool = False,
    plan: Optional[tuple] = None,
):
    """一天的三族行合成立方矩阵。

    返回 ``(codes, names, X)``。当天没有 tc、或留下的票不足调用方门槛时返回 ``None``。
    列顺序和别名与 items → ``build_day_cube`` 一致：先 oo 盖章，再盖 tc、co，然后 τc / τh / co。
    只有 co、没有 oo 也没有 tc 的票不进矩阵。
    """
    import numpy as np

    code_list = [str(c) for c in codes]
    n = len(code_list)
    if n == 0:
        return None
    has_oo = _row_finite(oo_block, n)
    has_tc = _row_finite(tc_block, n)
    if require_tc_day and not bool(has_tc.any()):
        return None
    include = has_oo if require_oo_item else (has_oo | has_tc)
    if not bool(include.any()):
        return None
    rules = plan if plan is not None else _alias_plan()
    oo_pairs, tc_pairs, th_pairs, co_pairs = rules
    cols: Dict[str, Any] = {}
    _overlay(cols, n, oo_names, oo_block)
    _apply_pairs(cols, n, oo_pairs, kind="pair")
    _overlay(cols, n, tc_names, tc_block)
    _overlay(cols, n, co_names, co_block)
    _apply_pairs(cols, n, tc_pairs, kind="pair")
    _apply_pairs(cols, n, th_pairs, kind="th")
    _apply_pairs(cols, n, co_pairs, kind="pair")
    order = [i for i in range(n) if bool(include[i])]
    order.sort(key=lambda i: code_list[i])
    if len(order) < 2:
        return None
    picked = np.asarray(order, dtype=int)
    names = sorted(
        name
        for name, col in cols.items()
        if bool(np.isfinite(np.asarray(col)[picked]).any())
    )
    if not names:
        return None
    kept = [code_list[i] for i in order]
    stacked = [np.asarray(cols[name], dtype=np.float64)[picked] for name in names]
    matrix = np.column_stack(stacked)
    return kept, names, matrix


def _write_vectorized_days(
    chunk: Sequence[str],
    *,
    code_list: Sequence[str],
    oo_s: Dict[str, tuple],
    tc_s: Dict[str, tuple],
    co_s: Dict[str, tuple],
    clock: str,
    univ: Sequence[str],
    need: int,
    require_tc_day: bool,
    require_oo_item: bool,
    horizon_ok: Optional[Callable[[Any], bool]],
    progress_cb: ProgressCb,
    cancel_cb: CancelCb,
    total: int,
    index: Dict[str, int],
    label: str,
    anchor: int,
    replace_existing: bool,
    swallow_ensure_errors: bool,
    stats: Dict[str, int],
    plan: tuple,
) -> bool:
    """用矩阵写这一块日期。取消时返回 True，调用方应停掉整个填充。"""
    from core.research.batch_score import ensure_day_cube_for_items

    try:
        oo_names, oo_x = _stack_days(oo_s, code_list, chunk)
        tc_names, tc_x = _stack_days(tc_s, code_list, chunk)
        co_names, co_x = _stack_days(co_s, code_list, chunk)
    except Exception:  # noqa: BLE001
        logger.warning(
            "cube batch stack failed clock=%s days=%s..%s",
            clock,
            chunk[0] if chunk else "",
            chunk[-1] if chunk else "",
            exc_info=True,
        )
        if swallow_ensure_errors:
            stats["days_skipped"] += len(list(chunk))
            return False
        raise

    def _pos(day: str, fallback: int) -> int:
        try:
            got = index.get(day)
            if got is None:
                return int(fallback)
            return int(got)
        except (TypeError, ValueError):
            return int(fallback)

    for offset, day in enumerate(chunk):
        if _cancelled(cancel_cb):
            return True
        pos = _pos(day, anchor + offset)
        _emit(progress_cb, pos, total, f"{label} {pos}/{total}")
        try:
            aligned = align_day_matrix(
                code_list,
                oo_names=oo_names,
                oo_block=oo_x[:, offset, :],
                tc_names=tc_names,
                tc_block=tc_x[:, offset, :],
                co_names=co_names,
                co_block=co_x[:, offset, :],
                require_oo_item=require_oo_item,
                require_tc_day=require_tc_day,
                plan=plan,
            )
        except Exception:  # noqa: BLE001
            logger.warning("cube batch align failed day=%s", day, exc_info=True)
            if swallow_ensure_errors:
                stats["days_skipped"] += 1
                continue
            raise
        if aligned is None or len(aligned[0]) < need:
            stats["days_skipped"] += 1
            continue
        kept, names, matrix = aligned
        clk = str(clock or "").strip() or "open"
        names, matrix = _maybe_enrich_day_cs(kept, names, matrix, day=day, clock=clk)
        try:
            cube = ensure_day_cube_for_items(
                [],
                date=day,
                clock=clock,
                persist=False,
                universe_codes=univ,
                replace=bool(replace_existing),
                aligned=(kept, names, matrix),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("cube batch ensure failed day=%s err=%s", day, exc)
            logger.debug("cube batch ensure failed day=%s", day, exc_info=True)
            if swallow_ensure_errors:
                stats["days_skipped"] += 1
                continue
            raise
        if not _keep_cube(
            cube,
            day=day,
            universe=univ,
            clock=clock,
            horizon_ok=horizon_ok,
        ):
            if cube is None:
                stats["days_skipped"] += 1
            else:
                stats["slim_reject"] += 1
            continue
        stats["cubes_built"] += 1
    return False


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
    shared_tc: Optional[Dict[str, tuple]] = None,
) -> Dict[str, int]:
    """为 ``days`` 写立方。返回 ``cubes_built`` / ``days_skipped`` / ``slim_reject``。

    ``require_oo_item``：开盘预热，没有 oo 行的票不进当天矩阵。
    ``require_tc_day``：分钟钟，当天没有任何 tc 行则整日跳过。
    ``shared_oo`` / ``shared_tc`` / ``shared_co`` 已由调用方切开时不再读该族。
    没有 ``on_items`` 时按矩阵落盘，别名与 items 路径一致。
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
    plan = None
    if on_items is None:
        try:
            plan = _alias_plan()
        except Exception:  # noqa: BLE001
            logger.warning("cube alias plan failed; using item path", exc_info=True)
            plan = None

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

        def _as_panel(raw: Any) -> Optional[Dict[str, tuple]]:
            """None=取消；明确脏类型当空 panel；其余 mapping 原样交给下游。"""
            if raw is None:
                return None
            if isinstance(raw, dict):
                return raw  # type: ignore[return-value]
            if isinstance(raw, (str, bytes, list, tuple, set, int, float, bool)):
                return {}
            # MagicMock / 自定义 mapping：保持引用
            return raw  # type: ignore[return-value]

        try:
            if shared_oo is not None:
                oo_s = _as_panel(shared_oo) or {}
            else:
                oo_got = slice_stock_panels(
                    family_oo,
                    code_list,
                    chunk,
                    progress_cb=lambda d, n: _read_prog(d, n, "oo"),
                    cancel_cb=cancel_cb,
                )
                if oo_got is None:
                    return stats  # 取消
                oo_s = _as_panel(oo_got) or {}
            if shared_tc is not None:
                tc_s = _as_panel(shared_tc)
                if tc_s is None:
                    return stats
            else:
                tc_got = slice_stock_panels(
                    family_tc,
                    code_list,
                    chunk,
                    progress_cb=lambda d, n: _read_prog(d, n, "tc"),
                    cancel_cb=cancel_cb,
                )
                if tc_got is None:
                    return stats  # 取消
                tc_s = _as_panel(tc_got)
            if shared_co is not None:
                co_s = _as_panel(shared_co) or {}
            else:
                co_got = slice_stock_panels(
                    family_co,
                    code_list,
                    chunk,
                    progress_cb=lambda d, n: _read_prog(d, n, "co"),
                    cancel_cb=cancel_cb,
                )
                if co_got is None:
                    return stats  # 取消
                co_s = _as_panel(co_got) or {}
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
        # 空 dict 表示该族无行（允许 tc-only）；None 仅表示取消（上面已 return）
        if oo_s is None:
            oo_s = {}
        if co_s is None:
            co_s = {}
        if tc_s is None:
            return stats
        if require_oo_item and not oo_s:
            stats["days_skipped"] += len(chunk)
            continue
        if require_tc_day and not tc_s:
            stats["days_skipped"] += len(chunk)
            continue
        try:
            if on_items is None and plan is not None:
                if _write_vectorized_days(
                    chunk,
                    code_list=code_list,
                    oo_s=oo_s,
                    tc_s=tc_s,
                    co_s=co_s,
                    clock=clock,
                    univ=univ,
                    need=need,
                    require_tc_day=require_tc_day,
                    require_oo_item=require_oo_item,
                    horizon_ok=horizon_ok,
                    progress_cb=progress_cb,
                    cancel_cb=cancel_cb,
                    total=total,
                    index=index,
                    label=label,
                    anchor=anchor,
                    replace_existing=replace_existing,
                    swallow_ensure_errors=swallow_ensure_errors,
                    stats=stats,
                    plan=plan,
                ):
                    return stats
                continue
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
                        persist=False,
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
                if not _keep_cube(
                    cube,
                    day=day,
                    universe=univ,
                    clock=clock,
                    horizon_ok=horizon_ok,
                ):
                    if cube is None:
                        stats["days_skipped"] += 1
                    else:
                        stats["slim_reject"] += 1
                    continue
                stats["cubes_built"] += 1
        finally:
            oo_s = None
            tc_s = None
            co_s = None
            forget_dates(chunk)
    return stats
