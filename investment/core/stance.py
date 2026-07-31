"""规则引擎：由 quote/signal/kline 等事实合成买卖 stance（确定性，供 LLM 引用）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.signal.config import get_stance_thresholds, load_signal_config

STANCE_LABELS = {
    "insufficient": "信息不足暂不建议操作",
    "avoid": "建议观望（暂不买入）",
    "wait": "建议观望（暂不买入）",
    "probe": "建议逢低分批关注但暂不追入",
    "buy_light": "可考虑轻仓试探（非追涨）",
}


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bad_kline_tags(tags: List[str]) -> bool:
    text = " ".join(str(t) for t in (tags or [])).lower()
    keys = ("大阴", "放量下跌", "破位", "急跌", "长阴", "bear", "down")
    return any(k in text for k in keys)


def compute_buy_stance(
    *,
    quote: Optional[dict] = None,
    signal_item: Optional[dict] = None,
    kline: Optional[dict] = None,
    peer: Optional[dict] = None,
    index: Optional[dict] = None,
) -> Dict[str, Any]:
    """输出确定性 stance；LLM 须引用 stance_label，不得自行升级/降级。"""
    reasons: List[str] = []
    quote = quote or {}
    signal_item = signal_item or {}
    kline = kline or {}

    if not quote.get("success"):
        return {
            "stance_code": "insufficient",
            "stance_label": STANCE_LABELS["insufficient"],
            "reasons": ["行情不可用"],
            "invalidation": [],
            "confidence": "low",
        }

    score = _f(signal_item.get("score"))
    hard_reject = bool(signal_item.get("hard_reject"))
    reject_reason = signal_item.get("reject_reason") or ""
    data_source = signal_item.get("data_source") or kline.get("data_source") or ""
    kline_depth = kline.get("depth") or ""
    change = _f(quote.get("change_raw"))
    tags = kline.get("latest_tags") or []
    if isinstance(kline.get("latest"), dict):
        tags = tags or kline["latest"].get("tags") or []

    invalidation = list(signal_item.get("invalidation") or [])[:3]

    if hard_reject:
        reasons.append(reject_reason or "短线硬拒绝")
        return {
            "stance_code": "avoid",
            "stance_label": STANCE_LABELS["avoid"],
            "reasons": reasons,
            "invalidation": invalidation,
            "confidence": "high",
            "score": score,
        }

    if score is None:
        reasons.append("无短线评分")
        return {
            "stance_code": "insufficient",
            "stance_label": STANCE_LABELS["insufficient"],
            "reasons": reasons,
            "invalidation": invalidation,
            "confidence": "low",
            "score": score,
        }

    thresholds = get_stance_thresholds(load_signal_config())
    t_avoid = thresholds["avoid"]
    t_wait = thresholds["wait"]
    t_probe = thresholds["probe"]

    if score < t_avoid:
        code = "avoid"
        reasons.append(f"短线分偏低({score})")
    elif score < t_wait:
        code = "wait"
        reasons.append(f"短线分中性偏弱({score})")
    elif score < t_probe:
        code = "probe"
        reasons.append(f"短线分尚可({score})，宜谨慎")
    else:
        code = "buy_light"
        reasons.append(f"短线分偏强({score})")

    penalties = 0
    if change is not None and change <= -5:
        penalties += 2
        reasons.append(f"当日大跌({change:+.2f}%)")
    elif change is not None and change <= -3:
        penalties += 1
        reasons.append(f"当日偏弱({change:+.2f}%)")

    if _bad_kline_tags(tags):
        penalties += 1
        reasons.append("K 线结构偏弱")

    if peer and peer.get("success"):
        rel = (peer.get("target") or {}).get("relative") or peer.get("relative_stance")
        if rel in ("偏弱", "最弱", "weak"):
            penalties += 1
            reasons.append("相对同业偏弱")

    if index and index.get("success"):
        excess = _f(index.get("excess_return_pct"))
        if excess is not None and excess <= -2:
            penalties += 1
            reasons.append(f"相对大盘偏弱(超额{excess:+.2f}%)")

    if data_source == "quote_fallback" or kline_depth == "intraday_proxy":
        penalties += 1
        reasons.append("日线不完整，结论偏短线")

    order = ["buy_light", "probe", "wait", "avoid"]
    idx = order.index(code) if code in order else 1
    idx = min(len(order) - 1, idx + penalties)
    code = order[idx]

    confidence = "high"
    if penalties >= 2 or data_source == "quote_fallback":
        confidence = "medium"
    if code in ("insufficient", "wait") and score is not None and score < 50:
        confidence = "medium"

    return {
        "stance_code": code,
        "stance_label": STANCE_LABELS.get(code, STANCE_LABELS["wait"]),
        "reasons": reasons[:6],
        "invalidation": invalidation,
        "confidence": confidence,
        "score": score,
        "data_source": data_source,
    }


def format_stance_block(stance: Dict[str, Any]) -> str:
    """供 Agent 注入的固定文本块。"""
    lines = [
        f"【规则引擎结论】{stance.get('stance_label')}",
        f"stance_code={stance.get('stance_code')} confidence={stance.get('confidence')}",
    ]
    for r in stance.get("reasons") or []:
        lines.append(f"- {r}")
    if stance.get("invalidation"):
        lines.append("失效条件：")
        for inv in stance["invalidation"][:2]:
            lines.append(f"- {inv}")
    lines.append("（LLM 须引用上述结论，不得自行升级/降级）")
    return "\n".join(lines)
