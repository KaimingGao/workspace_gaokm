"""开盘 L2：每只 L1 只解开一次，切到未压缩临时文件，再按块写立方。

内存里只留当前一块。整窗装进进程会超过上限时直接拒绝。
临时目录在成功、失败、取消时都删掉。
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import tempfile
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# 三族当前一块大约 350MB。超过这个数就不要装进进程。
CHUNK_BYTE_LIMIT = 700 * 1024 * 1024

# 进程被打断时，with 的清理可能来不及。退出时再扫一遍还留着的临时目录。
_LIVE_SPILLS: set = set()


def _cleanup_live_spills() -> None:
    for root in list(_LIVE_SPILLS):
        shutil.rmtree(root, ignore_errors=True)
    _LIVE_SPILLS.clear()


atexit.register(_cleanup_live_spills)

ProgressCb = Optional[Callable[[int, int, str], None]]
CancelCb = Optional[Callable[[], bool]]


def resident_block_bytes(
    n_days: int,
    n_codes: int,
    widths: Sequence[int],
) -> int:
    """一块日期 × 股票 × 列宽的 float64 字节数。"""
    days = max(0, int(n_days))
    codes = max(0, int(n_codes))
    total = 0
    for raw in widths or []:
        try:
            width = int(raw)
        except (TypeError, ValueError):
            width = 0
        if width > 0:
            total += days * codes * width * 8
    return int(total)


def refuse_resident_block(
    n_days: int,
    n_codes: int,
    widths: Sequence[int],
    *,
    limit: int = CHUNK_BYTE_LIMIT,
) -> None:
    """整块超过上限就拒绝。调用方应缩小日期块，而不是把全窗留在内存里。"""
    nbytes = resident_block_bytes(n_days, n_codes, widths)
    cap = max(1, int(limit))
    if nbytes > cap:
        raise MemoryError(
            f"拒绝把 {int(n_days)} 日 × {int(n_codes)} 只装进内存"
            f"（{nbytes} 字节，上限 {cap}）"
        )


def _cancelled(cb: CancelCb) -> bool:
    if not cb:
        return False
    try:
        return bool(cb())
    except Exception:  # noqa: BLE001
        return False


def _safe_code(code: str) -> str:
    text = "".join(ch for ch in str(code or "") if ch.isalnum())
    return text or "unknown"


def _emit(cb: ProgressCb, cur: int, tot: int, msg: str) -> None:
    if not cb:
        return
    try:
        cb(int(cur), max(1, int(tot)), str(msg or ""))
    except Exception:  # noqa: BLE001
        logger.debug("cube spill progress failed", exc_info=True)


class LakeSpill:
    """一个临时目录。``write`` 每只只 ``np.load`` 一次；``read`` 只取出要的日期。"""

    def __init__(self, root: str) -> None:
        self.root = str(root)
        if self.root:
            _LIVE_SPILLS.add(self.root)

    def close(self) -> None:
        path = self.root
        self.root = ""
        if path:
            _LIVE_SPILLS.discard(path)
        if path and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)

    def _dir(self, key: str) -> str:
        folder = os.path.join(self.root, _safe_code(key))
        os.makedirs(folder, exist_ok=True)
        return folder

    def write(
        self,
        key: str,
        family: str,
        codes: Sequence[str],
        days: Sequence[str],
        *,
        progress_cb: ProgressCb = None,
        cancel_cb: CancelCb = None,
        label: str = "",
    ) -> bool:
        """解开这一族。取消时返回 False。单票失败跳过，不中断其余股票。"""
        from core.research.feature_lake import load_stock_panel

        want = {str(d)[:10] for d in (days or []) if len(str(d or "")[:10]) >= 10}
        code_list: List[str] = []
        seen = set()
        for raw in codes or []:
            code = str(raw or "").strip()
            if not code or code in seen:
                continue
            seen.add(code)
            code_list.append(code)
        folder = self._dir(key)
        n_codes = len(code_list)
        prefix = str(label or key)
        for done, code in enumerate(code_list, start=1):
            if _cancelled(cancel_cb):
                return False
            if done == 1 or done == n_codes or done % 32 == 0:
                _emit(
                    progress_cb,
                    done,
                    max(1, n_codes),
                    f"{prefix} · 解开 L1 {key} {done}/{max(1, n_codes)}",
                )
            if not want:
                continue
            try:
                pack = load_stock_panel(family, code, fingerprint=None)
            except Exception:  # noqa: BLE001
                logger.debug("cube spill load failed %s %s", key, code, exc_info=True)
                continue
            if not pack:
                continue
            try:
                names = [str(n) for n in (pack.get("names") or [])]
                X = np.asarray(pack.get("X"))
                if X.ndim != 2 or not names:
                    continue
                kept_idx: List[int] = []
                kept_days: List[str] = []
                seen_days = set()
                n_rows = int(X.shape[0])
                for i, raw_day in enumerate(pack.get("dates") or []):
                    if i >= n_rows:
                        break
                    day = str(raw_day)[:10]
                    if day not in want or day in seen_days:
                        continue
                    seen_days.add(day)
                    kept_idx.append(i)
                    kept_days.append(day)
                if not kept_idx:
                    continue
                block = np.asarray(X[np.asarray(kept_idx, dtype=np.int64)], dtype=np.float64)
                stem = os.path.join(folder, _safe_code(code))
                np.save(stem + ".npy", block)
                with open(stem + ".json", "w", encoding="utf-8") as fh:
                    json.dump(
                        {"names": names, "dates": kept_days},
                        fh,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
            except Exception:  # noqa: BLE001
                logger.warning("cube spill write failed %s %s", key, code, exc_info=True)
            finally:
                pack = None
        return True

    def width(self, key: str) -> int:
        folder = os.path.join(self.root, _safe_code(key))
        if not os.path.isdir(folder):
            return 0
        widest = 0
        for name in os.listdir(folder):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(folder, name), encoding="utf-8") as fh:
                    payload = json.load(fh)
            except (OSError, ValueError, TypeError):
                continue
            names = payload.get("names") if isinstance(payload, dict) else None
            if isinstance(names, list):
                widest = max(widest, len(names))
        return int(widest)

    def read(
        self,
        key: str,
        codes: Sequence[str],
        days: Sequence[str],
    ) -> Dict[str, tuple]:
        """``code -> (names, {date: row})``。只装 ``days`` 里的行。"""
        want = {str(d)[:10] for d in (days or []) if len(str(d or "")[:10]) >= 10}
        folder = os.path.join(self.root, _safe_code(key))
        out: Dict[str, tuple] = {}
        if not want or not os.path.isdir(folder):
            return out
        refuse_resident_block(len(want), len(list(codes or [])), [self.width(key)])
        for raw in codes or []:
            code = str(raw or "").strip()
            if not code:
                continue
            stem = os.path.join(folder, _safe_code(code))
            meta_path = stem + ".json"
            npy_path = stem + ".npy"
            if not os.path.isfile(meta_path) or not os.path.isfile(npy_path):
                continue
            try:
                with open(meta_path, encoding="utf-8") as fh:
                    payload = json.load(fh)
                names = [str(n) for n in (payload.get("names") or [])]
                stored = [str(d)[:10] for d in (payload.get("dates") or [])]
                arr = np.load(npy_path)
            except (OSError, ValueError, TypeError, KeyError):
                logger.debug("cube spill read failed %s %s", key, code, exc_info=True)
                continue
            if arr.ndim != 2 or not names:
                continue
            by_day: Dict[str, np.ndarray] = {}
            n_rows = int(arr.shape[0])
            for i, day in enumerate(stored):
                if i >= n_rows or day not in want:
                    continue
                by_day[day] = np.array(arr[i], dtype=np.float64, copy=True)
            if by_day:
                out[code] = (names, by_day)
        return out


def open_lake_spill():
    """临时目录。离开 ``with`` 时删除，包括异常和取消。"""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        root = tempfile.mkdtemp(prefix="ql-cube-spill-")
        spill = LakeSpill(root)
        try:
            yield spill
        finally:
            spill.close()

    return _ctx()


def warmup_clock_from_spill(
    codes: Sequence[str],
    days: Sequence[str],
    *,
    clock: str,
    family_oo: str,
    family_tc: str,
    family_co: str,
    min_items: int,
    require_oo_item: bool,
    require_tc_day: bool,
    progress_cb: ProgressCb = None,
    cancel_cb: CancelCb = None,
    day_pos: Optional[Dict[str, int]] = None,
    n_days: int = 0,
) -> Dict[str, int]:
    """先扫哪些日子缺立方，三族各解开一次，再按 80 天一块向量化落盘。

    不走逐票 item。返回 ``cubes_hit`` / ``cubes_built``。
    """
    from core.research.day_cube import day_cube_status
    from core.research.materialize.cube_batch import (
        CUBE_FILL_CHUNK_DAYS,
        build_day_cubes_from_lake,
        day_chunks,
    )
    from core.research.materialize.consumer import consumer_read_only
    from core.research.materialize.flags import require_day_cube

    code_list = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    day_list = [str(d)[:10] for d in (days or []) if len(str(d or "")[:10]) >= 10]
    stats = {
        "cubes_hit": 0,
        "cubes_built": 0,
        "days_skipped": 0,
        "slim_reject": 0,
    }
    if len(code_list) < 2 or not day_list:
        return stats
    index = day_pos if isinstance(day_pos, dict) else {}
    total = max(1, int(n_days or len(day_list)))
    label = f"预热 DayCube {clock}"
    pending: List[str] = []
    load_failed = 0
    for day in day_list:
        if _cancelled(cancel_cb):
            return stats
        try:
            status = day_cube_status(day, code_list, clock=clock)
        except Exception:  # noqa: BLE001
            load_failed += 1
            if load_failed == 1:
                logger.warning(
                    "warmup spill status failed day=%s clock=%s",
                    day,
                    clock,
                    exc_info=True,
                )
            continue
        if status == "ok":
            stats["cubes_hit"] += 1
            continue
        try:
            blocked = require_day_cube() or consumer_read_only()
        except Exception:  # noqa: BLE001
            blocked = False
        if blocked:
            continue
        pending.append(day)
    if not pending or _cancelled(cancel_cb):
        return stats
    families = (
        ("oo", family_oo),
        ("tc", family_tc),
        ("co", family_co),
    )
    try:
        with open_lake_spill() as spill:
            anchor = index.get(pending[0], 1)
            for key, fam in families:
                if _cancelled(cancel_cb):
                    return stats
                wrote = spill.write(
                    key,
                    fam,
                    code_list,
                    pending,
                    progress_cb=progress_cb,
                    cancel_cb=cancel_cb,
                    label=f"{label} {anchor}/{total}",
                )
                if not wrote:
                    return stats
            for chunk in day_chunks(pending, CUBE_FILL_CHUNK_DAYS):
                if _cancelled(cancel_cb):
                    return stats
                widths = [spill.width(key) for key, _fam in families]
                refuse_resident_block(len(chunk), len(code_list), widths)
                oo_s = spill.read("oo", code_list, chunk)
                tc_s = spill.read("tc", code_list, chunk)
                co_s = spill.read("co", code_list, chunk)
                try:
                    filled = build_day_cubes_from_lake(
                        code_list,
                        chunk,
                        family_oo=family_oo,
                        family_tc=family_tc,
                        family_co=family_co,
                        clock=clock,
                        universe_codes=code_list,
                        min_items=min_items,
                        require_oo_item=bool(require_oo_item),
                        require_tc_day=bool(require_tc_day),
                        on_items=None,
                        progress_cb=progress_cb,
                        cancel_cb=cancel_cb,
                        progress_total=total,
                        progress_index=index,
                        label=label,
                        swallow_ensure_errors=True,
                        replace_existing=True,
                        shared_oo=oo_s,
                        shared_tc=tc_s,
                        shared_co=co_s,
                    )
                finally:
                    oo_s = None
                    tc_s = None
                    co_s = None
                if not isinstance(filled, dict):
                    filled = {}
                stats["cubes_built"] += int(filled.get("cubes_built") or 0)
                stats["days_skipped"] += int(filled.get("days_skipped") or 0)
                stats["slim_reject"] += int(filled.get("slim_reject") or 0)
                last = index.get(chunk[-1], total)
                _emit(progress_cb, last, total, f"{label} {last}/{total}")
    except MemoryError:
        logger.warning(
            "warmup spill refused resident block clock=%s days=%s",
            clock,
            len(pending),
        )
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "warmup spill failed clock=%s days=%s",
            clock,
            len(pending),
            exc_info=True,
        )
        stats["error"] = str(exc).strip() or type(exc).__name__
    return stats
