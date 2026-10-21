"""L1 Feature Lake：按股×日特征行（无同伴信息）。

布局::

    {FEATURE_LAKE_DIR}/v{SCHEMA}/{family}/{code}.npz
    {FEATURE_LAKE_DIR}/v{SCHEMA}/{family}/{code}.meta.json

``X[i]`` 对应 ``dates[i]``。fingerprint 含 bars + registry。
热路径：内存 ``merge_matrix_rows`` 后 ``write_stock_panel`` 整票覆盖；
指纹命中跳过，尾窗/历史前缀只重算再 merge。短窗真后缀不覆盖更长面板。
指纹或 ``bars_fp`` 变化时，把改过的日期交给 L2 回收。

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


def _lake_identity_same(
    old_meta: Optional[Dict[str, Any]],
    fingerprint: str,
    extra_meta: Optional[Dict[str, Any]],
) -> bool:
    """指纹和价量指纹都没变时，不把这次写入当成 L2 过期。"""
    if not isinstance(old_meta, dict):
        return False
    if str(old_meta.get("fingerprint") or "") != str(fingerprint):
        return False
    new_bars = ""
    if isinstance(extra_meta, dict):
        new_bars = str(extra_meta.get("bars_fp") or "")
    return str(old_meta.get("bars_fp") or "") == new_bars


def _l2_tracking() -> bool:
    try:
        from core.research.day_cube_gc import gc_enabled

        return bool(gc_enabled())
    except Exception:  # noqa: BLE001
        logger.debug("l2 gc probe failed", exc_info=True)
        return False


def _stale_feature_dates(
    old_pack: Optional[Dict[str, Any]],
    dates: Sequence[str],
    X: np.ndarray,
    names: Sequence[str],
) -> Set[str]:
    """新旧特征行对不上的日期。没有旧面板时，新日期都算过期。"""
    new_dates = [str(d)[:10] for d in dates]
    if not old_pack:
        return set(new_dates)
    old_dates = [str(d)[:10] for d in (old_pack.get("dates") or [])]
    old_names = [str(n) for n in (old_pack.get("names") or [])]
    new_names = [str(n) for n in names]
    if old_names != new_names:
        return set(old_dates) | set(new_dates)
    try:
        old_X = np.asarray(old_pack.get("X"), dtype=np.float64)
        new_X = np.asarray(X, dtype=np.float64)
    except (TypeError, ValueError):
        return set(old_dates) | set(new_dates)
    if old_X.ndim != 2 or new_X.ndim != 2:
        return set(old_dates) | set(new_dates)
    old_index = {d: i for i, d in enumerate(old_dates)}
    stale: Set[str] = set()
    for i, day in enumerate(new_dates):
        j = old_index.get(day)
        if j is None or j >= old_X.shape[0] or i >= new_X.shape[0]:
            stale.add(day)
            continue
        try:
            changed = not np.array_equal(old_X[j], new_X[i], equal_nan=True)
        except (TypeError, ValueError):
            changed = True
        if changed:
            stale.add(day)
    new_set = set(new_dates)
    for day in old_dates:
        if day not in new_set:
            stale.add(day)
    return stale


def _note_l2_stale(
    old_meta: Optional[Dict[str, Any]],
    old_pack: Optional[Dict[str, Any]],
    dates: Sequence[str],
    X: np.ndarray,
    names: Sequence[str],
    fingerprint: str,
    extra_meta: Optional[Dict[str, Any]],
    old_dates: Optional[Set[str]] = None,
) -> None:
    """物化写入才按变化日期拆立方。回测/拟合的训练缓存写穿不拆。"""
    if not _l2_tracking():
        return
    try:
        from core.research.materialize.consumer import writer_active

        if not writer_active():
            return
    except Exception:  # noqa: BLE001
        logger.debug("l2 stale writer probe failed", exc_info=True)
        return
    if _lake_identity_same(old_meta, fingerprint, extra_meta):
        return
    try:
        from core.research.day_cube_gc import note_stale_dates

        stale = _stale_feature_dates(old_pack, dates, X, names)
        if old_pack is None and old_dates:
            stale.update(old_dates)
        if isinstance(old_meta, dict) and old_pack is None:
            logger.warning("feature lake old panel unreadable; marking known dates stale")
        note_stale_dates(stale)
    except Exception:  # noqa: BLE001
        logger.warning("note l2 stale dates failed", exc_info=True)


def _is_shorter_suffix_panel(
    old_dates: Sequence[str],
    new_dates: Sequence[str],
) -> bool:
    """新面板是旧面板的真后缀（同末日、更短窗）→ 禁止短窗盖长窗。"""
    old = [str(d)[:10] for d in old_dates if len(str(d)[:10]) >= 10]
    new = [str(d)[:10] for d in new_dates if len(str(d)[:10]) >= 10]
    if not old or not new or len(new) >= len(old):
        return False
    return old[-len(new) :] == new


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
    若盘上已有更长且同后缀的面板，拒绝用短窗覆盖（返回 True，保留长窗）。
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
    track_l2 = _l2_tracking()
    old_meta = _read_json(paths["meta"])
    old_pack = None
    old_dates: Optional[Set[str]] = None
    identity_same = _lake_identity_same(old_meta, fingerprint, extra_meta)
    need_old = (track_l2 and not identity_same) or bool(old_meta)
    if need_old and isinstance(old_meta, dict):
        old_pack = load_stock_panel(family, code, fingerprint=None)
    if old_pack is not None:
        prev_dates = [str(d)[:10] for d in (old_pack.get("dates") or [])]
        if _is_shorter_suffix_panel(prev_dates, date_list):
            logger.info(
                "feature lake keep longer panel family=%s code=%s old_rows=%s new_rows=%s",
                family,
                code,
                len(prev_dates),
                len(date_list),
            )
            return True
    if track_l2 and not identity_same and old_pack is None:
        _, old_dates = peek_stock_dates(family, code)
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
    _note_l2_stale(
        old_meta,
        old_pack,
        date_list,
        x_arr,
        names,
        fingerprint,
        extra_meta,
        old_dates=old_dates,
    )
    return True


def read_stock_meta(family: str, code: str) -> Optional[Dict[str, Any]]:
    """只读 meta（含 ``bars_fp``）。schema 不符或文件缺失则 None。"""
    if not enabled() or not code:
        return None
    meta = _read_json(_paths(family, code)["meta"])
    if not meta or int(meta.get("schema") or 0) != FEATURE_SCHEMA:
        return None
    return meta


def peek_stock_dates(family: str, code: str) -> tuple:
    """``(分片在, 日期)``。不看 schema。日期读不出时第二项为 None。"""
    if not code:
        return False, None
    paths = _paths(family, code)
    npz_exists = os.path.isfile(paths["npz"])
    meta_exists = os.path.isfile(paths["meta"])
    if not npz_exists and not meta_exists:
        return False, None
    if not npz_exists:
        return True, None
    try:
        with np.load(paths["npz"], allow_pickle=False) as z:
            if "dates" not in z.files:
                return True, None
            return True, {str(d)[:10] for d in z["dates"].tolist() if len(str(d)[:10]) >= 10}
    except (OSError, ValueError, KeyError, EOFError) as exc:
        logger.debug("feature lake dates failed %s: %s", paths["npz"], exc)
        return True, None


def load_stock_dates(family: str, code: str) -> Optional[Set[str]]:
    """该票 L1 已有的交易日。无分片或 schema 不符则 None。"""
    if read_stock_meta(family, code) is None:
        return None
    _exists, found = peek_stock_dates(family, code)
    return found


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


def finite_feature_dict(names: Sequence[str], row: Any) -> Dict[str, float]:
    """一行里的有限值。坏单元格丢掉，不让整行失败。"""
    feats: Dict[str, float] = {}
    if not names or row is None:
        return feats
    try:
        vec = np.asarray(row).reshape(-1)
    except (TypeError, ValueError):
        return feats
    for j, name in enumerate(names):
        if j >= int(vec.shape[0]):
            break
        key = str(name or "").strip()
        if not key:
            continue
        try:
            v = float(vec[j])
        except (TypeError, ValueError):
            continue
        if np.isfinite(v):
            feats[key] = v
    return feats


def load_day_rows(
    family: str,
    codes: Sequence[str],
    date: str,
) -> Dict[str, Dict[str, float]]:
    """多票取同一日特征 dict（缺票跳过）。

    和按块写立方共用 ``slice_stock_panels``，不再各写一套打开面板的循环。
    """
    day = str(date or "")[:10]
    if len(day) < 10:
        return {}
    sliced = slice_stock_panels(family, codes, [day])
    if not sliced:
        return {}
    out: Dict[str, Dict[str, float]] = {}
    for code, hit in sliced.items():
        if not hit or len(hit) < 2 or not isinstance(hit[1], dict):
            continue
        feats = finite_feature_dict(hit[0], hit[1].get(day))
        if feats:
            out[code] = feats
    return out


def slice_stock_panels(
    family: str,
    codes: Sequence[str],
    dates: Sequence[str],
    *,
    progress_cb: Optional[Any] = None,
    cancel_cb: Optional[Any] = None,
) -> Optional[Dict[str, tuple]]:
    """每只股票只打开一次 L1，切出 ``dates`` 里有的行。

    返回 ``code -> (names, {date: row})``。缺票、坏文件、或这些日期都没有行的票省略。
    行是拷贝，调用方可以丢掉整份面板。
    取消时返回 ``None``（不是空字典）：空字典表示没读到行，``None`` 表示没读完，
    调用方不能用半池结果写立方。
    ``progress_cb(done, total)`` 每 32 只叫一次，避免长读盘把任务心跳饿死。
    """
    want = [str(d)[:10] for d in (dates or []) if len(str(d or "")[:10]) >= 10]
    want_set = set(want)
    out: Dict[str, tuple] = {}
    code_list: List[str] = []
    seen_codes: Set[str] = set()
    for raw in codes or []:
        code = str(raw or "").strip()
        if not code or code in seen_codes:
            continue
        seen_codes.add(code)
        code_list.append(code)
    if not want_set or not code_list:
        return out
    n_codes = len(code_list)
    failed = 0
    first_fail = ""

    def _cancelled() -> bool:
        if not cancel_cb:
            return False
        try:
            return bool(cancel_cb())
        except Exception:  # noqa: BLE001
            return False

    for done, code in enumerate(code_list, start=1):
        if _cancelled():
            return None
        if progress_cb and (done == 1 or done == n_codes or done % 32 == 0):
            try:
                progress_cb(done, n_codes)
            except Exception:  # noqa: BLE001
                logger.debug("slice_stock_panels progress failed", exc_info=True)
        try:
            pack = load_stock_panel(family, code, fingerprint=None)
            if not pack:
                continue
            names = [str(n) for n in (pack.get("names") or [])]
            by_day: Dict[str, np.ndarray] = {}
            seen: Set[str] = set()
            X = np.asarray(pack.get("X"))
            if X.ndim != 2:
                raise ValueError(f"panel X ndim={X.ndim}")
            n_rows = int(X.shape[0])
            for i, raw_day in enumerate(pack.get("dates") or []):
                if i >= n_rows:
                    break
                day = str(raw_day)[:10]
                if day not in want_set or day in seen:
                    continue
                seen.add(day)
                by_day[day] = np.array(X[i], dtype=np.float64, copy=True)
            if by_day:
                out[code] = (names, by_day)
        except Exception:  # noqa: BLE001
            failed += 1
            if not first_fail:
                first_fail = code
            logger.debug(
                "slice_stock_panels skip family=%s code=%s",
                family,
                code,
                exc_info=True,
            )
    if failed:
        logger.warning(
            "slice_stock_panels family=%s skipped %s codes first=%s",
            family,
            failed,
            first_fail,
        )
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
