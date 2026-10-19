"""L2 DayCube 回收：过期日期作废，冷宇宙与旧 schema 目录删除。

保留集是观察池读取名单与拟合名单的 ``universe_hash``。任一侧读取抛错，或两边都不足 2 只时，不回收冷目录。
日期作废扫当前 schema 下每个宇宙的每个钟，不打开立方。``QUANTLAB_DAY_CUBE_GC=0`` 或
``QUANTLAB_DAY_CUBE=0`` 时两个动作都不做。
"""

from __future__ import annotations

import contextvars
import logging
import os
import re
import shutil
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional, Sequence, Set

logger = logging.getLogger(__name__)

_ENV_GC = "QUANTLAB_DAY_CUBE_GC"
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BATCH: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "l2_drop_batch",
    default=None,
)
_LOCK = threading.Lock()


def _implicit_test_process() -> bool:
    """``python -m unittest`` / pytest 且未设置回收开关时，不扫本机立方。

    只认启动程序本身，不扫整条命令行，避免参数里出现这些词就把回收关掉。
    """
    if _ENV_GC in os.environ:
        return False
    argv = [str(a) for a in (sys.argv or [])]
    if not argv:
        return False
    prog = os.path.basename(argv[0]).lower()
    if prog in {"pytest", "py.test"}:
        return True
    if prog == "__main__.py":
        folder = os.path.basename(os.path.dirname(os.path.abspath(argv[0]))).lower()
        if folder in {"unittest", "pytest"}:
            return True
    return False


def gc_enabled() -> bool:
    """落盘关闭、回收开关关闭，或未显式打开的单测进程，不做删除。"""
    if _implicit_test_process():
        return False
    try:
        from core.research.day_cube import enabled as cube_enabled

        if not cube_enabled():
            return False
    except Exception:  # noqa: BLE001
        logger.debug("day cube gc enabled check failed", exc_info=True)
        return False
    raw = str(os.environ.get(_ENV_GC, "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _cube_root() -> str:
    from core.research.day_cube import _root

    return str(_root())


def _schema_name() -> str:
    from core.research.feature_registry import FEATURE_SCHEMA

    return f"v{FEATURE_SCHEMA}"


def _is_schema_dir(name: str) -> bool:
    return len(name) > 1 and name.startswith("v") and name[1:].isdigit()


def _normalize_dates(dates: Sequence[str] | Set[str] | None) -> Set[str]:
    out: Set[str] = set()
    for raw in dates or ():
        day = str(raw or "").strip()
        head = day[:10]
        if len(day) > 10 and day[10] not in "T ":
            continue
        if _DAY.fullmatch(head):
            out.add(head)
    return out


def _contained(root: str, path: str) -> bool:
    """``path`` 解析后必须落在 ``root`` 里面。符号链接不算在内。"""
    if os.path.islink(path):
        return False
    root_real = os.path.realpath(root)
    path_real = os.path.realpath(path)
    return path_real == root_real or path_real.startswith(root_real + os.sep)


def _child_dir(parent: str, name: str) -> Optional[str]:
    if (
        not name
        or name.startswith(".")
        or name in {".", ".."}
        or os.sep in name
        or (os.altsep and os.altsep in name)
    ):
        return None
    path = os.path.join(parent, name)
    if os.path.islink(path) or not os.path.isdir(path):
        return None
    if not _contained(parent, path):
        logger.warning("day cube gc skip linked dir %s", path)
        return None
    return path


def _clock_dirs(schema_root: str) -> list:
    found = []
    if not os.path.isdir(schema_root) or os.path.islink(schema_root):
        return found
    try:
        hashes = os.listdir(schema_root)
    except OSError:
        return found
    for uh in hashes:
        uh_path = _child_dir(schema_root, uh)
        if uh_path is None:
            continue
        try:
            clocks = os.listdir(uh_path)
        except OSError:
            continue
        for clock in clocks:
            clock_path = _child_dir(uh_path, clock)
            if clock_path is not None:
                found.append(clock_path)
    return found


def _cube_file(clock_dir: str, day: str, suffix: str) -> Optional[str]:
    if not _DAY.fullmatch(day):
        return None
    path = os.path.join(clock_dir, day + suffix)
    if os.path.islink(path):
        return path
    if not _contained(clock_dir, path):
        logger.warning("day cube gc skip path %s", path)
        return None
    return path


def _date_from_filename(name: str) -> Optional[str]:
    if name.endswith(".meta.json"):
        day = name[: -len(".meta.json")]
    elif name.endswith(".npz"):
        day = name[:-4]
    else:
        return None
    if len(day) >= 10:
        return day[:10]
    return None


def _unlink_file(path: str) -> bool:
    if os.path.islink(path):
        try:
            os.unlink(path)
            return True
        except OSError:
            logger.warning("day cube gc unlink link failed %s", path, exc_info=True)
            return False
    if not os.path.isfile(path):
        return False
    try:
        os.unlink(path)
    except OSError:
        logger.warning("day cube gc unlink failed %s", path, exc_info=True)
        return False
    return True


def _remove_old_tmp(clock_dir: str) -> None:
    """清掉崩溃留下的临时文件。一小时内的临时文件可能还在写，留下。"""
    try:
        names = os.listdir(clock_dir)
    except OSError:
        return
    cutoff = time.time() - 3600
    for name in names:
        if not (name.startswith(".cube_") and name.endswith(".tmp")):
            continue
        path = os.path.join(clock_dir, name)
        if not os.path.islink(path) and not _contained(clock_dir, path):
            continue
        try:
            if os.path.getmtime(path) >= cutoff:
                continue
        except OSError:
            continue
        _unlink_file(path)


def _remove_empty_dirs(schema_root: str) -> None:
    """日期删光后去掉空的钟点目录和宇宙目录。schema 根留下。"""
    if not os.path.isdir(schema_root) or os.path.islink(schema_root):
        return
    try:
        hashes = list(os.listdir(schema_root))
    except OSError:
        return
    for uh in hashes:
        uh_path = _child_dir(schema_root, uh)
        if uh_path is None:
            continue
        try:
            clocks = list(os.listdir(uh_path))
        except OSError:
            continue
        for clock in clocks:
            clock_path = _child_dir(uh_path, clock)
            if clock_path is None:
                continue
            _remove_old_tmp(clock_path)
            try:
                if not os.listdir(clock_path):
                    os.rmdir(clock_path)
            except OSError:
                logger.debug("day cube gc rmdir clock failed %s", clock_path, exc_info=True)
        try:
            if os.path.isdir(uh_path) and not os.listdir(uh_path):
                os.rmdir(uh_path)
        except OSError:
            logger.debug("day cube gc rmdir hash failed %s", uh_path, exc_info=True)


def _clear_memory() -> None:
    try:
        from core.research.day_cube import clear_memory

        clear_memory()
    except Exception:  # noqa: BLE001
        logger.warning("day cube gc clear memory failed", exc_info=True)


def _finish(
    files_removed: int,
    dates_dropped: int,
    *,
    clear: bool,
    schema_root: str,
    forget: Optional[Set[str]] = None,
) -> Dict[str, int]:
    if files_removed:
        _remove_empty_dirs(schema_root)
    if clear:
        _clear_memory()
    elif forget:
        try:
            from core.research.day_cube import forget_dates

            forget_dates(forget)
        except Exception:  # noqa: BLE001
            logger.warning("day cube gc forget dates failed", exc_info=True)
            _clear_memory()
    if files_removed or dates_dropped:
        logger.info(
            "day cube gc drop dates=%s files=%s",
            dates_dropped,
            files_removed,
        )
    return {"dates_dropped": int(dates_dropped), "files_removed": int(files_removed)}


def _remove_tree(path: str, root: str) -> bool:
    """删目录或符号链接本身。链接不跟着走，解析后跑出 ``root`` 的路径留下。"""
    if os.path.islink(path):
        try:
            os.unlink(path)
            return True
        except OSError:
            logger.warning("day cube gc unlink link failed %s", path, exc_info=True)
            return False
    if not os.path.isdir(path):
        return False
    if not _contained(root, path):
        logger.warning("day cube gc skip outside path %s", path)
        return False
    try:
        shutil.rmtree(path)
    except OSError:
        logger.warning("day cube gc rmtree failed %s", path, exc_info=True)
        return False
    return not os.path.exists(path)


def drop_dates(dates: Sequence[str] | Set[str] | None) -> Dict[str, int]:
    """删当前 schema 下这些日期的立方。同一天在每个宇宙、每个钟各删一次。"""
    if not gc_enabled():
        return {"dates_dropped": 0, "files_removed": 0}
    wanted = _normalize_dates(dates)
    if not wanted:
        return {"dates_dropped": 0, "files_removed": 0}
    schema_root = os.path.join(_cube_root(), _schema_name())
    removed_days: Set[str] = set()
    files = 0
    for clock_dir in _clock_dirs(schema_root):
        for day in wanted:
            for suffix in (".npz", ".meta.json"):
                path = _cube_file(clock_dir, day, suffix)
                if path and _unlink_file(path):
                    files += 1
                    removed_days.add(day)
    return _finish(
        files,
        len(removed_days),
        clear=False,
        schema_root=schema_root,
        forget=wanted,
    )


def drop_all_dates() -> Dict[str, int]:
    """当前 schema 下全部日期作废。L1 日期读不出来时用。"""
    if not gc_enabled():
        return {"dates_dropped": 0, "files_removed": 0}
    schema_root = os.path.join(_cube_root(), _schema_name())
    removed_days: Set[str] = set()
    files = 0
    for clock_dir in _clock_dirs(schema_root):
        try:
            names = os.listdir(clock_dir)
        except OSError:
            logger.warning("day cube gc listdir failed %s", clock_dir, exc_info=True)
            continue
        for name in names:
            day = _date_from_filename(name)
            if day is None or not _DAY.fullmatch(day):
                continue
            path = _cube_file(clock_dir, day, ".npz" if name.endswith(".npz") else ".meta.json")
            if path is None or os.path.basename(path) != name:
                continue
            if _unlink_file(path):
                files += 1
                removed_days.add(day)
    return _finish(files, len(removed_days), clear=True, schema_root=schema_root)


def resolve_keep_hashes(
    extra_codes: Optional[Sequence[str]] = None,
) -> Optional[Set[str]]:
    """观察池读取键与拟合键。任一来源抛错，或两边都不足 2 只，则 None。

    只拿到其中一边时不删冷目录，避免把另一边仍在读的宇宙清掉。
    """
    from core.research.feature_registry import universe_hash

    failed = False
    keep: Set[str] = set()
    try:
        from core.t0.score_policy import _watching_codes_for_z

        codes = [str(c).strip() for c in (_watching_codes_for_z() or []) if str(c or "").strip()]
        if len(codes) >= 2:
            keep.add(universe_hash(codes))
    except Exception:  # noqa: BLE001
        failed = True
        logger.warning("day cube keep hash watching failed", exc_info=True)
    try:
        from core.research_universe import resolve_model_fit_codes

        raw = (resolve_model_fit_codes() or {}).get("codes") or []
        codes = [str(c).strip() for c in raw if str(c or "").strip()]
        if len(codes) >= 2:
            keep.add(universe_hash(codes))
    except Exception:  # noqa: BLE001
        failed = True
        logger.warning("day cube keep hash fit failed", exc_info=True)
    if failed:
        return None
    extra: list = []
    seen: Set[str] = set()
    for raw in extra_codes or []:
        code = str(raw or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        extra.append(code)
    if len(extra) >= 2:
        keep.add(universe_hash(extra))
    if not keep:
        return None
    return keep


def prune_cold(
    extra_codes: Optional[Sequence[str]] = None,
    keep_hashes: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """删旧 schema 目录，以及当前 schema 里不在保留集的宇宙目录。

    ``keep_hashes`` 给定时不再读观察池。空集视为保留集未解析，一个目录都不删。
    """
    empty = {"skipped": True, "hashes_pruned": 0, "schemas_pruned": 0}
    if not gc_enabled():
        return {"enabled": False, **empty}
    if keep_hashes is None:
        keep = resolve_keep_hashes(extra_codes)
    else:
        keep = {str(h).strip() for h in keep_hashes if str(h or "").strip()} or None
    if not keep:
        logger.info("day cube gc prune skipped: keep unresolved")
        return {**empty, "reason": "keep unresolved"}
    root = _cube_root()
    schemas = 0
    hashes = 0
    current = _schema_name()
    if os.path.isdir(root) and not os.path.islink(root):
        try:
            top = list(os.listdir(root))
        except OSError:
            logger.warning("day cube gc listdir failed %s", root, exc_info=True)
            top = []
        for name in top:
            if name.startswith(".") or not _is_schema_dir(name) or name == current:
                continue
            path = os.path.join(root, name)
            if _remove_tree(path, root):
                schemas += 1
        schema_root = os.path.join(root, current)
        if os.path.isdir(schema_root) and not os.path.islink(schema_root):
            try:
                names = list(os.listdir(schema_root))
            except OSError:
                logger.warning("day cube gc listdir failed %s", schema_root, exc_info=True)
                names = []
            for name in names:
                if name.startswith(".") or name in keep:
                    continue
                path = os.path.join(schema_root, name)
                if _remove_tree(path, schema_root):
                    hashes += 1
    if schemas or hashes:
        _clear_memory()
        logger.info(
            "day cube gc prune hashes=%s schemas=%s keep=%s",
            hashes,
            schemas,
            len(keep),
        )
    return {
        "skipped": False,
        "hashes_pruned": hashes,
        "schemas_pruned": schemas,
        "keep": sorted(keep),
    }


def note_stale_dates(dates: Sequence[str] | Set[str] | None) -> Dict[str, Any]:
    """批次内只累积；批次外立即删。"""
    if not gc_enabled():
        return {"enabled": False, "dates_dropped": 0, "files_removed": 0}
    cleaned = _normalize_dates(dates)
    if not cleaned:
        return {"dates_dropped": 0, "files_removed": 0}
    with _LOCK:
        bucket = _BATCH.get()
        if bucket is not None:
            bucket["dates"].update(cleaned)
            return {"batched": True, "dates": len(cleaned)}
    return drop_dates(cleaned)


def note_all_dates() -> Dict[str, Any]:
    """L1 有 meta 但日期读失败：当前 schema 的立方全部作废。"""
    if not gc_enabled():
        return {"enabled": False, "dates_dropped": 0, "files_removed": 0}
    with _LOCK:
        bucket = _BATCH.get()
        if bucket is not None:
            bucket["all_dates"] = True
            return {"batched": True, "all_dates": True}
    return drop_all_dates()


def _flush_bucket(bucket: Dict[str, Any]) -> Dict[str, int]:
    with _LOCK:
        all_dates = bool(bucket.get("all_dates"))
        dates = set(bucket.get("dates") or ())
        bucket["dates"] = set()
        bucket["all_dates"] = False
    if not gc_enabled():
        return {"dates_dropped": 0, "files_removed": 0}
    if all_dates:
        return drop_all_dates()
    return drop_dates(dates)


@contextmanager
def l2_invalidation_batch() -> Iterator[Dict[str, Any]]:
    """物化整段 L1 时合并日期，退出时删一次盘。嵌套调用交给外层 flush。"""
    outer = _BATCH.get()
    if outer is not None:
        yield outer
        return
    bucket: Dict[str, Any] = {
        "dates": set(),
        "all_dates": False,
        "dates_dropped": 0,
        "files_removed": 0,
    }
    token = _BATCH.set(bucket)
    try:
        yield bucket
    finally:
        try:
            stats = _flush_bucket(bucket)
            extra = _flush_bucket(bucket)
            stats["dates_dropped"] = int(stats.get("dates_dropped") or 0) + int(
                extra.get("dates_dropped") or 0
            )
            stats["files_removed"] = int(stats.get("files_removed") or 0) + int(
                extra.get("files_removed") or 0
            )
        except Exception:  # noqa: BLE001
            logger.warning("l2 invalidation flush failed", exc_info=True)
            stats = {"dates_dropped": 0, "files_removed": 0}
        finally:
            _BATCH.reset(token)
        bucket.update(stats)
