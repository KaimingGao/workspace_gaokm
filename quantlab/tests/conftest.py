"""共享测试工具与合成 bar/minute 数据构造函数。

原本散落在 test_signal.py / test_p10_quant.py / test_alpha_tc.py 中，
被 18+ 个测试文件交叉 import，集中到此统一出口。
"""

from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

import numpy as np

# 测试进程不打东财，新闻库也不写进 data/store。
os.environ.setdefault("QUANTLAB_NEWS_ARCHIVE_POLL", "0")
os.environ.setdefault("QUANTLAB_NEWS_ARCHIVE_INTERVAL", "0")
os.environ.setdefault(
    "QUANTLAB_NEWS_ARCHIVE_DIR",
    tempfile.mkdtemp(prefix="ql-news-archive-"),
)

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


# ---------------------------------------------------------------------------
# Horizon / T0 共享夹具
# ---------------------------------------------------------------------------


def _session_times_5m():
    out = []
    for total in range(9 * 60 + 30, 11 * 60 + 30 + 1, 5):
        out.append(f"{total // 60:02d}:{total % 60:02d}")
    for total in range(13 * 60, 15 * 60 + 1, 5):
        out.append(f"{total // 60:02d}:{total % 60:02d}")
    return out


def _minute_bar_with_datetime(
    dkey: str,
    hm: str,
    close: float,
    open_px: float | None = None,
    volume: float = 1000,
):
    o = open_px if open_px is not None else close
    return {
        "date": dkey,
        "time": hm,
        "datetime": f"{dkey} {hm}:00",
        "open": round(o, 4),
        "high": round(max(o, close), 4),
        "low": round(min(o, close), 4),
        "close": round(close, 4),
        "volume": volume,
    }


def _minute_bar(dkey: str, hm: str, close: float, open_px: float | None = None):
    o = open_px if open_px is not None else close
    return {
        "date": dkey,
        "time": hm,
        "open": round(o, 4),
        "high": round(max(o, close), 4),
        "low": round(min(o, close), 4),
        "close": round(close, 4),
        "volume": 1000,
    }


def _horizon_tree_stock(code: str, start: float, n_days: int = 40):
    d0 = date(2025, 6, 2)
    daily = []
    minutes = []
    px = start
    times = _session_times_5m()
    i = 0
    day = d0
    while len(daily) < n_days:
        if day.weekday() < 5:
            dkey = day.isoformat()
            bars_m = []
            for j, hm in enumerate(times):
                c = px + (0.04 if j % 2 else 0.0) + j * 0.002 + (i % 3) * 0.01
                bars_m.append(_minute_bar(dkey, hm, c, open_px=px))
            minutes.extend(bars_m)
            close = bars_m[-1]["close"]
            daily.append(
                {
                    "date": dkey,
                    "open": px,
                    "high": max(px, close) * 1.01,
                    "low": min(px, close) * 0.99,
                    "close": close,
                    "volume": 1e6,
                    "prev_close": px * 0.995,
                }
            )
            px = close
            i += 1
        day = day + timedelta(days=1)
    return {"code": code, "bars": daily, "minute_bars": minutes}


def _has_lightgbm() -> bool:
    try:
        import lightgbm  # noqa: F401

        return True
    except ImportError:
        return False


def _pack_cross_section_day(date_s, codes, xs, ys):
    names = list(xs[0].keys()) if xs else []
    X = np.array(
        [
            [float(row[n]) if row.get(n) is not None else np.nan for n in names]
            for row in xs
        ],
        dtype=np.float64,
    )
    return {"date": date_s, "codes": codes, "ys": ys, "X": X, "names": names}


def _t0_window_heads(up=True):
    p = 0.8 if up else 0.2
    return {
        "y_τ30": p,
        "y_t30": p,
        "y_τ45": p,
        "y_t45": p,
        "y_τ60": p,
        "y_t60": p,
        "y_τ75": p,
        "y_t75": p,
        "y_τ90": p,
        "y_t90": p,
    }


def _panel_bars(n: int = 80, start: float = 10.0, seed: int = 0):
    px = start
    out = []
    for i in range(n):
        o = px * (1.0 + (0.01 if (i + seed) % 5 == 0 else 0.0))
        c = o * (1.0 + ((i % 7) - 3) * 0.002)
        out.append(
            {
                "date": f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                "open": round(o, 4),
                "high": round(max(o, c) * 1.01, 4),
                "low": round(min(o, c) * 0.99, 4),
                "close": round(c, 4),
                "volume": 1_000_000 + i * 1000,
            }
        )
        px = c
    return out


def _yhat_factory_job(run_id: str, pid: int = 7, **extra):
    job = {
        "run_id": run_id,
        "pid": pid,
        "head": "oo",
        "params": {
            "pool_n": 800,
            "lookback": 900,
            "holdout_trading_days": 60,
            "t_sample_pct": 10,
            "full_aoo": False,
            "loss": "mse",
        },
        "started_at": "2026-11-16T01:02:03",
        "finished_at": "2026-11-16T01:08:00",
        "cli": "python holdout.py --head oo",
    }
    job.update(extra)
    return job


def _yhat_factory_last(**extra):
    last = {
        "action": "holdout_result",
        "head": "oo",
        "metrics": {
            "cs_rank_ic": 0.042,
            "mse": 6.5,
            "arm": "T",
            "arms": {"T": {"success": True, "cs_rank_ic": 0.042}},
            "splits": {"T": {"n_train": 10, "n_test": 4, "eval_start": "2026-01-02"}},
        },
        "parity": {"formal": False, "eval_shared": True},
        "eval_universe": {"eval_shared": True, "eval_start": "2026-01-02", "n_shared": 4},
        "message": "Holdout 完成",
        "updated_at": "2026-11-16T01:08:00",
    }
    last.update(extra)
    return last
