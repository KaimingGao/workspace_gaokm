"""监管公告 prior + IPO 虹吸 prior（ŷ 外）。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

REGULATORY_REASON = "regulatory_prior_cooling"
IPO_DRAIN_REASON = "ipo_drain_prior"

DEFAULT_REGULATORY = {
    "mode": "gate",
    "scale_buy_pct": 0.5,
    "concept_penalty_tags": ["机器人", "人形机器人", "半导体", "芯片", "人工智能"],
}

DEFAULT_IPO_DRAIN = {
    "mode": "gate",
    "drain_ratio_high": 3.0,
    "scale_buy_pct": 0.45,
    "concept_tags": ["机器人", "人形机器人", "人工智能", "AI"],
}


def get_regulatory_prior_cfg(config: Optional[dict] = None) -> Dict[str, Any]:
    if config is None:
        try:
            from core.signal.config import load_signal_config

            config = load_signal_config()
        except Exception:
            config = {}
    raw = dict(DEFAULT_REGULATORY)
    raw.update(dict((config or {}).get("regulatory_prior") or {}))
    mode = str(raw.get("mode") or "off").strip().lower()
    raw["mode"] = "gate" if mode in ("gate", "block", "scale") else ("risk" if mode in ("risk", "warn") else "off")
    return raw


def get_ipo_drain_prior_cfg(config: Optional[dict] = None) -> Dict[str, Any]:
    if config is None:
        try:
            from core.signal.config import load_signal_config

            config = load_signal_config()
        except Exception:
            config = {}
    raw = dict(DEFAULT_IPO_DRAIN)
    raw.update(dict((config or {}).get("ipo_drain_prior") or {}))
    mode = str(raw.get("mode") or "off").strip().lower()
    raw["mode"] = "gate" if mode in ("gate", "block", "scale") else ("risk" if mode in ("risk", "warn") else "off")
    return raw


def build_regulatory_prior(
    announcement: Optional[dict],
    *,
    config: Optional[dict] = None,
    stock_code: Optional[str] = None,
    sector: Optional[str] = None,
) -> Dict[str, Any]:
    cfg = get_regulatory_prior_cfg(config)
    mode = cfg["mode"]
    base = {
        "success": True,
        "role": "regulatory_prior",
        "mode": mode,
        "stock_code": stock_code,
        "active": False,
        "actions": [],
        "warnings": [],
        "predicted_score_unchanged": True,
    }
    if mode == "off" or not isinstance(announcement, dict):
        return base

    reg = announcement.get("regulatory") or {}
    hits = list(reg.get("hits") or [])
    concept_tags = list(reg.get("concept_tags") or [])
    penalty_codes = set(str(c)[-6:] for c in (reg.get("penalty_codes") or []) if c)
    code_norm = str(stock_code or "")[-6:]
    code_hit = code_norm in penalty_codes or any(
        str(h.get("stock_code") or "")[-6:] == code_norm
        for h in hits
        if stock_code and h.get("stock_code")
    )
    sector_tags = list(cfg.get("concept_penalty_tags") or [])
    sector_hit = bool(sector and any(t in str(sector) for t in sector_tags))
    concept_hit = any(t in concept_tags for t in sector_tags)
    graph_hit = False
    if stock_code and reg.get("concept_graph_built"):
        try:
            from skills.announcement.concept_graph import stock_in_penalty_concepts

            graph_hit = stock_in_penalty_concepts(
                stock_code,
                reg,
                fallback_tags=sector_tags,
            )
        except Exception:
            logger.debug("concept graph prior lookup failed", exc_info=True)

    active = bool(reg.get("active")) and (
        code_hit or sector_hit or concept_hit or graph_hit
    )
    warnings: List[str] = []
    actions: List[Dict[str, Any]] = []
    if active:
        warnings.append(f"监管·公告降温 {len(hits)} 条")
        if concept_hit:
            warnings.append(f"监管·概念标签 {', '.join(concept_tags[:5])}")
        if graph_hit:
            graph = (reg.get("penalty_concept_graph") or {}).get(code_norm) or []
            cc = (reg.get("code_concepts") or {}).get(code_norm) or []
            tags = graph or cc
            if tags:
                warnings.append(f"监管·概念成分 {', '.join(tags[:4])}")
        if mode == "gate":
            actions.append(
                {
                    "type": "scale_buy",
                    "reason": REGULATORY_REASON,
                    "scale": float(cfg.get("scale_buy_pct") or 0.5),
                    "scale_pct": float(cfg.get("scale_buy_pct") or 0.5),
                }
            )
            actions.append(
                {
                    "type": "scale_hold",
                    "scale": float(cfg.get("scale_buy_pct") or 0.5),
                    "reason": REGULATORY_REASON,
                }
            )
            if code_hit:
                actions.append({"type": "block_new_buy", "reason": REGULATORY_REASON})

    base.update({"active": active, "warnings": warnings, "actions": actions})
    return base


def build_ipo_drain_prior(
    announcement: Optional[dict],
    *,
    config: Optional[dict] = None,
    stock_code: Optional[str] = None,
    sector: Optional[str] = None,
) -> Dict[str, Any]:
    cfg = get_ipo_drain_prior_cfg(config)
    mode = cfg["mode"]
    base = {
        "success": True,
        "role": "ipo_drain_prior",
        "mode": mode,
        "stock_code": stock_code,
        "active": False,
        "actions": [],
        "warnings": [],
        "predicted_score_unchanged": True,
    }
    if mode == "off" or not isinstance(announcement, dict):
        return base

    ipo = announcement.get("ipo") or {}
    extreme = bool(ipo.get("extreme_ipo_day"))
    ratio = ipo.get("liquidity_drain_ratio")
    if ratio is None:
        ratio = ipo.get("liquidity_drain_ratio_proxy")
    ratio_high = ratio is not None and float(ratio) >= float(cfg.get("drain_ratio_high") or 3.0)
    max_mv = ipo.get("max_ipo_market_cap")
    mega_ipo = max_mv is not None and float(max_mv) >= float(cfg.get("mega_ipo_cap") or 100_000_000_000)
    concept_tags = list(cfg.get("concept_tags") or [])
    sector_hit = bool(sector and any(t in str(sector) for t in concept_tags))
    drain_by_concept = dict(ipo.get("drain_ratios_by_concept") or {})
    high_drain = {
        c
        for c, r in drain_by_concept.items()
        if r is not None and float(r) >= float(cfg.get("drain_ratio_high") or 3.0)
    }
    concept_hit = False
    code_norm = str(stock_code or "")[-6:]
    if code_norm:
        idx = announcement.get("concept_index") or (
            (announcement.get("regulatory") or {}).get("code_concepts") or {}
        )
        stock_concepts = set(idx.get(code_norm) or [])
        if stock_concepts and high_drain:
            concept_hit = bool(stock_concepts & high_drain)
        elif stock_concepts and (extreme or ratio_high or mega_ipo):
            concept_hit = bool(stock_concepts & set(concept_tags))

    active = bool(extreme or ratio_high or mega_ipo) and (sector_hit or concept_hit)
    warnings: List[str] = []
    actions: List[Dict[str, Any]] = []
    if active:
        warnings.append("IPO·超级新股虹吸同板块流动性")
        if max_mv is not None:
            warnings.append(f"IPO·估算市值 {float(max_mv)/1e8:.0f} 亿")
        if ratio is not None:
            warnings.append(f"IPO· drain_ratio≈{float(ratio):.1f}x")
        if mode == "gate":
            actions.append(
                {
                    "type": "scale_buy",
                    "reason": IPO_DRAIN_REASON,
                    "scale": float(cfg.get("scale_buy_pct") or 0.45),
                    "scale_pct": float(cfg.get("scale_buy_pct") or 0.45),
                }
            )
            actions.append(
                {
                    "type": "scale_hold",
                    "scale": float(cfg.get("scale_buy_pct") or 0.45),
                    "reason": IPO_DRAIN_REASON,
                }
            )

    base.update({"active": active, "warnings": warnings, "actions": actions, "ipo": ipo})
    return base
