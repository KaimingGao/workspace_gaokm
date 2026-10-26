"""alpha_th 因子单元测试（ŷ_τ*：≤T−1 + 今开 + ≤τ 分钟；轻量 GAP/YK）。"""
from __future__ import annotations

import numpy as np

from core.signal.factors.alpha_th import (
    ALPHA_TH_FIELD_NAMES,
    ALPHA_TH_MIN_BARS,
    ALPHA_TH_RAW_PREFIX,
    alpha_th_fit_features_for_head,
    ath_raw_key,
    inject_raw_ath,
    is_alpha_th_raw_key,
    promote_ath_head_aliases,
    raw_ath_from_bars,
    score_alpha_th,
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

def test_alpha_th_field_count():
    assert len(ALPHA_TH_FIELD_NAMES) == 63
    assert len(set(ALPHA_TH_FIELD_NAMES)) == 63

def test_alpha_th_feat_labels_cover_fields():
    from core.signal.factors.alpha_th import ATH_FEAT_LABELS, ath_raw_key
    from core.signal.factors.meta.registry import factor_label

    for bare in ALPHA_TH_FIELD_NAMES:
        key = ath_raw_key(bare)
        assert key in ATH_FEAT_LABELS, bare
        lab = ATH_FEAT_LABELS[key]
        assert lab and lab != key
        assert factor_label(key) == lab
    assert factor_label("raw_ath_NEAR_UPLIMIT") == "距涨停"
    assert factor_label("raw_ath_CLOSE0") == "τ价/昨收"

def test_alpha_th_key_fields_listed():
    assert "GAP" in ALPHA_TH_FIELD_NAMES
    assert "CLOSE0" in ALPHA_TH_FIELD_NAMES
    assert "RET_OT" in ALPHA_TH_FIELD_NAMES
    assert "YBODY" in ALPHA_TH_FIELD_NAMES
    assert "MOM3_C" in ALPHA_TH_FIELD_NAMES
    assert "RET_LAST_30" in ALPHA_TH_FIELD_NAMES
    assert "H_LAG1" in ALPHA_TH_FIELD_NAMES
    assert "H_MA5" in ALPHA_TH_FIELD_NAMES
    assert "PROC5" not in ALPHA_TH_FIELD_NAMES
    assert "TC_LAG1" not in ALPHA_TH_FIELD_NAMES

def test_alpha_th_fit_features_per_head():
    for head in ("t30", "t45", "t60", "t75", "t90"):
        feats = alpha_th_fit_features_for_head(head)
        assert all(is_alpha_th_raw_key(k) for k in feats)
        assert ath_raw_key("GAP") in feats
        assert ath_raw_key("H_LAG1") in feats
        num = head[1:]
        assert ath_raw_key(f"RET_LAST_{num}") in feats
        assert ath_raw_key("RET_LAST_H") in feats

def test_raw_ath_from_bars_helper():
    assert raw_ath_from_bars(_synth_bars(10)) == {}
    bars = _synth_bars(40)
    hist = bars[:-1]
    open_t = float(bars[-1]["open"])
    out = raw_ath_from_bars(hist, open_t=open_t, tau_hm="open")
    assert out
    assert all(is_alpha_th_raw_key(k) for k in out)
    assert out.get(ath_raw_key("GAP")) is not None
    assert out.get(ath_raw_key("YBODY")) is not None
    # 不写全量 ROLL / TC_LAG
    assert not any(k.startswith(f"{ALPHA_TH_RAW_PREFIX}PROC") for k in out)
    assert ath_raw_key("TC_LAG1") not in out

def test_inject_promotes_seq_and_head_alias():
    dest: dict = {
        "ret_last_30m": 1.2,
        "ret_last_5m": 0.3,
        "t30_lag1": -0.5,
        "t30_ma5": 0.1,
    }
    inject_raw_ath(dest, None, head="t30", feats=dest)
    assert dest[ath_raw_key("RET_LAST_30")] == 1.2
    assert dest[ath_raw_key("RET_LAST_5M")] == 0.3
    assert dest[ath_raw_key("RET_LAST_H")] == 1.2
    assert dest[ath_raw_key("H_LAG1")] == -0.5
    assert dest[ath_raw_key("H_MA5")] == 0.1

def test_already_relabeled_matrix_keeps_head_lags():
    """hz_* 跳过整段 relabel 时，仍把同钟昨日 y 写成 raw_ath_H_LAG1。"""
    from core.research.tau_panel import _HORIZON_SPECS, ensure_matrix_horizon_lags
    from core.signal.factors.alpha_th import stamp_ath_on_panels

    panel = {
        "already_relabeled": True,
        "X": np.array([[0.0], [0.0]], dtype=np.float64),
        "x_names": ["raw_ath_RET_LAST_75"],
        "xs": [],
        "ys": [1.25, -0.4],
        "dates": ["2026-10-13", "2026-10-14"],
        "metas": [{"tau": "10:00"}, {"tau": "10:00"}],
    }
    ensure_matrix_horizon_lags(panel, _HORIZON_SPECS["t75"])
    stamp_ath_on_panels([panel], "t75")
    names = list(panel["x_names"])
    col = panel["X"][:, names.index(ath_raw_key("H_LAG1"))]
    assert names.index("t75_lag1") >= 0
    assert not np.isfinite(col[0])
    assert col[1] == np.float64(1.25)


def test_promote_ath_head_aliases_t60():
    row = {"ret_last_60m": 2.0, "t60_lag1": 0.4, "t60_ma5": 0.2}
    promote_ath_head_aliases(row, "t60")
    assert row[ath_raw_key("RET_LAST_H")] == 2.0
    assert row[ath_raw_key("H_LAG1")] == 0.4

def test_score_alpha_th_omit():
    bars = _synth_bars(40)
    hist = bars[:-1]
    open_t = float(bars[-1]["open"])
    score, meta = score_alpha_th(hist, open_t=open_t, tau_hm="open")
    assert score == 50.0
    assert meta.get("omit_sub_score") is True
    assert meta.get("alpha_th_anchor") == "price_tau"
    assert "GAP" in meta

def test_alpha_th_min_bars():
    assert ALPHA_TH_MIN_BARS == 22

def test_alpha_th_registered():
    assert "alpha_th" in registered_factor_names()
    bars = _synth_bars(40)
    hist = bars[:-1]
    open_t = float(bars[-1]["open"])
    score, meta = compute_factor(
        "alpha_th", hist, open_t=open_t, tau_hm="open"
    )
    assert score == 50.0
    assert meta.get("omit_sub_score") is True

def test_alpha_th_report_meta():
    from core.signal.factors.alpha_th import alpha_th_report_meta

    m = alpha_th_report_meta(n_alpha_th_features=43)
    assert m["feature_pack"] == "alpha_τh"
    assert m["feature_pack_key"] == "alpha_th"
    assert m["feature_pack_formula"] == "z_τh_* ∪ raw_ath_*"
    assert m["include_alpha_th"] is True
    assert m["n_alpha_th_features"] == 43

def test_horizon_config_includes_ath():
    from core.research.horizon_ridge import _horizon_ridge_config

    from core.research.horizon_ridge import T30_EXTRA_FIT_FEATURES

    cfg = _horizon_ridge_config("t30")
    assert cfg["schema"] == "t30_ridge_v4"
    assert cfg["feature_pack"] == "alpha_τh"
    assert cfg["feature_pack_formula"] == "z_τh_* ∪ raw_ath_* ∪ z_τc path ∪ raw_atc 5/20"
    ath = cfg["ath_features"]
    assert ath
    assert all(str(k).startswith("raw_ath_") for k in ath)
    zf = list(cfg["z_features"])
    assert any(str(k).startswith("raw_ath_") for k in zf)
    assert any(str(k).startswith("z_τh_") for k in zf)
    assert len(T30_EXTRA_FIT_FEATURES) == 45
    assert len(zf) == 100
    for name in T30_EXTRA_FIT_FEATURES:
        assert name in zf
    for head in ("t45", "t60", "t75", "t90"):
        other = list(_horizon_ridge_config(head)["z_features"])
        assert len(other) == 100
        assert "z_τc_path_sign" in other
        assert "raw_atc_PRANK5" in other

def test_inject_raw_ath_live_before_predict():
    from core.t0.score_policy import _inject_raw_ath_live

    bars = _synth_bars(40)
    hist = bars[:-1]
    open_t = float(bars[-1]["open"])
    item = {
        "date": "2024-06-15",
        "stock_code": "000001.SZ",
        "tau": "10:00",
        "price_tau": open_t * 1.01,
        "day_bar": {"prev_close": float(hist[-1]["close"]), "open": open_t},
        "features_tau": {
            "ret_last_30m": 0.8,
            "ret_last_5m": 0.2,
            "t30_lag1": -0.3,
            "t30_ma5": 0.1,
            "gap_pct": 1.5,
            "tau_lag1": 0.4,
            "price_tau": open_t * 1.01,
        },
    }
    feats = dict(item["features_tau"])
    out = _inject_raw_ath_live(
        feats, item, "t30", tau_hm="10:00", hist_bars=hist
    )
    assert out.get(ath_raw_key("RET_LAST_30")) == 0.8
    assert out.get(ath_raw_key("RET_LAST_H")) == 0.8
    assert out.get(ath_raw_key("H_LAG1")) == -0.3
    assert out.get(ath_raw_key("GAP")) is not None
    assert item["features_tau"].get(ath_raw_key("GAP")) is not None
    # live stamp：裸名 / z_τc_* → z_τh_*（拟合键）
    assert out.get("z_τh_gap_pct") == 1.5
    assert out.get("z_τh_tau_lag1") == 0.4
    assert item["features_tau"].get("z_τh_gap_pct") == 1.5


def _calendar_hist(n: int, end: str, *, last: dict, prev: dict) -> list:
    """n 根日 K，末两根覆盖 OHLC。日期从 end 往前按自然日排。"""
    from datetime import date, timedelta

    y, m, d = (int(x) for x in end.split("-"))
    end_d = date(y, m, d)
    bars = []
    for i in range(n):
        day = (end_d - timedelta(days=(n - 1 - i))).isoformat()
        bar = {
            "date": day,
            "trade_date": day,
            "open": 60.0,
            "high": 61.0,
            "low": 59.0,
            "close": 60.0,
            "volume": 1000.0,
        }
        if i == n - 2:
            bar.update(prev)
        if i == n - 1:
            bar.update(last)
        bar["date"] = str(bar.get("date") or day)[:10]
        bar["trade_date"] = bar["date"]
        bars.append(bar)
    return bars


def test_raw_ath_refuses_lagged_hist_when_session_is_later():
    """hist 停在 T−1、分钟已是 T、没传今开：不出 GAP，不能退成上一交易日。"""
    hist = _calendar_hist(
        24,
        "2026-10-09",
        prev={
            "date": "2026-10-08",
            "open": 67.02,
            "high": 68.18,
            "low": 62.88,
            "close": 64.50,
        },
        last={
            "date": "2026-10-09",
            "open": 63.54,
            "high": 64.15,
            "low": 58.60,
            "close": 61.72,
        },
    )
    lagged = 63.54 / 64.50 - 1.0
    out = raw_ath_from_bars(
        hist,
        price_tau=60.69,
        tau_hm="09:35",
        minute_bars=[
            {
                "datetime": "2026-10-12 09:35:00",
                "date": "2026-10-12",
                "open": 61.38,
                "high": 61.38,
                "low": 60.38,
                "close": 60.69,
                "volume": 1.0,
            }
        ],
    )
    assert ath_raw_key("GAP") not in out
    assert out.get(ath_raw_key("GAP")) != lagged


def test_raw_ath_uses_today_open_not_previous_bar():
    """航天 2026-10-12 09:35：GAP = 今开/昨收−1，现价未破昨低。"""
    hist = _calendar_hist(
        24,
        "2026-10-09",
        prev={
            "date": "2026-10-08",
            "open": 67.02,
            "high": 68.18,
            "low": 62.88,
            "close": 64.50,
        },
        last={
            "date": "2026-10-09",
            "open": 63.54,
            "high": 64.15,
            "low": 58.60,
            "close": 61.72,
        },
    )
    out = raw_ath_from_bars(
        hist,
        open_t=61.38,
        prev_close=61.72,
        price_tau=60.69,
        tau_hm="09:35",
        minute_bars=[
            {
                "datetime": "2026-10-12 09:35:00",
                "date": "2026-10-12",
                "open": 61.38,
                "high": 61.38,
                "low": 60.38,
                "close": 60.69,
                "volume": 1.0,
            }
        ],
    )
    gap = 61.38 / 61.72 - 1.0
    assert abs(out[ath_raw_key("GAP")] - gap) < 1e-12
    assert abs(out[ath_raw_key("RET_OT")] - (60.69 / 61.38 - 1.0)) < 1e-12
    assert out[ath_raw_key("BELOW_YLOW")] == 0.0
    assert abs(out[ath_raw_key("YLOW0")] - (58.60 / 60.69)) < 1e-12


def test_raw_ath_peels_last_bar_when_it_is_the_session():
    """末根日期等于会话日、未传今开：剥末根，今开用这根的开盘。"""
    hist = _calendar_hist(
        24,
        "2026-10-12",
        prev={"date": "2026-10-09", "close": 61.72, "low": 58.60, "open": 63.54},
        last={
            "date": "2026-10-12",
            "open": 61.38,
            "high": 61.38,
            "low": 50.0,
            "close": 99.0,
        },
    )
    out = raw_ath_from_bars(
        hist,
        price_tau=60.69,
        tau_hm="09:35",
        minute_bars=[{"date": "2026-10-12", "close": 60.69, "open": 61.38}],
    )
    assert abs(out[ath_raw_key("GAP")] - (61.38 / 61.72 - 1.0)) < 1e-12
    assert out[ath_raw_key("BELOW_YLOW")] == 0.0


def test_raw_ath_legacy_peel_without_session_date():
    """没有分钟/quote 日期时，仍把 hist 末根当今天（调用方把当日 K 放在末尾）。"""
    hist = _calendar_hist(
        24,
        "2026-10-12",
        prev={"date": "2026-10-09", "close": 61.72, "low": 58.60},
        last={"date": "2026-10-12", "open": 61.38, "close": 99.0, "low": 50.0},
    )
    out = raw_ath_from_bars(hist, price_tau=60.69, tau_hm="09:35")
    assert abs(out[ath_raw_key("GAP")] - (61.38 / 61.72 - 1.0)) < 1e-12


def test_prefix_rescore_stamps_day_bar_for_ath(monkeypatch):
    from core.t0 import score_policy as sp

    seen = {}

    def _resolve(*_a, **_k):
        return {
            "y_tau": 0.1,
            "stock_code": "002025",
            "features_tau": {"gap_pct": 0.2},
        }

    def _attach(item, **kwargs):
        seen["day_bar"] = dict(item.get("day_bar") or {})
        seen["hist_date"] = str((kwargs.get("hist_bars") or [{}])[-1].get("date") or "")

    monkeypatch.setattr(sp, "resolve_scores_for_code", _resolve)
    monkeypatch.setattr(sp, "_attach_y_path_to_item", _attach)
    sp._PREFIX_RESCORE_CACHE.clear()
    day = {
        "date": "2026-10-12",
        "open": 61.38,
        "prev_close": 61.72,
        "low": 58.22,
        "high": 61.38,
        "close": 58.40,
    }
    hist = [{"date": "2026-10-09", "open": 63.54, "high": 64.15, "low": 58.60, "close": 61.72}]
    prefix = [
        {
            "datetime": "2026-10-12 09:35:00",
            "date": "2026-10-12",
            "open": 61.38,
            "high": 61.38,
            "low": 60.38,
            "close": 60.69,
            "volume": 1.0,
        }
    ]
    out = sp.rescore_scores_at_fixed_prefix(
        stock_code="002025",
        minute_prefix=prefix,
        day_bar=day,
        hist_bars=hist,
        include_tau_horizons=True,
    )
    assert seen["day_bar"]["open"] == 61.38
    assert seen["day_bar"]["date"] == "2026-10-12"
    assert seen["hist_date"] == "2026-10-09"
    assert "day_bar" not in out


def test_raw_ath_refuses_undated_minute_prefix():
    """有分钟根但读不出会话日：不剥末根。"""
    hist = _calendar_hist(
        24,
        "2026-10-12",
        prev={"date": "2026-10-09", "close": 61.72, "low": 58.60},
        last={"date": "2026-10-12", "open": 61.38, "close": 99.0, "low": 50.0},
    )
    out = raw_ath_from_bars(
        hist,
        price_tau=60.69,
        tau_hm="09:35",
        minute_bars=[{"open": 61.38, "close": 60.69}],
    )
    assert ath_raw_key("GAP") not in out


def test_inject_raw_ath_uses_item_open_and_trade_day():
    """没有 day_bar 时，条目上的今开和交易日仍按 T 对齐，不退到上一根。"""
    from core.t0.score_policy import _inject_raw_ath_live

    hist = _calendar_hist(
        24,
        "2026-10-09",
        prev={
            "date": "2026-10-08",
            "open": 67.02,
            "high": 68.18,
            "low": 62.88,
            "close": 64.50,
        },
        last={
            "date": "2026-10-09",
            "open": 63.54,
            "high": 64.15,
            "low": 58.60,
            "close": 61.72,
        },
    )
    item = {
        "trade_day": "2026-10-12",
        "open_t": 61.38,
        "stock_code": "002025",
        "tau": "09:35",
        "features_tau": {"price_tau": 60.69},
    }
    out = _inject_raw_ath_live(
        dict(item["features_tau"]), item, "t30", tau_hm="09:35", hist_bars=hist
    )
    assert abs(out[ath_raw_key("GAP")] - (61.38 / 61.72 - 1.0)) < 1e-12
    assert out[ath_raw_key("BELOW_YLOW")] == 0.0

    bare = {
        "trade_day": "2026-10-12",
        "stock_code": "002025",
        "features_tau": {"price_tau": 60.69},
    }
    missed = _inject_raw_ath_live(
        dict(bare["features_tau"]), bare, "t30", tau_hm="09:35", hist_bars=hist
    )
    assert ath_raw_key("GAP") not in missed
