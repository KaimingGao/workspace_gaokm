"""开→开专用因子集 alpha_oo（约 206 列，前缀 ``raw_aoo_``）。

PIT：``hist=≤T−1`` 完整日 + ``open_t``（今开）。标签对齐 open[T+1]/open[T]−1。
单票可算；板块截面类不在此模块（留给面板 Z）。

计算产出全量 ``raw_aoo_*``；**ŷ_oo / ŷ_co Ridge·Tree 入模默认
``z_oo_* ∪ raw_aoo_*``（全量）**。精简集 aoo_core=GAP+YK+LAG 仅供消融
（``ridge_core_only=True`` / ``apply_aoo_core_ridge_policy``）。
ŷ_τc 现网为 **alpha_τc := z_τc_* ∪ raw_atc_***；ŷ_τ* 为 **alpha_τh := z_τh_* ∪ raw_ath_***；均不吃本包。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_WINDOWS: Tuple[int, ...] = (5, 10, 20, 30, 60)
_MIN_BARS: int = 61
_EPS: float = 1e-12

ALPHA_OO_FACTOR_KEY = "alpha_oo"
ALPHA_OO_RAW_PREFIX = "raw_aoo_"
ALPHA_OO_MIN_BARS = _MIN_BARS
ALPHA_OO_PANEL_WINDOW = _MIN_BARS + 1

_YK_KEYS = (
    "YKMID",
    "YKLEN",
    "YKMID2",
    "YKUP",
    "YKUP2",
    "YKLOW",
    "YKLOW2",
    "YKSFT",
    "YKSFT2",
    "YBODY",
    "YUPPER_SHARE",
    "YLOWER_SHARE",
)

_GAP_KEYS = (
    "GAP",
    "GAP_ABS",
    "CLOSE0",
    "YHIGH0",
    "YLOW0",
    "YVWAP0",
    "YOPEN0",
    "OPEN_IN_YRANGE",
    "GAP_VS_YRANGE",
    "GAP_ATR",
    "ABOVE_YHIGH",
    "BELOW_YLOW",
    "GAP_UP",
    "GAP_DN",
    "NEAR_UPLIMIT",
    "NEAR_DNLIMIT",
    "YCLOSE_LOC",
    "GAP_X_YVOL",
    "GAP_X_YRANGE",
    "YOPEN_RET",
    "YRANGE_PCT",  # 昨振幅 (H−L)/C；覆盖原 CO Z「昨振幅%」（比例，非百分点）
    "YVOL_Z20",
    "GAP_VS_OOMA5",
    "GAP_VS_OOMA20",
)

_ROLL_OPS = (
    "ROC",
    "MA",
    "STD",
    "BETA",
    "RSQR",
    "RESI",
    "MAX",
    "LOW",
    "QTLU",
    "QTLD",
    "RANK",
    "RSV",
    "IMAX",
    "IMIN",
    "IMXD",
    "CORR",
    "CORD",
    "CNTP",
    "CNTN",
    "CNTD",
    "SUMP",
    "SUMN",
    "SUMD",
)

_VOL_OPS = (
    "VMA",
    "VSTD",
    "WVMA",
    "VSUMP",
    "VSUMN",
    "VSUMD",
    "VROC",
    "VRAMA",
)

_LAG_KEYS = (
    "OO_LAG1",
    "OO_LAG2",
    "OO_LAG3",
    "OO_MA5",
    "OO_STD5",
    "CC_LAG1",
    "OC_LAG1",
    "OO_SIGN_MA5",
    "MOM3_O",
    "MOM3_C",  # 近 3 日收盘动量 close[T−1]/close[T−4]−1；原 CO/τ Z「近3日动量%」
    "REV1_O",
    "VOL_SHOCK",
    "RANGE_SHOCK",
    "OO_MA10",
    "OO_STD10",
)


def _roll_key(op: str, d: int) -> str:
    return f"O{op}{d}"


def _vol_key(op: str, d: int) -> str:
    return f"O{op}{d}"


ALPHA_OO_FIELD_NAMES: Tuple[str, ...] = (
    *_GAP_KEYS,
    *_YK_KEYS,
    *(_roll_key(op, d) for d in _WINDOWS for op in _ROLL_OPS),
    *(_vol_key(op, d) for d in _WINDOWS for op in _VOL_OPS),
    *_LAG_KEYS,
)

# 入模精简集：去掉多窗滚动/量能（Holdout 300/160/20 下全量拖累 IC）
# 精确别名不进 β：OC_LAG1≡YKMID；REV1_O≡−OO_LAG1（否则 Ridge 共线爆炸）
_AOO_CORE_LAG_DROP: frozenset = frozenset({"OC_LAG1", "REV1_O"})
ALPHA_OO_CORE_FIELD_NAMES: Tuple[str, ...] = (
    *_GAP_KEYS,
    *_YK_KEYS,
    *(k for k in _LAG_KEYS if k not in _AOO_CORE_LAG_DROP),
)

# 有 aoo_core 时剔除的 z_oo 裸名（与 GAP / MOM3 / 短动量双计）
Z_OO_DROP_WHEN_AOO_CORE: frozenset = frozenset(
    {"momentum", "gap_risk", "overheat"}
)


def is_alpha_oo_raw_key(name: str) -> bool:
    return str(name or "").startswith(ALPHA_OO_RAW_PREFIX)


def alpha_oo_ridge_raw_names() -> Tuple[str, ...]:
    """ŷ_oo / ŷ_co 入模用的 ``raw_aoo_*``（GAP+YK+LAG）。"""
    return tuple(f"{ALPHA_OO_RAW_PREFIX}{k}" for k in ALPHA_OO_CORE_FIELD_NAMES)


_ALPHA_OO_RIDGE_RAW_SET = frozenset(alpha_oo_ridge_raw_names())


def is_alpha_oo_ridge_raw_key(name: str) -> bool:
    """是否为现网 Ridge/Tree 允许的 ``raw_aoo_*`` 核心列。"""
    return str(name or "") in _ALPHA_OO_RIDGE_RAW_SET


def collect_alpha_oo_raw_keys_from_rows(
    xs: Sequence[dict],
    *,
    ridge_core_only: bool = False,
) -> List[str]:
    """从面板行收集出现过的 ``raw_aoo_*`` 列名（保序去重）。

    默认收全量；``ridge_core_only=True`` 只收 aoo_core（消融）。
    """
    names: List[str] = []
    seen = set()
    for row in xs or []:
        if not isinstance(row, dict):
            continue
        for k, v in row.items():
            sk = str(k)
            if sk in seen or not is_alpha_oo_raw_key(sk):
                continue
            if ridge_core_only and not is_alpha_oo_ridge_raw_key(sk):
                continue
            if v is None:
                continue
            try:
                float(v)
            except (TypeError, ValueError):
                continue
            seen.add(sk)
            names.append(sk)
    return names


def drop_aoo_overlap_z_oo(names: Sequence[str]) -> List[str]:
    """有 aoo_core（如 ``raw_aoo_GAP*``）时丢掉与之双计的 ``z_oo_*``。

    只动 ``momentum`` / ``gap_risk`` / ``overheat``；保留全部 ``raw_aoo_*``。
    Ridge 与 Tree 入模列共用，避免 Tree 再把 ``z_oo_gap_risk`` 当成主分裂。
    """
    from core.signal.factors.z_oo import is_z_oo_key, to_legacy_key

    has_core = any(is_alpha_oo_ridge_raw_key(n) for n in names or [])
    if not has_core:
        return [str(n) for n in names or [] if str(n or "").strip()]
    out: List[str] = []
    seen = set()
    for n in names or []:
        k = str(n or "").strip()
        if not k or k in seen:
            continue
        if is_z_oo_key(k) and to_legacy_key(k) in Z_OO_DROP_WHEN_AOO_CORE:
            continue
        seen.add(k)
        out.append(k)
    return out


def apply_aoo_core_ridge_policy(names: Sequence[str]) -> List[str]:
    """入模列策略：只留 aoo_core；有 core 时丢掉重叠 z_oo。"""
    from core.signal.factors.z_oo import is_z_oo_key, to_legacy_key

    has_core = any(is_alpha_oo_ridge_raw_key(n) for n in names or [])
    drop_legs = Z_OO_DROP_WHEN_AOO_CORE if has_core else frozenset()
    out: List[str] = []
    seen = set()
    for n in names or []:
        k = str(n or "").strip()
        if not k or k in seen:
            continue
        if is_alpha_oo_raw_key(k):
            if not is_alpha_oo_ridge_raw_key(k):
                continue
        else:
            leg = to_legacy_key(k) if is_z_oo_key(k) else k
            if leg in drop_legs:
                continue
        seen.add(k)
        out.append(k)
    return out


def expand_ridge_feature_names(
    xs: Sequence[dict],
    *,
    base_names: Optional[Sequence[str]] = None,
    ridge_core_only: bool = False,
) -> List[str]:
    """Ridge 入模列：注册日线 ``z_oo_*`` + 面板 ``raw_aoo_*``；去掉因子包常数分。

    默认全量 ``raw_aoo_*``，但有 aoo_core 时仍丢掉重叠 ``z_oo``（gap_risk 等）。
    ``ridge_core_only=True`` 时再收成只留 aoo_core。
    """
    from core.signal.factors.meta.registry import registered_factor_names
    from core.signal.factors.z_oo import (
        Z_OO_SKIP_PACKS,
        is_z_oo_key,
        to_z_oo_key,
    )

    base = list(base_names) if base_names is not None else list(registered_factor_names())
    names: List[str] = []
    seen = set()
    for n in base:
        k = str(n or "").strip()
        if not k or k in Z_OO_SKIP_PACKS:
            continue
        if is_alpha_oo_raw_key(k):
            canon = k
        elif is_z_oo_key(k):
            canon = k
        else:
            canon = to_z_oo_key(k)
        if canon not in seen:
            seen.add(canon)
            names.append(canon)
    for k in collect_alpha_oo_raw_keys_from_rows(
        xs, ridge_core_only=ridge_core_only
    ):
        if k not in seen:
            seen.add(k)
            names.append(k)
    if ridge_core_only:
        return apply_aoo_core_ridge_policy(names)
    return drop_aoo_overlap_z_oo(names)


def bump_window_for_alpha_oo(
    factor_names: Optional[Sequence[str]],
    *,
    min_history: int,
    max_window: int,
) -> Tuple[int, int]:
    """因子集含 alpha_oo / raw_aoo_* 时抬高窗口。"""
    names = {str(n).strip() for n in (factor_names or []) if str(n).strip()}
    if ALPHA_OO_FACTOR_KEY not in names and not any(
        is_alpha_oo_raw_key(n) for n in names
    ):
        return int(min_history), int(max_window)
    need = int(ALPHA_OO_PANEL_WINDOW)
    return max(int(min_history), need), max(int(max_window), need)


def inject_raw_aoo(
    dest: Optional[dict],
    bars: Optional[Sequence[dict]],
    *,
    open_t: Optional[float] = None,
    prev_close: Optional[float] = None,
    quote: Optional[dict] = None,
) -> dict:
    """把 ``raw_aoo_*`` 写入 ``dest``（就地；失败则原样返回）。"""
    out = dest if isinstance(dest, dict) else {}
    try:
        extras = raw_aoo_from_bars(
            bars, open_t=open_t, prev_close=prev_close, quote=quote
        )
    except Exception:  # noqa: BLE001
        logger.debug("alpha_oo inject failed", exc_info=True)
        return out
    if extras:
        out.update(extras)
    return out


def keep_aoo_raw_in_row(
    src: Optional[dict],
    *,
    dest: Optional[dict] = None,
) -> dict:
    """把 ``src`` 中的 ``raw_aoo_*`` 拷进 ``dest``。"""
    out = dest if isinstance(dest, dict) else {}
    for k, v in (src or {}).items():
        if is_alpha_oo_raw_key(str(k)) and v is not None and v != "":
            out[str(k)] = v
    return out


def _fpx(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        n = float(str(v).replace("元", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if n != n or n <= 0:
        return None
    return n


def resolve_open_t(
    *,
    open_t: Optional[float] = None,
    quote: Optional[dict] = None,
    bars: Optional[Sequence[dict]] = None,
) -> Optional[float]:
    ot = _fpx(open_t)
    if ot is not None:
        return ot
    if isinstance(quote, dict):
        for key in ("open", "open_raw", "today_open"):
            ot = _fpx(quote.get(key))
            if ot is not None:
                return ot
    if bars:
        ot = _fpx(bars[-1].get("open"))
        if ot is not None:
            return ot
    return None


def resolve_prev_close(
    *,
    prev_close: Optional[float] = None,
    quote: Optional[dict] = None,
    hist_complete: Optional[Sequence[dict]] = None,
) -> Optional[float]:
    pc = _fpx(prev_close)
    if pc is not None:
        return pc
    if isinstance(quote, dict):
        for key in ("prev_close", "pre_close", "yesterday_close"):
            pc = _fpx(quote.get(key))
            if pc is not None:
                return pc
    hist = [b for b in (hist_complete or []) if isinstance(b, dict)]
    if hist:
        return _fpx(hist[-1].get("close"))
    return None


def _to_arrays(
    bars: List[dict],
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n = len(bars)
    close = np.zeros(n, dtype=np.float64)
    open_ = np.zeros(n, dtype=np.float64)
    high = np.zeros(n, dtype=np.float64)
    low = np.zeros(n, dtype=np.float64)
    volume = np.zeros(n, dtype=np.float64)
    for i, b in enumerate(bars):
        close[i] = float(b.get("close") or 0.0)
        open_[i] = float(b.get("open") or 0.0)
        high[i] = float(b.get("high") or 0.0)
        low[i] = float(b.get("low") or 0.0)
        volume[i] = float(b.get("volume") or 0.0)
    return close, open_, high, low, volume


def _safe_corr(x: np.ndarray, y: np.ndarray) -> Optional[float]:
    if len(x) < 2:
        return None
    sx = float(x.std())
    sy = float(y.std())
    if sx < _EPS or sy < _EPS:
        return None
    cov = float(((x - x.mean()) * (y - y.mean())).mean())
    return cov / (sx * sy)


def _linear_fit(y: np.ndarray) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    n = len(y)
    if n < 2:
        return None, None, None
    x = np.arange(n, dtype=np.float64)
    xm = x.mean()
    ym = y.mean()
    denom = float(((x - xm) ** 2).sum())
    if denom < _EPS:
        return None, None, None
    slope = float(((x - xm) * (y - ym)).sum() / denom)
    intercept = float(ym - slope * xm)
    y_pred = intercept + slope * x
    ss_res = float(((y - y_pred) ** 2).sum())
    ss_tot = float(((y - ym) ** 2).sum())
    rsquare = 1.0 - ss_res / (ss_tot + _EPS) if ss_tot > _EPS else 0.0
    residual = float(y[-1] - y_pred[-1])
    return slope, rsquare, residual


def _rank_percentile(current: float, series: np.ndarray) -> Optional[float]:
    if len(series) == 0:
        return None
    return float((series < current).sum()) / len(series)


def _yk_fields(close: float, open_: float, high: float, low: float) -> dict:
    rng = high - low
    mx_oc = max(open_, close)
    mn_oc = min(open_, close)
    # 昨 K 无振幅时勿用 _EPS 当分母（会把价差放大到 ~1e12）
    if rng > _EPS:
        mid2 = (close - open_) / rng
        up2 = (high - mx_oc) / rng
        low2 = (mn_oc - low) / rng
        sft2 = (2.0 * close - high - low) / rng
        upper = (high - mx_oc) / rng
        lower = (mn_oc - low) / rng
    else:
        mid2 = up2 = low2 = sft2 = upper = lower = 0.0
    return {
        "YKMID": (close - open_) / (open_ + _EPS),
        "YKLEN": (high - low) / (open_ + _EPS),
        "YKMID2": mid2,
        "YKUP": (high - mx_oc) / (open_ + _EPS),
        "YKUP2": up2,
        "YKLOW": (mn_oc - low) / (open_ + _EPS),
        "YKLOW2": low2,
        "YKSFT": (2.0 * close - high - low) / (open_ + _EPS),
        "YKSFT2": sft2,
        "YBODY": abs(close - open_) / (open_ + _EPS),
        "YUPPER_SHARE": upper,
        "YLOWER_SHARE": lower,
    }


def _atr_pct(high: np.ndarray, low: np.ndarray, close: np.ndarray, window: int = 14) -> float:
    n = len(close)
    if n < 2:
        return _EPS
    w = min(window, n - 1)
    trs = []
    for i in range(n - w, n):
        pc = float(close[i - 1])
        h = float(high[i])
        l = float(low[i])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr / (pc + _EPS))
    return float(np.mean(trs)) if trs else _EPS


def _gap_fields(
    open_t: float,
    prev_close: float,
    prev_open: float,
    prev_high: float,
    prev_low: float,
    prev_vol: float,
    open_arr: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
) -> dict:
    """隔夜/今开位置；open_arr/high/… 含决策日占位根。"""
    gap = open_t / (prev_close + _EPS) - 1.0
    y_rng = prev_high - prev_low
    y_vwap = (prev_high + prev_low + prev_close) / 3.0
    atr = _atr_pct(high[:-1], low[:-1], close[:-1], 14)
    # 完整日开序列（不含决策占位）上的 oo
    o_hist = open_arr[:-1]
    oo = np.zeros(len(o_hist), dtype=np.float64)
    if len(o_hist) >= 2:
        oo[1:] = o_hist[1:] / (o_hist[:-1] + _EPS) - 1.0
    oo_ma5 = float(oo[-5:].mean()) if len(oo) >= 5 else 0.0
    oo_ma20 = float(oo[-20:].mean()) if len(oo) >= 20 else oo_ma5
    vol_hist = volume[:-1]
    v_mean20 = float(vol_hist[-20:].mean()) if len(vol_hist) >= 20 else float(prev_vol)
    v_std20 = float(vol_hist[-20:].std()) if len(vol_hist) >= 20 else _EPS
    yvol_z = (prev_vol - v_mean20) / (v_std20 + _EPS)
    # A 股约 ±10% 涨跌停（简化，不含 ST）
    up_lim = prev_close * 1.10
    dn_lim = prev_close * 0.90
    y_rng_pct = y_rng / (prev_close + _EPS)
    # 昨 H=L（一字/坏 K）时勿用 _EPS 当分母：分子是价格量级 → μ/σ 被 1e12 尖刺污染
    if y_rng > _EPS:
        open_in_yrange = (open_t - prev_low) / y_rng
        yclose_loc = (prev_close - prev_low) / y_rng
    else:
        open_in_yrange = 0.5
        yclose_loc = 0.5
    if y_rng_pct > _EPS:
        gap_vs_yrange = gap / y_rng_pct
    else:
        gap_vs_yrange = 0.0
    return {
        "GAP": gap,
        "GAP_ABS": abs(gap),
        "CLOSE0": prev_close / (open_t + _EPS),
        "YHIGH0": prev_high / (open_t + _EPS),
        "YLOW0": prev_low / (open_t + _EPS),
        "YVWAP0": y_vwap / (open_t + _EPS),
        "YOPEN0": prev_open / (open_t + _EPS),
        "OPEN_IN_YRANGE": open_in_yrange,
        "GAP_VS_YRANGE": gap_vs_yrange,
        "GAP_ATR": gap / (atr + _EPS),
        "ABOVE_YHIGH": 1.0 if open_t > prev_high else 0.0,
        "BELOW_YLOW": 1.0 if open_t < prev_low else 0.0,
        "GAP_UP": max(gap, 0.0),
        "GAP_DN": max(-gap, 0.0),
        "NEAR_UPLIMIT": (up_lim - open_t) / (prev_close + _EPS),
        "NEAR_DNLIMIT": (open_t - dn_lim) / (prev_close + _EPS),
        "YCLOSE_LOC": yclose_loc,
        "GAP_X_YVOL": gap * yvol_z,
        "GAP_X_YRANGE": gap * y_rng_pct,
        "YOPEN_RET": prev_open / (close[-3] + _EPS) - 1.0 if len(close) >= 3 else 0.0,
        "YRANGE_PCT": y_rng_pct,
        "YVOL_Z20": yvol_z,
        "GAP_VS_OOMA5": gap - oo_ma5,
        "GAP_VS_OOMA20": gap - oo_ma20,
    }


def _rolling_open_fields(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
) -> dict:
    out: dict = {}
    n = len(open_)
    o0 = float(open_[-1])
    v0 = float(volume[-1])
    open_ret = np.zeros(n, dtype=np.float64)
    open_ret[1:] = open_[1:] / (open_[:-1] + _EPS) - 1.0
    vol_delta = np.zeros(n, dtype=np.float64)
    vol_delta[1:] = volume[1:] - volume[:-1]
    log_vol = np.log(volume + 1.0)
    log_vol_ret = np.zeros(n, dtype=np.float64)
    log_vol_ret[1:] = np.log(volume[1:] / (volume[:-1] + _EPS) + 1.0)

    for d in _WINDOWS:
        if n < d + 1:
            for op in _ROLL_OPS:
                out[_roll_key(op, d)] = None
            for op in _VOL_OPS:
                out[_vol_key(op, d)] = None
            continue
        o_seg = open_[-d:]
        h_seg = high[-d:]
        l_seg = low[-d:]
        v_seg = volume[-d:]
        or_seg = open_ret[-d:]
        vd_seg = vol_delta[-d:]
        lv_seg = log_vol[-d:]
        lvr_seg = log_vol_ret[-d:]

        out[_roll_key("ROC", d)] = float(open_[-(d + 1)] / (o0 + _EPS))
        out[_roll_key("MA", d)] = float(o_seg.mean() / (o0 + _EPS))
        out[_roll_key("STD", d)] = float(o_seg.std() / (o0 + _EPS))
        slope, rsq, resi = _linear_fit(o_seg)
        out[_roll_key("BETA", d)] = (
            float(slope / (o0 + _EPS)) if slope is not None else None
        )
        out[_roll_key("RSQR", d)] = rsq
        out[_roll_key("RESI", d)] = (
            float(resi / (o0 + _EPS)) if resi is not None else None
        )
        out[_roll_key("MAX", d)] = float(h_seg.max() / (o0 + _EPS))
        out[_roll_key("LOW", d)] = float(l_seg.min() / (o0 + _EPS))
        out[_roll_key("QTLU", d)] = float(np.quantile(o_seg, 0.8) / (o0 + _EPS))
        out[_roll_key("QTLD", d)] = float(np.quantile(o_seg, 0.2) / (o0 + _EPS))
        out[_roll_key("RANK", d)] = _rank_percentile(o0, o_seg)
        h_max = float(h_seg.max())
        l_min = float(l_seg.min())
        out[_roll_key("RSV", d)] = float((o0 - l_min) / (h_max - l_min + _EPS))
        out[_roll_key("IMAX", d)] = float(np.argmax(h_seg) / d)
        out[_roll_key("IMIN", d)] = float(np.argmin(l_seg) / d)
        out[_roll_key("IMXD", d)] = float(
            (np.argmax(h_seg) - np.argmin(l_seg)) / d
        )
        out[_roll_key("CORR", d)] = _safe_corr(o_seg, lv_seg)
        out[_roll_key("CORD", d)] = _safe_corr(or_seg, lvr_seg)
        up = float((or_seg > 0).mean())
        dn = float((or_seg < 0).mean())
        out[_roll_key("CNTP", d)] = up
        out[_roll_key("CNTN", d)] = dn
        out[_roll_key("CNTD", d)] = up - dn
        pos_ret = np.where(or_seg > 0, or_seg, 0.0)
        neg_ret = np.where(or_seg < 0, -or_seg, 0.0)
        abs_sum = float(np.abs(or_seg).sum())
        out[_roll_key("SUMP", d)] = float(pos_ret.sum() / (abs_sum + _EPS))
        out[_roll_key("SUMN", d)] = float(neg_ret.sum() / (abs_sum + _EPS))
        out[_roll_key("SUMD", d)] = float(
            (pos_ret.sum() - neg_ret.sum()) / (abs_sum + _EPS)
        )

        out[_vol_key("VMA", d)] = float(v_seg.mean() / (v0 + _EPS))
        out[_vol_key("VSTD", d)] = float(v_seg.std() / (v0 + _EPS))
        wvma_input = np.abs(or_seg) * v_seg
        wvma_mean = float(wvma_input.mean())
        wvma_std = float(wvma_input.std())
        out[_vol_key("WVMA", d)] = (
            float(wvma_std / (wvma_mean + _EPS)) if wvma_mean > _EPS else None
        )
        pos_vd = np.where(vd_seg > 0, vd_seg, 0.0)
        neg_vd = np.where(vd_seg < 0, -vd_seg, 0.0)
        abs_vd_sum = float(np.abs(vd_seg).sum())
        out[_vol_key("VSUMP", d)] = float(pos_vd.sum() / (abs_vd_sum + _EPS))
        out[_vol_key("VSUMN", d)] = float(neg_vd.sum() / (abs_vd_sum + _EPS))
        out[_vol_key("VSUMD", d)] = float(
            (pos_vd.sum() - neg_vd.sum()) / (abs_vd_sum + _EPS)
        )
        # 量 ROC：完整日量，决策日用昨量占位 → VROC 用 volume[-(d+1)]/volume[-2]
        v_ref = float(volume[-2]) if n >= 2 else v0
        out[_vol_key("VROC", d)] = float(volume[-(d + 1)] / (v_ref + _EPS))
        o_ma = float(o_seg.mean())
        out[_vol_key("VRAMA", d)] = float(
            (v_seg.mean() / (v_ref + _EPS)) / (o_ma / (o0 + _EPS) + _EPS)
        )

    return out


def _lag_fields(
    open_: np.ndarray,
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
) -> dict:
    """开→开滞后等；open_/close 含决策占位，完整日为 [:-1]。"""
    o = open_[:-1]
    c = close[:-1]
    h = high[:-1]
    l = low[:-1]
    v = volume[:-1]
    ot = float(open_[-1])
    if len(o) < 5:
        return {k: None for k in _LAG_KEYS}
    oo = o[1:] / (o[:-1] + _EPS) - 1.0
    # 拼上今开相对昨开
    oo_today = ot / (o[-1] + _EPS) - 1.0
    oo_all = np.concatenate([oo, np.array([oo_today])])
    cc1 = c[-1] / (c[-2] + _EPS) - 1.0 if len(c) >= 2 else 0.0
    oc1 = c[-1] / (o[-1] + _EPS) - 1.0
    rng = (h - l) / (c + _EPS)
    v_ma20 = float(v[-20:].mean()) if len(v) >= 20 else float(v[-1])
    r_ma20 = float(rng[-20:].mean()) if len(rng) >= 20 else float(rng[-1])
    return {
        "OO_LAG1": float(oo_all[-1]),
        "OO_LAG2": float(oo_all[-2]) if len(oo_all) >= 2 else None,
        "OO_LAG3": float(oo_all[-3]) if len(oo_all) >= 3 else None,
        "OO_MA5": float(oo_all[-5:].mean()),
        "OO_STD5": float(oo_all[-5:].std()),
        "CC_LAG1": float(cc1),
        "OC_LAG1": float(oc1),
        "OO_SIGN_MA5": float(np.sign(oo_all[-5:]).mean()),
        "MOM3_O": float(oo_all[-3:].sum()) if len(oo_all) >= 3 else None,
        # 截止 T−1 的 3 日收盘累计（比例）；原 CO/τ Z「近3日动量%」×1/100
        "MOM3_C": (
            float(c[-1] / (c[-4] + _EPS) - 1.0) if len(c) >= 4 else None
        ),
        "REV1_O": float(-oo_all[-1]),
        "VOL_SHOCK": float(v[-1] / (v_ma20 + _EPS)),
        "RANGE_SHOCK": float(rng[-1] / (r_ma20 + _EPS)),
        "OO_MA10": float(oo_all[-10:].mean()) if len(oo_all) >= 10 else None,
        "OO_STD10": float(oo_all[-10:].std()) if len(oo_all) >= 10 else None,
    }


def _prepare_open_pit_bars(
    bars: Sequence[dict],
    *,
    open_t: Optional[float],
    prev_close: Optional[float],
    quote: Optional[dict],
) -> Tuple[Optional[List[dict]], Optional[float], Optional[float], Dict[str, Any]]:
    hist = [b for b in (bars or []) if isinstance(b, dict)]
    ot = resolve_open_t(open_t=open_t, quote=quote, bars=None)
    meta_flags: Dict[str, Any] = {}

    if ot is None and hist:
        ot = _fpx(hist[-1].get("open"))
        if ot is not None and len(hist) >= _MIN_BARS:
            complete = hist[:-1]
            pc = resolve_prev_close(
                prev_close=prev_close, quote=quote, hist_complete=complete
            )
            if pc is None and complete:
                pc = _fpx(complete[-1].get("close"))
            if not complete or pc is None:
                return None, None, None, {"alpha_oo_need_prev_close": True}
            syn = {
                "date": str(hist[-1].get("date") or "")[:10],
                "open": float(ot),
                "high": float(ot),
                "low": float(ot),
                "close": float(ot),
                "volume": float(complete[-1].get("volume") or 1.0),
                "session_open_pit": True,
            }
            return complete + [syn], float(ot), float(pc), meta_flags

    if ot is None:
        return None, None, None, {"alpha_oo_need_open": True}

    if len(hist) < _MIN_BARS - 1:
        return None, None, None, {"alpha_oo_insufficient_history": True}
    pc = resolve_prev_close(prev_close=prev_close, quote=quote, hist_complete=hist)
    if pc is None:
        return None, None, None, {"alpha_oo_need_prev_close": True}
    syn = {
        "date": "",
        "open": float(ot),
        "high": float(ot),
        "low": float(ot),
        "close": float(ot),
        "volume": float(hist[-1].get("volume") or 1.0),
        "session_open_pit": True,
    }
    if isinstance(quote, dict):
        syn["date"] = str(quote.get("date") or quote.get("trade_date") or "")[:10]
    out_bars = list(hist) + [syn]
    if len(out_bars) < _MIN_BARS:
        return None, None, None, {"alpha_oo_insufficient_history": True}
    return out_bars, float(ot), float(pc), meta_flags


def score_alpha_oo(
    bars: Optional[List[dict]] = None,
    *,
    open_t: Optional[float] = None,
    prev_close: Optional[float] = None,
    quote: Optional[dict] = None,
    **_kw: Any,
) -> Tuple[float, dict]:
    """计算 alpha_oo；返回 ``(50.0, meta)``，``omit_sub_score=True``。"""
    prepared, ot, pc, flags = _prepare_open_pit_bars(
        list(bars or []),
        open_t=open_t,
        prev_close=prev_close,
        quote=quote,
    )
    if prepared is None or ot is None or pc is None:
        meta = {"omit_sub_score": True}
        meta.update(flags or {"alpha_oo_insufficient_history": True})
        return 50.0, meta

    try:
        close, open_arr, high, low, volume = _to_arrays(prepared)
    except Exception as e:  # noqa: BLE001
        logger.debug("alpha_oo to_arrays failed: %s", e, exc_info=True)
        return 50.0, {"omit_sub_score": True, "alpha_oo_parse_error": str(e)[:120]}

    if float(open_arr[-1]) < _EPS:
        return 50.0, {"omit_sub_score": True, "alpha_oo_zero_open": True}
    if len(prepared) < 2:
        return 50.0, {"omit_sub_score": True, "alpha_oo_insufficient_history": True}

    y_o = float(open_arr[-2])
    y_c = float(close[-2])
    y_h = float(high[-2])
    y_l = float(low[-2])
    y_v = float(volume[-2])
    if y_c < _EPS or y_o < _EPS:
        return 50.0, {"omit_sub_score": True, "alpha_oo_bad_prev_bar": True}

    meta: dict = {"omit_sub_score": True, "alpha_oo_anchor": "open"}
    try:
        meta.update(
            _gap_fields(
                float(ot),
                float(pc),
                y_o,
                y_h,
                y_l,
                y_v,
                open_arr,
                high,
                low,
                close,
                volume,
            )
        )
        meta.update(_yk_fields(y_c, y_o, y_h, y_l))
        meta.update(_rolling_open_fields(open_arr, high, low, volume))
        meta.update(_lag_fields(open_arr, close, high, low, volume))
    except Exception as e:  # noqa: BLE001
        logger.debug("alpha_oo compute failed: %s", e, exc_info=True)
        meta["alpha_oo_partial_error"] = str(e)[:120]

    return 50.0, meta


def raw_aoo_from_bars(
    bars: Optional[Sequence[dict]],
    *,
    open_t: Optional[float] = None,
    prev_close: Optional[float] = None,
    quote: Optional[dict] = None,
) -> dict:
    _score, meta = score_alpha_oo(
        list(bars or []),
        open_t=open_t,
        prev_close=prev_close,
        quote=quote,
    )
    if not isinstance(meta, dict):
        return {}
    out: dict = {}
    for mk, mv in meta.items():
        sk = str(mk)
        if sk in ("omit_sub_score", "ok") or sk.startswith("alpha_oo_"):
            continue
        if isinstance(mv, (int, float)) and not isinstance(mv, bool):
            out[f"{ALPHA_OO_RAW_PREFIX}{sk}"] = float(mv)
    return out
