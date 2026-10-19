"""Feature Lake / DayCube 与训练矩阵、bars 刷新的桥接。"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# 与 panel head 对齐的 L1 family 名
FAMILY_OO = "oo"
FAMILY_CO = "co"
FAMILY_TC = "tc"
FAMILY_TC_RANK = "tc_rank"
FAMILY_OO_RANK = "oo_rank"


def normalize_tau_clock(tau_hm: str) -> str:
    """``open`` / ``0930`` / ``09:30`` → ``open``；其余 → ``HHMM`` 四位。"""
    s = str(tau_hm or "open").strip().lower()
    if s in ("", "open"):
        return "open"
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 4:
        hhmm = digits[:4]
        if hhmm in ("0930", "930"):
            return "open"
        return hhmm if len(hhmm) == 4 else "open"
    # 非数字钟点（少见）：字母数字叶子，避免路径穿越
    leaf = "".join(ch for ch in s if ch.isalnum()) or "open"
    return "open" if leaf in ("open", "0930") else leaf


def family_for_tc(*, tau_hm: str = "open", rank: bool = False) -> str:
    """开盘 τ → ``tc`` / ``tc_rank``；分钟钟 → ``tc_HHMM`` / ``tc_rank_HHMM``。

    避免分钟拟合写穿覆盖开盘 L1（replay 默认读 ``tc``）。
    """
    base = FAMILY_TC_RANK if rank else FAMILY_TC
    clock = normalize_tau_clock(tau_hm)
    if clock == "open":
        return base
    return f"{base}_{clock}"


def list_tc_l1_families() -> List[str]:
    """磁盘上已有的 tc / tc_* / tc_rank* family（含默认两族）。"""
    fams = {FAMILY_TC, FAMILY_TC_RANK}
    try:
        from core.research.feature_lake import FEATURE_SCHEMA, _root

        root = os.path.join(_root(), f"v{FEATURE_SCHEMA}")
        if os.path.isdir(root):
            for name in os.listdir(root):
                if name == FAMILY_TC or name.startswith("tc_"):
                    fams.add(str(name))
    except Exception:  # noqa: BLE001
        logger.debug("list_tc_l1_families failed", exc_info=True)
    return sorted(fams)


def try_load_l1_as_panel(
    family: str,
    code: str,
    fingerprint: str,
    *,
    names: Optional[Sequence[str]] = None,
    require_y: bool = True,
) -> Optional[tuple]:
    """若 L1 fingerprint 一致则返回 ``(X, y, names, dates)``。

    列名给定且与 L1 不一致 → miss（避免错位）。
    """
    try:
        from core.research import feature_lake as lake

        if not lake.enabled() or not code:
            return None
        pack = lake.load_stock_panel(family, code, fingerprint=str(fingerprint))
        if not pack:
            return None
        X = np.asarray(pack["X"], dtype=np.float64)
        dates = [str(d)[:10] for d in (pack.get("dates") or [])]
        got_names = [str(n) for n in (pack.get("names") or [])]
        if names is not None:
            want = [str(n) for n in names]
            if got_names and got_names != want:
                return None
            if not got_names:
                got_names = want
        if require_y and pack.get("y") is None:
            return None
        if pack.get("y") is not None:
            y = np.asarray(pack["y"], dtype=np.float64)
        else:
            y = np.full((len(dates),), np.nan, dtype=np.float64)
        if X.ndim != 2 or X.shape[0] != len(dates) or y.shape[0] != len(dates):
            return None
        if got_names and X.shape[1] != len(got_names):
            return None
        return X, y, got_names, dates
    except Exception:  # noqa: BLE001
        logger.debug(
            "try_load_l1_as_panel failed code=%s family=%s", code, family, exc_info=True
        )
        return None


def load_training_matrix(
    family: str,
    code: str,
    fingerprint: str,
    *,
    names: Optional[Sequence[str]] = None,
    require_y: bool = True,
) -> Optional[tuple]:
    """训练矩阵缓存：只读 L1。

    返回 ``(X, y, names, dates, source)``，``source`` 恒为 ``\"l1\"``。
    """
    if not code:
        return None
    hit = try_load_l1_as_panel(
        family, code, fingerprint, names=names, require_y=require_y
    )
    if hit is None:
        return None
    X, y, got_names, dates = hit
    return X, y, got_names, dates, "l1"


def sync_panel_to_lake(
    family: str,
    code: str,
    *,
    X: Any,
    dates: Sequence[str],
    names: Sequence[str],
    fingerprint: str,
    y: Optional[Any] = None,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> bool:
    """collect 写穿 L1（可带 stem 增量 meta）。失败不抛。"""
    try:
        from core.research import feature_lake as lake

        if not lake.enabled():
            return False
        y_arr = None if y is None else np.asarray(y, dtype=np.float64)
        return bool(
            lake.sync_from_matrix(
                family,
                code,
                X=np.asarray(X, dtype=np.float64),
                dates=dates,
                names=names,
                fingerprint=str(fingerprint),
                y=y_arr,
                extra_meta=extra_meta,
            )
        )
    except Exception:  # noqa: BLE001
        logger.debug("sync_panel_to_lake failed code=%s family=%s", code, family, exc_info=True)
        return False


def _l1_dates_before_invalidate(code: str, families: Sequence[str]) -> tuple:
    """删文件前取出日期。返回 ``(dates, had_shard, unreadable)``。

    单个 family 读失败只记这一支，不让整次失效中断。
    """
    from core.research import feature_lake as lake

    dates: set = set()
    had_shard = False
    unreadable = False
    for fam in families:
        try:
            exists, found = lake.peek_stock_dates(fam, code)
        except Exception:  # noqa: BLE001
            logger.warning("l1 dates read failed code=%s family=%s", code, fam, exc_info=True)
            unreadable = True
            had_shard = True
            continue
        if not exists:
            continue
        had_shard = True
        if found is None:
            unreadable = True
        else:
            dates.update(found)
    return dates, had_shard, unreadable


def invalidate_l1_for_code(code: str, *, families: Optional[Sequence[str]] = None) -> None:
    """bars topup / 复权改写后使该票 L1 miss，并作废这些日期的 L2。"""
    from core.research import feature_lake as lake
    from core.research.day_cube_gc import note_all_dates, note_stale_dates

    try:
        if families is not None:
            fams = list(families)
        else:
            fams = [
                FAMILY_OO,
                FAMILY_CO,
                FAMILY_OO_RANK,
                *list_tc_l1_families(),
            ]
    except Exception:  # noqa: BLE001
        logger.warning("l1 family list failed code=%s", code, exc_info=True)
        fams = [FAMILY_OO, FAMILY_CO, FAMILY_OO_RANK]
    dates, had_shard, unreadable = _l1_dates_before_invalidate(code, fams)
    for fam in fams:
        try:
            lake.invalidate_code(fam, code)
        except Exception:  # noqa: BLE001
            logger.warning("l1 invalidate file failed code=%s family=%s", code, fam, exc_info=True)
    if not had_shard:
        return
    try:
        if unreadable and not dates:
            logger.warning("l1 dates unreadable code=%s; dropping current-schema cubes", code)
            note_all_dates()
            return
        if unreadable:
            logger.warning(
                "l1 dates partial unreadable code=%s; dropping %s known dates",
                code,
                len(dates),
            )
        if dates:
            note_stale_dates(dates)
    except Exception:  # noqa: BLE001
        logger.warning("l2 note after l1 invalidate failed code=%s", code, exc_info=True)


def invalidate_l1_many(codes: Sequence[str]) -> Dict[str, Any]:
    """删这批票的 L1，合并后作废对应 L2 日期，再回收冷宇宙。"""
    from core.research.day_cube_gc import l2_invalidation_batch, prune_cold

    n = 0
    with l2_invalidation_batch() as bucket:
        for raw in codes or []:
            c = str(raw or "").strip()
            if not c:
                continue
            invalidate_l1_for_code(c)
            n += 1
    try:
        prune = prune_cold()
    except Exception as exc:  # noqa: BLE001
        logger.warning("day cube prune after l1 invalidate failed", exc_info=True)
        prune = {
            "skipped": True,
            "hashes_pruned": 0,
            "schemas_pruned": 0,
            "error": str(exc),
        }
    return {
        "n": n,
        "l2_dates_dropped": int((bucket or {}).get("dates_dropped") or 0),
        "l2_files_removed": int((bucket or {}).get("files_removed") or 0),
        "l2_hashes_pruned": int((prune or {}).get("hashes_pruned") or 0),
        "l2_schemas_pruned": int((prune or {}).get("schemas_pruned") or 0),
        "l2_prune_skipped": bool((prune or {}).get("skipped")),
    }
