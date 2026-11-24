"""盘前会话时钟：A 股阶段 + 美股 RTH 粗状态（Prior Desk / 非 PIT）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from core.signal.session_pit import shanghai_now

_CN_TZ = ZoneInfo("Asia/Shanghai")
_US_TZ = ZoneInfo("America/New_York")

# A 股连续竞价粗边界（上海）
_A_AUCTION = (9, 25)
_A_OPEN = (9, 30)
_A_LUNCH = (11, 30)
_A_AFTERNOON = (13, 0)
_A_CLOSE = (15, 0)
_A_EOD = (15, 5)

# 美股 RTH 粗边界（美东；忽略半日市）
_US_OPEN = (9, 30)
_US_CLOSE = (16, 0)


def _hm(dt: datetime) -> tuple:
    return (dt.hour, dt.minute)


def _a_share_phase(now: datetime) -> Dict[str, str]:
    """返回 code / label（短）/ hint。"""
    day = now.strftime("%Y-%m-%d")
    trading = now.weekday() < 5
    try:
        from core.market.calendar import is_trading_day

        trading = bool(is_trading_day(day))
    except Exception:  # noqa: BLE001
        pass
    if not trading:
        return {"code": "holiday", "label": "休市", "hint": "非 A 股交易日"}
    t = _hm(now)
    if t < _A_AUCTION:
        return {"code": "pre_open", "label": "未开", "hint": "集合竞价前"}
    if t < _A_OPEN:
        return {"code": "auction", "label": "竞价", "hint": "09:25–09:30 集合竞价"}
    if t < _A_LUNCH:
        return {"code": "morning", "label": "午前", "hint": "上午连续竞价"}
    if t < _A_AFTERNOON:
        return {"code": "lunch", "label": "午休", "hint": "11:30–13:00"}
    if t < _A_CLOSE:
        return {"code": "afternoon", "label": "午后", "hint": "下午连续竞价"}
    if t < _A_EOD:
        return {"code": "closing", "label": "收盘", "hint": "收盘集合附近"}
    return {"code": "closed", "label": "已收", "hint": "15:05 后"}


def _us_rth_phase(now_cn: datetime) -> Dict[str, str]:
    us = now_cn.astimezone(_US_TZ)
    if us.weekday() >= 5:
        return {"code": "closed", "label": "休市", "hint": "美股周末"}
    t = _hm(us)
    if t < _US_OPEN:
        return {"code": "pre", "label": "未开", "hint": "美东 RTH 前"}
    if t < _US_CLOSE:
        return {"code": "open", "label": "盘中", "hint": "美东 RTH 09:30–16:00"}
    return {"code": "closed", "label": "已收", "hint": "美东 RTH 已结束"}


def build_session_clock(
    *,
    now: Optional[datetime] = None,
    tech_session_as_of: Optional[str] = None,
    macro_as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """Prior Desk 会话时钟载荷。

    典型盘前叙事：美股已收 · A股未开；并附科技会话日 / macro as-of。
    """
    n = shanghai_now(now)
    if n.tzinfo is None:
        n = n.replace(tzinfo=_CN_TZ)
    else:
        n = n.astimezone(_CN_TZ)

    a = _a_share_phase(n)
    us = _us_rth_phase(n)
    tech = str(tech_session_as_of or "")[:10]
    macro = str(macro_as_of or "")[:10]

    # 一眼标签：优先「美股×A股」组合，避免堆字段
    if us["code"] == "closed" and a["code"] == "pre_open":
        label = "美股已收 · A未开"
        tone = "pre"
    elif us["code"] == "closed" and a["code"] in ("holiday", "closed"):
        label = f"美股已收 · A{a['label']}"
        tone = "idle"
    elif us["code"] == "open" and a["code"] in ("closed", "holiday", "pre_open"):
        label = "美股盘中 · 隔夜"
        tone = "live"
    elif a["code"] in ("morning", "afternoon", "auction"):
        label = f"A{a['label']} · 美股{us['label']}"
        tone = "live"
    elif a["code"] == "lunch":
        label = f"A午休 · 美股{us['label']}"
        tone = "idle"
    else:
        label = f"美股{us['label']} · A{a['label']}"
        tone = "idle"

    return {
        "now_cn": n.strftime("%Y-%m-%d %H:%M"),
        "now_hm": n.strftime("%H:%M"),
        "a_share": a,
        "us_rth": us,
        "tech_session_as_of": tech or None,
        "macro_as_of": macro or None,
        "label": label,
        "tone": tone,
        "tip": (
            f"上海 {n.strftime('%H:%M')} · A股{a['label']}（{a['hint']}）"
            f" · 美股{us['label']}（{us['hint']}）"
            + (f" · 科技会话 {tech[5:]}" if len(tech) >= 10 else "")
            + (f" · macro {macro}" if macro else "")
        ),
    }
