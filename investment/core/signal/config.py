"""signal / stance 配置加载（P6.1 / V2.1）。"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any, Dict, Optional

from core.paths import DATA_DIR

SIGNAL_CONFIG_PATH = os.path.join(DATA_DIR, "signal_config.json")

DEFAULT_SIGNAL_CONFIG: Dict[str, Any] = {
    "version": 3,
    "weights": {
        "momentum": 0.22,
        "volume_price": 0.14,
        "relative_strength": 0.11,
        "volatility": 0.06,
        "reversal": 0.07,
        "liquidity": 0.06,
        "value": 0.05,
        "quality": 0.04,
        "ma_slope": 0.03,
        "technical_pattern": 0.02,
        "weekly_confirm": 0.01,
        "gap_risk": 0.01,
        "size": 0.03,
        "earnings_yield": 0.03,
        "growth": 0.03,
        "dividend": 0.02,
        "money_flow": 0.0,
        "amihud": 0.02,
        "idio_momentum": 0.03,
        "alt_sentiment": 0.02,
    },
    "factor_groups": {
        "trend": ["momentum", "ma_slope", "technical_pattern", "weekly_confirm"],
        "value_quality": ["value", "earnings_yield", "quality", "growth", "dividend"],
        "liquidity_flow": ["liquidity", "money_flow", "amihud", "volume_price"],
        "risk": ["volatility", "gap_risk"],
        "residual": ["relative_strength", "idio_momentum", "size"],
    },
    "hard_reject": {
        "min_bars": 2,
        "mom3_gain_max_pct": 15.0,
        "mom3_loss_min_pct": -12.0,
    },
    "rank": {
        "min_score": 55.0,
        "default_limit": 8,
    },
    "stance_thresholds": {
        "avoid": 45.0,
        "wait": 55.0,
        "probe": 68.0,
    },
    "invalidation": {
        "stop_pct": 0.03,
    },
    "relative_strength": {
        "window_days": 20,
        "min_compare_days": 5,
        "fallback_to_last_change": True,
    },
    "regime": {
        "enabled": True,
        "weak_trend_days": 20,
        "weak_trend_threshold_pct": -3.0,
        "score_penalty": 5.0,
    },
    "fundamentals": {
        "enabled": True,
        "fetch_on_score": True,
        "use_in_ic_experiment": True,
        "use_in_backtest": True,
        "pit_mode": "as_of",
        "missing_as_of_policy": "zero_weight",
    },
    "cross_section": {
        "neutralize": True,
        "method": "zscore",
        "min_samples": 3,
        "zscore_scale": 10.0,
        "industry_residual": True,
        "size_residual": True,
        "size_buckets": 3,
    },
}

_cached: Optional[Dict[str, Any]] = None


def _deep_merge(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for key, val in (override or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def load_signal_config(*, reload: bool = False) -> Dict[str, Any]:
    global _cached
    if _cached is not None and not reload:
        return deepcopy(_cached)

    cfg = deepcopy(DEFAULT_SIGNAL_CONFIG)
    path = os.environ.get("INVESTMENT_SIGNAL_CONFIG", SIGNAL_CONFIG_PATH)
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
        cfg = _deep_merge(cfg, user)
    _cached = cfg
    return deepcopy(cfg)


def get_stance_thresholds(config: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    cfg = config or load_signal_config()
    raw = cfg.get("stance_thresholds") or {}
    return {
        "avoid": float(raw.get("avoid", 45)),
        "wait": float(raw.get("wait", 55)),
        "probe": float(raw.get("probe", 68)),
    }


def get_rank_defaults(config: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    cfg = config or load_signal_config()
    raw = cfg.get("rank") or {}
    return {
        "min_score": float(raw.get("min_score", 55)),
        "default_limit": int(raw.get("default_limit", 8)),
    }


def read_signal_config_file(*, reload: bool = True) -> Dict[str, Any]:
    """读取 signal_config（合并后配置 + 文件元信息，只读）。"""
    path = os.environ.get("INVESTMENT_SIGNAL_CONFIG", SIGNAL_CONFIG_PATH)
    exists = os.path.isfile(path)
    raw = None
    if exists:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    merged = load_signal_config(reload=reload)
    return {
        "success": True,
        "exists": exists,
        "path": path,
        "config": merged,
        "raw": raw,
        "readonly": True,
        "note": "修改须手动编辑 signal_config.json；IC/阈值建议仅导出 diff，不自动写盘。",
    }
