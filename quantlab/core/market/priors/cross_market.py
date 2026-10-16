"""跨市场 prior（ŷ 外）：海外科技拖累 + 流动性收紧。"""


import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CROSS_MARKET_REASON = "cross_market_prior_risk"

DEFAULT_CROSS_MARKET = {
    "mode": "gate",  # off | risk | gate
    "tech_drag_trigger_pct": -1.5,
    "tech_drag_severe_pct": -3.0,
    "liquidity_stress_min": 1.0,
    "scale_buy_pct": 0.5,
    "scale_holds": True,
    "sector_penalty_tags": ["半导体", "芯片", "通信", "电子", "计算机", "机器人", "人工智能"],
}


def get_cross_market_cfg(config: Optional[dict] = None) -> Dict[str, Any]:
    if config is None:
        try:
            from core.signal.config import load_signal_config

            config = load_signal_config()
        except Exception:
            logger.debug("load signal config failed", exc_info=True)
            config = {}
    raw = dict(DEFAULT_CROSS_MARKET)
    raw.update(dict((config or {}).get("cross_market") or {}))
    mode = str(raw.get("mode") or "off").strip().lower()
    if mode in ("risk", "warn", "warning"):
        raw["mode"] = "risk"
    elif mode in ("gate", "block", "scale"):
        raw["mode"] = "gate"
    else:
        raw["mode"] = "off"
    try:
        raw["scale_buy_pct"] = max(0.0, min(float(raw.get("scale_buy_pct") or 0.5), 1.0))
    except (TypeError, ValueError):
        raw["scale_buy_pct"] = 0.5
    raw["scale_holds"] = bool(raw.get("scale_holds", True))
    return raw


def _sector_hit(sector: Optional[str], tags: List[str]) -> bool:
    s = str(sector or "")
    if not s:
        return False
    return any(t in s for t in tags)


def build_cross_market_prior(
    macro: Optional[dict],
    *,
    config: Optional[dict] = None,
    stock_code: Optional[str] = None,
    sector: Optional[str] = None,
) -> Dict[str, Any]:
    """由 macro 快照构建跨市场先验；不改 ŷ。"""
    cfg = get_cross_market_cfg(config)
    mode = cfg["mode"]
    base = {
        "success": True,
        "role": "cross_market_prior",
        "mode": mode,
        "stock_code": stock_code,
        "active": False,
        "actions": [],
        "warnings": [],
        "macro": macro,
        "predicted_score_unchanged": True,
    }
    if mode == "off" or not isinstance(macro, dict):
        base["note"] = "跨市场先验关闭或无宏观数据"
        return base

    tech_1d = macro.get("overseas_tech_1d_pct")
    lead_lag = macro.get("lead_lag_expected_gap_pct")
    a50_1d = macro.get("a50_1d_pct")
    stress = float(macro.get("liquidity_stress_score") or 0.0)
    trigger = float(cfg.get("tech_drag_trigger_pct") or -1.5)
    severe = float(cfg.get("tech_drag_severe_pct") or -3.0)
    stress_min = float(cfg.get("liquidity_stress_min") or 1.0)

    tech_hit = tech_1d is not None and float(tech_1d) <= trigger
    stress_hit = stress >= stress_min
    sector_hit = _sector_hit(sector, list(cfg.get("sector_penalty_tags") or []))

    active = bool(tech_hit or stress_hit)
    if sector_hit and (tech_hit or stress_hit):
        active = True

    warnings: List[str] = []
    actions: List[Dict[str, Any]] = []
    if tech_hit:
        warnings.append(
            f"跨市场·海外科技隔夜 {float(tech_1d):+.2f}%"
            + ("（显著拖累）" if tech_1d is not None and float(tech_1d) <= severe else "")
        )
    if lead_lag is not None and float(lead_lag) <= trigger * float(cfg.get("lead_lag_beta_mult") or 0.55):
        warnings.append(f"跨市场·Lead-Lag 预期缺口 {float(lead_lag):+.2f}%")
        if not tech_hit and float(lead_lag) <= trigger:
            tech_hit = True
            active = True
    if a50_1d is not None and float(a50_1d) <= -1.0:
        warnings.append(f"跨市场·A50 期指 {float(a50_1d):+.2f}%")
        active = True
    if stress_hit:
        warnings.append(f"跨市场·流动性收紧 stress={stress:.0f}")
    if sector_hit and active:
        warnings.append(f"跨市场·板块映射 {sector}")

    if active and mode == "gate":
        actions.append(
            {
                "type": "scale_buy",
                "reason": CROSS_MARKET_REASON,
                "scale": cfg["scale_buy_pct"],
                "scale_pct": cfg["scale_buy_pct"],
                "scale_holds": cfg["scale_holds"],
                "tech_1d_pct": tech_1d,
                "liquidity_stress": stress,
            }
        )
        if cfg.get("scale_holds"):
            actions.append(
                {
                    "type": "scale_hold",
                    "scale": cfg["scale_buy_pct"],
                    "reason": CROSS_MARKET_REASON,
                }
            )
    elif active and mode == "risk":
        actions = []

    base.update(
        {
            "active": active and bool(warnings),
            "tech_drag": tech_hit,
            "liquidity_stress": stress_hit,
            "sector_mapped": sector_hit,
            "warnings": warnings,
            "actions": actions,
            "note": "跨市场 prior；不进 sub_scores。",
        }
    )
    return base
