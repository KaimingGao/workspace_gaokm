"""横截面批量打分：组合回测与 rank_cross_section 共用（P49）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from core.signal.config import load_signal_config
from core.signal.neutralize import apply_cross_section_neutralization
from core.signal.scorer import score_bars


def rank_scored_items(
    items: List[dict],
    *,
    min_score: float,
    score_key: str = "score",
) -> List[Tuple[str, float]]:
    picks: List[Tuple[str, float]] = []
    for item in items:
        raw = item.get(score_key, item.get("score"))
        try:
            score = float(raw or 0)
        except (TypeError, ValueError):
            continue
        if score >= min_score:
            code = str(item.get("stock_code") or "")
            if code:
                picks.append((code, score))
    picks.sort(key=lambda x: x[1], reverse=True)
    return picks


def score_and_rank_watching(
    entries: List[dict],
    *,
    min_score: float,
    config: Optional[dict] = None,
    neutralize: Optional[bool] = None,
    rank_mode: str = "predicted_score",
    return_model: Optional[Any] = None,
    return_models_by_code: Optional[Dict[str, Any]] = None,
    min_predicted_score: Optional[float] = None,
    min_return_score: Optional[float] = None,  # 旧名
    min_predicted_return: Optional[float] = None,  # 旧名
    allow_heuristic_baseline: bool = False,
) -> Tuple[List[Tuple[str, float]], Dict[str, Any]]:
    """
    对一批已算出的 signal_item 形条目做截面中性化（可选）并排序。

    默认仅 predicted_score（ŷ）。研究 OOS 可设 allow_heuristic_baseline=True，
    用 heuristic_score（人工线性加权 0–100）作对照基线臂。
    """
    from core.signal.return_score import (
        apply_predicted_scores,
        apply_predicted_scores_by_model,
        clamp_rank_mode,
        rank_by_predicted_score,
        resolve_research_rank_mode,
    )

    if min_predicted_score is None:
        min_predicted_score = min_return_score
    if min_predicted_score is None:
        min_predicted_score = min_predicted_return

    cfg = config or load_signal_config()
    cs_cfg = cfg.get("cross_section") or {}
    use_neutral = cs_cfg.get("neutralize", True) if neutralize is None else bool(neutralize)
    mode = (
        resolve_research_rank_mode(rank_mode)
        if allow_heuristic_baseline
        else clamp_rank_mode(rank_mode)
    )

    meta: Dict[str, Any] = {"applied": False, "rank_mode": mode}
    items = list(entries)

    # —— 研究对照基线：人工线性加权 ——
    if mode == "heuristic_score":
        for it in items:
            if it.get("heuristic_score") is None and it.get("score") is not None:
                try:
                    it["heuristic_score"] = float(it["score"])
                except (TypeError, ValueError):
                    pass
            it["rank_mode"] = "heuristic_score"
        if use_neutral:
            wmap = dict((cfg.get("weights") or {}))
            nmeta = apply_cross_section_neutralization(items, weights=wmap)
            items = list(nmeta.get("items") or items)
            # 中性化后 score 可能已重算；同步 heuristic_score
            for it in items:
                if it.get("score") is not None:
                    try:
                        it["heuristic_score"] = float(it["score"])
                    except (TypeError, ValueError):
                        pass
            meta["applied"] = bool(nmeta.get("applied"))
            meta["neutralize"] = {
                k: nmeta.get(k) for k in ("applied", "reason", "sample_count") if k in nmeta
            }
        picks = rank_scored_items(
            items, min_score=float(min_score), score_key="heuristic_score"
        )
        meta["items_by_code"] = {
            str(it.get("stock_code") or "").strip(): it
            for it in items
            if it.get("stock_code")
        }
        return picks, meta

    # —— 选股真源：predicted_score ——
    by_code = return_models_by_code or {}
    if by_code:
        items = apply_predicted_scores_by_model(
            items,
            by_code,
            write_rank_score=False,
            default_model=return_model,
        )
        mapped = sum(
            1
            for it in items
            if it.get("predicted_score") is not None
            and str(it.get("stock_code") or "").strip() in by_code
        )
        meta["return_model_source"] = "cluster_group_beta"
        meta["cluster_return_models"] = len(by_code)
        meta["cluster_predicted_mapped"] = mapped
        if return_model is not None:
            meta["return_model_fallback"] = {
                "sample_count": getattr(return_model, "sample_count", None),
                "fitted_as_of": getattr(return_model, "fitted_as_of", None),
            }
    elif return_model is not None:
        items = apply_predicted_scores(items, return_model, write_rank_score=False)
        meta["return_model_source"] = "global"
        meta["return_model"] = {
            "sample_count": getattr(return_model, "sample_count", None),
            "fitted_as_of": getattr(return_model, "fitted_as_of", None),
            "horizon_days": getattr(return_model, "horizon_days", None),
            "ridge_lambda": getattr(return_model, "ridge_lambda", None),
        }

    if use_neutral:
        meta["neutralize_skipped"] = "predicted_score_uses_factor_coefs"
    meta["rank_mode"] = mode

    has_preds = any(it.get("predicted_score") is not None for it in items)
    if not has_preds and return_model is None and not by_code:
        meta["predicted_score_fallback"] = "no_model"
        picks = []
    else:
        picks = rank_by_predicted_score(
            items, min_predicted_score=min_predicted_score
        )
        if not picks:
            meta["predicted_score_fallback"] = "empty_preds"
            picks = []

    meta["items_by_code"] = {
        str(it.get("stock_code") or "").strip(): it
        for it in items
        if it.get("stock_code")
    }
    return picks, meta

def score_bars_as_item(
    code: str,
    scored: dict,
    *,
    sector: Optional[str] = None,
    market_cap: Optional[float] = None,
) -> dict:
    item = {
        "stock_code": code,
        "score": scored.get("score"),
        # 研究 OOS 基线用；生产选股仍以 predicted_score 为准
        "heuristic_score": scored.get("score"),
        "sub_scores": scored.get("sub_scores") or {},
        "factor_contrib": scored.get("factor_contrib") or {},
        "reasons": scored.get("reasons") or [],
        "regime": scored.get("regime") or {},
        "hard_reject": scored.get("hard_reject"),
    }
    if sector is not None:
        item["sector"] = sector
    if market_cap is not None:
        item["market_cap"] = market_cap
    return item


def score_window_as_item(
    code: str,
    window: List[dict],
    *,
    horizon_days: int,
    quote: dict,
    index_bars: Optional[List[dict]] = None,
    config: Optional[dict] = None,
    fundamentals: Optional[dict] = None,
    sector: Optional[str] = None,
    market_cap: Optional[float] = None,
    required_factor_keys: Optional[List[str]] = None,
) -> Optional[dict]:
    scored = score_bars(
        window,
        horizon_days=horizon_days,
        quote=quote,
        index_bars=index_bars,
        config=config,
        fundamentals=fundamentals,
        required_factor_keys=required_factor_keys,
    )
    if scored.get("hard_reject"):
        return None
    mcap = market_cap
    if mcap is None and isinstance(fundamentals, dict):
        try:
            raw = fundamentals.get("market_cap")
            mcap = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            mcap = None
    sec = sector
    if sec is None:
        try:
            from core.portfolio_optimize import _sector_for, load_sector_map

            sec = _sector_for(code, load_sector_map())
        except Exception:
            sec = None
    return score_bars_as_item(code, scored, sector=sec, market_cap=mcap)
