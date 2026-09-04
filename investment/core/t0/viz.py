"""做 T 回测可视化数据聚合（供 Web 图表）。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence

SKIP_CAT_LABELS: Dict[str, str] = {
    "missing_minute": "缺分钟",
    "missing_scores": "缺ŷ",
    "y_path_missing": "缺y_path",
    "y_eod_flat": "y_eod未过门槛",
    "y_tau_flat": "y_τ横盘",
    "r_tau_flat": "R̂_τ超额不足",
    "y_tau_weak": "y_τ弱信号",
    "y_path_flat": "y_path横盘",
    "y_path_disagree": "y_τ↔y_path异号",
    "y_cx_high": "y_cx太折",
    "gap_tier_skip": "大缺口反向跳过",
    "path_abandon": "前缀无空间放弃",
    "prefix_vs_path": "前缀振幅超路径",
    "multi_slot_miss": "多轮均未成交",
    "tau_entry_price": "入场价vs开盘×ŷ_τ(旧)",
    "tau_exit_price": "出场价vs开盘×ŷ_τ",
    "y_trade_weak": "y_trade幅度不足",
    "eod_tau_disagree": "y_eod↔y_τ异号",
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
    "trigger_miss": "#b8c0c8",
    "other": "#cbd2d9",
    "amplitude": "#6e7378",
    "directional_amplitude": "#9aa0a6",
    # 门槛不足 · 冷钢蓝 / 青灰
    "y_eod_flat": "#3d6a8a",
    "y_tau_flat": "#3a7a72",
    "r_tau_flat": "#4a6e7a",
    "y_tau_weak": "#5a7d8c",
    "y_path_flat": "#7a6a55",
    "y_cx_high": "#6b4c8a",
    # 异号 / 冲突 · 克制酒红 / 梅紫
    "eod_tau_disagree": "#b33a3a",
    "trade_tau_disagree": "#8f3d5b",
    "trade_tau_sign": "#c45c4a",
    "tau_leg1_prior": "#a0653a",
    "y_path_disagree": "#6b4c7a",
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


def classify_t0_skip_reason(reason: Optional[str]) -> str:
    r = str(reason or "")
    if "price_space_mismatch" in r or ("价空间" in r and ("错位" in r or "不一致" in r)):
        return "price_space_mismatch"
    if "y_cx" in r and ("太折" in r or "曲折" in r):
        return "y_cx_high"
    if "缺" in r and ("分钟" in r or "minute" in r.lower()):
        return "missing_minute"
    if "缺 y_path" in r or ("缺" in r and "y_path" in r):
        return "y_path_missing"
    if "缺" in r and ("y_" in r or "快照" in r or "即时算分" in r):
        return "missing_scores"
    if "y_path" in r and "异号" in r:
        return "y_path_disagree"
    if "y_path" in r and ("未过门槛" in r or "≥-" in r):
        return "y_path_flat"
    if "y_eod" in r and "未过门槛" in r:
        return "y_eod_flat"
    if "|R̂_τ|" in r or "R̂_τ" in r or "超额不足" in r:
        return "r_tau_flat"
    if "y_τ" in r and "未过门槛" in r:
        return "y_tau_flat"
    if "y_eod" in r and "y_τ" in r and "异号" in r and "|y_eod|" in r:
        return "eod_tau_disagree"
    if "y_trade" in r and "y_τ" in r and "异号" in r and "|y_trade|" in r:
        return "trade_tau_disagree"
    if "日线先验" in r or "趋势不一致" in r or "正T风险高" in r or "反T风险高" in r:
        return "tau_leg1_prior"
    if "异号" in r or "trade_tau_sign" in r.lower():
        return "trade_tau_sign"
    if "y_trade" in r:
        return "y_trade_weak"
    # 已下线的冲突闸（eod↔τ / y_check）→ 其它，避免饼图再标「冲突」
    if (
        "先验≠" in r
        or ("冲突" in r and "y_eod" in r)
        or ("y_check" in r.lower() and "conflict" in r.lower())
    ):
        return "other"
    if (
        "y_path" in r
        and (
            "不一致" in r
            or "预测先" in r
            or "先高后低" in r
            or "先低后高" in r
            or "上冲偏大" in r
            or "下探偏大" in r
        )
    ):
        return "y_path_disagree"
    if "y_path" in r and ("横盘" in r or "|y_path|" in r):
        return "y_path_flat"
    if "弱信号" in r:
        return "y_tau_weak"
    if "|y_τ|" in r or ("y_τ" in r and "横盘" in r):
        return "y_tau_flat"
    if "大缺口" in r or "gap_tier" in r.lower():
        return "gap_tier_skip"
    # 确认根入场 / 第二腿出场价 vs open×(1+ŷ_τ×裕度)
    if "τ出场" in r:
        return "tau_exit_price"
    if (
        "τ入场" in r
        or "τ带" in r
        or ("买价" in r and ("ŷ_τ" in r or "开盘×(1+" in r))
        or ("卖价" in r and ("ŷ_τ" in r or "开盘×(1+" in r))
        or "入场价" in r
    ):
        return "tau_entry_price"
    if (
        "空间用尽" in r
        or ">|ŷ_path|" in r
        or ">|y_path|" in r
        or (
            "前缀振幅" in r
            and ("ŷ_path" in r or "y_path" in r or "|ŷ_path|" in r or "|y_path|" in r)
        )
    ):
        return "prefix_vs_path"
    if "放弃" in r and ("反T" in r or "正T" in r or "前缀" in r):
        return "path_abandon"
    if "多轮均未成交" in r:
        return "multi_slot_miss"
    if "上移振幅" in r or "下移振幅" in r or "方向振幅" in r:
        return "directional_amplitude"
    if "振幅" in r:
        return "amplitude"
    if "T+1" in r or "可卖旧仓" in r or "无可卖" in r or ("可卖" in r and "锁定" in r):
        return "tplus1"
    if "不足1手" in r or "动仓不足" in r or ("手" in r and "不足" in r):
        return "lot_size"
    # 「分钟路径未触及/未开成…」优先归未触达，勿因含「路径」误入 path
    if "未触及" in r or "未触" in r or "未开成第一腿" in r or "未开第一腿" in r:
        return "trigger_miss"
    if "路径" in r or "veto" in r.lower():
        return "path"
    if "重复落账" in r or "盘中已有成交腿" in r:
        return "intraday_legs_open"
    if "现金" in r or "买不起" in r:
        return "cash"
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


def extract_scores(day: dict) -> Dict[str, Optional[float]]:
    feats = day.get("direction_features") if isinstance(day.get("direction_features"), dict) else {}
    raw = day.get("scores") if isinstance(day.get("scores"), dict) else {}
    y_tau = _f(feats.get("y_tau"))
    if y_tau is None:
        y_tau = _f(raw.get("y_tau"))
    if y_tau is None:
        y_tau = _f(day.get("direction_score"))
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

    y_tau_oc = _pick("y_tau_oc")
    if y_tau_oc is None:
        y_tau_oc = _pick("predicted_score_tau_oc")
    if y_tau_oc is None:
        for blob in (feats, raw, day):
            ft = blob.get("formula_terms_tau") if isinstance(blob, dict) else None
            if isinstance(ft, dict):
                y_tau_oc = _f(ft.get("y_tau_raw"))
                if y_tau_oc is not None:
                    break
    ret_ot = None
    for blob in (feats, raw):
        if not isinstance(blob, dict):
            continue
        ft = blob.get("features_tau")
        if isinstance(ft, dict) and ft.get("ret_open_to_tau") is not None:
            ret_ot = _f(ft.get("ret_open_to_tau"))
            if ret_ot is not None:
                break
        if blob.get("ret_open_to_tau") is not None:
            ret_ot = _f(blob.get("ret_open_to_tau"))
            if ret_ot is not None:
                break

    y_path = _pick("y_path_portrait")
    if y_path is None:
        y_path = _pick("y_path")
    if y_path is None:
        y_path = _pick("predicted_score_path")
    if y_path is None:
        y_path = _pick("y_path_prefix")

    return {
        "y_tau": y_tau,
        "y_tau_oc": y_tau_oc,
        "y_tau_portrait_oc": _pick("y_tau_portrait_oc"),
        "ret_open_to_tau": ret_ot,
        "y_path": y_path,
        "y_path_portrait": _pick("y_path_portrait"),
        "y_eod": _pick("y_eod"),
        "y_trade": _pick("y_trade"),
        "y_on": _pick("y_on"),
        "gap_pct": gap,
    }


def _compound_pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """(1+a%)(1+b%)−1，用于剩余ŷ还原 OC ŷ。"""
    if a is None or b is None:
        return None
    try:
        return round(((1.0 + float(a) / 100.0) * (1.0 + float(b) / 100.0) - 1.0) * 100.0, 6)
    except (TypeError, ValueError):
        return None


def resolve_y_tau_oc_for_portrait(sc: Dict[str, Optional[float]]) -> Optional[float]:
    """画像 τ 命中用：优先 ≤τ 因果补记的 OC 头；再决策 OC；旧包可还原。"""
    if not isinstance(sc, dict):
        return None
    # 画像专用：开盘选向禁分钟后，用 ≤τ 重算的 OC 头（与训练同信息集）
    portrait = _f(sc.get("y_tau_portrait_oc"))
    if portrait is not None:
        return portrait
    oc = _f(sc.get("y_tau_oc"))
    if oc is not None:
        return oc
    y = _f(sc.get("y_tau"))
    rot = _f(sc.get("ret_open_to_tau"))
    restored = _compound_pct(y, rot)
    if restored is not None:
        return restored
    return y


def resolve_y_path_for_portrait(sc: Dict[str, Optional[float]]) -> Optional[float]:
    """画像 path 命中用：优先 ≤τ 因果 ŷ_path_portrait。"""
    if not isinstance(sc, dict):
        return None
    for key in ("y_path_portrait", "y_path", "predicted_score_path", "y_path_prefix"):
        v = _f(sc.get(key))
        if v is not None:
            return v
    return None

def _gap_bucket(gap: Optional[float]) -> str:
    if gap is None:
        return "unknown"
    g = float(gap)
    if g <= -2.0:
        return "≤-2%"
    if g <= -1.0:
        return "-2~-1%"
    if g < -0.3:
        return "-1~-0.3%"
    if g <= 0.3:
        return "≈0"
    if g < 1.0:
        return "+0.3~1%"
    if g < 2.0:
        return "+1~2%"
    return "≥+2%"


def _gap_bucket_order() -> List[str]:
    return ["≤-2%", "-2~-1%", "-1~-0.3%", "≈0", "+0.3~1%", "+1~2%", "≥+2%", "unknown"]


def _oc_realized_pct(day: dict) -> Optional[float]:
    o = _f(day.get("open"))
    c = _f(day.get("close"))
    if o is None or c is None or o <= 0:
        return None
    return round((c / o - 1.0) * 100.0, 4)


def _tau_oc_hit(y_tau: Optional[float], oc_real: Optional[float], *, eps: float = 0.05) -> Optional[bool]:
    if y_tau is None or oc_real is None:
        return None
    if abs(y_tau) < eps or abs(oc_real) < eps:
        return None
    return (y_tau > 0) == (oc_real > 0)


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


def _bump(pack: Dict[str, int], key: str) -> None:
    pack[key] = int(pack.get(key) or 0) + 1


def resolve_y_eod_for_portrait(sc: Dict[str, Optional[float]]) -> Optional[float]:
    if not isinstance(sc, dict):
        return None
    for key in ("y_eod", "predicted_score_eod", "predicted_score"):
        v = _f(sc.get(key))
        if v is not None:
            return v
    return None


def resolve_y_trade_for_portrait(sc: Dict[str, Optional[float]]) -> Optional[float]:
    if not isinstance(sc, dict):
        return None
    for key in ("y_trade", "predicted_score_blend", "decision_score", "score"):
        v = _f(sc.get(key))
        if v is not None:
            return v
    return None


def _eod_realized_pct(day: dict) -> Optional[float]:
    """涨跌 label：close[T]/prev_close−1（与 ŷ_eod / ŷ_trade 同目标）。"""
    n = _pick_realized(day, "eod_realized")
    if n is not None:
        return n
    pc = _f(day.get("prev_close"))
    c = _f(day.get("close"))
    if pc is None or c is None or pc <= 0:
        return None
    return round((c / pc - 1.0) * 100.0, 4)


def _fill_day_eod_trade_scores(day: dict, scores: Optional[dict]) -> dict:
    """槽位缺 y_eod/y_trade 时用日级分补洞（二者多为日级头）。"""
    out = dict(scores) if isinstance(scores, dict) else {}
    day_sc = extract_scores(day) if isinstance(day, dict) else {}
    for key in (
        "y_eod",
        "predicted_score_eod",
        "predicted_score",
        "y_trade",
        "predicted_score_blend",
        "decision_score",
        "score",
    ):
        if out.get(key) is None and day_sc.get(key) is not None:
            out[key] = day_sc.get(key)
    return out


def _close_band_scan_rows(day: dict) -> List[dict]:
    scan = day.get("close_band_scan") if isinstance(day, dict) else None
    if not isinstance(scan, list):
        return []
    return [r for r in scan if isinstance(r, dict) and str(r.get("hm") or "").strip()]


def _scores_from_scan_row(row: dict) -> Dict[str, Any]:
    """扫描行 y_τ / y_path → 画像字段（该钟前缀，对齐拟合 by_tau）。"""
    sc: Dict[str, Any] = {}
    y_tau = row.get("y_tau")
    y_path = row.get("y_path")
    if y_tau is not None:
        sc["y_tau_portrait_oc"] = y_tau
        sc["y_tau_oc"] = y_tau
        sc["y_tau"] = y_tau
    if y_path is not None:
        sc["y_path_portrait"] = y_path
        sc["y_path"] = y_path
    return sc


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
        if filled is not None:
            sc = dict(filled.get("scores") or {}) if isinstance(filled.get("scores"), dict) else {}
            sc.update(scores)
            row = dict(filled)
            row["scores"] = sc
            row["hm"] = hm
            rows.append(row)
            continue
        rows.append(
            {
                "id": None,
                "hm": hm,
                "skipped": True,
                "scores": scores,
                "sold_qty": 0,
                "bought_qty": 0,
            }
        )
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
    if extra.get("y_tau_portrait_oc") is not None:
        out["y_tau_portrait_oc"] = extra["y_tau_portrait_oc"]
    if extra.get("y_path_portrait") is not None:
        out["y_path_portrait"] = extra["y_path_portrait"]
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
    """槽位画像样本：本轮 ŷ + 全日标签（τ/path/eod 真实值仍按日）。"""
    filled = _slot_row_filled(row)
    slot_sc = row.get("scores") if isinstance(row.get("scores"), dict) else {}
    return {
        "date": day.get("date"),
        "open": day.get("open"),
        "close": day.get("close"),
        "prev_close": day.get("prev_close"),
        "eod_realized": day.get("eod_realized"),
        "tau_realized": day.get("tau_realized"),
        "path_realized": day.get("path_realized"),
        "y_cx": day.get("y_cx"),
        "cx_realized": day.get("cx_realized"),
        "scores": _fill_day_eod_trade_scores(day, slot_sc),
        "direction_features": row.get("direction_features")
        if isinstance(row.get("direction_features"), dict)
        else {},
        "direction": row.get("direction"),
        "direction_score": (slot_sc or {}).get("y_tau")
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
    """无该钟明细：天级 label + 日级 eod/trade；τ/path 空 → 命中 flat。"""
    return {
        "date": day.get("date"),
        "open": day.get("open"),
        "close": day.get("close"),
        "prev_close": day.get("prev_close"),
        "eod_realized": day.get("eod_realized"),
        "tau_realized": day.get("tau_realized"),
        "path_realized": day.get("path_realized"),
        "y_cx": day.get("y_cx"),
        "cx_realized": day.get("cx_realized"),
        "scores": _fill_day_eod_trade_scores(day, {}),
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
    """与日级画像同一入样条件（有分/标签/成交/跳过）。"""
    if not isinstance(day, dict):
        return False
    sc = extract_scores(day)
    y_tau = resolve_y_tau_oc_for_portrait(sc)
    y_path = resolve_y_path_for_portrait(sc)
    tau_r = _pick_realized(day, "tau_realized")
    if tau_r is None:
        tau_r = _oc_realized_pct(day)
    path_r = _pick_realized(day, "path_realized")
    return bool(
        y_tau is not None
        or sc.get("y_tau") is not None
        or y_path is not None
        or tau_r is not None
        or path_r is not None
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
    """从画像单位列表累计 τ/path/eod/trade 标签与预估命中。"""
    label_tau = {"pos": 0, "neg": 0, "zero": 0, "missing": 0}
    label_path = {"pos": 0, "neg": 0, "zero": 0, "missing": 0}
    label_eod = {"pos": 0, "neg": 0, "zero": 0, "missing": 0}
    label_joint = {"same_sign": 0, "opposite_sign": 0, "flat": 0}
    pred_joint = {"same_sign": 0, "opposite_sign": 0, "flat": 0}
    tau_hit = {"hit": 0, "miss": 0, "flat": 0}
    path_hit = {"hit": 0, "miss": 0, "flat": 0}
    eod_hit = {"hit": 0, "miss": 0, "flat": 0}
    trade_hit = {"hit": 0, "miss": 0, "flat": 0}
    path_by_pred = {
        "pred_pos": {"hit": 0, "miss": 0, "flat": 0},
        "pred_neg": {"hit": 0, "miss": 0, "flat": 0},
    }
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
        y_tau = resolve_y_tau_oc_for_portrait(sc)
        y_path = resolve_y_path_for_portrait(sc)
        y_eod = resolve_y_eod_for_portrait(sc)
        y_trade = resolve_y_trade_for_portrait(sc)
        tau_r = _pick_realized(d, "tau_realized")
        if tau_r is None:
            tau_r = _oc_realized_pct(d)
        path_r = _pick_realized(d, "path_realized")
        eod_r = _eod_realized_pct(d)
        has_any = (
            y_tau is not None
            or sc.get("y_tau") is not None
            or y_path is not None
            or y_eod is not None
            or y_trade is not None
            or tau_r is not None
            or path_r is not None
            or eod_r is not None
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

        tb = _sign_bucket(tau_r)
        pb = _sign_bucket(path_r)
        eb = _sign_bucket(eod_r)
        _bump(label_tau, tb)
        _bump(label_path, pb)
        _bump(label_eod, eb)
        if tb in ("pos", "neg") and pb in ("pos", "neg"):
            _bump(label_joint, "same_sign" if tb == pb else "opposite_sign")
        else:
            _bump(label_joint, "flat")

        if (
            y_tau is not None
            and y_path is not None
            and abs(float(y_tau)) > 1e-9
            and abs(float(y_path)) > 1e-9
        ):
            _bump(
                pred_joint,
                "same_sign"
                if (float(y_tau) > 0) == (float(y_path) > 0)
                else "opposite_sign",
            )
        else:
            _bump(pred_joint, "flat")

        th = _sign_hit(y_tau, tau_r, pred_eps=0.05, real_eps=0.05)
        if th is True:
            _bump(tau_hit, "hit")
        elif th is False:
            _bump(tau_hit, "miss")
        else:
            _bump(tau_hit, "flat")

        ph = _sign_hit(y_path, path_r, pred_eps=1e-9, real_eps=1e-9)
        if path_r is not None and abs(float(path_r)) <= 1e-9:
            ph = None
        if ph is True:
            _bump(path_hit, "hit")
        elif ph is False:
            _bump(path_hit, "miss")
        else:
            _bump(path_hit, "flat")

        eh = _sign_hit(y_eod, eod_r, pred_eps=0.05, real_eps=0.05)
        if eh is True:
            _bump(eod_hit, "hit")
        elif eh is False:
            _bump(eod_hit, "miss")
        else:
            _bump(eod_hit, "flat")

        # y_trade 与涨跌同目标（close/prev_close−1）
        trh = _sign_hit(y_trade, eod_r, pred_eps=0.05, real_eps=0.05)
        if trh is True:
            _bump(trade_hit, "hit")
        elif trh is False:
            _bump(trade_hit, "miss")
        else:
            _bump(trade_hit, "flat")

        if y_path is not None and abs(float(y_path)) > 1e-9:
            side = "pred_pos" if float(y_path) > 0 else "pred_neg"
            if ph is True:
                _bump(path_by_pred[side], "hit")
            elif ph is False:
                _bump(path_by_pred[side], "miss")
            else:
                _bump(path_by_pred[side], "flat")

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

    label_signed = int(label_joint["same_sign"]) + int(label_joint["opposite_sign"])
    pred_signed = int(pred_joint["same_sign"]) + int(pred_joint["opposite_sign"])

    def _side_pack(side: Dict[str, int]) -> Dict[str, Any]:
        h, m = int(side["hit"]), int(side["miss"])
        return {
            **side,
            "n": h + m + int(side["flat"]),
            "hit_rate": _rate(h, m),
            "err_rate": round(m / float(h + m), 4) if (h + m) else None,
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
        "label_path": {
            **label_path,
            "n": sum(label_path.values()),
            "pos_share": _share(label_path["pos"], label_path["neg"]),
        },
        "label_eod": {
            **label_eod,
            "n": sum(label_eod.values()),
            "pos_share": _share(label_eod["pos"], label_eod["neg"]),
        },
        "label_joint": {
            **label_joint,
            "signed_n": label_signed,
            "same_sign_rate": _rate(label_joint["same_sign"], label_joint["opposite_sign"]),
        },
        "pred_joint": {
            **pred_joint,
            "signed_n": pred_signed,
            "same_sign_rate": _rate(pred_joint["same_sign"], pred_joint["opposite_sign"]),
        },
        "tau_hit": _hit_pack(tau_hit),
        "path_hit": _hit_pack(path_hit),
        "eod_hit": _hit_pack(eod_hit),
        "trade_hit": _hit_pack(trade_hit),
        "path_by_pred_sign": {
            "pred_pos": _side_pack(path_by_pred["pred_pos"]),
            "pred_neg": _side_pack(path_by_pred["pred_neg"]),
        },
        "note": note
        or (
            f"scope={scope_s}；τ↔tau_realized；path↔path_realized；"
            "eod/trade↔涨跌(close/prev_close−1)；含跳过日（有分/标签才计入）"
        ),
    }



def build_score_portrait(
    days: Sequence[dict],
    *,
    traded_only: bool = False,
) -> Dict[str, Any]:
    """回测日 τ/path 标签分布、ŷ 同号、预估命中。

    默认覆盖全部回测日（含跳过）；``traded_only=True`` 仅成交日。
    τ/path 命中优先用 **前 N 根因果画像分**（``y_tau_portrait_oc`` / ``y_path_portrait``），
    与做 T 固定前缀同口径；勿用开盘选向的 open-only 决策分去对全日标签。
    """
    units = [
        _day_with_scan_portrait(d)
        for d in (days or [])
        if isinstance(d, dict)
    ]
    return _build_score_portrait_from_units(units, traded_only=traded_only)


def build_score_portrait_by_slot(days: Sequence[dict]) -> Dict[str, Any]:
    """按做T时钟拆画像：各钟因果 ŷ vs 同一套天级 τ/path 标签（对齐拟合 by_tau）。

    优先 ``close_band_scan``（11:00 前每根 5m 前缀 ŷ）；无扫描时回退破带轮
    ``t0_slot_results``。样本与日级对齐；缺该钟 ŷ 计 flat。越晚前缀越长，命中通常上升。
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
            f"槽位 {hm}：本钟扫描前缀 ŷ↔全日 tau_realized/path_realized（同拟合 OOS.by_tau）；"
            "样本=与日级同样本；缺该钟ŷ计flat；成交子集=该钟已破带成交；"
            "越晚钟前缀越长，命中通常更高"
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
            if resolve_y_tau_oc_for_portrait(extract_scores(u)) is not None
            or resolve_y_path_for_portrait(extract_scores(u)) is not None
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
            "分槽位画像：各钟因果 ŷ_τ/path 对同一套天级标签（对齐拟合 by_tau）；"
            "优先 close_band_scan 每根 5m；无扫描才用破带开轮钟；"
            "样本与日级对齐；越晚钟信息越多、命中通常更高"
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
    keys_joint = ("same_sign", "opposite_sign", "flat")
    keys_hit = ("hit", "miss", "flat")
    label_tau = {k: 0 for k in keys_sign}
    label_path = {k: 0 for k in keys_sign}
    label_eod = {k: 0 for k in keys_sign}
    label_joint = {k: 0 for k in keys_joint}
    pred_joint = {k: 0 for k in keys_joint}
    tau_hit = {k: 0 for k in keys_hit}
    path_hit = {k: 0 for k in keys_hit}
    eod_hit = {k: 0 for k in keys_hit}
    trade_hit = {k: 0 for k in keys_hit}
    path_by_pred = {
        "pred_pos": {k: 0 for k in keys_hit},
        "pred_neg": {k: 0 for k in keys_hit},
    }
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
        for dst, src_key in (
            (label_tau, "label_tau"),
            (label_path, "label_path"),
            (label_eod, "label_eod"),
        ):
            src = part.get(src_key) if isinstance(part.get(src_key), dict) else {}
            for k in keys_sign:
                dst[k] += int(src.get(k) or 0)
        for dst, src_key in (
            (label_joint, "label_joint"),
            (pred_joint, "pred_joint"),
        ):
            src = part.get(src_key) if isinstance(part.get(src_key), dict) else {}
            for k in keys_joint:
                dst[k] += int(src.get(k) or 0)
        for dst, src_key in (
            (tau_hit, "tau_hit"),
            (path_hit, "path_hit"),
            (eod_hit, "eod_hit"),
            (trade_hit, "trade_hit"),
        ):
            src = part.get(src_key) if isinstance(part.get(src_key), dict) else {}
            for k in keys_hit:
                dst[k] += int(src.get(k) or 0)
        by = part.get("path_by_pred_sign") if isinstance(part.get("path_by_pred_sign"), dict) else {}
        for side in ("pred_pos", "pred_neg"):
            src = by.get(side) if isinstance(by.get(side), dict) else {}
            for k in keys_hit:
                path_by_pred[side][k] += int(src.get(k) or 0)

    def _rate(hit: int, miss: int) -> Optional[float]:
        denom = hit + miss
        return round(hit / float(denom), 4) if denom else None

    def _share(pos: int, neg: int) -> Optional[float]:
        denom = pos + neg
        return round(pos / float(denom), 4) if denom else None

    def _side_pack(side: Dict[str, int]) -> Dict[str, Any]:
        h, m = int(side["hit"]), int(side["miss"])
        return {
            **side,
            "n": h + m + int(side["flat"]),
            "hit_rate": _rate(h, m),
            "err_rate": round(m / float(h + m), 4) if (h + m) else None,
        }

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

    label_signed = label_joint["same_sign"] + label_joint["opposite_sign"]
    pred_signed = pred_joint["same_sign"] + pred_joint["opposite_sign"]
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
            "label_path": {
                **label_path,
                "n": sum(label_path.values()),
                "pos_share": _share(label_path["pos"], label_path["neg"]),
            },
            "label_eod": {
                **label_eod,
                "n": sum(label_eod.values()),
                "pos_share": _share(label_eod["pos"], label_eod["neg"]),
            },
            "label_joint": {
                **label_joint,
                "signed_n": label_signed,
                "same_sign_rate": _rate(label_joint["same_sign"], label_joint["opposite_sign"]),
            },
            "pred_joint": {
                **pred_joint,
                "signed_n": pred_signed,
                "same_sign_rate": _rate(pred_joint["same_sign"], pred_joint["opposite_sign"]),
            },
            "tau_hit": _hit_pack(tau_hit),
            "path_hit": _hit_pack(path_hit),
            "eod_hit": _hit_pack(eod_hit),
            "trade_hit": _hit_pack(trade_hit),
            "path_by_pred_sign": {
                "pred_pos": _side_pack(path_by_pred["pred_pos"]),
                "pred_neg": _side_pack(path_by_pred["pred_neg"]),
            },
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
    """y_τ 方向命中 × 做T方向 × gap 分桶归因（成交日）。"""
    by_dir: Dict[str, Dict[str, Any]] = {
        "sell_then_buy": {"n": 0, "pnl": 0.0, "hit": 0, "miss": 0, "flat": 0},
        "buy_then_sell": {"n": 0, "pnl": 0.0, "hit": 0, "miss": 0, "flat": 0},
    }
    by_gap: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "pnl": 0.0, "hit": 0, "miss": 0}
    )
    by_hit: Dict[str, Dict[str, Any]] = {
        "hit": {"n": 0, "pnl": 0.0},
        "miss": {"n": 0, "pnl": 0.0},
        "flat": {"n": 0, "pnl": 0.0},
    }
    samples: List[Dict[str, Any]] = []

    for d in days or []:
        if not isinstance(d, dict) or not is_traded_t0_day(d):
            continue
        for unit in iter_traded_attribution_units(d):
            sc = extract_scores(unit)
            y_tau = sc.get("y_tau")
            oc = _oc_realized_pct(unit)
            hit = _tau_oc_hit(y_tau, oc)
            direction = str(unit.get("direction") or unit.get("direction_used") or "")
            pnl = round(float(unit.get("pnl") or 0) + float(unit.get("exposure_pnl") or 0), 2)
            gap_bin = _gap_bucket(sc.get("gap_pct"))
            row = {
                "date": str(unit.get("date") or d.get("date") or "")[:10],
                "direction": direction,
                "y_tau": y_tau,
                "y_path": sc.get("y_path"),
                "gap_pct": sc.get("gap_pct"),
                "gap_bin": gap_bin,
                "oc_real_pct": oc,
                "tau_oc_hit": hit,
                "pnl": pnl,
                "t0_slot": unit.get("t0_slot"),
                "t0_slot_hm": unit.get("t0_slot_hm"),
            }
            samples.append(row)
            if direction in by_dir:
                by_dir[direction]["n"] += 1
                by_dir[direction]["pnl"] = round(by_dir[direction]["pnl"] + pnl, 2)
                if hit is True:
                    by_dir[direction]["hit"] += 1
                elif hit is False:
                    by_dir[direction]["miss"] += 1
                else:
                    by_dir[direction]["flat"] += 1
            gb = by_gap[gap_bin]
            gb["n"] += 1
            gb["pnl"] = round(gb["pnl"] + pnl, 2)
            if hit is True:
                gb["hit"] += 1
            elif hit is False:
                gb["miss"] += 1
            if hit is True:
                by_hit["hit"]["n"] += 1
                by_hit["hit"]["pnl"] = round(by_hit["hit"]["pnl"] + pnl, 2)
            elif hit is False:
                by_hit["miss"]["n"] += 1
                by_hit["miss"]["pnl"] = round(by_hit["miss"]["pnl"] + pnl, 2)
            else:
                by_hit["flat"]["n"] += 1
                by_hit["flat"]["pnl"] = round(by_hit["flat"]["pnl"] + pnl, 2)

    total_hit = by_hit["hit"]["n"]
    total_miss = by_hit["miss"]["n"]
    denom = total_hit + total_miss
    # |ŷ_τ| 分桶同号率（验收用；不依赖泄漏 y_on）
    abs_buckets = {
        "abs_ge_0_4": {"n": 0, "hit": 0, "miss": 0, "pnl": 0.0},
        "abs_ge_0_6": {"n": 0, "hit": 0, "miss": 0, "pnl": 0.0},
    }
    for row in samples:
        yt = row.get("y_tau")
        if yt is None:
            continue
        try:
            ap = abs(float(yt))
        except (TypeError, ValueError):
            continue
        hit = row.get("tau_oc_hit")
        pnl = float(row.get("pnl") or 0)
        for key, thr in (("abs_ge_0_4", 0.4), ("abs_ge_0_6", 0.6)):
            if ap >= thr:
                pack = abs_buckets[key]
                pack["n"] += 1
                pack["pnl"] = round(pack["pnl"] + pnl, 2)
                if hit is True:
                    pack["hit"] += 1
                elif hit is False:
                    pack["miss"] += 1
    for pack in abs_buckets.values():
        n_hm = int(pack["hit"]) + int(pack["miss"])
        pack["sign_hit"] = round(pack["hit"] / n_hm, 4) if n_hm else None
    summary = {
        "traded_with_tau": len(samples),
        "oc_hit_rate_pct": round(total_hit / denom * 100.0, 2) if denom else None,
        "pnl_hit": by_hit["hit"]["pnl"],
        "pnl_miss": by_hit["miss"]["pnl"],
        "pnl_flat": by_hit["flat"]["pnl"],
        "abs_buckets": abs_buckets,
    }
    gap_rows = [
        {"bin": b, **dict(by_gap.get(b) or {"n": 0, "pnl": 0.0, "hit": 0, "miss": 0})}
        for b in _gap_bucket_order()
        if (by_gap.get(b) or {}).get("n")
    ]
    return {
        "summary": summary,
        "by_direction": by_dir,
        "by_gap": gap_rows,
        "by_oc_hit": by_hit,
        "samples": samples[-80:],
    }


def _path_agreement(
    y_path: Optional[float],
    direction: str,
    *,
    y_tau: Optional[float] = None,
    path_enter: float = 0.02,
) -> Optional[str]:
    """成交日 y_τ 与 y_path 是否同号（准入口径）；缺 y_τ 时回退方向对照。"""
    if y_path is None:
        return None
    if y_tau is not None:
        from core.t0.score_policy import _tau_path_same_sign

        if _tau_path_same_sign(y_tau, y_path):
            return "agree"
        if abs(float(y_path)) <= float(path_enter) and abs(float(y_tau)) < float(path_enter):
            return "flat"
        return "disagree"
    thr = float(path_enter)
    if abs(float(y_path)) <= thr:
        return "flat"
    wants_sell_then_buy = float(y_path) > thr
    wants_buy_then_sell = float(y_path) < -thr
    if direction == "sell_then_buy":
        if wants_sell_then_buy:
            return "agree"
        if wants_buy_then_sell:
            return "disagree"
    elif direction == "buy_then_sell":
        if wants_buy_then_sell:
            return "agree"
        if wants_sell_then_buy:
            return "disagree"
    return None


def build_y_path_attribution(
    days: Sequence[dict],
    *,
    path_enter: float = 0.02,
) -> Dict[str, Any]:
    """y_path 联合选向归因：成交一致率 + path 相关跳过分类。"""
    path_skip_ids = (
        "y_path_flat",
        "y_path_disagree",
        "path_abandon",
        "gap_tier_skip",
    )
    skip_by_cat: Dict[str, int] = defaultdict(int)
    traded_agree = 0
    traded_disagree = 0
    traded_flat = 0
    traded_with_path = 0
    traded_no_path = 0
    pnl_agree = 0.0
    pnl_disagree = 0.0

    for d in days or []:
        if not isinstance(d, dict):
            continue
        slot_rows = _slot_result_rows(d)
        if slot_rows:
            for r in slot_rows:
                if not r.get("skipped"):
                    continue
                reason = str(r.get("reason") or "")
                cat = str(classify_t0_skip_reason(reason))
                if cat in path_skip_ids:
                    skip_by_cat[cat] += 1
        elif d.get("skipped"):
            reason = str(d.get("reason") or d.get("direction_reason") or "")
            cat = str(d.get("skip_category") or classify_t0_skip_reason(reason))
            if cat in path_skip_ids:
                skip_by_cat[cat] += 1
            continue
        if d.get("skipped"):
            continue
        if not is_traded_t0_day(d):
            continue
        for unit in iter_traded_attribution_units(d):
            sc = extract_scores(unit)
            y_path = sc.get("y_path")
            y_tau = sc.get("y_tau")
            if y_tau is None and isinstance(unit.get("direction_features"), dict):
                y_tau = unit["direction_features"].get("y_tau")
            direction = str(unit.get("direction") or "")
            pnl = round(float(unit.get("pnl") or 0) + float(unit.get("exposure_pnl") or 0), 2)
            if y_path is None:
                traded_no_path += 1
                continue
            traded_with_path += 1
            agree = _path_agreement(
                y_path, direction, y_tau=y_tau, path_enter=path_enter
            )
            if agree == "agree":
                traded_agree += 1
                pnl_agree = round(pnl_agree + pnl, 2)
            elif agree == "disagree":
                traded_disagree += 1
                pnl_disagree = round(pnl_disagree + pnl, 2)
            elif agree == "flat":
                traded_flat += 1

    denom = traded_agree + traded_disagree
    summary = {
        "traded_with_path": traded_with_path,
        "traded_no_path": traded_no_path,
        "path_agree_n": traded_agree,
        "path_disagree_n": traded_disagree,
        "path_flat_n": traded_flat,
        "path_agree_rate_pct": round(traded_agree / denom * 100.0, 2) if denom else None,
        "pnl_agree": pnl_agree,
        "pnl_disagree": pnl_disagree,
        "path_skip_days": sum(skip_by_cat.values()),
    }
    skip_rows = [
        {
            "id": cid,
            "label": SKIP_CAT_LABELS.get(cid, cid),
            "count": int(cnt),
        }
        for cid, cnt in sorted(skip_by_cat.items(), key=lambda x: (-x[1], x[0]))
    ]
    return {
        "summary": summary,
        "skip_by_category": skip_rows,
        "path_enter": path_enter,
    }


def _merge_y_path_attribution(
    parts: Sequence[Optional[dict]],
    *,
    path_enter: float = 0.02,
) -> Dict[str, Any]:
    """合并多票 y_path 归因块。"""
    skip_by_cat: Dict[str, int] = defaultdict(int)
    traded_agree = traded_disagree = traded_flat = 0
    traded_with_path = traded_no_path = 0
    pnl_agree = pnl_disagree = 0.0
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        sm = part.get("summary") if isinstance(part.get("summary"), dict) else {}
        traded_with_path += int(sm.get("traded_with_path") or 0)
        traded_no_path += int(sm.get("traded_no_path") or 0)
        traded_agree += int(sm.get("path_agree_n") or 0)
        traded_disagree += int(sm.get("path_disagree_n") or 0)
        traded_flat += int(sm.get("path_flat_n") or 0)
        pnl_agree = round(pnl_agree + float(sm.get("pnl_agree") or 0), 2)
        pnl_disagree = round(pnl_disagree + float(sm.get("pnl_disagree") or 0), 2)
        for row in part.get("skip_by_category") or []:
            if isinstance(row, dict) and row.get("id"):
                skip_by_cat[str(row["id"])] += int(row.get("count") or 0)
    denom = traded_agree + traded_disagree
    summary = {
        "traded_with_path": traded_with_path,
        "traded_no_path": traded_no_path,
        "path_agree_n": traded_agree,
        "path_disagree_n": traded_disagree,
        "path_flat_n": traded_flat,
        "path_agree_rate_pct": round(traded_agree / denom * 100.0, 2) if denom else None,
        "pnl_agree": pnl_agree,
        "pnl_disagree": pnl_disagree,
        "path_skip_days": sum(skip_by_cat.values()),
    }
    skip_rows = [
        {
            "id": cid,
            "label": SKIP_CAT_LABELS.get(cid, cid),
            "count": int(cnt),
        }
        for cid, cnt in sorted(skip_by_cat.items(), key=lambda x: (-x[1], x[0]))
    ]
    return {"summary": summary, "skip_by_category": skip_rows, "path_enter": path_enter}


def summarize_dual_y_upgrade_acceptance(
    days: Sequence[dict],
    *,
    rules: Optional[dict] = None,
) -> Dict[str, Any]:
    """同窗口验收：|τ|<enter 成交、τ 强桶同号、path 否决笔数（不依赖泄漏 y_on）。"""
    cfg = rules if isinstance(rules, dict) else {}
    try:
        enter = float(cfg.get("y_tau_enter") if cfg.get("y_tau_enter") is not None else 0.02)
    except (TypeError, ValueError):
        enter = 0.6
    # 旧双闸兼容：有效入场 = max(enter, strong)
    try:
        strong_legacy = cfg.get("y_tau_enter_strong")
        if strong_legacy is not None:
            enter = max(enter, float(strong_legacy))
    except (TypeError, ValueError):
        pass

    traded = [d for d in (days or []) if isinstance(d, dict) and is_traded_t0_day(d)]
    flat_trades = 0
    for d in traded:
        sc = extract_scores(d)
        yt = sc.get("y_tau")
        if yt is None:
            continue
        try:
            a = abs(float(yt))
        except (TypeError, ValueError):
            continue
        if a < enter:
            flat_trades += 1

    att = build_y_tau_attribution(days)
    att_sm = att.get("summary") if isinstance(att.get("summary"), dict) else {}
    abs_b = att_sm.get("abs_buckets") if isinstance(att_sm.get("abs_buckets"), dict) else {}
    strong_b = abs_b.get("abs_ge_0_6") if isinstance(abs_b.get("abs_ge_0_6"), dict) else {}

    path_skip = 0
    for d in days or []:
        if not isinstance(d, dict) or not d.get("skipped"):
            continue
        reason = str(d.get("reason") or d.get("direction_reason") or "")
        if classify_t0_skip_reason(reason) == "y_path_disagree":
            path_skip += 1

    ok = flat_trades == 0
    return {
        "ok": ok,
        "traded_n": len(traded),
        "weak_tau_trades": 0,  # 双闸已合并；保留键兼容旧前端
        "flat_tau_trades": flat_trades,
        "tau_oc_hit_rate_pct": att_sm.get("oc_hit_rate_pct"),
        "strong_bucket": strong_b,
        "path_disagree_skips": path_skip,
        "y_tau_enter": enter,
        "y_tau_enter_strong": enter,
        "blockers": (["存在 |τ|<enter 成交"] if flat_trades else []),
    }


def extract_y_tau(day: dict) -> Optional[float]:
    return extract_scores(day).get("y_tau")


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
                "eod_realized": day.get("eod_realized"),
                "tau_realized": day.get("tau_realized"),
                "path_realized": day.get("path_realized"),
                "y_cx": day.get("y_cx"),
                "cx_realized": day.get("cx_realized"),
                "direction": r.get("direction"),
                "pnl": r.get("pnl") or 0,
                "exposure_pnl": r.get("exposure_pnl") or 0,
                "scores": r.get("scores") or day.get("scores"),
                "direction_features": r.get("direction_features")
                or day.get("direction_features"),
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
    rules: Optional[dict],
) -> Dict[str, Any]:
    total = traded_n + skip_n
    cfg = rules if isinstance(rules, dict) else {}
    return {
        "total_days": total,
        "trade_count": traded_n,
        "skip_count": skip_n,
        "signal_skip_count": signal_skip_n,
        "participate_rate_pct": round(traded_n / total * 100.0, 2) if total else None,
        "signal_skip_rate_pct": round(signal_skip_n / total * 100.0, 2) if total else None,
        "cover_rate_pct": round(cover_n / traded_n * 100.0, 2) if traded_n else None,
        "score_coverage_pct": round(score_seen / score_total * 100.0, 2) if score_total else None,
        "y_tau_enter": _f(cfg.get("y_tau_enter")) or 0.25,
        "r_tau_enter": _f(cfg.get("r_tau_enter")) if cfg.get("r_tau_enter") is not None else 0.1,
        "y_trade_enter": _f(cfg.get("y_trade_enter") or cfg.get("y_trade_floor")) or 0.15,
        "y_trade_floor": _f(cfg.get("y_trade_floor") or cfg.get("y_trade_enter")) or 0.15,
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
            for unit in units:
                usc = extract_scores(unit)
                yt = usc.get("y_tau")
                if yt is None:
                    continue
                unit_tau = True
                y_tau_traded.append(float(yt))
                y_tau_hist[_y_tau_bucket(float(yt))] += 1
                y_tau_scatter.append(
                    {
                        "date": date,
                        "y_tau": yt,
                        "outcome": "traded",
                        "direction": unit.get("direction"),
                        "stock_code": code,
                        "stock_name": name,
                        "t0_slot_hm": unit.get("t0_slot_hm"),
                    }
                )
            if unit_tau:
                score_seen += 1
            elif sc.get("y_tau") is not None:
                score_seen += 1
                y_tau_traded.append(float(sc["y_tau"]))
                y_tau_hist[_y_tau_bucket(float(sc["y_tau"]))] += 1
                y_tau_scatter.append(
                    {
                        "date": date,
                        "y_tau": sc["y_tau"],
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
                    "y_tau": first_sc.get("y_tau"),
                    "y_path": first_sc.get("y_path"),
                    "gap_pct": first_sc.get("gap_pct"),
                    "y_eod": first_sc.get("y_eod"),
                    "y_trade": first_sc.get("y_trade"),
                    "y_on": first_sc.get("y_on"),
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
        yt = sc.get("y_tau")
        if yt is not None:
            score_seen += 1
            if d.get("signal_skip"):
                y_tau_skipped.append(float(yt))
                y_tau_hist[_y_tau_bucket(float(yt))] += 1
                y_tau_scatter.append(
                    {
                        "date": date,
                        "y_tau": yt,
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
    y_tau_attribution = build_y_tau_attribution(days)
    path_enter = 0.02
    if isinstance(rules, dict) and rules.get("y_path_enter") is not None:
        try:
            path_enter = float(rules.get("y_path_enter"))
        except (TypeError, ValueError):
            path_enter = 0.02
    y_path_attribution = build_y_path_attribution(days, path_enter=path_enter)
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
        rules=rules,
    )
    summary["score_seen"] = score_seen
    summary["score_total"] = score_total
    if y_tau_traded:
        summary["avg_y_tau_traded"] = round(sum(y_tau_traded) / len(y_tau_traded), 3)
    if y_tau_skipped:
        summary["avg_y_tau_signal_skip"] = round(sum(y_tau_skipped) / len(y_tau_skipped), 3)
    att_sm = y_tau_attribution.get("summary") if isinstance(y_tau_attribution.get("summary"), dict) else {}
    if att_sm.get("oc_hit_rate_pct") is not None:
        summary["tau_oc_hit_rate_pct"] = att_sm.get("oc_hit_rate_pct")
    path_sm = (
        y_path_attribution.get("summary")
        if isinstance(y_path_attribution.get("summary"), dict)
        else {}
    )
    if path_sm.get("path_agree_rate_pct") is not None:
        summary["path_agree_rate_pct"] = path_sm.get("path_agree_rate_pct")
    if path_sm.get("path_skip_days") is not None:
        summary["path_skip_days"] = path_sm.get("path_skip_days")

    upgrade_acc = summarize_dual_y_upgrade_acceptance(days, rules=rules)
    summary["upgrade_acceptance"] = upgrade_acc
    summary["score_portrait"] = score_portrait
    if score_portrait.get("tau_hit", {}).get("hit_rate_pct") is not None:
        summary["tau_pred_hit_rate_pct"] = score_portrait["tau_hit"]["hit_rate_pct"]
    if score_portrait.get("path_hit", {}).get("hit_rate_pct") is not None:
        summary["path_pred_hit_rate_pct"] = score_portrait["path_hit"]["hit_rate_pct"]
    if score_portrait.get("pred_joint", {}).get("same_sign_rate") is not None:
        summary["pred_same_sign_rate_pct"] = round(
            float(score_portrait["pred_joint"]["same_sign_rate"]) * 100.0, 2
        )
    if score_portrait.get("label_joint", {}).get("same_sign_rate") is not None:
        summary["label_same_sign_rate_pct"] = round(
            float(score_portrait["label_joint"]["same_sign_rate"]) * 100.0, 2
        )

    return {
        "skip_categories": skip_categories,
        "cumulative_pnl": cumulative,
        "daily_activity": activity,
        "y_tau_buckets": y_tau_buckets,
        "y_tau_attribution": y_tau_attribution,
        "y_path_attribution": y_path_attribution,
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
                yt = _f(tp.get("y_tau"))
                if yt is not None:
                    score_seen += 1
                    y_tau_traded.append(yt)
                score_total += 1
        for pt in p.get("y_tau_scatter") or []:
            if isinstance(pt, dict):
                y_tau_scatter.append(dict(pt))
                if pt.get("outcome") == "signal_skip" and _f(pt.get("y_tau")) is not None:
                    y_tau_skipped.append(float(pt["y_tau"]))
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
        rules=rules,
    )
    if y_tau_traded:
        summary["avg_y_tau_traded"] = round(sum(y_tau_traded) / len(y_tau_traded), 3)
    if y_tau_skipped:
        summary["avg_y_tau_signal_skip"] = round(sum(y_tau_skipped) / len(y_tau_skipped), 3)
    summary["score_seen"] = score_seen
    summary["score_total"] = max(score_total, traded_n + signal_skip_n)

    y_tau_attribution = build_y_tau_attribution(
        [tp for tp in trade_points if tp.get("date")]
    )
    att_sm = y_tau_attribution.get("summary") if isinstance(y_tau_attribution.get("summary"), dict) else {}
    if att_sm.get("oc_hit_rate_pct") is not None:
        summary["tau_oc_hit_rate_pct"] = att_sm.get("oc_hit_rate_pct")

    y_path_attribution = _merge_y_path_attribution(
        [p.get("y_path_attribution") for p in (payloads or []) if isinstance(p, dict)],
        path_enter=float((rules or {}).get("y_path_enter") or 0.02),
    )
    path_sm = (
        y_path_attribution.get("summary")
        if isinstance(y_path_attribution.get("summary"), dict)
        else {}
    )
    if path_sm.get("path_agree_rate_pct") is not None:
        summary["path_agree_rate_pct"] = path_sm.get("path_agree_rate_pct")
    if path_sm.get("path_skip_days") is not None:
        summary["path_skip_days"] = path_sm.get("path_skip_days")

    score_portrait = _merge_score_portraits(
        [p.get("score_portrait") for p in (payloads or []) if isinstance(p, dict)]
    )
    summary["score_portrait"] = score_portrait
    if score_portrait.get("tau_hit", {}).get("hit_rate_pct") is not None:
        summary["tau_pred_hit_rate_pct"] = score_portrait["tau_hit"]["hit_rate_pct"]
    if score_portrait.get("path_hit", {}).get("hit_rate_pct") is not None:
        summary["path_pred_hit_rate_pct"] = score_portrait["path_hit"]["hit_rate_pct"]
    if score_portrait.get("pred_joint", {}).get("same_sign_rate") is not None:
        summary["pred_same_sign_rate_pct"] = round(
            float(score_portrait["pred_joint"]["same_sign_rate"]) * 100.0, 2
        )
    if score_portrait.get("label_joint", {}).get("same_sign_rate") is not None:
        summary["label_same_sign_rate_pct"] = round(
            float(score_portrait["label_joint"]["same_sign_rate"]) * 100.0, 2
        )

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
        "y_tau_attribution": y_tau_attribution,
        "y_path_attribution": y_path_attribution,
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
