"""双层 ŷ fusion：人审写盘（只改 dual_score.*，永不改 weights / scoring）。"""

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
        "w_oo": cfg.get("w_oo"),
        "w_eod": cfg.get("w_oo"),
        "w_tau": cfg.get("w_tau"),
        "w_mode": cfg.get("w_mode"),
        "theme_w_tau_boost": cfg.get("theme_w_tau_boost"),
        "eod_residual_var": cfg.get("eod_residual_var"),
        "enable_cascade_shadow": bool(cfg.get("enable_cascade_shadow")),
        "enable_tau_shadow_book": bool(cfg.get("enable_tau_shadow_book")),
        "enable_minute_tau": bool(cfg.get("enable_minute_tau")),
        "y_spec": cfg.get("y_spec"),
        "note": (
            "主 score 仍 ŷ_oo（入池地板 min_predicted_score）。"
            "盘中簿 ŷ_trade=w_oo·ŷ_oo+w_tau·(缺口∘ŷ_τ)；收盘 eod_next 剥离 τ。"
            "调仓 rank_lots 用独立 fusion_w_oo/oc，不是 dual_score.w_*。"
            "买入另过 τ 闸。"
        ),
    }


def save_dual_score(
    *,
    fusion_mode: Optional[str] = None,
    min_predicted_score_tau: Optional[float] = None,
    w_oo: Optional[float] = None,
    w_eod: Optional[float] = None,
    w_tau: Optional[float] = None,
    w_mode: Optional[str] = None,
    block_buy_if_tau_missing: Optional[bool] = None,
    note: str = "",
) -> Dict[str, Any]:
    """人审写入 ``signal_config.dual_score``；清缓存。"""
    from core.signal.config import get_signal_config_path, load_signal_config
    from core.signal.dual_score import normalize_fusion_mode

    path = get_signal_config_path()
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
    w_write = w_oo if w_oo is not None else w_eod
    if w_write is not None:
        dual["w_oo"] = float(w_write)
        dual["w_eod"] = float(w_write)
        changed["w_oo"] = float(w_write)
    if w_tau is not None:
        dual["w_tau"] = float(w_tau)
        changed["w_tau"] = float(w_tau)
    if w_mode is not None:
        mode_w = str(w_mode or "fixed").strip().lower()
        if mode_w not in ("fixed", "theme_boost", "variance"):
            mode_w = "fixed"
        dual["w_mode"] = mode_w
        changed["w_mode"] = mode_w
    if block_buy_if_tau_missing is not None:
        dual["block_buy_if_tau_missing"] = bool(block_buy_if_tau_missing)
        changed["block_buy_if_tau_missing"] = bool(block_buy_if_tau_missing)

    if not changed:
        return {
            "success": False,
            "error": "未提供可写字段（fusion_mode / min_predicted_score_tau / w_oo / w_tau / w_mode）",
            "signal_config_weights_touched": False,
        }

    # 融合分权重兜底
    if dual.get("w_oo") is None and dual.get("w_eod") is not None:
        dual["w_oo"] = dual.get("w_eod")
    if dual.get("w_oo") is None:
        dual["w_oo"] = 0.5
    dual["w_eod"] = dual.get("w_oo")
    if dual.get("w_tau") is None:
        dual["w_tau"] = 0.5
    dual.pop("minute_tau_hm", None)
    dual.pop("minute_tau_grid", None)
    dual.pop("nowcast", None)

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
