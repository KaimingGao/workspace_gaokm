"""徽章侧舆情先验（ŷ 外）：只作观察徽章，不进 sub_scores / predicted_score。

本模块的 ``prior.mode`` 产品强制 ``off``，不拦买、不缩仓。
调仓规则栏已下掉舆情软先验闸；本模块只做徽章，与 fuse/rank 无关。

契约：
  ŷ = ReturnScoreModel(...)     ← 唯一生产排序轴
  S = score_headlines(...)      ← 本模块：参考徽章；不改 fuse/rank
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PRIOR_REASON = "sentiment_prior_bearish"
PRIOR_REASON_BULLISH = "sentiment_prior_bullish_theme"

# 产品恒强制 mode=off；旧文件里的 gate 键在 get 时剥掉。
DEFAULT_PRIOR = {
    "mode": "off",
}

# 徽章 prior 已退役的硬闸键（读盘/写盘时丢弃，避免误以为仍生效）。
LEGACY_GATE_KEYS = (
    "block_new_buys",
    "scale_buy_pct",
    "scale_holds",
    "warn_only",
    "reduce_avoid_on_bullish",
    "bearish_score_min",
)


def normalize_prior_mode(raw: Any) -> str:
    m = str(raw or "off").strip().lower()
    if m in ("risk", "warn", "warning"):
        return "risk"
    if m in ("gate", "block", "scale"):
        return "gate"
    return "off"


def get_sentiment_prior_cfg(config: Optional[dict] = None) -> Dict[str, Any]:
    """合并 DEFAULT + signal_config.sentiment.prior；产品强制 mode=off。"""
    if config is None:
        try:
            from core.signal.config import load_signal_config

            config = load_signal_config()
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            config = {}
    sent = (config or {}).get("sentiment") or {}
    raw = dict(DEFAULT_PRIOR)
    raw.update(dict(sent.get("prior") or {}))
    for k in LEGACY_GATE_KEYS:
        raw.pop(k, None)
    # 产品：徽章参考；文件里写 gate/risk 也强制 off
    raw["mode"] = "off"
    raw["role"] = str(sent.get("role") or "prior")
    raw["include_in_score"] = bool(sent.get("include_in_score", False))
    return raw


def _is_bullish_label(label: str) -> bool:
    lab = str(label or "").lower()
    return lab in ("bullish", "positive", "利好") or "pos" in lab or "bull" in lab


def _theme_from_titles(sentiment: Optional[dict]) -> bool:
    """标题命中常见主题词 → 视为主题偏多（弱信号）。"""
    if not isinstance(sentiment, dict):
        return False
    blob = " ".join(
        str(x)
        for x in (
            sentiment.get("summary"),
            sentiment.get("title"),
            " ".join(str(t) for t in (sentiment.get("titles") or [])[:5]),
            sentiment.get("headline"),
        )
        if x
    )
    keys = ("云计算", "AI", "人工智能", "算力", "芯片", "半导体", "机器人", "新能源")
    return any(k in blob for k in keys)


def _bearish_strength(sentiment: Optional[dict]) -> Optional[float]:
    """提取 bearish 情绪的 score（仅用于展示；active 判定只看 label）。"""
    if not isinstance(sentiment, dict):
        return None
    if str(sentiment.get("label") or "") != "bearish":
        return None
    try:
        return float(sentiment.get("score"))
    except (TypeError, ValueError):
        return None


def build_sentiment_prior(
    sentiment: Optional[dict],
    *,
    config: Optional[dict] = None,
    stock_code: Optional[str] = None,
) -> Dict[str, Any]:
    """徽章侧先验：看空/看多给 risk_hints；恒 mode=off，不产出拦买/缩仓 actions。

    调仓规则栏已下掉软先验闸。超时/空源降级不标看空触发。
    """
    prior_cfg = get_sentiment_prior_cfg(config)
    mode = prior_cfg["mode"]  # 恒为 off
    label = str((sentiment or {}).get("label") or "neutral") if sentiment else "neutral"
    score = _bearish_strength(sentiment)
    degraded = bool(
        (sentiment or {}).get("degraded")
        or (sentiment or {}).get("prior_eligible") is False
    )
    active = label == "bearish" and not degraded
    bullish = (
        (_is_bullish_label(label) or _theme_from_titles(sentiment))
        and label != "bearish"
        and not degraded
    )

    risk_hints: List[Dict[str, Any]] = []
    note_base = "舆情徽章 · 不进 ŷ"
    if degraded:
        note_base += " · 降级"
        risk_hints.append(
            {
                "kind": "sentiment_degraded",
                "label": label,
                "score": score,
                "reason": "sentiment_degraded",
                "note": (sentiment or {}).get("degrade_reason") or note_base,
            }
        )
    if active:
        risk_hints.append(
            {
                "kind": "sentiment_bearish",
                "label": "bearish",
                "score": score,
                "reason": PRIOR_REASON,
                "note": f"{note_base} · 看空",
            }
        )
    elif bullish:
        risk_hints.append(
            {
                "kind": "sentiment_bullish",
                "label": label,
                "reason": PRIOR_REASON_BULLISH,
                "note": f"{note_base} · 看多",
            }
        )

    return {
        "success": True,
        "role": "prior",
        "mode": mode,
        "stock_code": str(stock_code or "").strip() or None,
        "label": label,
        "score": score,
        "active": bool(active),
        "degraded": bool(degraded),
        "bullish_theme": bool(bullish),
        "actions": [],
        "risk_hints": risk_hints,
        "warnings": [],
        "include_in_score": bool(prior_cfg["include_in_score"]),
        "note": note_base,
    }


def apply_prior_to_buy(
    prior: Dict[str, Any],
    *,
    position_ratio: float,
) -> Dict[str, Any]:
    """对单笔拟买入应用 prior pack：skip 或缩放 ratio；不改 score。

    供 M 层 ``policy.py`` / 合成 pack；徽章 ``build_sentiment_prior`` 不产出 actions。

    返回 {ok, skip, ratio, reason, warnings}。
    """
    ratio = float(position_ratio)
    warnings = list(prior.get("warnings") or [])
    if not prior.get("active"):
        return {
            "ok": True,
            "skip": False,
            "ratio": ratio,
            "reason": None,
            "warnings": warnings,
            "predicted_score_unchanged": True,
        }
    for act in prior.get("actions") or []:
        if act.get("type") == "block_new_buy":
            return {
                "ok": False,
                "skip": True,
                "ratio": 0.0,
                "reason": str(act.get("reason") or PRIOR_REASON),
                "warnings": warnings,
                "predicted_score_unchanged": True,
            }
        if act.get("type") == "scale_buy":
            try:
                scale = float(act.get("scale") if act.get("scale") is not None else 0.5)
            except (TypeError, ValueError):
                scale = 0.5
            ratio = ratio * max(0.0, min(scale, 1.0))
            return {
                "ok": True,
                "skip": False,
                "ratio": ratio,
                "reason": str(act.get("reason") or PRIOR_REASON),
                "warnings": warnings,
                "scale": scale,
                "predicted_score_unchanged": True,
            }
    return {
        "ok": True,
        "skip": False,
        "ratio": ratio,
        "reason": None,
        "warnings": warnings,
        "predicted_score_unchanged": True,
    }


def apply_prior_to_hold(
    prior: Dict[str, Any],
    *,
    shares: float,
    lot: int = 100,
) -> Dict[str, Any]:
    """对已持仓应用 prior pack 缩仓语义；不改 score。

    供 M 层 / 合成 pack；徽章 ``build_sentiment_prior`` 不调用本函数。

    返回 {trim, sell_shares, keep_shares, scale, reason, warnings}。
    """
    sh = float(shares or 0)
    warnings = list(prior.get("warnings") or [])
    empty = {
        "trim": False,
        "sell_shares": 0.0,
        "keep_shares": sh,
        "scale": None,
        "reason": None,
        "warnings": warnings,
        "predicted_score_unchanged": True,
    }
    if sh <= 0 or not prior.get("active"):
        return empty
    scale = None
    for act in prior.get("actions") or []:
        if act.get("type") == "scale_hold":
            try:
                scale = float(act.get("scale") if act.get("scale") is not None else 0.5)
            except (TypeError, ValueError):
                scale = 0.5
            break
    if scale is None:
        return empty
    scale = max(0.0, min(scale, 1.0))
    lot_n = max(1, int(lot or 100))
    if scale <= 0:
        sell = sh
        keep = 0.0
    else:
        keep = float(int(sh * scale // lot_n) * lot_n)
        if keep >= sh:
            return empty
        sell = round(sh - keep, 4)
    if sell <= 0:
        return empty
    return {
        "trim": True,
        "sell_shares": sell,
        "keep_shares": keep,
        "scale": scale,
        "reason": PRIOR_REASON,
        "warnings": warnings,
        "predicted_score_unchanged": True,
    }
