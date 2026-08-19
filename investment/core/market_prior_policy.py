"""市场级 prior 执行策略（ŷ 外）：串联 cross_market / sentiment / regulatory / IPO。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _prior_keys_from_item(item: Optional[dict]) -> List[str]:
    return [
        "cross_market_prior",
        "market_sentiment_prior",
        "regulatory_prior",
        "ipo_drain_prior",
    ]


def _merge_mode(config: Optional[dict] = None) -> str:
    if config is None:
        try:
            from core.signal.config import load_signal_config

            config = load_signal_config()
        except Exception:
            config = {}
    raw = str((config or {}).get("market_prior_policy", {}).get("merge_mode") or "min_scale")
    return raw if raw in ("min_scale", "chain") else "min_scale"


def _scale_from_pack(pack: dict) -> Optional[float]:
    for act in pack.get("actions") or []:
        if act.get("type") == "scale_buy":
            try:
                return max(0.0, min(float(act.get("scale") if act.get("scale") is not None else 0.5), 1.0))
            except (TypeError, ValueError):
                return 0.5
    return None


def _hold_scale_from_pack(pack: dict) -> Optional[float]:
    for act in pack.get("actions") or []:
        if act.get("type") == "scale_hold":
            try:
                return max(0.0, min(float(act.get("scale") if act.get("scale") is not None else 0.5), 1.0))
            except (TypeError, ValueError):
                return 0.5
    return None


def apply_market_priors_to_buy(
    item: Optional[dict],
    *,
    position_ratio: float,
    config: Optional[dict] = None,
) -> Dict[str, Any]:
    """对拟买入串联应用市场级 prior；复用 sentiment_prior.apply_prior_to_buy 语义。"""
    from core.sentiment_prior import apply_prior_to_buy

    ratio = float(position_ratio)
    warnings: List[str] = []
    reasons: List[str] = []
    applied = False
    merge = _merge_mode(config)

    if merge == "min_scale":
        min_scale = 1.0
        block_pack = None
        for key in _prior_keys_from_item(item):
            pack = (item or {}).get(key)
            if not isinstance(pack, dict) or not pack.get("active"):
                continue
            applied = True
            warnings.extend(list(pack.get("warnings") or []))
            for act in pack.get("actions") or []:
                if act.get("type") == "block_new_buy":
                    block_pack = pack
                    break
            sc = _scale_from_pack(pack)
            if sc is not None:
                min_scale = min(min_scale, sc)
                reasons.append(str(key))
        if block_pack:
            out = apply_prior_to_buy(block_pack, position_ratio=ratio)
            return {
                "ok": False,
                "skip": True,
                "ratio": 0.0,
                "reason": out.get("reason") or "market_prior",
                "warnings": warnings,
                "applied": True,
                "predicted_score_unchanged": True,
            }
        if applied and min_scale < 1.0:
            ratio = ratio * min_scale
        return {
            "ok": True,
            "skip": False,
            "ratio": ratio,
            "reason": reasons[-1] if reasons else None,
            "warnings": warnings,
            "applied": applied,
            "predicted_score_unchanged": True,
        }

    for key in _prior_keys_from_item(item):
        pack = (item or {}).get(key)
        if not isinstance(pack, dict) or not pack.get("active"):
            continue
        applied = True
        out = apply_prior_to_buy(pack, position_ratio=ratio)
        warnings.extend(list(out.get("warnings") or []))
        if out.get("skip"):
            return {
                "ok": False,
                "skip": True,
                "ratio": 0.0,
                "reason": out.get("reason") or key,
                "warnings": warnings,
                "applied": True,
                "predicted_score_unchanged": True,
            }
        if out.get("ratio") is not None:
            ratio = float(out["ratio"])
        if out.get("reason"):
            reasons.append(str(out["reason"]))

    return {
        "ok": True,
        "skip": False,
        "ratio": ratio,
        "reason": reasons[-1] if reasons else None,
        "warnings": warnings,
        "applied": applied,
        "predicted_score_unchanged": True,
    }


def apply_market_priors_to_hold(
    item: Optional[dict],
    *,
    shares: float,
    lot: int = 100,
    config: Optional[dict] = None,
) -> Dict[str, Any]:
    """对已持仓串联应用市场级 prior 缩仓。"""
    from core.sentiment_prior import apply_prior_to_hold

    sh = float(shares or 0)
    warnings: List[str] = []
    trim = False
    sell_shares = 0.0
    keep_shares = sh
    reason = None
    merge = _merge_mode(config)

    if merge == "min_scale":
        min_scale = 1.0
        active_pack = None
        for key in _prior_keys_from_item(item):
            pack = (item or {}).get(key)
            if not isinstance(pack, dict) or not pack.get("active"):
                continue
            warnings.extend(list(pack.get("warnings") or []))
            sc = _hold_scale_from_pack(pack)
            if sc is not None:
                min_scale = min(min_scale, sc)
                active_pack = pack
                reason = key
        if active_pack is not None and min_scale < 1.0:
            synthetic = dict(active_pack)
            synthetic["actions"] = [{"type": "scale_hold", "scale": min_scale, "reason": reason}]
            out = apply_prior_to_hold(synthetic, shares=sh, lot=lot)
            return {
                "trim": bool(out.get("trim")),
                "sell_shares": float(out.get("sell_shares") or 0),
                "keep_shares": float(out.get("keep_shares") or sh),
                "reason": out.get("reason") or reason,
                "warnings": warnings,
                "predicted_score_unchanged": True,
            }
        return {
            "trim": False,
            "sell_shares": 0.0,
            "keep_shares": sh,
            "reason": None,
            "warnings": warnings,
            "predicted_score_unchanged": True,
        }

    for key in _prior_keys_from_item(item):
        pack = (item or {}).get(key)
        if not isinstance(pack, dict) or not pack.get("active"):
            continue
        out = apply_prior_to_hold(pack, shares=keep_shares, lot=lot)
        warnings.extend(list(out.get("warnings") or []))
        if out.get("trim"):
            trim = True
            sell_shares += float(out.get("sell_shares") or 0)
            keep_shares = float(out.get("keep_shares") or keep_shares)
            reason = out.get("reason") or key

    return {
        "trim": trim and sell_shares > 0,
        "sell_shares": sell_shares,
        "keep_shares": keep_shares,
        "reason": reason,
        "warnings": warnings,
        "predicted_score_unchanged": True,
    }
