"""共享测试工具与合成 bar/minute 数据构造函数。

原本散落在 test_signal.py / test_p10_quant.py / test_alpha_tc.py 中，
被 18+ 个测试文件交叉 import，集中到此统一出口。
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import numpy as np

# ---------------------------------------------------------------------------
# 仓库根路径（少数需要拼文件路径的测试用）
# ---------------------------------------------------------------------------

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# 合成 bar 数据
# ---------------------------------------------------------------------------

def _rising_bars():
    """温和上涨 + 放量，15 根日 bar。"""
    base = 100.0
    bars = []
    for i in range(15):
        close = base + i * 0.4
        bars.append(
            {
                "date": f"2026-01-{i+1:02d}",
                "open": close - 0.2,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 1000 + i * 50,
            }
        )
    return bars


def _overheated_bars():
    """暴涨日 bar（连续 6%+ 涨幅），用于 hard_reject 测试。"""
    bars = []
    price = 100.0
    for i in range(10):
        price *= 1.06
        bars.append(
            {
                "date": f"d{i}",
                "open": price * 0.99,
                "high": price * 1.02,
                "low": price * 0.98,
                "close": price,
                "volume": 2000,
            }
        )
    return bars


def _aligned_bars(prefix: str = "a", n: int = 40, step: float = 0.4):
    """多股票横截面对齐 bar 序列（日期完全一致）。"""
    bars = []
    for i in range(n):
        close = 100 + i * step
        bars.append(
            {
                "date": f"2026-01-{i+1:02d}",
                "open": close - 0.2,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 1000 + i * 30,
            }
        )
    return bars


def _synth_bars(n: int, seed: int = 42) -> list:
    """numpy 驱动的随机合成日 bar（alpha_tc 测试用）。"""
    rng = np.random.default_rng(seed)
    bars = []
    c = 10.0
    for i in range(n):
        o = c * (1.0 + float(rng.normal(0, 0.005)))
        h = max(o, c) * (1.0 + abs(float(rng.normal(0, 0.01))))
        l = min(o, c) * (1.0 - abs(float(rng.normal(0, 0.01))))
        c = c * (1.0 + float(rng.normal(0, 0.015)))
        v = float(rng.integers(1000, 10000))
        bars.append(
            {
                "trade_date": f"2024{(i % 12) + 1:02d}{(i % 28) + 1:02d}",
                "date": f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
            }
        )
    return bars


def _unique_bars(n: int, seed: int = 7) -> list:
    """决策日唯一的合成 bar 序列（20000+i 伪日期，保证唯一）。"""
    bars = _synth_bars(n, seed=seed)
    for i, bar in enumerate(bars):
        day = 20000 + i
        text = f"{day:05d}"
        bar["date"] = text
        bar["trade_date"] = text
    return bars


def _dated_bars_and_minutes(n: int, *, seed: int = 3):
    """带真实日历日期的 bar + minute 合成序列（tau 截面测试用）。"""
    bars = _unique_bars(n, seed=seed)
    start = date(2020, 1, 2)
    minutes = []
    clocks = ("09:35", "09:40", "09:45", "09:50", "09:55", "10:00", "10:30", "11:00")
    for i, bar in enumerate(bars):
        day = (start + timedelta(days=i)).isoformat()
        bar["date"] = day
        bar["trade_date"] = day
        px = float(bar["open"])
        for k, hm in enumerate(clocks):
            px = px * (1.0 + (0.001 if (i + k) % 2 == 0 else -0.0004))
            minutes.append(
                {
                    "date": day,
                    "datetime": f"{day} {hm}:00",
                    "open": px,
                    "high": px * 1.002,
                    "low": px * 0.998,
                    "close": px * (1.0 + 0.0003 * ((k % 3) - 1)),
                    "volume": 200.0 + 10 * k + i,
                }
            )
    return bars, minutes
