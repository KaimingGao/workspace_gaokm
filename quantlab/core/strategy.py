"""策略规格与晋级闸门（Q2）。

内部 canonical：策略以版本化 StrategySpec 描述；回测 / 模拟调仓共用。
promote：research 配置 → 人工确认后写入 paper 使用的策略快照，禁止静默覆盖。
"""

import json
import logging
import os
from copy import deepcopy
from typing import Any, Dict, List, Optional

from core.numbers import now_iso_local as _now_iso

logger = logging.getLogger(__name__)

from core.backtest.strategies import (
    DEFAULT_STRATEGY,
    STRATEGY_SPECS,
    get_strategy,
    list_strategies,
    merge_strategy_params,
    resolve_strategy_id,
)
from core.io_atomic import atomic_write_json
from core.paths import DATA_DIR

# 重新导出，保持单入口
__all__ = [
    "DEFAULT_STRATEGY",
    "STRATEGY_SPECS",
    "get_strategy",
    "list_strategies",
    "merge_strategy_params",
    "resolve_strategy_id",
    "get_strategy_spec",
    "list_strategy_specs",
    "backtest_portfolio_defaults",
    "apply_strategy_to_paper",
    "promote_strategy",
    "load_promoted",
    "PROMOTED_PATH",
]

PROMOTED_PATH = os.path.join(DATA_DIR, "strategy_promoted.json")


def get_strategy_spec(name: str = DEFAULT_STRATEGY) -> Dict[str, Any]:
    """完整 StrategySpec：回测参数 + 模拟规则 + Execution + 成本/风控默认。"""
    from core.execution import execution_from_lifecycle

    base = get_strategy(name)
    key = base["name"]
    extra = (STRATEGY_SPECS.get(key) or {}).get("lifecycle") or {}
    paper_rules = deepcopy(extra.get("paper_rules") or {})
    # paper_rules 内嵌 t0 仅作兼容；正式位置在 execution.overlays.t0
    paper_rules.pop("t0", None)
    execution = execution_from_lifecycle(extra)
    risk = deepcopy(extra.get("risk") or {})
    # 补全风控真源，避免回测/纸面各自散落 or 25/40/5
    if risk.get("max_drawdown_pct") is None:
        risk["max_drawdown_pct"] = 20.0
    if risk.get("max_position_pct") is None:
        risk["max_position_pct"] = 25.0
    if risk.get("max_sector_pct") is None:
        risk["max_sector_pct"] = 40.0
    if risk.get("max_positions") is None:
        risk["max_positions"] = int(paper_rules.get("max_positions") or 20)
    if risk.get("weight_mode") is None:
        risk["weight_mode"] = str(paper_rules.get("weight_mode") or "score_budget")
    return {
        "strategy_id": key,
        "version": str(extra.get("version") or "1.0.0"),
        "label": base.get("label"),
        "description": base.get("description"),
        "params": deepcopy(base.get("params") or {}),
        "paper_rules": paper_rules,
        "execution": execution,
        "cost_model": extra.get("cost_model") or "simple_cn",
        "risk": risk,
    }


def backtest_portfolio_defaults(
    strategy: str = DEFAULT_STRATEGY,
) -> Dict[str, Any]:
    """Top-K / 组合回测默认：权重/限额/持有期对齐纸面；K 默认 3（研究用小组合，≠纸面 max_positions）。"""
    spec = get_strategy_spec(strategy)
    risk = spec.get("risk") or {}
    params = spec.get("params") or {}
    paper_rules = spec.get("paper_rules") or {}
    try:
        from core.signal.score_display import resolve_buy_floor

        min_pred = float(resolve_buy_floor())
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        min_pred = 1.0
    max_pos = int(
        risk.get("max_positions")
        or paper_rules.get("max_positions")
        or 20
    )
    return {
        "strategy_id": spec.get("strategy_id") or DEFAULT_STRATEGY,
        "top_k": 3,
        "top_k_cap": max(40, max_pos),
        "horizon_days": int(
            paper_rules.get("horizon_days")
            or params.get("horizon_days")
            or 3
        ),
        "weight_mode": str(
            risk.get("weight_mode")
            or paper_rules.get("weight_mode")
            or "score_budget"
        ),
        "max_position_pct": float(risk.get("max_position_pct") or 25.0),
        "max_sector_pct": float(risk.get("max_sector_pct") or 40.0),
        "max_positions": max_pos,
        "exclude_st": True,
        "min_predicted_score": min_pred,
        "min_score": float(params.get("min_score") or 55.0),
    }


def list_strategy_specs() -> List[Dict[str, Any]]:
    return [get_strategy_spec(s["name"]) for s in list_strategies()]


def apply_strategy_to_paper(paper: dict, strategy: str = DEFAULT_STRATEGY) -> Dict[str, Any]:
    """把策略规格合并进 paper.rules / cost_model / t0 overlay（不落盘）。"""
    spec = get_strategy_spec(strategy)
    rules = dict(paper.get("rules") or {})
    pr = deepcopy(spec.get("paper_rules") or {})
    rules.update(pr)
    exe = spec.get("execution") or {}
    t0_overlay = ((exe.get("overlays") or {}).get("t0")) or {}
    if t0_overlay:
        prev = dict(rules.get("t0") or {}) if isinstance(rules.get("t0"), dict) else {}
        if paper.get("t0_rules_locked"):
            # 账户锁定：保留账户键，缺省用 Spec 补齐
            merged_t0 = dict(t0_overlay)
            merged_t0.update(prev)
        else:
            # Spec 覆盖同名键；保留账户多写的键
            merged_t0 = dict(prev)
            merged_t0.update(t0_overlay)
        rules["t0"] = merged_t0
    timing = (exe.get("rebalance_timing") or {}) if isinstance(exe, dict) else {}
    mode = str(timing.get("execution_mode") or rules.get("execution_mode") or "next_open")
    rules["execution_mode"] = mode
    if isinstance(timing, dict) and timing:
        rules["execution"] = dict(rules.get("execution") or {})
        rules["execution"]["rebalance_timing"] = dict(timing)
    # 风控限额写入 paper.rules，与回测/optimize 同源
    risk = spec.get("risk") or {}
    for rk in (
        "max_position_pct",
        "max_sector_pct",
        "max_positions",
        "weight_mode",
        "max_drawdown_pct",
    ):
        if risk.get(rk) is not None and rules.get(rk) is None:
            rules[rk] = risk[rk]
    # 回测 params.min_score 不进纸面
    paper["rules"] = rules
    if not paper.get("cost_model") or paper.get("cost_model") == "zero":
        # 若账户仍为教学默认，跟随策略推荐成本（可被账户显式设置覆盖）
        if paper.get("cost_model_locked"):
            pass
        else:
            paper["cost_model"] = spec.get("cost_model") or paper.get("cost_model") or "simple_cn"
    paper["strategy_id"] = spec["strategy_id"]
    paper["strategy_version"] = spec["version"]
    paper["execution_version"] = (exe.get("version") if isinstance(exe, dict) else None) or "1.0.0"
    return spec


def load_promoted(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    p = path or PROMOTED_PATH
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def promote_strategy(
    strategy: str = DEFAULT_STRATEGY,
    *,
    note: str = "",
    path: Optional[str] = None,
    overrides: Optional[dict] = None,
) -> Dict[str, Any]:
    """显式晋级：写出 strategy_promoted.json，供模拟/日更读取。

    不静默改 signal_config.json；研究侧 diff 仍走原有 config 流程。
    """
    # FM0 · 当前 signal_config.weights 健康门（不写权，但晋级前可见）
    try:
        from core.signal.config import load_signal_config
        from core.signal.factors.meta.health import guard_weights_for_promote

        cfg = load_signal_config() or {}
        guard = guard_weights_for_promote(cfg.get("weights") or {}, force=False)
        if guard.get("blocked"):
            # 策略晋级不改 weights；仅附警告到 note，不硬拦（权重门在写权路径）
            note = (note or "") + (
                f" · [factor_health warn] {guard.get('error') or 'proxy weight'}"
            )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.warning("factor_health guard failed during promote", exc_info=True)
    spec = get_strategy_spec(strategy)
    if overrides:
        params = merge_strategy_params(strategy, overrides)
        spec["params"] = params
        for k, v in overrides.items():
            if k in (spec.get("paper_rules") or {}) or k in (
                "max_positions",
                "position_pct",
                "signal_limit",
                "horizon_days",
                "weight_mode",
            ):
                spec.setdefault("paper_rules", {})[k] = v
    entry = {
        "promoted_at": _now_iso(),
        "note": note or f"promote {spec['strategy_id']}@{spec['version']}",
        "spec": spec,
    }
    exe = spec.get("execution") or {}
    t0 = ((exe.get("overlays") or {}).get("t0")) or {}
    entry["execution_summary"] = {
        "t0_ratio": t0.get("t0_ratio"),
        "fill_mode": t0.get("fill_mode"),
        "coupling": (exe.get("coupling") or {}).get("t0_vs_stance"),
        "execution_version": exe.get("version"),
    }
    out = path or PROMOTED_PATH
    atomic_write_json(out, entry)
    try:
        from core.north_star import TTM_EVENT_PAPER, append_ttm_event

        append_ttm_event(
            TTM_EVENT_PAPER,
            ref=str(spec.get("strategy_id") or strategy),
            meta={
                "version": spec.get("version"),
                "note": entry.get("note"),
                "execution": entry.get("execution_summary"),
            },
        )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.warning("append_ttm_event failed during promote", exc_info=True)
    try:
        from core.live_config_manifest import write_live_config_manifest

        write_live_config_manifest(
            note=f"after promote_strategy {spec.get('strategy_id')}"
        )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.warning("write_live_config_manifest failed during promote", exc_info=True)
    return entry
