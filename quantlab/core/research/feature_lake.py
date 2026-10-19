"""L1 Feature Lake：按股×日特征行（无同伴信息）。

布局::

    {FEATURE_LAKE_DIR}/v{SCHEMA}/{family}/{code}.npz
    {FEATURE_LAKE_DIR}/v{SCHEMA}/{family}/{code}.meta.json

``X[i]`` 对应 ``dates[i]``。fingerprint 含 bars + registry；miss 则整票重写。
尾窗增量：``upsert_tail`` 按日 merge，保留未变日期行。

开关：``QUANTLAB_FEATURE_LAKE=0`` 绕过。
"""

from __future__ import annotations

import io
import json
import logging
import os
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Set

import numpy as np

from core.research.feature_registry import FEATURE_SCHEMA

logger = logging.getLogger(__name__)

_ENV_DISABLE = "QUANTLAB_FEATURE_LAKE"


def enabled() -> bool:
    raw = str(os.environ.get(_ENV_DISABLE, "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _root() -> str:
    override = os.environ.get("QUANTLAB_FEATURE_LAKE_DIR")
    if override:
        return str(override)
    from core.paths import FEATURE_LAKE_DIR

    return str(FEATURE_LAKE_DIR)


def _safe_code(code: str) -> str:
    return "".join(ch for ch in str(code) if ch.isalnum()).lower() or "unknown"


def _safe_family(family: str) -> str:
    s = str(family or "x").strip().replace(os.sep, "_").replace("/", "_").replace("..", "_")
    return s or "x"


def _paths(family: str, code: str) -> Dict[str, str]:
    root = os.path.join(_root(), f"v{FEATURE_SCHEMA}", _safe_family(family))
    base = os.path.join(root, _safe_code(code))
    return {"dir": root, "npz": base + ".npz", "meta": base + ".meta.json"}


def _atomic_write_bytes(path: str, data: bytes) -> None:
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".l1_", suffix=".tmp", dir=parent)
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


def write_stock_panel(
    family: str,
    code: str,
    *,
    dates: Sequence[str],
    X: np.ndarray,
    names: Sequence[str],
    fingerprint: str,
    y: Optional[np.ndarray] = None,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> bool:
    """整票覆盖写入。行数必须与 dates 对齐。

    ``extra_meta`` 可带 ``bars_fp`` / ``stem_fp`` / ``end_date``（尾窗增量）。
    """
    if not enabled() or not code:
        return False
    x_arr = np.asarray(X, dtype=np.float64)
    date_list = [str(d)[:10] for d in dates]
    if x_arr.ndim != 2 or x_arr.shape[0] != len(date_list):
        return False
    if len(names) and x_arr.shape[1] != len(names):
        return False
    y_arr = None
    if y is not None:
        y_arr = np.asarray(y, dtype=np.float64)
        if y_arr.shape[0] != len(date_list):
            return False
    paths = _paths(family, code)
    os.makedirs(paths["dir"], exist_ok=True)
    try:
        payload: Dict[str, Any] = {
            "X": x_arr,
            "dates": np.asarray(date_list, dtype="U10"),
        }
        if y_arr is not None:
            payload["y"] = y_arr
        buf = io.BytesIO()
        np.savez_compressed(buf, **payload)
        _atomic_write_bytes(paths["npz"], buf.getvalue())
    except Exception:
        logger.debug("feature lake write npz failed %s", paths["npz"], exc_info=True)
        return False
    meta: Dict[str, Any] = {
        "schema": FEATURE_SCHEMA,
        "fingerprint": str(fingerprint),
        "code": str(code),
        "family": _safe_family(family),
        "names": [str(n) for n in names],
        "n_rows": int(x_arr.shape[0]),
        "n_cols": int(x_arr.shape[1]),
    }
    if isinstance(extra_meta, dict):
        for k, v in extra_meta.items():
            if k in ("schema", "fingerprint", "code", "names", "n_rows", "n_cols", "family"):
                continue
            meta[k] = v
    try:
        _atomic_write_json(paths["meta"], meta)
    except Exception:
        logger.debug("feature lake write meta failed %s", paths["meta"], exc_info=True)
        return False
    return True


def read_stock_meta(family: str, code: str) -> Optional[Dict[str, Any]]:
    """只读 meta（含 ``bars_fp``）。schema 不符或文件缺失则 None。"""
    if not enabled() or not code:
        return None
    meta = _read_json(_paths(family, code)["meta"])
    if not meta or int(meta.get("schema") or 0) != FEATURE_SCHEMA:
        return None
    return meta


def load_stock_dates(family: str, code: str) -> Optional[Set[str]]:
    """该票 L1 已有的交易日。无分片则 None。"""
    if read_stock_meta(family, code) is None:
        return None
    paths = _paths(family, code)
    if not os.path.isfile(paths["npz"]):
        return None
    try:
        with np.load(paths["npz"], allow_pickle=False) as z:
            return {str(d)[:10] for d in z["dates"].tolist()}
    except (OSError, ValueError, KeyError, EOFError) as exc:
        logger.debug("feature lake dates failed %s: %s", paths["npz"], exc)
        return None


def load_stock_panel(
    family: str,
    code: str,
    fingerprint: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """命中返回 ``{X, dates, names, y?}``。``fingerprint`` 给定则必须一致。"""
    if not enabled() or not code:
        return None
    paths = _paths(family, code)
    meta = _read_json(paths["meta"])
    if not meta:
        return None
    if int(meta.get("schema") or 0) != FEATURE_SCHEMA:
        return None
    if fingerprint is not None and str(meta.get("fingerprint") or "") != str(fingerprint):
        return None
    if not os.path.isfile(paths["npz"]):
        return None
    try:
        with np.load(paths["npz"], allow_pickle=False) as z:
            X = np.asarray(z["X"], dtype=np.float64)
            dates = [str(d) for d in z["dates"].tolist()]
            y = np.asarray(z["y"], dtype=np.float64) if "y" in z.files else None
    except (OSError, ValueError, KeyError, EOFError) as exc:
        logger.debug("feature lake load failed %s: %s", paths["npz"], exc)
        return None
    names = [str(n) for n in (meta.get("names") or [])]
    if X.shape[0] != len(dates):
        return None
    if names and X.shape[1] != len(names):
        return None
    out: Dict[str, Any] = {"X": X, "dates": dates, "names": names, "_meta": meta}
    if y is not None:
        out["y"] = y
    return out


def upsert_tail(
    family: str,
    code: str,
    *,
    dates: Sequence[str],
    X: np.ndarray,
    names: Sequence[str],
    fingerprint: str,
    y: Optional[np.ndarray] = None,
    keep_from: Optional[str] = None,
) -> bool:
    """按日 merge：``keep_from`` 之前的旧行保留，其后用新块覆盖。

    ``keep_from`` 为空时等价于 ``write_stock_panel``。
    """
    if not enabled() or not code:
        return False
    date_list = [str(d)[:10] for d in dates]
    x_new = np.asarray(X, dtype=np.float64)
    if x_new.ndim != 2 or x_new.shape[0] != len(date_list):
        return False
    y_new = None
    if y is not None:
        y_new = np.asarray(y, dtype=np.float64)
        if y_new.shape[0] != len(date_list):
            return False
    cut = str(keep_from or "")[:10]
    if not cut:
        return write_stock_panel(
            family, code, dates=date_list, X=x_new, names=names, fingerprint=fingerprint, y=y_new
        )
    prev = load_stock_panel(family, code, fingerprint=None)
    if prev is None:
        return write_stock_panel(
            family, code, dates=date_list, X=x_new, names=names, fingerprint=fingerprint, y=y_new
        )
    old_names = list(prev.get("names") or [])
    name_list = [str(n) for n in names]
    if old_names and old_names != name_list:
        return write_stock_panel(
            family, code, dates=date_list, X=x_new, names=names, fingerprint=fingerprint, y=y_new
        )
    old_X = np.asarray(prev["X"], dtype=np.float64)
    old_dates = list(prev["dates"])
    old_y = prev.get("y")
    keep_idx = [i for i, d in enumerate(old_dates) if d < cut]
    # rebuild in date order from keep + new
    by_date_x: Dict[str, np.ndarray] = {}
    by_date_y: Dict[str, float] = {}
    for i in keep_idx:
        by_date_x[old_dates[i]] = old_X[i]
        if old_y is not None:
            by_date_y[old_dates[i]] = float(np.asarray(old_y)[i])
    for i, d in enumerate(date_list):
        by_date_x[d] = x_new[i]
        if y_new is not None:
            by_date_y[d] = float(y_new[i])
    ordered = sorted(by_date_x.keys())
    X_m = np.vstack([by_date_x[d] for d in ordered])
    y_m = None
    if y_new is not None or old_y is not None:
        y_m = np.asarray([by_date_y.get(d, np.nan) for d in ordered], dtype=np.float64)
    return write_stock_panel(
        family, code, dates=ordered, X=X_m, names=name_list, fingerprint=fingerprint, y=y_m
    )


def load_day_rows(
    family: str,
    codes: Sequence[str],
    date: str,
) -> Dict[str, Dict[str, float]]:
    """多票取同一日特征 dict（缺票跳过）。"""
    day = str(date or "")[:10]
    out: Dict[str, Dict[str, float]] = {}
    if len(day) < 10:
        return out
    for raw in codes or []:
        code = str(raw or "").strip()
        if not code:
            continue
        pack = load_stock_panel(family, code, fingerprint=None)
        if not pack:
            continue
        dates = pack["dates"]
        try:
            idx = dates.index(day)
        except ValueError:
            continue
        names = list(pack.get("names") or [])
        row = pack["X"][idx]
        feats: Dict[str, float] = {}
        for j, name in enumerate(names):
            if j >= row.shape[0]:
                break
            v = float(row[j])
            if np.isfinite(v):
                feats[str(name)] = v
        if feats:
            out[code] = feats
    return out


def invalidate_code(family: str, code: str) -> None:
    """删除单票 L1 分片（bars 变更后强制重算）。"""
    if not code:
        return
    paths = _paths(family, code)
    for key in ("npz", "meta"):
        try:
            if os.path.isfile(paths[key]):
                os.unlink(paths[key])
        except OSError:
            pass


def sync_from_matrix(
    family: str,
    code: str,
    *,
    X: np.ndarray,
    dates: Sequence[str],
    names: Sequence[str],
    fingerprint: str,
    y: Optional[np.ndarray] = None,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> bool:
    """collect 写穿 L1 入口。"""
    return write_stock_panel(
        family,
        code,
        dates=dates,
        X=X,
        names=names,
        fingerprint=fingerprint,
        y=y,
        extra_meta=extra_meta,
    )
