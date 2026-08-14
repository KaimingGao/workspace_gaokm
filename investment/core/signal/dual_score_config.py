"""双层 ŷ fusion：人审写盘（只改 dual_score.*，永不改 weights / scoring）。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from core.io_atomic import atomic_write_json


def read_dual_score_public() -> Dict[str, Any]:
    from core.signal.dual_score import get_dual_score_cfg

    cfg = get_dual_score_cfg()
    return {
        "fusion_mode": cfg.get("fusion_mode"),
        "tau": cfg.get("tau"),
        "min_predicted_score_tau": cfg.get("min_predicted_score_tau"),
        "block_buy_if_tau_missing": bool(cfg.get("block_buy_if_tau_missing")),
        "w_eod": cfg.get("w_eod"),
        "w_tau": cfg.get("w_tau"),
        "enable_tau_shadow_book": bool(cfg.get("enable_tau_shadow_book")),
        "enable_minute_tau": bool(cfg.get("enable_minute_tau")),
        "minute_tau_hm": cfg.get("minute_tau_hm"),
        "y_spec": cfg.get("y_spec"),
        "note": "簿排序用 ŷ_trade=w·ŷ_EOD_rem+w·ŷ_τ；主 score 仍 EOD；买入另过 τ 闸",
    }


def save_dual_score(
    *,
    fusion_mode: Optional[str] = None,
    min_predicted_score_tau: Optional[float] = None,
    w_eod: Optional[float] = None,
    w_tau: Optional[float] = None,
    block_buy_if_tau_missing: Optional[bool] = None,
    note: str = "",
) -> Dict[str, Any]:
    """人审写入 ``signal_config.dual_score``；清缓存。"""
    from core.paths import SIGNAL_CONFIG_PATH
    from core.signal.config import SIGNAL_CONFIG_PATH as CFG_PATH
    from core.signal.config import load_signal_config
    from core.signal.dual_score import normalize_fusion_mode

    path = os.environ.get("INVESTMENT_SIGNAL_CONFIG", SIGNAL_CONFIG_PATH or CFG_PATH)
    raw: Dict[str, Any] = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f) or {}

    dual = dict(raw.get("dual_score") or {})
    changed: Dict[str, Any] = {}

    if fusion_mode is not None:
        mode = normalize_fusion_mode(fusion_mode)
        dual["fusion_mode"] = mode
        changed["fusion_mode"] = mode
    else:
        # 产品仅正交加权
        dual["fusion_mode"] = "blend"
    if min_predicted_score_tau is not None:
        dual["min_predicted_score_tau"] = float(min_predicted_score_tau)
        changed["min_predicted_score_tau"] = float(min_predicted_score_tau)
    if w_eod is not None:
        dual["w_eod"] = float(w_eod)
        changed["w_eod"] = float(w_eod)
    if w_tau is not None:
        dual["w_tau"] = float(w_tau)
        changed["w_tau"] = float(w_tau)
    if block_buy_if_tau_missing is not None:
        dual["block_buy_if_tau_missing"] = bool(block_buy_if_tau_missing)
        changed["block_buy_if_tau_missing"] = bool(block_buy_if_tau_missing)

    if not changed:
        return {
            "success": False,
            "error": "未提供可写字段（fusion_mode / min_predicted_score_tau / w_eod / w_tau）",
            "signal_config_weights_touched": False,
        }

    # 融合分权重兜底
    if dual.get("w_eod") is None:
        dual["w_eod"] = 0.5
    if dual.get("w_tau") is None:
        dual["w_tau"] = 0.5

    raw["dual_score"] = dual
    atomic_write_json(path, raw)

    import core.signal.config as cfg_mod

    cfg_mod._cached = None
    load_signal_config(reload=True)

    return {
        "success": True,
        "task": "save_dual_score",
        "changed": changed,
        "dual_score": read_dual_score_public(),
        "path": path,
        "note": note
        or "仅改 dual_score；weights / scoring 未触碰。刷簿/预演后生效。",
        "signal_config_weights_touched": False,
    }
