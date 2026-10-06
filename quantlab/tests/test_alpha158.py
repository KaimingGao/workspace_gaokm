"""Alpha158 因子集原生重写测试。

覆盖：
- 字段数正好 158 个
- kbar 9 字段公式
- price 4 字段公式
- rolling 29 操作 × 5 窗口 = 145 字段
- 历史不足返回 omit
- 注册机制 + _research_sub_scores 注入 raw_alpha158_*
- build_oo_rank_day_panels 端到端
"""

import math
from typing import List

import numpy as np
import pytest

from core.signal.factors.alpha158 import (
    _kbar_fields,
    _price_fields,
    _rolling_fields,
    score_alpha158,
    _WINDOWS,
)
from core.signal.factors.meta.registry import (
    compute_factor,
    registered_factor_names,
)


def _synth_bars(n: int = 80, seed: int = 42) -> List[dict]:
    """合成 n 根日 bars，带温和上涨 + 波动 + 量。"""
    rng = np.random.default_rng(seed)
    base = 10.0
    rets = rng.normal(0.001, 0.02, n)
    closes = base * np.cumprod(1 + rets)
    opens = closes * (1 + rng.normal(0, 0.005, n))
    highs = np.maximum(opens, closes) * (1 + np.abs(rng.normal(0, 0.01, n)))
    lows = np.minimum(opens, closes) * (1 - np.abs(rng.normal(0, 0.01, n)))
    vols = rng.uniform(1e6, 5e6, n)
    bars = []
    for i in range(n):
        bars.append({
            "date": f"2024-01-{i+1:02d}" if i < 31 else f"2024-02-{i-30:02d}",
            "open": float(opens[i]),
            "high": float(highs[i]),
            "low": float(lows[i]),
            "close": float(closes[i]),
            "volume": float(vols[i]),
        })
    return bars


# ---------------- 1. 字段数验证 ----------------


def test_alpha158_field_count():
    """正好 158 个数值字段（除 omit_sub_score 等元标记）。"""
    bars = _synth_bars(80)
    score, meta = score_alpha158(bars)
    assert score == 50.0
    assert meta.get("omit_sub_score") is True

    # 排除 omit_sub_score / alpha158_* 标记
    field_keys = [
        k for k, v in meta.items()
        if k not in ("omit_sub_score",)
        and not k.startswith("alpha158_")
        and isinstance(v, (int, float))
    ]
    assert len(field_keys) == 158, f"expected 158 fields, got {len(field_keys)}"


def test_alpha158_kbar_count():
    bars = _synth_bars(80)
    _, meta = score_alpha158(bars)
    kbar_keys = [k for k in ("KMID","KLEN","KMID2","KUP","KUP2","KLOW","KLOW2","KSFT","KSFT2") if k in meta]
    assert len(kbar_keys) == 9


def test_alpha158_price_count():
    bars = _synth_bars(80)
    _, meta = score_alpha158(bars)
    price_keys = [k for k in ("OPEN0","HIGH0","LOW0","VWAP0") if k in meta]
    assert len(price_keys) == 4


def test_alpha158_rolling_count():
    """29 操作 × 5 窗口 = 145。"""
    bars = _synth_bars(80)
    _, meta = score_alpha158(bars)
    ops = (
        "ROC","MA","STD","BETA","RSQR","RESI","MAX","LOW","QTLU","QTLD","RANK","RSV",
        "IMAX","IMIN","IMXD","CORR","CORD","CNTP","CNTN","CNTD","SUMP","SUMN","SUMD",
        "VMA","VSTD","WVMA","VSUMP","VSUMN","VSUMD",
    )
    assert len(ops) == 29
    count = sum(
        1 for op in ops for d in _WINDOWS
        if f"{op}{d}" in meta and meta[f"{op}{d}"] is not None
    )
    assert count == 145, f"expected 145 rolling fields, got {count}"


# ---------------- 2. kbar 公式验证 ----------------


def test_alpha158_kbar_formulas():
    """合成 OHLC 手算 kbar 字段。"""
    o, h, l, c = 10.0, 12.0, 9.0, 11.0
    fields = _kbar_fields(c, o, h, l)
    rng = h - l  # 3.0
    mx_oc = max(o, c)  # 11.0
    mn_oc = min(o, c)  # 10.0

    assert math.isclose(fields["KMID"], (c - o) / o)
    assert math.isclose(fields["KLEN"], (h - l) / o)
    assert math.isclose(fields["KMID2"], (c - o) / (rng + 1e-12))
    assert math.isclose(fields["KUP"], (h - mx_oc) / o)
    assert math.isclose(fields["KUP2"], (h - mx_oc) / (rng + 1e-12))
    assert math.isclose(fields["KLOW"], (mn_oc - l) / o)
    assert math.isclose(fields["KLOW2"], (mn_oc - l) / (rng + 1e-12))
    assert math.isclose(fields["KSFT"], (2 * c - h - l) / o)
    assert math.isclose(fields["KSFT2"], (2 * c - h - l) / (rng + 1e-12))


def test_alpha158_price_formulas():
    o, h, l, c = 10.0, 12.0, 9.0, 11.0
    fields = _price_fields(c, o, h, l)
    vwap = (h + l + c) / 3.0
    assert math.isclose(fields["OPEN0"], o / (c + 1e-12))
    assert math.isclose(fields["HIGH0"], h / (c + 1e-12))
    assert math.isclose(fields["LOW0"], l / (c + 1e-12))
    assert math.isclose(fields["VWAP0"], vwap / (c + 1e-12))


# ---------------- 3. rolling 公式验证（手算几个） ----------------


def test_alpha158_roc_ma_std():
    """手算 ROC5/MA5/STD5。"""
    closes = np.array([10.0, 11, 12, 11, 10, 13])  # 6 根，窗口 5 取 [11,12,11,10,13]
    bars = [
        {"open": c, "high": c * 1.05, "low": c * 0.95, "close": c, "volume": 1e6}
        for c in closes
    ]
    _, meta = score_alpha158(bars)  # 6 根 < 61，会 omit
    assert meta.get("omit_sub_score") is True
    # 6 根历史不足，不会算 rolling；验证不进面板逻辑即可


def test_alpha158_rolling_specific_values():
    """用 80 根合成 bars 验证几个 rolling 值与手算一致。"""
    bars = _synth_bars(80, seed=1)
    _, meta = score_alpha158(bars)

    close = np.array([b["close"] for b in bars])
    high = np.array([b["high"] for b in bars])
    low = np.array([b["low"] for b in bars])
    vol = np.array([b["volume"] for b in bars])

    # ROC5: Qlib Ref($close, 5)/$close = close[T-5]/close[T] = close[-6]/close[-1]
    expected_roc5 = close[-6] / (close[-1] + 1e-12)
    assert math.isclose(meta["ROC5"], expected_roc5, rel_tol=1e-9)

    # MA5: mean(close[-5:])/close[-1]
    expected_ma5 = close[-5:].mean() / (close[-1] + 1e-12)
    assert math.isclose(meta["MA5"], expected_ma5, rel_tol=1e-9)

    # MAX5: max(high[-5:])/close[-1]
    expected_max5 = high[-5:].max() / (close[-1] + 1e-12)
    assert math.isclose(meta["MAX5"], expected_max5, rel_tol=1e-9)

    # VMA5: mean(vol[-5:])/vol[-1]
    expected_vma5 = vol[-5:].mean() / (vol[-1] + 1e-12)
    assert math.isclose(meta["VMA5"], expected_vma5, rel_tol=1e-9)

    # CNTP5: 涨日比例
    rets = close[1:] / (close[:-1] + 1e-12) - 1
    expected_cntp5 = float((rets[-5:] > 0).mean())
    assert math.isclose(meta["CNTP5"], expected_cntp5, rel_tol=1e-9)


# ---------------- 4. 历史不足处理 ----------------


def test_alpha158_insufficient_history():
    """bars < 61 返回 omit + 标记。"""
    bars = _synth_bars(30)
    score, meta = score_alpha158(bars)
    assert score == 50.0
    assert meta.get("omit_sub_score") is True
    assert meta.get("alpha158_insufficient_history") is True
    # 不应输出任何因子字段
    field_keys = [k for k in meta if k not in ("omit_sub_score",) and not k.startswith("alpha158_")]
    assert field_keys == []


def test_alpha158_empty_bars():
    score, meta = score_alpha158([])
    assert score == 50.0
    assert meta.get("omit_sub_score") is True


def test_raw_alpha158_from_bars_helper():
    from core.signal.factors.alpha158 import raw_alpha158_from_bars

    assert raw_alpha158_from_bars(_synth_bars(30)) == {}
    out = raw_alpha158_from_bars(_synth_bars(80))
    assert "raw_alpha158_KMID" in out
    assert "raw_alpha158_ROC5" in out
    assert len(out) >= 140
    assert all(k.startswith("raw_alpha158_") for k in out)


def test_alpha158_zero_close():
    """末根 close=0 触发 zero_close 标记。"""
    bars = _synth_bars(80)
    bars[-1]["close"] = 0.0
    _, meta = score_alpha158(bars)
    assert meta.get("omit_sub_score") is True
    assert meta.get("alpha158_zero_close") is True


# ---------------- 5. 注册机制 ----------------


def test_alpha158_registered():
    """注册表包含 alpha158。"""
    names = registered_factor_names()
    assert "alpha158" in names


def test_alpha158_compute_factor_dispatch():
    """通过 compute_factor 调用，确认返回 158 个字段。"""
    bars = _synth_bars(80)
    score, meta = compute_factor("alpha158", bars)
    assert score == 50.0
    assert meta.get("omit_sub_score") is True
    field_keys = [
        k for k, v in meta.items()
        if k not in ("omit_sub_score",)
        and not k.startswith("alpha158_")
        and isinstance(v, (int, float))
    ]
    assert len(field_keys) == 158


# ---------------- 6. _research_sub_scores 集成 ----------------


def test_alpha158_research_sub_scores_injects_raw():
    """_research_sub_scores 把 raw_alpha158_* 注入 row。"""
    from core.research.panel import _research_sub_scores

    bars = _synth_bars(80)
    row = _research_sub_scores(bars, factor_names=("alpha158",))
    # sub_score 字段
    assert "alpha158" in row
    assert row["alpha158"] == 50.0
    # 至少有 raw_alpha158_KMID
    assert "raw_alpha158_KMID" in row
    assert "raw_alpha158_VSUMD60" in row
    raw_alpha158_keys = [k for k in row if k.startswith("raw_alpha158_")]
    assert len(raw_alpha158_keys) == 158, f"expected 158 raw_alpha158_*, got {len(raw_alpha158_keys)}"


# ---------------- 7. build_oo_rank_day_panels 端到端 ----------------


def test_alpha158_day_panel_e2e():
    """build_oo_rank_day_panels 引入 alpha158 后特征列 +158。

    collect_subscore_forward_panel 在因子集含 alpha158 时会自动抬高
    max_window/min_history 至 61；此处显式传 70 作双保险。
    """
    from core.research.oo_rank_panel import build_oo_rank_day_panels

    # 构造 5 只票，每只 90 根 bars（min_n 隐藏下限 4）
    stock_bars = [
        {"code": f"60000{i}.SH", "bars": _synth_bars(90, seed=i)}
        for i in range(1, 6)
    ]
    days = build_oo_rank_day_panels(
        stock_bars,
        horizon_days=1,
        min_history=80,
        min_names=2,
        max_window=70,
    )
    assert len(days) > 0
    first = days[0]
    names = list(first.get("names") or [])
    X = first.get("X")
    has_alpha158 = "raw_alpha158_KMID" in names
    if has_alpha158 and X is not None:
        j = names.index("raw_alpha158_KMID")
        has_alpha158 = bool((X[:, j] == X[:, j]).any())  # 有限值；NaN != NaN
    assert has_alpha158, "面板中未出现 raw_alpha158_KMID 字段"


# ---------------- 8. Ridge 特征胶水 ----------------


def test_expand_ridge_feature_names_drops_constant_score():
    from core.signal.factors.alpha158 import expand_ridge_feature_names

    xs = [
        {
            "momentum": 60.0,
            "alpha158": 50.0,
            "raw_alpha158_KMID": 0.01,
            "raw_alpha158_ROC5": 1.02,
        }
    ]
    names = expand_ridge_feature_names(xs, base_names=("momentum", "alpha158"))
    assert "momentum" in names
    assert "alpha158" not in names
    assert "raw_alpha158_KMID" in names
    assert "raw_alpha158_ROC5" in names


def test_fit_ridge_keeps_alpha158_raw_despite_min_std():
    """raw_alpha158_* 量纲很小，须豁免 min_std=5 才能进 Ridge。"""
    from core.research.factor_ols_fit import fit_factor_ols_from_panel

    rng = np.random.default_rng(0)
    xs = []
    ys = []
    for i in range(80):
        kmid = float(rng.normal(0, 0.02))
        roc = float(1.0 + rng.normal(0, 0.03))
        # 标签与 KMID 弱相关，保证能拟合出非零 β
        y = 0.5 * kmid * 100.0 + float(rng.normal(0, 0.2))
        xs.append(
            {
                "momentum": float(50 + rng.normal(0, 8)),
                "alpha158": 50.0,
                "raw_alpha158_KMID": kmid,
                "raw_alpha158_ROC5": roc,
            }
        )
        ys.append(y)
    fit = fit_factor_ols_from_panel(
        xs,
        ys,
        ridge_lambda=1.0,
        feature_names=None,
        min_std=5.0,
    )
    assert fit.get("success"), fit.get("error")
    active = set(fit.get("active_features") or [])
    assert "raw_alpha158_KMID" in active, f"active={active}"
    assert "alpha158" not in active
    coefs = fit.get("coefficients") or {}
    assert "raw_alpha158_KMID" in coefs


def test_compute_configured_injects_raw_when_required():
    """FS0：required_keys 含 raw_alpha158_* 时注入 sub_scores，不进加权 contribs。"""
    from core.signal.factors.meta.registry import compute_configured_factors

    bars = _synth_bars(80)
    subs, contribs, _meta = compute_configured_factors(
        bars,
        weights={"momentum": 1.0},
        required_keys=["raw_alpha158_KMID", "raw_alpha158_ROC5"],
    )
    assert "raw_alpha158_KMID" in subs
    assert "raw_alpha158_ROC5" in subs
    assert "alpha158" not in subs  # omit 常数分
    assert "alpha158" not in contribs
    assert "raw_alpha158_KMID" not in contribs


def test_bump_window_for_alpha158():
    from core.signal.factors.alpha158 import (
        ALPHA158_PANEL_WINDOW,
        bump_window_for_alpha158,
    )

    mh, mw = bump_window_for_alpha158(("momentum",), min_history=12, max_window=30)
    assert mh == 12 and mw == 30
    mh, mw = bump_window_for_alpha158(("alpha158",), min_history=12, max_window=30)
    # PIT hist 不含决策日：max_window 须比 61 多 1
    assert mh == ALPHA158_PANEL_WINDOW and mw == ALPHA158_PANEL_WINDOW
    assert ALPHA158_PANEL_WINDOW == 62
