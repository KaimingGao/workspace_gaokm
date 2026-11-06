"""规范化 ``signal_config.sentiment.prior``（徽章侧；不改 weights / 不进 ŷ）。"""

import json
import os
from typing import Any, Dict, Optional

from core.io_atomic import atomic_write_json
from core.market.priors.stock_sentiment import DEFAULT_PRIOR, get_sentiment_prior_cfg


def read_sentiment_prior_public(*, config: Optional[dict] = None) -> Dict[str, Any]:
    """供 API 展示的徽章 prior 快照（不含已退役硬闸键）。"""
    cfg = get_sentiment_prior_cfg(config)
    return {
        "role": "prior",
        "include_in_score": bool(cfg.get("include_in_score", False)),
        "mode": cfg["mode"],
        "note": "个股舆情仅观察徽章 · 不进 ŷ；调仓规则栏已下掉软先验闸",
    }


def save_sentiment_prior(*, note: str = "") -> Dict[str, Any]:
    """兼容写盘：强制 ``mode=off``、剥掉旧硬闸键；不调仓。"""
    from core.market.priors.stock_sentiment import LEGACY_GATE_KEYS
    from core.signal.config import get_signal_config_path, load_signal_config

    path = get_signal_config_path()
    raw: Dict[str, Any] = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f) or {}

    sent = dict(raw.get("sentiment") or {})
    prior = dict(DEFAULT_PRIOR)
    prior.update(dict(sent.get("prior") or {}))
    prior["mode"] = "off"
    for k in LEGACY_GATE_KEYS:
        prior.pop(k, None)
    changed: Dict[str, Any] = {"mode": "off"}

    sent["include_in_score"] = False
    sent["role"] = "prior"
    sent["prior"] = prior
    # 旧 jsonl 流水键：读盘剥掉，不再写回
    sent.pop("append_history", None)
    sent.pop("history_max_lines_per_code", None)
    raw["sentiment"] = sent

    atomic_write_json(path, raw)

    import core.signal.config as cfg_mod

    cfg_mod._cached = None
    load_signal_config(reload=True)

    return {
        "success": True,
        "task": "save_sentiment_prior",
        "changed": changed,
        "sentiment_prior": read_sentiment_prior_public(),
        "path": path,
        "note": note
        or "个股舆情仅观察徽章；mode 强制 off；ŷ/weights 未触碰",
        "signal_config_weights_touched": False,
        "include_in_score": False,
    }
