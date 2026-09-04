"""训练用 τ 时钟网格（无 research 依赖，供 signal config / τ 面板共用）。"""

from __future__ import annotations

from typing import List, Optional, Sequence

# 午后钟仅兼容旧配置/手工传入；研究枢纽 y_τ / y_path 默认拟合不含 13:00 / 14:00
DEFAULT_AFTERNOON_TRAIN_TAU_GRID = ("13:00", "14:00")


def _hm_to_clock_minutes(hm: str) -> Optional[int]:
    s = str(hm or "").strip().replace(":", "")
    if len(s) != 4 or not s.isdigit():
        return None
    return int(s[:2]) * 60 + int(s[2:])


def _clock_minutes_to_hm(total: int) -> str:
    h, m = divmod(int(total), 60)
    return f"{h:02d}:{m:02d}"


def minute_tau_grid_5m_range(
    start_hm: str = "09:30",
    end_hm: str = "11:00",
    *,
    include_open: bool = True,
    step_min: int = 5,
) -> List[str]:
    """生成训练用 τ 网格：开盘钟 + 每 step_min 一根 5m 末钟（含 end_hm）。

    默认 09:30…11:00 与做 T v6 收盘带宽 leg1 扫描窗口对齐（09:30 仅开盘 Z）。
    """
    start = _hm_to_clock_minutes(start_hm)
    end = _hm_to_clock_minutes(end_hm)
    step = max(1, int(step_min or 5))
    if start is None or end is None or end < start:
        return []
    out: List[str] = []
    if include_open:
        out.append(_clock_minutes_to_hm(start))
    t = start + step
    while t <= end:
        out.append(_clock_minutes_to_hm(t))
        t += step
    return out


def format_shared_tau_formula(base: str, grid: Optional[Sequence[str]] = None) -> str:
    """训练标签文案：多 τ 写成「5m τ 首–末」，不枚举全表。"""
    clocks = [str(x).strip()[:5] for x in (grid or []) if str(x).strip()]
    base_s = str(base or "").strip()
    if len(clocks) >= 2:
        return f"{base_s} · 5m τ {clocks[0]}–{clocks[-1]}"
    if len(clocks) == 1:
        return f"{base_s} · τ={clocks[0]}"
    return base_s


# 早盘 leg1：09:30 开盘 Z + 09:35…11:00 每 5m（共享 β；与做 T 逐根 rescore 对齐）
DEFAULT_T0_TRAIN_TAU_GRID_5M: tuple = tuple(
    minute_tau_grid_5m_range("09:30", "11:00", include_open=True)
)
# 研究枢纽默认：仅 09:30…11:00（与做 T v6 leg1 窗口对齐）
DEFAULT_MINUTE_TAU_GRID: tuple = DEFAULT_T0_TRAIN_TAU_GRID_5M
