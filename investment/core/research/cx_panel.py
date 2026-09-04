"""ŷ_cx 训练面板：开盘 Z + 早盘前缀分钟小包 → 全日 5m 路径曲折度。

标签（Kaufman 效率比的补，有界、无量纲）：

    D = |C_last − C_first|     全日 5m 收价首末位移
    L = Σ |ΔC|                 相邻 5m 路径长（午休/停牌跳空不计入）
    y_cx = 1 − min(1, D/L)     ∈ [0, 1]

0 = 直线（曲折度最低）；1 = 最折。与波动率不同：单边趋势可以振幅大但 y_cx 低。

无未来函数：特征 = 开盘 Z + ≤τ 分钟前缀 + 历史真实 cx（cx_lag1 / cx_ma5，不含当日）；
标签用全日 5m 路径（1−D/L 不变）。
研究枢纽拟合；做 T 入场用盘中前缀 ŷ_cx，ŷ_cx > y_cx_max 则跳过（不用全日 realized 标签）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

from core.research.path_panel import (
    DEFAULT_PATH_MINUTE_TAU_HM,
    DEFAULT_PATH_TAU_GRID,
    PATH_Z_FEATURES,
    attach_path_minute_feats,
    path_features_from_open_row,
)
from core.research.tau_panel import (
    attach_cross_section_breadth,
    collect_tau_open_panel,
    hist_bars_pit,
    label_lag_features,
    normalize_minute_tau_grid,
)

CX_LAG_WINDOW = 5
CX_LAG_FEATURES = ("cx_lag1", "cx_ma5")
CX_LAG_FEAT_LABELS = {
    "cx_lag1": "昨真实曲折度",
    "cx_ma5": "近5日真实曲折度均",
}
CX_Z_FEATURES = PATH_Z_FEATURES + CX_LAG_FEATURES
DEFAULT_CX_MINUTE_TAU_HM = DEFAULT_PATH_MINUTE_TAU_HM
DEFAULT_CX_TAU_GRID = DEFAULT_PATH_TAU_GRID
# 相邻 5m 超过此时长视为会话断开（午休约 90m），不把跳空算进路径长
CX_MAX_STEP_MIN = 20
CX_MIN_BARS = 6
# live：code → {date: y_cx}，避免每根 5m 扫描重算历史曲折度
_CX_REALIZED_BY_CODE: Dict[str, Dict[str, float]] = {}


def _f(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return v


def _bar_hm_minutes(bar: dict) -> Optional[int]:
    t = str(bar.get("time") or bar.get("datetime") or bar.get("date") or "")
    hm = ""
    for part in t.replace("T", " ").split(" "):
        if ":" in part:
            hm = part[:5].replace(":", "")
            break
    if not hm:
        digits = "".join(ch for ch in t if ch.isdigit())
        if len(digits) >= 12:
            hm = digits[8:12]
        elif len(digits) >= 4:
            hm = digits[-4:]
    if len(hm) != 4 or not hm.isdigit():
        return None
    return int(hm[:2]) * 60 + int(hm[2:])


def _ordered_closes(minute_bars: Sequence[dict]) -> List[Tuple[int, float]]:
    pts: List[Tuple[int, float]] = []
    for bar in minute_bars or []:
        if not isinstance(bar, dict):
            continue
        c = _f(bar.get("close"))
        t = _bar_hm_minutes(bar)
        if c is None or c <= 0 or t is None:
            continue
        pts.append((t, float(c)))
    pts.sort(key=lambda x: x[0])
    return pts


def cx_complexity_label(
    minute_bars: Sequence[dict],
    *,
    min_bars: int = CX_MIN_BARS,
    max_step_min: int = CX_MAX_STEP_MIN,
) -> Tuple[Optional[float], str, Dict[str, Any]]:
    """全日 5m 收价路径曲折度 1−D/L ∈ [0,1]；直线≈0。"""
    pts = _ordered_closes(minute_bars)
    meta: Dict[str, Any] = {
        "n_bars": len(pts),
        "n_steps": 0,
        "n_skipped_gaps": 0,
        "path_len": None,
        "displacement": None,
        "efficiency": None,
    }
    if len(pts) < max(3, int(min_bars)):
        return None, "too_few_bars", meta
    length = 0.0
    n_steps = 0
    n_skip = 0
    cap = max(5, int(max_step_min))
    for i in range(1, len(pts)):
        dt = pts[i][0] - pts[i - 1][0]
        if dt <= 0:
            continue
        if dt > cap:
            n_skip += 1
            continue
        length += abs(pts[i][1] - pts[i - 1][1])
        n_steps += 1
    disp = abs(pts[-1][1] - pts[0][1])
    meta["n_steps"] = n_steps
    meta["n_skipped_gaps"] = n_skip
    meta["path_len"] = round(length, 6)
    meta["displacement"] = round(disp, 6)
    if n_steps < 2:
        return None, "too_few_steps", meta
    if length <= 1e-12:
        meta["efficiency"] = 1.0
        return 0.0, "flat_path", meta
    er = min(1.0, disp / length)
    meta["efficiency"] = round(er, 6)
    y = 1.0 - er
    return round(y, 6), "ok", meta


def realized_cx_by_date(
    minute_by_date: Optional[Dict[str, Sequence[dict]]],
) -> Dict[str, float]:
    """各日全日 5m 真实 y_cx；算不出的日不进表。"""
    out: Dict[str, float] = {}
    for dkey, bars in (minute_by_date or {}).items():
        day = str(dkey or "")[:10]
        if len(day) < 10:
            continue
        y, _reason, _meta = cx_complexity_label(bars)
        if y is None:
            continue
        out[day] = float(y)
    return out


def cx_lag_features(
    *,
    hist_bars: Sequence[dict],
    cx_by_date: Dict[str, float],
    asof_date: str,
    window: int = CX_LAG_WINDOW,
) -> Dict[str, Optional[float]]:
    """PIT：只用 asof 之前、且有 5m 真实 cx 的交易日；最多 ``window`` 天。"""
    return label_lag_features(
        hist_bars=hist_bars,
        by_date=cx_by_date,
        asof_date=asof_date,
        window=window,
        lag1_key="cx_lag1",
        ma_key="cx_ma5",
    )


def _load_realized_cx_map_for_code(stock_code: str) -> Dict[str, float]:
    code = str(stock_code or "").strip()
    if not code:
        return {}
    hit = _CX_REALIZED_BY_CODE.get(code)
    if hit is not None:
        return hit
    by_date: Dict[str, List[dict]] = {}
    try:
        from core.ports.market import resolve_market_code
        from core.signal.minute_tau_feats import _minute_bars_by_date
        from core.store import load_minute_cache

        mkt, pure = resolve_market_code(code)
        packed = load_minute_cache(
            mkt or "CN",
            pure or code,
            period="5",
            min_bars=CX_MIN_BARS,
            ignore_age=True,
        )
        bars = list((packed[0] if packed else None) or [])
        by_date = dict(_minute_bars_by_date(bars) or {})
    except Exception:  # noqa: BLE001
        logger.debug("load realized cx minutes failed", exc_info=True)
        by_date = {}
    mapped = realized_cx_by_date(by_date)
    _CX_REALIZED_BY_CODE[code] = mapped
    return mapped


def attach_cx_lag_features(
    feats: Optional[dict],
    *,
    hist_bars: Sequence[dict],
    asof_date: str,
    minute_by_date: Optional[Dict[str, Sequence[dict]]] = None,
    stock_code: str = "",
) -> Dict[str, Any]:
    """把 cx_lag1 / cx_ma5 写入特征行；缺历史分钟则留空，不挡打分。"""
    out: Dict[str, Any] = dict(feats or {})
    hist = hist_bars_pit(hist_bars, asof_date=asof_date)
    if isinstance(minute_by_date, dict) and minute_by_date:
        cx_map = realized_cx_by_date(minute_by_date)
    else:
        cx_map = _load_realized_cx_map_for_code(stock_code)
    lags = cx_lag_features(
        hist_bars=hist,
        cx_by_date=cx_map,
        asof_date=asof_date,
    )
    out["cx_lag1"] = lags.get("cx_lag1")
    out["cx_ma5"] = lags.get("cx_ma5")
    return out


def cx_as_unit_01(
    v: Any,
    *,
    model_doc: Optional[dict] = None,
) -> Optional[float]:
    """把曲折度统一到 [0, 1]。旧模型 (1−D/L)×100 在 |v|>1.5 时 /100。"""
    x = _f(v)
    if x is None:
        return None
    unit = ""
    if isinstance(model_doc, dict):
        rm = model_doc.get("return_model")
        spec = {}
        if isinstance(rm, dict) and isinstance(rm.get("y_spec"), dict):
            spec = rm["y_spec"]
        elif isinstance(model_doc.get("y_spec"), dict):
            spec = model_doc["y_spec"]
        unit = str(spec.get("unit") or "").strip()
    if unit == "complexity_01":
        pass
    elif unit == "pct_complexity" or abs(x) > 1.5:
        x = x / 100.0
    return max(0.0, min(round(x, 6), 1.0))


def attach_cx_realized(
    day: Optional[dict],
    minute_bars: Sequence[dict],
) -> Dict[str, Any]:
    """把全日 5m 曲折度标签写入日结果（对照用，不进做 T 闸）。"""
    out: Dict[str, Any] = dict(day or {})
    if not minute_bars:
        return out
    y, reason, meta = cx_complexity_label(minute_bars)
    if y is None:
        return out
    val = float(y)
    out["y_cx"] = val
    out["cx_realized"] = val
    out["cx_realized_reason"] = str(reason)
    if meta.get("efficiency") is not None:
        out["cx_efficiency"] = meta.get("efficiency")
    if meta.get("path_len") is not None:
        out["cx_path_len"] = meta.get("path_len")
    if meta.get("displacement") is not None:
        out["cx_displacement"] = meta.get("displacement")
    scores = dict(out.get("scores") or {}) if isinstance(out.get("scores"), dict) else {}
    scores["y_cx"] = val
    scores["cx_realized"] = val
    scores["cx_realized_reason"] = str(reason)
    out["scores"] = scores
    feats = (
        dict(out.get("direction_features") or {})
        if isinstance(out.get("direction_features"), dict)
        else {}
    )
    feats["y_cx"] = val
    feats["cx_realized"] = val
    feats["cx_realized_reason"] = str(reason)
    out["direction_features"] = feats
    return out


def _normalize_cx_tau_hm(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return DEFAULT_CX_MINUTE_TAU_HM
    if ":" in s:
        return s[:5]
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 4:
        return f"{digits[:2]}:{digits[2:4]}"
    return DEFAULT_CX_MINUTE_TAU_HM


def collect_cx_day_sample(
    *,
    code: str,
    day_bar: dict,
    hist_bars: Sequence[dict],
    minute_bars: Sequence[dict],
    tau_row: Optional[dict] = None,
    minute_tau_hm: Any = None,
    cx_by_date: Optional[Dict[str, float]] = None,
) -> Optional[Dict[str, Any]]:
    """单日：开盘 Z + 早盘前缀分钟小包 + 历史真实 cx + 全日曲折度标签。"""
    if not isinstance(day_bar, dict):
        return None
    daily_open = _f(day_bar.get("open"))
    minute_open = None
    for b in minute_bars or []:
        if not isinstance(b, dict):
            continue
        minute_open = _f(b.get("open"))
        if minute_open is not None and minute_open > 0:
            break
    ref = minute_open if minute_open is not None and minute_open > 0 else daily_open
    if ref is None or ref <= 0:
        return None
    hist = hist_bars_pit(hist_bars, asof_date=str(day_bar.get("date") or "")[:10])
    prev = hist[-1] if hist else None
    base = dict(tau_row or {})
    feats = path_features_from_open_row(base, hist=hist, prev_bar=prev)
    if feats.get("gap_pct") is None:
        pc = _f(day_bar.get("prev_close"))
        if pc is None and prev:
            pc = _f(prev.get("close"))
        gap_open = daily_open if daily_open and daily_open > 0 else ref
        if pc and gap_open:
            feats["gap_pct"] = round((float(gap_open) / float(pc) - 1.0) * 100.0, 4)
    dkey = str(day_bar.get("date") or "")[:10]
    pc_feat = _f(day_bar.get("prev_close"))
    if pc_feat is None and prev:
        pc_feat = _f(prev.get("close"))
    hm = minute_tau_hm
    if hm is None and isinstance(tau_row, dict):
        hm = tau_row.get("tau_hm") or tau_row.get("as_of_tau")
    feats = attach_path_minute_feats(
        feats,
        minute_bars=minute_bars,
        trade_date=dkey,
        open_px=float(ref),
        prev_close=pc_feat,
        tau_hm=hm,
    )
    lags = cx_lag_features(
        hist_bars=hist,
        cx_by_date=cx_by_date if isinstance(cx_by_date, dict) else {},
        asof_date=dkey,
    )
    feats["cx_lag1"] = lags.get("cx_lag1")
    feats["cx_ma5"] = lags.get("cx_ma5")
    label, reason, lab_meta = cx_complexity_label(minute_bars)
    if label is None:
        return None
    return {
        "code": str(code or "").strip(),
        "date": dkey,
        "features": feats,
        "label": float(label),
        "label_reason": reason,
        "label_meta": lab_meta,
        "ref": float(ref),
        "minute_tau_hm": _normalize_cx_tau_hm(hm),
    }


def build_cx_panels_from_bars(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    minute_by_code_date: Optional[Dict[str, Dict[str, Sequence[dict]]]] = None,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    minute_tau_hm: Any = None,
    tau_grid: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """批量：特征与 ŷ_τ / ŷ_path 对齐；标签=全日 5m 曲折度。

    ``tau_grid`` 非空：同日多 τ 各一行、标签相同、共享 β。
    """
    minute_map = minute_by_code_date if isinstance(minute_by_code_date, dict) else {}
    if tau_grid is not None:
        clocks = normalize_minute_tau_grid(
            tau_hm=minute_tau_hm or DEFAULT_CX_MINUTE_TAU_HM,
            tau_grid=tau_grid,
        )
    else:
        clocks = [
            _normalize_cx_tau_hm(
                minute_tau_hm if minute_tau_hm is not None else DEFAULT_CX_MINUTE_TAU_HM
            )
        ]
    enriched: List[Dict[str, Any]] = []
    for pack in stock_bars or []:
        if not isinstance(pack, dict):
            continue
        code = str(pack.get("code") or pack.get("stock_code") or "").strip()
        bars = [b for b in (pack.get("bars") or []) if isinstance(b, dict)]
        if not code or len(bars) < max(8, int(min_history) + 1):
            continue
        tau_xs, _tau_ys, tau_dates, _tau_metas = collect_tau_open_panel(
            bars,
            min_history=min_history,
            stock_code=code,
        )
        tau_by_date: Dict[str, dict] = {}
        for i, dkey in enumerate(tau_dates):
            if i < len(tau_xs) and isinstance(tau_xs[i], dict):
                tau_by_date[str(dkey)[:10]] = dict(tau_xs[i])
        code_mins = minute_map.get(code) if isinstance(minute_map.get(code), dict) else {}
        cx_by_date = realized_cx_by_date(code_mins)
        xs_out: List[dict] = []
        ys_out: List[float] = []
        dates_out: List[str] = []
        metas_out: List[dict] = []
        for i in range(1, len(bars)):
            day = bars[i]
            dkey = str(day.get("date") or "")[:10]
            if len(dkey) < 10:
                continue
            mins = code_mins.get(dkey) if isinstance(code_mins, dict) else None
            if not mins:
                continue
            hist = bars[:i]
            for hm in clocks:
                sample = collect_cx_day_sample(
                    code=code,
                    day_bar=day,
                    hist_bars=hist,
                    minute_bars=mins,
                    tau_row=tau_by_date.get(dkey),
                    minute_tau_hm=hm,
                    cx_by_date=cx_by_date,
                )
                if not sample:
                    continue
                xs_out.append(sample["features"])
                ys_out.append(float(sample["label"]))
                dates_out.append(dkey)
                metas_out.append(
                    {
                        "code": code,
                        "stock_code": code,
                        "date": dkey,
                        "tau": sample.get("minute_tau_hm") or hm,
                        "minute_tau_hm": sample.get("minute_tau_hm") or hm,
                        "label_reason": sample.get("label_reason"),
                        "gap_pct": sample["features"].get("gap_pct"),
                    }
                )
        if xs_out:
            enriched.append(
                {
                    "code": code,
                    "xs": xs_out,
                    "ys": ys_out,
                    "dates": dates_out,
                    "metas": metas_out,
                }
            )
    if not enriched:
        return []
    return attach_cross_section_breadth(enriched, gap_trigger_pct=gap_trigger_pct)
