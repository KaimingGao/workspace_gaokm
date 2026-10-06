"""PIT / as_of：回测与研究只暴露决策日及以前可见的 bars。

约定（与 data-layer 一致）：
- 日线：as_of 切到当日（含）收盘及以前；禁止窗口含未来 bar。
- 基本面/资讯：本模块不伪造历史；调用方须标明 non_pit。
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple


def _bar_date(bar: dict) -> str:
    return str((bar or {}).get("date") or "").strip()


def bars_as_of(
    bars: Sequence[dict],
    as_of: str,
    *,
    inclusive: bool = True,
) -> List[dict]:
    """返回 date <= as_of（inclusive）的 bars 副本（保序）。"""
    cutoff = str(as_of or "").strip()
    if not cutoff:
        return list(bars or [])
    out: List[dict] = []
    for b in bars or []:
        d = _bar_date(b)
        if not d:
            continue
        if inclusive:
            if d <= cutoff:
                out.append(b)
        else:
            if d < cutoff:
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
    # 校验：窗口内不得有 > as_of 的日期
    lookahead = sum(1 for b in window if _bar_date(b) > as_of)
    meta = {
        "ok": lookahead == 0,
        "as_of": as_of,
        "as_of_index": int(as_of_index),
        "window_len": len(window),
        "lookahead_bars": lookahead,
        "pit": True,
        "note": "日线 as_of 切条；决策日只用当日及以前 bars。",
    }
    return window, meta


def assert_window_pit(window: Sequence[dict], as_of: str) -> Dict[str, Any]:
    """检查窗口是否含未来 bar。"""
    cutoff = str(as_of or "").strip()
    future = [ _bar_date(b) for b in window if _bar_date(b) > cutoff ]
    return {
        "ok": len(future) == 0,
        "as_of": cutoff,
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
        "bars_pit": True,
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
