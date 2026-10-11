"""alpha_oo 因子单元测试（开盘 PIT：≤T−1 + 今开）。"""
from __future__ import annotations

import math

import numpy as np

from core.signal.factors.alpha_oo import (
    ALPHA_OO_CORE_FIELD_NAMES,
    ALPHA_OO_FIELD_NAMES,
    ALPHA_OO_MIN_BARS,
    ALPHA_OO_RAW_PREFIX,
    Z_OO_DROP_WHEN_AOO_CORE,
    alpha_oo_ridge_raw_names,
    apply_aoo_core_ridge_policy,
    expand_ridge_feature_names,
    is_alpha_oo_raw_key,
    is_alpha_oo_ridge_raw_key,
    raw_aoo_from_bars,
    score_alpha_oo,
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
                "trade_date": f"2024{i + 1:04d}",
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
            }
        )
    return bars


def test_alpha_oo_field_count():
    assert len(ALPHA_OO_FIELD_NAMES) == 206
    assert len(set(ALPHA_OO_FIELD_NAMES)) == 206
    assert len(ALPHA_OO_CORE_FIELD_NAMES) == 49
    assert len(alpha_oo_ridge_raw_names()) == 49
    assert "OROC5" not in ALPHA_OO_CORE_FIELD_NAMES
    assert "OO_LAG1" in ALPHA_OO_CORE_FIELD_NAMES
    assert "MOM3_C" in ALPHA_OO_CORE_FIELD_NAMES
    assert "OC_LAG1" not in ALPHA_OO_CORE_FIELD_NAMES  # ≡ YKMID
    assert "REV1_O" not in ALPHA_OO_CORE_FIELD_NAMES  # ≡ −OO_LAG1


def test_alpha_oo_key_fields_listed():
    assert "GAP" in ALPHA_OO_FIELD_NAMES
    assert "CLOSE0" in ALPHA_OO_FIELD_NAMES
    assert "YKMID" in ALPHA_OO_FIELD_NAMES
    assert "OROC5" in ALPHA_OO_FIELD_NAMES
    assert "OO_LAG1" in ALPHA_OO_FIELD_NAMES
    assert "YRANGE_PCT" in ALPHA_OO_FIELD_NAMES
    assert "MOM3_C" in ALPHA_OO_FIELD_NAMES


def test_aoo_core_ridge_policy():
    assert is_alpha_oo_ridge_raw_key(f"{ALPHA_OO_RAW_PREFIX}GAP")
    assert not is_alpha_oo_ridge_raw_key(f"{ALPHA_OO_RAW_PREFIX}OROC5")
    assert Z_OO_DROP_WHEN_AOO_CORE == frozenset(
        {"momentum", "gap_risk", "overheat"}
    )
    names = [
        "z_oo_momentum",
        "z_oo_value",
        "z_oo_gap_risk",
        "z_oo_overheat",
        f"{ALPHA_OO_RAW_PREFIX}GAP",
        f"{ALPHA_OO_RAW_PREFIX}OROC5",
        f"{ALPHA_OO_RAW_PREFIX}YKMID",
    ]
    out = apply_aoo_core_ridge_policy(names)
    assert "z_oo_momentum" not in out
    assert "z_oo_gap_risk" not in out
    assert "z_oo_overheat" not in out
    assert "z_oo_value" in out
    assert f"{ALPHA_OO_RAW_PREFIX}GAP" in out
    assert f"{ALPHA_OO_RAW_PREFIX}YKMID" in out
    assert f"{ALPHA_OO_RAW_PREFIX}OROC5" not in out


def test_expand_ridge_feature_names_defaults_to_full_union():
    xs = [
        {
            "momentum": 55.0,
            "value": 40.0,
            f"{ALPHA_OO_RAW_PREFIX}GAP": 0.01,
            f"{ALPHA_OO_RAW_PREFIX}OROC5": -0.02,
            f"{ALPHA_OO_RAW_PREFIX}YKMID": 0.03,
        }
    ]
    names = expand_ridge_feature_names(xs)
    assert f"{ALPHA_OO_RAW_PREFIX}GAP" in names
    assert f"{ALPHA_OO_RAW_PREFIX}YKMID" in names
    assert f"{ALPHA_OO_RAW_PREFIX}OROC5" in names
    assert "z_oo_momentum" in names
    assert "z_oo_value" in names
    core = expand_ridge_feature_names(xs, ridge_core_only=True)
    assert f"{ALPHA_OO_RAW_PREFIX}OROC5" not in core
    assert "z_oo_momentum" not in core
    assert f"{ALPHA_OO_RAW_PREFIX}GAP" in core


def test_alpha_oo_all_fields_present():
    bars = _synth_bars(80)
    score, meta = score_alpha_oo(bars)
    assert score == 50.0
    assert meta.get("omit_sub_score") is True
    assert meta.get("alpha_oo_anchor") == "open"
    for name in ALPHA_OO_FIELD_NAMES:
        assert name in meta, f"missing field: {name}"
        assert math.isfinite(meta[name]), f"non-finite: {name}={meta[name]}"


def test_alpha_oo_insufficient_history():
    score, meta = score_alpha_oo(_synth_bars(30))
    assert score == 50.0
    assert meta.get("omit_sub_score") is True
    assert meta.get("alpha_oo_insufficient_history") is True


def test_alpha_oo_min_bars():
    assert ALPHA_OO_MIN_BARS == 61


def test_alpha_oo_yk_formula():
    fields = _yk_fields(close=11.0, open_=10.0, high=12.0, low=9.0)
    assert abs(fields["YKMID"] - 0.1) < 1e-9
    assert abs(fields["YBODY"] - 0.1) < 1e-9


def test_alpha_oo_gap_close0():
    open_arr = np.array([9.0, 10.0, 11.5], dtype=np.float64)
    high = np.array([9.5, 12.0, 11.5], dtype=np.float64)
    low = np.array([8.5, 9.0, 11.5], dtype=np.float64)
    close = np.array([9.2, 11.0, 11.5], dtype=np.float64)
    volume = np.array([1e5, 2e5, 2e5], dtype=np.float64)
    fields = _gap_fields(
        11.5, 11.0, 10.0, 12.0, 9.0, 2e5, open_arr, high, low, close, volume
    )
    assert abs(fields["CLOSE0"] - 11.0 / 11.5) < 1e-9
    assert abs(fields["GAP"] - (11.5 / 11.0 - 1.0)) < 1e-9
    # 昨振幅 (H−L)/C
    assert abs(fields["YRANGE_PCT"] - (12.0 - 9.0) / 11.0) < 1e-9
    assert abs(fields["OPEN_IN_YRANGE"] - (11.5 - 9.0) / (12.0 - 9.0)) < 1e-9


def test_alpha_oo_flat_prev_bar_no_eps_blowup():
    """昨 H=L 时 OPEN_IN_YRANGE / GAP_VS_YRANGE 不得用 1e-12 当分母放大到 ~1e12。"""
    open_arr = np.array([9.0, 10.0, 11.0], dtype=np.float64)
    high = np.array([9.5, 10.0, 11.0], dtype=np.float64)
    low = np.array([8.5, 10.0, 11.0], dtype=np.float64)
    close = np.array([9.2, 10.0, 11.0], dtype=np.float64)
    volume = np.array([1e5, 2e5, 2e5], dtype=np.float64)
    fields = _gap_fields(
        11.0, 10.0, 10.0, 10.0, 10.0, 2e5, open_arr, high, low, close, volume
    )
    assert fields["OPEN_IN_YRANGE"] == 0.5
    assert fields["YCLOSE_LOC"] == 0.5
    assert fields["GAP_VS_YRANGE"] == 0.0
    assert abs(fields["OPEN_IN_YRANGE"]) < 10.0
    assert abs(fields["GAP_VS_YRANGE"]) < 10.0
    yk = _yk_fields(close=10.0, open_=10.0, high=10.0, low=10.0)
    assert yk["YKMID2"] == 0.0
    assert yk["YUPPER_SHARE"] == 0.0


def test_alpha_oo_mom3_c_matches_close_momentum():
    """MOM3_C = close[T−1]/close[T−4]−1（比例）。"""
    from core.signal.factors.alpha_oo import _lag_fields

    bars = _synth_bars(80)
    hist = bars[:-1]
    open_t = float(bars[-1]["open"])
    o = np.array([float(b["open"]) for b in hist] + [open_t], dtype=np.float64)
    h = np.array(
        [float(b["high"]) for b in hist] + [open_t], dtype=np.float64
    )
    l = np.array([float(b["low"]) for b in hist] + [open_t], dtype=np.float64)
    c = np.array(
        [float(b["close"]) for b in hist] + [open_t], dtype=np.float64
    )
    v = np.array(
        [float(b["volume"]) for b in hist] + [float(hist[-1]["volume"])],
        dtype=np.float64,
    )
    lag = _lag_fields(o, c, h, l, v)
    # _lag_fields 先剥决策占位，MOM3_C 用完整日收盘
    c_hist = np.array([float(b["close"]) for b in hist], dtype=np.float64)
    expected = float(c_hist[-1] / c_hist[-4] - 1.0)
    assert lag["MOM3_C"] is not None
    assert abs(float(lag["MOM3_C"]) - expected) < 1e-6


def test_raw_aoo_from_bars_helper():
    assert raw_aoo_from_bars(_synth_bars(30)) == {}
    out = raw_aoo_from_bars(_synth_bars(80))
    assert len(out) >= 200
    assert all(is_alpha_oo_raw_key(k) for k in out)
    assert all(k.startswith(ALPHA_OO_RAW_PREFIX) for k in out)


def test_raw_aoo_with_open_t():
    bars = _synth_bars(80)
    hist = bars[:-1]
    open_t = float(bars[-1]["open"])
    out = raw_aoo_from_bars(hist, open_t=open_t)
    assert f"{ALPHA_OO_RAW_PREFIX}GAP" in out
    assert math.isfinite(out[f"{ALPHA_OO_RAW_PREFIX}OROC5"])


def test_alpha_oo_registered():
    assert "alpha_oo" in registered_factor_names()
    bars = _synth_bars(80)
    score, meta = compute_factor("alpha_oo", bars)
    assert score == 50.0
    assert meta.get("omit_sub_score") is True
    assert "GAP" in meta
