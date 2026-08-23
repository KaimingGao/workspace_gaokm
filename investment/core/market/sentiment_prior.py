"""市场情绪 prior（ŷ 外）：连板溢价 / 炸板率退潮。"""


import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MARKET_SENTIMENT_REASON = "market_sentiment_prior_ebb"

DEFAULT_MARKET_SENTIMENT = {
    "mode": "gate",
    "premium_ebb_pct": 0.0,
    "broken_rate_high": 0.25,
    "scale_buy_pct": 0.55,
    "scale_holds": True,
    "block_high_board": True,
    "high_board_min": 3.0,
}


def get_market_sentiment_prior_cfg(config: Optional[dict] = None) -> Dict[str, Any]:
    if config is None:
        try:
            from core.signal.config import load_signal_config

            config = load_signal_config()
        except Exception:
            logger.debug("load signal config failed", exc_info=True)
            config = {}
    raw = dict(DEFAULT_MARKET_SENTIMENT)
    raw.update(dict((config or {}).get("market_sentiment_prior") or {}))
    mode = str(raw.get("mode") or "off").strip().lower()
    if mode in ("risk", "warn", "warning"):
        raw["mode"] = "risk"
    elif mode in ("gate", "block", "scale"):
        raw["mode"] = "gate"
    else:
        raw["mode"] = "off"
    try:
        raw["scale_buy_pct"] = max(0.0, min(float(raw.get("scale_buy_pct") or 0.55), 1.0))
    except (TypeError, ValueError):
        raw["scale_buy_pct"] = 0.55
    raw["scale_holds"] = bool(raw.get("scale_holds", True))
    raw["block_high_board"] = bool(raw.get("block_high_board", True))
    return raw


def build_market_sentiment_prior(
    sentiment_snap: Optional[dict],
    *,
    config: Optional[dict] = None,
    stock_code: Optional[str] = None,
    rev_limit_up_count: Optional[int] = None,
    rev_consecutive_limit: Optional[int] = None,
) -> Dict[str, Any]:
    """由涨停池统计构建情绪周期先验。"""
    cfg = get_market_sentiment_prior_cfg(config)
    mode = cfg["mode"]
    base = {
        "success": True,
        "role": "market_sentiment_prior",
        "mode": mode,
        "stock_code": stock_code,
        "active": False,
        "actions": [],
        "warnings": [],
        "snapshot": sentiment_snap,
        "predicted_score_unchanged": True,
    }
    if mode == "off" or not isinstance(sentiment_snap, dict):
        base["note"] = "市场情绪先验关闭或无快照"
        return base

    premium = sentiment_snap.get("limit_up_open_premium_pct")
    broken = sentiment_snap.get("broken_limit_rate")
    avg_board = sentiment_snap.get("avg_board_height")
    cycle_score = sentiment_snap.get("sentiment_cycle_score")

    premium_ebb = premium is not None and float(premium) <= float(cfg.get("premium_ebb_pct") or 0.0)
    broken_high = broken is not None and float(broken) >= float(cfg.get("broken_rate_high") or 0.25)
    cycle_weak = cycle_score is not None and float(cycle_score) < 40.0

    high_board_stock = False
    if cfg.get("block_high_board"):
        try:
            if rev_consecutive_limit is not None and float(rev_consecutive_limit) >= float(
                cfg.get("high_board_min") or 3.0
            ):
                high_board_stock = True
            if rev_limit_up_count is not None and int(rev_limit_up_count) >= 3:
                high_board_stock = True
        except (TypeError, ValueError):
            pass

    active = bool(premium_ebb or broken_high or cycle_weak)
    warnings: List[str] = []
    actions: List[Dict[str, Any]] = []

    if premium_ebb and premium is not None:
        warnings.append(f"情绪·连板溢价 {float(premium):+.2f}% 退潮")
    if broken_high and broken is not None:
        warnings.append(f"情绪·炸板率 {float(broken):.0%}")
    if cycle_weak and cycle_score is not None:
        warnings.append(f"情绪·周期分 {float(cycle_score):.0f}/100 偏弱")
    if avg_board is not None and float(avg_board) >= 3.0:
        warnings.append(f"情绪·市场均连板 {float(avg_board):.1f}")

    if active and mode == "gate":
        actions.append(
            {
                "type": "scale_buy",
                "reason": MARKET_SENTIMENT_REASON,
                "scale": cfg["scale_buy_pct"],
                "scale_pct": cfg["scale_buy_pct"],
                "scale_holds": cfg["scale_holds"],
            }
        )
        if cfg.get("scale_holds"):
            actions.append(
                {
                    "type": "scale_hold",
                    "scale": cfg["scale_buy_pct"],
                    "reason": MARKET_SENTIMENT_REASON,
                }
            )
    if high_board_stock and active and mode == "gate":
        actions.append(
            {
                "type": "block_new_buy",
                "reason": "market_sentiment_high_board",
            }
        )
        warnings.append("情绪·高位连板股接力退潮")

    base.update(
        {
            "active": active and bool(warnings or actions),
            "premium_ebb": premium_ebb,
            "broken_high": broken_high,
            "cycle_weak": cycle_weak,
            "high_board_stock": high_board_stock,
            "warnings": warnings,
            "actions": actions,
            "note": "市场情绪 prior；不进 sub_scores。",
        }
    )
    return base
