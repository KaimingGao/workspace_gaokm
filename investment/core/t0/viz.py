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
    "y_tau_weak": "y_τ弱信号",
    "y_path_flat": "y_path横盘",
    "y_path_disagree": "y_τ↔y_path异号",
    "gap_tier_skip": "大缺口反向跳过",
    "path_abandon": "前缀无空间放弃",
    "prefix_segment": "固定前缀待确认",
    "y_trade_weak": "y_trade幅度不足",
    "eod_tau_disagree": "y_eod↔y_τ异号",
    "trade_tau_disagree": "y_trade↔y_τ异号",
    "trade_tau_sign": "异号跳过",
    "conflict": "旧冲突(已下线)",
    "amplitude": "振幅不足",
    "directional_amplitude": "方向振幅",
    "lot_size": "手数不足",
    "cash": "现金不足",
    "tplus1": "T+1无可卖",
    "path": "路径否决",
    "trigger_miss": "未触达",
    "intraday_legs_open": "盘中已落账",
    "other": "其它",
}

SKIP_CAT_COLORS: Dict[str, str] = {
    "missing_minute": "#94a3b8",
    "missing_scores": "#94a3b8",
    "y_path_missing": "#64748b",
    "y_eod_flat": "#f59e0b",
    "y_tau_flat": "#f59e0b",
    "y_tau_weak": "#fbbf24",
    "y_path_flat": "#d97706",
    "y_path_disagree": "#dc2626",
    "gap_tier_skip": "#ea580c",
    "path_abandon": "#a8a29e",
    "prefix_segment": "#9ca3af",
    "y_trade_weak": "#fb923c",
    "eod_tau_disagree": "#b91c1c",
    "trade_tau_disagree": "#c2410c",
    "trade_tau_sign": "#e11d48",
    "conflict": "#ef4444",
    "amplitude": "#64748b",
    "directional_amplitude": "#78716c",
    "lot_size": "#a78bfa",
    "cash": "#f472b6",
    "tplus1": "#c084fc",
    "path": "#6366f1",
    "trigger_miss": "#cbd5e1",
    "intraday_legs_open": "#94a3b8",
    "other": "#d1d5db",
}


def classify_t0_skip_reason(reason: Optional[str]) -> str:
    r = str(reason or "")
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
    if "y_τ" in r and "未过门槛" in r:
        return "y_tau_flat"
    if "y_eod" in r and "y_τ" in r and "异号" in r and "|y_eod|" in r:
        return "eod_tau_disagree"
    if "y_trade" in r and "y_τ" in r and "异号" in r and "|y_trade|" in r:
        return "trade_tau_disagree"
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
    if "放弃" in r and ("反T" in r or "正T" in r or "前缀" in r):
        return "path_abandon"
    # 固定前缀齐窗/后半阴阳占比（现口径）；旧「回落/反弹」文案保留兼容历史账本
    if (
        "待固定前缀" in r
        or "固定前缀后半" in r
        or "固定前缀确认关" in r
        or "固定前缀未过" in r
        or "待回落" in r
        or "待反弹" in r
        or "回落确认" in r
        or "反弹确认" in r
    ):
        return "prefix_segment"
    if "上移振幅" in r or "下移振幅" in r or "方向振幅" in r:
        return "directional_amplitude"
    if "振幅" in r:
        return "amplitude"
    if "T+1" in r or "可卖旧仓" in r or "无可卖" in r or ("可卖" in r and "锁定" in r):
        return "tplus1"
    if "不足1手" in r or "动仓不足" in r or ("手" in r and "不足" in r):
        return "lot_size"
    # 「分钟路径未触及…」优先归未触达，勿因含「路径」误入 path
    if "未触及" in r or "未触" in r:
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
            return "正T未触低吸"
        if "反T" in r:
            return "反T未触卖出"
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
    return {
        "y_tau": y_tau,
        "y_path": _f(feats.get("y_path")) if feats else _f(raw.get("y_path")),
        "y_eod": _f(feats.get("y_eod")) if feats else _f(raw.get("y_eod")),
        "y_trade": _f(feats.get("y_trade")) if feats else _f(raw.get("y_trade")),
        "y_on": _f(feats.get("y_on")) if feats else _f(raw.get("y_on")),
        "gap_pct": gap,
    }


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


def build_y_tau_attribution(days: Sequence[dict]) -> Dict[str, Any]:
    """y_τ 方向命中 × 做T方向 × gap 分桶归因（成交日）。"""
    by_dir: Dict[str, Dict[str, Any]] = {
        "long_t": {"n": 0, "pnl": 0.0, "hit": 0, "miss": 0, "flat": 0},
        "reverse_t": {"n": 0, "pnl": 0.0, "hit": 0, "miss": 0, "flat": 0},
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
        sc = extract_scores(d)
        y_tau = sc.get("y_tau")
        oc = _oc_realized_pct(d)
        hit = _tau_oc_hit(y_tau, oc)
        direction = str(d.get("direction") or d.get("direction_used") or "")
        pnl = round(float(d.get("pnl") or 0) + float(d.get("exposure_pnl") or 0), 2)
        gap_bin = _gap_bucket(sc.get("gap_pct"))
        row = {
            "date": str(d.get("date") or "")[:10],
            "direction": direction,
            "y_tau": y_tau,
            "y_path": sc.get("y_path"),
            "gap_pct": sc.get("gap_pct"),
            "gap_bin": gap_bin,
            "oc_real_pct": oc,
            "tau_oc_hit": hit,
            "pnl": pnl,
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
    wants_long = float(y_path) > thr
    wants_reverse = float(y_path) < -thr
    if direction == "long_t":
        if wants_long:
            return "agree"
        if wants_reverse:
            return "disagree"
    elif direction == "reverse_t":
        if wants_reverse:
            return "agree"
        if wants_long:
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
        if d.get("skipped"):
            reason = str(d.get("reason") or d.get("direction_reason") or "")
            cat = str(d.get("skip_category") or classify_t0_skip_reason(reason))
            if cat in path_skip_ids:
                skip_by_cat[cat] += 1
            continue
        if not is_traded_t0_day(d):
            continue
        sc = extract_scores(d)
        y_path = sc.get("y_path")
        y_tau = sc.get("y_tau")
        if y_tau is None and isinstance(d.get("direction_features"), dict):
            y_tau = d["direction_features"].get("y_tau")
        direction = str(d.get("direction") or d.get("direction_used") or "")
        pnl = round(float(d.get("pnl") or 0) + float(d.get("exposure_pnl") or 0), 2)
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


def _cover_completed(day: dict) -> Optional[bool]:
    sold = int(day.get("sold_qty") or 0)
    covered = int(day.get("covered_qty") or 0)
    bought = int(day.get("bought_qty") or 0)
    sold_back = int(day.get("sold_back_qty") or 0)
    direction = str(day.get("direction") or day.get("direction_used") or "")
    exit_reason = str(day.get("exit_reason") or "")
    if direction == "long_t" and sold > 0:
        if covered >= sold:
            return True
        # 反T主动放弃回补（含现金不足放弃）按设计计为完成侧
        if exit_reason in ("abandon_cover", "abandon_cover_cash"):
            return True
        return False
    if direction == "reverse_t" and bought > 0:
        if sold_back >= bought:
            return True
        # 正T允许隔夜多头时主动放弃卖旧仓，与反T abandon 对称计完成
        if exit_reason in ("abandon_cover", "abandon_cover_cash"):
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
    direction_split = {"long_t": 0, "reverse_t": 0}
    pnl_by_direction = {"long_t": 0.0, "reverse_t": 0.0}
    traded_n = 0
    skip_n = 0
    signal_skip_n = 0
    cover_n = 0
    score_seen = 0
    score_total = 0
    y_tau_traded: List[float] = []
    y_tau_skipped: List[float] = []

    for d in days or []:
        if not isinstance(d, dict):
            continue
        date = str(d.get("date") or "")[:10]
        if not date:
            continue
        code = str(d.get("stock_code") or stock_code or "")
        name = str(d.get("stock_name") or stock_name or "")
        sc = extract_scores(d)

        if is_traded_t0_day(d):
            traded_n += 1
            daily[date]["traded"] += 1
            pnl = round(float(d.get("pnl") or 0) + float(d.get("exposure_pnl") or 0), 2)
            direction = str(d.get("direction") or d.get("direction_used") or "")
            if direction in direction_split:
                direction_split[direction] += 1
            if direction in pnl_by_direction:
                pnl_by_direction[direction] = round(pnl_by_direction[direction] + pnl, 2)
            covered = _cover_completed(d)
            if covered:
                cover_n += 1
            if sc.get("y_tau") is not None:
                score_seen += 1
                y_tau_traded.append(float(sc["y_tau"]))
                y_tau_hist[_y_tau_bucket(float(sc["y_tau"]))] += 1
            score_total += 1
            cp = d.get("cover_policy") if isinstance(d.get("cover_policy"), dict) else {}
            trade_points.append(
                {
                    "date": date,
                    "pnl": pnl,
                    "direction": direction,
                    "stock_code": code,
                    "stock_name": name,
                    "y_tau": sc.get("y_tau"),
                    "y_path": sc.get("y_path"),
                    "gap_pct": sc.get("gap_pct"),
                    "y_eod": sc.get("y_eod"),
                    "y_trade": sc.get("y_trade"),
                    "y_on": sc.get("y_on"),
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
            if sc.get("y_tau") is not None:
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
            continue

        if not d.get("skipped"):
            continue
        skip_n += 1
        reason = str(d.get("reason") or d.get("direction_reason") or "跳过")
        cat = str(d.get("skip_category") or classify_t0_skip_reason(reason))
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
            "long_days": direction_split.get("long_t") or 0,
            "reverse_days": direction_split.get("reverse_t") or 0,
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

    return {
        "skip_categories": skip_categories,
        "cumulative_pnl": cumulative,
        "daily_activity": activity,
        "y_tau_buckets": y_tau_buckets,
        "y_tau_attribution": y_tau_attribution,
        "y_path_attribution": y_path_attribution,
        "y_tau_scatter": y_tau_scatter,
        "stock_contrib": stock_contrib,
        "direction_split": direction_split,
        "pnl_by_direction": {
            k: round(v, 2) for k, v in pnl_by_direction.items()
        },
        "trade_count": traded_n,
        "skip_count": skip_n,
        "upgrade_acceptance": upgrade_acc,
        "summary": summary,
    }


def merge_t0_viz_payloads(
    payloads: Sequence[Optional[dict]],
    *,
    compare: Optional[dict] = None,
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
    direction_split = {"long_t": 0, "reverse_t": 0}
    pnl_by_direction = {"long_t": 0.0, "reverse_t": 0.0}
    traded_n = 0
    skip_n = 0
    signal_skip_n = 0
    cover_n = 0
    score_seen = 0
    score_total = 0
    y_tau_traded: List[float] = []
    y_tau_skipped: List[float] = []

    for p in payloads or []:
        if not isinstance(p, dict):
            continue
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
        direction_split["long_t"] += int(ds.get("long_t") or 0)
        direction_split["reverse_t"] += int(ds.get("reverse_t") or 0)
        pbd = p.get("pnl_by_direction") or {}
        pnl_by_direction["long_t"] += float(pbd.get("long_t") or 0)
        pnl_by_direction["reverse_t"] += float(pbd.get("reverse_t") or 0)
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
        "y_tau_scatter": y_tau_scatter,
        "stock_contrib": stock_contrib,
        "direction_split": direction_split,
        "pnl_by_direction": {k: round(v, 2) for k, v in pnl_by_direction.items()},
        "trade_count": traded_n,
        "skip_count": skip_n,
        "summary": summary,
    }
    if isinstance(compare, dict) and compare:
        out["compare"] = compare
    return out


def attach_compare_to_viz(report: dict) -> None:
    """把回测报告中的对照指标写入 viz.summary / viz.compare。"""
    viz = report.get("viz")
    if not isinstance(viz, dict):
        return
    opt = report.get("optimistic_compare") or {}
    if opt.get("t0_pnl_total") is not None:
        viz["compare"] = {
            "actual_pnl": report.get("t0_pnl_total"),
            "optimistic_pnl": opt.get("t0_pnl_total"),
            "delta_pnl": opt.get("delta_pnl"),
            "delta_ratio_pct": opt.get("delta_pnl_ratio_pct") or report.get("optimistic_delta_ratio_pct"),
        }
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
