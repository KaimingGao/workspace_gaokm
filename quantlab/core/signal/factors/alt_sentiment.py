"""另类情绪因子（N2 / V2.1）：舆情快照偏置；生产权重小人审可调。

评分口径与原 scorer._sentiment_adjustment 对齐后映射到 0～100，
避免同时存在硬编码加减分与因子双计。
"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Tuple


def score_alt_sentiment(
    bars: List[dict],
    *,
    sentiment: Optional[dict] = None,
) -> Tuple[float, Dict[str, Any]]:
    """0～100。无舆情 → 50；偏多抬、偏空压。"""
    _ = bars
    sent = sentiment or {}
    label = str(sent.get("label") or sent.get("sentiment_label") or "neutral").lower()
    score_raw = sent.get("score")
    try:
        s = float(score_raw) if score_raw is not None else None
    except (TypeError, ValueError):
        s = None

    # 与历史硬编码 adj 同向：bullish 抬、bearish 压、mixed 略压
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
        if -1.5 <= s <= 1.5:
            out = 50.0 + s * 25.0
        else:
            out = max(0.0, min(100.0, s))
    else:
        out = 50.0

    return round(max(10.0, min(95.0, out)), 1), {
        "alt_sentiment_label": label,
        "alt_sentiment_input": s,
    }
