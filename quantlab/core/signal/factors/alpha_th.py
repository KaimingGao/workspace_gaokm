"""ŷ_τ* 专用因子集 alpha_th（前缀 ``raw_ath_*``）。

PIT：``hist=≤T−1`` + 今开 + ≤τ 分钟前缀。锚价 ``price[τ]``。
标签对齐窗收益符号 → ``P(窗收益>0)``（非 τ→收）。

相对 ``raw_atc_*``：砍掉全量 ROLL / TC_LAG / 完整 PREFIX；保留 GAP 轻量 + YK 轻量
+ 短窗动量 / 会话钟 + 本头同钟滞后（``H_LAG1`` / ``H_MA5``）。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_EPS: float = 1e-12
_MIN_BARS: int = 22  # MOM3 + ATR14 够用；远小于 alpha_tc 的 61

ALPHA_TH_FACTOR_KEY = "alpha_th"
ALPHA_TH_RAW_PREFIX = "raw_ath_"
ALPHA_TH_MIN_BARS = _MIN_BARS
ALPHA_TH_PANEL_WINDOW = _MIN_BARS + 1

HORIZON_TH_HEADS = ("t30", "t45", "t60", "t75", "t90")

# 隔夜 / τ 位置（轻量，对齐 raw_atc GAP 子集）
_GAP_KEYS = (
    "GAP",
    "GAP_ABS",
    "RET_OT",
    "RET_PT",
    "OPEN0",
    "TAU_IN_YRANGE",
    "GAP_VS_YRANGE",
    "GAP_ATR",
    "ABOVE_YHIGH",
    "BELOW_YLOW",
    "NEAR_UPLIMIT",
    "NEAR_DNLIMIT",
    "YRANGE_PCT",
    "YCLOSE_LOC",
    "YVOL_Z20",
    "YHIGH0",
    "YLOW0",
    "CLOSE0",
)

# 昨 K 形状轻量
_YK_KEYS = (
    "YBODY",
    "YKLEN",
    "YUPPER_SHARE",
    "YLOWER_SHARE",
    "MOM3_C",
)

# 短窗 / 会话（由分钟包 / seq 升格；单位与源特征一致，多为 %）
_WIN_SHARED_KEYS = (
    "RET_LAST_5M",
    "RET_LAST_15M",
    "SESSION_ELAPSED",
    "SESSION_REMAIN",
    "SESSION_VWAP_DEV",
    "VOL_LAST3_VS_AVG",
    "REALIZED_VOL",
    "TAU_ELAPSED_MIN",
)

# 本头窗动量 / 同钟滞后（按 head 选用）
_WIN_HEAD_KEYS = (
    "RET_LAST_H",
    "VOL_LAST_H_VS_AVG",
    "CROSSES_LUNCH_H",
    "SECTOR_RET_LAST_H",
    "RET_LAST_H_VS_SECTOR",
    "H_LAG1",
    "H_MA5",
)

# 全头 seq 键也写成 raw（面板一次注入；各头 fit 只取本头）
_WIN_ALL_HORIZON_KEYS = (
    "RET_LAST_30",
    "RET_LAST_45",
    "RET_LAST_60",
    "RET_LAST_75",
    "RET_LAST_90",
    "VOL_LAST_30_VS_AVG",
    "VOL_LAST_45_VS_AVG",
    "VOL_LAST_60_VS_AVG",
    "VOL_LAST_75_VS_AVG",
    "VOL_LAST_90_VS_AVG",
    "CROSSES_LUNCH",
    "CROSSES_LUNCH_45",
    "CROSSES_LUNCH_60",
    "CROSSES_LUNCH_75",
    "CROSSES_LUNCH_90",
    "SECTOR_RET_LAST_30",
    "SECTOR_RET_LAST_45",
    "SECTOR_RET_LAST_60",
    "SECTOR_RET_LAST_75",
    "SECTOR_RET_LAST_90",
    "RET_LAST_30_VS_SECTOR",
    "RET_LAST_45_VS_SECTOR",
    "RET_LAST_60_VS_SECTOR",
    "RET_LAST_75_VS_SECTOR",
    "RET_LAST_90_VS_SECTOR",
)

ALPHA_TH_FIELD_NAMES: Tuple[str, ...] = (
    *_GAP_KEYS,
    *_YK_KEYS,
    *_WIN_SHARED_KEYS,
    *_WIN_ALL_HORIZON_KEYS,
    *_WIN_HEAD_KEYS,
)

# 源特征 → bare KEY（共享）
_FEAT_TO_ATH: Dict[str, str] = {
    "ret_last_5m": "RET_LAST_5M",
    "ret_last_15m": "RET_LAST_15M",
    "session_elapsed": "SESSION_ELAPSED",
    "session_remain": "SESSION_REMAIN",
    "session_vwap_dev": "SESSION_VWAP_DEV",
    "vol_last3_vs_avg": "VOL_LAST3_VS_AVG",
    "realized_vol": "REALIZED_VOL",
    "tau_elapsed_min": "TAU_ELAPSED_MIN",
    "ret_last_30m": "RET_LAST_30",
    "ret_last_45m": "RET_LAST_45",
    "ret_last_60m": "RET_LAST_60",
    "ret_last_75m": "RET_LAST_75",
    "ret_last_90m": "RET_LAST_90",
    "vol_last_30m_vs_avg": "VOL_LAST_30_VS_AVG",
    "vol_last_45m_vs_avg": "VOL_LAST_45_VS_AVG",
    "vol_last_60m_vs_avg": "VOL_LAST_60_VS_AVG",
    "vol_last_75m_vs_avg": "VOL_LAST_75_VS_AVG",
    "vol_last_90m_vs_avg": "VOL_LAST_90_VS_AVG",
    "crosses_lunch": "CROSSES_LUNCH",
    "crosses_lunch_45": "CROSSES_LUNCH_45",
    "crosses_lunch_60": "CROSSES_LUNCH_60",
    "crosses_lunch_75": "CROSSES_LUNCH_75",
    "crosses_lunch_90": "CROSSES_LUNCH_90",
    "sector_ret_last_30m": "SECTOR_RET_LAST_30",
    "sector_ret_last_45m": "SECTOR_RET_LAST_45",
    "sector_ret_last_60m": "SECTOR_RET_LAST_60",
    "sector_ret_last_75m": "SECTOR_RET_LAST_75",
    "sector_ret_last_90m": "SECTOR_RET_LAST_90",
    "ret_last_30m_vs_sector": "RET_LAST_30_VS_SECTOR",
    "ret_last_45m_vs_sector": "RET_LAST_45_VS_SECTOR",
    "ret_last_60m_vs_sector": "RET_LAST_60_VS_SECTOR",
    "ret_last_75m_vs_sector": "RET_LAST_75_VS_SECTOR",
    "ret_last_90m_vs_sector": "RET_LAST_90_VS_SECTOR",
}

_HEAD_PROMOTE: Dict[str, Dict[str, str]] = {
    "t30": {
        "RET_LAST_H": "ret_last_30m",
        "VOL_LAST_H_VS_AVG": "vol_last_30m_vs_avg",
        "CROSSES_LUNCH_H": "crosses_lunch",
        "SECTOR_RET_LAST_H": "sector_ret_last_30m",
        "RET_LAST_H_VS_SECTOR": "ret_last_30m_vs_sector",
        "H_LAG1": "t30_lag1",
        "H_MA5": "t30_ma5",
    },
    "t45": {
        "RET_LAST_H": "ret_last_45m",
        "VOL_LAST_H_VS_AVG": "vol_last_45m_vs_avg",
        "CROSSES_LUNCH_H": "crosses_lunch_45",
        "SECTOR_RET_LAST_H": "sector_ret_last_45m",
        "RET_LAST_H_VS_SECTOR": "ret_last_45m_vs_sector",
        "H_LAG1": "t45_lag1",
        "H_MA5": "t45_ma5",
    },
    "t60": {
        "RET_LAST_H": "ret_last_60m",
        "VOL_LAST_H_VS_AVG": "vol_last_60m_vs_avg",
        "CROSSES_LUNCH_H": "crosses_lunch_60",
        "SECTOR_RET_LAST_H": "sector_ret_last_60m",
        "RET_LAST_H_VS_SECTOR": "ret_last_60m_vs_sector",
        "H_LAG1": "t60_lag1",
        "H_MA5": "t60_ma5",
    },
    "t75": {
        "RET_LAST_H": "ret_last_75m",
        "VOL_LAST_H_VS_AVG": "vol_last_75m_vs_avg",
        "CROSSES_LUNCH_H": "crosses_lunch_75",
        "SECTOR_RET_LAST_H": "sector_ret_last_75m",
        "RET_LAST_H_VS_SECTOR": "ret_last_75m_vs_sector",
        "H_LAG1": "t75_lag1",
        "H_MA5": "t75_ma5",
    },
    "t90": {
        "RET_LAST_H": "ret_last_90m",
        "VOL_LAST_H_VS_AVG": "vol_last_90m_vs_avg",
        "CROSSES_LUNCH_H": "crosses_lunch_90",
        "SECTOR_RET_LAST_H": "sector_ret_last_90m",
        "RET_LAST_H_VS_SECTOR": "ret_last_90m_vs_sector",
        "H_LAG1": "t90_lag1",
        "H_MA5": "t90_ma5",
    },
}


def is_alpha_th_raw_key(name: str) -> bool:
    return str(name or "").startswith(ALPHA_TH_RAW_PREFIX)


def ath_raw_key(bare: str) -> str:
    return f"{ALPHA_TH_RAW_PREFIX}{bare}"


ALPHA_TH_PACK_NOTE = "alpha_τh=z_τh_* ∪ raw_ath_*"


def alpha_th_report_meta(*, n_alpha_th_features: int = 0) -> Dict[str, Any]:
    """拟合/promote 报告挂的特征包字段。"""
    return {
        "feature_pack": "alpha_τh",
        "feature_pack_key": "alpha_th",
        "feature_pack_formula": "z_τh_* ∪ raw_ath_*",
        "include_alpha_th": True,
        "n_alpha_th_features": int(n_alpha_th_features or 0),
    }


# bare KEY → 中文短标（与分钟 Z / REM_FEAT_LABELS 同语义处对齐）
_ATH_BARE_LABELS: Dict[str, str] = {
    "GAP": "跳空 %",
    "GAP_ABS": "|跳空| %",
    "RET_OT": "开→τ %",
    "RET_PT": "昨收→τ %",
    "OPEN0": "今开/昨收",
    "CLOSE0": "τ价/昨收",
    "TAU_IN_YRANGE": "τ价在昨高低位置",
    "GAP_VS_YRANGE": "跳空/昨振幅",
    "GAP_ATR": "跳空/ATR",
    "ABOVE_YHIGH": "今开破昨高",
    "BELOW_YLOW": "今开破昨低",
    "NEAR_UPLIMIT": "距涨停",
    "NEAR_DNLIMIT": "距跌停",
    "YRANGE_PCT": "昨振幅占比",
    "YCLOSE_LOC": "昨收位置",
    "YVOL_Z20": "昨量 Z20",
    "YHIGH0": "昨高/昨收",
    "YLOW0": "昨低/昨收",
    "YBODY": "昨实体占比",
    "YKLEN": "昨振幅占比",
    "YUPPER_SHARE": "昨上影占比",
    "YLOWER_SHARE": "昨下影占比",
    "MOM3_C": "近3日收盘动量",
    "RET_LAST_5M": "近5m 收益 %",
    "RET_LAST_15M": "近15m 收益 %",
    "SESSION_ELAPSED": "已过交易分钟（09:30=0）",
    "SESSION_REMAIN": "距收盘剩余交易分钟",
    "SESSION_VWAP_DEV": "τ价相对会话VWAP %",
    "VOL_LAST3_VS_AVG": "近3根量/均量",
    "REALIZED_VOL": "前缀已实现波动 %",
    "TAU_ELAPSED_MIN": "τ距开盘分钟",
    "RET_LAST_30": "近30交易分钟收益 %",
    "RET_LAST_45": "近45交易分钟收益 %",
    "RET_LAST_60": "近60交易分钟收益 %",
    "RET_LAST_75": "近75交易分钟收益 %",
    "RET_LAST_90": "近90交易分钟收益 %",
    "VOL_LAST_30_VS_AVG": "近30m量/前缀均量",
    "VOL_LAST_45_VS_AVG": "近45m量/前缀均量",
    "VOL_LAST_60_VS_AVG": "近60m量/前缀均量",
    "VOL_LAST_75_VS_AVG": "近75m量/前缀均量",
    "VOL_LAST_90_VS_AVG": "近90m量/前缀均量",
    "CROSSES_LUNCH": "未来30m是否跨午休",
    "CROSSES_LUNCH_45": "未来45m是否跨午休",
    "CROSSES_LUNCH_60": "未来60m是否跨午休",
    "CROSSES_LUNCH_75": "未来75m是否跨午休",
    "CROSSES_LUNCH_90": "未来90m是否跨午休",
    "SECTOR_RET_LAST_30": "板块中位近30m %",
    "SECTOR_RET_LAST_45": "板块中位近45m %",
    "SECTOR_RET_LAST_60": "板块中位近60m %",
    "SECTOR_RET_LAST_75": "板块中位近75m %",
    "SECTOR_RET_LAST_90": "板块中位近90m %",
    "RET_LAST_30_VS_SECTOR": "近30m相对板块 %",
    "RET_LAST_45_VS_SECTOR": "近45m相对板块 %",
    "RET_LAST_60_VS_SECTOR": "近60m相对板块 %",
    "RET_LAST_75_VS_SECTOR": "近75m相对板块 %",
    "RET_LAST_90_VS_SECTOR": "近90m相对板块 %",
    "RET_LAST_H": "本头短窗收益 %",
    "VOL_LAST_H_VS_AVG": "本头短窗量/均量",
    "CROSSES_LUNCH_H": "本头窗是否跨午休",
    "SECTOR_RET_LAST_H": "本头板块中位短窗 %",
    "RET_LAST_H_VS_SECTOR": "本头短窗相对板块 %",
    "H_LAG1": "本头同钟 lag1",
    "H_MA5": "本头同钟 ma5",
}

# 全量 raw_ath_* 中文短标（入模 tip / feat_labels / 因子表）
ATH_FEAT_LABELS: Dict[str, str] = {
    ath_raw_key(bare): lab for bare, lab in _ATH_BARE_LABELS.items()
}


def alpha_th_fit_features_for_head(head: str) -> Tuple[str, ...]:
    """本头入模的 ``raw_ath_*`` 列（共享 + 本头 H_*）。"""
    h = str(head or "").strip().lower()
    if h not in HORIZON_TH_HEADS:
        raise ValueError(f"unsupported alpha_th head: {head!r}")
    num = h[1:]
    shared = tuple(ath_raw_key(k) for k in (*_GAP_KEYS, *_YK_KEYS, *_WIN_SHARED_KEYS))
    horizon_n = (
        ath_raw_key(f"RET_LAST_{num}"),
        ath_raw_key(f"VOL_LAST_{num}_VS_AVG"),
        ath_raw_key("CROSSES_LUNCH" if num == "30" else f"CROSSES_LUNCH_{num}"),
        ath_raw_key(f"SECTOR_RET_LAST_{num}"),
        ath_raw_key(f"RET_LAST_{num}_VS_SECTOR"),
    )
    head_alias = tuple(ath_raw_key(k) for k in _WIN_HEAD_KEYS)
    return shared + horizon_n + head_alias


def bump_window_for_alpha_th(
    factor_names: Optional[Sequence[str]],
    *,
    min_history: int,
    max_window: int,
) -> Tuple[int, int]:
    names = {str(n).strip() for n in (factor_names or []) if str(n).strip()}
    if ALPHA_TH_FACTOR_KEY not in names and not any(
        is_alpha_th_raw_key(n) for n in names
    ):
        return int(min_history), int(max_window)
    need = int(ALPHA_TH_PANEL_WINDOW)
    return max(int(min_history), need), max(int(max_window), need)


def _fnum(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x or math.isinf(x):
        return None
    return x


def _promote_feats_to_ath(dest: dict, feats: Optional[dict]) -> None:
    if not isinstance(feats, dict):
        return
    for src_k, bare in _FEAT_TO_ATH.items():
        v = _fnum(feats.get(src_k))
        if v is None:
            continue
        dest[ath_raw_key(bare)] = float(v)


def promote_ath_head_aliases(dest: Optional[dict], head: str) -> dict:
    """把本头 seq/lag 键升格为 ``raw_ath_H_*``（拟合 relabel 后 / live 打分后调用）。"""
    out = dest if isinstance(dest, dict) else {}
    h = str(head or "").strip().lower()
    mapping = _HEAD_PROMOTE.get(h)
    if not mapping:
        return out
    for bare, src_k in mapping.items():
        v = _fnum(out.get(src_k))
        if v is None:
            continue
        out[ath_raw_key(bare)] = float(v)
    return out


def _promote_ath_matrix(panel: Dict[str, Any], head: str) -> None:
    """在 float64 块上把源列抄到 ``raw_ath_*``。源列缺测不覆盖已有值。"""
    import numpy as np

    X = panel.get("X")
    if X is None:
        return
    arr = np.asarray(X, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[0] == 0:
        return
    names = [str(n) for n in (panel.get("x_names") or [])]
    index = {n: i for i, n in enumerate(names)}
    pairs: List[Tuple[str, str]] = [
        (src, ath_raw_key(bare)) for src, bare in _FEAT_TO_ATH.items()
    ]
    mapping = _HEAD_PROMOTE.get(str(head or "").strip().lower()) or {}
    for bare, src in mapping.items():
        pairs.append((str(src), ath_raw_key(bare)))
    missing = [dst for _src, dst in pairs if dst not in index]
    # 去重且保序
    seen_m = set()
    missing_u = []
    for name in missing:
        if name not in seen_m:
            seen_m.add(name)
            missing_u.append(name)
    if missing_u:
        pad = np.full((int(arr.shape[0]), len(missing_u)), np.nan, dtype=np.float64)
        arr = np.concatenate([arr, pad], axis=1)
        for name in missing_u:
            index[name] = len(names)
            names.append(name)
    for src, dst in pairs:
        j_src = index.get(src)
        j_dst = index.get(dst)
        if j_src is None or j_dst is None:
            continue
        src_col = arr[:, j_src]
        finite = np.isfinite(src_col)
        if not np.any(finite):
            continue
        arr[finite, j_dst] = src_col[finite]
    panel["X"] = arr
    panel["x_names"] = names
    panel.pop("_x_index", None)


def stamp_ath_on_panels(
    panels: Sequence[Dict[str, Any]],
    head: str,
) -> None:
    """对已 relabel 的面板逐行补 ``raw_ath_H_*``。"""
    for panel in panels or []:
        if not isinstance(panel, dict):
            continue
        if panel.get("X") is not None and not (panel.get("xs") or []):
            _promote_ath_matrix(panel, head)
            continue
        xs = panel.get("xs")
        if not isinstance(xs, list):
            continue
        for row in xs:
            if isinstance(row, dict):
                promote_ath_head_aliases(row, head)


def inject_raw_ath(
    dest: Optional[dict],
    bars: Optional[Sequence[dict]],
    *,
    open_t: Optional[float] = None,
    prev_close: Optional[float] = None,
    price_tau: Optional[float] = None,
    minute_bars: Optional[Sequence[dict]] = None,
    tau_hm: Optional[str] = None,
    quote: Optional[dict] = None,
    feats: Optional[dict] = None,
    head: Optional[str] = None,
) -> dict:
    """写入 ``raw_ath_*``（就地）。``feats`` 为已有分钟/seq 行时可升格。"""
    out = dest if isinstance(dest, dict) else {}
    try:
        extras = raw_ath_from_bars(
            bars,
            open_t=open_t,
            prev_close=prev_close,
            price_tau=price_tau,
            minute_bars=minute_bars,
            tau_hm=tau_hm,
            quote=quote,
        )
    except Exception:  # noqa: BLE001
        logger.debug("alpha_th inject from bars failed", exc_info=True)
        extras = {}
    if extras:
        out.update(extras)
    _promote_feats_to_ath(out, feats if feats is not None else out)
    if head:
        promote_ath_head_aliases(out, head)
    return out


def _prepare_ath_pit_bars(
    bars: Optional[Sequence[dict]],
    *,
    open_t: Optional[float] = None,
    prev_close: Optional[float] = None,
    price_tau: Optional[float] = None,
    minute_bars: Optional[Sequence[dict]] = None,
    tau_hm: Optional[str] = None,
    quote: Optional[dict] = None,
) -> Tuple[
    Optional[List[dict]],
    Optional[float],
    Optional[float],
    Optional[float],
    Dict[str, Any],
]:
    """短窗 PIT：hist≤T−1 + 今开合成根；门槛 ``ALPHA_TH_MIN_BARS``（非 alpha_tc 61）。"""
    try:
        from core.signal.factors.alpha_tc import (
            _fpx,
            _normalize_tau_hm,
            _prefix_summary,
            _resolve_trade_date,
            resolve_open_t,
            resolve_prev_close,
        )
    except Exception:  # noqa: BLE001
        logger.debug("alpha_th import pit helpers failed", exc_info=True)
        return None, None, None, None, {"alpha_th_import_error": True}

    hist = [b for b in (bars or []) if isinstance(b, dict)]
    hm = _normalize_tau_hm(tau_hm)
    ot = resolve_open_t(open_t=open_t, quote=quote, bars=None)
    if ot is None and hist:
        ot = _fpx(hist[-1].get("open"))
        if ot is not None and len(hist) >= _MIN_BARS:
            complete = list(hist[:-1])
        else:
            complete = list(hist)
            ot = resolve_open_t(open_t=open_t, quote=quote, bars=hist)
    else:
        complete = list(hist)

    if ot is None:
        return None, None, None, None, {"alpha_th_need_open": True}
    if len(complete) < _MIN_BARS - 1:
        return None, None, None, None, {"alpha_th_insufficient_history": True}

    pc = resolve_prev_close(
        prev_close=prev_close, quote=quote, hist_complete=complete
    )
    if pc is None:
        return None, None, None, None, {"alpha_th_need_prev_close": True}

    day = _resolve_trade_date(quote=quote, hist=complete, minute_bars=minute_bars)
    pt_hint = _fpx(price_tau)
    prefix_fields, pref_hi, pref_lo, cum_vol, last_px = _prefix_summary(
        minute_bars,
        trade_date=day,
        tau_hm=hm,
        open_t=float(ot),
        price_tau=float(pt_hint or ot),
    )
    pt = pt_hint
    if pt is None and last_px > _EPS and minute_bars:
        pt = float(last_px)
    if pt is None:
        pt = float(ot)
    if minute_bars and day:
        prefix_fields, pref_hi, pref_lo, cum_vol, last_px = _prefix_summary(
            minute_bars,
            trade_date=day,
            tau_hm=hm,
            open_t=float(ot),
            price_tau=float(pt),
        )
    else:
        pref_hi = float(ot)
        pref_lo = float(ot)
        cum_vol = 0.0
    _ = prefix_fields
    y_vol = float(complete[-1].get("volume") or 1.0)
    syn_vol = float(cum_vol) if cum_vol > _EPS else y_vol
    syn = {
        "date": day,
        "trade_date": day,
        "open": float(ot),
        "high": float(max(pref_hi, ot, pt)),
        "low": float(min(pref_lo, ot, pt)),
        "close": float(pt),
        "volume": syn_vol,
        "session_tau_pit": True,
    }
    out_bars = list(complete) + [syn]
    if len(out_bars) < _MIN_BARS:
        return None, None, None, None, {"alpha_th_insufficient_history": True}
    return (
        out_bars,
        float(ot),
        float(pc),
        float(pt),
        {"prefix_hi": float(pref_hi), "prefix_lo": float(pref_lo)},
    )


def raw_ath_from_bars(
    bars: Optional[Sequence[dict]],
    *,
    open_t: Optional[float] = None,
    prev_close: Optional[float] = None,
    price_tau: Optional[float] = None,
    minute_bars: Optional[Sequence[dict]] = None,
    tau_hm: Optional[str] = None,
    quote: Optional[dict] = None,
) -> dict:
    """只算 GAP/YK 轻量；窗动量留给 ``inject`` 从 feats 升格。"""
    try:
        from core.signal.factors.alpha_tc import (
            _gap_fields,
            _to_arrays,
            _yk_fields,
        )
    except Exception:  # noqa: BLE001
        logger.debug("alpha_th import alpha_tc helpers failed", exc_info=True)
        return {}

    prepared, ot, pc, pt, flags = _prepare_ath_pit_bars(
        list(bars or []),
        open_t=open_t,
        prev_close=prev_close,
        price_tau=price_tau,
        minute_bars=minute_bars,
        tau_hm=tau_hm,
        quote=quote,
    )
    if prepared is None or ot is None or pc is None or pt is None:
        return {}
    if len(prepared) < 2:
        return {}
    try:
        close, open_arr, high, low, volume = _to_arrays(prepared)
    except Exception:  # noqa: BLE001
        logger.debug("alpha_th to_arrays failed", exc_info=True)
        return {}
    if float(pt) < _EPS or float(open_arr[-1]) < _EPS:
        return {}
    y_o = float(open_arr[-2])
    y_c = float(close[-2])
    y_h = float(high[-2])
    y_l = float(low[-2])
    y_v = float(volume[-2])
    if y_c < _EPS or y_o < _EPS:
        return {}
    pref_hi = float(flags.get("prefix_hi") or ot)
    pref_lo = float(flags.get("prefix_lo") or ot)
    bare: dict = {}
    try:
        gap = _gap_fields(
            float(ot),
            float(pt),
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
            prefix_hi=pref_hi,
            prefix_lo=pref_lo,
        )
        for k in _GAP_KEYS:
            if k in gap and gap[k] is not None:
                bare[k] = float(gap[k])
        yk = _yk_fields(y_c, y_o, y_h, y_l)
        for k in _YK_KEYS:
            if k == "MOM3_C":
                continue
            if k in yk and yk[k] is not None:
                bare[k] = float(yk[k])
        # MOM3_C：近 3 日收盘动量（hist 末根 = 昨收）
        if len(close) >= 5:
            bare["MOM3_C"] = float(close[-2] / (close[-5] + _EPS) - 1.0)
    except Exception:  # noqa: BLE001
        logger.debug("alpha_th gap/yk failed", exc_info=True)
        return {}
    out: dict = {}
    for k, v in bare.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[ath_raw_key(k)] = float(v)
    return out


def score_alpha_th(
    bars: Optional[List[dict]] = None,
    *,
    open_t: Optional[float] = None,
    prev_close: Optional[float] = None,
    price_tau: Optional[float] = None,
    minute_bars: Optional[Sequence[dict]] = None,
    tau_hm: Optional[str] = None,
    quote: Optional[dict] = None,
    **_kw: Any,
) -> Tuple[float, dict]:
    """registry 用：返回 omit 分 + bare meta（不含前缀）。"""
    raw = raw_ath_from_bars(
        bars,
        open_t=open_t,
        prev_close=prev_close,
        price_tau=price_tau,
        minute_bars=minute_bars,
        tau_hm=tau_hm,
        quote=quote,
    )
    meta: dict = {"omit_sub_score": True, "alpha_th_anchor": "price_tau"}
    pref = ALPHA_TH_RAW_PREFIX
    for k, v in raw.items():
        sk = str(k)
        if sk.startswith(pref):
            meta[sk[len(pref) :]] = v
    return 50.0, meta
