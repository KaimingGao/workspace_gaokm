"""L2 CrossSection DayCube：按日截面统计（μ/σ、breadth、gap 池）。

布局::

    {DAY_CUBE_DIR}/v{SCHEMA}/{universe_hash}/{clock}/{date}.npz
    {DAY_CUBE_DIR}/v{SCHEMA}/{universe_hash}/{clock}/{date}.meta.json

``clock``：``eod`` / ``open`` / ``tau_HHMM``。

开关：``QUANTLAB_DAY_CUBE=0`` 绕过磁盘；内存构建仍可用。
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from core.research.feature_registry import FEATURE_SCHEMA, universe_hash

logger = logging.getLogger(__name__)

_ENV_DISABLE = "QUANTLAB_DAY_CUBE"

# 进程内当日 cube（live 复用）
_MEMORY: Dict[str, "DayCube"] = {}


def enabled() -> bool:
    raw = str(os.environ.get(_ENV_DISABLE, "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _root() -> str:
    override = os.environ.get("QUANTLAB_DAY_CUBE_DIR")
    if override:
        return str(override)
    from core.paths import DAY_CUBE_DIR

    return str(DAY_CUBE_DIR)


def _safe_clock(clock: str) -> str:
    s = "".join(ch for ch in str(clock or "open") if ch.isalnum() or ch in "_-") or "open"
    return s


@dataclass
class DayCube:
    """单日截面块。"""

    date: str
    codes: List[str]
    names: List[str]
    X_raw: np.ndarray  # (N, P)
    z_mean: np.ndarray  # (P,)
    z_std: np.ndarray  # (P,)
    universe_hash: str
    clock: str = "open"
    slim: Dict[str, Any] = field(default_factory=dict)
    schema: int = FEATURE_SCHEMA

    def z_matrix(self, *, min_names: int = 2, eps: float = 1e-12) -> np.ndarray:
        """用预计算 μ/σ 做 z；与 ``cross_section_zscore_matrix`` 单组语义对齐。"""
        x = np.asarray(self.X_raw, dtype=np.float64)
        out = np.array(x, copy=True)
        n, p = out.shape
        need = max(1, int(min_names))
        for j in range(p):
            col = out[:, j]
            finite = np.isfinite(col)
            m = int(finite.sum())
            mu = float(self.z_mean[j]) if j < len(self.z_mean) else float("nan")
            sd = float(self.z_std[j]) if j < len(self.z_std) else float("nan")
            if m < need or (not math.isfinite(mu)):
                if m:
                    out[finite, j] = 0.0
                continue
            if (not math.isfinite(sd)) or sd <= eps:
                out[finite, j] = 0.0
                continue
            out[finite, j] = (col[finite] - mu) / sd
        return out

    def to_tau_pool_day(self) -> Dict[str, Any]:
        """兼容 ``seed_tau_cross_section_day`` 的 pool_day 形状。"""
        slim = dict(self.slim or {})
        out: Dict[str, Any] = {}
        for key in (
            "pool_gaps",
            "sector_gap_breadth",
            "gaps_by_code",
            "ref_by_code",
        ):
            if key in slim:
                out[key] = slim[key]
        return out


def _compute_mu_sigma(
    X: np.ndarray,
    *,
    min_names: int = 2,
    eps: float = 1e-12,
) -> Tuple[np.ndarray, np.ndarray]:
    x = np.asarray(X, dtype=np.float64)
    n, p = x.shape if x.ndim == 2 else (0, 0)
    mu = np.full(p, np.nan, dtype=np.float64)
    sd = np.full(p, np.nan, dtype=np.float64)
    need = max(1, int(min_names))
    for j in range(p):
        col = x[:, j]
        finite = np.isfinite(col)
        m = int(finite.sum())
        if m < need:
            continue
        vals = col[finite]
        m_u = float(vals.mean())
        var = float(np.mean((vals - m_u) ** 2))
        mu[j] = m_u
        if math.isfinite(var) and var > eps:
            s = math.sqrt(var)
            sd[j] = s if s >= 1e-12 else np.nan
    return mu, sd


def _universe_code_list(codes: Optional[Sequence[str]]) -> List[str]:
    return sorted({str(c).strip() for c in (codes or []) if str(c or "").strip()})


def build_day_cube(
    date: str,
    code_feats: Mapping[str, Mapping[str, Any]],
    *,
    names: Optional[Sequence[str]] = None,
    clock: str = "open",
    slim: Optional[Dict[str, Any]] = None,
    min_names: int = 2,
    universe_codes: Optional[Sequence[str]] = None,
) -> Optional[DayCube]:
    """从 ``{code: feature_dict}`` 构建 DayCube。

    ``universe_codes`` 有值时，哈希用这份完整名单（与 live/T0 的观察池读取键一致）。
    矩阵仍只含有特征行的代码；缺行的代码不进 μ/σ。
    """
    day = str(date or "")[:10]
    if len(day) < 10 or not code_feats:
        return None
    codes = sorted({str(c).strip() for c in code_feats.keys() if str(c or "").strip()})
    if not codes:
        return None
    uh_codes = _universe_code_list(universe_codes) or codes
    if names is None:
        keys = set()
        for feats in code_feats.values():
            if isinstance(feats, Mapping):
                keys.update(str(k) for k in feats.keys())
        name_list = sorted(keys)
    else:
        name_list = [str(n) for n in names]
    if not name_list:
        return None
    n, p = len(codes), len(name_list)
    X = np.full((n, p), np.nan, dtype=np.float64)
    for i, code in enumerate(codes):
        src = code_feats.get(code) or {}
        if not isinstance(src, Mapping):
            continue
        for j, name in enumerate(name_list):
            v = src.get(name)
            if v is None or v == "":
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(fv):
                X[i, j] = fv
    mu, sd = _compute_mu_sigma(X, min_names=min_names)
    uh = universe_hash(uh_codes)
    return DayCube(
        date=day,
        codes=codes,
        names=name_list,
        X_raw=X,
        z_mean=mu,
        z_std=sd,
        universe_hash=uh,
        clock=_safe_clock(clock),
        slim=dict(slim or {}),
    )


def build_from_matrix(
    date: str,
    codes: Sequence[str],
    X: np.ndarray,
    names: Sequence[str],
    *,
    clock: str = "open",
    slim: Optional[Dict[str, Any]] = None,
    min_names: int = 2,
) -> Optional[DayCube]:
    """已对齐矩阵直接建 cube。"""
    day = str(date or "")[:10]
    code_list = [str(c).strip() for c in codes if str(c or "").strip()]
    x_arr = np.asarray(X, dtype=np.float64)
    name_list = [str(n) for n in names]
    if len(day) < 10 or not code_list or x_arr.ndim != 2:
        return None
    if x_arr.shape[0] != len(code_list) or x_arr.shape[1] != len(name_list):
        return None
    mu, sd = _compute_mu_sigma(x_arr, min_names=min_names)
    return DayCube(
        date=day,
        codes=list(code_list),
        names=name_list,
        X_raw=x_arr,
        z_mean=mu,
        z_std=sd,
        universe_hash=universe_hash(code_list),
        clock=_safe_clock(clock),
        slim=dict(slim or {}),
    )


def _disk_paths(uh: str, clock: str, date: str) -> Dict[str, str]:
    root = os.path.join(_root(), f"v{FEATURE_SCHEMA}", str(uh), _safe_clock(clock))
    base = os.path.join(root, str(date)[:10])
    return {"dir": root, "npz": base + ".npz", "meta": base + ".meta.json"}


def _mem_key(uh: str, clock: str, date: str) -> str:
    return f"{uh}|{_safe_clock(clock)}|{str(date)[:10]}"


def _atomic_write_bytes(path: str, data: bytes) -> None:
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".cube_", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _atomic_write_bytes(path, raw)


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def save_day_cube(cube: DayCube) -> bool:
    if cube is None or not enabled():
        return False
    paths = _disk_paths(cube.universe_hash, cube.clock, cube.date)
    os.makedirs(paths["dir"], exist_ok=True)
    try:
        payload = {
            "X_raw": np.asarray(cube.X_raw, dtype=np.float64),
            "z_mean": np.asarray(cube.z_mean, dtype=np.float64),
            "z_std": np.asarray(cube.z_std, dtype=np.float64),
            "codes": np.asarray(cube.codes, dtype="U16"),
            "names": np.asarray(cube.names, dtype="U64"),
        }
        buf = io.BytesIO()
        np.savez_compressed(buf, **payload)
        _atomic_write_bytes(paths["npz"], buf.getvalue())
    except Exception:
        logger.debug("day cube save npz failed %s", paths["npz"], exc_info=True)
        return False
    meta = {
        "schema": FEATURE_SCHEMA,
        "date": cube.date,
        "clock": cube.clock,
        "universe_hash": cube.universe_hash,
        "n_codes": len(cube.codes),
        "n_cols": len(cube.names),
        "slim": cube.slim or {},
    }
    try:
        _atomic_write_json(paths["meta"], meta)
    except Exception:
        logger.debug("day cube save meta failed %s", paths["meta"], exc_info=True)
        return False
    _MEMORY[_mem_key(cube.universe_hash, cube.clock, cube.date)] = cube
    return True


def day_cube_status(
    date: str,
    codes: Sequence[str],
    *,
    clock: str = "open",
) -> str:
    """``ok`` / ``miss`` / ``thin`` / ``universe``。只读 meta，不把矩阵载入内存。"""
    day = str(date or "")[:10]
    if len(day) < 10:
        return "miss"
    uh = universe_hash(codes)
    clk = _safe_clock(clock)
    paths = _disk_paths(uh, clk, day)
    meta = _read_json(paths["meta"])
    if meta and int(meta.get("schema") or 0) == FEATURE_SCHEMA:
        if str(meta.get("universe_hash") or "") != uh:
            return "universe"
        if int(meta.get("n_cols") or 0) < 2:
            return "thin"
        if not os.path.isfile(paths["npz"]):
            return "miss"
        return "ok"
    return "miss"


def attach_slim(
    date: str,
    codes: Sequence[str],
    *,
    clock: str = "open",
    slim: Dict[str, Any],
) -> bool:
    """把 τ 池等字段并进已有立方的 slim，不改 ``X_raw`` / μ/σ。"""
    cube = load_day_cube(date, codes, clock=clock)
    if cube is None or not isinstance(slim, dict) or not slim:
        return False
    merged = dict(cube.slim or {})
    merged.update(slim)
    cube.slim = merged
    return bool(save_day_cube(cube))


def load_day_cube(
    date: str,
    codes: Sequence[str],
    *,
    clock: str = "open",
) -> Optional[DayCube]:
    """按当前宇宙 hash 加载；宇宙不一致则 miss。"""
    day = str(date or "")[:10]
    uh = universe_hash(codes)
    clk = _safe_clock(clock)
    mk = _mem_key(uh, clk, day)
    hit = _MEMORY.get(mk)
    if hit is not None:
        return hit
    if not enabled() or len(day) < 10:
        return None
    paths = _disk_paths(uh, clk, day)
    meta = _read_json(paths["meta"])
    if not meta or int(meta.get("schema") or 0) != FEATURE_SCHEMA:
        return None
    if str(meta.get("universe_hash") or "") != uh:
        return None
    if not os.path.isfile(paths["npz"]):
        return None
    try:
        with np.load(paths["npz"], allow_pickle=False) as z:
            X = np.asarray(z["X_raw"], dtype=np.float64)
            mu = np.asarray(z["z_mean"], dtype=np.float64)
            sd = np.asarray(z["z_std"], dtype=np.float64)
            code_list = [str(c) for c in z["codes"].tolist()]
            name_list = [str(n) for n in z["names"].tolist()]
    except (OSError, ValueError, KeyError, EOFError) as exc:
        logger.debug("day cube load failed %s: %s", paths["npz"], exc)
        return None
    cube = DayCube(
        date=day,
        codes=code_list,
        names=name_list,
        X_raw=X,
        z_mean=mu,
        z_std=sd,
        universe_hash=uh,
        clock=clk,
        slim=dict(meta.get("slim") or {}),
    )
    _MEMORY[mk] = cube
    return cube


def remember(cube: DayCube) -> None:
    if cube is None:
        return
    _MEMORY[_mem_key(cube.universe_hash, cube.clock, cube.date)] = cube


def clear_memory() -> None:
    _MEMORY.clear()


def build_or_load(
    date: str,
    code_feats: Mapping[str, Mapping[str, Any]],
    *,
    names: Optional[Sequence[str]] = None,
    clock: str = "open",
    slim: Optional[Dict[str, Any]] = None,
    persist: bool = True,
    universe_codes: Optional[Sequence[str]] = None,
) -> Optional[DayCube]:
    codes = [str(c).strip() for c in (code_feats or {}).keys() if str(c or "").strip()]
    lookup = _universe_code_list(universe_codes) or codes
    hit = load_day_cube(date, lookup, clock=clock)
    if hit is not None:
        return hit
    cube = build_day_cube(
        date,
        code_feats,
        names=names,
        clock=clock,
        slim=slim,
        universe_codes=lookup,
    )
    if cube is None:
        return None
    if persist:
        save_day_cube(cube)
    else:
        remember(cube)
    return cube


def cubes_from_stacked_panel(
    dates: Sequence[str],
    codes: Sequence[str],
    X: np.ndarray,
    names: Sequence[str],
    *,
    clock: str = "open",
    persist: bool = True,
) -> Dict[str, DayCube]:
    """训练堆叠面板 → 按日 DayCube（codes 与行一一对应）。"""
    date_list = [str(d)[:10] for d in dates]
    code_list = [str(c).strip() for c in codes]
    x_arr = np.asarray(X, dtype=np.float64)
    name_list = [str(n) for n in names]
    out: Dict[str, DayCube] = {}
    if x_arr.ndim != 2 or len(date_list) != x_arr.shape[0] or len(code_list) != x_arr.shape[0]:
        return out
    buckets: Dict[str, List[int]] = {}
    for i, d in enumerate(date_list):
        buckets.setdefault(d, []).append(i)
    for day, idxs in buckets.items():
        sub_codes = [code_list[i] for i in idxs]
        sub_X = x_arr[np.asarray(idxs, dtype=np.int64)]
        cube = build_from_matrix(day, sub_codes, sub_X, name_list, clock=clock)
        if cube is None:
            continue
        if persist:
            save_day_cube(cube)
        else:
            remember(cube)
        out[day] = cube
    return out


def apply_cube_z_to_dicts(
    rows: Sequence[Mapping[str, Any]],
    codes: Sequence[str],
    cube: DayCube,
) -> List[dict]:
    """用 cube μ/σ 对特征 dict 列表做 z（与行 codes 对齐）。"""
    out: List[dict] = []
    name_set = set(cube.names)
    mu_map = {n: float(cube.z_mean[j]) for j, n in enumerate(cube.names)}
    sd_map = {n: float(cube.z_std[j]) for j, n in enumerate(cube.names)}
    for i, row in enumerate(rows):
        src = dict(row) if isinstance(row, Mapping) else {}
        code = str(codes[i] if i < len(codes) else "").strip()
        # 即使 code 不在 cube.codes，仍用当日 μ/σ（与整池 z 一致）
        for name in list(src.keys()):
            if name not in name_set:
                continue
            v = src.get(name)
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(fv):
                continue
            mu = mu_map.get(name)
            sd = sd_map.get(name)
            if mu is None or (not math.isfinite(mu)):
                zval = 0.0
            elif sd is None or (not math.isfinite(sd)) or sd < 1e-12:
                zval = 0.0
            else:
                zval = (fv - mu) / sd
            try:
                from core.research.feature_standardize import mirror_z_alias_value

                mirror_z_alias_value(src, name, zval)
            except Exception:  # noqa: BLE001
                src[name] = zval
        out.append(src)
    return out


def load_tau_pool_day(
    date: str,
    codes: Sequence[str],
    *,
    clock: str = "open",
) -> Optional[Dict[str, Any]]:
    cube = load_day_cube(date, codes, clock=clock)
    if cube is None:
        return None
    pool = cube.to_tau_pool_day()
    return pool or None
