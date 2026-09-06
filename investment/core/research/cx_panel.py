"""ŷ_complexity 训练面板：开盘 Z + 早盘前缀分钟小包 → 全日 5m 路径曲折度。

标签（Kaufman 效率比的补，有界、无量纲）：

    D = |C_last − C_first|     全日 5m 收价首末位移
    L = Σ |ΔC|                 相邻 5m 路径长（午休/停牌跳空不计入）
    y_complexity = 1 − min(1, D/L)     ∈ [0, 1]

0 = 直线（曲折度最低）；1 = 最折。与波动率不同：单边趋势可以振幅大但 y_complexity 低。

无未来函数：特征 = 开盘 Z + ≤τ 分钟前缀 + 历史真实曲折度
（complexity_lag1 / complexity_ma5，不含当日）以及 TPD 滞后
（tpd_lag1 / tpd_ma5；旧键 complexity_tpd_lag* 仍可读）。
ŷ_complexity 与 ŷ_tpd 共用开盘 Z、路径小包与滞后，只换标签与前缀形状键
（prefix_complexity vs prefix_tpd）。
研究枢纽拟合；做 T 入场用盘中前缀 ŷ_complexity，ŷ_complexity > y_complexity_max 则跳过
（不用全日 realized 标签）。旧键 y_cx / cx_lag1 仍可读。
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
from core.signal.minute_tau_feats import MINUTE_TAU_CX_SHAPE_KEYS, MINUTE_TAU_TPD_SHAPE_KEYS

CX_LAG_WINDOW = 5
# cx_L_lag1 / cx_am_lag1 曾入模，做 T 回测变差后撤回
CX_LAG_FEATURES = ("complexity_lag1", "complexity_ma5")
CX_LAG_FEAT_LABELS = {
    "complexity_lag1": "昨真实曲折度",
    "complexity_ma5": "近5日真实曲折度均",
    "cx_lag1": "昨真实曲折度",
    "cx_ma5": "近5日真实曲折度均",
}
CX_LAG_KEY_ALIASES = (
    ("complexity_lag1", "cx_lag1"),
    ("complexity_ma5", "cx_ma5"),
)
# TPD 滞后（两头共用键）：ŷ_complexity 当形状因子，ŷ_tpd 当本头自回归
TPD_LAG_FEATURES = ("tpd_lag1", "tpd_ma5")
CX_TPD_LAG_FEATURES = TPD_LAG_FEATURES
TPD_LAG_FEAT_LABELS = {
    "tpd_lag1": "昨真实反转密度",
    "tpd_ma5": "近5日真实反转密度均",
    "complexity_tpd_lag1": "昨真实反转密度",
    "complexity_tpd_ma5": "近5日真实反转密度均",
    "cx_tpd_lag1": "昨真实反转密度",
    "cx_tpd_ma5": "近5日真实反转密度均",
}
CX_TPD_LAG_FEAT_LABELS = TPD_LAG_FEAT_LABELS
TPD_LAG_KEY_GROUPS = (
    ("tpd_lag1", "complexity_tpd_lag1", "cx_tpd_lag1"),
    ("tpd_ma5", "complexity_tpd_ma5", "cx_tpd_ma5"),
)
CX_TPD_LAG_KEY_ALIASES = (
    ("complexity_tpd_lag1", "cx_tpd_lag1"),
    ("complexity_tpd_ma5", "cx_tpd_ma5"),
)
Y_COMPLEXITY_HAT_KEYS = (
    "predicted_score_complexity",
    "y_complexity_hat",
    "predicted_score_cx",
    "y_cx_hat",
)
Y_COMPLEXITY_LABEL_KEYS = (
    "y_complexity",
    "complexity_realized",
    "y_cx",
    "cx_realized",
)
Y_TPD_HAT_KEYS = (
    "predicted_score_tpd",
    "y_tpd_hat",
)
Y_TPD_LABEL_KEYS = (
    "y_tpd",
    "tpd_realized",
    "y_complexity_tpd",
    "complexity_tpd_realized",
    "cx_tpd_realized",
)
CX_Z_FEATURES = PATH_Z_FEATURES + CX_LAG_FEATURES + TPD_LAG_FEATURES + MINUTE_TAU_CX_SHAPE_KEYS
TPD_Z_FEATURES = PATH_Z_FEATURES + CX_LAG_FEATURES + TPD_LAG_FEATURES + MINUTE_TAU_TPD_SHAPE_KEYS
# 研究枢纽因子表 / feat_labels 只用这四键；cx_lag* / complexity_tpd_lag* 仍可读
CANON_LAG_FEAT_LABELS = {
    "complexity_lag1": CX_LAG_FEAT_LABELS["complexity_lag1"],
    "complexity_ma5": CX_LAG_FEAT_LABELS["complexity_ma5"],
    "tpd_lag1": TPD_LAG_FEAT_LABELS["tpd_lag1"],
    "tpd_ma5": TPD_LAG_FEAT_LABELS["tpd_ma5"],
}
DEFAULT_CX_MINUTE_TAU_HM = DEFAULT_PATH_MINUTE_TAU_HM
DEFAULT_CX_TAU_GRID = DEFAULT_PATH_TAU_GRID
# 相邻 5m 超过此时长视为会话断开（午休约 90m），不把跳空算进路径长
CX_MAX_STEP_MIN = 20
CX_MIN_BARS = 6
# 昨早盘曲折度：对齐做 T 扫描窗 09:30–11:00（含 11:00）
CX_AM_FIRST_MIN = 9 * 60 + 30
CX_AM_LAST_MIN = 11 * 60
# live：code → {date: y_complexity}
_CX_REALIZED_BY_CODE: Dict[str, Dict[str, float]] = {}
_CX_TPD_REALIZED_BY_CODE: Dict[str, Dict[str, float]] = {}


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


def tpd_from_closes(
    closes: Sequence[float],
    times_min: Optional[Sequence[int]] = None,
    max_step_min: int = CX_MAX_STEP_MIN,
) -> Optional[float]:
    """转折点密度 = 方向反转次数 / 有效内点数 ∈ [0,1]。

    0 = 完美直线（无反转）；1 = 每根都反转（最大锯齿）。
    与 1−D/L 正交：1−D/L 管路径浪费比，TPD 管反转频次。
    若提供 ``times_min``，跳过 dt > max_step_min 的会话断点（午休），只在连续 5m 段内数转折。
    """
    n = len(closes)
    if n < 3:
        return None
    times = list(times_min) if times_min is not None else None
    if times is None or len(times) != n:
        turns = 0
        for i in range(1, n - 1):
            d1 = closes[i] - closes[i - 1]
            d2 = closes[i + 1] - closes[i]
            if d1 != 0 and d2 != 0 and d1 * d2 < 0:
                turns += 1
        return round(turns / float(n - 2), 6)

    cap = max(5, int(max_step_min))
    segments: List[List[float]] = []
    cur: List[float] = [float(closes[0])]
    for i in range(1, n):
        dt = int(times[i]) - int(times[i - 1])
        if dt <= 0:
            continue
        if dt > cap:
            if len(cur) >= 3:
                segments.append(cur)
            cur = [float(closes[i])]
        else:
            cur.append(float(closes[i]))
    if len(cur) >= 3:
        segments.append(cur)
    turns = 0
    denom = 0
    for seg in segments:
        if len(seg) < 3:
            continue
        denom += len(seg) - 2
        for i in range(1, len(seg) - 1):
            d1 = seg[i] - seg[i - 1]
            d2 = seg[i + 1] - seg[i]
            if d1 != 0 and d2 != 0 and d1 * d2 < 0:
                turns += 1
    if denom <= 0:
        return None
    return round(turns / float(denom), 6)


def _mirror_complexity_lag_aliases(out: Dict[str, Any]) -> None:
    """新键 complexity_lag* 与旧键 cx_lag* 互相同步，兼容已 promote 模型。"""
    for new_k, old_k in CX_LAG_KEY_ALIASES:
        if new_k in out and old_k not in out:
            out[old_k] = out[new_k]
        elif old_k in out and new_k not in out:
            out[new_k] = out[old_k]


def _mirror_tpd_lag_aliases(out: Dict[str, Any]) -> None:
    """tpd_lag*、complexity_tpd_lag*、cx_tpd_lag* 互相同步。"""
    for group in TPD_LAG_KEY_GROUPS:
        val = None
        for k in group:
            if k in out:
                val = out[k]
                break
        if val is None and not any(k in out for k in group):
            continue
        for k in group:
            if k not in out:
                out[k] = val


def resolve_feat_value(features: Optional[dict], name: str) -> Optional[Any]:
    """读特征，兼容 complexity / tpd / cx 滞后别名。"""
    row = features if isinstance(features, dict) else {}
    v = row.get(name)
    if v is not None:
        return v
    for group in TPD_LAG_KEY_GROUPS:
        if name not in group:
            continue
        for alt in group:
            v = row.get(alt)
            if v is not None:
                return v
        return None
    for new_k, old_k in CX_LAG_KEY_ALIASES:
        if name == new_k:
            v = row.get(old_k)
            if v is not None:
                return v
        elif name == old_k:
            v = row.get(new_k)
            if v is not None:
                return v
    return None


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


def pick_y_complexity_hat(*objs: Any) -> Optional[float]:
    """盘中 ŷ_complexity；兼容 predicted_score_cx / y_cx_hat。"""
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        for k in Y_COMPLEXITY_HAT_KEYS:
            v = cx_as_unit_01(obj.get(k))
            if v is not None:
                return v
    return None


def pick_y_complexity_label(*objs: Any) -> Optional[float]:
    """全日曲折度标签；兼容 y_cx / cx_realized。"""
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        for k in Y_COMPLEXITY_LABEL_KEYS:
            v = _f(obj.get(k))
            if v is not None:
                return v
    return None


def write_y_complexity_hat(dest: Dict[str, Any], val: float) -> None:
    x = float(val)
    dest["predicted_score_complexity"] = x
    dest["y_complexity_hat"] = x
    dest["predicted_score_cx"] = x
    dest["y_cx_hat"] = x


def write_y_complexity_label(
    dest: Dict[str, Any],
    val: float,
    reason: str = "ok",
) -> None:
    x = float(val)
    dest["y_complexity"] = x
    dest["complexity_realized"] = x
    dest["complexity_realized_reason"] = str(reason)
    dest["y_cx"] = x
    dest["cx_realized"] = x
    dest["cx_realized_reason"] = str(reason)


def pack_y_complexity_fields(day: Optional[dict]) -> Dict[str, Any]:
    """全日曲折度标签写成新/旧双键（0.0 直线有效，不用 or）。"""
    val = pick_y_complexity_label(day) if isinstance(day, dict) else None
    reason = None
    if isinstance(day, dict):
        if day.get("complexity_realized_reason") is not None:
            reason = day.get("complexity_realized_reason")
        else:
            reason = day.get("cx_realized_reason")
    return {
        "y_complexity": val,
        "y_cx": val,
        "complexity_realized": val,
        "cx_realized": val,
        "complexity_realized_reason": reason,
        "cx_realized_reason": reason,
    }


def tpd_as_unit_01(v: Any) -> Optional[float]:
    """把 TPD 钳到 [0, 1]；0.0 无反转有效。"""
    x = _f(v)
    if x is None:
        return None
    return max(0.0, min(round(x, 6), 1.0))


def pick_y_tpd_hat(*objs: Any) -> Optional[float]:
    """盘中 ŷ_tpd。"""
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        for k in Y_TPD_HAT_KEYS:
            v = tpd_as_unit_01(obj.get(k))
            if v is not None:
                return v
    return None


def pick_y_tpd_label(*objs: Any) -> Optional[float]:
    """全日 TPD 标签；兼容 y_complexity_tpd。"""
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        for k in Y_TPD_LABEL_KEYS:
            v = _f(obj.get(k))
            if v is not None:
                return v
    return None


def write_y_tpd_hat(dest: Dict[str, Any], val: float) -> None:
    x = float(val)
    dest["predicted_score_tpd"] = x
    dest["y_tpd_hat"] = x


def write_y_tpd_label(dest: Dict[str, Any], val: float) -> None:
    x = float(val)
    dest["y_tpd"] = x
    dest["tpd_realized"] = x
    dest["y_complexity_tpd"] = x
    dest["complexity_tpd_realized"] = x
    dest["cx_tpd_realized"] = x


def pack_y_tpd_fields(day: Optional[dict]) -> Dict[str, Any]:
    """全日 TPD 标签（0.0 无反转有效，不用 or）。"""
    val = pick_y_tpd_label(day) if isinstance(day, dict) else None
    return {
        "y_tpd": val,
        "tpd_realized": val,
        "y_complexity_tpd": val,
        "complexity_tpd_realized": val,
        "cx_tpd_realized": val,
    }


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
    """全日 5m 收价路径曲折度 1−D/L ∈ [0,1]（y_complexity）；直线≈0。"""
    pts = _ordered_closes(minute_bars)
    meta: Dict[str, Any] = {
        "n_bars": len(pts),
        "n_steps": 0,
        "n_skipped_gaps": 0,
        "path_len": None,
        "displacement": None,
        "efficiency": None,
    }
    if len(pts) < 3:
        return None, "too_few_bars", meta
    # TPD：转折点密度（与 1-D/L 正交，补中间形状信息）；午休跳空不计
    meta["tpd"] = tpd_from_closes(
        [p[1] for p in pts],
        times_min=[p[0] for p in pts],
        max_step_min=max_step_min,
    )
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
    first_c = pts[0][1] if pts else None
    if first_c is not None and first_c > 0:
        meta["path_len_pct"] = round(length / float(first_c) * 100.0, 6)
    if n_steps < 2:
        return None, "too_few_steps", meta
    if length <= 1e-12:
        meta["efficiency"] = 1.0
        return 0.0, "flat_path", meta
    er = min(1.0, disp / length)
    meta["efficiency"] = round(er, 6)
    y = 1.0 - er
    return round(y, 6), "ok", meta


def am_session_minute_bars(minute_bars: Sequence[dict]) -> List[dict]:
    """截取 09:30–11:00（含）5m，对齐做 T 扫描窗。"""
    out: List[dict] = []
    for bar in minute_bars or []:
        if not isinstance(bar, dict):
            continue
        t = _bar_hm_minutes(bar)
        if t is None:
            continue
        if CX_AM_FIRST_MIN <= t <= CX_AM_LAST_MIN:
            out.append(bar)
    return out


def realized_cx_stats_by_date(
    minute_by_date: Optional[Dict[str, Sequence[dict]]],
) -> Dict[str, Dict[str, float]]:
    """各日 {y, L_pct, y_am, tpd}；缺的键不进表。"""
    y_map: Dict[str, float] = {}
    len_map: Dict[str, float] = {}
    am_map: Dict[str, float] = {}
    tpd_map: Dict[str, float] = {}
    for dkey, bars in (minute_by_date or {}).items():
        day = str(dkey or "")[:10]
        if len(day) < 10:
            continue
        y, _reason, meta = cx_complexity_label(bars)
        if y is not None:
            y_map[day] = float(y)
        tpd = meta.get("tpd") if isinstance(meta, dict) else None
        if tpd is not None:
            tpd_map[day] = float(tpd)
        lp = meta.get("path_len_pct") if isinstance(meta, dict) else None
        if lp is None and isinstance(meta, dict) and meta.get("path_len") is not None:
            pts = _ordered_closes(bars)
            first_c = pts[0][1] if pts else None
            if first_c is not None and first_c > 0:
                lp = float(meta["path_len"]) / float(first_c) * 100.0
        if lp is not None:
            try:
                len_map[day] = round(float(lp), 6)
            except (TypeError, ValueError):
                pass
        y_am, _am_reason, _am_meta = cx_complexity_label(am_session_minute_bars(bars))
        if y_am is not None:
            am_map[day] = float(y_am)
    return {"y": y_map, "L": len_map, "am": am_map, "tpd": tpd_map}


def realized_cx_by_date(
    minute_by_date: Optional[Dict[str, Sequence[dict]]],
) -> Dict[str, float]:
    """各日全日 5m 真实 y_complexity；算不出的日不进表。"""
    return dict(realized_cx_stats_by_date(minute_by_date).get("y") or {})


def cx_lag_features(
    *,
    hist_bars: Sequence[dict],
    cx_by_date: Dict[str, float],
    asof_date: str,
    window: int = CX_LAG_WINDOW,
    len_by_date: Optional[Dict[str, float]] = None,
    am_by_date: Optional[Dict[str, float]] = None,
) -> Dict[str, Optional[float]]:
    """PIT：只用 asof 之前、且有 5m 真实曲折度的交易日；最多 ``window`` 天。"""
    _ = (len_by_date, am_by_date)
    out = label_lag_features(
        hist_bars=hist_bars,
        by_date=cx_by_date,
        asof_date=asof_date,
        window=window,
        lag1_key="complexity_lag1",
        ma_key="complexity_ma5",
    )
    _mirror_complexity_lag_aliases(out)
    return out


def _load_realized_cx_maps_for_code(
    stock_code: str,
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """加载并缓存 code 的 {date: y_complexity} 和 {date: tpd}。"""
    code = str(stock_code or "").strip()
    if not code:
        return {}, {}
    cx_hit = _CX_REALIZED_BY_CODE.get(code)
    tpd_hit = _CX_TPD_REALIZED_BY_CODE.get(code)
    if cx_hit is not None and tpd_hit is not None:
        return cx_hit, tpd_hit
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
    stats = realized_cx_stats_by_date(by_date)
    cx_map = dict(stats.get("y") or {})
    tpd_map = dict(stats.get("tpd") or {})
    _CX_REALIZED_BY_CODE[code] = cx_map
    _CX_TPD_REALIZED_BY_CODE[code] = tpd_map
    return cx_map, tpd_map


def _load_realized_cx_map_for_code(stock_code: str) -> Dict[str, float]:
    """向后兼容：只返回 y_complexity map。"""
    return _load_realized_cx_maps_for_code(stock_code)[0]


def cx_tpd_lag_features(
    *,
    hist_bars: Sequence[dict],
    tpd_by_date: Dict[str, float],
    asof_date: str,
    window: int = CX_LAG_WINDOW,
) -> Dict[str, Optional[float]]:
    """PIT：只用 asof 之前、且有真实 TPD 的交易日；最多 ``window`` 天。"""
    out = label_lag_features(
        hist_bars=hist_bars,
        by_date=tpd_by_date,
        asof_date=asof_date,
        window=window,
        lag1_key="tpd_lag1",
        ma_key="tpd_ma5",
    )
    _mirror_tpd_lag_aliases(out)
    return out


def attach_cx_lag_features(
    feats: Optional[dict],
    *,
    hist_bars: Sequence[dict],
    asof_date: str,
    minute_by_date: Optional[Dict[str, Sequence[dict]]] = None,
    stock_code: str = "",
) -> Dict[str, Any]:
    """把 complexity_lag1/ma5 + tpd_lag1/ma5（及 complexity_tpd_* 别名）写入特征行；缺历史分钟则留空。"""
    out: Dict[str, Any] = dict(feats or {})
    hist = hist_bars_pit(hist_bars, asof_date=asof_date)
    if isinstance(minute_by_date, dict) and minute_by_date:
        stats = realized_cx_stats_by_date(minute_by_date)
        cx_map = dict(stats.get("y") or {})
        tpd_map = dict(stats.get("tpd") or {})
    else:
        cx_map, tpd_map = _load_realized_cx_maps_for_code(stock_code)
    # 几何曲折度 lag
    lags = cx_lag_features(
        hist_bars=hist,
        cx_by_date=cx_map,
        asof_date=asof_date,
    )
    for k in CX_LAG_FEATURES:
        out[k] = lags.get(k)
    _mirror_complexity_lag_aliases(out)
    # 转折点密度 lag
    tpd_lags = cx_tpd_lag_features(
        hist_bars=hist,
        tpd_by_date=tpd_map,
        asof_date=asof_date,
    )
    for k in TPD_LAG_FEATURES:
        out[k] = tpd_lags.get(k)
    _mirror_tpd_lag_aliases(out)
    return out


def attach_cx_realized(
    day: Optional[dict],
    minute_bars: Sequence[dict],
) -> Dict[str, Any]:
    """把全日 5m 曲折度 / TPD 标签写入日结果（对照用，不进做 T 闸）。"""
    out: Dict[str, Any] = dict(day or {})
    if not minute_bars:
        return out
    y, reason, meta = cx_complexity_label(minute_bars)
    tpd = meta.get("tpd") if isinstance(meta, dict) else None
    if y is None and tpd is None:
        return out
    scores = dict(out.get("scores") or {}) if isinstance(out.get("scores"), dict) else {}
    feats = (
        dict(out.get("direction_features") or {})
        if isinstance(out.get("direction_features"), dict)
        else {}
    )
    if y is not None:
        val = float(y)
        write_y_complexity_label(out, val, str(reason))
        write_y_complexity_label(scores, val, str(reason))
        write_y_complexity_label(feats, val, str(reason))
    if tpd is not None:
        write_y_tpd_label(out, float(tpd))
        write_y_tpd_label(scores, float(tpd))
        write_y_tpd_label(feats, float(tpd))
    if meta.get("efficiency") is not None:
        out["cx_efficiency"] = meta.get("efficiency")
    if meta.get("path_len") is not None:
        out["cx_path_len"] = meta.get("path_len")
    if meta.get("displacement") is not None:
        out["cx_displacement"] = meta.get("displacement")
    out["scores"] = scores
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
    tpd_by_date: Optional[Dict[str, float]] = None,
) -> Optional[Dict[str, Any]]:
    """单日：开盘 Z + 早盘前缀分钟小包 + 历史真实 cx/TPD + 全日曲折度 / TPD 标签。"""
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
    for k in CX_LAG_FEATURES:
        feats[k] = lags.get(k)
    _mirror_complexity_lag_aliases(feats)
    tpd_lags = cx_tpd_lag_features(
        hist_bars=hist,
        tpd_by_date=tpd_by_date if isinstance(tpd_by_date, dict) else {},
        asof_date=dkey,
    )
    for k in TPD_LAG_FEATURES:
        feats[k] = tpd_lags.get(k)
    _mirror_tpd_lag_aliases(feats)
    label, reason, lab_meta = cx_complexity_label(minute_bars)
    tpd = lab_meta.get("tpd") if isinstance(lab_meta, dict) else None
    if label is None and tpd is None:
        return None
    return {
        "code": str(code or "").strip(),
        "date": dkey,
        "features": feats,
        "label": float(label) if label is not None else None,
        "tpd_label": float(tpd) if tpd is not None else None,
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
    label: str = "complexity",
) -> List[Dict[str, Any]]:
    """批量：特征与 ŷ_τ / ŷ_path 对齐。

    ``label="complexity"``：y=全日 5m 曲折度 1−D/L。
    ``label="tpd"``：y=全日转折点密度（午休跳空不计）。
    ``tau_grid`` 非空：同日多 τ 各一行、标签相同、共享 β。
    """
    kind = str(label or "complexity").strip().lower()
    if kind not in ("complexity", "tpd"):
        kind = "complexity"
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
        _cx_stats = realized_cx_stats_by_date(code_mins)
        cx_by_date = dict(_cx_stats.get("y") or {})
        cx_tpd_by_date = dict(_cx_stats.get("tpd") or {})
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
                    tpd_by_date=cx_tpd_by_date,
                )
                if not sample:
                    continue
                y_val = sample.get("tpd_label") if kind == "tpd" else sample.get("label")
                if y_val is None:
                    continue
                xs_out.append(sample["features"])
                ys_out.append(float(y_val))
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
                        "y_complexity": sample.get("label"),
                        "y_tpd": sample.get("tpd_label"),
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
