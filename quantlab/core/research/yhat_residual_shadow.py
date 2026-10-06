"""ŷ 行业残差（yhat_residual）影子对照：同截面排序 on/off。

不改组 β、不写 config；只比较 TopK 重叠与秩相关，供人审是否打开
``cross_section.yhat_residual``。
"""


import logging

logger = logging.getLogger(__name__)
import copy
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return x


def _rank_codes(
    items: Sequence[dict],
    *,
    score_key: str = "predicted_score",
    top_k: int = 10,
) -> List[Tuple[str, float]]:
    scored: List[Tuple[str, float]] = []
    for it in items or []:
        code = str(it.get("stock_code") or it.get("code") or "").strip()
        if not code:
            continue
        s = _f(it.get(score_key))
        if s is None and score_key != "score":
            s = _f(it.get("predicted_score_blend"))
        if s is None:
            s = _f(it.get("score"))
        if s is None:
            continue
        scored.append((code, float(s)))
    scored.sort(key=lambda x: x[1], reverse=True)
    k = max(1, int(top_k or 10))
    return scored[:k]


def _jaccard(a: Sequence[str], b: Sequence[str]) -> Optional[float]:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return None
    u = sa | sb
    if not u:
        return None
    return round(len(sa & sb) / len(u), 4)


def _spearman_ranks(
    order_a: Sequence[str], order_b: Sequence[str]
) -> Optional[float]:
    """对并集代码：用两榜名次做 Spearman（缺席赋末名+1）。"""
    codes = list(dict.fromkeys(list(order_a) + list(order_b)))
    if len(codes) < 3:
        return None
    n = len(codes)
    ra = {c: i + 1 for i, c in enumerate(order_a)}
    rb = {c: i + 1 for i, c in enumerate(order_b)}
    xs = [float(ra.get(c, n + 1)) for c in codes]
    ys = [float(rb.get(c, n + 1)) for c in codes]
    m = len(xs)
    mx = sum(xs) / m
    my = sum(ys) / m
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    dx = sum((a - mx) ** 2 for a in xs) ** 0.5
    dy = sum((b - my) ** 2 for b in ys) ** 0.5
    if dx < 1e-12 or dy < 1e-12:
        return None
    return round(num / (dx * dy), 4)


def compare_yhat_residual_shadow(
    items: Sequence[dict],
    *,
    top_k: int = 10,
    score_key: str = "predicted_score",
) -> Dict[str, Any]:
    """同批已打分条目：残差关 vs 开 → TopK / 秩相关。"""
    base = [dict(it) for it in (items or []) if isinstance(it, dict)]
    off_ranked = _rank_codes(base, score_key=score_key, top_k=top_k)
    off_codes = [c for c, _ in off_ranked]

    from core.signal.neutralize import residualize_rank_scores

    on_pack = residualize_rank_scores(
        copy.deepcopy(base),
        score_keys=[
            score_key,
            "predicted_score",
            "predicted_score_eod",
            "predicted_score_blend",
            "score",
        ],
        by="sector",
    )
    on_items = list(on_pack.get("items") or base)
    on_ranked = _rank_codes(on_items, score_key=score_key, top_k=top_k)
    on_codes = [c for c, _ in on_ranked]

    jac = _jaccard(off_codes, on_codes)
    spear = _spearman_ranks(off_codes, on_codes)
    changed = jac is not None and jac < 0.999

    winner = "tie"
    note = "两臂 TopK 几乎相同；开残差对排序影响小。"
    if changed and jac is not None:
        if jac < 0.5:
            winner = "divergent"
            note = (
                "残差开关后 TopK 重叠偏低；请用截面 IC/超额影子回测再决定是否打开 "
                "cross_section.yhat_residual。"
            )
        else:
            winner = "mild_shift"
            note = "有温和重排；可对照超额后再人审打开 yhat_residual。"

    return {
        "success": True,
        "ok": True,
        "task": "yhat_residual_shadow",
        "top_k": int(top_k),
        "score_key": score_key,
        "sample_count": len(base),
        "residual_meta": {
            k: on_pack.get(k)
            for k in ("applied", "touched_fields", "groups", "by", "note")
        },
        "arms": {
            "yhat_residual_off": {
                "top": [{"stock_code": c, "score": s} for c, s in off_ranked],
                "codes": off_codes,
            },
            "yhat_residual_on": {
                "top": [{"stock_code": c, "score": s} for c, s in on_ranked],
                "codes": on_codes,
            },
        },
        "compare": {
            "jaccard_topk": jac,
            "spearman_topk_ranks": spear,
            "overlap_n": len(set(off_codes) & set(on_codes)),
        },
        "winner": winner,
        "note": note,
        "promote_hint": (
            "优则人审改 signal_config.cross_section.yhat_residual=true；"
            "不自动写盘。"
        ),
    }
