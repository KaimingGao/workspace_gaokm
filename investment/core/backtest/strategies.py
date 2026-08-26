"""回测策略注册表（P9.3）。"""


import logging

logger = logging.getLogger(__name__)
from copy import deepcopy
from typing import Any, Dict, List, Optional

DEFAULT_STRATEGY = "short"

# 旧 ID → 新 canonical（纸面 / promote / API 入参仍可认旧名）
STRATEGY_ALIASES: Dict[str, str] = {
    "signal_v1": "short",
    "short_v1": "short",
    "signal_v1_conservative": "short_conservative",
}


def _strategy_t0_overlay(**overrides: Any) -> Dict[str, Any]:
    from core.execution import DEFAULT_T0_OVERLAY

    return {**DEFAULT_T0_OVERLAY, **overrides}


STRATEGY_SPECS: Dict[str, Dict[str, Any]] = {
    "short": {
        "label": "短线评分",
        "description": "短线主策略：组 β → ŷ 选股；持有约 3 日；组合最多 20 只（限额见卡片，ŷ 门槛见页脚）",
        "params": {
            "horizon_days": 3,
            # heuristic_score 单票 walk-forward 回测默认（0–100）；生产选股用 scoring.min_predicted_score
            "min_score": 65.0,
            "min_history": 12,
            "data_mode": "full",
            "apply_costs": True,
        },
        "lifecycle": {
            "version": "1.2.0",
            "cost_model": "simple_cn",
            "paper_rules": {
                # 选股门槛真源 = signal_config.scoring（ŷ 滞回）；勿再写 0–100 min_score
                "max_positions": 20,
                "position_pct": 0.15,
                "horizon_days": 3,
                "signal_limit": 8,
                "weight_mode": "score_budget",
            },
            "execution": {
                "version": "1.0.0",
                "overlays": {
                    "t0": _strategy_t0_overlay(),
                },
                "coupling": {"t0_vs_stance": "independent"},
                "runtime_defaults": {
                    "paper_direction_fallback": "dual_y",
                    "backtest_direction_fallback": "dual_y",
                    "backtest_path_mode_no_minute": "first_touch",
                    "backtest_path_mode_with_minute": "first_touch",
                },
                "rebalance_timing": {
                    "execution_mode": "next_open",
                    "open_fill_after_hm": "09:15",
                    "open_fill_until_hm": "10:00",
                },
            },
            "risk": {
                "max_drawdown_pct": 20.0,
                "target_drawdown_pct": 12.0,
                "max_position_pct": 25.0,
                "max_sector_pct": 40.0,
                "max_positions": 20,
            },
        },
    },
    "short_conservative": {
        "label": "保守短线",
        "description": "更紧组合限额（最多 15 只），控制换手与回撤；选股仍走组 β → ŷ",
        "params": {
            "horizon_days": 3,
            # heuristic_score 单票 walk-forward 回测默认（0–100）；生产选股用 scoring.min_predicted_score
            "min_score": 72.0,
            "min_history": 12,
            "data_mode": "full",
            "apply_costs": True,
        },
        "lifecycle": {
            "version": "1.2.0",
            "cost_model": "simple_cn",
            "paper_rules": {
                "max_positions": 15,
                "position_pct": 0.12,
                "horizon_days": 3,
                "signal_limit": 5,
                "weight_mode": "score_budget",
            },
            "execution": {
                "version": "1.0.0",
                "overlays": {
                    "t0": _strategy_t0_overlay(t0_ratio=0.35),
                },
                "coupling": {"t0_vs_stance": "independent"},
                "runtime_defaults": {
                    "paper_direction_fallback": "dual_y",
                    "backtest_direction_fallback": "dual_y",
                    "backtest_path_mode_no_minute": "first_touch",
                    "backtest_path_mode_with_minute": "first_touch",
                },
                "rebalance_timing": {
                    "execution_mode": "next_open",
                    "open_fill_after_hm": "09:15",
                    "open_fill_until_hm": "10:00",
                },
            },
            "risk": {
                "max_drawdown_pct": 15.0,
                "target_drawdown_pct": 10.0,
                "max_position_pct": 20.0,
                "max_sector_pct": 35.0,
                "max_positions": 15,
            },
        },
    },
}


def resolve_strategy_id(name: Optional[str] = None) -> str:
    key = (name or DEFAULT_STRATEGY).strip() or DEFAULT_STRATEGY
    return STRATEGY_ALIASES.get(key, key)


def list_strategies() -> List[Dict[str, Any]]:
    rows = []
    for name, spec in STRATEGY_SPECS.items():
        rows.append(
            {
                "name": name,
                "label": spec.get("label"),
                "description": spec.get("description"),
                "params": deepcopy(spec.get("params") or {}),
            }
        )
    return rows


def get_strategy(name: str) -> Dict[str, Any]:
    key = resolve_strategy_id(name)
    if key not in STRATEGY_SPECS:
        raise KeyError(f"未知策略: {name}（可选: {', '.join(STRATEGY_SPECS)}）")
    spec = STRATEGY_SPECS[key]
    return {
        "name": key,
        "label": spec.get("label"),
        "description": spec.get("description"),
        "params": deepcopy(spec.get("params") or {}),
    }


def merge_strategy_params(strategy: str, overrides: Optional[dict] = None) -> Dict[str, Any]:
    base = get_strategy(strategy)["params"]
    out = deepcopy(base)
    for k, v in (overrides or {}).items():
        if v is not None and v != "":
            out[k] = v
    return out


def run_strategy_backtest(
    bars: List[dict],
    strategy: str = DEFAULT_STRATEGY,
    *,
    overrides: Optional[dict] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    from core.backtest.engine import backtest_signal_on_bars

    canonical = resolve_strategy_id(strategy)
    params = merge_strategy_params(canonical, overrides)
    params.update(kwargs)
    result = backtest_signal_on_bars(bars, **params)
    result["strategy"] = canonical
    result["strategy_label"] = get_strategy(canonical).get("label")
    return result
