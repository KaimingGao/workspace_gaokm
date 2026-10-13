"""研究 panel 按股磁盘缓存：miss 写穿，fingerprint 失效。

布局::

    {PANEL_CACHE_DIR}/v{SCHEMA}/{head_key}/{code}.npz
    {PANEL_CACHE_DIR}/v{SCHEMA}/{head_key}/{code}.meta.json
    {PANEL_CACHE_DIR}/v{SCHEMA}/{head_key}/{code}.pack.json   # 可选 slim/xs/metas

开关：``QUANTLAB_PANEL_CACHE=0`` 强制绕过。
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import tempfile
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# 特征注册 / collect_* 语义变更时人工 +1 → 全池 miss
PANEL_CACHE_SCHEMA = 1

_ENV_DISABLE = "QUANTLAB_PANEL_CACHE"


def enabled() -> bool:
    raw = str(os.environ.get(_ENV_DISABLE, "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _cache_root() -> str:
    # 每次读 env，便于测试临时改目录；未设时回落 paths 常量
    override = os.environ.get("QUANTLAB_PANEL_CACHE_DIR")
    if override:
        return str(override)
    from core.paths import PANEL_CACHE_DIR

    return str(PANEL_CACHE_DIR)


def safe_code(code: str) -> str:
    return "".join(ch for ch in str(code) if ch.isalnum()).lower() or "unknown"


def bars_fingerprint(
    bars: Optional[Sequence[dict]],
    *,
    minute_bars: Optional[Sequence[dict]] = None,
) -> str:
    """日线 / 分钟跨度 + 末 bar 内容屑；新交易日写入后自然变。"""
    seq = [b for b in (bars or []) if isinstance(b, dict)]
    if not seq:
        base = "empty"
    else:
        first = str(seq[0].get("date") or seq[0].get("trade_date") or "")[:10]
        last_b = seq[-1]
        last = str(last_b.get("date") or last_b.get("trade_date") or "")[:10]
        crumb = (
            f"{last_b.get('open')}|{last_b.get('high')}|{last_b.get('low')}|"
            f"{last_b.get('close')}|{last_b.get('volume')}"
        )
        base = f"n{len(seq)}:{first}:{last}:{crumb}"
    mb = [b for b in (minute_bars or []) if isinstance(b, dict)]
    if mb:
        m0 = str(mb[0].get("datetime") or mb[0].get("date") or "")[:19]
        m1 = str(mb[-1].get("datetime") or mb[-1].get("date") or "")[:19]
        mcrumb = f"{mb[-1].get('close')}|{mb[-1].get('volume')}"
        base = f"{base}|m{len(mb)}:{m0}:{m1}:{mcrumb}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def make_fingerprint(*, bars_fp: str, **kwargs: Any) -> str:
    parts = [f"schema={PANEL_CACHE_SCHEMA}", f"bars={bars_fp}"]
    for key in sorted(kwargs):
        parts.append(f"{key}={kwargs[key]}")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:20]


def head_relpath(head: str, *parts: str) -> str:
    """相对 ``v{SCHEMA}/`` 的目录，如 ``oo/h1_core0``。"""
    safe_parts = []
    for p in parts:
        s = str(p).strip().replace(os.sep, "_").replace("/", "_").replace("..", "_")
        safe_parts.append(s or "_")
    head_s = str(head or "x").strip().replace(os.sep, "_") or "x"
    return os.path.join(head_s, *safe_parts) if safe_parts else head_s


def _shard_paths(head_key: str, code: str) -> Dict[str, str]:
    root = os.path.join(_cache_root(), f"v{PANEL_CACHE_SCHEMA}", head_key)
    base = os.path.join(root, safe_code(code))
    return {
        "dir": root,
        "npz": base + ".npz",
        "meta": base + ".meta.json",
        "pack": base + ".pack.json",
    }


def _atomic_write_bytes(path: str, data: bytes) -> None:
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".panel_", suffix=".tmp", dir=parent)
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


def load_stock_shard(
    head_key: str,
    code: str,
    fingerprint: str,
) -> Optional[Dict[str, Any]]:
    """命中返回 ``{X?, y, names?, dates, metas?, slim?, xs?}``；否则 None。"""
    if not enabled():
        return None
    paths = _shard_paths(head_key, code)
    meta = _read_json(paths["meta"])
    if not meta:
        return None
    if int(meta.get("schema") or 0) != PANEL_CACHE_SCHEMA:
        return None
    if str(meta.get("fingerprint") or "") != str(fingerprint):
        return None
    if not os.path.isfile(paths["npz"]):
        return None
    try:
        with np.load(paths["npz"], allow_pickle=False) as z:
            y = np.asarray(z["y"], dtype=np.float64)
            dates_arr = z["dates"]
            dates = [str(d) for d in dates_arr.tolist()]
            X = None
            if "X" in z.files:
                X = np.asarray(z["X"], dtype=np.float64)
    except (OSError, ValueError, KeyError, EOFError) as exc:
        logger.debug("panel cache load failed %s: %s", paths["npz"], exc)
        return None
    names = meta.get("names")
    out: Dict[str, Any] = {
        "y": y,
        "dates": dates,
        "names": [str(n) for n in names] if isinstance(names, list) else [],
    }
    if X is not None:
        out["X"] = X
        if X.shape[0] != y.shape[0] or X.shape[0] != len(dates):
            return None
    elif y.shape[0] != len(dates):
        return None
    pack = _read_json(paths["pack"])
    if pack:
        for key in ("metas", "slim", "xs"):
            if key in pack:
                out[key] = pack[key]
    return out


def save_stock_shard(
    head_key: str,
    code: str,
    fingerprint: str,
    *,
    y: np.ndarray,
    dates: Sequence[str],
    names: Optional[Sequence[str]] = None,
    X: Optional[np.ndarray] = None,
    metas: Optional[List[Any]] = None,
    slim: Optional[List[Any]] = None,
    xs: Optional[List[Any]] = None,
) -> None:
    if not enabled():
        return
    y_arr = np.asarray(y, dtype=np.float64)
    date_list = [str(d)[:10] for d in dates]
    if y_arr.shape[0] != len(date_list):
        return
    if X is not None:
        x_arr = np.asarray(X, dtype=np.float64)
        if x_arr.ndim != 2 or x_arr.shape[0] != y_arr.shape[0]:
            return
    else:
        x_arr = None
    paths = _shard_paths(head_key, code)
    os.makedirs(paths["dir"], exist_ok=True)
    try:
        payload: Dict[str, Any] = {
            "y": y_arr,
            "dates": np.asarray(date_list, dtype="U10"),
        }
        if x_arr is not None:
            payload["X"] = x_arr
        buf = io.BytesIO()
        np.savez_compressed(buf, **payload)
        _atomic_write_bytes(paths["npz"], buf.getvalue())
    except Exception:
        logger.debug("panel cache save npz failed %s", paths["npz"], exc_info=True)
        return
    meta = {
        "schema": PANEL_CACHE_SCHEMA,
        "fingerprint": str(fingerprint),
        "code": str(code),
        "names": [str(n) for n in (names or [])],
        "n_rows": int(y_arr.shape[0]),
        "n_cols": int(x_arr.shape[1]) if x_arr is not None else 0,
    }
    try:
        _atomic_write_json(paths["meta"], meta)
    except Exception:
        logger.debug("panel cache save meta failed %s", paths["meta"], exc_info=True)
        return
    pack: Dict[str, Any] = {}
    if metas is not None:
        pack["metas"] = list(metas)
    if slim is not None:
        pack["slim"] = list(slim)
    if xs is not None:
        pack["xs"] = list(xs)
    if pack:
        try:
            _atomic_write_json(paths["pack"], pack)
        except Exception:
            logger.debug("panel cache save pack failed %s", paths["pack"], exc_info=True)


def oo_head_key(*, horizon_days: int, ridge_core_only: bool) -> str:
    return head_relpath("oo", f"h{int(horizon_days)}_core{1 if ridge_core_only else 0}")


def oo_rank_head_key(
    *,
    horizon_days: int,
    min_history: int,
    max_window: int,
    excess_mode: str,
    respect_regime: bool,
) -> str:
    ex = "".join(ch for ch in str(excess_mode or "none") if ch.isalnum() or ch == "_") or "none"
    return head_relpath(
        "oo_rank",
        f"h{int(horizon_days)}_mh{int(min_history)}_mw{int(max_window)}_ex{ex}_rg{1 if respect_regime else 0}",
    )


def co_head_key(*, min_history: int, include_alpha_oo: bool) -> str:
    return head_relpath(
        "co", f"mh{int(min_history)}_aoo{1 if include_alpha_oo else 0}"
    )


def tc_head_key(
    *,
    tau_hm: str,
    min_history: int,
    include_alpha_tc: bool,
    use_minute: bool,
) -> str:
    hm = "".join(ch for ch in str(tau_hm or "open") if ch.isalnum() or ch in "_-") or "open"
    return head_relpath(
        "tc",
        f"hm_{hm}_mh{int(min_history)}_atc{1 if include_alpha_tc else 0}_m{1 if use_minute else 0}",
    )
