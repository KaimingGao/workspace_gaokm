"""Y 轨：ŷ 买卖门槛人审写盘（只改 scoring.*，永不改 weights）。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from core.io_atomic import atomic_write_json


def save_scoring_floors(
    *,
    min_predicted_score: Optional[float] = None,
    min_hold_predicted_score: Optional[float] = None,
    note: str = "",
) -> Dict[str, Any]:
    """人审写入 ``signal_config.scoring`` 滞回门槛；清缓存。

    传 ``None`` 表示该项不改；显式要「关闭门槛」请传字符串 ``\"null\"`` 或使用
    ``clear_buy`` / ``clear_hold``（由 API 层解析）。
    """
    from core.paths import SIGNAL_CONFIG_PATH
    from core.signal.config import SIGNAL_CONFIG_PATH as CFG_PATH
    from core.signal.config import load_signal_config
    from core.signal.score_display import resolve_buy_floor, resolve_hold_floor

    path = os.environ.get("INVESTMENT_SIGNAL_CONFIG", SIGNAL_CONFIG_PATH or CFG_PATH)
    raw: Dict[str, Any] = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f) or {}

    scoring = dict(raw.get("scoring") or {})
    scoring.setdefault("rank_mode", "predicted_score")
    changed: Dict[str, Any] = {}

    if min_predicted_score is not None:
        scoring["min_predicted_score"] = float(min_predicted_score)
        changed["min_predicted_score"] = float(min_predicted_score)
    if min_hold_predicted_score is not None:
        scoring["min_hold_predicted_score"] = float(min_hold_predicted_score)
        changed["min_hold_predicted_score"] = float(min_hold_predicted_score)

    if not changed:
        return {
            "success": False,
            "error": "未提供可写门槛（min_predicted_score / min_hold_predicted_score）",
            "signal_config_weights_touched": False,
        }

    raw["scoring"] = scoring
    atomic_write_json(path, raw)

    # 清 load_signal_config 缓存
    import core.signal.config as cfg_mod

    cfg_mod._cached = None
    load_signal_config(reload=True)

    return {
        "success": True,
        "task": "save_scoring_floors",
        "changed": changed,
        "scoring_floors": {
            "min_predicted_score": resolve_buy_floor(),
            "min_hold_predicted_score": resolve_hold_floor(),
            "unit": "predicted_score_pct",
        },
        "path": path,
        "note": note or "仅改 scoring 滞回门槛；weights 未触碰",
        "signal_config_weights_touched": False,
    }
