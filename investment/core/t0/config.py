"""做 T 规则配置（纸面 / 回测共用）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, Optional


def coerce_cfg_bool(val: Any, default: bool = True) -> bool:
    """解析纸面/请求里的 bool；避免 ``bool(\"false\")`` 误判为 True。"""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    s = str(val).strip().lower()
    if s in {"false", "0", "no", "off", "none", ""}:
        return False
    if s in {"true", "1", "yes", "on"}:
        return True
    return default


# t0_pm_degrade: HH:MM 起算中点追价（禁新开；已开未平则目标=mid(旧,现价)，触价成交，否则 eod）
# t0_pm_chase_interval_min: 起算后每隔 N 分钟再中点一次（1–60）
#
# fill_mode:
#   trigger     — 按触发价成交（默认，偏保守）
#   mid         — 触发价与极值中点
#   optimistic  — 卖用 high、买用 low（上界，对照用）
#
# direction:
#   dual_y  — y_trade 资格 · y_τ 主方向 · y_eod 目标价同向回升 · y_on 回补
#   y_tau_map（dual_y 子选项）— scalp|trend|fixed_long|fixed_reverse
#   （long_t / reverse_t / auto / signal 已下线；仅单测可经 load_t0_rules 显式传入）
#
# path_mode: 仅 first_touch（分钟时间序第一触达）。日线 dual_touch/veto/adverse 已删除。

DEFAULT_T0_RULES: Dict[str, Any] = {
    "enabled": True,
    "t0_ratio": 1.0,
    "sell_trigger_pct": 1.0,
    "buy_trigger_pct": 1.0,
    "must_cover_same_day": True,
    "lot_size": 100,
    "ref": "open",
    "fill_mode": "trigger",
    "direction": "dual_y",
    "auto_strong_pct": 0.5,
    "auto_weak_pct": 0.5,
    "dir_enter": 0.1,
    "w_gap": 0.45,
    "w_yclose_loc": 0.20,
    "w_mom3": 0.20,
    "w_gap_atr": 0.15,
    "path_mode": "first_touch",
    "minute_period": "5",
    "min_range_pct": 0.2,
    "use_atr": False,
    "atr_window": 14,
    "atr_sell_mult": 0.9,
    "atr_buy_mult": 0.7,
    # dual_y 阈值（百分比点）
    "y_trade_floor": 0.02,
    "y_eod_prior": 0.02,
    "y_tau_enter": 0.02,
    "y_on_risk": 0.01,
    "y_on_allow": 0.02,
    "y_block_tau_nowcast_sign": True,
    "y_tau_nowcast_sign_eps": 0.05,
    "y_nowcast_enter": 3.0,
    "y_tau_map": "trend",
    "y_use_path": True,
    "y_path_enter": 2.0,
    "y_path_required": False,
    "y_gap_tier_mode": "skip_opposite",
    "y_gap_tier_pct": 1.0,
    "y_nowcast_oc_gate": False,
    "y_path_abandon_enabled": True,
    "y_path_abandon_bars": 12,
    "y_ratio_boost_cap": 2.0,
    "y_ratio_cut": 0.60,
    "y_ratio_tau_boost_cap": 1.15,
    "y_ratio_eod_align_boost": 1.10,
    # |y_τ| 刚过入场线时额外压低目标价（乘 y_ratio_cut）
    "y_ratio_tau_soft_band": 0.20,
    # 中点追价：到点后禁新开；已开未平则旧目标↔现价中点，默认每 10 分钟再调
    "t0_pm_degrade": "14:00",
    "t0_pm_chase_interval_min": 10,
    # dual_y 分数来源：compute=开盘信息集即时算（默认）；live_book/ledger 仅兜底或对照
    "y_score_source": "compute",
    "note": "A股T+1底仓做T；仅5m first_touch（已删除日线模拟）；非实盘。",
}


def load_t0_rules(override: Optional[dict] = None) -> Dict[str, Any]:
    cfg = dict(DEFAULT_T0_RULES)
    if override:
        for k, v in override.items():
            if v is not None:
                cfg[k] = v
    cfg["t0_ratio"] = max(0.05, min(float(cfg.get("t0_ratio") or 1.0), 1.0))
    cfg["sell_trigger_pct"] = max(0.1, min(float(cfg.get("sell_trigger_pct") or 1.0), 20.0))
    cfg["buy_trigger_pct"] = max(0.1, min(float(cfg.get("buy_trigger_pct") or 1.0), 20.0))
    cfg["lot_size"] = max(1, int(cfg.get("lot_size") or 100))
    cfg["auto_strong_pct"] = max(0.0, min(float(cfg.get("auto_strong_pct") or 0.5), 5.0))
    cfg["auto_weak_pct"] = max(0.0, min(float(cfg.get("auto_weak_pct") or 0.5), 5.0))
    cfg["dir_enter"] = max(0.05, min(float(cfg.get("dir_enter") or 0.1), 1.0))
    for wk in ("w_gap", "w_yclose_loc", "w_mom3", "w_gap_atr"):
        cfg[wk] = max(0.0, min(float(cfg.get(wk) or 0.0), 1.0))
    # 归一化权重
    wsum = (
        float(cfg["w_gap"])
        + float(cfg["w_yclose_loc"])
        + float(cfg["w_mom3"])
        + float(cfg["w_gap_atr"])
    )
    if wsum <= 1e-9:
        cfg["w_gap"], cfg["w_yclose_loc"], cfg["w_mom3"], cfg["w_gap_atr"] = 0.45, 0.20, 0.20, 0.15
    else:
        cfg["w_gap"] = round(float(cfg["w_gap"]) / wsum, 4)
        cfg["w_yclose_loc"] = round(float(cfg["w_yclose_loc"]) / wsum, 4)
        cfg["w_mom3"] = round(float(cfg["w_mom3"]) / wsum, 4)
        cfg["w_gap_atr"] = round(float(cfg["w_gap_atr"]) / wsum, 4)
    fill = str(cfg.get("fill_mode") or "trigger").strip().lower()
    if fill not in {"trigger", "mid", "optimistic"}:
        fill = "trigger"
    cfg["fill_mode"] = fill
    direction = str(cfg.get("direction") or "auto").strip().lower()
    if direction in {"long", "正", "正t", "zheng"}:
        direction = "long_t"
    elif direction in {"reverse", "反", "反t", "fan", "short_t"}:
        direction = "reverse_t"
    elif direction in {"sig", "score", "factor"}:
        direction = "signal"
    elif direction in {"dual", "yhat", "dual_score", "multi_y"}:
        direction = "dual_y"
    if direction not in {"auto", "long_t", "reverse_t", "signal", "dual_y"}:
        direction = "dual_y"
    cfg["direction"] = direction
    for yk, lo, hi, default in (
        ("y_eod_prior", 0.01, 5.0, 0.02),
        ("y_tau_enter", 0.01, 5.0, 0.02),
        ("y_on_risk", 0.01, 10.0, 0.01),
        ("y_on_allow", 0.01, 10.0, 0.02),
        ("y_ratio_boost_cap", 1.0, 2.0, 2.0),
        ("y_ratio_cut", 0.2, 1.0, 0.60),
        ("y_ratio_tau_boost_cap", 1.0, 1.5, 1.15),
        ("y_ratio_eod_align_boost", 1.0, 1.5, 1.10),
        ("y_ratio_tau_soft_band", 0.0, 2.0, 0.20),
        ("y_tau_nowcast_sign_eps", 0.0, 1.0, 0.05),
        ("y_nowcast_enter", 0.05, 10.0, 3.0),
        ("y_path_enter", 1.0, 100.0, 2.0),
        ("y_gap_tier_pct", 0.3, 8.0, 1.0),
    ):
        try:
            # 旧键 y_trade_tau_sign_eps → y_tau_nowcast_sign_eps
            raw = cfg.get(yk)
            if (raw is None or raw == "") and yk == "y_tau_nowcast_sign_eps":
                raw = cfg.get("y_trade_tau_sign_eps")
            val = float(default if raw is None or raw == "" else raw)
        except (TypeError, ValueError):
            val = float(default)
        cfg[yk] = max(lo, min(val, hi))
    # 旧双闸：y_tau_enter_strong 并入入场；输出同步以免旧前端读到更低门槛
    try:
        strong_legacy = cfg.get("y_tau_enter_strong")
        if strong_legacy is not None and strong_legacy != "":
            strong_f = float(strong_legacy)
            if strong_f > float(cfg["y_tau_enter"]):
                cfg["y_tau_enter"] = max(0.01, min(strong_f, 5.0))
    except (TypeError, ValueError):
        pass
    cfg["y_tau_enter_strong"] = float(cfg["y_tau_enter"])
    from core.t0.score_policy import normalize_y_trade_floor

    cfg["y_trade_floor"] = normalize_y_trade_floor(cfg.get("y_trade_floor"))
    # 异号闸：新键 τ↔nowcast；旧 y_block_trade_tau_sign 迁移一次
    if "y_block_tau_nowcast_sign" in cfg:
        cfg["y_block_tau_nowcast_sign"] = coerce_cfg_bool(cfg.get("y_block_tau_nowcast_sign"))
    elif "y_block_trade_tau_sign" in cfg:
        cfg["y_block_tau_nowcast_sign"] = coerce_cfg_bool(cfg.get("y_block_trade_tau_sign"))
    else:
        cfg["y_block_tau_nowcast_sign"] = True
    cfg.pop("y_block_trade_tau_sign", None)
    cfg.pop("y_trade_tau_sign_eps", None)
    cfg["y_use_path"] = coerce_cfg_bool(cfg.get("y_use_path"), True)
    cfg["y_path_required"] = coerce_cfg_bool(cfg.get("y_path_required"), False)
    cfg["y_nowcast_oc_gate"] = coerce_cfg_bool(cfg.get("y_nowcast_oc_gate"), False)
    cfg["y_path_abandon_enabled"] = bool(cfg.get("y_path_abandon_enabled", True))
    try:
        cfg["y_path_abandon_bars"] = max(2, min(int(cfg.get("y_path_abandon_bars") or 12), 48))
    except (TypeError, ValueError):
        cfg["y_path_abandon_bars"] = 12
    gap_mode = str(cfg.get("y_gap_tier_mode") or "skip_opposite").strip().lower()
    if gap_mode not in {"off", "none", "false", "0", "skip_opposite", "revert"}:
        gap_mode = "skip_opposite"
    cfg["y_gap_tier_mode"] = gap_mode
    cfg.pop("y_block_conflict", None)  # 已下线：eod↔τ / y_check 冲突跳过
    # 丢弃已下线的不利/时间止损字段（旧账户 overlay 可能残留）
    for _dead in (
        "t0_adverse_stop_pct",
        "t0_adverse_stop_atr_mult",
        "t0_time_stop",
        "t0_time_stop_underwater_only",
    ):
        cfg.pop(_dead, None)
    pm_raw = cfg.get("t0_pm_degrade")
    if pm_raw is None:
        cfg["t0_pm_degrade"] = "14:00"
    else:
        s = str(pm_raw).strip()
        if s in {"", "0", "off", "none", "-"}:
            cfg["t0_pm_degrade"] = ""
        else:
            cfg["t0_pm_degrade"] = s
    try:
        iv = int(cfg.get("t0_pm_chase_interval_min") or 10)
    except (TypeError, ValueError):
        iv = 10
    cfg["t0_pm_chase_interval_min"] = max(1, min(iv, 60))
    from core.t0.score_policy import normalize_y_tau_map

    cfg["y_tau_map"] = normalize_y_tau_map(cfg.get("y_tau_map"))
    y_src = str(cfg.get("y_score_source") or "compute").strip().lower()
    if y_src in {"book", "cluster", "cluster_book"}:
        y_src = "live_book"
    elif y_src in {"ledger", "score_ledger", "freeze"}:
        y_src = "ledger"
    elif y_src in {"compute", "pit", "live", "realtime", "on_the_fly"}:
        y_src = "compute"
    else:
        y_src = "compute"
    cfg["y_score_source"] = y_src
    path_mode = str(cfg.get("path_mode") or "first_touch").strip().lower()
    if path_mode in {"minute", "min", "5m", "first", "dual_touch", "dual", "any", "veto", "adverse", "conservative", "worst"}:
        path_mode = "first_touch"
    if path_mode != "first_touch":
        path_mode = "first_touch"
    cfg["path_mode"] = path_mode
    period = str(cfg.get("minute_period") or "5").strip()
    if period not in {"1", "5", "15", "30", "60"}:
        period = "5"
    cfg["minute_period"] = period
    cfg["atr_window"] = max(3, min(int(cfg.get("atr_window") or 14), 60))
    cfg["atr_sell_mult"] = max(0.2, min(float(cfg.get("atr_sell_mult") or 0.9), 3.0))
    cfg["atr_buy_mult"] = max(0.2, min(float(cfg.get("atr_buy_mult") or 0.7), 3.0))
    if cfg.get("min_range_pct") is not None:
        cfg["min_range_pct"] = max(0.2, min(float(cfg["min_range_pct"]), 30.0))
    return cfg


def resolve_min_range_pct(cfg: dict) -> float:
    if cfg.get("min_range_pct") is not None:
        return float(cfg["min_range_pct"])
    # 相对触发阈值略宽；回测/纸面「自动」不宜高于 1.0%，否则横盘股整日跳过
    return round(
        max(0.5, (float(cfg["sell_trigger_pct"]) + float(cfg["buy_trigger_pct"])) * 0.35),
        4,
    )


# 盘中 Worker：与 5m K 线对齐的轮询与分钟缓存 TTL
T0_INTRADAY_TICK_SEC = 300.0
T0_INTRADAY_MINUTE_CACHE_HOURS = 5.0 / 60.0
T0_INTRADAY_MINUTE_LOOKBACK_DAYS = 5
