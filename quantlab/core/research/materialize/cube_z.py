"""拟合侧：用 DayCube μ/σ 对训练矩阵按日/钟打 z（不取代 L1 样本行）。"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def _universe_codes() -> List[str]:
    try:
        from core.research_universe import resolve_model_fit_codes

        return list((resolve_model_fit_codes() or {}).get("codes") or [])
    except Exception:  # noqa: BLE001
        pass
    try:
        from core.t0.score_policy import _watching_codes_for_z

        return list(_watching_codes_for_z() or [])
    except Exception:  # noqa: BLE001
        return []


def _day_clock_from_group(group: Any, *, default_clock: str) -> Tuple[str, str]:
    """分组键 → ``(YYYY-MM-DD, clock)``；``date|τ`` 用 τ，否则用 default_clock。"""
    from core.research.feature_standardize import normalize_cs_group_key

    key = normalize_cs_group_key(group)
    if not key or len(key) < 10:
        return "", str(default_clock or "open")
    if "|" in key:
        day, _, clk = key.partition("|")
        day = day[:10]
        clk = str(clk or "").strip() or "open"
        return day, clk
    return key[:10], str(default_clock or "open")


def _groups_have_tau_clock(groups: Sequence[Any]) -> bool:
    """任一分组为 ``date|τ`` 则视为窗头/按钟 PIT。"""
    for g in groups or []:
        s = str(g or "")
        if "|" in s and len(s) >= 12:
            return True
    return False


def apply_day_cube_mu_sigma_matrix(
    x: np.ndarray,
    names: Sequence[str],
    dates: Sequence[str],
    *,
    clock: str = "open",
    codes: Optional[Sequence[str]] = None,
    min_names: int = 2,
    eps: float = 1e-12,
    fallback_open: Optional[bool] = None,
) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """按行分组键读 L2 μ/σ 打 z。

    分组可为纯日期或 ``date|τ``（PIT）。``date|τ`` 优先读该钟 Cube；miss 且
    ``fallback_open`` 时再试 ``open``。``fallback_open=None`` 时：含 ``date|τ``
    默认关（禁静默 open），纯日期默认开。覆盖不足返回 ``(None, meta)``。
    """
    from core.research.day_cube import enabled, load_day_cube

    arr = np.asarray(x, dtype=np.float64)
    name_list = [str(n) for n in names]
    n, p = arr.shape if arr.ndim == 2 else (0, 0)
    if n == 0 or p == 0 or len(dates) != n or len(name_list) != p:
        return None, {"ok": False, "error": "shape mismatch"}
    if not enabled():
        return None, {"ok": False, "error": "day_cube disabled"}

    univ = [str(c).strip() for c in (codes or _universe_codes()) if str(c or "").strip()]
    if len(univ) < 2:
        return None, {"ok": False, "error": "universe < 2"}

    allow_open_fb = (
        bool(fallback_open)
        if fallback_open is not None
        else (not _groups_have_tau_clock(dates))
    )

    out = np.array(arr, copy=True)
    buckets: Dict[Tuple[str, str], List[int]] = {}
    for i, d in enumerate(dates):
        day, clk = _day_clock_from_group(d, default_clock=clock)
        if len(day) < 10:
            continue
        buckets.setdefault((day, clk), []).append(i)

    if not buckets:
        return None, {"ok": False, "error": "no date buckets"}

    hit = 0
    miss = 0
    open_fb = 0
    need = max(1, int(min_names))
    for (day, clk), idxs in buckets.items():
        cube = None
        try:
            cube = load_day_cube(day, univ, clock=clk)
        except Exception:  # noqa: BLE001
            cube = None
        if cube is None and allow_open_fb and str(clk) != "open":
            try:
                cube = load_day_cube(day, univ, clock="open")
            except Exception:  # noqa: BLE001
                cube = None
            if cube is not None:
                open_fb += 1
        if cube is None:
            miss += 1
            return None, {
                "ok": False,
                "error": f"cube miss {day}|{clk}",
                "hit_days": hit,
                "miss_days": miss,
                "open_fallback": open_fb,
                "fallback_open": allow_open_fb,
            }
        hit += 1
        mu_map = {str(n): float(cube.z_mean[j]) for j, n in enumerate(cube.names)}
        sd_map = {str(n): float(cube.z_std[j]) for j, n in enumerate(cube.names)}
        ix = np.asarray(idxs, dtype=np.int64)
        for j, name in enumerate(name_list):
            mu = mu_map.get(name)
            sd = sd_map.get(name)
            col = out[ix, j]
            finite = np.isfinite(col)
            m = int(finite.sum())
            if m < need or mu is None or (not math.isfinite(mu)):
                if m:
                    out[ix[finite], j] = 0.0
                continue
            if sd is None or (not math.isfinite(sd)) or sd <= eps:
                out[ix[finite], j] = 0.0
                continue
            out[ix[finite], j] = (col[finite] - float(mu)) / float(sd)

    return out, {
        "ok": True,
        "hit_days": hit,
        "miss_days": miss,
        "open_fallback": open_fb,
        "fallback_open": allow_open_fb,
        "clock": clock,
        "n_buckets": len(buckets),
    }


def cs_z_matrix_prefer_cube(
    x: np.ndarray,
    names: Sequence[str],
    groups: Sequence[Any],
    *,
    clock: str = "open",
    fallback_open: Optional[bool] = None,
) -> np.ndarray:
    """拟合/OOS：``FIT_USE_DAY_CUBE`` 时优先 L2 μ/σ，否则矩阵截面 z。"""
    from core.research.feature_standardize import cross_section_zscore_matrix

    try:
        from core.research.materialize.flags import fit_use_day_cube

        if fit_use_day_cube():
            z, meta = apply_day_cube_mu_sigma_matrix(
                x, names, groups, clock=clock, fallback_open=fallback_open
            )
            if z is not None and meta.get("ok"):
                return np.where(np.isfinite(z), z, 0.0)
    except Exception:  # noqa: BLE001
        logger.debug("cs_z_matrix_prefer_cube cube path failed", exc_info=True)
    z = cross_section_zscore_matrix(x, groups)
    return np.where(np.isfinite(z), z, 0.0)


def cs_z_dicts_prefer_cube(
    rows: Sequence[Mapping[str, Any]],
    names: Sequence[str],
    groups: Sequence[Any],
    *,
    clock: str = "open",
    fallback_open: Optional[bool] = None,
) -> List[dict]:
    """dict 版：``FIT_USE_DAY_CUBE`` 优先 L2，否则 ``cross_section_zscore_dicts``。"""
    from core.research.feature_standardize import cross_section_zscore_dicts

    try:
        from core.research.materialize.flags import fit_use_day_cube

        if fit_use_day_cube():
            out, meta = apply_day_cube_mu_sigma_dicts(
                rows, names, groups, clock=clock, fallback_open=fallback_open
            )
            if out is not None and meta.get("ok"):
                return out
    except Exception:  # noqa: BLE001
        logger.debug("cs_z_dicts_prefer_cube cube path failed", exc_info=True)
    return cross_section_zscore_dicts(rows, names, groups)


def apply_day_cube_mu_sigma_dicts(
    rows: Sequence[Mapping[str, Any]],
    names: Sequence[str],
    groups: Sequence[Any],
    *,
    clock: str = "open",
    codes: Optional[Sequence[str]] = None,
    min_names: int = 2,
    fallback_open: Optional[bool] = None,
) -> Tuple[Optional[List[dict]], Dict[str, Any]]:
    """dict 面板版：与 ``cross_section_zscore_dicts`` 同形；Cube miss → ``(None, meta)``。"""
    from core.research.feature_standardize import mirror_z_alias_value

    keys = [str(n) for n in names]
    n = len(rows)
    if n == 0 or len(groups) != n or not keys:
        return None, {"ok": False, "error": "shape mismatch"}
    mat = np.full((n, len(keys)), np.nan, dtype=np.float64)
    for i, row in enumerate(rows):
        src = row if isinstance(row, Mapping) else {}
        for j, name in enumerate(keys):
            v = src.get(name)
            if v is None or v == "":
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(fv):
                mat[i, j] = fv
    z_mat, meta = apply_day_cube_mu_sigma_matrix(
        mat,
        keys,
        [str(g) for g in groups],
        clock=clock,
        codes=codes,
        min_names=min_names,
        fallback_open=fallback_open,
    )
    if z_mat is None or not meta.get("ok"):
        return None, meta
    out: List[dict] = []
    for i, row in enumerate(rows):
        src = dict(row) if isinstance(row, Mapping) else {}
        for j, name in enumerate(keys):
            val = float(z_mat[i, j])
            if math.isfinite(val):
                mirror_z_alias_value(src, name, val)
        out.append(src)
    return out, meta
