"""双层 ŷ：τ / nowcast 影子簿与重叠对照。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

from core.signal.dual_score.resolve import (
    resolve_predicted_score_tau,
)
from core.signal.nowcast_kf import nordhaus_from_nowcast_rows


def build_tau_shadow_book(
    eligible_rows: Sequence[Dict[str, Any]],
    *,
    max_names: int,
    eod_book: Optional[Sequence[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """同 EOD 入池集合按 ŷ_τ 降序截断；缺失 τ 排末。不改主簿。"""
    max_n = max(1, int(max_names or 1))
    rows: List[Dict[str, Any]] = []
    missing_tau = 0
    for r in eligible_rows or []:
        if not isinstance(r, dict):
            continue
        row = dict(r)
        y_t = resolve_predicted_score_tau(row)
        if y_t is None:
            missing_tau += 1
        row["_tau_rank_key"] = (
            float(y_t) if y_t is not None else float("-inf")
        )
        rows.append(row)
    rows.sort(key=lambda x: float(x.get("_tau_rank_key") or float("-inf")), reverse=True)
    book: List[Dict[str, Any]] = []
    for i, r in enumerate(rows[:max_n]):
        r.pop("_tau_rank_key", None)
        r["rank"] = i + 1
        r["rank_key"] = "predicted_score_tau"
        book.append(r)
    for r in rows[max_n:]:
        r.pop("_tau_rank_key", None)

    compare = compare_book_overlap(eod_book or [], book)
    meta = {
        "mode": "tau_shadow",
        "rank_key": "predicted_score_tau",
        "max_names": max_n,
        "eligible_count": len(rows),
        "missing_tau_count": missing_tau,
        "note": "A2 影子簿：同池按 ŷ_τ 重排；不驱动 execution / 纸面买入",
        "vs_eod_book": compare,
    }
    return book, meta


def build_nowcast_shadow_book(
    eligible_rows: Sequence[Dict[str, Any]],
    *,
    max_names: int,
    eod_book: Optional[Sequence[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """同 EOD 入池集合按 ŷ_nowcast 降序截断。不改主簿、不驱动买入。"""
    max_n = max(1, int(max_names or 1))
    rows: List[Dict[str, Any]] = []
    missing = 0
    for r in eligible_rows or []:
        if not isinstance(r, dict):
            continue
        row = dict(r)
        y_n = row.get("predicted_score_nowcast")
        if y_n is None:
            missing += 1
            key = float("-inf")
        else:
            try:
                key = float(y_n)
            except (TypeError, ValueError):
                missing += 1
                key = float("-inf")
        row["_nowcast_rank_key"] = key
        rows.append(row)
    rows.sort(
        key=lambda x: float(x.get("_nowcast_rank_key") or float("-inf")), reverse=True
    )
    book: List[Dict[str, Any]] = []
    for i, r in enumerate(rows[:max_n]):
        r.pop("_nowcast_rank_key", None)
        r["rank"] = i + 1
        r["rank_key"] = "predicted_score_nowcast"
        book.append(r)
    for r in rows[max_n:]:
        r.pop("_nowcast_rank_key", None)
    compare = compare_book_overlap(eod_book or [], book)
    nordhaus = nordhaus_from_nowcast_rows(rows)
    meta = {
        "mode": "nowcast_shadow",
        "rank_key": "predicted_score_nowcast",
        "max_names": max_n,
        "eligible_count": len(rows),
        "missing_nowcast_count": missing,
        "nordhaus_revision_slope": nordhaus,
        "note": "N3 nowcast 影子簿：Kalman 昨收口径重排；不驱动 execution / 纸面买入",
        "vs_eod_book": compare,
    }
    return book, meta


def nowcast_shadow_alerts(
    shadow_book: Sequence[Dict[str, Any]],
    shadow_meta: Optional[Dict[str, Any]] = None,
    *,
    decision_book: Optional[Sequence[Dict[str, Any]]] = None,
    max_top_divergent: int = 5,
) -> List[Dict[str, Any]]:
    """nowcast 影子闭环告警：比较影子簿(ŷ_nowcast)与决策簿(ŷ_trade/EOD)，对显著背离发告警。

    闭环用途：影子预测 → 对照实际决策 → 告警，供 strategy_monitor / 证据层消费。
    不改决策、不驱动买入；仅产告警列表。
    """
    alerts: List[Dict[str, Any]] = []
    meta = shadow_meta if isinstance(shadow_meta, dict) else {}
    compare = meta.get("vs_eod_book")
    if not isinstance(compare, dict) and decision_book is not None:
        compare = compare_book_overlap(decision_book, shadow_book)
    if not isinstance(compare, dict):
        return alerts

    jaccard = compare.get("jaccard")
    spearman = compare.get("spearman_shared")

    # 1) 重叠过低 — nowcast 与决策簿显著背离
    if jaccard is not None:
        if jaccard < 0.3:
            sev = "critical" if jaccard < 0.15 else "warning"
            alerts.append({
                "code": "nowcast_shadow_low_overlap",
                "severity": sev,
                "metric": "jaccard",
                "value": jaccard,
                "threshold": 0.3,
                "message": (
                    f"nowcast 影子簿与决策簿重叠 Jaccard={jaccard:.2f}，"
                    "intraday 信号与 EOD 决策显著背离"
                ),
            })

    # 2) 秩倒挂 — spearman 低/负
    if spearman is not None:
        if spearman < 0.3:
            sev = "critical" if spearman < 0.0 else "warning"
            alerts.append({
                "code": "nowcast_shadow_rank_inversion",
                "severity": sev,
                "metric": "spearman_shared",
                "value": spearman,
                "threshold": 0.3,
                "message": (
                    f"nowcast 与决策簿共享票秩 Spearman={spearman:.2f}，排序倒挂"
                ),
            })

    # 3) Nordhaus 修正斜率陡 — 预测快速修正，不确定性高
    nord = meta.get("nordhaus_revision_slope")
    if nord is not None:
        try:
            nord_f = float(nord)
            if abs(nord_f) > 0.5:
                alerts.append({
                    "code": "nowcast_revision_volatile",
                    "severity": "warning",
                    "metric": "nordhaus_revision_slope",
                    "value": nord_f,
                    "threshold": 0.5,
                    "message": (
                        f"nowcast Nordhaus 修正斜率 {nord_f:+.3f}，"
                        "预测快速修正、不确定性高"
                    ),
                })
        except (TypeError, ValueError):
            pass

    # 4) nowcast 覆盖不足 — missing 比例高，影子簿不可信
    missing = meta.get("missing_nowcast_count")
    eligible = meta.get("eligible_count")
    if missing is not None and eligible:
        try:
            miss_ratio = float(missing) / float(eligible)
            if miss_ratio > 0.3:
                alerts.append({
                    "code": "nowcast_coverage_low",
                    "severity": "warning",
                    "metric": "missing_ratio",
                    "value": round(miss_ratio, 3),
                    "threshold": 0.3,
                    "message": (
                        f"nowcast 缺失率 {miss_ratio:.1%}（{missing}/{eligible}），"
                        "影子簿不可信"
                    ),
                })
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    # 5) 高秩 nowcast 票缺席决策簿 — 背离信号点名
    only_tau = set(compare.get("only_tau") or [])
    if only_tau:
        shadow_top = [
            str(r.get("stock_code") or "").strip()
            for r in (shadow_book or [])[:max_top_divergent]
            if isinstance(r, dict)
        ]
        divergent = [c for c in shadow_top if c and c in only_tau]
        if divergent:
            alerts.append({
                "code": "nowcast_high_rank_absent",
                "severity": "info",
                "metric": "top_rank_absent",
                "value": divergent,
                "message": (
                    f"nowcast 高秩票 {','.join(divergent[:5])} 缺席决策簿，"
                    "intraday 与 EOD 信号背离"
                ),
            })
    return alerts


def compare_book_overlap(
    book_a: Sequence[Dict[str, Any]],
    book_b: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """两簿代码重叠与 Top 秩对照（A2 验收用）。"""

    def _codes(book: Sequence[Dict[str, Any]]) -> List[str]:
        out: List[str] = []
        for r in book or []:
            if not isinstance(r, dict):
                continue
            c = str(r.get("stock_code") or "").strip()
            if c:
                out.append(c)
        return out

    a = _codes(book_a)
    b = _codes(book_b)
    set_a, set_b = set(a), set(b)
    inter = set_a & set_b
    union = set_a | set_b
    jaccard = (len(inter) / len(union)) if union else None
    # 共享代码在两簿中的秩 Spearman（近似：秩差平方）
    rank_a = {c: i + 1 for i, c in enumerate(a)}
    rank_b = {c: i + 1 for i, c in enumerate(b)}
    shared = [c for c in a if c in rank_b]
    spearman = None
    if len(shared) >= 2:
        n = len(shared)
        d2 = sum((rank_a[c] - rank_b[c]) ** 2 for c in shared)
        spearman = round(1.0 - (6.0 * d2) / (n * (n * n - 1)), 4)
    return {
        "n_a": len(a),
        "n_b": len(b),
        "overlap": len(inter),
        "jaccard": round(jaccard, 4) if jaccard is not None else None,
        "spearman_shared": spearman,
        "only_eod": sorted(set_a - set_b)[:12],
        "only_tau": sorted(set_b - set_a)[:12],
    }

