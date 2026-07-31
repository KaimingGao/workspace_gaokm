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
) -> List[Tuple[str, float]]:
    picks: List[Tuple[str, float]] = []
    for item in items:
        score = float(item.get("score") or 0)
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
) -> Tuple[List[Tuple[str, float]], Dict[str, Any]]:
    """
    对一批已算出的 signal_item 形条目做截面中性化（可选）并排序。
    entries 须含 stock_code / score / sub_scores。
    """
    cfg = config or load_signal_config()
    cs_cfg = cfg.get("cross_section") or {}
    use_neutral = cs_cfg.get("neutralize", True) if neutralize is None else bool(neutralize)

    meta: Dict[str, Any] = {"applied": False}
    items = list(entries)
    if use_neutral and items:
        neut = apply_cross_section_neutralization(
            items,
            weights=cfg.get("weights") or {},
            method=str(cs_cfg.get("method") or "zscore"),
            min_samples=int(cs_cfg.get("min_samples") or 3),
            zscore_scale=float(cs_cfg.get("zscore_scale") or 10.0),
            industry_residual=bool(cs_cfg.get("industry_residual")),
            size_residual=bool(cs_cfg.get("size_residual")),
            size_buckets=int(cs_cfg.get("size_buckets") or 3),
        )
        items = neut.get("items") or items
        meta = {k: v for k, v in neut.items() if k != "items"}

    return rank_scored_items(items, min_score=min_score), meta


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
        "sub_scores": scored.get("sub_scores") or {},
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
) -> Optional[dict]:
    scored = score_bars(
        window,
        horizon_days=horizon_days,
        quote=quote,
        index_bars=index_bars,
        config=config,
        fundamentals=fundamentals,
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
