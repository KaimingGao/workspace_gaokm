"""PIT / as_of：回测与研究只暴露决策日及以前可见的 bars。

约定（与 data-layer 一致）：
- 日线：as_of 切到当日（含）收盘及以前；禁止窗口含未来 bar。
- 基本面/资讯：本模块不伪造历史；调用方须标明 non_pit。
"""

from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _bar_date(bar: dict) -> str:
    return str((bar or {}).get("date") or "").strip()


def _iso_ord(value: str) -> Optional[Tuple[int, int, int]]:
    """YYYY-MM-DD、YYYY/MM/DD 或 YYYYMMDD → (y, m, d)。不是日历日则 None。"""
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] in "-/" and text[7] == text[4]:
        year_s, month_s, day_s = text[:4], text[5:7], text[8:10]
    elif len(text) >= 8 and text[:8].isdigit():
        year_s, month_s, day_s = text[:4], text[4:6], text[6:8]
    else:
        return None
    try:
        year, month, day = int(year_s), int(month_s), int(day_s)
        date(year, month, day)
    except ValueError:
        return None
    if year < 1900:
        return None
    return year, month, day


def count_lookahead_bars(window: Sequence[dict], as_of: str) -> int:
    """窗口内日期晚于决策日的 bar 数。

    决策日为空、或任一侧不是日历日时不计。``d0`` 这类占位符不会因为字母序
    大于 ``2024-01-01`` 而被当成前瞻。
    """
    cutoff = _iso_ord(as_of)
    if cutoff is None:
        return 0
    n = 0
    for bar in window or []:
        if not isinstance(bar, dict):
            continue
        bar_ord = _iso_ord(_bar_date(bar))
        if bar_ord is not None and bar_ord > cutoff:
            n += 1
    return n


def bars_as_of(
    bars: Sequence[dict],
    as_of: str,
    *,
    inclusive: bool = True,
) -> List[dict]:
    """返回日期不晚于 as_of 的 bars 副本（保序）。

    两侧都按日历日比较。``YYYYMMDD`` 与 ``YYYY-MM-DD`` 混用时不再靠字母序，
    避免 ``2024-12-31 <= 20240115`` 把未来日留在窗口里。解析不了的决策日不切条。
    """
    cutoff = _iso_ord(as_of)
    if cutoff is None:
        return list(bars or [])
    out: List[dict] = []
    for b in bars or []:
        if not isinstance(b, dict):
            continue
        bar_ord = _iso_ord(_bar_date(b))
        if bar_ord is None:
            continue
        if bar_ord <= cutoff if inclusive else bar_ord < cutoff:
            out.append(b)
    return out


def window_as_of(
    bars: Sequence[dict],
    as_of_index: int,
    *,
    max_window: int = 30,
) -> Tuple[List[dict], Dict[str, Any]]:
    """
    以 bars[as_of_index] 为决策日，只取该日及以前、最多 max_window 根。
    返回 (window, pit_meta)。
    """
    bars = list(bars or [])
    n = len(bars)
    if n == 0 or as_of_index < 0 or as_of_index >= n:
        return [], {
            "ok": False,
            "as_of": None,
            "lookahead_bars": 0,
            "note": "index 越界",
        }
    start = max(0, int(as_of_index) - max(1, int(max_window or 30)) + 1)
    window = bars[start : int(as_of_index) + 1]
    as_of = _bar_date(bars[as_of_index])
    if not as_of:
        return window, {
            "ok": False,
            "as_of": "",
            "as_of_index": int(as_of_index),
            "window_len": len(window),
            "lookahead_bars": 0,
            "pit": False,
            "note": "决策日缺少 as_of，未把窗口内日期计为前瞻",
        }
    lookahead = count_lookahead_bars(window, as_of)
    meta = {
        "ok": lookahead == 0,
        "as_of": as_of,
        "as_of_index": int(as_of_index),
        "window_len": len(window),
        "lookahead_bars": lookahead,
        "pit": lookahead == 0,
        "note": "日线 as_of 切条；决策日只用当日及以前 bars。",
    }
    return window, meta


def assert_window_pit(window: Sequence[dict], as_of: str) -> Dict[str, Any]:
    """检查窗口是否含未来 bar。只认日历日，与 ``count_lookahead_bars`` 同一把尺子。"""
    cutoff = _iso_ord(as_of)
    future: List[str] = []
    if cutoff is not None:
        for bar in window or []:
            if not isinstance(bar, dict):
                continue
            text = _bar_date(bar)
            bar_ord = _iso_ord(text)
            if bar_ord is not None and bar_ord > cutoff:
                future.append(text)
    return {
        "ok": len(future) == 0,
        "as_of": str(as_of or "").strip(),
        "lookahead_bars": len(future),
        "lookahead_dates": future[:5],
    }


def pit_report_for_backtest(
    *,
    as_of: Optional[str] = None,
    windows_checked: int = 0,
    lookahead_violations: int = 0,
    fundamentals_pit: bool = False,
    fundamentals_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    fm = fundamentals_meta or {}
    return {
        "bars_pit": int(lookahead_violations or 0) == 0,
        "as_of_sample": as_of,
        "windows_checked": windows_checked,
        "lookahead_violations": lookahead_violations,
        "fundamentals_pit": bool(fundamentals_pit or fm.get("fundamentals_pit")),
        "fundamentals": {
            "pit_partial": fm.get("fundamentals_pit_partial"),
            "resolved_ok": fm.get("resolved_ok"),
            "missing_as_of": fm.get("missing_as_of"),
            "snapshot_used": fm.get("snapshot_used"),
            "lookahead_blocked": fm.get("lookahead_blocked"),
            "sample_count": fm.get("sample_count"),
        }
        if fm
        else None,
        "note": (
            "bars 已 as_of 切条。"
            + (
                fm.get("note")
                if fm
                else (
                    "基本面非 PIT（快照）。"
                    if not fundamentals_pit
                    else "基本面按 PIT。"
                )
            )
        ),
    }
