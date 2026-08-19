"""人审写入 stance_thresholds（只改立场分档，永不改 weights / scoring）。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
import json
import os
from typing import Any, Dict, Optional

from core.io_atomic import atomic_write_json

STANCE_KEYS = ("avoid", "wait", "probe")
# ŷ% 合理带；遗留 0–100 用另一套上限
_YHAT_ABS_MAX = 10.0
_HEURISTIC_ABS_MAX = 100.0
_HEURISTIC_AVOID_FLOOR = 10.0


def _is_predicted_scale(avoid: float) -> bool:
    return float(avoid) < _HEURISTIC_AVOID_FLOOR


def save_stance_thresholds(
    *,
    avoid: Optional[float] = None,
    wait: Optional[float] = None,
    probe: Optional[float] = None,
    note: str = "",
) -> Dict[str, Any]:
    """人审写入 ``signal_config.stance_thresholds``；清缓存；不碰 weights/scoring。"""
    from core.paths import SIGNAL_CONFIG_PATH
    from core.signal.config import SIGNAL_CONFIG_PATH as CFG_PATH
    from core.signal.config import get_stance_thresholds, load_signal_config

    path = os.environ.get("INVESTMENT_SIGNAL_CONFIG", SIGNAL_CONFIG_PATH or CFG_PATH)
    raw: Dict[str, Any] = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f) or {}

    current = dict(raw.get("stance_thresholds") or {})
    if not current:
        current = dict(get_stance_thresholds(load_signal_config(reload=True)) or {})

    merged: Dict[str, float] = {}
    for key, val in (("avoid", avoid), ("wait", wait), ("probe", probe)):
        if val is None:
            if key not in current:
                return {
                    "success": False,
                    "error": f"缺少阈值 {key}",
                    "signal_config_weights_touched": False,
                }
            merged[key] = float(current[key])
        else:
            merged[key] = float(val)

    a, w, p = merged["avoid"], merged["wait"], merged["probe"]
    if not (a < w < p):
        return {
            "success": False,
            "error": f"须满足 avoid < wait < probe（收到 {a} / {w} / {p}）",
            "signal_config_weights_touched": False,
        }

    predicted = _is_predicted_scale(a)
    lim = _YHAT_ABS_MAX if predicted else _HEURISTIC_ABS_MAX
    for key, val in merged.items():
        if abs(val) > lim:
            return {
                "success": False,
                "error": f"{key}={val} 超出允许范围 ±{lim}",
                "signal_config_weights_touched": False,
            }

    before = {k: round(float(current.get(k, merged[k])), 4) for k in STANCE_KEYS}
    after = {k: round(float(merged[k]), 4) for k in STANCE_KEYS}
    if before == after:
        return {
            "success": False,
            "error": "与当前 stance_thresholds 相同，无需写入",
            "signal_config_weights_touched": False,
            "stance_thresholds": after,
        }

    raw["stance_thresholds"] = after
    atomic_write_json(path, raw)

    import core.signal.config as cfg_mod

    cfg_mod._cached = None
    load_signal_config(reload=True)

    return {
        "success": True,
        "task": "save_stance_thresholds",
        "changed": {
            k: {"from": before[k], "to": after[k]}
            for k in STANCE_KEYS
            if before[k] != after[k]
        },
        "stance_thresholds": after,
        "score_scale": "predicted" if predicted else "heuristic_0_100",
        "path": path,
        "note": note
        or "仅改 stance_thresholds；weights / scoring 未触碰",
        "signal_config_weights_touched": False,
        "scoring_touched": False,
    }
