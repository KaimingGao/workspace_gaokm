"""横截面排序：对 watching 候选批量打分（P9.2）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.signal.config import get_rank_defaults, load_signal_config
from core.signal.neutralize import apply_cross_section_neutralization
from core.signal.scorer import rank_candidates
from core.signal.score_stock import score_stock
from core.watching_store import read_watching, refresh_watchlist


def rank_cross_section(
    codes: Optional[List[str]] = None,
    *,
    horizon_days: int = 3,
    limit: int = 10,
    min_score: Optional[float] = None,
    watching_path: Optional[str] = None,
) -> Dict[str, Any]:
    """对候选列表做短线横截面排序，返回 Top N。"""
    cfg = load_signal_config()
    defaults = get_rank_defaults(cfg)
    if min_score is None:
        min_score = defaults["min_score"]
    limit = max(1, min(int(limit or defaults["default_limit"]), 30))
    horizon_days = max(1, min(int(horizon_days or 3), 3))

    if codes is None:
        try:
            uni = read_watching(watching_path)
        except FileNotFoundError:
            return {
                "success": False,
                "error": "watching.json 不存在，请先 init/refresh",
            }
        codes = uni.get("watchlist") or []
        if not codes:
            refreshed = refresh_watchlist(uni, path=watching_path)
            codes = refreshed.get("watchlist") or []

    if not codes:
        return {"success": False, "error": "候选池为空"}

    scored_items: List[dict] = []
    rejected: List[dict] = []
    for raw in codes[:50]:
        result = score_stock(str(raw), horizon_days=horizon_days)
        if not result.get("success"):
            rejected.append({"stock_code": raw, "reason": result.get("error")})
            continue
        item = result["signal_item"]
        if item.get("hard_reject"):
            rejected.append(
                {
                    "stock_code": item.get("stock_code"),
                    "stock_name": item.get("stock_name"),
                    "reason": item.get("reject_reason"),
                }
            )
            continue
        scored_items.append(item)

    neutralization: Dict[str, Any] = {"applied": False}
    cs_cfg = cfg.get("cross_section") or {}
    if cs_cfg.get("neutralize", True) and scored_items:
        neut = apply_cross_section_neutralization(
            scored_items,
            weights=cfg.get("weights") or {},
            method=str(cs_cfg.get("method") or "zscore"),
            min_samples=int(cs_cfg.get("min_samples") or 3),
            zscore_scale=float(cs_cfg.get("zscore_scale") or 10.0),
            industry_residual=bool(cs_cfg.get("industry_residual")),
            size_residual=bool(cs_cfg.get("size_residual")),
            size_buckets=int(cs_cfg.get("size_buckets") or 3),
        )
        scored_items = neut.get("items") or scored_items
        neutralization = {k: v for k, v in neut.items() if k != "items"}

    ranked = rank_candidates(
        scored_items,
        limit=limit,
        min_score=float(min_score),
        config=cfg,
    )
    if not ranked and scored_items:
        scored_items.sort(key=lambda x: x.get("score") or 0, reverse=True)
        ranked = scored_items[:limit]

    return {
        "success": True,
        "horizon_days": horizon_days,
        "candidate_count": len(codes),
        "ranked_count": len(ranked),
        "min_score": min_score,
        "ranking": ranked,
        "rejected_sample": rejected[:8],
        "neutralization": neutralization,
        "note": (
            "横截面排序基于 score_bars"
            + ("（截面中性化后重加权）" if neutralization.get("applied") else "")
            + "，供观察池/纸面参考，不保证收益。"
        ),
    }
