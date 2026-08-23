"""人审写入 signal_config 的 M 层 prior（cross_market / merge_policy；不改 weights / 不进 ŷ）。"""


import json
import logging
import os
from typing import Any, Dict, Optional

from core.cross_market_prior import DEFAULT_CROSS_MARKET, get_cross_market_cfg
from core.io_atomic import atomic_write_json
from core.market.sentiment_prior import (
    DEFAULT_MARKET_SENTIMENT,
    get_market_sentiment_prior_cfg,
)
from core.regulatory_prior import (
    DEFAULT_IPO_DRAIN,
    DEFAULT_REGULATORY,
    get_ipo_drain_prior_cfg,
    get_regulatory_prior_cfg,
)
from core.sentiment_prior import normalize_prior_mode

logger = logging.getLogger(__name__)


def _config_path() -> str:
    from core.signal.config import get_signal_config_path

    return get_signal_config_path()


def read_market_prior_public(*, config: Optional[dict] = None) -> Dict[str, Any]:
    """供 API / UI 展示的 M 层 prior 配置快照。"""
    if config is None:
        try:
            from core.signal.config import load_signal_config

            config = load_signal_config()
        except Exception:
            logger.exception('unexpected error in read_market_prior_public')
            config = {}
    cm = get_cross_market_cfg(config)
    msp = get_market_sentiment_prior_cfg(config)
    reg = get_regulatory_prior_cfg(config)
    ipo = get_ipo_drain_prior_cfg(config)
    policy = dict((config or {}).get("market_prior_policy") or {})
    merge = str(policy.get("merge_mode") or "min_scale")
    if merge not in ("min_scale", "chain"):
        merge = "min_scale"
    return {
        "cross_market": {
            "mode": cm.get("mode"),
            "tech_drag_trigger_pct": float(cm.get("tech_drag_trigger_pct") or -1.5),
            "tech_drag_severe_pct": float(cm.get("tech_drag_severe_pct") or -3.0),
            "liquidity_stress_min": float(cm.get("liquidity_stress_min") or 1.0),
            "scale_buy_pct": float(cm.get("scale_buy_pct") or 0.5),
            "scale_holds": bool(cm.get("scale_holds", True)),
        },
        "market_sentiment_prior": {
            "mode": msp.get("mode"),
            "scale_buy_pct": float(msp.get("scale_buy_pct") or 0.55),
            "scale_holds": bool(msp.get("scale_holds", True)),
            "broken_rate_high": float(msp.get("broken_rate_high") or 0.25),
        },
        "regulatory_prior": {
            "mode": reg.get("mode"),
            "scale_buy_pct": float(reg.get("scale_buy_pct") or 0.5),
        },
        "ipo_drain_prior": {
            "mode": ipo.get("mode"),
            "scale_buy_pct": float(ipo.get("scale_buy_pct") or 0.45),
            "drain_ratio_high": float(ipo.get("drain_ratio_high") or 3.0),
        },
        "market_prior_policy": {"merge_mode": merge},
        "note": "M 层 prior · 不进 predicted_score；改 cross_market.mode 影响调仓旁路",
    }


def save_market_prior(
    *,
    cross_market_mode: Optional[str] = None,
    tech_drag_trigger_pct: Optional[float] = None,
    scale_buy_pct: Optional[float] = None,
    scale_holds: Optional[bool] = None,
    market_sentiment_mode: Optional[str] = None,
    market_sentiment_scale_buy_pct: Optional[float] = None,
    market_sentiment_scale_holds: Optional[bool] = None,
    regulatory_mode: Optional[str] = None,
    regulatory_scale_buy_pct: Optional[float] = None,
    ipo_drain_mode: Optional[str] = None,
    ipo_drain_scale_buy_pct: Optional[float] = None,
    ipo_drain_ratio_high: Optional[float] = None,
    merge_mode: Optional[str] = None,
    note: str = "",
) -> Dict[str, Any]:
    """写入 cross_market.* / market_sentiment_prior.* / market_prior_policy。"""
    from core.signal.config import load_signal_config

    path = _config_path()
    raw: Dict[str, Any] = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f) or {}

    cm = dict(DEFAULT_CROSS_MARKET)
    cm.update(dict(raw.get("cross_market") or {}))
    msp = dict(DEFAULT_MARKET_SENTIMENT)
    msp.update(dict(raw.get("market_sentiment_prior") or {}))
    reg = dict(DEFAULT_REGULATORY)
    reg.update(dict(raw.get("regulatory_prior") or {}))
    ipo = dict(DEFAULT_IPO_DRAIN)
    ipo.update(dict(raw.get("ipo_drain_prior") or {}))
    policy = dict(raw.get("market_prior_policy") or {})
    changed: Dict[str, Any] = {}

    if cross_market_mode is not None:
        m = normalize_prior_mode(cross_market_mode)
        cm["mode"] = m
        changed["cross_market.mode"] = m
    if tech_drag_trigger_pct is not None:
        trig = float(tech_drag_trigger_pct)
        cm["tech_drag_trigger_pct"] = trig
        changed["cross_market.tech_drag_trigger_pct"] = trig
    if scale_buy_pct is not None:
        scale = max(0.0, min(float(scale_buy_pct), 1.0))
        cm["scale_buy_pct"] = scale
        changed["cross_market.scale_buy_pct"] = scale
    if scale_holds is not None:
        cm["scale_holds"] = bool(scale_holds)
        changed["cross_market.scale_holds"] = bool(scale_holds)
    if market_sentiment_mode is not None:
        m = normalize_prior_mode(market_sentiment_mode)
        msp["mode"] = m
        changed["market_sentiment_prior.mode"] = m
    if market_sentiment_scale_buy_pct is not None:
        sc = max(0.0, min(float(market_sentiment_scale_buy_pct), 1.0))
        msp["scale_buy_pct"] = sc
        changed["market_sentiment_prior.scale_buy_pct"] = sc
    if market_sentiment_scale_holds is not None:
        msp["scale_holds"] = bool(market_sentiment_scale_holds)
        changed["market_sentiment_prior.scale_holds"] = bool(market_sentiment_scale_holds)
    if regulatory_mode is not None:
        m = normalize_prior_mode(regulatory_mode)
        reg["mode"] = m
        changed["regulatory_prior.mode"] = m
    if regulatory_scale_buy_pct is not None:
        sc = max(0.0, min(float(regulatory_scale_buy_pct), 1.0))
        reg["scale_buy_pct"] = sc
        changed["regulatory_prior.scale_buy_pct"] = sc
    if ipo_drain_mode is not None:
        m = normalize_prior_mode(ipo_drain_mode)
        ipo["mode"] = m
        changed["ipo_drain_prior.mode"] = m
    if ipo_drain_scale_buy_pct is not None:
        sc = max(0.0, min(float(ipo_drain_scale_buy_pct), 1.0))
        ipo["scale_buy_pct"] = sc
        changed["ipo_drain_prior.scale_buy_pct"] = sc
    if ipo_drain_ratio_high is not None:
        ipo["drain_ratio_high"] = max(0.0, float(ipo_drain_ratio_high))
        changed["ipo_drain_prior.drain_ratio_high"] = float(ipo["drain_ratio_high"])
    if merge_mode is not None:
        mm = str(merge_mode or "min_scale").strip().lower()
        if mm not in ("min_scale", "chain"):
            mm = "min_scale"
        policy["merge_mode"] = mm
        changed["market_prior_policy.merge_mode"] = mm

    if not changed:
        return {
            "success": False,
            "error": (
                "未提供可写字段（cross_market_mode / tech_drag_trigger_pct / "
                "scale_buy_pct / scale_holds / market_sentiment_mode / "
                "market_sentiment_scale_buy_pct / market_sentiment_scale_holds / "
                "regulatory_mode / regulatory_scale_buy_pct / "
                "ipo_drain_mode / ipo_drain_scale_buy_pct / ipo_drain_ratio_high / merge_mode）"
            ),
            "signal_config_weights_touched": False,
        }

    raw["cross_market"] = cm
    raw["market_sentiment_prior"] = msp
    raw["regulatory_prior"] = reg
    raw["ipo_drain_prior"] = ipo
    raw["market_prior_policy"] = policy
    atomic_write_json(path, raw)

    import core.signal.config as cfg_mod

    cfg_mod._cached = None
    load_signal_config(reload=True)

    try:
        from core.market.context import invalidate_market_context_cache

        invalidate_market_context_cache()
    except Exception:
        logger.debug("market context cache invalidate skipped", exc_info=True)

    return {
        "success": True,
        "task": "save_market_prior",
        "changed": changed,
        "market_prior": read_market_prior_public(),
        "path": path,
        "note": note or "仅改 M 层 prior / market_prior_policy；ŷ/weights 未触碰",
        "signal_config_weights_touched": False,
    }
