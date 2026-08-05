"""横截面排序：对 watching 候选批量打分（P9.2）。仅收益分 ŷ。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.signal.config import get_rank_defaults, load_signal_config
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
    """对候选列表做短线横截面排序，返回 Top N（按 predicted_score）。"""
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

    from core.signal.return_score import (
        apply_predicted_scores,
        apply_predicted_scores_by_model,
        clamp_rank_mode,
        rank_by_predicted_score,
    )
    from core.signal.return_score_store import load_return_model

    scoring_cfg = cfg.get("scoring") or {}
    rank_mode = clamp_rank_mode(scoring_cfg.get("rank_mode"))
    rank_meta: Dict[str, Any] = {"rank_mode": rank_mode}
    cluster_models: Dict[str, Any] = {}
    try:
        from core.signal.cluster_live import (
            cluster_yhat_primary_allowed,
            get_cluster_scoring_cfg,
            load_cluster_return_models_by_code,
        )

        # FH0：仅 active 用组 β 驱动截面主分；off/shadow 不注入 by_code 图
        cs = get_cluster_scoring_cfg(cfg)
        rank_meta["cluster_mode"] = cs.get("mode")
        if cluster_yhat_primary_allowed(str(cs.get("mode") or "off")):
            cluster_models = load_cluster_return_models_by_code()
    except Exception:
        cluster_models = {}
    return_model, model_meta = load_return_model(prefer_active=True)
    if cluster_models:
        scored_items = apply_predicted_scores_by_model(
            scored_items,
            cluster_models,
            write_rank_score=False,
            default_model=return_model,
        )
        rank_meta["return_model_source"] = "cluster_group_beta"
        rank_meta["cluster_return_models"] = len(cluster_models)
        if return_model is not None:
            rank_meta["global_return_model"] = model_meta
    elif return_model is not None:
        scored_items = apply_predicted_scores(
            scored_items, return_model, write_rank_score=False
        )
        rank_meta["return_model_source"] = "global"
        rank_meta["return_model"] = model_meta
    else:
        rank_meta["fallback"] = "no_model"

    neutralization: Dict[str, Any] = {
        "applied": False,
        "skipped": True,
        "reason": "predicted_score_uses_factor_coefs",
    }

    has_pred = bool(cluster_models) or return_model is not None or any(
        it.get("predicted_score") is not None for it in scored_items
    )
    ranked: List[dict] = []
    if has_pred:
        min_pred = scoring_cfg.get("min_predicted_score")
        try:
            min_pred_f = float(min_pred) if min_pred is not None else None
        except (TypeError, ValueError):
            min_pred_f = None
        picks = rank_by_predicted_score(
            scored_items, min_predicted_score=min_pred_f, top_k=limit
        )
        by_code = {
            str(it.get("stock_code") or "").strip(): it for it in scored_items
        }
        for code, yhat in picks:
            it = dict(by_code.get(code) or {})
            it["score"] = yhat
            it["predicted_score"] = yhat
            it["rank_mode"] = "predicted_score"
            it.pop("heuristic_score", None)
            ranked.append(it)
        if not ranked:
            rank_meta["fallback"] = "empty_preds"
            ranked = [
                dict(it)
                for it in scored_items
                if it.get("predicted_score") is not None and not it.get("hard_reject")
            ]
            ranked.sort(
                key=lambda x: float(x.get("predicted_score") or 0.0), reverse=True
            )
            ranked = ranked[:limit]
            for it in ranked:
                it["score"] = it.get("predicted_score")
                it["rank_mode"] = "predicted_score"
                it.pop("heuristic_score", None)
    else:
        rank_meta["fallback"] = rank_meta.get("fallback") or "no_model"

    return {
        "success": True,
        "horizon_days": horizon_days,
        "candidate_count": len(codes),
        "ranked_count": len(ranked),
        "min_score": min_score,
        "ranking": ranked,
        "rejected_sample": rejected[:8],
        "neutralization": neutralization,
        "rank_meta": rank_meta,
        "note": "横截面排序基于 predicted_score（收益分 ŷ）；无模型则空榜，不保证收益。",
    }
