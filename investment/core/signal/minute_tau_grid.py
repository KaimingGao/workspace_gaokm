"""训练用 τ 时钟网格（无 research 依赖，供 signal config / τ 面板共用）。"""

from __future__ import annotations

from typing import List, Optional, Sequence


def _hm_to_clock_minutes(hm: str) -> Optional[int]:
    s = str(hm or "").strip().replace(":", "")
    if len(s) != 4 or not s.isdigit():
        return None
    return int(s[:2]) * 60 + int(s[2:])


def _clock_minutes_to_hm(total: int) -> str:
    h, m = divmod(int(total), 60)
    return f"{h:02d}:{m:02d}"


# A 股连续竞价：09:30–11:30 + 13:00–15:00 = 240 交易分钟
_SESSION_AM_START = 9 * 60 + 30
_SESSION_AM_END = 11 * 60 + 30
_SESSION_PM_START = 13 * 60
_SESSION_PM_END = 15 * 60
SESSION_TRADING_MINUTES = 240
HORIZON_T30_MIN = 30
HORIZON_T45_MIN = 45
HORIZON_T60_MIN = 60
HORIZON_T75_MIN = 75
HORIZON_T90_MIN = 90
# ŷ_τN 标签终点：中心钟 ±5m 三根 5m 收均价；右沿需 N+5 交易分钟
T30_LABEL_OFFSETS = (25, 30, 35)
T30_LABEL_NEED_MIN = 35
T45_LABEL_OFFSETS = (40, 45, 50)
T45_LABEL_NEED_MIN = 50
T60_LABEL_OFFSETS = (55, 60, 65)
T60_LABEL_NEED_MIN = 65
T75_LABEL_OFFSETS = (70, 75, 80)
T75_LABEL_NEED_MIN = 80
T90_LABEL_OFFSETS = (85, 90, 95)
T90_LABEL_NEED_MIN = 95


def session_elapsed(hm: str) -> Optional[float]:
    """壁钟 → 当日已过交易分钟：09:30=0，11:30=120，13:00=120，15:00=240。

    午休（11:30–13:00 开区间）视为 120。开盘前 / 收盘后返回 None。
    """
    clock = _hm_to_clock_minutes(hm)
    if clock is None:
        return None
    if _SESSION_AM_START <= clock <= _SESSION_AM_END:
        return float(clock - _SESSION_AM_START)
    if _SESSION_PM_START <= clock <= _SESSION_PM_END:
        return float(120 + (clock - _SESSION_PM_START))
    if _SESSION_AM_END < clock < _SESSION_PM_START:
        return 120.0
    return None


def session_clock(elapsed: float) -> Optional[str]:
    """交易分钟 → 壁钟。elapsed=120 → 11:30；>240 或 <0 → None。"""
    try:
        e = int(round(float(elapsed)))
    except (TypeError, ValueError):
        return None
    if e < 0 or e > SESSION_TRADING_MINUTES:
        return None
    if e <= 120:
        return _clock_minutes_to_hm(_SESSION_AM_START + e)
    return _clock_minutes_to_hm(_SESSION_PM_START + (e - 120))


def add_session_minutes(hm: str, add: int = HORIZON_T30_MIN) -> Optional[str]:
    """交易时钟加法（跳过午休）。不足剩余则 None：14:30+30=15:00，14:35+30=None。"""
    elapsed = session_elapsed(hm)
    if elapsed is None:
        return None
    try:
        delta = float(add)
    except (TypeError, ValueError):
        return None
    target = elapsed + delta
    if target > float(SESSION_TRADING_MINUTES):
        return None
    return session_clock(target)


def sub_session_minutes(hm: str, sub: int = HORIZON_T30_MIN) -> Optional[str]:
    """交易时钟减法（跳过午休）。不足已过则 None：10:00−30=09:30，09:50−30=None。"""
    elapsed = session_elapsed(hm)
    if elapsed is None:
        return None
    try:
        delta = float(sub)
    except (TypeError, ValueError):
        return None
    target = elapsed - delta
    if target < 0:
        return None
    return session_clock(target)


def session_remain(hm: str) -> Optional[float]:
    """距 15:00 剩余交易分钟：09:30=240，14:30=30，15:00=0。"""
    elapsed = session_elapsed(hm)
    if elapsed is None:
        return None
    return float(SESSION_TRADING_MINUTES) - float(elapsed)


def horizon_crosses_lunch(
    tau_hm: str,
    *,
    add_min: int = HORIZON_T30_MIN,
) -> Optional[float]:
    """τ⊕N 是否跨午休：11:15→13:15、11:30→13:30 为 1；11:00→11:30 为 0。"""
    start = session_elapsed(tau_hm)
    end_hm = add_session_minutes(tau_hm, add_min)
    if start is None or not end_hm:
        return None
    end = session_elapsed(end_hm)
    if end is None:
        return None
    return 1.0 if start <= 120.0 and end > 120.0 else 0.0


def tau_clock_allows_t30(hm: str, *, horizon_min: int = T30_LABEL_NEED_MIN) -> bool:
    """live / 训练：须能取到 τ⊕35（三根均价右沿）。14:25 可、14:30 不可。"""
    return add_session_minutes(hm, horizon_min) is not None


def tau_clock_allows_t45(hm: str, *, horizon_min: int = T45_LABEL_NEED_MIN) -> bool:
    """live / 训练：须能取到 τ⊕50。14:10 可、14:15 不可。"""
    return add_session_minutes(hm, horizon_min) is not None


def tau_clock_allows_t60(hm: str, *, horizon_min: int = T60_LABEL_NEED_MIN) -> bool:
    """live / 训练：须能取到 τ⊕65。13:55 可、14:00 不可。"""
    return add_session_minutes(hm, horizon_min) is not None


def tau_clock_allows_t75(hm: str, *, horizon_min: int = T75_LABEL_NEED_MIN) -> bool:
    """live / 训练：须能取到 τ⊕80。13:40 可、13:45 不可。"""
    return add_session_minutes(hm, horizon_min) is not None


def tau_clock_allows_t90(hm: str, *, horizon_min: int = T90_LABEL_NEED_MIN) -> bool:
    """live / 训练：须能取到 τ⊕95。13:25 可、13:30 不可。"""
    return add_session_minutes(hm, horizon_min) is not None


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


def _t0_leg1_end_hm() -> str:
    """ŷ_oc 训练窗终点 = 做 T 第一腿最晚钟。"""
    try:
        from core.t0.config import T0_LAST_LEG1_HM

        return str(T0_LAST_LEG1_HM or "11:00").strip()[:5] or "11:00"
    except Exception:  # noqa: BLE001
        return "11:00"


# 自动调仓窗结束（auto_worker.WINDOW_UNTIL）；live ŷ_oc 前缀不超过此时
REBALANCE_TAU_CAP_HM = "10:00"
LIVE_PREFIX_CAUSAL_REBALANCE = "causal_rebalance"
TRAIN_TAU_END_HM = _t0_leg1_end_hm()

# 早盘 leg1：09:30 开盘 Z + 每 5m 至做 T 扫描止（共享 β）
DEFAULT_T0_TRAIN_TAU_GRID_5M: tuple = tuple(
    minute_tau_grid_5m_range("09:30", TRAIN_TAU_END_HM, include_open=True)
)
# 研究枢纽默认：与做 T v6 leg1 窗口对齐
DEFAULT_MINUTE_TAU_GRID: tuple = DEFAULT_T0_TRAIN_TAU_GRID_5M
# ŷ_τ30：全日 5m，止于 14:25（其后不足 35 交易分钟，无法取 τ⊕25/30/35 三根）
DEFAULT_T30_TRAIN_TAU_GRID: tuple = tuple(
    minute_tau_grid_5m_range("09:30", "11:30", include_open=True)
    + minute_tau_grid_5m_range("13:05", "14:25", include_open=True)
)
# ŷ_τ45：全日 5m，止于 14:10（其后不足 50 交易分钟，无法取 τ⊕40/45/50 三根）
DEFAULT_T45_TRAIN_TAU_GRID: tuple = tuple(
    minute_tau_grid_5m_range("09:30", "11:30", include_open=True)
    + minute_tau_grid_5m_range("13:05", "14:10", include_open=True)
)
# ŷ_τ60：全日 5m，止于 13:55（其后不足 65 交易分钟，无法取 τ⊕55/60/65 三根）
DEFAULT_T60_TRAIN_TAU_GRID: tuple = tuple(
    minute_tau_grid_5m_range("09:30", "11:30", include_open=True)
    + minute_tau_grid_5m_range("13:05", "13:55", include_open=True)
)
# ŷ_τ75：全日 5m，止于 13:40（其后不足 80 交易分钟，无法取 τ⊕70/75/80 三根）
DEFAULT_T75_TRAIN_TAU_GRID: tuple = tuple(
    minute_tau_grid_5m_range("09:30", "11:30", include_open=True)
    + minute_tau_grid_5m_range("13:05", "13:40", include_open=True)
)
# ŷ_τ90：全日 5m，止于 13:25（其后不足 95 交易分钟，无法取 τ⊕85/90/95 三根）
DEFAULT_T90_TRAIN_TAU_GRID: tuple = tuple(
    minute_tau_grid_5m_range("09:30", "11:30", include_open=True)
    + minute_tau_grid_5m_range("13:05", "13:25", include_open=True)
)
