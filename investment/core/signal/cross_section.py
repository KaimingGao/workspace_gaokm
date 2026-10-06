"""横截面排序：对 watching 候选批量打分（P9.2）。仅收益分 ŷ。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Tuple

from core.signal.config import get_rank_defaults, load_signal_config
from core.signal.score_stock import score_stock
from core.watching.store import read_watching, refresh_watchlist


def rank_cross_section(
    codes: Optional[List[str]] = None,
    *,
    horizon_days: int = 3,
    limit: int = 10,
    min_score: Optional[float] = None,
    watching_path: Optional[str] = None,
    bypass_quality_gate: bool = False,
) -> Dict[str, Any]:
    """对候选列表做短线横截面排序，返回 Top N（按 predicted_score）。"""
    cfg = load_signal_config()
    defaults = get_rank_defaults(cfg)
    limit = max(1, min(int(limit or defaults["default_limit"]), 30))
    horizon_days = max(1, min(int(horizon_days or 3), 10))

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

    try:
        from core.signal.minute_prefetch import maybe_prefetch_for_tail_anomaly

        maybe_prefetch_for_tail_anomaly(list(codes), config=cfg)
    except Exception:
        logger.debug("tail_anomaly minute prefetch skipped", exc_info=True)

    scored_items: List[dict] = []
    rejected: List[dict] = []
    for raw in codes[:50]:
        result = score_stock(
            str(raw),
            horizon_days=horizon_days,
            bypass_quality_gate=bool(bypass_quality_gate),
        )
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
        clamp_rank_mode,
        rank_by_predicted_score,
    )
    from core.signal.return_score_store import load_return_model

    scoring_cfg = cfg.get("scoring") or {}
    rank_mode = clamp_rank_mode(scoring_cfg.get("rank_mode"))
    rank_meta: Dict[str, Any] = {
        "rank_mode": rank_mode,
        "oos_failed_excluded": 0,
        "cluster_mode": "off",
    }
    return_model, model_meta = load_return_model(prefer_active=True)
    if return_model is not None:
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

    has_pred = return_model is not None or any(
        it.get("predicted_score") is not None for it in scored_items
    )
    ranked: List[dict] = []
    if has_pred:
        min_pred = scoring_cfg.get("min_predicted_score")
        try:
            min_pred_f = float(min_pred) if min_pred is not None else None
        except (TypeError, ValueError):
            min_pred_f = None
        # S3 口径对齐：挂 dual_score（blend ŷ_trade + ŷ_τ），过 ŷ_oo floor + τ 闸，再按 blend 排序
        # 与 cross_section_batch 的排序/闸一致，避免跨入口产生不同榜单
        tau_gate_passes: Dict[str, Tuple[bool, Optional[str]]] = {}
        ranked_gated: List[Tuple[float, float, dict]] = []  # (blend, eod, item)
        import logging
        _cs_log = logging.getLogger(__name__)
        try:
            from core.signal.dual_score import (
                attach_dual_score_bulk,
                buy_passes_tau_gate,
                eod_gate_score_for_item,
                get_dual_score_cfg,
                rank_key_for_item,
            )
            _ds_cfg = get_dual_score_cfg(scoring_cfg.get("dual_score") if isinstance(scoring_cfg, dict) else None)
            scored_items = attach_dual_score_bulk(scored_items, config=_ds_cfg) or scored_items
            _dual_avail = True
        except Exception as _e:
            _dual_avail = False
            _cs_log.warning("rank_cross_section: attach_dual_score 失败，退回 predicted_score 路径：%s", _e)
        if _dual_avail:
            by_code = {str(it.get("stock_code") or "").strip(): it for it in scored_items}
            for it in scored_items:
                if it.get("hard_reject"):
                    continue
                eod_s = eod_gate_score_for_item(it)
                if eod_s is None:
                    continue
                if min_pred_f is not None and eod_s < min_pred_f:
                    continue
                _code = str(it.get("stock_code") or "").strip()
                ok, _tau_reason = buy_passes_tau_gate(it, config=_ds_cfg)
                tau_gate_passes[_code] = (ok, _tau_reason)
                if not ok:
                    continue
                rk = rank_key_for_item(it)
                if rk is None:
                    rk = eod_s
                ranked_gated.append((float(rk), float(eod_s), dict(it)))
            ranked_gated.sort(key=lambda x: x[0], reverse=True)
            for blend, eod_s, it in ranked_gated[:limit]:
                it["score"] = blend
                it["predicted_score"] = eod_s
                it["rank_mode"] = "blend_predicted_score"
                it.pop("heuristic_score", None)
                ranked.append(it)
            rank_meta["dual_score_aligned"] = True
            rank_meta["tau_gated_count"] = sum(1 for v in tau_gate_passes.values() if v[0])
        if not ranked:
            # 兼容回退：原 predicted_score 路径
            rank_meta["fallback"] = rank_meta.get("fallback") or "empty_after_dual_gate"
            picks = rank_by_predicted_score(
                scored_items, min_predicted_score=min_pred_f, top_k=limit
            )
            by_code = {str(it.get("stock_code") or "").strip(): it for it in scored_items}
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
