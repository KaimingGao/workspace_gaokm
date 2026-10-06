"""舆情先验（ŷ 外）：只作观察徽章，不进 sub_scores / predicted_score / 调仓 / 回测。

契约：
  ŷ = ReturnScoreModel(...)     ← 唯一生产排序轴
  S = score_headlines(...)      ← 参考徽章（看空/看多）
  live 调仓 = rank_lots(ranking=w·ŷ_oo+w·ŷ_oc)  ← 不读 S
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Sequence

PRIOR_REASON = "sentiment_prior_bearish"
PRIOR_REASON_BULLISH = "sentiment_prior_bullish_theme"

DEFAULT_PRIOR = {
    "mode": "off",  # off | risk | gate
    "block_new_buys": False,
    "scale_buy_pct": 0.5,
    "scale_holds": False,  # gate：看空时已持仓也缩至 scale_buy_pct
    "warn_only": True,
    # P1c：看多/主题时减少「负 ŷ 强回避」——卖出 soft hold（不改 ŷ）
    "reduce_avoid_on_bullish": True,
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
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            config = {}
    sent = (config or {}).get("sentiment") or {}
    raw = dict(DEFAULT_PRIOR)
    raw.update(dict(sent.get("prior") or {}))
    raw["mode"] = normalize_prior_mode(raw.get("mode"))
    # 产品：个股舆情只作徽章参考；调仓 / 回测不执行 gate/risk
    raw["mode"] = "off"
    raw.pop("bearish_score_min", None)  # 已废弃：看空即触发，清理旧配置残留
    try:
        scale = float(raw.get("scale_buy_pct") if raw.get("scale_buy_pct") is not None else 0.5)
    except (TypeError, ValueError):
        scale = 0.5
    raw["scale_buy_pct"] = max(0.0, min(scale, 1.0))
    raw["block_new_buys"] = bool(raw.get("block_new_buys", False))
    raw["scale_holds"] = bool(raw.get("scale_holds", False))
    raw["warn_only"] = bool(raw.get("warn_only", True))
    raw["reduce_avoid_on_bullish"] = bool(
        raw.get("reduce_avoid_on_bullish")
        if raw.get("reduce_avoid_on_bullish") is not None
        else True
    )
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
    """由规则情绪快照构建先验；看空(bearish)即触发，actions 不含任何改 ŷ 字段。

    超时/空源降级（degraded / prior_eligible=false）不触发 gate，避免误缩仓。
    """
    prior_cfg = get_sentiment_prior_cfg(config)
    mode = prior_cfg["mode"]
    label = str((sentiment or {}).get("label") or "neutral") if sentiment else "neutral"
    score = _bearish_strength(sentiment)
    degraded = bool(
        (sentiment or {}).get("degraded")
        or (sentiment or {}).get("prior_eligible") is False
    )
    # 看空即触发缩仓/拦截；降级数据不触发
    active = label == "bearish" and not degraded

    actions: List[Dict[str, Any]] = []
    risk_hints: List[Dict[str, Any]] = []
    warnings: List[str] = []
    blocks: List[str] = []

    note_base = "舆情先验 · 不进 ŷ / 非因子"
    if degraded:
        note_base += " · 降级跳过 gate"
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

    # P1c：看多 / 主题 → soft_hold（与 event_prior 对称，专治负 ŷ 踏空）
    bullish = (_is_bullish_label(label) or _theme_from_titles(sentiment)) and label != "bearish"
    if (
        bullish
        and not degraded
        and prior_cfg.get("reduce_avoid_on_bullish")
        and mode != "off"
    ):
        actions.append(
            {
                "type": "soft_hold",
                "reason": PRIOR_REASON_BULLISH,
                "label": label,
            }
        )
        warnings.append(f"{PRIOR_REASON_BULLISH}: 看多/主题·减少负ŷ回避")
        if not active:
            active = True
            risk_hints.append(
                {
                    "kind": "sentiment_bullish",
                    "label": label,
                    "reason": PRIOR_REASON_BULLISH,
                    "note": f"{note_base} · 看多 soft hold",
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
        "bullish_theme": bool(bullish and not degraded),
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


def should_soft_hold_from_sentiment(prior: Optional[dict]) -> bool:
    """低 ŷ 卖出时：看多/主题 soft_hold。"""
    if not isinstance(prior, dict):
        return False
    for act in prior.get("actions") or []:
        if act.get("type") == "soft_hold":
            return True
    return False


def prior_wants_scale_hold(prior: Optional[dict]) -> bool:
    """当前先验是否仍要求已持仓缩仓。"""
    if not isinstance(prior, dict) or not prior.get("active"):
        return False
    for act in prior.get("actions") or []:
        if act.get("type") == "scale_hold":
            return True
    return False


def apply_prior_restore_hold(
    prior: Optional[dict],
    *,
    current_shares: float,
    base_shares: Optional[float],
    lot: int = 100,
) -> Dict[str, Any]:
    """舆情缩仓后若先验已不再要求 scale_hold，则补回至 base_shares。

    返回 {restore, buy_shares, target_shares, reason, clear_base}。
    """
    cur = float(current_shares or 0)
    try:
        base = float(base_shares) if base_shares is not None else None
    except (TypeError, ValueError):
        base = None
    empty = {
        "restore": False,
        "buy_shares": 0.0,
        "target_shares": cur,
        "reason": None,
        "clear_base": False,
    }
    if base is None or base <= cur + 1e-9:
        # 已回到或超过基准 → 清掉标记
        return {**empty, "clear_base": base is not None and base <= cur + 1e-9}
    if prior_wants_scale_hold(prior):
        return empty
    lot_n = max(1, int(lot or 100))
    need = base - cur
    buy = float(int(need // lot_n) * lot_n)
    if buy < lot_n:
        return empty
    return {
        "restore": True,
        "buy_shares": buy,
        "target_shares": cur + buy,
        "reason": "sentiment_prior_restore",
        "clear_base": abs((cur + buy) - base) < lot_n,
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
            logger.exception('unexpected error in resolve_prior_for_code')
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
