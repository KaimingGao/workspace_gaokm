"""alpha_tc 因子单元测试（τ PIT：≤T−1 + 今开 + ≤τ 分钟）。"""
from __future__ import annotations

import math

import numpy as np

from core.signal.factors.alpha_tc import (
    ALPHA_TC_FIELD_NAMES,
    ALPHA_TC_MIN_BARS,
    ALPHA_TC_RAW_PREFIX,
    is_alpha_tc_raw_key,
    raw_atc_from_bars,
    score_alpha_tc,
    _gap_fields,
    _yk_fields,
)
from core.signal.factors.meta.registry import compute_factor, registered_factor_names


def _synth_bars(n: int, seed: int = 42) -> list:
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


def _synth_minute_prefix(
    *,
    trade_date: str,
    open_px: float,
    n_bars: int = 6,
    seed: int = 7,
) -> list:
    """合成 ≤τ 的 5m 前缀（从 09:30 起）。"""
    rng = np.random.default_rng(seed)
    bars = []
    px = float(open_px)
    for i in range(n_bars):
        tmin = 9 * 60 + 30 + i * 5
        hh, mm = divmod(tmin, 60)
        ret = float(rng.normal(0.001, 0.003))
        o = px
        c = px * (1.0 + ret)
        h = max(o, c) * (1.0 + abs(float(rng.normal(0, 0.002))))
        l = min(o, c) * (1.0 - abs(float(rng.normal(0, 0.002))))
        bars.append(
            {
                "trade_date": trade_date,
                "date": trade_date,
                "time": f"{hh:02d}:{mm:02d}",
                "hm": f"{hh:02d}:{mm:02d}",
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": float(rng.integers(1000, 5000)),
            }
        )
        px = c
    return bars


def test_alpha_tc_field_count():
    assert len(ALPHA_TC_FIELD_NAMES) == 224
    assert len(set(ALPHA_TC_FIELD_NAMES)) == 224


def test_alpha_tc_key_fields_listed():
    assert "GAP" in ALPHA_TC_FIELD_NAMES
    assert "CLOSE0" in ALPHA_TC_FIELD_NAMES
    assert "TAU_IN_YRANGE" in ALPHA_TC_FIELD_NAMES
    assert "RET_OT" in ALPHA_TC_FIELD_NAMES
    assert "YKMID" in ALPHA_TC_FIELD_NAMES
    assert "PROC5" in ALPHA_TC_FIELD_NAMES
    assert "PVMA5" in ALPHA_TC_FIELD_NAMES
    assert "TC_LAG1" in ALPHA_TC_FIELD_NAMES
    assert "RANGE_PCT" in ALPHA_TC_FIELD_NAMES
    assert "MOM3_C" in ALPHA_TC_FIELD_NAMES


def test_alpha_tc_all_fields_present_tau_open():
    bars = _synth_bars(80)
    score, meta = score_alpha_tc(bars, tau_hm="open")
    assert score == 50.0
    assert meta.get("omit_sub_score") is True
    assert meta.get("alpha_tc_anchor") == "price_tau"
    for name in ALPHA_TC_FIELD_NAMES:
        assert name in meta, f"missing field: {name}"
        assert math.isfinite(meta[name]), f"non-finite: {name}={meta[name]}"


def test_alpha_tc_insufficient_history():
    score, meta = score_alpha_tc(_synth_bars(30))
    assert score == 50.0
    assert meta.get("omit_sub_score") is True
    assert meta.get("alpha_tc_insufficient_history") is True


def test_alpha_tc_min_bars():
    assert ALPHA_TC_MIN_BARS == 61


def test_alpha_tc_yk_formula():
    fields = _yk_fields(close=11.0, open_=10.0, high=12.0, low=9.0)
    assert abs(fields["YKMID"] - 0.1) < 1e-9
    assert abs(fields["YBODY"] - 0.1) < 1e-9


def test_alpha_tc_gap_close0_anchored_on_price_tau():
    open_arr = np.array([9.0, 10.0, 11.5], dtype=np.float64)
    high = np.array([9.5, 12.0, 12.0], dtype=np.float64)
    low = np.array([8.5, 9.0, 11.0], dtype=np.float64)
    close = np.array([9.2, 11.0, 11.8], dtype=np.float64)  # price_τ=11.8
    volume = np.array([1e5, 2e5, 2e5], dtype=np.float64)
    fields = _gap_fields(
        11.5,
        11.8,
        11.0,
        10.0,
        12.0,
        9.0,
        2e5,
        open_arr,
        high,
        low,
        close,
        volume,
        prefix_hi=12.0,
        prefix_lo=11.0,
    )
    assert abs(fields["CLOSE0"] - 11.0 / 11.8) < 1e-9
    assert abs(fields["GAP"] - (11.5 / 11.0 - 1.0)) < 1e-9
    assert abs(fields["RET_OT"] - (11.8 / 11.5 - 1.0)) < 1e-9
    assert abs(fields["RET_PT"] - (11.8 / 11.0 - 1.0)) < 1e-9
    assert abs(fields["OPEN0"] - 11.5 / 11.8) < 1e-9
    assert abs(fields["TAU_IN_YRANGE"] - (11.8 - 9.0) / (12.0 - 9.0)) < 1e-9
    assert abs(fields["TAU_IN_PREFIX"] - (11.8 - 11.0) / (12.0 - 11.0)) < 1e-9


def test_alpha_tc_flat_prev_bar_no_eps_blowup():
    """昨 H=L / 无前缀振幅时位置类不得用 1e-12 放大。"""
    open_arr = np.array([9.0, 10.0, 11.0], dtype=np.float64)
    high = np.array([9.5, 10.0, 11.0], dtype=np.float64)
    low = np.array([8.5, 10.0, 11.0], dtype=np.float64)
    close = np.array([9.2, 10.0, 11.0], dtype=np.float64)
    volume = np.array([1e5, 2e5, 2e5], dtype=np.float64)
    fields = _gap_fields(
        11.0,
        11.0,
        10.0,
        10.0,
        10.0,
        10.0,
        2e5,
        open_arr,
        high,
        low,
        close,
        volume,
        prefix_hi=11.0,
        prefix_lo=11.0,
    )
    assert fields["TAU_IN_YRANGE"] == 0.5
    assert fields["YCLOSE_LOC"] == 0.5
    assert fields["GAP_VS_YRANGE"] == 0.0
    assert fields["TAU_IN_PREFIX"] == 0.5
    assert abs(fields["TAU_IN_YRANGE"]) < 10.0
    yk = _yk_fields(close=10.0, open_=10.0, high=10.0, low=10.0)
    assert yk["YKMID2"] == 0.0


def test_alpha_tc_with_minute_prefix_formulas():
    bars = _synth_bars(80)
    hist = bars[:-1]
    open_t = float(bars[-1]["open"])
    day = "2024-06-15"
    quote = {"date": day, "open": open_t, "prev_close": float(hist[-1]["close"])}
    minutes = _synth_minute_prefix(trade_date=day, open_px=open_t, n_bars=6)
    price_tau = float(minutes[-1]["close"])
    hi = max(float(b["high"]) for b in minutes)
    lo = min(float(b["low"]) for b in minutes)

    score, meta = score_alpha_tc(
        hist,
        open_t=open_t,
        prev_close=float(hist[-1]["close"]),
        price_tau=price_tau,
        minute_bars=minutes,
        tau_hm="10:00",
        quote=quote,
    )
    assert score == 50.0
    assert abs(meta["RET_OT"] - (price_tau / open_t - 1.0)) < 1e-9
    assert abs(meta["RANGE_PCT"] - (hi - lo) / open_t) < 1e-6
    assert abs(meta["CLOSE0"] - float(hist[-1]["close"]) / price_tau) < 1e-9
    assert meta["TAU_ELAPSED_MIN"] == 30.0  # 10:00 − 09:30
    for name in ALPHA_TC_FIELD_NAMES:
        assert math.isfinite(meta[name]), f"non-finite: {name}={meta[name]}"


def test_alpha_tc_mom3_c_matches_close_momentum():
    from core.signal.factors.alpha_tc import _lag_fields

    bars = _synth_bars(80)
    hist = bars[:-1]
    open_t = float(bars[-1]["open"])
    o = np.array([float(b["open"]) for b in hist] + [open_t], dtype=np.float64)
    h = np.array([float(b["high"]) for b in hist] + [open_t], dtype=np.float64)
    l = np.array([float(b["low"]) for b in hist] + [open_t], dtype=np.float64)
    c = np.array([float(b["close"]) for b in hist] + [open_t], dtype=np.float64)
    v = np.array(
        [float(b["volume"]) for b in hist] + [float(hist[-1]["volume"])],
        dtype=np.float64,
    )
    lag = _lag_fields(o, c, h, l, v)
    c_hist = np.array([float(b["close"]) for b in hist], dtype=np.float64)
    o_hist = np.array([float(b["open"]) for b in hist], dtype=np.float64)
    expected_mom3 = float(c_hist[-1] / c_hist[-4] - 1.0)
    expected_tc1 = float(c_hist[-1] / o_hist[-1] - 1.0)
    assert abs(float(lag["MOM3_C"]) - expected_mom3) < 1e-6
    assert abs(float(lag["TC_LAG1"]) - expected_tc1) < 1e-6


def test_raw_atc_from_bars_helper():
    assert raw_atc_from_bars(_synth_bars(30)) == {}
    out = raw_atc_from_bars(_synth_bars(80))
    assert len(out) >= 200
    assert all(is_alpha_tc_raw_key(k) for k in out)
    assert all(k.startswith(ALPHA_TC_RAW_PREFIX) for k in out)


def test_raw_atc_with_open_t():
    bars = _synth_bars(80)
    hist = bars[:-1]
    open_t = float(bars[-1]["open"])
    out = raw_atc_from_bars(hist, open_t=open_t, tau_hm="open")
    assert f"{ALPHA_TC_RAW_PREFIX}GAP" in out
    assert math.isfinite(out[f"{ALPHA_TC_RAW_PREFIX}PROC5"])
    assert math.isfinite(out[f"{ALPHA_TC_RAW_PREFIX}RET_OT"])
    # τ=open → RET_OT ≈ 0
    assert abs(out[f"{ALPHA_TC_RAW_PREFIX}RET_OT"]) < 1e-12


def test_alpha_tc_registered():
    assert "alpha_tc" in registered_factor_names()
    bars = _synth_bars(80)
    score, meta = compute_factor("alpha_tc", bars)
    assert score == 50.0
    assert meta.get("omit_sub_score") is True
    assert "GAP" in meta
    assert "PROC5" in meta
