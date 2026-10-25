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

    cfg = _horizon_ridge_config("t30")
    assert cfg["schema"] == "t30_ridge_v4"
    assert cfg["feature_pack"] == "alpha_τh"
    assert cfg["feature_pack_formula"] == "z_τh_* ∪ raw_ath_*"
    ath = cfg["ath_features"]
    assert ath
    assert all(str(k).startswith("raw_ath_") for k in ath)
    assert any(str(k).startswith("raw_ath_") for k in cfg["z_features"])
    assert any(str(k).startswith("z_τh_") for k in cfg["z_features"])
    assert all(
        str(k).startswith("z_τh_") or str(k).startswith("raw_ath_")
        for k in cfg["z_features"]
    )

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
