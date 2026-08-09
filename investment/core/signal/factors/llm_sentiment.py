"""LLM 情绪因子（研究轨 / N6）：Qwen 对新闻标题+正文打分；不进生产 ŷ。

定位同 ``alt_sentiment``（prior_only）：仅作研究轨因子，权重须保持 0。
LLM 情绪打分逻辑见 ``core.sentiment.score_headlines_llm``。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def score_llm_sentiment(
    bars: List[dict],
    *,
    llm_sentiment: Optional[dict] = None,
) -> Tuple[float, Dict[str, Any]]:
    """0~100。无 LLM 舆情 → 50；偏多抬、偏空压。

    与 ``alt_sentiment`` 的区别：输入来自 Qwen 对标题+正文的语义理解，
    而非规则关键词匹配。映射口径与 alt_sentiment 对齐以便横向比较。
    """
    _ = bars
    sent = llm_sentiment or {}
    label = str(sent.get("label") or "neutral").lower()
    score_raw = sent.get("score")
    try:
        s = float(score_raw) if score_raw is not None else None
    except (TypeError, ValueError):
        s = None

    # 与 alt_sentiment 同口径映射，便于研究轨横向比较
    if label in ("bullish", "positive", "利好") or "pos" in label:
        out = 65.0
    elif label in ("bearish", "negative", "利空") or "neg" in label:
        if s is not None and s >= 0.6:
            out = 35.0
        else:
            out = 42.0
    elif label == "mixed":
        out = 47.0
    elif s is not None:
        # LLM score 归一化到 [0, 1]：0.5 为中性
        if 0.0 <= s <= 1.0:
            out = 25.0 + s * 50.0
        elif -1.5 <= s <= 1.5:
            out = 50.0 + s * 25.0
        else:
            out = max(0.0, min(100.0, s))
    else:
        out = 50.0

    return round(max(10.0, min(95.0, out)), 1), {
        "llm_sentiment_label": label,
        "llm_sentiment_score": s,
        "llm_sentiment_source": str(sent.get("source") or "qwen"),
    }
