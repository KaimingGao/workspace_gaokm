"""做 T 规则配置（纸面 / 回测共用）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, Optional

# fill_mode:
#   trigger     — 按触发价成交（默认，偏保守）
#   mid         — 触发价与极值中点
#   optimistic  — 卖用 high、买用 low（上界，对照用）
#
# direction:
#   dual_y  — y_trade 资格 · y_τ 主方向 · y_eod 冲突跳过 · y_on 回补
#   y_tau_map（dual_y 子选项）— scalp|trend|fixed_long|fixed_reverse
#   （long_t / reverse_t / auto / signal 已下线；仅单测可经 load_t0_rules 显式传入）
#
# path_mode: 仅 first_touch（分钟时间序第一触达）。日线 dual_touch/veto/adverse 已删除。

DEFAULT_T0_RULES: Dict[str, Any] = {
    "enabled": True,
    "t0_ratio": 0.4,
    "sell_trigger_pct": 2.0,
    "buy_trigger_pct": 1.5,
    "must_cover_same_day": False,
    "lot_size": 100,
    "ref": "open",
    "fill_mode": "trigger",
    "direction": "dual_y",
    "auto_strong_pct": 0.5,
    "auto_weak_pct": 0.5,
    "dir_enter": 0.35,
    "w_gap": 0.45,
    "w_yclose_loc": 0.20,
    "w_mom3": 0.20,
    "w_gap_atr": 0.15,
    "path_mode": "first_touch",
    "minute_period": "5",
    "min_range_pct": None,  # None → max(sell+buy)*0.6
    "use_atr": True,
    "atr_window": 14,
    "atr_sell_mult": 0.9,
    "atr_buy_mult": 0.7,
    # dual_y 阈值（百分比点）
    "y_trade_floor": 0.15,
    "y_eod_prior": 0.35,
    "y_tau_enter": 0.25,
    "y_on_risk": 0.80,
    "y_on_allow": 1.20,
    "y_block_conflict": True,
    "y_tau_map": "scalp",
    "y_ratio_boost_cap": 1.25,
    "y_ratio_cut": 0.75,
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
    cfg["t0_ratio"] = max(0.05, min(float(cfg.get("t0_ratio") or 0.4), 1.0))
    cfg["sell_trigger_pct"] = max(0.1, min(float(cfg.get("sell_trigger_pct") or 2.0), 20.0))
    cfg["buy_trigger_pct"] = max(0.1, min(float(cfg.get("buy_trigger_pct") or 1.5), 20.0))
    cfg["lot_size"] = max(1, int(cfg.get("lot_size") or 100))
    cfg["auto_strong_pct"] = max(0.0, min(float(cfg.get("auto_strong_pct") or 0.5), 5.0))
    cfg["auto_weak_pct"] = max(0.0, min(float(cfg.get("auto_weak_pct") or 0.5), 5.0))
    cfg["dir_enter"] = max(0.05, min(float(cfg.get("dir_enter") or 0.35), 1.0))
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
        ("y_eod_prior", 0.05, 5.0, 0.35),
        ("y_tau_enter", 0.05, 5.0, 0.25),
        ("y_on_risk", 0.1, 10.0, 0.80),
        ("y_on_allow", 0.1, 10.0, 1.20),
        ("y_ratio_boost_cap", 1.0, 2.0, 1.25),
        ("y_ratio_cut", 0.2, 1.0, 0.75),
    ):
        try:
            raw = cfg.get(yk)
            val = float(default if raw is None or raw == "" else raw)
        except (TypeError, ValueError):
            val = float(default)
        cfg[yk] = max(lo, min(val, hi))
    from core.t0.score_policy import normalize_y_trade_floor

    cfg["y_trade_floor"] = normalize_y_trade_floor(cfg.get("y_trade_floor"))
    cfg["y_block_conflict"] = bool(cfg.get("y_block_conflict", True))
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
