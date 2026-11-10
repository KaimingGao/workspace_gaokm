"""会话日开盘 PIT：唯一写/读契约与 provenance。

盘中：行情今开 → provisional 立方（``builder=session_open_quote``）。
日线入库后：lake 今开 → final 立方（``builder=lake_open``）。
warmup / spill 对 provisional / legacy 不得 ``status=ok`` 跳过重建。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Sequence

logger = logging.getLogger(__name__)

PHASE_PROVISIONAL = "provisional"
PHASE_FINAL = "final"
OPEN_SOURCE_QUOTE = "quote"
OPEN_SOURCE_DAILY = "daily"
BUILDER_SESSION_OPEN_QUOTE = "session_open_quote"
BUILDER_LAKE_OPEN = "lake_open"

ProgressCb = Optional[Callable[[int, int, str], None]]

_PROVENANCE_KEYS = (
    "phase",
    "open_source",
    "builder",
    "eod_as_of",
    "built_at",
    "missing_open_n",
)


def provenance_dict(
    *,
    phase: str,
    open_source: str,
    builder: str,
    eod_as_of: Optional[str] = None,
    built_at: Optional[str] = None,
    missing_open_n: Optional[int] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "phase": str(phase or "").strip().lower() or PHASE_PROVISIONAL,
        "open_source": str(open_source or "").strip().lower() or OPEN_SOURCE_QUOTE,
        "builder": str(builder or "").strip().lower() or BUILDER_SESSION_OPEN_QUOTE,
    }
    eod = str(eod_as_of or "")[:10]
    if len(eod) >= 10:
        out["eod_as_of"] = eod
    out["built_at"] = str(
        built_at
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )
    if missing_open_n is not None:
        try:
            out["missing_open_n"] = int(missing_open_n)
        except (TypeError, ValueError):
            pass
    return out


def stamp_cube_provenance(cube: Any, **kwargs: Any) -> Any:
    """把 provenance 写入 ``cube.slim``（save_day_cube 会抬到 meta 顶栏）。"""
    if cube is None:
        return cube
    slim = dict(getattr(cube, "slim", None) or {})
    slim.update(provenance_dict(**kwargs))
    try:
        cube.slim = slim
    except Exception:  # noqa: BLE001
        logger.debug("stamp cube provenance failed", exc_info=True)
    return cube


def provenance_from_mapping(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """从 cube meta / slim / 出分行抽出 provenance（缺字段不补默认终态）。"""
    d = raw if isinstance(raw, dict) else {}
    slim = d.get("slim") if isinstance(d.get("slim"), dict) else {}
    out: Dict[str, Any] = {}
    for key in _PROVENANCE_KEYS:
        val = d.get(key)
        if val is None or val == "":
            val = slim.get(key)
        if val is None or val == "":
            continue
        if key in ("phase", "open_source", "builder"):
            out[key] = str(val).strip().lower()
        elif key == "eod_as_of":
            text = str(val)[:10]
            if len(text) >= 10:
                out[key] = text
        elif key == "missing_open_n":
            try:
                out[key] = int(val)
            except (TypeError, ValueError):
                pass
        else:
            out[key] = val
    return out


def meta_is_final_open(meta: Optional[Dict[str, Any]]) -> bool:
    """终态 open 立方：phase=final 且 builder=lake_open。"""
    prov = provenance_from_mapping(meta)
    return (
        prov.get("phase") == PHASE_FINAL
        and prov.get("builder") == BUILDER_LAKE_OPEN
    )


def meta_needs_lake_rebuild(meta: Optional[Dict[str, Any]]) -> bool:
    """warmup/spill：provisional / legacy / 非 lake 终态 → 不得 ok-skip。"""
    if not isinstance(meta, dict):
        return True
    return not meta_is_final_open(meta)


def daily_bars_ready_for_day(
    day: str,
    codes: Sequence[str] | None = None,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """本地完整日线已覆盖到 ``day``（可晋升 final）。"""
    day_s = str(day or "")[:10]
    if len(day_s) < 10:
        return False
    try:
        from core.research.materialize.coverage import complete_daily_as_of

        as_of = str(complete_daily_as_of(codes, now=now) or "")[:10]
    except Exception:  # noqa: BLE001
        logger.debug("complete_daily_as_of failed", exc_info=True)
        return False
    return len(as_of) >= 10 and as_of >= day_s


def should_force_open_lake_rebuild(
    day: str,
    codes: Sequence[str],
    meta: Optional[Dict[str, Any]],
) -> bool:
    """日线已齐且立方仍是 provisional/legacy/半池 final → L2 预热必须重建。"""
    if meta_needs_lake_rebuild(meta):
        return daily_bars_ready_for_day(day, codes)
    # 已 final 但截面成员明显少于宇宙门槛 → 半池卡住，强制重建
    if not isinstance(meta, dict):
        return False
    if not daily_bars_ready_for_day(day, codes):
        return False
    try:
        from core.research.materialize.cover_policy import LAKE_COVER_FRAC, cover_need

        code_list = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
        need = cover_need(len(code_list), frac=LAKE_COVER_FRAC, minimum=2)
        n_cube = int(meta.get("n_codes") or 0)
    except Exception:  # noqa: BLE001
        return False
    return bool(need > 0 and n_cube > 0 and n_cube < need)


def force_open_rebuild_after_status_ok(
    day: str,
    codes: Sequence[str],
    *,
    clock: str = "open",
) -> bool:
    """``day_cube_status==ok`` 后是否仍须重建。

    读 meta / provenance 失败时 fail-closed：日线已齐 → 重建；连日线状态都读不了 → 重建。
    warmup / spill 共用，避免「异常吞掉后当 hit」。
    """
    try:
        from core.research.day_cube import read_day_cube_meta

        meta = read_day_cube_meta(day, codes, clock=str(clock or "open"))
        return should_force_open_lake_rebuild(day, codes, meta)
    except Exception:  # noqa: BLE001
        logger.warning(
            "open provenance check failed day=%s clock=%s",
            day,
            clock,
            exc_info=True,
        )
        try:
            return bool(daily_bars_ready_for_day(day, codes))
        except Exception:  # noqa: BLE001
            return True


def read_open_cube_meta(
    day: str,
    codes: Sequence[str],
    *,
    clock: str = "open",
) -> Optional[Dict[str, Any]]:
    from core.research.day_cube import read_day_cube_meta

    return read_day_cube_meta(day, codes, clock=clock)


def prev_eod_as_of(day: str, codes: Sequence[str]) -> Optional[str]:
    """临时立方的因子截止：完整日线 as_of，但不超过 T−1。"""
    day_s = str(day or "")[:10]
    try:
        from core.research.materialize.coverage import complete_daily_as_of

        as_of = str(complete_daily_as_of(codes) or "")[:10]
    except Exception:  # noqa: BLE001
        return None
    if len(as_of) < 10:
        return None
    if as_of >= day_s:
        # 日线已含 T 时，临时路径仍只用 <T 的 hist；截止标到前一可用日。
        try:
            from core.market.calendar import prev_trading_day

            prev = str(prev_trading_day(day_s) or "")[:10]
            return prev if len(prev) >= 10 else as_of
        except Exception:  # noqa: BLE001
            return as_of
    return as_of


def ensure_session_open_l2(
    *,
    cycle_day: Optional[str] = None,
    codes: Sequence[str] | None = None,
    watching_limit: Optional[int] = None,
    progress_cb: ProgressCb = None,
    prefer: str = "auto",
) -> Dict[str, Any]:
    """唯一写入口：日线齐则 lake final，否则行情 provisional。

    ``prefer``: ``auto`` | ``quote`` | ``daily``。
    """
    from core.research.materialize.orchestrate import resolve_materialize_codes
    from core.signal.session_pit import oo_cycle_date
    from core.watching.store import WATCHING_MAX_SIZE

    prefer_s = str(prefer or "auto").strip().lower()
    if prefer_s not in ("auto", "quote", "daily"):
        prefer_s = "auto"
    limit = int(watching_limit or WATCHING_MAX_SIZE)
    day = str(cycle_day or oo_cycle_date() or "")[:10]
    out: Dict[str, Any] = {
        "ok": False,
        "success": False,
        "cycle_day": day or None,
        "clock": "open",
        "prefer": prefer_s,
    }
    if len(day) < 10:
        out["error"] = "无会话日"
        return out

    code_list = resolve_materialize_codes(codes=codes, watching_limit=limit)
    out["n_universe"] = len(code_list)
    if len(code_list) < 2:
        out["error"] = "观察池/宇宙不足 2 只"
        return out

    ready = daily_bars_ready_for_day(day, code_list)
    out["daily_ready"] = bool(ready)
    want_daily = prefer_s == "daily" or (prefer_s == "auto" and ready)
    if want_daily:
        lake = _build_final_open_from_lake(
            day,
            code_list,
            progress_cb=progress_cb,
        )
        out.update(lake)
        if out.get("ok"):
            return out
        if prefer_s == "daily":
            return out
        # auto：湖重建失败则回落行情临时立方（日线已齐时标 needs_final，勿当终态）
        out["lake_error"] = out.get("error")
        out.pop("error", None)
        if ready:
            out["needs_final"] = True

    from core.research.materialize.session_open import materialize_session_open_l2

    quote = materialize_session_open_l2(
        cycle_day=day,
        codes=code_list,
        watching_limit=limit,
        progress_cb=progress_cb,
    )
    out.update(quote)
    if out.get("ok"):
        out["phase"] = PHASE_PROVISIONAL
        out["open_source"] = OPEN_SOURCE_QUOTE
        out["builder"] = BUILDER_SESSION_OPEN_QUOTE
        if ready or out.get("needs_final"):
            out["needs_final"] = True
    return out


def membership_drift(
    prev_n: Optional[int],
    new_n: Optional[int],
    *,
    frac_thresh: float = 0.02,
) -> Optional[Dict[str, Any]]:
    """截面成员数相对变化超过阈值时返回漂移摘要。"""
    try:
        a = int(prev_n) if prev_n is not None else None
        b = int(new_n) if new_n is not None else None
    except (TypeError, ValueError):
        return None
    if a is None or b is None or a <= 0:
        return None
    frac = abs(b - a) / float(a)
    if frac < float(frac_thresh):
        return None
    return {
        "prev_n_codes": a,
        "n_codes": b,
        "delta": b - a,
        "frac": round(frac, 4),
        "note": f"截面成员 {a}→{b}（{frac:.1%}），z 已重标定",
    }


def _build_final_open_from_lake(
    day: str,
    codes: Sequence[str],
    *,
    progress_cb: ProgressCb = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ok": False,
        "success": False,
        "cycle_day": day,
        "clock": "open",
        "phase": PHASE_FINAL,
        "open_source": OPEN_SOURCE_DAILY,
        "builder": BUILDER_LAKE_OPEN,
    }
    try:
        from core.research.day_cube import day_cube_status, forget_dates
        from core.research.lake_bridge import FAMILY_OO, FAMILY_TC
        from core.research.materialize.cover_policy import LAKE_COVER_FRAC, cover_need
        from core.research.materialize.cube_batch import build_day_cubes_from_lake
    except Exception as exc:  # noqa: BLE001
        logger.warning("final open lake imports failed day=%s", day, exc_info=True)
        out["error"] = f"lake open 重建失败: {exc}"
        return out

    code_list = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    need = cover_need(len(code_list), frac=LAKE_COVER_FRAC, minimum=2)
    prev_meta = read_open_cube_meta(day, code_list, clock="open") or {}
    prev_n = prev_meta.get("n_codes")
    prev_phase = str(prev_meta.get("phase") or "").strip().lower()
    out["n_universe"] = len(code_list)
    try:
        stats = build_day_cubes_from_lake(
            code_list,
            [day],
            family_oo=FAMILY_OO,
            family_tc=FAMILY_TC,
            clock="open",
            universe_codes=code_list,
            min_items=need,
            require_oo_item=True,
            require_tc_day=True,
            replace_existing=True,
            progress_cb=progress_cb,
            label=f"终态 open {day}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("final open lake build failed day=%s", day, exc_info=True)
        out["error"] = f"lake open 重建失败: {exc}"
        return out
    built = int((stats or {}).get("cubes_built") or 0)
    out["cubes_built"] = built
    out["days_skipped"] = int((stats or {}).get("days_skipped") or 0)
    if built < 1:
        out["error"] = (
            f"会话日 {day} lake open 未建成"
            f"（skipped={out['days_skipped']}）"
        )
        return out
    try:
        forget_dates([day], clock="open")
    except Exception:  # noqa: BLE001
        logger.debug("forget open cube mem after lake build failed", exc_info=True)
    status = day_cube_status(day, code_list, clock="open")
    out["status"] = status
    if status != "ok":
        out["error"] = f"会话日 {day} lake open 落盘后 status={status}"
        return out
    meta = read_open_cube_meta(day, code_list, clock="open") or {}
    if not meta_is_final_open(meta):
        # 旧路径未打戳时补写一次（读出再 stamp 保存）
        try:
            from core.research.day_cube import load_day_cube, save_day_cube

            cube = load_day_cube(day, code_list, clock="open", remember=False)
            if cube is not None:
                stamp_cube_provenance(
                    cube,
                    phase=PHASE_FINAL,
                    open_source=OPEN_SOURCE_DAILY,
                    builder=BUILDER_LAKE_OPEN,
                    eod_as_of=day,
                )
                save_day_cube(cube)
        except Exception:  # noqa: BLE001
            logger.debug("backfill final provenance failed", exc_info=True)
    out["ok"] = True
    out["success"] = True
    out["persisted"] = True
    n_cube = int(meta.get("n_codes") or 0) or None
    out["n_cube_codes"] = n_cube
    drift = membership_drift(prev_n, n_cube)
    if drift:
        out["membership_drift"] = drift
    if prev_phase == PHASE_PROVISIONAL or (
        prev_meta and meta_needs_lake_rebuild(prev_meta)
    ):
        out["promoted_from"] = prev_phase or "legacy"
        try:
            from core.paper.rebalance.watching_matrix import (
                invalidate_live_rank_snapshot,
            )

            invalidate_live_rank_snapshot()
        except Exception:  # noqa: BLE001
            logger.debug("invalidate live ranks after open promote failed", exc_info=True)
    return out


def load_session_open_l2(
    day: str,
    codes: Sequence[str],
    *,
    clock: str = "open",
) -> Optional[Any]:
    """只读会话日 open 立方（不区分 provisional/final；调用方看 provenance）。"""
    from core.research.day_cube import load_day_cube

    return load_day_cube(day, codes, clock=clock)


def item_provenance_fields(cube: Any) -> Dict[str, Any]:
    """出分行透传字段。"""
    slim = getattr(cube, "slim", None) if cube is not None else None
    prov = provenance_from_mapping(slim if isinstance(slim, dict) else {})
    out: Dict[str, Any] = {}
    if prov.get("phase"):
        out["pit_phase"] = prov["phase"]
    if prov.get("open_source"):
        out["open_source"] = prov["open_source"]
    if prov.get("builder"):
        out["cube_builder"] = prov["builder"]
    # 因子截止：仅当是 T−1 及更早；不要写成会话日 T（会触发「盘中漏入」误判）
    eod = str(prov.get("eod_as_of") or "")[:10]
    day = str(getattr(cube, "date", "") or "")[:10]
    if len(eod) >= 10 and (not day or eod < day):
        out["eod_feature_as_of"] = eod
    return out


__all__ = [
    "BUILDER_LAKE_OPEN",
    "BUILDER_SESSION_OPEN_QUOTE",
    "OPEN_SOURCE_DAILY",
    "OPEN_SOURCE_QUOTE",
    "PHASE_FINAL",
    "PHASE_PROVISIONAL",
    "daily_bars_ready_for_day",
    "ensure_session_open_l2",
    "force_open_rebuild_after_status_ok",
    "item_provenance_fields",
    "load_session_open_l2",
    "membership_drift",
    "meta_is_final_open",
    "meta_needs_lake_rebuild",
    "prev_eod_as_of",
    "provenance_dict",
    "provenance_from_mapping",
    "read_open_cube_meta",
    "should_force_open_lake_rebuild",
    "stamp_cube_provenance",
]
