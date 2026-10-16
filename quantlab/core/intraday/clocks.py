"""Fill-clock allowlists and clamping (shared by replay and live).

A-share 5-minute bar alignment: first bar often stamped 09:35 = 09:30–09:35.
"""

from __future__ import annotations

from typing import Any, Tuple

# 调仓成交钟：与 A 股 5 分钟 K 对齐（首根通常标 09:35 = 09:30–09:35）。
REPLAY_FILL_CLOCKS = (
    "09:30",
    "09:35",
    "09:40",
    "09:45",
    "09:50",
    "09:55",
    "10:00",
)
REPLAY_FILL_CLOCK = "09:30"
# Live「启动时间」：可含集合竞价定开 09:25；回测钟仍从 09:30 起。
LIVE_FILL_CLOCKS = ("09:25",) + REPLAY_FILL_CLOCKS
LIVE_FILL_CLOCK = "09:30"


def clamp_fill_clock(
    raw: Any,
    *,
    allowed: Tuple[str, ...],
    default: str,
) -> str:
    """Parse ``HH:MM`` (or digits) into an allowed clock; else ``default``."""
    fallback = str(default or "").strip()[:5] or (allowed[0] if allowed else "09:30")
    if fallback not in allowed:
        fallback = allowed[0] if allowed else "09:30"
    s = str(raw or "").strip().replace("：", ":")
    if not s:
        return fallback
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 4:
        hh, mm = digits[:2], digits[2:4]
        cand = f"{hh}:{mm}"
        if cand in allowed:
            return cand
    head = s[:5]
    if len(head) >= 4 and head[1] == ":":
        head = f"0{head}"
    head = head[:5]
    if head in allowed:
        return head
    return fallback


def clamp_replay_fill_clock(raw: Any, default: str = REPLAY_FILL_CLOCK) -> str:
    """历史回测成交钟：09:30–10:00 每 5 分钟一档；无法解析则回默认。"""
    return clamp_fill_clock(raw, allowed=REPLAY_FILL_CLOCKS, default=default)


def clamp_live_fill_clock(raw: Any, default: str = LIVE_FILL_CLOCK) -> str:
    """Live 启动时间：09:25–10:00（含集合竞价定开）；无法解析则回默认。"""
    return clamp_fill_clock(raw, allowed=LIVE_FILL_CLOCKS, default=default)
