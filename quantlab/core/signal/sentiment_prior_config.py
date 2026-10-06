"""人审写入 ``signal_config.sentiment.prior``（不改 weights / 不进 ŷ）。"""

import json
import os
from typing import Any, Dict, Optional

from core.io_atomic import atomic_write_json
from core.sentiment_prior import DEFAULT_PRIOR, get_sentiment_prior_cfg


def read_sentiment_prior_public(*, config: Optional[dict] = None) -> Dict[str, Any]:
    """供 API / UI 展示的先验配置快照。"""
    cfg = get_sentiment_prior_cfg(config)
    return {
        "role": "prior",
        "include_in_score": bool(cfg.get("include_in_score", False)),
        "mode": cfg["mode"],
        "block_new_buys": bool(cfg["block_new_buys"]),
        "scale_buy_pct": float(cfg["scale_buy_pct"]),
        "scale_holds": bool(cfg.get("scale_holds", False)),
        "warn_only": bool(cfg["warn_only"]),
        "note": "舆情仅观察徽章 · 不进 predicted_score · 不参与调仓",
    }


def save_sentiment_prior(
    *,
    mode: Optional[str] = None,
    block_new_buys: Optional[bool] = None,
    scale_buy_pct: Optional[float] = None,
    scale_holds: Optional[bool] = None,
    note: str = "",
) -> Dict[str, Any]:
    """人审写盘仍可调用；产品强制 ``mode=off``（仅徽章，不调仓）。"""
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
    prior.pop("bearish_score_min", None)
    changed: Dict[str, Any] = {"mode": "off"}
    _ = (mode, block_new_buys, scale_buy_pct, scale_holds)

    sent["include_in_score"] = False
    sent["role"] = "prior"
    sent["prior"] = prior
    if "append_history" not in sent:
        sent["append_history"] = True
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
