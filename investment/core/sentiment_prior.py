"""舆情先验（ŷ 外）：确定性规则 S，不进 sub_scores / predicted_score / 回测。

契约：
  ŷ = ReturnScoreModel(...)     ← 唯一生产排序轴
  S = score_headlines(...)      ← 先验
  action = policy(ŷ, S)         ← warn / block_new_buy / scale_buy / scale_hold
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


PRIOR_REASON = "sentiment_prior_bearish"

DEFAULT_PRIOR = {
    "mode": "off",  # off | risk | gate
    "block_new_buys": False,
    "scale_buy_pct": 0.5,
    "scale_holds": False,  # gate：看空时已持仓也缩至 scale_buy_pct
    "warn_only": True,
}


def normalize_prior_mode(raw: Any) -> str:
    m = str(raw or "off").strip().lower()
    if m in ("risk", "warn", "warning"):
        return "risk"
    if m in ("gate", "block", "scale"):
        return "gate"
    return "off"


def get_sentiment_prior_cfg(config: Optional[dict] = None) -> Dict[str, Any]:
    """合并 DEFAULT + signal_config.sentiment.prior。"""
    if config is None:
        try:
            from core.signal.config import load_signal_config

            config = load_signal_config()
        except Exception:
            config = {}
    sent = (config or {}).get("sentiment") or {}
    raw = dict(DEFAULT_PRIOR)
    raw.update(dict(sent.get("prior") or {}))
    raw["mode"] = normalize_prior_mode(raw.get("mode"))
    raw.pop("bearish_score_min", None)  # 已废弃：看空即触发，清理旧配置残留
    try:
        scale = float(raw.get("scale_buy_pct") if raw.get("scale_buy_pct") is not None else 0.5)
    except (TypeError, ValueError):
        scale = 0.5
    raw["scale_buy_pct"] = max(0.0, min(scale, 1.0))
    raw["block_new_buys"] = bool(raw.get("block_new_buys", False))
    raw["scale_holds"] = bool(raw.get("scale_holds", False))
    raw["warn_only"] = bool(raw.get("warn_only", True))
    raw["role"] = str(sent.get("role") or "prior")
    raw["include_in_score"] = bool(sent.get("include_in_score", False))
    return raw


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
    """由规则情绪快照构建先验；看空(bearish)即触发，actions 不含任何改 ŷ 字段。"""
    prior_cfg = get_sentiment_prior_cfg(config)
    mode = prior_cfg["mode"]
    label = str((sentiment or {}).get("label") or "neutral") if sentiment else "neutral"
    score = _bearish_strength(sentiment)
    # 看空即触发缩仓/拦截，不再依赖 score ≥ 阈值
    active = label == "bearish"

    actions: List[Dict[str, Any]] = []
    risk_hints: List[Dict[str, Any]] = []
    warnings: List[str] = []
    blocks: List[str] = []

    note_base = "舆情先验 · 不进 ŷ / 非因子"

    if active:
        hint = {
            "kind": "sentiment_bearish",
            "label": "bearish",
            "score": score,
            "reason": PRIOR_REASON,
            "note": f"{note_base} · 看空",
        }
        # off 也给 UI 提示；policy 动作仅 risk/gate
        risk_hints.append(hint)
        if mode == "risk":
            actions.append({"type": "warn", "reason": PRIOR_REASON, "score": score})
            warnings.append(f"{PRIOR_REASON}: bearish={score}")
        elif mode == "gate":
            actions.append({"type": "warn", "reason": PRIOR_REASON, "score": score})
            warnings.append(f"{PRIOR_REASON}: bearish={score}")
            if prior_cfg["block_new_buys"]:
                actions.append(
                    {
                        "type": "block_new_buy",
                        "reason": PRIOR_REASON,
                        "score": score,
                    }
                )
                blocks.append(f"{PRIOR_REASON}: 跳过新开仓（bearish={score}）")
            else:
                scale = float(prior_cfg["scale_buy_pct"])
                actions.append(
                    {
                        "type": "scale_buy",
                        "reason": PRIOR_REASON,
                        "score": score,
                        "scale": scale,
                    }
                )
                warnings.append(
                    f"{PRIOR_REASON}: 新开仓缩至 {scale:.0%}（bearish={score}）"
                )
            if prior_cfg.get("scale_holds"):
                scale_h = float(prior_cfg["scale_buy_pct"])
                actions.append(
                    {
                        "type": "scale_hold",
                        "reason": PRIOR_REASON,
                        "score": score,
                        "scale": scale_h,
                    }
                )
                warnings.append(
                    f"{PRIOR_REASON}: 已持仓缩至 {scale_h:.0%}（bearish={score}）"
                )

    return {
        "success": True,
        "role": "prior",
        "mode": mode,
        "stock_code": str(stock_code or "").strip() or None,
        "label": label,
        "score": score,
        "active": bool(active),
        "actions": actions,
        "risk_hints": risk_hints,
        "warnings": warnings,
        "blocks": blocks,
        "block_new_buys": bool(prior_cfg["block_new_buys"]) if mode == "gate" else False,
        "scale_buy_pct": float(prior_cfg["scale_buy_pct"]) if mode == "gate" else 1.0,
        "scale_holds": bool(prior_cfg.get("scale_holds")) if mode == "gate" else False,
        "include_in_score": bool(prior_cfg["include_in_score"]),
        "note": note_base,
    }


def apply_prior_to_buy(
    prior: Dict[str, Any],
    *,
    position_ratio: float,
) -> Dict[str, Any]:
    """对单笔拟买入应用先验：skip 或缩放 ratio；不改 score。

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
    # risk / off：不改仓
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
    """对已持仓应用先验缩仓：卖出至剩余约 scale；不改 score。

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


def resolve_prior_for_code(
    code: str,
    *,
    config: Optional[dict] = None,
    sentiment: Optional[dict] = None,
    fetch: bool = True,
) -> Dict[str, Any]:
    """取先验：可传入已有 sentiment，否则读标题缓存。"""
    sent = sentiment
    if sent is None and fetch:
        try:
            from core.sentiment import fetch_stock_headlines

            pack = fetch_stock_headlines(str(code), limit=5)
            if pack.get("ok"):
                sent = pack.get("sentiment")
        except Exception as exc:
            return {
                "success": False,
                "role": "prior",
                "mode": get_sentiment_prior_cfg(config)["mode"],
                "stock_code": str(code),
                "active": False,
                "actions": [],
                "risk_hints": [],
                "warnings": [f"sentiment_fetch_failed:{exc}"],
                "blocks": [],
                "error": str(exc),
                "note": "舆情先验 · 拉取失败",
            }
    return build_sentiment_prior(sent, config=config, stock_code=code)


def check_sentiment_priors_for_codes(
    codes: Sequence[str],
    *,
    config: Optional[dict] = None,
    sentiments: Optional[Dict[str, dict]] = None,
) -> Dict[str, Any]:
    """批量先验门禁摘要：供调仓预检 / 风控旁路。

    分流：已有 sentiment 的走快路径（无网络，串行即可）；缺失的走进程池
    并发拉取新闻（隔离 AkShare py_mini_racer，避免 ~80 票串行卡顿）。
    """
    warnings: List[str] = []
    blocks: List[str] = []
    block_items: List[Dict[str, Any]] = []
    by_code: Dict[str, Any] = {}
    sent_map = dict(sentiments or {})

    _seen: set = set()
    clean = [
        c
        for c in (str(r or "").strip() for r in (codes or []) if str(r or "").strip())
        if not (c in _seen or _seen.add(c))
    ]
    fast = [c for c in clean if c in sent_map]
    fetch = [c for c in clean if c not in sent_map]

    # 快路径：已有 sentiment，无网络
    for code in fast:
        by_code[code] = resolve_prior_for_code(
            code, config=config, sentiment=sent_map.get(code), fetch=False
        )

    # 慢路径：需拉新闻，进程池并发
    if fetch:
        from core.ports.market import batch_map

        results = batch_map(
            resolve_prior_for_code, fetch, config=config, fetch=True
        )
        for code, prior in zip(fetch, results):
            if not isinstance(prior, dict):
                prior = {
                    "success": False,
                    "role": "prior",
                    "mode": get_sentiment_prior_cfg(config)["mode"],
                    "stock_code": code,
                    "active": False,
                    "actions": [],
                    "risk_hints": [],
                    "warnings": ["sentiment_fetch_failed:pool"],
                    "blocks": [],
                    "error": "pool worker returned None",
                    "note": "舆情先验 · 进程池拉取失败",
                }
            by_code[code] = prior

    # 按原始顺序汇总 warnings / blocks
    for code in clean:
        prior = by_code.get(code) or {}
        for w in prior.get("warnings") or []:
            if w not in warnings:
                warnings.append(w)
        for b in prior.get("blocks") or []:
            if b not in blocks:
                blocks.append(b)
            block_items.append(
                {
                    "code": code,
                    "message": b,
                    "reason": PRIOR_REASON,
                    "kind": "sentiment_prior",
                }
            )
    return {
        "ok": not blocks,
        "warnings": warnings,
        "blocks": blocks,
        "block_items": block_items,
        "by_code": by_code,
        "note": "舆情先验旁路；不改 predicted_score",
    }
