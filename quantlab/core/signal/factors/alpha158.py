"""Alpha158 因子集（原生重写，无 pyqlib 依赖）。

微软 Qlib Alpha158 的 158 个 OHLCV 衍生因子，分三类：
- kbar（9 个）：当日 K 线形态
- price（4 个）：当日价相对收盘（OPEN0/HIGH0/LOW0/VWAP0）
- rolling（29 操作 × 5 窗口 = 145 个）：5/10/20/30/60 日滚动统计

参考：https://github.com/microsoft/qlib/blob/main/qlib/contrib/data/loader.py

注册为单 factor ``alpha158``，sub_score 固定 50 + ``omit_sub_score=True``，
仅通过 meta 暴露 158 个 ``raw_alpha158_*`` 字段供 Ridge/LightGBM/LambdaRank 消费。
"""

import logging
import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Qlib Alpha158 默认滚动窗口
_WINDOWS: Tuple[int, ...] = (5, 10, 20, 30, 60)
_MIN_BARS: int = 61  # 60 用于滚动 + 1 当日
_EPS: float = 1e-12

ALPHA158_FACTOR_KEY = "alpha158"
ALPHA158_RAW_PREFIX = "raw_alpha158_"
ALPHA158_MIN_BARS = _MIN_BARS
# 研究面板经 pit_oo_window_quote：hist=bars[start:idx] 不含决策日，
# len(hist)=max_window-1。要让 hist≥61，面板 max_window 须 ≥62。
ALPHA158_PANEL_WINDOW = _MIN_BARS + 1


def is_alpha158_raw_key(name: str) -> bool:
    return str(name or "").startswith(ALPHA158_RAW_PREFIX)


def collect_alpha158_raw_keys_from_rows(xs: Sequence[dict]) -> List[str]:
    """从面板行收集出现过的 ``raw_alpha158_*`` 列名（保序去重）。"""
    names: List[str] = []
    seen = set()
    for row in xs or []:
        if not isinstance(row, dict):
            continue
        for k, v in row.items():
            sk = str(k)
            if sk in seen or not is_alpha158_raw_key(sk):
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


def expand_ridge_feature_names(
    xs: Sequence[dict],
    *,
    base_names: Optional[Sequence[str]] = None,
) -> List[str]:
    """Ridge 入模列：注册因子 + 面板 ``raw_alpha158_*``；去掉常数 ``alpha158`` 分。"""
    from core.signal.factors.meta.registry import registered_factor_names

    base = list(base_names) if base_names is not None else list(registered_factor_names())
    names: List[str] = []
    seen = set()
    for n in base:
        k = str(n or "").strip()
        if not k or k == ALPHA158_FACTOR_KEY:
            continue
        if k not in seen:
            seen.add(k)
            names.append(k)
    for k in collect_alpha158_raw_keys_from_rows(xs):
        if k not in seen:
            seen.add(k)
            names.append(k)
    return names


def merge_alpha158_min_std_exempt(
    feature_names: Sequence[str],
    min_std_exempt: Optional[Sequence[str]] = None,
) -> List[str]:
    """``raw_alpha158_*`` 非 0–100 分制，跳过 ``min_std=5`` 低方差门槛。"""
    out = [str(x) for x in (min_std_exempt or []) if str(x).strip()]
    seen = set(out)
    for n in feature_names or []:
        k = str(n or "").strip()
        if is_alpha158_raw_key(k) and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def bump_window_for_alpha158(
    factor_names: Optional[Sequence[str]],
    *,
    min_history: int,
    max_window: int,
) -> Tuple[int, int]:
    """因子集含 alpha158 时抬高窗口，保证 PIT hist 仍有 ≥61 根可算滚动。"""
    names = {str(n).strip() for n in (factor_names or []) if str(n).strip()}
    if ALPHA158_FACTOR_KEY not in names and not any(
        is_alpha158_raw_key(n) for n in names
    ):
        return int(min_history), int(max_window)
    need = int(ALPHA158_PANEL_WINDOW)
    return max(int(min_history), need), max(int(max_window), need)


def raw_alpha158_from_bars(bars: Optional[Sequence[dict]]) -> dict:
    """日 K hist → ``{raw_alpha158_*: float}``；不足 61 根返回 ``{}``。

    供 y_co / y_τc 面板直接注入；不含常数 ``alpha158`` sub_score。
    调用方须保证 bars 末根 ≤ 决策日前一交易日（PIT）。
    """
    hist = [b for b in (bars or []) if isinstance(b, dict)]
    if len(hist) < _MIN_BARS:
        return {}
    _score, meta = score_alpha158(hist)
    if not isinstance(meta, dict):
        return {}
    out: dict = {}
    for mk, mv in meta.items():
        sk = str(mk)
        if sk in ("omit_sub_score", "ok") or sk.startswith("alpha158_"):
            continue
        if isinstance(mv, (int, float)) and not isinstance(mv, bool):
            out[f"{ALPHA158_RAW_PREFIX}{sk}"] = float(mv)
    return out


def keep_alpha158_raw_in_row(
    src: Optional[dict],
    *,
    dest: Optional[dict] = None,
) -> dict:
    """把 ``src`` 中的 ``raw_alpha158_*`` 拷进 ``dest``（就地并返回）。"""
    out = dest if isinstance(dest, dict) else {}
    for k, v in (src or {}).items():
        if is_alpha158_raw_key(str(k)) and v is not None and v != "":
            out[str(k)] = v
    return out


def _to_arrays(bars: List[dict]) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """bars -> (close, open, high, low, volume) ndarray。"""
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


# ---------------- kbar 因子（9 个）----------------


def _kbar_fields(close: float, open_: float, high: float, low: float) -> dict:
    """当日 K 线形态，全部用当日 OHLC。"""
    rng = high - low
    mx_oc = max(open_, close)
    mn_oc = min(open_, close)
    return {
        "KMID": (close - open_) / (open_ + _EPS),
        "KLEN": (high - low) / (open_ + _EPS),
        "KMID2": (close - open_) / (rng + _EPS),
        "KUP": (high - mx_oc) / (open_ + _EPS),
        "KUP2": (high - mx_oc) / (rng + _EPS),
        "KLOW": (mn_oc - low) / (open_ + _EPS),
        "KLOW2": (mn_oc - low) / (rng + _EPS),
        "KSFT": (2.0 * close - high - low) / (open_ + _EPS),
        "KSFT2": (2.0 * close - high - low) / (rng + _EPS),
    }


# ---------------- price 因子（4 个）----------------


def _price_fields(close: float, open_: float, high: float, low: float) -> dict:
    """当日价相对收盘；VWAP 用 (high+low+close)/3 近似。"""
    vwap = (high + low + close) / 3.0
    return {
        "OPEN0": open_ / (close + _EPS),
        "HIGH0": high / (close + _EPS),
        "LOW0": low / (close + _EPS),
        "VWAP0": vwap / (close + _EPS),
    }


# ---------------- rolling 因子（29 操作 × 5 窗口 = 145 个）----------------


def _safe_corr(x: np.ndarray, y: np.ndarray) -> Optional[float]:
    """两数组相关系数；方差为 0 返回 None。"""
    if len(x) < 2:
        return None
    sx = x.std()
    sy = y.std()
    if sx < _EPS or sy < _EPS:
        return None
    cov = ((x - x.mean()) * (y - y.mean())).mean()
    return float(cov / (sx * sy))


def _linear_fit(y: np.ndarray) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """线性回归 y = a + b*x (x=0..n-1)，返回 (slope, rsquare, residual)。"""
    n = len(y)
    if n < 2:
        return None, None, None
    x = np.arange(n, dtype=np.float64)
    xm = x.mean()
    ym = y.mean()
    denom = ((x - xm) ** 2).sum()
    if denom < _EPS:
        return None, None, None
    slope = ((x - xm) * (y - ym)).sum() / denom
    intercept = ym - slope * xm
    y_pred = intercept + slope * x
    ss_res = ((y - y_pred) ** 2).sum()
    ss_tot = ((y - ym) ** 2).sum()
    rsquare = 1.0 - ss_res / (ss_tot + _EPS) if ss_tot > _EPS else 0.0
    residual = (y[-1] - y_pred[-1])  # 末日残差
    return float(slope), float(rsquare), float(residual)


def _rank_percentile(current: float, series: np.ndarray) -> Optional[float]:
    """当日值在 series 中的分位 (0~1)。"""
    if len(series) == 0:
        return None
    below = float((series < current).sum())
    return below / len(series)


def _rolling_fields(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
) -> dict:
    """计算 29 个 rolling 操作 × 5 窗口 = 145 个字段。

    全部基于末日 T 视角，窗口 [T-d+1 .. T]。
    """
    out: dict = {}
    n = len(close)
    c0 = close[-1]
    v0 = volume[-1]

    # 末日收益率序列（close_ret[i] = close[i]/close[i-1] - 1）
    # 窗口 d 内需要 d 个 ret，相当于 d+1 根 close
    close_ret = np.zeros(n, dtype=np.float64)
    close_ret[1:] = close[1:] / (close[:-1] + _EPS) - 1.0
    vol_delta = np.zeros(n, dtype=np.float64)
    vol_delta[1:] = volume[1:] - volume[:-1]
    log_vol = np.log(volume + 1.0)
    log_vol_ret = np.zeros(n, dtype=np.float64)
    log_vol_ret[1:] = np.log(volume[1:] / (volume[:-1] + _EPS) + 1.0)

    for d in _WINDOWS:
        if n < d + 1:
            # 窗口不足，全部置 None
            for op in (
                "ROC", "MA", "STD", "BETA", "RSQR", "RESI", "MAX", "LOW",
                "QTLU", "QTLD", "RANK", "RSV", "IMAX", "IMIN", "IMXD",
                "CORR", "CORD", "CNTP", "CNTN", "CNTD", "SUMP", "SUMN",
                "SUMD", "VMA", "VSTD", "WVMA", "VSUMP", "VSUMN", "VSUMD",
            ):
                out[f"{op}{d}"] = None
            continue

        # 切片 [-d:] 即最近 d 根
        c_seg = close[-d:]
        h_seg = high[-d:]
        l_seg = low[-d:]
        v_seg = volume[-d:]
        cr_seg = close_ret[-d:]  # d 个 ret
        vd_seg = vol_delta[-d:]
        lv_seg = log_vol[-d:]
        lvr_seg = log_vol_ret[-d:]

        # ---- 价格变化类 ----
        # Qlib: ROC = Ref($close, d)/$close = close[T-d]/close[T]，需 close 数组的 close[-(d+1)]
        out[f"ROC{d}"] = float(close[-(d + 1)] / (c0 + _EPS))
        out[f"MA{d}"] = float(c_seg.mean() / (c0 + _EPS))
        out[f"STD{d}"] = float(c_seg.std() / (c0 + _EPS))
        slope, rsq, resi = _linear_fit(c_seg)
        out[f"BETA{d}"] = float(slope / (c0 + _EPS)) if slope is not None else None
        out[f"RSQR{d}"] = rsq
        out[f"RESI{d}"] = float(resi / (c0 + _EPS)) if resi is not None else None

        # ---- 极值分位类 ----
        out[f"MAX{d}"] = float(h_seg.max() / (c0 + _EPS))
        out[f"LOW{d}"] = float(l_seg.min() / (c0 + _EPS))
        out[f"QTLU{d}"] = float(np.quantile(c_seg, 0.8) / (c0 + _EPS))
        out[f"QTLD{d}"] = float(np.quantile(c_seg, 0.2) / (c0 + _EPS))
        out[f"RANK{d}"] = _rank_percentile(c0, c_seg)
        h_max = h_seg.max()
        l_min = l_seg.min()
        out[f"RSV{d}"] = float((c0 - l_min) / (h_max - l_min + _EPS))

        # ---- Aroon 类 ----
        out[f"IMAX{d}"] = float(np.argmax(h_seg) / d)
        out[f"IMIN{d}"] = float(np.argmin(l_seg) / d)
        out[f"IMXD{d}"] = float((np.argmax(h_seg) - np.argmin(l_seg)) / d)

        # ---- 量价相关类 ----
        out[f"CORR{d}"] = _safe_corr(c_seg, lv_seg)
        out[f"CORD{d}"] = _safe_corr(cr_seg, lvr_seg)

        # ---- 涨跌日占比类 ----
        up = float((cr_seg > 0).mean())
        dn = float((cr_seg < 0).mean())
        out[f"CNTP{d}"] = up
        out[f"CNTN{d}"] = dn
        out[f"CNTD{d}"] = up - dn

        # ---- RSI 类（涨跌累计）----
        pos_ret = np.where(cr_seg > 0, cr_seg, 0.0)
        neg_ret = np.where(cr_seg < 0, -cr_seg, 0.0)
        abs_sum = np.abs(cr_seg).sum()
        sump = float(pos_ret.sum() / (abs_sum + _EPS))
        sumn = float(neg_ret.sum() / (abs_sum + _EPS))
        out[f"SUMP{d}"] = sump
        out[f"SUMN{d}"] = sumn
        out[f"SUMD{d}"] = float((pos_ret.sum() - neg_ret.sum()) / (abs_sum + _EPS))

        # ---- 量能类 ----
        out[f"VMA{d}"] = float(v_seg.mean() / (v0 + _EPS))
        out[f"VSTD{d}"] = float(v_seg.std() / (v0 + _EPS))
        wvma_input = np.abs(cr_seg) * v_seg
        wvma_mean = wvma_input.mean()
        wvma_std = wvma_input.std()
        out[f"WVMA{d}"] = float(wvma_std / (wvma_mean + _EPS)) if wvma_mean > _EPS else None

        # ---- 量 RSI 类 ----
        pos_vd = np.where(vd_seg > 0, vd_seg, 0.0)
        neg_vd = np.where(vd_seg < 0, -vd_seg, 0.0)
        abs_vd_sum = np.abs(vd_seg).sum()
        vsump = float(pos_vd.sum() / (abs_vd_sum + _EPS))
        vsumn = float(neg_vd.sum() / (abs_vd_sum + _EPS))
        out[f"VSUMP{d}"] = vsump
        out[f"VSUMN{d}"] = vsumn
        out[f"VSUMD{d}"] = float((pos_vd.sum() - neg_vd.sum()) / (abs_vd_sum + _EPS))

    return out


# ---------------- 主入口 ----------------


def score_alpha158(bars: List[dict]) -> Tuple[float, dict]:
    """计算 Alpha158 因子集。

    返回:
        (50.0, meta)：meta 包含 158 个数值字段（KMID/KLEN/.../VSUMD60），
        以及 ``omit_sub_score=True`` 标记，让生产路径跳过 sub_score 加权，
        但 _research_sub_scores 仍会把 raw_alpha158_* 注入 row。

    bars 不足 61 根时返回 omit + 标记，不进面板。
    """
    if not bars or len(bars) < _MIN_BARS:
        return 50.0, {
            "omit_sub_score": True,
            "alpha158_insufficient_history": True,
        }

    try:
        close, open_, high, low, volume = _to_arrays(bars)
    except Exception as e:  # noqa: BLE001
        logger.debug("alpha158 to_arrays failed: %s", e, exc_info=True)
        return 50.0, {"omit_sub_score": True, "alpha158_parse_error": str(e)[:120]}

    c0 = float(close[-1])
    o0 = float(open_[-1])
    h0 = float(high[-1])
    l0 = float(low[-1])

    if c0 < _EPS:
        return 50.0, {"omit_sub_score": True, "alpha158_zero_close": True}

    meta: dict = {"omit_sub_score": True}
    try:
        meta.update(_kbar_fields(c0, o0, h0, l0))
        meta.update(_price_fields(c0, o0, h0, l0))
        meta.update(_rolling_fields(close, high, low, volume))
    except Exception as e:  # noqa: BLE001
        logger.debug("alpha158 compute failed: %s", e, exc_info=True)
        # 部分计算成功也保留已算出的字段，并加 error 标记
        meta["alpha158_partial_error"] = str(e)[:120]

    return 50.0, meta
