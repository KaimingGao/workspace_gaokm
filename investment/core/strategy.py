"""策略规格与晋级闸门（Q2）。

内部 canonical：策略以版本化 StrategySpec 描述；回测 / 模拟调仓共用。
promote：research 配置 → 人工确认后写入 paper 使用的策略快照，禁止静默覆盖。
"""

from __future__ import annotations
from core.numbers import now_iso_local as _now_iso

import json
import logging
import os
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from core.backtest.strategies import (
    DEFAULT_STRATEGY,
    STRATEGY_SPECS,
    get_strategy,
    list_strategies,
    merge_strategy_params,
    resolve_strategy_id,
)
from core.paths import DATA_DIR
from core.io_atomic import atomic_write_json

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
    return {
        "strategy_id": key,
        "version": str(extra.get("version") or "1.0.0"),
        "label": base.get("label"),
        "description": base.get("description"),
        "params": deepcopy(base.get("params") or {}),
        "paper_rules": paper_rules,
        "execution": execution,
        "cost_model": extra.get("cost_model") or "simple_cn",
        "risk": deepcopy(
            extra.get("risk")
            or {
                "max_drawdown_pct": 20.0,
                "max_position_pct": 25.0,
                "max_positions": int(paper_rules.get("max_positions") or 5),
            }
        ),
    }


def list_strategy_specs() -> List[Dict[str, Any]]:
    return [get_strategy_spec(s["name"]) for s in list_strategies()]


# 纸面 rules 中不得再作为 live 选股门槛的遗留键（0–100 时代）
_LEGACY_PAPER_SCORE_KEYS = (
    "min_score",
    "add_score",
    "min_hold_score",
    "reduce_score",
)


def apply_strategy_to_paper(paper: dict, strategy: str = DEFAULT_STRATEGY) -> Dict[str, Any]:
    """把策略规格合并进 paper.rules / cost_model / t0 overlay（不落盘）。

    不合并 0–100 的 min_score/add_score 等；选股门槛真源为 signal_config.scoring。
    """
    spec = get_strategy_spec(strategy)
    rules = dict(paper.get("rules") or {})
    pr = deepcopy(spec.get("paper_rules") or {})
    for k in _LEGACY_PAPER_SCORE_KEYS:
        pr.pop(k, None)
        rules.pop(k, None)
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
    with open(p, "r", encoding="utf-8") as f:
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
        from core.signal.factor_health import guard_weights_for_promote

        cfg = load_signal_config() or {}
        guard = guard_weights_for_promote(cfg.get("weights") or {}, force=False)
        if guard.get("blocked"):
            # 策略晋级不改 weights；仅附警告到 note，不硬拦（权重门在写权路径）
            note = (note or "") + (
                f" · [factor_health warn] {guard.get('error') or 'proxy weight'}"
            )
    except Exception:
        logger.warning("factor_health guard failed during promote", exc_info=True)
    spec = get_strategy_spec(strategy)
    if overrides:
        params = merge_strategy_params(strategy, overrides)
        spec["params"] = params
        for k, v in overrides.items():
            if k in _LEGACY_PAPER_SCORE_KEYS:
                # 0–100 选股门不进 promote；真源为 signal_config.scoring
                continue
            if k in (spec.get("paper_rules") or {}) or k in (
                "max_positions",
                "position_pct",
                "signal_limit",
                "horizon_days",
                "weight_mode",
            ):
                spec.setdefault("paper_rules", {})[k] = v
    # 防御：规格或旧 promote 残留
    for k in _LEGACY_PAPER_SCORE_KEYS:
        (spec.get("paper_rules") or {}).pop(k, None)
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
        "sell_trigger_pct": t0.get("sell_trigger_pct"),
        "buy_trigger_pct": t0.get("buy_trigger_pct"),
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
    except Exception:
        logger.warning("append_ttm_event failed during promote", exc_info=True)
    try:
        from core.live_config_manifest import write_live_config_manifest

        write_live_config_manifest(
            note=f"after promote_strategy {spec.get('strategy_id')}"
        )
    except Exception:
        logger.warning("write_live_config_manifest failed during promote", exc_info=True)
    return entry
