"""做 T 回测可视化数据聚合（供 Web 图表）。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence

from core.signal.yhat_windows import pack_y_tc_fields

SKIP_CAT_LABELS: Dict[str, str] = {
    "missing_minute": "缺分钟",
    "missing_scores": "缺ŷ",
    "y_path_missing": "缺y_path",
    "y_eod_flat": "y_oo未过门槛",
    "y_tau_flat": "y_τ横盘",
    "y_tc_flat": "ŷ_τc未过入场",
    "r_tau_flat": "R̂_τ超额不足",
    "y_tau_weak": "y_τ弱信号",
    "y_path_flat": "y_hl横盘",
    "y_path_disagree": "y_τ↔y_hl异号",
    "y_tc_disagree": "ŷ_τc旁路逆带",
    "y_t30_disagree": "ŷ_τ30旁路逆带",
    "y_t30_flat": "ŷ_τ30横盘",
    "y_t45_disagree": "ŷ_τ45旁路逆带",
    "y_t45_flat": "ŷ_τ45横盘",
    "y_tw_disagree": "ŷ_τw入场逆带",
    "y_tw_flat": "ŷ_τw未过入场",
    "bar_shape": "收在极值未开腿",
    "bar_oc": "K线阴阳逆选向(旧)",
    "ytw_prefix": "前序ŷ_τw未达标",
    "y_t60_disagree": "ŷ_τ60旁路逆带",
    "y_t60_flat": "ŷ_τ60横盘",
    "y_t75_disagree": "ŷ_τ75旁路逆带",
    "y_t75_flat": "ŷ_τ75横盘",
    "y_t90_disagree": "ŷ_τ90旁路逆带",
    "y_t90_flat": "ŷ_τ90横盘",
    "y_complexity_high": "y_cx太折",
    "y_cx_high": "y_cx太折",
    "y_tpd_high": "y_tpd反转过密",
    "gap_tier_skip": "大缺口反向跳过",
    "path_abandon": "前缀无空间放弃",
    "prefix_vs_path": "前缀振幅超路径",
    "multi_slot_miss": "多轮均未成交",
    "tau_entry_price": "入场价vs开盘×ŷ_τ(旧)",
    "tau_exit_price": "出场价vs开盘×ŷ_τ",
    "y_trade_weak": "y_trade幅度不足",
    "eod_tau_disagree": "y_oo↔y_τ异号",
    "trade_tau_disagree": "y_trade↔y_τ异号",
    "trade_tau_sign": "异号跳过",
    "tau_leg1_prior": "局部↔整体趋势不一致",
    "conflict": "旧冲突(已下线)",
    "amplitude": "振幅不足(旧)",
    "directional_amplitude": "方向振幅(旧)",
    "lot_size": "手数不足",
    "cash": "现金不足",
    "tplus1": "T+1无可卖",
    "path": "路径否决",
    "trigger_miss": "未触达",
    "intraday_legs_open": "盘中已落账",
    "price_space_mismatch": "日分价空间错位",
    "other": "其它",
}

# 研究仪表盘语义色：同族近饱和、异族可辨；忌荧光粉/柠檬黄
SKIP_CAT_COLORS: Dict[str, str] = {
    # 缺数 / 中性石板
    "missing_minute": "#5c6b7a",
    "missing_scores": "#8b98a5",
    "y_path_missing": "#44525f",
    "price_space_mismatch": "#6a7380",
    "bar_shape": "#7a8478",
    "bar_oc": "#6e7a58",
    "ytw_prefix": "#2f6e72",
    "trigger_miss": "#b8c0c8",
    "other": "#cbd2d9",
    "amplitude": "#6e7378",
    "directional_amplitude": "#9aa0a6",
    # 门槛不足 · 冷钢蓝 / 青灰
    "y_eod_flat": "#3d6a8a",
    "y_tau_flat": "#3a7a72",
    "y_tc_flat": "#3d6e7a",
    "r_tau_flat": "#4a6e7a",
    "y_tau_weak": "#5a7d8c",
    "y_path_flat": "#7a6a55",
    "y_complexity_high": "#6b4c8a",
    "y_cx_high": "#6b4c8a",
    "y_tpd_high": "#0891b2",
    # 异号 / 冲突 · 克制酒红 / 梅紫
    "eod_tau_disagree": "#b33a3a",
    "trade_tau_disagree": "#8f3d5b",
    "trade_tau_sign": "#c45c4a",
    "tau_leg1_prior": "#a0653a",
    "y_path_disagree": "#6b4c7a",
    "y_tc_disagree": "#4c6b8a",
    "y_t30_disagree": "#3d7a8a",
    "y_t30_flat": "#6a8894",
    "y_t45_disagree": "#35748a",
    "y_t45_flat": "#628890",
    "y_tw_disagree": "#2a6e82",
    "y_tw_flat": "#5e7a86",
    "y_t60_disagree": "#2d6a7a",
    "y_t60_flat": "#5a7884",
    "y_t75_disagree": "#256275",
    "y_t75_flat": "#52707c",
    "y_t90_disagree": "#1d5a6a",
    "y_t90_flat": "#4a6874",
    "conflict": "#a04848",
    # 前缀 / 空间 / 缺口 · 海石青 + 一枚赭石
    "path_abandon": "#3f6f68",
    "prefix_vs_path": "#4a8a7e",
    "multi_slot_miss": "#b8c0c8",
    "tau_entry_price": "#2a5f7a",
    "tau_exit_price": "#356b85",
    "gap_tier_skip": "#b07a3a",
    "y_trade_weak": "#9a5b32",
    "path": "#4a5f8a",
    # 约束类 · 灰紫 / 藕色
    "lot_size": "#6b5b8a",
    "cash": "#8a5a6e",
    "tplus1": "#6e5c82",
    "intraday_legs_open": "#4f7a6e",
}


def _reason_is_tau_head(r: str) -> bool:
    """幅度文案是否指 ŷ_τc（含旧 |y_τ| / R̂_τ）。不含 ŷ_τw / ŷ_τ30。"""
    if any(
        k in r
        for k in ("ŷ_τc", "y_τc", "|y_τ|", "|ŷ_τ|", "R̂_τ", "超额不足")
    ):
        return True
    for token in ("y_τ", "ŷ_τ"):
        i = 0
        while True:
            j = r.find(token, i)
            if j < 0:
                break
            nxt = r[j + len(token) : j + len(token) + 1]
            if nxt not in "w0123456789":
                return True
            i = j + len(token)
    return False


def classify_t0_skip_reason(reason: Optional[str]) -> str:
    """跳过文案归类。幅度闸只认 ŷ_τc；旧 |y_τ| / R̂_τ 并入同一类。

    已下线的 path / 旁路 / 阴阳 / 前序 / dual_y 异号闸不再单列，落入 other。
    """
    r = str(reason or "")
    if "price_space_mismatch" in r or ("价空间" in r and ("错位" in r or "不一致" in r)):
        return "price_space_mismatch"
    if "缺" in r and ("分钟" in r or "minute" in r.lower()):
        return "missing_minute"
    if "缺" in r and ("y_" in r or "ŷ_" in r or "快照" in r or "即时算分" in r):
        return "missing_scores"
    if ("ŷ_τw" in r or "y_τw" in r or "y_tw" in r) and (
        "未过" in r or "横盘" in r or "缺失" in r or "弃权" in r or "未开腿" in r
    ):
        return "y_tw_flat"
    flat = "未过入场" in r or "未过门槛" in r or "横盘" in r or "缺失" in r or "弱信号" in r
    if flat and _reason_is_tau_head(r):
        return "y_tc_flat"
    if "τ出场" in r:
        return "tau_exit_price"
    if "多轮均未成交" in r:
        return "multi_slot_miss"
    if "T+1" in r or "可卖旧仓" in r or "无可卖" in r or ("可卖" in r and "锁定" in r):
        return "tplus1"
    if "不足1手" in r or "动仓不足" in r or ("手" in r and "不足" in r):
        return "lot_size"
    if "未破带" in r:
        return "trigger_miss"
    if "未触及" in r or "未触" in r or "未开成第一腿" in r or "未开第一腿" in r:
        return "trigger_miss"
    if "重复落账" in r or "盘中已有成交腿" in r:
        return "intraday_legs_open"
    if "现金" in r or "买不起" in r:
        return "cash"
    if "路径" in r or "veto" in r.lower():
        return "path"
    return "other"


def summarize_skip_reason_label(reason: Optional[str]) -> str:
    """跳过诊断摘要用短标签（合并 |y_τ|=0.009% / 0.016% 等同族）。"""
    r = str(reason or "").strip() or "跳过"
    cat = classify_t0_skip_reason(r)
    if cat == "trigger_miss":
        if "正T" in r:
            return "正T未开第一腿" if ("未开成" in r or "未开第一腿" in r or "确认根" in r) else "正T未触达"
        if "反T" in r:
            return "反T未开第一腿" if "未开" in r else "反T未触卖出"
        return SKIP_CAT_LABELS.get(cat, "未触达")
    if cat == "tplus1":
        return "T+1无可卖"
    return SKIP_CAT_LABELS.get(cat, cat)


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


def _first_present(src: Any, *keys: str) -> Any:
    if not isinstance(src, dict):
        return None
    for key in keys:
        if src.get(key) is not None:
            return src.get(key)
    return None


def extract_scores(day: dict) -> Dict[str, Optional[float]]:
    """画像用分。主字段 y_τc / predicted_score_τc。"""
    feats = day.get("direction_features") if isinstance(day.get("direction_features"), dict) else {}
    raw = day.get("scores") if isinstance(day.get("scores"), dict) else {}
    gap = _f(feats.get("gap_pct"))
    if gap is None and isinstance(feats.get("features_tau"), dict):
        gap = _f(feats["features_tau"].get("gap_pct"))
    if gap is None and isinstance(raw.get("features_tau"), dict):
        gap = _f(raw["features_tau"].get("gap_pct"))

    def _pick(key: str) -> Optional[float]:
        v = _f(feats.get(key))
        if v is None:
            v = _f(raw.get(key))
        if v is None:
            v = _f(day.get(key))
        return v

    def _pick_keys(*keys: str) -> Optional[float]:
        for key in keys:
            v = _pick(key)
            if v is not None:
                return v
        return None

    r_hat = _pick("r_hat")
    if r_hat is None:
        r_hat = _pick("residual")

    return {
        "y_τc": _pick_keys("y_τc", "predicted_score_τc"),
        "y_τ30": _pick_keys("y_τ30", "y_t30", "y_t30_hat", "predicted_score_t30"),
        "y_t30_realized": _pick_keys("y_t30_realized", "t30_realized"),
        "y_τ45": _pick_keys("y_τ45", "y_t45", "y_t45_hat", "predicted_score_t45"),
        "y_t45_realized": _pick_keys("y_t45_realized", "t45_realized"),
        "y_τ60": _pick_keys("y_τ60", "y_t60", "y_t60_hat", "predicted_score_t60"),
        "y_t60_realized": _pick_keys("y_t60_realized", "t60_realized"),
        "y_τ75": _pick_keys("y_τ75", "y_t75", "y_t75_hat", "predicted_score_t75"),
        "y_t75_realized": _pick_keys("y_t75_realized", "t75_realized"),
        "y_τ90": _pick_keys("y_τ90", "y_t90", "y_t90_hat", "predicted_score_t90"),
        "y_t90_realized": _pick_keys("y_t90_realized", "t90_realized"),
        "r_hat": r_hat,
        "y_co": _pick("y_co"),
        "gap_pct": gap,
    }


def _pick_realized(day: dict, key: str) -> Optional[float]:
    feats = day.get("direction_features") if isinstance(day.get("direction_features"), dict) else {}
    scores = day.get("scores") if isinstance(day.get("scores"), dict) else {}
    v = _f(feats.get(key))
    if v is None:
        v = _f(scores.get(key))
    if v is None:
        v = _f(day.get(key))
    return v


def _sign_bucket(v: Optional[float], *, eps: float = 1e-9) -> str:
    if v is None:
        return "missing"
    if abs(float(v)) <= eps:
        return "zero"
    return "pos" if float(v) > 0 else "neg"


def _sign_hit(
    pred: Optional[float],
    real: Optional[float],
    *,
    pred_eps: float = 0.05,
    real_eps: float = 0.05,
) -> Optional[bool]:
    if pred is None or real is None:
        return None
    if abs(float(pred)) < pred_eps or abs(float(real)) < real_eps:
        return None
    return (float(pred) > 0) == (float(real) > 0)


def _horizon_sign_hit(pred: Optional[float], real: Optional[float]) -> Optional[bool]:
    """ŷ_τ*=p_up vs 已实现百分点：P>0.5 对涨。"""
    if pred is None or real is None:
        return None
    return (float(pred) > 0.5) == (float(real) > 0.0)


def _bump(pack: Dict[str, int], key: str) -> None:
    pack[key] = int(pack.get(key) or 0) + 1


def _close_band_scan_rows(day: dict) -> List[dict]:
    scan = day.get("close_band_scan") if isinstance(day, dict) else None
    if not isinstance(scan, list):
        return []
    return [r for r in scan if isinstance(r, dict) and str(r.get("hm") or "").strip()]


def _scan_row_at_hm(day: dict, hm: str) -> Optional[dict]:
    key = str(hm or "").strip()[:5]
    if not key:
        return None
    for row in _close_band_scan_rows(day):
        if str(row.get("hm") or "").strip()[:5] == key:
            return row
    return None


def _r_pct_for_unit(day: dict, unit: Optional[dict] = None) -> Optional[float]:
    """成交点 Y：R̂_τ = Ĉ_τ/price(τ)−1。"""
    hm = ""
    if isinstance(unit, dict):
        hm = str(unit.get("t0_slot_hm") or unit.get("hm") or "")[:5]
    row = _scan_row_at_hm(day, hm) if hm else None
    if row is not None:
        from core.t0.close_band import r_hat_from_c_tau_px

        rp = r_hat_from_c_tau_px(row.get("c_tau") or row.get("c_hat"), row.get("c"))
        if rp is not None:
            return rp
        rp = _f(row.get("r_hat"))
        if rp is not None:
            return rp
    blobs = []
    if isinstance(unit, dict):
        blobs.append(unit.get("close_band"))
    if isinstance(day, dict):
        blobs.append(day.get("close_band"))
    for blob in blobs:
        if isinstance(blob, dict):
            rp = _f(blob.get("r_hat")) or _f(blob.get("residual")) or _f(blob.get("r_pct"))
            if rp is not None:
                return rp
    return None


def _scores_from_scan_row(row: dict) -> Dict[str, Any]:
    """扫描行 → 画像字段（该钟前缀）。只留画像读取的主字段。"""
    sc: Dict[str, Any] = {}
    y_tc = _first_present(row, "y_τc", "predicted_score_τc")
    if y_tc is not None:
        sc["y_τc"] = y_tc
    for canon, keys in (
        ("y_τ30", ("y_τ30", "y_t30", "y_t30_hat", "predicted_score_t30")),
        ("y_τ45", ("y_τ45", "y_t45", "y_t45_hat", "predicted_score_t45")),
        ("y_τ60", ("y_τ60", "y_t60", "y_t60_hat", "predicted_score_t60")),
        ("y_τ75", ("y_τ75", "y_t75", "y_t75_hat", "predicted_score_t75")),
        ("y_τ90", ("y_τ90", "y_t90", "y_t90_hat", "predicted_score_t90")),
        ("y_t30_realized", ("y_t30_realized", "t30_realized")),
        ("y_t45_realized", ("y_t45_realized", "t45_realized")),
        ("y_t60_realized", ("y_t60_realized", "t60_realized")),
        ("y_t75_realized", ("y_t75_realized", "t75_realized")),
        ("y_t90_realized", ("y_t90_realized", "t90_realized")),
    ):
        val = _first_present(row, *keys)
        if val is not None:
            sc[canon] = val
    r_hat = _first_present(row, "r_hat", "residual")
    if r_hat is not None:
        sc["r_hat"] = r_hat
    r_real = row.get("r_realized")
    if r_real is not None:
        sc["r_realized"] = r_real
    return sc


def resolve_y_tc_for_portrait(sc: Dict[str, Optional[float]]) -> Optional[float]:
    """画像 y_τc 命中：ŷ_τc ↔ close[T]/price(τ)−1。"""
    if not isinstance(sc, dict):
        return None
    for key in ("y_τc", "predicted_score_τc"):
        v = _f(sc.get(key))
        if v is not None:
            return v
    return None


def _resolve_horizon_for_portrait(sc: Dict[str, Optional[float]], *keys: str) -> Optional[float]:
    if not isinstance(sc, dict):
        return None
    for key in keys:
        v = _f(sc.get(key))
        if v is not None:
            return v
    return None


def resolve_y_t30_for_portrait(sc: Dict[str, Optional[float]]) -> Optional[float]:
    """ŷ_τ30 ↔ mean(price(τ⊕25/30/35))/price(τ)−1。"""
    return _resolve_horizon_for_portrait(sc, "y_τ30", "predicted_score_t30", "y_t30", "y_t30_hat")


def resolve_y_t45_for_portrait(sc: Dict[str, Optional[float]]) -> Optional[float]:
    """ŷ_τ45 ↔ mean(price(τ⊕40/45/50))/price(τ)−1。"""
    return _resolve_horizon_for_portrait(sc, "y_τ45", "predicted_score_t45", "y_t45", "y_t45_hat")


def resolve_y_t60_for_portrait(sc: Dict[str, Optional[float]]) -> Optional[float]:
    """ŷ_τ60 ↔ mean(price(τ⊕55/60/65))/price(τ)−1。"""
    return _resolve_horizon_for_portrait(sc, "y_τ60", "predicted_score_t60", "y_t60", "y_t60_hat")


def resolve_y_t75_for_portrait(sc: Dict[str, Optional[float]]) -> Optional[float]:
    """ŷ_τ75 ↔ mean(price(τ⊕70/75/80))/price(τ)−1。"""
    return _resolve_horizon_for_portrait(sc, "y_τ75", "predicted_score_t75", "y_t75", "y_t75_hat")


def resolve_y_t90_for_portrait(sc: Dict[str, Optional[float]]) -> Optional[float]:
    """ŷ_τ90 ↔ mean(price(τ⊕85/90/95))/price(τ)−1。"""
    return _resolve_horizon_for_portrait(sc, "y_τ90", "predicted_score_t90", "y_t90", "y_t90_hat")


def _r_hat_from_scan_ctau(day: dict, unit: Optional[dict] = None) -> Optional[float]:
    """R̂_τ = Ĉ_τ/C−1（与成交明细价带同目标）。"""
    from core.t0.close_band import r_hat_from_c_tau_px

    hm = ""
    if isinstance(unit, dict):
        hm = str(unit.get("t0_slot_hm") or unit.get("hm") or "")[:5]
    row = _scan_row_at_hm(day, hm) if hm else None
    if row is None and isinstance(unit, dict):
        row = unit if unit.get("c_tau") is not None or unit.get("c") is not None else None
    if not isinstance(row, dict):
        return None
    return r_hat_from_c_tau_px(row.get("c_tau") or row.get("c_hat"), row.get("c") or row.get("bar_c"))


def resolve_r_hat_for_portrait(day: dict, unit: Optional[dict] = None) -> Optional[float]:
    """画像 R_τ 命中用 R̂_τ。"""
    band = _r_hat_from_scan_ctau(day, unit)
    if band is not None:
        return band
    sc = extract_scores(unit or day)
    v = _f(sc.get("r_hat")) or _f(sc.get("residual"))
    if v is not None:
        return v
    return _r_pct_for_unit(day, unit)


def _remaining_realized_pct(day: dict, unit: Optional[dict] = None) -> Optional[float]:
    """close[T]/price(τ)−1：优先扫描 r_realized，再用该钟 5m 收对日收。"""
    blobs: List[Any] = []
    if isinstance(unit, dict):
        blobs.extend((unit, unit.get("scores"), unit.get("close_band")))
    if isinstance(day, dict):
        blobs.extend((day, day.get("scores"), day.get("close_band")))
    for blob in blobs:
        if not isinstance(blob, dict):
            continue
        v = _f(blob.get("r_realized")) or _f(blob.get("y_r_realized"))
        if v is not None:
            return v
    hm = ""
    if isinstance(unit, dict):
        hm = str(unit.get("t0_slot_hm") or unit.get("hm") or "")[:5]
    row = _scan_row_at_hm(day, hm) if hm else None
    if row is not None:
        v = _f(row.get("r_realized")) or _f(row.get("y_r_realized"))
        if v is not None:
            return v
        bar_c = _f(row.get("c"))
        close = _f(day.get("close")) if isinstance(day, dict) else None
        if bar_c is not None and bar_c > 0 and close is not None:
            return round((float(close) / float(bar_c) - 1.0) * 100.0, 4)
    bar_c = _f(unit.get("bar_c") or unit.get("c")) if isinstance(unit, dict) else None
    close = _f(day.get("close")) if isinstance(day, dict) else None
    if bar_c is not None and bar_c > 0 and close is not None:
        return round((float(close) / float(bar_c) - 1.0) * 100.0, 4)
    return None


def _horizon_realized_pct(
    day: dict,
    unit: Optional[dict],
    *keys: str,
) -> Optional[float]:
    """窗实现收益：先看单位/日快照，再看该钟扫描行。"""
    blobs: List[Any] = []
    if isinstance(unit, dict):
        blobs.extend((unit, unit.get("scores"), unit.get("close_band")))
    if isinstance(day, dict):
        blobs.extend((day, day.get("scores"), day.get("close_band")))
    for blob in blobs:
        if not isinstance(blob, dict):
            continue
        for key in keys:
            v = _f(blob.get(key))
            if v is not None:
                return v
    hm = ""
    if isinstance(unit, dict):
        hm = str(unit.get("t0_slot_hm") or unit.get("hm") or "")[:5]
    row = _scan_row_at_hm(day, hm) if hm else None
    if row is not None:
        for key in keys:
            v = _f(row.get(key))
            if v is not None:
                return v
    return None


def _t30_realized_pct(day: dict, unit: Optional[dict] = None) -> Optional[float]:
    return _horizon_realized_pct(day, unit, "y_t30_realized", "t30_realized")


def _t45_realized_pct(day: dict, unit: Optional[dict] = None) -> Optional[float]:
    return _horizon_realized_pct(day, unit, "y_t45_realized", "t45_realized")


def _t60_realized_pct(day: dict, unit: Optional[dict] = None) -> Optional[float]:
    return _horizon_realized_pct(day, unit, "y_t60_realized", "t60_realized")


def _t75_realized_pct(day: dict, unit: Optional[dict] = None) -> Optional[float]:
    return _horizon_realized_pct(day, unit, "y_t75_realized", "t75_realized")


def _t90_realized_pct(day: dict, unit: Optional[dict] = None) -> Optional[float]:
    return _horizon_realized_pct(day, unit, "y_t90_realized", "t90_realized")


def _bump_sign_hit(
    pack: Dict[str, int],
    pred: Optional[float],
    real: Optional[float],
    *,
    pred_eps: float = 0.05,
    real_eps: float = 0.05,
) -> None:
    h = _sign_hit(pred, real, pred_eps=pred_eps, real_eps=real_eps)
    if h is True:
        _bump(pack, "hit")
    elif h is False:
        _bump(pack, "miss")
    else:
        _bump(pack, "flat")


def _bump_horizon_sign_hit(
    pack: Dict[str, int],
    pred: Optional[float],
    real: Optional[float],
) -> None:
    h = _horizon_sign_hit(pred, real)
    if h is True:
        _bump(pack, "hit")
    elif h is False:
        _bump(pack, "miss")
    else:
        _bump(pack, "flat")


def _slot_fill_by_hm(day: dict) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for r in _slot_result_rows(day):
        hm = str(r.get("hm") or "").strip()[:5]
        if hm and _slot_row_filled(r):
            out[hm] = r
    return out


def _portrait_slot_rows(day: dict) -> List[dict]:
    """分槽样本：优先 ``close_band_scan`` 每根因果 ŷ；成交标记来自破带轮。"""
    scan = _close_band_scan_rows(day)
    fills = _slot_fill_by_hm(day)
    if not scan:
        return _slot_result_rows(day)
    rows: List[dict] = []
    for s in scan:
        hm = str(s.get("hm") or "").strip()[:5]
        if not hm:
            continue
        scores = _scores_from_scan_row(s)
        filled = fills.get(hm)
        extras = {
            "c": s.get("c"),
            "bar_c": s.get("c"),
            "r_hat": _first_present(s, "r_hat", "residual"),
            "r_realized": s.get("r_realized"),
            "y_τc": _first_present(s, "y_τc", "predicted_score_τc"),
            "pick": s.get("pick"),
            "y_tc_agree": s.get("y_tc_agree"),
        }
        for canon, alias in (
            ("y_τ30", "y_t30"),
            ("y_τ45", "y_t45"),
            ("y_τ60", "y_t60"),
            ("y_τ75", "y_t75"),
            ("y_τ90", "y_t90"),
            ("y_t30_realized", "t30_realized"),
            ("y_t45_realized", "t45_realized"),
            ("y_t60_realized", "t60_realized"),
            ("y_t75_realized", "t75_realized"),
            ("y_t90_realized", "t90_realized"),
        ):
            extras[canon] = _first_present(s, canon, alias)
        if filled is not None:
            sc = dict(filled.get("scores") or {}) if isinstance(filled.get("scores"), dict) else {}
            sc.update(scores)
            row = dict(filled)
            row["scores"] = sc
            row["hm"] = hm
            for k, v in extras.items():
                if v is not None and row.get(k) is None:
                    row[k] = v
            rows.append(row)
            continue
        skip_row = {
            "id": None,
            "hm": hm,
            "skipped": True,
            "scores": scores,
            "sold_qty": 0,
            "bought_qty": 0,
        }
        for k, v in extras.items():
            if v is not None:
                skip_row[k] = v
        rows.append(skip_row)
    return rows


def _day_with_scan_portrait(day: dict, *, hm: Optional[str] = None) -> dict:
    """日级预估用固定钟扫描 ŷ（默认 09:35），避免跳过日用 11:00 前缀垫高命中。"""
    from core.t0.config import T0_PORTRAIT_DAY_HM

    scan = _close_band_scan_rows(day)
    if not scan:
        return day
    want = str(hm or T0_PORTRAIT_DAY_HM or "").strip()[:5]
    pick = None
    for r in scan:
        if str(r.get("hm") or "").strip()[:5] == want:
            pick = r
            break
    if pick is None:
        pick = scan[0]
    extra = _scores_from_scan_row(pick)
    if not extra:
        return day
    out = dict(day)
    sc = dict(out.get("scores") or {}) if isinstance(out.get("scores"), dict) else {}
    sc.update(extra)
    out["scores"] = sc
    for key in (
        "y_τc",
        "r_realized",
        "y_τ30",
        "y_τ45",
        "y_τ60",
        "y_τ75",
        "y_τ90",
        "y_t30_realized",
        "y_t45_realized",
        "y_t60_realized",
        "y_t75_realized",
        "y_t90_realized",
    ):
        if extra.get(key) is not None:
            out[key] = extra[key]
    if extra.get("r_hat") is not None:
        out["r_hat"] = extra["r_hat"]
        cb = dict(out.get("close_band") or {}) if isinstance(out.get("close_band"), dict) else {}
        cb["r_hat"] = extra["r_hat"]
        out["close_band"] = cb
    if pick.get("c") is not None:
        out["bar_c"] = pick.get("c")
    return out


def _portrait_clocks(days: Sequence[dict]) -> List[str]:
    from core.t0.config import DEFAULT_T0_SLOT_CLOCKS, T0_PORTRAIT_SLOT_CLOCKS

    clocks: List[str] = []
    seen = set()
    for hm in tuple(T0_PORTRAIT_SLOT_CLOCKS or ()) + tuple(DEFAULT_T0_SLOT_CLOCKS or ()):
        key = str(hm or "").strip()[:5]
        if key and key not in seen:
            clocks.append(key)
            seen.add(key)
    for d in days or []:
        if not isinstance(d, dict):
            continue
        for r in _portrait_slot_rows(d):
            hm = str(r.get("hm") or "").strip()[:5]
            if hm and hm not in seen:
                clocks.append(hm)
                seen.add(hm)
    return clocks


def _slot_as_portrait_unit(day: dict, row: dict) -> dict:
    """槽位画像样本：本钟 ŷ_τc 与窗头，真实收益仍按该钟或全日。"""
    filled = _slot_row_filled(row)
    slot_sc = row.get("scores") if isinstance(row.get("scores"), dict) else {}
    return {
        "date": day.get("date"),
        "open": day.get("open"),
        "close": day.get("close"),
        "prev_close": day.get("prev_close"),
        "tau_realized": day.get("tau_realized"),
        **pack_y_tc_fields(day),
        "r_realized": row.get("r_realized"),
        "y_t30_realized": _first_present(row, "y_t30_realized", "t30_realized"),
        "y_t45_realized": _first_present(row, "y_t45_realized", "t45_realized"),
        "y_t60_realized": _first_present(row, "y_t60_realized", "t60_realized"),
        "y_t75_realized": _first_present(row, "y_t75_realized", "t75_realized"),
        "y_t90_realized": _first_present(row, "y_t90_realized", "t90_realized"),
        "bar_c": row.get("bar_c") if row.get("bar_c") is not None else row.get("c"),
        "c": row.get("c"),
        "r_hat": row.get("r_hat"),
        "scores": dict(slot_sc) if isinstance(slot_sc, dict) else {},
        "close_band": {
            "r_hat": (slot_sc or {}).get("r_hat")
            if isinstance(slot_sc, dict) and slot_sc.get("r_hat") is not None
            else row.get("r_hat"),
        },
        "direction_features": row.get("direction_features")
        if isinstance(row.get("direction_features"), dict)
        else {},
        "direction": row.get("direction") or row.get("pick"),
        "direction_score": (slot_sc or {}).get("y_τc")
        if isinstance(slot_sc, dict)
        else None,
        "skipped": bool(row.get("skipped")) or not filled,
        "signal_skip": bool(row.get("skipped")) and not filled,
        "sold_qty": int(row.get("sold_qty") or 0) if filled else 0,
        "bought_qty": int(row.get("bought_qty") or 0) if filled else 0,
        "pnl": row.get("pnl") if filled else 0,
        "exposure_pnl": row.get("exposure_pnl") if filled else 0,
        "t0_slot": row.get("id"),
        "t0_slot_hm": row.get("hm"),
        "reason": row.get("reason"),
    }


def _slot_placeholder_unit(day: dict, hm: str) -> dict:
    """无该钟明细：天级标签在，该钟 ŷ 空，命中计 flat。"""
    return {
        "date": day.get("date"),
        "open": day.get("open"),
        "close": day.get("close"),
        "prev_close": day.get("prev_close"),
        "tau_realized": day.get("tau_realized"),
        **pack_y_tc_fields(day),
        "scores": {},
        "direction_features": {},
        "direction": None,
        "direction_score": None,
        "skipped": True,
        "signal_skip": False,
        "sold_qty": 0,
        "bought_qty": 0,
        "pnl": 0,
        "exposure_pnl": 0,
        "t0_slot": None,
        "t0_slot_hm": hm,
        "reason": "无槽位明细",
    }


def _portrait_day_eligible(day: dict) -> bool:
    """与日级画像同一入样条件（有 ŷ_τc / 窗头 / 成交 / 跳过）。"""
    if not isinstance(day, dict):
        return False
    sc = extract_scores(day)
    return bool(
        sc.get("y_τc") is not None
        or sc.get("y_τ30") is not None
        or sc.get("y_τ45") is not None
        or sc.get("y_τ60") is not None
        or sc.get("y_τ75") is not None
        or sc.get("y_τ90") is not None
        or _pick_realized(day, "tau_realized") is not None
        or _portrait_slot_rows(day)
        or is_traded_t0_day(day)
        or bool(day.get("skipped"))
    )


def _build_score_portrait_from_units(
    units: Sequence[dict],
    *,
    traded_only: bool = False,
    scope: Optional[str] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """从画像单位累计 ŷ_τc / ŷ_τw / 窗头命中。"""
    from core.t0.close_band import (
        blend_y_tw_from_scores,
        blend_y_tw_realized,
        y_t30_band_agree,
        y_t45_band_agree,
        y_t60_band_agree,
        y_t75_band_agree,
        y_t90_band_agree,
        y_tc_band_agree,
    )

    label_tau = {"pos": 0, "neg": 0, "zero": 0, "missing": 0}
    tau_hit = {"hit": 0, "miss": 0, "flat": 0}
    r_tau_hit = {"hit": 0, "miss": 0, "flat": 0}
    y_tc_hit = {"hit": 0, "miss": 0, "flat": 0}
    y_tc_band = {"hit": 0, "miss": 0, "flat": 0}
    y_t30_hit = {"hit": 0, "miss": 0, "flat": 0}
    y_t30_band = {"hit": 0, "miss": 0, "flat": 0}
    y_t45_hit = {"hit": 0, "miss": 0, "flat": 0}
    y_t45_band = {"hit": 0, "miss": 0, "flat": 0}
    y_t60_hit = {"hit": 0, "miss": 0, "flat": 0}
    y_t60_band = {"hit": 0, "miss": 0, "flat": 0}
    y_t75_hit = {"hit": 0, "miss": 0, "flat": 0}
    y_t75_band = {"hit": 0, "miss": 0, "flat": 0}
    y_t90_hit = {"hit": 0, "miss": 0, "flat": 0}
    y_t90_band = {"hit": 0, "miss": 0, "flat": 0}
    y_tw_hit = {"hit": 0, "miss": 0, "flat": 0}
    n_days = 0
    n_traded = 0
    n_skipped = 0
    n_signal_skip = 0

    for d in units or []:
        if not isinstance(d, dict):
            continue
        traded = is_traded_t0_day(d)
        skipped = bool(d.get("skipped"))
        if traded_only and not traded:
            continue
        sc = extract_scores(d)
        y_tc_p = resolve_y_tc_for_portrait(sc)
        tau_r = _pick_realized(d, "tau_realized")
        if tau_r is None:
            tau_r = _remaining_realized_pct(d, d)
        y_t30_p = resolve_y_t30_for_portrait(sc)
        y_t45_p = resolve_y_t45_for_portrait(sc)
        y_t60_p = resolve_y_t60_for_portrait(sc)
        y_t75_p = resolve_y_t75_for_portrait(sc)
        y_t90_p = resolve_y_t90_for_portrait(sc)
        r_hat_p = resolve_r_hat_for_portrait(d, d)
        rem_r = _remaining_realized_pct(d, d)
        t30_r = _t30_realized_pct(d, d)
        t45_r = _t45_realized_pct(d, d)
        t60_r = _t60_realized_pct(d, d)
        t75_r = _t75_realized_pct(d, d)
        t90_r = _t90_realized_pct(d, d)
        y_tw_p = blend_y_tw_from_scores(sc)
        y_tw_r = blend_y_tw_realized(t30_r, t60_r, t90_r, t45_r, t75_r)
        has_any = (
            y_tc_p is not None
            or y_t30_p is not None
            or y_t45_p is not None
            or y_t60_p is not None
            or y_t75_p is not None
            or y_t90_p is not None
            or y_tw_p is not None
            or r_hat_p is not None
            or tau_r is not None
            or rem_r is not None
            or t30_r is not None
            or t45_r is not None
            or t60_r is not None
            or t75_r is not None
            or t90_r is not None
            or y_tw_r is not None
            or traded
            or skipped
        )
        if not has_any:
            continue

        n_days += 1
        if traded:
            n_traded += 1
        if skipped:
            n_skipped += 1
            if d.get("signal_skip"):
                n_signal_skip += 1

        _bump(label_tau, _sign_bucket(tau_r))
        th = _sign_hit(y_tc_p, tau_r, pred_eps=0.05, real_eps=0.05)
        if th is True:
            _bump(tau_hit, "hit")
        elif th is False:
            _bump(tau_hit, "miss")
        else:
            _bump(tau_hit, "flat")

        _bump_sign_hit(y_tc_hit, y_tc_p, rem_r, pred_eps=0.05, real_eps=0.05)
        _bump_sign_hit(r_tau_hit, r_hat_p, rem_r, pred_eps=0.05, real_eps=0.05)
        _bump_horizon_sign_hit(y_t30_hit, y_t30_p, t30_r)
        _bump_horizon_sign_hit(y_t45_hit, y_t45_p, t45_r)
        _bump_horizon_sign_hit(y_t60_hit, y_t60_p, t60_r)
        _bump_horizon_sign_hit(y_t75_hit, y_t75_p, t75_r)
        _bump_horizon_sign_hit(y_t90_hit, y_t90_p, t90_r)
        _bump_sign_hit(y_tw_hit, y_tw_p, y_tw_r, pred_eps=1e-9, real_eps=1e-9)
        band_ag = y_tc_band_agree(d.get("direction") or d.get("pick"), y_tc_p)
        if band_ag is True:
            _bump(y_tc_band, "hit")
        elif band_ag is False:
            _bump(y_tc_band, "miss")
        else:
            _bump(y_tc_band, "flat")
        t30_ag = y_t30_band_agree(d.get("direction") or d.get("pick"), y_t30_p)
        if t30_ag is True:
            _bump(y_t30_band, "hit")
        elif t30_ag is False:
            _bump(y_t30_band, "miss")
        else:
            _bump(y_t30_band, "flat")
        t45_ag = y_t45_band_agree(d.get("direction") or d.get("pick"), y_t45_p)
        if t45_ag is True:
            _bump(y_t45_band, "hit")
        elif t45_ag is False:
            _bump(y_t45_band, "miss")
        else:
            _bump(y_t45_band, "flat")
        t60_ag = y_t60_band_agree(d.get("direction") or d.get("pick"), y_t60_p)
        if t60_ag is True:
            _bump(y_t60_band, "hit")
        elif t60_ag is False:
            _bump(y_t60_band, "miss")
        else:
            _bump(y_t60_band, "flat")
        t75_ag = y_t75_band_agree(d.get("direction") or d.get("pick"), y_t75_p)
        if t75_ag is True:
            _bump(y_t75_band, "hit")
        elif t75_ag is False:
            _bump(y_t75_band, "miss")
        else:
            _bump(y_t75_band, "flat")
        t90_ag = y_t90_band_agree(d.get("direction") or d.get("pick"), y_t90_p)
        if t90_ag is True:
            _bump(y_t90_band, "hit")
        elif t90_ag is False:
            _bump(y_t90_band, "miss")
        else:
            _bump(y_t90_band, "flat")

    def _rate(hit: int, miss: int) -> Optional[float]:
        denom = hit + miss
        return round(hit / float(denom), 4) if denom else None

    def _share(pos: int, neg: int) -> Optional[float]:
        denom = pos + neg
        return round(pos / float(denom), 4) if denom else None

    def _hit_pack(pack: Dict[str, int]) -> Dict[str, Any]:
        hm = int(pack["hit"]) + int(pack["miss"])
        return {
            **pack,
            "n_judged": hm,
            "hit_rate": _rate(pack["hit"], pack["miss"]),
            "hit_rate_pct": round((_rate(pack["hit"], pack["miss"]) or 0) * 100.0, 2)
            if hm
            else None,
        }

    scope_s = scope or ("traded" if traded_only else "all")
    return {
        "scope": scope_s,
        "n_days": n_days,
        "n_traded": n_traded,
        "n_skipped": n_skipped,
        "n_signal_skip": n_signal_skip,
        "n_traded_compat": n_traded,
        "label_tau": {
            **label_tau,
            "n": sum(label_tau.values()),
            "pos_share": _share(label_tau["pos"], label_tau["neg"]),
        },
        "tau_hit": _hit_pack(tau_hit),
        "r_tau_hit": _hit_pack(r_tau_hit),
        "y_tc_hit": _hit_pack(y_tc_hit),
        "y_tc_band": _hit_pack(y_tc_band),
        "y_t30_hit": _hit_pack(y_t30_hit),
        "y_t30_band": _hit_pack(y_t30_band),
        "y_t45_hit": _hit_pack(y_t45_hit),
        "y_t45_band": _hit_pack(y_t45_band),
        "y_t60_hit": _hit_pack(y_t60_hit),
        "y_t60_band": _hit_pack(y_t60_band),
        "y_t75_hit": _hit_pack(y_t75_hit),
        "y_t75_band": _hit_pack(y_t75_band),
        "y_t90_hit": _hit_pack(y_t90_hit),
        "y_t90_band": _hit_pack(y_t90_band),
        "y_tw_hit": _hit_pack(y_tw_hit),
        "note": note
        or (
            f"scope={scope_s}；"
            "y_τc↔close[T]/price(τ)−1；y_τ30↔mean(price(τ⊕25/30/35))/price(τ)−1；"
            "y_τ45↔mean(price(τ⊕40/45/50))/price(τ)−1；"
            "y_τ60↔mean(price(τ⊕55/60/65))/price(τ)−1；"
            "y_τ75↔mean(price(τ⊕70/75/80))/price(τ)−1；"
            "y_τ90↔mean(price(τ⊕85/90/95))/price(τ)−1；"
            "y_τw↔五窗符号和 vs 真实窗收益符号和；"
            "旁路=破带方向↔ŷ_τc向ĉ回归；τ30旁路=破带方向↔ŷ_τ30后30m同号；"
            "τ45旁路=破带方向↔ŷ_τ45后45m同号；"
            "τ60旁路=破带方向↔ŷ_τ60后60m同号；"
            "τ75旁路=破带方向↔ŷ_τ75后75m同号；"
            "τ90旁路=破带方向↔ŷ_τ90后90m同号；"
            "含跳过日（有分/标签才计入）"
        ),
    }



def build_score_portrait(
    days: Sequence[dict],
    *,
    traded_only: bool = False,
) -> Dict[str, Any]:
    """回测日 ŷ_τc / 窗头命中。

    默认覆盖全部回测日（含跳过）；``traded_only=True`` 仅成交日。
    ŷ_τc 对 close[T]/price(τ)−1。旧画像头只在缺 y_τc 时并入。
    """
    units = [
        _day_with_scan_portrait(d)
        for d in (days or [])
        if isinstance(d, dict)
    ]
    return _build_score_portrait_from_units(units, traded_only=traded_only)


def build_score_portrait_by_slot(days: Sequence[dict]) -> Dict[str, Any]:
    """按做T时钟拆画像：各钟 ŷ_τc / 窗头对同钟或全日标签。

    优先 ``close_band_scan``（11:00 前每根 5m 前缀 ŷ）；无扫描时回退破带轮
    ``t0_slot_results``。样本与日级对齐；缺该钟 ŷ 计 flat。
    τ→close 口径下晚钟 hit 不因开→τ 机械垫高；分钟对照看 IC。
    """
    from collections import OrderedDict

    eligible = [d for d in (days or []) if _portrait_day_eligible(d)]
    by_hm: "OrderedDict[str, List[dict]]" = OrderedDict()
    for hm in _portrait_clocks(eligible):
        by_hm[hm] = []

    for d in eligible:
        rows = _portrait_slot_rows(d)
        by_row_hm = {
            str(r.get("hm") or "").strip()[:5]: r
            for r in rows
            if str(r.get("hm") or "").strip()
        }
        for hm in by_hm:
            row = by_row_hm.get(hm)
            if row is not None:
                by_hm[hm].append(_slot_as_portrait_unit(d, row))
            else:
                by_hm[hm].append(_slot_placeholder_unit(d, hm))

    slots: List[Dict[str, Any]] = []
    for hm in sorted(by_hm.keys()):
        units = by_hm[hm]
        if not units:
            continue
        note = (
            f"槽位 {hm}：本钟扫描 ŷ_τc / ŷ_τw / ŷ_τ30 / ŷ_τ45 / ŷ_τ60 / ŷ_τ75 / ŷ_τ90 ↔ 同钟或全日标签；"
            "样本=与日级同样本；缺该钟ŷ计flat；成交子集=该钟已破带成交"
        )
        all_port = _build_score_portrait_from_units(
            units, traded_only=False, scope=f"slot:{hm}", note=note
        )
        traded_port = _build_score_portrait_from_units(
            units, traded_only=True, scope=f"slot_traded:{hm}", note=note
        )
        n_with_yhat = sum(
            1
            for u in units
            if resolve_r_hat_for_portrait(u, u) is not None
            or resolve_y_tc_for_portrait(extract_scores(u)) is not None
            or resolve_y_t30_for_portrait(extract_scores(u)) is not None
            or resolve_y_t45_for_portrait(extract_scores(u)) is not None
            or resolve_y_t60_for_portrait(extract_scores(u)) is not None
            or resolve_y_t75_for_portrait(extract_scores(u)) is not None
            or resolve_y_t90_for_portrait(extract_scores(u)) is not None
        )
        slots.append(
            {
                "hm": hm,
                **all_port,
                "traded": traded_port,
                "n_with_yhat": n_with_yhat,
            }
        )

    return {
        "slots": slots,
        "n_slots": len(slots),
        "n_days": len(eligible),
        "note": (
            "分槽位画像：各钟 ŷ_τc / ŷ_τw / ŷ_τ30 / ŷ_τ45 / ŷ_τ60 / ŷ_τ75 / ŷ_τ90 对标签；"
            "旁路=该钟破带方向是否与 ŷ_τc 同号；"
            "τ30旁路=破带方向是否与 ŷ_τ30 后 30 交易分钟同号；"
            "τ45旁路=破带方向是否与 ŷ_τ45 后 45 交易分钟同号；"
            "τ60旁路=破带方向是否与 ŷ_τ60 后 60 交易分钟同号；"
            "τ75旁路=破带方向是否与 ŷ_τ75 后 75 交易分钟同号；"
            "τ90旁路=破带方向是否与 ŷ_τ90 后 90 交易分钟同号；"
            "优先 close_band_scan 每根 5m；无扫描才用破带开轮钟；"
            "样本与日级对齐"
        ),
    }


def build_traded_score_portrait(days: Sequence[dict]) -> Dict[str, Any]:
    """兼容旧名：仅成交日画像。"""
    return build_score_portrait(days, traded_only=True)


def build_backtest_score_portrait(days: Sequence[dict]) -> Dict[str, Any]:
    """全回测日画像 + 成交子集对照 + 分槽位。"""
    all_port = build_score_portrait(days, traded_only=False)
    traded_port = build_score_portrait(days, traded_only=True)
    by_slot = build_score_portrait_by_slot(days)
    return {
        **all_port,
        "traded": traded_port,
        "n_traded": traded_port.get("n_traded") or all_port.get("n_traded") or 0,
        "by_slot": by_slot,
    }


def _merge_score_portraits(
    parts: Sequence[Optional[dict]],
) -> Dict[str, Any]:
    """合并多票 score_portrait 计数并重算比率。"""
    empty = build_score_portrait([])
    keys_sign = ("pos", "neg", "zero", "missing")
    keys_hit = ("hit", "miss", "flat")
    label_tau = {k: 0 for k in keys_sign}
    tau_hit = {k: 0 for k in keys_hit}
    r_tau_hit = {k: 0 for k in keys_hit}
    y_tc_hit = {k: 0 for k in keys_hit}
    y_tc_band = {k: 0 for k in keys_hit}
    y_t30_hit = {k: 0 for k in keys_hit}
    y_t30_band = {k: 0 for k in keys_hit}
    y_t45_hit = {k: 0 for k in keys_hit}
    y_t45_band = {k: 0 for k in keys_hit}
    y_t60_hit = {k: 0 for k in keys_hit}
    y_t60_band = {k: 0 for k in keys_hit}
    y_t75_hit = {k: 0 for k in keys_hit}
    y_t75_band = {k: 0 for k in keys_hit}
    y_t90_hit = {k: 0 for k in keys_hit}
    y_t90_band = {k: 0 for k in keys_hit}
    y_tw_hit = {k: 0 for k in keys_hit}
    n_days = n_traded = n_skipped = n_signal_skip = 0
    traded_parts: List[dict] = []
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        n_days += int(part.get("n_days") or part.get("n_traded") or 0)
        n_traded += int(part.get("n_traded") or 0)
        n_skipped += int(part.get("n_skipped") or 0)
        n_signal_skip += int(part.get("n_signal_skip") or 0)
        if isinstance(part.get("traded"), dict):
            traded_parts.append(part["traded"])
        src = part.get("label_tau") if isinstance(part.get("label_tau"), dict) else {}
        for k in keys_sign:
            label_tau[k] += int(src.get(k) or 0)
        for dst, src_key in (
            (tau_hit, "tau_hit"),
            (r_tau_hit, "r_tau_hit"),
            (y_tc_hit, "y_tc_hit"),
            (y_tc_band, "y_tc_band"),
            (y_t30_hit, "y_t30_hit"),
            (y_t30_band, "y_t30_band"),
            (y_t45_hit, "y_t45_hit"),
            (y_t45_band, "y_t45_band"),
            (y_t60_hit, "y_t60_hit"),
            (y_t60_band, "y_t60_band"),
            (y_t75_hit, "y_t75_hit"),
            (y_t75_band, "y_t75_band"),
            (y_t90_hit, "y_t90_hit"),
            (y_t90_band, "y_t90_band"),
            (y_tw_hit, "y_tw_hit"),
        ):
            src = part.get(src_key) if isinstance(part.get(src_key), dict) else {}
            for k in keys_hit:
                dst[k] += int(src.get(k) or 0)

    def _rate(hit: int, miss: int) -> Optional[float]:
        denom = hit + miss
        return round(hit / float(denom), 4) if denom else None

    def _share(pos: int, neg: int) -> Optional[float]:
        denom = pos + neg
        return round(pos / float(denom), 4) if denom else None

    def _hit_pack(pack: Dict[str, int]) -> Dict[str, Any]:
        hm = int(pack["hit"]) + int(pack["miss"])
        return {
            **pack,
            "n_judged": hm,
            "hit_rate": _rate(pack["hit"], pack["miss"]),
            "hit_rate_pct": round((_rate(pack["hit"], pack["miss"]) or 0) * 100.0, 2)
            if hm
            else None,
        }

    out = dict(empty)
    out.update(
        {
            "scope": "all",
            "n_days": n_days,
            "n_traded": n_traded,
            "n_skipped": n_skipped,
            "n_signal_skip": n_signal_skip,
            "label_tau": {
                **label_tau,
                "n": sum(label_tau.values()),
                "pos_share": _share(label_tau["pos"], label_tau["neg"]),
            },
            "tau_hit": _hit_pack(tau_hit),
            "r_tau_hit": _hit_pack(r_tau_hit),
            "y_tc_hit": _hit_pack(y_tc_hit),
            "y_tc_band": _hit_pack(y_tc_band),
            "y_t30_hit": _hit_pack(y_t30_hit),
            "y_t30_band": _hit_pack(y_t30_band),
            "y_t45_hit": _hit_pack(y_t45_hit),
            "y_t45_band": _hit_pack(y_t45_band),
            "y_t60_hit": _hit_pack(y_t60_hit),
            "y_t60_band": _hit_pack(y_t60_band),
            "y_t75_hit": _hit_pack(y_t75_hit),
            "y_t75_band": _hit_pack(y_t75_band),
            "y_t90_hit": _hit_pack(y_t90_hit),
            "y_t90_band": _hit_pack(y_t90_band),
            "y_tw_hit": _hit_pack(y_tw_hit),
            "note": empty.get("note"),
        }
    )
    if traded_parts:
        out["traded"] = _merge_score_portraits(traded_parts)
        # nested traded should not recurse forever — traded parts are traded_only portraits
        if isinstance(out["traded"], dict):
            out["traded"].pop("traded", None)
            out["traded"].pop("by_slot", None)
            out["traded"]["scope"] = "traded"

    # 分槽位：按 hm 合并各票；缺钟的票用占位对齐日级样本（避免 1/10 vs 34/110）
    slot_groups: List[tuple] = []
    slot_seen: set = set()
    slot_order: List[str] = []
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        by = part.get("by_slot") if isinstance(part.get("by_slot"), dict) else None
        rows = (by or {}).get("slots") if by else part.get("slots")
        if not isinstance(rows, list) or not rows:
            continue
        by_hm: Dict[str, dict] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            hm = str(row.get("hm") or "").strip()[:5]
            if not hm:
                continue
            by_hm[hm] = row
            if hm not in slot_seen:
                slot_seen.add(hm)
                slot_order.append(hm)
        n_part = int(part.get("n_days") or 0)
        slot_groups.append((n_part, by_hm))
    slot_parts: Dict[str, List[dict]] = {}
    if slot_groups:
        for hm in slot_order:
            bucket: List[dict] = []
            for n_part, by_hm in slot_groups:
                if hm in by_hm:
                    bucket.append(by_hm[hm])
                    continue
                if n_part <= 0:
                    continue
                stub = _build_score_portrait_from_units(
                    [{"skipped": True, "date": f"_pad_{hm}_{i}"} for i in range(n_part)],
                    traded_only=False,
                    scope=f"slot:{hm}",
                )
                stub["n_with_yhat"] = 0
                stub["traded"] = _build_score_portrait_from_units(
                    [], traded_only=True, scope=f"slot_traded:{hm}"
                )
                bucket.append(stub)
            if bucket:
                slot_parts[hm] = bucket
    if slot_parts:
        merged_slots = []
        for hm in sorted(slot_order):
            merged = _merge_score_portraits(slot_parts[hm])
            if isinstance(merged, dict):
                merged.pop("by_slot", None)
                traded_nested = []
                for row in slot_parts[hm]:
                    if isinstance(row.get("traded"), dict):
                        traded_nested.append(row["traded"])
                traded_m = _merge_score_portraits(traded_nested) if traded_nested else None
                if isinstance(traded_m, dict):
                    traded_m.pop("traded", None)
                    traded_m.pop("by_slot", None)
                    traded_m["scope"] = f"slot_traded:{hm}"
                n_with = sum(int(r.get("n_with_yhat") or 0) for r in slot_parts[hm])
                if isinstance(merged, dict):
                    merged["n_with_yhat"] = n_with
                merged_slots.append({"hm": hm, **merged, "traded": traded_m})
        out["by_slot"] = {
            "slots": merged_slots,
            "n_slots": len(merged_slots),
            "n_days": int(out.get("n_days") or 0),
            "note": "分槽位画像（多票合并）：各钟扫描 ŷ 对同一套天级标签；缺钟占位对齐样本",
        }
    return out


def _merge_traded_score_portraits(
    parts: Sequence[Optional[dict]],
) -> Dict[str, Any]:
    """兼容旧名。"""
    return _merge_score_portraits(parts)


def build_y_tau_attribution(days: Sequence[dict]) -> Dict[str, Any]:
    """成交轮 ŷ_τc 符号 vs close[T]/price(τ)−1。KPI 键仍叫 tau_oc_hit_rate_pct。"""
    hit_n = 0
    miss_n = 0
    traded = 0
    for d in days or []:
        if not isinstance(d, dict) or not is_traded_t0_day(d):
            continue
        for unit in iter_traded_attribution_units(d):
            traded += 1
            sc = extract_scores(unit)
            real = _remaining_realized_pct(unit, unit)
            hit = _sign_hit(sc.get("y_τc"), real, pred_eps=0.05, real_eps=0.05)
            if hit is True:
                hit_n += 1
            elif hit is False:
                miss_n += 1
    denom = hit_n + miss_n
    return {
        "traded_with_tau": traded,
        "hit": hit_n,
        "miss": miss_n,
        "oc_hit_rate_pct": round(hit_n / denom * 100.0, 2) if denom else None,
    }


def summarize_dual_y_upgrade_acceptance(
    days: Sequence[dict],
    *,
    rules: Optional[dict] = None,
) -> Dict[str, Any]:
    """同窗口验收：成交 |ŷ_τc|。旧入场闸不再生效，flat 只在阈值为正时才会计数。"""
    _ = rules
    enter = 0.0

    traded = [d for d in (days or []) if isinstance(d, dict) and is_traded_t0_day(d)]
    flat_trades = 0
    for d in traded:
        sc = extract_scores(d)
        yt = sc.get("y_τc")
        if yt is None:
            continue
        try:
            a = abs(float(yt))
        except (TypeError, ValueError):
            continue
        if a < enter:
            flat_trades += 1

    att = build_y_tau_attribution(days)
    ok = flat_trades == 0
    return {
        "ok": ok,
        "traded_n": len(traded),
        "weak_tau_trades": 0,
        "flat_tau_trades": flat_trades,
        "tau_oc_hit_rate_pct": att.get("oc_hit_rate_pct"),
        "path_disagree_skips": 0,
        "y_tau_enter": enter,
        "y_tau_enter_strong": enter,
        "blockers": (["存在 |ŷ_τc|<enter 成交"] if flat_trades else []),
    }


def is_traded_t0_day(day: dict) -> bool:
    if day.get("skipped"):
        return False
    return (
        int(day.get("sold_qty") or 0) > 0
        or int(day.get("bought_qty") or 0) > 0
        or abs(float(day.get("pnl") or 0)) > 1e-9
        or abs(float(day.get("exposure_pnl") or 0)) > 1e-9
    )


def _slot_result_rows(day: dict) -> List[dict]:
    rows = day.get("t0_slot_results")
    if not day.get("t0_slots_enabled") or not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _slot_row_filled(row: dict) -> bool:
    if row.get("skipped"):
        return False
    return int(row.get("sold_qty") or 0) > 0 or int(row.get("bought_qty") or 0) > 0


def iter_traded_attribution_units(day: dict) -> List[dict]:
    """成交归因样本：多轮拆成已成交槽位，ŷ/方向/PnL 跟该轮走。"""
    filled = [r for r in _slot_result_rows(day) if _slot_row_filled(r)]
    if not filled:
        return [day] if is_traded_t0_day(day) else []
    units: List[dict] = []
    for r in filled:
        units.append(
            {
                "date": day.get("date"),
                "open": day.get("open"),
                "close": day.get("close"),
                "prev_close": day.get("prev_close"),
                "tau_realized": day.get("tau_realized"),
                "r_realized": r.get("r_realized")
                if r.get("r_realized") is not None
                else day.get("r_realized"),
                **pack_y_tc_fields(day),
                "direction": r.get("direction"),
                "pnl": r.get("pnl") or 0,
                "exposure_pnl": r.get("exposure_pnl") or 0,
                "scores": r.get("scores") or day.get("scores"),
                "direction_features": r.get("direction_features")
                or day.get("direction_features"),
                "close_band": r.get("close_band"),
                "t0_slot": r.get("id"),
                "t0_slot_hm": r.get("hm"),
            }
        )
    return units


def _cover_completed(day: dict) -> Optional[bool]:
    from core.t0.minute_path import T0_INTENTIONAL_ABANDON_EXITS, T0_PENDING_EXIT

    rows = day.get("t0_slot_results")
    if day.get("t0_slots_enabled") and isinstance(rows, list) and rows:
        filled = []
        for r in rows:
            if not isinstance(r, dict) or r.get("skipped"):
                continue
            sold = int(r.get("sold_qty") or 0)
            bought = int(r.get("bought_qty") or 0)
            if sold > 0 or bought > 0:
                filled.append(r)
        if not filled:
            return None
        flags = []
        for r in filled:
            flags.append(
                _cover_completed(
                    {
                        "direction": r.get("direction"),
                        "sold_qty": r.get("sold_qty"),
                        "covered_qty": r.get("covered_qty"),
                        "bought_qty": r.get("bought_qty"),
                        "sold_back_qty": r.get("sold_back_qty"),
                        "exit_reason": r.get("exit_reason"),
                    }
                )
            )
        if any(f is False for f in flags):
            return False
        if all(f is True for f in flags):
            return True
        return None

    sold = int(day.get("sold_qty") or 0)
    covered = int(day.get("covered_qty") or 0)
    bought = int(day.get("bought_qty") or 0)
    sold_back = int(day.get("sold_back_qty") or 0)
    direction = str(day.get("direction") or day.get("direction_used") or "")
    exit_reason = str(day.get("exit_reason") or "")
    if exit_reason == T0_PENDING_EXIT:
        return False
    if direction == "sell_then_buy" and sold > 0:
        if covered >= sold:
            return True
        # 反T主动放弃回补（含现金不足放弃）按设计计为完成侧
        if exit_reason in T0_INTENTIONAL_ABANDON_EXITS:
            return True
        return False
    if direction == "buy_then_sell" and bought > 0:
        if sold_back >= bought:
            return True
        # 正T允许隔夜多头 / 可卖旧仓不足时主动放弃卖旧，与反T abandon 对称计完成
        if exit_reason in T0_INTENTIONAL_ABANDON_EXITS:
            return True
        return False
    if sold > 0 or bought > 0:
        return (sold > 0 and covered >= sold) or (bought > 0 and sold_back >= bought)
    return None


def _y_tau_bucket(val: float) -> str:
    if val <= -0.5:
        return "≤-0.5"
    if val <= -0.25:
        return "-0.5~-0.25"
    if val < -0.05:
        return "-0.25~-0.05"
    if val <= 0.05:
        return "≈0"
    if val < 0.25:
        return "+0.05~0.25"
    if val < 0.5:
        return "+0.25~0.5"
    return "≥+0.5"


def _bucket_order() -> List[str]:
    return [
        "≤-0.5",
        "-0.5~-0.25",
        "-0.25~-0.05",
        "≈0",
        "+0.05~0.25",
        "+0.25~0.5",
        "≥+0.5",
    ]


def _day_market_price(d: dict) -> Optional[float]:
    for k in ("close", "open", "ref"):
        v = _f(d.get(k))
        if v is not None and v > 0:
            return round(v, 4)
    return None


def _stock_contrib_window(
    days: Sequence[dict],
    *,
    initial_shares: Optional[float] = None,
) -> Dict[str, Any]:
    """分票贡献：回测窗口起止日与收盘价、模拟股数。"""
    dated: Dict[str, dict] = {}
    for d in days or []:
        if not isinstance(d, dict):
            continue
        dt = str(d.get("date") or "")[:10]
        if dt:
            dated[dt] = d
    if not dated:
        return {}
    keys = sorted(dated.keys())
    start, end = keys[0], keys[-1]
    start_px = _day_market_price(dated[start])
    end_px = _day_market_price(dated[end])
    shares = initial_shares
    if shares is None:
        for d in (dated[start], dated[end]):
            s = _f(d.get("shares"))
            if s is not None and s > 0:
                shares = s
                break
    out: Dict[str, Any] = {
        "window_start_date": start,
        "window_end_date": end,
    }
    if shares is not None and float(shares) > 0:
        out["shares"] = int(float(shares))
    if start_px is not None:
        out["start_price"] = start_px
    if end_px is not None:
        out["end_price"] = end_px
    net_pnl = round(
        sum(
            float(d.get("pnl") or 0) + float(d.get("exposure_pnl") or 0)
            for d in days or []
            if isinstance(d, dict)
        ),
        2,
    )
    out["net_pnl"] = net_pnl
    sh = out.get("shares")
    sp = out.get("start_price")
    if sh and sp and float(sh) > 0 and float(sp) > 0:
        hold_mv = round(float(sh) * float(sp), 2)
        out["hold_mv_start"] = hold_mv
        out["return_pct"] = round(net_pnl / hold_mv * 100.0, 4)
    return out


def _summary_from_counts(
    *,
    traded_n: int,
    skip_n: int,
    signal_skip_n: int,
    cover_n: int,
    score_seen: int,
    score_total: int,
) -> Dict[str, Any]:
    total = traded_n + skip_n
    return {
        "total_days": total,
        "trade_count": traded_n,
        "skip_count": skip_n,
        "signal_skip_count": signal_skip_n,
        "participate_rate_pct": round(traded_n / total * 100.0, 2) if total else None,
        "signal_skip_rate_pct": round(signal_skip_n / total * 100.0, 2) if total else None,
        "cover_rate_pct": round(cover_n / traded_n * 100.0, 2) if traded_n else None,
        "score_coverage_pct": round(score_seen / score_total * 100.0, 2) if score_total else None,
        "y_tau_enter": 0.0,
    }


def build_t0_viz_payload(
    days: Sequence[dict],
    *,
    stock_code: str = "",
    stock_name: str = "",
    rules: Optional[dict] = None,
    initial_shares: Optional[float] = None,
) -> Dict[str, Any]:
    """从单日 walk 全量 days 生成 viz 块。"""
    skip_counts: Dict[str, int] = defaultdict(int)
    daily: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"traded": 0, "signal_skip": 0, "other_skip": 0}
    )
    y_tau_hist: Dict[str, int] = defaultdict(int)
    trade_points: List[Dict[str, Any]] = []
    y_tau_scatter: List[Dict[str, Any]] = []
    direction_split = {"sell_then_buy": 0, "buy_then_sell": 0, "mixed": 0}
    pnl_by_direction = {"sell_then_buy": 0.0, "buy_then_sell": 0.0}
    traded_n = 0
    skip_n = 0
    signal_skip_n = 0
    cover_n = 0
    score_seen = 0
    score_total = 0
    y_tau_traded: List[float] = []
    y_tau_skipped: List[float] = []
    skip_scope_slot = False

    for d in days or []:
        if not isinstance(d, dict):
            continue
        date = str(d.get("date") or "")[:10]
        if not date:
            continue
        code = str(d.get("stock_code") or stock_code or "")
        name = str(d.get("stock_name") or stock_name or "")
        sc = extract_scores(d)
        slot_rows = _slot_result_rows(d)
        if slot_rows:
            skip_scope_slot = True
            for r in slot_rows:
                if not r.get("skipped"):
                    continue
                reason = str(r.get("reason") or "")
                if not str(reason).strip():
                    continue
                skip_counts[classify_t0_skip_reason(reason)] += 1

        if is_traded_t0_day(d):
            traded_n += 1
            daily[date]["traded"] += 1
            pnl = round(float(d.get("pnl") or 0) + float(d.get("exposure_pnl") or 0), 2)
            direction = str(d.get("direction") or d.get("direction_used") or "")
            slot_seen = set()
            if d.get("t0_slots_enabled"):
                for r in d.get("t0_slot_results") or []:
                    if not isinstance(r, dict) or r.get("skipped"):
                        continue
                    rd = str(r.get("direction") or "")
                    if rd not in pnl_by_direction:
                        continue
                    if int(r.get("sold_qty") or 0) <= 0 and int(r.get("bought_qty") or 0) <= 0:
                        continue
                    slot_seen.add(rd)
                    pnl_by_direction[rd] = round(
                        pnl_by_direction[rd] + float(r.get("pnl") or 0), 2
                    )
            if slot_seen:
                if len(slot_seen) > 1:
                    direction_split["mixed"] += 1
                else:
                    rd = next(iter(slot_seen))
                    direction_split[rd] += 1
            elif direction in direction_split:
                direction_split[direction] += 1
                pnl_by_direction[direction] = round(pnl_by_direction[direction] + pnl, 2)
            covered = _cover_completed(d)
            if covered:
                cover_n += 1
            units = iter_traded_attribution_units(d)
            unit_tau = False
            unit_scatter = False
            for unit in units:
                usc = extract_scores(unit)
                yt = usc.get("y_τc")
                hm = str(unit.get("t0_slot_hm") or unit.get("hm") or "")[:5]
                scan_row = _scan_row_at_hm(d, hm)
                if yt is None and scan_row is not None:
                    yt = _f(scan_row.get("y_τc"))
                    if yt is None:
                        yt = _f(scan_row.get("predicted_score_τc"))
                rp = _r_pct_for_unit(d, unit)
                if yt is None and rp is None:
                    continue
                if yt is not None:
                    unit_tau = True
                    y_tau_traded.append(float(yt))
                    y_tau_hist[_y_tau_bucket(float(yt))] += 1
                unit_scatter = True
                y_tau_scatter.append(
                    {
                        "date": date,
                        "y_τc": yt,
                        "r_pct": rp,
                        "outcome": "traded",
                        "direction": unit.get("direction"),
                        "stock_code": code,
                        "stock_name": name,
                        "t0_slot_hm": unit.get("t0_slot_hm"),
                    }
                )
            if unit_tau:
                score_seen += 1
            elif sc.get("y_τc") is not None:
                score_seen += 1
                y_tau_traded.append(float(sc["y_τc"]))
                y_tau_hist[_y_tau_bucket(float(sc["y_τc"]))] += 1
                if not unit_scatter:
                    y_tau_scatter.append(
                        {
                            "date": date,
                            "y_τc": sc["y_τc"],
                            "r_pct": _r_pct_for_unit(d, None),
                            "outcome": "traded",
                            "direction": direction,
                            "stock_code": code,
                            "stock_name": name,
                        }
                    )
            score_total += 1
            cp = d.get("cover_policy") if isinstance(d.get("cover_policy"), dict) else {}
            first_sc = extract_scores(units[0]) if units else sc
            trade_points.append(
                {
                    "date": date,
                    "pnl": pnl,
                    "direction": direction,
                    "stock_code": code,
                    "stock_name": name,
                    "y_τc": first_sc.get("y_τc"),
                    "gap_pct": first_sc.get("gap_pct"),
                    "y_co": first_sc.get("y_co"),
                    "sold_qty": int(d.get("sold_qty") or 0),
                    "covered_qty": int(d.get("covered_qty") or 0),
                    "bought_qty": int(d.get("bought_qty") or 0),
                    "sold_back_qty": int(d.get("sold_back_qty") or 0),
                    "cover_completed": covered,
                    "must_cover": cp.get("must_cover") if cp else None,
                    "cover_reason": cp.get("reason") if cp else None,
                    "minute_path": bool(d.get("minute_path")),
                    "path_mode": d.get("path_mode"),
                }
            )
            continue

        if not d.get("skipped"):
            continue
        skip_n += 1
        reason = str(d.get("reason") or d.get("direction_reason") or "跳过")
        cat = str(d.get("skip_category") or classify_t0_skip_reason(reason))
        if not slot_rows:
            skip_counts[cat] += 1
        if d.get("signal_skip"):
            signal_skip_n += 1
            daily[date]["signal_skip"] += 1
        else:
            daily[date]["other_skip"] += 1
        yt = sc.get("y_τc")
        if yt is not None:
            score_seen += 1
            if d.get("signal_skip"):
                y_tau_skipped.append(float(yt))
                y_tau_hist[_y_tau_bucket(float(yt))] += 1
                y_tau_scatter.append(
                    {
                        "date": date,
                        "y_τc": yt,
                        "outcome": "signal_skip",
                        "direction": None,
                        "stock_code": code,
                        "stock_name": name,
                        "skip_category": cat,
                    }
                )
        # 分母=可评估日（不含缺分钟）；分子=有 y_τ。避免路径跳过有分却不进分母 → 覆盖率>100%
        if cat != "missing_minute":
            score_total += 1

    trade_points.sort(key=lambda x: (str(x.get("date") or ""), str(x.get("stock_code") or "")))
    cum = 0.0
    cumulative: List[Dict[str, Any]] = []
    for tp in trade_points:
        cum = round(cum + float(tp.get("pnl") or 0), 2)
        row = dict(tp)
        row["cum_pnl"] = cum
        cumulative.append(row)

    y_tau_scatter.sort(key=lambda x: str(x.get("date") or ""))
    if len(y_tau_scatter) > 120:
        y_tau_scatter = y_tau_scatter[-120:]

    activity = [
        {
            "date": dt,
            "traded": v["traded"],
            "signal_skip": v["signal_skip"],
            "other_skip": v["other_skip"],
            "total": v["traded"] + v["signal_skip"] + v["other_skip"],
        }
        for dt, v in sorted(daily.items())
    ]

    skip_total = max(1, sum(skip_counts.values()))
    skip_categories = [
        {
            "id": cid,
            "label": SKIP_CAT_LABELS.get(cid, cid),
            "count": int(cnt),
            "pct": round(int(cnt) / skip_total * 100.0, 1),
            "color": SKIP_CAT_COLORS.get(cid, "#94a3b8"),
        }
        for cid, cnt in sorted(skip_counts.items(), key=lambda x: (-x[1], x[0]))
    ]

    y_tau_buckets = [
        {"bin": b, "count": int(y_tau_hist[b])}
        for b in _bucket_order()
        if y_tau_hist.get(b)
    ]
    y_tau_hit = build_y_tau_attribution(days)
    score_portrait = build_backtest_score_portrait(days)

    stock_contrib: List[Dict[str, Any]] = []
    if stock_code or traded_n or skip_n:
        pnl_sum = round(sum(float(tp.get("pnl") or 0) for tp in trade_points), 2)
        total = traded_n + skip_n
        top_skip = skip_categories[0] if skip_categories else None
        row: Dict[str, Any] = {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "pnl": pnl_sum,
            "trade_days": traded_n,
            "skip_days": skip_n,
            "sell_then_buy_days": direction_split.get("sell_then_buy") or 0,
            "buy_then_sell_days": direction_split.get("buy_then_sell") or 0,
            "mixed_days": direction_split.get("mixed") or 0,
            "participate_rate_pct": round(traded_n / total * 100.0, 2) if total else None,
            "score_coverage_pct": round(score_seen / score_total * 100.0, 2) if score_total else None,
        }
        if skip_categories:
            row["skip_breakdown"] = skip_categories[:4]
            top_skip = skip_categories[0]
            row["skip_top_id"] = top_skip.get("id")
            row["skip_top_label"] = top_skip.get("label")
            row["skip_top_count"] = top_skip.get("count")
            row["skip_top_pct"] = top_skip.get("pct")
        row.update(_stock_contrib_window(days, initial_shares=initial_shares))
        stock_contrib.append(row)

    summary = _summary_from_counts(
        traded_n=traded_n,
        skip_n=skip_n,
        signal_skip_n=signal_skip_n,
        cover_n=cover_n,
        score_seen=score_seen,
        score_total=score_total,
    )
    summary["score_seen"] = score_seen
    summary["score_total"] = score_total
    if y_tau_traded:
        summary["avg_y_tau_traded"] = round(sum(y_tau_traded) / len(y_tau_traded), 3)
    if y_tau_skipped:
        summary["avg_y_tau_signal_skip"] = round(sum(y_tau_skipped) / len(y_tau_skipped), 3)
    if y_tau_hit.get("oc_hit_rate_pct") is not None:
        summary["tau_oc_hit_rate_pct"] = y_tau_hit.get("oc_hit_rate_pct")

    upgrade_acc = summarize_dual_y_upgrade_acceptance(days, rules=rules)
    summary["upgrade_acceptance"] = upgrade_acc
    summary["score_portrait"] = score_portrait
    if score_portrait.get("tau_hit", {}).get("hit_rate_pct") is not None:
        summary["tau_pred_hit_rate_pct"] = score_portrait["tau_hit"]["hit_rate_pct"]
    if score_portrait.get("y_tc_hit", {}).get("hit_rate_pct") is not None:
        summary["tc_pred_hit_rate_pct"] = score_portrait["y_tc_hit"]["hit_rate_pct"]
    if score_portrait.get("y_tw_hit", {}).get("hit_rate_pct") is not None:
        summary["tw_pred_hit_rate_pct"] = score_portrait["y_tw_hit"]["hit_rate_pct"]
    if score_portrait.get("y_t30_hit", {}).get("hit_rate_pct") is not None:
        summary["t30_pred_hit_rate_pct"] = score_portrait["y_t30_hit"]["hit_rate_pct"]
    if score_portrait.get("y_t30_band", {}).get("hit_rate_pct") is not None:
        summary["t30_band_hit_rate_pct"] = score_portrait["y_t30_band"]["hit_rate_pct"]
    if score_portrait.get("y_t45_hit", {}).get("hit_rate_pct") is not None:
        summary["t45_pred_hit_rate_pct"] = score_portrait["y_t45_hit"]["hit_rate_pct"]
    if score_portrait.get("y_t45_band", {}).get("hit_rate_pct") is not None:
        summary["t45_band_hit_rate_pct"] = score_portrait["y_t45_band"]["hit_rate_pct"]
    if score_portrait.get("y_t60_hit", {}).get("hit_rate_pct") is not None:
        summary["t60_pred_hit_rate_pct"] = score_portrait["y_t60_hit"]["hit_rate_pct"]
    if score_portrait.get("y_t60_band", {}).get("hit_rate_pct") is not None:
        summary["t60_band_hit_rate_pct"] = score_portrait["y_t60_band"]["hit_rate_pct"]
    if score_portrait.get("y_t75_hit", {}).get("hit_rate_pct") is not None:
        summary["t75_pred_hit_rate_pct"] = score_portrait["y_t75_hit"]["hit_rate_pct"]
    if score_portrait.get("y_t75_band", {}).get("hit_rate_pct") is not None:
        summary["t75_band_hit_rate_pct"] = score_portrait["y_t75_band"]["hit_rate_pct"]
    if score_portrait.get("y_t90_hit", {}).get("hit_rate_pct") is not None:
        summary["t90_pred_hit_rate_pct"] = score_portrait["y_t90_hit"]["hit_rate_pct"]
    if score_portrait.get("y_t90_band", {}).get("hit_rate_pct") is not None:
        summary["t90_band_hit_rate_pct"] = score_portrait["y_t90_band"]["hit_rate_pct"]
    return {
        "skip_categories": skip_categories,
        "cumulative_pnl": cumulative,
        "daily_activity": activity,
        "y_tau_buckets": y_tau_buckets,
        "score_portrait": score_portrait,
        "y_tau_scatter": y_tau_scatter,
        "stock_contrib": stock_contrib,
        "direction_split": direction_split,
        "pnl_by_direction": {
            k: round(v, 2) for k, v in pnl_by_direction.items()
        },
        "trade_count": traded_n,
        "skip_count": skip_n,
        "skip_scope": "slot" if skip_scope_slot else "day",
        "skip_round_count": int(sum(skip_counts.values())) if skip_scope_slot else int(skip_n),
        "upgrade_acceptance": upgrade_acc,
        "summary": summary,
    }


def merge_t0_viz_payloads(
    payloads: Sequence[Optional[dict]],
    *,
    rules: Optional[dict] = None,
) -> Dict[str, Any]:
    """多持仓回测合并各票 viz。"""
    _ = rules
    skip_counts: Dict[str, int] = defaultdict(int)
    daily: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"traded": 0, "signal_skip": 0, "other_skip": 0}
    )
    y_tau_hist: Dict[str, int] = defaultdict(int)
    trade_points: List[Dict[str, Any]] = []
    y_tau_scatter: List[Dict[str, Any]] = []
    stock_contrib: List[Dict[str, Any]] = []
    direction_split = {"sell_then_buy": 0, "buy_then_sell": 0, "mixed": 0}
    pnl_by_direction = {"sell_then_buy": 0.0, "buy_then_sell": 0.0}
    traded_n = 0
    skip_n = 0
    signal_skip_n = 0
    cover_n = 0
    score_seen = 0
    score_total = 0
    y_tau_traded: List[float] = []
    y_tau_skipped: List[float] = []

    skip_scope_slot = False

    for p in payloads or []:
        if not isinstance(p, dict):
            continue
        if str(p.get("skip_scope") or "") == "slot":
            skip_scope_slot = True
        for sc in p.get("skip_categories") or []:
            if isinstance(sc, dict):
                skip_counts[str(sc.get("id") or "other")] += int(sc.get("count") or 0)
        for row in p.get("daily_activity") or []:
            if not isinstance(row, dict):
                continue
            dt = str(row.get("date") or "")[:10]
            if not dt:
                continue
            daily[dt]["traded"] += int(row.get("traded") or 0)
            daily[dt]["signal_skip"] += int(row.get("signal_skip") or 0)
            daily[dt]["other_skip"] += int(row.get("other_skip") or 0)
        for row in p.get("y_tau_buckets") or []:
            if isinstance(row, dict) and row.get("bin"):
                y_tau_hist[str(row["bin"])] += int(row.get("count") or 0)
        for tp in p.get("cumulative_pnl") or []:
            if isinstance(tp, dict) and tp.get("date"):
                trade_points.append(dict(tp))
                if tp.get("cover_completed"):
                    cover_n += 1
                yt = _f(tp.get("y_τc"))
                if yt is not None:
                    score_seen += 1
                    y_tau_traded.append(yt)
                score_total += 1
        for pt in p.get("y_tau_scatter") or []:
            if isinstance(pt, dict):
                y_tau_scatter.append(dict(pt))
                if pt.get("outcome") == "signal_skip" and _f(pt.get("y_τc")) is not None:
                    y_tau_skipped.append(float(pt["y_τc"]))
        for st in p.get("stock_contrib") or []:
            if isinstance(st, dict):
                stock_contrib.append(dict(st))
        ds = p.get("direction_split") or {}
        direction_split["sell_then_buy"] += int(ds.get("sell_then_buy") or 0)
        direction_split["buy_then_sell"] += int(ds.get("buy_then_sell") or 0)
        direction_split["mixed"] += int(ds.get("mixed") or 0)
        pbd = p.get("pnl_by_direction") or {}
        pnl_by_direction["sell_then_buy"] += float(pbd.get("sell_then_buy") or 0)
        pnl_by_direction["buy_then_sell"] += float(pbd.get("buy_then_sell") or 0)
        traded_n += int(p.get("trade_count") or 0)
        skip_n += int(p.get("skip_count") or 0)
        sm = p.get("summary") or {}
        signal_skip_n += int(sm.get("signal_skip_count") or 0)
        score_seen += int(sm.get("score_seen") or 0)
        score_total += int(sm.get("score_total") or 0)

    trade_points.sort(key=lambda x: (str(x.get("date") or ""), str(x.get("stock_code") or "")))
    cum = 0.0
    cumulative: List[Dict[str, Any]] = []
    for tp in trade_points:
        cum = round(cum + float(tp.get("pnl") or 0), 2)
        row = dict(tp)
        row["cum_pnl"] = cum
        cumulative.append(row)

    y_tau_scatter.sort(key=lambda x: str(x.get("date") or ""))
    if len(y_tau_scatter) > 160:
        y_tau_scatter = y_tau_scatter[-160:]

    skip_total = max(1, sum(skip_counts.values()))
    summary = _summary_from_counts(
        traded_n=traded_n,
        skip_n=skip_n,
        signal_skip_n=signal_skip_n,
        cover_n=cover_n,
        score_seen=score_seen,
        score_total=max(score_total, traded_n + signal_skip_n),
    )
    if y_tau_traded:
        summary["avg_y_tau_traded"] = round(sum(y_tau_traded) / len(y_tau_traded), 3)
    if y_tau_skipped:
        summary["avg_y_tau_signal_skip"] = round(sum(y_tau_skipped) / len(y_tau_skipped), 3)
    summary["score_seen"] = score_seen
    summary["score_total"] = max(score_total, traded_n + signal_skip_n)

    y_tau_hit = build_y_tau_attribution(
        [tp for tp in trade_points if tp.get("date")]
    )
    if y_tau_hit.get("oc_hit_rate_pct") is not None:
        summary["tau_oc_hit_rate_pct"] = y_tau_hit.get("oc_hit_rate_pct")

    score_portrait = _merge_score_portraits(
        [p.get("score_portrait") for p in (payloads or []) if isinstance(p, dict)]
    )
    summary["score_portrait"] = score_portrait
    if score_portrait.get("tau_hit", {}).get("hit_rate_pct") is not None:
        summary["tau_pred_hit_rate_pct"] = score_portrait["tau_hit"]["hit_rate_pct"]
    if score_portrait.get("y_tc_hit", {}).get("hit_rate_pct") is not None:
        summary["tc_pred_hit_rate_pct"] = score_portrait["y_tc_hit"]["hit_rate_pct"]
    if score_portrait.get("y_tw_hit", {}).get("hit_rate_pct") is not None:
        summary["tw_pred_hit_rate_pct"] = score_portrait["y_tw_hit"]["hit_rate_pct"]
    if score_portrait.get("y_t30_hit", {}).get("hit_rate_pct") is not None:
        summary["t30_pred_hit_rate_pct"] = score_portrait["y_t30_hit"]["hit_rate_pct"]
    if score_portrait.get("y_t30_band", {}).get("hit_rate_pct") is not None:
        summary["t30_band_hit_rate_pct"] = score_portrait["y_t30_band"]["hit_rate_pct"]
    if score_portrait.get("y_t45_hit", {}).get("hit_rate_pct") is not None:
        summary["t45_pred_hit_rate_pct"] = score_portrait["y_t45_hit"]["hit_rate_pct"]
    if score_portrait.get("y_t45_band", {}).get("hit_rate_pct") is not None:
        summary["t45_band_hit_rate_pct"] = score_portrait["y_t45_band"]["hit_rate_pct"]
    if score_portrait.get("y_t60_hit", {}).get("hit_rate_pct") is not None:
        summary["t60_pred_hit_rate_pct"] = score_portrait["y_t60_hit"]["hit_rate_pct"]
    if score_portrait.get("y_t60_band", {}).get("hit_rate_pct") is not None:
        summary["t60_band_hit_rate_pct"] = score_portrait["y_t60_band"]["hit_rate_pct"]
    if score_portrait.get("y_t75_hit", {}).get("hit_rate_pct") is not None:
        summary["t75_pred_hit_rate_pct"] = score_portrait["y_t75_hit"]["hit_rate_pct"]
    if score_portrait.get("y_t75_band", {}).get("hit_rate_pct") is not None:
        summary["t75_band_hit_rate_pct"] = score_portrait["y_t75_band"]["hit_rate_pct"]
    if score_portrait.get("y_t90_hit", {}).get("hit_rate_pct") is not None:
        summary["t90_pred_hit_rate_pct"] = score_portrait["y_t90_hit"]["hit_rate_pct"]
    if score_portrait.get("y_t90_band", {}).get("hit_rate_pct") is not None:
        summary["t90_band_hit_rate_pct"] = score_portrait["y_t90_band"]["hit_rate_pct"]

    stock_contrib.sort(key=lambda x: -abs(float(x.get("pnl") or 0)))

    out: Dict[str, Any] = {
        "skip_categories": [
            {
                "id": cid,
                "label": SKIP_CAT_LABELS.get(cid, cid),
                "count": int(cnt),
                "pct": round(int(cnt) / skip_total * 100.0, 1),
                "color": SKIP_CAT_COLORS.get(cid, "#94a3b8"),
            }
            for cid, cnt in sorted(skip_counts.items(), key=lambda x: (-x[1], x[0]))
        ],
        "cumulative_pnl": cumulative,
        "daily_activity": [
            {
                "date": dt,
                "traded": v["traded"],
                "signal_skip": v["signal_skip"],
                "other_skip": v["other_skip"],
                "total": v["traded"] + v["signal_skip"] + v["other_skip"],
            }
            for dt, v in sorted(daily.items())
        ],
        "y_tau_buckets": [
            {"bin": b, "count": int(y_tau_hist[b])}
            for b in _bucket_order()
            if y_tau_hist.get(b)
        ],
        "score_portrait": score_portrait,
        "y_tau_scatter": y_tau_scatter,
        "stock_contrib": stock_contrib,
        "direction_split": direction_split,
        "pnl_by_direction": {k: round(v, 2) for k, v in pnl_by_direction.items()},
        "trade_count": traded_n,
        "skip_count": skip_n,
        "skip_scope": "slot" if skip_scope_slot else "day",
        "skip_round_count": int(sum(skip_counts.values())) if skip_scope_slot else int(skip_n),
        "summary": summary,
    }
    return out


def attach_summary_to_viz(report: dict) -> None:
    """把回测报告中的质量指标写入 viz.summary。"""
    viz = report.get("viz")
    if not isinstance(viz, dict):
        return
    sm = dict(viz.get("summary") or {})
    for k in (
        "participate_rate_pct",
        "cover_rate_pct",
        "t0_pnl_with_exposure",
        "pnl_vs_hold_mv_pct",
        "avg_pnl_per_trade_day",
        "missing_minute_days",
    ):
        if report.get(k) is not None:
            sm[k] = report.get(k)
    viz["summary"] = sm
