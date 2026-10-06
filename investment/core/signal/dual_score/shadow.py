"""双层 ŷ：τ 影子簿与重叠对照。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

from core.signal.dual_score.resolve import (
    resolve_predicted_score_tau,
)


def build_tau_shadow_book(
    eligible_rows: Sequence[Dict[str, Any]],
    *,
    max_names: int,
    oo_book: Optional[Sequence[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """同 oo 入池集合按 ŷ_τ 降序截断；缺失 τ 排末。不改主簿。"""
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

    compare = compare_book_overlap(oo_book or [], book)
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

