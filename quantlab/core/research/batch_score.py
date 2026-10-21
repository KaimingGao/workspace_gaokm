"""统一截面 batch 打分：L1/L2 → 单遍 ŷ（rescore 的优选路径）。

``QUANTLAB_BATCH_SCORE=0`` 时调用方应回退旧双遍。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Collection, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

_ENV = "QUANTLAB_BATCH_SCORE"


def enabled() -> bool:
    raw = str(os.environ.get(_ENV, "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _raw_feat_dict(item: Mapping[str, Any]) -> Dict[str, Any]:
    """从 signal_item 抽出截面 raw 特征（sub_scores + τ/co + raw_*）。"""
    out: Dict[str, Any] = {}
    # ŷ_oo 截面 z 用 sub_scores（含 z_oo / raw_aoo）
    sub = item.get("sub_scores")
    if isinstance(sub, dict) and sub:
        out.update(sub)
        try:
            from core.signal.factors.z_oo import stamp_z_oo_aliases

            stamp_z_oo_aliases(out)
        except Exception:  # noqa: BLE001
            pass
    for key in (
        "features_tau",
        "features_co",
        "features_on",
        "co_features",
        "factor_raw",
        "raw_features",
    ):
        block = item.get(key)
        if isinstance(block, dict):
            out.update(block)
    for k, v in item.items():
        if str(k).startswith("raw_") and v is not None:
            out[str(k)] = v
    # τ / co / τ* 块合并后对齐裸名↔canon，避免 DayCube μ/σ 落在分叉列上
    try:
        from core.signal.factors.z_tc import stamp_z_tc_aliases

        stamp_z_tc_aliases(out)
    except Exception:  # noqa: BLE001
        pass
    try:
        from core.signal.factors.z_th import stamp_z_th_aliases

        stamp_z_th_aliases(out)
    except Exception:  # noqa: BLE001
        pass
    try:
        from core.signal.factors.z_co import stamp_z_co_aliases

        stamp_z_co_aliases(out)
    except Exception:  # noqa: BLE001
        pass
    return out


def features_by_code_from_items(
    items: Sequence[Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    by_code: Dict[str, Dict[str, Any]] = {}
    for it in items or []:
        if not isinstance(it, Mapping):
            continue
        code = str(it.get("stock_code") or it.get("code") or "").strip()
        if not code:
            continue
        feats = _raw_feat_dict(it)
        if feats:
            by_code[code] = feats
    return by_code


def _ensure_aligned_cube(
    aligned: tuple,
    *,
    date: Optional[str],
    clock: str,
    persist: bool,
    universe_codes: Optional[Sequence[str]],
    replace: bool,
    load_day_cube: Any,
    build_aligned_cube: Any,
    save_day_cube: Any,
    consumer_read_only: Any,
) -> Optional[Any]:
    """``aligned`` 是 ``(codes, names, X)``。列已经按别名规则对齐。"""
    if not isinstance(aligned, tuple) or len(aligned) != 3:
        return None
    codes, names, raw = aligned
    day = str(date or "")[:10]
    if len(day) < 10:
        return None
    lookup = list(universe_codes) if universe_codes is not None else list(codes or [])
    if consumer_read_only():
        return load_day_cube(day, lookup, clock=clock)
    if not replace:
        hit = load_day_cube(day, lookup, clock=clock)
        if hit is not None:
            return hit
    cube = build_aligned_cube(
        day,
        list(codes or []),
        raw,
        list(names or []),
        clock=clock,
        universe_codes=lookup,
    )
    if cube is None:
        return None
    if not persist:
        from core.research.day_cube import remember

        remember(cube)
        return cube
    if not save_day_cube(cube):
        if replace:
            return None
    return cube


def ensure_day_cube_for_items(
    items: Sequence[Mapping[str, Any]],
    *,
    date: Optional[str] = None,
    clock: str = "open",
    persist: bool = True,
    universe_codes: Optional[Sequence[str]] = None,
    replace: bool = False,
    aligned: Optional[tuple] = None,
) -> Optional[Any]:
    """从已打分 items 构建/加载当日 DayCube。

    ``universe_codes`` 是读取方会拿去哈希的完整名单。缺行的代码仍留在键里，
    矩阵只用有特征的 items。``replace=True`` 时盖掉已有立方（窗头列不够的重建）。
    ``aligned`` 为 ``(codes, names, X)`` 时跳过 dict 拼装，直接落盘。
    """
    from core.research.day_cube import (
        build_aligned_cube,
        build_or_load,
        load_day_cube,
        save_day_cube,
    )
    from core.research.materialize.consumer import consumer_read_only

    if aligned is not None:
        return _ensure_aligned_cube(
            aligned,
            date=date,
            clock=clock,
            persist=persist,
            universe_codes=universe_codes,
            replace=replace,
            load_day_cube=load_day_cube,
            build_aligned_cube=build_aligned_cube,
            save_day_cube=save_day_cube,
            consumer_read_only=consumer_read_only,
        )

    by_code = features_by_code_from_items(items)
    if len(by_code) < 2:
        return None
    day = str(date or "")[:10]
    if len(day) < 10:
        for it in items:
            if not isinstance(it, Mapping):
                continue
            for key in ("as_of", "date", "trade_date", "session_date"):
                raw = str(it.get(key) or "")[:10]
                if len(raw) >= 10:
                    day = raw
                    break
            if len(day) >= 10:
                break
    if len(day) < 10:
        from datetime import date as _date

        day = _date.today().isoformat()
    lookup = list(universe_codes) if universe_codes is not None else list(by_code)
    if consumer_read_only():
        return load_day_cube(day, lookup, clock=clock)
    return build_or_load(
        day,
        by_code,
        clock=clock,
        persist=persist,
        universe_codes=universe_codes,
        replace=bool(replace),
    )


def _resolve_cube_universe_codes(
    items: Sequence[dict],
    *,
    universe_codes: Optional[Sequence[str]] = None,
) -> Optional[List[str]]:
    """L2 目录键：完整观察池优先；禁止用分档 score_codes 当哈希。"""
    if universe_codes is not None:
        out = [str(c or "").strip() for c in universe_codes if str(c or "").strip()]
        return out or None
    try:
        from core.research.materialize.coverage import read_cube_codes

        pool = read_cube_codes()
        if pool:
            return list(pool)
    except Exception:  # noqa: BLE001
        pass
    from_items = [
        str(it.get("stock_code") or "").strip()
        for it in items
        if isinstance(it, dict) and str(it.get("stock_code") or "").strip()
    ]
    return from_items or None


def rescore_fitted_cross_section_batched(
    items: Sequence[dict],
    *,
    score_codes: Optional[Collection[str]] = None,
    date: Optional[str] = None,
    clock: str = "open",
    persist_cube: bool = True,
    universe_codes: Optional[Sequence[str]] = None,
) -> List[dict]:
    """构建 DayCube 后单遍 rescore（τ/co 直接用 cube μ/σ；cube 落盘供 live/T0）。

    ``universe_codes`` = L2 目录键（完整观察池）。未传时读 ``read_cube_codes()``，
    再退回 items 里的代码。``score_codes`` 只决定写谁的 ŷ，**不**参与哈希。
    """
    from core.signal.cross_section_rescore import rescore_fitted_cross_section

    cube = None
    uh = _resolve_cube_universe_codes(items, universe_codes=universe_codes)
    if enabled() and len([it for it in items if isinstance(it, dict)]) >= 2:
        try:
            cube = ensure_day_cube_for_items(
                items,
                date=date,
                clock=clock,
                persist=persist_cube,
                universe_codes=uh,
            )
            if cube is not None:
                logger.debug(
                    "batch_score day_cube date=%s clock=%s n=%s uh=%s",
                    cube.date,
                    cube.clock,
                    len(cube.codes),
                    cube.universe_hash,
                )
        except Exception:  # noqa: BLE001
            logger.debug("batch_score day_cube build failed", exc_info=True)
    from core.research.materialize.consumer import MaterializeRequired, consumer_read_only

    if consumer_read_only() and cube is None and len([it for it in items if isinstance(it, dict)]) >= 2:
        raise MaterializeRequired(
            {
                "error_code": "materialize_required",
                "error": f"请先物化 L2：{str(date or '')[:10] or '当日'} {clock} 截面立方缺失",
                "layer": "l2",
                "hint": "分钟线物化" if str(clock) not in ("open", "09:30", "0930") else "日线物化",
                "clocks": [str(clock or "open")],
            }
        )
    return rescore_fitted_cross_section(items, score_codes=score_codes, day_cube=cube)
