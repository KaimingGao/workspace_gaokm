"""人审写入 ``signal_config.sentiment.prior``（不改 weights / 不进 ŷ）。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from core.sentiment_prior import DEFAULT_PRIOR, get_sentiment_prior_cfg, normalize_prior_mode
from core.io_atomic import atomic_write_json


def read_sentiment_prior_public(*, config: Optional[dict] = None) -> Dict[str, Any]:
    """供 API / UI 展示的先验配置快照。"""
    cfg = get_sentiment_prior_cfg(config)
    return {
        "role": "prior",
        "include_in_score": bool(cfg.get("include_in_score", False)),
        "mode": cfg["mode"],
        "bearish_score_min": float(cfg["bearish_score_min"]),
        "block_new_buys": bool(cfg["block_new_buys"]),
        "scale_buy_pct": float(cfg["scale_buy_pct"]),
        "scale_holds": bool(cfg.get("scale_holds", False)),
        "warn_only": bool(cfg["warn_only"]),
        "note": "舆情先验 · 不进 predicted_score；改 mode 影响调仓旁路",
    }


def save_sentiment_prior(
    *,
    mode: Optional[str] = None,
    bearish_score_min: Optional[float] = None,
    block_new_buys: Optional[bool] = None,
    scale_buy_pct: Optional[float] = None,
    scale_holds: Optional[bool] = None,
    note: str = "",
) -> Dict[str, Any]:
    """写入 prior.*；强制 ``include_in_score=false``、``role=prior``。"""
    from core.paths import SIGNAL_CONFIG_PATH
    from core.signal.config import SIGNAL_CONFIG_PATH as CFG_PATH
    from core.signal.config import load_signal_config

    path = os.environ.get("INVESTMENT_SIGNAL_CONFIG", SIGNAL_CONFIG_PATH or CFG_PATH)
    raw: Dict[str, Any] = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f) or {}

    sent = dict(raw.get("sentiment") or {})
    prior = dict(DEFAULT_PRIOR)
    prior.update(dict(sent.get("prior") or {}))
    changed: Dict[str, Any] = {}

    if mode is not None:
        m = normalize_prior_mode(mode)
        prior["mode"] = m
        changed["mode"] = m
    if bearish_score_min is not None:
        thr = max(0.0, min(float(bearish_score_min), 1.0))
        prior["bearish_score_min"] = thr
        changed["bearish_score_min"] = thr
    if block_new_buys is not None:
        prior["block_new_buys"] = bool(block_new_buys)
        changed["block_new_buys"] = bool(block_new_buys)
    if scale_buy_pct is not None:
        scale = max(0.0, min(float(scale_buy_pct), 1.0))
        prior["scale_buy_pct"] = scale
        changed["scale_buy_pct"] = scale
    if scale_holds is not None:
        prior["scale_holds"] = bool(scale_holds)
        changed["scale_holds"] = bool(scale_holds)

    if not changed:
        return {
            "success": False,
            "error": "未提供可写字段（mode / bearish_score_min / block_new_buys / scale_buy_pct / scale_holds）",
            "signal_config_weights_touched": False,
        }

    # 契约：先验不是因子
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
        or "仅改 sentiment.prior；ŷ/weights 未触碰；include_in_score 强制 false",
        "signal_config_weights_touched": False,
        "include_in_score": False,
    }
