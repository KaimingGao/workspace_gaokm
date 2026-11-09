"""盘中补齐会话日 open L2：昨收日线 + 行情今开 → 官方 open DayCube 落盘。

消费路径只读立方；缺会话日 open 时走本写入入口，禁止在调仓线程临时拼数。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[int, int, str], None]]


def _emit(cb: ProgressCb, cur: int, tot: int, msg: str) -> None:
    if not cb:
        return
    try:
        cb(int(cur), max(1, int(tot)), str(msg or ""))
    except Exception:  # noqa: BLE001
        logger.debug("session open materialize progress failed", exc_info=True)


def session_open_materialize_request(cycle_day: str) -> Dict[str, Any]:
    """给 ``MaterializeRequired.request`` / 前端一键补齐用。"""
    day = str(cycle_day or "")[:10]
    return {
        "do_session_open_l2": True,
        "do_l1": False,
        "do_l1_tau_grid": False,
        "do_l2": True,
        "clocks": ["open"],
        "families": ["oo", "tc"],
        "cycle_day": day or None,
    }


def materialize_session_open_l2(
    *,
    cycle_day: Optional[str] = None,
    codes: Sequence[str] | None = None,
    watching_limit: Optional[int] = None,
    progress_cb: ProgressCb = None,
) -> Dict[str, Any]:
    """用今开行情 + ≤T−1 日线写会话日官方 open DayCube。

    必须在 ``materialize_writer`` 上下文中调用（``run_materialize`` 已包一层）。
    """
    from core.paper.rebalance.open_pit_score import build_open_pit_feature_items
    from core.research.batch_score import features_by_code_from_items
    from core.research.day_cube import build_day_cube, remember, save_day_cube
    from core.research.materialize.orchestrate import resolve_materialize_codes
    from core.signal.session_pit import oo_cycle_date
    from core.watching.store import WATCHING_MAX_SIZE

    limit = int(watching_limit or WATCHING_MAX_SIZE)

    t0 = time.perf_counter()
    day = str(cycle_day or oo_cycle_date() or "")[:10]
    out: Dict[str, Any] = {
        "ok": False,
        "success": False,
        "cycle_day": day or None,
        "clock": "open",
        "n_universe": 0,
        "n_items": 0,
        "persisted": False,
        "elapsed_s": 0.0,
    }
    if len(day) < 10:
        out["error"] = "无会话日"
        return out

    code_list = resolve_materialize_codes(codes=codes, watching_limit=limit)
    out["n_universe"] = len(code_list)
    if len(code_list) < 2:
        out["error"] = "观察池/宇宙不足 2 只"
        return out

    _emit(progress_cb, 0, 3, f"补齐 {day} 开盘 L2：拉今开…")
    scored, rejected, meta = build_open_pit_feature_items(
        code_list,
        cycle_day=day,
        universe_codes=code_list,
    )
    if not meta.get("ok"):
        out["error"] = (
            f"会话日 {day} 开盘截面覆盖不足"
            f"（open={meta.get('n_open')}/{meta.get('n_universe')}"
            f" items={meta.get('n_items')} reason={meta.get('reason')}）"
        )
        out["meta"] = {
            k: meta.get(k)
            for k in (
                "n_open",
                "n_items",
                "n_style_gap",
                "need_open",
                "reason",
                "cover_frac",
            )
        }
        out["n_rejected"] = len(rejected or [])
        out["elapsed_s"] = round(time.perf_counter() - t0, 2)
        return out

    pool_items = list(meta.get("pool_items") or scored or [])
    out["n_items"] = len(pool_items)
    if len(pool_items) < 2:
        out["error"] = f"会话日 {day} 可建特征不足 2 只"
        out["elapsed_s"] = round(time.perf_counter() - t0, 2)
        return out

    _emit(progress_cb, 1, 3, f"补齐 {day} 开盘 L2：建立方…")
    by_code = features_by_code_from_items(pool_items)
    cube = build_day_cube(
        day,
        by_code,
        clock="open",
        universe_codes=code_list,
    )
    if cube is None:
        out["error"] = f"会话日 {day} build_day_cube 失败"
        out["elapsed_s"] = round(time.perf_counter() - t0, 2)
        return out

    from core.research.pit_session import (
        BUILDER_SESSION_OPEN_QUOTE,
        OPEN_SOURCE_QUOTE,
        PHASE_PROVISIONAL,
        prev_eod_as_of,
        stamp_cube_provenance,
    )

    n_open = int(meta.get("n_open") or 0)
    n_univ = int(meta.get("n_universe") or len(code_list) or 0)
    missing_open_n = max(0, n_univ - n_open) if n_univ else None
    stamp_cube_provenance(
        cube,
        phase=PHASE_PROVISIONAL,
        open_source=OPEN_SOURCE_QUOTE,
        builder=BUILDER_SESSION_OPEN_QUOTE,
        eod_as_of=prev_eod_as_of(day, code_list),
        missing_open_n=missing_open_n,
    )

    _emit(progress_cb, 2, 3, f"补齐 {day} 开盘 L2：落盘…")
    if not save_day_cube(cube):
        out["error"] = f"会话日 {day} open 立方落盘失败"
        out["elapsed_s"] = round(time.perf_counter() - t0, 2)
        return out
    remember(cube)
    out["ok"] = True
    out["success"] = True
    out["persisted"] = True
    out["phase"] = PHASE_PROVISIONAL
    out["open_source"] = OPEN_SOURCE_QUOTE
    out["builder"] = BUILDER_SESSION_OPEN_QUOTE
    out["n_cube_codes"] = len(getattr(cube, "codes", []) or [])
    out["n_names"] = len(getattr(cube, "names", []) or [])
    out["universe_hash"] = str(getattr(cube, "universe_hash", "") or "")
    out["n_rejected"] = len(rejected or [])
    out["elapsed_s"] = round(time.perf_counter() - t0, 2)
    _emit(progress_cb, 3, 3, f"已补齐 {day} 开盘 L2 · {out['elapsed_s']}s")
    logger.info(
        "session open L2 materialized day=%s n_items=%s n_cube=%s elapsed=%.1fs",
        day,
        out["n_items"],
        out.get("n_cube_codes"),
        out["elapsed_s"],
    )
    return out
