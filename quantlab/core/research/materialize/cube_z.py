"""拟合侧：用 DayCube μ/σ 对训练矩阵按日/钟打 z（不取代 L1 样本行）。"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def universe_codes() -> List[str]:
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


def _l2_materialize_hint(
    groups: Sequence[Any],
    *,
    default_clock: str = "open",
) -> Tuple[str, List[str]]:
    """消费缺 L2 时的提示：有分钟钟 → 分钟线物化；否则日线物化。"""
    clocks: List[str] = []
    seen = set()
    for g in groups or []:
        _day, clk = _day_clock_from_group(g, default_clock=default_clock)
        c = str(clk or "open").strip() or "open"
        if c not in seen:
            seen.add(c)
            clocks.append(c)
    if not clocks:
        clocks = [str(default_clock or "open")]
    minute = [
        c
        for c in clocks
        if str(c).lower() not in ("open", "09:30", "0930")
    ]
    if minute:
        return "分钟线物化", minute
    return "日线物化", clocks


def _cube_disk_span_note(clock: str) -> str:
    """本地该钟已有 Cube 的日期跨度（便于区分「没物化」vs「物化窗短于 lookback」）。"""
    try:
        import os

        from core.research.day_cube import _disk_paths  # noqa: PLC0415
        from core.research.feature_registry import universe_hash

        uh = universe_hash(universe_codes())
        if not uh:
            return ""
        root = _disk_paths(uh, clock, "1970-01-01").get("dir") or ""
        if not root or not os.path.isdir(root):
            return ""

        days = sorted(
            {
                name[:10]
                for name in os.listdir(root)
                if name.endswith(".npz") and len(name) >= 14
            }
        )
        if not days:
            return ""
        if len(days) == 1:
            return f"；本地 {clock} Cube 仅有 {days[0]}（n=1）"
        return f"；本地 {clock} Cube 仅有 {days[0]}→{days[-1]}（n={len(days)}）"
    except Exception:  # noqa: BLE001
        logger.debug("cube disk span note failed", exc_info=True)
        return ""


def _format_l2_miss_error(meta: Optional[Mapping[str, Any]], *, default_clock: str) -> str:
    base = str((meta or {}).get("error") or "截面立方缺失")
    # cube miss YYYY-MM-DD|HH:MM → 附本地已有跨度
    clk = str((meta or {}).get("clock") or "").strip()
    if not clk and "cube miss" in base and "|" in base:
        clk = base.rsplit("|", 1)[-1].strip()
    if not clk:
        clk = str(default_clock or "open")
    note = _cube_disk_span_note(clk)
    if note and "→" in note:
        note += "；请先跑分钟线物化覆盖该窗，或确认宇宙键一致"
    return "请先物化 L2：" + base + note


def _cube_mu_sd_for_name(
    name: str,
    mu_map: Mapping[str, float],
    sd_map: Mapping[str, float],
) -> Tuple[Optional[float], Optional[float]]:
    """Cube μ/σ 查找：精确名 → 裸名 / z_τh / z_τc / z_co 别名。"""
    key = str(name or "")
    if key in mu_map:
        return mu_map.get(key), sd_map.get(key)
    try:
        from core.signal.factors.z_co import to_legacy_key as co_legacy
        from core.signal.factors.z_co import to_z_co_key
        from core.signal.factors.z_tc import to_z_tc_key
        from core.signal.factors.z_th import to_legacy_key, to_z_th_key
    except Exception:  # noqa: BLE001
        return None, None
    bare = str(to_legacy_key(key) or "").strip()
    if not bare or bare == key:
        bare = str(co_legacy(key) or key).strip()
    for cand in (bare, to_z_th_key(bare), to_z_tc_key(bare), to_z_co_key(bare)):
        c = str(cand or "").strip()
        if c and c in mu_map:
            return mu_map.get(c), sd_map.get(c)
    return None, None


def _apply_bucket_matrix_cs(
    out: np.ndarray,
    idxs: Sequence[int],
    *,
    min_names: int,
    eps: float,
    cols: Optional[Sequence[int]] = None,
) -> None:
    """单组桶内截面 z（缺 L2 日 / 缺列时的有多少用多少回退）。"""
    if out is None or getattr(out, "ndim", 0) != 2:
        return
    n_rows = int(out.shape[0])
    ix = np.asarray(
        [int(i) for i in idxs if 0 <= int(i) < n_rows],
        dtype=np.int64,
    )
    if ix.size == 0:
        return
    need = max(1, int(min_names))
    p = int(out.shape[1])
    if cols is None:
        col_js = range(p)
    else:
        col_js = [int(j) for j in cols if 0 <= int(j) < p]
    for j in col_js:
        col = out[ix, j]
        finite = np.isfinite(col)
        m = int(finite.sum())
        if m < need:
            if m:
                out[ix[finite], j] = 0.0
            continue
        vals = col[finite]
        mu = float(vals.mean())
        var = float(np.mean((vals - mu) ** 2))
        if (not math.isfinite(var)) or var <= eps:
            out[ix[finite], j] = 0.0
            continue
        sd = math.sqrt(var)
        if sd < 1e-12:
            out[ix[finite], j] = 0.0
        else:
            out[ix[finite], j] = (vals - mu) / sd


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
    默认关（禁静默 open），纯日期默认开。

    **有多少用多少**：部分日缺 Cube 时，已命中日用 L2，缺日用桶内截面 z；
    Cube 缺列（旧盘 / 半窗）同桶内 z，并尝试裸名与 ``z_τh_*``/``z_τc_*``/``z_co_*`` 别名；
    **仅当该钟全日无 Cube**（hit=0）才 ``ok=False``（促物化）。
    """
    from core.research.day_cube import enabled, load_day_cube

    arr = np.asarray(x, dtype=np.float64)
    name_list = [str(n) for n in names]
    n, p = arr.shape if arr.ndim == 2 else (0, 0)
    if n == 0 or p == 0 or len(dates) != n or len(name_list) != p:
        return None, {"ok": False, "error": "shape mismatch"}
    if not enabled():
        return None, {"ok": False, "error": "day_cube disabled"}

    univ = [str(c).strip() for c in (codes or universe_codes()) if str(c or "").strip()]
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
    miss_buckets: List[Tuple[str, str, List[int]]] = []
    missing_col_seen: List[str] = []
    missing_col_set: set = set()
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
            miss_buckets.append((day, clk, list(idxs)))
            continue
        cube_names = list(getattr(cube, "names", None) or [])
        try:
            z_mean = np.asarray(getattr(cube, "z_mean", None), dtype=np.float64).reshape(
                -1
            )
            z_std = np.asarray(getattr(cube, "z_std", None), dtype=np.float64).reshape(
                -1
            )
        except (TypeError, ValueError):
            z_mean = np.zeros(0, dtype=np.float64)
            z_std = np.zeros(0, dtype=np.float64)
        width = min(len(cube_names), int(z_mean.size), int(z_std.size))
        if width < 1:
            miss += 1
            miss_buckets.append((day, clk, list(idxs)))
            continue
        hit += 1
        mu_map = {str(cube_names[j]): float(z_mean[j]) for j in range(width)}
        sd_map = {str(cube_names[j]): float(z_std[j]) for j in range(width)}
        # 旧 Cube / 半窗缺截面列：该列桶内 z（新 L2 已 enrich 进 Cube）
        ix = np.asarray(
            [int(i) for i in idxs if 0 <= int(i) < n],
            dtype=np.int64,
        )
        if ix.size == 0:
            continue
        matrix_js: List[int] = []
        for j, name in enumerate(name_list):
            if str(name).startswith("raw_ath_"):
                matrix_js.append(j)
                continue
            mu, sd = _cube_mu_sd_for_name(str(name), mu_map, sd_map)
            if mu is None:
                matrix_js.append(j)
                key = str(name)
                if key not in missing_col_set:
                    missing_col_set.add(key)
                    missing_col_seen.append(key)
                continue
            col = out[ix, j]
            finite = np.isfinite(col)
            m = int(finite.sum())
            if m < need or (not math.isfinite(float(mu))):
                if m:
                    out[ix[finite], j] = 0.0
                continue
            if sd is None or (not math.isfinite(float(sd))) or float(sd) <= eps:
                out[ix[finite], j] = 0.0
                continue
            out[ix[finite], j] = (col[finite] - float(mu)) / float(sd)
        if matrix_js:
            _apply_bucket_matrix_cs(
                out, idxs, min_names=need, eps=eps, cols=matrix_js
            )

    if hit == 0:
        day0, clk0, _idxs0 = miss_buckets[0] if miss_buckets else ("", clock, [])
        return None, {
            "ok": False,
            "error": f"cube miss {day0}|{clk0}",
            "hit_days": 0,
            "miss_days": miss,
            "open_fallback": open_fb,
            "fallback_open": allow_open_fb,
            "clock": clk0 or clock,
        }

    for _day, _clk, idxs in miss_buckets:
        _apply_bucket_matrix_cs(out, idxs, min_names=need, eps=eps)

    return out, {
        "ok": True,
        "hit_days": hit,
        "miss_days": miss,
        "matrix_fallback_days": miss,
        "matrix_fallback_cols": missing_col_seen[:32],
        "open_fallback": open_fb,
        "fallback_open": allow_open_fb,
        "clock": clock,
        "n_buckets": len(buckets),
    }


def _warn_cube_cs_soft(meta: Optional[Mapping[str, Any]], *, where: str) -> None:
    """L2 soft-miss / open 回退写 WARNING，避免拟合侧静默吞掉。"""
    if not isinstance(meta, dict):
        return
    miss = int(meta.get("miss_days") or meta.get("matrix_fallback_days") or 0)
    open_fb = int(meta.get("open_fallback") or 0)
    miss_cols = list(meta.get("matrix_fallback_cols") or [])[:8]
    if miss <= 0 and open_fb <= 0 and not miss_cols:
        return
    logger.warning(
        "%s soft CS: miss_days=%s open_fallback=%s miss_cols=%s hit=%s",
        where,
        miss,
        open_fb,
        miss_cols,
        meta.get("hit_days"),
    )


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

    from core.research.materialize.consumer import MaterializeRequired, consumer_read_only

    read_only = consumer_read_only()
    want_cube = False
    try:
        from core.research.materialize.flags import fit_use_day_cube

        want_cube = bool(fit_use_day_cube() or read_only)
        if want_cube:
            z, meta = apply_day_cube_mu_sigma_matrix(
                x, names, groups, clock=clock, fallback_open=fallback_open
            )
            if z is not None and meta.get("ok"):
                _warn_cube_cs_soft(meta, where="cs_z_matrix_prefer_cube")
                return np.where(np.isfinite(z), z, 0.0)
            if read_only:
                hint, clks = _l2_materialize_hint(groups, default_clock=clock)
                raise MaterializeRequired(
                    {
                        "error_code": "materialize_required",
                        "error": _format_l2_miss_error(meta, default_clock=clock),
                        "layer": "l2",
                        "hint": hint,
                        "clocks": clks,
                    }
                )
            logger.warning(
                "cs_z_matrix_prefer_cube: L2 unavailable (%s); matrix CS-z fallback",
                (meta or {}).get("error") or "ok=False",
            )
    except MaterializeRequired:
        raise
    except Exception:  # noqa: BLE001
        logger.debug("cs_z_matrix_prefer_cube cube path failed", exc_info=True)
        if read_only:
            hint, clks = _l2_materialize_hint(groups, default_clock=clock)
            raise MaterializeRequired(
                {
                    "error_code": "materialize_required",
                    "error": "请先物化 L2：截面立方读取失败"
                    + _cube_disk_span_note(str(clock or "open")),
                    "layer": "l2",
                    "hint": hint,
                    "clocks": clks,
                }
            )
        if want_cube:
            logger.warning(
                "cs_z_matrix_prefer_cube: cube path exception; matrix CS-z fallback",
                exc_info=True,
            )
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

    from core.research.materialize.consumer import MaterializeRequired, consumer_read_only

    read_only = consumer_read_only()
    want_cube = False
    try:
        from core.research.materialize.flags import fit_use_day_cube

        want_cube = bool(fit_use_day_cube() or read_only)
        if want_cube:
            out, meta = apply_day_cube_mu_sigma_dicts(
                rows, names, groups, clock=clock, fallback_open=fallback_open
            )
            if out is not None and meta.get("ok"):
                _warn_cube_cs_soft(meta, where="cs_z_dicts_prefer_cube")
                return out
            if read_only:
                hint, clks = _l2_materialize_hint(groups, default_clock=clock)
                raise MaterializeRequired(
                    {
                        "error_code": "materialize_required",
                        "error": _format_l2_miss_error(meta, default_clock=clock),
                        "layer": "l2",
                        "hint": hint,
                        "clocks": clks,
                    }
                )
            logger.warning(
                "cs_z_dicts_prefer_cube: L2 unavailable (%s); matrix CS-z fallback",
                (meta or {}).get("error") or "ok=False",
            )
    except MaterializeRequired:
        raise
    except Exception:  # noqa: BLE001
        logger.debug("cs_z_dicts_prefer_cube cube path failed", exc_info=True)
        if read_only:
            hint, clks = _l2_materialize_hint(groups, default_clock=clock)
            raise MaterializeRequired(
                {
                    "error_code": "materialize_required",
                    "error": "请先物化 L2：截面立方读取失败"
                    + _cube_disk_span_note(str(clock or "open")),
                    "layer": "l2",
                    "hint": hint,
                    "clocks": clks,
                }
            )
        if want_cube:
            logger.warning(
                "cs_z_dicts_prefer_cube: cube path exception; matrix CS-z fallback",
                exc_info=True,
            )
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
    """dict 面板版：与 ``cross_section_zscore_dicts`` 同形。

    语义同 ``apply_day_cube_mu_sigma_matrix``：部分缺 Cube 桶内截面回退；全日无 Cube 才失败。
    """
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
