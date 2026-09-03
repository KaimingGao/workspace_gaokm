"""signal / stance 配置加载（P6.1 / V2.1）。"""


import logging

logger = logging.getLogger(__name__)
import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from typing import Any, Dict, Iterator, List, Optional, Tuple

from core.paths import DATA_DIR
from core.signal.minute_tau_grid import DEFAULT_MINUTE_TAU_GRID

SIGNAL_CONFIG_PATH = os.path.join(DATA_DIR, "signal_config.json")


def get_signal_config_path() -> str:
    """统一入口：读取 INVESTMENT_SIGNAL_CONFIG 环境变量，回退到 SIGNAL_CONFIG_PATH。"""
    return os.environ.get("INVESTMENT_SIGNAL_CONFIG", SIGNAL_CONFIG_PATH)


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
        "alt_sentiment": 0.02,
        "tail_anomaly": 0.02,
    },
    "factor_groups": {
        "trend": ["momentum", "ma_slope", "technical_pattern", "weekly_confirm"],
        "value_quality": ["value", "earnings_yield", "quality", "growth", "dividend"],
        "liquidity_flow": ["liquidity", "money_flow", "amihud", "volume_price"],
        "risk": ["volatility", "gap_risk", "tail_anomaly", "overheat"],
        "residual": ["relative_strength", "idio_momentum", "size"],
    },
    "hard_reject": {
        "min_bars": 2,
        "mom3_gain_max_pct": 15.0,
        "mom3_loss_min_pct": -12.0,
        "mom5_gain_max_pct": 10.0,
        "day_gain_max_pct": 6.0,
        "soft_reject": False,
        "paper_buy_enforce": True,
        "soft_scale_yhat": False,
    },
    # FH3：rank.min_score 已 deprecated；生产选股门槛只认 scoring.min_predicted_score
    "rank": {
        "min_score": 55.0,  # deprecated · 启发式遗留，勿作 ŷ 买入门
        "default_limit": 8,
    },
    # 排序键：仅收益分 predicted_score（ŷ%）
    "scoring": {
        "rank_mode": "predicted_score",
        # 滞回：买入/入簿 ŷ≥+1%；卖出仅 ŷ<-1%；中间带持有不因未进簿清仓
        "min_predicted_score": 1.0,
        "min_hold_predicted_score": -1.0,
        # 校准层已下线；标志仅兼容旧配置，不进决策
        "enable_calibration": False,
        "horizon_days": 1,
    },
    # 双层 ŷ：EOD predicted_score + τ predicted_score_tau（见 dual_score / docs §2.5）
    "dual_score": {
        "fusion_mode": "blend",  # ŷ_trade = w·ŷ_EOD + w·(缺口∘ŷ_τ)；买入另须 ŷ_τ≥floor
        "tau": "open",
        "min_predicted_score_tau": 0.1,  # 基线；全池无人过闸时见 tau_freeze_breakglass
        "tau_freeze_breakglass": True,
        "min_predicted_score_tau_relax": None,  # None = 0.5 × 基线
        # rem 未推 ŷ_τ 时阻断买入（与 DEFAULT_DUAL_SCORE 一致；缺模型勿静默放行）
        "block_buy_if_tau_missing": True,
        "w_eod": 0.5,
        "w_tau": 0.5,
        "w_mode": "fixed",  # fixed | theme_boost | variance | kalman
        "theme_w_tau_boost": 1.25,
        "eod_residual_var": 1.0,
        "enable_cascade_shadow": True,
        "enable_tau_shadow_book": False,
        "enable_minute_tau": False,
        "minute_tau_hm": "10:30",
        "minute_tau_grid": list(DEFAULT_MINUTE_TAU_GRID),
        "nowcast": {
            "enabled": False,
            "write_shadow": False,
            "use_as_rank_key": False,
            "taus": ["eod", "open"],
            "q_process": 0.05,
            "theme_q_boost": 2.0,
            "gap_q_trigger_pct": 2.0,
            "gap_q_boost": 2.0,
        },
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
    # 舆情：默认不进 ŷ（include_in_score=false）；role=prior 作 ŷ 外旁路。
    "sentiment": {
        "include_in_score": False,
        "role": "prior",
        "prior": {
            "mode": "off",
            "block_new_buys": False,
            "scale_buy_pct": 0.5,
            "scale_holds": False,
            "warn_only": True,
            "reduce_avoid_on_bullish": True,
        },
        "append_history": True,
        "history_max_lines_per_code": 500,
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
    # 分组 live：开关在此；权向量在 data/live/cluster_weights_*.json
    "cluster_scoring": {
        "enabled": False,
        "mode": "off",
        "top_n_per_group": 10,
        "max_names": 40,
        "min_coverage": 0.5,
        "max_age_days": 14,
        "refit_max_age_days": 14,
        "auto_demote_on_stale": True,
        # FH0/Y1.4：OOS 失败率超过该值则禁止 active（默认 0.5）
        "max_oos_fail_rate": 0.5,
        # Y1.3：滚动 ŷ IC 低于此值时禁止 active（FS1 默认硬拦；force+审计可豁免）
        "min_yhat_rolling_ic": 0.0,
        "block_active_on_yhat_ic": True,
        # Y3.3：行业 map 覆盖率低于此值 → 启用警告
        "min_sector_map_coverage": 0.5,
        # B1：相对 active 默认软提示（True）；False=恢复「不得差于 active」硬闸
        "promote_allow_worse_oos_than_active": True,
        # 建簿约束：可成交过滤 · 行业名额 · τ 闸 defer（与纸面 risk 同配置源）
        "book_constraints": {
            "enabled": True,
            "filter_untradeable": True,
            "enforce_sector_cap": True,
            "tau_fail_mode": "defer",
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


def load_signal_config(*, reload: bool = False) -> Dict[str, Any]:
    global _cached
    if _cached is not None and not reload:
        return _apply_overlays(deepcopy(_cached))

    cfg = deepcopy(DEFAULT_SIGNAL_CONFIG)
    path = get_signal_config_path()
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        cfg = _deep_merge(cfg, user)
    # 遗留开关已退役：OOS 失败组固定剔主分/主簿
    cs = cfg.get("cluster_scoring")
    if isinstance(cs, dict) and "exclude_oos_failed_groups" in cs:
        cs = dict(cs)
        cs.pop("exclude_oos_failed_groups", None)
        cfg["cluster_scoring"] = cs
    floor_warns = _validate_scoring_floors(cfg)
    if floor_warns:
        cfg.setdefault("_config_warnings", [])
        cfg["_config_warnings"] = list(cfg.get("_config_warnings") or []) + floor_warns
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
    """观察池 limit 等；``min_score`` 仅为启发式遗留默认，生产选股用 scoring ŷ。"""
    cfg = config or load_signal_config()
    raw = cfg.get("rank") or {}
    scoring = cfg.get("scoring") or {}
    out: Dict[str, Any] = {
        "min_score": float(raw.get("min_score", 55)),
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
