"""ExecutionSpec：调仓 + overlays（做 T）+ coupling。

合并顺序（后者覆盖前者非空键）：
  DEFAULT_EXECUTION → StrategySpec.lifecycle.execution → paper.rules
  → request_override → channel runtime_defaults（仅填未显式声明的方向/路径）

纸面 / 回测入口应只读 resolve_effective_*，禁止各自硬编码 direction/path_mode。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
import hashlib
import os
import json
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from core.t0.config import drop_dead_t0_keys, load_t0_rules

DEFAULT_COUPLING: Dict[str, Any] = {
    "t0_vs_stance": "independent",  # independent | skip_if_avoid | only_if_hold
}

COUPLING_MODES = frozenset({"independent", "skip_if_avoid", "only_if_hold"})

# 纸面可写的 T0 覆盖键（防误写内部字段）
ALLOWED_T0_PATCH_KEYS = frozenset(
    {
        "enabled",
        "t0_ratio",
        "must_cover_same_day",
        "must_cover_same_day_sell_then_buy",
        "must_cover_same_day_buy_then_sell",
        "lot_size",
        "ref",
        "fill_mode",
        "fill_mode_sell_then_buy",
        "fill_mode_buy_then_sell",
        "direction",
        "path_mode",
        "minute_period",
        "min_range_pct",
        "min_range_pct_sell_then_buy",
        "min_range_pct_buy_then_sell",
        "use_atr",
        "atr_window",
        "atr_sell_mult",
        "atr_buy_mult",
        "dir_enter",
        "auto_strong_pct",
        "auto_weak_pct",
        "w_gap",
        "w_yclose_loc",
        "w_mom3",
        "w_gap_atr",
        "y_trade_enter",
        "y_trade_strong",
        "y_eod_prior",
        "y_eod_enter",
        "y_eod_strong",
        "y_trade_floor",
        "y_eod_tau_sign_gate",
        "y_trade_tau_sign_gate",
        "y_tau_enter",
        "y_tau_enter_strong",
        "y_tau_enter_sell_then_buy",
        "y_tau_enter_buy_then_sell",
        "y_enter_enabled",
        "y_enter_alt_enabled",
        "r_tau_enter",
        "r_tau_enter_alt",
        "y_tau_enter_alt",
        "y_path_enter_alt",
        "y_on_risk",
        "y_on_allow",
        "y_block_tau_nowcast_sign",
        "y_nc_enter",
        "y_nc_strong",
        "y_nowcast_enter",
        "y_tau_map",
        "y_use_path",
        "y_tau_leg1_prior",
        "y_tau_leg1_prior_mode",
        "y_tau_leg1_prior_risk",
        "y_tau_leg1_prior_shift_scale",
        "y_path_enter",
        "y_path_enter_sell_then_buy",
        "y_path_enter_buy_then_sell",
        "y_path_strong",
        "y_complexity_max",
        "y_cx_max",
        "y_tpd_max",
        "y_complexity_max_alt",
        "y_tpd_max_alt",
        "y_path_required",
        "y_gap_tier_mode",
        "y_gap_tier_pct",
        "y_nowcast_oc_gate",
        "t0_close_band_delta_pct",
        "t0_price_space_gate",
        "t0_price_space_max_dev_pct",
        "t0_price_space_prev_dev_pct",
        "t0_round_ratio",
        "t0_max_position_pct",
        "y_tau_exit_price_skip",
        "y_tau_exit_price_mult",
        "y_tau_exit_price_bias",
        "y_tau_exit_price_move_min",
        "y_tau_exit_price_move_max",
        "y_tau_exit_price_skip_buy_then_sell",
        "y_tau_exit_price_mult_buy_then_sell",
        "y_tau_exit_price_bias_buy_then_sell",
        "y_tau_exit_price_move_min_buy_then_sell",
        "y_tau_exit_price_move_max_buy_then_sell",
        "y_tau_exit_price_skip_sell_then_buy",
        "y_tau_exit_price_mult_sell_then_buy",
        "y_tau_exit_price_bias_sell_then_buy",
        "y_tau_exit_price_move_min_sell_then_buy",
        "y_tau_exit_price_move_max_sell_then_buy",
        "y_ratio_boost_cap",
        "y_ratio_cut",
        "y_score_source",
        "t0_pm_degrade",
        "t0_pm_degrade_sell_then_buy",
        "t0_pm_degrade_buy_then_sell",
        "t0_pm_chase_interval_min",
        "t0_pm_chase_interval_min_sell_then_buy",
        "t0_pm_chase_interval_min_buy_then_sell",
        "t0_pm_chase_cap_leg1_sell_then_buy",
        "t0_pm_chase_cap_leg1_buy_then_sell",
        "t0_stop_pct_buy_then_sell",
        "t0_stop_pct_sell_then_buy",
        "t0_stop_arm_bars",
        "t0_stop_on_close",
        "t0_slots_enabled",
        "t0_slots",
        "t0_slots_max_rounds",
    }
)

DEFAULT_RUNTIME: Dict[str, Any] = {
    "paper_direction_fallback": "dual_y",
    "backtest_direction_fallback": "dual_y",
    # 纸面/回测：强制 first_touch（已删除日线模拟）
    "backtest_path_mode_no_minute": "first_touch",
    "backtest_path_mode_with_minute": "first_touch",
}

# 生产默认 overlay：钉住与现行纸面/回测一致的关键行为；其余继承 DEFAULT_T0_RULES
DEFAULT_T0_OVERLAY: Dict[str, Any] = {
    "enabled": True,
    "t0_ratio": 1.0,
    "fill_mode": "trigger",
    "fill_mode_sell_then_buy": "trigger",
    "fill_mode_buy_then_sell": "trigger",
    "use_atr": False,
    "must_cover_same_day": True,
    "must_cover_same_day_sell_then_buy": True,
    "must_cover_same_day_buy_then_sell": True,
    "min_range_pct": 0.0,
    "min_range_pct_sell_then_buy": 0.0,
    "min_range_pct_buy_then_sell": 0.0,
    "ref": "open",
    "lot_size": 100,
    "y_tau_enter": 0.0,
    "y_tau_enter_sell_then_buy": 0.0,
    "y_tau_enter_buy_then_sell": 0.0,
    "y_enter_enabled": True,
    "y_enter_alt_enabled": True,
    "r_tau_enter": 0.0,
    "r_tau_enter_alt": 0.0,
    "y_tau_enter_alt": 0.0,
    "y_path_enter_alt": 0.0,
    "y_trade_enter": 0.01,
    "y_trade_strong": 0.2,
    "y_eod_prior": 0.01,
    "y_eod_enter": 0.01,
    "y_eod_strong": 0.2,
    "y_on_allow": 0.01,
    "y_nc_enter": 0.01,
    "y_nc_strong": 0.2,
    "y_nowcast_oc_gate": False,
    "y_use_path": True,
    "y_tau_leg1_prior": True,
    "y_tau_leg1_prior_mode": "score",
    "y_tau_leg1_prior_risk": 10.0,
    "y_tau_leg1_prior_shift_scale": 1.0,
    "y_path_enter": 0.0,
    "y_path_enter_sell_then_buy": 0.0,
    "y_path_enter_buy_then_sell": 0.0,
    "y_path_strong": 5.0,
    "y_complexity_max": 1.0,
    "y_cx_max": 1.0,
    "y_tpd_max": 1.0,
    "y_complexity_max_alt": 1.0,
    "y_tpd_max_alt": 1.0,
    "y_gap_tier_pct": 1.0,
    "t0_close_band_delta_pct": 3.0,
    "t0_price_space_gate": True,
    "t0_price_space_max_dev_pct": 5.0,
    "t0_price_space_prev_dev_pct": 5.0,
    "t0_round_ratio": 0.4,
    "t0_max_position_pct": 1.0,
    "t0_slots_max_rounds": 5,
    "y_tau_exit_price_skip": True,
    "y_tau_exit_price_mult": 1.0,
    "y_tau_exit_price_skip_buy_then_sell": True,
    "y_tau_exit_price_mult_buy_then_sell": 1.0,
    "y_tau_exit_price_bias_buy_then_sell": 1.0,
    "y_tau_exit_price_skip_sell_then_buy": True,
    "y_tau_exit_price_mult_sell_then_buy": 1.0,
    "y_tau_exit_price_bias_sell_then_buy": -1.0,
    "y_block_tau_nowcast_sign": True,
    "t0_pm_degrade": "13:00",
    "t0_pm_degrade_sell_then_buy": "13:00",
    "t0_pm_degrade_buy_then_sell": "13:00",
    "t0_pm_chase_cap_leg1_sell_then_buy": True,
    "t0_pm_chase_cap_leg1_buy_then_sell": True,
    "t0_pm_chase_interval_min": 5,
    "t0_pm_chase_interval_min_sell_then_buy": 5,
    "t0_pm_chase_interval_min_buy_then_sell": 5,
    "t0_stop_pct_buy_then_sell": 1.2,
    "t0_stop_pct_sell_then_buy": 1.2,
    "t0_stop_arm_bars": 1,
    "t0_stop_on_close": True,
    "t0_slots_enabled": True,
    # direction / path_mode 留给 runtime_defaults，避免与 DEFAULT_T0 双轨
}

DEFAULT_REBALANCE_TIMING: Dict[str, Any] = {
    "execution_mode": "next_open",  # next_open=盘中现价、收盘后挂次日开盘；close=确认即成交
    "open_fill_after_hm": "09:15",
    "open_fill_until_hm": "10:00",
    "pending_chase_interval_min": 10,
    "pending_chase_eod_hm": "14:50",
    # 策略调仓：09:30 rank_lots（y_fuse/y_on · 200/500 股）
    "rank_lots": {
        "enabled": True,
        "mode": "rank_lots",
        "rank_enter": 0.012,
        "rank_strong": 0.012,
        "cash_floor": 0.0,
        "holdings_mv_cap": 150000.0,
        "fusion_w_trade": 0.5,
        "fusion_w_nowcast": 0.5,
        "y_on_alpha": 0.0,
    },
    # 旧键：读盘仍认；写入与 rank_lots 同步
    "path_matrix": {
        "enabled": True,
        "mode": "rank_lots",
        "rank_enter": 0.012,
        "rank_strong": 0.012,
        "cash_floor": 0.0,
        "holdings_mv_cap": 150000.0,
        "fusion_w_trade": 0.5,
        "fusion_w_nowcast": 0.5,
        "y_on_alpha": 0.0,
    },
}

DEFAULT_EXECUTION: Dict[str, Any] = {
    "version": "1.0.0",
    "overlays": {"t0": deepcopy(DEFAULT_T0_OVERLAY)},
    "coupling": deepcopy(DEFAULT_COUPLING),
    "runtime_defaults": deepcopy(DEFAULT_RUNTIME),
    "rebalance_timing": deepcopy(DEFAULT_REBALANCE_TIMING),
}

_REBALANCE_KEYS = (
    "min_score",
    "add_score",
    "min_hold_score",
    "reduce_score",
    "max_positions",
    "position_pct",
    "min_cash_pct",
    "horizon_days",
    "signal_limit",
    "stop_loss_pnl",
    "max_hold_days",
)


# 表单只写共用键时，下层 DEFAULT_T0_OVERLAY / 纸面里的分侧默认值必须让路，
# 否则 load_t0_rules 会把分侧 0 当成「显式覆盖」，闸仍走 overlay 默认。
_SHARED_TO_SIDE_KEYS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("y_path_enter", ("y_path_enter_buy_then_sell", "y_path_enter_sell_then_buy")),
    ("y_tau_enter", ("y_tau_enter_buy_then_sell", "y_tau_enter_sell_then_buy")),
)


def _drop_stale_side_keys(merged: dict, sources: Dict[str, str], layer: dict) -> None:
    """本层写了共用键、没写分侧 → 丢掉下层残留分侧，供 load_t0_rules 跟随共用键。"""
    for shared, sides in _SHARED_TO_SIDE_KEYS:
        if shared not in layer or layer.get(shared) is None:
            continue
        for sk in sides:
            if sk in layer and layer.get(sk) is not None:
                continue
            merged.pop(sk, None)
            sources.pop(sk, None)


def _merge_dict(base: dict, overlay: Optional[dict]) -> dict:
    out = deepcopy(base) if base else {}
    if not overlay:
        return out
    for k, v in overlay.items():
        if v is None:
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_dict(out[k], v)
        else:
            out[k] = deepcopy(v)
    return out


def default_execution_spec() -> Dict[str, Any]:
    return deepcopy(DEFAULT_EXECUTION)


def execution_from_lifecycle(lifecycle: Optional[dict]) -> Dict[str, Any]:
    """从 StrategySpec.lifecycle 抽出 execution；无则用默认 + 兼容 paper_rules.t0。"""
    life = lifecycle or {}
    base = default_execution_spec()
    raw = life.get("execution")
    if isinstance(raw, dict):
        base = _merge_dict(base, raw)
    # 兼容：旧纸面把 t0 写在 paper_rules.t0
    pr = life.get("paper_rules") or {}
    if isinstance(pr, dict) and isinstance(pr.get("t0"), dict):
        base["overlays"] = base.get("overlays") or {}
        base["overlays"]["t0"] = _merge_dict(
            base["overlays"].get("t0") or {}, pr.get("t0") or {}
        )
    return base


def get_strategy_execution(strategy: Optional[str] = None) -> Dict[str, Any]:
    from core.strategy import get_strategy_spec

    sid = strategy
    if not sid:
        from core.backtest.strategies import DEFAULT_STRATEGY

        sid = DEFAULT_STRATEGY
    spec = get_strategy_spec(sid)
    exe = deepcopy(spec.get("execution") or default_execution_spec())
    exe["_strategy_id"] = spec.get("strategy_id")
    exe["_strategy_version"] = spec.get("version")
    exe["_paper_rules"] = deepcopy(spec.get("paper_rules") or {})
    return exe


def _layer_t0(
    *,
    strategy_exe: dict,
    paper: Optional[dict],
    request_override: Optional[dict],
) -> Tuple[dict, Dict[str, str], List[str]]:
    """合并 T0 覆盖层；返回 (raw_override_for_load_t0_rules, sources, notes)。"""
    sources: Dict[str, str] = {}
    notes: List[str] = []
    layers: List[Tuple[str, dict]] = []

    spec_t0 = ((strategy_exe.get("overlays") or {}).get("t0")) or {}
    if spec_t0:
        layers.append(("spec", dict(spec_t0)))

    paper_rules = (paper or {}).get("rules") or {}
    paper_t0 = paper_rules.get("t0") if isinstance(paper_rules, dict) else None
    if isinstance(paper_t0, dict) and paper_t0:
        layers.append(("paper", dict(paper_t0)))

    # request 可以是完整 execution、{t0:...} 或扁平 t0 规则
    req = request_override or {}
    req_t0: dict = {}
    if isinstance(req.get("overlays"), dict) and isinstance(req["overlays"].get("t0"), dict):
        req_t0 = dict(req["overlays"]["t0"])
    elif isinstance(req.get("t0"), dict):
        req_t0 = dict(req["t0"])
    elif req and not any(k in req for k in ("overlays", "coupling", "runtime_defaults", "rebalance")):
        # 扁平规则（旧 API）
        req_t0 = {k: v for k, v in req.items() if k not in ("channel", "has_minute")}
    if req_t0:
        layers.append(("request", req_t0))

    merged: dict = {}
    for name, layer in layers:
        for k, v in layer.items():
            if v is None:
                continue
            merged[k] = v
            sources[k] = name
        _drop_stale_side_keys(merged, sources, layer)
    if not layers:
        notes.append("无 Spec/纸面/请求覆盖，T0 用库默认 + runtime")
    return merged, sources, notes


def _apply_channel_fallbacks(
    *,
    raw_t0: dict,
    sources: Dict[str, str],
    runtime: dict,
    channel: str,
    has_minute: Optional[bool],
    notes: List[str],
) -> dict:
    _ = has_minute  # 研究回测已不按有无分钟切换 path；保留形参兼容调用方
    out = dict(raw_t0)
    ch = (channel or "paper").strip().lower()
    if ch not in {"paper", "backtest"}:
        ch = "paper"

    if "direction" not in out:
        if ch == "backtest":
            fb = runtime.get("backtest_direction_fallback") or "dual_y"
        else:
            fb = runtime.get("paper_direction_fallback") or "dual_y"
        out["direction"] = fb
        sources["direction"] = "runtime_fallback"
        notes.append(f"{ch}: direction 未显式声明 → {fb}")

    # 生产仅保留 dual_y；旧 sell_then_buy/signal/auto/buy_then_sell 一律收敛
    dir_now = str(out.get("direction") or "").strip().lower()
    if dir_now != "dual_y":
        notes.append(f"direction={dir_now} 已下线 → dual_y")
        out["direction"] = "dual_y"
        sources["direction"] = f"{sources.get('direction') or 'unknown'}→dual_y"

    # 纸面/回测：强制分钟第一触达（已删除日线模拟）
    if str(out.get("path_mode") or "") != "first_touch":
        notes.append(f"{ch}: path_mode={out.get('path_mode')} → first_touch")
        out["path_mode"] = "first_touch"
        sources["path_mode"] = f"{sources.get('path_mode') or 'unknown'}→first_touch"
    elif "path_mode" not in out:
        out["path_mode"] = "first_touch"
        sources["path_mode"] = "runtime_fallback"
        notes.append(f"{ch}: path_mode 未显式声明 → first_touch")

    return out


def _effective_hash(payload: dict) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def resolve_effective_execution(
    *,
    strategy: Optional[str] = None,
    paper: Optional[dict] = None,
    request_override: Optional[dict] = None,
    channel: str = "paper",
    has_minute: Optional[bool] = None,
) -> Dict[str, Any]:
    """解析生效 Execution（含 t0 / rebalance / coupling）。"""
    sid = strategy or (paper or {}).get("strategy_id")
    strategy_exe = get_strategy_execution(sid)
    runtime = _merge_dict(
        DEFAULT_RUNTIME,
        strategy_exe.get("runtime_defaults"),
    )
    if isinstance(request_override, dict) and isinstance(
        request_override.get("runtime_defaults"), dict
    ):
        runtime = _merge_dict(runtime, request_override["runtime_defaults"])

    coupling = _merge_dict(DEFAULT_COUPLING, strategy_exe.get("coupling"))
    if isinstance(request_override, dict) and isinstance(request_override.get("coupling"), dict):
        coupling = _merge_dict(coupling, request_override["coupling"])
    # 纸面账户级 coupling（rules.execution.coupling）
    paper_rules = (paper or {}).get("rules") or {}
    if isinstance(paper_rules, dict):
        paper_exe = paper_rules.get("execution")
        if isinstance(paper_exe, dict) and isinstance(paper_exe.get("coupling"), dict):
            coupling = _merge_dict(coupling, paper_exe["coupling"])
    mode = str((coupling or {}).get("t0_vs_stance") or "independent").strip().lower()
    if mode not in COUPLING_MODES:
        mode = "independent"
    coupling["t0_vs_stance"] = mode

    raw_t0, sources, notes = _layer_t0(
        strategy_exe=strategy_exe,
        paper=paper,
        request_override=request_override,
    )
    raw_t0 = _apply_channel_fallbacks(
        raw_t0=raw_t0,
        sources=sources,
        runtime=runtime,
        channel=channel,
        has_minute=has_minute,
        notes=notes,
    )
    t0 = load_t0_rules(raw_t0)
    t0["t0_ratio"] = 1.0

    # rebalance 规则键：Spec paper_rules ← paper.rules（非 t0）
    rebalance = deepcopy(strategy_exe.get("_paper_rules") or {})
    paper_rules = (paper or {}).get("rules") or {}
    if isinstance(paper_rules, dict):
        for k in _REBALANCE_KEYS:
            if k in paper_rules and paper_rules[k] is not None:
                rebalance[k] = paper_rules[k]

    # 成交时机 + rank_lots（仍认旧键 path_matrix）：Spec ← paper.rules.execution.rebalance_timing
    timing = deepcopy(DEFAULT_REBALANCE_TIMING)
    spec_timing = strategy_exe.get("rebalance_timing")
    if isinstance(spec_timing, dict):
        timing = _merge_dict(timing, spec_timing)
    if isinstance(paper_rules, dict):
        if paper_rules.get("execution_mode"):
            timing["execution_mode"] = paper_rules.get("execution_mode")
        paper_exe = paper_rules.get("execution")
        if isinstance(paper_exe, dict):
            if paper_exe.get("execution_mode"):
                timing["execution_mode"] = paper_exe.get("execution_mode")
            rt = paper_exe.get("rebalance_timing")
            if isinstance(rt, dict):
                timing = _merge_dict(timing, rt)
    if isinstance(request_override, dict):
        if request_override.get("execution_mode"):
            timing["execution_mode"] = request_override.get("execution_mode")
        req_t = request_override.get("rebalance_timing")
        if isinstance(req_t, dict):
            timing = _merge_dict(timing, req_t)
    mode = str(timing.get("execution_mode") or "next_open").strip().lower()
    if mode not in ("next_open", "close"):
        mode = "next_open"
    timing["execution_mode"] = mode

    execution = {
        "version": strategy_exe.get("version") or DEFAULT_EXECUTION["version"],
        "overlays": {"t0": {k: t0[k] for k in t0 if k != "note"}},
        "coupling": coupling,
        "runtime_defaults": runtime,
        "rebalance_timing": timing,
    }

    hash_payload = {
        "strategy_id": strategy_exe.get("_strategy_id"),
        "strategy_version": strategy_exe.get("_strategy_version"),
        "channel": channel,
        "t0": {
            k: t0.get(k)
            for k in (
                "enabled",
                "t0_ratio",
                "fill_mode",
                "direction",
                "path_mode",
                "use_atr",
                "dir_enter",
                "min_range_pct",
            )
        },
        "coupling": coupling,
        "rebalance": {k: rebalance.get(k) for k in _REBALANCE_KEYS if k in rebalance},
        "rebalance_timing": timing,
    }

    return {
        "ok": True,
        "strategy_id": strategy_exe.get("_strategy_id"),
        "strategy_version": strategy_exe.get("_strategy_version"),
        "channel": (channel or "paper").strip().lower(),
        "execution": execution,
        "rebalance": rebalance,
        "t0": t0,
        "t0_sources": sources,
        "coupling": coupling,
        "runtime_defaults": runtime,
        "rebalance_timing": timing,
        "effective_hash": _effective_hash(hash_payload),
        "notes": notes,
        "summary": _human_summary(
            t0, coupling, channel=(channel or "paper"), timing=timing
        ),
    }


def resolve_t0_rules(
    *,
    strategy: Optional[str] = None,
    paper: Optional[dict] = None,
    rules: Optional[dict] = None,
    channel: str = "paper",
    has_minute: Optional[bool] = None,
) -> Dict[str, Any]:
    """便捷：只返回生效 t0 规则（兼容旧 load_t0_rules(rules) 调用点）。"""
    bundle = resolve_effective_execution(
        strategy=strategy,
        paper=paper,
        request_override=rules,
        channel=channel,
        has_minute=has_minute,
    )
    out = dict(bundle["t0"])
    out["_execution_meta"] = {
        "effective_hash": bundle["effective_hash"],
        "t0_sources": bundle["t0_sources"],
        "notes": bundle["notes"],
        "channel": bundle["channel"],
        "coupling": bundle["coupling"],
        "summary": bundle["summary"],
    }
    return out


def strip_execution_meta(rules: Optional[dict]) -> dict:
    """传给 simulate_t0_day 前去掉内部 meta。"""
    if not rules:
        return {}
    return {k: v for k, v in rules.items() if not str(k).startswith("_")}


def _human_summary(
    t0: dict, coupling: dict, *, channel: str, timing: Optional[dict] = None
) -> str:
    ratio = t0.get("t0_ratio")
    ratio_s = f"{int(round(float(ratio) * 100))}%" if ratio is not None else "—"
    sell_m = t0.get("y_tau_exit_price_mult_buy_then_sell")
    buy_m = t0.get("y_tau_exit_price_mult_sell_then_buy")
    trig = (
        f"τ卖×{sell_m}/买×{buy_m}"
        if sell_m is not None and buy_m is not None
        else "τ闸"
    )
    coup = (coupling or {}).get("t0_vs_stance") or "independent"
    coup_lbl = {
        "independent": "独立",
        "skip_if_avoid": "avoid跳过",
        "only_if_hold": "仅持有",
    }.get(str(coup), str(coup))
    fill_lbl = {
        "trigger": "触价",
        "mid": "中点",
        "optimistic": "乐观",
    }.get(str(t0.get("fill_mode") or "trigger"), str(t0.get("fill_mode") or "—"))
    path_lbl = {
        "first_touch": "5m首触",
    }.get(str(t0.get("path_mode") or "first_touch"), str(t0.get("path_mode") or "—"))
    ch_lbl = {"backtest": "回测", "paper": "纸面"}.get(str(channel), str(channel))
    return (
        f"{ch_lbl} · 动仓 {ratio_s} · dual_y · {path_lbl} · {trig} · {fill_lbl} · "
        f"stance {coup_lbl} · {_timing_summary(timing)}"
    )


def _timing_summary(timing: Optional[dict]) -> str:
    t = timing or {}
    mode = str(t.get("execution_mode") or "next_open")
    if mode == "close":
        return "调仓：确认即成交"
    after = t.get("open_fill_after_hm") or "09:15"
    until = t.get("open_fill_until_hm") or "10:00"
    return f"调仓：盘中现价 · 收盘挂次日开盘 {after}–{until}"


def execution_public_view(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """API / Web 用精简视图。"""
    t0 = bundle.get("t0") or {}
    path_model_present = False
    path_model_shadow = False
    path_model_promoted = False
    path_last_report_exists = False
    try:
        from core.research.path_ridge import load_path_last_report, load_path_model, path_model_path

        pm = load_path_model()
        path_model_present = pm is not None
        path_model_shadow = bool(pm and pm.get("_shadow"))
        path_model_promoted = os.path.isfile(path_model_path())
        path_last_report_exists = load_path_last_report() is not None
    except Exception:  # noqa: BLE001
        logger.debug("path model probe failed", exc_info=True)
        path_model_promoted = False
        path_last_report_exists = False
    view = {
        "ok": True,
        "strategy_id": bundle.get("strategy_id"),
        "strategy_version": bundle.get("strategy_version"),
        "channel": bundle.get("channel"),
        "effective_hash": bundle.get("effective_hash"),
        "summary": bundle.get("summary"),
        "notes": bundle.get("notes") or [],
        "coupling": bundle.get("coupling") or {},
        "t0": {
            "enabled": t0.get("enabled"),
            "t0_ratio": t0.get("t0_ratio"),
            "must_cover_same_day": bool(t0.get("must_cover_same_day")),
            "must_cover_same_day_sell_then_buy": t0.get("must_cover_same_day_sell_then_buy"),
            "must_cover_same_day_buy_then_sell": t0.get("must_cover_same_day_buy_then_sell"),
            "fill_mode": t0.get("fill_mode"),
            "fill_mode_sell_then_buy": t0.get("fill_mode_sell_then_buy"),
            "fill_mode_buy_then_sell": t0.get("fill_mode_buy_then_sell"),
            "direction": t0.get("direction"),
            "path_mode": t0.get("path_mode"),
            "use_atr": t0.get("use_atr"),
            "atr_window": t0.get("atr_window"),
            "min_range_pct": t0.get("min_range_pct"),
            "min_range_pct_sell_then_buy": t0.get("min_range_pct_sell_then_buy"),
            "min_range_pct_buy_then_sell": t0.get("min_range_pct_buy_then_sell"),
            "ref": t0.get("ref"),
            "lot_size": t0.get("lot_size"),
            "minute_period": t0.get("minute_period"),
            "y_trade_enter": t0.get("y_trade_enter") or t0.get("y_trade_floor"),
            "y_trade_strong": t0.get("y_trade_strong") or t0.get("y_trade_tau_sign_gate"),
            "y_eod_prior": t0.get("y_eod_prior"),
            "y_eod_enter": t0.get("y_eod_enter"),
            "y_eod_strong": t0.get("y_eod_strong") or t0.get("y_eod_tau_sign_gate"),
            "y_trade_floor": t0.get("y_trade_floor") or t0.get("y_trade_enter"),
            "y_eod_tau_sign_gate": t0.get("y_eod_tau_sign_gate") or t0.get("y_eod_strong"),
            "y_trade_tau_sign_gate": t0.get("y_trade_tau_sign_gate") or t0.get("y_trade_strong"),
            "y_tau_enter": t0.get("y_tau_enter"),
            "y_tau_enter_strong": t0.get("y_tau_enter"),
            "y_tau_enter_sell_then_buy": t0.get("y_tau_enter_sell_then_buy"),
            "y_tau_enter_buy_then_sell": t0.get("y_tau_enter_buy_then_sell"),
            "y_enter_enabled": t0.get("y_enter_enabled"),
            "y_enter_alt_enabled": t0.get("y_enter_alt_enabled"),
            "r_tau_enter": t0.get("r_tau_enter"),
            "r_tau_enter_alt": t0.get("r_tau_enter_alt"),
            "y_tau_enter_alt": t0.get("y_tau_enter_alt"),
            "y_path_enter_alt": t0.get("y_path_enter_alt"),
            "y_on_risk": t0.get("y_on_risk"),
            "y_on_allow": t0.get("y_on_allow"),
            "y_block_tau_nowcast_sign": t0.get("y_block_tau_nowcast_sign"),
            "y_nc_enter": t0.get("y_nc_enter"),
            "y_nc_strong": t0.get("y_nc_strong") or t0.get("y_nowcast_enter"),
            "y_nowcast_enter": t0.get("y_nowcast_enter") or t0.get("y_nc_strong"),
            "y_tau_map": t0.get("y_tau_map"),
            "y_use_path": t0.get("y_use_path"),
            "y_tau_leg1_prior": t0.get("y_tau_leg1_prior"),
            "y_tau_leg1_prior_mode": t0.get("y_tau_leg1_prior_mode"),
            "y_tau_leg1_prior_risk": t0.get("y_tau_leg1_prior_risk"),
            "y_tau_leg1_prior_shift_scale": t0.get("y_tau_leg1_prior_shift_scale"),
            "y_path_enter": t0.get("y_path_enter"),
            "y_path_enter_sell_then_buy": t0.get("y_path_enter_sell_then_buy"),
            "y_path_enter_buy_then_sell": t0.get("y_path_enter_buy_then_sell"),
            "y_path_strong": t0.get("y_path_strong"),
            "y_complexity_max": (
                t0.get("y_complexity_max")
                if t0.get("y_complexity_max") not in (None, "")
                else t0.get("y_cx_max")
            ),
            "y_cx_max": (
                t0.get("y_complexity_max")
                if t0.get("y_complexity_max") not in (None, "")
                else t0.get("y_cx_max")
            ),
            "y_tpd_max": t0.get("y_tpd_max"),
            "y_complexity_max_alt": t0.get("y_complexity_max_alt"),
            "y_tpd_max_alt": t0.get("y_tpd_max_alt"),
            "y_path_required": t0.get("y_path_required"),
            "y_gap_tier_mode": t0.get("y_gap_tier_mode"),
            "y_gap_tier_pct": t0.get("y_gap_tier_pct"),
            "y_nowcast_oc_gate": t0.get("y_nowcast_oc_gate"),
            "t0_close_band_delta_pct": t0.get("t0_close_band_delta_pct"),
            "t0_price_space_gate": t0.get("t0_price_space_gate"),
            "t0_price_space_max_dev_pct": t0.get("t0_price_space_max_dev_pct"),
            "t0_price_space_prev_dev_pct": t0.get("t0_price_space_prev_dev_pct"),
            "t0_round_ratio": t0.get("t0_round_ratio"),
            "t0_max_position_pct": t0.get("t0_max_position_pct"),
            "y_tau_exit_price_skip": t0.get("y_tau_exit_price_skip"),
            "y_tau_exit_price_mult": t0.get("y_tau_exit_price_mult"),
            "y_tau_exit_price_bias": t0.get("y_tau_exit_price_bias"),
            "y_tau_exit_price_move_min": t0.get("y_tau_exit_price_move_min"),
            "y_tau_exit_price_move_max": t0.get("y_tau_exit_price_move_max"),
            "y_tau_exit_price_skip_buy_then_sell": t0.get(
                "y_tau_exit_price_skip_buy_then_sell"
            ),
            "y_tau_exit_price_mult_buy_then_sell": t0.get(
                "y_tau_exit_price_mult_buy_then_sell"
            ),
            "y_tau_exit_price_bias_buy_then_sell": t0.get(
                "y_tau_exit_price_bias_buy_then_sell"
            ),
            "y_tau_exit_price_move_min_buy_then_sell": t0.get(
                "y_tau_exit_price_move_min_buy_then_sell"
            ),
            "y_tau_exit_price_move_max_buy_then_sell": t0.get(
                "y_tau_exit_price_move_max_buy_then_sell"
            ),
            "y_tau_exit_price_skip_sell_then_buy": t0.get(
                "y_tau_exit_price_skip_sell_then_buy"
            ),
            "y_tau_exit_price_mult_sell_then_buy": t0.get(
                "y_tau_exit_price_mult_sell_then_buy"
            ),
            "y_tau_exit_price_bias_sell_then_buy": t0.get(
                "y_tau_exit_price_bias_sell_then_buy"
            ),
            "y_tau_exit_price_move_min_sell_then_buy": t0.get(
                "y_tau_exit_price_move_min_sell_then_buy"
            ),
            "y_tau_exit_price_move_max_sell_then_buy": t0.get(
                "y_tau_exit_price_move_max_sell_then_buy"
            ),
            "y_ratio_boost_cap": t0.get("y_ratio_boost_cap"),
            "y_ratio_cut": t0.get("y_ratio_cut"),
            "y_score_source": t0.get("y_score_source"),
            "t0_pm_degrade": t0.get("t0_pm_degrade"),
            "t0_pm_degrade_sell_then_buy": t0.get("t0_pm_degrade_sell_then_buy"),
            "t0_pm_degrade_buy_then_sell": t0.get("t0_pm_degrade_buy_then_sell"),
            "t0_pm_chase_interval_min": t0.get("t0_pm_chase_interval_min"),
            "t0_pm_chase_interval_min_sell_then_buy": t0.get(
                "t0_pm_chase_interval_min_sell_then_buy"
            ),
            "t0_pm_chase_interval_min_buy_then_sell": t0.get(
                "t0_pm_chase_interval_min_buy_then_sell"
            ),
            "t0_pm_chase_cap_leg1_sell_then_buy": t0.get(
                "t0_pm_chase_cap_leg1_sell_then_buy"
            ),
            "t0_pm_chase_cap_leg1_buy_then_sell": t0.get(
                "t0_pm_chase_cap_leg1_buy_then_sell"
            ),
            "t0_stop_pct_buy_then_sell": t0.get("t0_stop_pct_buy_then_sell"),
            "t0_stop_pct_sell_then_buy": t0.get("t0_stop_pct_sell_then_buy"),
            "t0_stop_arm_bars": t0.get("t0_stop_arm_bars"),
            "t0_stop_on_close": t0.get("t0_stop_on_close"),
            "t0_slots_enabled": t0.get("t0_slots_enabled"),
            "t0_slots": t0.get("t0_slots"),
            "t0_slots_max_rounds": t0.get("t0_slots_max_rounds"),
        },
        "t0_sources": bundle.get("t0_sources") or {},
        "rebalance": bundle.get("rebalance") or {},
        "rebalance_timing": bundle.get("rebalance_timing")
        or (bundle.get("execution") or {}).get("rebalance_timing")
        or {},
        "runtime_defaults": bundle.get("runtime_defaults") or {},
        "path_model_present": path_model_present,
        "path_model_promoted": path_model_promoted,
        "path_model_shadow": path_model_shadow,
        "path_last_report_exists": path_last_report_exists,
    }
    # 旧 signal 选向字段：仅非 dual_y 时透出，避免与 y_τ 门槛混淆
    if str(t0.get("direction") or "") != "dual_y":
        view["t0"]["dir_enter"] = t0.get("dir_enter")
        view["t0"]["w_gap"] = t0.get("w_gap")
        view["t0"]["w_yclose_loc"] = t0.get("w_yclose_loc")
        view["t0"]["w_mom3"] = t0.get("w_mom3")
        view["t0"]["w_gap_atr"] = t0.get("w_gap_atr")
    return view


def stance_allows_t0(
    coupling_mode: Optional[str],
    stance_code: Optional[str],
) -> Tuple[bool, str]:
    """按 t0_vs_stance 决定是否允许做 T。返回 (allowed, reason)。"""
    mode = str(coupling_mode or "independent").strip().lower()
    if mode not in COUPLING_MODES:
        mode = "independent"
    code = str(stance_code or "").strip().lower() or None
    if mode == "independent":
        return True, ""
    if mode == "skip_if_avoid":
        if code == "avoid":
            return False, "coupling:skip_if_avoid"
        return True, ""
    if mode == "only_if_hold":
        if code in {"wait", "probe", "buy_light"}:
            return True, ""
        return False, f"coupling:only_if_hold(stance={code or 'unknown'})"
    return True, ""


def validate_execution_patch(raw: Any) -> Tuple[bool, Dict[str, Any], List[str]]:
    """校验纸面 Execution 补丁；返回 (ok, normalized, errors)。

    接受形态：
      { t0: {...}, coupling: {...}, lock: bool }
      或扁平 t0 字段（与旧 API 兼容）
    """
    errors: List[str] = []
    if raw is None:
        return False, {}, ["body 不能为空"]
    if not isinstance(raw, dict):
        return False, {}, ["须为 JSON object"]

    t0_in: dict
    coupling_in: Optional[dict] = None
    lock = bool(raw.get("lock", True))

    if (
        isinstance(raw.get("t0"), dict)
        or "coupling" in raw
        or "overlays" in raw
        or "rebalance_timing" in raw
    ):
        t0_in = dict(raw.get("t0") or {})
        if isinstance(raw.get("overlays"), dict) and isinstance(raw["overlays"].get("t0"), dict):
            t0_in = {**t0_in, **raw["overlays"]["t0"]}
        if isinstance(raw.get("coupling"), dict):
            coupling_in = dict(raw["coupling"])
    else:
        t0_in = {
            k: v
            for k, v in raw.items()
            if k
            not in {
                "lock",
                "reset",
                "note",
                "coupling",
                "channel",
                "rebalance_timing",
            }
        }

    # 旧异号键 → τ↔nowcast
    if "y_block_tau_nowcast_sign" not in t0_in and "y_block_trade_tau_sign" in t0_in:
        t0_in["y_block_tau_nowcast_sign"] = t0_in.pop("y_block_trade_tau_sign")
    elif "y_block_trade_tau_sign" in t0_in:
        t0_in.pop("y_block_trade_tau_sign", None)
    # 已下线键：忽略
    t0_in.pop("y_block_conflict", None)
    drop_dead_t0_keys(t0_in)

    unknown = [k for k in t0_in.keys() if k not in ALLOWED_T0_PATCH_KEYS]
    if unknown:
        errors.append(f"不允许的 t0 键: {', '.join(sorted(unknown)[:8])}")

    # 归一化：走 load_t0_rules 边界，再只保留补丁键
    try:
        normalized_full = load_t0_rules(
            {k: v for k, v in t0_in.items() if k in ALLOWED_T0_PATCH_KEYS}
        )
    except Exception as e:
        logger.exception('unexpected error in validate_execution_patch')
        return False, {}, [f"t0 校验失败: {e}"]

    t0_out = {k: normalized_full[k] for k in t0_in if k in ALLOWED_T0_PATCH_KEYS and k in normalized_full}
    # legacy 共用键只写一侧时，补上 load_t0_rules 对齐的分侧键，避免 resolve 时
    # DEFAULT_T0_OVERLAY 里的分侧默认值盖掉纸面补丁。
    _legacy_side_expand = (
        ("t0_pm_chase_interval_min", ("t0_pm_chase_interval_min_buy_then_sell",)),
        ("t0_pm_degrade", ("t0_pm_degrade_buy_then_sell",)),
        ("y_tau_exit_price_mult", ("y_tau_exit_price_mult_buy_then_sell",)),
        ("y_tau_exit_price_bias", ("y_tau_exit_price_bias_buy_then_sell",)),
        ("y_tau_exit_price_skip", ("y_tau_exit_price_skip_buy_then_sell",)),
        ("fill_mode", ("fill_mode_buy_then_sell",)),
        ("must_cover_same_day", ("must_cover_same_day_buy_then_sell",)),
        ("y_path_enter", ("y_path_enter_buy_then_sell", "y_path_enter_sell_then_buy")),
        ("y_tau_enter", ("y_tau_enter_buy_then_sell", "y_tau_enter_sell_then_buy")),
    )
    for legacy, sides in _legacy_side_expand:
        if legacy not in t0_in:
            continue
        if legacy in normalized_full:
            t0_out[legacy] = normalized_full[legacy]
        for sk in sides:
            if sk in normalized_full:
                t0_out[sk] = normalized_full[sk]
    # enabled 等 bool
    if "enabled" in t0_in:
        t0_out["enabled"] = bool(t0_in.get("enabled"))
    if "use_atr" in t0_in:
        t0_out["use_atr"] = bool(t0_in.get("use_atr"))
    if "must_cover_same_day" in t0_in:
        t0_out["must_cover_same_day"] = bool(t0_in.get("must_cover_same_day"))
    if "must_cover_same_day_sell_then_buy" in t0_in:
        t0_out["must_cover_same_day_sell_then_buy"] = bool(t0_in.get("must_cover_same_day_sell_then_buy"))
    if "must_cover_same_day_buy_then_sell" in t0_in:
        t0_out["must_cover_same_day_buy_then_sell"] = bool(t0_in.get("must_cover_same_day_buy_then_sell"))
    if "y_block_tau_nowcast_sign" in t0_in:
        t0_out["y_block_tau_nowcast_sign"] = bool(t0_in.get("y_block_tau_nowcast_sign"))
    if "y_nowcast_oc_gate" in t0_in:
        t0_out["y_nowcast_oc_gate"] = bool(t0_in.get("y_nowcast_oc_gate"))
    if "y_use_path" in t0_in:
        t0_out["y_use_path"] = bool(t0_in.get("y_use_path"))
    if "y_enter_enabled" in t0_in:
        t0_out["y_enter_enabled"] = bool(t0_in.get("y_enter_enabled"))
    if "y_enter_alt_enabled" in t0_in:
        t0_out["y_enter_alt_enabled"] = bool(t0_in.get("y_enter_alt_enabled"))
    if "t0_price_space_gate" in t0_in:
        t0_out["t0_price_space_gate"] = bool(t0_in.get("t0_price_space_gate"))
    if "y_path_required" in t0_in:
        t0_out["y_path_required"] = bool(t0_in.get("y_path_required"))
    if "y_tau_exit_price_skip" in t0_in:
        t0_out["y_tau_exit_price_skip"] = bool(t0_in.get("y_tau_exit_price_skip"))
    if "y_tau_exit_price_skip_buy_then_sell" in t0_in:
        t0_out["y_tau_exit_price_skip_buy_then_sell"] = bool(
            t0_in.get("y_tau_exit_price_skip_buy_then_sell")
        )
    if "y_tau_exit_price_skip_sell_then_buy" in t0_in:
        t0_out["y_tau_exit_price_skip_sell_then_buy"] = bool(
            t0_in.get("y_tau_exit_price_skip_sell_then_buy")
        )
    if t0_in:
        t0_out["t0_stop_on_close"] = True

    coupling_out: Dict[str, Any] = {}
    if coupling_in is not None:
        mode = str(coupling_in.get("t0_vs_stance") or "independent").strip().lower()
        if mode not in COUPLING_MODES:
            errors.append(
                f"coupling.t0_vs_stance 须为 {', '.join(sorted(COUPLING_MODES))}"
            )
        else:
            coupling_out["t0_vs_stance"] = mode

    if errors:
        return False, {}, errors

    # 选向仅 dual_y（有 t0 补丁时才写入，避免仅改 rebalance_timing 污染 t0）
    if t0_in:
        t0_out["direction"] = "dual_y"
        if "t0_ratio" in t0_in:
            t0_out["t0_ratio"] = 1.0

    timing_out: Dict[str, Any] = {}
    raw_timing = raw.get("rebalance_timing") if isinstance(raw, dict) else None
    if isinstance(raw_timing, dict):
        pm_in = raw_timing.get("rank_lots")
        if not isinstance(pm_in, dict):
            pm_in = raw_timing.get("path_matrix")
        if isinstance(pm_in, dict):
            try:
                from core.paper.rebalance.path_matrix import get_path_matrix_cfg

                pm = get_path_matrix_cfg({"rank_lots": pm_in})
                lots = {
                    "enabled": bool(pm.get("enabled")),
                    "mode": "rank_lots",
                    "rank_enter": float(pm.get("rank_enter") or 0.012),
                    "rank_strong": float(pm.get("rank_strong") or 0.012),
                    "cash_floor": 0.0,
                    "holdings_mv_cap": float(
                        pm.get("holdings_mv_cap")
                        if pm.get("holdings_mv_cap") is not None
                        else 150_000.0
                    ),
                    "fusion_w_trade": float(pm.get("fusion_w_trade") or 0.5),
                    "fusion_w_nowcast": float(pm.get("fusion_w_nowcast") or 0.5),
                    "y_on_alpha": float(pm.get("y_on_alpha") if pm.get("y_on_alpha") is not None else 0.0),
                }
                timing_out["rank_lots"] = lots
                timing_out["path_matrix"] = lots
            except Exception as e:  # noqa: BLE001
                errors.append(f"rank_lots 校验失败: {e}")
                return False, {}, errors
        if raw_timing.get("execution_mode") is not None:
            mode = str(raw_timing.get("execution_mode") or "next_open").strip().lower()
            if mode not in ("next_open", "close"):
                errors.append("rebalance_timing.execution_mode 须为 next_open|close")
                return False, {}, errors
            timing_out["execution_mode"] = mode

    out_norm: Dict[str, Any] = {"t0": t0_out, "coupling": coupling_out, "lock": lock}
    if timing_out:
        out_norm["rebalance_timing"] = timing_out
    return True, out_norm, []


def apply_execution_patch_to_paper(
    paper: dict,
    patch: dict,
    *,
    note: str = "",
) -> Dict[str, Any]:
    """写入 paper.rules.t0 / paper.rules.execution（coupling · rebalance_timing）。"""
    ok, normalized, errors = validate_execution_patch(patch)
    if not ok:
        return {"ok": False, "errors": errors}

    rules = dict(paper.get("rules") or {})
    t0_patch = normalized.get("t0") or {}
    if t0_patch:
        prev_t0 = dict(rules.get("t0") or {}) if isinstance(rules.get("t0"), dict) else {}
        new_t0 = dict(prev_t0)
        new_t0.update(t0_patch)
        new_t0["t0_ratio"] = 1.0
        rules["t0"] = new_t0

    exe = dict(rules.get("execution") or {}) if isinstance(rules.get("execution"), dict) else {}
    coupling_patch = normalized.get("coupling") or {}
    if coupling_patch:
        coup = dict(exe.get("coupling") or {})
        coup.update(coupling_patch)
        exe["coupling"] = coup

    timing_patch = normalized.get("rebalance_timing") or {}
    if timing_patch:
        rt = dict(exe.get("rebalance_timing") or {})
        if isinstance(timing_patch.get("rank_lots"), dict) or isinstance(
            timing_patch.get("path_matrix"), dict
        ):
            incoming = timing_patch.get("rank_lots") or timing_patch.get("path_matrix") or {}
            prev_pm = {}
            if isinstance(rt.get("rank_lots"), dict):
                prev_pm.update(rt["rank_lots"])
            if isinstance(rt.get("path_matrix"), dict):
                prev_pm.update(rt["path_matrix"])
            prev_pm.update(incoming)
            rt["rank_lots"] = prev_pm
            rt["path_matrix"] = prev_pm
        for k, v in timing_patch.items():
            if k in ("path_matrix", "rank_lots") or v is None:
                continue
            rt[k] = v
        exe["rebalance_timing"] = rt

    if coupling_patch or timing_patch:
        rules["execution"] = exe

    paper["rules"] = rules
    if normalized.get("lock"):
        paper["t0_rules_locked"] = True
    if note:
        paper["execution_note"] = str(note)[:200]
    return {"ok": True, "normalized": normalized}


def reset_paper_execution_overlay(paper: dict) -> Dict[str, Any]:
    """清除账户级 t0 / execution 覆盖，恢复 Spec 默认。"""
    rules = dict(paper.get("rules") or {})
    rules.pop("t0", None)
    rules.pop("execution", None)
    paper["rules"] = rules
    paper.pop("t0_rules_locked", None)
    paper.pop("execution_note", None)
    from core.strategy import apply_strategy_to_paper

    sid = paper.get("strategy_id") or "short_conservative"
    apply_strategy_to_paper(paper, str(sid))
    return {"ok": True}


def diff_t0_maps(before: dict, after: dict) -> List[Dict[str, Any]]:
    """简易 t0/coupling 字段 diff。"""
    keys = sorted(set(before.keys()) | set(after.keys()))
    changes: List[Dict[str, Any]] = []
    for k in keys:
        if str(k).startswith("_"):
            continue
        a, b = before.get(k), after.get(k)
        if a != b:
            changes.append({"path": k, "from": a, "to": b})
    return changes


def execution_diff_against_strategy(
    paper: Optional[dict] = None,
    *,
    strategy: Optional[str] = None,
) -> Dict[str, Any]:
    """纸面生效 vs 纯 Spec（无 paper overlay）的 diff。"""
    sid = strategy or (paper or {}).get("strategy_id")
    base = resolve_effective_execution(strategy=sid, paper=None, channel="paper")
    cur = resolve_effective_execution(strategy=sid, paper=paper, channel="paper")
    t0_changes = diff_t0_maps(base.get("t0") or {}, cur.get("t0") or {})
    coup_changes = diff_t0_maps(base.get("coupling") or {}, cur.get("coupling") or {})
    return {
        "ok": True,
        "strategy_id": cur.get("strategy_id"),
        "base_hash": base.get("effective_hash"),
        "paper_hash": cur.get("effective_hash"),
        "t0_changes": t0_changes,
        "coupling_changes": coup_changes,
        "changed": bool(t0_changes or coup_changes),
        "base_summary": base.get("summary"),
        "paper_summary": cur.get("summary"),
    }
