"""signal / stance 配置加载（P6.1 / V2.1）。"""


import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from typing import Any, Dict, Iterator, List, Optional, Tuple

from core.paths import DATA_DIR

SIGNAL_CONFIG_PATH = os.path.join(DATA_DIR, "signal_config.json")


def get_signal_config_path() -> str:
    """统一入口：读取 QUANTLAB_SIGNAL_CONFIG 环境变量，回退到 SIGNAL_CONFIG_PATH。"""
    return os.environ.get("QUANTLAB_SIGNAL_CONFIG", SIGNAL_CONFIG_PATH)


DEFAULT_SIGNAL_CONFIG: Dict[str, Any] = {
    "version": 3,
    # weights：诊断/遗留规则综合分用；非选股权。选股真源=因子系数 ReturnScoreModel
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
        "overheat": 0.03,
        "size": 0.03,
        "earnings_yield": 0.03,
        "growth": 0.03,
        "dividend": 0.02,
        "money_flow": 0.0,
        "amihud": 0.02,
        "idio_momentum": 0.03,
        "tail_anomaly": 0.02,
    },
    "factor_groups": {
        "trend": ["momentum", "ma_slope", "technical_pattern", "weekly_confirm"],
        "value_quality": ["value", "earnings_yield", "quality", "growth", "dividend"],
        "liquidity_flow": ["liquidity", "money_flow", "amihud", "volume_price"],
        "risk": ["volatility", "gap_risk", "tail_anomaly", "overheat"],
        "residual": ["relative_strength", "idio_momentum", "size"],
        "pv_derived": ["alpha_oo"],
    },
    "hard_reject": {
        "min_bars": 2,
        "mom3_gain_max_pct": 15.0,
        "mom3_loss_min_pct": -12.0,
        "mom5_gain_max_pct": 10.0,
        "day_gain_max_pct": 6.0,
        "soft_reject": False,
        "soft_scale_yhat": False,
    },
    # FH3：rank.min_score 已下线；生产选股门槛只认 scoring.min_predicted_score
    "rank": {
        "default_limit": 8,
    },
    # 排序键：仅收益分 predicted_score（ŷ%）
    "scoring": {
        "rank_mode": "predicted_score",
        # 滞回：买入/入簿 ŷ≥+1%；卖出仅 ŷ<-1%；中间带持有不因未进簿清仓
        "min_predicted_score": 1.0,
        "min_hold_predicted_score": -1.0,
        "horizon_days": 1,
    },
    # 双层 ŷ：ŷ_oo predicted_score + τ predicted_score_tau（见 dual_score / docs §2.5）
    "dual_score": {
        "fusion_mode": "blend",  # ŷ_trade = w·ŷ_oo + w·(缺口∘ŷ_τ)；买入另须 ŷ_τ≥floor
        "tau": "open",
        "min_predicted_score_tau": 0.1,  # 基线；全池无人过闸时见 tau_freeze_breakglass
        "tau_freeze_breakglass": True,
        "min_predicted_score_tau_relax": None,  # None = 0.5 × 基线
        # rem 未推 ŷ_τ 时阻断买入（与 DEFAULT_DUAL_SCORE 一致；缺模型勿静默放行）
        "block_buy_if_tau_missing": True,
        "w_oo": 0.5,
        "w_eod": 0.5,
        "w_tau": 0.5,
        "w_mode": "fixed",  # fixed | theme_boost | variance
        "theme_w_tau_boost": 1.25,
        "eod_residual_var": 1.0,
        "enable_minute_tau": False,
    },
    # stance 门槛按收益分 ŷ%（百分点）
    "stance_thresholds": {
        "avoid": -0.5,
        "wait": 0.0,
        "probe": 0.35,
    },
    # 遗留：旧 0–100 门槛表（不再用于 stance）
    "stance_thresholds_heuristic": {
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
        # 阈值附近对罚分/仓位/权数软插值（百分点带宽）；0=硬跳变
        "blend_width_pct": 1.0,
        # P3：弱市时 optimize / 仓位缩放（pro_core.regime_position_scale）
        "apply_position_scale": True,
        "exposure_note": "仓位闸管 β 敞口；不进选股 ŷ。",
    },
    "fundamentals": {
        "enabled": True,
        "fetch_on_score": True,
        "use_in_ic_experiment": True,
        "use_in_backtest": True,
        "pit_mode": "as_of",
        "missing_as_of_policy": "zero_weight",
        # DS-R3：缺公告日 → warn | zero_weight | hard_reject
        "ann_missing_policy": "zero_weight",
        "ann_missing_code_ratio_warn": 0.3,
        "ann_missing_code_ratio_block": 0.5,
    },
    # 舆情：观察徽章。已退出因子集；标题仓 / 调仓软先验见 news.db。
    "sentiment": {
        "include_in_score": False,
        "role": "prior",
        "prior": {
            "mode": "off",
        },
    },
    "cross_section": {
        "neutralize": True,
        "method": "zscore",
        "min_samples": 3,
        "zscore_scale": 10.0,
        "industry_residual": True,
        "size_residual": True,
        "size_buckets": 3,
        # P2a：对 predicted 排序键做行业残差（默认关；影子/人审后开）
        "yhat_residual": False,
    },
    # 建簿约束：可成交过滤 · 行业名额 · τ 闸 defer（与纸面 risk 同配置源）
    "book_constraints": {
        "enabled": True,
        "filter_untradeable": True,
        "enforce_sector_cap": True,
        "tau_fail_mode": "defer",
        "max_names": 40,
    },
    # 双轨调仓：predicted ŷ% + heuristic 0–100；按 OOS 是否通过分流
    "rebalance_tracks": {
        "enabled": True,
        "oos_fail_policy": "exclude",
        "predicted_buy_floor": None,
        "predicted_hold_floor": None,
        "heuristic_buy_floor": 55.0,
        "heuristic_hold_floor": 45.0,
        "heuristic_sleeve_max": 8,
        "heuristic_apply_tau_gate": False,
    },
    # P1：T 日事件先验（缺口/板块开盘）· 不进 ŷ，只影响调仓动作
    "event_prior": {
        "mode": "gate",  # off | risk | gate
        "gap_trigger_pct": 2.0,
        "sector_breadth_min": 0.5,
        "soft_hold_on_theme": True,
        "warn_only": False,
        "rem_gate_enabled": True,
        "rem_soft_hold_min": 0.25,
    },
}

_cached: Optional[Dict[str, Any]] = None
_config_overlays: ContextVar[Tuple[Dict[str, Any], ...]] = ContextVar(
    "signal_config_overlays", default=()
)


def _deep_merge(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for key, val in (override or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def _apply_overlays(cfg: Dict[str, Any]) -> Dict[str, Any]:
    out = cfg
    for patch in _config_overlays.get():
        out = _deep_merge(out, patch)
    return out


@contextmanager
def signal_config_overlay(patch: Optional[Dict[str, Any]]) -> Iterator[None]:
    """临时叠加 signal_config（如权重），供研究对照回测；不写盘。"""
    if not patch:
        yield
        return
    stack = _config_overlays.get()
    token = _config_overlays.set(stack + (dict(patch),))
    try:
        yield
    finally:
        _config_overlays.reset(token)


def _validate_scoring_floors(cfg: Dict[str, Any]) -> List[str]:
    """启动时校验买入/持有 ŷ 门槛；hold 必须 ≤ buy（滞回）。"""
    warnings: List[str] = []
    scoring = cfg.get("scoring") if isinstance(cfg.get("scoring"), dict) else {}
    try:
        buy = float(scoring.get("min_predicted_score"))
    except (TypeError, ValueError):
        buy = None
    try:
        hold = float(scoring.get("min_hold_predicted_score"))
    except (TypeError, ValueError):
        hold = None
    if buy is not None and hold is not None and hold > buy:
        warnings.append(
            f"scoring.min_hold_predicted_score({hold}) > "
            f"min_predicted_score({buy})：刚买即卖风险；已钳制 hold=buy"
        )
        scoring["min_hold_predicted_score"] = buy
        cfg["scoring"] = scoring
    return warnings


def _migrate_legacy_signal_config(user: Dict[str, Any]) -> Dict[str, Any]:
    """把旧配置里退役分组命名空间下的在用子块迁到顶层。

    book_constraints / rebalance_tracks / max_names 仍被建簿与双轨调仓使用，
    旧配置里它们挂在退役分组命名空间下；顶层已显式配置时以顶层为准。
    迁移后不再保留该命名空间。
    """
    out = dict(user or {})
    cs = out.get("cluster_scoring")
    if not isinstance(cs, dict):
        return out
    cs = dict(cs)
    for key in ("book_constraints", "rebalance_tracks"):
        legacy = cs.pop(key, None)
        if isinstance(legacy, dict):
            top = out.get(key)
            out[key] = {**legacy, **(top if isinstance(top, dict) else {})}
    legacy_max = cs.pop("max_names", None)
    if legacy_max is not None:
        bc = dict(out.get("book_constraints") or {})
        bc.setdefault("max_names", legacy_max)
        out["book_constraints"] = bc
    out.pop("cluster_scoring", None)
    return out


def load_signal_config(*, reload: bool = False) -> Dict[str, Any]:
    global _cached
    if _cached is not None and not reload:
        return _apply_overlays(deepcopy(_cached))

    cfg = deepcopy(DEFAULT_SIGNAL_CONFIG)
    path = get_signal_config_path()
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        cfg = _deep_merge(cfg, _migrate_legacy_signal_config(user))
    floor_warns = _validate_scoring_floors(cfg)
    if floor_warns:
        cfg.setdefault("_config_warnings", [])
        cfg["_config_warnings"] = list(cfg.get("_config_warnings") or []) + floor_warns
    hr = cfg.get("hard_reject")
    if isinstance(hr, dict) and "paper_buy_enforce" in hr:
        hr = dict(hr)
        hr.pop("paper_buy_enforce", None)
        cfg["hard_reject"] = hr
    scoring = cfg.get("scoring")
    if isinstance(scoring, dict) and "enable_calibration" in scoring:
        scoring = dict(scoring)
        scoring.pop("enable_calibration", None)
        cfg["scoring"] = scoring
    # alpha158 → alpha_oo：旧盘 factor_groups / weights 键名改写
    groups = cfg.get("factor_groups")
    if isinstance(groups, dict):
        rewritten = False
        out_groups = dict(groups)
        for gname, members in list(out_groups.items()):
            if not isinstance(members, list):
                continue
            new_members = [
                "alpha_oo" if str(m) == "alpha158" else m for m in members
            ]
            if new_members != members:
                out_groups[gname] = new_members
                rewritten = True
        if rewritten:
            cfg["factor_groups"] = out_groups
    weights = cfg.get("weights")
    if isinstance(weights, dict) and (
        "alpha158" in weights or "alt_sentiment" in weights
    ):
        weights = dict(weights)
        if "alpha158" in weights:
            if "alpha_oo" not in weights:
                weights["alpha_oo"] = weights.pop("alpha158")
            else:
                weights.pop("alpha158", None)
        if "alt_sentiment" in weights:
            try:
                extra = float(weights.pop("alt_sentiment") or 0.0)
            except (TypeError, ValueError):
                extra = 0.0
            if extra:
                try:
                    base = float(weights.get("momentum") or 0.0)
                except (TypeError, ValueError):
                    base = 0.0
                weights["momentum"] = base + extra
        cfg["weights"] = weights
    _cached = cfg
    return _apply_overlays(deepcopy(cfg))


PREDICTED_STANCE_DEFAULTS: Dict[str, float] = {
    "avoid": -0.5,
    "wait": 0.0,
    "probe": 0.35,
}


def _legacy_stance_thresholds_in_predicted_table(raw: Dict[str, Any]) -> bool:
    """``stance_thresholds`` 中 avoid≥10 视为遗留 0–100 分档（非 ŷ%）。"""
    try:
        return float(raw.get("avoid", PREDICTED_STANCE_DEFAULTS["avoid"])) >= 10.0
    except (TypeError, ValueError):
        return False


def get_stance_thresholds(
    config: Optional[Dict[str, Any]] = None,
    *,
    kind: str = "predicted",
) -> Dict[str, float]:
    """stance 门槛。``kind=predicted`` 用 ŷ% 门槛；``heuristic`` 用 0–100。"""
    return get_stance_thresholds_with_meta(config, kind=kind)["thresholds"]


def get_stance_thresholds_with_meta(
    config: Optional[Dict[str, Any]] = None,
    *,
    kind: str = "predicted",
) -> Dict[str, Any]:
    """同 ``get_stance_thresholds``，并在遗留 0–100 写入 ``stance_thresholds`` 时附 ``legacy_detected``。"""
    cfg = config or load_signal_config()
    k = str(kind or "predicted").strip().lower()
    if k in ("heuristic", "rule", "heuristic_score", "score"):
        raw = cfg.get("stance_thresholds_heuristic") or {}
        return {
            "thresholds": {
                "avoid": float(raw.get("avoid", 45)),
                "wait": float(raw.get("wait", 55)),
                "probe": float(raw.get("probe", 68)),
            },
            "legacy_detected": False,
        }
    raw = cfg.get("stance_thresholds") or {}
    if _legacy_stance_thresholds_in_predicted_table(raw):
        return {
            "thresholds": dict(PREDICTED_STANCE_DEFAULTS),
            "legacy_detected": True,
            "note": (
                "stance_thresholds 含遗留 0–100 分档；"
                "已改用 ŷ 默认门槛（请迁移至 stance_thresholds_heuristic）"
            ),
        }
    return {
        "thresholds": {
            "avoid": float(raw.get("avoid", PREDICTED_STANCE_DEFAULTS["avoid"])),
            "wait": float(raw.get("wait", PREDICTED_STANCE_DEFAULTS["wait"])),
            "probe": float(raw.get("probe", PREDICTED_STANCE_DEFAULTS["probe"])),
        },
        "legacy_detected": False,
    }


def get_rank_defaults(config: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """观察池 limit 等；生产选股门槛用 scoring.min_predicted_score。"""
    cfg = config or load_signal_config()
    raw = cfg.get("rank") or {}
    scoring = cfg.get("scoring") or {}
    out: Dict[str, Any] = {
        "default_limit": int(raw.get("default_limit", 8)),
    }
    if "min_predicted_score" in scoring and scoring.get("min_predicted_score") is not None:
        try:
            out["min_predicted_score"] = float(scoring["min_predicted_score"])
        except (TypeError, ValueError):
            pass
    return out


def get_scoring_horizon_days(config: Optional[Dict[str, Any]] = None) -> int:
    """生产 ŷ / 分组 / 纸面共用的 ``scoring.horizon_days``（夹在 1～10）。"""
    cfg = config or load_signal_config()
    raw = (cfg.get("scoring") or {}).get("horizon_days")
    try:
        h = int(raw if raw is not None else 1)
    except (TypeError, ValueError):
        h = 1
    return max(1, min(h, 10))


def read_signal_config_file(*, reload: bool = True) -> Dict[str, Any]:
    """读取 signal_config（合并后配置 + 文件元信息，只读）。"""
    path = get_signal_config_path()
    exists = os.path.isfile(path)
    raw = None
    if exists:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    merged = load_signal_config(reload=reload)
    return {
        "success": True,
        "exists": exists,
        "path": path,
        "config": merged,
        "raw": raw,
        "readonly": True,
        "note": (
            "weights 须人审导出后手动合并；"
            "scoring 滞回 / stance_thresholds 可通过 Web 人审写入（不静默）。"
        ),
    }
