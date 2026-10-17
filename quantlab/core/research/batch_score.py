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


def ensure_day_cube_for_items(
    items: Sequence[Mapping[str, Any]],
    *,
    date: Optional[str] = None,
    clock: str = "open",
    persist: bool = True,
) -> Optional[Any]:
    """从已打分 items 构建/加载当日 DayCube。"""
    from core.research.day_cube import build_or_load

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
    return build_or_load(day, by_code, clock=clock, persist=persist)


def rescore_fitted_cross_section_batched(
    items: Sequence[dict],
    *,
    score_codes: Optional[Collection[str]] = None,
    date: Optional[str] = None,
    clock: str = "open",
    persist_cube: bool = True,
) -> List[dict]:
    """构建 DayCube 后单遍 rescore（τ/co 直接用 cube μ/σ；cube 落盘供 live/T0）。"""
    from core.signal.cross_section_rescore import rescore_fitted_cross_section

    cube = None
    if enabled() and len([it for it in items if isinstance(it, dict)]) >= 2:
        try:
            cube = ensure_day_cube_for_items(
                items, date=date, clock=clock, persist=persist_cube
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
    return rescore_fitted_cross_section(items, score_codes=score_codes, day_cube=cube)
