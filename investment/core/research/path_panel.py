"""ŷ_path 训练面板：与 ŷ_τ 同因子集（开盘 Z + 早盘前缀分钟小包）→ 全日极值时间序。

标签（相对锚价 ref 的百分点）：
  先 low 后 high → y_path = (high−low)/ref×100
  先 high 后 low → y_path = (low−high)/ref×100

``first_touch_path_label`` 保留供触价对照；训练与 path实 用 ``extreme_order_path_label``。

无未来函数：特征 = 开盘信息集 + ≤τ（默认 10:30）分钟前缀；标签可用全日分钟极值序。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

from core.research.tau_panel import (
    DEFAULT_MINUTE_TAU_GRID,
    attach_cross_section_breadth,
    collect_tau_open_panel,
    hist_bars_pit,
    mom3_pct_from_hist,
    normalize_minute_tau_grid,
    tau_elapsed_min_from_open,
    yclose_loc_from_prev,
)
from core.signal.minute_tau_feats import MINUTE_TAU_ALL_KEYS, extract_minute_tau_pack

# 与 ŷ_τ 开盘 Z 同源；分钟小包同属做 T 早盘前缀信息集（默认 τ=10:30）
PATH_OPEN_FEATURES = (
    "gap_pct",
    "sector_gap_breadth",
    "theme_day",
    "gap_atr",
    "gap_vs_sector",
    "yclose_loc",
    "mom3_pct",
)
PATH_Z_FEATURES = PATH_OPEN_FEATURES + MINUTE_TAU_ALL_KEYS
# path live 默认决策钟（与 dual_score / τ 头一致；做 T 选腿已改 v6 收盘带宽）
DEFAULT_PATH_MINUTE_TAU_HM = "10:30"
# 训练多 τ 默认网格（09:30…11:00 每 5m；与 τ 头 / dual_score.minute_tau_grid 一致）
DEFAULT_PATH_TAU_GRID = DEFAULT_MINUTE_TAU_GRID


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


def _yclose_loc(prev_bar: Optional[dict], open_px: float) -> Optional[float]:
    return yclose_loc_from_prev(prev_bar, open_px)


def _mom3_pct(hist: Sequence[dict]) -> Optional[float]:
    return mom3_pct_from_hist(hist)


def feature_fill_rates(xs: Sequence[dict], keys: Sequence[str]) -> Dict[str, Any]:
    """面板特征非空率（path / τ 验收）。"""
    rows = [r for r in xs if isinstance(r, dict)]
    n = len(rows)
    out: Dict[str, Any] = {"n": n, "keys": {}}
    for k in keys:
        filled = sum(1 for r in rows if r.get(k) is not None and r.get(k) != "")
        out["keys"][str(k)] = {
            "filled": filled,
            "rate": round(filled / float(n), 4) if n else None,
        }
    return out


def audit_path_minute_coverage(
    stock_bars: Sequence[Dict[str, Any]],
    minute_by_code_date: Optional[Dict[str, Dict[str, Sequence[dict]]]],
    *,
    sell_trig_pct: float = 2.0,
    buy_trig_pct: float = 1.5,
) -> Dict[str, Any]:
    """日线↔分钟对齐与触达丢弃诊断（不改标签）。"""
    minute_map = minute_by_code_date if isinstance(minute_by_code_date, dict) else {}
    daily_days = 0
    minute_days = 0
    overlap = 0
    no_minute = 0
    reasons: Dict[str, int] = {}
    spans: List[int] = []
    mismatch_gt = 0
    mismatch_checked = 0
    for pack in stock_bars or []:
        if not isinstance(pack, dict):
            continue
        code = str(pack.get("code") or pack.get("stock_code") or "").strip()
        bars = [b for b in (pack.get("bars") or []) if isinstance(b, dict)]
        by_day = minute_map.get(code) if isinstance(minute_map.get(code), dict) else {}
        if not isinstance(by_day, dict):
            by_day = {}
        if by_day:
            spans.append(len(by_day))
            minute_days += len(by_day)
        daily_set = {
            str(b.get("date") or "")[:10]
            for b in bars
            if len(str(b.get("date") or "")[:10]) == 10
        }
        daily_days += len(daily_set)
        overlap += len(daily_set & set(by_day.keys()))
        for i in range(1, len(bars)):
            day = bars[i]
            dkey = str(day.get("date") or "")[:10]
            if len(dkey) < 10:
                continue
            mins = by_day.get(dkey)
            if not mins:
                no_minute += 1
                continue
            daily_open = _f(day.get("open"))
            minute_open = None
            for b in mins:
                if isinstance(b, dict):
                    minute_open = _f(b.get("open"))
                    if minute_open is not None and minute_open > 0:
                        break
            if daily_open and daily_open > 0 and minute_open and minute_open > 0:
                mismatch_checked += 1
                if abs(float(minute_open) / float(daily_open) - 1.0) > 0.005:
                    mismatch_gt += 1
            ref = minute_open if minute_open and minute_open > 0 else daily_open
            if ref is None or ref <= 0:
                reasons["invalid_ref"] = reasons.get("invalid_ref", 0) + 1
                continue
            _lab, why = extreme_order_path_label(
                mins,
                ref=float(ref),
            )
            reasons[str(why)] = reasons.get(str(why), 0) + 1
    spans_sorted = sorted(spans)
    med_span = spans_sorted[len(spans_sorted) // 2] if spans_sorted else 0
    return {
        "daily_days": daily_days,
        "minute_days": minute_days,
        "overlap_days": overlap,
        "no_minute_for_day": no_minute,
        "minute_span_days_min": spans_sorted[0] if spans_sorted else 0,
        "minute_span_days_med": med_span,
        "minute_span_days_max": spans_sorted[-1] if spans_sorted else 0,
        "codes_with_minute": len(spans),
        "open_mismatch_gt_0_5pct": mismatch_gt,
        "open_mismatch_checked": mismatch_checked,
        "touch_reasons": reasons,
        "warnings": (
            [f"分钟跨度中位仅 {med_span} 日（远短于日线回看，OOS 易噪声）"]
            if med_span and med_span < 40
            else []
        )
        + (
            [f"日线/分钟开盘偏差>0.5%：{mismatch_gt}/{mismatch_checked} 日"]
            if mismatch_gt
            else []
        ),
    }


def _minute_day_extremes(minute_bars: Sequence[dict]) -> Tuple[Optional[float], Optional[float]]:
    day_hi: Optional[float] = None
    day_lo: Optional[float] = None
    for bar in minute_bars or []:
        if not isinstance(bar, dict):
            continue
        hi = _f(bar.get("high"))
        lo = _f(bar.get("low"))
        if hi is not None:
            day_hi = hi if day_hi is None else max(day_hi, hi)
        if lo is not None:
            day_lo = lo if day_lo is None else min(day_lo, lo)
    return day_hi, day_lo


def _first_extreme_bar_indices(
    minute_bars: Sequence[dict],
    day_high: float,
    day_low: float,
    *,
    eps: float = 1e-6,
) -> Tuple[Optional[int], Optional[int]]:
    """返回 (low_idx, high_idx)：日内极值首次出现的 bar 下标。"""
    low_idx: Optional[int] = None
    high_idx: Optional[int] = None
    for i, bar in enumerate(minute_bars or []):
        if not isinstance(bar, dict):
            continue
        lo = _f(bar.get("low"))
        hi = _f(bar.get("high"))
        if low_idx is None and lo is not None and lo <= day_low + eps:
            low_idx = i
        if high_idx is None and hi is not None and hi >= day_high - eps:
            high_idx = i
        if low_idx is not None and high_idx is not None:
            break
    return low_idx, high_idx


def extreme_order_path_label(
    minute_bars: Sequence[dict],
    *,
    ref: float,
) -> Tuple[float, str]:
    """极值时间序：先 low→high 则 (H−L)/ref%；先 high→low 则 (L−H)/ref%。"""
    if ref <= 0 or not minute_bars:
        return 0.0, "invalid_ref_or_empty"
    day_hi, day_lo = _minute_day_extremes(minute_bars)
    if day_hi is None or day_lo is None:
        return 0.0, "invalid_ref_or_empty"
    span = float(day_hi) - float(day_lo)
    if span <= 1e-9:
        return 0.0, "flat_range"
    low_idx, high_idx = _first_extreme_bar_indices(minute_bars, day_hi, day_lo)
    if low_idx is None or high_idx is None:
        return 0.0, "invalid_ref_or_empty"
    if low_idx == high_idx:
        return 0.0, "same_bar_extreme"
    span_pct = span / float(ref) * 100.0
    if low_idx < high_idx:
        return round(span_pct, 4), "low_then_high"
    return round(-span_pct, 4), "high_then_low"


def first_touch_path_label(
    minute_bars: Sequence[dict],
    *,
    ref: float,
    sell_trig_pct: float,
    buy_trig_pct: float,
) -> Tuple[float, str]:
    """返回 (label_pct, reason)。label 为 ±100 或 0。"""
    if ref <= 0 or not minute_bars:
        return 0.0, "invalid_ref_or_empty"
    sell_level = float(ref) * (1.0 + float(sell_trig_pct) / 100.0)
    buy_level = float(ref) * (1.0 - float(buy_trig_pct) / 100.0)
    for bar in minute_bars:
        if not isinstance(bar, dict):
            continue
        hi = _f(bar.get("high"))
        lo = _f(bar.get("low"))
        if hi is None or lo is None:
            continue
        hit_sell = hi >= sell_level
        hit_buy = lo <= buy_level
        if hit_sell and hit_buy:
            return 0.0, "same_bar_both"
        if hit_sell:
            return 100.0, "sell_first"
        if hit_buy:
            return -100.0, "buy_first"
    return 0.0, "no_touch"


def attach_path_realized(
    day: Optional[dict],
    minute_bars: Sequence[dict],
    *,
    ref: Optional[float] = None,
    sell_trig_pct: float = 2.0,
    buy_trig_pct: float = 1.5,
) -> Dict[str, Any]:
    """把真实极值序标签写入日结果（signed (H−L)/ref %，与 ŷ_path 同尺度）。"""
    out: Dict[str, Any] = dict(day or {})
    minute_open = None
    for b in minute_bars or []:
        if isinstance(b, dict):
            minute_open = _f(b.get("open"))
            if minute_open is not None and minute_open > 0:
                break
    open_px = ref
    if open_px is None:
        open_px = minute_open if minute_open and minute_open > 0 else _f(out.get("open"))
    if open_px is None and isinstance(out.get("bar"), dict):
        open_px = _f(out["bar"].get("open"))
    if open_px is None or float(open_px) <= 0 or not minute_bars:
        return out
    label, reason = extreme_order_path_label(
        minute_bars,
        ref=float(open_px),
    )
    day_hi, day_lo = _minute_day_extremes(minute_bars)
    out["path_realized"] = float(label)
    out["path_realized_reason"] = str(reason)
    if day_hi is not None and day_lo is not None:
        out["path_realized_range"] = round(float(day_hi) - float(day_lo), 6)
        out["path_realized_range_pct"] = round(
            (float(day_hi) - float(day_lo)) / float(open_px) * 100.0, 4
        )
    # 同步进 scores / direction_features，供 tip / 表列读取
    scores = dict(out.get("scores") or {}) if isinstance(out.get("scores"), dict) else {}
    scores["path_realized"] = float(label)
    scores["path_realized_reason"] = str(reason)
    out["scores"] = scores
    feats = (
        dict(out.get("direction_features") or {})
        if isinstance(out.get("direction_features"), dict)
        else {}
    )
    feats["path_realized"] = float(label)
    feats["path_realized_reason"] = str(reason)
    out["direction_features"] = feats
    return out


def path_features_from_open_row(
    row: dict,
    *,
    hist: Optional[Sequence[dict]] = None,
    prev_bar: Optional[dict] = None,
) -> Dict[str, Optional[float]]:
    """从 τ 开盘/前缀行补齐 PATH_Z（含分钟小包键；缺则 None）。"""
    out: Dict[str, Optional[float]] = {}
    for k in PATH_Z_FEATURES:
        if k in row:
            out[k] = _f(row.get(k))
        else:
            out[k] = None
    open_px = _f(row.get("open"))
    if open_px is None:
        open_px = _f(row.get("open_px"))
    if out.get("yclose_loc") is None and open_px is not None:
        out["yclose_loc"] = _yclose_loc(prev_bar, float(open_px))
    if out.get("mom3_pct") is None and hist:
        out["mom3_pct"] = _mom3_pct(hist)
    return out


def _normalize_path_tau_hm(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return DEFAULT_PATH_MINUTE_TAU_HM
    if ":" in s:
        return s[:5]
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 4:
        return f"{digits[:2]}:{digits[2:4]}"
    return DEFAULT_PATH_MINUTE_TAU_HM


def attach_path_minute_feats(
    feats: dict,
    *,
    minute_bars: Sequence[dict],
    trade_date: str,
    open_px: Optional[float] = None,
    prev_close: Optional[float] = None,
    tau_hm: Any = None,
    overwrite: bool = False,
) -> Dict[str, Optional[float]]:
    """写入 ≤τ 分钟小包（与 ŷ_τ enable_minute_tau 同源）。

    默认已有非空键不覆盖；``overwrite=True`` 时用当前前缀重算覆盖
    （画像/确认根因果打分须避开开盘快照里的脏分钟键）。
    """
    out = dict(feats or {})
    hm = _normalize_path_tau_hm(tau_hm)
    pack = extract_minute_tau_pack(
        minute_bars,
        trade_date=str(trade_date or "")[:10],
        tau_hm=hm,
        open_px=open_px,
        prev_close=prev_close,
    )
    for k in MINUTE_TAU_ALL_KEYS:
        if not overwrite and out.get(k) is not None:
            continue
        if k in pack and pack.get(k) is not None:
            out[k] = _f(pack.get(k))
        elif overwrite:
            out.pop(k, None)
    elapsed = tau_elapsed_min_from_open(hm)
    if elapsed is not None and (overwrite or out.get("tau_elapsed_min") is None):
        out["tau_elapsed_min"] = elapsed
    return out


def collect_path_day_sample(
    *,
    code: str,
    day_bar: dict,
    hist_bars: Sequence[dict],
    minute_bars: Sequence[dict],
    tau_row: Optional[dict] = None,
    sell_trig_pct: float = 2.0,
    buy_trig_pct: float = 1.5,
    minute_tau_hm: Any = None,
) -> Optional[Dict[str, Any]]:
    """单日：开盘 Z + 早盘前缀分钟小包 + 全日极值时间序标签。"""
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
    ref_src = "minute_open" if minute_open is not None and minute_open > 0 else "daily_open"
    ref_mismatch_pct = None
    if daily_open and daily_open > 0 and minute_open and minute_open > 0:
        ref_mismatch_pct = round((float(minute_open) / float(daily_open) - 1.0) * 100.0, 4)
    hist = hist_bars_pit(hist_bars, asof_date=str(day_bar.get("date") or "")[:10])
    prev = hist[-1] if hist else None
    base = dict(tau_row or {})
    feats = path_features_from_open_row(base, hist=hist, prev_bar=prev)
    if feats.get("gap_pct") is None:
        pc = _f(day_bar.get("prev_close"))
        if pc is None and prev:
            pc = _f(prev.get("close"))
        # gap 仍用日线 open（与 τ/截面同源）；触发锚点可与 gap 解耦
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
    label, reason = extreme_order_path_label(
        minute_bars,
        ref=ref,
    )
    if label == 0.0:
        return None
    day_hi, day_lo = _minute_day_extremes(minute_bars)
    label_range = (
        round(float(day_hi) - float(day_lo), 6)
        if day_hi is not None and day_lo is not None
        else None
    )
    return {
        "code": str(code or "").strip(),
        "date": dkey,
        "features": feats,
        "label": label,
        "label_reason": reason,
        "label_range": label_range,
        "ref": float(ref),
        "ref_src": ref_src,
        "ref_mismatch_pct": ref_mismatch_pct,
        "minute_tau_hm": _normalize_path_tau_hm(hm),
    }


def build_path_panels_from_bars(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    minute_by_code_date: Optional[Dict[str, Dict[str, Sequence[dict]]]] = None,
    sell_trig_pct: float = 2.0,
    buy_trig_pct: float = 1.5,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    minute_tau_hm: Any = None,
    tau_grid: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """批量：每票日线 + 可选 ``{code: {date: minute_bars}}``。

    特征与 ŷ_τ 对齐（开盘 Z + ≤τ 分钟小包）；标签仍用全日极值序。
    ``tau_grid`` 非空：同日多 τ 各一行、标签相同、共享 β（与 τ 头变长前缀同款）。
    ``tau_grid is None``：单点 ``minute_tau_hm``（默认 10:30）。
    """
    minute_map = minute_by_code_date if isinstance(minute_by_code_date, dict) else {}
    if tau_grid is not None:
        clocks = normalize_minute_tau_grid(
            tau_hm=minute_tau_hm or DEFAULT_PATH_MINUTE_TAU_HM,
            tau_grid=tau_grid,
        )
    else:
        clocks = [
            _normalize_path_tau_hm(
                minute_tau_hm if minute_tau_hm is not None else DEFAULT_PATH_MINUTE_TAU_HM
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
        tau_xs, _tau_ys, tau_dates, tau_metas = collect_tau_open_panel(
            bars,
            min_history=min_history,
            stock_code=code,
        )
        tau_by_date: Dict[str, dict] = {}
        for i, dkey in enumerate(tau_dates):
            if i < len(tau_xs) and isinstance(tau_xs[i], dict):
                tau_by_date[str(dkey)[:10]] = dict(tau_xs[i])
        code_mins = minute_map.get(code) if isinstance(minute_map.get(code), dict) else {}
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
                sample = collect_path_day_sample(
                    code=code,
                    day_bar=day,
                    hist_bars=hist,
                    minute_bars=mins,
                    tau_row=tau_by_date.get(dkey),
                    sell_trig_pct=sell_trig_pct,
                    buy_trig_pct=buy_trig_pct,
                    minute_tau_hm=hm,
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
                        "ref_src": sample.get("ref_src"),
                        "ref_mismatch_pct": sample.get("ref_mismatch_pct"),
                        "ret_open_to_tau": sample["features"].get("ret_open_to_tau"),
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
    # 与 τ/on 一致：全池一次截面广度，勿逐票 attach（否则 breadth 恒 0/1）
    return attach_cross_section_breadth(enriched, gap_trigger_pct=gap_trigger_pct)
