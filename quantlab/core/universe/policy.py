"""可交易宇宙门槛配置。"""

from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from typing import Any, Dict, Optional

from core.paths import DATA_DIR

logger = logging.getLogger(__name__)

POLICY_PATH = os.path.join(DATA_DIR, "tradable_universe_policy.json")

DEFAULT_POLICY: Dict[str, Any] = {
    "version": "tradable_v1",
    "note": "可交易宇宙 P0 门槛",
    "exclude_st": True,
    "exclude_index": True,
    "require_spot_row": True,
    "require_name": True,
    "min_float_mv": 2_000_000_000.0,
    "min_amount": 30_000_000.0,
    "amount_lookback_days": 20,
    "prefer_bars_avg_amount": False,
    "min_listing_bars": 60,
    "min_price": 1.0,
    "max_price": 5000.0,
    "drop_suspended_keywords": True,
}


def default_tradable_policy() -> Dict[str, Any]:
    return deepcopy(DEFAULT_POLICY)


def load_tradable_policy(*, path: Optional[str] = None) -> Dict[str, Any]:
    """读 ``tradable_universe_policy.json``，缺文件用内置默认。"""
    out = default_tradable_policy()
    p = path or POLICY_PATH
    if not os.path.isfile(p):
        return out
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            out.update(data)
    except (OSError, json.JSONDecodeError, TypeError):
        logger.debug("load_tradable_policy failed path=%s", p, exc_info=True)
    return out


def watching_subset_enforced() -> bool:
    """观察池须 ⊆ 研究池。默认开；``QUANTLAB_WATCHING_SUBSET_OF_RESEARCH=0`` 关闭。"""
    raw = str(os.environ.get("QUANTLAB_WATCHING_SUBSET_OF_RESEARCH", "1") or "1").strip()
    return raw not in ("0", "false", "False", "no", "OFF", "off")
