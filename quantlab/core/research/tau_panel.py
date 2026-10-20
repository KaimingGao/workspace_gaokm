"""ŷ_τ 训练面板：y = close[T]/price[τ]-1（τ→close 剩余收益）；特征 = 开盘 Z + 分钟路径。

无未来函数：决策在 τ，标签为 τ→收盘。open 时钟 price[τ]=open，自动退化为 close/open-1。
另加 PIT 历史真实 close/open−1（tau_lag1 / tau_ma5，不含当日；τ=open 时即 ŷ_τc）。
"""


import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)
from statistics import median, stdev
from typing import Any, Collection, Dict, List, Mapping, Optional, Sequence, Tuple

GAP_ATR_WINDOW = 14
GAP_ATR_CLIP = 10.0
_SECTOR_REL_MIN_N = 3
LABEL_LAG_WINDOW = 5
# tau_std5 / yest_gap 曾入模，做 T 回测变差后撤回；键真源在 yhat.keys
from core.yhat.keys import TAU_LAG_FEATURES, TAU_LAG_FEAT_LABELS  # noqa: F401 — 对外 re-export
from core.signal.minute_tau_grid import (
    DEFAULT_MINUTE_TAU_GRID,
    HORIZON_T30_MIN,
    T30_LABEL_OFFSETS,
    T45_LABEL_OFFSETS,
    T60_LABEL_OFFSETS,
    T75_LABEL_OFFSETS,
    T90_LABEL_OFFSETS,
    add_session_minutes,
)


# ---------------------------------------------------------------------------
# HorizonSpec：t30/45/60/75/90 五档参数化配置
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HorizonSpec:
    """单个 horizon 的全部参数化配置。"""

    tag: str  # "t30" / "t45" / ...
    horizon_min: int  # 30 / 45 / ...
    offsets: tuple  # T30_LABEL_OFFSETS 等
    ret_key: str  # "ret_last_30m"
    cs_ret_key: str  # "sector_ret_last_30m"
    cs_rel_key: str  # "ret_last_30m_vs_sector"
    crosses_lunch_key: str  # "crosses_lunch" / "crosses_lunch_45" 等
    vol_key: str  # "vol_last_30m_vs_avg"
    lag1_key: str  # "t30_lag1"
    ma_key: str  # "t30_ma5"
    y_key: str  # "y_t30"
    y_label: str  # "y_τ30"
    px_key: str  # "price_tau30"
    tau_plus_key: str  # "tau_plus_30"
    label_desc: str  # "τ⊕25/30/35" 等

    @property
    def lag_features(self) -> tuple:
        return (self.lag1_key, self.ma_key)

    @property
    def lag_feat_labels(self) -> dict:
        return {
            self.lag1_key: f"昨同钟真实 {self.label_desc}均 %",
            self.ma_key: f"近5日同钟真实 {self.label_desc}均 %",
        }

    @property
    def seq_cs_keys(self) -> tuple:
        return (self.cs_ret_key, self.cs_rel_key)

    @property
    def seq_features(self) -> tuple:
        return (
            "ret_last_5m",
            self.ret_key,
            "session_elapsed",
            "session_remain",
            self.crosses_lunch_key,
            "session_vwap_dev",
            self.vol_key,
        ) + self.seq_cs_keys + self.lag_features


_HORIZON_SPECS: Dict[str, HorizonSpec] = {
    "t30": HorizonSpec(
        tag="t30",
        horizon_min=30,
        offsets=T30_LABEL_OFFSETS,
        ret_key="ret_last_30m",
        cs_ret_key="sector_ret_last_30m",
        cs_rel_key="ret_last_30m_vs_sector",
        crosses_lunch_key="crosses_lunch",
        vol_key="vol_last_30m_vs_avg",
        lag1_key="t30_lag1",
        ma_key="t30_ma5",
        y_key="y_t30",
        y_label="y_τ30",
        px_key="price_tau30",
        tau_plus_key="tau_plus_30",
        label_desc="τ⊕25/30/35",
    ),
    "t45": HorizonSpec(
        tag="t45",
        horizon_min=45,
        offsets=T45_LABEL_OFFSETS,
        ret_key="ret_last_45m",
        cs_ret_key="sector_ret_last_45m",
        cs_rel_key="ret_last_45m_vs_sector",
        crosses_lunch_key="crosses_lunch_45",
        vol_key="vol_last_45m_vs_avg",
        lag1_key="t45_lag1",
        ma_key="t45_ma5",
        y_key="y_t45",
        y_label="y_τ45",
        px_key="price_tau45",
        tau_plus_key="tau_plus_45",
        label_desc="τ⊕40/45/50",
    ),
    "t60": HorizonSpec(
        tag="t60",
        horizon_min=60,
        offsets=T60_LABEL_OFFSETS,
        ret_key="ret_last_60m",
        cs_ret_key="sector_ret_last_60m",
        cs_rel_key="ret_last_60m_vs_sector",
        crosses_lunch_key="crosses_lunch_60",
        vol_key="vol_last_60m_vs_avg",
        lag1_key="t60_lag1",
        ma_key="t60_ma5",
        y_key="y_t60",
        y_label="y_τ60",
        px_key="price_tau60",
        tau_plus_key="tau_plus_60",
        label_desc="τ⊕55/60/65",
    ),
    "t75": HorizonSpec(
        tag="t75",
        horizon_min=75,
        offsets=T75_LABEL_OFFSETS,
        ret_key="ret_last_75m",
        cs_ret_key="sector_ret_last_75m",
        cs_rel_key="ret_last_75m_vs_sector",
        crosses_lunch_key="crosses_lunch_75",
        vol_key="vol_last_75m_vs_avg",
        lag1_key="t75_lag1",
        ma_key="t75_ma5",
        y_key="y_t75",
        y_label="y_τ75",
        px_key="price_tau75",
        tau_plus_key="tau_plus_75",
        label_desc="τ⊕70/75/80",
    ),
    "t90": HorizonSpec(
        tag="t90",
        horizon_min=90,
        offsets=T90_LABEL_OFFSETS,
        ret_key="ret_last_90m",
        cs_ret_key="sector_ret_last_90m",
        cs_rel_key="ret_last_90m_vs_sector",
        crosses_lunch_key="crosses_lunch_90",
        vol_key="vol_last_90m_vs_avg",
        lag1_key="t90_lag1",
        ma_key="t90_ma5",
        y_key="y_t90",
        y_label="y_τ90",
        px_key="price_tau90",
        tau_plus_key="tau_plus_90",
        label_desc="τ⊕85/90/95",
    ),
}

# 从 spec 派生常量（保持外部调用方兼容）
T30_LAG_FEATURES = _HORIZON_SPECS["t30"].lag_features
T30_LAG_FEAT_LABELS = _HORIZON_SPECS["t30"].lag_feat_labels
T30_SEQ_CS_KEYS = _HORIZON_SPECS["t30"].seq_cs_keys
T30_SEQ_FEATURES = _HORIZON_SPECS["t30"].seq_features
T45_LAG_FEATURES = _HORIZON_SPECS["t45"].lag_features
T45_LAG_FEAT_LABELS = _HORIZON_SPECS["t45"].lag_feat_labels
T45_SEQ_CS_KEYS = _HORIZON_SPECS["t45"].seq_cs_keys
T45_SEQ_FEATURES = _HORIZON_SPECS["t45"].seq_features
T60_LAG_FEATURES = _HORIZON_SPECS["t60"].lag_features
T60_LAG_FEAT_LABELS = _HORIZON_SPECS["t60"].lag_feat_labels
T60_SEQ_CS_KEYS = _HORIZON_SPECS["t60"].seq_cs_keys
T60_SEQ_FEATURES = _HORIZON_SPECS["t60"].seq_features
T75_LAG_FEATURES = _HORIZON_SPECS["t75"].lag_features
T75_LAG_FEAT_LABELS = _HORIZON_SPECS["t75"].lag_feat_labels
T75_SEQ_CS_KEYS = _HORIZON_SPECS["t75"].seq_cs_keys
T75_SEQ_FEATURES = _HORIZON_SPECS["t75"].seq_features
T90_LAG_FEATURES = _HORIZON_SPECS["t90"].lag_features
T90_LAG_FEAT_LABELS = _HORIZON_SPECS["t90"].lag_feat_labels
T90_SEQ_CS_KEYS = _HORIZON_SPECS["t90"].seq_cs_keys
T90_SEQ_FEATURES = _HORIZON_SPECS["t90"].seq_features

# (个股近窗收益键, 板块中位键, 相对板块键) — 训练截面按 (date, τ) 中位
HORIZON_SEQ_CS_SPECS = tuple(
    (s.ret_key, s.cs_ret_key, s.cs_rel_key) for s in _HORIZON_SPECS.values()
)

# 默认五档：τc / 对照路径仍写满 price_tau30…90；ŷ_τ* 单头拟合可缩到本头
_DEFAULT_LABEL_HEADS: Tuple[str, ...] = tuple(_HORIZON_SPECS.keys())


def normalize_label_heads(
    label_heads: Optional[Sequence[str]] = None,
) -> Tuple[str, ...]:
    """解析要写入 meta 的 horizon 窗价档；``None`` / 空 → 五档全开。"""
    if label_heads is None:
        return _DEFAULT_LABEL_HEADS
    out: List[str] = []
    for raw in label_heads:
        tag = str(raw or "").strip().lower().replace("τ", "t").replace("y_", "")
        if tag.startswith("t") and tag[1:].isdigit():
            pass
        elif tag.isdigit():
            tag = f"t{tag}"
        if tag in _HORIZON_SPECS and tag not in out:
            out.append(tag)
    return tuple(out) if out else _DEFAULT_LABEL_HEADS


def normalize_minute_tau_grid(
    tau_hm: Optional[str] = None,
    tau_grid: Optional[Sequence[str]] = None,
) -> List[str]:
    """解析训练用 τ 网格；空则回退单点 ``tau_hm`` / 默认网格。"""
    if tau_grid is not None:
        out: List[str] = []
        for t in tau_grid:
            s = str(t or "").strip()
            if not s or s.lower() == "open":
                continue
            if ":" not in s and len(s) == 4 and s.isdigit():
                s = f"{s[:2]}:{s[2:]}"
            if s not in out:
                out.append(s)
        if out:
            return out
    hm = str(tau_hm or "").strip()
    if hm and hm.lower() not in ("", "open"):
        if ":" not in hm and len(hm) == 4 and hm.isdigit():
            hm = f"{hm[:2]}:{hm[2:]}"
        return [hm]
    return list(DEFAULT_MINUTE_TAU_GRID)


def tau_elapsed_min_from_open(tau_hm: str) -> Optional[float]:
    """相对 09:30 的分钟数（5m 前缀长度代理）。"""
    s = str(tau_hm or "").strip().replace(":", "")
    if len(s) != 4 or not s.isdigit():
        return None
    h, m = int(s[:2]), int(s[2:])
    return float(h * 60 + m - (9 * 60 + 30))


def is_open_minute_clock(tau_hm: str) -> bool:
    """09:30 / 已开盘 0 分钟：无 5m 前缀，训练行只保留开盘 Z。"""
    elapsed = tau_elapsed_min_from_open(tau_hm)
    return elapsed is not None and elapsed <= 0.0


def _gap_pct(prev_close: float, open_px: float) -> Optional[float]:
    if prev_close is None or open_px is None:
        return None
    try:
        pc = float(prev_close)
        o = float(open_px)
    except (TypeError, ValueError):
        return None
    if pc <= 0 or o <= 0:
        return None
    return (o / pc - 1.0) * 100.0


def y_r_pct(price_tau: Any, close: Any) -> Optional[float]:
    """ŷ_τc 真值（百分点）：close[T]/price(τ) − 1。"""
    try:
        p = float(price_tau)
        c = float(close)
    except (TypeError, ValueError):
        return None
    if p <= 0 or c <= 0:
        return None
    return (c / p - 1.0) * 100.0


def _open_to_close_pct(open_px: float, close_px: float) -> Optional[float]:
    """τ=open 时的 ŷ_τc：close/open−1。"""
    return y_r_pct(open_px, close_px)


def y_t30_pct(price_tau: Any, price_tau30: Any) -> Optional[float]:
    """ŷ_τ30 真值（百分点）：mean(price(τ⊕25/30/35))/price(τ) − 1。

    ``price_tau30`` 为三根均价（由 ``price_at_t30_mean_session`` 写入）。
    """
    try:
        p0 = float(price_tau)
        p1 = float(price_tau30)
    except (TypeError, ValueError):
        return None
    if p0 <= 0 or p1 <= 0:
        return None
    return (p1 / p0 - 1.0) * 100.0


def y_t45_pct(price_tau: Any, price_tau45: Any) -> Optional[float]:
    """ŷ_τ45 真值（百分点）：mean(price(τ⊕40/45/50))/price(τ) − 1。"""
    return y_t30_pct(price_tau, price_tau45)


def y_t60_pct(price_tau: Any, price_tau60: Any) -> Optional[float]:
    """ŷ_τ60 真值（百分点）：mean(price(τ⊕55/60/65))/price(τ) − 1。"""
    return y_t30_pct(price_tau, price_tau60)


def y_t75_pct(price_tau: Any, price_tau75: Any) -> Optional[float]:
    """ŷ_τ75 真值（百分点）：mean(price(τ⊕70/75/80))/price(τ) − 1。"""
    return y_t30_pct(price_tau, price_tau75)


def y_t90_pct(price_tau: Any, price_tau90: Any) -> Optional[float]:
    """ŷ_τ90 真值（百分点）：mean(price(τ⊕85/90/95))/price(τ) − 1。"""
    return y_t30_pct(price_tau, price_tau90)


def _attach_horizon_lags_to_panel(
    panel: Dict[str, Any], spec: HorizonSpec
) -> Dict[str, Any]:
    """按同钟 PIT 写入 tN_lag1 / tN_ma5（只用早于该日的同钟真实 y_τN）。"""
    if not isinstance(panel, dict):
        return panel
    xs = list(panel.get("xs") or [])
    dates = list(panel.get("dates") or [])
    metas = list(panel.get("metas") or [])
    ys = list(panel.get("ys") or [])
    n = min(len(xs), len(dates), len(metas), len(ys))
    by_hm: Dict[str, Dict[str, float]] = {}
    for i in range(n):
        m = metas[i] if isinstance(metas[i], dict) else {}
        hm = str(m.get("tau") or "")[:5]
        d = str(dates[i] or "")[:10]
        if not hm or len(d) < 10:
            continue
        try:
            by_hm.setdefault(hm, {})[d] = float(ys[i])
        except (TypeError, ValueError):
            continue
    for i in range(n):
        row = xs[i] if isinstance(xs[i], dict) else {}
        if not isinstance(row, dict):
            continue
        m = metas[i] if isinstance(metas[i], dict) else {}
        hm = str(m.get("tau") or "")[:5]
        asof = str(dates[i] or "")[:10]
        clock_map = by_hm.get(hm) or {}
        hist = [{"date": d} for d in sorted(clock_map.keys())]
        lags = label_lag_features(
            hist_bars=hist,
            by_date=clock_map,
            asof_date=asof,
            lag1_key=spec.lag1_key,
            ma_key=spec.ma_key,
        )
        xs[i] = dict(row)
        for k in spec.lag_features:
            xs[i][k] = lags.get(k)
    panel["xs"] = xs
    return panel


def _relabel_tau_panels_as_horizon(
    enriched: Sequence[Dict[str, Any]],
    spec: HorizonSpec,
) -> List[Dict[str, Any]]:
    """把 τ 面板标签换成 ŷ_τ{N} = mean(price(τ⊕offsets))/price(τ) − 1。缺三根均价丢行。"""
    out: List[Dict[str, Any]] = []
    for p in enriched or []:
        if not isinstance(p, dict):
            continue
        xs_in = list(p.get("xs") or [])
        dates_in = list(p.get("dates") or [])
        metas_in = list(p.get("metas") or [])
        n = min(len(xs_in), len(dates_in), len(metas_in))
        xs: List[Any] = []
        ys: List[float] = []
        dates: List[str] = []
        metas: List[Dict[str, Any]] = []
        for i in range(n):
            m = metas_in[i] if isinstance(metas_in[i], dict) else {}
            pxN = m.get(spec.px_key)
            tauN_hm = m.get(spec.tau_plus_key)
            day_i = str(m.get("date") or dates_in[i] or "")[:10]
            tau_i = str(m.get("tau") or "").strip()
            mb = p.get("minute_bars")
            # 有分钟线：用精确 τ 根重算（L1 stitch 可能缺/宽分母）。
            # 无分钟线：信任 collect 已写入的 price_tau（面板不常驻 5m）。
            if mb and tau_i and len(day_i) >= 10:
                px_tau = label_price_at_tau(mb, trade_date=day_i, tau_hm=tau_i)
            else:
                px_tau = m.get("price_tau")
            if pxN is None and mb and tau_i and len(day_i) >= 10:
                tauN_hm, pxN = price_at_horizon_mean_session(
                    mb,
                    trade_date=day_i,
                    tau_hm=tau_i,
                    offsets=spec.offsets,
                )
            yt = y_t30_pct(px_tau, pxN)
            if yt is None:
                continue
            row_x = dict(xs_in[i]) if isinstance(xs_in[i], dict) else {}
            for k in spec.seq_features:
                if k in spec.lag_features:
                    continue
                if m.get(k) is not None:
                    row_x[k] = m[k]
            xs.append(row_x)
            ys.append(float(yt))
            dates.append(dates_in[i])
            meta = dict(m)
            if px_tau is not None:
                meta["price_tau"] = px_tau
            meta[spec.y_key] = float(yt)
            meta[spec.y_label] = float(yt)
            if tauN_hm:
                meta[spec.tau_plus_key] = tauN_hm
            if pxN is not None:
                meta[spec.px_key] = pxN
            metas.append(meta)
        if len(ys) < 4:
            continue
        row = dict(p)
        row["xs"] = xs
        row["ys"] = ys
        row["dates"] = dates
        row["metas"] = metas
        _attach_horizon_lags_to_panel(row, spec)
        out.append(row)
    return out


def relabel_tau_panels_as_t30(
    enriched: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """把 τ 面板标签换成 ŷ_τ30 = mean(price(τ⊕25/30/35))/price(τ) − 1。"""
    return _relabel_tau_panels_as_horizon(enriched, _HORIZON_SPECS["t30"])


def relabel_tau_panels_as_t45(
    enriched: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """把 τ 面板标签换成 ŷ_τ45 = mean(price(τ⊕40/45/50))/price(τ) − 1。"""
    return _relabel_tau_panels_as_horizon(enriched, _HORIZON_SPECS["t45"])


def relabel_tau_panels_as_t60(
    enriched: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """把 τ 面板标签换成 ŷ_τ60 = mean(price(τ⊕55/60/65))/price(τ) − 1。"""
    return _relabel_tau_panels_as_horizon(enriched, _HORIZON_SPECS["t60"])


def relabel_tau_panels_as_t75(
    enriched: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """把 τ 面板标签换成 ŷ_τ75 = mean(price(τ⊕70/75/80))/price(τ) − 1。"""
    return _relabel_tau_panels_as_horizon(enriched, _HORIZON_SPECS["t75"])


def relabel_tau_panels_as_t90(
    enriched: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """把 τ 面板标签换成 ŷ_τ90 = mean(price(τ⊕85/90/95))/price(τ) − 1。"""
    return _relabel_tau_panels_as_horizon(enriched, _HORIZON_SPECS["t90"])


def _finite_median(vals: Sequence[float]) -> Optional[float]:
    xs = [float(v) for v in vals if v is not None]
    if not xs:
        return None
    return float(median(xs))


def hist_bars_pit(
    bars: Optional[Sequence[dict]],
    *,
    asof_date: Optional[str] = None,
) -> List[dict]:
    """去掉 asof 当日 K 线，ATR / 日线窗口不吃 T 日振幅。"""
    hist = list(bars or [])
    day = str(asof_date or "")[:10]
    if day and hist and str(hist[-1].get("date") or "")[:10] == day:
        return hist[:-1]
    return hist


def _series_std(vals: Sequence[float]) -> Optional[float]:
    if not vals:
        return None
    if len(vals) < 2:
        return 0.0
    try:
        return round(float(stdev(vals)), 6)
    except Exception:  # noqa: BLE001
        return None


def _series_sign_streak(vals: Sequence[float]) -> Optional[float]:
    """最近有效值起、同号连续天数（带符号）；0 断开；无样本则空。"""
    if not vals:
        return None

    def _sign(x: float) -> int:
        if x > 0:
            return 1
        if x < 0:
            return -1
        return 0

    s0 = _sign(float(vals[0]))
    if s0 == 0:
        return 0.0
    n = 0
    for v in vals:
        if _sign(float(v)) != s0:
            break
        n += 1
    return float(s0 * n)


def label_lag_features(
    *,
    hist_bars: Sequence[dict],
    by_date: Mapping[str, float],
    asof_date: str,
    window: int = LABEL_LAG_WINDOW,
    lag1_key: str,
    ma_key: Optional[str] = None,
    std_key: Optional[str] = None,
    streak_key: Optional[str] = None,
) -> Dict[str, Optional[float]]:
    """PIT：只用 **严格早于** asof 且 ``by_date`` 有值的交易日；最多 ``window`` 天。

    asof 缺失则全部留空（宁缺，避免 hist 含 T 日 K 时把当日 label 当因子）。
    ``by_date`` 即使含 T/未来日也不读。
    """
    asof = str(asof_date or "")[:10]

    def _empty() -> Dict[str, Optional[float]]:
        out: Dict[str, Optional[float]] = {lag1_key: None}
        if ma_key:
            out[ma_key] = None
        if std_key:
            out[std_key] = None
        if streak_key:
            out[streak_key] = None
        return out

    if len(asof) < 10:
        return _empty()
    # 键已是 YYYY-MM-DD 时直接查。每天滤整张表是 O(交易日²)，开盘面板 1000 天主耗时在这。
    # 当日及未来仍由下面的 ``d >= asof`` 丢掉，不读进因子。
    src = by_date or {}
    lookup: Mapping[str, Any] = src
    if src:
        sample = next(iter(src))
        sample_key = sample if isinstance(sample, str) else str(sample)
        if len(sample_key) != 10:
            folded: Dict[str, Any] = {}
            for key, value in src.items():
                day = str(key)[:10]
                if len(day) >= 10:
                    folded[day] = value
            lookup = folded
    win = max(1, int(window or LABEL_LAG_WINDOW))
    vals: List[float] = []
    seen: set = set()
    seq = hist_bars if isinstance(hist_bars, (list, tuple)) else tuple(hist_bars or ())
    for bar in reversed(seq):
        if not isinstance(bar, dict):
            continue
        d = str(bar.get("date") or bar.get("trade_date") or "")[:10]
        if len(d) < 10:
            d = str(bar.get("datetime") or "")[:10]
        if len(d) < 10 or d in seen:
            continue
        if d >= asof:
            continue
        seen.add(d)
        y = lookup.get(d)
        if y is None:
            continue
        try:
            vals.append(float(y))
        except (TypeError, ValueError):
            continue
        if len(vals) >= win:
            break
    lag1 = round(vals[0], 6) if vals else None
    out: Dict[str, Optional[float]] = {lag1_key: lag1}
    if ma_key:
        out[ma_key] = round(sum(vals) / float(len(vals)), 6) if vals else None
    if std_key:
        out[std_key] = _series_std(vals)
    if streak_key:
        out[streak_key] = _series_sign_streak(vals)
    return out


def yest_gap_from_hist(
    hist_bars: Optional[Sequence[dict]],
    asof_date: str,
) -> Optional[float]:
    """昨隔夜缺口 (O_{T-1}/C_{T-2}-1)×100；缺两根则空。不是当日 gap_pct。"""
    asof = str(asof_date or "")[:10]
    if len(asof) < 10:
        return None
    hist = hist_bars_pit(hist_bars, asof_date=asof)
    if len(hist) < 2:
        return None
    b_yest = hist[-1]
    b_prev = hist[-2]
    g = _gap_pct(b_prev.get("close"), b_yest.get("open"))
    if g is None:
        return None
    return round(float(g), 6)


def realized_tau_by_date(bars: Optional[Sequence[dict]]) -> Dict[str, float]:
    """各日真实 ŷ_τc（τ=open，close/open−1）。算不出的日不进表。"""
    out: Dict[str, float] = {}
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        d = str(bar.get("date") or bar.get("trade_date") or "")[:10]
        if len(d) < 10:
            d = str(bar.get("datetime") or "")[:10]
        if len(d) < 10:
            continue
        y = y_r_pct(bar.get("open"), bar.get("close"))
        if y is None:
            continue
        out[d] = round(float(y), 6)
    return out


def tau_lag_features(
    *,
    hist_bars: Sequence[dict],
    tau_by_date: Mapping[str, float],
    asof_date: str,
    window: int = LABEL_LAG_WINDOW,
) -> Dict[str, Optional[float]]:
    return label_lag_features(
        hist_bars=hist_bars,
        by_date=tau_by_date,
        asof_date=asof_date,
        window=window,
        lag1_key="tau_lag1",
        ma_key="tau_ma5",
    )


def attach_tau_lag_features(
    feats: Optional[dict],
    *,
    hist_bars: Sequence[dict],
    asof_date: str,
) -> Dict[str, Any]:
    """把 tau_lag1 / tau_ma5 写入特征行；缺历史则留空，不挡打分。"""
    out: Dict[str, Any] = dict(feats or {})
    hist = hist_bars_pit(hist_bars, asof_date=asof_date)
    lags = tau_lag_features(
        hist_bars=hist,
        tau_by_date=realized_tau_by_date(hist),
        asof_date=asof_date,
    )
    for k in TAU_LAG_FEATURES:
        out[k] = lags.get(k)
    return out


def attach_t30_lags_to_panel(panel: Dict[str, Any]) -> Dict[str, Any]:
    """按同钟 PIT 写入 t30_lag1 / t30_ma5（只用早于该日的同钟真实 y_τ30）。"""
    return _attach_horizon_lags_to_panel(panel, _HORIZON_SPECS["t30"])


def attach_t45_lags_to_panel(panel: Dict[str, Any]) -> Dict[str, Any]:
    """按同钟 PIT 写入 t45_lag1 / t45_ma5（只用早于该日的同钟真实 y_τ45）。"""
    return _attach_horizon_lags_to_panel(panel, _HORIZON_SPECS["t45"])


def attach_t60_lags_to_panel(panel: Dict[str, Any]) -> Dict[str, Any]:
    """按同钟 PIT 写入 t60_lag1 / t60_ma5（只用早于该日的同钟真实 y_τ60）。"""
    return _attach_horizon_lags_to_panel(panel, _HORIZON_SPECS["t60"])


def attach_t75_lags_to_panel(panel: Dict[str, Any]) -> Dict[str, Any]:
    """按同钟 PIT 写入 t75_lag1 / t75_ma5（只用早于该日的同钟真实 y_τ75）。"""
    return _attach_horizon_lags_to_panel(panel, _HORIZON_SPECS["t75"])


def attach_t90_lags_to_panel(panel: Dict[str, Any]) -> Dict[str, Any]:
    """按同钟 PIT 写入 t90_lag1 / t90_ma5（只用早于该日的同钟真实 y_τ90）。"""
    return _attach_horizon_lags_to_panel(panel, _HORIZON_SPECS["t90"])


_REALIZED_BY_CODE_HM: Dict[Tuple[str, str, str], Dict[str, float]] = {}
_MINUTE_BY_CODE: Dict[str, Dict[str, List[dict]]] = {}


def _minute_by_date_for_lags(stock_code: str) -> Dict[str, List[dict]]:
    code = str(stock_code or "").strip()
    if not code:
        return {}
    hit = _MINUTE_BY_CODE.get(code)
    if hit is not None:
        return hit
    by_date: Dict[str, List[dict]] = {}
    try:
        from core.ports.market import resolve_market_code
        from core.store import load_minute_cache

        mkt, pure = resolve_market_code(code)
        packed = load_minute_cache(
            mkt or "CN",
            pure or code,
            period="5",
            min_bars=6,
            ignore_age=True,
        )
        bars = list((packed[0] if packed else None) or [])
        by_date = dict(_index_minute_bars_by_date(bars) or {})
    except Exception:  # noqa: BLE001
        logger.debug("load realized horizon minutes failed", exc_info=True)
        by_date = {}
    _MINUTE_BY_CODE[code] = by_date
    return by_date


def _realized_horizon_by_date_for_clock(
    stock_code: str, tau_hm: str, spec: HorizonSpec
) -> Dict[str, float]:
    """同票同钟历史真实 y_τ{N}（date → %）。缺分钟仓则空。"""
    code = str(stock_code or "").strip()
    hm = str(tau_hm or "").strip()[:5]
    if not code or len(hm) < 4:
        return {}
    if ":" not in hm and len(hm) == 4 and hm.isdigit():
        hm = f"{hm[:2]}:{hm[2:]}"
    key = (code, hm, spec.tag)
    hit = _REALIZED_BY_CODE_HM.get(key)
    if hit is not None:
        return hit
    by_date = _minute_by_date_for_lags(code)
    out: Dict[str, float] = {}
    for d, day_mins in by_date.items():
        day = str(d or "")[:10]
        if len(day) < 10:
            continue
        px_tau = label_price_at_tau(day_mins, trade_date=day, tau_hm=hm)
        _, pxN = price_at_horizon_mean_session(day_mins, trade_date=day, tau_hm=hm, offsets=spec.offsets)
        yt = y_t30_pct(px_tau, pxN)
        if yt is not None:
            out[day] = float(yt)
    _REALIZED_BY_CODE_HM[key] = out
    return out


def realized_t30_by_date_for_clock(stock_code: str, tau_hm: str) -> Dict[str, float]:
    """同票同钟历史真实 y_τ30（date → %）。缺分钟仓则空。"""
    return _realized_horizon_by_date_for_clock(stock_code, tau_hm, _HORIZON_SPECS["t30"])


def realized_t45_by_date_for_clock(stock_code: str, tau_hm: str) -> Dict[str, float]:
    """同票同钟历史真实 y_τ45（date → %）。缺分钟仓则空。"""
    return _realized_horizon_by_date_for_clock(stock_code, tau_hm, _HORIZON_SPECS["t45"])


def realized_t60_by_date_for_clock(stock_code: str, tau_hm: str) -> Dict[str, float]:
    """同票同钟历史真实 y_τ60（date → %）。缺分钟仓则空。"""
    return _realized_horizon_by_date_for_clock(stock_code, tau_hm, _HORIZON_SPECS["t60"])


def realized_t75_by_date_for_clock(stock_code: str, tau_hm: str) -> Dict[str, float]:
    """同票同钟历史真实 y_τ75（date → %）。缺分钟仓则空。"""
    return _realized_horizon_by_date_for_clock(stock_code, tau_hm, _HORIZON_SPECS["t75"])


def _attach_horizon_lag_features(
    feats: Optional[dict],
    *,
    spec: HorizonSpec,
    hist_bars: Optional[Sequence[dict]] = None,
    asof_date: str,
    tau_hm: str,
    stock_code: str = "",
) -> Dict[str, Any]:
    """把 tN_lag1 / tN_ma5 写入特征行；缺同钟历史则留空，不挡打分。"""
    out: Dict[str, Any] = dict(feats or {})
    hist = hist_bars_pit(hist_bars, asof_date=asof_date)
    clock_map = _realized_horizon_by_date_for_clock(stock_code, tau_hm, spec)
    if not hist and clock_map:
        hist = [{"date": d} for d in sorted(clock_map.keys())]
    lags = label_lag_features(
        hist_bars=hist,
        by_date=clock_map,
        asof_date=asof_date,
        lag1_key=spec.lag1_key,
        ma_key=spec.ma_key,
    )
    if lags.get(spec.lag1_key) is None and clock_map:
        hist = [{"date": d} for d in sorted(clock_map.keys())]
        lags = label_lag_features(
            hist_bars=hist,
            by_date=clock_map,
            asof_date=asof_date,
            lag1_key=spec.lag1_key,
            ma_key=spec.ma_key,
        )
    for k in spec.lag_features:
        out[k] = lags.get(k)
    return out


def attach_t30_lag_features(
    feats: Optional[dict],
    *,
    hist_bars: Optional[Sequence[dict]] = None,
    asof_date: str,
    tau_hm: str,
    stock_code: str = "",
) -> Dict[str, Any]:
    """把 t30_lag1 / t30_ma5 写入特征行；缺同钟历史则留空，不挡打分。"""
    return _attach_horizon_lag_features(feats, spec=_HORIZON_SPECS["t30"], hist_bars=hist_bars, asof_date=asof_date, tau_hm=tau_hm, stock_code=stock_code)


def attach_t45_lag_features(
    feats: Optional[dict],
    *,
    hist_bars: Optional[Sequence[dict]] = None,
    asof_date: str,
    tau_hm: str,
    stock_code: str = "",
) -> Dict[str, Any]:
    """把 t45_lag1 / t45_ma5 写入特征行；缺同钟历史则留空，不挡打分。"""
    return _attach_horizon_lag_features(feats, spec=_HORIZON_SPECS["t45"], hist_bars=hist_bars, asof_date=asof_date, tau_hm=tau_hm, stock_code=stock_code)


def attach_t60_lag_features(
    feats: Optional[dict],
    *,
    hist_bars: Optional[Sequence[dict]] = None,
    asof_date: str,
    tau_hm: str,
    stock_code: str = "",
) -> Dict[str, Any]:
    """把 t60_lag1 / t60_ma5 写入特征行；缺同钟历史则留空，不挡打分。"""
    return _attach_horizon_lag_features(feats, spec=_HORIZON_SPECS["t60"], hist_bars=hist_bars, asof_date=asof_date, tau_hm=tau_hm, stock_code=stock_code)


def realized_t90_by_date_for_clock(stock_code: str, tau_hm: str) -> Dict[str, float]:
    """同票同钟历史真实 y_τ90（date → %）。缺分钟仓则空。"""
    return _realized_horizon_by_date_for_clock(stock_code, tau_hm, _HORIZON_SPECS["t90"])


def attach_t75_lag_features(
    feats: Optional[dict],
    *,
    hist_bars: Optional[Sequence[dict]] = None,
    asof_date: str,
    tau_hm: str,
    stock_code: str = "",
) -> Dict[str, Any]:
    """把 t75_lag1 / t75_ma5 写入特征行；缺同钟历史则留空，不挡打分。"""
    return _attach_horizon_lag_features(feats, spec=_HORIZON_SPECS["t75"], hist_bars=hist_bars, asof_date=asof_date, tau_hm=tau_hm, stock_code=stock_code)


def attach_t90_lag_features(
    feats: Optional[dict],
    *,
    hist_bars: Optional[Sequence[dict]] = None,
    asof_date: str,
    tau_hm: str,
    stock_code: str = "",
) -> Dict[str, Any]:
    """把 t90_lag1 / t90_ma5 写入特征行；缺同钟历史则留空，不挡打分。"""
    return _attach_horizon_lag_features(feats, spec=_HORIZON_SPECS["t90"], hist_bars=hist_bars, asof_date=asof_date, tau_hm=tau_hm, stock_code=stock_code)


def gap_atr_from_hist(
    gap_pct: Optional[float],
    hist_bars: Optional[Sequence[dict]],
    *,
    window: int = GAP_ATR_WINDOW,
    clip: float = GAP_ATR_CLIP,
) -> Optional[float]:
    """缺口 / ATR%（T−1 窗口）。量纲：跳了几个 ATR；clip 防极小波动炸值。"""
    if gap_pct is None:
        return None
    try:
        g = float(gap_pct)
    except (TypeError, ValueError):
        return None
    try:
        from core.t0.rules import atr_pct_from_bars

        atr = atr_pct_from_bars(list(hist_bars or []), int(window))
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        atr = None
    if atr is None or float(atr) < 1e-6:
        return None
    v = g / float(atr)
    cap = float(clip) if clip is not None else None
    if cap is not None and cap > 0:
        v = max(-cap, min(cap, v))
    return round(v, 4)


def sector_gap_reference_by_code(
    gaps_by_code: Dict[str, Optional[float]],
    *,
    sector_map: Optional[Dict[str, str]] = None,
    min_sector_n: int = _SECTOR_REL_MIN_N,
) -> Dict[str, Optional[float]]:
    """每个 code 的参照缺口：同行中位（n≥门槛）否则全截面中位。"""
    valid = {
        str(c).strip(): float(g)
        for c, g in (gaps_by_code or {}).items()
        if str(c or "").strip() and g is not None
    }
    pool_med = _finite_median(list(valid.values()))
    sm = sector_map if isinstance(sector_map, dict) else {}
    try:
        from core.portfolio_optimize import _sector_for
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        def _sector_for(code: str, m: Optional[Dict[str, str]] = None) -> str:  # type: ignore
            return str((m or {}).get(code) or "")

    code_sec: Dict[str, str] = {}
    by_sec: Dict[str, List[float]] = {}
    for c, g in valid.items():
        sec = str(_sector_for(c, sm) or "").strip()
        if not sec:
            continue
        code_sec[c] = sec
        by_sec.setdefault(sec, []).append(g)
    need = max(2, int(min_sector_n or _SECTOR_REL_MIN_N))
    sec_med = {
        s: _finite_median(gs) for s, gs in by_sec.items() if len(gs) >= need
    }
    out: Dict[str, Optional[float]] = {}
    for c, g in (gaps_by_code or {}).items():
        key = str(c or "").strip()
        if not key or g is None:
            out[key] = None
            continue
        med = sec_med.get(code_sec.get(key, ""))
        if med is None:
            med = pool_med
        out[key] = round(float(med), 4) if med is not None else None
    return out


def gap_vs_sector_value(
    gap_pct: Optional[float],
    sector_median: Optional[float],
) -> Optional[float]:
    if gap_pct is None or sector_median is None:
        return None
    try:
        return round(float(gap_pct) - float(sector_median), 4)
    except (TypeError, ValueError):
        return None


def yclose_loc_from_prev(prev_bar: Optional[dict], open_px: float) -> Optional[float]:
    """昨收相对昨高低的位置 [0,1]；开盘可得，无泄漏。"""
    if not isinstance(prev_bar, dict):
        return None
    try:
        hi = float(prev_bar.get("high"))
        lo = float(prev_bar.get("low"))
        o = float(open_px)
    except (TypeError, ValueError):
        return None
    if hi <= lo or o <= 0:
        return None
    return round(max(0.0, min(1.0, (o - lo) / (hi - lo))), 4)




def _open_atc_items(cols: object, n_rows: int) -> Optional[Tuple[Tuple[str, Any], ...]]:
    """每一列都必须正好覆盖全部决策日。有一列对不上就整段弃用。"""
    if not isinstance(cols, dict) or n_rows <= 0:
        return None
    items: List[Tuple[str, Any]] = []
    for key, col in cols.items():
        if getattr(col, "shape", None) != (int(n_rows),):
            return None
        items.append((str(key), col))
    return tuple(items) if items else None


def _finite_raw(val: Any) -> Optional[float]:
    try:
        num = float(val)
    except (TypeError, ValueError):
        return None
    if num != num or abs(num) == float("inf"):
        return None
    return num


def _inject_open_raw(row: dict, window: Sequence[dict], prev_bar: Any, open_px: float) -> None:
    from core.signal.factors.alpha_tc import inject_raw_atc

    pc_i = None
    try:
        pc_i = float((prev_bar or {}).get("close") or 0) or None
    except (TypeError, ValueError, AttributeError):
        pc_i = None
    inject_raw_atc(
        row,
        window,
        open_t=open_px,
        prev_close=pc_i,
        price_tau=open_px,
        tau_hm="open",
    )


def _fill_column_row(row: dict, items: Sequence[Tuple[str, Any]], ridx: int) -> bool:
    """把一整段列写进当天。

    一个有限值都没写上，或下标坏了，就清掉已写的 ``raw_atc_*`` 并返回 False，
    调用方改走逐日。这样前缀没锚上的那一天不会被当成「算完但没有因子」。
    """
    wrote = 0
    try:
        for key, col in items:
            num = _finite_raw(col[ridx])
            if num is not None:
                row[key] = num
                wrote += 1
    except Exception:  # noqa: BLE001
        logger.debug("tau column row fallback", exc_info=True)
        wrote = 0
    if wrote > 0:
        return True
    for key in list(row):
        if str(key).startswith("raw_atc_"):
            row.pop(key, None)
    return False


def _copy_ath_gap_from_atc(row: dict) -> None:
    """窗头的隔夜字段与 raw_atc 同源，直接拷贝，不再把前缀和 ATR 重走一遍。"""
    from core.signal.factors.alpha_tc import ALPHA_TC_RAW_PREFIX
    from core.signal.factors.alpha_th import ALPHA_TH_RAW_PREFIX, _GAP_KEYS, _YK_KEYS

    for bare in (*_GAP_KEYS, *_YK_KEYS):
        num = _finite_raw(row.get(f"{ALPHA_TC_RAW_PREFIX}{bare}"))
        if num is not None:
            row[f"{ALPHA_TH_RAW_PREFIX}{bare}"] = num


def _scan_one_minute_clock_atc(
    bars: Sequence[dict],
    minutes_by_day: Mapping[str, Sequence[dict]],
    *,
    hist: int,
    clock: str,
) -> Optional[Tuple[Tuple[str, Any], ...]]:
    """一个钟的整段 ``raw_atc_*``。日线解析失败返回 None，调用方逐日注入。"""
    from core.signal.factors.alpha_tc import (
        _PREFIX_KEYS,
        _prefix_neutral,
        _prefix_summary,
        ohlcv_from_bars,
        raw_atc_tau_columns,
    )

    packed = ohlcv_from_bars(bars)
    if not packed:
        return None
    n = len(bars)
    hist_n = int(hist)
    m = n - hist_n
    if m <= 0:
        return None
    nan = float("nan")
    pt = [nan] * m
    phi = [nan] * m
    plo = [nan] * m
    pvol = [0.0] * m
    defaults = _prefix_neutral(clock)
    pref = {key: [float(defaults.get(key, 0.0))] * m for key in _PREFIX_KEYS}
    anchored = 0
    for r, i in enumerate(range(hist_n, n)):
        bar = bars[i]
        if not isinstance(bar, dict):
            continue
        try:
            open_px = float(bar.get("open"))
            if not (open_px > 0):
                continue
            day = str(bar.get("date") or "")[:10]
            day_mins = minutes_by_day.get(day) or ()
            px = price_at_tau_from_minutes(day_mins, trade_date=day, tau_hm=clock)
            if (px is None or px <= 0) and is_open_minute_clock(clock):
                o_min, _c_min = minute_session_open_close(day_mins, trade_date=day)
                if o_min and o_min > 0:
                    px = o_min
            px_n = _finite_raw(px)
            if px_n is None or px_n <= 0:
                continue
            fields, hi, lo, cum, _last = _prefix_summary(
                day_mins,
                trade_date=day,
                tau_hm=clock,
                open_t=open_px,
                price_tau=px_n,
            )
            hi_n = _finite_raw(hi)
            lo_n = _finite_raw(lo)
            if cum is None:
                continue
            cum_n = _finite_raw(cum)
            if hi_n is None or lo_n is None or cum_n is None:
                continue
            pt[r] = px_n
            phi[r] = hi_n
            plo[r] = lo_n
            pvol[r] = cum_n
            if isinstance(fields, dict):
                for key in _PREFIX_KEYS:
                    if key not in fields:
                        continue
                    num = _finite_raw(fields.get(key))
                    if num is not None:
                        pref[key][r] = num
            anchored += 1
        except Exception:  # noqa: BLE001
            logger.debug("tau minute anchor skipped i=%s", i, exc_info=True)
            continue
    if anchored <= 0:
        return None
    cols = raw_atc_tau_columns(
        *packed,
        hist=hist_n,
        price_tau=pt,
        prefix_hi=phi,
        prefix_lo=plo,
        prefix_vol=pvol,
        prefix_fields=pref,
        tau_hm=clock,
    )
    return _open_atc_items(cols, m)


def _minute_clocks_atc_items(
    bars: Sequence[dict],
    minutes_by_day: Mapping[str, Sequence[dict]],
    *,
    hist: int,
    clocks: Sequence[str],
) -> Dict[str, Tuple[Tuple[str, Any], ...]]:
    """多个钟的 ``raw_atc_*``。日线只解析一次，每个交易日只滤到最晚钟。

    某个钟锚定失败则这一钟缺席，其它钟保留。列与逐钟 ``_minute_clock_atc_items`` 相同。
    """
    from bisect import bisect_right

    from core.signal.factors.alpha_tc import (
        _PREFIX_KEYS,
        _prefix_neutral,
        _prefix_summary,
        ohlcv_from_bars,
        raw_atc_tau_columns,
    )
    from core.signal.minute_tau_feats import _bar_hm, _clock_hhmm, _filter_day_bars_upto_tau

    clock_list = [str(c) for c in (clocks or []) if str(c or "").strip()]
    if not clock_list:
        return {}
    packed = ohlcv_from_bars(bars)
    if not packed:
        return {}
    n = len(bars)
    hist_n = int(hist)
    m = n - hist_n
    if m <= 0:
        return {}
    timed: List[Tuple[str, str]] = []
    untimed: List[str] = []
    for clock in clock_list:
        target = _clock_hhmm(clock)
        if target is None:
            untimed.append(clock)
        else:
            timed.append((clock, target))
    timed.sort(key=lambda item: item[1])
    latest = timed[-1][1] if timed else ""
    nan = float("nan")
    buckets: Dict[str, Dict[str, Any]] = {}
    for clock, _target in timed:
        defaults = _prefix_neutral(clock)
        buckets[clock] = {
            "pt": [nan] * m,
            "phi": [nan] * m,
            "plo": [nan] * m,
            "pvol": [0.0] * m,
            "pref": {key: [float(defaults.get(key, 0.0))] * m for key in _PREFIX_KEYS},
            "anchored": 0,
        }
    day_cache: Dict[str, Tuple[List[dict], List[str]]] = {}
    for r, i in enumerate(range(hist_n, n) if timed else ()):
        bar = bars[i]
        if not isinstance(bar, dict):
            continue
        try:
            open_px = float(bar.get("open"))
        except (TypeError, ValueError):
            continue
        if not (open_px > 0):
            continue
        day = str(bar.get("date") or "")[:10]
        day_mins = minutes_by_day.get(day) or ()
        ordered_hms = day_cache.get(day)
        if ordered_hms is None:
            ordered = _filter_day_bars_upto_tau(
                day_mins, day=day, target=latest, match_date=False
            )
            ordered_hms = (ordered, [_bar_hm(b) for b in ordered])
            day_cache[day] = ordered_hms
        ordered, hms = ordered_hms
        for clock, target in timed:
            bucket = buckets[clock]
            end = bisect_right(hms, target)
            prefix = ordered[:end]
            try:
                px = price_at_tau_from_minutes(day_mins, trade_date=day, tau_hm=clock)
                if (px is None or px <= 0) and is_open_minute_clock(clock):
                    o_min, _c_min = minute_session_open_close(day_mins, trade_date=day)
                    if o_min and o_min > 0:
                        px = o_min
                px_n = _finite_raw(px)
                if px_n is None or px_n <= 0:
                    continue
                fields, hi, lo, cum, _last = _prefix_summary(
                    prefix,
                    trade_date=day,
                    tau_hm=clock,
                    open_t=open_px,
                    price_tau=px_n,
                )
                hi_n = _finite_raw(hi)
                lo_n = _finite_raw(lo)
                if cum is None:
                    continue
                cum_n = _finite_raw(cum)
                if hi_n is None or lo_n is None or cum_n is None:
                    continue
                bucket["pt"][r] = px_n
                bucket["phi"][r] = hi_n
                bucket["plo"][r] = lo_n
                bucket["pvol"][r] = cum_n
                if isinstance(fields, dict):
                    for key in _PREFIX_KEYS:
                        if key not in fields:
                            continue
                        num = _finite_raw(fields.get(key))
                        if num is not None:
                            bucket["pref"][key][r] = num
                bucket["anchored"] += 1
            except Exception:  # noqa: BLE001
                logger.debug(
                    "tau minute anchor skipped clock=%s i=%s", clock, i, exc_info=True
                )
                continue
    out: Dict[str, Tuple[Tuple[str, Any], ...]] = {}
    for clock, _target in timed:
        bucket = buckets[clock]
        if int(bucket["anchored"]) <= 0:
            continue
        try:
            cols = raw_atc_tau_columns(
                *packed,
                hist=hist_n,
                price_tau=bucket["pt"],
                prefix_hi=bucket["phi"],
                prefix_lo=bucket["plo"],
                prefix_vol=bucket["pvol"],
                prefix_fields=bucket["pref"],
                tau_hm=clock,
            )
        except Exception:  # noqa: BLE001
            logger.debug("tau minute columns failed clock=%s", clock, exc_info=True)
            continue
        items = _open_atc_items(cols, m)
        if items:
            out[clock] = items
    for clock in untimed:
        try:
            items = _scan_one_minute_clock_atc(
                bars,
                minutes_by_day,
                hist=hist_n,
                clock=clock,
            )
        except Exception:  # noqa: BLE001
            logger.debug("tau minute columns failed clock=%s", clock, exc_info=True)
            items = None
        if items:
            out[clock] = items
    return out


def _minute_clock_atc_items(
    bars: Sequence[dict],
    minutes_by_day: Mapping[str, Sequence[dict]],
    *,
    hist: int,
    clock: str,
) -> Optional[Tuple[Tuple[str, Any], ...]]:
    """单钟入口。走批量路径，与网格多钟结果一致。"""
    key = str(clock or "").strip()
    if not key:
        return None
    batch = _minute_clocks_atc_items(
        bars,
        minutes_by_day,
        hist=hist,
        clocks=[key],
    )
    return batch.get(key)


def collect_tau_open_panel(
    bars: List[dict],
    *,
    min_history: int = 12,
    max_window: int = 30,
    index_bars: Optional[List[dict]] = None,
    fundamentals: Optional[dict] = None,
    stock_code: Optional[str] = None,
    respect_regime: bool = False,
    config: Optional[dict] = None,
    include_alpha_tc: bool = False,
    keep_dates: Optional[Collection[str]] = None,
    atc_out: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Optional[float]]], List[float], List[str], List[Dict[str, Any]]]:
    """单票 ŷ_τc 面板（τ=open，标签 close/open−1）。

    返回 ``(xs, ys, decision_dates, meta_rows)``。
    ``decision_dates`` = 交易日 T（开盘决策日）；ATR 窗口截止 T-1。
    只写 Z 特征（缺口 / ATR）；日线因子已在 ŷ_oo，此处不算。
    ``include_alpha_tc=True`` 时附加 τ 锚 ``raw_atc_*``（现网 ŷ_τc）。
    ``meta_rows`` 含 ``y_τc``。
    ``keep_dates``：给定时只保留这些决策日（尾窗增量）。
    ``atc_out``：给定时不把 ``raw_atc_*`` 抄进每一行，改为写入
    ``atc_out["cols"]``（按日对齐的列）和 ``atc_out["index"]``（保留行在列中的下标，
    ``-1`` 表示该行已走逐日回退、列上不要覆盖）。
    """
    min_history = max(5, int(min_history or 12))
    max_window = max(min_history, int(max_window or 30))
    if include_alpha_tc:
        try:
            from core.signal.factors.alpha_tc import bump_window_for_alpha_tc

            min_history, max_window = bump_window_for_alpha_tc(
                ("alpha_tc",),
                min_history=min_history,
                max_window=max_window,
            )
        except Exception:  # noqa: BLE001
            logger.debug("tau open alpha_tc window bump failed", exc_info=True)
    _ = (index_bars, fundamentals, respect_regime, config)
    xs: List[Dict[str, Optional[float]]] = []
    ys: List[float] = []
    dates: List[str] = []
    metas: List[Dict[str, Any]] = []
    keep = None
    if keep_dates is not None:
        keep = {str(d)[:10] for d in keep_dates if str(d or "").strip()[:10]}

    n = len(bars or [])
    tau_map = realized_tau_by_date(bars)
    atc_items = None
    if (
        include_alpha_tc
        and int(min_history) >= int(max_window) >= 60
        and n > int(max_window)
    ):
        try:
            from core.signal.factors.alpha_tc import (
                ohlcv_from_bars,
                raw_atc_open_columns,
            )

            packed = ohlcv_from_bars(bars)
            if packed is not None:
                atc_cols = raw_atc_open_columns(*packed, hist=int(max_window))
                atc_items = _open_atc_items(atc_cols, n - int(max_window))
        except Exception:  # noqa: BLE001
            logger.debug("tau open raw_atc columns failed", exc_info=True)
            atc_items = None
    # i = index of day T；需要 i-1 有昨收。一根坏 K 线只丢掉这一天。
    atc_index: Optional[List[int]] = None
    if atc_out is not None:
        atc_out.clear()
        if atc_items is not None:
            atc_index = []
            atc_out["cols"] = atc_items
            atc_out["index"] = atc_index
    skipped_days = 0
    for i in range(min_history, n):
        b_t = bars[i]
        b_prev = bars[i - 1]
        if not isinstance(b_t, dict) or not isinstance(b_prev, dict):
            skipped_days += 1
            continue
        try:
            date_t = str(b_t.get("date") or "")[:10]
            if keep is not None and date_t not in keep:
                continue
            try:
                o = float(b_t.get("open"))
                c = float(b_t.get("close"))
                pc = float(b_prev.get("close"))
            except (TypeError, ValueError):
                continue
            if o <= 0 or c <= 0 or pc <= 0:
                continue
            y = y_r_pct(o, c)
            gap = _gap_pct(pc, o)
            if y is None or gap is None:
                continue
            # ATR 截止 T-1：窗口 bars[:i] 末根为 T-1
            window = bars[max(0, i - max_window) : i]
            if len(window) < min_history:
                continue
            lags = tau_lag_features(
                hist_bars=window,
                tau_by_date=tau_map,
                asof_date=date_t,
            )
            row: Dict[str, Optional[float]] = {
                "gap_pct": float(gap),
                "open_gap": float(gap),
                "gap_atr": gap_atr_from_hist(gap, window),
                "yclose_loc": yclose_loc_from_prev(b_prev, o),
            }
            for k in TAU_LAG_FEATURES:
                row[k] = lags.get(k)
            atc_ridx = -1
            if include_alpha_tc:
                filled = False
                if atc_items is not None:
                    try:
                        ridx = i - int(max_window)
                        if atc_index is not None:
                            col0 = atc_items[0][1]
                            if 0 <= ridx < int(getattr(col0, "shape", (0,))[0]):
                                atc_ridx = ridx
                                filled = True
                        else:
                            for key, col in atc_items:
                                num = _finite_raw(col[ridx])
                                if num is not None:
                                    row[key] = num
                            filled = True
                    except Exception:  # noqa: BLE001
                        logger.debug(
                            "tau open column day fallback i=%s", i, exc_info=True
                        )
                        for key in list(row):
                            if str(key).startswith("raw_atc_"):
                                row.pop(key, None)
                        atc_ridx = -1
                if not filled:
                    _inject_open_raw(row, window, b_prev, o)
            xs.append(row)
            if atc_index is not None:
                atc_index.append(atc_ridx)
            ys.append(float(y))
            dates.append(date_t)
            metas.append(
                {
                    "stock_code": stock_code,
                    "date": date_t,
                    "gap_pct": float(gap),
                    "open": o,
                    "close": c,
                    "prev_close": pc,
                    "y_τc": float(y),
                    "yclose_loc": row.get("yclose_loc"),
                }
            )
        except Exception:  # noqa: BLE001
            skipped_days += 1
            logger.debug("tau open day skipped i=%s", i, exc_info=True)
    if skipped_days:
        logger.warning(
            "tau open skipped %s bars code=%s", skipped_days, stock_code or ""
        )
    return xs, ys, dates, metas


def _optional_feat_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _row_or_meta_feat(
    xs_p: Sequence[dict], i: int, meta: dict, key: str
) -> Optional[float]:
    if i < len(xs_p) and isinstance(xs_p[i], dict):
        val = _optional_feat_float(xs_p[i].get(key))
        if val is not None:
            return val
    return _optional_feat_float(meta.get(key))


def _write_sector_horizon_ret(
    xs_row: dict,
    meta: Optional[dict],
    *,
    sret: Optional[float],
    ret_key: str,
    sector_key: str,
    vs_key: str,
) -> None:
    if sret is None:
        return
    s = round(float(sret), 6)
    xs_row[sector_key] = s
    r = xs_row.get(ret_key)
    if r is None and meta is not None:
        r = meta.get(ret_key)
    if r is not None:
        try:
            xs_row[vs_key] = round(float(r) - float(sret), 6)
        except (TypeError, ValueError):
            pass
    if meta is not None:
        meta[sector_key] = s
        if xs_row.get(vs_key) is not None:
            meta[vs_key] = xs_row[vs_key]


def attach_cross_section_breadth(
    panels: Sequence[Dict[str, Any]],
    *,
    gap_trigger_pct: float = 2.0,
    sector_map: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """多票面板：按日计算 gap 广度，写回每行 xs 的 sector_gap_breadth（全市场代理）。

    同时写 ``gap_vs_sector`` = 个股缺口 − 同行中位（同伴不足则减全截面中位）。
    ``sector_ret_to_tau`` 按 **(date, τ)** 聚合，避免变长前缀把不同时钟的开→τ 混中位。
    近窗 ``sector_ret_last_{30,45,60,75,90}m`` 同口径，避免 τ45/τ75 缺板块两因子。
    ``panels`` 元素：``{code, xs, ys, dates, metas}``。
    """
    # date -> [(code, gap), ...]  unique per (date, code)
    by_date: Dict[str, List[Tuple[str, float]]] = {}
    seen_gap: set = set()
    # (date, tau) -> ret_open_to_tau / ret_last_*m
    ret_by_date_tau: Dict[Tuple[str, str], List[float]] = {}
    horizon_ret_by_date_tau: Dict[str, Dict[Tuple[str, str], List[float]]] = {
        spec[0]: {} for spec in HORIZON_SEQ_CS_SPECS
    }
    for p in panels:
        code_p = str(p.get("code") or "").strip()
        xs_p = list(p.get("xs") or [])
        for i, m in enumerate(p.get("metas") or []):
            if not isinstance(m, dict):
                continue
            d = str(m.get("date") or "")[:10]
            g = m.get("gap_pct")
            c = str(m.get("stock_code") or code_p or "").strip()
            tau_k = str(m.get("tau") or "open").strip() or "open"
            if not d or g is None:
                continue
            gap_key = (d, c)
            if gap_key not in seen_gap:
                seen_gap.add(gap_key)
                by_date.setdefault(d, []).append((c, float(g)))
            rot = _row_or_meta_feat(xs_p, i, m, "ret_open_to_tau")
            if rot is not None:
                ret_by_date_tau.setdefault((d, tau_k), []).append(rot)
            for ret_key, _sector_key, _vs_key in HORIZON_SEQ_CS_SPECS:
                rv = _row_or_meta_feat(xs_p, i, m, ret_key)
                if rv is not None:
                    horizon_ret_by_date_tau[ret_key].setdefault((d, tau_k), []).append(
                        rv
                    )

    sm = sector_map
    if sm is None:
        try:
            from core.portfolio_optimize import load_sector_map

            sm = load_sector_map() or {}
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            sm = {}

    breadth_by_date: Dict[str, float] = {}
    theme_by_date: Dict[str, int] = {}
    gaps_by_date: Dict[str, List[float]] = {}
    ref_by_date: Dict[str, Dict[str, Optional[float]]] = {}
    sector_ret_by_date_tau: Dict[Tuple[str, str], Optional[float]] = {}
    sector_horizon_by_date_tau: Dict[str, Dict[Tuple[str, str], Optional[float]]] = {
        spec[0]: {} for spec in HORIZON_SEQ_CS_SPECS
    }
    trigger = float(gap_trigger_pct)
    from core.research.tau_theme import resolve_theme_day

    for d, pairs in by_date.items():
        if not pairs:
            continue
        gaps = [g for _c, g in pairs]
        gaps_by_date[d] = gaps
        # 大缺口广度：|gap|≥trigger（旧实现只计正缺口 → theme 几乎恒 0）
        hit = sum(1 for g in gaps if abs(float(g)) >= trigger)
        b = hit / len(gaps)
        breadth_by_date[d] = round(b, 4)
        theme_by_date[d] = int(
            resolve_theme_day(
                sector_breadth=b,
                pool_gaps=gaps,
                gap_trigger_pct=trigger,
            )
        )
        gaps_map: Dict[str, Optional[float]] = {}
        for c, g in pairs:
            if c:
                gaps_map[c] = g
        ref_by_date[d] = sector_gap_reference_by_code(gaps_map, sector_map=sm)

    for key, vals in ret_by_date_tau.items():
        sector_ret_by_date_tau[key] = _finite_median(vals)
    for ret_key, bucket in horizon_ret_by_date_tau.items():
        dest = sector_horizon_by_date_tau[ret_key]
        for key, vals in bucket.items():
            dest[key] = _finite_median(vals)

    out: List[Dict[str, Any]] = []
    for p in panels:
        code_p = str(p.get("code") or "").strip()
        xs = [dict(r) for r in (p.get("xs") or [])]
        metas = [dict(m) for m in (p.get("metas") or [])]
        dates = list(p.get("dates") or [])
        for i, d in enumerate(dates):
            b = breadth_by_date.get(d)
            th = theme_by_date.get(d, 0)
            code_i = code_p
            tau_k = "open"
            if i < len(metas):
                code_i = str(metas[i].get("stock_code") or code_p or "").strip()
                tau_k = str(metas[i].get("tau") or "open").strip() or "open"
            gap_i = None
            if i < len(xs) and xs[i].get("gap_pct") is not None:
                try:
                    gap_i = float(xs[i]["gap_pct"])
                except (TypeError, ValueError):
                    gap_i = None
            elif i < len(metas) and metas[i].get("gap_pct") is not None:
                try:
                    gap_i = float(metas[i]["gap_pct"])
                except (TypeError, ValueError):
                    gap_i = None
            ref = (ref_by_date.get(d) or {}).get(code_i)
            rel = gap_vs_sector_value(gap_i, ref)
            sret = sector_ret_by_date_tau.get((d, tau_k))
            # 日级主题 OR 本票大缺口（与 resolve_theme_day 对齐）
            th_row = int(
                resolve_theme_day(
                    gap_pct=gap_i,
                    sector_breadth=b,
                    pool_gaps=gaps_by_date.get(d),
                    gap_trigger_pct=trigger,
                )
            )
            if th_row:
                th = 1
            meta_i = metas[i] if i < len(metas) else None
            if i < len(xs):
                xs[i]["sector_gap_breadth"] = b
                xs[i]["theme_day"] = float(th)
                xs[i]["gap_vs_sector"] = rel
                if sret is not None:
                    xs[i]["sector_ret_to_tau"] = round(float(sret), 6)
                    rot = xs[i].get("ret_open_to_tau")
                    if rot is not None:
                        try:
                            xs[i]["ret_vs_sector"] = round(
                                float(rot) - float(sret), 6
                            )
                        except (TypeError, ValueError):
                            pass
                for ret_key, sector_key, vs_key in HORIZON_SEQ_CS_SPECS:
                    _write_sector_horizon_ret(
                        xs[i],
                        meta_i if isinstance(meta_i, dict) else None,
                        sret=sector_horizon_by_date_tau[ret_key].get((d, tau_k)),
                        ret_key=ret_key,
                        sector_key=sector_key,
                        vs_key=vs_key,
                    )
            elif isinstance(meta_i, dict):
                for ret_key, sector_key, _vs_key in HORIZON_SEQ_CS_SPECS:
                    sret_h = sector_horizon_by_date_tau[ret_key].get((d, tau_k))
                    if sret_h is not None:
                        meta_i[sector_key] = round(float(sret_h), 6)
            if i < len(metas):
                metas[i]["sector_gap_breadth"] = b
                metas[i]["theme_day"] = th
                metas[i]["gap_vs_sector"] = rel
                metas[i]["sector_gap_median"] = ref
                if sret is not None:
                    metas[i]["sector_ret_to_tau"] = round(float(sret), 6)
                    if i < len(xs) and xs[i].get("ret_vs_sector") is not None:
                        metas[i]["ret_vs_sector"] = xs[i]["ret_vs_sector"]
                # 近窗板块键由 _write_sector_horizon_ret 同步写 meta

        out.append({**p, "xs": xs, "metas": metas, "dates": dates})
    return out


# L2 组 Cube 时补写的截面裸名（与 attach_cross_section_breadth 对齐）
DAY_MATRIX_CS_KEYS: Tuple[str, ...] = (
    "sector_gap_breadth",
    "theme_day",
    "gap_vs_sector",
    "sector_ret_to_tau",
    "ret_vs_sector",
)


def _matrix_pick_col(
    names: Sequence[str],
    x: Any,
    *candidates: str,
) -> Optional[Any]:
    """按候选名取一日截面列；优先有有限值的列。"""
    import numpy as np

    pos = {str(n): j for j, n in enumerate(names)}
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim != 2:
        return None
    for name in candidates:
        j = pos.get(str(name))
        if j is None:
            continue
        col = arr[:, int(j)]
        if bool(np.isfinite(col).any()):
            return col
    return None


# open / co 入模截面（无分钟板块收益）
DAY_MATRIX_CS_OPEN_KEYS: Tuple[str, ...] = (
    "sector_gap_breadth",
    "theme_day",
    "gap_vs_sector",
)


def enrich_day_matrix_cross_section(
    codes: Sequence[str],
    names: Sequence[str],
    x: Any,
    *,
    clock: str = "open",
    gap_trigger_pct: float = 2.0,
    sector_map: Optional[Dict[str, str]] = None,
) -> Tuple[List[str], Any, List[str]]:
    """一日截面矩阵补 ``DAY_MATRIX_CS_KEYS``，口径同 ``attach_cross_section_breadth``。

    源列：``gap_pct`` / ``ret_open_to_tau``（裸名或 ``z_τh_*`` / ``z_τc_*`` / ``z_co_*``）。
    写出裸名后镜像 ``z_τh_*`` / ``z_τc_*``；开盘截面三列另镜像 ``z_co_*``。
    缺 ``gap_pct`` 源或内部异常则**原样返回**（不抛）。返回 ``(names, X, added_bare_keys)``。
    ``clock`` 仅作诊断/扩展保留，口径与面板一致不按钟分支。
    """
    import numpy as np

    name_list = [str(n) for n in (names or [])]
    code_list = [str(c).strip() for c in (codes or [])]
    try:
        arr = np.asarray(x, dtype=np.float64)
    except (TypeError, ValueError):
        return name_list, np.asarray(x) if x is not None else np.zeros((0, 0)), []
    if arr.ndim != 2 or arr.shape[0] != len(code_list) or arr.shape[1] != len(name_list):
        return name_list, arr, []
    if len(code_list) < 2:
        return name_list, arr, []

    try:
        return _enrich_day_matrix_cross_section_body(
            code_list,
            name_list,
            arr,
            gap_trigger_pct=gap_trigger_pct,
            sector_map=sector_map,
        )
    except Exception:  # noqa: BLE001
        logger.debug(
            "enrich_day_matrix_cross_section failed clock=%s n=%s",
            clock,
            len(code_list),
            exc_info=True,
        )
        return name_list, arr, []


def _enrich_day_matrix_cross_section_body(
    code_list: List[str],
    name_list: List[str],
    arr: Any,
    *,
    gap_trigger_pct: float,
    sector_map: Optional[Dict[str, str]],
) -> Tuple[List[str], Any, List[str]]:
    import numpy as np

    from core.signal.factors.z_co import Z_CO_PREFIX
    from core.signal.factors.z_tc import Z_TC_PREFIX
    from core.signal.factors.z_th import Z_TH_PREFIX

    gap_col = _matrix_pick_col(
        name_list,
        arr,
        "gap_pct",
        f"{Z_TH_PREFIX}gap_pct",
        f"{Z_TC_PREFIX}gap_pct",
        f"{Z_CO_PREFIX}gap_pct",
    )
    if gap_col is None:
        return name_list, arr, []

    ret_col = _matrix_pick_col(
        name_list,
        arr,
        "ret_open_to_tau",
        f"{Z_TH_PREFIX}ret_open_to_tau",
        f"{Z_TC_PREFIX}ret_open_to_tau",
    )

    gaps_map: Dict[str, Optional[float]] = {}
    gap_vals: List[float] = []
    for i, code in enumerate(code_list):
        g = float(gap_col[i]) if np.isfinite(gap_col[i]) else None
        if code:
            gaps_map[code] = g
        if g is not None:
            gap_vals.append(g)
    if len(gap_vals) < 2:
        return name_list, arr, []

    try:
        trigger = float(gap_trigger_pct)
    except (TypeError, ValueError):
        trigger = 2.0
    if not math.isfinite(trigger) or trigger < 0:
        trigger = 2.0

    hit = sum(1 for g in gap_vals if abs(float(g)) >= trigger)
    breadth = round(hit / len(gap_vals), 4)
    from core.research.tau_theme import resolve_theme_day

    theme_day = int(
        resolve_theme_day(
            sector_breadth=breadth,
            pool_gaps=gap_vals,
            gap_trigger_pct=trigger,
        )
    )
    sm = sector_map
    if sm is None:
        try:
            from core.portfolio_optimize import load_sector_map

            sm = load_sector_map() or {}
        except Exception:  # noqa: BLE001
            sm = {}
    try:
        ref_by_code = sector_gap_reference_by_code(gaps_map, sector_map=sm)
    except Exception:  # noqa: BLE001
        logger.debug("sector_gap_reference_by_code failed", exc_info=True)
        ref_by_code = {}

    sret: Optional[float] = None
    if ret_col is not None:
        rets = [
            float(ret_col[i])
            for i in range(len(code_list))
            if np.isfinite(ret_col[i])
        ]
        sret = _finite_median(rets)

    n = len(code_list)
    breadth_col = np.full(n, np.nan, dtype=np.float64)
    theme_col = np.full(n, np.nan, dtype=np.float64)
    gap_vs_col = np.full(n, np.nan, dtype=np.float64)
    sret_col = np.full(n, np.nan, dtype=np.float64)
    rvs_col = np.full(n, np.nan, dtype=np.float64)

    for i, code in enumerate(code_list):
        if not np.isfinite(gap_col[i]):
            continue
        gap_i = float(gap_col[i])
        try:
            th_row = int(
                resolve_theme_day(
                    gap_pct=gap_i,
                    sector_breadth=breadth,
                    pool_gaps=gap_vals,
                    gap_trigger_pct=trigger,
                )
            )
        except Exception:  # noqa: BLE001
            th_row = 0
        th = 1 if th_row else theme_day
        breadth_col[i] = breadth
        theme_col[i] = float(th)
        rel = gap_vs_sector_value(gap_i, ref_by_code.get(code) if code else None)
        if rel is not None:
            gap_vs_col[i] = float(rel)
        if sret is not None:
            sret_col[i] = round(float(sret), 6)
            if ret_col is not None and np.isfinite(ret_col[i]):
                rvs_col[i] = round(float(ret_col[i]) - float(sret), 6)

    bare_cols = {
        "sector_gap_breadth": breadth_col,
        "theme_day": theme_col,
        "gap_vs_sector": gap_vs_col,
        "sector_ret_to_tau": sret_col,
        "ret_vs_sector": rvs_col,
    }
    if sret is None:
        bare_cols.pop("sector_ret_to_tau", None)
        bare_cols.pop("ret_vs_sector", None)

    added: List[str] = []
    out_names = list(name_list)
    pos = {str(n): j for j, n in enumerate(out_names)}
    blocks = [arr[:, j] for j in range(arr.shape[1])]

    def _upsert(name: str, col: Any) -> None:
        nonlocal blocks
        col_arr = np.asarray(col, dtype=np.float64).reshape(-1)
        if col_arr.size != n or not bool(np.isfinite(col_arr).any()):
            return
        j = pos.get(name)
        if j is None:
            pos[name] = len(out_names)
            out_names.append(name)
            blocks.append(col_arr)
            added.append(name)
        else:
            blocks[j] = col_arr
            if name not in added:
                added.append(name)

    for bare, col in bare_cols.items():
        _upsert(bare, col)
        _upsert(f"{Z_TH_PREFIX}{bare}", col)
        _upsert(f"{Z_TC_PREFIX}{bare}", col)
        if bare in DAY_MATRIX_CS_OPEN_KEYS:
            _upsert(f"{Z_CO_PREFIX}{bare}", col)

    if not added:
        return name_list, arr, []
    out_x = np.column_stack(blocks)
    if out_x.ndim != 2 or out_x.shape[0] != n or out_x.shape[1] != len(out_names):
        return name_list, arr, []
    added_bare = [k for k in DAY_MATRIX_CS_KEYS if k in added]
    return out_names, out_x, added_bare


def theme_sample_weights(
    metas: Sequence[dict],
    *,
    theme_boost: float = 1.5,
    normal_weight: float = 1.0,
) -> List[float]:
    """R2：主题日样本提权（开盘可得 theme_day，无泄漏）。"""
    boost = max(0.1, float(theme_boost))
    base = max(0.1, float(normal_weight))
    weights: List[float] = []
    for m in metas:
        if int(m.get("theme_day") or 0) == 1:
            weights.append(boost)
        else:
            weights.append(base)
    return weights


def _minute_bar_day(b: dict) -> str:
    if not isinstance(b, dict):
        return ""
    d = str(b.get("date") or "")[:10]
    if len(d) == 10 and d[4] == "-":
        return d
    t = str(b.get("datetime") or b.get("time") or "")
    return t[:10] if len(t) >= 10 else ""


def _minute_bar_hm(b: dict) -> str:
    t = str(b.get("datetime") or b.get("time") or b.get("date") or "")
    for part in t.replace("T", " ").split(" "):
        if ":" in part:
            return part[:5].replace(":", "")
    return ""


def _minute_bar_matches_day(b: dict, day: str) -> bool:
    if _minute_bar_day(b) == day:
        return True
    return day in str(b.get("datetime") or b.get("time") or "")


def _index_minute_bars_by_date(
    minute_bars: Optional[Sequence[dict]],
) -> Dict[str, List[dict]]:
    """全历史分钟按日切一次。组面板按天×τ 查价时禁止每次扫整表。"""
    by: Dict[str, List[dict]] = {}
    for b in minute_bars or []:
        if not isinstance(b, dict):
            continue
        try:
            day = _minute_bar_day(b)
        except Exception:  # noqa: BLE001
            logger.debug("minute bar day skipped", exc_info=True)
            continue
        if len(day) < 10:
            continue
        by.setdefault(day, []).append(b)
    return by


def price_at_tau_from_minutes(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str = "10:30",
) -> Optional[float]:
    """R1：从分钟线取 τ 时刻价（≤τ 的最后一根）。无数据返回 None。"""
    day = str(trade_date or "")[:10]
    if not day or not minute_bars:
        return None
    target = tau_hm.replace(":", "")
    best = None
    best_hm = ""
    for b in minute_bars:
        if not isinstance(b, dict):
            continue
        if not _minute_bar_matches_day(b, day):
            continue
        hm = _minute_bar_hm(b)
        if not hm:
            continue
        if hm <= target and hm >= best_hm:
            try:
                px = float(b.get("close") or b.get("price"))
            except (TypeError, ValueError):
                continue
            if px > 0:
                best = px
                best_hm = hm
    return best


def price_at_tau_hm_exact(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str,
) -> Optional[float]:
    """仅当 ≤τ 的最后一根时钟恰好等于 τ 时取收价。"""
    day = str(trade_date or "")[:10]
    target = str(tau_hm or "").replace(":", "")
    if not day or not minute_bars or len(target) != 4 or not target.isdigit():
        return None
    best = None
    best_hm = ""
    for b in minute_bars:
        if not isinstance(b, dict):
            continue
        if not _minute_bar_matches_day(b, day):
            continue
        hm = _minute_bar_hm(b)
        if not hm:
            continue
        if hm <= target and hm >= best_hm:
            try:
                px = float(b.get("close") or b.get("price"))
            except (TypeError, ValueError):
                continue
            if px > 0:
                best = px
                best_hm = hm
    if best is None or best_hm != target:
        return None
    return best


def label_price_at_tau(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str,
) -> Optional[float]:
    """标签分母。非开盘钟必须精确命中 τ；开盘钟可退回会话开盘价。"""
    if is_open_minute_clock(tau_hm):
        px = price_at_tau_from_minutes(
            minute_bars, trade_date=trade_date, tau_hm=tau_hm
        )
        if px is not None and px > 0:
            return px
        o_min, _ = minute_session_open_close(minute_bars, trade_date=trade_date)
        if o_min and o_min > 0:
            return float(o_min)
        return None
    return price_at_tau_hm_exact(
        minute_bars, trade_date=trade_date, tau_hm=tau_hm
    )


def price_at_tau_plus_session(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str,
    add_min: int = HORIZON_T30_MIN,
) -> Tuple[Optional[str], Optional[float]]:
    """τ⊕N 交易分钟的时钟与该钟分钟收。不足剩余返回 (None, None)；缺根返回 (hm, None)。"""
    target = add_session_minutes(tau_hm, add_min)
    if not target:
        return None, None
    px = price_at_tau_hm_exact(minute_bars, trade_date=trade_date, tau_hm=target)
    return target, px


def price_at_horizon_mean_session(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str,
    offsets: Sequence[int],
) -> Tuple[Optional[str], Optional[float]]:
    """τ⊕N 终点价：offsets 三根 5m 收的均价。任一偏移越界或缺根则不给价。

    返回中心钟（offsets 中间项）与三根均价。
    """
    offs = tuple(int(x) for x in (offsets or ()))
    if not offs:
        return None, None
    clocks: List[str] = []
    for off in offs:
        hm = add_session_minutes(tau_hm, off)
        if not hm:
            return None, None
        clocks.append(hm)
    pxs: List[float] = []
    for hm in clocks:
        px = price_at_tau_hm_exact(minute_bars, trade_date=trade_date, tau_hm=hm)
        if px is None or px <= 0:
            center = clocks[len(clocks) // 2] if clocks else None
            return center, None
        pxs.append(float(px))
    center = clocks[len(clocks) // 2]
    return center, sum(pxs) / float(len(pxs))


def price_at_t30_mean_session(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str,
    offsets: Sequence[int] = T30_LABEL_OFFSETS,
) -> Tuple[Optional[str], Optional[float]]:
    """ŷ_τ30 终点价：τ⊕25 / τ⊕30 / τ⊕35 三根 5m 收的均价。"""
    return price_at_horizon_mean_session(
        minute_bars, trade_date=trade_date, tau_hm=tau_hm, offsets=offsets
    )


def price_at_t45_mean_session(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str,
    offsets: Sequence[int] = T45_LABEL_OFFSETS,
) -> Tuple[Optional[str], Optional[float]]:
    """ŷ_τ45 终点价：τ⊕40 / τ⊕45 / τ⊕50 三根 5m 收的均价。"""
    return price_at_horizon_mean_session(
        minute_bars, trade_date=trade_date, tau_hm=tau_hm, offsets=offsets
    )


def price_at_t60_mean_session(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str,
    offsets: Sequence[int] = T60_LABEL_OFFSETS,
) -> Tuple[Optional[str], Optional[float]]:
    """ŷ_τ60 终点价：τ⊕55 / τ⊕60 / τ⊕65 三根 5m 收的均价。"""
    return price_at_horizon_mean_session(
        minute_bars, trade_date=trade_date, tau_hm=tau_hm, offsets=offsets
    )


def price_at_t75_mean_session(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str,
    offsets: Sequence[int] = T75_LABEL_OFFSETS,
) -> Tuple[Optional[str], Optional[float]]:
    """ŷ_τ75 终点价：τ⊕70 / τ⊕75 / τ⊕80 三根 5m 收的均价。"""
    return price_at_horizon_mean_session(
        minute_bars, trade_date=trade_date, tau_hm=tau_hm, offsets=offsets
    )


def price_at_t90_mean_session(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
    tau_hm: str,
    offsets: Sequence[int] = T90_LABEL_OFFSETS,
) -> Tuple[Optional[str], Optional[float]]:
    """ŷ_τ90 终点价：τ⊕85 / τ⊕90 / τ⊕95 三根 5m 收的均价。"""
    return price_at_horizon_mean_session(
        minute_bars, trade_date=trade_date, tau_hm=tau_hm, offsets=offsets
    )


def minute_session_open_close(
    minute_bars: Sequence[dict],
    *,
    trade_date: str,
) -> Tuple[Optional[float], Optional[float]]:
    """同日分钟序列的开盘价（首根 open）与收盘价（末根 close）。"""
    day = str(trade_date or "")[:10]
    if not day or not minute_bars:
        return None, None
    first_hm = ""
    last_hm = ""
    open_px: Optional[float] = None
    close_px: Optional[float] = None
    for b in minute_bars:
        if not isinstance(b, dict):
            continue
        if _minute_bar_day(b) != day and day not in str(b.get("datetime") or ""):
            continue
        hm = _minute_bar_hm(b)
        if not hm:
            continue
        try:
            o = float(b.get("open") or 0.0) or None
            c = float(b.get("close") or b.get("price") or 0.0) or None
        except (TypeError, ValueError):
            continue
        if open_px is None or hm < first_hm:
            if o is not None and o > 0:
                open_px = o
                first_hm = hm
            elif c is not None and c > 0:
                open_px = c
                first_hm = hm
        if close_px is None or hm >= last_hm:
            if c is not None and c > 0:
                close_px = c
                last_hm = hm
    return open_px, close_px


def collect_tau_intraday_panel(
    bars: List[dict],
    minute_bars: Optional[List[dict]] = None,
    *,
    tau_hm: str = "10:30",
    tau_grid: Optional[Sequence[str]] = None,
    min_history: int = 12,
    max_window: int = 30,
    index_bars: Optional[List[dict]] = None,
    fundamentals: Optional[dict] = None,
    stock_code: Optional[str] = None,
    include_alpha_tc: bool = False,
    include_alpha_th: bool = False,
    label_heads: Optional[Sequence[str]] = None,
) -> Tuple[List[Dict[str, Optional[float]]], List[float], List[str], List[Dict[str, Any]]]:
    """分钟 τ 面板（变长前缀 / 少数时钟）：特征 ≤τ，标签 ŷ_τc = close[T]/price[τ]−1。

    τ=open 时 price[τ]=open，等于 close/open−1。
    ``tau_grid`` 非空时：同一交易日在多个 τ 各采一行（共享 β，**同日 y 随 τ 不同**）；
    默认网格 ``09:30|09:35|…|11:00`` 每 5m（09:30 无分钟前缀只留开盘 Z；与做 T v6 扫描至 11:00 对齐；不含 13:00 / 14:00）。
    ``include_alpha_tc=True`` 时按钟附加 τ 锚 ``raw_atc_*``（现网）。
    ``include_alpha_th=True`` 时按钟附加 ŷ_τ* ``raw_ath_*``。
    ``label_heads``：只写这些头的 ``price_tau{N}``（默认五档；单头拟合传 ``(\"t30\",)``）。
    """
    min_history = max(5, int(min_history or 12))
    max_window = max(min_history, int(max_window or 30))
    heads = normalize_label_heads(label_heads)
    head_set = set(heads)
    label_price_fns = tuple(
        (tag, fn)
        for tag, fn in (
            ("t30", price_at_t30_mean_session),
            ("t45", price_at_t45_mean_session),
            ("t60", price_at_t60_mean_session),
            ("t75", price_at_t75_mean_session),
            ("t90", price_at_t90_mean_session),
        )
        if tag in head_set
    )
    seq_specs = tuple(
        (tag, feat_keys, lag_keys)
        for tag, feat_keys, lag_keys in (
            ("t30", T30_SEQ_FEATURES, T30_LAG_FEATURES),
            ("t45", T45_SEQ_FEATURES, T45_LAG_FEATURES),
            ("t60", T60_SEQ_FEATURES, T60_LAG_FEATURES),
            ("t75", T75_SEQ_FEATURES, T75_LAG_FEATURES),
            ("t90", T90_SEQ_FEATURES, T90_LAG_FEATURES),
        )
        if tag in head_set
    )
    if include_alpha_tc:
        try:
            from core.signal.factors.alpha_tc import bump_window_for_alpha_tc

            min_history, max_window = bump_window_for_alpha_tc(
                ("alpha_tc",),
                min_history=min_history,
                max_window=max_window,
            )
        except Exception:  # noqa: BLE001
            logger.debug("tau intraday alpha_tc window bump failed", exc_info=True)
    if include_alpha_th:
        try:
            from core.signal.factors.alpha_th import bump_window_for_alpha_th

            min_history, max_window = bump_window_for_alpha_th(
                ("alpha_th",),
                min_history=min_history,
                max_window=max_window,
            )
        except Exception:  # noqa: BLE001
            logger.debug("tau intraday alpha_th window bump failed", exc_info=True)
    _ = (index_bars, fundamentals)
    clocks = normalize_minute_tau_grid(tau_hm=tau_hm, tau_grid=tau_grid)
    xs: List[Dict[str, Optional[float]]] = []
    ys: List[float] = []
    dates: List[str] = []
    metas: List[Dict[str, Any]] = []
    n = len(bars or [])
    tau_map = realized_tau_by_date(bars)
    minutes_by_day = _index_minute_bars_by_date(minute_bars)
    empty_day: List[dict] = []
    atc_by_clock: Dict[str, Tuple[Tuple[str, Any], ...]] = {}
    if (
        include_alpha_tc
        and int(min_history) >= int(max_window) >= 60
        and n > int(max_window)
    ):
        try:
            atc_by_clock = _minute_clocks_atc_items(
                bars,
                minutes_by_day,
                hist=int(max_window),
                clocks=clocks,
            )
        except Exception:  # noqa: BLE001
            logger.debug("tau minute columns failed", exc_info=True)
            atc_by_clock = {}
    skipped_days = 0
    skipped_clocks = 0
    from core.signal.minute_tau_feats import (
        extract_horizon_seq_packs_for_clocks,
        extract_minute_tau_packs,
    )

    for i in range(min_history, n):
        b_t = bars[i]
        b_prev = bars[i - 1]
        if not isinstance(b_t, dict) or not isinstance(b_prev, dict):
            skipped_days += 1
            continue
        date_t = str(b_t.get("date") or "")[:10]
        date_prev = str(b_prev.get("date") or "")[:10]
        try:
            o = float(b_t.get("open"))
            c = float(b_t.get("close"))
            pc = float(b_prev.get("close"))
        except (TypeError, ValueError):
            continue
        if o <= 0 or c <= 0 or pc <= 0:
            continue
        try:
            day_mins = minutes_by_day.get(date_t) or empty_day
            prev_mins = minutes_by_day.get(date_prev) or empty_day
            o_min, c_min = minute_session_open_close(day_mins, trade_date=date_t)
            gap = _gap_pct(pc, o)
            window = bars[max(0, i - max_window) : i]
            if len(window) < min_history:
                continue
            _, c_prev_min = minute_session_open_close(prev_mins, trade_date=date_prev)
            lags = tau_lag_features(
                hist_bars=window,
                tau_by_date=tau_map,
                asof_date=date_t,
            )
        except Exception:  # noqa: BLE001
            skipped_days += 1
            logger.debug("tau intraday day skipped date=%s", date_t, exc_info=True)
            continue
        open_for_pack = o_min if o_min and o_min > 0 else o
        prev_for_pack = c_prev_min if c_prev_min and c_prev_min > 0 else None
        tau_packs: Optional[Dict[str, Dict[str, float]]] = None
        seq_packs: Optional[Dict[str, Dict[str, float]]] = None
        try:
            tau_packs = extract_minute_tau_packs(
                day_mins,
                trade_date=date_t,
                clocks=clocks,
                open_px=open_for_pack,
                prev_close=prev_for_pack,
            )
        except Exception:  # noqa: BLE001
            logger.debug("tau day packs failed date=%s", date_t, exc_info=True)
            tau_packs = None
        try:
            seq_packs = extract_horizon_seq_packs_for_clocks(
                day_mins,
                trade_date=date_t,
                clocks=clocks,
                open_px=open_for_pack,
            )
        except Exception:  # noqa: BLE001
            logger.debug("tau horizon packs failed date=%s", date_t, exc_info=True)
            seq_packs = None
        for clock in clocks:
            try:
                _ = _emit_intraday_clock(
                    clock=clock,
                    xs=xs,
                    ys=ys,
                    dates=dates,
                    metas=metas,
                    day_mins=day_mins,
                    date_t=date_t,
                    o=o,
                    c=c,
                    pc=pc,
                    o_min=o_min,
                    c_min=c_min,
                    c_prev_min=c_prev_min,
                    gap=gap,
                    window=window,
                    lags=lags,
                    stock_code=stock_code,
                    include_alpha_tc=include_alpha_tc,
                    include_alpha_th=include_alpha_th,
                    label_price_fns=label_price_fns,
                    seq_specs=seq_specs,
                    atc_items=atc_by_clock.get(clock),
                    atc_ridx=i - int(max_window),
                    tau_pack=(
                        tau_packs.get(clock) if tau_packs is not None else None
                    ),
                    seq_pack=(
                        seq_packs.get(clock) if seq_packs is not None else None
                    ),
                )
            except Exception:  # noqa: BLE001
                skipped_clocks += 1
                logger.debug(
                    "tau intraday clock skipped date=%s tau=%s",
                    date_t,
                    clock,
                    exc_info=True,
                )
    if skipped_days or skipped_clocks:
        logger.warning(
            "tau intraday skipped days=%s clocks=%s code=%s",
            skipped_days,
            skipped_clocks,
            stock_code or "",
        )
    return xs, ys, dates, metas


def _emit_intraday_clock(
    *,
    clock: str,
    xs: List[Dict[str, Optional[float]]],
    ys: List[float],
    dates: List[str],
    metas: List[Dict[str, Any]],
    day_mins: Sequence[dict],
    date_t: str,
    o: float,
    c: float,
    pc: float,
    o_min: Optional[float],
    c_min: Optional[float],
    c_prev_min: Optional[float],
    gap: Optional[float],
    window: Sequence[dict],
    lags: Mapping[str, Any],
    stock_code: Optional[str],
    include_alpha_tc: bool,
    include_alpha_th: bool,
    label_price_fns: Sequence[Tuple[str, Any]],
    seq_specs: Sequence[Tuple[str, Any, Any]],
    atc_items: Optional[Sequence[Tuple[str, Any]]] = None,
    atc_ridx: Optional[int] = None,
    tau_pack: Optional[Dict[str, float]] = None,
    seq_pack: Optional[Dict[str, float]] = None,
) -> None:
    """写入一个钟的一行。缺价或前缀不够时直接返回；计算失败由调用方丢掉这个钟。"""
    from core.signal.minute_tau_feats import extract_horizon_seq_packs, extract_minute_tau_pack

    open_clock = is_open_minute_clock(clock)
    if not open_clock:
        from core.signal.minute_tau_feats import prefix_has_tau_clock

        if not prefix_has_tau_clock(day_mins, trade_date=date_t, tau_hm=clock):
            return
    px_tau = label_price_at_tau(day_mins, trade_date=date_t, tau_hm=clock)
    if (px_tau is None or px_tau <= 0) and open_clock:
        # 当日须有分钟会话（5m 通常从 09:35 起）；无缓存日不混进 09:30
        if not (o_min and o_min > 0):
            return
        px_tau = o_min
    if px_tau is None or px_tau <= 0:
        return
    y_τc = y_r_pct(px_tau, c)
    if y_τc is None:
        return
    if tau_pack is not None:
        pack = tau_pack
    else:
        pack = extract_minute_tau_pack(
            day_mins,
            trade_date=date_t,
            tau_hm=clock,
            open_px=o_min if o_min and o_min > 0 else o,
            prev_close=c_prev_min if c_prev_min and c_prev_min > 0 else None,
        )
    if len(pack) < 2:
        if not open_clock:
            return
        pack = {}
    row: Dict[str, Optional[float]] = {
        "gap_pct": float(gap) if gap is not None else None,
        "open_gap": float(gap) if gap is not None else None,
        "gap_atr": gap_atr_from_hist(gap, window),
    }
    for k in TAU_LAG_FEATURES:
        row[k] = lags.get(k)
    row.update(pack)
    if "ret_open_to_tau" not in row:
        open_for_ret = float(o_min) if o_min and o_min > 0 else float(o)
        if open_for_ret > 0:
            row["ret_open_to_tau"] = (px_tau / open_for_ret - 1.0) * 100.0
    elapsed = tau_elapsed_min_from_open(clock)
    if elapsed is not None:
        row["tau_elapsed_min"] = elapsed
    if seq_pack is not None:
        seq_all = seq_pack
    else:
        seq_all = extract_horizon_seq_packs(
            day_mins,
            trade_date=date_t,
            tau_hm=clock,
            open_px=o_min if o_min and o_min > 0 else o,
        )
    if not isinstance(seq_all, dict):
        seq_all = {}
    atc_filled = False
    if include_alpha_tc and atc_items is not None and atc_ridx is not None:
        atc_filled = _fill_column_row(row, atc_items, int(atc_ridx))
    if include_alpha_tc and not atc_filled:
        from core.signal.factors.alpha_tc import inject_raw_atc

        inject_raw_atc(
            row,
            window,
            open_t=o,
            prev_close=pc,
            price_tau=px_tau,
            minute_bars=day_mins,
            tau_hm=clock,
        )
    if include_alpha_th:
        try:
            from core.signal.factors.alpha_th import _promote_feats_to_ath, inject_raw_ath

            # seq_all 已并入 row；升格短窗动量；H_LAG* 在 relabel 后 stamp
            if isinstance(seq_all, dict):
                for sk, sv in seq_all.items():
                    if sv is not None and row.get(sk) is None:
                        row[sk] = sv
            if atc_filled:
                _copy_ath_gap_from_atc(row)
                _promote_feats_to_ath(row, row)
            else:
                inject_raw_ath(
                    row,
                    window,
                    open_t=o,
                    prev_close=pc,
                    price_tau=px_tau,
                    minute_bars=day_mins,
                    tau_hm=clock,
                    feats=row,
                )
        except Exception:  # noqa: BLE001
            logger.debug("tau ath day fallback date=%s", date_t, exc_info=True)
            for key in list(row):
                if str(key).startswith("raw_ath_"):
                    row.pop(key, None)
            try:
                from core.signal.factors.alpha_th import inject_raw_ath

                inject_raw_ath(
                    row,
                    window,
                    open_t=o,
                    prev_close=pc,
                    price_tau=px_tau,
                    minute_bars=day_mins,
                    tau_hm=clock,
                    feats=row,
                )
            except Exception:  # noqa: BLE001
                logger.debug("tau ath inject failed date=%s", date_t, exc_info=True)
    meta = {
        "stock_code": stock_code,
        "date": date_t,
        "tau": clock,
        "gap_pct": gap,
        "price_tau": px_tau,
        "open": o,
        "close": c,
        "open_minute": o_min,
        "close_minute": c_min,
        "y_τc": float(y_τc),
        "ret_open_to_tau": row.get("ret_open_to_tau"),
        "tau_elapsed_min": elapsed,
    }
    for tag, fn in label_price_fns:
        try:
            spec = _HORIZON_SPECS[tag]
            tau_hm_h, px_h = fn(
                day_mins,
                trade_date=date_t,
                tau_hm=clock,
            )
            meta[spec.tau_plus_key] = tau_hm_h
            meta[spec.px_key] = px_h
        except Exception:  # noqa: BLE001
            logger.debug(
                "tau horizon meta skipped date=%s tau=%s head=%s",
                date_t,
                clock,
                tag,
                exc_info=True,
            )
    if isinstance(seq_all, dict):
        for _tag, feat_keys, lag_keys in seq_specs:
            try:
                skip = set(lag_keys)
                for k in feat_keys:
                    if k in skip:
                        continue
                    if seq_all.get(k) is not None:
                        meta[k] = seq_all[k]
            except Exception:  # noqa: BLE001
                logger.debug(
                    "tau seq meta skipped date=%s tau=%s",
                    date_t,
                    clock,
                    exc_info=True,
                )
    xs.append(row)
    ys.append(float(y_τc))
    dates.append(date_t)
    metas.append(meta)

