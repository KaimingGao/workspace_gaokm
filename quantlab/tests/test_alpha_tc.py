"""alpha_tc 因子单元测试（τ PIT：≤T−1 + 今开 + ≤τ 分钟）。"""
from __future__ import annotations

import math

import numpy as np

from core.signal.factors.alpha_tc import (
    ALPHA_TC_FIELD_NAMES,
    ALPHA_TC_MIN_BARS,
    ALPHA_TC_PANEL_WINDOW,
    ALPHA_TC_RAW_PREFIX,
    inject_raw_atc,
    is_alpha_tc_raw_key,
    ohlcv_from_bars,
    raw_atc_from_bars,
    raw_atc_open_columns,
    raw_atc_tau_columns,
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


def _unique_bars(n: int, seed: int = 7) -> list:
    bars = _synth_bars(n, seed=seed)
    for i, bar in enumerate(bars):
        day = 20000 + i
        # 20000+i 不是合法日历，只用来保证决策日唯一
        text = f"{day:05d}"
        bar["date"] = text
        bar["trade_date"] = text
    return bars


def _assert_open_columns_match(bars: list, *, hist: int = ALPHA_TC_PANEL_WINDOW) -> None:
    packed = ohlcv_from_bars(bars)
    assert packed is not None
    cols = raw_atc_open_columns(*packed, hist=hist)
    assert cols
    n = len(bars)
    for i in range(hist, n):
        window = bars[i - hist : i]
        o = float(bars[i]["open"])
        pc = float(bars[i - 1]["close"])
        ref: dict = {}
        inject_raw_atc(
            ref, window, open_t=o, prev_close=pc, price_tau=o, tau_hm="open"
        )
        ridx = i - hist
        got = {}
        for key, col in cols.items():
            val = float(col[ridx])
            if val == val:
                got[key] = val
        assert set(got) == set(ref), (i, set(got) ^ set(ref))
        for key, exp in ref.items():
            diff = abs(got[key] - float(exp))
            assert diff <= 1e-9, (i, key, got[key], exp, diff)


def test_open_columns_match_inject_short_and_long():
    for n in (80, 400, 1000):
        _assert_open_columns_match(_unique_bars(n, seed=n))


def test_open_columns_zero_yesterday_open_omits_keys():
    bars = _unique_bars(90, seed=3)
    bars[70]["open"] = 0.0
    packed = ohlcv_from_bars(bars)
    cols = raw_atc_open_columns(*packed, hist=ALPHA_TC_PANEL_WINDOW)
    ridx = 71 - ALPHA_TC_PANEL_WINDOW
    for col in cols.values():
        assert col[ridx] != col[ridx]
    ref: dict = {}
    inject_raw_atc(
        ref,
        bars[71 - ALPHA_TC_PANEL_WINDOW : 71],
        open_t=float(bars[71]["open"]),
        prev_close=float(bars[70]["close"]),
        price_tau=float(bars[71]["open"]),
        tau_hm="open",
    )
    assert ref == {}


def test_open_columns_zero_volume_matches_inject():
    bars = _unique_bars(90, seed=5)
    bars[80]["volume"] = 0.0
    _assert_open_columns_match(bars)


def test_collect_open_panel_uses_columns():
    from core.research.tau_panel import collect_tau_open_panel

    bars = _unique_bars(120, seed=11)
    xs, _ys, dates, _metas = collect_tau_open_panel(
        bars, include_alpha_tc=True
    )
    hist = ALPHA_TC_PANEL_WINDOW
    assert dates
    by_date = {d: row for d, row in zip(dates, xs)}
    for i in range(hist, len(bars)):
        day = bars[i]["date"]
        if float(bars[i]["open"]) <= 0 or float(bars[i]["close"]) <= 0:
            continue
        if float(bars[i - 1]["close"]) <= 0:
            continue
        ref: dict = {}
        inject_raw_atc(
            ref,
            bars[i - hist : i],
            open_t=float(bars[i]["open"]),
            prev_close=float(bars[i - 1]["close"]),
            price_tau=float(bars[i]["open"]),
            tau_hm="open",
        )
        row = by_date[day]
        got = {k: float(v) for k, v in row.items() if is_alpha_tc_raw_key(k)}
        assert set(got) == set(ref)
        for key, exp in ref.items():
            assert abs(got[key] - float(exp)) <= 1e-9


def test_collect_keep_dates_matches_full_slice():
    from core.research.tau_panel import collect_tau_open_panel

    bars = _unique_bars(100, seed=13)
    full_xs, _, full_dates, _ = collect_tau_open_panel(bars, include_alpha_tc=True)
    tail = full_dates[-5:]
    xs, _, dates, _ = collect_tau_open_panel(
        bars, include_alpha_tc=True, keep_dates=tail
    )
    assert dates == tail
    full_map = {d: row for d, row in zip(full_dates, full_xs)}
    for day, row in zip(dates, xs):
        got = {k: row[k] for k in row if is_alpha_tc_raw_key(k)}
        exp = {k: full_map[day][k] for k in full_map[day] if is_alpha_tc_raw_key(k)}
        assert got.keys() == exp.keys()
        for key in got:
            assert abs(float(got[key]) - float(exp[key])) <= 1e-9


def test_open_block_matches_dict_rows_including_dropped_day():
    """列直写矩阵与逐格抄 dict 同值。中间丢掉一天时下标仍对齐。"""
    from copy import deepcopy

    from core.research.panel_matrix import (
        _assemble_tau_open_block,
        _dict_rows_to_matrix,
        _tc_matrix_seed_names,
    )
    from core.research.tau_panel import collect_tau_open_panel
    from core.signal.factors.z_tc import stamp_z_tc_rows
    from core.yhat.keys import TAU_Z_FEATURES

    bars = _unique_bars(160, seed=21)
    bars[100]["close"] = 0.0
    names = _tc_matrix_seed_names(list(TAU_Z_FEATURES), include_alpha_tc=True)
    xs, ys, dates, _metas = collect_tau_open_panel(bars, include_alpha_tc=True)
    stamped = deepcopy(xs)
    stamp_z_tc_rows(stamped)
    expect = _dict_rows_to_matrix(stamped, names)

    atc_out: dict = {}
    xs2, ys2, dates2, _metas2 = collect_tau_open_panel(
        bars, include_alpha_tc=True, atc_out=atc_out
    )
    assert dates2 == dates
    assert ys2 == ys
    assert xs2 and not any(is_alpha_tc_raw_key(k) for k in xs2[0])
    got = _assemble_tau_open_block(xs2, names, atc_out)
    assert got.shape == expect.shape
    assert np.allclose(got, expect, equal_nan=True, rtol=0, atol=0)


def test_label_lag_skips_future_and_accepts_long_keys():
    from core.research.tau_panel import label_lag_features

    lags = label_lag_features(
        hist_bars=[{"date": "2025-06-01"}, {"date": "2025-06-02"}],
        by_date={"2025-06-01": 0.2, "2025-06-02": 9.0},
        asof_date="2025-06-02",
        lag1_key="tau_lag1",
        ma_key="tau_ma5",
    )
    assert lags["tau_lag1"] == 0.2
    assert lags["tau_ma5"] == 0.2

    long = label_lag_features(
        hist_bars=[{"date": "2025-06-01"}],
        by_date={"2025-06-01T00:00:00": 0.8, "2025-06-02T00:00:00": 9.0},
        asof_date="2025-06-02",
        lag1_key="tau_lag1",
        ma_key="tau_ma5",
    )
    assert long["tau_lag1"] == 0.8
    assert long["tau_ma5"] == 0.8


def test_open_columns_reject_bad_shape():
    bars = _unique_bars(80, seed=19)
    packed = ohlcv_from_bars(bars)
    assert packed is not None
    open_, high, low, close, volume = packed
    hist = ALPHA_TC_PANEL_WINDOW
    assert raw_atc_open_columns(open_.reshape(-1, 1), high, low, close, volume, hist=hist) == {}
    assert raw_atc_open_columns(open_[:-1], high, low, close, volume, hist=hist) == {}
    assert raw_atc_open_columns(open_, high, low, close, ["x"] * len(close), hist=hist) == {}


def test_nondict_bar_does_not_abort_open_panel():
    from core.research.tau_panel import collect_tau_open_panel

    bars = _unique_bars(100, seed=23)
    bars[80] = None
    xs, _ys, dates, _metas = collect_tau_open_panel(bars, include_alpha_tc=True)
    assert dates
    assert all(day != "" for day in dates)
    assert any(is_alpha_tc_raw_key(k) for k in xs[-1])


def test_short_columns_fall_back_to_inject():
    from unittest import mock

    from core.research.tau_panel import collect_tau_open_panel

    bars = _unique_bars(90, seed=29)
    hist = ALPHA_TC_PANEL_WINDOW

    def _short(*args, **kwargs):
        cols = raw_atc_open_columns(*args, **kwargs)
        key = next(iter(cols))
        cols[key] = cols[key][:3]
        return cols

    with mock.patch(
        "core.signal.factors.alpha_tc.raw_atc_open_columns",
        side_effect=_short,
    ):
        xs, _ys, dates, _metas = collect_tau_open_panel(bars, include_alpha_tc=True)
    assert dates
    ref: dict = {}
    inject_raw_atc(
        ref,
        bars[-hist - 1 : -1],
        open_t=float(bars[-1]["open"]),
        prev_close=float(bars[-2]["close"]),
        price_tau=float(bars[-1]["open"]),
        tau_hm="open",
    )
    got = {k: float(v) for k, v in xs[-1].items() if is_alpha_tc_raw_key(k)}
    assert set(got) == set(ref)
    for key, exp in ref.items():
        assert abs(got[key] - float(exp)) <= 1e-9


def _dated_bars_and_minutes(n: int, *, seed: int = 3):
    from datetime import date, timedelta

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


def test_minute_columns_match_inject_and_ath():
    from core.research.tau_panel import collect_tau_intraday_panel
    from core.signal.factors.alpha_th import inject_raw_ath

    bars, minutes = _dated_bars_and_minutes(90, seed=31)
    xs, _ys, dates, metas = collect_tau_intraday_panel(
        bars,
        minutes,
        tau_hm="10:00",
        include_alpha_tc=True,
        include_alpha_th=True,
    )
    assert dates
    assert [m.get("tau") for m in metas] == ["10:00"] * len(dates)
    hist = ALPHA_TC_PANEL_WINDOW
    by_date = {d: row for d, row in zip(dates, xs)}
    for i in range(hist, len(bars)):
        day = bars[i]["date"]
        if day not in by_date:
            continue
        day_mins = [m for m in minutes if m["date"] == day]
        got = {k: float(v) for k, v in by_date[day].items() if is_alpha_tc_raw_key(k)}
        from core.research.tau_panel import price_at_tau_from_minutes

        px = price_at_tau_from_minutes(day_mins, trade_date=day, tau_hm="10:00")
        ref = {}
        inject_raw_atc(
            ref,
            bars[i - hist : i],
            open_t=float(bars[i]["open"]),
            prev_close=float(bars[i - 1]["close"]),
            price_tau=float(px),
            minute_bars=day_mins,
            tau_hm="10:00",
        )
        assert set(got) == set(ref), (day, set(got) ^ set(ref))
        for key, exp in ref.items():
            assert abs(got[key] - float(exp)) <= 1e-9, (day, key, got[key], exp)
        bare = {
            k: v
            for k, v in by_date[day].items()
            if not is_alpha_tc_raw_key(k) and not str(k).startswith("raw_ath_")
        }
        ath: dict = dict(bare)
        inject_raw_ath(
            ath,
            bars[i - hist : i],
            open_t=float(bars[i]["open"]),
            prev_close=float(bars[i - 1]["close"]),
            price_tau=float(px),
            minute_bars=day_mins,
            tau_hm="10:00",
            feats=bare,
        )
        got_ath = {
            k: float(v)
            for k, v in by_date[day].items()
            if str(k).startswith("raw_ath_")
        }
        exp_ath = {k: float(v) for k, v in ath.items() if str(k).startswith("raw_ath_")}
        assert set(got_ath) == set(exp_ath), (day, set(got_ath) ^ set(exp_ath))
        for key, exp in exp_ath.items():
            assert abs(got_ath[key] - exp) <= 1e-9, (day, key, got_ath[key], exp)


def _assert_minute_row_matches_inject(bars, minutes, xs, dates, *, clock: str) -> None:
    from core.research.tau_panel import price_at_tau_from_minutes

    hist = ALPHA_TC_PANEL_WINDOW
    by_date = {d: row for d, row in zip(dates, xs)}
    checked = 0
    for i in range(hist, len(bars)):
        day = bars[i]["date"]
        if day not in by_date:
            continue
        day_mins = [m for m in minutes if m["date"] == day]
        px = price_at_tau_from_minutes(day_mins, trade_date=day, tau_hm=clock)
        ref: dict = {}
        inject_raw_atc(
            ref,
            bars[i - hist : i],
            open_t=float(bars[i]["open"]),
            prev_close=float(bars[i - 1]["close"]),
            price_tau=float(px),
            minute_bars=day_mins,
            tau_hm=clock,
        )
        got = {k: float(v) for k, v in by_date[day].items() if is_alpha_tc_raw_key(k)}
        assert set(got) == set(ref), (day, set(got) ^ set(ref))
        for key, exp in ref.items():
            assert abs(got[key] - float(exp)) <= 1e-9
        checked += 1
    assert checked > 0


def test_blank_minute_column_row_falls_back():
    from unittest import mock

    import numpy as np

    from core.research.tau_panel import collect_tau_intraday_panel
    from core.signal.factors.alpha_tc import raw_atc_tau_columns

    bars, minutes = _dated_bars_and_minutes(90, seed=37)

    def _blank(*args, **kwargs):
        cols = raw_atc_tau_columns(*args, **kwargs)
        for col in cols.values():
            col[0] = np.nan
        return cols

    with mock.patch(
        "core.signal.factors.alpha_tc.raw_atc_tau_columns",
        side_effect=_blank,
    ):
        xs, _ys, dates, _metas = collect_tau_intraday_panel(
            bars, minutes, tau_hm="10:00", include_alpha_tc=True
        )
    _assert_minute_row_matches_inject(bars, minutes, xs, dates, clock="10:00")


def test_one_bad_prefix_does_not_drop_other_days():
    from unittest import mock

    from core.research.tau_panel import collect_tau_intraday_panel
    from core.signal.factors.alpha_tc import _prefix_summary

    bars, minutes = _dated_bars_and_minutes(90, seed=41)
    bad_day = bars[70]["date"]

    def _boom(*args, **kwargs):
        if kwargs.get("trade_date") == bad_day:
            raise RuntimeError("prefix down")
        return _prefix_summary(*args, **kwargs)

    with mock.patch(
        "core.signal.factors.alpha_tc._prefix_summary",
        side_effect=_boom,
    ):
        xs, _ys, dates, _metas = collect_tau_intraday_panel(
            bars, minutes, tau_hm="10:00", include_alpha_tc=True
        )
    assert bad_day in dates
    assert len(dates) > 2
    good = [d for d in dates if d != bad_day]
    by_date = {d: row for d, row in zip(dates, xs)}
    assert any(is_alpha_tc_raw_key(k) for k in by_date[good[-1]])


def test_bad_prefix_field_keeps_other_columns():
    bars = _unique_bars(80, seed=43)
    packed = ohlcv_from_bars(bars)
    assert packed is not None
    open_, high, low, close, volume = packed
    hist = ALPHA_TC_PANEL_WINDOW
    m = len(bars) - hist
    anchor = open_[hist:]
    cols = raw_atc_tau_columns(
        open_,
        high,
        low,
        close,
        volume,
        hist=hist,
        price_tau=anchor,
        prefix_hi=anchor,
        prefix_lo=anchor,
        prefix_vol=np.zeros(m),
        prefix_fields={"RANGE_PCT": np.array([1.0])},
        tau_hm="10:00",
    )
    assert len(cols) >= 200
    got = cols[f"{ALPHA_TC_RAW_PREFIX}RANGE_PCT"]
    assert got.shape == (m,)
    assert np.all(got == 0.0)


def test_ath_copy_failure_still_writes_row():
    from unittest import mock

    from core.research.tau_panel import collect_tau_intraday_panel
    from core.signal.factors.alpha_th import inject_raw_ath

    bars, minutes = _dated_bars_and_minutes(90, seed=47)
    with mock.patch(
        "core.research.tau_panel._copy_ath_gap_from_atc",
        side_effect=RuntimeError("ath copy down"),
    ):
        xs, _ys, dates, _metas = collect_tau_intraday_panel(
            bars,
            minutes,
            tau_hm="10:00",
            include_alpha_tc=True,
            include_alpha_th=True,
        )
    assert dates
    row = xs[-1]
    assert any(is_alpha_tc_raw_key(k) for k in row)
    assert any(str(k).startswith("raw_ath_") for k in row)
    day = dates[-1]
    i = next(i for i, bar in enumerate(bars) if bar["date"] == day)
    day_mins = [m for m in minutes if m["date"] == day]
    from core.research.tau_panel import price_at_tau_from_minutes

    px = price_at_tau_from_minutes(day_mins, trade_date=day, tau_hm="10:00")
    bare = {
        k: v
        for k, v in row.items()
        if not is_alpha_tc_raw_key(k) and not str(k).startswith("raw_ath_")
    }
    ath = dict(bare)
    inject_raw_ath(
        ath,
        bars[i - ALPHA_TC_PANEL_WINDOW : i],
        open_t=float(bars[i]["open"]),
        prev_close=float(bars[i - 1]["close"]),
        price_tau=float(px),
        minute_bars=day_mins,
        tau_hm="10:00",
        feats=bare,
    )
    got = {k: float(v) for k, v in row.items() if str(k).startswith("raw_ath_")}
    exp = {k: float(v) for k, v in ath.items() if str(k).startswith("raw_ath_")}
    assert set(got) == set(exp)


def test_bad_minute_clock_keeps_other_clocks():
    from unittest import mock

    from core.research.tau_panel import collect_tau_intraday_panel
    from core.signal import minute_tau_feats as feats

    day = "2026-08-28"
    daily = [
        {
            "date": f"2026-08-{10 + i:02d}",
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.2,
        }
        for i in range(6)
    ]
    daily[-1]["date"] = day
    minutes = [
        {
            "date": day,
            "datetime": f"{day} 09:35:00",
            "open": 10.0,
            "high": 10.2,
            "low": 9.9,
            "close": 10.1,
            "volume": 1000,
        },
        {
            "date": day,
            "datetime": f"{day} 10:00:00",
            "open": 10.1,
            "high": 10.4,
            "low": 10.0,
            "close": 10.3,
            "volume": 800,
        },
    ]

    real_pack = feats._tau_pack_from_bars

    def _boom(prefix, **kwargs):
        if prefix and feats._bar_hm(prefix[-1]) >= "1000":
            raise RuntimeError("bad clock")
        return real_pack(prefix, **kwargs)

    with mock.patch(
        "core.signal.minute_tau_feats._tau_pack_from_bars",
        side_effect=_boom,
    ):
        _xs, _ys, dates, metas = collect_tau_intraday_panel(
            daily,
            minutes,
            tau_grid=["09:30", "10:00"],
            min_history=5,
        )
    assert dates == [day]
    assert [m.get("tau") for m in metas] == ["09:30"]


def test_bad_horizon_keeps_row_and_other_heads():
    from unittest import mock

    from core.research.tau_panel import collect_tau_intraday_panel

    day = "2026-08-28"
    daily = [
        {
            "date": f"2026-08-{10 + i:02d}",
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.2,
        }
        for i in range(6)
    ]
    daily[-1]["date"] = day
    minutes = [
        {
            "date": day,
            "datetime": f"{day} 10:00:00",
            "open": 10.1,
            "high": 10.4,
            "low": 10.0,
            "close": 10.3,
            "volume": 800,
        }
    ]

    def _t30(*_a, **_k):
        raise RuntimeError("t30 down")

    def _t45(*_a, **_k):
        return ("10:15", 10.2)

    with mock.patch(
        "core.research.tau_panel.price_at_t30_mean_session",
        side_effect=_t30,
    ), mock.patch(
        "core.research.tau_panel.price_at_t45_mean_session",
        side_effect=_t45,
    ):
        xs, ys, dates, metas = collect_tau_intraday_panel(
            daily,
            minutes,
            tau_hm="10:00",
            min_history=5,
            label_heads=("t30", "t45"),
        )
    assert dates == [day]
    assert len(xs) == len(ys) == len(dates) == len(metas) == 1
    assert "price_tau30" not in metas[0]
    assert metas[0]["price_tau45"] == 10.2
    assert metas[0]["tau"] == "10:00"


def test_bad_minute_bar_does_not_drop_day():
    from unittest import mock

    from core.research.tau_panel import _minute_bar_day, collect_tau_intraday_panel

    day = "2026-08-28"
    daily = [
        {
            "date": f"2026-08-{10 + i:02d}",
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.2,
        }
        for i in range(6)
    ]
    daily[-1]["date"] = day
    minutes = [
        {"date": "boom", "datetime": "nope"},
        {
            "date": day,
            "datetime": f"{day} 10:00:00",
            "open": 10.1,
            "high": 10.4,
            "low": 10.0,
            "close": 10.3,
            "volume": 800,
        },
    ]
    real = _minute_bar_day

    def _day(b):
        if b.get("date") == "boom":
            raise RuntimeError("bad minute")
        return real(b)

    with mock.patch("core.research.tau_panel._minute_bar_day", side_effect=_day):
        _xs, _ys, dates, metas = collect_tau_intraday_panel(
            daily,
            minutes,
            tau_hm="10:00",
            min_history=5,
            label_heads=("t30",),
        )
    assert dates == [day]
    assert metas[0]["tau"] == "10:00"


def test_tau_compact_skips_failed_fold():
    from unittest import mock

    import numpy as np

    from core.research.panel_matrix import PanelMatrix, collect_tau_compact

    calls = {"n": 0}
    orig = PanelMatrix.append_block

    def _boom(self, arr):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("fold down")
        return orig(self, arr)

    def _job(item, *_args, **_kwargs):
        return (
            item.get("code"),
            np.zeros((4, 1), dtype=np.float64),
            [0.1, 0.1, 0.1, 0.1],
            ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
            [{}, {}, {}, {}],
            [{}, {}, {}, {}],
        )

    with mock.patch.object(PanelMatrix, "append_block", _boom), mock.patch(
        "core.research.panel_matrix._tau_stock_job",
        side_effect=_job,
    ):
        _x, _names, ys, _dates, _metas, n_stocks = collect_tau_compact(
            [
                {"code": "A", "bars": [{"date": "2024-01-02"}] * 20},
                {"code": "B", "bars": [{"date": "2024-01-02"}] * 20},
            ],
            ["gap_pct"],
            workers=1,
            include_alpha_tc=False,
        )
    assert n_stocks == 1
    assert len(ys) == 4


def test_tau_compact_skips_failed_stock():
    from unittest import mock

    import numpy as np

    from core.research.panel_matrix import collect_tau_compact

    def _job(item, *_args, **_kwargs):
        if item.get("code") == "BAD":
            raise RuntimeError("boom")
        return (
            "OK",
            np.zeros((4, 1), dtype=np.float64),
            [0.1, 0.1, 0.1, 0.1],
            ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
            [{}, {}, {}, {}],
            [{}, {}, {}, {}],
        )

    with mock.patch("core.research.panel_matrix._tau_stock_job", side_effect=_job):
        _x, _names, ys, _dates, _metas, n_stocks = collect_tau_compact(
            [
                {"code": "OK", "bars": [{"date": "2024-01-02"}] * 20},
                {"code": "BAD", "bars": [{"date": "2024-01-02"}] * 20},
            ],
            ["gap_pct"],
            workers=1,
            include_alpha_tc=False,
        )
    assert n_stocks == 1
    assert len(ys) == 4


def test_unparseable_bar_falls_back_to_inject():
    from core.research.tau_panel import collect_tau_open_panel

    bars = _unique_bars(100, seed=17)
    bars[40]["close"] = "bad"
    assert ohlcv_from_bars(bars) is None
    xs, _, dates, _ = collect_tau_open_panel(bars, include_alpha_tc=True)
    hist = ALPHA_TC_PANEL_WINDOW
    by_date = {d: row for d, row in zip(dates, xs)}
    for i in range(hist, len(bars)):
        day = str(bars[i]["date"])
        if day not in by_date:
            continue
        ref: dict = {}
        inject_raw_atc(
            ref,
            bars[i - hist : i],
            open_t=float(bars[i]["open"]),
            prev_close=float(bars[i - 1]["close"]),
            price_tau=float(bars[i]["open"]),
            tau_hm="open",
        )
        got = {
            k: float(v)
            for k, v in by_date[day].items()
            if is_alpha_tc_raw_key(k)
        }
        assert set(got) == set(ref), (i, set(got) ^ set(ref))


def test_alpha_tc_registered():
    assert "alpha_tc" in registered_factor_names()
    bars = _synth_bars(80)
    score, meta = compute_factor("alpha_tc", bars)
    assert score == 50.0
    assert meta.get("omit_sub_score") is True
    assert "GAP" in meta
    assert "PROC5" in meta
